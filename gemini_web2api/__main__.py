"""Entry point: ``python -m gemini_web2api`` or the ``gemini-web2api`` script."""
import argparse
import contextlib
import os
import signal
import sys
import threading

from . import __version__
from .config import CONFIG, find_config, load_config, load_warnings, mark_explicit
from .gemini import HAS_HTTPX, set_log_level, warm_up
from .models import MODELS, default_model
from .server import build_server

BANNER = """\
gemini-web2api v{version}
  Listening:  http://{host}:{port}
  Base URL:   http://localhost:{port}/v1
  Dashboard:  http://localhost:{port}/
  Health:     http://localhost:{port}/health
  Models:     {model_count} ({default_model} default)
  Auth:       {auth}
  Cookie:     {cookie}
  Proxy:      {proxy}
  Streaming:  {streaming}
  Build tag:  {bl}
  Rate limit: {rate_limit}
  Temporary:  {temporary}
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gemini-web2api",
        description="Serve Google Gemini Web as an OpenAI-compatible API.",
        epilog="Every option can also be set in config.json or via "
               "GEMINI_WEB2API_<OPTION> environment variables.",
    )
    parser.add_argument("--port", type=int, default=None, help="TCP port to listen on")
    parser.add_argument("--host", type=str, default=None, help="bind address (default 0.0.0.0)")
    parser.add_argument("--config", type=str, default=None, help="path to config.json")
    parser.add_argument("--cookie-file", type=str, default=None,
                        help="path to a cookie file or gemini-auth.json")
    parser.add_argument("--proxy", type=str, default=None,
                        help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--api-key", action="append", default=None, dest="api_keys",
                        help="require this API key (repeatable; replaces config.json keys)")
    parser.add_argument("--default-model", type=str, default=None,
                        help=f"model used when a request omits one (default {default_model()})")
    parser.add_argument("--gemini-bl", type=str, default=None,
                        help="pin the Gemini build tag and disable auto-refresh")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                        default=None, help="minimum log severity (default info)")
    parser.add_argument("--rate-limit", type=int, default=None, metavar="N",
                        help="max requests per window per key/IP (0 disables)")
    parser.add_argument("--rate-limit-window", type=int, default=None, metavar="SEC",
                        help="rate limit window in seconds (default 60)")
    parser.add_argument("--quiet", action="store_true", help="disable request logging")
    parser.add_argument("--no-auto-bl", action="store_true",
                        help="do not refresh the build tag from upstream at startup")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    return parser


def apply_cli_overrides(args):
    """Push CLI flags into CONFIG, recording them as explicit operator choices."""
    applied = {}
    if args.host:
        applied["host"] = args.host
    if args.port:
        applied["port"] = args.port
    if args.cookie_file:
        applied["cookie_file"] = args.cookie_file
    if args.proxy:
        applied["proxy"] = args.proxy
    if args.api_keys:
        applied["api_keys"] = list(args.api_keys)
    if args.default_model:
        applied["default_model"] = args.default_model
    if args.gemini_bl:
        applied["gemini_bl"] = args.gemini_bl
        # An operator who pins the build tag means it.
        applied["auto_update_bl"] = False
    if args.quiet:
        applied["log_requests"] = False
    if args.no_auto_bl:
        applied["auto_update_bl"] = False
    if args.rate_limit is not None:
        applied["rate_limit_max"] = args.rate_limit
    if args.rate_limit_window is not None:
        applied["rate_limit_window_sec"] = args.rate_limit_window
    if args.log_level:
        set_log_level(args.log_level)

    CONFIG.update(applied)
    mark_explicit(applied.keys())
    return applied


def print_banner(server):
    host, port = server.server_address[0], server.server_address[1]
    keys = CONFIG.get("api_keys") or []
    cookie_file = CONFIG.get("cookie_file")
    cookie = "none (anonymous)"
    if cookie_file:
        cookie = f"{cookie_file}" + ("" if os.path.exists(cookie_file) else "  [MISSING FILE]")
    print(BANNER.format(
        version=__version__,
        host=host,
        port=port,
        model_count=len(MODELS),
        default_model=CONFIG.get("default_model"),
        auth=(f"{len(keys)} key(s) required" if keys else "DISABLED — open access"),
        cookie=cookie,
        proxy=CONFIG.get("proxy") or "system env (HTTP_PROXY/HTTPS_PROXY)",
        streaming=("httpx (true streaming)" if HAS_HTTPX else
                   "urllib (buffered — pip install httpx for real streaming)"),
        bl=CONFIG.get("gemini_bl"),
        rate_limit=(f"{CONFIG['rate_limit_max']}/{CONFIG['rate_limit_window_sec']}s"
                    if CONFIG.get("rate_limit_max") else "disabled"),
        temporary="yes" if CONFIG.get("temporary_chats") else "no",
    ))
    if not keys:
        print("  ⚠ No API keys configured: anyone who can reach this port can use it.")
        print("    Set api_keys in config.json, or pass --api-key.")
    if not HAS_HTTPX:
        print("  ⚠ httpx is not installed; 'stream': true returns one buffered chunk.")
        print("    Install it with: pip install httpx")
    for warning in load_warnings():
        print(f"  ⚠ {warning}")
    sys.stdout.flush()


def main(argv=None):
    args = build_parser().parse_args(argv)

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG") or find_config()
    load_config(config_path)
    apply_cli_overrides(args)

    port = int(CONFIG["port"])
    host = CONFIG.get("host", "0.0.0.0")
    try:
        server = build_server(host, port)
    except OSError as exc:
        print(f"error: cannot bind {host}:{port} — {exc}", file=sys.stderr)
        return 1

    # Resolve the build tag and pre-read the cookie before serving, so the first
    # request is not paying for either. Never fatal.
    try:
        warm_up()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  ⚠ startup warm-up failed: {exc}", file=sys.stderr)

    print_banner(server)

    stopping = [False]

    def handle_signal(signum, _frame):
        if stopping[0]:
            return
        stopping[0] = True
        name = signal.Signals(signum).name
        print(f"\nReceived {name}, shutting down…")
        sys.stdout.flush()
        # shutdown_gracefully() blocks on serve_forever's poll loop, so it must
        # run off the signal handler.
        threading.Thread(target=server.shutdown_gracefully, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        # Not every platform/thread allows every handler; failing to install one
        # must not stop the server from starting.
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(signum, handle_signal)

    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if not stopping[0]:
            server.shutdown_gracefully()
        from .gemini import close_client
        close_client()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
