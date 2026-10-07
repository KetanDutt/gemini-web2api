"""HTTP server exposing OpenAI-compatible, Google-native and status endpoints.

Built on :mod:`http.server` so the project keeps working with no third-party
dependencies. Runs HTTP/1.1 with keep-alive; SSE responses opt out of keep-alive
because they have no Content-Length.
"""
import hmac
import json
import platform
import re
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import unquote, urlsplit

from . import __version__, jsonmode, prometheus
from .config import CONFIG
from .config import snapshot as config_snapshot
from .gemini import HAS_HTTPX, generate, generate_stream, log
from .gemini import pool_snapshot as cookie_pool_snapshot
from .metrics import history as metrics_history
from .metrics import inc, record_latency, record_request, record_status
from .metrics import snapshot as metrics_snapshot
from .models import (
    MODE_CATEGORY,
    MODELS,
    google_model_detail,
    google_model_list,
    model_list,
    resolve_model,
)
from .multimodal import detect_image_mime, fetch_image_bytes, upload_image
from .ratelimit import LIMITER
from .tools import (
    google_contents_to_prompt,
    messages_to_prompt,
    parse_google_function_calls,
    parse_tool_calls,
)
from .webui import render_dashboard

_START_TIME = time.time()
_request_count = [0]
_count_lock = threading.Lock()

# Paths that stay reachable when API keys are configured: liveness probes must
# work from Docker/Kubernetes without a secret, and the dashboard renders for a
# browser that has not been given one yet.
_PUBLIC_PATHS = frozenset({
    "/", "/health", "/healthz", "/live", "/ready", "/favicon.ico", "/logo.png",
})

_EMPTY_COMPLETION = "I apologize, but I was unable to generate a response. Please try again."


def _usage(prompt, text):
    """Approximate token counts.

    Gemini Web does not report token usage, so this is the usual chars/4
    estimate. It is documented as an estimate wherever it is surfaced.
    """
    prompt_tokens = len(prompt or "") // 4
    completion_tokens = len(text or "") // 4
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _google_usage(prompt, text):
    usage = _usage(prompt, text)
    return {
        "promptTokenCount": usage["prompt_tokens"],
        "candidatesTokenCount": usage["completion_tokens"],
        "totalTokenCount": usage["total_tokens"],
    }


class ImageRejected(Exception):
    """A client-supplied image could not be used. Maps to HTTP 400."""


def _upload_images(images):
    """Upload images and return Gemini file references, or None if there are none.

    Distinguishes client-side problems (a URL we refuse to fetch, an undecodable
    payload) from upstream ones (Google rejecting the upload), so the caller can
    answer 400 rather than blaming the upstream with a 502.
    """
    if not images:
        return None
    file_refs = []
    for item in images:
        if not (isinstance(item, tuple) and len(item) == 2):
            continue
        data, mime = item
        if isinstance(data, str):
            fetched = fetch_image_bytes(data)
            if not fetched:
                raise ImageRejected(f"could not fetch image URL: {data[:200]}")
            data, mime = fetched, mime or "image/png"
        if not data:
            raise ImageRejected("empty image payload")
        mime = detect_image_mime(data, mime or "image/png")
        try:
            file_refs.append(upload_image(data, "image.png", mime or "image/png"))
        except Exception as exc:
            raise RuntimeError(f"image upload failed: {exc}") from exc
    if not file_refs:
        return None
    inc("images_uploaded", len(file_refs))
    return file_refs


class GeminiHandler(BaseHTTPRequestHandler):
    """Request handler for every endpoint."""

    # HTTP/1.1 keeps connections alive between requests. Every response must
    # therefore declare an exact Content-Length or close the connection; SSE
    # responses do the latter.
    protocol_version = "HTTP/1.1"
    # Streaming latency matters more than throughput here.
    disable_nagle_algorithm = True
    server_version = f"gemini-web2api/{__version__}"
    sys_version = ""

    # ─── plumbing ────────────────────────────────────────────────────────────

    def __init__(self, *args, **kwargs):
        self.request_id = uuid.uuid4().hex[:12]
        self._started = time.time()
        self._headers_sent = False
        self._recorded = False
        # History is written once, at the end of the request, so that a stream's
        # recorded duration covers the whole exchange rather than its headers.
        self._history_done = False
        self._final_status = None
        # Set by the handlers so request history can attribute a model without
        # the recorder having to re-parse the body.
        self._model_name = None
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        client_ip = self.client_address[0] if self.client_address else "-"
        log(f"{self.request_id} {client_ip} {fmt % args}", "debug")

    def _split_path(self):
        """Return ``(path, query)`` with the query string removed from the path.

        Routing used to compare ``self.path`` exactly, so ``/v1/models?limit=10``
        — which real OpenAI clients send — returned 404.
        """
        parts = urlsplit(self.path)
        path = unquote(parts.path) or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        return path, parts.query

    @property
    def query_params(self):
        from urllib.parse import parse_qs
        _, query = self._split_path()
        return parse_qs(query)

    def _cors_headers(self):
        return {
            "Access-Control-Allow-Origin": CONFIG.get("cors_origin", "*"),
            "Access-Control-Expose-Headers": "X-Request-Id",
            "X-Request-Id": self.request_id,
        }

    def send_json(self, data, status=200, extra_headers=None):
        """Serialise ``data`` as JSON with an accurate Content-Length."""
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        self._record(status)

    def send_html(self, body, status=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        self._record(status)

    def send_text(self, body, content_type="text/plain; charset=utf-8", status=200):
        """Write a non-HTML text body with an accurate Content-Length."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        self._record(status)

    def _metrics_authorized(self):
        """Whether the caller may read `/metrics`.

        Gated exactly like `/status`, because the numbers describe traffic
        volume, model mix and error rate — an unauthenticated feed of that is a
        free reconnaissance endpoint. Prometheus supports credentials in its
        scrape config, so requiring them costs a scraper nothing.
        """
        return not (CONFIG.get("api_keys") or []) or self._authorized()

    def send_error_json(self, status, message, err_type="invalid_request_error", code=None, param=None):
        """Emit an error in the shape OpenAI clients expect."""
        payload = {"error": {"message": message, "type": err_type, "code": code, "param": param}}
        extra = {"WWW-Authenticate": 'Bearer realm="gemini-web2api"'} if status == 401 else None
        self.send_json(payload, status, extra_headers=extra)

    # Kept as a thin alias so the Google-native path can reuse the same shape.
    def _send_error(self, status, message, err_type="invalid_request_error", code=None):
        self.send_error_json(status, message, err_type, code)

    def _record(self, status):
        """Count a response. Idempotent per request: the first status wins.

        The SSE path records when the stream *opens*, because the 200 header is
        already on the wire and must be counted even if the stream later fails
        or the client disconnects. Everything else records on completion.

        The history entry is written separately by :meth:`_finish_history`.
        Recording it here instead would measure time-to-first-byte for streams:
        a reply that took four seconds to generate was logged as 0.6ms, which
        made the Activity tab's latency column actively misleading.
        """
        if self._recorded:
            return
        self._recorded = True
        self._final_status = status
        with _count_lock:
            _request_count[0] += 1
        inc("requests")
        record_status(status)

    def _finish_history(self):
        """Append this request to history with its true total duration.

        Called from a ``finally`` in each verb handler, so a stream that fails
        mid-flight, or a client that disconnects, is still logged — and with the
        elapsed time of the whole exchange rather than of the header flush.
        """
        if self._history_done:
            return
        self._history_done = True
        status = self._final_status
        if status is None:
            # No response was ever produced (the connection broke first), so
            # there is no counted request to describe.
            return
        limit = int(CONFIG.get("history_max") or 0)
        if limit <= 0:
            return
        # Strip the query string: Google-native clients may pass ?key=<api_key>,
        # and history is readable through /status.
        path = self._split_path()[0]
        record_request({
            "ts": round(time.time(), 3),
            "id": self.request_id,
            "method": self.command,
            "path": path,
            "status": status,
            "model": self._model_name,
            "ms": round((time.time() - self._started) * 1000.0, 1),
            "client": self.client_address[0] if self.client_address else None,
        })

    def _start_sse(self):
        """Begin a text/event-stream response.

        ``Connection: close`` is required: an SSE body has no Content-Length, so
        under HTTP/1.1 the only way to frame it is to close the connection at the
        end. ``X-Accel-Buffering: no`` stops nginx from swallowing the stream.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        self._headers_sent = True
        # An SSE body has no Content-Length and never passes through send_json,
        # so without this streaming requests were absent from requests_served
        # and from the status-code counts entirely.
        self._record(200)

    def _sse_write(self, data, event=None):
        """Write one SSE frame. Returns False when the client has gone away."""
        frame = ""
        if event:
            frame += f"event: {event}\n"
        frame += f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        try:
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
            return False

    def _sse_done(self):
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _parse_body(self, body):
        if not body:
            return None
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def _read_request_body(self):
        """Read the whole request body, honouring chunked encoding and the size cap.

        The body must always be consumed before responding, otherwise an
        early 401/413 leaves unread bytes on a keep-alive connection and
        desynchronises the next request.
        """
        limit = int(CONFIG.get("max_request_bytes", 25 * 1024 * 1024))
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()

        if "chunked" in transfer_encoding:
            chunks = []
            total = 0
            while True:
                size_line = self.rfile.readline(65536)
                if not size_line:
                    break
                size_text = size_line.split(b";", 1)[0].strip()
                try:
                    size = int(size_text, 16)
                except ValueError as exc:
                    raise ValueError("invalid chunked request body") from exc
                if size == 0:
                    while True:
                        trailer = self.rfile.readline(65536)
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                if size < 0:
                    raise ValueError("invalid chunk size")
                total += size
                if total > limit:
                    self.close_connection = True
                    raise BufferError("request body too large")
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)  # trailing CRLF
            return b"".join(chunks)

        raw_length = (self.headers.get("Content-Length") or "0").strip()
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError(f"invalid Content-Length: {raw_length!r}") from exc
        if length < 0:
            raise ValueError("negative Content-Length")
        if length > limit:
            self.close_connection = True
            raise BufferError("request body too large")
        return self.rfile.read(length) if length else b""

    def _drain_body(self):
        """Consume and discard a request body we are not going to use."""
        try:
            self._read_request_body()
        except (ValueError, BufferError, OSError):
            self.close_connection = True

    # ─── auth & rate limiting ────────────────────────────────────────────────

    def _presented_key(self):
        """The credential the caller supplied, from any supported location."""
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        for header in ("x-api-key", "x-goog-api-key"):
            value = self.headers.get(header)
            if value:
                return value.strip()
        for key, values in self.query_params.items():
            if key.lower() in ("key", "api_key") and values:
                return values[0]
        return ""

    def _authorized(self):
        keys = CONFIG.get("api_keys") or []
        if not keys:
            return True
        presented = self._presented_key()
        if not presented:
            return False
        # Constant-time comparison: `in` would short-circuit on the first
        # differing byte and leak the key length/timing.
        return any(hmac.compare_digest(presented, str(key)) for key in keys)

    def _rate_limit_key(self):
        return self._presented_key() or (self.client_address[0] if self.client_address else "unknown")

    def _check_rate_limit(self):
        allowed, retry_after, _remaining = LIMITER.check(self._rate_limit_key())
        if allowed:
            return True
        inc("rate_limited")
        self.send_error_json(
            429,
            f"rate limit exceeded ({CONFIG.get('rate_limit_max')} requests / "
            f"{CONFIG.get('rate_limit_window_sec')}s)",
            err_type="rate_limit_error",
            code="rate_limit_exceeded",
        )
        return False

    # ─── verbs ───────────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", CONFIG.get("cors_origin", "*"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Authorization, Content-Type, x-api-key, x-goog-api-key, "
                             "x-request-id")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._record(204)
        finally:
            self._finish_history()

    def do_HEAD(self):
        try:
            path, _ = self._split_path()
            status = 200 if path in ("/health", "/healthz", "/live", "/ready") else 404
            self.send_response(status)
            self.send_header("Content-Length", "0")
            for key, value in self._cors_headers().items():
                self.send_header(key, value)
            self.end_headers()
            self._record(status)
        finally:
            self._finish_history()

    def do_GET(self):
        try:
            self._route_get(*self._split_path())
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            log(f"{self.request_id} GET error: {exc}", "error")
            try:
                self.send_error_json(500, f"internal error: {exc}", "api_error")
            except Exception:
                self.close_connection = True
        finally:
            self._finish_history()

    def _route_get(self, path, query):
        is_api = path.startswith("/v1")
        if is_api and not self._authorized():
            self.send_error_json(401, "invalid api key", "authentication_error", "invalid_api_key")
            return
        if is_api and not self._check_rate_limit():
            return

        if path == "/v1/models":
            self.send_json({"object": "list", "data": model_list()})
        elif path.startswith("/v1/models/"):
            self._openai_model_detail(path[len("/v1/models/"):])
        elif path == "/v1beta/models":
            self.send_json({"models": google_model_list()})
        elif path.startswith("/v1beta/models/"):
            self._google_model_detail(path[len("/v1beta/models/"):])
        elif path in ("/health", "/healthz", "/live"):
            self.send_json(self._health_payload())
        elif path == "/ready":
            payload = self._health_payload()
            ready = payload.get("ready", True)
            self.send_json(payload, 200 if ready else 503)
        elif path == "/status":
            # Exposes configuration state, so it is not public.
            if (CONFIG.get("api_keys") or []) and not self._authorized():
                self.send_error_json(401, "invalid api key", "authentication_error", "invalid_api_key")
                return
            self.send_json(self._status_payload())
        elif path == "/metrics":
            # Prometheus scrape target. Handled here rather than through the
            # catch-all so it works regardless of whether `httpx` is present and
            # never consults the upstream.
            if not self._metrics_authorized():
                self.send_error_json(401, "invalid api key", "authentication_error",
                                     "invalid_api_key")
                return
            self.send_text(prometheus.render(metrics_snapshot()), prometheus.CONTENT_TYPE)
        elif path == "/":
            self._root(query)
        elif path in ("/favicon.ico", "/logo.png"):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error_json(404, f"unknown endpoint: {path}", "invalid_request_error")

    def _root(self, query):
        """Dashboard for browsers, JSON for programs.

        Content negotiation keeps the historical ``GET /`` JSON contract intact
        for anything that was already parsing it.
        """
        accept = self.headers.get("Accept", "")
        wants_html = "text/html" in accept or "application/xhtml" in accept
        if wants_html and query != "format=json":
            self.send_html(render_dashboard(self._dashboard_state()))
            return
        self.send_json({
            "status": "ok",
            "version": __version__,
            "models": list(MODELS.keys()),
            "endpoints": ["/v1/chat/completions", "/v1/completions", "/v1/responses",
                          "/v1/models", "/v1beta/models", "/health", "/status"],
            "dashboard": "send 'Accept: text/html' for the web dashboard",
        })

    def _openai_model_detail(self, model_id):
        if model_id not in MODELS:
            self.send_error_json(404, f"unknown model: {model_id}", "invalid_request_error",
                                 "model_not_found")
            return
        cfg = MODELS[model_id]
        self.send_json({
            "id": model_id,
            "object": "model",
            "created": 1700000000,
            "owned_by": "google",
            "description": cfg["desc"],
        })

    def _google_model_detail(self, remainder):
        """``GET /v1beta/models/{model}`` — Gemini CLI asks for one model.

        Previously fell through to the list handler and returned every model.
        """
        model_id = remainder.split(":", 1)[0]
        detail = google_model_detail(model_id)
        if detail is None:
            self.send_error_json(404, f"unknown model: {model_id}", "invalid_request_error",
                                 "model_not_found")
            return
        self.send_json(detail)

    def do_POST(self):
        try:
            # The body is read before authorisation so a rejected request cannot
            # leave unread bytes on a keep-alive connection.
            try:
                body = self._read_request_body()
            except BufferError:
                self.send_error_json(413, "request body too large", "invalid_request_error",
                                     "payload_too_large")
                return
            except ValueError as exc:
                self.send_error_json(400, str(exc), "invalid_request_error")
                return
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
                return

            try:
                self._route_post(*self._split_path(), body=body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as exc:
                log(f"{self.request_id} POST error: {exc}", "error")
                if self._headers_sent:
                    self._sse_done()
                    return
                try:
                    self.send_error_json(500, f"internal error: {exc}", "api_error")
                except Exception:
                    self.close_connection = True
        finally:
            self._finish_history()

    def _route_post(self, path, query, body):
        is_api = path.startswith("/v1")
        if is_api and not self._authorized():
            self.send_error_json(401, "invalid api key", "authentication_error", "invalid_api_key")
            return
        if is_api and not self._check_rate_limit():
            return

        if path == "/v1/chat/completions":
            self._handle_chat(body)
        elif path == "/v1/completions":
            self._handle_legacy_completions(body)
        elif path == "/v1/responses":
            self._handle_responses(body)
        elif ":streamGenerateContent" in path:
            self._handle_google_generate(body, stream=True)
        elif ":generateContent" in path:
            self._handle_google_generate(body, stream=False)
        elif path in ("/v1/embeddings", "/v1/audio/speech", "/v1/images/generations"):
            self.send_error_json(501, f"{path} is not implemented by gemini-web2api",
                                 "invalid_request_error", "unsupported_endpoint")
        else:
            self.send_error_json(404, f"unknown endpoint: {path}", "invalid_request_error")

    # ─── shared helpers ──────────────────────────────────────────────────────

    def _resolve(self, requested):
        """Resolve a model name, answering 400 when it cannot be used."""
        name, model_id, think_mode, error, extra = resolve_model(
            requested or CONFIG.get("default_model"))
        if error:
            self.send_error_json(400, error, "invalid_request_error", "invalid_model")
            return None
        # Record the resolved name (not the requested one) so history shows what
        # actually served the request, including silent fallbacks.
        self._model_name = name
        return name, model_id, think_mode, extra

    def _generate(self, prompt, model_id, think_mode, file_refs, extra_fields, model_name):
        """Non-streaming generation with latency tracking."""
        started = time.time()
        try:
            text = generate(prompt, model_id, think_mode, file_refs, extra_fields)
        finally:
            record_latency(model_name, time.time() - started)
        return text

    def _stream(self, prompt, model_id, think_mode, file_refs, extra_fields, model_name):
        started = time.time()
        try:
            yield from generate_stream(prompt, model_id, think_mode, file_refs, extra_fields)
        finally:
            record_latency(model_name, time.time() - started)

    def _prepare_images(self, images):
        """Upload images, translating failures into the right HTTP status."""
        try:
            return _upload_images(images), None
        except ImageRejected as exc:
            self.send_error_json(400, str(exc), "invalid_request_error", "image_rejected")
            return None, exc
        except RuntimeError as exc:
            self.send_error_json(502, f"upstream error: {exc}", "api_error", "image_upload_failed")
            return None, exc

    def _upstream_failure(self, exc):
        """Map an upstream exception onto an HTTP response."""
        inc("upstream_failures")
        status = getattr(exc, "status", None)
        if status == 429:
            self.send_error_json(429, f"upstream rate limited: {exc}", "api_error",
                                 "upstream_rate_limited")
            return
        if status == 405:
            self.send_error_json(502,
                                 f"upstream rejected the build tag (bl): {exc}. "
                                 "Enable auto_update_bl or refresh gemini_bl.",
                                 "api_error", "stale_build_tag")
            return
        self.send_error_json(502, f"upstream error: {exc}", "api_error")

    def _json_content(self, spec, text):
        """Return the model's reply as canonical JSON, or None after answering.

        The *parsed* value is re-serialised rather than the raw reply being
        forwarded, because the guarantee applies to the value that was checked.
        Passing the raw text through would mean validating one string and
        sending another — and the raw text is often not JSON at all, since
        models fence it or preface it with a sentence no matter what the
        instruction asked for.

        A None return unambiguously means "already answered with an error":
        ``json.dumps`` never returns None, so a valid result is always a string.
        """
        if not spec:
            return text
        value, error = jsonmode.extract_json(text)
        if error is not None:
            inc("json_mode_failures")
            log(f"{self.request_id} json mode: {error}", "error")
            self.send_error_json(502, error, "api_error", "json_parse_failed")
            return None
        errors = jsonmode.validate(spec, value)
        if errors:
            inc("json_mode_failures")
            message = jsonmode.describe(errors)
            log(f"{self.request_id} json mode: {message}", "error")
            self.send_error_json(502, message, "api_error", "json_schema_violation")
            return None
        return json.dumps(value, ensure_ascii=False)

    def _chunk(self, cid, model_name, delta, finish=None):
        return {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    # ─── /v1/chat/completions ────────────────────────────────────────────────

    def _handle_chat(self, body):
        req = self._parse_body(body)
        if req is None:
            self.send_error_json(400, "request body must be a JSON object", "invalid_request_error")
            return
        self._chat(req)

    def _chat(self, req):
        """Shared implementation for /v1/chat/completions and /v1/completions."""
        resolved = self._resolve(req.get("model"))
        if resolved is None:
            return
        model_name, model_id, think_mode, extra_fields = resolved

        # Parsed before the prompt is built so a malformed request costs
        # nothing, and before any image upload so it cannot consume quota.
        json_spec, error = jsonmode.parse(req.get("response_format"))
        if error:
            self.send_error_json(400, error, "invalid_request_error",
                                 "invalid_response_format")
            return

        tools = req.get("tools")
        tool_choice = req.get("tool_choice", "auto")
        prompt, images = messages_to_prompt(req.get("messages", []), tools, tool_choice)
        if not prompt.strip():
            self.send_error_json(400, "empty prompt: 'messages' produced no content",
                                 "invalid_request_error", "empty_prompt")
            return

        # Appended last because the conversation is flattened into one block and
        # the most recent instruction governs, so a client's "reply in prose"
        # earlier in the conversation cannot override this one.
        if json_spec:
            prompt += "\n\n" + jsonmode.instruction_for(json_spec)

        stream = bool(req.get("stream", False))
        include_usage = bool((req.get("stream_options") or {}).get("include_usage"))
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        file_refs, error = self._prepare_images(images)
        if error is not None:
            return

        wants_tools = bool(tools) and tool_choice != "none"
        log(f"{self.request_id} chat model={model_name} think={think_mode} stream={stream} "
            f"tools={wants_tools} prompt_chars={len(prompt)} images={len(images or [])} "
            f"json={json_spec['kind'] if json_spec else 'off'}")

        # JSON mode buffers for the same reason tool calls do: validity can only
        # be judged on the complete reply, and a violation must not be reported
        # after a 200 has already gone out.
        if stream and not wants_tools and not json_spec:
            self._stream_chat(cid, model_name, model_id, think_mode, prompt,
                              file_refs, extra_fields, include_usage)
            return

        try:
            text = self._generate(prompt, model_id, think_mode, file_refs, extra_fields, model_name)
        except Exception as exc:
            self._upstream_failure(exc)
            return

        tool_calls = None
        if wants_tools and text:
            text, tool_calls = parse_tool_calls(text)
            if tool_calls:
                inc("tool_calls_parsed", len(tool_calls))

        # Skipped when the model answered with a tool call instead: that is a
        # legitimate outcome for a request that also offered tools, and the
        # leftover text after the call block is not the answer.
        if json_spec and not tool_calls:
            text = self._json_content(json_spec, text)
            if text is None:
                return

        message = {"role": "assistant", "content": text or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"
        usage = _usage(prompt, text)

        if stream:
            # Tool calls and JSON mode can only be recognised once the full
            # reply is in hand, so this is a single-chunk stream.
            self._start_sse()
            inc("streams")
            self._sse_write(self._chunk(cid, model_name, message, finish))
            if include_usage:
                final = self._chunk(cid, model_name, {}, finish)
                final["choices"] = []
                final["usage"] = usage
                self._sse_write(final)
            self._sse_done()
        else:
            inc("completions")
            self.send_json({
                "id": cid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "message": message, "finish_reason": finish,
                             "logprobs": None}],
                "usage": usage,
                "system_fingerprint": f"gemini-web2api-{__version__}",
            })

    def _stream_chat(self, cid, model_name, model_id, think_mode, prompt,
                     file_refs, extra_fields, include_usage):
        self._start_sse()
        inc("streams")
        # The opening chunk carries the role only. Adding an empty `content`
        # field here trips up strictly-typed OpenAI clients.
        if not self._sse_write(self._chunk(cid, model_name, {"role": "assistant"})):
            return
        collected = []
        failed = False
        try:
            for delta in self._stream(prompt, model_id, think_mode, file_refs,
                                      extra_fields, model_name):
                if not delta:
                    continue
                collected.append(delta)
                if not self._sse_write(self._chunk(cid, model_name, {"content": delta})):
                    return
        except Exception as exc:
            failed = True
            # Counted here rather than via _upstream_failure(), which cannot be
            # used once the stream is open: it would try to send an HTTP error
            # status after a 200 header has already gone out. Without this the
            # counter only ever tracked non-streaming failures, so a server
            # whose streams were all failing still looked healthy.
            inc("upstream_failures")
            log(f"{self.request_id} stream error after {sum(map(len, collected))} chars: {exc}",
                "error")
            self._sse_write({
                "error": {"message": f"upstream error: {exc}", "type": "api_error",
                          "code": getattr(exc, "status", None)},
            })

        text = "".join(collected)
        finish = "stop" if not failed else "length"
        self._sse_write(self._chunk(cid, model_name, {}, finish))
        if include_usage:
            final = self._chunk(cid, model_name, {}, finish)
            final["choices"] = []
            final["usage"] = _usage(prompt, text)
            self._sse_write(final)
        # [DONE] is always sent, even on failure: without it an OpenAI client
        # waits for a terminator that never arrives.
        self._sse_done()

    # ─── /v1/completions (legacy) ────────────────────────────────────────────

    def _handle_legacy_completions(self, body):
        """Support the pre-chat ``text_completion`` API.

        Several clients and proxies still call ``/v1/completions`` with a
        ``prompt`` string. It previously 404'd; it is now translated into a
        single-turn request and answered in the legacy response shape.
        """
        req = self._parse_body(body)
        if req is None:
            self.send_error_json(400, "request body must be a JSON object", "invalid_request_error")
            return

        raw_prompt = req.get("prompt", "")
        if isinstance(raw_prompt, list):
            raw_prompt = "\n".join(str(part) for part in raw_prompt)
        if not isinstance(raw_prompt, str):
            raw_prompt = str(raw_prompt)
        if not raw_prompt.strip():
            self.send_error_json(400, "'prompt' is required and must be non-empty",
                                 "invalid_request_error", "empty_prompt")
            return

        resolved = self._resolve(req.get("model"))
        if resolved is None:
            return
        model_name, model_id, think_mode, extra_fields = resolved

        # Supported here as well as on chat completions, because OpenAI's legacy
        # endpoint accepts `response_format` too — accepting the parameter and
        # ignoring it would be the silent-drop failure again.
        json_spec, error = jsonmode.parse(req.get("response_format"))
        if error:
            self.send_error_json(400, error, "invalid_request_error",
                                 "invalid_response_format")
            return
        if json_spec:
            raw_prompt += "\n\n" + jsonmode.instruction_for(json_spec)

        stream = bool(req.get("stream", False))
        cid = f"cmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        # Buffered in JSON mode so the reply can be validated before any part of
        # it is sent; a violation cannot be retracted after a 200 header.
        if stream and not json_spec:
            self._start_sse()
            inc("streams")
            collected = []
            try:
                for delta in self._stream(raw_prompt, model_id, think_mode, None,
                                          extra_fields, model_name):
                    if not delta:
                        continue
                    collected.append(delta)
                    if not self._sse_write({
                        "id": cid, "object": "text_completion", "created": created,
                        "model": model_name,
                        "choices": [{"index": 0, "text": delta, "logprobs": None,
                                     "finish_reason": None}],
                    }):
                        return
            except Exception as exc:
                log(f"{self.request_id} legacy stream error: {exc}", "error")
                self._sse_write({"error": {"message": f"upstream error: {exc}",
                                           "type": "api_error"}})
            self._sse_write({
                "id": cid, "object": "text_completion", "created": created, "model": model_name,
                "choices": [{"index": 0, "text": "", "logprobs": None,
                             "finish_reason": "stop"}],
            })
            self._sse_done()
            return

        try:
            text = self._generate(raw_prompt, model_id, think_mode, None,
                                  extra_fields, model_name)
        except Exception as exc:
            self._upstream_failure(exc)
            return

        if json_spec:
            text = self._json_content(json_spec, text)
            if text is None:
                return

        if stream:
            # JSON mode, buffered: emit the validated reply as one event so the
            # legacy streaming shape still terminates correctly.
            self._start_sse()
            inc("streams")
            self._sse_write({
                "id": cid, "object": "text_completion", "created": created,
                "model": model_name,
                "choices": [{"index": 0, "text": text, "logprobs": None,
                             "finish_reason": "stop"}],
            })
            self._sse_done()
            return

        inc("completions")
        self.send_json({
            "id": cid, "object": "text_completion", "created": created, "model": model_name,
            "choices": [{"index": 0, "text": text or "", "logprobs": None,
                         "finish_reason": "stop"}],
            "usage": _usage(raw_prompt, text),
        })

    # ─── /v1/responses (Codex CLI) ───────────────────────────────────────────

    def _responses_to_messages(self, req):
        """Convert a Responses API request into OpenAI chat messages."""
        messages = []
        if req.get("instructions"):
            messages.append({"role": "system", "content": req["instructions"]})

        items = req.get("input", [])
        if isinstance(items, str):
            messages.append({"role": "user", "content": items})
            return messages
        if not isinstance(items, list):
            return messages

        for item in items:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "content": item.get("output", ""),
                })
            elif item_type == "function_call":
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": item.get("name", ""),
                                     "arguments": item.get("arguments", "{}")},
                    }],
                })
            elif item_type in ("input_text", "input_image", "image", "output_text", "text"):
                messages.append({"role": item.get("role", "user"), "content": [item]})
            elif item.get("role") == "assistant" or (item_type == "message" and item.get("role") == "assistant"):
                content = item.get("content", [])
                text_acc, calls = "", []
                if isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "output_text":
                            text_acc += part.get("text", "") or ""
                        elif part.get("type") == "function_call":
                            calls.append(part)
                elif isinstance(content, str):
                    text_acc = content
                message = {"role": "assistant", "content": text_acc or None}
                if calls:
                    message["tool_calls"] = [{
                        "id": call.get("call_id") or f"call_{index}",
                        "type": "function",
                        "function": {"name": call.get("name", ""),
                                     "arguments": call.get("arguments", "{}")},
                    } for index, call in enumerate(calls)]
                messages.append(message)
            else:
                messages.append({"role": item.get("role", "user"),
                                 "content": item.get("content", "")})
        return messages

    def _handle_responses(self, body):
        req = self._parse_body(body)
        if req is None:
            self.send_error_json(400, "request body must be a JSON object", "invalid_request_error")
            return
        resolved = self._resolve(req.get("model"))
        if resolved is None:
            return
        model_name, model_id, think_mode, extra_fields = resolved

        # The Responses API spells this `text.format`; `response_format` is
        # accepted too because clients migrating from chat completions send it.
        # Reading only the documented spelling would silently ignore the other
        # — the exact failure mode this project removed from its Worker.
        text_options = req.get("text")
        format_spec = None
        if isinstance(text_options, dict) and "format" in text_options:
            format_spec = text_options.get("format")
        elif "response_format" in req:
            format_spec = req.get("response_format")
        json_spec, error = jsonmode.parse(format_spec)
        if error:
            self.send_error_json(400, error, "invalid_request_error",
                                 "invalid_response_format")
            return

        messages = self._responses_to_messages(req)
        tools = req.get("tools")
        tool_choice = req.get("tool_choice", "auto")
        prompt, images = messages_to_prompt(messages, tools, tool_choice)
        if not prompt.strip():
            self.send_error_json(400, "empty input", "invalid_request_error", "empty_input")
            return
        if json_spec:
            prompt += "\n\n" + jsonmode.instruction_for(json_spec)

        file_refs, error = self._prepare_images(images)
        if error is not None:
            return

        wants_tools = bool(tools) and tool_choice != "none"
        log(f"{self.request_id} responses model={model_name} tools={wants_tools} "
            f"prompt_chars={len(prompt)} "
            f"json={json_spec['kind'] if json_spec else 'off'}")

        try:
            text = self._generate(prompt, model_id, think_mode, file_refs, extra_fields, model_name)
        except Exception as exc:
            self._upstream_failure(exc)
            return

        tool_calls = None
        if wants_tools and text:
            text, tool_calls = parse_tool_calls(text)
            if tool_calls:
                inc("tool_calls_parsed", len(tool_calls))

        if json_spec and not tool_calls:
            text = self._json_content(json_spec, text)
            if text is None:
                return

        rid = f"resp_{uuid.uuid4().hex[:16]}"
        mid = f"msg_{uuid.uuid4().hex[:12]}"
        output = []
        if tool_calls:
            for call in tool_calls:
                output.append({
                    "type": "function_call", "id": call["id"], "call_id": call["id"],
                    "name": call["function"]["name"], "arguments": call["function"]["arguments"],
                    "status": "completed",
                })
        if text or not tool_calls:
            output.append({
                "type": "message", "id": mid, "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text or "", "annotations": []}],
            })

        if req.get("stream"):
            self._stream_responses(rid, model_name, prompt, text, output)
        else:
            usage = _usage(prompt, text)
            self.send_json({
                "id": rid, "object": "response", "created_at": int(time.time()),
                "status": "completed", "model": model_name, "output": output,
                "usage": {
                    "input_tokens": usage["prompt_tokens"],
                    "output_tokens": usage["completion_tokens"],
                    "total_tokens": usage["total_tokens"],
                },
            })

    def _stream_responses(self, rid, model_name, prompt, text, output):
        """Emit the Responses API event sequence.

        The ordering and the ``sequence_number`` field are part of the contract
        Codex CLI relies on, so they are asserted in the test suite.
        """
        self._start_sse()
        inc("streams")
        sequence = [0]

        def emit(event_type, **fields):
            sequence[0] += 1
            return self._sse_write({"type": event_type, "sequence_number": sequence[0], **fields},
                                   event=event_type)

        usage = _usage(prompt, text)
        base = {"id": rid, "object": "response", "created_at": int(time.time()), "model": model_name}
        in_progress = {**base, "status": "in_progress", "output": [], "usage": None}
        emit("response.created", response=in_progress)
        emit("response.in_progress", response=in_progress)

        for output_index, item in enumerate(output):
            if item["type"] == "function_call":
                emit("response.output_item.added", output_index=output_index, item={
                    "type": "function_call", "id": item["id"], "call_id": item["call_id"],
                    "name": item["name"], "arguments": "", "status": "in_progress",
                })
                emit("response.function_call_arguments.delta", item_id=item["id"],
                     output_index=output_index, delta=item["arguments"])
                emit("response.function_call_arguments.done", item_id=item["id"],
                     output_index=output_index, arguments=item["arguments"])
                emit("response.output_item.done", output_index=output_index, item=item)
            elif item["type"] == "message":
                emit("response.output_item.added", output_index=output_index, item={
                    "type": "message", "id": item["id"], "role": "assistant",
                    "status": "in_progress", "content": [],
                })
                for content_index, part in enumerate(item["content"]):
                    emit("response.content_part.added", item_id=item["id"],
                         output_index=output_index, content_index=content_index,
                         part={"type": "output_text", "text": "", "annotations": []})
                    emit("response.output_text.delta", item_id=item["id"],
                         output_index=output_index, content_index=content_index,
                         delta=part["text"])
                    emit("response.output_text.done", item_id=item["id"],
                         output_index=output_index, content_index=content_index,
                         text=part["text"])
                    emit("response.content_part.done", item_id=item["id"],
                         output_index=output_index, content_index=content_index, part=part)
                emit("response.output_item.done", output_index=output_index, item=item)

        emit("response.completed", response={
            **base, "status": "completed", "output": output,
            "usage": {
                "input_tokens": usage["prompt_tokens"],
                "output_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
            },
        })

    # ─── /v1beta (Google-native, Gemini CLI) ─────────────────────────────────

    def _handle_google_generate(self, body, stream):
        req = self._parse_body(body)
        if req is None:
            self.send_error_json(400, "request body must be a JSON object", "invalid_request_error")
            return

        path, _ = self._split_path()
        match = re.match(r"/v1beta/models/([^:?]+)", path)
        if not match:
            self.send_error_json(400, "model not specified in path", "invalid_request_error")
            return
        resolved = self._resolve(match.group(1))
        if resolved is None:
            return
        model_name, model_id, think_mode, extra_fields = resolved

        tool_config = req.get("toolConfig") or req.get("tool_config") or {}
        fc_mode = (tool_config.get("functionCallingConfig")
                   or tool_config.get("function_calling_config") or {}).get("mode", "AUTO")
        has_tools = bool(req.get("tools")) and fc_mode != "NONE"

        prompt, images = google_contents_to_prompt(req)
        if not prompt.strip():
            self.send_error_json(400, "empty content", "invalid_request_error", "empty_content")
            return

        file_refs, error = self._prepare_images(images)
        if error is not None:
            return

        log(f"{self.request_id} google model={model_name} stream={stream} tools={has_tools} "
            f"prompt_chars={len(prompt)}")

        if stream and not has_tools:
            self._stream_google(model_name, model_id, think_mode, prompt, file_refs, extra_fields)
            return

        try:
            text = self._generate(prompt, model_id, think_mode, file_refs, extra_fields, model_name)
        except Exception as exc:
            self._upstream_failure(exc)
            return

        if not text:
            log(f"{self.request_id} empty response from Gemini", "warning")

        parts = []
        if has_tools and text:
            remaining, calls = parse_google_function_calls(text)
            if calls:
                inc("tool_calls_parsed", len(calls))
                if remaining:
                    parts.append({"text": remaining})
                for call in calls:
                    parts.append({"functionCall": {"name": call["name"], "args": call["args"]}})
            else:
                parts.append({"text": text})
        else:
            parts.append({"text": text or _EMPTY_COMPLETION})

        response_obj = {
            "candidates": [{
                "content": {"parts": parts, "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }],
            "usageMetadata": _google_usage(prompt, text),
            "modelVersion": model_name,
        }

        if stream:
            # Tools require the whole reply before calls can be parsed, so this
            # is a single-frame stream.
            self._start_sse()
            inc("streams")
            self._sse_write(response_obj)
        else:
            self.send_json(response_obj)

    def _stream_google(self, model_name, model_id, think_mode, prompt, file_refs, extra_fields):
        self._start_sse()
        inc("streams")
        collected = []
        failed = False
        try:
            for delta in self._stream(prompt, model_id, think_mode, file_refs,
                                      extra_fields, model_name):
                if not delta:
                    continue
                collected.append(delta)
                if not self._sse_write({
                    "candidates": [{"content": {"parts": [{"text": delta}], "role": "model"},
                                    "index": 0}],
                    "modelVersion": model_name,
                }):
                    return
        except Exception as exc:
            failed = True
            log(f"{self.request_id} google stream error: {exc}", "error")
            self._sse_write({"error": {"code": 502, "message": f"upstream error: {exc}",
                                       "status": "INTERNAL"}})

        text = "".join(collected)
        # A terminating frame is always sent so the client can tell a completed
        # stream from a dropped connection.
        self._sse_write({
            "candidates": [{"finishReason": "STOP" if not failed else "OTHER",
                            "index": 0, "content": {"parts": [], "role": "model"}}],
            "usageMetadata": _google_usage(prompt, text),
            "modelVersion": model_name,
        })

    # ─── status payloads ─────────────────────────────────────────────────────

    def _health_payload(self):
        from .metrics import snapshot as metrics_snapshot
        metrics = metrics_snapshot()
        warnings = self._startup_warnings()
        return {
            "status": "ok",
            "ready": not warnings.get("fatal"),
            "version": __version__,
            "uptime_sec": round(time.time() - _START_TIME, 1),
            "requests_served": metrics["counters"]["requests"],
            "streaming": "httpx" if HAS_HTTPX else "buffered (install httpx for real streaming)",
            "cookie_configured": bool(CONFIG.get("cookie_file")),
            "auth_enabled": bool(CONFIG.get("api_keys")),
            "default_model": CONFIG.get("default_model"),
            "model_count": len(MODELS),
            "checks": warnings,
        }

    def _startup_warnings(self):
        """Operational problems worth surfacing without failing the probe."""
        checks = {"fatal": [], "warnings": []}
        cookie_file = CONFIG.get("cookie_file")
        if cookie_file:
            import os
            if not os.path.exists(cookie_file):
                checks["warnings"].append(f"cookie_file does not exist: {cookie_file}")
        if not CONFIG.get("api_keys"):
            checks["warnings"].append(
                "authentication is disabled: anyone who can reach this port can use it")
        if not HAS_HTTPX:
            checks["warnings"].append("httpx is not installed; streaming is buffered")
        if CONFIG.get("host") == "0.0.0.0" and not CONFIG.get("block_private_image_urls", True):
            checks["warnings"].append(
                "block_private_image_urls is disabled while listening on 0.0.0.0 (SSRF risk)")
        return checks

    def _status_payload(self):
        payload = self._health_payload()
        payload["config"] = config_snapshot()
        payload["config_file"] = None
        metrics = metrics_snapshot()
        # History is kept out of the metrics snapshot itself: it can hold
        # hundreds of entries and every /status poll would re-serialise them.
        limit = int(CONFIG.get("history_max") or 0)
        history = metrics_history(limit) if limit > 0 else []
        metrics.pop("history", None)
        payload["metrics"] = metrics
        payload["history"] = history
        payload["rate_limit"] = LIMITER.snapshot()
        # Per-account health, so an operator can see which cookie is in use and
        # which is resting. Never the cookie itself.
        payload["credentials"] = cookie_pool_snapshot()
        payload["python"] = platform.python_version()
        payload["platform"] = platform.platform()
        return payload

    def _dashboard_state(self):
        from .metrics import snapshot as metrics_snapshot
        metrics = metrics_snapshot()
        config = config_snapshot()
        keys = CONFIG.get("api_keys") or []
        rate = LIMITER.snapshot()
        endpoints = [
            ("POST", "/v1/chat/completions", "OpenAI chat (streaming + tools)"),
            ("POST", "/v1/completions", "OpenAI legacy completions"),
            ("POST", "/v1/responses", "OpenAI Responses API (Codex CLI)"),
            ("GET", "/v1/models", "OpenAI model list"),
            ("GET", "/v1/models/{id}", "OpenAI model detail"),
            ("POST", "/v1beta/models/{id}:generateContent", "Google-native (Gemini CLI)"),
            ("POST", "/v1beta/models/{id}:streamGenerateContent", "Google-native streaming"),
            ("GET", "/v1beta/models", "Google-native model list"),
            ("GET", "/health", "Liveness probe (JSON, always public)"),
            ("GET", "/ready", "Readiness probe (503 when not ready)"),
            ("GET", "/status", "Detailed JSON status incl. metrics"),
        ]
        return {
            "version": __version__,
            "uptime_sec": metrics["uptime_sec"],
            "base_url": f"http://{self.headers.get('Host') or 'localhost'}/v1",
            "streaming": "httpx (true streaming)" if HAS_HTTPX else "urllib (buffered)",
            "api_keys": f"{len(keys)} configured" if keys else "disabled — open access",
            "cookie": "loaded" if config.get("cookie_present") else
                      ("configured but missing" if CONFIG.get("cookie_file") else "anonymous"),
            "default_model": CONFIG.get("default_model"),
            "gemini_bl": CONFIG.get("gemini_bl"),
            "proxy": CONFIG.get("proxy"),
            "rate_limit": (f"{rate['max_requests']}/{rate['window_sec']}s"
                           if rate["enabled"] else "disabled"),
            "temporary_chats": bool(CONFIG.get("temporary_chats")),
            "requests_served": metrics["counters"]["requests"],
            # The page is served from the public "/", so it may carry facts but
            # not history: request history includes client addresses and is only
            # available from the auth-gated /status. These flags let the UI
            # explain what it can and cannot show.
            "auth_enabled": bool(keys),
            "history_enabled": int(CONFIG.get("history_max") or 0) > 0,
            "python": platform.python_version(),
            "warnings": self._startup_warnings()["warnings"],
            "models": [
                {
                    "id": name,
                    "category": MODE_CATEGORY.get(cfg["mode"], str(cfg["mode"])),
                    "desc": cfg["desc"],
                    "output": cfg.get("output", "varies"),
                    "needs_cookie": bool(cfg.get("needs_cookie")),
                    "think": cfg["think"],
                }
                for name, cfg in MODELS.items()
            ],
            "endpoints": endpoints,
        }


class ThreadedServer(ThreadingMixIn, HTTPServer):
    """One thread per connection.

    ``daemon_threads`` stays on so a hung upstream cannot block process exit,
    but :meth:`shutdown_gracefully` gives in-flight requests a window to finish
    first. Without that, ``docker stop`` truncated active streams.
    """

    daemon_threads = True
    allow_reuse_address = True
    # Bind explicitly to IPv4; IPv6 dual-stack differs between platforms and
    # surprises people running in containers.
    address_family = socket.AF_INET
    # socketserver's default listen backlog is 5, which is far too small for a
    # service whose clients open several connections at once (chat UIs, the
    # Codex/Gemini CLIs, anything with a connection pool). Once the backlog
    # fills, the kernel drops the SYN and the client sits on its initial
    # retransmit timeout — a full second — before it is even accepted.
    # Measured here at 32 concurrent clients: 95% of requests answered in under
    # 10 ms while ~4% took ~1000 ms, a sharply bimodal distribution with almost
    # nothing in between, which is the signature of backlog overflow rather than
    # contention. 128 is the traditional Linux SOMAXCONN and comfortably below
    # this host's net.core.somaxconn, so it is not silently clamped.
    request_queue_size = 128

    def __init__(self, *args, **kwargs):
        self._request_threads = set()
        self._threads_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request, client_address):
        with self._threads_lock:
            self._request_threads.add(threading.current_thread())
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._threads_lock:
                self._request_threads.discard(threading.current_thread())

    def shutdown_gracefully(self, timeout=None):
        """Stop accepting, drain in-flight requests, then close the socket."""
        timeout = CONFIG.get("shutdown_timeout_sec", 5) if timeout is None else timeout
        self.shutdown()
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            with self._threads_lock:
                if not self._request_threads:
                    break
            time.sleep(0.05)
        self.server_close()

    def handle_error(self, request, client_address):
        """Log, but do not print a traceback for ordinary disconnects."""
        exc_type = sys.exc_info()[0]
        if exc_type in (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            return
        super().handle_error(request, client_address)


def build_server(host=None, port=None):
    """Create a :class:`ThreadedServer` bound to the configured address."""
    host = host or CONFIG.get("host", "0.0.0.0")
    port = int(port or CONFIG.get("port", 8081))
    LIMITER.configure(CONFIG.get("rate_limit_max", 0), CONFIG.get("rate_limit_window_sec", 60))
    return ThreadedServer((host, port), GeminiHandler)
