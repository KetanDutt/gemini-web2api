"""Configuration layering: file, environment, coercion and validation."""
import json
import os
import tempfile
import unittest
from unittest import mock

from gemini_web2api.config import (
    DEFAULT_CONFIG,
    ENV_PREFIX,
    find_config,
    load_config,
    load_warnings,
    reset_config,
    snapshot,
)
from tests.support import ConfigTestCase


class DefaultValueTests(unittest.TestCase):
    def test_defaults_are_safe_out_of_the_box(self):
        self.assertEqual(DEFAULT_CONFIG["host"], "0.0.0.0")
        self.assertEqual(DEFAULT_CONFIG["api_keys"], [])
        self.assertTrue(DEFAULT_CONFIG["block_private_image_urls"])
        self.assertTrue(DEFAULT_CONFIG["auto_update_bl"])
        self.assertEqual(DEFAULT_CONFIG["rate_limit_max"], 0)
        self.assertGreater(DEFAULT_CONFIG["max_request_bytes"], 0)

    def test_mutable_defaults_are_not_aliased_into_config(self):
        """CONFIG must deep-copy, or mutating it would corrupt DEFAULT_CONFIG."""
        from gemini_web2api.config import CONFIG
        reset_config()
        self.assertIsNot(CONFIG["api_keys"], DEFAULT_CONFIG["api_keys"])
        CONFIG["api_keys"].append("leaked")
        self.assertEqual(DEFAULT_CONFIG["api_keys"], [])
        reset_config()
        self.assertEqual(CONFIG["api_keys"], [])


class FileLoadingTests(ConfigTestCase):
    def write(self, payload, name="config.json"):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_file_values_are_applied(self):
        path = self.write({"port": 9999, "temporary_chats": True})
        load_config(path)
        self.assertEqual(self.CONFIG["port"], 9999)
        self.assertTrue(self.CONFIG["temporary_chats"])

    def test_missing_file_is_not_an_error(self):
        load_config("/nonexistent/config.json")
        self.assertEqual(self.CONFIG["port"], DEFAULT_CONFIG["port"])

    def test_invalid_json_falls_back_to_defaults_with_a_warning(self):
        path = self.write("{not json at all")
        load_config(path)
        self.assertEqual(self.CONFIG["port"], DEFAULT_CONFIG["port"])
        self.assertTrue(any("not valid JSON" in w for w in load_warnings()))

    def test_non_object_json_is_rejected(self):
        path = self.write("[1, 2, 3]")
        load_config(path)
        self.assertTrue(any("must contain a JSON object" in w for w in load_warnings()))

    def test_unknown_keys_warn_instead_of_being_adopted(self):
        path = self.write({"apikey": "typo", "port": 8123})
        load_config(path)
        self.assertEqual(self.CONFIG["port"], 8123)
        self.assertNotIn("apikey", self.CONFIG)
        self.assertTrue(any("unknown option 'apikey'" in w for w in load_warnings()))

    def test_string_typed_booleans_are_coerced(self):
        path = self.write({"log_requests": "false", "temporary_chats": "yes"})
        load_config(path)
        self.assertFalse(self.CONFIG["log_requests"])
        self.assertTrue(self.CONFIG["temporary_chats"])

    def test_invalid_number_keeps_the_default_and_warns(self):
        path = self.write({"port": "not-a-number"})
        load_config(path)
        self.assertEqual(self.CONFIG["port"], DEFAULT_CONFIG["port"])
        self.assertTrue(any("not an integer" in w for w in load_warnings()))

    def test_out_of_range_port_is_clamped(self):
        path = self.write({"port": 999999})
        load_config(path)
        self.assertEqual(self.CONFIG["port"], 8081)

    def test_unknown_default_model_is_replaced(self):
        path = self.write({"default_model": "gpt-4"})
        load_config(path)
        self.assertEqual(self.CONFIG["default_model"], DEFAULT_CONFIG["default_model"])
        self.assertTrue(any("default_model" in w for w in load_warnings()))

    def test_explicit_keys_are_recorded(self):
        from gemini_web2api.config import is_explicit
        path = self.write({"gemini_bl": "pinned_bl"})
        load_config(path)
        self.assertTrue(is_explicit("gemini_bl"))
        self.assertFalse(is_explicit("xsrf_token"))


class EnvironmentTests(ConfigTestCase):
    def test_environment_overrides_file(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"port": 1111}, handle)
        with mock.patch.dict(os.environ, {ENV_PREFIX + "PORT": "2222"}, clear=False):
            load_config(path)
        self.assertEqual(self.CONFIG["port"], 2222)

    def test_api_keys_accept_a_comma_list(self):
        with mock.patch.dict(os.environ, {ENV_PREFIX + "API_KEYS": "a, b ,c"}, clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["api_keys"], ["a", "b", "c"])

    def test_api_keys_accept_a_json_array(self):
        with mock.patch.dict(os.environ, {ENV_PREFIX + "API_KEYS": '["x","y"]'}, clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["api_keys"], ["x", "y"])

    def test_api_keys_accept_a_pipe_list(self):
        with mock.patch.dict(os.environ, {ENV_PREFIX + "API_KEYS": "x|y"}, clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["api_keys"], ["x", "y"])

    def test_empty_api_keys_disables_auth(self):
        with mock.patch.dict(os.environ, {ENV_PREFIX + "API_KEYS": ""}, clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["api_keys"], [])

    def test_boolean_environment_values(self):
        with mock.patch.dict(os.environ, {ENV_PREFIX + "TEMPORARY_CHATS": "true"}, clear=False):
            load_config(None)
        self.assertTrue(self.CONFIG["temporary_chats"])

    def test_nullable_keys_treat_empty_string_as_unset(self):
        with mock.patch.dict(os.environ, {ENV_PREFIX + "XSRF_TOKEN": ""}, clear=False):
            load_config(None)
        self.assertIsNone(self.CONFIG["xsrf_token"])

    def test_standard_proxy_variables_are_honoured(self):
        environment = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        environment["HTTPS_PROXY"] = "http://127.0.0.1:7890"
        with mock.patch.dict(os.environ, environment, clear=True):
            load_config(None)
        self.assertEqual(self.CONFIG["proxy"], "http://127.0.0.1:7890")

    def test_explicit_proxy_wins_over_environment(self):
        with mock.patch.dict(os.environ, {
            ENV_PREFIX + "PROXY": "http://explicit:1080",
            "HTTPS_PROXY": "http://from-env:7890",
        }, clear=False):
            load_config(None)
        self.assertEqual(self.CONFIG["proxy"], "http://explicit:1080")


class ApplyDefaultsTests(ConfigTestCase):
    def test_apply_defaults_only_fills_unset_keys(self):
        from gemini_web2api.config import apply_defaults, mark_explicit
        mark_explicit(["xsrf_token"])
        self.CONFIG["xsrf_token"] = "operator-value"
        applied = apply_defaults({"xsrf_token": "from-file", "gemini_bl": "bl-from-file"})
        self.assertEqual(applied, ["gemini_bl"])
        self.assertEqual(self.CONFIG["xsrf_token"], "operator-value")
        self.assertEqual(self.CONFIG["gemini_bl"], "bl-from-file")

    def test_apply_defaults_ignores_empty_values(self):
        from gemini_web2api.config import apply_defaults
        self.assertEqual(apply_defaults({"auth_user": "", "xsrf_token": None}), [])


class SnapshotTests(ConfigTestCase):
    def test_snapshot_redacts_secrets(self):
        self.CONFIG["api_keys"] = ["super-secret", "another"]
        self.CONFIG["xsrf_token"] = "AOOh0Psecret"
        data = snapshot()
        self.assertEqual(data["api_keys"], "2 configured")
        self.assertEqual(data["xsrf_token"], "set")
        self.assertNotIn("super-secret", json.dumps(data))
        self.assertNotIn("AOOh0Psecret", json.dumps(data))

    def test_snapshot_can_be_serialised(self):
        self.assertIsInstance(json.dumps(snapshot()), str)


class FindConfigTests(unittest.TestCase):
    def test_environment_variable_wins(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "custom.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        with mock.patch.dict(os.environ, {ENV_PREFIX + "CONFIG": path}, clear=False):
            self.assertEqual(find_config(), path)


if __name__ == "__main__":
    unittest.main()
