"""Production-readiness regressions.

Four behaviours that only show up in real deployments, not in single
requests from a test client:

* SDK clients pool keep-alive connections, so per-request accounting that
  latches onto the *connection* undercounts everything after the first
  request and reuses its request ID.
* A threaded server with no bound on concurrent generations turns a burst
  of slow upstream calls into thread exhaustion; the limit must fail fast
  with 503 rather than queue invisibly.
* The Responses API is what Codex actually speaks to; buffering its
  "streaming" replies made time-to-first-token the whole generation time.
* Nested request fields with the wrong JSON type are client errors and
  must answer 400, never crash into a 500.
"""

import http.client
import os
import threading
import time
import unittest
from unittest import mock

from gemini_web2api import metrics
from gemini_web2api.config import ENV_PREFIX, load_config
from gemini_web2api.ratelimit import LIMITER

from .support import ConfigTestCase, ServerTestCase, decode_sse

CHAT_BODY = {"model": "gemini-3.6-flash",
             "messages": [{"role": "user", "content": "hi"}]}


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class KeepAliveAccountingTests(ServerTestCase):
    """One handler instance serves a whole connection; state must not leak."""

    def setUp(self):
        super().setUp()
        metrics.reset()
        self.addCleanup(metrics.reset)

    def _connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    @staticmethod
    def _get(connection, path, headers=None):
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), body

    def test_every_request_on_a_reused_connection_is_counted(self):
        before = metrics.snapshot()["counters"]["requests"]
        connection = self._connection()
        ids = []
        try:
            for _ in range(3):
                status, headers, _body = self._get(connection, "/health")
                self.assertEqual(status, 200)
                ids.append(headers["X-Request-Id"])
        finally:
            connection.close()

        # Distinct IDs: a reused ID makes log correlation meaningless.
        self.assertEqual(len(set(ids)), 3)
        after = metrics.snapshot()["counters"]["requests"]
        self.assertEqual(after - before, 3)

        def recorded():
            entries = [e for e in metrics.history(50) if e["id"] in ids]
            return len(entries) == 3

        self.assertTrue(_wait_for(recorded),
                        "not every keep-alive request reached the history")

    def test_rate_limit_headers_do_not_leak_into_the_next_request(self):
        self.CONFIG["rate_limit_max"] = 100
        self.CONFIG["rate_limit_window_sec"] = 60
        LIMITER.configure(100, 60)
        LIMITER._windows.clear()
        self.addCleanup(LIMITER.configure, 0, 60)

        connection = self._connection()
        try:
            _status, headers, _ = self._get(connection, "/v1/models")
            self.assertIn("X-RateLimit-Limit-Requests", headers)
            # /health never consults the limiter; yesterday's headers on it
            # would be state leaking across requests on one connection.
            _status, headers, _ = self._get(connection, "/health")
            self.assertNotIn("X-RateLimit-Limit-Requests", headers)
        finally:
            connection.close()


class ConcurrencyLimitTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        metrics.reset()
        self.addCleanup(metrics.reset)
        self.CONFIG["max_concurrent_requests"] = 1

    def _blocked_generate(self):
        entered = threading.Event()
        release = threading.Event()

        def fake_generate(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=10)
            return "ok"

        return fake_generate, entered, release

    def test_a_second_generation_fails_fast_with_503(self):
        fake, entered, release = self._blocked_generate()
        first = {}

        def call_first():
            first["result"] = self.post_json("/v1/chat/completions", CHAT_BODY)

        with mock.patch("gemini_web2api.server.generate", side_effect=fake):
            thread = threading.Thread(target=call_first)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                # The slot shows up in the capacity the operator watches.
                _status, _headers, body = self.get_json("/status")
                self.assertEqual(body["concurrency"], {"in_flight": 1, "max": 1})

                status, headers, body = self.post_json(
                    "/v1/chat/completions", CHAT_BODY)
                self.assertEqual(status, 503)
                self.assertEqual(body["error"]["code"], "server_busy")
                self.assertEqual(headers["Retry-After"], "5")
                self.assertEqual(
                    metrics.snapshot()["counters"]["server_busy"], 1)
                # A refusal is not an upstream failure: the upstream was
                # never called, and the counters must not claim it was.
                self.assertEqual(
                    metrics.snapshot()["counters"]["upstream_failures"], 0)
            finally:
                release.set()
                thread.join(timeout=10)

        self.assertEqual(first["result"][0], 200)
        # The gauge returns to zero once the holder finishes.
        self.assertEqual(metrics.snapshot()["gauges"]["in_flight"], 0)

    def test_a_busy_streaming_request_gets_503_not_an_sse_stream(self):
        fake, entered, release = self._blocked_generate()
        first = {}

        def call_first():
            first["result"] = self.post_json("/v1/chat/completions", CHAT_BODY)

        with mock.patch("gemini_web2api.server.generate", side_effect=fake):
            thread = threading.Thread(target=call_first)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                status, headers, body = self.post_json(
                    "/v1/chat/completions", {**CHAT_BODY, "stream": True})
                # The slot is taken before the SSE headers, so the refusal
                # is an ordinary JSON 503 a client can act on.
                self.assertEqual(status, 503)
                self.assertIn("application/json", headers["Content-Type"])
                self.assertEqual(body["error"]["code"], "server_busy")
            finally:
                release.set()
                thread.join(timeout=10)

    def test_unlimited_by_default_and_gauge_balances(self):
        self.CONFIG["max_concurrent_requests"] = 0
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            status, _headers, _body = self.post_json(
                "/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 200)
        self.assertEqual(metrics.snapshot()["gauges"]["in_flight"], 0)
        _status, _headers, body = self.get_json("/status")
        self.assertEqual(body["concurrency"], {"in_flight": 0, "max": 0})


class ResponsesLiveStreamingTests(ServerTestCase):
    def test_plain_replies_stream_delta_by_delta(self):
        with mock.patch("gemini_web2api.server.generate_stream",
                        return_value=iter(["Hello ", "world"])) as stream:
            status, _headers, body = self.post_json(
                "/v1/responses", {"input": "hi", "stream": True})
        self.assertEqual(status, 200)
        stream.assert_called_once()

        events = decode_sse(body)
        types = [event_type for event_type, _payload in events]
        self.assertEqual(types, [
            "response.created", "response.in_progress",
            "response.output_item.added", "response.content_part.added",
            "response.output_text.delta", "response.output_text.delta",
            "response.output_text.done", "response.content_part.done",
            "response.output_item.done", "response.completed",
        ])
        sequences = [payload["sequence_number"] for _t, payload in events]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences))

        deltas = [payload["delta"] for t, payload in events
                  if t == "response.output_text.delta"]
        self.assertEqual("".join(deltas), "Hello world")
        completed = events[-1][1]["response"]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            completed["output"][0]["content"][0]["text"], "Hello world")
        self.assertIn("input_tokens", completed["usage"])

    def test_a_mid_stream_failure_closes_with_response_failed(self):
        def broken(*_args, **_kwargs):
            yield "part"
            raise RuntimeError("upstream died")

        with mock.patch("gemini_web2api.server.generate_stream",
                        side_effect=broken):
            status, _headers, body = self.post_json(
                "/v1/responses", {"input": "hi", "stream": True})
        self.assertEqual(status, 200)
        types = [t for t, _p in decode_sse(body)]
        self.assertIn("response.output_text.delta", types)
        self.assertEqual(types[-1], "response.failed")
        self.assertNotIn("response.completed", types)

    def test_tool_replies_stay_buffered(self):
        reply = '```tool_call\n{"name": "get_weather", "arguments": {"city": "Tokyo"}}\n```'
        tools = [{"type": "function", "name": "get_weather",
                  "parameters": {"type": "object"}}]
        with mock.patch("gemini_web2api.server.generate", return_value=reply), \
                mock.patch("gemini_web2api.server.generate_stream") as stream:
            status, _headers, body = self.post_json(
                "/v1/responses",
                {"input": "weather?", "tools": tools, "stream": True})
        self.assertEqual(status, 200)
        stream.assert_not_called()
        types = [t for t, _p in decode_sse(body)]
        self.assertIn("response.function_call_arguments.delta", types)
        self.assertEqual(types[-1], "response.completed")

    def test_json_replies_stay_buffered(self):
        with mock.patch("gemini_web2api.server.generate",
                        return_value='{"a": 1}') as generate, \
                mock.patch("gemini_web2api.server.generate_stream") as stream:
            status, _headers, body = self.post_json(
                "/v1/responses",
                {"input": "hi", "stream": True,
                 "text": {"format": {"type": "json_object"}}})
        self.assertEqual(status, 200)
        generate.assert_called_once()
        stream.assert_not_called()
        deltas = [p["delta"] for t, p in decode_sse(body)
                  if t == "response.output_text.delta"]
        self.assertEqual(deltas, ['{"a": 1}'])


class MalformedStructureTests(ServerTestCase):
    """Wrong-typed nested fields are 400s (or ignored), never 500s."""

    def test_chat_rejects_a_non_list_tools_field(self):
        for bad in ({"name": "f"}, "get_weather", 3):
            with self.subTest(tools=bad):
                status, _headers, body = self.post_json(
                    "/v1/chat/completions", {**CHAT_BODY, "tools": bad})
                self.assertEqual(status, 400)
                self.assertEqual(body["error"]["code"], "invalid_tools")

    def test_chat_rejects_a_non_scalar_tool_choice(self):
        status, _headers, body = self.post_json(
            "/v1/chat/completions", {**CHAT_BODY, "tool_choice": ["auto"]})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_tool_choice")

    def test_responses_rejects_a_non_list_tools_field(self):
        status, _headers, body = self.post_json(
            "/v1/responses", {"input": "hi", "tools": {"name": "f"}})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_tools")

    def test_google_ignores_a_mistyped_tool_config(self):
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            status, _headers, body = self.post_json(
                "/v1beta/models/gemini-3.6-flash:generateContent",
                {"contents": [{"parts": [{"text": "hi"}]}],
                 "toolConfig": "AUTO"})
        self.assertEqual(status, 200)
        self.assertEqual(
            body["candidates"][0]["content"]["parts"][0]["text"], "ok")

    def test_google_ignores_a_mistyped_function_calling_config(self):
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            status, _headers, _body = self.post_json(
                "/v1beta/models/gemini-3.6-flash:generateContent",
                {"contents": [{"parts": [{"text": "hi"}]}],
                 "toolConfig": {"functionCallingConfig": ["ANY"]},
                 "tools": [{"functionDeclarations": [{"name": "f"}]}]})
        self.assertEqual(status, 200)


class TrustedProxyTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        LIMITER._windows.clear()
        self.addCleanup(LIMITER.configure, 0, 60)
        self.addCleanup(LIMITER._windows.clear)

    def _limit(self, max_requests):
        self.CONFIG["rate_limit_max"] = max_requests
        self.CONFIG["rate_limit_window_sec"] = 60
        LIMITER.configure(max_requests, 60)
        LIMITER._windows.clear()

    def test_forwarded_addresses_get_separate_buckets_when_trusted(self):
        self.CONFIG["trust_proxy_headers"] = True
        self._limit(1)
        first = self.get("/v1/models",
                         headers={"X-Forwarded-For": "198.51.100.1"})
        second = self.get("/v1/models",
                          headers={"X-Forwarded-For": "198.51.100.2"})
        third = self.get("/v1/models",
                         headers={"X-Forwarded-For": "198.51.100.1"})
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200)
        self.assertEqual(third[0], 429)

    def test_forwarded_headers_are_ignored_until_trusted(self):
        self._limit(1)
        first = self.get("/v1/models",
                         headers={"X-Forwarded-For": "198.51.100.1"})
        second = self.get("/v1/models",
                          headers={"X-Forwarded-For": "198.51.100.2"})
        self.assertEqual(first[0], 200)
        # Same socket peer: a spoofed header must not mint a fresh bucket.
        self.assertEqual(second[0], 429)

    def test_history_records_the_forwarded_client_when_trusted(self):
        self.CONFIG["trust_proxy_headers"] = True
        _status, headers, _body = self.get(
            "/health", headers={"X-Forwarded-For": "198.51.100.7"})
        request_id = headers["X-Request-Id"]

        def recorded():
            return any(e["id"] == request_id
                       and e["client"] == "198.51.100.7"
                       for e in metrics.history(50))

        self.assertTrue(_wait_for(recorded))


class ProductionConfigTests(ConfigTestCase):
    def test_cookie_files_accept_an_environment_list(self):
        with mock.patch.dict(
                os.environ,
                {ENV_PREFIX + "COOKIE_FILES": "/a.json, /b.json"},
                clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["cookie_files"], ["/a.json", "/b.json"])

    def test_cookie_files_accept_a_json_array(self):
        with mock.patch.dict(
                os.environ,
                {ENV_PREFIX + "COOKIE_FILES": '["/a.json", "/b.json"]'},
                clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["cookie_files"], ["/a.json", "/b.json"])

    def test_new_production_keys_come_from_the_environment(self):
        with mock.patch.dict(
                os.environ,
                {ENV_PREFIX + "MAX_CONCURRENT_REQUESTS": "4",
                 ENV_PREFIX + "TRUST_PROXY_HEADERS": "true"},
                clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["max_concurrent_requests"], 4)
        self.assertTrue(self.CONFIG["trust_proxy_headers"])

    def test_nonsensical_numbers_are_clamped_not_kept(self):
        with mock.patch.dict(
                os.environ,
                {ENV_PREFIX + "MAX_IMAGE_BYTES": "0",
                 ENV_PREFIX + "MAX_CONCURRENT_REQUESTS": "-2",
                 ENV_PREFIX + "RATE_LIMIT_WINDOW_SEC": "0"},
                clear=False):
            load_config(None)
        self.assertGreater(self.CONFIG["max_image_bytes"], 0)
        self.assertEqual(self.CONFIG["max_concurrent_requests"], 0)
        self.assertEqual(self.CONFIG["rate_limit_window_sec"], 60)

    def test_cli_flags_reach_the_config(self):
        from gemini_web2api.__main__ import apply_cli_overrides, build_parser

        args = build_parser().parse_args(
            ["--cookie-files", "/a.json", "--cookie-files", "/b.json",
             "--max-concurrent", "4", "--trust-proxy-headers"])
        apply_cli_overrides(args)
        self.assertEqual(self.CONFIG["cookie_files"], ["/a.json", "/b.json"])
        self.assertEqual(self.CONFIG["max_concurrent_requests"], 4)
        self.assertTrue(self.CONFIG["trust_proxy_headers"])


if __name__ == "__main__":
    unittest.main()
