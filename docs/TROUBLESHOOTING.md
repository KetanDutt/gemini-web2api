# Troubleshooting

Start here: **most problems announce themselves.**

```bash
curl -s http://localhost:8081/health | python3 -m json.tool
```

The `checks` object lists operational problems (missing cookie file, auth off,
httpx absent). Then reproduce with the server in debug mode:

```bash
python -m gemini_web2api --log-level debug
```

---

## Empty or null responses

**`"content": null` with status 200.**

Before 1.2.0 the most common cause was in the parser, not the network: response
frames shorter than an arbitrary 200-character threshold were discarded outright,
so short answers came back empty. Those gates are gone; frames are now selected
structurally. If you are on an older version, upgrade first.

Still happening?

1. Check `checks.warnings` in `/health`.
2. Try the Google-native endpoint, which returns a placeholder instead of empty:
   ```bash
   curl -s http://localhost:8081/v1beta/models/gemini-3.6-flash:generateContent \
     -H 'Content-Type: application/json' \
     -d '{"contents":[{"role":"user","parts":[{"text":"Say hello"}]}]}'
   ```
3. On Docker's bridge network, Google may reject the NAT address range. Switch to
   host networking:
   ```bash
   docker compose -f docker-compose.local.yml up -d
   ```
4. Try `temporary_chats: true` — a full conversation history can push the
   upstream into refusing.
5. Configure a cookie. Anonymous traffic is throttled much sooner.

---

## HTTP 405 from upstream

**Symptom:** `502` with `"code": "stale_build_tag"`.

The `bl` build tag identifies the Gemini frontend build your request was composed
for. Google rotates it, and a stale value is rejected with 405.

The server handles this automatically: it scrapes the current tag at startup and
again on a 405 (rate-limited to once per minute). Look for
`bl auto-updated: <old> -> <new>` in the logs.

If it is not recovering:

* **You pinned `gemini_bl`.** Pinning disables auto-refresh for that key — that
  is the point of pinning. Remove it from `config.json`, or update it manually.
  To find the current value: open [gemini.google.com/app](https://gemini.google.com/app),
  F12 → Network → search any request URL for `boq_assistant`.
* **`auto_update_bl` is off** (`--no-auto-bl` or the config key). Re-enable it.
* **Google is unreachable** from the server. Check the proxy settings and
  `Could not refresh bl from upstream` in the logs.

> In versions before 1.2.0 the Docker image could not recover from this at all:
> `bl` refresh existed only in the single-file implementation, and the image
> shipped the package. A 405 meant rebuilding the container.

---

## HTTP 429 from upstream

Google is throttling you.

1. **Configure a cookie** — by far the most effective step.
2. Enable rate limiting so you stay under the threshold:
   ```json
   {"rate_limit_max": 30, "rate_limit_window_sec": 60}
   ```
3. Use `temporary_chats: true`.
4. Spread load over several accounts (the Cloudflare Worker port supports
   cookie rotation natively).
5. Back off. Sustained heavy automated use can get an account flagged.

A 429 from *your* server (not upstream) says `rate_limit_exceeded` and includes
`Retry-After`.

---

## Pro does not behave like Pro

**Symptom:** `gemini-3.1-pro` gives Flash-quality answers.

Payload slot 79 selects a **mode** (`3 = PRO`), not a checkpoint. Google honours
that mode only for entitled accounts, and says nothing when it declines. So this
fails silently — there is no error to look for.

Work through it in order:

1. **Do you have Gemini Advanced?** A free account authenticates fine and still
   falls back to Flash. This is the usual answer.
2. **Is `SAPISID` present in the cookie?** Without it the
   `Authorization: SAPISIDHASH` header is never sent. Check the logs for
   `Cookie loaded but SAPISID is absent`.
3. **Is the cookie format being parsed?** Before 1.2.0 only `A=1; B=2` (with a
   space) worked. `A=1;B=2`, newline-separated, and Netscape cookie jars all
   silently lost `SAPISID`. Verify:
   ```bash
   curl -s http://localhost:8081/health | python3 -c \
     'import json,sys; print(json.load(sys.stdin)["cookie_configured"])'
   ```
   and look for the SAPISID warning at startup.
4. **Is `xsrf_token` set?** Authenticated requests generally need it. Using the
   extension export applies it automatically.
5. **Does `auth_user` match your URL?** If you browse to
   `gemini.google.com/u/1/app`, set `auth_user: "1"`.
6. **Is the cookie expired?** Re-export and try again.

Verify Pro is actually routing with a prompt that separates the models, then
compare against `gemini-3.6-flash`.

---

## Cookie problems

| Log message | Cause | Fix |
|---|---|---|
| `Cookie file not found: <path>` | Wrong path, or the Docker mount is missing | Check the path; `docker exec … ls /data` |
| `Cookie loaded but SAPISID is absent` | Incomplete cookie | Include `SAPISID` — it is required for the auth header |
| `Cookie file is not valid JSON` | Truncated or hand-edited export | Re-export |
| `Cookie load error: [Errno 13] Permission denied` | File permissions | `chmod 600`, and make sure the container user can read it |
| HTTP 400 mentioning `xsrf` | Missing or stale XSRF token | Refresh Gemini Web, re-export |

The cookie file is cached by `(path, mtime, size)` and re-read automatically when
it changes — replacing the file refreshes the session without a restart.

Supported formats are listed in
[AUTHENTICATION.md](AUTHENTICATION.md#accepted-cookie-file-formats). If your
export tool produces something else, open an issue with the shape (values
redacted).

---

## Streaming problems

**`stream: true` returns everything at once.**

`httpx` is not installed. Without it the server falls back to `urllib`, buffers
the whole reply, and emits one chunk:

```bash
pip install httpx
```

`/health` reports `"streaming": "buffered (install httpx for real streaming)"`
and the startup banner warns.

**No typewriter effect behind nginx.**

Buffering. The server sends `X-Accel-Buffering: no` on SSE responses, but set it
in the proxy too:

```nginx
proxy_buffering off;
proxy_cache off;
```

See [DEPLOYMENT.md](DEPLOYMENT.md#reverse-proxy).

**The client hangs at the end of a stream.**

That was a real bug: an exception mid-stream was logged and swallowed, so no
`data: [DONE]` was ever written and the client waited for a terminator. Fixed in
1.2.0 — `[DONE]` is now always sent, preceded by an `{"error": …}` frame when the
stream failed, and `finish_reason` is `"length"` rather than `"stop"` so a
truncation is distinguishable from a clean end.

If you are on an older version, upgrade.

**Streaming stops partway with `Gemini stream content changed during retry`.**

Also fixed in 1.2.0. The old code assumed every frame extends the previous text,
and raised when Gemini emitted a separate part — after the client had already
received `200 OK` and some deltas. Multi-part responses are now emitted normally.

**Streaming is disabled when `tools` are present.**

By design. Tool calls can only be recognised once the full reply is in hand, so
the server buffers and emits a single chunk with `finish_reason: "tool_calls"`.

---

## Model problems

**`400 Unknown model: X`.** Only with `strict_models: true`. Turn it off, or use
a name from `GET /v1/models`.

**Unknown model silently becomes something else.** That is the default, on
purpose: clients probe with `gpt-4` and `claude-3-5-sonnet` during setup and a
hard 400 breaks their connection test. It is logged at info level. Set
`strict_models: true` if you would rather have the error.

**`400 Invalid think level: 99`.** `@think=N` accepts 0–4 (0 deepest, 4
shallowest). Out-of-range values were previously accepted and produced undefined
behaviour.

**`400 Invalid think level: 'deep'`.** The suffix must be an integer.

---

## Startup problems

**`error: cannot bind 0.0.0.0:8081 — Address already in use`**

```bash
lsof -i :8081          # or: ss -ltnp | grep 8081
python -m gemini_web2api --port 8082
```

Ports below 1024 need root; use a higher port and forward from your proxy.

**Startup takes many seconds.**

`warm_up()` scrapes the current `bl` before serving. The timeout is short and a
failure is non-fatal, so this should cost a second or two. If the network is
blocked entirely, use `--no-auto-bl`.

**`config.json is not valid JSON`.** A warning, not a crash — defaults are used.
Validate:

```bash
python3 -m json.tool config.json
```

**`ignoring unknown option 'apikey'`.** Typo. Every valid key is listed in
[CONFIGURATION.md](CONFIGURATION.md).

---

## Image problems

**`400` with `code: image_rejected`.** The URL was refused by the SSRF policy or
could not be fetched. The server log has the reason. Loopback, link-local
(including `169.254.169.254`), private and reserved addresses are refused, as
are non-HTTP(S) schemes and URLs with embedded credentials. See
[SECURITY.md](SECURITY.md#ssrf-protection-on-image-fetching).

**`502` with `image upload failed`.** Google rejected the upload. Anonymous
uploads fail more often — configure a cookie. HTTP 401/403 from the upload
endpoint says so explicitly in the message.

**Wrong colours or a rejected file.** The MIME type is sniffed from the bytes,
not taken from the client's `mime_type` field, because clients get it wrong
often and Google rejects mismatches. If a format is not recognised the declared
type is used as a fallback.

**Large images.** Capped at `max_image_bytes` (20 MiB).

---

## Client-specific notes

**Cherry Studio / ChatBox / NextChat**

| Field | Value |
|---|---|
| Base URL | `http://localhost:8081/v1` |
| API key | one of your `api_keys`, or anything when auth is off |
| Model | `gemini-3.6-flash` |

If the model list does not populate, check that `GET /v1/models` works with your
key. Some clients append query parameters, which 404'd before 1.2.0.

**OpenAI Python SDK**

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8081/v1", api_key="sk-your-key")
print([m.id for m in client.models.list().data])
print(client.models.retrieve("gemini-3.6-flash").id)
```

`client.models.retrieve()` needs `GET /v1/models/{id}`, which 404'd before 1.2.0.

**Codex CLI**

Uses `/v1/responses`. Requires the exact streaming event sequence and
`sequence_number` fields; both are asserted in the test suite. If Codex reports a
protocol error, capture the SSE output with `curl -N` and compare against
[API.md](API.md#streaming-event-sequence).

**Gemini CLI**

```bash
export GEMINI_API_KEY=none
export GOOGLE_GEMINI_BASE_URL=http://localhost:8081
gemini
```

If it hangs on an empty reply, note that `generateContent` now returns a
placeholder string rather than an empty `parts[0].text`, which is what caused the
hang.

---

## Getting help

Include all of the following — without them a report is usually unactionable:

1. Version: `python -m gemini_web2api --version`
2. `curl -s http://localhost:8081/status` (secrets are redacted automatically)
3. Server logs at `--log-level debug` covering one failing request
4. The exact client request, with your key and cookie **removed**
5. What you expected versus what happened

Never paste a cookie, `xsrf_token`, or `gemini-auth.json` contents into an issue.

Check [existing issues](https://github.com/KetanDutt/gemini-web2api/issues) first.
