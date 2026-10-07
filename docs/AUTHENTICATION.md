# Authentication

There are two independent directions of authentication, and conflating them
causes most of the confusion around this project:

1. **Inbound** — clients proving themselves to *your server* (`api_keys`).
2. **Outbound** — your server proving itself to *Google* (cookies, XSRF, `auth_user`).

---

## Inbound: protecting your server

### Off by default

With `api_keys: []` (the default) **anyone who can reach the port can use the
server**. That is convenient on `127.0.0.1` and dangerous anywhere else. The
server prints a warning at startup and `/health` reports it under `checks.warnings`.

### Enabling keys

```json
{"api_keys": ["sk-your-first-key", "sk-your-second-key"]}
```

```bash
python -m gemini_web2api --api-key sk-one --api-key sk-two
GEMINI_WEB2API_API_KEYS="sk-one,sk-two" python -m gemini_web2api
```

Multiple keys let you issue one per client or per person, and revoke one without
touching the others.

### Accepted credential locations

| Form | Example | Used by |
|---|---|---|
| `Authorization: Bearer <key>` | `Bearer sk-one` | OpenAI SDKs |
| `x-api-key: <key>` | `sk-one` | OpenAI-compatible tools |
| `x-goog-api-key: <key>` | `sk-one` | Gemini CLI |
| `?key=<key>` query parameter | `/v1beta/models?key=sk-one` | Google-native clients |

The `Bearer` scheme is matched case-insensitively.

Comparisons use `hmac.compare_digest` rather than `in`, so the check does not
short-circuit on the first differing byte.

### What is protected

Everything under `/v1` and `/v1beta`, plus `/status` when keys are configured.

Deliberately **not** protected: `/health`, `/healthz`, `/live`, `/ready`, `/`,
`/favicon.ico`. Container orchestrators must be able to probe liveness without
holding a secret. None of these expose credentials — `/status` (which does) is
gated, and the config it returns is redacted.

### Failure response

```json
{"error": {"message": "invalid api key", "type": "authentication_error",
           "code": "invalid_api_key", "param": null}}
```

with `WWW-Authenticate: Bearer realm="gemini-web2api"` and status 401. The
supplied key is never echoed back.

Even a rejected request has its body fully read first. Under HTTP/1.1
keep-alive, answering 401 before draining would leave unread bytes on the
connection and corrupt the next request.

### Rate limiting

Independent of authentication, and off by default:

```json
{"rate_limit_max": 60, "rate_limit_window_sec": 60}
```

Requests are counted per API key when one is presented, otherwise per client IP.
Exceeding the limit returns 429 with `Retry-After`:

```json
{"error": {"message": "rate limit exceeded (60 requests / 60s)",
           "type": "rate_limit_error", "code": "rate_limit_exceeded"}}
```

Counters are per-process and in memory. Behind a reverse proxy, the client IP is
the proxy's unless you set `X-Forwarded-For` handling at the proxy — prefer
key-based limiting in that setup.

---

## Outbound: authenticating to Google

### Anonymous by default

The StreamGenerate endpoint accepts unauthenticated requests, which is why the
server works with no setup. The cost:

* `gemini-3.1-pro` and `gemini-3.1-pro-enhanced` do not route to a Pro model.
  Payload slot 79 selects a **mode** (`3 = PRO`), not a checkpoint, and Google
  honours that mode only for entitled accounts.
* Anonymous traffic is throttled sooner.
* Image uploads are more likely to be refused.

### With a cookie

A signed-in session changes all three. For Pro routing specifically you need a
**Gemini Advanced** (paid) account: a free account authenticates fine and still
falls back to Flash, silently. There is no way to detect this from the response,
so verify with a prompt only Pro answers well.

```bash
python -m gemini_web2api --cookie-file ./gemini-auth.json
```

### Getting the session — the easy way

Use the bundled browser extension ([setup guide](../gemini-cookie-sync-extension/SETUP.md)).
It exports `gemini-auth.json`:

```json
{
  "cookie": "SID=…; HSID=…; SSID=…; APISID=…; SAPISID=…; __Secure-1PSID=…",
  "sapisid": "…",
  "auth_user": "0",
  "xsrf_token": "AOOh0P…",
  "gemini_bl": "boq_assistant-bard-web-server_20260901.01_p0"
}
```

Point `cookie_file` at it and **everything is applied automatically**. Before
1.2.0 only `cookie` and `sapisid` were read, so `xsrf_token`, `gemini_bl` and
`auth_user` had to be moved into `config.json` by hand with a `jq` pipeline.
That is no longer necessary.

Values you set explicitly in `config.json` or the environment still win — the
file only fills in what you have not chosen yourself.

### Getting the session — manually

1. Sign in at [gemini.google.com](https://gemini.google.com).
2. DevTools (F12) → Application → Cookies → `https://gemini.google.com`.
3. Copy `SID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `__Secure-1PSID`.
4. Write them to a file:

```
SID=…; HSID=…; SSID=…; APISID=…; SAPISID=…; __Secure-1PSID=…
```

If your signed-in URL contains an account index (`https://gemini.google.com/u/1/app`),
set `auth_user` to `1`. The XSRF token is exposed in the rendered page source as
`SNlM0e`; set it as `xsrf_token`.

### Accepted cookie file formats

Any of these work — the parser is tolerant, because every export tool produces
something slightly different:

| Format | Notes |
|---|---|
| `A=1; B=2` | Standard header form |
| `A=1;B=2` | No space after the semicolon |
| `A=1\nB=2` | One pair per line |
| `Cookie: A=1; B=2` | With a leading header name |
| `{"cookie": "…", "sapisid": "…"}` | JSON object |
| `{"cookie": …, "xsrf_token": …, "gemini_bl": …, "auth_user": …}` | Extension export |
| `[{"name": "SID", "value": "…"}, …]` | JSON array from an export extension |
| Netscape / curl cookie jar | Tab-separated, including `#HttpOnly_` records |

Only the first of these worked before 1.2.0. Splitting on the literal `"; "`
meant `A=1;B=2` lost `SAPISID`, which dropped the `Authorization` header and
silently downgraded Pro to Flash with no error anywhere.

`#HttpOnly_` records matter: Google's session cookies are HttpOnly, so a parser
that skips every `#` line discards exactly the cookies that authenticate you.

Values containing `=` (base64 padding) are preserved — only the first `=` in a
pair is treated as a separator.

### Hot reload

The cookie file is cached by `(path, mtime, size)` and re-read when it changes,
so replacing the file refreshes the session without a restart. One `stat()` per
request on a warm process.

### How the credential is used

With a `SAPISID` present, each request carries:

```
Cookie: <the full cookie string>
Authorization: SAPISIDHASH <timestamp>_<sha1(timestamp + " " + sapisid + " " + origin)>
```

This is the same scheme every `*.google.com` web app uses. The hash is
recomputed per request because the timestamp is part of it.

With `auth_user` set, requests also carry `X-Goog-AuthUser: <n>` and use the
`/u/<n>/` path prefix in both the URL and the `Referer`.

With `xsrf_token` set, it is sent as the `at` form field.

### Multiple accounts and rate-limit failover

Google rate limits each account on its own, so with one cookie a 429 is the whole
deployment failing until the limit expires. Name extra accounts and the proxy will
rotate:

```json
{
  "cookie_file": "/data/primary.json",
  "cookie_files": ["/data/second.json", "/data/third.json"]
}
```

The primary is whatever `cookie_file` names; `cookie_files` adds to it, so a
single-cookie configuration behaves exactly as before. Each file is read through
the same parser as the primary, including hot reload: re-exporting one account
refreshes that account alone, with no restart.

| Upstream status | What happens |
| --- | --- |
| `429 Too Many Requests` | That account rests for `cookie_cooldown_sec` (default 60s) and the request is retried immediately on the next account. No retry delay — the point is to move now. |
| `401` / `403` | The cookie is stale rather than throttled, so it rests for 15 minutes. Re-export it; it is picked up automatically. |
| Anything else (`5xx`, timeouts, `405`) | Not an account problem, so nothing rotates. A `5xx` would otherwise burn every account in turn during one outage and then blame the last one. |
| Every account resting | The request fails immediately with `429` and a message saying so, rather than spending a call on an unauthenticated attempt to earn a less informative error. |

**Rotation engages only above one credential.** With a single cookie the pool
always hands back that cookie and never rests it, so retry behaviour is
byte-for-byte what it was before this feature existed — that is a test-enforced
guarantee, not a hope.

Each account is addressed with its **own** account index when its auth file
supplies `auth_user`. Sending account A's `/u/1` with account B's cookies would
authenticate as the wrong account, which is the subtle failure this exists to
avoid. `gemini_bl` stays global: it identifies Google's frontend build, not an
account.

Per-account health appears in `/status` under `credentials` — source, whether a
SAPISID is present, cooldown remaining, last error and use count. Never the
cookie, and never a path to it beyond the file the operator configured.

### Temporary chats

```json
{"temporary_chats": true}
```

Sets payload slots `41 = [1]` and `45 = 1`, matching Gemini Web's
temporary-chat requests. Nothing is written to your account's conversation
history. Recommended when a proxy will be issuing many throwaway requests.

### Cookie expiry

Google session cookies last weeks to months, but can be invalidated early by a
password change, a sign-out everywhere, or unusual activity. Symptoms of an
expired cookie are in [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Re-export from
the extension; no other change is needed.

---

## Handling secrets

`gemini-auth.json` and `cookie.txt` **are your Google account**. Anyone holding
them can act as you.

* Both are in `.gitignore` (`config.json`, `cookie.txt`, `cookie.json`,
  `gemini-auth.json`, `.env`). Do not force-add them.
* `chmod 600` the file.
* Never put a cookie in a URL, a log line, an issue report or a container
  environment variable that shows up in `docker inspect`.
* `/status` redacts secrets: `api_keys` becomes `"2 configured"`, `xsrf_token`
  becomes `"set"`. No endpoint returns a cookie. The `credentials` section
  reports each account's health without serialising a cookie at all — the
  pool's snapshot has no field a cookie could travel in.
* In Docker, mount the file read-only rather than baking it into an image:

  ```bash
  docker run -v ./gemini-auth.json:/data/gemini-auth.json:ro \
    -e GEMINI_WEB2API_COOKIE_FILE=/data/gemini-auth.json …
  ```

See [SECURITY.md](SECURITY.md) for the wider threat model.
