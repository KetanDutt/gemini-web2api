"""OpenAI ``response_format`` support: JSON mode and structured outputs.

The upstream Gemini web endpoint has no equivalent of OpenAI's
``response_format``, and nothing in this project can make that untrue. What a
proxy *can* do is the two things this module does:

1. **Tell the model what is wanted.** ``instruction_for()`` returns a precise
   instruction — and, when a schema was supplied, the schema itself — to append
   to the prompt. Without this, a client asking for JSON simply gets prose.
2. **Check the answer.** ``extract_json()`` finds a JSON value in the reply and
   ``validate()`` compares it against the schema. Nothing trusts the model's
   compliance, because nothing can.

The alternative to (2) is the failure this project has already been bitten by
elsewhere: returning something that *looks* like what was asked for. A client
that requested JSON and receives prose has no way to tell whether the upstream
ignored the instruction or the proxy did, so an unvalidated passthrough would
be lying about a guarantee it does not have. When the model does not comply,
``json_parse_failed`` / ``json_schema_violation`` say so.

**What is honestly not guaranteed.** OpenAI's ``json_schema`` mode is enforced
during generation, so its output cannot violate the schema. Here the constraint
is a request in a prompt, checked afterwards, so a violation is possible and is
reported rather than prevented. ``SCHEMA_KEYWORDS`` lists exactly which
keywords are enforced; a schema relying on any other keyword is *not* fully
validated, and the documentation says so rather than implying otherwise.
"""

import json
import re

TEXT = "text"
JSON_OBJECT = "json_object"
JSON_SCHEMA = "json_schema"

#: Recognised ``response_format.type`` values. ``text`` is OpenAI's default and
#: is accepted explicitly rather than rejected, because clients do send it.
KNOWN_TYPES = (TEXT, JSON_OBJECT, JSON_SCHEMA)

#: JSON Schema keywords this module enforces. Anything absent from this set is
#: not validated — see the module docstring. Kept as a module constant so the
#: documentation and the tests can both assert against the real capability
#: instead of a hand-copied list that drifts.
SCHEMA_KEYWORDS = (
    "type", "enum", "const", "required", "properties", "additionalProperties",
    "items", "minItems", "maxItems", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
)

#: Cap on reported violations. A wrong-shaped reply can violate a schema in
#: hundreds of places, and a 200-error message helps nobody.
MAX_ERRORS = 20

_JSON_TYPES = frozenset(
    ["object", "array", "string", "number", "integer", "boolean", "null"]
)

_INSTRUCTION_JSON_OBJECT = (
    "Respond with a single valid JSON object and nothing else. "
    "Do not wrap it in Markdown code fences. Do not add commentary, "
    "explanations or text before or after the JSON."
)

_INSTRUCTION_JSON_SCHEMA = (
    "Respond with a single valid JSON value and nothing else. "
    "Do not wrap it in Markdown code fences. Do not add commentary, "
    "explanations or text before or after the JSON. "
    "The value must validate against this JSON Schema, exactly:\n"
)


def parse(value):
    """Validate a client-supplied ``response_format``.

    Returns ``(spec, error)``. ``spec`` is ``None`` for text mode — the default
    and a no-op — or a dict describing the requested constraint. ``error`` is a
    message describing why the request was rejected, or ``None`` on success.

    ``value`` of ``None`` is accepted and means text mode: the parameter is
    optional, and clients that omit it must keep working unchanged.
    """
    if value is None:
        return None, None
    if not isinstance(value, dict):
        return None, "response_format must be an object"

    kind = value.get("type")
    if kind is None:
        # OpenAI treats a missing type as `text`. Being permissive here matches
        # that and cannot produce a wrong answer, because text is a no-op.
        return None, None
    if not isinstance(kind, str):
        return None, "response_format.type must be a string"
    if kind == TEXT:
        return None, None
    if kind == JSON_OBJECT:
        return {"kind": JSON_OBJECT}, None
    if kind != JSON_SCHEMA:
        return None, (
            f"response_format.type {kind!r} is not supported; expected one of "
            + ", ".join(repr(t) for t in KNOWN_TYPES)
        )

    # ── json_schema ──────────────────────────────────────────────────────────
    spec = value.get("json_schema")
    if not isinstance(spec, dict):
        return None, "response_format.json_schema must be an object"
    schema = spec.get("schema")
    if not isinstance(schema, dict):
        # `schema` is required by OpenAI. Accepting its absence would mean
        # silently downgrading to json_object, which is not what was asked for.
        return None, "response_format.json_schema.schema must be an object"
    name = spec.get("name")
    if name is not None and not isinstance(name, str):
        return None, "response_format.json_schema.name must be a string"
    return {"kind": JSON_SCHEMA, "schema": schema, "name": name or "response"}, None


def instruction_for(spec):
    """Return the prompt instruction for ``spec``, or ``""`` for text mode.

    Appended to the end of the prompt rather than inserted at the front: the
    conversation is flattened into one text block, and the most recent
    instruction is the one that governs, so putting it last is what makes it
    win against a conflicting request earlier in the conversation.
    """
    if not spec:
        return ""
    if spec["kind"] == JSON_OBJECT:
        return _INSTRUCTION_JSON_OBJECT
    return _INSTRUCTION_JSON_SCHEMA + json.dumps(
        spec["schema"], indent=2, ensure_ascii=False, sort_keys=True)


def extract_json(text):
    """Find a JSON value in ``text``.

    Returns ``(value, error)``. Models wrap JSON in code fences whatever the
    instruction says, and sometimes preface it with a sentence, so three
    strategies are tried in order of fidelity:

    1. the whole reply, stripped — the case the instruction asked for;
    2. the contents of any fenced block, because a fence is unambiguous;
    3. the first balanced ``{...}`` or ``[...]`` in the reply, which recovers a
       value embedded in surrounding prose.

    A candidate must parse *and* not be a bare number/string/bool/null when
    extracted from a fenced block or a balanced span, because those are far
    more likely to be prose that happens to be valid JSON — the word "null" or
    "1" appearing in a sentence would otherwise be accepted as the answer.
    """
    if not isinstance(text, str):
        return None, "model returned no text"
    stripped = text.strip()
    if not stripped:
        return None, "model returned an empty response"

    value, error = _try_load(stripped)
    if error is None:
        return value, None

    for body in _fenced_blocks(stripped):
        candidate, error = _try_load(body.strip())
        if error is None and isinstance(candidate, (dict, list)):
            return candidate, None

    for span in _balanced_spans(stripped):
        candidate, error = _try_load(span)
        if error is None and isinstance(candidate, (dict, list)):
            return candidate, None

    return None, f"model response is not valid JSON: {error}"


class _Violations(list):
    """The violations found, capped, with the true total attached.

    A ``list`` subclass so callers can treat it as one — ``validate(a, b) == []``
    still means "valid" — while ``total`` carries how many were found before the
    cap. Without it a reply violating a schema in 500 places would report only
    the 20 kept, understating the problem rather than reporting it.
    """

    def __init__(self, items=(), total=None):
        list.__init__(self, items)
        self.total = len(self) if total is None else total


def validate(spec, value):
    """Return the ways ``value`` violates ``spec``; empty means valid.

    A text-mode spec has no constraint, so nothing can violate it.
    """
    if not spec:
        return _Violations()
    if spec["kind"] == JSON_OBJECT:
        # `json_object` means a JSON *object*, not merely "some JSON". A bare
        # `"text"`, `[1, 2]`, `1` or `null` is valid JSON and not what was
        # asked for, so accepting it would report success for a non-compliant
        # reply — the failure mode this module exists to prevent.
        if not isinstance(value, dict):
            return _Violations(
                [f"$: expected object, got {_type_of(value)}"])
        return _Violations()
    counter = _ErrorCounter()
    _check(value, spec["schema"], "$", counter)
    return _Violations(counter.items, counter.total)


class _ErrorCounter:
    """Collects up to ``MAX_ERRORS`` messages and counts the rest."""

    def __init__(self):
        self.items = []
        self.total = 0

    def add(self, message):
        self.total += 1
        if len(self.items) < MAX_ERRORS:
            self.items.append(message)


def describe(errors):
    """Render validation errors as one readable sentence."""
    if not errors:
        return ""
    total = getattr(errors, "total", len(errors))
    message = "model response does not match the requested schema: " + "; ".join(errors)
    if total > len(errors):
        message += f" (and {total - len(errors)} more)"
    return message


# ─── internals ───────────────────────────────────────────────────────────────


def _try_load(text):
    """``json.loads`` without raising, so callers can try many candidates."""
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, str(exc)


def _fenced_blocks(text):
    """Yield the body of each ```-fenced block, in order.

    The info string is ignored rather than required to be ``json``: models
    label fences inconsistently, and the contents are parsed anyway, so a
    label mismatch costs nothing.
    """
    pattern = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\n(.*?)```", re.DOTALL)
    return pattern.findall(text)


def _balanced_spans(text):
    """Yield candidate JSON spans starting at each ``{`` or ``[``."""
    spans = []
    for index, char in enumerate(text):
        if char in "{[":
            span = _balanced_span(text, index)
            if span is not None:
                spans.append(span)
    return spans


def _balanced_span(text, start):
    """Return the JSON value starting at ``text[start]``, or None.

    Tracking the closing character is what makes this work for arrays: a
    scanner that assumed ``}`` would run to the end of the reply and return
    None on any top-level array.

    Not shared with ``tools._extract_balanced_json``, which deliberately accepts
    only objects — tool arguments must be an object — while JSON mode must also
    accept a top-level array.
    """
    opener = text[start]
    closer = "}" if opener == "{" else "]"
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
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _type_of(value):
    """JSON Schema's name for the type of a decoded Python value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        # JSON Schema counts 1.0 as an integer; a model emitting "1.0" for an
        # `integer` field is emitting a valid integer.
        return "integer" if value.is_integer() else "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _matches_type(value, expected):
    """Whether ``value`` satisfies the JSON Schema type name ``expected``.

    Delegates to ``_type_of`` rather than using ``isinstance`` against a map of
    Python types, because that map has two traps: ``bool`` is a subclass of
    ``int``, so ``isinstance(True, int)`` makes ``true`` a valid ``integer``;
    and a float with an integral value (``1.0``) is a valid JSON ``integer``.
    Naming the type once, in one place, is what keeps those two rules honest.
    An unrecognised type name constrains nothing.
    """
    if expected not in _JSON_TYPES:
        return True
    actual = _type_of(value)
    if expected == "number":
        return actual in ("number", "integer")
    return actual == expected


def _check(value, schema, path, errors, depth=0):
    """Record every way ``value`` violates ``schema`` on ``errors``.

    Walks the whole value even once the cap is reached: ``errors.add`` keeps
    counting without storing, so ``describe`` can report how many violations
    there really were. The walk is O(size of the reply), the same order as
    parsing it, so there is no depth or width where stopping early is worth
    reporting a number that is merely a lower bound.
    """
    if not isinstance(schema, dict):
        # A boolean schema (`true`/`false`) or a malformed one constrains
        # nothing this module claims to enforce; silently ignoring it beats
        # inventing a rule.
        return
    if depth > 32:
        return

    if "const" in schema and value != schema["const"]:
        errors.add(f"{path}: expected {schema['const']!r}")

    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        errors.add(f"{path}: {_preview(value)} is not one of "
                   f"{_preview(schema['enum'])}")

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        allowed = [t for t in allowed if isinstance(t, str)]
        if allowed and not any(_matches_type(value, t) for t in allowed):
            wanted = " or ".join(allowed)
            errors.add(f"{path}: expected {wanted}, got {_type_of(value)}")
            # Further keyword checks would report confusing follow-on errors
            # against the wrong type (e.g. minLength on a list).
            return

    if isinstance(value, dict):
        _check_object(value, schema, path, errors, depth)
    elif isinstance(value, list):
        _check_array(value, schema, path, errors, depth)
    elif isinstance(value, str):
        _check_string(value, schema, path, errors)
    elif _type_of(value) in ("number", "integer"):
        _check_number(value, schema, path, errors)


def _check_object(value, schema, path, errors, depth):
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in value:
                errors.add(f"{path}: missing required property {name!r}")

    additional = schema.get("additionalProperties")
    if additional is False:
        for name in value:
            if name not in properties:
                errors.add(f"{path}: unexpected property {name!r}")

    for name, subschema in properties.items():
        if name in value:
            _check(value[name], subschema, f"{path}.{name}", errors, depth + 1)

    if isinstance(additional, dict):
        for name in value:
            if name not in properties:
                _check(value[name], additional, f"{path}.{name}", errors, depth + 1)


def _check_array(value, schema, path, errors, depth):
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(value) < minimum:
        errors.add(f"{path}: expected at least {minimum} item(s), got {len(value)}")
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(value) > maximum:
        errors.add(f"{path}: expected at most {maximum} item(s), got {len(value)}")
    items = schema.get("items")
    if items is not None:
        for index, item in enumerate(value):
            _check(item, items, f"{path}[{index}]", errors, depth + 1)


def _check_string(value, schema, path, errors):
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and len(value) < minimum:
        errors.add(f"{path}: expected at least {minimum} character(s), got {len(value)}")
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and len(value) > maximum:
        errors.add(f"{path}: expected at most {maximum} character(s), got {len(value)}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value) is not None
        except re.error:
            # An invalid regex is the client's schema being wrong. Reporting it
            # as a model violation would blame the model for the client's typo.
            return
        if not matched:
            errors.add(f"{path}: {_preview(value)} does not match pattern {pattern!r}")


def _check_number(value, schema, path, errors):
    # Draft-06 and later define the exclusive bounds as numbers. Draft-04 used
    # them as booleans modifying `minimum`/`maximum`; the `isinstance` guard
    # ignores that older form rather than misreading `exclusiveMinimum: true`
    # as the bound 1. OpenAI's schemas use the modern form.
    checks = (
        ("minimum", lambda a, b: a < b, "at least"),
        ("maximum", lambda a, b: a > b, "at most"),
        ("exclusiveMinimum", lambda a, b: a <= b, "greater than"),
        ("exclusiveMaximum", lambda a, b: a >= b, "less than"),
    )
    for keyword, violates, phrase in checks:
        bound = schema.get(keyword)
        if (isinstance(bound, (int, float)) and not isinstance(bound, bool)
                and violates(value, bound)):
            errors.add(f"{path}: expected {phrase} {bound}, got {value}")


def _preview(value, limit=60):
    """A short, single-line rendering of a value for an error message."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."
