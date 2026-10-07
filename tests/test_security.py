"""Security: authentication, SSRF, request limits and rate limiting."""
import json
import socket
import unittest
from unittest import mock

from gemini_web2api.multimodal import (
    ImageFetchError,
    _is_blocked_address,
    fetch_image_bytes,
)
from gemini_web2api.ratelimit import LIMITER, RateLimiter
from tests.support import ServerTestCase

CHAT_BODY = {"model": "gemini-3.6-flash",
             "messages": [{"role": "user", "content": "hello"}]}


class ApiKeyTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.CONFIG["api_keys"] = ["sk-secret", "sk-second"]

    def test_no_credentials_is_401(self):
        status, headers, body = self.get_json("/v1/models")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["type"], "authentication_error")
        self.assertIn("WWW-Authenticate", headers)

    def test_wrong_key_is_401(self):
        status, _headers, _body = self.get_json(
            "/v1/models", headers={"Authorization": "Bearer sk-wrong"})
        self.assertEqual(status, 401)

    def test_bearer_scheme_is_case_insensitive(self):
        status, _headers, _body = self.get_json(
            "/v1/models", headers={"Authorization": "bearer sk-secret"})
        self.assertEqual(status, 200)

    def test_partial_key_is_rejected(self):
        status, _headers, _body = self.get_json(
            "/v1/models", headers={"Authorization": "Bearer sk-sec"})
        self.assertEqual(status, 401)

    def test_x_api_key_header(self):
        status, _headers, _body = self.get_json(
            "/v1/models", headers={"x-api-key": "sk-second"})
        self.assertEqual(status, 200)

    def test_x_goog_api_key_header(self):
        status, _headers, _body = self.get_json(
            "/v1beta/models", headers={"x-goog-api-key": "sk-secret"})
        self.assertEqual(status, 200)

    def test_key_query_parameter(self):
        status, _headers, _body = self.get_json("/v1/models?key=sk-secret")
        self.assertEqual(status, 200)

    def test_post_endpoints_are_protected(self):
        status, _headers, _body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 401)

    def test_post_with_a_valid_key(self):
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            status, _headers, _body = self.post_json(
                "/v1/chat/completions", CHAT_BODY,
                headers={"Authorization": "Bearer sk-secret"})
            self.assertEqual(status, 200)

    def test_google_native_endpoints_are_protected(self):
        status, _headers, _body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent", {"contents": []})
        self.assertEqual(status, 401)

    def test_rejected_post_does_not_desynchronise_the_connection(self):
        """The body must be consumed before an early 401, or HTTP/1.1 keep-alive
        leaves unread bytes that corrupt the next request on the connection."""
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = json.dumps(CHAT_BODY)
        connection.request("POST", "/v1/chat/completions", body=payload,
                           headers={"Content-Type": "application/json"})
        first = connection.getresponse()
        first.read()
        self.assertEqual(first.status, 401)

        # Same connection, now with credentials. The patch must be installed
        # BEFORE the request is sent: patching after `connection.request()`
        # races the server thread, which can reach the real upstream and answer
        # 502 instead of 200.
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            connection.request("POST", "/v1/chat/completions", body=payload,
                               headers={"Content-Type": "application/json",
                                        "Authorization": "Bearer sk-secret"})
            second = connection.getresponse()
            body = second.read().decode()
        self.assertEqual(second.status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "ok")
        connection.close()

    def test_public_paths_need_no_key(self):
        for path in ("/health", "/healthz", "/live", "/ready", "/"):
            with self.subTest(path=path):
                status, _headers, _body = self.get_json(path)
                self.assertEqual(status, 200)

    def test_auth_disabled_when_no_keys_configured(self):
        self.CONFIG["api_keys"] = []
        status, _headers, _body = self.get_json("/v1/models")
        self.assertEqual(status, 200)

    def test_error_body_does_not_echo_the_key(self):
        _status, _headers, body = self.get_json(
            "/v1/models", headers={"Authorization": "Bearer sk-typo"})
        self.assertNotIn("sk-typo", body)


class SsrfPolicyTests(unittest.TestCase):
    """Image URLs are fetched server-side, so they must not reach internal hosts."""

    def blocked(self, host):
        with self.subTest(host=host):
            self.assertTrue(_is_blocked_address(host), f"{host} should be blocked")

    def test_loopback_is_blocked(self):
        for host in ("127.0.0.1", "localhost", "127.5.5.5"):
            self.blocked(host)

    def test_cloud_metadata_endpoint_is_blocked(self):
        self.blocked("169.254.169.254")

    def test_private_ranges_are_blocked(self):
        for host in ("10.0.0.1", "192.168.1.1", "172.16.0.1", "172.31.255.255"):
            self.blocked(host)

    def test_ipv6_loopback_is_blocked(self):
        self.blocked("::1")

    def test_ipv4_mapped_ipv6_loopback_is_blocked(self):
        self.blocked("::ffff:127.0.0.1")

    def test_unspecified_and_multicast_are_blocked(self):
        self.blocked("0.0.0.0")
        self.blocked("224.0.0.1")

    def test_unresolvable_host_is_blocked(self):
        self.assertTrue(_is_blocked_address("this-host-does-not-exist.invalid"))

    def test_public_address_is_allowed(self):
        with mock.patch("socket.getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "",
                                       ("93.184.216.34", 0))]):
            self.assertFalse(_is_blocked_address("example.com"))

    def test_policy_can_be_disabled_for_trusted_networks(self):
        from gemini_web2api.config import CONFIG
        saved = CONFIG.get("block_private_image_urls")
        CONFIG["block_private_image_urls"] = False
        try:
            self.assertFalse(_is_blocked_address("127.0.0.1"))
        finally:
            CONFIG["block_private_image_urls"] = saved


class FetchImageTests(unittest.TestCase):
    """Image-fetch policy tests.

    No test here may touch the network. setUp installs a hard blocker on
    socket connection so an accidental real request fails immediately and
    deterministically. That is not hypothetical: test_redirects_are_revalidated
    used to mock getaddrinfo but not the opener, so it really connected to
    93.184.216.34 (example.com). Its result then depended on external network
    state - it passed when the connection timed out and failed when it succeeded
    and returned bytes - and it added seconds of latency to the suite.
    """

    def setUp(self):
        from gemini_web2api.config import CONFIG
        self._saved = dict(CONFIG)
        CONFIG["log_requests"] = False

        # Record attempts rather than only raising: fetch_image_bytes swallows
        # every Exception by contract ("raise nothing, return b\"\")", and
        # AssertionError IS an Exception, so a raise-only guard would be caught
        # and discarded - the test would pass while a real connection happened.
        self._network_attempts = []

        def _no_network(*args, **kwargs):
            self._network_attempts.append(args[:1] or kwargs)
            raise OSError("network I/O is disabled in FetchImageTests")

        self._patches = [
            mock.patch("socket.socket.connect", side_effect=_no_network),
            mock.patch("socket.socket.connect_ex", side_effect=_no_network),
            mock.patch("socket.create_connection", side_effect=_no_network),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        from gemini_web2api.config import CONFIG
        CONFIG.clear()
        CONFIG.update(self._saved)
        # Assert after restoring CONFIG so a failure here is not masked.
        self.assertEqual(
            self._network_attempts, [],
            "FetchImageTests must not perform real network I/O; mock "
            "urllib.request.OpenerDirector.open as well as socket.getaddrinfo. "
            f"Attempted: {self._network_attempts}"
        )

    def test_non_http_schemes_are_refused(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x.png",
                    "gopher://127.0.0.1:6379/_INFO", "data:image/png;base64,AAAA",
                    "/etc/passwd", ""):
            with self.subTest(url=url):
                self.assertEqual(fetch_image_bytes(url), b"")

    def test_loopback_is_refused(self):
        self.assertEqual(fetch_image_bytes("http://127.0.0.1:8081/secret"), b"")
        self.assertEqual(fetch_image_bytes("http://localhost/secret"), b"")

    def test_metadata_endpoint_is_refused(self):
        self.assertEqual(
            fetch_image_bytes("http://169.254.169.254/latest/meta-data/iam/"), b"")

    def test_credentials_in_the_url_are_refused(self):
        self.assertEqual(fetch_image_bytes("https://user:pass@93.184.216.34/x.png"), b"")

    def test_redirect_to_internal_host_is_refused(self):
        """The guard itself must reject a private redirect target.

        urlopen follows redirects transparently, so without re-validation a
        public URL could 302 to http://169.254.169.254/ and defeat a check that
        only inspected the original target.
        """
        from gemini_web2api.multimodal import _GuardedRedirect

        handler = _GuardedRedirect()
        request = mock.MagicMock(full_url="http://public.example/x.png")
        for internal in ("http://127.0.0.1/secret",
                         "http://169.254.169.254/latest/meta-data/",
                         "http://192.168.1.1/admin",
                         "file:///etc/passwd"):
            with self.subTest(target=internal), self.assertRaises(ImageFetchError):
                handler.redirect_request(request, None, 302, "Found", {}, internal)

    def test_redirect_to_public_host_is_delegated(self):
        """A public target must fall through to the normal redirect handling.

        The guard's contract is "re-validate, then delegate". Patching the
        parent method to a sentinel proves delegation happens without having to
        reproduce urllib's internal Request bookkeeping.
        """
        from gemini_web2api.multimodal import _GuardedRedirect

        handler = _GuardedRedirect()
        request = mock.MagicMock(full_url="http://public.example/x.png")
        sentinel = object()
        with mock.patch("socket.getaddrinfo",
                        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "",
                                       ("93.184.216.34", 0))]), \
             mock.patch("urllib.request.HTTPRedirectHandler.redirect_request",
                        return_value=sentinel) as parent:
            result = handler.redirect_request(
                request, None, 302, "Found", {}, "http://cdn.example/x.png")
        self.assertIs(result, sentinel)
        parent.assert_called_once()

    def test_fetch_refuses_when_a_redirect_hop_is_internal(self):
        """End to end: a public URL that redirects internally yields b""."""
        from gemini_web2api.multimodal import _GuardedRedirect

        def guard(self_, req, fp, code, msg, headers, newurl):
            raise ImageFetchError(f"redirect to {newurl} refused")

        with mock.patch("gemini_web2api.multimodal._validate_url"), \
             mock.patch.object(_GuardedRedirect, "redirect_request", guard), \
             mock.patch("urllib.request.OpenerDirector.open",
                        side_effect=ImageFetchError("redirect refused")):
            self.assertEqual(fetch_image_bytes("http://public.example/x.png"), b"")

    def test_oversized_image_is_refused(self):
        from gemini_web2api.config import CONFIG
        CONFIG["max_image_bytes"] = 10
        payload = b"x" * 100
        response = mock.MagicMock()
        response.headers = {"Content-Length": "100"}
        response.read.return_value = payload[:11]
        response.__enter__ = lambda self_: response
        response.__exit__ = lambda *a: False

        with mock.patch("gemini_web2api.multimodal._validate_url"), \
             mock.patch("urllib.request.OpenerDirector.open", return_value=response):
            self.assertEqual(fetch_image_bytes("https://example.com/big.png"), b"")

    def test_declared_content_length_over_the_limit_is_refused_early(self):
        from gemini_web2api.config import CONFIG
        CONFIG["max_image_bytes"] = 10
        response = mock.MagicMock()
        response.headers = {"Content-Length": "99999999"}
        response.__enter__ = lambda self_: response
        response.__exit__ = lambda *a: False
        with mock.patch("gemini_web2api.multimodal._validate_url"), \
             mock.patch("urllib.request.OpenerDirector.open", return_value=response):
            self.assertEqual(fetch_image_bytes("https://example.com/huge.png"), b"")

    def test_network_errors_return_empty_rather_than_raising(self):
        with mock.patch("gemini_web2api.multimodal._validate_url"), \
             mock.patch("urllib.request.OpenerDirector.open",
                        side_effect=socket.timeout("timed out")):
            self.assertEqual(fetch_image_bytes("https://example.com/x.png"), b"")


class RequestSizeTests(ServerTestCase):
    def test_oversized_content_length_is_413(self):
        self.CONFIG["max_request_bytes"] = 64
        status, _headers, body = self.post_json("/v1/chat/completions", CHAT_BODY)
        self.assertEqual(status, 413)
        self.assertEqual(body["error"]["code"], "payload_too_large")

    def test_oversized_chunked_body_is_413(self):
        self.CONFIG["max_request_bytes"] = 64
        status, _headers, _body = self.post_json("/v1/chat/completions", CHAT_BODY, chunked=True)
        self.assertEqual(status, 413)

    def test_body_within_the_limit_is_accepted(self):
        self.CONFIG["max_request_bytes"] = 10_000_000
        with mock.patch("gemini_web2api.server.generate", return_value="ok"):
            status, _headers, _body = self.post_json("/v1/chat/completions", CHAT_BODY)
            self.assertEqual(status, 200)

    def test_malformed_content_length_is_400_not_500(self):
        status, _headers, _body = self.request(
            "POST", "/v1/chat/completions", raw_body="x",
            headers={"Content-Length": "not-a-number"})
        self.assertEqual(status, 400)

    def test_negative_content_length_is_400(self):
        status, _headers, _body = self.request(
            "POST", "/v1/chat/completions", raw_body="",
            headers={"Content-Length": "-5"})
        self.assertEqual(status, 400)


class RateLimitUnitTests(unittest.TestCase):
    def test_disabled_by_default(self):
        limiter = RateLimiter(0, 60)
        self.assertFalse(limiter.enabled)
        for _ in range(100):
            self.assertEqual(limiter.check("k")[0], True)

    def test_limit_is_enforced_per_key(self):
        limiter = RateLimiter(3, 60)
        self.assertEqual([limiter.check("a")[0] for _ in range(5)],
                         [True, True, True, False, False])
        self.assertTrue(limiter.check("b")[0])

    def test_remaining_count_decreases(self):
        limiter = RateLimiter(2, 60)
        self.assertEqual(limiter.check("a")[2], 1)
        self.assertEqual(limiter.check("a")[2], 0)

    def test_retry_after_is_positive_when_limited(self):
        limiter = RateLimiter(1, 60)
        limiter.check("a")
        allowed, retry_after, _remaining = limiter.check("a")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)

    def test_window_expiry_resets_the_count(self):
        limiter = RateLimiter(1, 60)
        self.assertTrue(limiter.check("a")[0])
        self.assertFalse(limiter.check("a")[0])
        limiter._windows["a"] = (limiter._windows["a"][0] - 61, 1)
        self.assertTrue(limiter.check("a")[0])

    def test_reconfigure_at_runtime(self):
        limiter = RateLimiter(0, 60)
        self.assertTrue(limiter.check("a")[0])
        limiter.configure(1, 60)
        self.assertTrue(limiter.check("a")[0])
        self.assertFalse(limiter.check("a")[0])
        limiter.configure(0, 60)
        self.assertTrue(limiter.check("a")[0])

    def test_sweeps_expired_keys(self):
        limiter = RateLimiter(5, 60)
        for index in range(500):
            limiter.check(f"key-{index}")
        self.assertEqual(limiter.snapshot()["tracked_keys"], 500)
        now = limiter._windows["key-0"][0] + 61
        with mock.patch("time.time", return_value=now):
            limiter._last_sweep = 0
            limiter.check("fresh")
        self.assertLess(limiter.snapshot()["tracked_keys"], 10)

    def test_snapshot_shape(self):
        limiter = RateLimiter(10, 30)
        snapshot = limiter.snapshot()
        self.assertEqual(snapshot, {"enabled": True, "max_requests": 10,
                                    "window_sec": 30.0, "tracked_keys": 0})


class RateLimitEndpointTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.CONFIG["api_keys"] = ["sk-test"]
        self.CONFIG["rate_limit_max"] = 3
        self.CONFIG["rate_limit_window_sec"] = 60
        LIMITER.configure(3, 60)
        LIMITER._windows.clear()

    def tearDown(self):
        LIMITER.configure(0, 60)
        LIMITER._windows.clear()
        super().tearDown()

    def test_requests_beyond_the_limit_get_429(self):
        headers = {"Authorization": "Bearer sk-test"}
        statuses = [self.get("/v1/models", headers=headers)[0] for _ in range(5)]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3:], [429, 429])

    def test_429_body_is_openai_shaped(self):
        headers = {"Authorization": "Bearer sk-test"}
        for _ in range(4):
            status, response_headers, body = self.get("/v1/models", headers=headers)
        self.assertEqual(status, 429)
        payload = json.loads(body)["error"]
        self.assertEqual(payload["type"], "rate_limit_error")
        self.assertEqual(payload["code"], "rate_limit_exceeded")

    def test_limits_are_tracked_per_key(self):
        self.CONFIG["api_keys"] = ["sk-a", "sk-b"]
        for _ in range(3):
            self.get("/v1/models", headers={"Authorization": "Bearer sk-a"})
        self.assertEqual(self.get("/v1/models", headers={"Authorization": "Bearer sk-a"})[0], 429)
        self.assertEqual(self.get("/v1/models", headers={"Authorization": "Bearer sk-b"})[0], 200)

    def test_anonymous_requests_are_limited_by_ip(self):
        self.CONFIG["api_keys"] = []
        statuses = [self.get("/v1/models")[0] for _ in range(5)]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3:], [429, 429])

    def test_public_paths_are_not_limited(self):
        for _ in range(10):
            self.assertEqual(self.get("/health")[0], 200)

    def test_status_reports_the_limiter(self):
        _status, _headers, body = self.get_json(
            "/status", headers={"Authorization": "Bearer sk-test"})
        self.assertTrue(body["rate_limit"]["enabled"])


class InformationLeakTests(ServerTestCase):
    def test_config_snapshot_never_contains_a_key(self):
        self.CONFIG["api_keys"] = ["sk-super-secret-value"]
        _status, _headers, body = self.get_json(
            "/status", headers={"Authorization": "Bearer sk-super-secret-value"})
        self.assertNotIn("sk-super-secret-value", body)

    def test_xsrf_token_is_not_exposed(self):
        self.CONFIG["xsrf_token"] = "AOOh0P-secret-xsrf"
        _status, _headers, body = self.get_json("/status")
        self.assertNotIn("AOOh0P-secret-xsrf", body)

    def test_internal_exceptions_do_not_leak_paths(self):
        with mock.patch("gemini_web2api.server.generate",
                        side_effect=ValueError("/home/deploy/secrets/cookie.txt")):
            _status, _headers, raw = self.post("/v1/chat/completions", CHAT_BODY)
        # The message is surfaced (it is the client's error report), but the
        # response must stay JSON and never a stack trace.
        self.assertNotIn("Traceback", raw)
        self.assertTrue(raw.startswith("{"))
        self.assertIn("error", json.loads(raw))


if __name__ == "__main__":
    unittest.main()
