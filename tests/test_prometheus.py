"""Tests for the Prometheus `/metrics` endpoint.

The point of an exposition endpoint is that *another program* can read it, so
these tests parse the output with a real parser rather than searching it with
regexes. A grep for `gemini_web2api_requests_total 3` would pass on output that
Prometheus itself rejects, and the failure would surface in production as an
empty dashboard rather than a red build.

The parser here is deliberately strict about the things Prometheus is strict
about: every sample line must be `name{labels} value`, every value must parse as
a float, labels must be quoted and escaped, and a histogram's buckets must be
cumulative and end at `+Inf`.
"""

import re
import time
import unittest
from unittest import mock

from gemini_web2api import metrics, prometheus

from .support import ServerTestCase

SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
                    r"(?P<labels>\{.*\})?"
                    r" (?P<value>.+)$")


class PrometheusParser:
    """Just enough of the text format to validate an exposition."""

    def __init__(self, text):
        self.lines = text.split("\n")
        self.samples = []          # (name, {label: value}, float value)
        self.types = {}            # metric name -> declared type
        self.helps = {}            # metric name -> help text
        self.parse()

    def parse(self):
        for number, line in enumerate(self.lines, start=1):
            if line == "":
                continue
            if line.startswith("#"):
                self._parse_comment(line, number)
                continue
            self._parse_sample(line, number)

    def _parse_comment(self, line, number):
        match = re.match(r"^# (HELP|TYPE) (\S+)(?: (.*))?$", line)
        if not match:
            raise AssertionError(f"line {number}: malformed comment {line!r}")
        keyword, name, rest = match.group(1), match.group(2), match.group(3)
        if keyword == "HELP":
            if not rest:
                raise AssertionError(f"line {number}: HELP without text")
            self.helps[name] = rest
        else:
            if rest not in ("counter", "gauge", "histogram", "summary", "untyped"):
                raise AssertionError(f"line {number}: unknown type {rest!r}")
            self.types[name] = rest

    def _parse_sample(self, line, number):
        match = SAMPLE.match(line)
        if not match:
            raise AssertionError(
                f"line {number}: not a valid sample line: {line!r}")
        labels = {}
        if match.group("labels"):
            labels = self._parse_labels(match.group("labels"), line, number)
        try:
            value = float(match.group("value"))
        except ValueError as exc:
            raise AssertionError(
                f"line {number}: value is not a number: "
                f"{match.group('value')!r}") from exc
        self.samples.append((match.group("name"), labels, value))

    def _parse_labels(self, text, line, number):
        body = text[1:-1]
        labels = {}
        # A simple scanner rather than a regex, so a mis-escaped quote shows up
        # as a malformed label instead of being silently tolerated.
        index = 0
        while index < len(body):
            while index < len(body) and body[index] in " ,":
                index += 1
            if index >= len(body):
                break
            key_match = re.match(r"[a-zA-Z_][a-zA-Z0-9_]*", body[index:])
            if not key_match:
                raise AssertionError(f"line {number}: bad label name in {text!r}")
            key = key_match.group(0)
            index += len(key)
            if index >= len(body) or body[index] != "=":
                raise AssertionError(f"line {number}: label {key} has no '='")
            index += 1
            if index >= len(body) or body[index] != '"':
                raise AssertionError(f"line {number}: label {key} value not quoted")
            index += 1
            chars = []
            while index < len(body):
                char = body[index]
                if char == "\\":
                    if index + 1 >= len(body):
                        raise AssertionError(f"line {number}: trailing backslash")
                    following = body[index + 1]
                    chars.append({"n": "\n", "\\": "\\", '"': '"'}.get(following, following))
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                chars.append(char)
                index += 1
            else:
                raise AssertionError(f"line {number}: unterminated label value")
            labels[key] = "".join(chars)
        return labels

    def values(self, name):
        return [value for sample_name, _labels, value in self.samples
                if sample_name == name]

    def by_label(self, name, key):
        return {labels[key]: value for sample_name, labels, value in self.samples
                if sample_name == name and key in labels}


class PrometheusFormatTests(unittest.TestCase):
    """The renderer, against crafted snapshots."""

    def render(self, **state):
        base = {"uptime_sec": 12.5, "counters": {"requests": 4},
                "status_codes": {}, "models": {}, "latency_histogram_ms": {},
                "latency_ms_samples": 0, "latency_ms_avg": 0.0}
        base.update(state)
        return prometheus.render(base)

    def test_the_output_parses(self):
        parsed = PrometheusParser(self.render())
        self.assertTrue(parsed.samples)

    def test_a_counter_is_named_with_the_total_suffix(self):
        """Prometheus convention, and what `rate()` expects.

        Exposing a counter without `_total` makes Grafana's counter detection
        mislabel it and misleads anyone writing a query by hand.
        """
        parsed = PrometheusParser(self.render())
        self.assertEqual(parsed.values("gemini_web2api_requests_total"), [4.0])
        self.assertEqual(parsed.types["gemini_web2api_requests_total"], "counter")

    def test_every_sample_has_help_and_type(self):
        """A series without TYPE is untyped, which silently disables `rate()`."""
        parsed = PrometheusParser(self.render())
        for name, _labels, _value in parsed.samples:
            base = name.replace("_bucket", "").replace("_sum", "").replace("_count", "")
            declared = name if name in parsed.types else base
            self.assertIn(declared, parsed.types,
                          f"{name} has no TYPE declaration")
            self.assertIn(declared, parsed.helps,
                          f"{name} has no HELP declaration")

    def test_the_declared_help_covers_every_real_counter(self):
        """"Counter without a description" must not be reachable.

        The renderer falls back to generic help rather than raising, so that a
        counter added in a hurry cannot break the endpoint — which means this
        test is the only thing that notices the description is missing.
        """
        self.assertEqual(
            sorted(prometheus.COUNTER_HELP), sorted(metrics.snapshot()["counters"]),
            "a counter exists without help text, or help text exists for a "
            "counter that does not")

    def test_a_counter_is_exported_even_when_zero(self):
        """A series that appears only once it is non-zero breaks `rate()`.

        Grafana and alert rules treat a missing series as "no data", which is
        indistinguishable from "the counter was reset".
        """
        text = self.render(counters={"requests": 0, "streams": 0})
        parsed = PrometheusParser(text)
        self.assertIn("gemini_web2api_requests_total", parsed.helps)
        self.assertEqual(parsed.values("gemini_web2api_requests_total"), [0.0])

    # ─── the histogram, which is the part that is easy to get wrong ──────────

    def test_buckets_are_converted_from_counts_to_cumulative(self):
        """The source counts per-bucket; Prometheus reads `le` as cumulative.

        This snapshot says "one observation in the 250ms bucket" and nothing
        else. Prometheus must therefore be told that one observation is at or
        below 250ms, 500ms, ... — not 1 only in the 250ms bucket and 0 in the
        larger ones. Emitting the raw values understates every bucket except
        the last, and the resulting percentiles are nonsense in a way nobody
        notices until an alert fires late.
        """
        state = {"latency_histogram_ms": {"50": 0, "100": 0, "250": 1, "500": 0,
                                          "inf": 0},
                 "latency_ms_samples": 1, "latency_ms_avg": 200.0}
        parsed = PrometheusParser(self.render(**state))
        buckets = parsed.by_label("gemini_web2api_request_duration_seconds_bucket", "le")
        self.assertEqual(buckets["0.05"], 0, "nothing at or below 50ms")
        self.assertEqual(buckets["0.1"], 0)
        self.assertEqual(buckets["0.25"], 1, "the observation is at or below 250ms")
        self.assertEqual(buckets["0.5"], 1, "and therefore at or below 500ms too")
        self.assertEqual(buckets["+Inf"], 1)

    def test_buckets_never_decrease_and_end_at_the_sample_count(self):
        """The invariant Prometheus enforces: `le` must be monotonic.

        Read in the order they are written, because that is how a scraper reads
        them: the bounds must ascend and the counts must never fall, or
        `histogram_quantile` returns values outside the observed range.
        """
        state = {"latency_histogram_ms": {"50": 2, "100": 3, "250": 0, "500": 5,
                                          "inf": 1},
                 "latency_ms_samples": 11, "latency_ms_avg": 400.0}
        parsed = PrometheusParser(self.render(**state))
        series = [(labels["le"], value) for name, labels, value in parsed.samples
                  if name == "gemini_web2api_request_duration_seconds_bucket"]
        self.assertTrue(series)
        self.assertEqual(series[-1][0], "+Inf",
                         "the +Inf bucket must be written last")
        self.assertEqual(series[-1][1], 11.0,
                         "the +Inf bucket must be the sample count")

        finite = [(float(bound), value) for bound, value in series[:-1]]
        self.assertEqual([b for b, _v in finite], sorted(b for b, _v in finite),
                         "bucket bounds must ascend")
        counts = [v for _b, v in finite]
        self.assertEqual(counts, sorted(counts),
                         "cumulative bucket counts must never decrease")
        self.assertLessEqual(counts[-1], series[-1][1],
                             "no finite bucket may exceed the total")

    def test_the_plus_inf_bucket_is_always_present(self):
        """A histogram without +Inf is invalid, so Prometheus drops the family."""
        parsed = PrometheusParser(self.render())
        buckets = parsed.by_label("gemini_web2api_request_duration_seconds_bucket", "le")
        self.assertIn("+Inf", buckets)

    def test_a_histogram_with_no_samples_still_parses(self):
        """Before the first request the endpoint must still be scrapeable."""
        parsed = PrometheusParser(self.render())
        self.assertEqual(parsed.values("gemini_web2api_request_duration_seconds_count"),
                         [0.0])
        self.assertEqual(parsed.values("gemini_web2api_request_duration_seconds_sum"),
                         [0.0])

    def test_durations_are_seconds_not_milliseconds(self):
        """A metric named `_seconds` carrying milliseconds is off by 1000×.

        That is not a subtle bug: it silently rescales every dashboard and every
        alert threshold, and the name actively asserts it is correct.

        Driven through the real `metrics` module rather than a hand-built state,
        so the bucket list is the one the server actually produces — a crafted
        dict could agree with the renderer while both disagreed with reality.
        """
        metrics.reset()
        self.addCleanup(metrics.reset)
        metrics.record_latency("gemini-3.6-flash", 1.5)
        parsed = PrometheusParser(prometheus.render(metrics.snapshot()))

        self.assertEqual(
            parsed.values("gemini_web2api_request_duration_seconds_sum"), [1.5],
            "1500ms must be exported as 1.5 seconds")
        self.assertEqual(
            parsed.values("gemini_web2api_request_duration_seconds_count"), [1.0])
        buckets = parsed.by_label("gemini_web2api_request_duration_seconds_bucket", "le")
        self.assertEqual(buckets["1"], 0, "1500ms is not within one second")
        self.assertEqual(buckets["2.5"], 1, "1500ms is within 2.5 seconds")
        self.assertEqual(buckets["+Inf"], 1)

    def test_a_real_latency_lands_in_exactly_one_bucket_boundary(self):
        """The boundary itself must count as inside, not outside.

        `record_latency` bumps the first bucket whose bound the value does not
        exceed, so exactly 100ms belongs to `le="0.1"`. Off-by-one here moves
        every measurement at a boundary into the next bucket, which shifts every
        percentile at the values an operator most cares about.
        """
        metrics.reset()
        self.addCleanup(metrics.reset)
        metrics.record_latency("m", 0.1)
        parsed = PrometheusParser(prometheus.render(metrics.snapshot()))
        buckets = parsed.by_label("gemini_web2api_request_duration_seconds_bucket", "le")
        self.assertEqual(buckets["0.05"], 0)
        self.assertEqual(buckets["0.1"], 1, "exactly 100ms is within 100ms")

    def test_a_hostile_model_name_cannot_forge_a_metric_line(self):
        """Label values are attacker-influenced in principle, so they are escaped.

        A model name containing a newline and a quote would otherwise close the
        label and emit an extra sample line — letting anyone who can influence a
        model name invent metrics, which is both a false-alarm and a
        misconfiguration vector. The parser used here would reject the result,
        so this also proves the payload is structurally intact.
        """
        hostile = 'a"\n gemini_web2api_injected 1\n# "b'
        state = {"models": {hostile: {"requests": 1, "avg_ms": 0.0, "max_ms": 0.0}}}
        parsed = PrometheusParser(self.render(**state))
        names = {name for name, _labels, _value in parsed.samples}
        self.assertNotIn("gemini_web2api_injected", names)
        self.assertEqual(
            parsed.values("gemini_web2api_model_requests_total"), [1.0])
        model_values = parsed.by_label("gemini_web2api_model_requests_total", "model")
        self.assertIn(hostile, model_values,
                      "the name must survive as one label value, not be dropped")

    def test_output_is_deterministic(self):
        """Random ordering would create new series and destroy time series.

        Prometheus identifies a series by name and labels, not by position, so
        ordering does not break correctness — but a *changing* order makes the
        output impossible to diff, which is how this gets reviewed.
        """
        state = {"status_codes": {"500": 1, "200": 2, "404": 1},
                 "models": {"b": {"requests": 1}, "a": {"requests": 2}}}
        self.assertEqual(self.render(**state), self.render(**state))

    def test_status_codes_are_sorted_numerically(self):
        """Lexicographic sorting puts 1000 before 200 and reads as a bug."""
        parsed = PrometheusParser(self.render(status_codes={"500": 1, "200": 2}))
        names = [name for name, _labels, _value in parsed.samples
                 if name == "gemini_web2api_http_responses_total"]
        self.assertEqual(len(names), 2)
        order = [labels["status"] for name, labels, _value in parsed.samples
                 if name == "gemini_web2api_http_responses_total"]
        self.assertEqual(order, ["200", "500"])

    def test_malformed_state_does_not_raise(self):
        """`/metrics` must never be the endpoint that takes the server down.

        The values come from counters this module controls, but a 0.0.4 payload
        that raises would turn a monitoring scrape into a 500, and monitoring is
        exactly what is needed when something else is already wrong.
        """
        for state in ({"counters": {"requests": True}},
                      {"counters": {"requests": "7"}},
                      {"uptime_sec": None},
                      {"latency_histogram_ms": {"not-a-number": 3, "50": 1}},
                      {"latency_ms_samples": 5, "latency_ms_avg": 0.0},
                      {"models": None},
                      {"status_codes": {"weird": 1}}):
            with self.subTest(state=state):
                text = self.render(**state)
                if text.strip():
                    PrometheusParser(text)


class PrometheusEndpointTests(ServerTestCase):
    """The route, over real HTTP."""

    def test_metrics_is_served_with_the_scraper_content_type(self):
        status, headers, text = self.get("/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), prometheus.CONTENT_TYPE)
        self.assertEqual(int(headers["Content-Length"]), len(text.encode("utf-8")))
        PrometheusParser(text)

    def test_metrics_is_not_public_when_keys_are_configured(self):
        """Gate parity with `/status`, which exists for the same reason.

        Traffic volume, model mix and error rate are reconnaissance: they say
        what a deployment is for and when it is struggling. Prometheus takes
        credentials in its scrape config, so this costs a scraper nothing.
        """
        self.CONFIG["api_keys"] = ["sk-test"]
        try:
            self.assertEqual(self.get("/metrics")[0], 401)
            self.assertEqual(
                self.get("/metrics", headers={"Authorization": "Bearer nope"})[0], 401)
            self.assertEqual(
                self.get("/metrics", headers={"Authorization": "Bearer sk-test"})[0], 200)
        finally:
            self.CONFIG["api_keys"] = []

    def _settled_request_count(self, timeout=3.0):
        """The request counter, once no further increments are arriving.

        Counters are incremented by `_record()` in a `finally`, *after* the
        response body has been flushed, so a request made by an earlier test can
        still land while this one is reading. An exact `+1` assertion therefore
        cannot hold, and the first version of this test failed about one run in
        twenty on the shared server. Settling first and then asserting a strict
        increase guards the decision that matters — that `/metrics` is *not*
        exempt from counting itself — without depending on timing.

        Bounded, so a counter that stops moving still returns rather than
        hanging until the CI timeout.
        """
        deadline = time.monotonic() + timeout
        value = metrics.snapshot()["counters"]["requests"]
        stable = 0
        while time.monotonic() < deadline and stable < 3:
            time.sleep(0.01)
            current = metrics.snapshot()["counters"]["requests"]
            stable = stable + 1 if current == value else 0
            value = current
        return value

    def test_metrics_records_its_own_request_after_answering(self):
        """The endpoint counts as a request like any other.

        Counting it is the honest choice: excluding it would make `/metrics`
        lie about the very traffic it reports.

        Polled rather than read once, because `_record()` runs in a `finally`
        *after* the response body is flushed: the client can have read a
        complete response before the server thread has counted it. Reading once
        made this test fail about one run in twenty. Same race, and same fix, as
        `RequestHistoryTests._history_waiting_for`.
        """
        before = self._settled_request_count()
        self.get("/metrics")
        deadline = time.monotonic() + 3.0
        after = metrics.snapshot()["counters"]["requests"]
        while after <= before and time.monotonic() < deadline:
            time.sleep(0.01)
            after = metrics.snapshot()["counters"]["requests"]
        self.assertGreater(after, before,
                           "the endpoint did not count itself")

    def test_the_reading_reflects_a_completed_request(self):
        """End-to-end: traffic shows up in the exposition, not just in /status."""
        before = metrics.snapshot()["counters"]["completions"]
        with mock.patch("gemini_web2api.server.generate", return_value="a reply"):
            status, _headers, _body = self.post_json(
                "/v1/chat/completions",
                {"model": "gemini-3.6-flash",
                 "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 200)
        _status, _headers, text = self.get("/metrics")
        parsed = PrometheusParser(text)
        self.assertEqual(parsed.values("gemini_web2api_completions_total"),
                         [float(before + 1)])

    def test_metrics_never_consults_the_upstream(self):
        """A monitoring endpoint that needs the network is not monitoring."""
        with mock.patch("gemini_web2api.gemini.generate") as generate:
            self.get("/metrics")
        generate.assert_not_called()

    def test_history_is_not_exposed(self):
        """`/metrics` is a metrics endpoint, not a second /status.

        Requests are recorded with their path, model and client; exporting them
        as labels would turn this into an unbounded-cardinality series per
        request and leak request details to a scraper.
        """
        _status, _headers, text = self.get("/metrics")
        self.assertNotIn("history", text)
        self.assertNotIn("127.0.0.1", text)


if __name__ == "__main__":
    unittest.main()
