"""Prometheus text exposition for the in-process metrics.

A view module, like ``webui.py``: ``metrics.py`` holds the data, this turns it
into one particular representation, and the server decides who may read it.
Taking the snapshot as an argument rather than reaching into ``metrics`` keeps
the renderer pure, so it can be tested against crafted inputs — including inputs
this process would never produce.

Two details of the format are easy to get wrong and are handled here:

* **Histogram buckets are cumulative in Prometheus and are not in
  ``metrics.py``.** ``record_latency`` increments exactly one bucket per
  observation, so ``_latency[0]`` is "the count at or below 50ms". Prometheus
  reads ``le="0.05"`` as "the count at or below 50ms" *inclusive of every
  smaller bucket*, so the series has to be accumulated as it is written. Emitting
  the raw values would understate every bucket except the last.
* **Durations are seconds by convention.** The internal histograms are in
  milliseconds; exposing them unchanged would produce a metric named
  ``_seconds`` whose values are a thousand times too large.

Label values are escaped, not interpolated. Model names reach this module from
``metrics``' own keys, but a label value that is not escaped can carry a newline
and forge an entire extra metric line in the exposition, so the escaping is what
makes that impossible rather than merely unlikely.
"""

#: Prefix for every metric exported, so several services can share a scrape.
PREFIX = "gemini_web2api"

#: ``Content-Type`` a Prometheus scraper expects. The version suffix selects the
#: text format; without it some scrapers fall back to the OpenMetrics parser and
#: reject a payload that is otherwise fine.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Help text for each counter in ``metrics._counters``. Kept here rather than in
#: ``metrics.py`` because it is exposition metadata, not data. A counter with no
#: entry is still exported — the test suite asserts the two lists agree, so a new
#: counter cannot be added without its description.
COUNTER_HELP = {
    "requests": "HTTP requests handled, including rejected ones.",
    "completions": "Non-streaming chat completions answered.",
    "streams": "Streaming responses opened.",
    "errors_4xx": "Responses with a 4xx status.",
    "errors_5xx": "Responses with a 5xx status.",
    "upstream_failures": "Upstream calls that raised, streaming included.",
    "rate_limited": "Requests rejected by the built-in rate limiter.",
    "images_uploaded": "Images uploaded to Gemini's own upload endpoint.",
    "tool_calls_parsed": "Tool calls parsed out of model replies.",
    "json_mode_failures": "Replies that did not satisfy the requested response_format.",
}

#: Names of the numeric fields in ``metrics``' latency histogram, in order.
_BUCKET_LABEL = "le"


def render(state):
    """Render a ``metrics.snapshot()`` dict as Prometheus text exposition."""
    lines = []
    _gauge(lines, "uptime_seconds", "Seconds since the process started.",
           _number(state.get("uptime_sec", 0)))

    counters = state.get("counters") or {}
    for name in sorted(counters):
        _counter(lines, name + "_total",
                 COUNTER_HELP.get(name, "Counter reported by the service."),
                 _number(counters[name]))

    statuses = state.get("status_codes") or {}
    if statuses:
        lines.append(f"# HELP {PREFIX}_http_responses_total Responses by status code.")
        lines.append(f"# TYPE {PREFIX}_http_responses_total counter")
        for status in sorted(statuses, key=_status_sort_key):
            lines.append(
                f"{PREFIX}_http_responses_total"
                f'{{status="{_escape(status)}"}} {_number(statuses[status])}')

    _histogram(lines, state)
    _models(lines, state)

    # Prometheus requires LF endings and a trailing newline.
    return "\n".join(lines) + "\n"


def _histogram(lines, state):
    """The global latency histogram, in seconds as Prometheus expects."""
    histogram = state.get("latency_histogram_ms") or {}
    samples = int(state.get("latency_ms_samples") or 0)
    total_ms = state.get("latency_ms_avg", 0.0) * samples

    name = f"{PREFIX}_request_duration_seconds"
    lines.append(f"# HELP {name} Upstream round-trip time for chat requests.")
    lines.append(f"# TYPE {name} histogram")

    # Accumulate as we go: the source counts are per-bucket, the format is
    # cumulative. `inf` and any non-numeric key are skipped here and handled
    # below, so a malformed key cannot corrupt the running total.
    running = 0
    for key, count in _ordered_buckets(histogram):
        running += int(count or 0)
        seconds = int(key) / 1000.0
        lines.append(f'{name}_bucket{{{_BUCKET_LABEL}="{_bound(seconds)}"}} {running}')
    # The +Inf bucket is the total, and is always emitted even with no samples:
    # a histogram without one is invalid, and Prometheus drops the whole family.
    running = max(running, samples)
    lines.append(f'{name}_bucket{{{_BUCKET_LABEL}="+Inf"}} {running}')
    lines.append(f"{name}_sum {_number(total_ms / 1000.0)}")
    lines.append(f"{name}_count {_number(samples)}")


def _models(lines, state):
    """Per-model request counts and latency.

    Sum and count are exported rather than an average, because Prometheus
    computes rates and averages from those two and cannot recover them from an
    average. The histogram above is deliberately global rather than per-model:
    the source keeps one set of buckets, and naming a per-model series that did
    not exist would misrepresent the data.
    """
    models = state.get("models") or {}
    if not models:
        return
    name = f"{PREFIX}_model_requests_total"
    lines.append(f"# HELP {name} Upstream requests by model.")
    lines.append(f"# TYPE {name} counter")
    for model in sorted(models):
        count = int((models[model] or {}).get("requests") or 0)
        lines.append(f'{name}{{model="{_escape(model)}"}} {_number(count)}')

    lines.append(f"# HELP {PREFIX}_model_request_duration_seconds_sum "
                 "Total upstream time by model, in seconds.")
    lines.append(f"# TYPE {PREFIX}_model_request_duration_seconds_sum counter")
    lines.append(f"# HELP {PREFIX}_model_request_duration_seconds_max "
                 "Slowest upstream request seen by model, in seconds.")
    lines.append(f"# TYPE {PREFIX}_model_request_duration_seconds_max gauge")
    for model in sorted(models):
        entry = models[model] or {}
        label = f'{{model="{_escape(model)}"}}'
        seconds = (entry.get("avg_ms") or 0.0) * (entry.get("requests") or 0) / 1000.0
        lines.append(f"{PREFIX}_model_request_duration_seconds_sum{label} "
                     f"{_number(seconds)}")
        lines.append(f"{PREFIX}_model_request_duration_seconds_max{label} "
                     f"{_number((entry.get('max_ms') or 0.0) / 1000.0)}")


def _ordered_buckets(histogram):
    """Yield ``(numeric key, count)`` numerically ascending, ignoring ``inf``."""
    numeric = []
    for key, count in histogram.items():
        try:
            numeric.append((int(key), count))
        except (TypeError, ValueError):
            # `inf` and anything unrecognised: the +Inf bucket is written from
            # the sample count instead.
            continue
    numeric.sort(key=lambda pair: pair[0])
    return numeric


def _bound(seconds):
    """Format a bucket bound the way Prometheus writes floats.

    ``50 / 1000.0`` is ``0.05``, but ``int / 1000.0`` also produces values like
    ``0.3`` as ``0.30000000000000004``. Trimming to the shortest representation
    that round-trips keeps the label stable across scrapes — a changing label
    creates a new time series, which silently breaks rate() and alerts.
    """
    text = repr(round(seconds, 9))
    return text[:-2] if text.endswith(".0") else text


def _status_sort_key(status):
    """Sort status codes numerically, so 200 precedes 1000 and 404 precedes 500."""
    try:
        return (0, int(status))
    except (TypeError, ValueError):
        return (1, 0)


def _counter(lines, name, help_text, value):
    lines.append(f"# HELP {PREFIX}_{name} {help_text}")
    lines.append(f"# TYPE {PREFIX}_{name} counter")
    lines.append(f"{PREFIX}_{name} {value}")


def _gauge(lines, name, help_text, value):
    lines.append(f"# HELP {PREFIX}_{name} {help_text}")
    lines.append(f"# TYPE {PREFIX}_{name} gauge")
    lines.append(f"{PREFIX}_{name} {value}")


def _escape(value):
    """Escape a label value: backslash, double quote and newline.

    A raw newline in a label would end the sample line and let whatever follows
    be parsed as further metrics, so this is the difference between a label and
    an injection.
    """
    return (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n"))


def _number(value):
    """Format a number without Python's ``repr`` artefacts.

    ``True`` is rendered as ``1`` rather than ``True``: Prometheus parses
    neither Python bools nor the strings ``nan``/``inf`` in every version, so a
    stray bool in the state would otherwise produce an unparseable sample.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    try:
        return repr(round(float(value), 6))
    except (TypeError, ValueError):
        return "0"
