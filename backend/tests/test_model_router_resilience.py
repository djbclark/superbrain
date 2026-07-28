#!/usr/bin/env python3

import contextlib
import base64
import io
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import core.model_router as model_router


def make_router():
    router = model_router.ModelRouter.__new__(model_router.ModelRouter)
    router._lock = threading.Lock()
    router._dynamic_models_lock = threading.Lock()
    router._dynamic_models = {}
    router._api_keys = {
        "GROQ_API_KEY": "test",
        "GEMINI_API_KEY": "test",
        "OPENROUTER_API_KEY": "test",
    }
    router._state = {
        key: router._default_model_state_dynamic(key)
        for key in model_router.MODELS_BY_KEY
    }
    router._save_state = lambda: None
    return router


class TestModelRouterResilience(unittest.TestCase):
    def test_unexpired_cooldowns_are_not_cleared(self):
        router = make_router()
        down_until = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)
        ).isoformat()
        for state in router._state.values():
            state["down_until"] = down_until
        self.assertEqual(router._ranked_models("text"), [])
        self.assertTrue(
            all(state["down_until"] == down_until for state in router._state.values())
        )

    def test_provider_tiers_are_strict(self):
        router = make_router()
        providers = [
            model_router.MODELS_BY_KEY[key]["provider"]
            for key in router._ranked_models("text")
        ]
        compressed = [
            provider
            for index, provider in enumerate(providers)
            if index == 0 or providers[index - 1] != provider
        ]
        self.assertEqual(
            compressed, ["groq", "gemini", "openrouter", "ollama", "omlx"]
        )

    def test_environment_overrides_ignored_api_keys_file(self):
        router = model_router.ModelRouter.__new__(model_router.ModelRouter)
        router._api_keys = {}
        with tempfile.TemporaryDirectory() as tempdir:
            api_keys_file = Path(tempdir) / ".api_keys"
            api_keys_file.write_text("GEMINI_API_KEY=file-value\n")
            with (
                patch.object(model_router, "API_KEYS_FILE", api_keys_file),
                patch.dict(os.environ, {"GEMINI_API_KEY": "environment-value"}),
            ):
                router._load_api_keys()
        self.assertEqual(router._key("GEMINI_API_KEY"), "environment-value")

    def test_waiting_section_reports_without_resetting_state(self):
        router = make_router()
        down_until = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=120)
        ).isoformat()
        for state in router._state.values():
            state["down_until"] = down_until
            state["last_error"] = "429 rate limit"
        with contextlib.redirect_stdout(io.StringIO()):
            wait_seconds = router.print_all_waiting_section("text")
        self.assertGreaterEqual(wait_seconds, 115)
        self.assertTrue(all(router._state[key]["down_until"] for key in router._state))

    def test_gemini_uses_installed_google_genai_sdk(self):
        router = make_router()
        response = type("Response", (), {"text": "  generated  "})()
        with patch("google.genai.Client") as client_class:
            client_class.return_value.models.generate_content.return_value = response
            self.assertEqual(
                router._gemini_text("gemini-3.6-flash", "hello"),
                "generated",
            )
            self.assertEqual(
                router._gemini_vision(
                    "gemini-3.6-flash",
                    "describe",
                    [base64.b64encode(b"jpeg bytes").decode()],
                ),
                "generated",
            )
        self.assertEqual(
            client_class.return_value.models.generate_content.call_count,
            2,
        )


if __name__ == "__main__":
    unittest.main()
