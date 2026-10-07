"""In-process request metrics.

Deliberately tiny: counters plus a coarse latency histogram, guarded by one
lock. There is no persistence and no external dependency — the numbers exist so
the dashboard and ``/health`` can answer "is this thing working, and how fast",
and so an operator tailing logs can correlate a request ID with an outcome.

For a multi-process deployment these are per-process. That is intentional;
aggregating them would require a shared store this project does not have.
"""
import threading
import time
from collections import deque

_START = time.time()

# Upper bounds (ms) for the latency histogram; the final bucket is "+inf".
_LATENCY_BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000)

# Hard ceiling on the retained request history. The number of entries actually
# returned is min(history_max, this), so a misconfigured history_max cannot make
# the buffer grow without bound in a long-lived process.
HISTORY_CAP = 1000

_lock = threading.Lock()
_counters = {
    "requests": 0,
    "completions": 0,
    "streams": 0,
    "errors_4xx": 0,
    "errors_5xx": 0,
    "upstream_failures": 0,
    "rate_limited": 0,
    "images_uploaded": 0,
    "tool_calls_parsed": 0,
    # Requests where the client asked for JSON and the model did not comply.
    # Separate from upstream_failures because the upstream answered fine — it
    # just answered prose, which only a client with a parser cares about.
    "json_mode_failures": 0,
}
_latency = [0] * (len(_LATENCY_BUCKETS) + 1)
_latency_sum = [0.0]
_latency_count = [0]
_by_model = {}
_by_status = {}
_history = deque(maxlen=HISTORY_CAP)


def inc(name, amount=1):
    """Increment a counter, ignoring unknown names."""
    with _lock:
        if name in _counters:
            _counters[name] += amount


def record_status(status):
    """Track a response status code and the matching error bucket."""
    with _lock:
        key = str(status)
        _by_status[key] = _by_status.get(key, 0) + 1
        if 400 <= status < 500:
            _counters["errors_4xx"] += 1
        elif status >= 500:
            _counters["errors_5xx"] += 1


def record_latency(model, seconds):
    """Record an upstream round trip for ``model``."""
    millis = max(0.0, seconds * 1000.0)
    with _lock:
        for index, bound in enumerate(_LATENCY_BUCKETS):
            if millis <= bound:
                _latency[index] += 1
                break
        else:
            _latency[-1] += 1
        _latency_sum[0] += millis
        _latency_count[0] += 1
        entry = _by_model.setdefault(model or "unknown", {"count": 0, "ms_total": 0.0, "ms_max": 0.0})
        entry["count"] += 1
        entry["ms_total"] += millis
        entry["ms_max"] = max(entry["ms_max"], millis)


def record_request(entry):
    """Append one entry to the bounded request history.

    Entries hold operational facts only — method, path, status, model, latency,
    request id, client. Never a prompt, a response body or a credential: this
    buffer is reachable through ``/status`` and the dashboard, so anything
    sensitive stored here would become readable by anyone who can reach them.
    Callers must also strip query strings, which can carry ``?key=<api_key>``.

    Anything that is not a dict is dropped rather than stored: this buffer is
    serialised straight to JSON for a UI, so one malformed entry would break
    the whole response.
    """
    if not isinstance(entry, dict):
        return
    with _lock:
        _history.append(dict(entry))


def history(limit=100):
    """The most recent entries, newest first.

    ``limit`` is a maximum, not a page size: ``limit <= 0`` returns nothing,
    so a caller passing a disabled/zero budget can never receive the full
    buffer by accident.
    """
    if not limit or limit <= 0:
        return []
    with _lock:
        items = list(_history)[-limit:]
    items.reverse()
    # Copies, so a caller (or a JSON encoder hook) cannot mutate retained state.
    return [dict(item) for item in items]


def uptime():
    """Seconds since the module was imported (i.e. since process start)."""
    return time.time() - _START


def snapshot():
    """A JSON-safe copy of every metric."""
    with _lock:
        average = (_latency_sum[0] / _latency_count[0]) if _latency_count[0] else 0.0
        models = {
            name: {
                "requests": entry["count"],
                "avg_ms": round(entry["ms_total"] / entry["count"], 1) if entry["count"] else 0.0,
                "max_ms": round(entry["ms_max"], 1),
            }
            for name, entry in sorted(_by_model.items(), key=lambda kv: -kv[1]["count"])
        }
        return {
            "uptime_sec": round(uptime(), 1),
            "counters": dict(_counters),
            "status_codes": dict(sorted(_by_status.items())),
            "latency_ms_avg": round(average, 1),
            "latency_ms_samples": _latency_count[0],
            "latency_histogram_ms": {
                **{str(bound): _latency[index] for index, bound in enumerate(_LATENCY_BUCKETS)},
                "inf": _latency[-1],
            },
            "models": models,
        }


def reset():
    """Clear all metrics. Intended for tests."""
    global _START
    with _lock:
        _START = time.time()
        for key in _counters:
            _counters[key] = 0
        for index in range(len(_latency)):
            _latency[index] = 0
        _latency_sum[0] = 0.0
        _latency_count[0] = 0
        _by_model.clear()
        _by_status.clear()
        _history.clear()
