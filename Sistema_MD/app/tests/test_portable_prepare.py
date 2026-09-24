import importlib.util
import io
import json
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from support import workspace_temp

spec = importlib.util.spec_from_file_location('portable_prepare', Path(__file__).resolve().parents[2] / 'preparar_equipo.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)

    def test_inspection_does_not_create_or_install(self):
        with patch.object(prepare,'ROOT',self.root), patch.object(prepare.subprocess,'run') as run, redirect_stdout(io.StringIO()):
            self.assertEqual(prepare.main([]),0)
        run.assert_not_called()
        self.assertFalse((self.root / '.venv-local').exists())

    def test_failed_install_can_resume_without_deleting_environment(self):
        with patch.object(prepare,'ROOT',self.root), patch.object(prepare.sys,'version_info',(3,12)), \
             patch.object(prepare.importlib.util,'find_spec',return_value=object()), \
             patch.object(prepare.venv,'EnvBuilder') as builder, \
             patch.object(prepare.subprocess,'run',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(returncode=0),SimpleNamespace(returncode=0)]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(prepare.main(['--instalar']),1)
            target = self.root / '.venv-local'
            sentinel = target / 'conservar.txt'
            sentinel.write_text('se conserva',encoding='utf-8')
            self.assertFalse((target / 'sistema_md_ready.json').exists())
            self.assertEqual(prepare.main(['--instalar','--reanudar']),0)
            self.assertEqual(sentinel.read_text(),'se conserva')
            self.assertTrue((target / 'sistema_md_ready.json').exists())
            self.assertTrue(all(call.kwargs['clear'] is False for call in builder.call_args_list))

    def test_resume_rejects_foreign_environment(self):
        target = self.root / '.venv-local'
        target.mkdir()
        (target / 'sistema_md_preparacion.json').write_text(json.dumps({'installation_root':'otra PC'}))
        with patch.object(prepare,'ROOT',self.root), patch.object(prepare.sys,'version_info',(3,12)), \
             patch.object(prepare.subprocess,'run') as run, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            prepare.main(['--instalar','--reanudar'])
        run.assert_not_called()
