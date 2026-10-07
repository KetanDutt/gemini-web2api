"""Prompt construction and tool-call parsing.

Gemini Web has no function-calling contract on this endpoint, so tools are
described in the prompt and parsed back out of the reply. Two dialects exist:

* OpenAI (``/v1/chat/completions``, ``/v1/responses``) uses ``tool_call`` blocks
  with an ``arguments`` object.
* Google-native (``/v1beta/models/...``) uses ``function_call`` blocks with
  ``args``, which is what Gemini CLI expects.
"""
import base64
import binascii
import json
import re
import uuid
from urllib.parse import unquote_to_bytes

from .gemini import log

# Serialised tool schemas are inlined into the prompt. Above this size the
# parameter blocks are dropped (names and descriptions survive) so a client with
# a huge schema cannot crowd out the actual conversation.
MAX_TOOL_SCHEMA_CHARS = 30000

# Fences used to carry calls in and out of the prompt.
OPENAI_FENCE = "tool_call"
GOOGLE_FENCE = "function_call"


# ─── tool_choice ─────────────────────────────────────────────────────────────

def _build_tool_choice_instruction(tool_choice, tool_defs=None):
    """Translate OpenAI's ``tool_choice`` into a prompt constraint.

    Supported values: ``"none"``, ``"auto"``, ``"required"``, and
    ``{"type": "function", "function": {"name": ...}}``.
    """
    if tool_choice == "none":
        return "\n\nIMPORTANT: Do NOT call any tools. Respond with text only."
    if tool_choice == "required":
        return "\n\nIMPORTANT: You MUST call at least one tool. Do not respond with text only."
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function") or {}
        name = function.get("name") or tool_choice.get("name") or ""
        if name:
            return f'\n\nIMPORTANT: You MUST call the tool "{name}". Do not call other tools.'
    return ""


def normalize_tools(tools):
    """Flatten OpenAI/Responses tool declarations into a uniform list.

    ``/v1/responses`` sends tools as ``{"type": "function", "name": ...}``
    (flat) while chat completions sends ``{"type": "function", "function": {...}}``
    (nested). Both shapes are accepted here.
    """
    if not tools or not isinstance(tools, list):
        return []
    normalized = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = function.get("name") or tool.get("name") or ""
        if not name:
            continue
        normalized.append({
            "name": name,
            "description": function.get("description", tool.get("description", "")) or "",
            "parameters": function.get("parameters", tool.get("parameters", {})) or {},
        })
    return normalized


def _render_tool_schema(tool_defs):
    """Serialise tool definitions, trimming parameters when they are huge."""
    rendered = json.dumps(tool_defs, indent=2, ensure_ascii=False)
    if len(rendered) <= MAX_TOOL_SCHEMA_CHARS:
        return rendered
    slim = [{"name": t["name"], "description": t["description"]} for t in tool_defs]
    log(f"Tool schema too large ({len(rendered)} chars for {len(tool_defs)} tools); "
        "parameters stripped", "warning")
    return json.dumps(slim, indent=2, ensure_ascii=False)


def build_tool_prompt(tool_defs, fence=OPENAI_FENCE, arg_key="arguments"):
    """Build the tool-use section of the prompt."""
    schema = _render_tool_schema(tool_defs)
    example = json.dumps({"name": "func_name", arg_key: {}}, ensure_ascii=False)
    return (
        "# Tool Use\n\n"
        "You can call the following tools. Call format:\n"
        f"```{fence}\n{example}\n```\n"
        "When calling tools, output ONLY the fenced block(s) and nothing else.\n\n"
        f"Available tools:\n{schema}"
    )


# ─── Image parts ─────────────────────────────────────────────────────────────

def _decode_data_url(url):
    """Decode a ``data:`` URL into ``(bytes, mime)`` or None."""
    match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", url, re.DOTALL)
    if not match:
        return None
    mime = match.group(1) or "image/png"
    is_base64 = bool(match.group(2))
    data = match.group(3)
    try:
        if is_base64:
            return base64.b64decode(data, validate=True), mime
        return unquote_to_bytes(data), mime
    except (ValueError, TypeError, binascii.Error):
        return None


def _image_from_url(url, mime=None):
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        return _decode_data_url(url)
    # Remote URLs are returned as-is and downloaded by the upload stage.
    return url, mime or "image/png"


def _image_from_part(part):
    """Extract ``(bytes_or_url, mime)`` from any supported image part shape."""
    if not isinstance(part, dict):
        return None
    part_type = part.get("type")
    if part_type == "image_url":
        image_url = part.get("image_url", {})
        if isinstance(image_url, dict):
            return _image_from_url(image_url.get("url"), image_url.get("mime_type"))
        return _image_from_url(image_url)
    if part_type in ("input_image", "image"):
        image_url = part.get("image_url") or part.get("url")
        if isinstance(image_url, dict):
            return _image_from_url(image_url.get("url"), image_url.get("mime_type"))
        if image_url:
            return _image_from_url(image_url, part.get("mime_type"))
        image_data = part.get("data") or part.get("base64")
        if isinstance(image_data, str):
            mime = part.get("mime_type") or part.get("media_type") or "image/png"
            if image_data.startswith("data:"):
                return _decode_data_url(image_data)
            try:
                return base64.b64decode(image_data, validate=True), mime
            except (ValueError, TypeError, binascii.Error):
                return None
    return None


def _flatten_content(content):
    """Split a message ``content`` value into ``(text, images)``.

    Accepts a plain string, an OpenAI part list, or a Responses part list.
    """
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []

    texts, images = [], []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") in ("text", "input_text", "output_text"):
            texts.append(part.get("text", "") or "")
            continue
        image = _image_from_part(part)
        if image:
            images.append(image)
            texts.append("[Image attached]")
    return " ".join(t for t in texts if t is not None), images


# ─── OpenAI message conversion ───────────────────────────────────────────────

def messages_to_prompt(messages, tools=None, tool_choice=None):
    """Convert OpenAI messages to ``(prompt, images)``.

    ``images`` is a list of ``(bytes, mime)`` or ``(url, mime)`` tuples.

    Gemini Web is single-turn: there is no conversation identifier to continue,
    so multi-turn history is flattened into one prompt with role markers.
    """
    parts = []
    images = []

    tool_defs = normalize_tools(tools) if tool_choice != "none" else []
    if tool_defs:
        parts.append(build_tool_prompt(tool_defs) + _build_tool_choice_instruction(tool_choice, tool_defs))

    for message in messages or []:
        if isinstance(message, str):
            parts.append(message)
            continue
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        text, message_images = _flatten_content(message.get("content", ""))
        images.extend(message_images)

        if role == "system" or role == "developer":
            if text:
                parts.append(f"[System instruction]: {text}")
        elif role == "assistant":
            rendered = [f"[Assistant]: {text}"] if text else []
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                rendered.append(
                    f"```{OPENAI_FENCE}\n"
                    + json.dumps(
                        {"name": function.get("name"),
                         "arguments": _safe_json(function.get("arguments", "{}"))},
                        ensure_ascii=False)
                    + "\n```"
                )
            if rendered:
                parts.append("\n".join(rendered))
        elif role == "tool":
            name = message.get("name") or message.get("tool_call_id") or ""
            parts.append(f"[Tool result for {name}]: {text}")
        else:
            parts.append(text)

    return "\n\n".join(p for p in parts if p), images


def _safe_json(value):
    """Return a JSON-ready value from either a dict or a JSON string."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def parse_tool_calls(text):
    """Extract ``tool_call`` blocks. Returns ``(clean_text, tool_calls)``."""
    if not text:
        return text or "", []
    tool_calls = []
    pattern = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)
    clean_parts = []
    last_end = 0
    for match in pattern.finditer(text):
        clean_parts.append(text[last_end:match.start()])
        last_end = match.end()
        try:
            data = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("name"):
            continue
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": data["name"],
                "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False),
            },
        })
    clean_parts.append(text[last_end:])
    return "".join(clean_parts).strip(), tool_calls


# ─── Google-native conversion ────────────────────────────────────────────────

def _google_tool_choice_instruction(req):
    """Translate Google's ``toolConfig.functionCallingConfig`` into a constraint."""
    config = (req.get("toolConfig") or {}).get("functionCallingConfig") or {}
    mode = config.get("mode", "AUTO")
    allowed = config.get("allowedFunctionNames") or []

    if mode == "NONE":
        return "\n\nIMPORTANT: Do NOT call any tools. Respond with text only."
    if mode == "ANY":
        if allowed:
            names = ", ".join(f'"{name}"' for name in allowed)
            return f"\n\nIMPORTANT: You MUST call one of these tools: {names}. Do not respond with text only."
        return "\n\nIMPORTANT: You MUST call at least one tool. Do not respond with text only."
    if mode == "AUTO" and allowed:
        names = ", ".join(f'"{name}"' for name in allowed)
        return f"\n\nIMPORTANT: Only call these tools: {names}."
    return ""


def _google_tool_defs(req):
    """Extract functionDeclarations from a Google-native request."""
    config = (req.get("toolConfig") or {}).get("functionCallingConfig") or {}
    if config.get("mode", "AUTO") == "NONE":
        return []
    defs = []
    for group in req.get("tools") or []:
        if not isinstance(group, dict):
            continue
        for declaration in group.get("functionDeclarations") or []:
            if not isinstance(declaration, dict):
                continue
            entry = {
                "name": declaration.get("name", ""),
                "description": declaration.get("description", ""),
            }
            params = declaration.get("parameters") or declaration.get("parametersJsonSchema")
            if params:
                entry["parameters"] = params
            if entry["name"]:
                defs.append(entry)
    return defs


def google_contents_to_prompt(req):
    """Convert a Google-native request body to ``(prompt, images)``."""
    parts = []
    images = []

    tool_defs = _google_tool_defs(req)
    constraint = _google_tool_choice_instruction(req) if tool_defs else ""

    system = req.get("systemInstruction") or req.get("system_instruction")
    system_text = ""
    if isinstance(system, dict):
        system_text = " ".join(
            p.get("text", "") for p in (system.get("parts") or [])
            if isinstance(p, dict) and p.get("text")
        )
    elif isinstance(system, str):
        system_text = system

    if system_text and tool_defs:
        parts.append(system_text + "\n\n" + build_tool_prompt(tool_defs, GOOGLE_FENCE, "args") + constraint)
    elif system_text:
        parts.append(f"[System instruction]: {system_text}")
    elif tool_defs:
        parts.append(build_tool_prompt(tool_defs, GOOGLE_FENCE, "args") + constraint)

    for content in req.get("contents") or []:
        if not isinstance(content, dict):
            continue
        role = content.get("role", "user")
        message_parts = []
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("text"):
                message_parts.append(part["text"])
            elif part.get("inlineData") or part.get("inline_data"):
                data = part.get("inlineData") or part.get("inline_data") or {}
                try:
                    images.append((
                        base64.b64decode(data["data"], validate=True),
                        data.get("mimeType") or data.get("mime_type") or "image/png",
                    ))
                    message_parts.append("[Image attached]")
                except (KeyError, ValueError, TypeError, binascii.Error):
                    log("Skipped an undecodable inlineData image part", "debug")
            elif part.get("fileData") or part.get("file_data"):
                data = part.get("fileData") or part.get("file_data") or {}
                uri = data.get("fileUri") or data.get("file_uri")
                if uri:
                    images.append((uri, data.get("mimeType") or data.get("mime_type") or "image/png"))
                    message_parts.append("[Image attached]")
            elif part.get("functionCall") or part.get("function_call"):
                call = part.get("functionCall") or part.get("function_call") or {}
                message_parts.append(
                    f"```{GOOGLE_FENCE}\n"
                    + json.dumps({"name": call.get("name"), "args": call.get("args", {})},
                                 ensure_ascii=False)
                    + "\n```"
                )
            elif part.get("functionResponse") or part.get("function_response"):
                response = part.get("functionResponse") or part.get("function_response") or {}
                message_parts.append(
                    f"[Tool result for {response.get('name', '')}]: "
                    + json.dumps(response.get("response", {}), ensure_ascii=False)
                )
        text = "\n".join(p for p in message_parts if p)
        if not text:
            continue
        parts.append(f"[Assistant]: {text}" if role == "model" else text)

    return "\n\n".join(p for p in parts if p), images


def _extract_balanced_json(text, start):
    """Return the JSON object starting at ``text[start]``, or None.

    A regex like ``\\{[^`]*?\\}`` stops at the first ``}``, so it truncates any
    nested argument object — which is the common case
    (``{"name": "f", "args": {"city": "Tokyo"}}``). This scans for the matching
    brace instead, skipping over string literals.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def parse_google_function_calls(text):
    """Extract ``function_call`` blocks from model output.

    Accepts the fenced form, an unfenced ``function_call`` header, and a bare
    JSON object, because models drift between them.

    Returns ``(clean_text, [{"name": ..., "args": ...}])``.
    """
    if not text:
        return text or "", []

    calls = []

    def add(candidate):
        if not candidate:
            return
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(data, dict) and data.get("name"):
            calls.append({"name": data["name"],
                          "args": data.get("args", data.get("arguments", {}))})

    # 1. Fenced blocks — the documented format.
    fenced = re.compile(r"```function_call\s*\n(.*?)\n```", re.DOTALL)
    for match in fenced.findall(text):
        add(match.strip())
    clean = fenced.sub("", text)

    # 2. Unfenced "function_call" headers, with brace-balanced extraction so
    #    nested argument objects survive.
    header = re.compile(r"(?:^|\n)\s*function_call\s*\n?", re.IGNORECASE)
    while True:
        match = header.search(clean)
        if not match:
            break
        brace = clean.find("{", match.end())
        if brace < 0:
            break
        candidate = _extract_balanced_json(clean, brace)
        if candidate is None:
            break
        add(candidate)
        clean = clean[:match.start()] + clean[brace + len(candidate):]

    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

    # 3. A bare JSON object with no marker at all.
    if not calls:
        stripped = clean.strip()
        if stripped.startswith("{"):
            candidate = _extract_balanced_json(stripped, 0)
            if candidate:
                before = len(calls)
                add(candidate)
                if len(calls) > before and ("args" in candidate or "arguments" in candidate):
                    clean = ""

    return clean, calls
