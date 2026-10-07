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
  dashboard_render_assertions.js  run under Node against the console's escaper
  test_config.py           config layering and coercion
  test_cookies.py          every cookie format, auth-file adoption
  test_models.py           model table and resolution
  test_protocol.py         payload, parsing, streaming
  test_tools.py            prompt building and call parsing
  test_endpoints.py        every HTTP route, request history
  test_security.py         auth, SSRF, limits, rate limiting
  test_metrics.py          metrics, history ring, health probe, dashboard
  test_packaging.py        build, shim, structure, docs, launcher, CI itself
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
* `test_metrics.DashboardScriptTests` — the console's escaping functions are
  *executed* under Node, not merely parsed. Model output is attacker-influenced
  content assigned to `innerHTML`, so `node --check` is not enough. The
  assertions live in `tests/dashboard_render_assertions.js`, which is
  concatenated after the extracted functions so they share one scope; that file
  is not standalone and `node --check` on it alone will fail on undefined names.
  It is shipped in the sdist via `MANIFEST.in` — without it the test errors on a
  machine that has Node.
* `test_packaging.WorkerRoutingTests` — `cloudflare/worker.js` is *executed*
  under Node with upstream `fetch` stubbed to throw **and to record that it was
  called**. Its only other test is `node --check`, which proves the file parses
  and nothing about what it does: a syntax check cannot see a route answering
  the wrong status, or a request quietly spending a real Gemini call. Stubbing
  `fetch` rather than blocking the network is what makes "zero upstream calls"
  assertable, so an unimplemented endpoint can be proven not to consume quota.
  Two of the assertions are deliberately paired — one requires
  `/v1/embeddings`, `/v1/audio/speech` and `/v1/images/generations` to answer
  501, the other requires the remaining `/v1/*` fall-through to *still* forward
  to chat, because that fall-through is intentional tolerance for clients that
  post to slightly different paths. Without the pair, "fixed" and
  "over-corrected" are indistinguishable. The dangling-timer assertion works by
  wall clock: a leaked `setTimeout` keeps Node's event loop alive for
  `requestTimeoutSec`, so a regression cannot finish inside `MAX_SECONDS`.
  Skips when Node is absent, and runs from the sdist because `MANIFEST.in` ships
  `cloudflare/`.
* `test_packaging.WorkerImageHandlingTests` — the same execution harness, pointed
  at multimodal bodies. The `fetch` stub here records the **form-encoded request
  body** and decodes it before throwing, so the assertions read the exact prompt
  Gemini would have received rather than inferring it from a status code. That
  is the only way to test this defect at all: the Worker answered 502 either way,
  and before 1.6.2 it discarded image parts while looking completely healthy.
  Coverage spans all three protocol routes, because each parses multimodal
  content in its own place and each had to be fixed separately — asserting one
  would have left two silently discarding. The control cases are what make the
  suite mean something: they require text-only requests and a `null` content part
  to come out *unannotated*, so "fixed" is distinguishable from "appends the
  note to everything". Shares `_run_worker_harness` with `WorkerRoutingTests`.
* `test_jsonmode.JsonModeEndpointTests` — the most likely regression in JSON
  mode is not a bug in the validator, it is someone editing `_chat` and dropping
  the call to it. Validation is a separate module, so a request would still
  succeed and still return prose with a `200` — exactly the behaviour the feature
  exists to prevent, and undetectable from the response shape. These tests
  therefore assert against the **prompt the upstream actually received** (via
  `generate.call_args`), not only the response, because a stubbed upstream returns
  valid JSON whether or not the instruction was ever sent. Removing the
  instruction append, the chat enforcement, the `/v1/responses` `text.format`
  read, the legacy-endpoint enforcement or the streaming buffer each fails a test;
  all five were injected and confirmed.
* `test_packaging.DocumentationConsistencyTests.test_the_documented_counters_are_the_counters_that_exist`
  — `metrics.inc` ignores unknown names by design (a typo must not create a
  phantom metric), which means a new counter that is never registered is
  *silently* dropped and reads 0 forever. Adding `json_mode_failures` hit exactly
  that: the increment was a no-op and only a test caught it. The guard compares
  `metrics.snapshot()["counters"]` against the sample in `docs/API.md` key-for-key,
  so a counter the docs omit, or one the docs invent, fails the build.
* `test_packaging.CIWorkflowTests` — CI must fail when the suite fails. Actions'
  default shell on Linux is `bash -e {0}`, **without** `pipefail`, so
  `python -m unittest ... | tee log` reports `tee`'s status and a failing suite
  still turns the checkmark green. The guards reject a piped suite invocation
  unless the block really enables `pipefail`, require a captured `$?` to be
  re-raised with `exit`, and require the step to report how many tests it
  skipped — a guard that silently skips is indistinguishable from one that ran.
  They parse `run:` blocks by hand rather than with pyyaml, because the
  stdlib-only job runs this suite with no third-party packages installed.
  Note that they scan **commands, not prose**: an earlier version checked for
  the word `pipefail` in the raw block, and the block's own comment explaining
  the hazard satisfied it. `test_the_run_blocks_are_actually_parsed` guards the
  parser itself, since a parser that finds nothing makes the rest pass
  vacuously.
* `test_packaging.MarkdownLinkTests` — every relative link and heading anchor in
  the Markdown corpus resolves. Renaming a heading without updating what points
  at it fails here. If you add an anchor link, the slug is GitHub's: lowercase,
  punctuation dropped, and **each** space becomes one hyphen (so an em dash
  removed between words leaves a double hyphen).

### Git-dependent tests must skip, not pass vacuously

An sdist or a `git archive` checkout has no `.git`. `git ls-files` outside a
repository prints its fatal error to **stderr** and leaves **stdout empty**, so
a guard written as "assert stdout is empty" is satisfied by git being absent or
broken — it passes vacuously and proves nothing. Two such tests existed here
before this was noticed.

Every git-based test therefore carries `@unittest.skipUnless(HAS_GIT, NO_GIT)`
*and* asserts `result.returncode == 0`, so a green run in a real repository
means git actually answered. `HAS_GIT` comes from
`git rev-parse --is-inside-work-tree` at import time.

Guards on **repository metadata** (`.gitignore`, `.gitattributes`,
`.github/workflows/ci.yml`) use a different gate, because those files are
deliberately absent from a distribution but their disappearance from a
repository is a defect. They are gated on `file exists OR this is a git tree`,
not on `HAS_GIT` alone and not on existence alone:

- existence alone would turn a deleted `.gitattributes` into a silent skip — a
  vacuous pass wearing a disguise;
- `HAS_GIT` alone would skip them in a `git archive` tree, which has no `.git`
  yet still carries every tracked file.

### Run the suite from an extracted sdist

A working tree and a `git archive` checkout both carry every tracked file, so
neither can tell you what a *source distribution* contains — `MANIFEST.in`
decides that, and it is easy to ship documentation that promises files the
sdist omits. That happened here: the sdist carried `docs/DEPLOYMENT.md` telling
the reader to use `docker-compose.yml` and the `Dockerfile`, and a README
documenting `python gemini_web2api.py` with an embedded `logo.png`, while
`MANIFEST.in` listed none of them. Both a working tree and a `git archive`
reported 507 passing; the sdist reported 14 errors.

```bash
python -m build --sdist && mkdir -p /tmp/sdx \
  && tar -xzf dist/*.tar.gz -C /tmp/sdx && cd /tmp/sdx/*/ \
  && python -m unittest discover -s tests -t .
```

CI does this too: the `package` job runs the suite from the extracted sdist and
emits an `sdist-suite` annotation with the exit status and summary, so a
manifest regression fails the build instead of shipping.

That step compares the number of tests the sdist ran against the count the
README documents, and fails if they differ. The expectation is **derived from
the README rather than written into the workflow**, because `README.md`'s figure
is already enforced against the repository suite by
`test_documentation_consistency` — so it cannot drift, whereas a literal in
`ci.yml` went stale the moment the suite grew and nothing noticed.

The comparison is also what stops the step passing vacuously. `unittest` exits 0
on a *smaller* suite, so a `MANIFEST.in` that shipped fewer test files would
otherwise look green while quietly covering less. Dropping one test module from
the manifest was verified to fail the step with
`ran=460 expected=518`. Locally the same run
reports 23 skips rather than 12 unless `httpx` is installed, because the
incremental-streaming tests gate on it — CI installs it first.

Expect **573 tests and 12 skips** from an extracted sdist: 5 git-dependent and
7 repository-metadata. Anything else means either a file stopped shipping or a
guard started skipping for a new reason. `test_the_sdist_ships_every_file_the_docs_promise`
guards the first half automatically.

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
| `package` | `python -m build`, **run the suite from the extracted sdist**, install the wheel, run the console script and the shim |
| `lint` | `ruff check` + `compileall` |
| `docker` | build the image (no push) with layer caching |

`.github/workflows/docker.yml` publishes multi-arch images to `ghcr.io` on pushes
to `main` and on `v*` tags.

The `package` job is a regression guard, not a formality: flat-layout
auto-discovery used to fail outright because the root holds both `cloudflare/`
and `gemini_web2api/`, so the project could not be built at all.

### Expected skips

The `test` job prints a `test-suite` **annotation** carrying `exit=`, `skipped=`
and `node=`. It exists because a green checkmark is not evidence on its own: a
guard that silently skips is indistinguishable from one that ran, and job logs
are not always reachable when you need to check. `CIWorkflowTests` fails if that
audit is removed.

The baseline is **`skipped=0`** on Python 3.11+ and **`skipped=7`** on 3.8 — the
seven `PyprojectTests`/`VersionConsistencyTests` cases that read
`pyproject.toml` with `tomllib`, which is 3.11+. Those are benign: the file they
check is the same on every leg, and the 3.11–3.13 jobs do run them. Anything
*other* than those seven on the 3.8 leg, or any skip at all elsewhere, means a
guard has stopped guarding — most likely because `node` went missing, which the
annotation reports alongside.

The `test-without-httpx` job legitimately skips more (every incremental-streaming
test needs `httpx`), which is the point of that job rather than a gap in it.

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
