"""Launcher used by the Windows executable built with ``scripts/build_windows.py``.

The frozen ``gemini-web2api.exe`` is the same server as ``python -m
gemini_web2api``. This module only adds what a double-clicked program needs:

* everything it reads or writes (``config.json``, relative cookie paths, logs)
  lives beside the executable instead of wherever the shortcut happened to
  start it from;
* a first run writes a ``config.json`` bound to ``127.0.0.1``, the same safe
  default ``start.bat`` uses, so the proxy is not exposed to the LAN by
  accident;
* the dashboard opens in the default browser once the port is listening.

Pass ``--no-browser`` to skip the browser. Every other argument goes to the
server, so ``gemini-web2api.exe --port 9000 --api-key sk-...`` works as expected.
"""
import copy
import json
import os
import sys
import threading
import webbrowser

# Localhost-only on first run. See scripts/win_setup.py for the reasoning.
FIRST_RUN_HOST = "127.0.0.1"

NO_BROWSER_FLAG = "--no-browser"

# Flags that print and exit without starting a server.
INFO_FLAGS = {"--version", "--help", "-h"}

# Seconds to wait after the port is bound before opening the browser, so the
# first page load does not race the server's own startup output.
BROWSER_DELAY_SEC = 0.6


def is_frozen():
    """True inside the PyInstaller bundle, False when run from source."""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """Folder that holds the executable (frozen) or the repository (source)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def split_launcher_args(argv):
    """Return ``(open_browser, server_argv)``.

    ``--no-browser`` is consumed here so argparse in the server never sees it.
    """
    open_browser = True
    server_argv = []
    for arg in argv:
        if arg == NO_BROWSER_FLAG:
            open_browser = False
        else:
            server_argv.append(arg)
    return open_browser, server_argv


def first_run_config_text():
    """The JSON written to ``config.json`` on first run.

    Built from the package's own defaults, so it cannot drift from them, with
    the bind address forced to localhost and no API keys (open access on this
    machine only, which is what the first-run message tells the user).
    """
    from .config import DEFAULT_CONFIG

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["host"] = FIRST_RUN_HOST
    config["api_keys"] = []
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def ensure_config(directory):
    """Create ``config.json`` in ``directory`` if absent. Returns True if created."""
    path = os.path.join(directory, "config.json")
    if os.path.exists(path):
        return False
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(first_run_config_text())
    return True


def dashboard_url(port):
    return f"http://localhost:{port}/"


def open_dashboard_later(port, delay=BROWSER_DELAY_SEC):
    """Open the dashboard in a background timer. Failures are non-fatal."""

    def open_it():
        try:
            webbrowser.open(dashboard_url(port))
        except Exception as exc:  # pragma: no cover - depends on the desktop
            print(f"  could not open a browser ({exc}); open {dashboard_url(port)} yourself")

    timer = threading.Timer(delay, open_it)
    timer.daemon = True
    timer.start()
    return timer


def run(argv=None):
    """Entry point for the frozen executable and ``scripts/windows_entry.py``."""
    from . import __main__ as server_main

    argv = list(sys.argv[1:] if argv is None else argv)
    open_browser, server_argv = split_launcher_args(argv)

    if is_frozen():
        # Relative paths in config.json and --cookie-file resolve from here.
        os.chdir(app_dir())
        # --version and --help only inspect the program; they must not leave a
        # config.json behind (the build's smoke test runs --version).
        if not set(server_argv) & INFO_FLAGS and ensure_config(app_dir()):
            print(f"  created config.json (bound to {FIRST_RUN_HOST}; authentication off)")

    def on_ready(port):
        if open_browser:
            open_dashboard_later(port)

    return server_main.main(server_argv, on_ready=on_ready)
