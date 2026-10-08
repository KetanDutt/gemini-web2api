"""Model definitions and mapping from the Gemini frontend JS source.

The Gemini web client selects a backend model through the ``MODE_CATEGORY`` enum
embedded in field ``[79]`` of the StreamGenerate payload:

    1 = FAST, 2 = THINKING, 3 = PRO, 4 = AUTO,
    5 = FAST_DYNAMIC_THINKING, 6 = FLASH_LITE

Thinking depth is field ``[17]``: ``0`` is the deepest, ``4`` the shallowest.
"""
from functools import lru_cache

MODE_CATEGORY = {
    1: "FAST",
    2: "THINKING",
    3: "PRO",
    4: "AUTO",
    5: "FAST_DYNAMIC_THINKING",
    6: "FLASH_LITE",
}

# Deepest (0) .. shallowest (4).
THINK_MIN = 0
THINK_MAX = 4

MODELS = {
    "gemini-3.7-flash": {
        "mode": 1, "think": 4,
        "desc": "Latest all-around model (Gemini 3.7 Flash)",
        "output": "~12k chars",
    },
    "gemini-3.6-flash": {
        "mode": 1, "think": 4,
        "desc": "All-around model (Gemini 3.6 Flash)",
        "output": "~12k chars",
    },
    "gemini-3.5-flash": {
        "mode": 1, "think": 4,
        "desc": "Alias for gemini-3.6-flash (backend upgraded)",
        "output": "~12k chars",
    },
    "gemini-3.5-flash-thinking": {
        "mode": 2, "think": 0,
        "desc": "Deep thinking mode, longest output (~20k chars)",
        "output": "~20k chars",
    },
    "gemini-3.1-pro": {
        "mode": 3, "think": 4,
        "desc": "Pro model (requires cookie for real routing)",
        "output": "~12k chars",
        "needs_cookie": True,
    },
    "gemini-3.1-pro-enhanced": {
        "mode": 3, "think": 4, "extra": {31: 2, 80: 3},
        "desc": "Pro with enhanced output (experimental)",
        "output": "~12k chars",
        "needs_cookie": True,
    },
    "gemini-auto": {
        "mode": 4, "think": 4,
        "desc": "Auto model selection",
        "output": "varies",
    },
    "gemini-3.5-flash-thinking-lite": {
        "mode": 5, "think": 0,
        "desc": "Dynamic thinking with adaptive depth",
        "output": "~15k chars",
    },
    "gemini-flash-lite": {
        "mode": 6, "think": 4,
        "desc": "Lightweight fast model",
        "output": "~10k chars",
    },
}

# Payload slots used by `extra`. Field 80 exists only because the outgoing
# payload is sized at 102 slots; the old monolith sized it at 80 and raised
# IndexError for gemini-3.1-pro-enhanced.
PAYLOAD_SLOTS = 102


def default_model():
    """The configured default model, falling back to the built-in default."""
    from .config import CONFIG, DEFAULT_CONFIG
    name = CONFIG.get("default_model") or DEFAULT_CONFIG["default_model"]
    return name if name in MODELS else DEFAULT_CONFIG["default_model"]


def resolve_model(model_name, default=None):
    """Resolve a model name to ``(name, mode_id, think_mode, error, extra)``.

    Accepts an optional ``@think=N`` suffix that overrides the model's default
    thinking depth (0 = deepest, 4 = shallowest).

    By default an unknown model name falls back to ``default`` rather than
    erroring, because upstream clients frequently probe with arbitrary model
    identifiers (``gpt-4``, ``claude-3``, ...). Set ``strict_models`` in the
    config to turn that into a hard error instead.
    """
    from .config import CONFIG

    if default is None:
        default = default_model()
    if not isinstance(model_name, str) or not model_name.strip():
        model_name = default
    model_name = model_name.strip()

    think_override = None
    if "@think=" in model_name:
        model_name, think_str = model_name.rsplit("@think=", 1)
        model_name = model_name.strip()
        try:
            think_override = int(think_str.strip())
        except (TypeError, ValueError):
            return None, None, None, f"Invalid think level: {think_str!r} (expected an integer)", None
        if not THINK_MIN <= think_override <= THINK_MAX:
            return (None, None, None,
                    f"Invalid think level: {think_override} (expected {THINK_MIN}..{THINK_MAX})", None)

    cfg = MODELS.get(model_name)
    if not cfg:
        if CONFIG.get("strict_models"):
            return None, None, None, f"Unknown model: {model_name}", None
        from .gemini import log
        log(f"Unknown model '{model_name}', falling back to '{default}'")
        model_name = default
        cfg = MODELS.get(default) or MODELS[default_model()]

    think_mode = think_override if think_override is not None else cfg["think"]
    return model_name, cfg["mode"], think_mode, None, cfg.get("extra")


def model_list():
    """Models in the shape OpenAI's ``GET /v1/models`` expects.

    Cached: the model table is static for the life of the process, and
    ``GET /v1/models`` is the endpoint clients poll most often — rebuilding
    nine dicts per call is pure waste on a hot path.
    """
    return _model_list()


@lru_cache(maxsize=1)
def _model_list():
    return [
        {
            "id": name,
            "object": "model",
            "created": 1700000000,
            "owned_by": "google",
            "description": cfg["desc"],
        }
        for name, cfg in MODELS.items()
    ]


def google_model_list():
    """Models in the shape Google's ``GET /v1beta/models`` expects.

    Cached for the same reason as :func:`model_list`.
    """
    return _google_model_list()


@lru_cache(maxsize=1)
def _google_model_list():
    return [
        {
            "name": f"models/{name}",
            "displayName": name,
            "description": cfg["desc"],
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        }
        for name, cfg in MODELS.items()
    ]


def google_model_detail(name):
    """One model in Google's ``GET /v1beta/models/{model}`` shape.

    Cached: model detail is a pure function of the static table.
    """
    return _google_model_detail(name)


@lru_cache(maxsize=64)
def _google_model_detail(name):
    cfg = MODELS.get(name)
    if not cfg:
        return None
    return {
        "name": f"models/{name}",
        "displayName": name,
        "description": cfg["desc"],
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
    }
