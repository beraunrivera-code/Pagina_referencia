"""Ejecución nativa en Linux/contenedor: sin winreg, os.startfile, PowerShell ni escritorio."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from conversion import native, platform_support, runtime_paths, semantic_judgments
from conversion.access import launch_login
from support import workspace_temp


class SystemOpenTests(TestCase):
    def test_headless_only_logs_the_generated_file(self):
        with patch.object(platform_support, "is_windows", return_value=False), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(platform_support.sys, "platform", "linux"), \
                patch.object(platform_support.subprocess, "Popen") as popen, \
                self.assertLogs("sistema_md", level="INFO") as log:
            self.assertEqual(platform_support.open_with_system("/datos/vista.html"), "headless")
        popen.assert_not_called()
        self.assertIn("Modo headless activo: archivo generado en /datos/vista.html", log.output[0])

    def test_desktop_uses_xdg_open_without_shell_or_blocking(self):
        with patch.object(platform_support, "is_windows", return_value=False), \
                patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True), \
                patch.object(platform_support.sys, "platform", "linux"), \
                patch.object(platform_support.shutil, "which", return_value="/usr/bin/xdg-open"), \
                patch.object(platform_support.subprocess, "Popen") as popen:
            self.assertEqual(platform_support.open_with_system(Path("/datos/documento.md")), "xdg-open")
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", "/datos/documento.md"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_display_without_xdg_open_degrades_to_headless(self):
        with patch.object(platform_support, "is_windows", return_value=False), \
                patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
                patch.object(platform_support.sys, "platform", "linux"), \
                patch.object(platform_support.shutil, "which", return_value=None), \
                patch.object(platform_support.subprocess, "Popen") as popen:
            self.assertEqual(platform_support.open_with_system("/x.html"), "headless")
        popen.assert_not_called()

    def test_windows_keeps_the_user_association(self):
        with patch.object(platform_support, "is_windows", return_value=True), \
                patch.object(platform_support.os, "startfile", create=True) as startfile:
            self.assertEqual(platform_support.open_with_system("file:///C:/vista.html#a"), "asociacion")
        startfile.assert_called_once_with("file:///C:/vista.html#a")

    def test_reveal_in_folder_opens_parent_on_posix(self):
        with patch.object(platform_support, "is_windows", return_value=False), \
                patch.object(platform_support, "open_with_system", return_value="headless") as opener:
            self.assertEqual(platform_support.reveal_in_folder(Path("/datos/doc/documento.md")), "headless")
        opener.assert_called_once_with(Path("/datos/doc"))


class OdaLinuxTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)

    def executable(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_explicit_variable_wins(self):
        with patch.dict(os.environ, {"SISTEMA_MD_ODA": str(self.root / "oda")}, clear=True):
            self.assertEqual(runtime_paths.find_oda(), str((self.root / "oda").resolve()))

    def test_standard_linux_locations_are_detected(self):
        if os.name == "nt":
            self.skipTest("Rutas estándar de Linux")
        opt = self.executable("opt/ODAFileConverter/ODAFileConverter")
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(runtime_paths.shutil, "which", return_value=None), \
                patch.object(runtime_paths, "ODA_LINUX_PATHS", ("/no/existe", str(opt))):
            self.assertEqual(runtime_paths.find_oda(), str(opt))

    def test_versioned_deb_folder_is_detected(self):
        if os.name == "nt":
            self.skipTest("Rutas estándar de Linux")
        versioned = self.executable("usr/bin/ODAFileConverter_QT6_lnxX64_8.3dll_25.12/ODAFileConverter")
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(runtime_paths.shutil, "which", return_value=None), \
                patch.object(runtime_paths, "ODA_LINUX_PATHS", ()), \
                patch.object(runtime_paths, "ODA_LINUX_GLOBS", ((str(self.root / "usr/bin"), "ODAFileConverter_*/ODAFileConverter"),)):
            self.assertEqual(runtime_paths.find_oda(), str(versioned))

    def test_headless_linux_wraps_oda_with_xvfb_run(self):
        with patch.object(native.os, "name", "posix"), patch.dict(os.environ, {}, clear=True), \
                patch("shutil.which", return_value="/usr/bin/xvfb-run"):
            command, environment = native._oda_command(["in", "out", "ACAD2018", "DXF", "0", "1"], exe="/usr/bin/ODAFileConverter")
        self.assertEqual(command[:3], ["/usr/bin/xvfb-run", "-a", "/usr/bin/ODAFileConverter"])
        self.assertIsNone(environment)

    def test_headless_without_xvfb_requests_qt_offscreen(self):
        with patch.object(native.os, "name", "posix"), patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True), \
                patch("shutil.which", return_value=None):
            command, environment = native._oda_command(["in", "out"], exe="/opt/oda")
        self.assertEqual(command, ["/opt/oda", "in", "out"])
        self.assertEqual(environment["QT_QPA_PLATFORM"], "offscreen")

    def test_desktop_session_runs_oda_directly(self):
        with patch.object(native.os, "name", "posix"), patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
            command, environment = native._oda_command(["in"], exe="/opt/oda")
        self.assertEqual((command, environment), (["/opt/oda", "in"], None))


class LinuxSecretsAndLoginTests(TestCase):
    def test_typesafe_key_comes_from_environment_without_registry(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": " clave-fixture "}, clear=True), \
                patch.dict(sys.modules, {"winreg": None}):
            self.assertEqual(semantic_judgments._key(), "clave-fixture")

    def test_missing_typesafe_key_is_explicit_off_windows(self):
        if os.name == "nt":
            self.skipTest("Fuera de Windows no hay registro de usuario")
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"winreg": None}):
            with self.assertRaises(semantic_judgments.JudgmentError):
                semantic_judgments._key()

    def test_posix_login_inherits_terminal_and_rejects_windows_fab_identity(self):
        if os.name == "nt":
            self.skipTest("Comportamiento POSIX")
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        with patch("conversion.access.cli_binary", return_value=["/usr/bin/claude"]), \
                patch("conversion.access.cli_environment", return_value={"CLAUDE_CONFIG_DIR": str(Path(fixture.name) / "p")}), \
                patch("conversion.access.subprocess.Popen", return_value=SimpleNamespace(pid=7)) as popen:
            result = launch_login(Path(fixture.name), "claude-cli", "cuenta-2")
            self.assertNotIn("creationflags", popen.call_args.kwargs)
            self.assertIn("terminal", result["notice"])
            with self.assertRaises(ValueError):
                launch_login(Path(fixture.name), "antigravity-cli", "fab1")
        self.assertEqual(popen.call_count, 1)

    def test_cli_profiles_use_xdg_data_home_without_localappdata(self):
        if os.name == "nt":
            self.skipTest("Comportamiento POSIX")
        from conversion.providers import cli_profile_dir
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/home/u/.local/share"}, clear=True):
            path = cli_profile_dir("codex-cli", "cuenta-1")
        self.assertEqual(path, Path("/home/u/.local/share/SistemaMD/perfiles_cli/codex-cli/cuenta-1"))

    def test_venv_interpreter_symlink_is_not_resolved_out_of_the_venv(self):
        if os.name == "nt":
            self.skipTest("En Windows python.exe de la venv es una copia, no un enlace")
        from conversion import local_engines
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        venv_python = Path(fixture.name) / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        self.assertEqual(local_engines.engine_python("markitdown", venv_python), venv_python)
