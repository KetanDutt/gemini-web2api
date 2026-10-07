"""Gemini wire protocol: payload construction, response parsing, streaming."""
import json
import unittest
from unittest import mock
from urllib.parse import parse_qs

from gemini_web2api.gemini import (
    HAS_HTTPX,
    GeminiUpstreamError,
    _build_payload,
    _extract_texts_from_line,
    _get_url,
    clean_text,
    extract_response_text,
    generate_stream,
)
from tests.support import ConfigTestCase, cumulative_response, gemini_frame, gemini_response


def decode_payload(payload):
    outer = json.loads(parse_qs(payload)["f.req"][0])
    return json.loads(outer[1])


class PayloadTests(ConfigTestCase):
    def test_model_id_lands_in_slot_79(self):
        self.assertEqual(decode_payload(_build_payload("hi", 3, 4))[79], 3)

    def test_think_mode_lands_in_slot_17(self):
        self.assertEqual(decode_payload(_build_payload("hi", 1, 0))[17], [[0]])

    def test_persistent_chat_by_default(self):
        self.CONFIG["temporary_chats"] = False
        inner = decode_payload(_build_payload("hello", 1, 4))
        self.assertEqual(inner[41], [2])
        self.assertIsNone(inner[45])

    def test_temporary_chat_sets_both_flags(self):
        self.CONFIG["temporary_chats"] = True
        inner = decode_payload(_build_payload("hello", 1, 4))
        self.assertEqual(inner[41], [1])
        self.assertEqual(inner[45], 1)

    def test_image_references_are_attached(self):
        inner = decode_payload(_build_payload("describe", 1, 4, ["/uploaded/ref"]))
        self.assertEqual(inner[0][0], "describe")
        self.assertEqual(inner[0][3], [[None, None, "/uploaded/ref"]])

    def test_no_image_slot_when_there_are_no_images(self):
        inner = decode_payload(_build_payload("describe", 1, 4))
        self.assertIsNone(inner[0][3])

    def test_payload_has_room_for_high_extra_fields(self):
        """gemini-3.1-pro-enhanced writes slot 80; an 80-slot payload raised IndexError."""
        inner = decode_payload(_build_payload("hi", 3, 4, None, {31: 2, 80: 3}))
        self.assertEqual(inner[31], 2)
        self.assertEqual(inner[80], 3)

    def test_out_of_range_extra_fields_are_ignored_not_fatal(self):
        inner = decode_payload(_build_payload("hi", 3, 4, None, {9999: 1}))
        self.assertEqual(len(inner), 102)

    def test_xsrf_token_is_sent_as_at(self):
        self.CONFIG["xsrf_token"] = "TOKEN123"
        self.assertIn("at=TOKEN123", _build_payload("hi", 1, 4))

    def test_no_at_parameter_without_a_token(self):
        self.CONFIG["xsrf_token"] = None
        self.assertNotIn("&at=", _build_payload("hi", 1, 4))

    def test_each_payload_gets_a_fresh_conversation_id(self):
        first = decode_payload(_build_payload("hi", 1, 4))[59]
        second = decode_payload(_build_payload("hi", 1, 4))[59]
        self.assertNotEqual(first, second)

    def test_prompt_is_preserved_verbatim(self):
        prompt = "line1\nline2\ttab \"quotes\" émoji 🚀"
        self.assertEqual(decode_payload(_build_payload(prompt, 1, 4))[0][0], prompt)


class UrlTests(ConfigTestCase):
    def test_default_url(self):
        url = _get_url(reqid=42)
        self.assertIn("gemini.google.com/_/BardChatUi/data/", url)
        self.assertIn("assistant.lamda.BardFrontendService/StreamGenerate", url)
        self.assertIn("_reqid=42", url)
        self.assertIn("rt=c", url)

    def test_account_prefix_is_inserted(self):
        self.CONFIG["auth_user"] = "1"
        self.assertIn("gemini.google.com/u/1/_/BardChatUi", _get_url(reqid=1))

    def test_build_tag_is_url_encoded(self):
        self.CONFIG["gemini_bl"] = "bl with spaces&more"
        url = _get_url(reqid=1)
        self.assertNotIn("bl with spaces&more", url)
        self.assertIn("bl%20with%20spaces%26more", url)


class CleanTextTests(unittest.TestCase):
    def test_strips_code_execution_artifacts(self):
        raw = "answer\n```python?code_reference&code_event_index=0\nsecret\n```\ntail"
        self.assertEqual(clean_text(raw), "answer\ntail")

    def test_strips_stdout_artifacts(self):
        raw = "before\n```text?code_stdout&code_event_index=3\nnoise\n```\nafter"
        self.assertNotIn("noise", clean_text(raw))

    def test_strips_card_content_urls(self):
        raw = "text http://googleusercontent.com/card_content/12345 more"
        self.assertEqual(clean_text(raw), "text  more".strip())

    def test_no_strip_option_preserves_whitespace(self):
        self.assertEqual(clean_text("  hi  ", strip=False), "  hi  ")

    def test_handles_none_and_empty(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(""), "")


class ExtractResponseTextTests(unittest.TestCase):
    def test_single_frame(self):
        self.assertEqual(extract_response_text(gemini_response("hello world")), "hello world")

    def test_cumulative_frames_return_the_final_text(self):
        raw = gemini_response("A", "AB", "ABC", "ABCDE")
        self.assertEqual(extract_response_text(raw), "ABCDE")

    def test_final_short_answer_wins_over_a_longer_earlier_segment(self):
        """Regression: the old code returned the longest segment, not the last."""
        raw = gemini_response("a very long preamble " + "x" * 200, "short final answer")
        self.assertEqual(extract_response_text(raw), "short final answer")

    def test_short_frames_are_not_discarded(self):
        """Regression: a len(line) < 200 gate silently dropped short replies."""
        inner = [None] * 5
        inner[4] = [[None, ["42"]]]
        tiny = json.dumps([["wrb.fr", "id", json.dumps(inner)]])
        self.assertLess(len(tiny), 200)
        self.assertEqual(_extract_texts_from_line(tiny), ["42"])
        self.assertEqual(extract_response_text(tiny), "42")

    def test_multi_part_frame_returns_all_segments(self):
        self.assertEqual(_extract_texts_from_line(gemini_frame(["one", "two"])), ["one", "two"])

    def test_bard_error_is_raised_with_its_code(self):
        with self.assertRaises(GeminiUpstreamError) as ctx:
            extract_response_text(')]}\'\n["wrb.fr","x","BardErrorInfo [429]",null]')
        self.assertEqual(ctx.exception.code, 429)
        self.assertIn("429", str(ctx.exception))

    def test_empty_and_garbage_input(self):
        self.assertEqual(extract_response_text(""), "")
        self.assertEqual(extract_response_text(None), "")
        self.assertEqual(extract_response_text("not json at all"), "")
        self.assertEqual(extract_response_text(")]}'\n\nnull\n"), "")

    def test_non_string_frames_are_skipped(self):
        self.assertEqual(_extract_texts_from_line(json.dumps(["wrb.fr", "x", "y"])), [])
        self.assertEqual(_extract_texts_from_line(json.dumps([["wrb.fr", None, 123]])), [])
        self.assertEqual(_extract_texts_from_line("{}"), [])

    def test_frames_without_text_are_skipped(self):
        inner = [None] * 5
        inner[4] = None
        line = json.dumps([["wrb.fr", "id", json.dumps(inner)]])
        self.assertEqual(_extract_texts_from_line(line), [])

    def test_realistic_response_shape(self):
        raw = ")]}'\n\n" + cumulative_response("The answer is 42.", steps=4) + "\n"
        self.assertEqual(extract_response_text(raw), "The answer is 42.")


class FakeStreamResponse:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code
        self._entered = False

    def __enter__(self):
        self._entered = True
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", "https://gemini.google.com/")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)

    def iter_text(self):
        yield from self._chunks

    def read(self):
        return b""


def stream_with(chunks, status_code=200):
    """Patch gemini.py so generate_stream consumes ``chunks``."""
    client = mock.MagicMock()
    client.stream.return_value = FakeStreamResponse(chunks, status_code)
    return mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=client)


@unittest.skipUnless(HAS_HTTPX, "true streaming requires httpx")
class GenerateStreamTests(ConfigTestCase):
    def setUp(self):
        super().setUp()
        self.CONFIG["log_requests"] = False
        self.CONFIG["retry_attempts"] = 1
        self.CONFIG["retry_delay_sec"] = 0

    def test_cumulative_frames_yield_only_new_suffixes(self):
        chunks = [gemini_frame("Hel") + "\n",
                  gemini_frame("Hello") + "\n",
                  gemini_frame("Hello world") + "\n"]
        with stream_with(chunks):
            self.assertEqual("".join(generate_stream("hi", 1, 4)), "Hello world")

    def test_repeated_frames_do_not_duplicate_output(self):
        chunks = [gemini_frame("same") + "\n"] * 5
        with stream_with(chunks):
            self.assertEqual("".join(generate_stream("hi", 1, 4)), "same")

    def test_stale_shorter_frame_is_ignored(self):
        chunks = [gemini_frame("Hello world") + "\n", gemini_frame("Hello") + "\n"]
        with stream_with(chunks):
            self.assertEqual("".join(generate_stream("hi", 1, 4)), "Hello world")

    def test_multi_part_response_does_not_abort_the_stream(self):
        """Regression: a second, unrelated part used to raise mid-stream.

        The client had already received HTTP 200 plus deltas, so the exception
        left the SSE response without a terminator.
        """
        chunks = [gemini_frame("Hello") + "\n",
                  gemini_frame("Hello there") + "\n",
                  gemini_frame("Separate segment") + "\n"]
        with stream_with(chunks):
            deltas = list(generate_stream("hi", 1, 4))
        self.assertEqual("".join(deltas), "Hello thereSeparate segment")

    def test_frames_split_across_network_chunks(self):
        full = gemini_frame("Hello") + "\n" + gemini_frame("Hello world") + "\n"
        mid = len(full) // 2
        with stream_with([full[:mid], full[mid:]]):
            self.assertEqual("".join(generate_stream("hi", 1, 4)), "Hello world")

    def test_trailing_frame_without_newline_is_still_parsed(self):
        with stream_with([gemini_frame("no trailing newline")]):
            self.assertEqual("".join(generate_stream("hi", 1, 4)), "no trailing newline")

    def test_bard_error_raises_upstream_error(self):
        with stream_with(['["wrb.fr","x","BardErrorInfo [429]",null]\n']), \
             self.assertRaises(GeminiUpstreamError) as ctx:
            list(generate_stream("hi", 1, 4))
        self.assertEqual(ctx.exception.code, 429)

    def test_empty_stream_yields_nothing(self):
        with stream_with([]):
            self.assertEqual(list(generate_stream("hi", 1, 4)), [])

    def test_artifacts_are_cleaned_from_deltas(self):
        text = "answer\n```python?code_reference&code_event_index=0\nhidden\n```"
        with stream_with([gemini_frame(text) + "\n"]):
            self.assertNotIn("hidden", "".join(generate_stream("hi", 1, 4)))

    def test_http_error_before_any_output_is_retried(self):
        self.CONFIG["retry_attempts"] = 3
        client = mock.MagicMock()
        client.stream.side_effect = [
            FakeStreamResponse([], status_code=500),
            FakeStreamResponse([], status_code=500),
            FakeStreamResponse([gemini_frame("recovered") + "\n"]),
        ]
        with mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=client):
            self.assertEqual("".join(generate_stream("hi", 1, 4)), "recovered")

    def test_failure_after_partial_output_propagates_immediately(self):
        """Retrying after bytes reached the client would duplicate them."""
        self.CONFIG["retry_attempts"] = 3
        client = mock.MagicMock()

        class Broken(FakeStreamResponse):
            def iter_text(self):
                yield gemini_frame("partial") + "\n"
                raise OSError("connection reset")

        client.stream.return_value = Broken([])
        with mock.patch("gemini_web2api.gemini._get_httpx_client", return_value=client), \
             self.assertRaises(OSError):
            list(generate_stream("hi", 1, 4))
        self.assertEqual(client.stream.call_count, 1)


class NoHttpxFallbackTests(ConfigTestCase):
    def test_falls_back_to_buffered_generation(self):
        with mock.patch("gemini_web2api.gemini.HAS_HTTPX", False), \
             mock.patch("gemini_web2api.gemini.generate", return_value="buffered") as gen:
            self.assertEqual(list(generate_stream("hi", 1, 4)), ["buffered"])
            gen.assert_called_once()

    def test_fallback_yields_nothing_for_an_empty_reply(self):
        with mock.patch("gemini_web2api.gemini.HAS_HTTPX", False), \
             mock.patch("gemini_web2api.gemini.generate", return_value=""):
            self.assertEqual(list(generate_stream("hi", 1, 4)), [])


if __name__ == "__main__":
    unittest.main()
