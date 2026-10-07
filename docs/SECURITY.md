# Security

## Threat model

Be clear about what this is: a proxy that translates OpenAI API calls into
requests to a Google web endpoint that has no published API. That shapes the
threat model in three ways.

**1. Your Google session is the crown jewel.** A cookie file grants full access
to the account it came from — Gmail, Drive, everything. It is worth more than
anything else in this deployment.

**2. The upstream is not a stable contract.** Google can change the wire format,
rotate the build tag, or tighten throttling at any time. Nothing here can
prevent that; the design goal is to degrade legibly rather than silently.

**3. It is a single-process Python server.** There is no sandboxing, no
multi-tenancy, no per-user isolation. One operator, one trust domain.

### In scope

Protecting the server from its clients: authentication, request limits, SSRF,
secret leakage in responses and logs.

### Out of scope

* Guaranteeing Google accepts the traffic. This is an unofficial client; a
  determined upstream can always refuse it.
* Anonymising you from Google. Requests carry your real session and IP.
* Protecting Google from you. Sustained high-volume automated use may breach
  Google's Terms of Service. That risk is yours to assess.
* Multi-tenant isolation. Do not expose one instance to mutually distrustful
  users; run one per trust domain.

---

## What is protected by default

### Authentication is honest about being off

`api_keys: []` means no authentication. Rather than pretending otherwise, the
server:

* prints a warning in the startup banner;
* reports it in `/health` under `checks.warnings`;
* shows it on the dashboard.

### Constant-time key comparison

```python
any(hmac.compare_digest(presented, str(key)) for key in keys)
```

A plain `presented in keys` short-circuits on the first differing byte, leaking
timing that correlates with how much of the key an attacker has guessed.

### SSRF protection on image fetching

`image_url` parts are fetched **server-side**. With the default `host: 0.0.0.0`,
that made the server a proxy into whatever network it sits on:

```json
{"image_url": {"url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"}}
{"image_url": {"url": "http://127.0.0.1:6379/"}}
{"image_url": {"url": "http://192.168.1.1/admin"}}
```

The bytes come back through the model's description, so the exfiltration channel
is the response itself.

Defence, all on by default via `block_private_image_urls`:

* Only `http` and `https` schemes. `file:`, `gopher:`, `ftp:` and bare paths are
  refused.
* The hostname is resolved and **every** returned address is checked against
  `ipaddress`: private, loopback, link-local, reserved, multicast and
  unspecified are all refused.
* IPv4-mapped IPv6 (`::ffff:127.0.0.1`) is unwrapped and judged on its inner
  address, which otherwise slips past a naive check.
* Hostnames that do not resolve are refused — allowing them would permit a
  DNS-rebinding bypass.
* **Redirects are re-validated on every hop.** A public URL that 302s to
  `169.254.169.254` is caught, which a check on the original target alone would
  miss.
* Credentials embedded in a URL (`https://user:pass@host/`) are refused.
* Downloads are capped at `max_image_bytes` (20 MiB), checked against
  `Content-Length` first and then against the actual bytes read.

A refusal is a **400** to the client with the reason logged — not a 502, since
it is the client's request that was unacceptable.

Set `block_private_image_urls: false` only for a fully trusted LAN where you
genuinely need to fetch internal images. `/health` warns when it is off while
bound to `0.0.0.0`.

### Request size limits

`max_request_bytes` (25 MiB) applies to both `Content-Length` and chunked
bodies, checked *while accumulating* so an attacker cannot stream unbounded data
before being cut off. Exceeding it returns 413 and closes the connection.

A malformed `Content-Length` returns 400 rather than an unhandled `ValueError`
becoming a 500.

### Rate limiting

Off by default (`rate_limit_max: 0`), because enabling it silently would break
existing deployments. When on, it is a fixed-window counter per key (or per IP),
which is O(1) in memory regardless of traffic — a sliding-window log would grow
without bound. Expired keys are swept every 30 s and whenever the table exceeds
10 000 entries, so a long-lived process does not accumulate state for clients
that never return.

### No secrets in responses or logs

* `/status` returns a **redacted** config: `api_keys` becomes `"2 configured"`,
  `xsrf_token` becomes `"set"`. No endpoint ever returns a cookie value.
* `/status` requires an API key when keys are configured.
* Error responses never echo the presented key.
* Logs record prompt length, not prompt content.
* The dashboard injects the same redacted state and is served with
  `Cache-Control: no-store`.
* `config.json`, `cookie.txt`, `cookie.json`, `gemini-auth.json` and `.env` are
  all gitignored, and a test asserts none of them are tracked.

### The Docker image ships no credentials

Earlier images copied `config.example.json` to `/app/config.json`, baking
`api_keys: ["sk-gemini"]` into every published image. That is worse than no
authentication: it looks protected while using a secret that is in the public
repository.

The image now contains no config. Configuration comes from a mount or the
environment, and the container warns at startup when auth is off. It also runs
as an unprivileged user (uid 10001) and works on a read-only filesystem.

---

## What you must decide

### Binding address

`host` defaults to `0.0.0.0`, which is right for a container and wrong for a
laptop. For local use:

```json
{"host": "127.0.0.1"}
```

If you must expose it, put a reverse proxy with TLS in front and bind the
server to loopback.

### CORS

`cors_origin` defaults to `*`. That is intentional — browser-based OpenAI clients
need it, and the endpoints carry no ambient credentials (no cookies are read from
the *client's* browser). Tighten it if you serve a specific frontend:

```json
{"cors_origin": "https://chat.example.com"}
```

### API key strength

Keys are compared, not hashed, so they live in memory and in your config file in
cleartext. Use long random values:

```bash
python3 -c "import secrets; print('sk-' + secrets.token_urlsafe(32))"
```

Issue one key per client so a leak can be revoked narrowly.

### Cookie storage

```bash
chmod 600 gemini-auth.json
```

Prefer a file mount over an environment variable: `docker inspect` and
`/proc/<pid>/environ` both expose environment contents to anyone with host
access.

### Logging

`log_requests: true` writes request lines to stderr, including client IPs. That
is personal data under GDPR. Turn it off (`--quiet`) or restrict log retention
if that applies to you.

---

## Denial of service

Honest assessment of what remains possible:

* **Thread exhaustion.** One thread per connection, unbounded. A slow-loris
  client can consume them. Mitigate at the proxy with connection and
  read timeouts.
* **Upstream amplification.** Each client request costs at least one Google
  round trip, and thinking models take tens of seconds. Rate limiting is the
  main defence; keep it on when exposed.
* **Memory.** Bodies are read fully into memory, bounded by
  `max_request_bytes`. 25 MiB × many concurrent requests adds up; lower it if
  you do not send large payloads.
* **No request queueing.** There is no backpressure between the HTTP layer and
  the upstream.

---

## Supply chain

* **Zero required dependencies.** The core runs on the Python standard library.
  `httpx` is an optional extra. Fewer packages means less transitive risk.
* **No CDN assets.** The dashboard inlines its CSS and JavaScript and makes no
  external requests. It works air-gapped, and there is no third-party script
  that could be swapped out.
* **Pinned base image.** `python:3.12-slim`.
* `pip install --no-cache-dir`, no build-time network access beyond PyPI.

---

## Reporting a vulnerability

Do not open a public issue for anything that could be exploited in a running
deployment. Use the private contact on the repository profile, or GitHub's
private vulnerability reporting if enabled.

Include the version, the affected configuration (redacted), and a reproduction.
Fixes for confirmed issues are prioritised over feature work.

---

## Summary of the 1.2.0 security changes

| Change | Before |
|---|---|
| SSRF guard on image fetching | any URL fetched, redirects followed blindly |
| Image download size cap | none |
| Request body size cap | none |
| API key comparison | `key in keys` (short-circuits) |
| 401 response | no `WWW-Authenticate`, non-OpenAI shape |
| `/status` exposure | did not exist; config had no redacted view |
| Docker image credentials | `sk-gemini` baked in |
| Container user | root |
| Rate limiting | absent (present only in the Cloudflare port) |
| Cookie file in logs | full value could appear in error messages |

See [AUDIT.md](AUDIT.md) for reproductions.
