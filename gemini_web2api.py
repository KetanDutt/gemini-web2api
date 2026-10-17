#!/usr/bin/env python3
"""gemini-web2api — serve Google Gemini Web as an OpenAI-compatible API.

This file is a compatibility entry point. All of the implementation lives in the
``gemini_web2api`` package next to it; keeping the logic in one place prevents
the two copies from drifting apart (they had already diverged on build-tag
refresh, model support, tool_choice and streaming semantics).

Usage::

    python gemini_web2api.py [--port 8081] [--config config.json] [--help]

Equivalent to::

    python -m gemini_web2api
    gemini-web2api          # after `pip install .`

Quick start::

    pip install httpx        # optional; the stdlib transport streams too
    python gemini_web2api.py

Then point any OpenAI client at ``http://localhost:8081/v1``.
Open ``http://localhost:8081/`` in a browser for the status dashboard.
"""
import os
import sys

# Prefer the sibling source package when this shim is in a checkout. For a
# standalone copy beside an installed wheel, remove the script's directory from
# import search: this file has the same name as the package and would otherwise
# shadow the installed ``gemini_web2api`` package.
_HERE = os.path.dirname(os.path.realpath(__file__))
if os.path.isdir(os.path.join(_HERE, "gemini_web2api")):
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
else:
    sys.path[:] = [
        entry for entry in sys.path
        if os.path.realpath(os.path.abspath(entry or os.getcwd())) != _HERE
    ]

try:
    from gemini_web2api.__main__ import main
except ImportError as exc:  # pragma: no cover - only when the package is absent
    sys.stderr.write(
        "\n"
        "gemini-web2api could not start: the 'gemini_web2api' package is missing.\n"
        "\n"
        "This script is only an entry point — the implementation lives in the\n"
        f"'gemini_web2api/' package directory ({exc}).\n"
        "\n"
        "Fix it with either of:\n"
        "  1. keep this file inside the repository, next to gemini_web2api/\n"
        "  2. pip install gemini-web2api   (or: pip install . from the repo root)\n"
        "\n"
    )
    # `from None`: the diagnostic above is the whole point, and the ImportError
    # is already interpolated into it. A chained traceback would bury it.
    raise SystemExit(1) from None


if __name__ == "__main__":
    sys.exit(main())
