"""A pool of Google accounts, so one rate-limited cookie is not fatal.

A single cookie means a single account, and Google rate-limits accounts. When
that happens the request fails, and every request after it fails the same way
until the limit expires — the deployment is down even though the operator may
have several accounts available. The Cloudflare Worker has rotated cookies for
this reason; this brings the same behaviour to the Python server.

The pool holds one :class:`Credential` per configured cookie file and hands them
out round-robin. When an account comes back rate-limited or rejected, it is put
on a cooldown and the next request picks a different one.

Three deliberate choices:

* **Rotation engages only with more than one credential.** With a single cookie
  the pool always returns it, never cools it down, and the request path is
  byte-for-byte what it was before — a deployment with one account cannot be
  changed by adding a feature it does not use. Retrying a *rate-limited* single
  account is not obviously useful, but it is the behaviour that already shipped,
  and silently changing it would be a surprise in the other direction.
* **A cooldown is time-based, not permanent.** A rate limit expires; a
  credential that is never retried means an account that came back is never
  noticed. After the cooldown the credential is a candidate again.
* **A rejected credential cools down longer than a rate-limited one.** `401` from
  Google means the cookie is stale or wrong, which does not fix itself in a
  minute; retrying it on every request would waste a call each time.
"""

import threading
import time

#: How long a rate-limited credential sits out, unless configured otherwise.
DEFAULT_COOLDOWN_SEC = 60

#: How long a credential Google *rejected* sits out. Longer than a rate limit,
#: because an expired cookie does not recover on its own — only re-running the
#: cookie export does — and a request spent on it is a request wasted.
REJECTED_COOLDOWN_SEC = 900

#: Statuses that mean "this account", not "this request". Anything else (a 500,
#: a timeout, a DNS failure) is the service being unwell, and rotating accounts
#: for it would burn every credential in turn for one outage.
ACCOUNT_STATUSES = (429, 401, 403)


class Credential:
    """One account: the cookies that identify it, and its health."""

    __slots__ = ("cookie", "sapisid", "auth_user", "xsrf_token", "source",
                 "cooldown_until", "uses", "last_error")

    def __init__(self, cookie, sapisid=None, auth_user=None, xsrf_token=None,
                 source=""):
        self.cookie = cookie or ""
        self.sapisid = sapisid
        # Per-account rather than global: the exported auth files each carry
        # their own `auth_user`, and sending account A's index with account B's
        # cookies addresses the wrong account entirely.
        self.auth_user = auth_user
        self.xsrf_token = xsrf_token
        self.source = source or ("cookie_file" if cookie else "")
        self.cooldown_until = 0.0
        self.uses = 0
        self.last_error = None

    @property
    def usable(self):
        """Whether this credential can be sent right now."""
        return bool(self.cookie) and self.cooldown_until <= time.time()

    def cooldown_remaining(self):
        return max(0.0, self.cooldown_until - time.time())

    def cool(self, seconds, error=None):
        self.cooldown_until = time.time() + max(0.0, seconds)
        self.last_error = error

    def clear_cooldown(self):
        self.cooldown_until = 0.0
        self.last_error = None

    def label(self):
        """A safe identifier for logs — never the cookie itself."""
        return self.source or "credential"


class CredentialPool:
    """A round-robin pool of credentials with cooldowns.

    Thread-safe: the server is threaded, so two requests must not be handed the
    same credential by racing, and a cooldown recorded by one request must be
    visible to the others immediately.
    """

    def __init__(self, credentials=None, cooldown_sec=DEFAULT_COOLDOWN_SEC):
        self._lock = threading.Lock()
        self._credentials = list(credentials or [])
        self._next = 0
        self.cooldown_sec = cooldown_sec

    def __len__(self):
        return len(self._credentials)

    @property
    def rotating(self):
        """Whether failover can do anything.

        One credential has nothing to rotate to, and treating a lone cookie as a
        pool would change retry behaviour for deployments that never asked for
        this feature.
        """
        return len(self._credentials) > 1

    def credentials(self):
        with self._lock:
            return list(self._credentials)

    def replace(self, credentials, cooldown_sec=None):
        """Swap the whole pool, e.g. after a cookie file changed on disk.

        Cooldowns live on the credentials themselves, so replacing the pool
        discards them — a re-read means the operator changed something, and
        carrying a cooldown onto a different cookie would be wrong.
        """
        with self._lock:
            self._credentials = list(credentials or [])
            self._next = 0
            if cooldown_sec is not None:
                self.cooldown_sec = cooldown_sec

    def acquire(self):
        """Return the next usable credential, or ``None`` when none can serve.

        No ``exclude`` parameter, though one was written first. A failed
        credential is *cooled* by the report methods below, so it is already
        unusable, and a returned credential always advances the round robin — so
        the account that just failed cannot be handed back anyway. Injecting
        `acquire()` in place of `acquire(exclude=...)` left every rotation test
        passing, which is what proved the branch unreachable. An unreachable
        branch is not a safety net; it is somewhere for a future bug to hide.
        """
        with self._lock:
            pool = self._credentials
            if not pool:
                return None
            if not self.rotating:
                # Single credential: returned unconditionally, exactly as the
                # pre-pool code did. No cooldown, no skip. There is nothing to
                # rotate to, so skipping it would turn a retry that might
                # succeed into an immediate failure.
                return pool[0]
            for offset in range(len(pool)):
                index = (self._next + offset) % len(pool)
                candidate = pool[index]
                if candidate.usable:
                    self._next = (index + 1) % len(pool)
                    return candidate
            return None

    def report_success(self, credential):
        if credential is None:
            return
        with self._lock:
            credential.uses += 1
            credential.clear_cooldown()

    def report_rate_limited(self, credential, error=None):
        """Cool a credential after the upstream refused to serve it."""
        if credential is None:
            return
        with self._lock:
            if not self.rotating:
                # Nothing to fall back to; cooling it would only turn a retry
                # that might succeed into an immediate failure.
                return
            credential.cool(self.cooldown_sec, error)

    def report_rejected(self, credential, error=None):
        """Cool a credential Google refused outright, for much longer."""
        if credential is None:
            return
        with self._lock:
            if not self.rotating:
                return
            credential.cool(REJECTED_COOLDOWN_SEC, error)

    def report_status(self, credential, status, error=None):
        """Route a failed status to the right cooldown, and say if it was one.

        False means "no account was set aside", which the caller reads as "keep
        the existing retry behaviour". With a single credential that is the
        honest answer: nothing was cooldowned, so nothing was rotated.
        """
        if not self.rotating:
            return False
        if status == 429:
            self.report_rate_limited(credential, error)
            return True
        if status in (401, 403):
            self.report_rejected(credential, error)
            return True
        return False

    def available(self):
        """How many credentials could serve a request right now."""
        with self._lock:
            return sum(1 for c in self._credentials if c.usable)

    def snapshot(self):
        """Per-credential state for ``/status`` — never the cookie itself.

        The dashboard shows this, and it is reachable by anyone who can read
        ``/status``, so it carries only what an operator needs to see which
        account is being used and which is resting.
        """
        with self._lock:
            return {
                "size": len(self._credentials),
                "rotating": self.rotating,
                "available": sum(1 for c in self._credentials if c.usable),
                "cooldown_sec": self.cooldown_sec,
                "entries": [
                    {
                        "source": c.label(),
                        "has_sapisid": bool(c.sapisid),
                        "auth_user": c.auth_user,
                        "usable": c.usable,
                        "cooldown_remaining_sec": round(c.cooldown_remaining(), 1),
                        "uses": c.uses,
                        "last_error": c.last_error,
                    }
                    for c in self._credentials
                ],
            }
