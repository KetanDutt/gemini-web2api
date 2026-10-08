"""The upstream transport: connection reuse, incremental reads, timeouts.

Latency in this server is almost entirely the upstream leg, and the upstream
leg is almost entirely the transport: whether a request pays for a TCP+TLS
handshake, whether the first token can be handed to the client before the last
one exists, and whether a stalled connect is bounded. None of that is visible
in the protocol tests, which feed the stream a scripted list of chunks.

So these tests use a real socket. A local stand-in for Gemini speaks the same
framing, counts the connections it accepts, spaces its frames out in time, and
can close a connection between requests on purpose — which is the race a
keep-alive pool has to survive, because a socket the far end closed while it
was idle looks perfectly healthy right up until it is used.
"""
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from gemini_web2api import gemini
from gemini_web2api.gemini import _StdlibStatusError, generate, generate_stream

try:
    import httpx
except ImportError:  # pragma: no cover - depends on the environment
    httpx = None

from tests.support import ConfigTestCase

WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten"]


class FakeGemini(BaseHTTPRequestHandler):
    """A Gemini stand-in: framed frames, one per word, spaced out in time."""

    protocol_version = "HTTP/1.1"

    #: Seconds a frame is held back, so incremental reads are observable.
    frame_delay = 0.02
    #: Answer every request with this status instead of the stream.
    status = 200
    #: Hang up after each response, to exercise the stale-connection race.
    close_after_response = False

    connections = 0
    requests = 0
    _lock = threading.Lock()

    @classmethod
    def reset(cls):
        cls.connections = 0
        cls.requests = 0
        cls.frame_delay = 0.02
        cls.status = 200
        cls.close_after_response = False

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        with self._lock:
            type(self).connections += 1

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        with self._lock:
            type(self).requests += 1

        if self.status != 200:
            body = b'{"error": {"message": "quota exceeded"}}'
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        frames, cumulative = [], ""
        for word in WORDS:
            cumulative += word + " "
            payload = json.dumps([["wrb.fr", None, json.dumps(
                [None, None, None, None, [[None, [cumulative]]]])]])
            frames.append(("\n".join([")]}'", str(len(payload)), payload, ""])).encode())

        body = b"".join(frames)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for index, frame in enumerate(frames):
            self.wfile.write(frame)
            self.wfile.flush()
            if index < len(frames) - 1:
                time.sleep(self.frame_delay)
        if self.close_after_response:
            self.close_connection = True


class ChunkedGemini(FakeGemini):
    """The same frames, sent with ``Transfer-Encoding: chunked``."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        with self._lock:
            type(self).requests += 1
        frames, cumulative = [], ""
        for word in WORDS:
            cumulative += word + " "
            payload = json.dumps([["wrb.fr", None, json.dumps(
                [None, None, None, None, [[None, [cumulative]]]])]])
            frame = ("\n".join([")]}'", str(len(payload)), payload, ""])).encode()
            frames.append(b"%x\r\n%s\r\n" % (len(frame), frame))
        frames.append(b"0\r\n\r\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index, frame in enumerate(frames):
            self.wfile.write(frame)
            self.wfile.flush()
            if index < len(frames) - 1:
                time.sleep(self.frame_delay)


class TransportTestCase(ConfigTestCase):
    """A fake upstream, and gemini.py pointed at it."""

    def setUp(self):
        super().setUp()
        FakeGemini.reset()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeGemini)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"

        self._saved_transport = (gemini.GEMINI_ORIGIN, gemini.HAS_HTTPX)
        gemini.GEMINI_ORIGIN = self.origin
        # The stdlib transport is the one under test; the httpx path has its own
        # tests below and skipping its import keeps the two independent.
        gemini.HAS_HTTPX = False
        gemini.close_client()
        self.CONFIG["retry_attempts"] = 1
        self.CONFIG["retry_delay_sec"] = 0
        self.CONFIG["request_timeout_sec"] = 10
        self.CONFIG["auto_update_bl"] = False

    def tearDown(self):
        gemini.close_client()
        gemini.GEMINI_ORIGIN, gemini.HAS_HTTPX = self._saved_transport
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        super().tearDown()


class ConnectionReuseTests(TransportTestCase):
    def test_five_requests_cost_one_connection(self):
        """The handshake is paid once, not once per request."""
        for _ in range(5):
            self.assertEqual(generate("hi", 1, 4),
                             "one two three four five six seven eight nine ten")
        self.assertEqual(FakeGemini.requests, 5)
        self.assertEqual(FakeGemini.connections, 1)

    def test_streaming_reuses_the_connection_too(self):
        for _ in range(3):
            self.assertTrue("".join(generate_stream("hi", 1, 4)))
        self.assertEqual(FakeGemini.connections, 1)

    def test_a_connection_closed_between_requests_is_retried_on_a_fresh_one(self):
        """The keep-alive race: the far end hangs up while the socket is idle.

        The failure happens before any response byte is read, which is what
        makes repeating the request safe even though it is a POST.
        """
        FakeGemini.close_after_response = True
        self.assertTrue("".join(generate_stream("hi", 1, 4)))
        self.assertTrue("".join(generate_stream("hi", 1, 4)),
                        "the second request must recover from the stale socket")
        self.assertEqual(FakeGemini.requests, 2)
        self.assertEqual(FakeGemini.connections, 2, "a fresh socket for the retry")

    def test_pooled_connections_are_per_host(self):
        """A second host must not be handed the first host's socket."""
        keys = {gemini._connection_key(("https", "gemini.google.com", 443, None)),
                gemini._connection_key(("https", "example.invalid", 443, None)),
                gemini._connection_key(("https", "gemini.google.com", 443, "http://proxy:1"))}
        self.assertEqual(len(keys), 3)


class HeaderTests(TransportTestCase):
    def test_the_stdlib_transport_asks_for_uncompressed_bytes(self):
        """It cannot decompress, so a compressed body would be parsed as garbage."""
        seen = {}

        class Recorder(FakeGemini):
            def do_POST(inner_self):
                seen.update({k.lower(): v for k, v in inner_self.headers.items()})
                return FakeGemini.do_POST(inner_self)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            gemini.GEMINI_ORIGIN = f"http://127.0.0.1:{httpd.server_address[1]}"
            gemini.close_client()
            list(generate_stream("hi", 1, 4))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        self.assertEqual(seen.get("accept-encoding"), "identity")
        # The protocol headers must survive alongside it.
        self.assertEqual(seen.get("origin"), gemini.GEMINI_ORIGIN)
        self.assertEqual(seen.get("x-same-domain"), "1")
        self.assertIn("user-agent", seen)


class IncrementalReadTests(TransportTestCase):
    def test_the_first_delta_arrives_before_the_reply_is_finished(self):
        """Streaming means the client sees text while the model is still talking."""
        FakeGemini.frame_delay = 0.03
        started = time.perf_counter()
        first = None
        text = ""
        for delta in generate_stream("hi", 1, 4):
            if first is None:
                first = time.perf_counter() - started
            text += delta
        total = time.perf_counter() - started
        self.assertIn("ten", text)
        self.assertIsNotNone(first)
        # Ten frames at 30 ms is ~270 ms of writing; the first delta must not
        # wait for it. Generous bound so a loaded machine cannot flake it.
        self.assertLess(first, total / 2,
                        f"first delta {first * 1000:.0f} ms of {total * 1000:.0f} ms total")


class StatusHandlingTests(TransportTestCase):
    def test_an_error_status_carries_its_code_and_body(self):
        FakeGemini.status = 429
        with self.assertRaises(gemini.GeminiUpstreamError) as ctx:
            generate("hi", 1, 4)
        self.assertEqual(ctx.exception.status, 429)
        self.assertIn("quota exceeded", str(ctx.exception))

    def test_the_status_error_is_the_shape_the_retry_loop_expects(self):
        """It has to be caught by ``except _HTTP_ERRORS`` and described the same
        way httpx's error is, or a 429 would stop rotating accounts."""
        response = type("R", (), {"status_code": 429, "text": "quota exceeded"})()
        exc = _StdlibStatusError(response)
        self.assertIsInstance(exc, gemini._HTTP_ERRORS)
        message, status = gemini._describe_http_error(exc)
        self.assertEqual(status, 429)
        self.assertIn("quota exceeded", message)


class TimeoutTests(TransportTestCase):
    def test_connect_and_read_timeouts_are_separate_and_configurable(self):
        self.CONFIG["request_timeout_sec"] = 240
        self.CONFIG["connect_timeout_sec"] = 5
        self.assertEqual(gemini._timeouts(), (5.0, 240.0))

    def test_the_connect_timeout_cannot_exceed_the_read_timeout(self):
        self.CONFIG["request_timeout_sec"] = 4
        self.CONFIG["connect_timeout_sec"] = 30
        connect, read = gemini._timeouts()
        self.assertLessEqual(connect, read)

    def test_a_connect_that_never_answers_fails_within_the_connect_timeout(self):
        """A black-holed route must fail fast enough to be retried.

        The read timeout is deliberately long here: if the handshake inherited
        it, this test would sit for ten seconds instead of two.
        """
        self.CONFIG["request_timeout_sec"] = 10
        self.CONFIG["connect_timeout_sec"] = 0.3
        # 203.0.113.0/24 is TEST-NET-3: reserved, never routed, so a connect to
        # it hangs the way a black-holed route does.
        gemini.GEMINI_ORIGIN = "http://203.0.113.1:9"
        started = time.perf_counter()
        # Which socket error surfaces is platform-dependent; what matters is
        # that it surfaces promptly.
        with self.assertRaises(OSError):
            generate("hi", 1, 4)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0, f"connect took {elapsed:.1f}s with a 0.3s budget")

    def test_the_pooled_socket_keeps_the_read_timeout(self):
        """Connecting is bounded by the connect timeout, reading by the read one."""
        self.CONFIG["connect_timeout_sec"] = 2
        self.CONFIG["request_timeout_sec"] = 45
        target = gemini._connection_target(self.origin)
        _key, connection = gemini._connect(target)
        try:
            self.assertAlmostEqual(connection.sock.gettimeout(), 45.0, places=1)
        finally:
            connection.close()


class RetryTimingTests(TransportTestCase):
    def test_a_transport_failure_retries_immediately(self):
        """Nothing was considered, refused or answered, so nothing is waited for.

        A DNS blip or a refused connection does not repair itself in two
        seconds; sleeping would only add the delay to a failure that is
        otherwise instant.
        """
        self.CONFIG["retry_attempts"] = 3
        self.CONFIG["retry_delay_sec"] = 5
        sleeps = []
        gemini.GEMINI_ORIGIN = "http://127.0.0.1:1"  # nothing listens here
        with mock.patch("gemini_web2api.gemini.time.sleep", side_effect=sleeps.append), \
                self.assertRaises(OSError):
            generate("hi", 1, 4)
        self.assertEqual(sleeps, [], "a transport failure must not sleep before retrying")

    def test_an_http_error_still_waits_before_retrying(self):
        """Upstream being busy is exactly the case the delay exists for."""
        self.CONFIG["retry_attempts"] = 2
        self.CONFIG["retry_delay_sec"] = 0.01
        FakeGemini.status = 500
        sleeps = []
        with mock.patch("gemini_web2api.gemini.time.sleep", side_effect=sleeps.append), \
                self.assertRaises(gemini.GeminiUpstreamError):
            generate("hi", 1, 4)
        self.assertEqual(len(sleeps), 1)


class PreconnectTests(TransportTestCase):
    def test_preconnect_leaves_a_connection_ready(self):
        self.assertTrue(gemini.preconnect())
        self.assertEqual(gemini._keepalive.idle_count(), 1)
        # The server accepts asynchronously, so its counter needs a moment; the
        # assertion that matters is that the request itself opens nothing new.
        deadline = time.time() + 2
        while FakeGemini.connections < 1 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(FakeGemini.connections, 1)
        generate("hi", 1, 4)
        self.assertEqual(FakeGemini.connections, 1, "the warmed socket is used")

    def test_a_chunked_reply_reuses_its_connection(self):
        """Google may answer chunked rather than with a Content-Length.

        The completion rule differs between the two framings, so both are
        exercised: the length-delimited case is finished with a zero-length
        read, the chunked case closes itself as its terminating chunk is parsed.
        """
        ChunkedGemini.reset()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), ChunkedGemini)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            gemini.GEMINI_ORIGIN = f"http://127.0.0.1:{httpd.server_address[1]}"
            gemini.close_client()
            for _ in range(3):
                self.assertIn("one two three", "".join(generate_stream("hi", 1, 4)))
            self.assertEqual(ChunkedGemini.connections, 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


class _Closable:
    """A stand-in socket for the pool's own bookkeeping."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


if __name__ == "__main__":
    sys.exit(unittest.main())
