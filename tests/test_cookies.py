"""Cookie file parsing.

Every layout here was found in the wild; the pre-fix parser only handled the
first one and silently lost SAPISID for the rest, which dropped the
``Authorization: SAPISIDHASH`` header and downgraded Pro to Flash without any
error being reported.
"""
import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from gemini_web2api.gemini import (
    load_cookie,
    make_sapisidhash,
    reset_cookie_cache,
)
from tests.support import ConfigTestCase


class CookieFileTests(ConfigTestCase):
    def setUp(self):
        super().setUp()
        self.CONFIG["log_requests"] = False
        self.directory = tempfile.mkdtemp()
        reset_cookie_cache()

    def tearDown(self):
        reset_cookie_cache()
        super().tearDown()

    def write_cookie(self, content, name="cookie.txt"):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        self.CONFIG["cookie_file"] = path
        return path

    def test_semicolon_space_separated(self):
        self.write_cookie("SID=aaa; SAPISID=bbb; HSID=ccc")
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")
        self.assertIn("SID=aaa", cookie)

    def test_semicolon_without_space(self):
        self.write_cookie("SID=aaa;SAPISID=bbb;HSID=ccc")
        _cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")

    def test_newline_separated(self):
        self.write_cookie("SID=aaa\nSAPISID=bbb\nHSID=ccc\n")
        _cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")

    def test_windows_line_endings(self):
        self.write_cookie("SID=aaa;\r\nSAPISID=bbb;\r\n")
        _cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")

    def test_leading_cookie_header_prefix(self):
        self.write_cookie("Cookie: SID=aaa; SAPISID=bbb")
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")
        self.assertFalse(cookie.lower().startswith("cookie:"))

    def test_trailing_semicolon_and_whitespace(self):
        self.write_cookie("  SID=aaa; SAPISID=bbb;  \n")
        _cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")

    def test_values_containing_equals_are_preserved(self):
        # Google cookie values are base64-ish and routinely contain '=' padding.
        self.write_cookie("__Secure-1PSID=g.ABC=def==; SAPISID=x/y+z==")
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "x/y+z==")
        self.assertIn("__Secure-1PSID=g.ABC=def==", cookie)

    def test_json_cookie_format(self):
        self.write_cookie(json.dumps({"cookie": "SID=aaa; SAPISID=bbb", "sapisid": "bbb"}),
                          name="cookie.json")
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")
        self.assertIn("SID=aaa", cookie)

    def test_json_without_explicit_sapisid_extracts_it(self):
        self.write_cookie(json.dumps({"cookie": "SID=aaa; SAPISID=derived"}), name="c.json")
        _cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "derived")

    def test_json_array_of_cookie_objects(self):
        self.write_cookie(json.dumps([
            {"name": "SID", "value": "aaa", "domain": ".google.com"},
            {"name": "SAPISID", "value": "bbb", "domain": ".google.com"},
        ]), name="export.json")
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")
        self.assertIn("SID=aaa", cookie)

    def test_netscape_cookie_jar(self):
        jar = "\n".join([
            "# Netscape HTTP Cookie File",
            "# https://curl.se/docs/http-cookies.html",
            "",
            ".google.com\tTRUE\t/\tTRUE\t1800000000\tSID\tnetscape_sid",
            ".google.com\tTRUE\t/\tTRUE\t1800000000\tSAPISID\tnetscape_sapisid",
            ".gemini.google.com\tTRUE\t/\tTRUE\t1800000000\t__Secure-1PSID\tone_psid",
            "",
        ])
        self.write_cookie(jar, name="cookies.txt")
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "netscape_sapisid")
        self.assertIn("__Secure-1PSID=one_psid", cookie)
        self.assertNotIn("#", cookie)

    def test_netscape_comment_lines_are_skipped(self):
        jar = "\n".join([
            "# comment",
            "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1800000000\tSID\thttponly_sid",
            ".google.com\tTRUE\t/\tTRUE\t1800000000\tSAPISID\treal",
        ])
        self.write_cookie(jar)
        _cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "real")

    def test_missing_file_returns_empty(self):
        self.CONFIG["cookie_file"] = os.path.join(self.directory, "nope.txt")
        self.assertEqual(load_cookie(), ("", None))

    def test_no_cookie_file_configured(self):
        self.CONFIG["cookie_file"] = None
        self.assertEqual(load_cookie(), ("", None))

    def test_invalid_json_logs_and_returns_empty(self):
        self.write_cookie("{ this is not json", name="bad.json")
        cookie, sapisid = load_cookie()
        self.assertEqual((cookie, sapisid), ("", None))

    def test_cookie_without_sapisid_is_still_returned(self):
        self.write_cookie("SID=aaa; HSID=bbb")
        cookie, sapisid = load_cookie()
        self.assertIn("SID=aaa", cookie)
        self.assertIsNone(sapisid)

    def test_result_is_cached_by_file_fingerprint(self):
        path = self.write_cookie("SID=aaa; SAPISID=first")
        self.assertEqual(load_cookie()[1], "first")

        reads = []
        real_open = open

        def counting_open(*args, **kwargs):
            reads.append(args[0])
            return real_open(*args, **kwargs)

        with mock.patch("builtins.open", counting_open):
            load_cookie()
            load_cookie()
        self.assertEqual(reads, [], "cached cookie should not re-read the file")

        # A content change (with a distinguishable mtime/size) must be picked up.
        with real_open(path, "w", encoding="utf-8") as handle:
            handle.write("SID=aaa; SAPISID=second-longer")
        os.utime(path, (1_900_000_000, 1_900_000_000))
        self.assertEqual(load_cookie()[1], "second-longer")

    def test_unreadable_file_does_not_crash(self):
        path = self.write_cookie("SID=aaa; SAPISID=bbb")
        os.chmod(path, 0)
        try:
            cookie, sapisid = load_cookie()
        finally:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        # Root ignores the mode bits, so accept either outcome; never an exception.
        self.assertIn(sapisid, ("bbb", None))
        self.assertIsInstance(cookie, str)


class AuthFileTests(ConfigTestCase):
    """The bundled browser extension exports gemini-auth.json.

    Only `cookie` and `sapisid` used to be read; `xsrf_token`, `gemini_bl` and
    `auth_user` were dropped, which forced the manual jq pipeline that SETUP.md
    documented.
    """

    def setUp(self):
        super().setUp()
        self.CONFIG["log_requests"] = False
        self.directory = tempfile.mkdtemp()
        reset_cookie_cache()

    def tearDown(self):
        reset_cookie_cache()
        super().tearDown()

    def export(self, payload, name="gemini-auth.json"):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        self.CONFIG["cookie_file"] = path
        return path

    def test_all_extension_fields_are_adopted(self):
        from gemini_web2api.config import load_config
        load_config(None)  # clear any explicit markers from earlier tests
        self.export({
            "cookie": "SID=aaa; SAPISID=bbb",
            "sapisid": "bbb",
            "auth_user": "1",
            "xsrf_token": "AOOh0PfromExtension",
            "gemini_bl": "boq_assistant-bard-web-server_20260901.01_p0",
        })
        cookie, sapisid = load_cookie()
        self.assertEqual(sapisid, "bbb")
        self.assertIn("SID=aaa", cookie)
        self.assertEqual(self.CONFIG["xsrf_token"], "AOOh0PfromExtension")
        self.assertEqual(self.CONFIG["gemini_bl"], "boq_assistant-bard-web-server_20260901.01_p0")
        self.assertEqual(self.CONFIG["auth_user"], "1")

    def test_explicit_config_wins_over_the_auth_file(self):
        from gemini_web2api.config import load_config, mark_explicit
        load_config(None)
        self.CONFIG["xsrf_token"] = "operator-pinned"
        mark_explicit(["xsrf_token", "gemini_bl"])
        self.CONFIG["gemini_bl"] = "pinned_bl"
        self.export({
            "cookie": "SID=aaa; SAPISID=bbb",
            "xsrf_token": "from-extension",
            "gemini_bl": "from-extension-bl",
        })
        load_cookie()
        self.assertEqual(self.CONFIG["xsrf_token"], "operator-pinned")
        self.assertEqual(self.CONFIG["gemini_bl"], "pinned_bl")

    def test_auth_file_values_reach_the_wire_request(self):
        from gemini_web2api.config import load_config
        from gemini_web2api.gemini import _build_headers, _build_payload, _get_url
        load_config(None)
        self.export({
            "cookie": "SID=aaa; SAPISID=bbb",
            "auth_user": "2",
            "xsrf_token": "XSRF-TOKEN-VALUE",
        })
        headers = _build_headers()
        payload = _build_payload("hello", 1, 4)
        self.assertIn("XSRF-TOKEN-VALUE", payload)
        self.assertIn("at=", payload)
        self.assertEqual(headers["X-Goog-AuthUser"], "2")
        self.assertIn("/u/2/", _get_url())
        self.assertIn("SAPISIDHASH", headers["Authorization"])


class SapisidHashTests(unittest.TestCase):
    def test_format(self):
        header = make_sapisidhash("test-sapisid")
        self.assertTrue(header.startswith("SAPISIDHASH "))
        timestamp, _, digest = header[len("SAPISIDHASH "):].partition("_")
        self.assertTrue(timestamp.isdigit())
        self.assertEqual(len(digest), 40)  # sha1 hex

    def test_origin_is_part_of_the_hash(self):
        import hashlib
        sapisid = "abc"
        expected = hashlib.sha1(
            f"{int(time.time())} {sapisid} https://gemini.google.com".encode()).hexdigest()
        header = make_sapisidhash(sapisid)
        timestamp, _, digest = header[len("SAPISIDHASH "):].partition("_")
        # Allow for the clock ticking over between the two computations.
        self.assertIn(digest, (
            expected,
            hashlib.sha1(f"{timestamp} {sapisid} https://gemini.google.com".encode()).hexdigest(),
        ))

    def test_differs_per_timestamp(self):
        first = make_sapisidhash("same")
        time.sleep(1.01)
        second = make_sapisidhash("same")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
