"""Preparación/reanudación con procesos simulados; no instala nada."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest import TestCase
from unittest.mock import patch

from conversion.local_engines import WORKER
from tests.support import workspace_temp


class WorkerBootstrapTests(TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location("worker_bootstrap", WORKER.with_name("prepare_markitdown.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.module.ROOT = self.folder
        self.module.TARGET = self.folder / "workers/markitdown/.venv"
        self.arguments = ["--python", sys.executable, "--install"]

    def test_interrupted_install_resumes_owned_worker_without_recreating_venv(self):
        module = self.module
        def fail_install(command, **kwargs):
            if command[2] == "-c":
                return subprocess.CompletedProcess(command, 0, stdout='[[3, 12], 64]')
            if "venv" in command:
                executable = module.TARGET / "Scripts/python.exe"
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"synthetic-do-not-execute")
                return subprocess.CompletedProcess(command, 0)
            raise subprocess.CalledProcessError(1, command)
        with patch.object(module.subprocess, "run", side_effect=fail_install), patch("builtins.print"):
            self.assertEqual(module.main(self.arguments), 2)
        self.assertEqual(json.loads(module._marker_path().read_text(encoding="utf-8"))["state"], "installing")
        def success(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout='[[3, 12], 64]')
        with patch.object(module.subprocess, "run", side_effect=success) as run, patch("builtins.print"):
            self.assertEqual(module.main(self.arguments + ["--resume"]), 0)
        self.assertFalse(any("venv" in call.args[0] for call in run.call_args_list))
        self.assertEqual(json.loads(module._marker_path().read_text(encoding="utf-8"))["state"], "ready")

    def test_resume_rejects_ready_missing_marker_and_changed_owner(self):
        module = self.module
        for mode in ("missing", "ready", "wrong_root", "wrong_python", "wrong_lock"):
            with self.subTest(mode=mode):
                marker = module._marker_value(sys.executable, "installing")
                if mode == "ready":
                    marker["state"] = "ready"
                elif mode == "wrong_root":
                    marker["root"] = str(self.folder / "other")
                elif mode == "wrong_python":
                    marker["base_python"] = str(self.folder / "python.exe")
                elif mode == "wrong_lock":
                    marker["lock_sha256"] = "wrong"
                if mode != "missing":
                    module._write_marker(marker)
                with patch.object(module.subprocess, "run") as run, patch("builtins.print"):
                    self.assertEqual(module.main(self.arguments + ["--resume"]), 2)
                    run.assert_not_called()

    def test_copied_ready_receipt_without_runtime_prepares_new_and_preserves_old(self):
        module = self.module
        receipt = module._marker_value(sys.executable, 'ready')
        receipt['root'] = 'D:/otra-PC'
        module._write_marker(receipt)
        def success(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout='[[3, 12], 64]')
        with patch.object(module.subprocess, 'run', side_effect=success) as run, patch('builtins.print'):
            self.assertEqual(module.main(self.arguments), 0)
        self.assertTrue(any('venv' in call.args[0] for call in run.call_args_list))
        archives = list(module.TARGET.parent.glob('preparacion.anterior-*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text(encoding='utf-8')), receipt)
        self.assertEqual(json.loads(module._marker_path().read_text(encoding='utf-8'))['root'], str(self.folder.resolve()))
