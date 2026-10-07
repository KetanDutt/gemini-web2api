"""Configuration management.

Configuration is resolved in increasing order of precedence:

1. :data:`DEFAULT_CONFIG`
2. a JSON file (``./config.json``, ``~/.config/gemini-web2api/config.json``,
   ``--config``, or ``$GEMINI_WEB2API_CONFIG``)
3. environment variables (``GEMINI_WEB2API_<KEY>``)
4. command-line flags

Every key in :data:`DEFAULT_CONFIG` is settable from the environment by
upper-casing it and prefixing it, e.g. ``GEMINI_WEB2API_PORT=9000``.
"""
import copy
import json
import os
import threading

ENV_PREFIX = "GEMINI_WEB2API_"

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260716.08_p0",
    "auth_user": None,
    "xsrf_token": None,
    "default_model": "gemini-3.6-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "temporary_chats": False,
    # --- added in 1.2.0 -------------------------------------------------
    # Refresh `gemini_bl` from the live Gemini page instead of trusting the
    # pinned value. Without this a Google frontend rollout breaks the server
    # with HTTP 405 until it is rebuilt.
    "auto_update_bl": True,
    # Return HTTP 400 for unknown model names instead of substituting the
    # default. Off by default because upstream clients routinely probe with
    # arbitrary model identifiers.
    "strict_models": False,
    # Reject image URLs that resolve to loopback/link-local/private/reserved
    # addresses. Turning this off re-opens an SSRF hole; only do so on a
    # trusted network.
    "block_private_image_urls": True,
    # Hard cap on a single inbound request body, in bytes (25 MiB).
    "max_request_bytes": 25 * 1024 * 1024,
    # Hard cap on a single fetched remote image, in bytes (20 MiB).
    "max_image_bytes": 20 * 1024 * 1024,
    # Sliding-window rate limit. 0 disables it.
    "rate_limit_max": 0,
    "rate_limit_window_sec": 60,
    # Value for the Access-Control-Allow-Origin response header.
    "cors_origin": "*",
    # Seconds to wait for in-flight requests to finish on shutdown.
    "shutdown_timeout_sec": 5,
}

# Deep copy: a shallow one would alias mutable defaults such as
# `api_keys`, so an in-place CONFIG mutation would corrupt DEFAULT_CONFIG.
CONFIG = copy.deepcopy(DEFAULT_CONFIG)

# Keys whose environment representation needs custom parsing rather than the
# generic bool/int/str coercion.
_LIST_KEYS = {"api_keys"}

_lock = threading.RLock()

# Populated by load_config so the CLI/startup banner can explain where each
# value came from and warn about mistakes.
_last_load = {"path": None, "warnings": []}

# Keys the operator set explicitly (file, env or CLI). Anything not in here is
# still at its default, which lets a cookie/auth file supply values such as
# `xsrf_token` and `gemini_bl` without ever overriding a deliberate choice.
_explicit = set()


def _coerce(key, raw):
    """Coerce a raw (usually string) value to the type of its default."""
    default = DEFAULT_CONFIG.get(key)
    if key in _LIST_KEYS:
        if isinstance(raw, list):
            return [str(v) for v in raw]
        text = str(raw).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
        return [part.strip() for part in text.replace("|", ",").split(",") if part.strip()]
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            _warn(f"config: {key}={raw!r} is not an integer, keeping {default!r}")
            return default
    if isinstance(default, float):
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            _warn(f"config: {key}={raw!r} is not a number, keeping {default!r}")
            return default
    if default is None:
        # Untyped (nullable) key: accept strings, treat "" as "unset".
        if isinstance(raw, str) and not raw.strip():
            return None
        return raw
    return raw


def _warn(message):
    _last_load["warnings"].append(message)


def _apply_mapping(mapping, source, mark_explicit=True):
    unknown = [k for k in mapping if k not in DEFAULT_CONFIG]
    for key in unknown:
        _warn(f"{source}: ignoring unknown option '{key}'")
    for key, value in mapping.items():
        if key in DEFAULT_CONFIG:
            CONFIG[key] = _coerce(key, value)
            if mark_explicit:
                _explicit.add(key)


def load_config(path=None):
    """Load config from a JSON file, then apply environment overrides.

    Returns the live :data:`CONFIG` dict. Missing files are not an error: the
    defaults are usable on their own.
    """
    with _lock:
        _last_load["warnings"] = []
        _last_load["path"] = None

        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, ValueError) as exc:
                _warn(f"config: {path} is not valid JSON ({exc}); using defaults")
                data = {}
            except OSError as exc:
                _warn(f"config: cannot read {path} ({exc}); using defaults")
                data = {}
            if not isinstance(data, dict):
                _warn(f"config: {path} must contain a JSON object; using defaults")
                data = {}
            _apply_mapping(data, path)
            _last_load["path"] = path

        for key in DEFAULT_CONFIG:
            env_name = ENV_PREFIX + key.upper()
            if env_name in os.environ:
                _apply_mapping({key: os.environ[env_name]}, "env")

        # Standard proxy variables are honoured as a last resort so that
        # `HTTPS_PROXY=... python -m gemini_web2api` works as documented.
        if not CONFIG.get("proxy"):
            for env_name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
                value = os.environ.get(env_name)
                if value:
                    CONFIG["proxy"] = value
                    break

        _validate()
        return CONFIG


def _validate():
    """Clamp nonsensical numeric config into a usable range."""
    if not 0 <= int(CONFIG.get("port", 8081)) <= 65535:
        _warn(f"config: port {CONFIG['port']} out of range, using 8081")
        CONFIG["port"] = 8081
    if CONFIG.get("retry_attempts", 0) < 1:
        _warn("config: retry_attempts must be >= 1, using 1")
        CONFIG["retry_attempts"] = 1
    if CONFIG.get("request_timeout_sec", 0) <= 0:
        _warn("config: request_timeout_sec must be > 0, using 180")
        CONFIG["request_timeout_sec"] = 180
    if CONFIG.get("max_request_bytes", 0) <= 0:
        CONFIG["max_request_bytes"] = DEFAULT_CONFIG["max_request_bytes"]
    if CONFIG.get("default_model") not in _known_models():
        _warn(
            f"config: default_model {CONFIG.get('default_model')!r} is not a known model, "
            f"using {DEFAULT_CONFIG['default_model']!r}"
        )
        CONFIG["default_model"] = DEFAULT_CONFIG["default_model"]


def _known_models():
    # Imported lazily: models.py imports this module, so a module-level import
    # would be circular.
    try:
        from .models import MODELS
        return set(MODELS)
    except Exception:
        return set()


def find_config():
    """Search for a config file in the standard locations."""
    candidates = [
        os.environ.get(ENV_PREFIX + "CONFIG"),
        "./config.json",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
        os.path.expanduser("~/.config/gemini-web2api/config.json"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def reset_config():
    """Restore pristine defaults. Intended for tests."""
    with _lock:
        CONFIG.clear()
        CONFIG.update(copy.deepcopy(DEFAULT_CONFIG))
        _explicit.clear()
        _last_load["path"] = None
        _last_load["warnings"] = []
    return CONFIG


def mark_explicit(keys):
    """Record that the operator set ``keys`` deliberately (e.g. via CLI flags)."""
    with _lock:
        _explicit.update(keys)


def is_explicit(key):
    """True when the operator set ``key`` themselves rather than taking the default."""
    return key in _explicit


def apply_defaults(mapping, source="auth file"):
    """Fill in config values that the operator has *not* set explicitly.

    Used by the cookie loader: an exported ``gemini-auth.json`` carries
    ``xsrf_token``, ``gemini_bl`` and ``auth_user`` alongside the cookie, and
    those should be adopted automatically — but never in preference to a value
    the operator wrote into ``config.json`` or the environment.

    Returns the list of keys that were actually applied.
    """
    applied = []
    with _lock:
        for key, value in mapping.items():
            if key not in DEFAULT_CONFIG or key in _explicit:
                continue
            if value in (None, ""):
                continue
            CONFIG[key] = _coerce(key, value)
            applied.append(key)
        if applied:
            _warn(f"{source}: applied {', '.join(sorted(applied))}")
    return applied


def snapshot(redact=True):
    """Return a JSON-safe copy of the config for status endpoints.

    Secrets are replaced with a boolean "is it set" marker so the dashboard can
    display state without leaking credentials.
    """
    data = dict(CONFIG)
    if redact:
        data["api_keys"] = f"{len(CONFIG.get('api_keys') or [])} configured"
        for key in ("xsrf_token",):
            data[key] = "set" if data.get(key) else None
        cookie_file = data.get("cookie_file")
        data["cookie_present"] = bool(cookie_file and os.path.exists(cookie_file))
    return data


def load_warnings():
    """Warnings collected during the most recent :func:`load_config`."""
    return list(_last_load["warnings"])


def load_path():
    """Path of the config file used by the most recent :func:`load_config`."""
    return _last_load["path"]
