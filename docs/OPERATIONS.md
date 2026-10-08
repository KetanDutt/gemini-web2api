# Operations

Day-2 running of a deployed gemini-web2api: probes, metrics, rate limiting,
logging, shutdown and scaling. [DEPLOYMENT.md](DEPLOYMENT.md) covers *installing*
(Docker, systemd, reverse proxies); this page covers *keeping it running*.

Everything here works the same whether the server runs on a laptop, in Docker
or under systemd — the endpoints are the interface.

---

## Probes

| Endpoint | Auth | Healthy | Unhealthy |
|---|---|---|---|
| `GET /health` (also `/healthz`, `/live`) | none | `200` + JSON | — |
| `GET /ready` | none | `200` when all startup checks pass | `503` with the failing checks |

`/health` is the cheap liveness probe — it answers from memory and never
touches the upstream. `/ready` is the readiness probe: it reports the checks
computed at startup (cookie present, build tag fetched, …), so a container
that cannot actually serve should not receive traffic.

```yaml
# Kubernetes example
livenessProbe:
  httpGet: { path: /health, port: 8081 }
readinessProbe:
  httpGet: { path: /ready, port: 8081 }
```

Docker's `HEALTHCHECK` already points at `/health`; see
[DEPLOYMENT.md](DEPLOYMENT.md).

## Metrics

`GET /metrics` (Prometheus text exposition, auth-gated when keys are set)
exports:

* **Counters** — `requests`, `errors`, `rate_limited`, `upstream_failures`,
  `images_uploaded`, `tool_calls_parsed`, per-model and per-status-code.
* **Latency histogram** — `latency_ms` in milliseconds, so p50/p95/p99 come
  from `histogram_quantile` on your side.

Alert on what a human would notice:

| Signal | Query sketch | Meaning |
|---|---|---|
| Error rate | `rate(errors[5m]) / rate(requests[5m])` | Something is broken |
| Upstream failures | `rate(upstream_failures[5m])` | Gemini is unhappy (405/429/5xx) |
| Rate-limited clients | `rate(rate_limited[5m])` | Your limit is biting real users |
| Latency | `histogram_quantile(0.95, rate(latency_ms_bucket[5m]))` | Upstream slowdown |

`GET /status` (auth-gated) is the human-readable version of the same data:
counters, latency averages, per-model stats, credential pool state (which
account is cooling down and why) and recent request history. Use it when
debugging; use `/metrics` for dashboards.

Every response carries an `X-Request-Id` (12 hex chars). Quote it when
reporting a problem — it ties the log line, the metrics label and the history
entry to one request.

## Rate limiting

Off by default (`rate_limit_max: 0`). Enable with e.g.
`GEMINI_WEB2API_RATE_LIMIT_MAX=120` and `GEMINI_WEB2API_RATE_LIMIT_WINDOW_SEC=60`
(see [CONFIGURATION.md](CONFIGURATION.md)). The limiter is a fixed-window
counter per API key (or per client IP when no keys are configured) and is O(1)
in memory; expired keys are swept every 30 s.

Clients see:

* `X-RateLimit-Limit-Requests` / `X-RateLimit-Remaining-Requests` /
  `X-RateLimit-Reset-Requests` on success **and** on the 429, so a
  well-behaved client backs off without parsing an error body.
* `Retry-After` (seconds) on both 429 paths — the local limiter's own wait, or
  the credential cooldown an upstream 429 is sitting out.

Tuning guidance: start from your real traffic (requests per minute per key),
set the limit a little above it, and keep the window short (60 s) so a burst
costs a client one minute, not one hour. The headers are absent when the
limiter is disabled, so clients never see a limit of 0.

**The limiter is per process.** Each replica counts independently — with two
replicas behind a load balancer, the effective limit is `2 × rate_limit_max`.
Halve the configured value per replica, or accept the multiplier.

## Response headers

Every response — JSON, HTML, SSE and errors — carries
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
`X-Request-Id`. The dashboard additionally carries `X-Frame-Options: DENY` and
a strict Content-Security-Policy (no external origins; inline script/styles,
a `data:` favicon and same-origin fetches only). The details and the reasoning
are in [API.md](API.md#response-headers) and
[SECURITY.md](SECURITY.md#response-security-headers); there is nothing to
configure — if a security scanner flags their absence, the deployment is not
running this version.

## Logging

The server logs to stderr: a startup summary (listen address, auth state,
cookie state, rate limit, build tag), warnings (missing cookie, `httpx` not
installed), per-request errors with their `X-Request-Id`, and credential
pool events (an account entering cooldown and why). It does **not** log
prompt content — history entries record path, status, model, latency and
client address only.

Request history is kept **in memory**, capped by `history_max` (default 200,
`0` disables), and is visible in the dashboard's Activity tab and in `/status`.
It is lost on restart by design: it exists to answer "what did my client just
send?", not to be an audit log. For a durable record, scrape `/metrics` or run
your own access log in front (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Credentials and rotation

The cookie pool is rebuilt whenever a configured credential file changes on
disk, so you can rotate accounts **without a restart**: export a fresh
`gemini-auth.json` (the [browser extension](../gemini-cookie-sync-extension/SETUP.md)
or a `cookie.txt`) over the old path, and the next request picks it up. An
account that hits a 429 or an auth error is put in cooldown
(`cookie_cooldown_sec`, default 60 s) and skipped while another account
serves; with a single account that means the request waits out the cooldown.
Watch the Accounts section of `/status` to see which account is resting and
why.

## Graceful shutdown

`SIGINT`/`SIGTERM` stop accepting new connections, let in-flight requests
finish (up to `shutdown_timeout_sec`, default 5 s), then close the listening
socket. A streaming client whose request is cut off sees the connection end;
the partial reply is not resumed. For zero-downtime deploys, drain with the
load balancer first (stop sending traffic, wait for `/ready` to disappear or
for connections to close), then signal the process.

## Scaling

One process is one unit of everything: the rate limiter, the metrics, the
request history and the credential cooldowns are all in-memory and
per-process. That makes a single replica simple and predictable, and it means:

* **Replicas are independent.** N replicas = N × the configured rate limit,
  N × the metrics (aggregate them in Prometheus), N × the history.
* **No sticky sessions are needed.** Nothing is stored per connection.
* **The upstream is the real bottleneck.** Gemini Web throttles per account;
  more replicas only help if you also rotate more accounts
  (`cookie_files`), because the pool — not the process — is the concurrency
  limit for a single account.

## Backup and restore

Two kinds of state, both on disk, both safe to copy while the server is
stopped (and the credential files even while it runs — see above):

* `config.json` — the server configuration.
* `cookie.txt` / `cookie.json` / `gemini-auth.json` — the Google session.
  Treat these as passwords: they are gitignored, never logged and never
  returned by any endpoint, but anyone who reads one can call Gemini as you.

There is nothing else to back up: metrics, history and cooldowns are ephemeral
by design.
