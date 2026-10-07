# Project Audit — findings and remediation

This document records the state of the repository at the time of the audit, every
defect that was reproduced, and what was done about it. It is kept as a permanent
record so future maintainers can see *why* the code looks the way it does.

Every item below was **reproduced**, not guessed. The reproduction method is noted.

---

## 1. Blocking defects

### 1.1 The package could not be built or installed — `CRITICAL`

`pyproject.toml` declared `[project.scripts]` but no package discovery
configuration, and the repository root contains two importable top-level names
(`gemini_web2api/` and `cloudflare/`). Setuptools flat-layout auto-discovery
refused to guess:

```
$ python -m build --wheel --no-isolation
error: Multiple top-level packages discovered in a flat-layout:
       ['cloudflare', 'gemini_web2api'].
```

So `pip install .`, `pip install -e .`, and any publish to PyPI all failed.
The advertised console script `gemini-web2api` was therefore unreachable.

**Fix:** explicit `[tool.setuptools] packages = ["gemini_web2api"]` plus
`[tool.setuptools.package-data]` for the bundled web UI. Verified by building a
wheel and installing it into a clean venv.

### 1.2 Two divergent implementations of the same server — `CRITICAL`

The repository shipped **both** a 1108-line monolith (`gemini_web2api.py`) and a
modular package (`gemini_web2api/`), and they had drifted:

| Capability | monolith | package (shipped in Docker image) |
|---|---|---|
| `bl` auto-refresh on HTTP 405 | yes | **no** |
| `bl` refresh at startup | yes | **no** |
| oversized tool-schema trimming | yes | **no** |
| `gemini-3.1-pro-enhanced` (needs payload slot 80) | **no** (`IndexError`) | yes |
| `tool_choice` support | **no** | yes |
| Google-native `functionCall` parsing | **no** | yes |
| true streaming for Google-native API | **no** | yes |
| shared/pooled httpx client | **no** | yes |
| cookie mtime cache | **no** | yes |

Because `gemini_web2api/` (a package) takes import precedence over
`gemini_web2api.py` (a module), `import gemini_web2api` silently resolved to the
package:

```
$ python -c "import gemini_web2api; print(gemini_web2api.__file__)"
.../gemini_web2api/__init__.py
```

The Dockerfile copies **only** the package, so the deployed artifact was the
*less* capable of the two: when Google rotates the `bl` build tag the container
starts returning HTTP 405 and can never recover without a rebuild.

Two further consequences:

* `extract_response_text` disagreed between the files. The monolith returned the
  **last** non-empty segment; the package returned the **longest**. Identical
  wire input could produce different answers depending on which file ran.
* `_resolve_model` disagreed: the monolith returned HTTP 400 for an unknown
  model, the package silently substituted the default.

**Fix:** the package is now the single source of truth. Every monolith-only
capability was ported into it. `gemini_web2api.py` is now a thin compatibility
shim that delegates to `gemini_web2api.__main__:main`, so the documented
`python gemini_web2api.py` invocation keeps working while no logic is duplicated.

### 1.3 The "single file" claim was false — `HIGH`

The README advertised *"Pure Python, single file"*, but the monolith imports the
package for image handling:

```
$ cp gemini_web2api.py /tmp/ && cd /tmp && python -c "...upload_images(...)"
ModuleNotFoundError: No module named 'gemini_web2api.multimodal';
                     'gemini_web2api' is not a package
```

Copying the file out of the repository broke every image request with a
misleading error. **Fix:** documentation now describes the real layout, and the
shim reports a clear actionable error if the package is missing.

---

## 2. Correctness defects (all reproduced)

### 2.1 Cookie parsing lost `SAPISID` unless separators were exactly `"; "`

`load_cookie` split on the two-character string `"; "`. Any other layout lost
`SAPISID`, which silently dropped the `Authorization: SAPISIDHASH` header and
downgraded Pro requests to Flash **with no error**:

| cookie file content | `SAPISID` found |
|---|---|
| `SID=aaa; SAPISID=bbb` | yes |
| `SID=aaa;SAPISID=bbb` | **no** |
| `SID=aaa\nSAPISID=bbb` | **no** |

**Fix:** separator-agnostic parsing (`;`, `,`, newline), whitespace stripping,
`Cookie:` header prefix tolerance, and Netscape/curl cookie-jar support (the
README previously told users to convert that format by hand).

### 2.2 The bundled browser extension's export was mostly ignored — missing feature

`gemini-cookie-sync-extension` exports `gemini-auth.json` containing
`cookie`, `sapisid`, `auth_user`, `xsrf_token` **and** `gemini_bl`. The server
read only the first two. `SETUP.md` had to instruct users to hand-run a `jq`
pipeline to move the remaining three fields into `config.json`.

**Fix:** pointing `cookie_file` at `gemini-auth.json` now applies
`xsrf_token`, `gemini_bl` and `auth_user` automatically, with explicit
`config.json` values still winning. `SETUP.md` was reduced accordingly.

### 2.3 Streaming aborted mid-response on multi-part output

`generate_stream` treated any text that was not a prefix of what had already been
emitted as a fatal "stream content changed during retry". Gemini legitimately
emits multiple parts, so a normal reply could raise *after* deltas had already
been written to the client:

```
deltas yielded before failure: ['Hello', ' there']
RAISED: RuntimeError Gemini stream content changed during retry
```

The handler caught and logged this, so the client had already received
`HTTP 200` + `text/event-stream`, then the connection ended with **no
`[DONE]`** — an OpenAI client hangs waiting for the terminator.

**Fix:** part-aware accumulation, and every streaming path now emits a terminal
event (`[DONE]` for OpenAI, a `finishReason` chunk for Google) even on failure,
plus an SSE error event so the client can distinguish truncation from completion.

### 2.4 Fragile magic-number gates could discard content

`_extract_texts_from_line` returned nothing when `len(line) < 200` or
`len(inner_str) < 50`. Real payloads are ~950+ characters so this usually held,
but it is a silent data-loss landmine: a legitimately short frame is dropped and
the API answers `content: null`. That is precisely the symptom the README blamed
on Docker bridge networking.

**Fix:** the arbitrary length gates are gone; frames are selected structurally
(`"wrb.fr"` marker + successful parse + presence of text).

### 2.5 Query strings broke exact-match routing

```
GET /v1/models            -> 200
GET /v1/models?limit=100  -> 404
```

**Fix:** paths are matched on the query-stripped component.

### 2.6 `GET /v1beta/models/{model}` returned the whole list

```
GET /v1beta/models/gemini-3.6-flash  -> 200 {"models": [ ...all 9 models... ]}
```

Gemini CLI asks for one model and received a list. **Fix:** single-model
responses for both `/v1/models/{id}` (OpenAI shape) and
`/v1beta/models/{id}` (Google shape); both endpoints previously 404'd.

### 2.7 Keep-alive desynchronisation introduced by HTTP/1.1

`do_POST` answered `401` **before** reading the request body. Under the default
HTTP/1.0 + close that is harmless; after enabling HTTP/1.1 keep-alive the
unread body would corrupt the next pipelined request on that connection.

**Fix:** the body is always drained (within a size cap) before any early
response, and SSE responses set `Connection: close`.

### 2.8 Blocking network call on the startup path

`fetch_latest_bl()` ran synchronously before the banner with a 15 s timeout, so
an unreachable network delayed startup by 15 s. **Fix:** short timeout, failure
is non-fatal, and the resolved value is reported in the banner.

### 2.9 `server.shutdown()` instead of `server_close()`

On `KeyboardInterrupt` the code called `shutdown()` on a `serve_forever` loop
that had already exited, leaving the listening socket open. **Fix:** graceful
drain, then `server_close()`, plus `SIGTERM` handling so `docker stop` is clean.

---

## 3. Security defects

### 3.1 Server-side request forgery via `image_url` — `HIGH`

`fetch_image_bytes` fetched any user-supplied URL from the server. With the
default `host: 0.0.0.0`, any client could make the server request
`http://169.254.169.254/…` (cloud metadata), `http://127.0.0.1:6379`, or internal
RFC1918 hosts, and receive the bytes indirectly through the model. Redirects
bypassed any naive hostname check because `urlopen` follows them.

**Fix:** DNS-resolved allow/deny using `ipaddress`, re-validated on every
redirect hop, non-HTTP(S) schemes rejected, response size capped. Opt-out via
`allow_private_image_urls` for trusted LAN deployments.

### 3.2 Non-constant-time API key comparison

`key in keys` short-circuits on the first differing byte. **Fix:**
`hmac.compare_digest` over the whole key list.

### 3.3 The Docker image baked in a known API key

The Dockerfile copied `config.example.json` to `/app/config.json`, shipping
`api_keys: ["sk-gemini"]` inside every published image — authentication that
looks enabled but uses a publicly documented secret. **Fix:** no config is
baked; configuration comes from a mount or environment, and the image warns at
startup when auth is disabled.

### 3.4 Unbounded request bodies

No size limit existed on `Content-Length` or chunked bodies — a memory-exhaustion
vector. **Fix:** `max_request_bytes` (default 25 MiB), HTTP 413 on breach.

---

## 4. Missing production features (now implemented)

| Gap | Resolution |
|---|---|
| No health/readiness endpoint; Dockerfile had no `HEALTHCHECK` | `GET /health`, `/healthz`, `/ready`, `/live` + Docker `HEALTHCHECK` |
| No environment-variable configuration (12-factor) | every key readable from `GEMINI_WEB2API_*`, with type coercion |
| Container ran as root | non-root `USER` |
| No graceful shutdown on `SIGTERM` | signal handlers + drain |
| No rate limiting (the Cloudflare Worker had it, Python did not) | sliding-window limiter per API key / client IP, off by default |
| No request IDs; logs impossible to correlate | `X-Request-Id` echoed and logged |
| `X-Accel-Buffering: no` absent, so nginx buffered SSE | added |
| HTTP/1.0 — a new TCP connection per request | HTTP/1.1 + keep-alive, `TCP_NODELAY`, pooled httpx client |
| No CI for the test suite | `.github/workflows/ci.yml` |
| No `docs/` | this folder |
| No machine-readable status page / UI | dashboard at `GET /` |

---

## 5. Dead code removed

* `server._usage()` — defined, never called; usage dicts were inlined
  inconsistently in four places instead.
* `tools._compress_b64_if_needed()` and `MAX_IMAGE_B64_SIZE` — never called;
  vestigial from an older design that embedded images as base64 text. Images now
  travel through the upload path, so the Pillow dependency and the silent
  `b64[:50000]` truncation branch were both pointless.
* `gemini_web2api.py` — 1108 duplicated lines (see 1.2).

---

## 6. Deliberately *not* changed

* **`cloudflare/worker.js`** is a separate, self-contained edge deployment with
  its own Chinese documentation and its own release history. It duplicates the
  protocol layer by necessity (different runtime, no shared code possible). It
  was left intact and documented rather than deleted — removing it would break
  existing deployments. Its ideas (rate limiting, health endpoint) were ported
  *into* the Python server instead.
* **Token accounting** remains a `len(text) // 4` estimate. Gemini Web does not
  return token counts, so any number here is an approximation; it is now
  documented as such rather than silently presented as exact.
* **`gemini_bl` default** is left at the pinned value; the auto-refresh path
  makes it self-healing.
