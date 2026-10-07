"""HTTP endpoint behaviour: OpenAI, Responses, Google-native and status routes."""
import base64
import json
import socket
import socketserver
import time
import unittest
from unittest import mock

from gemini_web2api import metrics
from gemini_web2api.models import MODELS
from tests.support import ServerTestCase, decode_sse, sse_deltas

IMAGE_B64 = base64.b64encode(b"fake png").decode()
CHAT_BODY = {"model": "gemini-3.6-flash",
             "messages": [{"role": "user", "content": "hello"}]}


class RoutingTests(ServerTestCase):
    def test_query_strings_do_not_break_routing(self):
        """Regression: exact-match routing 404'd on /v1/models?limit=100."""
        for path in ("/v1/models?limit=100", "/v1/models?page=2&limit=20"):
            with self.subTest(path=path):
                status, _headers, body = self.get_json(path)
                self.assertEqual(status, 200)
                self.assertEqual(body["object"], "list")

    def test_trailing_slash_is_tolerated(self):
        status, _headers, body = self.get_json("/v1/models/")
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "list")

    def test_unknown_path_returns_a_json_404(self):
        status, _headers, body = self.get_json("/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_unknown_post_path_returns_a_json_404(self):
        status, _headers, body = self.post_json("/v1/chat/completion", CHAT_BODY)
        self.assertEqual(status, 404)

    def test_unimplemented_endpoints_say_so_explicitly(self):
        for path in ("/v1/embeddings", "/v1/audio/speech", "/v1/images/generations"):
            with self.subTest(path=path):
                status, _headers, body = self.post_json(path, {})
                self.assertEqual(status, 501)
                self.assertIn("not implemented", body["error"]["message"])

    def test_head_is_supported_for_probes(self):
        status, _headers, body = self.request("HEAD", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, "")

    def test_options_preflight(self):
        status, headers, _body = self.request("OPTIONS", "/v1/chat/completions")
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])


class ModelListingTests(ServerTestCase):
    def test_openai_model_list(self):
        status, _headers, body = self.get_json("/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "list")
        self.assertEqual(len(body["data"]), len(MODELS))
        for entry in body["data"]:
            self.assertEqual(entry["object"], "model")
            self.assertIn(entry["id"], MODELS)

    def test_openai_single_model(self):
        """Regression: GET /v1/models/{id} used to 404."""
        status, _headers, body = self.get_json("/v1/models/gemini-3.6-flash")
        self.assertEqual(status, 200)
        self.assertEqual(body["id"], "gemini-3.6-flash")
        self.assertEqual(body["object"], "model")

    def test_openai_single_model_unknown(self):
        status, _headers, body = self.get_json("/v1/models/gpt-4")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "model_not_found")

    def test_google_model_list(self):
        status, _headers, body = self.get_json("/v1beta/models")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["models"]), len(MODELS))
        self.assertTrue(body["models"][0]["name"].startswith("models/"))

    def test_google_single_model(self):
        """Regression: this returned the entire list instead of one model."""
        status, _headers, body = self.get_json("/v1beta/models/gemini-3.6-flash")
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "models/gemini-3.6-flash")
        self.assertNotIn("models", body)

    def test_google_single_model_unknown(self):
        status, _headers, _body = self.get_json("/v1beta/models/nope")
        self.assertEqual(status, 404)


class HealthTests(ServerTestCase):
    def test_health_is_always_reachable(self):
        for path in ("/health", "/healthz", "/live"):
            with self.subTest(path=path):
                status, _headers, body = self.get_json(path)
                self.assertEqual(status, 200)
                self.assertEqual(body["status"], "ok")
                self.assertIn("uptime_sec", body)
                self.assertIn("version", body)

    def test_health_stays_public_when_keys_are_configured(self):
        self.CONFIG["api_keys"] = ["sk-secret"]
        status, _headers, _body = self.get_json("/health")
        self.assertEqual(status, 200)

    def test_ready_reports_auth_state(self):
        status, _headers, body = self.get_json("/ready")
        self.assertEqual(status, 200)
        self.assertTrue(body["ready"])
        self.assertTrue(any("authentication is disabled" in w
                            for w in body["checks"]["warnings"]))

    def test_ready_flags_a_missing_cookie_file(self):
        self.CONFIG["cookie_file"] = "/nonexistent/cookie.txt"
        status, _headers, body = self.get_json("/ready")
        self.assertEqual(status, 200)
        self.assertTrue(any("does not exist" in w for w in body["checks"]["warnings"]))

    def test_status_exposes_metrics_and_redacted_config(self):
        status, _headers, body = self.get_json("/status")
        self.assertEqual(status, 200)
        self.assertIn("metrics", body)
        self.assertIn("config", body)
        self.assertIn("rate_limit", body)
        self.assertIn("requests", body["metrics"]["counters"])

    def test_status_is_protected_when_keys_are_configured(self):
        self.CONFIG["api_keys"] = ["sk-secret"]
        status, _headers, _body = self.get_json("/status")
        self.assertEqual(status, 401)
        status, _headers, body = self.get_json(
            "/status", headers={"Authorization": "Bearer sk-secret"})
        self.assertEqual(status, 200)
        self.assertNotIn("sk-secret", json.dumps(body))


class RootNegotiationTests(ServerTestCase):
    def test_json_for_programmatic_clients(self):
        status, headers, body = self.get_json("/")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(sorted(body["models"]), sorted(MODELS.keys()))
        self.assertIn("application/json", headers["Content-Type"])

    def test_dashboard_for_browsers(self):
        status, headers, body = self.get("/", headers={"Accept": "text/html"})
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("<!doctype html>", body.lower())
        self.assertIn("gemini-web2api", body)
        # The state placeholder must have been substituted with valid JSON.
        self.assertNotIn("@@STATE@@", body)

    def test_dashboard_state_is_valid_json(self):
        _status, _headers, body = self.get("/", headers={"Accept": "text/html"})
        marker = "const STATE = "
        start = body.index(marker) + len(marker)
        end = body.index(";\n", start)
        state = json.loads(body[start:end].replace("<\\/", "</"))
        self.assertEqual(len(state["models"]), len(MODELS))
        self.assertGreater(len(state["endpoints"]), 5)
        for model in state["models"]:
            self.assertIn("category", model)

    def test_dashboard_makes_no_external_requests(self):
        _status, _headers, body = self.get("/", headers={"Accept": "text/html"})
        import re
        external = re.findall(r'(?:src|href)\s*=\s*"(https?://[^"]+)"', body)
        self.assertEqual(external, [])

    def test_json_can_be_forced_with_a_query(self):
        status, headers, _body = self.get("/", headers={"Accept": "text/html",
                                                        "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        status, headers, body = self.request("GET", "/?format=json",
                                             headers={"Accept": "text/html"})
        self.assertIn("application/json", headers["Content-Type"])


class ChatCompletionTests(ServerTestCase):
    @mock.patch("gemini_web2api.server.generate", return_value="a reply")
    def test_non_streaming(self, _generate):
        status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], "gemini-3.6-flash")
        self.assertTrue(body["id"].startswith("chatcmpl-"))
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertEqual(body["choices"][0]["message"]["content"], "a reply")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertIn("usage", body)

    @mock.patch("gemini_web2api.server.generate", return_value="a reply")
    def test_usage_is_reported(self, _generate):
        _status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY)
        usage = body["usage"]
        self.assertEqual(usage["total_tokens"],
                         usage["prompt_tokens"] + usage["completion_tokens"])

    @mock.patch("gemini_web2api.server.generate", return_value="a reply")
    def test_model_is_omitted_uses_the_default(self, _generate):
        status, _headers, body = self.post_json(
            "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200)
        self.assertEqual(body["model"], self.CONFIG["default_model"])

    @mock.patch("gemini_web2api.server.generate", return_value="a reply")
    def test_think_suffix_is_applied(self, generate):
        body = dict(CHAT_BODY, model="gemini-3.6-flash@think=1")
        self.post_json("/v1/chat/completions", body)
        self.assertEqual(generate.call_args.args[2], 1)

    @mock.patch("gemini_web2api.server.generate", return_value="a reply")
    def test_unknown_model_falls_back(self, _generate):
        status, _headers, body = self.post_json(
            "/v1/chat/completions", dict(CHAT_BODY, model="gpt-4"))
        self.assertEqual(status, 200)
        self.assertEqual(body["model"], self.CONFIG["default_model"])

    def test_unknown_model_is_rejected_in_strict_mode(self):
        self.CONFIG["strict_models"] = True
        status, _headers, body = self.post_json(
            "/v1/chat/completions", dict(CHAT_BODY, model="gpt-4"))
        self.assertEqual(status, 400)
        self.assertIn("Unknown model", body["error"]["message"])

    def test_invalid_think_level_is_a_400(self):
        status, _headers, body = self.post_json(
            "/v1/chat/completions", dict(CHAT_BODY, model="gemini-3.6-flash@think=99"))
        self.assertEqual(status, 400)
        self.assertIn("Invalid think level", body["error"]["message"])

    def test_malformed_json_is_a_400(self):
        status, _headers, body = self.post_json(
            "/v1/chat/completions", None, raw_body="{not json")
        self.assertEqual(status, 400)
        self.assertIn("JSON", body["error"]["message"])

    def test_json_array_body_is_a_400(self):
        status, _headers, _body = self.post_json(
            "/v1/chat/completions", None, raw_body="[1,2,3]")
        self.assertEqual(status, 400)

    def test_empty_messages_is_a_400(self):
        status, _headers, body = self.post_json(
            "/v1/chat/completions", {"model": "gemini-3.6-flash", "messages": []})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "empty_prompt")

    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_chunked_request_body(self, _generate):
        status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY, chunked=True)
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "ok")

    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_multi_turn_history_is_forwarded(self, generate):
        self.post_json("/v1/chat/completions", {
            "model": "gemini-3.6-flash",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
                {"role": "user", "content": "third"},
            ],
        })
        prompt = generate.call_args.args[0]
        for fragment in ("be brief", "first", "second", "third"):
            self.assertIn(fragment, prompt)

    @mock.patch("gemini_web2api.server.generate", side_effect=RuntimeError("boom"))
    def test_upstream_failure_is_a_502(self, _generate):
        status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 502)
        self.assertIn("boom", body["error"]["message"])

    @mock.patch("gemini_web2api.server.generate")
    def test_upstream_429_is_passed_through(self, generate):
        from gemini_web2api.gemini import GeminiUpstreamError
        generate.side_effect = GeminiUpstreamError("HTTP 429", status=429)
        status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["code"], "upstream_rate_limited")

    @mock.patch("gemini_web2api.server.generate")
    def test_stale_build_tag_gives_an_actionable_error(self, generate):
        from gemini_web2api.gemini import GeminiUpstreamError
        generate.side_effect = GeminiUpstreamError("HTTP 405", status=405)
        status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "stale_build_tag")
        self.assertIn("gemini_bl", body["error"]["message"])

    def test_request_id_is_returned(self):
        _status, headers, _body = self.get_json("/health")
        self.assertIn("X-Request-Id", headers)
        self.assertEqual(len(headers["X-Request-Id"]), 12)

    def test_request_ids_are_unique(self):
        first = self.get("/health")[1]["X-Request-Id"]
        second = self.get("/health")[1]["X-Request-Id"]
        self.assertNotEqual(first, second)


class ChatStreamingTests(ServerTestCase):
    @mock.patch("gemini_web2api.server.generate_stream")
    def test_chunk_sequence(self, generate_stream):
        generate_stream.return_value = iter(["hel", "lo"])
        status, headers, body = self.post("/v1/chat/completions",
                                          dict(CHAT_BODY, stream=True))
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        chunks = [payload for _event, payload in decode_sse(body)
                  if isinstance(payload, dict)]
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "hel"})
        self.assertEqual(chunks[2]["choices"][0]["delta"], {"content": "lo"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_sse_headers_disable_proxy_buffering(self, generate_stream):
        generate_stream.return_value = iter(["x"])
        _status, headers, _body = self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        self.assertEqual(headers["X-Accel-Buffering"], "no")
        self.assertEqual(headers["Connection"], "close")
        self.assertIn("no-cache", headers["Cache-Control"])

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_all_chunks_share_one_id(self, generate_stream):
        generate_stream.return_value = iter(["a", "b", "c"])
        _status, _headers, body = self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        ids = {payload["id"] for _e, payload in decode_sse(body) if isinstance(payload, dict)}
        self.assertEqual(len(ids), 1)

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_text_is_reassembled(self, generate_stream):
        generate_stream.return_value = iter(["The ", "answer ", "is 42."])
        _status, _headers, body = self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        self.assertEqual(sse_deltas(body), "The answer is 42.")

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_empty_deltas_are_not_forwarded(self, generate_stream):
        generate_stream.return_value = iter(["", "real", "", None] + [""])
        _status, _headers, body = self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        self.assertEqual(sse_deltas(body), "real")

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_stream_failure_still_terminates_with_done(self, generate_stream):
        """Regression: an exception mid-stream left the client with no [DONE]."""
        def broken():
            yield "partial"
            raise RuntimeError("connection dropped")
        generate_stream.return_value = broken()
        _status, _headers, body = self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        self.assertIn("partial", body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))
        events = [payload for _e, payload in decode_sse(body)]
        self.assertTrue(any(isinstance(p, dict) and "error" in p for p in events))

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_stream_failure_before_any_output(self, generate_stream):
        def broken():
            raise RuntimeError("upstream gone")
            yield  # pragma: no cover
        generate_stream.return_value = broken()
        _status, _headers, body = self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_include_usage_emits_a_final_usage_chunk(self, generate_stream):
        generate_stream.return_value = iter(["hello"])
        _status, _headers, body = self.post("/v1/chat/completions", dict(
            CHAT_BODY, stream=True, stream_options={"include_usage": True}))
        events = [payload for _e, payload in decode_sse(body) if isinstance(payload, dict)]
        with_usage = [e for e in events if "usage" in e]
        self.assertEqual(len(with_usage), 1)
        self.assertEqual(with_usage[0]["choices"], [])
        self.assertIn("total_tokens", with_usage[0]["usage"])
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    @mock.patch("gemini_web2api.server.generate")
    def test_streaming_with_tools_returns_one_chunk(self, generate):
        """Tool calls can only be recognised from the complete reply."""
        generate.return_value = '```tool_call\n{"name": "f", "arguments": {"a": 1}}\n```'
        _status, _headers, body = self.post("/v1/chat/completions", dict(
            CHAT_BODY, stream=True,
            tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}]))
        events = [p for _e, p in decode_sse(body) if isinstance(p, dict)]
        self.assertEqual(events[0]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(events[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"], "f")
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_tool_choice_none_still_streams(self, generate_stream):
        generate_stream.return_value = iter(["plain"])
        _status, _headers, body = self.post("/v1/chat/completions", dict(
            CHAT_BODY, stream=True, tool_choice="none",
            tools=[{"type": "function", "function": {"name": "f"}}]))
        self.assertEqual(sse_deltas(body), "plain")


class LegacyCompletionTests(ServerTestCase):
    @mock.patch("gemini_web2api.server.generate", return_value="42")
    def test_legacy_completions(self, _generate):
        status, _headers, body = self.post_json("/v1/completions", {
            "model": "gemini-3.6-flash", "prompt": "What is 6*7?"})
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "text_completion")
        self.assertTrue(body["id"].startswith("cmpl-"))
        self.assertEqual(body["choices"][0]["text"], "42")

    @mock.patch("gemini_web2api.server.generate", return_value="42")
    def test_legacy_completions_accepts_a_prompt_list(self, generate):
        status, _headers, _body = self.post_json("/v1/completions", {
            "model": "gemini-3.6-flash", "prompt": ["line one", "line two"]})
        self.assertEqual(status, 200)
        self.assertIn("line one", generate.call_args.args[0])

    def test_legacy_completions_requires_a_prompt(self):
        status, _headers, body = self.post_json("/v1/completions", {"model": "gemini-3.6-flash"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "empty_prompt")

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_legacy_completions_stream(self, generate_stream):
        generate_stream.return_value = iter(["for", "ty"])
        _status, _headers, body = self.post("/v1/completions", {
            "model": "gemini-3.6-flash", "prompt": "count", "stream": True})
        events = [p for _e, p in decode_sse(body) if isinstance(p, dict)]
        self.assertEqual(events[0]["object"], "text_completion")
        self.assertEqual("".join(e["choices"][0]["text"] for e in events), "forty")
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))


class ToolCallingTests(ServerTestCase):
    TOOLS = [{"type": "function", "function": {
        "name": "get_weather", "description": "Weather for a city",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}}]

    @mock.patch("gemini_web2api.server.generate")
    def test_tool_call_is_parsed_into_openai_shape(self, generate):
        generate.return_value = '```tool_call\n{"name": "get_weather", "arguments": {"city": "Tokyo"}}\n```'
        status, _headers, body = self.post_json("/v1/chat/completions", dict(
            CHAT_BODY, tools=self.TOOLS))
        self.assertEqual(status, 200)
        message = body["choices"][0]["message"]
        self.assertEqual(body["choices"][0]["finish_reason"], "tool_calls")
        self.assertIsNone(message["content"])
        call = message["tool_calls"][0]
        self.assertEqual(call["type"], "function")
        self.assertTrue(call["id"].startswith("call_"))
        self.assertEqual(call["function"]["name"], "get_weather")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"city": "Tokyo"})

    @mock.patch("gemini_web2api.server.generate")
    def test_tool_definitions_reach_the_prompt(self, generate):
        generate.return_value = "sure"
        self.post_json("/v1/chat/completions", dict(CHAT_BODY, tools=self.TOOLS))
        prompt = generate.call_args.args[0]
        self.assertIn("get_weather", prompt)
        self.assertIn("tool_call", prompt)

    @mock.patch("gemini_web2api.server.generate")
    def test_tool_choice_none_suppresses_the_tool_block(self, generate):
        generate.return_value = "plain text"
        _status, _headers, body = self.post_json("/v1/chat/completions", dict(
            CHAT_BODY, tools=self.TOOLS, tool_choice="none"))
        self.assertNotIn("Tool Use", generate.call_args.args[0])
        self.assertNotIn("tool_calls", body["choices"][0]["message"])

    @mock.patch("gemini_web2api.server.generate")
    def test_tool_choice_required_adds_a_constraint(self, generate):
        generate.return_value = "x"
        self.post_json("/v1/chat/completions", dict(CHAT_BODY, tools=self.TOOLS,
                                                    tool_choice="required"))
        self.assertIn("MUST call at least one tool", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.generate")
    def test_plain_reply_with_tools_has_no_tool_calls(self, generate):
        generate.return_value = "It is sunny."
        _status, _headers, body = self.post_json("/v1/chat/completions", dict(
            CHAT_BODY, tools=self.TOOLS))
        self.assertEqual(body["choices"][0]["message"]["content"], "It is sunny.")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("tool_calls", body["choices"][0]["message"])

    @mock.patch("gemini_web2api.server.generate")
    def test_tool_result_history_is_forwarded(self, generate):
        generate.return_value = "It is 21C."
        self.post_json("/v1/chat/completions", {
            "model": "gemini-3.6-flash", "tools": self.TOOLS,
            "messages": [
                {"role": "user", "content": "Weather in Tokyo?"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'}}]},
                {"role": "tool", "tool_call_id": "call_1", "name": "get_weather",
                 "content": '{"temp": 21}'},
            ]})
        prompt = generate.call_args.args[0]
        self.assertIn("[Tool result for get_weather]", prompt)
        self.assertIn('{"city":"Tokyo"}', prompt.replace(" ", ""))


class ImageTests(ServerTestCase):
    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/ref")
    @mock.patch("gemini_web2api.server.generate", return_value="looks good")
    def test_data_url_image(self, generate, upload_image):
        status, _headers, body = self.post_json("/v1/chat/completions", {
            "model": "gemini-3.6-flash",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMAGE_B64}"}}]}]})
        self.assertEqual(status, 200)
        upload_image.assert_called_once_with(b"fake png", "image.png", "image/png")
        self.assertEqual(generate.call_args.args[3], ["/uploaded/ref"])
        self.assertIn("[Image attached]", generate.call_args.args[0])
        self.assertEqual(body["choices"][0]["message"]["content"], "looks good")

    @mock.patch("gemini_web2api.server.fetch_image_bytes", return_value=b"\xff\xd8\xffjpegdata")
    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/remote")
    @mock.patch("gemini_web2api.server.generate", return_value="remote ok")
    def test_remote_image_url_is_fetched_and_mime_sniffed(self, _generate, upload_image, fetch):
        status, _headers, _body = self.post_json("/v1/responses", {
            "model": "gemini-3.6-flash",
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": "What is shown?"},
                {"type": "input_image", "image_url": "https://example.com/image.jpg"}]}]})
        self.assertEqual(status, 200)
        fetch.assert_called_once_with("https://example.com/image.jpg")
        # The client said image/png; the bytes say JPEG. The bytes win.
        upload_image.assert_called_once_with(b"\xff\xd8\xffjpegdata", "image.png", "image/jpeg")

    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/ref")
    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_responses_top_level_input_image(self, _generate, upload_image):
        status, _headers, _body = self.post_json("/v1/responses", {
            "model": "gemini-3.6-flash",
            "input": [{"type": "input_text", "text": "What is shown?"},
                      {"type": "input_image", "image_url": f"data:image/png;base64,{IMAGE_B64}"}]})
        self.assertEqual(status, 200)
        upload_image.assert_called_once_with(b"fake png", "image.png", "image/png")

    @mock.patch("gemini_web2api.server.fetch_image_bytes", return_value=b"")
    def test_unfetchable_image_is_a_client_error(self, _fetch):
        status, _headers, body = self.post_json("/v1/chat/completions", {
            "model": "gemini-3.6-flash",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "x"},
                {"type": "image_url", "image_url": {"url": "https://example.com/gone.png"}}]}]})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "image_rejected")

    @mock.patch("gemini_web2api.server.upload_image", side_effect=RuntimeError("upload denied"))
    def test_google_upload_failure_is_an_upstream_error(self, _upload):
        status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent",
            {"contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": "image/png", "data": IMAGE_B64}}]}]})
        self.assertEqual(status, 502)
        self.assertIn("image upload failed: upload denied", body["error"]["message"])

    @mock.patch("gemini_web2api.server.upload_image", return_value="/ref")
    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_multiple_images_produce_multiple_references(self, generate, _upload):
        self.post_json("/v1/chat/completions", {
            "model": "gemini-3.6-flash",
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMAGE_B64}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMAGE_B64}"}}]}]})
        self.assertEqual(generate.call_args.args[3], ["/ref", "/ref"])

    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_no_images_means_no_file_references(self, generate):
        self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertIsNone(generate.call_args.args[3])


class ResponsesApiTests(ServerTestCase):
    @mock.patch("gemini_web2api.server.generate", return_value="hello back")
    def test_string_input(self, _generate):
        status, _headers, body = self.post_json("/v1/responses", {
            "model": "gemini-3.6-flash", "input": "hello"})
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "response")
        self.assertTrue(body["id"].startswith("resp_"))
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"][0]["type"], "message")
        self.assertEqual(body["output"][0]["content"][0]["text"], "hello back")
        self.assertIn("input_tokens", body["usage"])

    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_instructions_become_a_system_message(self, generate):
        self.post_json("/v1/responses", {"model": "gemini-3.6-flash", "input": "hi",
                                         "instructions": "You are terse."})
        self.assertIn("You are terse.", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_function_call_output_becomes_a_tool_message(self, generate):
        self.post_json("/v1/responses", {
            "model": "gemini-3.6-flash",
            "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "weather?"}]},
                {"type": "function_call", "call_id": "c1", "name": "get_weather",
                 "arguments": '{"city":"Tokyo"}'},
                {"type": "function_call_output", "call_id": "c1", "name": "get_weather",
                 "output": '{"temp":21}'},
            ]})
        prompt = generate.call_args.args[0]
        self.assertIn("[Tool result for get_weather]", prompt)
        self.assertIn("```tool_call", prompt)

    def test_empty_input_is_a_400(self):
        status, _headers, body = self.post_json("/v1/responses", {
            "model": "gemini-3.6-flash", "input": []})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "empty_input")

    @mock.patch("gemini_web2api.server.parse_tool_calls")
    @mock.patch("gemini_web2api.server.generate", return_value="tool output")
    def test_function_call_output_items(self, _generate, parse_tool_calls):
        parse_tool_calls.return_value = ("", [{
            "id": "call_test", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Shanghai"}'}}])
        status, _headers, body = self.post_json("/v1/responses", {
            "model": "gemini-3.6-flash", "input": "weather",
            "tools": [{"type": "function", "name": "get_weather",
                       "description": "Get weather", "parameters": {"type": "object"}}]})
        self.assertEqual(status, 200)
        item = body["output"][0]
        self.assertEqual(item["type"], "function_call")
        self.assertEqual(item["name"], "get_weather")
        self.assertEqual(item["call_id"], "call_test")
        self.assertEqual(item["arguments"], '{"city":"Shanghai"}')

    @mock.patch("gemini_web2api.server.generate", return_value="hello")
    def test_stream_event_sequence(self, _generate):
        status, headers, body = self.post("/v1/responses", {
            "model": "gemini-3.6-flash", "input": "hello", "stream": True})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        events = decode_sse(body)
        self.assertEqual([event for event, _ in events], [
            "response.created", "response.in_progress", "response.output_item.added",
            "response.content_part.added", "response.output_text.delta",
            "response.output_text.done", "response.content_part.done",
            "response.output_item.done", "response.completed",
        ])
        self.assertEqual([payload["sequence_number"] for _e, payload in events],
                         list(range(1, len(events) + 1)))
        self.assertEqual(events[4][1]["delta"], "hello")
        self.assertEqual(events[-1][1]["response"]["status"], "completed")
        self.assertEqual(events[-1][1]["response"]["output"][0]["content"][0]["text"], "hello")

    @mock.patch("gemini_web2api.server.parse_tool_calls")
    @mock.patch("gemini_web2api.server.generate", return_value="tool output")
    def test_stream_function_call_event_sequence(self, _generate, parse_tool_calls):
        parse_tool_calls.return_value = ("", [{
            "id": "call_test", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Shanghai"}'}}])
        _status, _headers, body = self.post("/v1/responses", {
            "model": "gemini-3.6-flash", "input": "weather", "stream": True,
            "tools": [{"type": "function", "name": "get_weather",
                       "description": "Get weather", "parameters": {"type": "object"}}]})
        events = decode_sse(body)
        self.assertEqual([event for event, _ in events], [
            "response.created", "response.in_progress", "response.output_item.added",
            "response.function_call_arguments.delta", "response.function_call_arguments.done",
            "response.output_item.done", "response.completed",
        ])
        self.assertEqual(events[3][1]["delta"], '{"city":"Shanghai"}')
        self.assertEqual(events[-1][1]["response"]["output"][0]["name"], "get_weather")


class GoogleNativeTests(ServerTestCase):
    def generate_body(self, text="Stream this"):
        return {"contents": [{"role": "user", "parts": [{"text": text}]}]}

    @mock.patch("gemini_web2api.server.generate", return_value="an answer")
    def test_generate_content(self, _generate):
        status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent", self.generate_body())
        self.assertEqual(status, 200)
        self.assertEqual(body["candidates"][0]["content"]["parts"][0]["text"], "an answer")
        self.assertEqual(body["candidates"][0]["content"]["role"], "model")
        self.assertEqual(body["candidates"][0]["finishReason"], "STOP")
        self.assertIn("usageMetadata", body)
        self.assertEqual(body["modelVersion"], "gemini-3.6-flash")

    @mock.patch("gemini_web2api.server.generate", return_value="")
    def test_empty_reply_returns_a_placeholder_not_an_empty_part(self, _generate):
        _status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent", self.generate_body())
        self.assertTrue(body["candidates"][0]["content"]["parts"][0]["text"])

    @mock.patch("gemini_web2api.server.generate_stream", return_value=iter(["streamed"]))
    def test_stream_generate_content(self, _generate_stream):
        status, headers, body = self.post(
            "/v1beta/models/gemini-3.6-flash:streamGenerateContent", self.generate_body())
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        self.assertIn('"text": "streamed"', body)

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_stream_emits_a_terminating_frame(self, generate_stream):
        generate_stream.return_value = iter(["a", "b"])
        _status, _headers, body = self.post(
            "/v1beta/models/gemini-3.6-flash:streamGenerateContent", self.generate_body())
        frames = [p for _e, p in decode_sse(body)]
        self.assertIn("finishReason", frames[-1]["candidates"][0])
        self.assertEqual(frames[-1]["candidates"][0]["finishReason"], "STOP")
        self.assertIn("usageMetadata", frames[-1])

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_stream_failure_marks_the_final_frame(self, generate_stream):
        def broken():
            yield "partial"
            raise RuntimeError("dropped")
        generate_stream.return_value = broken()
        _status, _headers, body = self.post(
            "/v1beta/models/gemini-3.6-flash:streamGenerateContent", self.generate_body())
        frames = [p for _e, p in decode_sse(body)]
        self.assertEqual(frames[-1]["candidates"][0]["finishReason"], "OTHER")

    def test_model_is_required_in_the_path(self):
        status, _headers, _body = self.post_json("/v1beta/models/:generateContent",
                                                 self.generate_body())
        self.assertIn(status, (400, 404))

    def test_empty_contents_is_a_400(self):
        status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent", {"contents": []})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "empty_content")

    @mock.patch("gemini_web2api.server.generate")
    def test_system_instruction_is_forwarded(self, generate):
        generate.return_value = "ok"
        self.post_json("/v1beta/models/gemini-3.6-flash:generateContent", {
            "systemInstruction": {"parts": [{"text": "Answer in French"}]},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        self.assertIn("Answer in French", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.generate")
    def test_function_call_response_shape(self, generate):
        generate.return_value = '```function_call\n{"name": "get_weather", "args": {"city": "Tokyo"}}\n```'
        _status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent", {
                "tools": [{"functionDeclarations": [
                    {"name": "get_weather", "description": "w", "parameters": {"type": "object"}}]}],
                "contents": [{"role": "user", "parts": [{"text": "weather?"}]}]})
        parts = body["candidates"][0]["content"]["parts"]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["functionCall"]["name"], "get_weather")
        self.assertEqual(parts[0]["functionCall"]["args"], {"city": "Tokyo"})

    @mock.patch("gemini_web2api.server.generate")
    def test_mode_none_disables_tool_parsing(self, generate):
        generate.return_value = '```function_call\n{"name": "f", "args": {}}\n```'
        _status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent", {
                "tools": [{"functionDeclarations": [{"name": "f"}]}],
                "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}]})
        parts = body["candidates"][0]["content"]["parts"]
        self.assertEqual(len(parts), 1)
        self.assertIn("text", parts[0])

    @mock.patch("gemini_web2api.server.generate")
    def test_think_suffix_on_the_path_model(self, generate):
        generate.return_value = "ok"
        _status, _headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash@think=0:generateContent", self.generate_body())
        self.assertEqual(generate.call_args.args[2], 0)
        self.assertEqual(body["modelVersion"], "gemini-3.6-flash")

    def test_query_parameter_alt_key_is_accepted(self):
        """Gemini CLI sends ?alt=sse&key=... on the native endpoints."""
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            status, _headers, _body = self.post_json(
                "/v1beta/models/gemini-3.6-flash:generateContent?alt=sse",
                self.generate_body())
            self.assertEqual(status, 200)


class KeepAliveTests(ServerTestCase):
    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_protocol_is_http_1_1(self, _generate):
        from gemini_web2api.server import GeminiHandler
        self.assertEqual(GeminiHandler.protocol_version, "HTTP/1.1")

    def test_content_length_is_exact(self):
        _status, headers, body = self.get("/")
        self.assertEqual(int(headers["Content-Length"]), len(body.encode("utf-8")))


class ListenBacklogTests(unittest.TestCase):
    """The listen backlog must be big enough for clients that connect in bursts.

    ``socketserver`` defaults ``request_queue_size`` to 5. Once the backlog
    fills, the kernel drops the SYN and the client sits on its initial
    retransmit timeout — a full second — before it is even accepted. Measured
    here at 32 concurrent clients against the default: 95% of requests answered
    in under 10 ms while ~4% took ~1000 ms, a sharply bimodal distribution with
    almost nothing in between. That shape is the signature of backlog overflow
    rather than contention, and it is invisible to any single-request test
    because one request never fills a backlog of five.

    Raising it to 128 removed the second band entirely (worst case 1438 ms ->
    16 ms) and made latency scale smoothly to 128 concurrent clients.
    """

    def test_the_configured_backlog_actually_reaches_listen(self):
        """Asserting the class attribute alone would be nearly vacuous.

        An attribute that nothing reads passes just as happily, so the real
        ``listen()`` call is intercepted and its argument checked. This is what
        distinguishes "the knob is set" from "the kernel was told".
        """
        from gemini_web2api.server import GeminiHandler, ThreadedServer

        seen = []
        real_listen = socket.socket.listen

        def spy(self, *args):
            seen.append(args[0] if args else None)
            return real_listen(self, *args)

        with mock.patch.object(socket.socket, "listen", spy):
            server = ThreadedServer(("127.0.0.1", 0), GeminiHandler)
        try:
            self.assertEqual(
                seen, [ThreadedServer.request_queue_size],
                f"listen() was called with {seen}, not the configured "
                f"request_queue_size={ThreadedServer.request_queue_size}")
        finally:
            server.server_close()

    def test_the_backlog_is_not_left_at_the_stdlib_default(self):
        from gemini_web2api.server import ThreadedServer

        self.assertEqual(
            socketserver.TCPServer.request_queue_size, 5,
            "the stdlib default changed; re-check whether 128 is still the "
            "right ceiling and update this test's explanation")
        self.assertGreater(
            ThreadedServer.request_queue_size,
            socketserver.TCPServer.request_queue_size,
            "ThreadedServer is still inheriting the stdlib backlog of 5, so a "
            "burst of more than five simultaneous connections stalls for a "
            "second each")
        self.assertGreaterEqual(
            ThreadedServer.request_queue_size, 64,
            "a backlog below 64 will still overflow for a modest connection "
            "pool; chat UIs and CLI agents open several sockets at once")


class RequestHistoryTests(ServerTestCase):
    """The Activity tab's data source: /status exposes a bounded request log.

    History carries client addresses, so it must never reach the public
    dashboard state served from "/".
    """

    def setUp(self):
        super().setUp()
        metrics.reset()
        self.addCleanup(metrics.reset)
        self.CONFIG["history_max"] = 50

    def _history(self):
        status, _headers, body = self.get_json("/status")
        self.assertEqual(status, 200)
        return body["history"]

    def test_status_exposes_history(self):
        self.get("/health")
        entries = self._history()
        self.assertTrue(entries)
        paths = [e["path"] for e in entries]
        self.assertIn("/health", paths)

    def test_entry_shape(self):
        self.get("/health")
        entry = next(e for e in self._history() if e["path"] == "/health")
        for field in ("ts", "id", "method", "path", "status", "model", "ms", "client"):
            self.assertIn(field, entry, f"history entry is missing {field!r}")
        self.assertEqual(entry["method"], "GET")
        self.assertEqual(entry["status"], 200)

    def test_query_string_is_stripped(self):
        """Query strings can carry ?key=<api_key>; they must not be retained."""
        self.request("GET", "/?format=json&key=sk-super-secret")
        entries = self._history()
        self.assertTrue(entries)
        for entry in entries:
            self.assertNotIn("?", entry["path"])
            self.assertNotIn("sk-super-secret", json.dumps(entry))

    def test_errors_are_recorded(self):
        self.get("/definitely-not-a-route")
        statuses = {e["path"]: e["status"] for e in self._history()}
        self.assertEqual(statuses.get("/definitely-not-a-route"), 404)

    def test_resolved_model_is_recorded(self):
        """An unknown model silently falls back; history shows what actually ran."""
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            self.post_json("/v1/chat/completions",
                           dict(CHAT_BODY, model="gemini-9.9-does-not-exist"))
        entry = next(e for e in self._history() if e["path"] == "/v1/chat/completions")
        self.assertEqual(entry["model"], "gemini-3.6-flash")

    def test_history_max_zero_disables_it(self):
        self.CONFIG["history_max"] = 0
        self.get("/health")
        self.assertEqual(self._history(), [])

    def test_history_is_bounded_by_config(self):
        self.CONFIG["history_max"] = 3
        for _ in range(10):
            self.get("/health")
        self.assertLessEqual(len(self._history()), 3)

    def test_streaming_upstream_failure_is_counted(self):
        """Regression: a stream that failed mid-flight incremented neither
        upstream_failures nor anything else, so an outage affecting only
        streaming traffic looked like a healthy server. The HTTP status is
        legitimately 200 - the header left before the failure - so the counter
        is the only place the failure can be recorded."""
        metrics.reset()
        with mock.patch("gemini_web2api.server.generate_stream") as stream:
            stream.side_effect = RuntimeError("TLS/SSL connection has been closed")
            status, _headers, body = self.post(
                "/v1/chat/completions", dict(CHAT_BODY, stream=True))
        self.assertEqual(status, 200)
        self.assertIn('"error"', body)
        _s, _h, payload = self.get_json("/status")
        self.assertEqual(payload["metrics"]["counters"]["upstream_failures"], 1)

    def test_non_streaming_upstream_failure_is_also_counted(self):
        """The two paths must agree, or the counter means different things
        depending on how the client asked for the reply."""
        metrics.reset()
        with mock.patch("gemini_web2api.server.generate",
                        side_effect=RuntimeError("boom")):
            status, _headers, _body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 502)
        _s, _h, payload = self.get_json("/status")
        self.assertEqual(payload["metrics"]["counters"]["upstream_failures"], 1)

    def test_streaming_duration_covers_the_whole_exchange(self):
        """Regression: history was written when the SSE headers went out, so a
        stream that took four seconds to generate was logged as time-to-first-
        byte. The Activity tab's latency column was worse than useless."""
        metrics.reset()

        def slow_stream(*_args, **_kwargs):
            yield "first"
            time.sleep(0.25)
            yield "last"

        with mock.patch("gemini_web2api.server.generate_stream", slow_stream):
            self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))

        entry = next(e for e in self._history() if e["path"] == "/v1/chat/completions")
        self.assertGreaterEqual(entry["ms"], 250.0,
                                f"stream duration looks like time-to-first-byte: {entry['ms']}ms")
        self.assertEqual(entry["status"], 200)

    def test_non_streaming_duration_is_also_total(self):
        """The two paths must measure the same thing."""
        metrics.reset()

        def slow_generate(*_args, **_kwargs):
            time.sleep(0.2)
            return "done"

        with mock.patch("gemini_web2api.server.generate", slow_generate):
            self.post_json("/v1/chat/completions", CHAT_BODY)
        entry = next(e for e in self._history() if e["path"] == "/v1/chat/completions")
        self.assertGreaterEqual(entry["ms"], 200.0)

    def test_history_is_written_even_when_the_stream_fails(self):
        """A failed stream must still appear in the log - that is exactly the
        entry an operator is looking for."""
        metrics.reset()
        with mock.patch("gemini_web2api.server.generate_stream",
                        side_effect=RuntimeError("upstream went away")):
            self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        entries = [e for e in self._history() if e["path"] == "/v1/chat/completions"]
        self.assertEqual(len(entries), 1, f"expected exactly one entry, got {entries}")

    def test_request_is_not_recorded_twice(self):
        """_record() is idempotent and _finish_history() runs in a finally, so
        an error response after an opened stream must not double-log."""
        metrics.reset()
        with mock.patch("gemini_web2api.server.generate_stream") as stream:
            stream.return_value = iter(["ok"])
            self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        entries = [e for e in self._history() if e["path"] == "/v1/chat/completions"]
        self.assertEqual(len(entries), 1)
        _s, _h, payload = self.get_json("/status")
        self.assertEqual(payload["metrics"]["counters"]["requests"], len(payload["history"]))

    def test_streaming_request_is_counted(self):
        """Regression: _start_sse() returned before _record(), so streamed
        replies never appeared in requests_served or status_codes."""
        with mock.patch("gemini_web2api.server.generate_stream") as stream:
            stream.return_value = iter(["hello"])
            self.post("/v1/chat/completions", dict(CHAT_BODY, stream=True))
        status, _headers, body = self.get_json("/status")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["metrics"]["counters"]["requests"], 1)
        self.assertIn("200", body["metrics"]["status_codes"])
        self.assertTrue(any(e["path"] == "/v1/chat/completions"
                            for e in body["history"]))

    def _dashboard_state(self):
        _status, _headers, body = self.get("/", headers={"Accept": "text/html"})
        marker = "const STATE = "
        start = body.index(marker) + len(marker)
        end = body.index(";\n", start)
        return json.loads(body[start:end].replace("<\\/", "</"))

    def test_history_is_absent_from_the_public_dashboard(self):
        """/ is unauthenticated, so its embedded state must not carry history.

        History entries are the only place a "client" or "ms" field appears, so
        asserting those keys are absent proves no entry was serialised into the
        page - a stronger check than looking for one known path, since routes
        like /health legitimately appear in the documented endpoint list.
        """
        self.get("/health")
        entries = self._history()
        self.assertTrue(entries, "expected /status to expose history first")
        self.assertTrue(any("client" in e for e in entries))

        state = self._dashboard_state()
        blob = json.dumps(state)
        self.assertNotIn("history", state)
        self.assertNotIn('"client"', blob)
        self.assertNotIn('"ms"', blob)
        # The page may say *whether* history is on, never *what* is in it.
        self.assertIn("history_enabled", state)

    def test_dashboard_state_leaks_no_request_ids(self):
        """Each history entry carries its X-Request-Id; none may reach "/". """
        _status, headers, _body = self.get("/health")
        request_id = headers["X-Request-Id"]
        self.assertIn(request_id, [e["id"] for e in self._history()])
        self.assertNotIn(request_id, json.dumps(self._dashboard_state()))


if __name__ == "__main__":
    unittest.main()
