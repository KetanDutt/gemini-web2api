# Development

## Getting started

```bash
git clone https://github.com/KetanDutt/gemini-web2api
cd gemini-web2api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # httpx + pytest + ruff
python -m unittest discover -s tests -t .
```

No network access is needed to run the suite: the Gemini wire protocol is faked
at the frame level, so tests exercise real parsing and real HTTP handling offline.

Run the server:

```bash
python -m gemini_web2api --port 8081 --log-level debug
```

## Layout

```
gemini_web2api.py          compatibility shim — delegates to the package
gemini_web2api/
  __init__.py              version and package docs
  __main__.py              CLI, config precedence, signals, banner
  config.py                defaults, file/env layering, validation, redaction
  models.py                model table, MODE_CATEGORY, @think= resolution
  gemini.py                wire protocol, cookies, bl refresh, streaming
  multimodal.py            image fetch (SSRF-guarded) + Scotty upload
  tools.py                 prompt construction, tool-call parsing
  server.py                HTTP endpoints, auth, rate limit, SSE
  webui.py                 self-contained dashboard
  ratelimit.py             fixed-window limiter
  metrics.py               counters and latency histogram
  _healthcheck.py          container liveness probe (standalone)
tests/
  support.py               server fixture, fake wire frames, SSE decoder
  test_config.py           config layering and coercion
  test_cookies.py          every cookie format, auth-file adoption
  test_models.py           model table and resolution
  test_protocol.py         payload, parsing, streaming
  test_tools.py            prompt building and call parsing
  test_endpoints.py        every HTTP route, request history
  test_security.py         auth, SSRF, limits, rate limiting
  test_metrics.py          metrics, history ring, health probe, dashboard
  test_packaging.py        build, shim, structure, docs, Windows launcher
scripts/
  win_setup.py             config generation for start.bat (plain Python)
start.bat                  one-click Windows launcher
docs/                      all documentation
cloudflare/                independent Workers port
gemini-cookie-sync-extension/  Chrome extension
```

### The single-implementation rule

There is exactly one implementation, in `gemini_web2api/`. The root
`gemini_web2api.py` is a shim. `tests/test_packaging.py` enforces this: it fails
if the shim grows past 120 lines or starts containing `class GeminiHandler`,
`def do_POST`, `StreamGenerate`, `MODELS =` or `DEFAULT_CONFIG =`.

This guard exists because the project previously shipped two full copies that
had drifted apart in both directions. See
[AUDIT.md](AUDIT.md#12-two-divergent-implementations-of-the-same-server--critical).

## Testing

```bash
python -m unittest discover -s tests -t .          # everything
python -m unittest tests.test_endpoints -v         # one module
python -m unittest tests.test_endpoints.ChatStreamingTests.test_chunk_sequence
pytest                                             # also works; pyproject configures testpaths
```

### Verifying the zero-dependency claim

The core must work with no third-party packages. Test it:

```bash
python3 -m venv /tmp/nohttpx
/tmp/nohttpx/bin/python -m unittest discover -s tests -t .
```

11 streaming tests skip; everything else must pass. CI runs this as a separate
job.

### Test helpers

`tests/support.py` provides:

* `ServerTestCase` — a real `ThreadedServer` on an ephemeral port, with
  `get_json` / `post_json` / `post(chunked=True)` helpers and automatic CONFIG
  restoration.
* `ConfigTestCase` — resets CONFIG per test, logging off by default.
* `gemini_frame(texts)` — builds one realistic `wrb.fr` line. Pass a list for a
  multi-part reply.
* `cumulative_response(final, steps)` — frames growing towards `final`, as Gemini
  actually streams.
* `decode_sse(body)` — `[(event_type, payload)]`.
* `sse_deltas(body)` — the concatenated chat-completion content.

Mock the seam, not the internals:

```python
@mock.patch("gemini_web2api.server.generate", return_value="a reply")
def test_x(self, _generate):
    ...
```

`server.py` imports `generate`, `generate_stream`, `upload_image`,
`fetch_image_bytes` and `parse_tool_calls` into its own namespace precisely so
tests can patch them there. Keep that.

### What is asserted

Beyond ordinary behaviour, the suite locks in specific regressions. When
touching these areas, keep the tests green:

* `test_protocol.ExtractResponseTextTests` — last-frame selection, short frames
  not discarded, multi-part handling.
* `test_protocol.GenerateStreamTests.test_multi_part_response_does_not_abort_the_stream`
* `test_endpoints.ChatStreamingTests.test_stream_failure_still_terminates_with_done`
* `test_security.ApiKeyTests.test_rejected_post_does_not_desynchronise_the_connection`
* `test_endpoints.ModelListingTests.test_google_single_model`
* `test_endpoints.RoutingTests.test_query_strings_do_not_break_routing`
* `test_cookies.*` — every cookie format
* `test_packaging.PyprojectTests.test_packages_are_declared_explicitly`
* `test_endpoints.RequestHistoryTests.test_streaming_request_is_counted` —
  `_start_sse()` used to return before `_record()`, so streamed replies never
  appeared in `requests_served` or `status_codes`.
* `test_endpoints.RequestHistoryTests.test_history_is_absent_from_the_public_dashboard`
  and `test_dashboard_state_leaks_no_request_ids` — `GET /` is unauthenticated,
  so history (which carries client addresses) must never be embedded in it.
* `test_metrics.HistoryTests.test_history_is_not_in_the_snapshot` — same
  invariant from the other side: `snapshot()` must stay history-free.
* `test_packaging.WindowsLauncherTests.*` — the launcher cannot execute on the
  Linux CI runner, so these are content guards. Each was verified to fail by
  injecting the violation it forbids.

### Editing `start.bat`

**Keep CRLF line endings.** `cmd.exe` locates `goto` labels by scanning for
CR-terminated lines, so a LF-only batch file can fail to find a label or
mis-parse a parenthesised block. The launcher uses `goto :fail`, a `:scanargs`
loop and several `if ... ( ... )` blocks, so this is a correctness requirement.

`.gitattributes` pins `*.bat -text`, which stores the CRLF bytes verbatim rather
than normalising to LF and converting at checkout. Do not "fix" that to
`text eol=crlf`: a GitHub ZIP download, `git archive` or raw-file fetch bypasses
checkout conversion and would hand out LF-only bytes.

If your editor converts the file anyway, `test_launcher_uses_crlf_line_endings`
and `test_git_stores_the_launcher_blob_as_crlf` will fail. Restore with:

```bash
git add --renormalize start.bat
```

**Do not expand `%*` after a `shift`.** Whether `shift` also empties `%*` is
cmd.exe version-dependent. The launcher scans its arguments for `--port` with a
`shift` loop, so the caller's arguments are copied into `USERARGS` *before* that
loop and the launch line expands the variable. Expanding `%*` directly would
silently drop every flag on some Windows versions — `start.bat --api-key
sk-secret` would start an open server while still printing the key.
`test_arguments_are_captured_before_the_shift_loop` guards the ordering.

Anything reading or writing JSON belongs in `scripts/win_setup.py`, not in the
batch file — batch quoting cannot express it reliably, and Python can be tested.
That helper prints `PORT=<n>` / `CREATED=<0|1>` on stdout for the batch file to
parse, so keep human-readable messages on stderr (`say()`).

## Linting

```bash
ruff check gemini_web2api tests
python -m compileall -q gemini_web2api tests
```

Config is in `pyproject.toml`: line length 110, target py38, rules `E,F,I,B,SIM,UP`.
`cloudflare/` is excluded — it is a separate codebase in a different language
dialect.

## Common tasks

### Adding a model

Edit `MODELS` in `gemini_web2api/models.py`:

```python
"gemini-4.0-flash": {
    "mode": 1,          # MODE_CATEGORY: 1 FAST, 2 THINKING, 3 PRO, 4 AUTO,
                        #                 5 FAST_DYNAMIC_THINKING, 6 FLASH_LITE
    "think": 4,         # 0 deepest … 4 shallowest
    "desc": "Shown in /v1/models and on the dashboard",
    "output": "~12k chars",     # optional, documentation only
    "needs_cookie": True,       # optional, only for mode 3
    "extra": {31: 2, 80: 3},    # optional extra payload slots
},
```

Then add it to the README model table — `test_packaging` asserts that every model
appears in the README and that the README mentions no model that does not exist.

Find the right `mode` by inspecting the frontend bundle: DevTools → Sources →
search for `MODE_CATEGORY`.

`extra` slots must stay below `PAYLOAD_SLOTS` (102); a test enforces this. That
guard exists because `gemini-3.1-pro-enhanced` writes slot 80 and the old
80-slot payload raised `IndexError`.

### Adding an endpoint

1. Add a branch in `_route_get` or `_route_post` in `server.py`. Both match on
   the query-stripped path.
2. Decide whether it needs auth. `_PUBLIC_PATHS` and the `path.startswith("/v1")`
   check govern this. Anything exposing configuration must be gated.
3. Use `send_json` / `send_error_json` / `send_html` — never write to `wfile`
   directly outside the SSE helpers.
4. Add tests in `test_endpoints.py`.
5. Document it in [API.md](API.md) and add it to the `endpoints` list in
   `_dashboard_state`.

### Adding a config option

1. Add the key with its default to `DEFAULT_CONFIG` in `config.py`. The type of
   the default drives environment coercion — use `None` for nullable strings,
   `0` for ints, `False` for bools.
2. If it needs a CLI flag, add it in `build_parser` and wire it in
   `apply_cli_overrides`.
3. Add it to `config.example.json` — `test_packaging` asserts the example has no
   keys that `DEFAULT_CONFIG` lacks.
4. Document it in [CONFIGURATION.md](CONFIGURATION.md).
5. Add a test in `test_config.py`.

### Changing the wire protocol

`gemini.py` is the only place that should know about payload slots. Changes here
need:

* a test in `test_protocol.py` using `gemini_frame` / `decode_payload`,
* an update to the slot table in [ARCHITECTURE.md](ARCHITECTURE.md).

## Versioning

`__version__` in `gemini_web2api/__init__.py` and `version` in `pyproject.toml`
must match — a test asserts it. Bump both, and add a [CHANGELOG.md](CHANGELOG.md)
entry.

* **patch** — bug fixes, no behaviour change for correct clients
* **minor** — new endpoints, new config keys, new models
* **major** — anything that breaks an existing client or config

## Release checklist

```bash
ruff check gemini_web2api tests
python -m unittest discover -s tests -t .
python3 -m venv /tmp/nohttpx && /tmp/nohttpx/bin/python -m unittest discover -s tests -t .
python -m build
python -m venv /tmp/wheelenv && /tmp/wheelenv/bin/pip install dist/*.whl httpx
/tmp/wheelenv/bin/gemini-web2api --version
python gemini_web2api.py --version
docker build -t gemini-web2api:rc .
docker run --rm -p 8081:8081 gemini-web2api:rc &
sleep 5 && curl -s localhost:8081/health && docker inspect --format='{{.State.Health.Status}}' $!
```

Then:

- [ ] Versions bumped in `__init__.py` and `pyproject.toml`
- [ ] `CHANGELOG.md` entry written
- [ ] README model table current (tests enforce this)
- [ ] `config.example.json` matches `DEFAULT_CONFIG` (tests enforce this)
- [ ] Tag `v<version>` — the Docker workflow publishes `ghcr.io` images on tags

## CI

`.github/workflows/ci.yml` runs on push and PR:

| Job | What it does |
|---|---|
| `test` | full suite on Python 3.8, 3.11, 3.12, 3.13 |
| `test-without-httpx` | suite with no third-party packages installed |
| `package` | `python -m build`, install the wheel, run the console script and the shim |
| `lint` | `ruff check` + `compileall` |
| `docker` | build the image (no push) with layer caching |

`.github/workflows/docker.yml` publishes multi-arch images to `ghcr.io` on pushes
to `main` and on `v*` tags.

The `package` job is a regression guard, not a formality: flat-layout
auto-discovery used to fail outright because the root holds both `cloudflare/`
and `gemini_web2api/`, so the project could not be built at all.

## Design notes worth knowing

**Why the standard library HTTP server?** Zero required dependencies is the
project's core promise — it must run anywhere Python runs, with no compilation
and no supply chain. `http.server` is not fast, but this server is I/O bound on
Google, not on request handling. If that ever stops being true, the swap to
`asyncio`/`uvicorn` is contained in `server.py`.

**Why HTTP/1.1 with explicit `Connection: close` on SSE?** Keep-alive saves a
handshake per request for clients making many small calls. But an SSE body has no
`Content-Length`, so under HTTP/1.1 the only way to frame it is to close. Two
invariants keep this safe, and both have tests: every request body is fully read
before responding (including before a 401), and every JSON response declares an
exact length.

**Why prompt-based tool calling?** The Gemini Web endpoint exposes no
function-calling contract. Describing tools in the prompt and parsing fenced
blocks out of the reply is the only option, and it is why tool calls cannot be
truly streamed.

**Why is `extract_response_text` last-frame rather than longest?** Frames are
cumulative, so the last frame holds the complete text. Longest-wins returns a
preamble whenever a response has an earlier long segment. Both were present in
the codebase simultaneously before 1.2.0, in the two divergent copies.

**Why fixed-window rate limiting?** O(1) memory per key regardless of traffic. A
sliding-window log grows with request volume, which is a memory leak in a
long-lived process.
