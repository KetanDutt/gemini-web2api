"""Shared test helpers.

Everything here is offline: the Gemini wire protocol is faked at the frame
level so the suite exercises real parsing and real HTTP handling without
touching the network.
"""
import copy
import http.client
import json
import threading
import unittest

from gemini_web2api.config import CONFIG, DEFAULT_CONFIG, reset_config
from gemini_web2api.server import GeminiHandler, ThreadedServer


def gemini_frame(texts, rpc_id="AOvVawXtest", conv_id="c_abc123"):
    """Build one ``wrb.fr`` line carrying ``texts``.

    ``texts`` may be a single string or a list of strings (multi-part reply).
    The shape mirrors the real payload: ``[[rpcid, rpcid, inner_json, ...]]``
    where ``inner_json[4]`` holds the parts and ``part[1]`` the text list.
    """
    if isinstance(texts, str):
        texts = [texts]
    inner = [None] * 102
    inner[0] = [None, None, None, None, None, None, None, None, "r_" + rpc_id]
    inner[1] = texts[0] if texts else None
    inner[4] = [[None, texts, None, None, None, None, None, None, None, None,
                 ["msg_" + conv_id], None, None, None, None, [None, None, None, 0]]]
    inner[8] = [["conversation_" + conv_id]]
    inner[31] = [["response_" + conv_id]]
    return json.dumps([["wrb.fr", rpc_id, json.dumps(inner),
                        None, None, None, None, None, None, "generic"]])


def gemini_response(*texts):
    """A complete non-streaming body: the )]}' guard plus cumulative frames."""
    lines = [")]}'", "", "null"]
    for text in texts:
        lines.append(gemini_frame(text))
    return "\n".join(lines) + "\n"


def cumulative_response(final, steps=3):
    """Frames that grow towards ``final``, as Gemini actually streams."""
    if steps <= 1:
        return gemini_response(final)
    points = [final[:max(1, len(final) * i // steps)] for i in range(1, steps + 1)]
    points[-1] = final
    return gemini_response(*points)


def decode_sse(body):
    """Parse an SSE body into ``[(event_type_or_None, payload)]``.

    ``payload`` is decoded JSON, or the raw string for ``data: [DONE]``.
    """
    events = []
    for block in body.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event_type = next(
            (line[len("event: "):] for line in lines if line.startswith("event: ")), None)
        data = next(
            (line[len("data: "):] for line in lines if line.startswith("data: ")), None)
        if data is None:
            continue
        if data == "[DONE]":
            events.append((event_type, "[DONE]"))
            continue
        try:
            events.append((event_type, json.loads(data)))
        except (json.JSONDecodeError, ValueError):
            events.append((event_type, data))
    return events


def sse_deltas(body):
    """Concatenate the ``content`` deltas of a chat.completion stream."""
    out = []
    for _event, payload in decode_sse(body):
        if not isinstance(payload, dict):
            continue
        for choice in payload.get("choices") or []:
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                out.append(delta)
    return "".join(out)


class ConfigTestCase(unittest.TestCase):
    """Restores CONFIG around every test."""

    def setUp(self):
        self._saved = copy.deepcopy(CONFIG)
        reset_config()
        self.CONFIG = CONFIG
        # Test output should stay readable; individual tests re-enable logging
        # when they are asserting on it.
        CONFIG["log_requests"] = False
        CONFIG["auto_update_bl"] = False

    def tearDown(self):
        reset_config()
        CONFIG.clear()
        CONFIG.update(copy.deepcopy(self._saved))


class ServerTestCase(ConfigTestCase):
    """Runs a real HTTP server on an ephemeral port for the duration of a class."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedServer(("127.0.0.1", 0), GeminiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        super().setUp()
        CONFIG["api_keys"] = []
        CONFIG["log_requests"] = False
        CONFIG["rate_limit_max"] = 0
        CONFIG["auto_update_bl"] = False

    def request(self, method, path, body=None, headers=None, chunked=False, raw_body=None):
        """Perform one request and return ``(status, headers, body_text)``."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Content-Type": "application/json"}
        if headers:
            sent.update(headers)
        payload = raw_body
        if payload is None and body is not None:
            payload = json.dumps(body)
        connection.request(method, path, body=payload, headers=sent, encode_chunked=chunked)
        response = connection.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        got = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, got, text

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.request("POST", path, body=body, **kwargs)

    def get_json(self, path, **kwargs):
        status, headers, text = self.get(path, **kwargs)
        try:
            return status, headers, json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return status, headers, text

    def post_json(self, path, body, **kwargs):
        status, headers, text = self.post(path, body, **kwargs)
        try:
            return status, headers, json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return status, headers, text


__all__ = [
    "CONFIG",
    "DEFAULT_CONFIG",
    "ConfigTestCase",
    "ServerTestCase",
    "cumulative_response",
    "decode_sse",
    "gemini_frame",
    "gemini_response",
    "reset_config",
    "sse_deltas",
]
