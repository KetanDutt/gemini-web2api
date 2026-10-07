# API reference

Base URL: `http://<host>:<port>` — OpenAI clients append `/v1`.

Every response carries `X-Request-Id`. Quote it when reporting a problem: the
server logs it alongside the outcome, so one ID ties a client-side error to a
server-side cause.

## Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | yes | OpenAI chat, streaming and tools |
| `POST` | `/v1/completions` | yes | OpenAI legacy text completion |
| `POST` | `/v1/responses` | yes | OpenAI Responses API (Codex CLI) |
| `GET` | `/v1/models` | yes | OpenAI model list |
| `GET` | `/v1/models/{id}` | yes | OpenAI model detail |
| `POST` | `/v1beta/models/{id}:generateContent` | yes | Google-native (Gemini CLI) |
| `POST` | `/v1beta/models/{id}:streamGenerateContent` | yes | Google-native streaming |
| `GET` | `/v1beta/models` | yes | Google-native model list |
| `GET` | `/v1beta/models/{id}` | yes | Google-native model detail |
| `GET` | `/` | no | Dashboard (browser) or JSON (programmatic) |
| `GET` | `/health`, `/healthz`, `/live` | no | Liveness probe |
| `GET` | `/ready` | no | Readiness probe, 503 when not ready |
| `GET` | `/status` | when keys set | Metrics and redacted config |
| `GET` | `/metrics` | when keys set | Prometheus text exposition |
| `OPTIONS` | any | no | CORS preflight |
| `HEAD` | `/health` | no | Probe without a body |

Query strings are ignored for routing, so `/v1/models?limit=100` works.
A trailing slash is accepted.

---

## POST /v1/chat/completions

### Request

```json
{
  "model": "gemini-3.5-flash-thinking",
  "messages": [
    {"role": "system", "content": "Answer in one sentence."},
    {"role": "user", "content": "Why is the sky blue?"}
  ],
  "stream": false
}
```

| Field | Notes |
|---|---|
| `model` | Optional; defaults to `default_model`. Unknown names fall back to the default unless `strict_models` is set. |
| `messages` | `system`, `developer`, `user`, `assistant`, `tool`. Content may be a string or a part list. |
| `stream` | Requires `httpx` for incremental delivery. |
| `stream_options.include_usage` | Emits a final chunk with `choices: []` and `usage`. |
| `tools` | OpenAI function declarations. See [Tool calling](#tool-calling). |
| `tool_choice` | `"none"`, `"auto"`, `"required"`, or `{"type":"function","function":{"name":…}}`. |
| `response_format` | `{"type":"json_object"}` or `{"type":"json_schema",…}`. See [JSON mode](#json-mode). |

`temperature`, `top_p`, `max_tokens` and `n` are **accepted and ignored**: the
Gemini Web endpoint exposes no such controls.

Model names accept an `@think=N` suffix (`0` deepest … `4` shallowest):

```json
{"model": "gemini-3.5-flash-thinking@think=2", "messages": […]}
```

### Response

```json
{
  "id": "chatcmpl-9f3a1c2b8d40",
  "object": "chat.completion",
  "created": 1780000000,
  "model": "gemini-3.5-flash-thinking",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "Rayleigh scattering."},
    "finish_reason": "stop",
    "logprobs": null
  }],
  "usage": {"prompt_tokens": 24, "completion_tokens": 5, "total_tokens": 29},
  "system_fingerprint": "gemini-web2api-1.2.0"
}
```

`usage` is estimated as characters ÷ 4. Gemini Web reports no token counts, so
any number here is an approximation.

### Streaming

```bash
curl -N http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" -H "Authorization: Bearer $KEY" \
  -d '{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"Count to five"}],"stream":true}'
```

```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"One"},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Guarantees:

* The first chunk carries only `{"role": "assistant"}`. Adding an empty
  `content` field trips up strictly-typed clients.
* `data: [DONE]` is **always** sent, including on failure. Without it an OpenAI
  client waits forever for a terminator.
* On a mid-stream failure an `{"error": {…}}` data frame is emitted first, then
  the terminating chunk, then `[DONE]`. `finish_reason` is `"length"` rather than
  `"stop"` so the client can tell a truncation from a clean end.
* Response headers include `X-Accel-Buffering: no` and
  `Cache-Control: no-cache, no-transform` so reverse proxies do not buffer.

When the request carries `tools`, streaming degrades to a single chunk: calls
can only be recognised once the full reply is in hand.

---

## POST /v1/completions

The legacy text-completion API, for clients that predate chat.

```json
{"model": "gemini-3.6-flash", "prompt": "The capital of France is", "stream": false}
```

`prompt` may be a string or a list of strings (joined with newlines).

```json
{
  "id": "cmpl-…",
  "object": "text_completion",
  "created": 1780000000,
  "model": "gemini-3.6-flash",
  "choices": [{"index": 0, "text": " Paris.", "logprobs": null, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
}
```

Streaming uses the same `data: [DONE]` contract, with `choices[0].text` carrying
each delta.

---

## POST /v1/responses

The Responses API, used by OpenAI Codex CLI.

```json
{
  "model": "gemini-3.6-flash",
  "instructions": "You are a terse assistant.",
  "input": [
    {"type": "message", "role": "user",
     "content": [{"type": "input_text", "text": "Weather in Tokyo?"}]},
    {"type": "function_call", "call_id": "c1", "name": "get_weather",
     "arguments": "{\"city\":\"Tokyo\"}"},
    {"type": "function_call_output", "call_id": "c1", "name": "get_weather",
     "output": "{\"temp\":21}"}
  ],
  "tools": [{"type": "function", "name": "get_weather",
             "description": "Weather for a city", "parameters": {"type": "object"}}],
  "stream": true
}
```

`input` may also be a plain string. Note that Responses-style tools are *flat*
(`name` at the top level) while chat-completion tools are *nested* under
`function`; both are accepted anywhere.

### Non-streaming response

```json
{
  "id": "resp_…", "object": "response", "created_at": 1780000000,
  "status": "completed", "model": "gemini-3.6-flash",
  "output": [
    {"type": "function_call", "id": "call_ab12cd34", "call_id": "call_ab12cd34",
     "name": "get_weather", "arguments": "{\"city\":\"Tokyo\"}", "status": "completed"},
    {"type": "message", "id": "msg_…", "role": "assistant", "status": "completed",
     "content": [{"type": "output_text", "text": "21°C.", "annotations": []}]}
  ],
  "usage": {"input_tokens": 40, "output_tokens": 8, "total_tokens": 48}
}
```

### Streaming event sequence

Text output:

```
response.created
response.in_progress
response.output_item.added
response.content_part.added
response.output_text.delta
response.output_text.done
response.content_part.done
response.output_item.done
response.completed
```

Function-call output substitutes the four middle events for:

```
response.output_item.added
response.function_call_arguments.delta
response.function_call_arguments.done
response.output_item.done
```

Each event is written as both an SSE `event:` line and a `data:` line, and
carries a `sequence_number` starting at 1 and increasing by one. Codex CLI
depends on all three; the ordering is asserted in the test suite.

---

## Google-native endpoints (Gemini CLI)

```bash
export GEMINI_API_KEY=none
export GOOGLE_GEMINI_BASE_URL=http://localhost:8081
gemini
```

### POST /v1beta/models/{model}:generateContent

```json
{
  "systemInstruction": {"parts": [{"text": "Answer in French"}]},
  "contents": [{"role": "user", "parts": [{"text": "Bonjour"}]}],
  "tools": [{"functionDeclarations": [
    {"name": "get_weather", "description": "Weather", "parameters": {"type": "object"}}]}],
  "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}}
}
```

`functionCallingConfig.mode` accepts `AUTO`, `ANY` (with optional
`allowedFunctionNames`) and `NONE`. Both camelCase and snake_case field spellings
are accepted (`inlineData` / `inline_data`, `systemInstruction` /
`system_instruction`), because Gemini CLI versions differ.

```json
{
  "candidates": [{
    "content": {"parts": [{"text": "Bonjour !"}], "role": "model"},
    "finishReason": "STOP", "index": 0
  }],
  "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3, "totalTokenCount": 12},
  "modelVersion": "gemini-3.6-flash"
}
```

Function calls come back as parts:

```json
{"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Tokyo"}}}]}
```

When the model produces no text at all, `parts[0].text` is a short placeholder
rather than an empty string, because an empty part makes Gemini CLI hang.

### POST /v1beta/models/{model}:streamGenerateContent

SSE, one `data:` frame per delta:

```
data: {"candidates":[{"content":{"parts":[{"text":"Bon"}],"role":"model"},"index":0}],"modelVersion":"gemini-3.6-flash"}

data: {"candidates":[{"content":{"parts":[{"text":"jour"}],"role":"model"},"index":0}],"modelVersion":"gemini-3.6-flash"}

data: {"candidates":[{"finishReason":"STOP","index":0,"content":{"parts":[],"role":"model"}}],"usageMetadata":{…},"modelVersion":"gemini-3.6-flash"}
```

The final frame always arrives, carrying `finishReason` and `usageMetadata` —
`STOP` on success, `OTHER` if the upstream failed mid-stream. Add `?alt=sse`;
query parameters are ignored for routing.

### GET /v1beta/models and GET /v1beta/models/{model}

The list returns every model as `{"name": "models/<id>", …}`. The single-model
form returns just that object — it used to return the entire list, which
confused clients that asked for one model.

---

## Status endpoints

### GET /health

Always public, always 200 while the process is up. Suitable for a container
liveness probe.

```json
{
  "status": "ok", "ready": true, "version": "1.2.0", "uptime_sec": 42.1,
  "requests_served": 17, "streaming": "httpx",
  "cookie_configured": false, "auth_enabled": false,
  "default_model": "gemini-3.6-flash", "model_count": 9,
  "checks": {"fatal": [], "warnings": ["authentication is disabled: …"]}
}
```

### GET /ready

Same payload; returns **503** when `checks.fatal` is non-empty. Use it as a
Kubernetes readiness probe.

### GET /status

Adds `config` (secrets redacted), `metrics`, `rate_limit` and `history`.
Requires an API key when keys are configured.

```json
{
  "metrics": {
    "uptime_sec": 3600.2,
      "counters": {"requests": 512, "completions": 480, "streams": 32,
                   "errors_4xx": 3, "errors_5xx": 1, "upstream_failures": 1,
                   "rate_limited": 0, "images_uploaded": 4, "tool_calls_parsed": 12,
                   "json_mode_failures": 2},
    "status_codes": {"200": 508, "401": 3, "502": 1},
    "latency_ms_avg": 1840.5, "latency_ms_samples": 512,
    "latency_histogram_ms": {"50": 0, "100": 0, "…": 0, "inf": 3},
    "models": {"gemini-3.6-flash": {"requests": 500, "avg_ms": 1830.1, "max_ms": 9204.7}}
  },
  "history": [
    {"ts": 1760000000.1, "id": "9f2c1ab7d403", "method": "POST",
     "path": "/v1/chat/completions", "status": 200,
     "model": "gemini-3.6-flash", "ms": 1841.2, "client": "203.0.113.9"}
  ]
}
```

`history` is the request log behind the dashboard's **Activity** tab: the most
recent `history_max` requests (default 200, capped at 1000), newest first. Set
`"history_max": 0` to disable it entirely.

Entries hold operational facts only — timestamp, `X-Request-Id`, method, path,
status, the **resolved** model (so a silent fallback from an unknown model name
is visible), latency and client address. Prompts, response bodies and
credentials are never stored, and query strings are stripped so a
`?key=<api_key>` cannot be retained.

Because entries include client addresses, `history` is served **only** from this
auth-gated endpoint. It is deliberately excluded from `metrics` and from the
state embedded in the public `GET /` dashboard, which carries an
`history_enabled` flag instead of any entries.

### GET /metrics

Prometheus text exposition (version `0.0.4`), for a scraper you already run.
Requires an API key when keys are configured — the same gate as `/status`, and
for the same reason: traffic volume, model mix and error rate describe what a
deployment is for and when it is struggling.

```
# HELP gemini_web2api_completions_total Non-streaming chat completions answered.
# TYPE gemini_web2api_completions_total counter
gemini_web2api_completions_total 480
# HELP gemini_web2api_request_duration_seconds Upstream round-trip time for chat requests.
# TYPE gemini_web2api_request_duration_seconds histogram
gemini_web2api_request_duration_seconds_bucket{le="2.5"} 431
gemini_web2api_request_duration_seconds_bucket{le="+Inf"} 480
gemini_web2api_request_duration_seconds_sum 720.4
gemini_web2api_request_duration_seconds_count 480
gemini_web2api_model_requests_total{model="gemini-3.6-flash"} 500
```

Exported series:

| Series | Type | Notes |
|---|---|---|
| `gemini_web2api_uptime_seconds` | gauge | Since process start. |
| `gemini_web2api_<counter>_total` | counter | One per `/status` counter, including zeros. |
| `gemini_web2api_http_responses_total{status}` | counter | Per status code. |
| `gemini_web2api_request_duration_seconds` | histogram | Upstream round trip; buckets at 50ms–60s. |
| `gemini_web2api_model_requests_total{model}` | counter | Requests per resolved model. |
| `gemini_web2api_model_request_duration_seconds_sum{model}` | counter | For `rate()`-based averages. |
| `gemini_web2api_model_request_duration_seconds_max{model}` | gauge | Slowest seen. |

Three things are worth knowing before pointing a scraper at it:

* **Durations are seconds.** The internal histogram is in milliseconds; the
  export converts, because a metric named `_seconds` carrying milliseconds
  rescales every dashboard and alert threshold by 1000 without changing a label.
* **The histogram is global, not per-model.** There is one set of buckets, so
  per-model latency is exposed as sum/count/max rather than as separate
  histograms. A per-model `histogram_quantile` is therefore not available; use
  `rate(..._sum{model="…"}[5m]) / rate(..._count{model="…"}[5m])` for an average.
* **The numbers are per-process.** With several worker processes, each exposes
  its own; Prometheus scraping one port sees one process. Run one process per
  scrape target, or aggregate in the query.

A scrape config with authentication:

```yaml
scrape_configs:
  - job_name: gemini-web2api
    metrics_path: /metrics
    authorization:
      credentials: sk-your-key
    static_configs:
      - targets: ["127.0.0.1:8081"]
```

### GET /

Content-negotiated. A browser (`Accept: text/html`) gets the web console;
anything else gets JSON:

```json
{"status": "ok", "version": "1.2.0", "models": ["gemini-3.7-flash", "…"],
 "endpoints": ["…"], "dashboard": "send 'Accept: text/html' for the web dashboard"}
```

Force JSON from a browser with `/?format=json`.

The console is a single self-contained page — no CDN, no fonts, no external
requests, so it works air-gapped. It has five tabs:

| Tab | What it does |
|---|---|
| **Chat** | A streaming playground. Keeps conversations in your browser's `localStorage`, supports multiple saved chats, model and thinking-depth selection, and a Stop button. Multi-turn works by resending the transcript, since Gemini's web endpoint is single-turn. |
| **Status** | Runtime cards, health checks, counters, latency, histogram, per-model breakdown, status codes and the redacted config. Auto-refreshes every 10s. |
| **Activity** | The `history` table from `/status`, filterable by all / errors / route. Auto-refreshes every 5s. |
| **Models** | Every model with its category, output budget and whether it needs a cookie. |
| **API** | Endpoint reference plus ready-to-paste client config and a `curl` example. |

The **Status** and **Activity** tabs call `/status`, so when API keys are
configured they need one pasted into the key field; it is stored in
`localStorage` for that browser only and is never embedded in the page. Chat
history stays client-side — the server keeps no conversation records.

---

## Error format

Errors follow OpenAI's shape, so client SDKs surface them properly:

```json
{"error": {"message": "invalid api key", "type": "authentication_error",
           "code": "invalid_api_key", "param": null}}
```

| Status | `code` | Cause |
|---|---|---|
| 400 | `invalid_model` | Bad `@think=` value, or unknown model with `strict_models` on |
| 400 | `empty_prompt` / `empty_input` / `empty_content` | No content after conversion |
| 400 | `image_rejected` | Image URL refused (SSRF policy) or unfetchable |
| 401 | `invalid_api_key` | Missing or wrong key. Response includes `WWW-Authenticate`. |
| 404 | `model_not_found` | Unknown model on a retrieve endpoint |
| 404 | — | Unknown path |
| 413 | `payload_too_large` | Body over `max_request_bytes` |
| 429 | `rate_limit_exceeded` | Your rate limit; `Retry-After` is set |
| 429 | `upstream_rate_limited` | Google is throttling |
| 501 | `unsupported_endpoint` | `/v1/embeddings`, `/v1/audio/speech`, `/v1/images/generations` |
| 502 | `stale_build_tag` | HTTP 405 upstream — the `bl` build tag is out of date |
| 502 | `image_upload_failed` | Google rejected the image upload |
| 502 | — | Any other upstream failure |
| 500 | — | Unexpected internal error |

---

## Tool calling

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8081/v1", api_key="sk-your-key")

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}]

response = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
    tools=tools,
)
call = response.choices[0].message.tool_calls[0]
print(call.function.name, call.function.arguments)
```

Tool calling is prompt-based, so reliability depends on the model following the
requested format. In practice:

* Prefer `gemini-3.6-flash` or newer for tool use.
* Keep schemas small. Above 30 000 characters of JSON the parameter blocks are
  dropped and only names and descriptions are sent.
* Send tool results back as a `role: "tool"` message; the server formats them as
  `[Tool result for <name>]: <json>`.
* Use `tool_choice: "required"` when a call is mandatory — it adds an explicit
  instruction to the prompt.

  Malformed tool blocks are dropped rather than returned as text, so a client
  never sees raw fence syntax.

## JSON mode

```python
response = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": "Invent a person."}],
    response_format={"type": "json_object"},
)
json.loads(response.choices[0].message.content)   # always parses
```

Supported on `POST /v1/chat/completions`, `POST /v1/completions`, and — spelled
`text.format`, as that API defines it — `POST /v1/responses`. Both spellings are
read on `/v1/responses`, because clients migrating from chat completions send
`response_format`.

| `type` | Behaviour |
|---|---|
| `text` | The default. No constraint; identical to omitting the field. |
| `json_object` | The reply must be a JSON **object**. |
| `json_schema` | The reply must validate against `json_schema.schema`. |

### How it works, and what it does not guarantee

The Gemini Web endpoint has no equivalent of `response_format`, and a proxy
cannot create one. What this service does instead is:

1. **Append an instruction** to the prompt asking for a single bare JSON value,
   including the schema when one was given. The instruction is appended *last*,
   because the conversation is flattened into one text block and the most
   recent instruction governs — so a client's earlier "reply in prose" cannot
   override it.
2. **Validate the reply** before returning it. A reply is searched for a JSON
   value (bare, inside a code fence, or embedded in a sentence), and if a schema
   was supplied the value is checked against it.

**The difference from OpenAI matters, and is not hidden.** OpenAI enforces
`json_schema` *during* generation, so its output cannot violate the schema.
Here the constraint is a request in a prompt, checked afterwards, so a
violation is possible. When the model does not comply, the request fails with
`502` rather than returning something that looks right:

| `code` | Meaning |
|---|---|
| `json_parse_failed` | The reply contained no JSON value at all. |
| `json_schema_violation` | The reply parsed, but did not match the schema. |

Both are `502`, because the fault is the model's, not the client's. The error
message names the failing path (for example `$.items[1].n: expected integer,
got string`) so the cause is visible without re-running the request.

What is returned is the **validated value re-serialised**, not the raw reply.
Passing the raw text through would mean checking one string and sending
another — and the raw text is frequently not JSON, since models add code fences
or a sentence of preamble no matter what they were told.

A request offering `tools` is exempt: if the model answers with a tool call,
that is a legitimate outcome (`finish_reason: "tool_calls"`) and the leftover
text is not the answer, so it is not validated against the schema.

Streaming requests are buffered in JSON mode and delivered as a single chunk,
for the same reason tool calls are: validity can only be judged once the reply
is complete, and a `200` header cannot be retracted. The stream is still
well-formed and terminates with `[DONE]`.

### Enforced schema keywords

A partial validator that does not say what it skips is worse than none, so this
is the complete list of what is checked:

`type` (including unions) · `enum` · `const` · `required` · `properties` ·
`additionalProperties` (`false` and subschema forms) · `items` · `minItems` ·
`maxItems` · `minLength` · `maxLength` · `pattern` · `minimum` · `maximum` ·
`exclusiveMinimum` · `exclusiveMaximum`

Any other keyword is **not** enforced — `format`, `oneOf`, `anyOf`, `allOf`,
`$ref`, `not` and the rest are ignored. A schema relying on them is not fully
validated, and this service does not claim otherwise. An unenforceable
`pattern` (an invalid regular expression) is ignored rather than reported as a
model violation, since that is the client's schema being wrong.

  ## Image input

```python
response = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "https://example.com/diagram.png"}},
    ]}],
)
```

Accepted forms: `http(s)` URLs, `data:` URLs (base64 or percent-encoded), and
bare base64 in `data`/`base64` fields. Google-native requests accept
`inlineData` and `fileData`.

The MIME type is sniffed from the bytes, not taken from the client's claim.
Remote URLs are refused when they resolve to loopback, link-local, private or
reserved addresses — see [SECURITY.md](SECURITY.md#ssrf-protection-on-image-fetching). Anonymous uploads
sometimes fail; configure a cookie if they do.
