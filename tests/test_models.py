"""Model table and @think= resolution."""
import unittest

from gemini_web2api.models import (
    MODE_CATEGORY,
    MODELS,
    PAYLOAD_SLOTS,
    default_model,
    google_model_detail,
    google_model_list,
    model_list,
    resolve_model,
)
from tests.support import ConfigTestCase


class ModelTableTests(unittest.TestCase):
    def test_every_model_has_the_required_fields(self):
        for name, cfg in MODELS.items():
            with self.subTest(model=name):
                self.assertIn("mode", cfg)
                self.assertIn("think", cfg)
                self.assertIn("desc", cfg)
                self.assertIn(cfg["mode"], MODE_CATEGORY)
                self.assertTrue(cfg["desc"])

    def test_mode_ids_match_the_frontend_enum(self):
        self.assertEqual(MODE_CATEGORY[1], "FAST")
        self.assertEqual(MODE_CATEGORY[2], "THINKING")
        self.assertEqual(MODE_CATEGORY[3], "PRO")
        self.assertEqual(MODE_CATEGORY[4], "AUTO")
        self.assertEqual(MODE_CATEGORY[5], "FAST_DYNAMIC_THINKING")
        self.assertEqual(MODE_CATEGORY[6], "FLASH_LITE")

    def test_payload_slots_accommodate_every_extra_field(self):
        """gemini-3.1-pro-enhanced writes slot 80; the old 80-slot payload raised IndexError."""
        for name, cfg in MODELS.items():
            for slot in (cfg.get("extra") or {}):
                with self.subTest(model=name, slot=slot):
                    self.assertLess(slot, PAYLOAD_SLOTS)

    def test_documented_models_exist(self):
        for name in ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                     "gemini-3.5-flash-thinking", "gemini-3.1-pro", "gemini-auto",
                     "gemini-flash-lite"):
            self.assertIn(name, MODELS)

    def test_pro_models_are_flagged_as_needing_a_cookie(self):
        for name, cfg in MODELS.items():
            if cfg["mode"] == 3:
                with self.subTest(model=name):
                    self.assertTrue(cfg.get("needs_cookie"))


class ResolveModelTests(ConfigTestCase):
    def test_known_model(self):
        name, mode, think, error, extra = resolve_model("gemini-3.5-flash-thinking")
        self.assertEqual((name, mode, think, error), ("gemini-3.5-flash-thinking", 2, 0, None))
        self.assertIsNone(extra)

    def test_extra_fields_are_returned(self):
        _name, _mode, _think, error, extra = resolve_model("gemini-3.1-pro-enhanced")
        self.assertIsNone(error)
        self.assertEqual(extra, {31: 2, 80: 3})

    def test_think_suffix_overrides_the_default(self):
        self.assertEqual(resolve_model("gemini-3.5-flash-thinking@think=2")[2], 2)
        self.assertEqual(resolve_model("gemini-3.6-flash@think=0")[2], 0)

    def test_think_suffix_is_stripped_from_the_name(self):
        self.assertEqual(resolve_model("gemini-3.6-flash@think=1")[0], "gemini-3.6-flash")

    def test_think_bounds(self):
        self.assertEqual(resolve_model("gemini-3.6-flash@think=0")[2], 0)
        self.assertEqual(resolve_model("gemini-3.6-flash@think=4")[2], 4)

    def test_think_out_of_range_is_an_error(self):
        for bad in ("gemini-3.6-flash@think=5", "gemini-3.6-flash@think=-1"):
            with self.subTest(model=bad):
                error = resolve_model(bad)[3]
                self.assertIsNotNone(error)
                self.assertIn("Invalid think level", error)

    def test_think_non_integer_is_an_error(self):
        error = resolve_model("gemini-3.6-flash@think=deep")[3]
        self.assertIn("Invalid think level", error)

    def test_whitespace_is_tolerated(self):
        self.assertEqual(resolve_model("  gemini-3.6-flash  ")[0], "gemini-3.6-flash")
        self.assertEqual(resolve_model("gemini-3.6-flash@think= 2 ")[2], 2)

    def test_unknown_model_falls_back_by_default(self):
        name, mode, _think, error, _extra = resolve_model("gpt-4-turbo")
        self.assertIsNone(error)
        self.assertEqual(name, default_model())
        self.assertEqual(mode, MODELS[default_model()]["mode"])

    def test_strict_models_rejects_unknown_names(self):
        self.CONFIG["strict_models"] = True
        name, _mode, _think, error, _extra = resolve_model("gpt-4-turbo")
        self.assertIsNone(name)
        self.assertIn("Unknown model", error)

    def test_empty_and_none_use_the_default(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertEqual(resolve_model(value)[3], None)
                self.assertEqual(resolve_model(value)[0], default_model())

    def test_explicit_default_argument_is_honoured(self):
        name = resolve_model("no-such-model", default="gemini-flash-lite")[0]
        self.assertEqual(name, "gemini-flash-lite")

    def test_configured_default_model_is_used(self):
        self.CONFIG["default_model"] = "gemini-flash-lite"
        self.assertEqual(default_model(), "gemini-flash-lite")
        self.assertEqual(resolve_model("unknown-model")[0], "gemini-flash-lite")

    def test_invalid_configured_default_falls_back_safely(self):
        self.CONFIG["default_model"] = "not-a-real-model"
        self.assertIn(default_model(), MODELS)

    def test_non_string_input_does_not_crash(self):
        self.assertEqual(resolve_model(12345)[3], None)


class ListingTests(unittest.TestCase):
    def test_openai_shape(self):
        for entry in model_list():
            self.assertEqual(entry["object"], "model")
            self.assertEqual(entry["owned_by"], "google")
            self.assertIn("id", entry)
            self.assertIn("created", entry)
        self.assertEqual(len(model_list()), len(MODELS))

    def test_google_shape(self):
        for entry in google_model_list():
            self.assertTrue(entry["name"].startswith("models/"))
            self.assertIn("generateContent", entry["supportedGenerationMethods"])
            self.assertIn("streamGenerateContent", entry["supportedGenerationMethods"])

    def test_google_detail_for_one_model(self):
        detail = google_model_detail("gemini-3.6-flash")
        self.assertEqual(detail["name"], "models/gemini-3.6-flash")
        self.assertEqual(detail["displayName"], "gemini-3.6-flash")

    def test_google_detail_for_unknown_model(self):
        self.assertIsNone(google_model_detail("nope"))

    def test_listings_are_cached(self):
        """GET /v1/models is the endpoint clients poll; it must not rebuild
        the table per call. Identity is the proof — the table is static."""
        self.assertIs(model_list(), model_list())
        self.assertIs(google_model_list(), google_model_list())
        self.assertIs(google_model_detail("gemini-3.6-flash"),
                      google_model_detail("gemini-3.6-flash"))
        # A cached miss is cached too, so an unknown model does not rescan.
        self.assertIs(google_model_detail("nope"), google_model_detail("nope"))


if __name__ == "__main__":
    unittest.main()
