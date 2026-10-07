"""Prompt construction and tool-call parsing for both API dialects."""
import base64
import json
import unittest

from gemini_web2api.tools import (
    MAX_TOOL_SCHEMA_CHARS,
    build_tool_prompt,
    google_contents_to_prompt,
    messages_to_prompt,
    normalize_tools,
    parse_google_function_calls,
    parse_tool_calls,
)
from tests.support import ConfigTestCase

IMAGE_B64 = base64.b64encode(b"fake png").decode()


class NormalizeToolsTests(unittest.TestCase):
    def test_openai_nested_shape(self):
        tools = [{"type": "function", "function": {
            "name": "get_weather", "description": "Weather", "parameters": {"type": "object"}}}]
        self.assertEqual(normalize_tools(tools)[0]["name"], "get_weather")

    def test_responses_flat_shape(self):
        tools = [{"type": "function", "name": "get_weather", "description": "Weather",
                  "parameters": {"type": "object"}}]
        normalized = normalize_tools(tools)
        self.assertEqual(normalized[0]["name"], "get_weather")
        self.assertEqual(normalized[0]["parameters"], {"type": "object"})

    def test_bare_function_shape(self):
        self.assertEqual(normalize_tools([{"name": "f"}])[0]["name"], "f")

    def test_entries_without_a_name_are_dropped(self):
        self.assertEqual(normalize_tools([{"type": "function"}, "junk", None, 42]), [])

    def test_empty_and_none(self):
        self.assertEqual(normalize_tools(None), [])
        self.assertEqual(normalize_tools([]), [])

    def test_non_function_tool_types_are_still_named(self):
        normalized = normalize_tools([{"type": "web_search", "name": "search"}])
        self.assertEqual(len(normalized), 1)


class OpenAIMessageTests(unittest.TestCase):
    def test_plain_string_content(self):
        prompt, images = messages_to_prompt([{"role": "user", "content": "hello"}])
        self.assertEqual(prompt, "hello")
        self.assertEqual(images, [])

    def test_system_role_is_labelled(self):
        prompt, _ = messages_to_prompt([{"role": "system", "content": "be terse"}])
        self.assertEqual(prompt, "[System instruction]: be terse")

    def test_developer_role_is_treated_as_system(self):
        prompt, _ = messages_to_prompt([{"role": "developer", "content": "be terse"}])
        self.assertIn("[System instruction]: be terse", prompt)

    def test_assistant_role_is_labelled(self):
        prompt, _ = messages_to_prompt([{"role": "assistant", "content": "prior answer"}])
        self.assertEqual(prompt, "[Assistant]: prior answer")

    def test_tool_result_is_labelled(self):
        prompt, _ = messages_to_prompt([
            {"role": "tool", "name": "get_weather", "content": '{"temp": 21}'}])
        self.assertEqual(prompt, '[Tool result for get_weather]: {"temp": 21}')

    def test_tool_result_falls_back_to_the_call_id(self):
        prompt, _ = messages_to_prompt([
            {"role": "tool", "tool_call_id": "call_9", "content": "ok"}])
        self.assertIn("call_9", prompt)

    def test_multi_turn_history_is_flattened_in_order(self):
        prompt, _ = messages_to_prompt([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ])
        self.assertEqual(prompt.split("\n\n"),
                         ["[System instruction]: sys", "one", "[Assistant]: two", "three"])

    def test_content_parts_are_joined_with_spaces(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMAGE_B64}"}},
            ],
        }])
        self.assertEqual(prompt, "Describe [Image attached]")
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_output_text_parts_are_accepted(self):
        prompt, _ = messages_to_prompt([{
            "role": "user", "content": [{"type": "output_text", "text": "from responses"}]}])
        self.assertEqual(prompt, "from responses")

    def test_remote_image_url_is_passed_through_for_later_fetch(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "input_image", "image_url": "https://example.com/image.png"},
            ],
        }])
        self.assertEqual(prompt, "Describe [Image attached]")
        self.assertEqual(images, [("https://example.com/image.png", "image/png")])

    def test_malformed_data_url_is_skipped(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,%%%"}},
            ],
        }])
        self.assertEqual(prompt, "Describe")
        self.assertEqual(images, [])

    def test_raw_base64_field_is_decoded(self):
        _prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [{"type": "input_image", "data": IMAGE_B64, "mime_type": "image/png"}],
        }])
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_url_encoded_data_url_is_decoded(self):
        _prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:text/plain,hello%20world"}}],
        }])
        self.assertEqual(images, [(b"hello world", "text/plain")])

    def test_assistant_tool_calls_round_trip(self):
        """A history entry must re-render in the same format we parse."""
        messages = [{"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'},
        }]}]
        prompt, _ = messages_to_prompt(messages)
        self.assertIn("```tool_call", prompt)
        _clean, calls = parse_tool_calls(prompt)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"city": "Tokyo"})

    def test_assistant_tool_calls_with_dict_arguments_round_trip(self):
        messages = [{"role": "assistant", "content": "", "tool_calls": [{
            "function": {"name": "f", "arguments": {"a": 1}},
        }]}]
        prompt, _ = messages_to_prompt(messages)
        _clean, calls = parse_tool_calls(prompt)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"a": 1})

    def test_empty_and_missing_content_do_not_crash(self):
        for messages in ([], [{"role": "user"}], [{"role": "user", "content": None}],
                         ["a bare string"], [42]):
            with self.subTest(messages=messages):
                prompt, images = messages_to_prompt(messages)
                self.assertIsInstance(prompt, str)
                self.assertEqual(images, [])

    def test_messages_may_be_none(self):
        self.assertEqual(messages_to_prompt(None), ("", []))


class ToolChoiceTests(unittest.TestCase):
    TOOLS = [{"type": "function", "function": {"name": "get_weather", "description": "w",
                                               "parameters": {"type": "object"}}}]

    def test_none_suppresses_the_tool_block(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}],
                                       self.TOOLS, tool_choice="none")
        self.assertNotIn("Tool Use", prompt)

    def test_auto_includes_the_tool_block(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}],
                                       self.TOOLS, tool_choice="auto")
        self.assertIn("Tool Use", prompt)
        self.assertIn("get_weather", prompt)
        self.assertNotIn("MUST call", prompt)

    def test_required_forces_a_call(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}],
                                       self.TOOLS, tool_choice="required")
        self.assertIn("MUST call at least one tool", prompt)

    def test_named_tool_is_enforced(self):
        prompt, _ = messages_to_prompt(
            [{"role": "user", "content": "hi"}], self.TOOLS,
            tool_choice={"type": "function", "function": {"name": "get_weather"}})
        self.assertIn('MUST call the tool "get_weather"', prompt)

    def test_default_tool_choice_behaves_like_auto(self):
        prompt, _ = messages_to_prompt([{"role": "user", "content": "hi"}], self.TOOLS)
        self.assertIn("Tool Use", prompt)


class ToolSchemaSizeTests(ConfigTestCase):
    def test_huge_schemas_are_trimmed(self):
        big = {"type": "object", "properties": {f"field_{i}": {"type": "string",
                                                               "description": "x" * 400}
                                                for i in range(200)}}
        prompt = build_tool_prompt([{"name": "big_tool", "description": "d", "parameters": big}])
        self.assertLess(len(prompt), MAX_TOOL_SCHEMA_CHARS + 500)
        self.assertIn("big_tool", prompt)
        self.assertNotIn("field_199", prompt)

    def test_normal_schemas_keep_their_parameters(self):
        prompt = build_tool_prompt([{"name": "t", "description": "d",
                                     "parameters": {"type": "object",
                                                    "properties": {"city": {"type": "string"}}}}])
        self.assertIn("city", prompt)

    def test_non_ascii_descriptions_survive(self):
        prompt = build_tool_prompt([{"name": "t", "description": "天气查询 🌦", "parameters": {}}])
        self.assertIn("天气查询", prompt)


class ParseToolCallsTests(unittest.TestCase):
    def test_single_call(self):
        text = '```tool_call\n{"name": "get_weather", "arguments": {"city": "Tokyo"}}\n```'
        clean, calls = parse_tool_calls(text)
        self.assertEqual(clean, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["type"], "function")
        self.assertTrue(calls[0]["id"].startswith("call_"))
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"city": "Tokyo"})

    def test_multiple_calls(self):
        text = ('```tool_call\n{"name": "a", "arguments": {}}\n```\n'
                '```tool_call\n{"name": "b", "arguments": {"x": 1}}\n```')
        clean, calls = parse_tool_calls(text)
        self.assertEqual([c["function"]["name"] for c in calls], ["a", "b"])
        self.assertEqual(clean, "")

    def test_surrounding_text_is_preserved(self):
        text = 'Let me check.\n```tool_call\n{"name": "a", "arguments": {}}\n```\nThanks!'
        clean, calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertIn("Let me check.", clean)
        self.assertIn("Thanks!", clean)
        self.assertNotIn("tool_call", clean)

    def test_missing_arguments_defaults_to_an_empty_object(self):
        _clean, calls = parse_tool_calls('```tool_call\n{"name": "a"}\n```')
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {})

    def test_invalid_json_block_is_skipped_but_removed(self):
        clean, calls = parse_tool_calls('```tool_call\n{not json}\n```\nafter')
        self.assertEqual(calls, [])
        self.assertIn("after", clean)
        self.assertNotIn("{not json}", clean)

    def test_block_without_a_name_is_ignored(self):
        _clean, calls = parse_tool_calls('```tool_call\n{"arguments": {}}\n```')
        self.assertEqual(calls, [])

    def test_ids_are_unique(self):
        text = ('```tool_call\n{"name": "a", "arguments": {}}\n```\n'
                '```tool_call\n{"name": "a", "arguments": {}}\n```')
        _clean, calls = parse_tool_calls(text)
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])

    def test_empty_and_none(self):
        self.assertEqual(parse_tool_calls(""), ("", []))
        self.assertEqual(parse_tool_calls(None), ("", []))
        self.assertEqual(parse_tool_calls("no blocks here"), ("no blocks here", []))

    def test_unicode_arguments_are_not_escaped(self):
        _clean, calls = parse_tool_calls(
            '```tool_call\n{"name": "a", "arguments": {"city": "東京"}}\n```')
        self.assertIn("東京", calls[0]["function"]["arguments"])


class GooglePromptTests(unittest.TestCase):
    def test_text_content(self):
        prompt, images = google_contents_to_prompt({
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}]})
        self.assertEqual(prompt, "hello")
        self.assertEqual(images, [])

    def test_inline_image(self):
        prompt, images = google_contents_to_prompt({
            "contents": [{"role": "user", "parts": [
                {"text": "Describe"},
                {"inlineData": {"mimeType": "image/png", "data": IMAGE_B64}},
            ]}]})
        self.assertEqual(prompt, "Describe\n[Image attached]")
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_snake_case_inline_data(self):
        _prompt, images = google_contents_to_prompt({
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "image/png", "data": IMAGE_B64}}]}]})
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_malformed_inline_image_is_skipped(self):
        prompt, images = google_contents_to_prompt({
            "contents": [{"role": "user", "parts": [
                {"text": "Describe"}, {"inlineData": {"mimeType": "image/png", "data": "%%%"}}]}]})
        self.assertEqual(prompt, "Describe")
        self.assertEqual(images, [])

    def test_model_role_is_labelled(self):
        prompt, _ = google_contents_to_prompt({
            "contents": [{"role": "model", "parts": [{"text": "prior"}]}]})
        self.assertEqual(prompt, "[Assistant]: prior")

    def test_system_instruction(self):
        prompt, _ = google_contents_to_prompt({
            "systemInstruction": {"parts": [{"text": "be terse"}]},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        self.assertIn("[System instruction]: be terse", prompt)
        self.assertIn("hi", prompt)

    def test_system_instruction_as_a_plain_string(self):
        prompt, _ = google_contents_to_prompt({"systemInstruction": "be terse",
                                               "contents": [{"parts": [{"text": "hi"}]}]})
        self.assertIn("be terse", prompt)

    def test_function_declarations_become_a_tool_prompt(self):
        prompt, _ = google_contents_to_prompt({
            "tools": [{"functionDeclarations": [
                {"name": "get_weather", "description": "Weather",
                 "parameters": {"type": "object"}}]}],
            "contents": [{"role": "user", "parts": [{"text": "Tokyo?"}]}]})
        self.assertIn("get_weather", prompt)
        self.assertIn("```function_call", prompt)
        self.assertIn('"args"', prompt)

    def test_mode_none_omits_tools(self):
        prompt, _ = google_contents_to_prompt({
            "tools": [{"functionDeclarations": [{"name": "get_weather"}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        self.assertNotIn("get_weather", prompt)

    def test_mode_any_forces_a_call(self):
        prompt, _ = google_contents_to_prompt({
            "tools": [{"functionDeclarations": [{"name": "get_weather"}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        self.assertIn("MUST call", prompt)

    def test_allowed_function_names_are_listed(self):
        prompt, _ = google_contents_to_prompt({
            "tools": [{"functionDeclarations": [{"name": "a"}, {"name": "b"}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY",
                                                     "allowedFunctionNames": ["b"]}},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        self.assertIn('"b"', prompt)

    def test_history_function_call_and_response(self):
        prompt, _ = google_contents_to_prompt({"contents": [
            {"role": "user", "parts": [{"text": "weather?"}]},
            {"role": "model", "parts": [{"functionCall": {"name": "get_weather",
                                                          "args": {"city": "Tokyo"}}}]},
            {"role": "user", "parts": [{"functionResponse": {"name": "get_weather",
                                                             "response": {"temp": 21}}}]},
        ]})
        self.assertIn("```function_call", prompt)
        self.assertIn("get_weather", prompt)
        self.assertIn('[Tool result for get_weather]', prompt)

    def test_empty_contents(self):
        self.assertEqual(google_contents_to_prompt({}), ("", []))
        self.assertEqual(google_contents_to_prompt({"contents": []}), ("", []))

    def test_junk_entries_are_skipped(self):
        prompt, images = google_contents_to_prompt({
            "contents": ["junk", None, {"parts": ["junk", 42]},
                         {"role": "user", "parts": [{"text": "real"}]}]})
        self.assertEqual(prompt, "real")
        self.assertEqual(images, [])


class ParseGoogleFunctionCallsTests(unittest.TestCase):
    def test_fenced_block(self):
        clean, calls = parse_google_function_calls(
            '```function_call\n{"name": "get_weather", "args": {"city": "Tokyo"}}\n```')
        self.assertEqual(clean, "")
        self.assertEqual(calls[0]["name"], "get_weather")
        self.assertEqual(calls[0]["args"], {"city": "Tokyo"})

    def test_unfenced_block(self):
        _clean, calls = parse_google_function_calls(
            'function_call\n{"name": "get_weather", "args": {"city": "Tokyo"}}')
        self.assertEqual(len(calls), 1)

    def test_unfenced_block_with_nested_arguments(self):
        """Regression: a non-greedy ``\\{[^`]*?\\}`` truncated at the first '}'."""
        _clean, calls = parse_google_function_calls(
            'function_call\n{"name": "book", "args": {"where": {"city": "Tokyo", '
            '"dates": {"from": "2026-01-01", "to": "2026-01-05"}}, "seats": 2}}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"]["where"]["city"], "Tokyo")
        self.assertEqual(calls[0]["args"]["where"]["dates"]["to"], "2026-01-05")
        self.assertEqual(calls[0]["args"]["seats"], 2)

    def test_braces_inside_string_values_do_not_confuse_the_scanner(self):
        _clean, calls = parse_google_function_calls(
            'function_call\n{"name": "write", "args": {"body": "a { b } c \\"quote\\""}}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"]["body"], 'a { b } c "quote"')

    def test_unfenced_block_leaves_surrounding_text(self):
        clean, calls = parse_google_function_calls(
            'Calling now.\nfunction_call\n{"name": "f", "args": {}}\nDone.')
        self.assertEqual(len(calls), 1)
        self.assertIn("Calling now.", clean)
        self.assertIn("Done.", clean)
        self.assertNotIn("function_call", clean)

    def test_unfenced_header_is_case_insensitive(self):
        _clean, calls = parse_google_function_calls(
            'Function_call\n{"name": "f", "args": {"a": 1}}')
        self.assertEqual(len(calls), 1)

    def test_unbalanced_json_does_not_hang_or_crash(self):
        clean, calls = parse_google_function_calls('function_call\n{"name": "f", "args": {')
        self.assertEqual(calls, [])
        self.assertIsInstance(clean, str)

    def test_two_unfenced_blocks(self):
        _clean, calls = parse_google_function_calls(
            'function_call\n{"name": "a", "args": {"x": {"y": 1}}}\n'
            'function_call\n{"name": "b", "args": {}}')
        self.assertEqual([c["name"] for c in calls], ["a", "b"])

    def test_bare_json_object(self):
        clean, calls = parse_google_function_calls(
            '{"name": "get_weather", "args": {"city": "Tokyo"}}')
        self.assertEqual(clean, "")
        self.assertEqual(calls[0]["name"], "get_weather")

    def test_arguments_key_is_accepted_as_args(self):
        _clean, calls = parse_google_function_calls(
            '```function_call\n{"name": "f", "arguments": {"a": 1}}\n```')
        self.assertEqual(calls[0]["args"], {"a": 1})

    def test_multiple_calls(self):
        text = ('```function_call\n{"name": "a", "args": {}}\n```\n'
                '```function_call\n{"name": "b", "args": {}}\n```')
        _clean, calls = parse_google_function_calls(text)
        self.assertEqual([c["name"] for c in calls], ["a", "b"])

    def test_plain_text_is_left_alone(self):
        clean, calls = parse_google_function_calls("just an answer")
        self.assertEqual(clean, "just an answer")
        self.assertEqual(calls, [])

    def test_json_without_a_name_is_not_a_call(self):
        clean, calls = parse_google_function_calls('{"foo": "bar"}')
        self.assertEqual(calls, [])
        self.assertIn("foo", clean)

    def test_empty_and_none(self):
        self.assertEqual(parse_google_function_calls(""), ("", []))
        self.assertEqual(parse_google_function_calls(None), ("", []))


if __name__ == "__main__":
    unittest.main()
