"""Metrics, the health-probe script, and dashboard rendering.

These modules have no upstream interaction, so they are tested directly. The
dashboard tests focus on the injection-escaping contract, because the dashboard
interpolates live server state into an inline <script> block.
"""
import json
import os
import unittest
from unittest import mock

from gemini_web2api import _healthcheck, metrics, webui


class MetricsTests(unittest.TestCase):
    def setUp(self):
        metrics.reset()
        self.addCleanup(metrics.reset)

    def test_reset_clears_everything(self):
        metrics.inc("requests", 5)
        metrics.record_status(500)
        metrics.record_latency("m", 1.0)
        metrics.reset()
        snap = metrics.snapshot()
        self.assertEqual(snap["counters"]["requests"], 0)
        self.assertEqual(snap["status_codes"], {})
        self.assertEqual(snap["latency_ms_samples"], 0)
        self.assertEqual(snap["models"], {})

    def test_unknown_counter_names_are_ignored(self):
        """A typo at a call site must not raise or invent a counter."""
        metrics.inc("not_a_real_counter")
        self.assertNotIn("not_a_real_counter", metrics.snapshot()["counters"])

    def test_inc_accumulates(self):
        metrics.inc("completions")
        metrics.inc("completions", 4)
        self.assertEqual(metrics.snapshot()["counters"]["completions"], 5)

    def test_status_codes_bucket_into_4xx_and_5xx(self):
        for status in (200, 200, 400, 404, 429, 500, 502):
            metrics.record_status(status)
        snap = metrics.snapshot()
        self.assertEqual(snap["counters"]["errors_4xx"], 3)
        self.assertEqual(snap["counters"]["errors_5xx"], 2)
        self.assertEqual(snap["status_codes"]["200"], 2)
        self.assertEqual(snap["status_codes"]["502"], 1)

    def test_2xx_and_3xx_are_not_errors(self):
        for status in (200, 204, 301, 304):
            metrics.record_status(status)
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["errors_4xx"], 0)
        self.assertEqual(counters["errors_5xx"], 0)

    def test_latency_histogram_places_samples_in_buckets(self):
        metrics.record_latency("m", 0.010)   # 10ms   -> <=50
        metrics.record_latency("m", 0.200)   # 200ms  -> <=250
        metrics.record_latency("m", 90.0)    # 90s    -> inf
        hist = metrics.snapshot()["latency_histogram_ms"]
        self.assertEqual(hist["50"], 1)
        self.assertEqual(hist["250"], 1)
        self.assertEqual(hist["inf"], 1)
        # Exactly one bucket per sample.
        self.assertEqual(sum(hist.values()), 3)

    def test_bucket_boundaries_are_inclusive(self):
        """50ms must land in the 50 bucket, not the next one."""
        metrics.record_latency("m", 0.050)
        self.assertEqual(metrics.snapshot()["latency_histogram_ms"]["50"], 1)

    def test_negative_latency_is_clamped_not_crashing(self):
        metrics.record_latency("m", -5.0)
        snap = metrics.snapshot()
        self.assertEqual(snap["latency_ms_samples"], 1)
        self.assertEqual(snap["latency_histogram_ms"]["50"], 1)
        self.assertGreaterEqual(snap["latency_ms_avg"], 0.0)

    def test_average_latency(self):
        metrics.record_latency("m", 0.100)
        metrics.record_latency("m", 0.300)
        self.assertEqual(metrics.snapshot()["latency_ms_avg"], 200.0)

    def test_average_is_zero_with_no_samples(self):
        self.assertEqual(metrics.snapshot()["latency_ms_avg"], 0.0)

    def test_per_model_breakdown(self):
        metrics.record_latency("gemini-3.6-flash", 0.100)
        metrics.record_latency("gemini-3.6-flash", 0.300)
        metrics.record_latency("gemini-3.1-pro", 0.200)
        models = metrics.snapshot()["models"]
        self.assertEqual(models["gemini-3.6-flash"]["requests"], 2)
        self.assertEqual(models["gemini-3.6-flash"]["avg_ms"], 200.0)
        self.assertEqual(models["gemini-3.6-flash"]["max_ms"], 300.0)
        self.assertEqual(models["gemini-3.1-pro"]["requests"], 1)

    def test_models_are_sorted_by_request_count(self):
        """The dashboard shows the busiest model first."""
        metrics.record_latency("quiet", 0.1)
        for _ in range(3):
            metrics.record_latency("busy", 0.1)
        self.assertEqual(list(metrics.snapshot()["models"]), ["busy", "quiet"])

    def test_empty_model_name_is_labelled(self):
        metrics.record_latency("", 0.1)
        metrics.record_latency(None, 0.1)
        self.assertEqual(metrics.snapshot()["models"]["unknown"]["requests"], 2)

    def test_snapshot_is_json_serialisable(self):
        metrics.inc("requests")
        metrics.record_status(200)
        metrics.record_latency("m", 0.5)
        json.dumps(metrics.snapshot())  # must not raise

    def test_snapshot_is_a_copy(self):
        """Mutating the result must not corrupt internal state."""
        metrics.inc("requests")
        snap = metrics.snapshot()
        snap["counters"]["requests"] = 999
        snap["counters"]["injected"] = True
        self.assertEqual(metrics.snapshot()["counters"]["requests"], 1)
        self.assertNotIn("injected", metrics.snapshot()["counters"])

    def test_uptime_is_non_negative_and_grows(self):
        first = metrics.uptime()
        self.assertGreaterEqual(first, 0.0)
        self.assertGreaterEqual(metrics.uptime(), first)

    def test_counters_are_thread_safe(self):
        """The lock must make concurrent increments exact."""
        import threading

        def worker():
            for _ in range(500):
                metrics.inc("requests")
                metrics.record_status(200)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        snap = metrics.snapshot()
        self.assertEqual(snap["counters"]["requests"], 4000)
        self.assertEqual(snap["status_codes"]["200"], 4000)


class HistoryTests(unittest.TestCase):
    """The bounded request-history ring behind the dashboard Activity tab."""

    def setUp(self):
        metrics.reset()
        self.addCleanup(metrics.reset)

    def _entry(self, **overrides):
        entry = {
            "ts": 1700000000.0, "id": "abc123", "method": "POST",
            "path": "/v1/chat/completions", "status": 200,
            "model": "gemini-3.6-flash", "ms": 12.5, "client": "127.0.0.1",
        }
        entry.update(overrides)
        return entry

    def test_empty_by_default(self):
        self.assertEqual(metrics.history(), [])

    def test_records_and_returns_newest_first(self):
        metrics.record_request(self._entry(id="one"))
        metrics.record_request(self._entry(id="two"))
        got = metrics.history()
        self.assertEqual([e["id"] for e in got], ["two", "one"])

    def test_limit_is_honoured(self):
        for i in range(10):
            metrics.record_request(self._entry(id=f"r{i}"))
        self.assertEqual(len(metrics.history(limit=3)), 3)
        self.assertEqual(len(metrics.history(limit=0)), 0)

    def test_ring_is_bounded_at_the_cap(self):
        """A long-running server must not grow history without limit."""
        over = metrics.HISTORY_CAP + 250
        for i in range(over):
            metrics.record_request(self._entry(id=f"r{i}"))
        got = metrics.history(limit=metrics.HISTORY_CAP + 500)
        self.assertEqual(len(got), metrics.HISTORY_CAP)
        # Oldest entries were dropped; newest survived.
        self.assertEqual(got[0]["id"], f"r{over - 1}")
        self.assertEqual(got[-1]["id"], f"r{over - metrics.HISTORY_CAP}")

    def test_non_dict_entries_are_ignored(self):
        """A buggy call site must not corrupt the ring."""
        metrics.record_request(None)
        metrics.record_request("nope")
        metrics.record_request(self._entry())
        self.assertEqual(len(metrics.history()), 1)

    def test_history_is_returned_as_copies(self):
        """Callers must not be able to mutate retained state."""
        metrics.record_request(self._entry())
        first = metrics.history()
        first[0]["path"] = "/tampered"
        self.assertEqual(metrics.history()[0]["path"], "/v1/chat/completions")

    def test_reset_clears_history(self):
        metrics.record_request(self._entry())
        metrics.reset()
        self.assertEqual(metrics.history(), [])

    def test_history_is_not_in_the_snapshot(self):
        """snapshot() feeds the public dashboard state, which must not leak
        client addresses. History is served only from auth-gated /status."""
        metrics.record_request(self._entry(client="203.0.113.9"))
        self.assertNotIn("history", metrics.snapshot())
        snap = json.dumps(metrics.snapshot())
        self.assertNotIn("203.0.113.9", snap)


class HealthCheckTests(unittest.TestCase):
    def test_default_url_uses_standard_port(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_healthcheck.default_url(), "http://127.0.0.1:8081/health")

    def test_default_url_honours_gemini_port(self):
        with mock.patch.dict(os.environ, {"GEMINI_WEB2API_PORT": "9000"}, clear=True):
            self.assertEqual(_healthcheck.default_url(), "http://127.0.0.1:9000/health")

    def test_default_url_falls_back_to_port(self):
        """Container platforms commonly inject a bare PORT."""
        with mock.patch.dict(os.environ, {"PORT": "7000"}, clear=True):
            self.assertEqual(_healthcheck.default_url(), "http://127.0.0.1:7000/health")

    def test_gemini_port_wins_over_port(self):
        env = {"GEMINI_WEB2API_PORT": "9000", "PORT": "7000"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(_healthcheck.default_url(), "http://127.0.0.1:9000/health")

    def test_default_url_honours_host_override(self):
        with mock.patch.dict(
            os.environ, {"GEMINI_WEB2API_HEALTH_HOST": "0.0.0.0"}, clear=True
        ):
            self.assertEqual(_healthcheck.default_url(), "http://0.0.0.0:8081/health")

    def test_probe_true_on_2xx(self):
        response = mock.MagicMock(status=200)
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            self.assertTrue(_healthcheck.probe("http://x/health", 1.0))

    def test_probe_false_on_5xx(self):
        response = mock.MagicMock(status=503)
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            self.assertFalse(_healthcheck.probe("http://x/health", 1.0))

    def test_probe_false_on_connection_error(self):
        """A dead server must not raise out of the probe."""
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertFalse(_healthcheck.probe("http://x/health", 1.0))

    def test_probe_false_on_timeout(self):
        import socket

        with mock.patch("urllib.request.urlopen", side_effect=socket.timeout()):
            self.assertFalse(_healthcheck.probe("http://x/health", 1.0))

    def test_probe_false_on_invalid_url(self):
        with mock.patch("urllib.request.urlopen", side_effect=ValueError("bad url")):
            self.assertFalse(_healthcheck.probe("not-a-url", 1.0))

    def test_main_returns_zero_when_healthy(self):
        with mock.patch.object(_healthcheck, "probe", return_value=True):
            self.assertEqual(_healthcheck.main([]), 0)

    def test_main_returns_one_when_unhealthy(self):
        with mock.patch.object(_healthcheck, "probe", return_value=False):
            self.assertEqual(_healthcheck.main([]), 1)

    def test_main_accepts_explicit_url_and_timeout(self):
        with mock.patch.object(_healthcheck, "probe", return_value=True) as probe:
            _healthcheck.main(["--url", "http://other:1234/healthz", "--timeout", "2.5"])
        probe.assert_called_once_with("http://other:1234/healthz", 2.5)

    def test_main_passes_the_timeout_through(self):
        with mock.patch.object(_healthcheck, "probe", return_value=True) as probe:
            _healthcheck.main(["--timeout", "9"])
        self.assertEqual(probe.call_args[0][1], 9.0)

    def test_no_package_imports(self):
        """The probe must work even if the application package is broken."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "gemini_web2api",
            "_healthcheck.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("from .", source)
        self.assertNotIn("from gemini_web2api", source)
        self.assertNotIn("import gemini_web2api", source)


def _dashboard_script():
    """The inline <script> of a rendered dashboard."""
    import re

    state = {
        "version": "1.2.0", "uptime_sec": 1.0,
        "base_url": "http://localhost:8081/v1", "streaming": "httpx",
        "api_keys": "1 configured", "cookie": "anonymous",
        "default_model": "gemini-3.6-flash", "gemini_bl": "boq_x",
        "proxy": None, "rate_limit": "disabled", "temporary_chats": False,
        "requests_served": 0, "auth_enabled": True, "history_enabled": True,
        "python": "3.11.2", "warnings": [], "models": [], "endpoints": [],
    }
    html = webui.render_dashboard(state).decode("utf-8")
    return re.findall(r"<script>(.*?)</script>", html, re.DOTALL)[0]


def _pure_render_functions(script):
    """Extract ``esc`` through ``renderMd``: the DOM-free part of the console.

    These are the functions that turn attacker-influenced model output into HTML
    assigned to ``innerHTML``. They reference neither ``document`` nor
    ``localStorage``, which is what makes them runnable under bare Node.
    """
    start = script.index("const esc = ")
    fn = script.index("function renderMd(src){", start)
    depth = 0
    i = script.index("{", fn)
    while True:
        if script[i] == "{":
            depth += 1
        elif script[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return script[start:i + 1]


class DashboardScriptTests(unittest.TestCase):
    """Execute the console's rendering functions under Node.

    The dashboard is several hundred lines of inline JavaScript that no Python
    test can exercise, and ``node --check`` only proves it parses. Model output
    is attacker-influenced content that ends up in ``innerHTML``, so the
    escaping contract is worth running rather than reading. Skipped when Node is
    absent, matching the Worker's syntax guard.

    The assertions live in ``tests/dashboard_render_assertions.js`` and are
    concatenated *after* the extracted functions, so both share one scope —
    Node's ``eval`` would not leak ``const`` bindings into the caller.
    """

    ASSERTIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "dashboard_render_assertions.js")

    def setUp(self):
        import shutil

        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("node is not installed")
        self.chunk = _pure_render_functions(_dashboard_script())

    def _run_node(self, text, check_only=False):
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(text)
            path = handle.name
        self.addCleanup(os.unlink, path)
        argv = [self.node] + (["--check", path] if check_only else [path])
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)

    def test_extraction_is_dom_free(self):
        """Guards the extraction itself. If it ever pulled in DOM calls the run
        below would fail for reasons unrelated to escaping."""
        self.assertIn("const esc = ", self.chunk)
        self.assertIn("function renderMd", self.chunk)
        self.assertNotIn("document.", self.chunk)
        self.assertNotIn("localStorage", self.chunk)

    def test_extracted_chunk_parses(self):
        result = self._run_node(self.chunk, check_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_escaping_survives_hostile_model_output(self):
        with open(self.ASSERTIONS, encoding="utf-8") as handle:
            assertions = handle.read()
        result = self._run_node(self.chunk + "\n" + assertions)
        self.assertEqual(
            result.returncode, 0,
            f"dashboard rendering assertions failed:\n{result.stdout}{result.stderr}")


class DashboardTests(unittest.TestCase):
    def _state(self, **overrides):
        state = {
            "version": "1.2.0",
            "status": "ok",
            "ready": True,
            "model_count": 9,
            "streaming": "httpx",
            "checks": {"fatal": [], "warnings": []},
        }
        state.update(overrides)
        return state

    def test_renders_bytes(self):
        html = webui.render_dashboard(self._state())
        self.assertIsInstance(html, bytes)

    def test_renders_a_complete_document(self):
        html = webui.render_dashboard(self._state()).decode("utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("</html>", html)

    def test_state_token_is_replaced(self):
        html = webui.render_dashboard(self._state()).decode("utf-8")
        self.assertNotIn("@@STATE@@", html)

    def test_state_is_embedded_and_recoverable(self):
        html = webui.render_dashboard(self._state(version="9.9.9")).decode("utf-8")
        self.assertIn("9.9.9", html)

    def test_no_external_resources(self):
        """The dashboard must work air-gapped: no CDN, font or script fetches."""
        import re

        html = webui.render_dashboard(self._state()).decode("utf-8")
        self.assertEqual(re.findall(r"<script[^>]+src=", html), [])
        self.assertNotIn("@import", html)
        external = [
            url
            for url in re.findall(r"https?://[^\s\"')]+", html)
            if "w3.org" not in url and "localhost" not in url and "127.0.0.1" not in url
        ]
        self.assertEqual(external, [], f"dashboard references external URLs: {external}")

    def test_script_breakout_is_escaped(self):
        """A </script> inside interpolated state must not close the block."""
        html = webui.render_dashboard(
            self._state(version='</script><script>alert(1)</script>')
        ).decode("utf-8")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("<\\/script>", html)

    def test_line_separators_are_escaped(self):
        """U+2028/U+2029 are legal JSON but terminate JS string literals."""
        html = webui.render_dashboard(
            self._state(version="a\u2028b\u2029c")
        ).decode("utf-8")
        self.assertNotIn("\u2028", html)
        self.assertNotIn("\u2029", html)
        self.assertIn("\\u2028", html)
        self.assertIn("\\u2029", html)

    def test_non_ascii_survives(self):
        """ensure_ascii=False keeps model descriptions readable in the source."""
        html = webui.render_dashboard(self._state(status="正常")).decode("utf-8")
        self.assertIn("正常", html)

    def test_non_serialisable_values_do_not_crash(self):
        """default=str keeps an unexpected object from taking down GET /."""
        html = webui.render_dashboard(self._state(weird=object())).decode("utf-8")
        self.assertIn("object object", html.replace("<", " ").replace(">", " "))

    def test_chat_tab_is_present(self):
        """The dashboard is a console, not just a status board."""
        html = webui.render_dashboard(self._state(models=[])).decode("utf-8")
        for marker in ('data-tab="chat"', "gw2a.convs", "v1/chat/completions"):
            self.assertIn(marker, html, f"missing dashboard marker {marker}")

    def test_activity_tab_is_present(self):
        html = webui.render_dashboard(self._state(models=[])).decode("utf-8")
        self.assertIn('data-tab="activity"', html)

    def test_auth_flag_reaches_the_page(self):
        """With auth on, the UI must tell the user a key is required."""
        html = webui.render_dashboard(
            self._state(models=[], auth_enabled=True, history_enabled=True)
        ).decode("utf-8")
        self.assertIn('"auth_enabled": true', html)
        self.assertIn('"history_enabled": true', html)

    def test_history_disabled_flag_reaches_the_page(self):
        html = webui.render_dashboard(
            self._state(models=[], auth_enabled=False, history_enabled=False)
        ).decode("utf-8")
        self.assertIn('"history_enabled": false', html)

    def test_api_key_is_never_embedded_in_the_page(self):
        """The page is served from the public "/", so state carries facts, not
        secrets - and no key material either."""
        html = webui.render_dashboard(
            self._state(models=[], api_keys="1 configured", auth_enabled=True)
        ).decode("utf-8")
        self.assertNotIn("sk-demo-key", html)
        self.assertNotIn("xsrf_token", html)

    def test_warnings_are_rendered(self):
        state = self._state(checks={"fatal": [], "warnings": ["auth is disabled"]})
        html = webui.render_dashboard(state).decode("utf-8")
        self.assertIn("auth is disabled", html)


if __name__ == "__main__":
    unittest.main()
