"""Preferencias recuperables, sin secretos, envío ni migración de autenticación."""
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from conversion.connection_settings import (
    default_connection, default_settings, load_settings, save_settings, validate_settings,
)
from conversion.providers import PROVIDERS, profiles_for
from support import workspace_temp


class ConnectionSettingsTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)

    def example(self):
        return {"version": 1, "selected_provider": "openai-api", "connections": {
            "openai-api": {"model": "control-model-1", "key_slot": 5, "cli_profile": "principal"},
            "antigravity-cli": {"model": "control-2", "key_slot": 1, "cli_profile": "fab5"},
        }}

    def test_missing_does_not_create_file(self):
        settings, notice = load_settings(self.root)
        self.assertEqual(settings, default_settings())
        self.assertIn("Sin selección", notice)
        self.assertFalse((self.root / "conexiones.json").exists())

    def test_roundtrip_independent_provider_choices(self):
        expected = self.example()
        path = save_settings(self.root, expected)
        actual, notice = load_settings(self.root)
        self.assertEqual(actual, expected)
        self.assertIn("no verificados", notice)
        self.assertNotIn("send", path.read_text(encoding="utf-8"))
        self.assertNotIn("mode", path.read_text(encoding="utf-8").replace("model", ""))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_all_providers_and_profiles_validate(self):
        for provider in PROVIDERS:
            for profile in profiles_for(provider):
                value = {"version": 1, "selected_provider": provider, "connections": {
                    provider: dict(default_connection(), cli_profile=profile, key_slot=5)}}
                self.assertEqual(validate_settings(value), value)

    def test_unknown_secret_permission_fields_rejected(self):
        for field in ("api_key", "send", "mode", "authorized", "password", "budget"):
            candidate = self.example()
            candidate[field] = "must-not-persist"
            with self.assertRaises(ValueError):
                save_settings(self.root, candidate)
            self.assertFalse((self.root / "conexiones.json").exists())
            candidate = self.example()
            candidate["connections"]["openai-api"][field] = "must-not-persist"
            with self.assertRaises(ValueError):
                save_settings(self.root, candidate)

    def test_invalid_slots_models_and_cross_provider_profiles(self):
        mutations = [("key_slot", item) for item in (0, 6, True, "5", None)]
        mutations += [("model", item) for item in ("a\nsecret", "command --flag", "x" * 201, 3)]
        mutations += [("cli_profile", "fab1"), ("cli_profile", "../../profile")]
        for field, bad_value in mutations:
            candidate = self.example()
            candidate["connections"]["openai-api"][field] = bad_value
            with self.subTest(field=field, value=bad_value), self.assertRaises(ValueError):
                validate_settings(candidate)

    def test_corrupt_file_preserved_and_fails_closed(self):
        path = self.root / "conexiones.json"
        for raw in (b'{"version":', b'[]', b'null', b'{"send":true}', b'x' * 32769,
                    b'{"version":1,"version":2}', b'\xff'):
            path.write_bytes(raw)
            settings, notice = load_settings(self.root)
            self.assertEqual(settings, default_settings())
            self.assertIn("modo Local", notice)
            self.assertEqual(path.read_bytes(), raw)

    def test_replacement_failure_preserves_previous_file_and_cleans_temp(self):
        path = save_settings(self.root, self.example())
        before = path.read_bytes()
        with patch("conversion.connection_settings.os.replace", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                save_settings(self.root, default_settings())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
