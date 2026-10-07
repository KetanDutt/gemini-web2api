"""Tests for multi-account cookie rotation.

Two layers again, for the same reason as JSON mode: the pool's own logic is
unit-tested against crafted credentials, and the wiring is tested through the
real `generate()` with the transport stubbed.

The property that matters most is not "rotation works" but **"it cannot make a
single-cookie deployment behave differently"** — the pool is a change to the
request path of a server people are already running, and the failure that would
hurt most is silent: a request served by the wrong account, or retries quietly
disabled. Several tests below exist only to pin that.

Cookie values never appear in any test assertion output by accident: the pool's
`snapshot()` is asserted to contain no cookie material at all.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from gemini_web2api import gemini
from gemini_web2api.credentials import DEFAULT_COOLDOWN_SEC, REJECTED_COOLDOWN_SEC, Credential, CredentialPool

from .support import ConfigTestCase, ServerTestCase

# Two recognisable cookie strings, so a leak is obvious in a failing assertion.
COOKIE_A = "SID=aaa; SAPISID=sapisidAAA"
COOKIE_B = "SID=bbb; SAPISID=sapisidBBB"


def auth_file(cookie, sapisid, auth_user=None, xsrf=None):
    payload = {"cookie": cookie, "sapisid": sapisid}
    if auth_user is not None:
        payload["auth_user"] = auth_user
    if xsrf is not None:
        payload["xsrf_token"] = xsrf
    return json.dumps(payload)


class CredentialPoolUnitTests(unittest.TestCase):
    def test_a_single_credential_is_always_returned(self):
        """Not just "usually": a lone cookie must never be skipped.

        There is nothing to rotate to, so skipping it on a cooldown would turn
        a retry that might succeed into an immediate failure.
        """
        pool = CredentialPool([Credential(COOKIE_A, "sa", source="a")])
        self.assertFalse(pool.rotating)
        for _ in range(5):
            self.assertIs(pool.acquire(), pool.credentials()[0])

    def test_a_single_credential_is_never_cooled_down(self):
        pool = CredentialPool([Credential(COOKIE_A, "sa", source="a")])
        credential = pool.acquire()
        pool.report_rate_limited(credential, "429")
        self.assertTrue(credential.usable)
        self.assertIs(pool.acquire(), credential)
        self.assertFalse(pool.report_status(credential, 429),
                         "with nothing to rotate to, the caller must keep its "
                         "existing retry behaviour rather than skipping the sleep")

    def test_a_lone_credential_is_returned_even_if_it_were_cooled(self):
        """The single-cookie guarantee, stated at the point that enforces it.

        `report_status` already declines to cool a non-rotating pool, so this
        cannot be reached through the normal path — the cooling here is done
        behind the pool's back to prove the *second* half of the guarantee: with
        nothing to rotate to, `acquire()` returns the credential it has rather
        than `None`. A future change that let a lone credential be cooled would
        otherwise silently turn every request into "all accounts are resting".
        """
        credential = Credential("a", source="a")
        pool = CredentialPool([credential], cooldown_sec=60)
        credential.cool(60, "429")
        self.assertFalse(credential.usable)
        self.assertIs(pool.acquire(), credential)

    def test_an_empty_pool_returns_nothing(self):
        """No cookie file configured is the anonymous path, not a rotation."""
        pool = CredentialPool([])
        self.assertFalse(pool.rotating)
        self.assertIsNone(pool.acquire())
        self.assertEqual(pool.available(), 0)

    def test_rotation_is_round_robin(self):
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b"),
                               Credential("c", source="c")])
        self.assertTrue(pool.rotating)
        seen = [pool.acquire().source for _ in range(6)]
        self.assertEqual(seen, ["a", "b", "c", "a", "b", "c"])

    def test_a_rate_limited_credential_is_skipped(self):
        """The point of the feature: a throttled account is not reused."""
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b")], cooldown_sec=60)
        first = pool.acquire()
        pool.report_rate_limited(first, "429")
        self.assertTrue(pool.report_status(first, 429))
        second = pool.acquire()
        self.assertIsNot(second, first)
        self.assertEqual(second.source, "b")
        self.assertEqual(pool.available(), 1)

    def test_a_credential_comes_back_after_its_cooldown(self):
        """Otherwise a marked account is dead for the life of the process, and
        an account that recovered is never noticed."""
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b")], cooldown_sec=60)
        first = pool.acquire()
        pool.report_rate_limited(first, "429")
        self.assertFalse(first.usable)
        # Pretend the cooldown elapsed, rather than sleeping for it.
        first.cooldown_until = 0.0
        self.assertTrue(first.usable)
        self.assertEqual(pool.available(), 2)
        self.assertIn(first.source, [c.source for c in pool.credentials()])

    def test_a_rejected_credential_cools_longer_than_a_throttled_one(self):
        """A stale cookie does not fix itself in a minute.

        Treating 401 like 429 would spend a real upstream call on a cookie that
        cannot work, on every request, for the whole cooldown.
        """
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b")])
        throttled = pool.acquire()
        pool.report_rate_limited(throttled)
        rejected = pool.acquire()
        pool.report_rejected(rejected)
        self.assertGreater(rejected.cooldown_remaining(),
                           throttled.cooldown_remaining())
        self.assertLessEqual(throttled.cooldown_remaining(), DEFAULT_COOLDOWN_SEC + 1)
        self.assertLessEqual(rejected.cooldown_remaining(), REJECTED_COOLDOWN_SEC + 1)

    def test_a_transport_failure_does_not_rotate(self):
        """A 500 or a timeout is the service being unwell, not the account.

        Rotating for it would burn every credential in turn during one outage
        and then report the last account as the broken one.
        """
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b")])
        credential = pool.acquire()
        for status in (500, 502, 504, 405, None):
            with self.subTest(status=status):
                self.assertFalse(pool.report_status(credential, status, "boom"))
        self.assertEqual(pool.available(), 2)

    def test_all_cooling_yields_nothing_rather_than_a_stale_one(self):
        """Returning a cooling credential would defeat the cooldown entirely."""
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b")])
        for credential in pool.credentials():
            pool.report_rate_limited(credential)
        self.assertIsNone(pool.acquire())
        self.assertEqual(pool.available(), 0)

    def test_snapshot_never_contains_cookie_material(self):
        """`/status` is readable by anyone with a key, and the dashboard renders
        it in a browser. A cookie in there would be a credential disclosure."""
        pool = CredentialPool([Credential(COOKIE_A, "sapisidAAA", auth_user="1",
                                          source="/data/a.json"),
                               Credential(COOKIE_B, "sapisidBBB",
                                          source="/data/b.json")])
        blob = json.dumps(pool.snapshot())
        for secret in (COOKIE_A, COOKIE_B, "sapisidAAA", "sapisidBBB",
                       "aaa", "bbb"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob)
        self.assertIn("/data/a.json", blob, "the source is what makes it readable")
        self.assertEqual(pool.snapshot()["size"], 2)
        self.assertTrue(pool.snapshot()["rotating"])

    def test_snapshot_reports_cooldown_state(self):
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b")])
        pool.report_rate_limited(pool.acquire(), "429")
        snapshot = pool.snapshot()
        self.assertEqual(snapshot["available"], 1)
        self.assertEqual(snapshot["cooldown_sec"], DEFAULT_COOLDOWN_SEC)
        resting = [e for e in snapshot["entries"] if not e["usable"]]
        self.assertEqual(len(resting), 1)
        self.assertGreater(resting[0]["cooldown_remaining_sec"], 0)
        self.assertEqual(resting[0]["last_error"], "429")

    def test_concurrent_acquire_does_not_hand_out_one_credential_twice(self):
        """The server is threaded, so the pool is the shared mutable state.

        Two threads racing in `acquire()` must not both be told "use account A"
        — that would send a burst to one account while another sat idle, which
        is the opposite of what a pool is for.
        """
        import threading
        pool = CredentialPool([Credential("a", source="a"),
                               Credential("b", source="b"),
                               Credential("c", source="c")])
        results = []
        lock = threading.Lock()

        def worker():
            for _ in range(50):
                credential = pool.acquire()
                with lock:
                    results.append(credential.source)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 400)
        # Perfectly even would be 400/3; what matters is that no account was
        # starved, which a lost update would cause.
        for source in "abc":
            self.assertGreater(results.count(source), 60,
                               f"account {source} was starved: {results.count(source)}")


class CookieFilePoolTests(ConfigTestCase):
    """Building the pool from files on disk."""

    def setUp(self):
        super().setUp()
        gemini.reset_cookie_cache()
        self.addCleanup(gemini.reset_cookie_cache)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_no_cookie_file_means_an_empty_pool(self):
        self.assertEqual(gemini.load_credentials(), [])
        self.assertEqual(gemini.load_cookie(), ("", None))

    def test_cookie_files_extend_the_primary(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        pool = gemini.load_credentials()
        self.assertEqual(len(pool), 2)
        self.assertEqual(pool[0].cookie, COOKIE_A)
        self.assertEqual(pool[1].cookie, COOKIE_B)
        self.assertTrue(gemini._credentials.rotating)

    def test_the_primary_cookie_file_keeps_its_place(self):
        """`load_cookie()` is the single-account accessor; it must still report
        the primary, or every existing deployment would silently switch."""
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        self.assertEqual(gemini.load_cookie(), (COOKIE_A, "sapisidAAA"))

    def test_the_same_file_twice_is_one_credential(self):
        """De-duplication, so a user who lists the primary again by mistake does
        not get a pool that rotates between one account and itself."""
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [primary]
        self.assertEqual(len(gemini.load_credentials()), 1)
        self.assertFalse(gemini._credentials.rotating)

    def test_each_account_keeps_its_own_auth_user(self):
        """The bug this guards: a pool that rotates cookies but not the account
        index addresses the wrong account — or none — on every failover."""
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA", auth_user="0"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB", auth_user="2"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        pool = gemini.load_credentials()
        self.assertEqual(pool[0].auth_user, "0")
        self.assertEqual(pool[1].auth_user, "2")
        self.assertEqual(gemini._get_url(credential=pool[1]).count("/u/2/"), 1)
        self.assertEqual(gemini._get_url(credential=pool[0]).count("/u/0/"), 1)

    def test_a_secondary_file_does_not_mutate_global_config(self):
        """`apply_defaults` is global, so letting a secondary file apply its
        overrides would make the last file read win for *every* request."""
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA", auth_user="0"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB", auth_user="7"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        gemini.load_credentials()
        self.assertEqual(self.CONFIG["auth_user"], "0",
                         "only the primary file may set global config")

    def test_each_account_carries_its_own_xsrf_token(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA", xsrf="tok-a"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB", xsrf="tok-b"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        pool = gemini.load_credentials()
        self.assertEqual(pool[0].xsrf_token, "tok-a")
        self.assertEqual(pool[1].xsrf_token, "tok-b")

    def test_a_missing_secondary_file_is_skipped_not_fatal(self):
        """One unreadable account must not take down the others."""
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [os.path.join(self.tmp, "nope.json")]
        pool = gemini.load_credentials()
        self.assertEqual(len(pool), 1)
        self.assertFalse(gemini._credentials.rotating)

    def test_editing_a_file_is_picked_up_without_a_restart(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        self.CONFIG["cookie_file"] = primary
        self.assertEqual(gemini.load_credentials()[0].cookie, COOKIE_A)
        self.write("a.json", auth_file(COOKIE_B, "sapisidBBB"))
        # mtime granularity can be coarse; make the change unmistakable.
        os.utime(primary, (0, 0))
        self.assertEqual(gemini.load_credentials()[0].cookie, COOKIE_B)

    def test_reset_clears_the_pool(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        self.CONFIG["cookie_file"] = primary
        self.assertEqual(len(gemini.load_credentials()), 1)
        gemini.reset_cookie_cache()
        self.assertEqual(gemini._credentials.credentials(), [])


class RotationTests(ConfigTestCase):
    """`generate()` and `generate_stream()` over a real-ish transport."""

    def setUp(self):
        super().setUp()
        gemini.reset_cookie_cache()
        self.addCleanup(gemini.reset_cookie_cache)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.CONFIG["retry_delay_sec"] = 0
        self.CONFIG["auto_update_bl"] = False

    def write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def two_accounts(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        return gemini.load_credentials()

    def fake_client(self, status_by_cookie, body=None):
        """A stub transport that answers by which cookie it was sent.

        Keyed on the Cookie header rather than a call counter, so the assertion
        is "the *other account* was tried", not merely "something happened
        twice" — a rotation that retried the same account would pass the latter.
        """
        calls = []

        class Response:
            def __init__(self, status, text):
                self.status_code = status
                self.text = text

            def raise_for_status(self):
                if self.status_code >= 400:
                    import urllib.error
                    raise urllib.error.HTTPError(
                        "u", self.status_code, "err", {}, None)

        class Client:
            def post(self, url, content=None, headers=None):
                cookie = (headers or {}).get("Cookie", "")
                calls.append({"cookie": cookie, "url": url,
                              "auth": (headers or {}).get("Authorization")})
                status = status_by_cookie.get(cookie, 500)
                if status == 200:
                    return Response(200, body or self_body())
                return Response(status, "")

        def self_body():
            from tests.support import gemini_response
            return gemini_response("rotated answer")

        return Client(), calls

    def test_a_429_moves_to_the_next_account(self):
        """The feature's whole purpose, and the reason it needs a *different*
        cookie rather than a second attempt."""
        pool = self.two_accounts()
        client, calls = self.fake_client({COOKIE_A: 429, COOKIE_B: 200})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client):
            text = gemini.generate("hi", 1, 4)
        self.assertEqual([c["cookie"] for c in calls], [COOKIE_A, COOKIE_B])
        self.assertIn("rotated answer", text)
        self.assertFalse(pool[0].usable, "the throttled account should be resting")
        self.assertTrue(pool[1].usable)

    def test_the_second_account_is_sent_its_own_auth_header(self):
        """Rotating the cookie but reusing the first account's SAPISIDHASH would
        authenticate as the wrong account on the retry."""
        pool = self.two_accounts()
        client, calls = self.fake_client({COOKIE_A: 429, COOKIE_B: 200})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client):
            gemini.generate("hi", 1, 4)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["auth"], calls[1]["auth"])
        self.assertNotIn("sapisidAAA", calls[1]["auth"])
        self.assertTrue(calls[1]["auth"].startswith("SAPISIDHASH "))
        del pool

    def test_a_401_cools_the_account_for_longer(self):
        pool = self.two_accounts()
        client, _calls = self.fake_client({COOKIE_A: 401, COOKIE_B: 200})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client):
            gemini.generate("hi", 1, 4)
        self.assertGreater(pool[0].cooldown_remaining(),
                           self.CONFIG.get("cookie_cooldown_sec", 60))

    def test_a_zero_cooldown_still_rotates(self):
        """`cookie_cooldown_sec: 0` means "rotate, but do not rest the account".

        This is the case that makes `acquire(exclude=...)` load-bearing rather
        than decorative: with no cooldown the failed credential is immediately
        `usable` again, so a pool asked for the next credential without being
        told which one just failed hands the same account straight back and the
        failover silently becomes a retry. Found by injecting that exact change
        and watching nothing fail.
        """
        self.CONFIG["cookie_cooldown_sec"] = 0
        self.two_accounts()
        client, calls = self.fake_client({COOKIE_A: 429, COOKIE_B: 200})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client):
            gemini.generate("hi", 1, 4)
        self.assertEqual(
            [c["cookie"] for c in calls], [COOKIE_A, COOKIE_B],
            "with no cooldown the failed account must still be skipped, or the "
            "retry goes to the account that just failed")

    def test_every_account_limited_fails_with_429_not_an_anonymous_call(self):
        """Sending no cookie at all would be worse than failing: it spends a
        call to earn a confusing error, and it is not what the client asked
        for."""
        self.two_accounts()
        client, calls = self.fake_client({COOKIE_A: 429, COOKIE_B: 429})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client), \
                self.assertRaises(gemini.GeminiUpstreamError) as caught:
            gemini.generate("hi", 1, 4)
        self.assertEqual(getattr(caught.exception, "status", None), 429)
        self.assertTrue(all(c["cookie"] for c in calls),
                        "an unauthenticated attempt was made")
        self.assertLessEqual(len(calls), 2,
                             "each account should be tried at most once per request")

    def test_a_single_account_still_retries_the_same_one(self):
        """Behaviour that already shipped must not change underneath users."""
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["retry_attempts"] = 3
        client, calls = self.fake_client({COOKIE_A: 429})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client), \
                self.assertRaises(gemini.GeminiUpstreamError):
            gemini.generate("hi", 1, 4)
        self.assertEqual([c["cookie"] for c in calls], [COOKIE_A] * 3)

    def test_a_server_error_does_not_burn_the_pool(self):
        """A 500 is the service being unwell; rotating would blame accounts."""
        pool = self.two_accounts()
        client, calls = self.fake_client({COOKIE_A: 500, COOKIE_B: 500})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client), \
                self.assertRaises(gemini.GeminiUpstreamError):
            gemini.generate("hi", 1, 4)
        self.assertTrue(all(c["cookie"] == COOKIE_A for c in calls),
                        "a 5xx must not rotate to another account")
        self.assertTrue(pool[1].usable)
        self.assertTrue(pool[0].usable)

    def test_no_cookie_file_retries_exactly_as_before(self):
        """The anonymous path: the pool is empty, and the retry loop must still
        make all of its attempts.

        This is the regression the suite caught when the rotation was first
        wired in — an empty pool was mistaken for "every account is cooling"
        and the loop broke out after one attempt.
        """
        self.CONFIG["retry_attempts"] = 3
        client, calls = self.fake_client({COOKIE_A: 500})
        with mock.patch.object(gemini, "_get_httpx_client", return_value=client), \
                self.assertRaises(gemini.GeminiUpstreamError):
            gemini.generate("hi", 1, 4)
        self.assertEqual(len(calls), 3, "the anonymous path must still retry")
        self.assertTrue(all(c["cookie"] == "" for c in calls),
                        "no cookie should be sent when none is configured")


class StatusCredentialReportingTests(ServerTestCase):
    """`/status` is where an operator sees which account is resting.

    Guarded here rather than assumed: forgetting to add `credentials` to the
    payload is invisible — `/status` still answers 200 with everything else — so
    nothing but a test would notice the field going missing.
    """

    def setUp(self):
        super().setUp()
        gemini.reset_cookie_cache()
        self.addCleanup(gemini.reset_cookie_cache)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_status_reports_one_entry_per_account(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        gemini.reset_cookie_cache()

        status, _headers, payload = self.get_json("/status")
        self.assertEqual(status, 200)
        credentials = payload["credentials"]
        self.assertEqual(credentials["size"], 2)
        self.assertTrue(credentials["rotating"])
        self.assertEqual(credentials["available"], 2)
        self.assertEqual([e["source"] for e in credentials["entries"]],
                         [primary, second])

    def test_status_never_carries_a_cookie(self):
        primary = self.write("a.json", auth_file(COOKIE_A, "sapisidAAA"))
        second = self.write("b.json", auth_file(COOKIE_B, "sapisidBBB"))
        self.CONFIG["cookie_file"] = primary
        self.CONFIG["cookie_files"] = [second]
        gemini.reset_cookie_cache()

        _status, _headers, payload = self.get_json("/status")
        blob = json.dumps(payload)
        for secret in (COOKIE_A, COOKIE_B, "sapisidAAA", "sapisidBBB"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob)

    def test_a_single_cookie_reports_no_rotation(self):
        self.CONFIG["cookie_file"] = self.write(
            "a.json", auth_file(COOKIE_A, "sapisidAAA"))
        gemini.reset_cookie_cache()
        _status, _headers, payload = self.get_json("/status")
        self.assertFalse(payload["credentials"]["rotating"])
        self.assertEqual(payload["credentials"]["size"], 1)


if __name__ == "__main__":
    unittest.main()
