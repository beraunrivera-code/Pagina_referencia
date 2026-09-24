"""Puente fab con dobles: nunca runas, firmas/ACL reales, C:\\FABRICA ni IA."""
from contextlib import ExitStack, nullcontext
import hashlib
import json
from pathlib import Path
import subprocess
from unittest import TestCase
from unittest.mock import patch

from conversion import fab_bridge as bridge
from support import workspace_temp


class FabBridgeTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.folder = self.root / "intento"
        self.folder.mkdir()
        for name in bridge.PACKAGE_FILES:
            (self.folder / name).write_bytes(b"dato autorizado, no argumento de proceso")
        (self.folder / "secreto.env").write_text("NO COPIAR", encoding="utf-8")
        self.binary = self.root / "agy-fixture.exe"
        self.binary.write_bytes(b"fixture solamente")
        self.binary_hash = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.base = self.root / "espacio compartido"
        self.acl = self.acl_evidence()
        self.payload = {"status": "SUCCESS", "response": "{}", "model": "modelo-explicito"}

    def acl_evidence(self):
        current, fab = "S-1-5-21-101", "S-1-5-21-102"
        return {"current": current, "fab": fab, "owner": current, "protected": True,
                "rules": [{"sid": sid, "rights": 2032127, "inherited": False, "type": "Allow",
                           "inheritance": 3, "propagation": 0}
                          for sid in (current, fab, "S-1-5-18", "S-1-5-32-544")]}

    def finish(self, stage, profile):
        control = json.loads((stage / "control.json").read_text())
        raw = json.dumps(self.payload).encode()
        (stage / "stdout.json").write_bytes(raw)
        (stage / "stderr.txt").write_bytes(b"")
        receipt = {"nonce": control["nonce"], "sid": self.acl["fab"], "state": "finished", "exit_code": 0,
                   "stdout_sha256": hashlib.sha256(raw).hexdigest()}
        (stage / "fin.json").write_text(json.dumps(receipt))

    def fixtures(self):
        stack = ExitStack()
        stack.enter_context(patch.object(bridge, "_locked_path", side_effect=lambda *a, **kw: nullcontext()))
        stack.enter_context(patch.object(bridge, "verified_binary", return_value=(self.binary, self.binary_hash)))
        stack.enter_context(patch.object(bridge, "_secure_folder", return_value=self.acl))
        self.launch = stack.enter_context(patch.object(bridge, "_launch", side_effect=self.finish))
        return stack

    def run_bridge(self, **kwargs):
        return bridge.run_fab("fab3", "modelo-explicito", self.folder, 5,
                              executable=self.binary, expected_hash=self.binary_hash, shared_base=self.base, **kwargs)

    def test_explicit_profile_uses_only_three_authorized_files(self):
        with self.fixtures():
            result = self.run_bridge()
        self.assertEqual(result, self.payload)
        self.assertEqual(self.launch.call_count, 1)
        stage, profile = self.launch.call_args.args
        self.assertEqual(profile, "fab3")
        self.assertFalse((stage / "secreto.env").exists())
        self.assertEqual((stage / "agy.exe").read_bytes(), self.binary.read_bytes())
        self.assertEqual(json.loads((self.folder / "stdout.json").read_text()), self.payload)

    def test_no_package_or_helper_exists_when_acl_is_applied(self):
        def secure(stage, profile):
            self.assertEqual(list(stage.iterdir()), [])
            return self.acl
        with self.fixtures(), patch.object(bridge, "_secure_folder", side_effect=secure):
            self.run_bridge()

    def test_acl_failure_copies_no_documents_and_does_not_dispatch(self):
        with self.fixtures(), patch.object(bridge, "_secure_folder", side_effect=bridge.FabBridgeFault("fab_acl", "ACL")):
            with self.assertRaises(bridge.FabBridgeFault):
                self.run_bridge()
            self.launch.assert_not_called()
        self.assertTrue(all(not list(path.iterdir()) for path in self.base.iterdir()))
        self.assertFalse((self.folder / "fab-bridge.json").exists())

    def test_same_attempt_never_relaunches_even_after_success(self):
        with self.fixtures():
            self.run_bridge()
            with self.assertRaises(bridge.FabBridgeFault) as failure:
                self.run_bridge()
            self.assertEqual(failure.exception.code, "fab_prior_attempt")
            self.assertEqual(self.launch.call_count, 1)

    def test_timeout_is_unknown_preserved_and_never_retried(self):
        with self.fixtures(), patch.object(bridge, "_wait_result", side_effect=bridge.FabBridgeFault("cli_timeout", "consumo desconocido")):
            with self.assertRaisesRegex(bridge.FabBridgeFault, "desconocido"):
                self.run_bridge()
            stage = self.launch.call_args.args[0]
            self.assertTrue(stage.exists())
            with self.assertRaises(bridge.FabBridgeFault) as failure:
                self.run_bridge()
            self.assertEqual(failure.exception.code, "fab_prior_attempt")
            self.assertEqual(self.launch.call_count, 1)

    def test_receipt_boolean_false_is_not_exit_zero(self):
        with self.fixtures():
            def finish_invalid(stage, profile):
                self.finish(stage, profile)
                path = stage / "fin.json"
                receipt = json.loads(path.read_text())
                receipt["exit_code"] = False
                path.write_text(json.dumps(receipt))
            self.launch.side_effect = finish_invalid
            with self.assertRaises(bridge.FabBridgeFault) as failure:
                self.run_bridge()
            self.assertEqual(failure.exception.code, "fab_receipt")

    def test_nonce_or_identity_mismatch_rejected(self):
        for field in ("nonce", "sid", "stdout_sha256"):
            stage = self.root / field
            stage.mkdir()
            raw = b'{"status":"SUCCESS"}'
            (stage / "stdout.json").write_bytes(raw)
            receipt = {"nonce": "nonce", "sid": "sid", "state": "finished", "exit_code": 0,
                       "stdout_sha256": hashlib.sha256(raw).hexdigest(), field: "incorrecto"}
            (stage / "fin.json").write_text(json.dumps(receipt))
            with self.assertRaises(bridge.FabBridgeFault):
                bridge._wait_result(stage, "nonce", "sid", 5)

    def test_reported_model_mismatch_has_no_fallback(self):
        self.payload["model"] = "otro-modelo"
        with self.fixtures():
            with self.assertRaises(bridge.FabBridgeFault) as failure:
                self.run_bridge()
            self.assertEqual(failure.exception.code, "fab_model_mismatch")
            self.assertEqual(self.launch.call_count, 1)
        self.assertTrue((self.folder / "fab-bridge.json").exists())

    def test_profile_model_injection_and_bad_time_rejected_before_io(self):
        values = [("fab1 & whoami", "modelo", 5), ("fab1", "a\";whoami", 5),
                  ("fab1", "-p=oculto", 5), ("fab1", "a\nb", 5), ("fab1", "modelo", True)]
        with patch.object(bridge, "_launch") as launch, patch.object(bridge, "_locked_path") as lock:
            for profile, model, timeout in values:
                with self.assertRaises(bridge.FabBridgeFault):
                    bridge.run_fab(profile, model, self.folder, timeout)
            launch.assert_not_called()
            lock.assert_not_called()

    def test_runas_uses_shell_false_no_document_argv_and_no_api_environment(self):
        stage = self.base / ("a" * 32)
        with (patch.object(bridge.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
              patch.dict(bridge.os.environ, {"ANTHROPIC_API_KEY": "fixture-secret", "CUSTOM_SECRET": "fixture-secret"})):
            bridge._launch(stage, "fab3")
        args, options = run.call_args.args[0], run.call_args.kwargs
        self.assertIn("/user:.\\fab3", args)
        self.assertIn("/savecred", args)
        self.assertIn("/profile", args)
        self.assertIs(options["shell"], False)
        self.assertNotIn("dato autorizado", str(args))
        self.assertNotIn("fixture-secret", str(options["env"]))
        self.assertIn('"', args[-1])  # ruta elegida contiene espacios, se entrecomilla

    def test_invalid_acl_cannot_be_reported_as_ready(self):
        stage = self.base / ("b" * 32)
        evidence = self.acl_evidence()
        for field, bad in (("protected", False), ("owner", "S-1-5-21-999")):
            invalid = {**evidence, field: bad}
            with patch.object(bridge, "_powershell", return_value=invalid):
                with self.assertRaises(bridge.FabBridgeFault):
                    bridge._secure_folder(stage, "fab1")
        evidence["rules"].append({"sid": "S-1-1-0", "rights": 2032127})
        with patch.object(bridge, "_powershell", return_value=evidence):
            with self.assertRaises(bridge.FabBridgeFault):
                bridge._secure_folder(stage, "fab1")

    def test_valid_acl_exactly_four_identities(self):
        with patch.object(bridge, "_powershell", return_value=self.acl):
            result = bridge._secure_folder(self.base / ("c" * 32), "fab1")
        self.assertEqual(result, self.acl)

    def test_wait_without_receipt_timeout_no_second_launch(self):
        stage = self.root / "empty"
        stage.mkdir()
        with patch.object(bridge.time, "monotonic", side_effect=[0, 30]), patch.object(bridge, "_launch") as launch:
            with self.assertRaises(bridge.FabBridgeFault) as failure:
                bridge._wait_result(stage, "n", "s", 5)
        self.assertEqual(failure.exception.code, "cli_timeout")
        launch.assert_not_called()

    def test_portable_binary_needs_explicit_hash_and_valid_google_signature(self):
        with self.assertRaises(bridge.FabBridgeFault) as failure:
            bridge.verified_binary(self.binary)
        self.assertEqual(failure.exception.code, "fab_binary_unverified")
        with patch.object(bridge, "_powershell", return_value={"status": "Valid", "subject": "CN=Google LLC, O=Google LLC"}):
            self.assertEqual(bridge.verified_binary(self.binary, self.binary_hash), (self.binary, self.binary_hash))
        with patch.object(bridge, "_powershell", return_value={"status": "NotSigned", "subject": ""}):
            with self.assertRaises(bridge.FabBridgeFault):
                bridge.verified_binary(self.binary, self.binary_hash)

    def test_oversized_output_rejected_without_reading_all(self):
        path = self.root / "large"
        path.write_bytes(b"abcd")
        with self.assertRaises(bridge.FabBridgeFault) as failure:
            bridge._bounded(path, 3)
        self.assertEqual(failure.exception.code, "response_size")
