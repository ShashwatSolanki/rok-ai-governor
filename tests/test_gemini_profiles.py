import os
import unittest
from unittest.mock import patch

from rok_governor.gemini_profiles import (
    GeminiProfile,
    GeminiProfilesExhausted,
    generate_with_profiles,
    load_candidate_profiles,
    load_gemini_profiles,
)


class GeminiProfileTests(unittest.TestCase):
    def setUp(self):
        self.dotenv = patch("rok_governor.gemini_profiles.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)

    def test_numbered_keys_keep_their_models_and_rotate_in_numeric_order(self):
        values = {
            "GEMINI_API_KEY_3": "secret-three",
            "GEMINI_MODEL_3": "model-three",
            "GEMINI_API_KEY_1": "secret-one",
            "GEMINI_MODEL_1": "model-one",
            "GEMINI_API_KEY_2": "secret-two",
            "GEMINI_MODEL_2": "model-two",
            "GEMINI_MODEL": "model-default",
        }
        with patch.dict(os.environ, values, clear=True):
            profiles = load_gemini_profiles(default_model="fallback-model")

        self.assertEqual(
            [(p.name, p.model, p.api_key) for p in profiles],
            [
                ("GEMINI_API_KEY_1", "model-one", "secret-one"),
                ("GEMINI_API_KEY_2", "model-two", "secret-two"),
                ("GEMINI_API_KEY_3", "model-three", "secret-three"),
            ],
        )
        self.assertNotIn("secret-one", repr(profiles[0]))

    def test_override_changes_only_primary_and_legacy_key_remains_supported(self):
        values = {
            "GEMINI_API_KEY": "legacy-secret",
            "GEMINI_MODEL": "configured-model",
            "GEMINI_API_KEY_2": "second-secret",
            "GEMINI_MODEL_2": "second-model",
        }
        with patch.dict(os.environ, values, clear=True):
            profiles = load_gemini_profiles(
                model_override="cli-model", default_model="fallback-model"
            )

        self.assertEqual(profiles[0].model, "cli-model")
        self.assertEqual(profiles[1].model, "configured-model")

    def test_placeholder_keys_are_ignored(self):
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "your_gemini_api_key_here", "GEMINI_MODEL": "m"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "No AI API key"):
                load_gemini_profiles(default_model="m")

    def test_quota_failure_rotates_and_redacts_secret(self):
        profiles = [
            GeminiProfile("key one", "model one", "secret-one"),
            GeminiProfile("key two", "model two", "secret-two"),
        ]
        called = []

        def generate(profile):
            called.append(profile.name)
            if profile.name == "key one":
                raise RuntimeError(f"429 RESOURCE_EXHAUSTED key={profile.api_key}")
            return "ok"

        with patch("builtins.print") as output:
            used, response = generate_with_profiles(profiles, generate)

        self.assertEqual(called, ["key one", "key two"])
        self.assertEqual((used.name, response), ("key two", "ok"))
        self.assertNotIn("secret-one", str(output.call_args))

    def test_non_retryable_error_does_not_spend_other_profiles(self):
        profiles = [
            GeminiProfile("key one", "model one", "secret-one"),
            GeminiProfile("key two", "model two", "secret-two"),
        ]
        called = []

        def generate(profile):
            called.append(profile.name)
            raise ValueError("invalid response schema")

        with self.assertRaisesRegex(ValueError, "invalid response schema"):
            generate_with_profiles(profiles, generate)
        self.assertEqual(called, ["key one"])

    def test_exhaustion_reports_profile_models_without_api_keys(self):
        profiles = [GeminiProfile("key one", "model one", "private-key")]

        def generate(_profile):
            raise RuntimeError("429 quota exceeded")

        with self.assertRaises(GeminiProfilesExhausted) as raised:
            generate_with_profiles(profiles, generate)
        self.assertIn("key one/model one", str(raised.exception))
        self.assertNotIn("private-key", str(raised.exception))

    def test_model_fallbacks_follow_configured_profiles(self):
        values = {
            "GEMINI_API_KEY_1": "secret-one",
            "GEMINI_MODEL_1": "model-one",
            "GEMINI_API_KEY_2": "secret-two",
            "GEMINI_MODEL_2": "gemini-2.5-flash",
        }
        with patch.dict(os.environ, values, clear=True):
            profiles = load_candidate_profiles(default_model="default-model")

        self.assertEqual(profiles[0].model, "model-one")
        self.assertEqual(profiles[1].model, "gemini-2.5-flash")
        self.assertEqual(profiles[2].model, "gemini-2.5-flash")
        self.assertEqual(profiles[2].api_key, "secret-one")
        self.assertEqual(profiles[3].model, "gemini-3.5-flash-lite")


if __name__ == "__main__":
    unittest.main()
