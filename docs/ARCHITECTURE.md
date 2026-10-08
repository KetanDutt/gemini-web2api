# Architecture

## What this project is

`gemini-web2api` speaks two protocols at once. To its clients it is an OpenAI
API server. To Google it is a Chrome tab running the Gemini web app. Everything
here is translation between those two.

```
  OpenAI SDK / Cherry Studio / Codex CLI / Gemini CLI
                        │
                        ▼  HTTP/1.1, OpenAI or Google-native JSON
        ┌───────────────────────────────────────┐
        │            server.py                  │  routing, auth, rate limit,
        │        GeminiHandler                  │  SSE framing, error shapes
        └───────────────────────────────────────┘
              │                         │
              ▼                         ▼
      ┌──────────────┐          ┌──────────────┐
      │   tools.py   │          │ multimodal.py│   prompt assembly,
      │              │          │              │   tool-call parsing,
      └──────────────┘          └──────────────┘   image upload (SSRF-guarded)
              │                         │
              └────────────┬────────────┘
                           ▼
              ┌────────────────────────┐
              │       gemini.py        │  StreamGenerate framing, cookies,
              │                        │  SAPISIDHASH, build-tag refresh,
              └────────────────────────┘  cumulative-stream decoding
                           │
                           ▼  HTTPS, form-encoded f.req
              https://gemini.google.com/_/BardChatUi/data/
                assistant.lamda.BardFrontendService/StreamGenerate
```

## Module map

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Defaults, JSON file, environment variables, validation, secret redaction | — |
| `models.py` | Model table, `MODE_CATEGORY` mapping, `@think=` parsing | `config` |
| `gemini.py` | Cookie and credential-pool loading, payload framing, upstream HTTP, response parsing, streaming | `config`, `models`, `credentials` |
| `credentials.py` | The cookie pool: acquire, cooldowns, rotation state | — |
| `multimodal.py` | Remote image fetching (SSRF-guarded), MIME sniffing, Scotty resumable upload | `config`, `gemini` |
| `jsonmode.py` | `response_format` instruction, validation of the reply against it | — |
| `tools.py` | Prompt construction for both dialects, tool-call extraction | `gemini` |
| `server.py` | HTTP endpoints, auth, rate limiting, SSE, error mapping | everything |
| `webui.py` | Self-contained status dashboard (one HTML page, design tokens, no external assets) | — |
| `ratelimit.py` | Fixed-window limiter | — |
| `metrics.py` | Counters and latency histogram | — |
| `prometheus.py` | Prometheus text exposition of a metrics snapshot | `metrics` |
| `__main__.py` | CLI, config precedence, signal handling, startup banner | everything |
| `_healthcheck.py` | Container liveness probe (no package imports) | stdlib |

Dependencies point downwards only; there are no import cycles. Two modules
(`models.py`, `gemini.py`) do use lazy function-level imports to break what
would otherwise be a cycle with `config.py`.

## The upstream wire protocol

Gemini Web does not have a documented API. What follows is reverse-engineered
from the web client, and can change whenever Google ships a new frontend build.

### Request

A `POST` to:

```
https://gemini.google.com[/u/<auth_user>]/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate
  ?bl=<build tag>&hl=en&_reqid=<n>&rt=c
```

with `Content-Type: application/x-www-form-urlencoded` and one form field:

```
f.req = [null, "<json-encoded inner array>"]
```

plus `at=<xsrf token>` when authenticated.

The inner array is a positional protobuf-like structure. Only some slots matter:

| Slot | Meaning |
|---|---|
| `0` | `[prompt, 0, null, <image refs>, null, null, 0]` — the user text, and optionally uploaded file references |
| `1` | `["en"]` — language |
| `17` | `[[think_mode]]` — thinking depth, `0` deepest to `4` shallowest |
| `41` | `[2]` persist to history, `[1]` temporary chat |
| `45` | `1` when temporary |
| `59` | a fresh UUID per request — Gemini treats this as the conversation turn id |
| `79` | **the model selector** (`MODE_CATEGORY`) |
| `31`, `80` | experimental output-shaping fields used by `gemini-3.1-pro-enhanced` |

The payload is sized at 102 slots. It used to be 80, which is why
`gemini-3.1-pro-enhanced` (which writes slot 80) raised `IndexError` in the old
single-file implementation.

### `MODE_CATEGORY`

Taken from the Gemini frontend bundle:

```
1 = FAST                      2 = THINKING
3 = PRO                       4 = AUTO
5 = FAST_DYNAMIC_THINKING     6 = FLASH_LITE
```

Slot 79 selects a *mode*, not a specific checkpoint. This is the single most
misunderstood thing about the project: asking for mode `3` expresses a
preference the backend honours only if your account is entitled to it. See
[Limitations](../README.md#limitations).

### Response

The body is newline-delimited JSON, prefixed by a `)]}'` guard:

```
)]}'

[["wrb.fr","<rpcid>","<json-encoded inner>",null,...,"generic"]]
```

Each frame's `inner[4]` is a list of parts; `part[1]` is a list of strings.
**Frames are cumulative** — every frame repeats the answer so far. The complete
text is therefore in the last frame, which is what `extract_response_text`
takes.

Two consequences that were previously bugs:

* Selecting the *longest* segment instead of the last returns a preamble when a
  response has more than one segment.
* Length gates like `len(line) < 200` silently discard legitimate short frames,
  surfacing as `content: null`.

Internal artifacts are stripped before delivery:

```
```python?code_reference&code_event_index=0 ... ```     ← code-execution noise
http://googleusercontent.com/card_content/<n>           ← card placeholders
```

### Build tag (`bl`)

`bl` identifies the frontend build the request was composed for. Google rotates
it. A stale value produces **HTTP 405**. `gemini.py` scrapes the current value
from the Gemini page:

* at startup (`warm_up`), and
* on demand when a 405 arrives (`update_bl_if_needed`, rate-limited to once per
  minute so an outage cannot become a scrape storm).

If you pin `gemini_bl` in your config, auto-refresh is disabled for that key —
pinning means you want that exact value.

## Image handling

Gemini Web accepts images only as server-side file references, never inline. So
an OpenAI `image_url` part becomes:

1. **Resolve** — a `data:` URL is decoded in place; an `http(s)` URL is fetched
   (SSRF-guarded, size-capped, redirects re-validated).
2. **Sniff** — the MIME type is taken from the bytes, not from what the client
   claimed. Clients get this wrong often and Google rejects mismatches.
3. **Upload** — a Scotty resumable upload to `content-push.googleapis.com`:
   `X-Goog-Upload-Command: start` returns an upload URL, then
   `upload, finalize` sends the bytes and returns a file reference.
4. **Attach** — references go into payload slot `0[3]` as `[[null, null, ref]]`.

Page tokens (`Push-ID`, `X-Client-Pctx`) are scraped from the Gemini page and
cached for 10 minutes, with hardcoded fallbacks if the scrape fails.

## Tool calling

Gemini Web has no function-calling contract on this endpoint, so tools are
*described in the prompt* and *parsed out of the reply*. Two dialects:

| | fence | argument key | used by |
|---|---|---|---|
| OpenAI | ` ```tool_call ` | `arguments` | `/v1/chat/completions`, `/v1/responses` |
| Google | ` ```function_call ` | `args` | `/v1beta/...` |

Because parsing needs the complete reply, a request that carries `tools` cannot
be truly streamed: the server buffers, parses, then emits a single SSE chunk
with `finish_reason: "tool_calls"`. This is the standard trade-off every
prompt-based tool implementation makes.

Schemas are inlined into the prompt. Above `MAX_TOOL_SCHEMA_CHARS` (30 000) the
parameter blocks are dropped and only names and descriptions are kept, so a
client with a huge schema cannot crowd out the actual conversation.

Unfenced `function_call` output is parsed with a brace-balancing scanner rather
than a regex, because a non-greedy `\{[^`]*?\}` truncates at the first `}` and
nested arguments are the common case.

## Conversation model

**Every request is a fresh conversation.** Slot 59 gets a new UUID each time;
nothing is carried server-side. Multi-turn context exists only because the
client resends its history and `tools.py` flattens it into one prompt with role
markers:

```
[System instruction]: be brief

first question

[Assistant]: first answer

second question
```

This means token usage grows with conversation length, and very long histories
will eventually exceed what the upstream accepts. Set `temporary_chats: true` to
stop these single-turn exchanges from accumulating in your Google account's
conversation history.

## Concurrency

`ThreadedServer` is `ThreadingMixIn` + `HTTPServer`: one thread per connection,
daemon threads, `TCP_NODELAY` enabled for streaming latency.

Shared mutable state is deliberately minimal:

| State | Guard |
|---|---|
| `CONFIG` | written at startup only; `apply_defaults` uses a lock |
| cookie cache | keyed by `(path, mtime, size)` |
| httpx client | created under `_client_lock`, reused process-wide |
| SSL context | created under `_ssl_lock` |
| `bl` refresh | `_bl_lock` plus a 60-second cooldown |
| metrics | single lock |
| rate limiter | single lock |

The pooled httpx client is the reason non-streaming requests got faster: the old
code constructed a client per request and paid a full TLS handshake every time.

## HTTP/1.1 and keep-alive

`protocol_version = "HTTP/1.1"` keeps connections alive between requests, which
matters for clients that issue many small calls. Two invariants make that safe:

1. **Every request body is fully consumed before responding.** `do_POST` reads
   the body *before* checking the API key. Answering 401 first would leave
   unread bytes on the connection and corrupt the next pipelined request.
2. **Every response declares its length, or closes.** JSON responses set an
   exact `Content-Length`. SSE responses cannot, so they send `Connection: close`
   and set `close_connection = True`.

SSE responses also carry `Cache-Control: no-transform` and
`X-Accel-Buffering: no`; without the latter, nginx buffers the whole stream and
the typewriter effect disappears.

## The compatibility shim

`gemini_web2api.py` at the repository root is **not** an implementation. It is a
60-line entry point that calls `gemini_web2api.__main__:main`, kept so the
historical `python gemini_web2api.py` invocation still works.

Python resolves `import gemini_web2api` to the *package* (a directory beats a
module of the same name), so the shim can never shadow the real code. Before
1.2.0 both were full implementations and had drifted apart; see
[AUDIT.md](AUDIT.md#12-two-divergent-implementations-of-the-same-server--critical).
