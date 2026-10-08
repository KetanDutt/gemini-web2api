# Deployment

## Choosing a deployment

| Option | Use when | Notes |
|---|---|---|
| `start.bat` (Windows) | Desktop use on Windows | One click: venv, dependencies, config, launch |
| Direct Python | Local use, development | No Docker, no build step |
| Docker | Always-on server | Non-root, health-checked, env-configured |
| Docker Compose | Single-host deployment | Two variants: bridge and host networking |
| systemd | Bare-metal Linux service | Restarts, journald logging |
| Cloudflare Workers | Serverless, no server | Separate implementation in [`cloudflare/`](../cloudflare/README.MD) |

---

## Windows: one-click launcher

Double-click [`start.bat`](../start.bat) in the repository root. It runs five
steps and then leaves the server in the foreground of that window:

| Step | What it does |
|---|---|
| 1 | Finds a Python 3.8+ interpreter (`py -3`, then `python`, then `python3`) |
| 2 | Creates `.venv`, or recreates it if an existing one is broken |
| 3 | Installs `requirements.txt` (`httpx`) |
| 4 | Writes `config.json` via [`scripts/win_setup.py`](../scripts/win_setup.py) |
| 5 | Starts the server and opens `http://localhost:PORT/` in your browser |

Press **Ctrl+C** in that window to stop. Arguments are passed straight through
to the server, so `start.bat --port 9000 --api-key sk-secret` works.

### Design decisions worth knowing

**The generated `config.json` binds `127.0.0.1`, not `0.0.0.0`, and has no API
keys.** A double-clicked launcher runs on somebody's desktop, and the obvious
default would publish an unauthenticated proxy to every machine on the LAN.
Localhost-only means only that computer can reach it, so auth-off is safe. To
serve other machines, edit `config.json`:

```json
{ "host": "0.0.0.0", "api_keys": ["sk-your-long-random-key"] }
```

Read [SECURITY.md](SECURITY.md) before doing so.

**An existing `config.json` is never modified.** Re-running the launcher reuses
your settings, including a custom port — which the launcher reads back so it
opens the right URL.

**Python is detected by executing it, not by searching PATH.** Windows ships a
Microsoft Store `python.exe` stub that opens the Store instead of running
Python; the launcher verifies each candidate with a real version check and skips
it if that fails.

**A failed `pip install` is a warning, not a fatal error.** `httpx` is optional:
without it the stdlib transport still streams incrementally and keeps its
connections alive, so `stream: true` behaves the same way; `httpx` only changes
which HTTP client does the talking. Offline users still get a full-speed server.

**The window stays open on failure.** A launcher that exits immediately hides
the reason it failed, so errors end in `pause`.

### Running it from a terminal instead

```bat
cd C:\path\to\gemini-web2api
start.bat
```

The launcher is a thin batch file; all JSON handling lives in
`scripts/win_setup.py`, which is plain Python and can be inspected or run
directly:

```bat
.venv\Scripts\python.exe scripts\win_setup.py
```

It prints `PORT=<n>` and `CREATED=<0|1>` on stdout for the batch file to parse,
and everything human-readable on stderr.

---

## Direct Python

```bash
git clone https://github.com/KetanDutt/gemini-web2api
cd gemini-web2api
pip install httpx          # optional but strongly recommended
python -m gemini_web2api
```

Or from PyPI-style installation:

```bash
pip install .                      # core, stdlib only
pip install ".[streaming]"         # + httpx
gemini-web2api --port 8081         # console script
```

`python gemini_web2api.py` also works — that file is a compatibility shim
delegating to the package.

Requirements: Python 3.8+. No compiled dependencies.

Verify:

```bash
curl -s http://localhost:8081/health
```

---

## Docker

### Build and run

```bash
docker build -t gemini-web2api .
docker run -d --name gemini-web2api -p 8081:8081 \
  -e GEMINI_WEB2API_API_KEYS="sk-your-key" \
  gemini-web2api
```

Or use the published image:

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  ghcr.io/ketandutt/gemini-web2api:latest
```

### Image properties

* `python:3.12-slim` base.
* Runs as an unprivileged user (`gemini`, uid 10001).
* **No configuration is baked in.** Earlier images copied `config.example.json`
  to `/app/config.json`, shipping `api_keys: ["sk-gemini"]` — a publicly
  documented secret that looked like authentication but was not.
* Built-in `HEALTHCHECK` hitting `/health` every 30 s.
* `PYTHONUNBUFFERED=1` so logs reach `docker logs` immediately.
* `PYTHONDONTWRITEBYTECODE=1` so the read-only filesystem is safe.

### Configuration

Environment variables are the natural fit for containers — every config key
maps to `GEMINI_WEB2API_<UPPER_SNAKE_CASE>`:

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  -e GEMINI_WEB2API_API_KEYS="sk-one,sk-two" \
  -e GEMINI_WEB2API_DEFAULT_MODEL="gemini-3.5-flash-thinking" \
  -e GEMINI_WEB2API_TEMPORARY_CHATS=true \
  -e GEMINI_WEB2API_RATE_LIMIT_MAX=60 \
  -e HTTPS_PROXY="http://proxy:7890" \
  gemini-web2api
```

To use a config file instead, mount it read-only:

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  -v ./config.json:/app/config.json:ro \
  gemini-web2api --config /app/config.json
```

> If you mount a path that does not exist, Docker creates a **directory** there
> and the container fails to read it. Create the file first.

### With an authenticated session

```bash
docker run -d --name gemini-web2api -p 8081:8081 \
  -v ./gemini-auth.json:/data/gemini-auth.json:ro \
  -e GEMINI_WEB2API_COOKIE_FILE=/data/gemini-auth.json \
  -e GEMINI_WEB2API_API_KEYS="sk-your-key" \
  gemini-web2api
```

`xsrf_token`, `gemini_bl` and `auth_user` are read from the file automatically.
Mounting read-only is enough — the server never writes to it, and re-exporting
the file on the host is picked up without a restart.

### Networking caveat

If responses come back empty or Google returns 403/429 on the default bridge
network, Google may be rejecting Docker's NAT address range. Switch to host
networking:

```bash
docker run -d --network host \
  -e GEMINI_WEB2API_PORT=8081 \
  gemini-web2api
```

Note that `ports:` is ignored in host mode.

### Useful commands

```bash
docker logs -f gemini-web2api                  # request logs
docker inspect --format='{{.State.Health.Status}}' gemini-web2api
docker exec gemini-web2api python -m gemini_web2api._healthcheck
docker stats gemini-web2api --no-stream
```

---

## Docker Compose

A canonical `docker-compose.yml` is included (it was previously missing, so the
documented `docker compose up -d` failed — only the non-default
`docker-compose.local.yml` existed).

**Bridge networking (default):**

```bash
GEMINI_WEB2API_API_KEYS="sk-your-key" docker compose up -d
```

**Host networking** — use this if Google rejects the Docker NAT range:

```bash
GEMINI_WEB2API_API_KEYS="sk-your-key" docker compose -f docker-compose.local.yml up -d
```

Both files:

* take configuration from the environment, so they work with no `config.json`;
* define a healthcheck;
* set `restart: unless-stopped`;
* cap log growth (`json-file`, 10 MB × 3);
* set `no-new-privileges:true`;
* the bridge variant additionally runs `read_only: true` with a `/tmp` tmpfs.

Overrides go in `docker-compose.override.yml` (automatically merged) or a
`.env` file:

```bash
# .env
GEMINI_WEB2API_PORT=9000
GEMINI_WEB2API_API_KEYS=sk-your-key
GEMINI_WEB2API_DEFAULT_MODEL=gemini-3.5-flash-thinking
```

```bash
docker compose up -d
docker compose logs -f
docker compose down
```

To mount a session file, uncomment the `volumes:` block in the compose file.

---

## systemd

`/etc/systemd/system/gemini-web2api.service`:

```ini
[Unit]
Description=gemini-web2api (Gemini Web to OpenAI API)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gemini
Group=gemini
WorkingDirectory=/opt/gemini-web2api
EnvironmentFile=-/etc/gemini-web2api.env
ExecStart=/usr/bin/python3 -m gemini_web2api --config /etc/gemini-web2api/config.json
Restart=always
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=/var/log/gemini-web2api

[Install]
WantedBy=multi-user.target
```

`/etc/gemini-web2api.env`:

```
GEMINI_WEB2API_HOST=127.0.0.1
GEMINI_WEB2API_PORT=8081
GEMINI_WEB2API_API_KEYS=sk-your-key
GEMINI_WEB2API_COOKIE_FILE=/etc/gemini-web2api/gemini-auth.json
```

```bash
sudo chmod 600 /etc/gemini-web2api.env /etc/gemini-web2api/gemini-auth.json
sudo systemctl daemon-reload
sudo systemctl enable --now gemini-web2api
systemctl status gemini-web2api
journalctl -u gemini-web2api -f
```

`SIGTERM` is handled: the server stops accepting, waits up to
`shutdown_timeout_sec` for in-flight requests, then closes. So
`systemctl stop` and `docker stop` do not truncate active streams.

---

## Reverse proxy

### nginx

```nginx
upstream gemini_web2api {
    server 127.0.0.1:8081;
    keepalive 32;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    # Long thinking-model responses need generous timeouts.
    proxy_read_timeout    300s;
    proxy_send_timeout    300s;
    proxy_connect_timeout  10s;

    location / {
        proxy_pass http://gemini_web2api;
        proxy_http_version 1.1;
        proxy_set_header Connection        "";
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for SSE. The server also sends X-Accel-Buffering: no.
        proxy_buffering    off;
        proxy_cache        off;
        proxy_request_buffering off;
        chunked_transfer_encoding on;
    }
}
```

Without `proxy_buffering off`, nginx collects the whole response before
forwarding and the typewriter effect disappears. The server sends
`X-Accel-Buffering: no` on SSE responses, which nginx honours, but setting it
explicitly in the proxy config is more robust.

Keep the client IP accurate (`X-Real-IP` / `X-Forwarded-For`) if you rely on
IP-based rate limiting; the server sees the proxy's address otherwise, so prefer
key-based limiting behind a proxy.

### Caddy

```
api.example.com {
    reverse_proxy 127.0.0.1:8081 {
        flush_interval -1
    }
}
```

`flush_interval -1` disables buffering for streaming responses.

### TLS

Terminate TLS at the proxy and bind the server to `127.0.0.1`. The server has no
TLS support of its own — that is deliberate, since every reverse proxy already
does it better.

---

## Cloudflare Workers

[`cloudflare/worker.js`](../cloudflare/worker.js) is an independent port for
serverless deployment, with its own documentation in
[`cloudflare/README.MD`](../cloudflare/README.MD) (Chinese).

It is not built from the Python code and cannot be — different runtime, no
shared modules. It adds things the Python server treats differently:

* browser-fingerprint rotation (User-Agent, `Accept-Language`, `Sec-Ch-Ua`)
* multi-cookie rotation across accounts
* randomised pre-request jitter
* configuration from Worker environment variables

The rate-limiting and health-endpoint ideas from that port were brought back
into the Python server in 1.2.0.

Deploy it by pasting `worker.js` into a new Worker. Configure `COOKIE_STRING`,
`SAPISID`, `API_KEYS` and `GEMINI_BL` as Worker secrets/variables.

---

## Production checklist

Before exposing this beyond localhost:

- [ ] `api_keys` is set to values only you know — **not** `sk-gemini`.
- [ ] Bound to `127.0.0.1` behind a proxy, or firewalled.
- [ ] TLS terminated at the proxy.
- [ ] `rate_limit_max` set to something you would actually tolerate.
- [ ] `temporary_chats: true` if you do not want the traffic in your Google history.
- [ ] Cookie file is `chmod 600`, mounted read-only, and not in the image or repo.
- [ ] `block_private_image_urls` left at `true`.
- [ ] `curl /health` returns 200 and `checks.fatal` is empty.
- [ ] A healthcheck is wired into your orchestrator.
- [ ] Log rotation configured (Docker `max-size`, or journald).
- [ ] You have read [SECURITY.md](SECURITY.md) and accept the threat model.

Full details in [SECURITY.md](SECURITY.md).
