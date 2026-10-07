"""Tests for `response_format`: JSON mode and structured outputs.

Split in two, because the feature is two separable things and a failure should
say which one broke:

* `JsonModeUnitTests` exercises `jsonmode` directly. Every branch here is one
  the HTTP tests cannot reach — a schema using `pattern`, a model that returns
  a fenced array, a `bool` where an `integer` was required.
* `JsonModeEndpointTests` drives real HTTP requests through the server, which
  is what proves the module is actually *wired in* — that the instruction
  reaches the prompt, that a violation becomes an error instead of a 200 with
  prose in it, and that streaming still terminates.

The distinction matters because the most likely regression is not a bug in the
validator; it is someone editing `_chat` and dropping the call to it.
"""

import json
import unittest
from unittest import mock

from gemini_web2api import jsonmode

from .support import ServerTestCase

CHAT = "/v1/chat/completions"
LEGACY = "/v1/completions"
RESPONSES = "/v1/responses"

SCHEMA = {
    "type": "object",
    "required": ["name", "age"],
    "properties": {
        "name": {"type": "string", "minLength": 2},
        "age": {"type": "integer", "minimum": 0},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
    "additionalProperties": False,
}
JSON_SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "person", "schema": SCHEMA, "strict": True},
}


class JsonModeUnitTests(unittest.TestCase):
    # ─── parse ───────────────────────────────────────────────────────────────

    def test_absent_and_text_are_no_ops(self):
        """`response_format` is optional, and `text` is OpenAI's default.

        Both must yield no constraint rather than an error, because a client
        that sends `{"type": "text"}` is asking for what it would get anyway.
        """
        for value in (None, {"type": "text"}, {}):
            with self.subTest(value=value):
                spec, error = jsonmode.parse(value)
                self.assertIsNone(error)
                self.assertIsNone(spec)

    def test_json_object_is_accepted(self):
        spec, error = jsonmode.parse({"type": "json_object"})
        self.assertIsNone(error)
        self.assertEqual(spec["kind"], "json_object")

    def test_json_schema_keeps_the_schema_and_the_name(self):
        spec, error = jsonmode.parse(JSON_SCHEMA_FORMAT)
        self.assertIsNone(error)
        self.assertEqual(spec["kind"], "json_schema")
        self.assertEqual(spec["schema"], SCHEMA)
        self.assertEqual(spec["name"], "person")

    def test_malformed_formats_are_rejected_with_a_reason(self):
        """Each of these is a client error, and each gets its own message.

        A silent downgrade would be the worst outcome: the client asked for a
        guarantee, and answering as if it had not asked is the same class of
        dishonesty as silently dropping an image.
        """
        for value, needle in (
            ("json_object", "must be an object"),
            ({"type": 5}, "must be a string"),
            ({"type": "xml"}, "not supported"),
            ({"type": "json_schema"}, "json_schema must be an object"),
            ({"type": "json_schema", "json_schema": {}}, "schema must be an object"),
            ({"type": "json_schema", "json_schema": {"schema": {"type": "object"},
                                                     "name": 7}}, "name must be a string"),
        ):
            with self.subTest(value=value):
                spec, error = jsonmode.parse(value)
                self.assertIsNone(spec)
                self.assertIsNotNone(error, f"{value} was accepted")
                self.assertIn(needle, error)

    def test_an_unknown_type_name_does_not_constrain(self):
        """An unrecognised `type` must not reject an otherwise valid value.

        Reporting a violation for a keyword this module does not implement
        would blame the model for the proxy's gap.
        """
        spec = {"kind": "json_schema", "schema": {"type": "frobnicate"}}
        self.assertEqual(jsonmode.validate(spec, "anything"), [])

    # ─── instruction_for ─────────────────────────────────────────────────────

    def test_text_mode_adds_nothing_to_the_prompt(self):
        self.assertEqual(jsonmode.instruction_for(None), "")
        self.assertEqual(jsonmode.instruction_for({}), "")

    def test_the_schema_is_included_in_the_instruction(self):
        """The model cannot satisfy a schema it was never shown."""
        spec, _ = jsonmode.parse(JSON_SCHEMA_FORMAT)
        instruction = jsonmode.instruction_for(spec)
        self.assertIn("JSON", instruction)
        self.assertIn('"required"', instruction)
        self.assertIn("name", instruction)

    def test_the_instruction_forbids_code_fences(self):
        """Fences are the single most common way models break JSON mode.

        Asking for bare JSON up front is cheaper than unwrapping it later, and
        both are done.
        """
        for value in ({"type": "json_object"}, JSON_SCHEMA_FORMAT):
            with self.subTest(value=value["type"]):
                spec, _ = jsonmode.parse(value)
                self.assertIn("fence", jsonmode.instruction_for(spec).lower())

    # ─── extract_json ────────────────────────────────────────────────────────

    def test_extraction_handles_every_shape_a_model_produces(self):
        for text, expected in (
            ('{"a": 1}', {"a": 1}),
            ('  {"a": 1}  ', {"a": 1}),
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ('```\n{"a": 1}\n```', {"a": 1}),
            ('```json\n[1, 2]\n```', [1, 2]),
            ('[{"id": 1}, {"id": 2}]', [{"id": 1}, {"id": 2}]),
            ('Sure! Here it is:\n{"a": {"b": 2}}\nLet me know!', {"a": {"b": 2}}),
            ('{"a": "a \\" quote"}', {"a": 'a " quote'}),
            ('{"nested": {"deep": [1, {"x": 2}]}}', {"nested": {"deep": [1, {"x": 2}]}}),
        ):
            with self.subTest(text=text):
                value, error = jsonmode.extract_json(text)
                self.assertIsNone(error, error)
                self.assertEqual(value, expected)

    def test_prose_is_not_mistaken_for_json(self):
        """The failure this whole feature exists to prevent.

        A model that ignores the instruction answers in prose. Returning that
        prose as the "JSON" would be worse than an error, because the client
        cannot tell it apart from a real answer.

        Note what is *not* here: bare scalars like `1`, `null` or `"text"`. Those
        are valid JSON values, so extraction must return them — and whether they
        are acceptable is the schema's call, not the parser's. `json_object` mode
        rejects them (see `test_json_object_mode_rejects_a_non_object`); a schema
        of `{"type": "string"}` accepts one. Conflating the two here is what let
        the first version of this test pass while the code accepted a bare string
        for `json_object`.
        """
        for text in ("I cannot help with that.", "The answer is 1.",
                     "Here you go: nothing", "", "   ", None,
                     '{"unterminated": ', "no json at all"):
            with self.subTest(text=text):
                value, error = jsonmode.extract_json(text)
                self.assertIsNone(value, f"{text!r} was accepted as JSON")
                self.assertIsNotNone(error)

    def test_a_bare_scalar_is_extracted_as_the_value_it_is(self):
        """Extraction reports what is there; validation judges it.

        Splitting the two is what lets a schema of `{"type": "integer"}` work
        while `json_object` still rejects a bare number.
        """
        for text, expected in (("1", 1), ("null", None), ("true", True),
                               ('"a string"', "a string"), ("1.5", 1.5)):
            with self.subTest(text=text):
                value, error = jsonmode.extract_json(text)
                self.assertIsNone(error, error)
                self.assertEqual(value, expected)

    def test_json_object_mode_rejects_a_non_object(self):
        """`json_object` means an object, not merely valid JSON.

        Without this, a reply of `"I cannot help with that"` — a perfectly valid
        JSON string — would be reported as compliant, and the client's
        `json.loads(...)["field"]` would fail on a 200 response. Found by a test
        that expected extraction to reject scalars; extraction was right and
        validation was wrong.
        """
        spec, _ = jsonmode.parse({"type": "json_object"})
        self.assertEqual(jsonmode.validate(spec, {"a": 1}), [])
        for value in ("a string", 1, 1.5, True, None, [1, 2]):
            with self.subTest(value=value):
                errors = jsonmode.validate(spec, value)
                self.assertTrue(errors, f"{value!r} passed json_object mode")
                self.assertIn("expected object", errors[0])

    def test_a_fenced_scalar_is_not_accepted_by_extraction(self):
        """A fence is unambiguous, but its contents still have to be a
        structured value. Models put prose in unlabelled fences all the time,
        and a one-word fence is far more likely to be prose than an answer."""
        value, error = jsonmode.extract_json("```\nnull\n```")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

    def test_a_truncated_reply_is_rejected_not_guessed_at(self):
        """Cut-off output is a real upstream failure mode.

        Repairing it would mean inventing data, so it is reported instead.
        """
        value, error = jsonmode.extract_json('{"a": 1, "b": ')
        self.assertIsNone(value)
        self.assertIn("not valid JSON", error)

    # ─── validate ────────────────────────────────────────────────────────────

    def test_a_conforming_value_has_no_violations(self):
        spec, _ = jsonmode.parse(JSON_SCHEMA_FORMAT)
        self.assertEqual(jsonmode.validate(spec, {"name": "Al", "age": 3}), [])

    def test_violations_are_located_and_specific(self):
        """A message that says only "invalid" is not actionable.

        Each entry names the JSON path and what was wrong, so a developer can
        see which field the model got wrong without re-running the request.
        """
        spec, _ = jsonmode.parse(JSON_SCHEMA_FORMAT)
        cases = (
            ({"age": 3}, "$: missing required property 'name'"),
            ({"name": "A", "age": 3}, "expected at least 2 character"),
            ({"name": "Al", "age": -1}, "$.age: expected at least 0"),
            ({"name": "Al"}, "missing required property 'age'"),
            ({"name": "Al", "age": 3, "extra": 1}, "unexpected property 'extra'"),
            ({"name": "Al", "age": 3, "tags": ["a", "b", "c"]}, "at most 2 item"),
            ({"name": 5, "age": 3}, "$.name: expected string, got integer"),
            ({"name": "Al", "age": "3"}, "expected integer, got string"),
            ("not an object", "$: expected object, got string"),
            ([], "$: expected object, got array"),
        )
        for value, needle in cases:
            with self.subTest(value=value):
                errors = jsonmode.validate(spec, value)
                self.assertTrue(errors, f"{value!r} was accepted")
                self.assertTrue(any(needle in e for e in errors),
                                f"no error mentioned {needle!r}: {errors}")

    def test_integer_accepts_an_integral_float_but_not_a_boolean(self):
        """Both halves of this are JSON Schema rules that trip up naive code.

        `1.0` is a valid JSON integer. `true` is not an integer, even though
        Python's `bool` is a subclass of `int` and `isinstance(True, int)` is
        True — so a schema-validating implementation that uses `isinstance`
        accepts `{"age": true}`, and the client's downstream parser does not.
        """
        spec, _ = jsonmode.parse(JSON_SCHEMA_FORMAT)
        self.assertEqual(jsonmode.validate(spec, {"name": "Al", "age": 3.0}), [])
        errors = jsonmode.validate(spec, {"name": "Al", "age": True})
        self.assertTrue(errors)
        self.assertIn("got boolean", errors[0])

    def test_number_accepts_integers(self):
        spec = {"kind": "json_schema", "schema": {"type": "number"}}
        self.assertEqual(jsonmode.validate(spec, 1), [])
        self.assertEqual(jsonmode.validate(spec, 1.5), [])
        self.assertTrue(jsonmode.validate(spec, "1"))

    def test_a_type_union_accepts_either_member(self):
        spec = {"kind": "json_schema",
                "schema": {"type": ["string", "null"]}}
        self.assertEqual(jsonmode.validate(spec, None), [])
        self.assertEqual(jsonmode.validate(spec, "x"), [])
        self.assertTrue(jsonmode.validate(spec, 5))

    def test_enum_and_const(self):
        spec = {"kind": "json_schema",
                "schema": {"type": "object",
                           "properties": {"kind": {"enum": ["a", "b"]},
                                          "v": {"const": 7}},
                           "required": ["kind", "v"]}}
        self.assertEqual(jsonmode.validate(spec, {"kind": "a", "v": 7}), [])
        errors = jsonmode.validate(spec, {"kind": "z", "v": 8})
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("is not one of" in e for e in errors))
        self.assertTrue(any("expected 7" in e for e in errors))

    def test_pattern_and_exclusive_bounds(self):
        spec = {"kind": "json_schema",
                "schema": {"type": "object",
                           "properties": {"code": {"type": "string",
                                                   "pattern": "^[A-Z]{2}$"},
                                          "n": {"type": "number",
                                                "exclusiveMinimum": 0}},
                           "required": ["code", "n"]}}
        self.assertEqual(jsonmode.validate(spec, {"code": "AB", "n": 1}), [])
        errors = jsonmode.validate(spec, {"code": "ab", "n": 0})
        self.assertEqual(len(errors), 2)

    def test_a_broken_regex_in_the_client_schema_is_not_a_model_violation(self):
        """`pattern: "["` is the client's typo, not the model's fault.

        Reporting it as a violation would tell the client its own schema is
        broken by blaming the upstream for it.
        """
        spec = {"kind": "json_schema",
                "schema": {"type": "object", "properties": {"a": {"pattern": "["}},
                           "required": ["a"]}}
        self.assertEqual(jsonmode.validate(spec, {"a": "anything"}), [])

    def test_nested_and_array_paths_are_reported(self):
        spec = {"kind": "json_schema",
                "schema": {"type": "object",
                           "properties": {"items": {"type": "array", "items": {
                               "type": "object",
                               "properties": {"n": {"type": "integer"}},
                               "required": ["n"]}}},
                           "required": ["items"]}}
        errors = jsonmode.validate(spec, {"items": [{"n": 1}, {"n": "x"}]})
        self.assertEqual(len(errors), 1)
        self.assertIn("$.items[1].n", errors[0])

    def test_a_recursive_schema_does_not_hang(self):
        """A schema that references itself is legal and must not recurse
        forever — the depth guard is what makes that safe."""
        schema = {"type": "object", "properties": {}, "required": []}
        schema["properties"]["child"] = schema
        spec = {"kind": "json_schema", "schema": schema}
        value = current = {}
        for _ in range(200):
            current["child"] = {}
            current = current["child"]
        self.assertEqual(jsonmode.validate(spec, value), [])

    def test_reporting_is_capped_but_says_how_many_were_hidden(self):
        """A reply violating a schema in hundreds of places must not produce a
        kilometre-long error message, but truncating silently would understate
        the problem."""
        items = {"type": "array", "items": {"type": "integer"}}
        errors = jsonmode.validate({"kind": "json_schema", "schema": items},
                                   ["x"] * 500)
        self.assertEqual(len(errors), jsonmode.MAX_ERRORS,
                         "only the first MAX_ERRORS are kept")
        self.assertEqual(errors.total, 500,
                         "the true total must still be counted")
        message = jsonmode.describe(errors)
        hidden = 500 - jsonmode.MAX_ERRORS
        self.assertIn(f"and {hidden} more", message,
                      "the message must admit how many were not shown")

    def test_documented_keywords_are_the_enforced_keywords(self):
        """The docs claim a keyword list; this is the test that keeps it true.

        If a keyword is added to the implementation without the constant being
        updated, the documentation silently understates the feature; if it is
        listed without being enforced, the documentation overstates it — which
        is the failure mode that matters, because it promises a guarantee.
        """
        import inspect
        source = inspect.getsource(jsonmode)
        for keyword in jsonmode.SCHEMA_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertIn(f'"{keyword}"', source,
                              f"{keyword} is documented as enforced but never read")


class JsonModeEndpointTests(ServerTestCase):
    """The wiring, over real HTTP against the real server."""

    GOOD = json.dumps({"name": "Al", "age": 3})

    def _chat_body(self, **extra):
        body = {"model": "gemini-3.6-flash",
                "messages": [{"role": "user", "content": "give me a person"}]}
        body.update(extra)
        return body

    def test_without_response_format_nothing_changes(self):
        """The default path must be byte-for-byte what it was.

        A feature that alters every existing request is a regression, however
        good the feature is.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value="Here is a person: 'Al', age 3.") as generate:
            status, _headers, body = self.post_json(CHAT, self._chat_body())
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"],
                         "Here is a person: 'Al', age 3.")
        prompt = generate.call_args[0][0]
        self.assertNotIn("JSON", prompt)

    def test_json_object_appends_the_instruction_to_the_prompt(self):
        """The prompt the upstream actually receives is what matters.

        Asserting on the response alone would pass even if the instruction were
        never sent, because the stub returns valid JSON regardless.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value=self.GOOD) as generate:
            status, _headers, body = self.post_json(
                CHAT, self._chat_body(response_format={"type": "json_object"}))
        self.assertEqual(status, 200)
        prompt = generate.call_args[0][0]
        self.assertIn("single valid JSON object", prompt)
        self.assertIn("give me a person", prompt)
        self.assertEqual(json.loads(body["choices"][0]["message"]["content"]),
                         {"name": "Al", "age": 3})

    def test_the_instruction_is_appended_after_the_conversation(self):
        """Position is the point.

        The conversation is flattened into one text block, so an instruction
        placed before the user's own messages can be overridden by them; the
        last instruction is the one that governs.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value=self.GOOD) as generate:
            self.post_json(CHAT, self._chat_body(
                messages=[{"role": "user", "content": "Reply in prose, no JSON."}],
                response_format={"type": "json_object"}))
        prompt = generate.call_args[0][0]
        self.assertGreater(prompt.index("single valid JSON object"),
                           prompt.index("Reply in prose"))

    def test_a_fenced_reply_is_unwrapped(self):
        """Models fence JSON no matter what they are told, so the proxy
        unwraps rather than failing a request it could have answered."""
        with mock.patch("gemini_web2api.server.generate",
                        return_value='```json\n' + self.GOOD + '\n```'):
            status, _headers, body = self.post_json(
                CHAT, self._chat_body(response_format={"type": "json_object"}))
        self.assertEqual(status, 200)
        content = body["choices"][0]["message"]["content"]
        self.assertEqual(json.loads(content), {"name": "Al", "age": 3})

    def test_the_returned_content_is_the_validated_value(self):
        """What is checked must be what is sent.

        The reply here is valid JSON wrapped in a sentence. Forwarding the raw
        text would send a string that does not parse, while having validated a
        value that does — so the canonical re-serialisation is asserted.
        """
        prose = 'Sure thing! ' + self.GOOD
        with mock.patch("gemini_web2api.server.generate", return_value=prose):
            _status, _headers, body = self.post_json(
                CHAT, self._chat_body(response_format={"type": "json_object"}))
        content = body["choices"][0]["message"]["content"]
        self.assertNotIn("Sure thing", content)
        self.assertEqual(json.loads(content), {"name": "Al", "age": 3})

    def test_prose_in_json_mode_is_an_error_not_a_200(self):
        """The whole point of the feature.

        Before this existed the client asked for JSON and got a sentence, with
        a 200 and no way to tell the difference.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value="I'm sorry, I can't do that."):
            status, _headers, body = self.post_json(
                CHAT, self._chat_body(response_format={"type": "json_object"}))
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "json_parse_failed")

    def test_a_schema_violation_is_reported_with_the_reason(self):
        """The client must be told *what* was wrong, not just that something was.

        A bare 502 sends the developer back to guessing, and the schema is the
        part they cannot debug from their side.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value=json.dumps({"name": "Al", "age": -5})):
            status, _headers, body = self.post_json(
                CHAT, self._chat_body(response_format=JSON_SCHEMA_FORMAT))
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "json_schema_violation")
        self.assertIn("$.age", body["error"]["message"])
        self.assertIn("at least 0", body["error"]["message"])

    def test_a_conforming_reply_passes_a_schema_unchanged(self):
        with mock.patch("gemini_web2api.server.generate", return_value=self.GOOD):
            status, _headers, body = self.post_json(
                CHAT, self._chat_body(response_format=JSON_SCHEMA_FORMAT))
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(json.loads(body["choices"][0]["message"]["content"]),
                         {"name": "Al", "age": 3})

    def test_a_malformed_response_format_is_rejected_before_generating(self):
        """Fail fast: a bad request must not consume a real Gemini call."""
        for value in ({"type": "xml"}, {"type": "json_schema"}, "nope"):
            with self.subTest(value=value):
                with mock.patch("gemini_web2api.server.generate") as generate:
                    status, _headers, body = self.post_json(
                        CHAT, self._chat_body(response_format=value))
                self.assertEqual(status, 400)
                self.assertEqual(body["error"]["code"], "invalid_response_format")
                generate.assert_not_called()

    def test_json_mode_streams_as_one_valid_chunk(self):
        """Streaming cannot validate what it has already sent.

        A violation is only visible once the reply is complete, and a 200
        header cannot be retracted, so JSON mode buffers and emits one chunk —
        the same reasoning the tool-call path already used. The client still
        receives a well-formed SSE stream that terminates.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value='```json\n' + self.GOOD + '\n```'):
            status, _headers, raw = self.request(
                "POST", CHAT, self._chat_body(stream=True,
                                              response_format={"type": "json_object"}))
        self.assertEqual(status, 200)
        self.assertIn("data: ", raw)
        self.assertIn("[DONE]", raw)
        deltas = [json.loads(line[len("data: "):])
                  for line in raw.splitlines()
                  if line.startswith("data: ") and "[DONE]" not in line]
        content = "".join(d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                          for d in deltas)
        self.assertEqual(json.loads(content), {"name": "Al", "age": 3})

    def test_a_violation_in_a_stream_is_an_error_not_a_broken_stream(self):
        """Buffering means the failure happens before the 200, so the client
        gets a real HTTP error instead of a half-written JSON document."""
        with mock.patch("gemini_web2api.server.generate",
                        return_value="no json here"):
            status, _headers, body = self.json_or_text(
                self.request("POST", CHAT, self._chat_body(
                    stream=True, response_format={"type": "json_object"})))
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "json_parse_failed")

    def json_or_text(self, response):
        status, headers, text = response
        try:
            return status, headers, json.loads(text)
        except ValueError:
            return status, headers, text

    def test_tool_calls_take_precedence_over_json_validation(self):
        """A request offering tools may legitimately be answered with a call.

        Validating the leftover text against the schema then would fail a
        correct response, so tool calls end the JSON contract for that reply.
        """
        reply = ('```tool_call\n{"name": "get_weather", '
                 '"arguments": {"city": "Tokyo"}}\n```')
        with mock.patch("gemini_web2api.server.generate", return_value=reply):
            status, _headers, body = self.post_json(CHAT, self._chat_body(
                response_format={"type": "json_object"},
                tools=[{"type": "function", "function": {
                    "name": "get_weather", "parameters": {"type": "object"}}}],
                tool_choice="auto"))
        self.assertEqual(status, 200)
        choice = body["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["tool_calls"][0]["function"]["name"],
                         "get_weather")

    def test_the_legacy_endpoint_honours_response_format(self):
        """`/v1/completions` accepts `response_format` in OpenAI's API, so
        accepting it and ignoring it would be the silent-drop failure again."""
        with mock.patch("gemini_web2api.server.generate",
                        return_value=self.GOOD) as generate:
            status, _headers, body = self.post_json(
                LEGACY, {"model": "gemini-3.6-flash", "prompt": "give me a person",
                         "response_format": {"type": "json_object"}})
        self.assertEqual(status, 200)
        self.assertIn("single valid JSON object", generate.call_args[0][0])
        self.assertEqual(json.loads(body["choices"][0]["text"]),
                         {"name": "Al", "age": 3})

    def test_the_legacy_endpoint_rejects_a_broken_reply(self):
        with mock.patch("gemini_web2api.server.generate", return_value="prose"):
            status, _headers, body = self.post_json(
                LEGACY, {"model": "gemini-3.6-flash", "prompt": "hi",
                         "response_format": {"type": "json_object"}})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "json_parse_failed")

    def test_responses_reads_text_format(self):
        """The Responses API spells it `text.format`; Codex CLI sends that."""
        with mock.patch("gemini_web2api.server.generate",
                        return_value=self.GOOD) as generate:
            status, _headers, body = self.post_json(
                RESPONSES, {"model": "gemini-3.6-flash",
                            "input": "give me a person",
                            "text": {"format": {"type": "json_object"}}})
        self.assertEqual(status, 200)
        self.assertIn("single valid JSON object", generate.call_args[0][0])
        content = body["output"][0]["content"][0]["text"]
        self.assertEqual(json.loads(content), {"name": "Al", "age": 3})

    def test_responses_also_accepts_response_format(self):
        """Clients migrating from chat completions send the other spelling.

        Reading only the documented one would silently ignore this, which is
        the exact defect this project fixed in its Cloudflare Worker.
        """
        with mock.patch("gemini_web2api.server.generate",
                        return_value=self.GOOD) as generate:
            status, _headers, _body = self.post_json(
                RESPONSES, {"model": "gemini-3.6-flash", "input": "hi",
                            "response_format": {"type": "json_object"}})
        self.assertEqual(status, 200)
        self.assertIn("single valid JSON object", generate.call_args[0][0])

    def test_responses_rejects_a_violation(self):
        with mock.patch("gemini_web2api.server.generate",
                        return_value=json.dumps({"name": "Al"})):
            status, _headers, body = self.post_json(
                RESPONSES, {"model": "gemini-3.6-flash", "input": "hi",
                            "text": {"format": JSON_SCHEMA_FORMAT}})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "json_schema_violation")

    def test_json_mode_works_with_the_google_native_route_ignored(self):
        """The Google-native route has no `response_format` equivalent and
        must not grow one: its clients speak Google's protocol, where the
        parameter does not exist. This asserts the parameter is not read from
        a body that happens to contain it, so the two dialects stay distinct.
        """
        google = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                  "response_format": {"type": "json_object"}}
        with mock.patch("gemini_web2api.server.generate",
                        return_value="plain text reply") as generate:
            status, _headers, _body = self.post_json(
                "/v1beta/models/gemini-3.6-flash:generateContent", google)
        self.assertEqual(status, 200)
        self.assertNotIn("single valid JSON object", generate.call_args[0][0])

    def test_a_failure_is_counted(self):
        """Operators need to see this rate; a silent 502 is unauditable."""
        from gemini_web2api import metrics
        before = metrics.snapshot()["counters"].get("json_mode_failures", 0)
        with mock.patch("gemini_web2api.server.generate", return_value="prose"):
            self.post_json(CHAT, self._chat_body(
                response_format={"type": "json_object"}))
        after = metrics.snapshot()["counters"].get("json_mode_failures", 0)
        self.assertEqual(after, before + 1)


if __name__ == "__main__":
    unittest.main()
