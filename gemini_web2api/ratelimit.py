"""Fixed-window rate limiter.

Ports the mechanism the Cloudflare Worker already had to the Python server.
Disabled unless ``rate_limit_max`` is positive, so the default behaviour is
unchanged for existing deployments.

A fixed window (rather than a sliding log) is used because it is O(1) in memory
regardless of traffic volume: one integer per key per window. Expired keys are
swept opportunistically so a long-lived process does not accumulate entries for
clients that never come back.
"""
import math
import threading
import time


class RateLimiter:
    """Per-key fixed-window counter."""

    # Sweep at most this often, and always when the table grows past the limit.
    _SWEEP_INTERVAL_SEC = 30.0
    _MAX_KEYS = 10000

    def __init__(self, max_requests=0, window_sec=60):
        self._lock = threading.Lock()
        self._windows = {}
        self._max = int(max_requests or 0)
        self._window = float(window_sec or 60)
        self._last_sweep = time.time()

    def configure(self, max_requests, window_sec):
        """Update limits at runtime (config reload)."""
        with self._lock:
            self._max = int(max_requests or 0)
            self._window = float(window_sec or 60)
            if self._max <= 0:
                self._windows.clear()

    @property
    def enabled(self):
        return self._max > 0

    def check(self, key):
        """Record a hit for ``key``.

        Returns ``(allowed, retry_after_seconds, remaining, reset_seconds)``.
        ``retry_after`` is only positive when the hit was refused; ``reset``
        is how long until the current window ends either way, which is what
        the ``X-RateLimit-Reset-Requests`` response header reports. When the
        limiter is disabled it always allows and reports ``remaining`` and
        ``reset`` as ``None``.
        """
        with self._lock:
            if self._max <= 0:
                return True, 0, None, None
            now = time.time()
            if now - self._last_sweep > self._SWEEP_INTERVAL_SEC or len(self._windows) > self._MAX_KEYS:
                self._sweep(now)
                self._last_sweep = now

            window_start, count = self._windows.get(key, (now, 0))
            if now - window_start >= self._window:
                window_start, count = now, 0
            count += 1
            self._windows[key] = (window_start, count)
            # ceil, not int()+1: on the first hit of a window the elapsed time
            # is exactly 0.0, and int(60 - 0.0) + 1 reports 61 seconds for a
            # 60-second window.
            reset = max(1, math.ceil(self._window - (now - window_start)))

            if count > self._max:
                return False, reset, 0, reset
            return True, 0, self._max - count, reset

    def _sweep(self, now):
        stale = [k for k, (start, _) in self._windows.items() if now - start >= self._window]
        for key in stale:
            del self._windows[key]

    def snapshot(self):
        """Current limiter state for the status endpoint."""
        with self._lock:
            return {
                "enabled": self._max > 0,
                "max_requests": self._max,
                "window_sec": self._window,
                "tracked_keys": len(self._windows),
            }


# Process-wide instance; the server configures it from CONFIG at startup.
LIMITER = RateLimiter()
