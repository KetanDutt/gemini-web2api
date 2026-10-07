# Contributing

Thanks for considering a change to gemini-web2api.

This document is about **how to contribute** — what a good issue and pull
request look like here, and which project-specific rules will cause CI to reject
an otherwise reasonable change. For the technical how-to (repository layout,
adding a model, adding an endpoint, changing the wire protocol, releasing), see
[DEVELOPMENT.md](DEVELOPMENT.md).

---

## Filing an issue

Please include:

1. **The version** — `gemini-web2api --version`, or the `version` field from
   `/health`.
2. **A redacted `/status` response** — `curl -s http://localhost:8081/status`.
   It already redacts secrets, but check before pasting: it includes your
   configured host, port and model names.
3. **Server logs with the `X-Request-Id`** — every response carries one, and
   every log line echoes it. An ID ties your client-side symptom to the exact
   server-side cause, which is usually the difference between a fast fix and a
   guessing game.
4. **How you deployed it** — bare Python, Docker, Docker Compose, systemd or the
   Cloudflare Worker. These are genuinely different code paths — see
   [Project-specific invariants](#project-specific-invariants) below.

For "it worked, then stopped", read [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
first. The overwhelmingly common cause is Google rolling their frontend, which
invalidates the `bl` build tag and shows up as **HTTP 405**; `auto_update_bl`
normally heals this on its own.

**Never paste a cookie, an XSRF token, or an API key into an issue.** Delete the
issue and rotate the credential if you do — a cookie grants access to your Google
account.

---

## Pull requests

### What CI enforces

A PR is green when all of these hold:

| Check | Command |
|---|---|
| Tests pass on Python 3.8, 3.11, 3.12, 3.13 | `python -m unittest discover -s tests -t .` |
| Tests pass with **no third-party packages** | same, in a venv without `httpx` |
| Lint | `ruff check gemini_web2api tests scripts` |
| Byte-compile | `python -m compileall -q gemini_web2api tests scripts` |
| Package builds, installs, and the console script runs | `python -m build` |
| Docker image builds | `docker build .` |

Run the **no-httpx** configuration locally before pushing. It is the one people
forget, and it catches code that quietly assumes `httpx` is present:

```bash
python -m venv /tmp/nohttpx
/tmp/nohttpx/bin/python -m unittest discover -s tests -t .
```

Eleven streaming tests skip there by design. Everything else must pass.

### The test-count rule

**This trips up every contributor, including the maintainers.**

`README.md`, `README_CN.md` and `docs/CHANGELOG.md` each state the number of
tests in the suite, and a test asserts all three match the number that actually
runs. Add or remove a test and you must update all three in the same commit, or
CI fails with something like:

```
AssertionError: 484 != 486 : README.md claims 484 tests but the suite has 486
```

The count is deliberate: a README that advertises a test total nobody verifies
is exactly the kind of stale claim this project spent a release cleaning up.

### Python 3.8 is the floor, and it is checked by AST

The project supports Python 3.8. That is enforced by parsing the source with
`ast` and rejecting newer syntax in annotation position — not by trusting
`compile()`, which succeeds under a newer interpreter and proves nothing.

So these will fail the build even though they work on your machine:

```python
def f(items: list[str]) -> dict[str, int]: ...   # PEP 585, needs 3.9+
def f(x: int | None): ...                        # PEP 604, needs 3.10+
s.removeprefix("/")                              # needs 3.9+
d1 | d2                                          # dict union, needs 3.9+
```

Use `typing.List`, `typing.Optional`, `s[len(p):] if s.startswith(p) else s`,
and `{**d1, **d2}`.

### Guard tests are part of the design

A large share of the suite does not test behaviour — it asserts that the
repository has not drifted. These exist because the 1.2.0 audit found
documentation that was confidently wrong, two divergent copies of the same
server, and a package that could not be built at all.

If you change something a guard watches, **fix the guard's subject, not the
guard**. Guards currently cover: the shim stays a shim, `config.example.json`
matches `DEFAULT_CONFIG`, every config key is documented, every documented model
exists and every real model is documented, every docs page is reachable from the
index and the README, API.md documents no unrouted endpoint, no secret file is
tracked by git, the Cloudflare Worker's model table matches Python's,
`start.bat` keeps CRLF line endings, and CI lints every Python directory.

The full list with rationale is in
[DEVELOPMENT.md — What is asserted](DEVELOPMENT.md#what-is-asserted).

**If you add a guard, verify it can fail.** Inject the violation it forbids,
watch it go red, then restore. A guard that cannot fail is not a guard, and
several here were written, proved, and only then kept.

---

## Project-specific invariants

These are easy to break accidentally and expensive to debug afterwards.

**Never let a test's pass condition be "the network is unreachable."** The suite
is fully offline; the Gemini wire protocol is faked at the frame level. A test
that mocks DNS resolution but leaves `urlopen` live will pass in CI and tell you
nothing. Worse, `fetch_image_bytes` swallows exceptions into `b""`, so a guard
that *raises* into it is not a guard at all — record the attempt and assert
afterwards.

**Patch before you send.** `mock.patch(...)` entered *after* issuing a request
races the server thread, which then reaches the real upstream. Install mocks
first.

**The dashboard must work air-gapped.** No CDN, no web fonts, no external
`src`/`href`/`@import`. Only `w3.org` (the SVG namespace in the favicon data
URI) and localhost addresses are permitted, and a test enforces it.

**The dashboard interpolates live server state into an inline `<script>`.**
`render_dashboard` escapes `</`, U+2028 and U+2029 for this reason. If you add
state, keep it JSON-serialisable and remember the page is served from the
**unauthenticated** `GET /` — so it may carry facts, but never secrets, client
addresses, or request history.

**History stores operational facts only.** Method, query-stripped path, status,
resolved model, latency, request id, client address. Never a prompt, a response
body, or a credential. Query strings are stripped precisely because
Google-native clients pass `?key=<api_key>`.

**HTTP/1.1 framing is load-bearing.** The request body is drained in `do_POST`
*before* auth and routing, so a rejected request cannot leave unread bytes on a
keep-alive connection. `_start_sse()` sends `Connection: close` and sets
`self.close_connection = True`, because an SSE body has no `Content-Length`.
Malformed `Content-Length` and chunk sizes produce 400, not 500. Don't undo any
of this while refactoring.

**Config/auth ordering.** Anything reading `auth_user`, `xsrf_token` or
`gemini_bl` must run *after* `load_cookie()`, because a `gemini-auth.json`
export supplies them. And `apply_defaults()` must never write a key the user set
explicitly.

**The Worker is a separate implementation.** `cloudflare/worker.js` is not
generated from the Python code and the two diverge in *both* directions: the
Worker adds multi-cookie and fingerprint rotation, while lacking image input
(silently discarded), `/v1/completions`, and the `/ready` and `/status` probes.
`cloudflare/README.MD`'s model table tracks the Worker, not Python — do not
"fix" it to match. Known gaps are declared in `WorkerParityTests.KNOWN_GAPS`;
read [its divergence table](../cloudflare/README.MD) before touching either side.

**Keep `start.bat` in CRLF.** `cmd.exe` finds `goto` labels by scanning for
CR-terminated lines. See
[DEVELOPMENT.md — Editing start.bat](DEVELOPMENT.md#editing-startbat).

---

## Style

- Ruff is the linter; its config is in `pyproject.toml`. `ruff check --fix`
  resolves almost everything.
- No new required dependencies. `httpx` is optional and the server must run
  without it — that is a tested property, not an aspiration.
- Comments explain **why**, especially where the reason is a bug that was fixed
  or an invariant that is easy to break. The codebase is written that way
  deliberately; match it.
- Documentation lives in `docs/`. A new page must be linked from
  `docs/README.md` and from the root `README.md`, or CI fails.

---

## Security reports

Please do **not** open a public issue for a security vulnerability. Report it
privately to the repository owner via GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability),
or by the contact details in the repository profile.

Read [SECURITY.md](SECURITY.md) first for the threat model and what is protected
by default. Note that the server ships **open by default** — no API keys — which
is a documented design choice for local use, not an oversight. If you believe
you have found a flaw in that reasoning, that is exactly the kind of report
worth sending privately.

---

## License

By contributing, you agree your contributions will be licensed under the
project's [MIT License](../LICENSE).
