import hashlib
import importlib.util
import json
from pathlib import Path
from unittest import TestCase
from support import workspace_temp

spec = importlib.util.spec_from_file_location('portable_probe', Path(__file__).resolve().parents[2] / 'PROBAR_EQUIPO.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class PortableProbeTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)

    def manifest(self, files):
        (self.root/'MANIFIESTO_PAQUETE.json').write_text(json.dumps({'schema_version':1,'files':files}),encoding='utf-8')

    def test_verified_hash_then_tamper_then_missing(self):
        target = self.root / 'control.txt'
        target.write_bytes(b'control')
        self.manifest({'control.txt':hashlib.sha256(b'control').hexdigest()})
        self.assertTrue(probe.verify_package(self.root)['ok'])
        target.write_bytes(b'changed')
        self.assertFalse(probe.verify_package(self.root)['ok'])
        target.unlink()
        self.assertEqual(probe.verify_package(self.root)['problems'][0]['status'],'ausente')

    def test_path_escape_rejected_before_reading(self):
        self.manifest({'../outside.txt':'0'*64})
        with self.assertRaises(ValueError):
            probe.verify_package(self.root)

    def test_existing_output_is_never_overwritten(self):
        (self.root/'personal.txt').write_text('conservar',encoding='utf-8')
        with self.assertRaises(FileExistsError):
            probe.run_control(self.root)
        self.assertEqual((self.root/'personal.txt').read_text(),'conservar')
