# Configuration

## Precedence

Settings are layered. Later sources win:

```
1. built-in defaults          (gemini_web2api/config.py)
2. JSON config file           (--config, $GEMINI_WEB2API_CONFIG, ./config.json,
                               ~/.config/gemini-web2api/config.json)
3. environment variables      (GEMINI_WEB2API_<KEY>)
4. command-line flags
```

There is one deliberate exception. A cookie file that is a `gemini-auth.json`
export carries `xsrf_token`, `gemini_bl` and `auth_user` alongside the cookie.
Those are adopted **only for keys you have not set yourself** — an explicit
value in `config.json` or the environment always wins. That is what lets the
browser extension refresh your session without you editing config by hand.

## Options

### Server

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `host` | `"0.0.0.0"` | `GEMINI_WEB2API_HOST` | `--host` | Bind address. Use `127.0.0.1` to keep the server local. |
| `port` | `8081` | `GEMINI_WEB2API_PORT` | `--port` | TCP port. Validated to 1–65535; an out-of-range value falls back to 8081 with a warning. |
| `cors_origin` | `"*"` | `GEMINI_WEB2API_CORS_ORIGIN` | — | Value of `Access-Control-Allow-Origin`. |
| `shutdown_timeout_sec` | `5` | `GEMINI_WEB2API_SHUTDOWN_TIMEOUT_SEC` | — | How long to wait for in-flight requests when stopping. |
| `max_request_bytes` | `26214400` (25 MiB) | `GEMINI_WEB2API_MAX_REQUEST_BYTES` | — | Inbound body cap. Larger requests get HTTP 413. |

### Authentication

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `api_keys` | `[]` | `GEMINI_WEB2API_API_KEYS` | `--api-key` (repeatable) | **Empty means authentication is disabled.** See [SECURITY.md](SECURITY.md). |

From the environment, `api_keys` accepts a comma list (`a,b,c`), a pipe list
(`a|b|c`, matching the Cloudflare Worker convention) or a JSON array
(`["a","b"]`). An empty string disables auth.

### Google session

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `cookie_file` | `null` | `GEMINI_WEB2API_COOKIE_FILE` | `--cookie-file` | Path to a cookie file, `gemini-auth.json`, or a Netscape cookie jar. |
| `auth_user` | `null` | `GEMINI_WEB2API_AUTH_USER` | — | Google account index for `/u/<n>/` URLs. |
| `xsrf_token` | `null` | `GEMINI_WEB2API_XSRF_TOKEN` | — | Sent as the `at` form field. Required for authenticated requests. |
| `gemini_bl` | pinned build tag | `GEMINI_WEB2API_GEMINI_BL` | `--gemini-bl` | Frontend build tag. Setting this **disables auto-refresh**. |
| `auto_update_bl` | `true` | `GEMINI_WEB2API_AUTO_UPDATE_BL` | `--no-auto-bl` | Scrape the current `bl` at startup and after an HTTP 405. |
| `temporary_chats` | `false` | `GEMINI_WEB2API_TEMPORARY_CHATS` | — | Do not write conversations to your Google account history. |

### Upstream behaviour

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `retry_attempts` | `3` | `GEMINI_WEB2API_RETRY_ATTEMPTS` | — | Total attempts per request. Clamped to ≥ 1. |
| `retry_delay_sec` | `2` | `GEMINI_WEB2API_RETRY_DELAY_SEC` | — | Delay between attempts. |
| `request_timeout_sec` | `180` | `GEMINI_WEB2API_REQUEST_TIMEOUT_SEC` | — | Upstream timeout. Thinking models can take a while. |
| `proxy` | `null` | `GEMINI_WEB2API_PROXY` | `--proxy` | HTTP proxy. Falls back to `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`. |
| `max_image_bytes` | `20971520` (20 MiB) | `GEMINI_WEB2API_MAX_IMAGE_BYTES` | — | Cap on a fetched remote image. |
| `block_private_image_urls` | `true` | `GEMINI_WEB2API_BLOCK_PRIVATE_IMAGE_URLS` | — | Refuse image URLs resolving to loopback/link-local/private/reserved addresses. |

### Models

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `default_model` | `"gemini-3.6-flash"` | `GEMINI_WEB2API_DEFAULT_MODEL` | `--default-model` | Used when a request omits `model`, and as the fallback for unknown names. Validated against the model table. |
| `strict_models` | `false` | `GEMINI_WEB2API_STRICT_MODELS` | — | Return HTTP 400 for unknown model names instead of substituting the default. |

Keep `strict_models` off unless you control every client. Tools routinely probe
with `gpt-4` or `claude-3-5-sonnet` during setup, and a hard 400 breaks their
"test connection" flow.

### Rate limiting

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `rate_limit_max` | `0` | `GEMINI_WEB2API_RATE_LIMIT_MAX` | `--rate-limit` | Requests per window per key (or per IP when unauthenticated). `0` disables. |
| `rate_limit_window_sec` | `60` | `GEMINI_WEB2API_RATE_LIMIT_WINDOW_SEC` | `--rate-limit-window` | Window length. |

### Logging

| Key | Default | Environment | CLI | Description |
|---|---|---|---|---|
| `log_requests` | `true` | `GEMINI_WEB2API_LOG_REQUESTS` | `--quiet` | Write request logs to stderr. |
| — | `info` | — | `--log-level` | `debug`, `info`, `warning` or `error`. |

`debug` adds per-frame HTTP logging and upload details. Start there when
reporting a bug.

## Configuration sources

### File

```bash
cp config.example.json config.json
```

`config.example.json` lists every key with its default. The file is searched for
in this order: `--config`, `$GEMINI_WEB2API_CONFIG`, `./config.json`, the
repository root, `~/.config/gemini-web2api/config.json`.

A missing file is not an error — the defaults are usable on their own. Invalid
JSON is reported as a warning and the defaults are used, rather than crashing at
startup. Unknown keys produce a warning naming the key, which catches typos like
`apikey` instead of `api_keys`.

### Environment

Every key is settable as `GEMINI_WEB2API_<UPPER_SNAKE_CASE>`:

```bash
export GEMINI_WEB2API_PORT=9000
export GEMINI_WEB2API_API_KEYS="sk-one,sk-two"
export GEMINI_WEB2API_TEMPORARY_CHATS=true
export HTTPS_PROXY=http://127.0.0.1:7890
python -m gemini_web2api
```

Types are coerced from the default's type: `"true"`/`"1"`/`"yes"`/`"on"` for
booleans, integers for numbers, and an empty string means "unset" for nullable
keys.

### Command line

```bash
python -m gemini_web2api --help
```

```
--port N                  --host ADDR             --config PATH
--cookie-file PATH        --proxy URL             --api-key KEY (repeatable)
--default-model NAME      --gemini-bl TAG         --log-level LEVEL
--rate-limit N            --rate-limit-window S   --quiet
--no-auto-bl              --version
```

## Inspecting the running configuration

```bash
curl -s http://localhost:8081/status | python3 -m json.tool
```

Returns the effective config with secrets redacted (`api_keys` becomes
`"2 configured"`, `xsrf_token` becomes `"set"`), plus live metrics. This
endpoint requires an API key when keys are configured.

```bash
curl -s http://localhost:8081/health | python3 -m json.tool
```

Returns liveness plus a `checks` object listing operational warnings — always
public, so container probes work without a secret.

The web dashboard at `http://localhost:8081/` shows the same thing in a browser.

## Warnings you may see at startup

| Warning | Meaning |
|---|---|
| `No API keys configured` | Anyone who can reach the port can use the server. |
| `httpx is not installed` | `stream: true` returns one buffered chunk instead of incremental deltas. |
| `cookie_file does not exist` | The configured path is wrong, or the mount is missing. |
| `Could not refresh bl from upstream` | Google was unreachable at startup; the pinned value is used. Harmless if the pinned value is current. |
| `Cookie loaded but SAPISID is absent` | The cookie is incomplete. Authenticated Pro routing will not work. |
| `Applied from gemini-auth.json: …` | Informational: the auth file supplied values you had not set. |
| `ignoring unknown option 'x'` | Typo in your config file. |

## Minimal examples

**Local, no auth, nothing persisted to your account:**

```json
{
  "host": "127.0.0.1",
  "temporary_chats": true
}
```

**Exposed on a LAN, authenticated, rate limited:**

```json
{
  "host": "0.0.0.0",
  "api_keys": ["sk-change-me"],
  "rate_limit_max": 60,
  "rate_limit_window_sec": 60,
  "log_requests": true
}
```

**Authenticated Pro through a proxy, session managed by the extension:**

```json
{
  "cookie_file": "/data/gemini-auth.json",
  "proxy": "http://127.0.0.1:7890",
  "temporary_chats": true
}
```

`auth_user`, `xsrf_token` and `gemini_bl` are picked up from
`gemini-auth.json` automatically.
