"""Gemini Web StreamGenerate protocol implementation.

Talks to the same endpoint the Gemini web app uses and converts between that
internal protobuf-like framing and plain text. Two transports are supported:

* ``httpx`` — real incremental streaming plus connection pooling/keep-alive.
* ``http.client`` — dependency-free fallback with the same two properties: a
  small pool of persistent connections (``urllib.request.urlopen`` opens and
  closes one per call, paying DNS + TCP + TLS every time) and incremental
  reads, so the first token reaches the client instead of the whole reply
  arriving at once. ``urlopen`` is still used for the rare build-tag scrape.
"""
import base64
import contextlib
import hashlib
import http.client
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import httpx
    HAS_HTTPX = True
except ImportError:  # pragma: no cover - depends on the environment
    httpx = None
    HAS_HTTPX = False

from .config import CONFIG, apply_defaults, is_explicit
from .credentials import DEFAULT_COOLDOWN_SEC, Credential, CredentialPool

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
GEMINI_ORIGIN = "https://gemini.google.com"

# Raised when Gemini itself rejects the request. Carries the upstream code so
# callers can map it to a sensible HTTP status instead of a blanket 502.
class GeminiUpstreamError(RuntimeError):
    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


_ssl_ctx = None
_cookie_cache = {"str": "", "sapisid": None, "mtime": None, "path": None}
# The account pool. Rebuilt by `load_credentials()` whenever a configured file
# changes on disk; empty when no cookie file is configured, in which case
# `acquire()` returns None and the request path behaves exactly as before.
_credentials = CredentialPool()
_pool_cache = {"fingerprint": None}
_pool_lock = threading.Lock()
_httpx_client = None
_client_lock = threading.Lock()
_ssl_lock = threading.Lock()
_bl_lock = threading.Lock()
_bl_last_attempt = [0.0]

# Never refresh the build tag more than once per minute, so a hard outage does
# not turn into a request storm against gemini.google.com.
_BL_REFRESH_COOLDOWN_SEC = 60

LOG_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_log_level = [LOG_LEVELS["info"]]


def set_log_level(level):
    """Set the minimum severity that reaches stderr."""
    _log_level[0] = LOG_LEVELS.get(str(level).lower(), LOG_LEVELS["info"])


def log(msg, level="info"):
    """Write a timestamped line to stderr when logging is enabled."""
    if not CONFIG.get("log_requests", True):
        return
    if LOG_LEVELS.get(level, 20) < _log_level[0]:
        return
    import sys
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


def _get_ssl_ctx():
    global _ssl_ctx
    if _ssl_ctx is None:
        with _ssl_lock:
            if _ssl_ctx is None:
                _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def _timeouts():
    """``(connect, read)`` seconds, from config, in a sane relationship.

    The two are separate on purpose. A read timeout has to be generous — a
    thinking model can legitimately take minutes — but applying that same
    number to the handshake means a black-holed route keeps a request hanging
    for three minutes before anything can be retried. Ten seconds to connect,
    as much as the model needs to answer.
    """
    read = max(1.0, float(CONFIG.get("request_timeout_sec", 180) or 180))
    connect = max(1.0, float(CONFIG.get("connect_timeout_sec", 10) or 10))
    return min(connect, read), read


def _get_httpx_client():
    """Return a process-wide pooled httpx client.

    Reusing one client keeps TLS sessions and TCP connections alive across
    requests; constructing a client per request (as the old code did) paid a
    full handshake on every call.
    """
    global _httpx_client
    if not HAS_HTTPX:
        return None
    if _httpx_client is not None:
        return _httpx_client
    connect, read = _timeouts()
    with _client_lock:
        if _httpx_client is None:
            proxy = CONFIG.get("proxy")
            transport = httpx.HTTPTransport(proxy=proxy, retries=0) if proxy else None
            _httpx_client = httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(connect=connect, read=read, write=read, pool=connect),
                verify=True,
                # We set Origin/Referer/User-Agent explicitly per request.
                trust_env=not proxy,
            )
    return _httpx_client


def close_client():
    """Release the pooled clients. Used at shutdown and by tests."""
    global _httpx_client
    with _client_lock:
        client, _httpx_client = _httpx_client, None
    if client is not None:
        with contextlib.suppress(Exception):  # pragma: no cover - best effort
            client.close()
    _keepalive.close_all()


# ─── Cookie / auth ───────────────────────────────────────────────────────────

# Fields a cookie file may supply in addition to the cookie string itself.
# `gemini-cookie-sync-extension` exports all of these; previously only `cookie`
# and `sapisid` were read, so users had to hand-edit config.json with jq.
_AUTH_FILE_CONFIG_KEYS = {
    "xsrf_token": "xsrf_token",
    "gemini_bl": "gemini_bl",
    "auth_user": "auth_user",
}


def _parse_cookie_pairs(text):
    """Parse ``name=value`` pairs out of a cookie string.

    Tolerates the layouts people actually paste: ``;`` with or without a space,
    newlines, a leading ``Cookie:`` header, and trailing semicolons. Splitting
    on the literal ``"; "`` (as before) silently lost SAPISID for anything else,
    which dropped the Authorization header and quietly downgraded Pro to Flash.
    """
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    pairs = {}
    for chunk in re.split(r"[;\r\n]+", text):
        chunk = chunk.strip().strip(",")
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name, value = name.strip(), value.strip()
        if name:
            pairs[name] = value
    return pairs


# curl marks HttpOnly cookies with this prefix on an otherwise ordinary record.
# Google's session cookies (SID, __Secure-1PSID, ...) are HttpOnly, so skipping
# comment lines wholesale would silently drop exactly the ones that matter.
_HTTP_ONLY_PREFIX = "#HttpOnly_"


def _parse_netscape(text):
    """Parse a Netscape/curl cookie-jar file into ``{name: value}``.

    Browser "export cookies" extensions produce this format; the README used to
    tell users to convert it to a single line by hand.
    """
    pairs = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(_HTTP_ONLY_PREFIX):
            line = line[len(_HTTP_ONLY_PREFIX):]
        elif line.startswith("#"):
            continue
        # domain, include-subdomains, path, secure, expiry, name, value
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        name, value = fields[5].strip(), fields[6].strip()
        if name:
            pairs[name] = value
    return pairs


def _looks_like_netscape(text):
    """Detect a cookie jar without relying on the first line being a record.

    Real exports usually start with a comment banner, so scanning only line one
    misses them.
    """
    head = text.lstrip()
    if head.startswith("# Netscape HTTP Cookie File") or head.startswith("# HTTP Cookie File"):
        return True
    for line in head.splitlines()[:20]:
        candidate = line.strip()
        if candidate.startswith(_HTTP_ONLY_PREFIX):
            candidate = candidate[len(_HTTP_ONLY_PREFIX):]
        if candidate.startswith("#") or "\t" not in candidate:
            continue
        if len(candidate.split("\t")) >= 7:
            return True
    return False


def _cookie_string_from_objects(items):
    """Build a cookie header from a list of ``{name, value}`` objects."""
    pairs = {}
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            pairs[str(item["name"])] = str(item.get("value", ""))
    return pairs


def _parse_auth_file(content):
    """Return ``(cookie_str, sapisid, overrides)`` from any supported layout.

    Accepts: a plain ``name=value; ...`` header, a Netscape/curl cookie jar, the
    ``{"cookie": ..., "sapisid": ...}`` object, the ``gemini-auth.json`` export
    produced by the bundled browser extension, and a raw array of cookie objects.
    """
    overrides = {}
    content = content.strip()

    if content.startswith(("[", "{")):
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            log(f"Cookie file is not valid JSON: {exc}", "error")
            return "", None, overrides

        if isinstance(data, list):
            pairs = _cookie_string_from_objects(data)
            cookie_str = "; ".join(f"{k}={v}" for k, v in pairs.items())
            return cookie_str, pairs.get("SAPISID") or None, overrides

        if not isinstance(data, dict):
            return "", None, overrides

        cookie_str = data.get("cookie") or data.get("cookie_string") or ""
        sapisid = data.get("sapisid") or ""
        if not cookie_str and isinstance(data.get("cookies"), (list, dict)):
            raw = data["cookies"]
            pairs = (_cookie_string_from_objects(raw) if isinstance(raw, list)
                     else {str(k): str(v) for k, v in raw.items()})
            cookie_str = "; ".join(f"{k}={v}" for k, v in pairs.items())
            sapisid = sapisid or pairs.get("SAPISID", "")
        if not sapisid and cookie_str:
            sapisid = _parse_cookie_pairs(cookie_str).get("SAPISID", "")
        for cfg_key, file_key in _AUTH_FILE_CONFIG_KEYS.items():
            if data.get(file_key) not in (None, ""):
                overrides[cfg_key] = data[file_key]
        return cookie_str.strip(), (sapisid or None), overrides

    if _looks_like_netscape(content):
        pairs = _parse_netscape(content)
        cookie_str = "; ".join(f"{k}={v}" for k, v in pairs.items())
        return cookie_str, pairs.get("SAPISID") or None, overrides

    pairs = _parse_cookie_pairs(content)
    cookie_str = "; ".join(f"{k}={v}" for k, v in pairs.items())
    return cookie_str, pairs.get("SAPISID") or None, overrides


def configured_cookie_files():
    """Every configured cookie file, primary first and de-duplicated.

    ``cookie_file`` names the primary account and ``cookie_files`` adds the
    rest, so an existing single-cookie configuration keeps working untouched
    and adding a pool is purely additive.
    """
    primary = CONFIG.get("cookie_file")
    extras = CONFIG.get("cookie_files") or []
    if not isinstance(extras, (list, tuple)):
        extras = []
    files = []
    seen = set()
    for path in [primary] + list(extras):
        if not isinstance(path, str) or not path:
            continue
        # De-duplicated by real path, not by the string. `/tmp/a.json` and
        # `/private/tmp/a.json` are one file, and listing both would build a pool
        # of two credentials carrying the *same* cookie: rotation would report
        # itself as active while a "failover" landed on the account that was
        # already rate limited. The operator's own spelling is kept for messages.
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            files.append(path)
    return files


def _refresh_pool(files, fingerprint):
    """Rebuild the credential pool, or return early when nothing changed."""
    with _pool_lock:
        if _pool_cache["fingerprint"] == fingerprint and len(_credentials):
            return
        built = []
        for index, path in enumerate(files):
            if not os.path.exists(path):
                if _cookie_cache["path"] != path:
                    log(f"Cookie file not found: {path}", "warning")
                    _cookie_cache["path"] = path
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except OSError as exc:
                log(f"Cookie load error in {os.path.basename(path)}: {exc}", "error")
                continue
            cookie_str, sapisid, overrides = _parse_auth_file(content)
            if not cookie_str:
                log(f"No cookie found in {os.path.basename(path)}", "warning")
                continue
            # The primary file keeps applying its overrides globally, which is
            # what single-cookie deployments already relied on. Secondary files
            # must not: `apply_defaults` is global, so the last file read would
            # silently win and every request would use one account's settings.
            if index == 0 and overrides:
                applied = apply_defaults(overrides, source=os.path.basename(path))
                if applied:
                    log(f"Applied from {os.path.basename(path)}: {', '.join(applied)}")
            if cookie_str and not sapisid:
                log(f"Cookie in {os.path.basename(path)} has no SAPISID — "
                    "authenticated Pro routing will not be available for it.",
                    "warning")
            built.append(Credential(
                cookie=cookie_str,
                sapisid=sapisid,
                auth_user=overrides.get("auth_user"),
                xsrf_token=overrides.get("xsrf_token"),
                source=path,
            ))
        _credentials.replace(
            built, cooldown_sec=CONFIG.get("cookie_cooldown_sec") or DEFAULT_COOLDOWN_SEC)
        _pool_cache["fingerprint"] = fingerprint
        if len(built) > 1:
            log(f"Cookie pool: {len(built)} accounts, rotating on 429")


def load_credentials():
    """Return the current list of credentials, re-reading changed files."""
    files = configured_cookie_files()
    if not files:
        with _pool_lock:
            if _pool_cache["fingerprint"] is not None:
                _credentials.replace([])
                _pool_cache["fingerprint"] = None
        return []
    fingerprint = []
    for path in files:
        try:
            stat = os.stat(path)
            fingerprint.append((path, stat.st_mtime, stat.st_size))
        except OSError:
            fingerprint.append((path, None, None))
    _refresh_pool(files, tuple(fingerprint))
    return _credentials.credentials()


def load_cookie():
    """Load ``(cookie_str, sapisid)`` for the primary account.

    Kept as the single-account accessor: existing callers and tests use it, and
    it is the credential a deployment configured with one cookie file has.
    """
    pool = load_credentials()
    if pool:
        primary = pool[0]
        cookie_str, sapisid = primary.cookie, primary.sapisid
    else:
        cookie_str, sapisid = "", None
    _cookie_cache.update({
        "str": cookie_str,
        "sapisid": sapisid,
        "path": (CONFIG.get("cookie_file") or ""),
        "mtime": None,
    })
    return cookie_str, sapisid


def pool_snapshot():
    """Per-credential health for ``/status``. Never exposes a cookie.

    Loading first means the first ``/status`` poll after startup reports the
    accounts that are configured, rather than an empty pool.
    """
    load_credentials()
    return _credentials.snapshot()


def reset_cookie_cache():
    """Drop the cached cookie so the next request re-reads the files."""
    _cookie_cache.update({"str": "", "sapisid": None, "mtime": None,
                          "path": None, "fingerprint": None})
    with _pool_lock:
        _credentials.replace([])
        _pool_cache["fingerprint"] = None


def make_sapisidhash(sapisid):
    """Build the ``SAPISIDHASH`` Authorization header Google's web apps use."""
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {GEMINI_ORIGIN}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def _account_prefix(credential=None):
    """Return the Gemini account path prefix for non-default Google accounts.

    Read from the credential when there is one, because in a pool each account
    has its own index: sending account A's `/u/1` with account B's cookies
    addresses the wrong account, or none.
    """
    auth_user = None
    if credential is not None:
        auth_user = credential.auth_user
    if auth_user is None or auth_user == "":
        auth_user = CONFIG.get("auth_user")
    if auth_user is None or auth_user == "":
        return ""
    return f"/u/{auth_user}"


def _build_headers(credential=None):
    """Headers for one request, for ``credential`` or the primary account.

    When no credential is passed the primary account is used, which is what a
    single-cookie deployment has — and ``load_credentials()`` must run first
    because it is what adopts auth_user/xsrf_token/gemini_bl from a
    gemini-auth.json export.
    """
    if credential is None:
        pool = load_credentials()
        credential = pool[0] if pool else None
    cookie_str = credential.cookie if credential is not None else ""
    sapisid = credential.sapisid if credential is not None else None
    prefix = _account_prefix(credential)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": GEMINI_ORIGIN,
        "Referer": f"{GEMINI_ORIGIN}{prefix}/app",
        "X-Same-Domain": "1",
        "User-Agent": USER_AGENT,
    }
    auth_user = (credential.auth_user if credential is not None else None)
    if auth_user is None or auth_user == "":
        auth_user = CONFIG.get("auth_user")
    if prefix and auth_user not in (None, ""):
        headers["X-Goog-AuthUser"] = str(auth_user)
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def _apply_chat_persistence_flags(inner):
    """Apply Gemini Web persistence flags to an outgoing request payload."""
    if CONFIG.get("temporary_chats", False):
        # Match Gemini Web temporary-chat requests: nothing is written to the
        # account's conversation history.
        inner[41] = [1]
        inner[45] = 1
    else:
        inner[41] = [2]


def _build_payload(prompt, model_id, think_mode, file_refs=None, extra_fields=None,
                   credential=None):
    from .models import PAYLOAD_SLOTS

    # See _build_headers: the auth file can supply the XSRF token, and it is
    # only read as a side effect of loading the cookie. Cached, so this is one
    # stat() on a warm process.
    load_cookie()
    inner = [None] * PAYLOAD_SLOTS
    if file_refs:
        refs = [[None, None, ref] for ref in file_refs]
        inner[0] = [prompt, 0, None, refs, None, None, 0]
    else:
        inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    _apply_chat_persistence_flags(inner)
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    if extra_fields:
        for key, value in extra_fields.items():
            index = int(key)
            if 0 <= index < len(inner):
                inner[index] = value
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    # The `at` token belongs to the account, so a pool sends the credential's
    # own when the exported auth file carried one and falls back to the global
    # value otherwise (which is all a single-cookie deployment has).
    xsrf = getattr(credential, "xsrf_token", None) or CONFIG.get("xsrf_token")
    if xsrf:
        params["at"] = xsrf
    return urllib.parse.urlencode(params)


def _get_url(reqid=None, credential=None):
    if reqid is None:
        reqid = int(time.time()) % 1000000
    prefix = _account_prefix(credential)
    return (
        f"{GEMINI_ORIGIN}{prefix}/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={urllib.parse.quote(CONFIG['gemini_bl'], safe='')}&hl=en&_reqid={reqid}&rt=c"
    )


# ─── Build tag (`bl`) discovery ──────────────────────────────────────────────

_BL_PATTERN = re.compile(r"(boq_assistant-bard-web-server_\d+\.\d+_p\d+)")


def fetch_latest_bl(timeout=8):
    """Scrape the current ``bl`` build tag from the Gemini page.

    Returns ``None`` when it cannot be determined; callers must treat that as
    "keep the configured value", never as a fatal error.
    """
    try:
        client = _get_httpx_client()
        if client is not None:
            resp = client.get(
                f"{GEMINI_ORIGIN}{_account_prefix()}/app",
                headers={"User-Agent": USER_AGENT, **_cookie_headers()},
                timeout=timeout,
            )
            html = resp.text
        else:
            req = urllib.request.Request(
                f"{GEMINI_ORIGIN}{_account_prefix()}/app",
                headers={"User-Agent": USER_AGENT, **_cookie_headers()},
            )
            html = _urlopen(req, timeout=timeout).read().decode("utf-8", errors="replace")
        match = _BL_PATTERN.search(html)
        return match.group(1) if match else None
    except Exception as exc:
        log(f"bl auto-update fetch failed: {exc}", "debug")
        return None


def _cookie_headers(credential=None):
    if credential is None:
        pool = load_credentials()
        credential = pool[0] if pool else None
    cookie_str = credential.cookie if credential is not None else ""
    sapisid = credential.sapisid if credential is not None else None
    headers = {}
    if cookie_str:
        headers["Cookie"] = cookie_str
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    return headers


def update_bl_if_needed(force=False):
    """Refresh ``gemini_bl`` from upstream. Returns True when it changed.

    Rate-limited so a sustained outage cannot amplify into a scrape storm.
    """
    now = time.time()
    with _bl_lock:
        if not force and now - _bl_last_attempt[0] < _BL_REFRESH_COOLDOWN_SEC:
            return False
        _bl_last_attempt[0] = now
    new_bl = fetch_latest_bl()
    if new_bl and new_bl != CONFIG["gemini_bl"]:
        log(f"bl auto-updated: {CONFIG['gemini_bl']} -> {new_bl}")
        CONFIG["gemini_bl"] = new_bl
        return True
    return False


def warm_up():
    """Prime caches at startup: resolve ``bl`` and pre-read the cookie file.

    Runs on a short timeout and never raises, so an unreachable network delays
    startup by at most a second or two instead of blocking the banner.
    """
    if not CONFIG.get("auto_update_bl", True):
        return CONFIG.get("gemini_bl")
    if is_explicit("gemini_bl"):
        log("Using gemini_bl from config (auto_update_bl skipped for pinned values)", "debug")
        return CONFIG["gemini_bl"]
    new_bl = fetch_latest_bl(timeout=8)
    if new_bl:
        if new_bl != CONFIG["gemini_bl"]:
            log(f"bl auto-updated at startup: {CONFIG['gemini_bl']} -> {new_bl}")
        CONFIG["gemini_bl"] = new_bl
    else:
        log("Could not refresh bl from upstream; using the pinned value. "
            "Set auto_update_bl=false to silence this.", "warning")
    # Touch the cookie once so the first request is not paying for file IO.
    load_cookie()
    return CONFIG["gemini_bl"]


# ─── Response parsing ────────────────────────────────────────────────────────

# Internal code-execution artifacts and card placeholders that must never reach
# a client.
_ARTIFACT_PATTERNS = [
    re.compile(r"```(?:python|javascript|text)\?code_(?:reference|stdout)&code_event_index=\d+\n.*?```\n?",
               re.DOTALL),
    re.compile(r"http://googleusercontent\.com/card_content/\d+\n?"),
]

_BARD_ERROR = re.compile(r"BardErrorInfo\s*\[(\d+)\]")


def clean_text(text, strip=True):
    """Remove internal code-execution artifacts from model output."""
    if not text:
        return ""
    for pattern in _ARTIFACT_PATTERNS:
        text = pattern.sub("", text)
    return text.strip() if strip else text


def _extract_texts_from_line(line):
    """Parse one ``wrb.fr`` frame and return the text segments it carries.

    Frames are selected structurally. The previous implementation also required
    ``len(line) >= 200`` and ``len(inner_str) >= 50``, which silently discarded
    short frames and surfaced to users as ``content: null``.
    """
    if '"wrb.fr"' not in line:
        return []
    try:
        arr = json.loads(line)
        frame = arr[0]
        if not isinstance(frame, list) or len(frame) < 3:
            return []
        inner_str = frame[2]
        if not isinstance(inner_str, str) or not inner_str:
            return []
        inner = json.loads(inner_str)
    except (json.JSONDecodeError, IndexError, TypeError, ValueError):
        return []
    if not (isinstance(inner, list) and len(inner) > 4 and inner[4]):
        return []
    texts = []
    for part in inner[4]:
        if isinstance(part, list) and len(part) > 1 and isinstance(part[1], list):
            for item in part[1]:
                if isinstance(item, str) and item:
                    texts.append(item)
    return texts


def extract_response_text(raw):
    """Parse a complete StreamGenerate response and return the final text.

    Gemini streams cumulatively: each frame repeats the answer so far, so the
    last frame holds the complete text. Earlier code took the *longest* segment
    across all frames, which disagrees with the last-frame rule whenever a
    response contains an earlier long segment (a preamble, or a separate part).
    Taking the last non-empty frame matches both the wire semantics and the
    behaviour the streaming path already relies on.
    """
    if not raw:
        return ""
    match = _BARD_ERROR.search(raw)
    if match:
        raise GeminiUpstreamError(
            f"Gemini upstream rejected request: BardErrorInfo [{match.group(1)}]",
            code=int(match.group(1)),
        )

    result = ""
    longest = ""
    for line in raw.split("\n"):
        for text in _extract_texts_from_line(line):
            if text.strip():
                result = text
            if len(text) > len(longest):
                longest = text
    # Fall back to the longest segment if no frame carried usable text.
    return clean_text(result or longest)


# ─── Transports ──────────────────────────────────────────────────────────────

# Errors that mean "this socket is gone", raised by http.client when a pooled
# connection was closed by the far end while it sat idle. They are only ever
# raised before a response has been read, which is what makes one retry on a
# fresh socket safe for a POST: the request never reached the server.
_STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    http.client.CannotSendRequest,
    http.client.ResponseNotReady,
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
)


class _StdlibStatusError(Exception):
    """An error status from the stdlib transport.

    Carries the same two attributes ``_describe_http_error`` reads off httpx's
    ``HTTPStatusError`` (``status_code`` and ``response``), so a 429 produces
    the same message — upstream body included — on both transports.
    """

    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}")
        self.response = response
        self.status_code = response.status_code


class _KeepAlivePool:
    """Persistent ``http.client`` connections for the stdlib transport.

    ``urlopen`` opens a connection, sends, reads and closes: DNS + TCP + TLS
    before every single call. Against a local stand-in upstream, five requests
    cost five connections. This keeps them instead and hands one back once its
    response has been read in full, which is where the handshake goes.

    Idle connections are expired rather than trusted: the far end may close one
    whenever it likes, and a stale socket is indistinguishable from a healthy
    one until it is used. ``_STALE_CONNECTION_ERRORS`` covers that race with a
    single retry on a fresh connection.
    """

    #: How long a connection may sit unused before it is not worth trusting.
    IDLE_SECONDS = 90
    #: Connections kept per (scheme, host, port, proxy) key.
    MAX_IDLE = 8

    def __init__(self):
        self._lock = threading.Lock()
        self._idle = {}

    def take(self, key):
        """Return a pooled connection for ``key``, or ``None``."""
        now = time.monotonic()
        with self._lock:
            sockets = self._idle.get(key)
            while sockets:
                connection, returned_at = sockets.pop()
                if now - returned_at <= self.IDLE_SECONDS:
                    return connection
                _close_quietly(connection)
        return None

    def give(self, key, connection):
        """Return a fully-read connection to the pool for reuse."""
        with self._lock:
            sockets = self._idle.setdefault(key, [])
            sockets.append((connection, time.monotonic()))
            while len(sockets) > self.MAX_IDLE:
                _close_quietly(sockets.pop(0)[0])

    def close_all(self):
        with self._lock:
            sockets, self._idle = self._idle, {}
        for entries in sockets.values():
            for connection, _returned_at in entries:
                _close_quietly(connection)

    def idle_count(self):
        """How many connections are held. For tests and diagnostics."""
        with self._lock:
            return sum(len(entries) for entries in self._idle.values())


_keepalive = _KeepAlivePool()


def _close_quietly(connection):
    with contextlib.suppress(Exception):
        connection.close()


def _connection_key(target):
    """Identify a connection by what it connects *to*, proxy included.

    A change of proxy or host must not reuse the previous tunnel, so both are
    part of the key rather than assumed to be constant.
    """
    scheme, host, port, proxy = target
    return (scheme, host, port, proxy)


def _connection_target(url):
    """Split ``url`` into ``(scheme, host, port, proxy_url)``."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = parts.hostname
    if not host:
        raise ValueError(f"upstream URL has no host: {url!r}")
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, host, port, CONFIG.get("proxy")


def _proxy_parts(proxy_url):
    """``(host, port, authorization_header)`` for a proxy URL."""
    parts = urllib.parse.urlsplit(proxy_url if "://" in proxy_url else "http://" + proxy_url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    header = None
    if parts.username:
        raw = f"{urllib.parse.unquote(parts.username)}:{urllib.parse.unquote(parts.password or '')}"
        header = "Basic " + base64.b64encode(raw.encode()).decode()
    return host, port, header


def _connect(target):
    """Open (or take) a connection ready to send, with both timeouts applied.

    Connecting happens here rather than lazily inside ``request()`` so the
    handshake is bounded by the *connect* timeout; the socket is then given the
    (longer) read timeout, which is what the response needs.
    """
    connect_timeout, read_timeout = _timeouts()
    scheme, host, port, proxy = target
    key = _connection_key(target)
    connection = _keepalive.take(key)
    if connection is None:
        ctx = _get_ssl_ctx() if scheme == "https" else None
        if proxy:
            proxy_host, proxy_port, _auth = _proxy_parts(proxy)
            if scheme == "https":
                connection = http.client.HTTPSConnection(
                    proxy_host, proxy_port, timeout=connect_timeout, context=ctx)
                connection.set_tunnel(host, port)
            else:
                connection = http.client.HTTPConnection(
                    proxy_host, proxy_port, timeout=connect_timeout)
        elif scheme == "https":
            connection = http.client.HTTPSConnection(
                host, port, timeout=connect_timeout, context=ctx)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=connect_timeout)
        connection.connect()
    if connection.sock is not None:
        connection.sock.settimeout(read_timeout)
    return key, connection


class _StdlibResponse:
    """An ``http.client`` response wearing the part of the httpx surface we use.

    ``generate()`` and ``generate_stream()`` read ``status_code``, ``text``,
    ``read()``, ``iter_text()`` and ``raise_for_status()``, and the retry loop
    catches a status error. Adapting the stdlib response to that surface (rather
    than teaching the callers about a second kind of response) keeps one retry
    path, one set of rotation rules and one delta-tracking implementation.
    """

    __slots__ = ("status_code", "headers", "_response", "_key", "_connection", "_body")

    def __init__(self, response, key, connection):
        self.status_code = response.status
        self.headers = response.headers
        self._response = response
        self._key = key
        self._connection = connection
        self._body = b""

    # -- reading ---------------------------------------------------------
    def read(self):
        """Read the whole body, then release the connection for reuse."""
        if not self._body:
            self._body = self._response.read() or b""
        return self._body

    def iter_text(self):
        """Yield decoded text as it arrives.

        ``read1`` performs at most one underlying read, so a chunk is handed
        over as soon as the socket has one instead of waiting for a buffer to
        fill or for the response to end. That is the difference between the
        first token arriving immediately and arriving with the last one.
        """
        response = self._response
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            yield chunk.decode("utf-8", errors="replace")
        self._settle()

    def _settle(self):
        """Finish the response so its connection can be reused.

        A length-delimited body ends with ``read1`` returning an empty string
        *without* closing the response, and ``http.client`` refuses to reuse a
        connection whose previous response has not reported itself closed. The
        public way to say "this body is finished" is a zero-length read, which
        is what makes the difference between a pooled connection and a fresh
        handshake on every request. A chunked body closes itself as its
        terminating chunk is parsed, and a connection-delimited one cannot be
        reused at all, so this only matters for ``Content-Length``.
        """
        response = self._response
        with contextlib.suppress(Exception):
            if not response.isclosed() and response.length == 0:
                response.read(0)

    @property
    def text(self):
        return self.read().decode("utf-8", errors="replace")

    # -- control ---------------------------------------------------------
    def raise_for_status(self):
        if self.status_code >= 400:
            # Read the body first: httpx does the same, and the error message
            # is built from it. Without this the caller would see a bare
            # "HTTP 429" where the httpx path shows Google's own explanation.
            with contextlib.suppress(Exception):
                self.read()
            raise _StdlibStatusError(self)

    def close(self):
        """Hand the connection back when the body was fully consumed.

        Two conditions have to hold before the socket is worth keeping: the
        server did not say it is closing (``will_close``), and the body was
        actually read to the end (``isclosed``). A response abandoned halfway —
        a client disconnecting, or a read timeout — leaves unread bytes in the
        socket, and reusing it would parse the tail of the last reply as the
        head of the next one.
        """
        response = self._response
        reusable = (getattr(response, "will_close", True) is False
                    and getattr(response, "isclosed", lambda: False)())
        with contextlib.suppress(Exception):
            response.close()
        if reusable:
            # The *connection* goes back to the pool, not the response: the
            # response has been consumed and is done with.
            _keepalive.give(self._key, self._connection)
        else:
            _close_quietly(self._connection)

    # -- context manager -------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class _StdlibStream:
    """Opens a request on a pooled connection and yields its response.

    One retry on a *fresh* connection covers the keep-alive race: a pooled
    socket the far end closed while idle fails immediately, and because that
    happens before any response byte is read, sending the request again is safe
    even though it is a POST.
    """

    __slots__ = ("_url", "_body", "_headers", "_response")

    def __init__(self, url, body, headers):
        self._url = url
        self._body = body
        self._headers = headers
        self._response = None

    def _send(self):
        target = _connection_target(self._url)
        scheme, _host, _port, proxy = target
        key, connection = _connect(target)
        path = urllib.parse.urlsplit(self._url)
        # Through a proxy an HTTP request carries the absolute URL; an HTTPS one
        # is tunnelled, so it carries the path.
        address = self._url if (proxy and scheme == "http") else (
            path.path + (f"?{path.query}" if path.query else "")) or "/"
        headers = dict(self._headers)
        # httpx decompresses transparently; http.client does not, so bytes that
        # arrived gzipped would be handed to the frame parser as garbage. Ask
        # for the bytes as they are — the framing is JSON text either way.
        headers.setdefault("Accept-Encoding", "identity")
        if proxy and scheme == "http":
            _proxy_host, _proxy_port, auth = _proxy_parts(proxy)
            if auth:
                headers["Proxy-Authorization"] = auth
        try:
            connection.request("POST", address, body=self._body, headers=headers)
            return key, connection, connection.getresponse()
        except _STALE_CONNECTION_ERRORS:
            _close_quietly(connection)
            raise

    def __enter__(self):
        try:
            key, connection, response = self._send()
        except _STALE_CONNECTION_ERRORS:
            # The pooled socket was closed under us; the request never left.
            key, connection, response = self._send()
        self._response = _StdlibResponse(response, key, connection)
        return self._response

    def __exit__(self, *exc_info):
        # Closing the response is what returns the connection to the pool; a
        # stream that is merely abandoned would leak a socket per request.
        if self._response is not None:
            self._response.close()
        self._response = None
        return False


def _open_stream(body, headers, url, client):
    """Return a context manager yielding a response, on either transport."""
    if client is not None:
        return client.stream("POST", url, content=body, headers=headers)
    return _StdlibStream(url, body, headers)


def preconnect():
    """Open one upstream connection ahead of the first request.

    A cold connection costs DNS + TCP + TLS — a few hundred milliseconds on a
    good day, and the client pays it as time-to-first-byte because the request
    cannot be sent until the socket is ready. Doing it once at startup means
    the first caller after a restart gets the latency of a warm process. The
    connection carries no request, and every failure is swallowed: a server
    that cannot reach the internet yet must still start and report why.

    Returns True when a connection is ready for reuse.
    """
    try:
        target = _connection_target(_get_url())
        _connect_timeout, read_timeout = _timeouts()
        key, connection = _connect(target)
        with contextlib.suppress(Exception):
            connection.sock.settimeout(read_timeout)
        _keepalive.give(key, connection)
        log("Upstream connection pre-established", "debug")
        return True
    except Exception as exc:
        log(f"Preconnect failed (will connect on demand): {exc}", "debug")
        return False


def _urlopen(req, timeout=None):
    """urllib request honouring the configured proxy and shared SSL context.

    Kept for the build-tag scrape, which runs once per rollout rather than once
    per request and does not benefit from a pooled connection.
    """
    timeout = timeout or CONFIG.get("request_timeout_sec", 180)
    proxy = CONFIG.get("proxy")
    ctx = _get_ssl_ctx()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ctx),
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, context=ctx, timeout=timeout)


def _describe_http_error(exc):
    """Turn an HTTP error into a message that includes upstream detail.

    Attributes are read from ``exc.__dict__`` rather than with ``getattr``.
    ``urllib.error.HTTPError`` inherits ``tempfile._TemporaryFileWrapper`` via
    ``addinfourl``, and on Python 3.8 that class's ``__getattr__`` looks up
    ``self.__dict__["file"]`` — a key that only exists once ``addbase.__init__``
    has run, which happens only when the error carries an ``fp``. For an
    ``HTTPError`` built without one, *any* missing attribute raises
    ``KeyError: 'file'`` instead of ``AttributeError``, and a ``getattr(...,
    default)`` does not catch it. So a clean "HTTP 429" became an unexplained
    KeyError from inside the standard library — a failure while reporting a
    failure, which is the worst place for one.
    """
    attrs = getattr(exc, "__dict__", {})
    response = attrs.get("response")
    status = attrs.get("status_code") or attrs.get("code")
    if status is None and response is not None:
        status = attrs.get("status_code") or getattr(response, "status_code", None)
    body = ""
    try:
        if response is not None:
            # httpx: .text is available once the body has been read.
            body = getattr(response, "text", "") or ""
            if not body and hasattr(response, "read"):
                body = (response.read() or b"").decode("utf-8", errors="replace")
        elif hasattr(exc, "read"):
            # urllib.error.HTTPError
            body = (exc.read() or b"").decode("utf-8", errors="replace")
    except Exception:
        body = ""
    body = body.strip()[:300]
    return (f"HTTP {status} — {body}" if body else f"HTTP {status}"), status


# httpx raises HTTPStatusError; urllib raises HTTPError; the stdlib transport
# raises _StdlibStatusError. Collect whichever exist so the retry logic below
# stays readable, works without httpx installed, and treats a 429 the same way
# on every transport.
_HTTP_ERRORS = [urllib.error.HTTPError, _StdlibStatusError]
if HAS_HTTPX:
    _HTTP_ERRORS.append(httpx.HTTPStatusError)
_HTTP_ERRORS = tuple(_HTTP_ERRORS)

# Errors that must never buy an account a cooldown. `_rotate` is only reached
# for statuses the pool recognises as "this account", but a transport failure
# has no status at all, so it is named here and checked where it matters.
_TRANSPORT_ERRORS = [urllib.error.URLError, ssl.SSLError, OSError,
                     http.client.HTTPException]
if HAS_HTTPX:
    # ConnectError, RemoteProtocolError, ReadTimeout, ...: the request never
    # produced a response, so it can be repeated at once.
    _TRANSPORT_ERRORS.append(httpx.TransportError)
_TRANSPORT_ERRORS = tuple(_TRANSPORT_ERRORS)


def _next_credential():
    """The credential to use for the next attempt, or ``None`` if none can serve.

    Rotation needs no memory of which account just failed: the failure cooled
    that account, so `acquire()` skips it, and handing a credential out advances
    the round robin past it. That is what makes a 429 a retry on a healthy
    account rather than a repeat of the one that is throttled.
    """
    credential = _credentials.acquire()
    if credential is None and _credentials.rotating:
        log("No credential is currently available; all are cooling down", "warning")
    return credential


def _rotate(credential, status, message):
    """Cool a failed credential and report whether a different one should be tried.

    False for a single-account deployment: there is nothing to rotate to, and
    the retry loop's existing sleep-and-retry behaviour is what already shipped.
    """
    if not _credentials.rotating:
        return False
    if not _credentials.report_status(credential, status, message):
        return False
    log(f"Account {credential.label()} is unavailable (HTTP {status}); "
        f"trying another credential", "warning")
    return True


def generate(prompt, model_id, think_mode, file_refs=None, extra_fields=None):
    """Non-streaming generation with retry. Returns the final text."""
    attempts = max(1, int(CONFIG.get("retry_attempts", 3)))
    delay = CONFIG.get("retry_delay_sec", 2)
    last_err = None
    credential = _next_credential()

    for attempt in range(attempts):
        if credential is None and _credentials.rotating:
            # Every account is cooling. Failing fast with the real reason beats
            # sending an unauthenticated request, which would spend a call to
            # get a less informative error. Note the `rotating` guard: an empty
            # pool means no cookie file is configured at all, which is the
            # anonymous path whose retry behaviour must not change.
            if last_err is None:
                last_err = GeminiUpstreamError(
                    "every configured account is rate limited or rejected; "
                    "retry shortly", status=429)
            break
        # Built per attempt from this attempt's credential: a pool sends a
        # different account's cookies on failover, so headers and URL cannot be
        # computed once outside the loop.
        body = _build_payload(prompt, model_id, think_mode, file_refs,
                              extra_fields, credential).encode()
        headers = _build_headers(credential)
        url = _get_url(credential=credential)
        try:
            client = _get_httpx_client()
            if client is not None:
                resp = client.post(url, content=body, headers=headers)
                if resp.status_code == 405 and update_bl_if_needed():
                    log("Retrying with refreshed bl…")
                    last_err = GeminiUpstreamError("HTTP 405 from upstream", status=405)
                    continue
                resp.raise_for_status()
                _credentials.report_success(credential)
                return extract_response_text(resp.text)
            # No httpx: the stdlib transport, which reuses its connections.
            with _open_stream(body, headers, url, None) as resp:
                if resp.status_code == 405 and update_bl_if_needed():
                    log("Retrying with refreshed bl…")
                    last_err = GeminiUpstreamError("HTTP 405 from upstream", status=405)
                    continue
                resp.raise_for_status()
                _credentials.report_success(credential)
                return extract_response_text(resp.text)
        except _HTTP_ERRORS as exc:
            message, status = _describe_http_error(exc)
            last_err = GeminiUpstreamError(message, status=status)
            if _rotate(credential, status, message):
                # No sleep: the point is to move to a healthy account now.
                credential = _next_credential()
                continue
            if status == 405 and update_bl_if_needed():
                log("Retrying with refreshed bl…")
                continue
        except _TRANSPORT_ERRORS as exc:
            # Connecting failed or the response died mid-read. No account was
            # refused, so nothing is cooled and nothing is rotated — and there
            # is no reason to wait, because a DNS or TLS failure does not
            # repair itself in two seconds. Retrying immediately turns a
            # transient blip into a retry instead of a pause plus a retry.
            last_err = exc
            log(f"Transport error on attempt {attempt + 1}/{attempts}: {exc}", "warning")
            continue
        except Exception as exc:
            last_err = exc
        if attempt < attempts - 1:
            log(f"Retry {attempt + 1}/{attempts}: {last_err}", "warning")
            time.sleep(delay)

    if last_err is None:  # pragma: no cover - defensive
        last_err = RuntimeError("generation failed")
    raise last_err


def _iter_stream_frames(resp):
    """Yield text segments from a streaming response, frame by frame."""
    buf = ""
    for chunk in resp.iter_text():
        buf += chunk
        if "BardErrorInfo" in buf:
            match = _BARD_ERROR.search(buf)
            if match:
                raise GeminiUpstreamError(
                    f"Gemini upstream rejected request: BardErrorInfo [{match.group(1)}]",
                    code=int(match.group(1)),
                )
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            for text in _extract_texts_from_line(line):
                yield text
    for text in _extract_texts_from_line(buf):
        yield text


def generate_stream(prompt, model_id, think_mode, file_refs=None, extra_fields=None):
    """Yield incremental text deltas.

    Both transports stream: httpx reads through its own pool, the stdlib path
    through ``_StdlibStream``, which keeps its connections alive and hands each
    network chunk over as it lands. Deltas are cumulative-safe: a frame that
    repeats or extends what was already emitted produces only the new suffix,
    and a frame that belongs to a *different* part is emitted in full rather
    than treated as a corruption (the old code raised "stream content changed
    during retry" here, which killed the response after the client had already
    received 200 OK).
    """
    client = _get_httpx_client()
    attempts = max(1, int(CONFIG.get("retry_attempts", 3)))
    delay = CONFIG.get("retry_delay_sec", 2)

    emitted = ""       # text already handed to the caller
    seen_longest = ""  # longest cumulative frame observed
    last_err = None
    credential = _next_credential()

    for attempt in range(attempts):
        if credential is None and _credentials.rotating:
            # Every account is cooling. Failing fast with the real reason beats
            # sending an unauthenticated request, which would spend a call to
            # get a less informative error. Note the `rotating` guard: an empty
            # pool means no cookie file is configured at all, which is the
            # anonymous path whose retry behaviour must not change.
            if last_err is None:
                last_err = GeminiUpstreamError(
                    "every configured account is rate limited or rejected; "
                    "retry shortly", status=429)
            break
        body = _build_payload(prompt, model_id, think_mode, file_refs,
                              extra_fields, credential)
        headers = _build_headers(credential)
        url = _get_url(credential=credential)
        try:
            with _open_stream(body, headers, url, client) as resp:
                if resp.status_code == 405:
                    resp.read()
                    if update_bl_if_needed():
                        log("Retrying stream with refreshed bl…")
                        last_err = GeminiUpstreamError("HTTP 405 from upstream", status=405)
                        continue
                resp.raise_for_status()
                for text in _iter_stream_frames(resp):
                    if not text:
                        continue
                    if text == emitted or emitted.startswith(text):
                        # A repeat or a stale/partial frame: already delivered.
                        continue
                    if text.startswith(emitted):
                        # Normal cumulative growth — emit only the new suffix.
                        suffix = text[len(emitted):]
                        emitted = text
                        delta = clean_text(suffix, strip=False)
                    else:
                        # A separate part of the answer (multi-part response),
                        # or a fresh sequence after a retry. Emit it in full
                        # instead of aborting the stream.
                        emitted = text
                        delta = clean_text(text, strip=False)
                    if len(text) > len(seen_longest):
                        seen_longest = text
                    if delta:
                        yield delta
                return
        except Exception as exc:
            status = None
            message = None
            if isinstance(exc, _HTTP_ERRORS):
                message, status = _describe_http_error(exc)
                last_err = GeminiUpstreamError(message, status=status)
            else:
                last_err = exc
            if emitted:
                # Bytes already went to the client; retrying would duplicate
                # them. Surface the failure so the handler can terminate the
                # stream cleanly instead of silently truncating it. Checked
                # before rotating, because a rotation is still a retry.
                log(f"Stream failed after {len(emitted)} chars: {last_err}", "error")
                raise last_err from None
            if status is None and isinstance(exc, _TRANSPORT_ERRORS):
                # The socket never produced a response, so this attempt can be
                # repeated at once: nothing was answered and nothing was
                # refused. A stale pooled connection lands here and succeeds on
                # the next attempt instead of costing the client a retry delay.
                log(f"Stream transport error on attempt {attempt + 1}/{attempts}: {exc}",
                    "warning")
                continue
            if status is not None and _rotate(credential, status, message):
                credential = _next_credential()
                continue
            if status == 405 and update_bl_if_needed():
                continue
            if attempt < attempts - 1:
                log(f"Stream retry {attempt + 1}/{attempts}: {last_err}", "warning")
                time.sleep(delay)

    if last_err is None:  # pragma: no cover - defensive
        last_err = RuntimeError("streaming generation failed")
    raise last_err
