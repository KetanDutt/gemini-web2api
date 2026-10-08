#!/usr/bin/env python3
"""Measure the server's own latency, with the upstream stubbed out.

A real Gemini call is dominated by Google's thinking time, which is not ours to
optimise. What *is* ours is everything around it: parsing, model resolution,
prompt building, payload/header/URL construction, credential lookup, the socket
write, and the per-request bookkeeping. This harness replaces `generate()` and
`generate_stream()` with a stub and drives the real server over a real loopback
socket, so the numbers are the overhead a caller actually pays on top of the
model's own time.

Usage:  python3 scripts/bench.py [--n 200] [--stream]
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gemini_web2api import gemini, server  # noqa: E402

N = 200
WARMUP = 20

# ── stub the upstream ────────────────────────────────────────────────────────
REPLY = "A short answer that is long enough to be realistic. " * 4


def _fake_generate(prompt, model_id, think_mode, file_refs=None, extra_fields=None):
    return REPLY


def _fake_stream(prompt, model_id, think_mode, file_refs=None, extra_fields=None):
    for piece in REPLY.split(" "):
        yield piece + " "


def _install_stubs():
    """Patch the names server.py imported, plus the client pool."""
    server.generate = _fake_generate
    server.generate_stream = _fake_stream
    gemini.generate = _fake_generate
    gemini.generate_stream = _fake_stream


class _Server(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        from http.server import ThreadingHTTPServer

        class Handler(server.GeminiHandler):
            pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]

    def run(self):
        self.httpd.serve_forever(poll_interval=0.05)

    def stop(self):
        self.httpd.shutdown()


def _post(port, body, stream=False, path="/v1/chat/completions"):
    """Return ``(elapsed_ms, time_to_first_byte_ms, payload)``.

    Time to first byte is the number a chat user feels, so it is measured
    separately from the total: a reply that starts arriving immediately and
    takes ten seconds to finish is a different experience from one that
    arrives all at once after ten seconds.
    """
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept":
                 "text/event-stream" if stream else "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req) as resp:
        first = resp.read(1)
        ttfb = (time.perf_counter() - started) * 1000.0
        payload = first + resp.read()
    return (time.perf_counter() - started) * 1000.0, ttfb, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=N)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--path", default="/v1/chat/completions")
    parser.add_argument("--long-prompt", action="store_true",
                        help="send a 4k-token conversation instead of a one-liner")
    args = parser.parse_args()

    _install_stubs()
    srv = _Server()
    srv.start()
    time.sleep(0.1)

    messages = [{"role": "user", "content": "Hello there, what can you do?"}]
    if args.long_prompt:
        messages = ([{"role": "system", "content": "You are a helpful assistant. " * 40}]
                    + [{"role": "user" if i % 2 == 0 else "assistant",
                        "content": "This is a turn in a long conversation. " * 30}
                       for i in range(20)]
                    + [{"role": "user", "content": "Summarise that."}])
    body = {"model": "gemini-3.6-flash", "messages": messages, "stream": args.stream}

    for _ in range(WARMUP):
        _post(srv.port, body, args.stream, args.path)

    samples = []
    firsts = []
    for _ in range(args.n):
        ms, ttfb, payload = _post(srv.port, body, args.stream, args.path)
        samples.append(ms)
        firsts.append(ttfb)

    samples.sort()
    firsts.sort()
    label = "stream" if args.stream else "json"
    print(f"{args.path}  [{label}]  n={args.n}  prompt={'long' if args.long_prompt else 'short'}")
    print(f"  min {samples[0]:7.2f} ms")
    print(f"  p50 {statistics.median(samples):7.2f} ms")
    print(f"  p90 {samples[int(len(samples) * 0.9)]:7.2f} ms")
    print(f"  p99 {samples[int(len(samples) * 0.99)]:7.2f} ms")
    print(f"  max {samples[-1]:7.2f} ms")
    print(f"  first byte: min {firsts[0]:.2f} ms, p50 {statistics.median(firsts):.2f} ms")
    print(f"  response bytes: {len(payload)}")
    srv.stop()


if __name__ == "__main__":
    main()
