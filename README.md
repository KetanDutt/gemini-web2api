# gemini-web2api

<p align="center">
  <img src="logo.png" width="200" alt="gemini-web2api logo">
</p>

<p align="center">
  <a href="README_CN.md">中文文档</a> ·
  <a href="docs/README.md">Documentation</a> ·
  <a href="docs/API.md">API reference</a> ·
  <a href="docs/CHANGELOG.md">Changelog</a>
</p>

Serve Google Gemini's web interface as an **OpenAI-compatible API**. No API key
from Google, no billing, no compiled dependencies — the core runs on the Python
standard library alone.

Point any OpenAI client at `http://localhost:8081/v1` and it works: Cherry
Studio, ChatBox, NextChat, LobeChat, the OpenAI SDK, Codex CLI, Gemini CLI.

```
  your OpenAI client  ──►  gemini-web2api  ──►  gemini.google.com
   /v1/chat/completions     translation          StreamGenerate
   /v1/responses            layer                (the web app's own endpoint)
   /v1beta/...
```

## Features

- **OpenAI-compatible** — `/v1/chat/completions`, `/v1/completions`,
  `/v1/responses`, `/v1/models`, `/v1/models/{id}`
- **Google-native** — `/v1beta/...` endpoints for Gemini CLI
- **Streaming** — real incremental SSE via `httpx`, with a buffered stdlib
  fallback
- **Tool calling** — function calling in both OpenAI and Google formats,
  including `tool_choice`
- **Image input** — URLs and base64, SSRF-guarded, uploaded through Gemini's own
  upload path
- **Nine models** — Flash, Extended Thinking, Pro, Auto, Lite, with adjustable
  thinking depth
- **Optional auth** — open by default, Bearer/`x-api-key`/`x-goog-api-key` when
  you set keys, plus optional rate limiting
- **Self-healing** — refreshes Google's build tag automatically instead of
  breaking on a frontend rollout
- **Web console** — open `http://localhost:8081/` for a chat playground, live
  status, request activity, model picker and ready-to-paste client config
- **Production-ready packaging** — non-root Docker image, healthchecks,
  environment configuration, graceful shutdown, CI
- **One-click Windows setup** — double-click `start.bat` to create a venv,
  install dependencies, write a safe `config.json` and launch
- **Zero required dependencies** — Python 3.8+, `httpx` optional

## Quick start

**Windows** — double-click [`start.bat`](start.bat). It finds Python, creates a
virtual environment, installs dependencies, writes a `config.json` bound to
`127.0.0.1`, starts the server and opens the dashboard. Nothing else to do.

**macOS / Linux**

```bash
pip install httpx          # optional, but needed for true streaming
python -m gemini_web2api
```

Then:

| What | Where |
|---|---|
| OpenAI base URL | `http://localhost:8081/v1` |
| Dashboard | `http://localhost:8081/` |
| Health check | `http://localhost:8081/health` |
| Status + metrics | `http://localhost:8081/status` |

Or install it properly:

```bash
pip install ".[streaming]"
gemini-web2api --port 8081
```

`python gemini_web2api.py` also works — that file is a compatibility shim
delegating to the package.

### Docker

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  -e GEMINI_WEB2API_API_KEYS="sk-your-key" \
  ghcr.io/ketandutt/gemini-web2api:latest
```

### Docker Compose

```bash
GEMINI_WEB2API_API_KEYS="sk-your-key" docker compose up -d
```

If Google rejects Docker's NAT address range (empty replies, 403/429), use the
host-networking variant:

```bash
docker compose -f docker-compose.local.yml up -d
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for systemd, reverse proxies and
Cloudflare Workers.

## Client configuration

### Cherry Studio / ChatBox / NextChat / any OpenAI client

| Field | Value |
|-------|-------|
| Base URL | `http://localhost:8081/v1` |
| API key | one of your `api_keys`; anything when auth is off |
| Model | `gemini-3.6-flash` |

### curl

```bash
curl http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-key" \
  -d '{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"Hello!"}]}'
```

Streaming (note `-N` to disable curl's own buffering):

```bash
curl -N http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"Count to five"}],"stream":true}'
```

<details>
<summary>PowerShell (Windows)</summary>

```powershell
curl.exe --% http://127.0.0.1:8081/v1/chat/completions -H "Content-Type: application/json" -H "Authorization: Bearer sk-your-key" -d "{\"model\":\"gemini-3.6-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello!\"}]}"
```

Use `curl.exe` and `--%` so PowerShell does not reinterpret the JSON quoting.

</details>

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8081/v1", api_key="sk-your-key")

print([m.id for m in client.models.list().data])

resp = client.chat.completions.create(
    model="gemini-3.5-flash-thinking",
    messages=[{"role": "user", "content": "Explain quantum computing"}],
)
print(resp.choices[0].message.content)

for chunk in client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": "Write a haiku about the sea"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### Gemini CLI

```bash
export GEMINI_API_KEY=none
export GOOGLE_GEMINI_BASE_URL=http://localhost:8081
gemini
```

### Codex CLI

Uses the Responses API (`/v1/responses`) with the full streaming event sequence.

## Models

| Model | Category | Description | Typical output |
|-------|----------|-------------|----------------|
| `gemini-3.7-flash` | FAST | Latest all-around model | ~12k chars |
| `gemini-3.6-flash` | FAST | All-around model (**default**) | ~12k chars |
| `gemini-3.5-flash` | FAST | Alias for gemini-3.6-flash | ~12k chars |
| `gemini-3.5-flash-thinking` | THINKING | Deep thinking, longest output | **~20k chars** |
| `gemini-3.5-flash-thinking-lite` | DYNAMIC | Adaptive thinking depth | ~15k chars |
| `gemini-3.1-pro` | PRO | Advanced reasoning — **needs a paid-session cookie** | ~12k chars |
| `gemini-3.1-pro-enhanced` | PRO | Pro with experimental output shaping | ~12k chars |
| `gemini-auto` | AUTO | Upstream picks the model | varies |
| `gemini-flash-lite` | FLASH_LITE | Fastest, most lightweight | ~10k chars |

The live list is always at `GET /v1/models`.

### Thinking depth

Append `@think=N` to any model name — `0` is deepest, `4` shallowest:

```
gemini-3.5-flash-thinking@think=0   # deepest (this model's default)
gemini-3.6-flash@think=0            # give Flash the deep-thinking budget
gemini-3.5-flash-thinking@think=2   # medium
gemini-3.5-flash-thinking@think=4   # shallowest, fastest
```

Out-of-range and non-integer values are rejected with a clear 400.

## Configuration

Create `config.json` (see [`config.example.json`](config.example.json) for every
key), use environment variables, or pass CLI flags. Precedence:
**defaults → file → environment → CLI**.

```json
{
  "port": 8081,
  "host": "0.0.0.0",
  "api_keys": ["sk-your-key"],
  "default_model": "gemini-3.6-flash",
  "cookie_file": null,
  "temporary_chats": false,
  "proxy": null,
  "rate_limit_max": 0,
  "log_requests": true
}
```

Every option is also an environment variable:

```bash
GEMINI_WEB2API_PORT=9000 \
GEMINI_WEB2API_API_KEYS="sk-one,sk-two" \
GEMINI_WEB2API_TEMPORARY_CHATS=true \
python -m gemini_web2api
```

```bash
python -m gemini_web2api --help          # all flags
```

Full reference: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**

> **Auth is off by default.** With `api_keys: []`, anyone who can reach the port
> can use the server. Fine on `127.0.0.1`; set keys before exposing it. The
> server warns at startup and `/health` reports it.

Set `temporary_chats: true` to keep these single-turn exchanges out of your
Google account's conversation history.

## Tool calling

```python
resp = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }],
)
print(resp.choices[0].message.tool_calls)
```

`tool_choice` supports `"none"`, `"auto"`, `"required"` and a named function.
Google-native requests use `toolConfig.functionCallingConfig` with `AUTO`/`ANY`/`NONE`.

Tool calling is prompt-based (Gemini Web exposes no function-calling contract on
this endpoint), so a request carrying `tools` cannot be truly streamed — the
server buffers, parses, then emits one chunk with `finish_reason: "tool_calls"`.

## Image input

```python
resp = client.chat.completions.create(
    model="gemini-3.6-flash",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "https://example.com/diagram.png"}},
    ]}],
)
```

`http(s)` URLs, base64 `data:` URLs and Google-native `inlineData` are all
accepted. MIME types are sniffed from the bytes rather than trusted from the
client. Remote URLs are refused when they resolve to loopback, link-local or
private addresses, and every redirect hop is re-validated.

## Authenticating to Google

Anonymous access works for every model, but `gemini-3.1-pro` will not route to a
Pro model without an entitled session. Payload slot 79 selects a *mode*
(`3 = PRO`), and Google honours it only for accounts that have it — silently
declining otherwise.

Real Pro routing needs a **Gemini Advanced** (paid) cookie. The bundled browser
extension exports everything at once:

```bash
python -m gemini_web2api --cookie-file ./gemini-auth.json
```

`gemini-auth.json` carries `cookie`, `sapisid`, `xsrf_token`, `gemini_bl` and
`auth_user`, and **all of them are applied automatically**. Values you set
yourself in `config.json` still win.

Cookie files may be a plain header string (any separator), a JSON object, a JSON
array, or a Netscape/curl cookie jar — including `#HttpOnly_` records.

Full guide: **[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md)** ·
Extension setup: **[gemini-cookie-sync-extension/SETUP.md](gemini-cookie-sync-extension/SETUP.md)**

## Proxy

If `gemini.google.com` is unreachable from your network:

```bash
python -m gemini_web2api --proxy http://127.0.0.1:7890
```

```json
{"proxy": "http://127.0.0.1:7890"}
```

```bash
export HTTPS_PROXY=http://127.0.0.1:7890   # auto-detected when proxy is unset
```

Works with Clash, V2Ray, Shadowsocks or any HTTP proxy.

## Monitoring

```bash
curl -s http://localhost:8081/health | python3 -m json.tool   # liveness, always public
curl -s http://localhost:8081/status | python3 -m json.tool   # metrics + redacted config
```

`/status` reports request counts, error counts by status code, average latency,
a latency histogram, per-model breakdowns and a `history` array of recent
requests. Or open the console at `/` — it shows the same thing, plus a chat
playground you can stream through and a filterable activity log.

`history` holds the last `history_max` requests (default 200, capped at 1000;
`0` disables it) with timestamp, `X-Request-Id`, method, path, status, the
**resolved** model, latency and client address. Prompts, response bodies and
credentials are never stored and query strings are stripped. Because entries
carry client addresses, `history` is served only from the auth-gated `/status`
— it is never embedded in the public `GET /` page.

Every response carries `X-Request-Id`, echoed in the server logs and in each
history entry, so a browser request can be traced to its log line.

## Documentation

| | |
|---|---|
| [docs/README.md](docs/README.md) | Documentation index |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Request flow, module map, the wire protocol |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every option, its default and how to set it |
| [docs/API.md](docs/API.md) | Every endpoint with request/response examples |
| [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) | API keys, cookies, XSRF, `auth_user` |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, Compose, systemd, reverse proxy, Workers |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model and what is protected by default |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptom → cause → fix |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Layout, tests, adding a model, release checklist |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | How to contribute, what CI enforces, project invariants |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history |
| [docs/AUDIT.md](docs/AUDIT.md) | The 1.2.0 defect audit, with reproductions |

## Limitations

Read these before filing a bug — most "broken" reports are one of them.

- **Pro is not really Pro without a paid session.** `gemini-3.1-pro` sets a UI
  mode preference, not a backend model switch. A free account authenticates and
  silently falls back to Flash.
- **Single-turn only.** Each request is an independent conversation with a fresh
  ID. Multi-turn context exists only because your client resends history and the
  server flattens it into one prompt — so long conversations cost more tokens and
  can eventually exceed what the upstream accepts.
- **Unofficial protocol.** This reverse-engineers a private web endpoint. Google
  can change the format, rotate the build tag, or tighten throttling at any time.
  The build tag self-heals; a format change needs a code update.
- **Rate limits.** Google throttles high-frequency requests, and anonymous
  traffic sooner. Retries are automatic and you can cap your own rate, but
  sustained heavy use may be blocked.
- **Token counts are estimates.** Gemini Web reports no usage, so counts are
  characters ÷ 4.
- **Sampling parameters are ignored.** `temperature`, `top_p`, `max_tokens` and
  `n` are accepted and dropped — the upstream exposes no such controls.
- **Web search is not a toggle.** Gemini decides for itself when to search.
- **Terms of Service.** Automated use of a consumer web endpoint may breach
  Google's ToS. Assess that risk yourself.

## How it works

The server sends requests to the same `StreamGenerate` endpoint the Gemini web
app uses, translating between OpenAI's JSON and Gemini's internal
protobuf-like framing. Model selection is payload field `[79]`, mapped from the
`MODE_CATEGORY` enum in Gemini's frontend JavaScript. Responses arrive as
newline-delimited JSON whose frames are **cumulative** — each repeats the answer
so far — so the complete text is in the last frame.

Details, including the payload slot map and the image upload flow:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Development

```bash
pip install -e ".[dev]"
python -m unittest discover -s tests -t .
ruff check gemini_web2api tests
```

499 tests, all offline — the Gemini wire protocol is faked at the frame level.
CI covers Python 3.8–3.13, a stdlib-only run with no third-party packages, a
build-and-install-the-wheel check, lint and a Docker build.

**[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**

## Requirements

- Python 3.8+
- `httpx` — optional but strongly recommended; without it `stream: true` returns
  one buffered chunk
- Network access to `gemini.google.com` (a proxy may be needed in some regions)

## Cloudflare Workers

[`cloudflare/`](cloudflare/README.MD) holds an independent serverless port with
multi-cookie rotation, browser-fingerprint rotation and request jitter.
Documentation is in Chinese.

It is **not** built from the Python code, and the two diverge in both
directions. The Worker adds multi-account cookie rotation, fingerprint rotation
and request jitter; it lacks image input (image parts are **silently
discarded**), `/v1/completions`, and the `/ready` and `/status` probes, and it
routes any unmatched `POST /v1/*` to chat completions rather than returning 501.
It also ships 8 models versus this server's 9 — it lacks
`gemini-3.1-pro-enhanced`, which needs payload slots the Worker does not
allocate. Read the
[divergence table](cloudflare/README.MD#-与-python-版本的差异) before choosing.

Version numbers are independent sequences: the Worker is
`1.6.0-cf-multifingerprint`, this package is `1.2.0`.

## Acknowledgments

- Inspired by the open-source API proxy ecosystem

## License

MIT — see [LICENSE](LICENSE).

---

## 致谢

本项目的开发 agent 能力由 [GenericAgent](https://github.com/lsdefine/GenericAgent) 提供。

### 🚩 友情链接

[![GenericAgent](https://img.shields.io/badge/Agent_Framework-GenericAgent-orange?style=for-the-badge&logo=github)](https://github.com/lsdefine/GenericAgent)
[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)
