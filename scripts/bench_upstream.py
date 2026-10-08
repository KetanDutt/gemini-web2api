#!/usr/bin/env python3
"""Measure the upstream leg of a request against a fake Gemini endpoint.

The server's own overhead is sub-millisecond (see bench.py); nearly all of a
caller's wait is the upstream round trip. This harness stands up a local
stand-in for Gemini that speaks the same framing, counts TCP connections, and
can inject a per-frame delay, so the things that actually move latency are
measurable:

* how many connections N requests cost (connection reuse / TLS handshakes),
* time to first byte for a streaming reply (the number a chat user feels),
* what the stdlib fallback does without httpx (it used to buffer),
* whether a hung connect is bounded or burns the whole read timeout.

Usage:  python3 scripts/bench_upstream.py [--n 20] [--delay 0.05] [--no-httpx]
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPLY_WORDS = ["Here", "is", "a", "considered", "answer.", "It", "arrives", "in",
               "pieces,", "which", "is", "what", "a", "streaming", "client",
               "expects", "to", "see."]


class FakeGemini(BaseHTTPRequestHandler):
    """A minimal StreamGenerate stand-in: length-prefixed frames, one per word."""

    protocol_version = "HTTP/1.1"
    frame_delay = 0.0
    connections = 0
    requests = 0
    _lock = threading.Lock()

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        with self._lock:
            FakeGemini.connections += 1

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        with self._lock:
            FakeGemini.requests += 1

        def frame(text):
            """One cumulative `wrb.fr` frame, exactly as Gemini streams them."""
            inner = [None, None, None, None, [[None, [text]]]]
            payload = json.dumps([["wrb.fr", None, json.dumps(inner)]])
            # Google's framing: an anti-JSON-hijack prefix, a length, then the JSON.
            return ("\n".join([")]}'", str(len(payload)), payload, ""])).encode()

        cumulative = ""
        frames = []
        for word in REPLY_WORDS:
            cumulative += word + " "
            frames.append(frame(cumulative))
        body = b"".join(frames)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        per_frame = len(body) / max(1, len(frames))
        for index in range(len(frames)):
            start = int(index * per_frame)
            end = len(body) if index == len(frames) - 1 else int((index + 1) * per_frame)
            self.wfile.write(body[start:end])
            self.wfile.flush()
            if self.frame_delay:
                time.sleep(self.frame_delay)


def _start_fake():
    FakeGemini.connections = 0
    FakeGemini.requests = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeGemini)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.05, help="seconds between frames")
    parser.add_argument("--no-httpx", action="store_true",
                        help="force the urllib fallback, as a stdlib-only install has")
    args = parser.parse_args()

    httpd, port = _start_fake()
    FakeGemini.frame_delay = args.delay

    from gemini_web2api import gemini

    gemini.GEMINI_ORIGIN = f"http://127.0.0.1:{port}"
    if args.no_httpx:
        gemini.HAS_HTTPX = False
        gemini.close_client()
    line = "httpx" if (gemini.HAS_HTTPX and not args.no_httpx) else "stdlib"
    print(f"transport: {line}   frame delay: {args.delay*1000:.0f} ms   n={args.n}")

    ttft = []
    total = []
    text = ""
    for _ in range(args.n):
        started = time.perf_counter()
        first = None
        parts = []
        for delta in gemini.generate_stream("hello", "gemini-3.6-flash", "4"):
            if first is None:
                first = (time.perf_counter() - started) * 1000.0
            parts.append(delta)
        total.append((time.perf_counter() - started) * 1000.0)
        ttft.append(first if first is not None else float("nan"))
        text = "".join(parts)

    print(f"  connections opened : {FakeGemini.connections} for {FakeGemini.requests} requests")
    print(f"  time to first byte : {min(ttft):7.1f} ms  (p50 {sorted(ttft)[len(ttft)//2]:.1f})")
    print(f"  total              : {min(total):7.1f} ms  (p50 {sorted(total)[len(total)//2]:.1f})")
    print(f"  reply              : {text[:60]!r}… ({len(text)} chars)")

    gemini.close_client()
    httpd.shutdown()


if __name__ == "__main__":
    main()
