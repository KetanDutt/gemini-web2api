"""Multimodal support: remote image fetching and Scotty resumable upload.

Gemini Web accepts images only as server-side file references, so an inbound
OpenAI ``image_url`` part has to be downloaded and re-uploaded to Google's
``content-push`` endpoint before the prompt is sent.
"""
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from .config import CONFIG
from .gemini import (
    USER_AGENT,
    GeminiUpstreamError,
    _cookie_headers,
    _get_ssl_ctx,
    _urlopen,
    log,
    make_sapisidhash,
)

# Fallbacks for the WIZ_global_data tokens when the page cannot be scraped.
_FALLBACK_PUSH_ID = "feeds/mcudyrk2a4khkz"
_FALLBACK_PCTX = "CgcSBWjK7pYx"

_page_tokens_cache = {"tokens": {}, "ts": 0.0}
_PAGE_TOKEN_TTL_SEC = 600

# WIZ_global_data keys are obfuscated and do rotate, so several historical
# spellings are tried in order.
_TOKEN_PATTERNS = {
    "push_id": [r'"qKIAYe":"([^"]+)"', r'"pushId":"([^"]+)"'],
    "pctx": [r'"Ylro7b":"([^"]+)"', r'"clientPctx":"([^"]+)"'],
    "at": [r'"thykhd":"([^"]+)"', r'SNlM0e":"([^"]+)"'],
}

UPLOAD_TIMEOUT_SEC = 60


class ImageFetchError(RuntimeError):
    """A remote image could not be fetched, or was refused by policy."""


# ─── SSRF guard ──────────────────────────────────────────────────────────────

def _is_blocked_address(host):
    """True when ``host`` resolves to an address that must not be fetched.

    The server binds to 0.0.0.0 by default and fetches whatever URL a client
    puts in ``image_url``. Without this check any caller could use it to reach
    cloud metadata (169.254.169.254), localhost services, or the internal
    network, and read the result back through the model.
    """
    if not CONFIG.get("block_private_image_urls", True):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable hosts are refused: letting them through would allow a
        # DNS-rebinding style bypass on the redirect hop.
        return True
    for info in infos:
        raw = info[4][0]
        try:
            addr = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return True
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) must be judged on its inner address.
        mapped = getattr(addr, "ipv4_mapped", None)
        if mapped is not None and (mapped.is_private or mapped.is_loopback
                                   or mapped.is_link_local or mapped.is_reserved):
            return True
    return False


def _validate_url(url):
    """Return a parsed URL that is safe to fetch, or raise ImageFetchError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageFetchError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise ImageFetchError("URL has no hostname")
    if parsed.username or parsed.password:
        raise ImageFetchError("credentials in image URLs are not allowed")
    if _is_blocked_address(parsed.hostname):
        raise ImageFetchError(
            f"refusing to fetch {parsed.hostname!r}: resolves to a private, "
            "loopback or link-local address (set block_private_image_urls=false "
            "to override on a trusted network)"
        )
    return parsed


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect hop.

    ``urlopen`` follows redirects transparently, so a public URL could 302 to
    ``http://169.254.169.254/`` and defeat a check that only looked at the
    original target.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# One opener per (proxy, guarded) combination, reused across calls. The
# handlers urllib builds are stateless per request, so sharing an opener is
# safe, and image fetches/uploads are the only place the stdlib transport is
# constructed per call — building one per image was pure waste on a path that
# already pays two round trips per image.
_opener_cache = {}
_opener_lock = threading.Lock()


def _cached_opener(proxy, guarded=False):
    """Return the shared urllib opener for this proxy/redirect policy."""
    key = (proxy or "", guarded)
    with _opener_lock:
        opener = _opener_cache.get(key)
        if opener is None:
            handlers = [urllib.request.HTTPSHandler(context=_get_ssl_ctx())]
            if guarded:
                handlers.insert(0, _GuardedRedirect)
            if proxy:
                handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            opener = urllib.request.build_opener(*handlers)
            _opener_cache[key] = opener
        return opener


def _read_capped(response, limit):
    """Read at most ``limit`` bytes, refusing oversized payloads."""
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ImageFetchError(f"image exceeds the {limit}-byte limit")
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ImageFetchError(f"image exceeds the {limit}-byte limit")
    return data


def fetch_image_bytes(url):
    """Download an image and return its bytes, or ``b""`` on any failure.

    Raises nothing by contract: every refusal (bad scheme, private address,
    oversize, network error) is logged and reported as an empty result, and the
    caller turns that into a 400 naming the URL — a missing or blocked image is
    the client's problem, and failing the request is louder than silently
    answering a prompt the user thinks included a picture.
    """
    limit = int(CONFIG.get("max_image_bytes", 20 * 1024 * 1024))
    try:
        _validate_url(url)
    except ImageFetchError as exc:
        log(f"Image fetch refused: {exc}", "warning")
        return b""

    opener = _cached_opener(CONFIG.get("proxy"), guarded=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=30) as resp:
            return _read_capped(resp, limit)
    except ImageFetchError as exc:
        log(f"Image fetch refused: {exc}", "warning")
        return b""
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        log(f"Image fetch failed for {url}: {exc}", "warning")
        return b""
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Image fetch failed for {url}: {exc}", "warning")
        return b""


# ─── MIME sniffing ───────────────────────────────────────────────────────────

def detect_image_mime(image_bytes, fallback="image/png"):
    """Infer a raster image MIME type from its file signature.

    Clients routinely send a wrong or missing ``mime_type``; Google rejects
    uploads whose declared type disagrees with the payload.
    """
    if not isinstance(image_bytes, (bytes, bytearray)):
        return fallback
    head = bytes(image_bytes[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"hevc", b"hevx"):
            return "image/heic"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return fallback


# ─── Page tokens ─────────────────────────────────────────────────────────────

def _get_page_tokens():
    """Scrape the WIZ_global_data tokens the upload endpoint requires."""
    from .gemini import _account_prefix, _get_httpx_client

    headers = {"User-Agent": USER_AGENT, **_cookie_headers()}
    url = f"https://gemini.google.com{_account_prefix()}/app"
    try:
        client = _get_httpx_client()
        if client is not None:
            html = client.get(url, headers=headers, timeout=30).text
        else:
            req = urllib.request.Request(url, headers=headers)
            html = _urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
    except Exception as exc:
        log(f"Page token fetch failed: {exc}", "warning")
        return {}

    tokens = {}
    for key, patterns in _TOKEN_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                tokens[key] = match.group(1)
                break
    # The page also carries the XSRF token; adopt it when the operator has not
    # pinned one, which is what makes authenticated requests work out of the box.
    if tokens.get("at"):
        from .config import apply_defaults
        apply_defaults({"xsrf_token": tokens["at"]}, source="gemini page")
    return tokens


def _cached_page_tokens():
    now = time.time()
    if now - _page_tokens_cache["ts"] > _PAGE_TOKEN_TTL_SEC or not _page_tokens_cache["tokens"]:
        _page_tokens_cache["tokens"] = _get_page_tokens()
        _page_tokens_cache["ts"] = now
    return _page_tokens_cache["tokens"]


def reset_page_token_cache():
    """Drop cached page tokens. Intended for tests."""
    _page_tokens_cache.update({"tokens": {}, "ts": 0.0})


# ─── Upload ──────────────────────────────────────────────────────────────────

def upload_image(image_bytes, filename="image.png", mime_type="image/png"):
    """Upload an image via Scotty resumable upload; return the file reference.

    Two round trips: ``start`` to obtain an upload URL, then
    ``upload, finalize`` with the bytes.
    """
    if not image_bytes:
        raise RuntimeError("empty image payload")

    tokens = _cached_page_tokens()
    push_id = tokens.get("push_id", _FALLBACK_PUSH_ID)
    pctx = tokens.get("pctx", _FALLBACK_PCTX)

    from .gemini import load_cookie
    cookie_str, sapisid = load_cookie()

    opener = _cached_opener(CONFIG.get("proxy"))

    start_headers = {
        "Push-ID": push_id,
        "X-Tenant-Id": "bard-storage",
        "X-Client-Pctx": pctx,
        "X-Goog-Upload-Header-Content-Length": str(len(image_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "User-Agent": USER_AGENT,
    }
    if cookie_str:
        start_headers["Cookie"] = cookie_str
    if sapisid:
        start_headers["Authorization"] = make_sapisidhash(sapisid)

    req = urllib.request.Request("https://content-push.googleapis.com/upload/",
                                 data=b"", headers=start_headers, method="POST")
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", errors="replace")[:200]
        hint = (" — image upload usually requires a signed-in cookie"
                if exc.code in (401, 403) else "")
        raise RuntimeError(f"upload start failed: HTTP {exc.code}{hint} {detail}".strip()) from exc

    upload_url = resp.headers.get("X-Goog-Upload-URL") or resp.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError(f"no upload URL in response headers: {dict(resp.headers)}")
    log(f"Upload session started: {upload_url[:80]}…", "debug")

    upload_headers = {
        "X-Goog-Upload-Command": "upload, finalize",
        "X-Goog-Upload-Offset": "0",
        "Content-Type": "application/octet-stream",
        "User-Agent": USER_AGENT,
    }
    req2 = urllib.request.Request(upload_url, data=bytes(image_bytes),
                                  headers=upload_headers, method="POST")
    try:
        resp2 = opener.open(req2, timeout=UPLOAD_TIMEOUT_SEC)
        file_ref = resp2.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"upload finalize failed: HTTP {exc.code} {detail}".strip()) from exc

    if not file_ref or not file_ref.startswith("/"):
        raise RuntimeError(f"invalid file reference: {file_ref[:100]!r}")

    log(f"Image uploaded: {filename} -> {file_ref[:50]}…")
    return file_ref


__all__ = [
    "ImageFetchError",
    "GeminiUpstreamError",
    "detect_image_mime",
    "fetch_image_bytes",
    "reset_page_token_cache",
    "upload_image",
]
