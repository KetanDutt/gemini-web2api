"""Setup helper for the one-click Windows launcher (``start.bat``).

Batch scripting is the wrong tool for reading and writing JSON, so ``start.bat``
delegates configuration to this module once a virtual environment exists. It is
deliberately tiny and idempotent: running it twice changes nothing.

Prints machine-readable lines on stdout for the batch file to parse:

    PORT=8081
    CREATED=1        (1 = wrote a new config.json, 0 = left the existing one alone)

Everything human-readable goes to stderr so it cannot corrupt that contract.
"""
import json
import os
import sys

# Localhost-only by default. A one-click launcher runs on somebody's desktop,
# and binding 0.0.0.0 there would expose an unauthenticated proxy to the whole
# LAN. Users who want that can change it, and are told how.
SAFE_HOST = "127.0.0.1"


def say(message):
    """Human-readable output that will not be parsed by the caller."""
    print(message, file=sys.stderr)


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    return os.path.join(repo_root(), "config.json")


def default_config():
    """Build defaults from the package itself, not from config.example.json.

    Reading DEFAULT_CONFIG means this file cannot drift from the real defaults
    the way a copied template would.
    """
    sys.path.insert(0, repo_root())
    import copy

    from gemini_web2api.config import DEFAULT_CONFIG
    return copy.deepcopy(DEFAULT_CONFIG)


def read_port(path):
    """Return the port an existing config.json asks for, or None."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    return port if isinstance(port, int) and 1 <= port <= 65535 else None


def ensure_config(path=None):
    """Create config.json if absent. Returns (port, created)."""
    path = path or config_path()
    if os.path.exists(path):
        port = read_port(path)
        if port is None:
            say("  ! config.json exists but has no usable port; using 8081")
            port = 8081
        say("  config.json already exists - leaving it untouched")
        return port, False

    config = default_config()
    config["host"] = SAFE_HOST
    config["api_keys"] = []
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as exc:
        say(f"  ! could not write config.json: {exc}")
        return config.get("port") or 8081, False

    say(f"  created config.json (bound to {SAFE_HOST})")
    return config.get("port") or 8081, True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else None
    port, created = ensure_config(path)
    print(f"PORT={port}")
    print(f"CREATED={1 if created else 0}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
