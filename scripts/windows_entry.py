"""PyInstaller entry script for the Windows executable.

Kept separate from the package so PyInstaller has a plain top-level script to
analyse. All behaviour lives in :mod:`gemini_web2api.windows`.
"""
import os
import sys

# Running from a source checkout, make the repository root importable.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gemini_web2api.windows import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
