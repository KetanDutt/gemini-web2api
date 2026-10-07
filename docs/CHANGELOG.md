# Changelog

All notable changes to this project. Format follows
[Keep a Changelog](https://keepachangelog.com/); the project uses semantic
versioning.

The 1.2.0 entries reference [AUDIT.md](AUDIT.md), which records how each defect
was reproduced.

---

## [1.2.0]

### Fixed

**Blocking**

- The package could not be built or installed at all. `pyproject.toml` relied on
  setuptools flat-layout auto-discovery, which refuses to choose between
  `cloudflare/` and `gemini_web2api/`. `pip install .` and any PyPI publish
  failed, and the declared `gemini-web2api` console script was unreachable.
  Package discovery is now explicit.

- The repository shipped two divergent implementations of the same server: a
  1108-line `gemini_web2api.py` and the `gemini_web2api/` package. They
  disagreed on build-tag refresh, model support, `tool_choice`, streaming
  semantics, and response parsing. Because a package outranks a module of the
  same name, `import gemini_web2api` silently resolved to the package — which
  is also what the Dockerfile copied, so the deployed artifact was the *less*
  capable copy and could never recover from an HTTP 405 without a rebuild.
  The package is now the single source of truth and every monolith-only
  capability was ported into it.

- `extract_response_text` returned the **longest** text segment instead of the
  **last**. Since Gemini frames are cumulative these usually agree, but a
  response containing an earlier long segment returned that segment instead of
  the answer.

- Response frames shorter than arbitrary thresholds (`len(line) < 200`,
  `len(inner_str) < 50`) were discarded outright, silently producing
  `content: null`. Frames are now selected structurally.

- Streaming aborted mid-response with
  `RuntimeError: Gemini stream content changed during retry` whenever Gemini
  emitted a second, separate part. Because deltas had already been written, the
  client was left with `200 OK`, a partial body and no `data: [DONE]`, so
  OpenAI clients hung waiting for a terminator. Multi-part frames are now
  emitted normally, and every streaming path always sends its terminator.

- Cookie parsing split on the literal `"; "`, so `A=1;B=2`, newline-separated
  cookies and cookie jars all lost `SAPISID`. That dropped the
  `Authorization: SAPISIDHASH` header and silently downgraded Pro to Flash with
  no error anywhere. Parsing is now separator-agnostic.

- Query strings broke routing: `GET /v1/models?limit=100` returned 404 because
  paths were compared exactly.

- `GET /v1beta/models/{model}` returned the entire model list instead of one
  model, because it fell through to the list handler.

- `gemini-3.1-pro-enhanced` raised `IndexError`: it writes payload slot 80 and
  the single-file implementation sized the payload at 80 slots.

- Unfenced `function_call` output with nested arguments was truncated at the
  first `}` by a non-greedy regex, so the JSON failed to parse and the call was
  dropped. Extraction is now brace-balancing and string-aware.

- `do_POST` answered `401` before reading the request body. Harmless under
  HTTP/1.0, but it desynchronises a keep-alive connection once HTTP/1.1 is
  enabled. The body is now always drained first.

- On `KeyboardInterrupt` the server called `shutdown()` on an already-exited
  `serve_forever` loop, leaving the listening socket open. Now a graceful drain
  followed by `server_close()`.

- A malformed `Content-Length` raised `ValueError` and produced a 500 instead of
  a 400.

- Streaming requests were never counted. `_start_sse()` returned before the
  response was recorded, so every `stream: true` completion was missing from
  `requests_served`, `status_codes` and the per-model latency table — the metrics
  under-reported exactly the traffic that matters most. `_record()` is now called
  when the stream opens, and is idempotent so a later error response on the same
  request cannot double-count.

- Request history measured time-to-first-byte instead of request duration for
  streaming replies. The entry was written when the SSE headers went out, so a
  reply that took four seconds to generate was logged as `0.6ms`. The Activity
  tab's latency column was therefore not merely imprecise but inverted in
  meaning — the slowest requests looked the fastest. Counters are still recorded
  when the stream opens (the `200` header is already on the wire and must count
  even if the client disconnects), but the history entry is now written from a
  `finally` in each verb handler, so it covers the whole exchange and is still
  logged when a stream fails part-way.

- `HEAD` and `OPTIONS` responses bypassed `send_json`, so they were never counted
  in `requests_served` or `status_codes` at all.

- The Docker image could not be built. The Dockerfile's `COPY README.md
  LICENSE ./` and the `.dockerignore` entries `*.md` and `LICENSE` were added in
  the same pass and contradicted each other, so neither file reached the build
  context and buildx failed with `"/README.md": not found`. Only CI could catch
  it: there is no container runtime in the development environment, and the PR
  said so. The image now copies `LICENSE` alone — MIT requires the notice to
  accompany redistributed copies, and a published image is one — with a
  `!LICENSE` negation placed *after* the pattern it undoes, since in
  `.dockerignore` the last match wins. `README.md` is genuinely unnecessary:
  nothing reads it at runtime and the image never runs `pip install .`, which is
  the only step that would need pyproject's `readme`.

- CI itself could have gone green on a failing suite. While adding a skip audit,
  the test step was rewritten as `python -m unittest ... | tee /tmp/out.txt`.
  Actions' default shell on Linux is `bash -e {0}` — **without** `pipefail` — so
  a pipeline reports the status of its *last* command. `tee` always succeeds,
  which means all four matrix jobs would have passed no matter what the suite
  did. The step now captures the status explicitly, prints the output and the
  skip audit, and re-raises with `exit ${rc}`. `CIWorkflowTests` guards the
  invariant so the same edit cannot land silently again.

- `renderMd` in the web console threw on a non-string argument. `re.exec(src)`
  coerces implicitly, but the `src.slice()` that follows does not, so a `null`
  content would break the Chat tab. The server legitimately emits
  `content: null` for an empty reply — one of the documented failure modes — so
  this would have crashed the console in exactly the case where the user most
  needs to see "(empty response)". Both call sites happened to guard with
  `|| ''`, which is why it never surfaced; the function now coerces like `esc()`
  already did, so it no longer depends on every caller remembering to.

- `README_CN.md` did not link `docs/CONTRIBUTING.md`. The reachability guard
  only ever read `README.md`, so a translation that listed fewer documents than
  the original passed unnoticed — each page still works when linked, which is
  why nobody reports this class of drift. The guard now reads both READMEs, and
  a second test asserts the two doc indexes are identical, so a page linked from
  one and not the other fails even when both satisfy reachability individually.

- Three documentation links pointed at anchors that do not exist:
  `SECURITY.md#ssrf` (the heading is "SSRF protection on image fetching"),
  `AUTHENTICATION.md#authenticating-to-google` (the heading is "Outbound:
  authenticating to Google"), and a self-reference in the new CONTRIBUTING.md.
  Clicking any of them landed at the top of the page instead of the section.
  Relative links were already checked for reachability; anchors now are too.

- `start.bat` was committed with LF-only line endings and no `.gitattributes`.
  `cmd.exe` locates `goto` labels by scanning for CR-terminated lines, so a
  LF-only batch file can fail to find a label or mis-parse a parenthesised block
  — and the launcher depends on `goto :fail`, a `:scanargs` loop and several
  `if ... ( ... )` blocks. `.gitattributes` now pins `*.bat -text`, storing the
  CRLF bytes verbatim. `-text` rather than `text eol=crlf` deliberately: the
  latter normalises the blob to LF and converts only at checkout, so a GitHub
  "Download ZIP", `git archive` or raw-file fetch would still hand out LF-only
  bytes to exactly the users least equipped to debug the result.

- The lint job checked `gemini_web2api tests` but not `scripts/`, so the Python
  the Windows launcher depends on was never linted or compile-checked in CI.

- `start.bat` expanded `%*` on the launch line, *after* its `--port` scan had
  run `shift` over every argument. Whether `shift` also empties `%*` is
  cmd.exe version-dependent; where it does, the launcher silently discarded
  every flag the user passed, so `start.bat --api-key sk-secret` would have
  started an open, unauthenticated server while printing the key in its banner.
  The arguments are now captured into `USERARGS` before any `shift`, making the
  behaviour identical on every Windows.

- A stream that failed mid-flight never incremented `upstream_failures`. The
  non-streaming path routes through `_upstream_failure()`, which counts it; the
  streaming path caught the exception, emitted an SSE error event and moved on.
  The result was that an outage affecting only streaming traffic — the common
  case, since streaming holds connections open longest — reported a perfectly
  healthy server. It cannot reuse `_upstream_failure()`, because that sends an
  HTTP error status after the `200` header has already gone out, so the counter
  is incremented directly. The HTTP status remains legitimately `200`: headers
  were sent before the failure, and the error travels as an SSE event.

- `resolve_model` ignored the configured `default_model` when falling back,
  using a hardcoded name instead.

- `@think=N` accepted out-of-range and non-integer values silently. Now validated
  to 0–4 with a clear error.

- `CONFIG = dict(DEFAULT_CONFIG)` shallow-copied, aliasing mutable defaults such
  as `api_keys`; an in-place mutation would have corrupted `DEFAULT_CONFIG`.
  Now a deep copy.

- Startup called `fetch_latest_bl()` synchronously with a 15-second timeout, so
  an unreachable network delayed the banner by 15 s. Now short-timeout,
  non-fatal, and reported in the banner.

### Added

**Endpoints**

- `GET /health`, `/healthz`, `/live` — always-public liveness probes reporting
  version, uptime, request count, streaming backend and a `checks` object of
  operational warnings.
- `GET /ready` — readiness probe, 503 when `checks.fatal` is non-empty.
- `GET /status` — live metrics plus a secret-redacted config view. Gated when
  API keys are configured.
- `GET /v1/models/{id}` and `GET /v1beta/models/{id}` — single-model retrieval.
  `client.models.retrieve()` in the OpenAI SDK previously 404'd.
- `POST /v1/completions` — the legacy text-completion API, in both streaming and
  non-streaming form. Previously 404.
- `POST /v1/embeddings`, `/v1/audio/speech`, `/v1/images/generations` now return
  an explicit `501 not implemented` rather than a bare 404.
- `HEAD` support, and `OPTIONS` now advertises the real method and header sets
  with a preflight cache.
- `stream_options.include_usage` on chat completions, emitting a final
  `choices: []` chunk with usage as the OpenAI spec requires.
- `X-Request-Id` on every response, echoed in logs, so a client-side error can be
  tied to a server-side cause.

**Web console**

- A self-contained console at `GET /` for browsers, replacing the status-only
  page. Five tabs — **Chat**, **Status**, **Activity**, **Models**, **API** —
  with inlined CSS and JS and no external requests, so it works air-gapped.
  `GET /` still returns JSON for programmatic clients via content negotiation,
  and `/?format=json` forces it.
- The **Chat** tab is a real streaming playground: it consumes the SSE stream
  incrementally, has a Stop button backed by `AbortController`, a model picker
  with thinking-depth selection (`@think=0..4`), and renders a safe subset of
  Markdown. Conversations are saved in the browser's `localStorage` and can be
  switched between, renamed and deleted; multi-turn works by resending the
  transcript, since Gemini's web endpoint is single-turn. The server stores no
  conversation records.
- The **Activity** tab shows the request history from `/status`, filterable by
  all / errors / route, auto-refreshing every 5s.

**Request history**

- `/status` gained a `history` array: the most recent requests, newest first,
  each with timestamp, `X-Request-Id`, method, path, status, the **resolved**
  model, latency and client address. Recording the resolved name means a silent
  fallback from an unknown model is visible rather than looking like success.
- Bounded by the new `history_max` option (default 200, hard cap 1000, `0`
  disables it), so a long-running process cannot grow without limit.
- Stores operational facts only. Prompts, response bodies and credentials are
  never recorded, and query strings are stripped so a `?key=<api_key>` cannot be
  retained. Because entries carry client addresses, history is served **only**
  from the auth-gated `/status` and is excluded from `metrics.snapshot()` and
  from the state embedded in the public `GET /` page, which gets an
  `history_enabled` flag instead.

**Contributing**

- `docs/CONTRIBUTING.md` — the contribution process, kept separate from
  DEVELOPMENT.md rather than duplicating it: what to include in an issue, what
  CI enforces, and the project-specific rules that reject otherwise reasonable
  changes. Those rules are written down because each one has already cost
  somebody a debugging session: the test-count guard that fails the same commit
  which adds the tests, the Python 3.8 floor enforced by AST rather than trust,
  the no-httpx configuration that must be run locally, the offline-only test
  rule, and the guard tests that must be proved to fail before they are kept.
  GitHub surfaces it in the PR and new-issue UI.

**Windows**

- `start.bat` — a one-click launcher at the repository root. It finds a Python
  3.8+ interpreter, creates `.venv` (recreating it if broken), installs
  `requirements.txt`, writes a default `config.json` and starts the server,
  then opens the dashboard once the socket has bound.
- The generated config binds `127.0.0.1` with no API keys. A double-clicked
  launcher runs on a desktop, and binding `0.0.0.0` there would publish an
  unauthenticated proxy to the whole LAN; localhost-only makes auth-off safe.
  An existing `config.json` is never modified.
- Python is detected by executing each candidate and checking its version, not
  by searching PATH — Windows ships a Microsoft Store `python.exe` stub that
  opens the Store instead of running Python.
- A failed dependency install warns rather than aborting, since `httpx` is
  optional; errors end in `pause` so the window cannot vanish unexplained.
- `scripts/win_setup.py` holds the JSON handling the batch file delegates to it.

**Configuration**

- Environment variables for every option as `GEMINI_WEB2API_<KEY>`, with type
  coercion. `api_keys` accepts a comma list, a pipe list or a JSON array.
- `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` are honoured when `proxy` is unset.
- A cookie file that is a `gemini-auth.json` export now applies `xsrf_token`,
  `gemini_bl` and `auth_user` automatically — previously only `cookie` and
  `sapisid` were read, so the bundled browser extension's output required a
  manual `jq` pipeline. Values you set explicitly still win.
- Netscape/curl cookie-jar files are parsed directly, including `#HttpOnly_`
  records — Google's session cookies are HttpOnly, so a parser that skipped
  comment lines discarded exactly the ones that matter.
- Cookie arrays exported as JSON (`[{"name": …, "value": …}]`) are accepted.
- New options: `auto_update_bl`, `strict_models`, `block_private_image_urls`,
  `max_request_bytes`, `max_image_bytes`, `rate_limit_max`,
  `rate_limit_window_sec`, `cors_origin`, `shutdown_timeout_sec`.
- Unknown config keys now warn by name instead of being silently ignored, which
  catches typos like `apikey`.
- Invalid JSON, a non-object config, out-of-range ports and bad numbers warn and
  fall back to defaults instead of crashing at startup.
- New CLI flags: `--host`, `--api-key` (repeatable), `--default-model`,
  `--gemini-bl`, `--log-level`, `--rate-limit`, `--rate-limit-window`, `--quiet`,
  `--no-auto-bl`.

**Security** — see [SECURITY.md](SECURITY.md)

- SSRF protection on `image_url` fetching: only `http(s)`, DNS-resolved and
  checked against private/loopback/link-local/reserved/multicast ranges,
  IPv4-mapped IPv6 unwrapped, unresolvable hosts refused, credentials in URLs
  rejected, and **every redirect hop re-validated** so a public URL cannot 302 to
  the cloud metadata endpoint.
- Response size caps: `max_image_bytes` (20 MiB) and `max_request_bytes`
  (25 MiB), the latter enforced while accumulating a chunked body.
- Constant-time API key comparison via `hmac.compare_digest`.
- `WWW-Authenticate` on 401, and all errors now use OpenAI's
  `{message, type, code, param}` shape so SDKs surface them properly.
- Optional fixed-window rate limiting per key (or per IP), returning 429 with
  `Retry-After`. Ported from the Cloudflare Workers implementation.
- `GET /status` redacts secrets; no endpoint returns a cookie value.

**Packaging and operations**

- Docker image runs as an unprivileged user (uid 10001), no longer bakes
  `config.example.json` in as `config.json` (which shipped the public key
  `sk-gemini`), and has a built-in `HEALTHCHECK`.
- `SIGTERM` is handled with a graceful drain, so `docker stop` and
  `systemctl stop` no longer truncate active streams.
- A canonical `docker-compose.yml` — the documented `docker compose up -d`
  previously failed because only the non-default `docker-compose.local.yml`
  existed. Both variants now have healthchecks, restart policy, log rotation and
  `no-new-privileges`.
- `python -m gemini_web2api._healthcheck`, a standalone probe needing no package
  imports and no curl in the image.
- `TCP_NODELAY` for streaming latency.
- `.github/workflows/ci.yml`: the suite on Python 3.8/3.11/3.12/3.13, a
  stdlib-only job, a build-and-install-the-wheel job, ruff, and a Docker build.

### Changed

**Performance**

- One pooled `httpx.Client` process-wide instead of a new client per request, so
  TLS sessions and TCP connections are reused. This applies to non-streaming
  requests too, which previously paid a full handshake every time.
- HTTP/1.1 with keep-alive, replacing HTTP/1.0's connection-per-request.
- `X-Accel-Buffering: no` and `Cache-Control: no-transform` on SSE responses so
  nginx does not buffer the stream.
- Cookie file cached by `(path, mtime, size)`; one `stat()` per request warm.
- Page tokens cached for 10 minutes.
- Regexes compiled once at module scope.
- Build-tag refresh rate-limited to once per minute so an outage cannot become a
  scrape storm.

**Behaviour**

- An unknown model still falls back to the default (clients probe with `gpt-4`
  during setup and a hard 400 breaks their connection test), but it is now
  logged, and `strict_models: true` opts into the error.
- An unfetchable or policy-refused image is a **400** `image_rejected`, not a 502
  blaming the upstream.
- Empty Google-native replies return a short placeholder instead of an empty
  `parts[0].text`, which made Gemini CLI hang.
- `clean_text` also strips `googleusercontent.com/card_content/` placeholders.
- The User-Agent is a complete, current Chrome string rather than a truncated one.
- Startup banner reports the dashboard and health URLs, the resolved build tag,
  rate-limit state, and actionable warnings for missing httpx or missing API keys.
- Upstream 429 is passed through as 429; a 405 explains that the build tag is
  stale and names `gemini_bl`.

### Removed

- `gemini_web2api.py`'s 1108-line duplicate implementation. The file remains as a
  ~60-line compatibility shim so `python gemini_web2api.py` keeps working; it now
  exits with a clear actionable message if the package is absent. The README's
  "single file" claim was already false — the monolith imported
  `gemini_web2api.multimodal`, so copying it elsewhere broke image requests with
  `ModuleNotFoundError: 'gemini_web2api' is not a package`.
- Dead code: `server._usage()` was defined but never called while four call sites
  inlined the calculation inconsistently — it is now the single implementation.
  `tools._compress_b64_if_needed()` and `MAX_IMAGE_B64_SIZE` were never called;
  they were vestigial from a design that embedded images as base64 text, and
  carried an unused Pillow dependency plus a silent `b64[:50000]` truncation path.
- The arbitrary length gates in `_extract_texts_from_line`.
- `tests/test_modular_sync.py`, whose purpose was comparing the two divergent
  copies. Its cases were folded into `test_protocol.py`, `test_tools.py` and
  `test_endpoints.py`.

### Documentation

- New `docs/` folder: [README](README.md) (index),
  [ARCHITECTURE](ARCHITECTURE.md), [CONFIGURATION](CONFIGURATION.md),
  [API](API.md), [AUTHENTICATION](AUTHENTICATION.md),
  [DEPLOYMENT](DEPLOYMENT.md), [SECURITY](SECURITY.md),
  [TROUBLESHOOTING](TROUBLESHOOTING.md), [DEVELOPMENT](DEVELOPMENT.md),
  [CHANGELOG](CHANGELOG.md) and [AUDIT](AUDIT.md).
- README rewritten: correct model table (it was missing `gemini-3.7-flash` and
  `gemini-3.1-pro-enhanced`), the "single file" claim removed, every config key
  documented, and links into `docs/`.
- `gemini-cookie-sync-extension/SETUP.md` simplified — the `jq` pipeline it
  prescribed is no longer needed.
- `config.example.json` now lists every option.
- `cloudflare/README.MD` gained a divergence table against the Python server,
  verified against `worker.js` rather than assumed. Two silent-failure
  behaviours are now called out: image parts are discarded without error, and
  any unmatched `POST /v1/*` falls through to chat completions instead of
  returning 501. A placeholder upstream URL reading
  `github.com/your-repo/gemini-web2api` was corrected.

### Cloudflare Worker (1.6.0-cf-multifingerprint)

- Added `gemini-3.7-flash`, bringing the Worker to 8 models. Its
  `(mode, think) = (1, 4)` matches the existing `gemini-3.6-flash`, and the
  emitted payload was verified byte-identical apart from the per-request UUID in
  slot 59 — so this cannot change behaviour for any existing model.
- `gemini-3.1-pro-enhanced` was deliberately **not** added. It requires payload
  slots 31 and 80, but `buildPayload()` allocates `new Array(80)` (slot 80 is out
  of range; JS would silently grow the array to 81 rather than raise, sending a
  payload shape that differs from the Python server's 102 slots), and
  `resolveModel()` has no field to carry extra slots at all. Supporting it means
  changing the payload shape for *every* model, which is not justified without
  end-to-end verification against `gemini.google.com`. The gap and the required
  changes are documented in `cloudflare/README.MD`.

### Tests

- 18 tests → **507**, all offline. The Gemini wire protocol is faked at the frame
  level so real parsing and real HTTP handling are exercised without a network.
- New modules for config layering, cookie formats, model resolution, protocol
  framing and streaming, prompt/tool parsing, every HTTP route, security
  behaviour, and packaging/structure.
- Regression tests guard each fixed defect, plus structural invariants: the shim
  stays a shim, `config.example.json` matches `DEFAULT_CONFIG`, every model
  appears in the README, the README documents no nonexistent model, versions
  agree, and no secret file is tracked by git.
- Documentation is now checked for *accuracy*, not just presence — the original
  defect was pages that existed and were wrong. Tests assert every endpoint in
  `API.md` is really routed, every `DEFAULT_CONFIG` key is documented, every
  docs page is reachable from the index and the README, the current version has
  a CHANGELOG entry, and the test count both READMEs advertise matches the suite
  that actually runs. (That last guard caught its own author: adding tests
  invalidated the count in the same commit that added them.)
- `requires-python = ">=3.8"` is enforced by AST inspection rather than trust,
  since `compile()` under a newer interpreter accepts newer syntax and proves
  nothing. A first attempt scanned source text with a regex and false-positived
  on a `|` inside a regex *string literal* in `gemini.py`; only nodes in
  annotation position are inspected now.
- Worker/Python model parity is checked by parsing `cloudflare/worker.js`:
  phantom models, `(mode, think)` disagreements, and undocumented gaps all fail,
  and `node --check` guards the Worker's syntax (skipped when Node is absent).
  Each guard was verified to fail on an injected violation before being kept.
- The console's JavaScript is now **executed**, not just parsed. `node --check`
  proves syntax; it says nothing about whether the escaping works. 
  `DashboardScriptTests` extracts the DOM-free portion of the dashboard's inline
  `<script>` (`esc` through `renderMd`), concatenates
  `tests/dashboard_render_assertions.js` after it so both share one scope, and
  runs the pair under Node. It asserts that eight injection payloads — including
  attribute and script-context breakouts, and the same payloads nested inside
  inline code, bold and a heading — produce no live markup, that fenced blocks
  are escaped rather than formatted, that legitimate Markdown still renders, and
  that hostile input cannot throw. Model output is attacker-influenced content
  that the console assigns to `innerHTML`, so this is the one part of the UI
  where "it looks escaped" is not good enough. All three variants were verified
  to fail on an injected violation: a no-op `esc`, formatting applied before
  escaping, and the removed null coercion. Skipped when Node is absent.
- `CIWorkflowTests` turns the CI workflow into a guarded artifact. A green
  checkmark is only evidence if a failure would have turned it red, and this
  branch shipped a step where it would not (see Fixed). The guards reject a
  piped suite invocation unless the block really enables `pipefail`, require a
  captured `$?` to be re-raised with `exit`, and require the step to report how
  many tests it skipped — since a guard that silently skips is indistinguishable
  from one that ran. They parse `run:` blocks by hand rather than with pyyaml,
  because the stdlib-only job runs this suite with no third-party packages.
  Writing these caught the author twice: the first version checked for the word
  `pipefail` in the raw block, which the block's own comment explaining the
  hazard satisfied, so the guard passed on the exact defect it existed to
  forbid. It now scans commands with comments stripped, and a fifth test guards
  the parser itself, because a parser that finds nothing makes the other four
  pass vacuously. All five were verified to fail on an injected violation, and a
  sixth check confirms a genuine `set -o pipefail` does *not* trip them.
- `DockerfileTests` cross-checks the Dockerfile against `.dockerignore`
  **statically**, which is the only way to guard this without a container
  runtime: every `COPY` source must survive the ignore rules and exist in the
  repository, secrets must stay excluded, no COPY may land on `config.json`, and
  the `!LICENSE` negation must follow the pattern it undoes. The ignore matcher
  reproduces Go's `filepath.Match` semantics rather than using `fnmatch`, whose
  `*` crosses path separators and would report `docs/API.md` as matched by
  `*.md`; two tests pin that behaviour, because a checker that cries wolf gets
  ignored. All four guards were verified to fail on an injected violation,
  including restoring the exact defect CI had just caught.
- Every relative link **and heading anchor** in the 15-file Markdown corpus is
  resolved by `MarkdownLinkTests`. Anchor slugs follow github-slugger exactly —
  including the detail that each space becomes one hyphen, so an em dash removed
  between two words leaves a double hyphen. A first version collapsed space runs
  instead and reported two healthy AUDIT.md anchors as broken; two tests now pin
  the slugifier's behaviour so the guard cannot silently start lying. All three
  link guards were verified to fail on an injected violation, including renaming
  a real heading to orphan everything pointing at it.
- Request history is covered from both sides: `HistoryTests` exercises the ring
  directly (bound at the cap, `limit <= 0` returns nothing, non-dict entries
  dropped, entries returned as copies, absent from `snapshot()`), and
  `RequestHistoryTests` drives it over real HTTP (query strings stripped, errors
  recorded, resolved model attributed, `history_max: 0` disables it, streaming
  counted, nothing leaked into the public `GET /` state).
- The Windows launcher cannot execute on a Linux CI runner, so
  `WindowsLauncherTests` guards its content — version-checked Python detection,
  quoted paths, `pause` on failure, localhost-only defaults, requirements.txt as
  the single dependency source — and `WindowsSetupHelperTests` imports
  `scripts/win_setup.py` and tests its real behaviour (creates, idempotent,
  preserves a user port, survives corrupt JSON, rejects out-of-range ports).
  Every content guard was verified to fail by injecting the violation it
  forbids, then restored.

---

## [1.1.0]

- Modular `gemini_web2api/` package alongside the single-file implementation.
- `temporary_chats` support via payload slots 41 and 45.
- Responses API streaming with the full Codex CLI event sequence and
  `sequence_number`.
- True SSE streaming for the Google-native `streamGenerateContent` endpoint.
- `tool_choice` support (`none`, `auto`, `required`, named function).
- Google-native `functionCall` parsing and `functionCallingConfig` handling.
- `gemini-3.1-pro-enhanced` with experimental output-shaping fields.
- Image MIME sniffing from file signatures.
- Cookie file caching by mtime.
- Chunked request-body support.
- Test suite covering payload persistence flags and the streaming endpoints.

## [1.0.0]

- Initial release: single-file Gemini Web to OpenAI API proxy.
- `/v1/chat/completions`, `/v1/models`, `/v1/responses`.
- Google-native `/v1beta` endpoints for Gemini CLI.
- Prompt-based tool calling.
- Optional API keys, cookie authentication, SAPISIDHASH, `auth_user` and XSRF.
- Multimodal image input via Scotty resumable upload.
- SSE streaming with `httpx` and a `urllib` fallback.
- Proxy support, retries, Docker packaging.

[1.2.0]: https://github.com/KetanDutt/gemini-web2api/releases/tag/v1.2.0
[1.1.0]: https://github.com/KetanDutt/gemini-web2api/releases/tag/v1.1.0
[1.0.0]: https://github.com/KetanDutt/gemini-web2api/releases/tag/v1.0.0
