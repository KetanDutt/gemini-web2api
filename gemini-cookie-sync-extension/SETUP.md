# Gemini Cookie Sync Setup

Extract a fresh Gemini session from your browser and apply it to
`gemini-web2api`.

## What this extension exports

It reads the current signed-in Gemini session and exports:

| Field | Config key it fills | Why it matters |
|---|---|---|
| Google session cookies | `cookie` | Authentication itself |
| `SAPISID` | `sapisid` | Builds the `SAPISIDHASH` authorization header |
| `SNlM0e` | `xsrf_token` | The `at` form field on every request |
| `cfb2h` | `gemini_bl` | The frontend build tag |
| account index | `auth_user` | The `/u/<n>/` path prefix |

It saves them locally as `gemini-auth.json`.

## Install and export

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the `gemini-cookie-sync-extension` folder
5. Open <https://gemini.google.com/app>
6. Sign in and refresh the page
7. Open the extension and click **Inspect session**
8. Confirm the session looks ready
9. Click **Export gemini-auth.json**

Expected ready state:

```text
XSRF / SNlM0e: present
gemini_bl / cfb2h: present
Session and XSRF are ready for export.
```

## Apply it

Move the exported file into the project:

```bash
cd /path/to/gemini-web2api
cp ~/Downloads/gemini-auth.json ./gemini-auth.json
chmod 600 gemini-auth.json
```

Then point the server at it — that is the whole setup:

```bash
python -m gemini_web2api --cookie-file "$(pwd)/gemini-auth.json"
```

or in `config.json`:

```json
{"cookie_file": "/path/to/gemini-web2api/gemini-auth.json"}
```

or in the environment:

```bash
export GEMINI_WEB2API_COOKIE_FILE=/path/to/gemini-auth.json
```

### No further steps

`cookie`, `sapisid`, `auth_user`, `xsrf_token` and `gemini_bl` are all read from
the file automatically.

> This guide used to prescribe a `jq` pipeline that copied `auth_user`,
> `xsrf_token` and `gemini_bl` out of the export and into `config.json` by hand.
> Since 1.2.0 that is unnecessary — the server adopts those fields when it loads
> the cookie file. Anything you set explicitly in `config.json` or the
> environment still wins, so the pipeline still works if you prefer it.

### In Docker

Mount it read-only rather than baking it into an image:

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  -v ./gemini-auth.json:/data/gemini-auth.json:ro \
  -e GEMINI_WEB2API_COOKIE_FILE=/data/gemini-auth.json \
  -e GEMINI_WEB2API_API_KEYS="sk-your-key" \
  gemini-web2api
```

With Compose, uncomment the `volumes:` block in `docker-compose.yml`.

### Rotating the session

The file is cached by `(path, mtime, size)` and re-read when it changes, so
re-exporting and overwriting `gemini-auth.json` refreshes the session **without a
restart**.

## Verify

```bash
curl -s http://localhost:8081/health | python3 -m json.tool
```

`cookie_configured` should be `true`. Then confirm the session is actually being
used:

```bash
curl -sS http://localhost:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-key" \
  -d '{"model":"gemini-3.1-pro","messages":[{"role":"user","content":"Reply exactly with: authenticated-ok"}]}' \
  | python3 -m json.tool
```

Look for a startup log line like:

```
INFO  Cookie loaded: xsrf_token from auth file, bl from auth file
```

That confirms the XSRF token and build tag were adopted.

### Did Pro actually route to Pro?

Authenticating is not the same as being entitled. `gemini-3.1-pro` sets a UI mode
preference that Google honours only for Gemini Advanced accounts, and it declines
silently otherwise. Test with a prompt that separates Pro from Flash rather than
trusting the model name. See
[docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md#pro-does-not-behave-like-pro).

## If it does not work

| Symptom | Cause |
|---|---|
| `Cookie file not found` in the log | Wrong path, or the Docker mount is missing |
| `Cookie loaded but SAPISID is absent` | Incomplete export — re-export after a fresh page load |
| `cookie_configured: false` in `/health` | `cookie_file` was never set |
| HTTP 400 mentioning `xsrf` | Token stale — refresh Gemini Web and re-export |
| Still behaves like Flash | No Gemini Advanced entitlement, or expired session |

More in [docs/AUTHENTICATION.md](../docs/AUTHENTICATION.md) and
[docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md).

## Keep it secret

`gemini-auth.json` is a real Google session — it grants access to the whole
account, not just Gemini.

- `chmod 600` it
- It is gitignored; never force-add it
- Never paste it into an issue, a log, or a chat message
- Prefer a read-only file mount over an environment variable: `docker inspect`
  and `/proc/<pid>/environ` expose environment contents to anyone with host access
