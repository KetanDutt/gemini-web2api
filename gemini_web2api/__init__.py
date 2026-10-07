"""gemini-web2api: expose Google Gemini Web as an OpenAI-compatible API.

The package is the single source of truth for the implementation. The
top-level ``gemini_web2api.py`` script in the repository root is only a
compatibility entry point that calls :func:`gemini_web2api.__main__.main`.

Modules
-------
``config``      defaults, JSON file, environment and CLI layering
``models``      model table and ``@think=`` resolution
``gemini``      StreamGenerate protocol, cookies, build-tag refresh
``multimodal``  image fetching (SSRF-guarded) and Scotty upload
``tools``       prompt construction and tool-call parsing
``server``      HTTP endpoints (OpenAI, Google-native, status)
``webui``       self-contained status dashboard
``ratelimit``   fixed-window limiter
``metrics``     in-process counters and latency histogram
"""

__version__ = "1.2.0"

__all__ = ["__version__"]
