"""Guards for the performance work on the request path.

These assert the *mechanisms* (fewer frames parsed, byte-identical encoding,
parallel uploads, negotiated compression), never wall-clock timings — a
timing assertion would be flaky on a heavily loaded machine, while a broken
mechanism is exactly the regression worth catching.
"""
import gzip
import http.client
import json
import threading
import unittest
import urllib.parse
from unittest import mock

from gemini_web2api import gemini, server
from gemini_web2api.gemini import (
    _iter_stream_frames,
    _quote_form_value,
    _retry_delay,
    extract_response_text,
    fetch_latest_bl,
)
from gemini_web2api.tools import build_tool_prompt
from tests.support import ConfigTestCase, ServerTestCase, gemini_frame, gemini_response


class QuoteFormValueTests(unittest.TestCase):
    def test_byte_identical_to_quote_plus(self):
        samples = [
            "",
            "plain text",
            "a+b=c&d=e%f",
            "100% sure & ready = yes + more",
            "héllo wörld 🚀 ünïcödé",
            "日本語のテキスト、テストです。",
            "\n\t\r\x01\x1f\x7f",
            "".join(chr(i) for i in range(1, 1024)),
            "long prompt with code: def f(x): return {'a': [1, 2]} " * 50,
        ]
        for sample in samples:
            with self.subTest(sample=sample[:30]):
                self.assertEqual(_quote_form_value(sample), urllib.parse.quote_plus(sample))

    def test_payload_round_trips_a_hostile_prompt(self):
        prompt = "a&b=c+d%e f🚀\nsecond line"
        payload = gemini._build_payload(prompt, 1, 4)
        outer = json.loads(urllib.parse.parse_qs(payload)["f.req"][0])
        inner = json.loads(outer[1])
        self.assertEqual(inner[0][0], prompt)


class RetryDelayTests(ConfigTestCase):
    def test_backoff_grows_to_the_configured_cap(self):
        self.CONFIG["retry_delay_sec"] = 2
        self.assertEqual([_retry_delay(i) for i in range(5)], [0.25, 0.5, 1.0, 2.0, 2.0])

    def test_a_small_configured_delay_is_honoured(self):
        self.CONFIG["retry_delay_sec"] = 0.01
        self.assertEqual(_retry_delay(0), 0.01)
        self.assertEqual(_retry_delay(3), 0.01)

    def test_zero_delay_stays_zero(self):
        self.CONFIG["retry_delay_sec"] = 0
        self.assertEqual(_retry_delay(0), 0.0)


class ExtractResponseTextTests(unittest.TestCase):
    def test_only_the_tail_frames_are_parsed(self):
        """A 20-frame cumulative reply must not cost 20 JSON parses."""
        raw = gemini_response(*[f"answer {i} " + "x" * i for i in range(20)])
        real = gemini._extract_texts_from_line
        with mock.patch.object(gemini, "_extract_texts_from_line", wraps=real) as spy:
            self.assertTrue(extract_response_text(raw).startswith("answer 19"))
        self.assertLessEqual(spy.call_count, 2)


class StreamFrameTests(unittest.TestCase):
    class _Response:
        def __init__(self, chunks):
            self._chunks = chunks

        def iter_text(self):
            yield from self._chunks

    def test_frames_split_into_tiny_chunks(self):
        full = gemini_frame("Hello") + "\n" + gemini_frame("Hello world") + "\n"
        chunks = [full[i:i + 7] for i in range(0, len(full), 7)]
        self.assertEqual(list(_iter_stream_frames(self._Response(chunks))),
                         ["Hello", "Hello world"])

    def test_bard_error_split_across_chunks_still_raises(self):
        line = '["wrb.fr","x","BardErrorInfo [429]",null]\n'
        chunks = [line[i:i + 5] for i in range(0, len(line), 5)]
        with self.assertRaises(gemini.GeminiUpstreamError) as caught:
            list(_iter_stream_frames(self._Response(chunks)))
        self.assertEqual(caught.exception.code, 429)


class FetchLatestBlTests(ConfigTestCase):
    def test_reading_stops_at_the_tag(self):
        """The rest of the (megabyte-class) page is never consumed."""

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_text(self):
                yield "<script>boq_assistant-bard-web-server_20260101.01_p0</script>"
                raise AssertionError("the page was read past the build tag")

        class Client:
            def stream(self, *args, **kwargs):
                return Response()

        with mock.patch.object(gemini, "_get_httpx_client", return_value=Client()):
            self.assertEqual(fetch_latest_bl(),
                             "boq_assistant-bard-web-server_20260101.01_p0")


class StaticBodyTests(unittest.TestCase):
    def test_the_producer_runs_once(self):
        calls = []

        def producer():
            calls.append(1)
            return {"calls": len(calls)}

        first = server._static_json_body("test_key", producer)
        second = server._static_json_body("test_key", producer)
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)
        server._STATIC_JSON_BODIES.pop("test_key", None)


class ToolSchemaTests(unittest.TestCase):
    def test_schemas_are_compact(self):
        prompt = build_tool_prompt([{
            "name": "get_weather", "description": "Weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }])
        self.assertIn('"name":"get_weather"', prompt)
        self.assertNotIn('\n  "name"', prompt)


class ParallelUploadTests(unittest.TestCase):
    def test_images_upload_concurrently_and_keep_their_order(self):
        barrier = threading.Barrier(3)

        def fake_upload(data, filename, mime):
            index = data[-1]  # marker byte identifying the image
            # A sequential implementation never gets three parties here and
            # the barrier breaks instead of the test hanging forever.
            barrier.wait(timeout=5)
            threading.Event().wait(0.05 * (3 - index))
            return f"/ref{index}"

        images = [(b"\x89PNG\r\n\x1a\nimg" + bytes([i]), "image/png") for i in range(3)]
        with mock.patch.object(server, "upload_image", side_effect=fake_upload):
            self.assertEqual(server._upload_images(images), ["/ref0", "/ref1", "/ref2"])


class GzipTests(ServerTestCase):
    def raw_get(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        connection.close()
        return result

    def test_models_are_gzipped_when_advertised(self):
        status, headers, body = self.raw_get("/v1/models", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(json.loads(gzip.decompress(body))["object"], "list")

    def test_gzip_q_zero_is_refused(self):
        status, headers, body = self.raw_get("/v1/models", {"Accept-Encoding": "gzip;q=0"})
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(json.loads(body)["object"], "list")

    def test_no_advertisement_means_no_compression(self):
        status, headers, body = self.raw_get("/v1/models")
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(json.loads(body)["object"], "list")

    def test_the_dashboard_shrinks(self):
        _status, plain_headers, plain = self.raw_get(
            "/", {"Accept": "text/html"})
        status, headers, packed = self.raw_get(
            "/", {"Accept": "text/html", "Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", plain_headers)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertLess(len(packed), len(plain) // 2)
        # Not byte-compared with the plain render: the injected state is live
        # (uptime, requests served), so the two renders legitimately differ.
        unpacked = gzip.decompress(packed)
        self.assertTrue(unpacked.startswith(b"<!doctype html>"))
        self.assertTrue(unpacked.rstrip().endswith(b"</html>"))

    def test_streams_are_never_compressed(self):
        """Compressing SSE would buffer it and kill time-to-first-token."""
        with mock.patch("gemini_web2api.server.generate_stream",
                        return_value=iter(["hi"])):
            status, headers, body = self.request(
                "POST", "/v1/chat/completions",
                body={"model": "gemini-3.6-flash", "stream": True,
                      "messages": [{"role": "user", "content": "hello"}]},
                headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertIn("[DONE]", body)


if __name__ == "__main__":
    unittest.main()
