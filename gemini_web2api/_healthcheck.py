"""Container liveness probe.

Run by the Dockerfile ``HEALTHCHECK``. Exits 0 when ``GET /health`` answers 200,
1 otherwise. Kept as a standalone script with no package imports so it works
even if the application itself is wedged, and so it does not need curl or wget
in the image.

    python -m gemini_web2api._healthcheck [--url URL] [--timeout SECONDS]
"""
import argparse
import os
import sys
import urllib.request


def probe(url, timeout):
    """Return True when the endpoint answers with a 2xx."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def default_url():
    host = os.environ.get("GEMINI_WEB2API_HEALTH_HOST", "127.0.0.1")
    port = os.environ.get("GEMINI_WEB2API_PORT") or os.environ.get("PORT") or "8081"
    return f"http://{host}:{port}/health"


def main(argv=None):
    parser = argparse.ArgumentParser(description="gemini-web2api health probe")
    parser.add_argument("--url", default=default_url())
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args(argv)
    return 0 if probe(args.url, args.timeout) else 1


if __name__ == "__main__":
    sys.exit(main())
