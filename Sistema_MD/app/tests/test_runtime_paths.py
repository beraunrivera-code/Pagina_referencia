import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from support import workspace_temp
from conversion.runtime_paths import data_root, export_root, configure_data_root, configure_export_root, find_oda


class RuntimePathTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.app = self.root / "app"
        self.app.mkdir()

    def test_default_tracks_installation_without_writing_config(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(data_root(self.app), self.app / 'datos')
            self.assertEqual(export_root(self.app, self.app / 'datos'), self.root / 'MD')
        self.assertFalse((self.root / 'sistema_md.json').exists())

    def test_relocation_keeps_relative_data_and_export_paths(self):
        data = self.root / 'Mis documentos'
        data.mkdir()
        configure_data_root(self.app, data)
        (data / 'exportacion.json').write_text(json.dumps({'carpeta_md': 'MD'}), encoding='utf-8')
        other = self.root / 'otra PC'
        other.mkdir()
        (other / 'app').mkdir()
        (other / 'Mis documentos').mkdir()
        (other / 'sistema_md.json').write_bytes((self.root / 'sistema_md.json').read_bytes())
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(data_root(other / 'app'), other / 'Mis documentos')
        self.assertEqual(export_root(self.app, data), data / 'MD')

    def test_corrupt_config_not_overwritten(self):
        config = self.root / 'sistema_md.json'
        config.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            configure_data_root(self.app, self.root)
        self.assertEqual(config.read_text(), '{broken')

    def test_missing_configured_drive_does_not_create_an_empty_library(self):
        (self.root / 'sistema_md.json').write_text(json.dumps({'schema_version':1,'data_dir':'unidad-ausente'}), encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(ValueError):
            data_root(self.app)
        self.assertFalse((self.root / 'unidad-ausente').exists())

    def test_oda_is_discovered_not_specific_version(self):
        with patch.dict('os.environ', {}, clear=True), patch('conversion.runtime_paths.shutil.which', return_value='D:/Tools/ODA.exe'):
            self.assertEqual(find_oda(), 'D:/Tools/ODA.exe')

    def test_export_selection_inside_program_travels_with_copy(self):
        data = self.app / 'datos'
        output = self.root / 'Mis MD'
        output.mkdir()
        configure_export_root(self.app, data, output)
        setting = json.loads((data / 'exportacion.json').read_text())
        self.assertFalse(Path(setting['carpeta_md']).is_absolute())
        other = self.root / 'otra copia'
        other_data = other / 'app' / 'datos'
        other_data.mkdir(parents=True)
        (other / 'Mis MD').mkdir()
        (other_data / 'exportacion.json').write_bytes((data / 'exportacion.json').read_bytes())
        self.assertEqual(export_root(other / 'app', other_data), other / 'Mis MD')

    def test_export_selection_preserves_corrupt_config(self):
        data = self.app / 'datos'
        data.mkdir()
        config = data / 'exportacion.json'
        config.write_text('{bad', encoding='utf-8')
        with self.assertRaises(ValueError):
            configure_export_root(self.app, data, self.root)
        self.assertEqual(config.read_text(), '{bad')

    def test_absolute_export_missing_even_with_parent_fails_closed(self):
        data = self.app / 'datos'
        data.mkdir()
        missing = self.root / 'antigua salida'
        (data / 'exportacion.json').write_text(json.dumps({'carpeta_md':str(missing)}), encoding='utf-8')
        with self.assertRaises(ValueError):
            export_root(self.app, data)
        self.assertFalse(missing.exists())

    def test_environment_override_cannot_silently_ignore_new_selection(self):
        forced = self.root / 'forzada'
        selected = self.root / 'elegida'
        forced.mkdir()
        selected.mkdir()
        with patch.dict('os.environ',{'SISTEMA_MD_DATA':str(forced)}), self.assertRaisesRegex(ValueError,'SISTEMA_MD_DATA'):
            configure_data_root(self.app, selected)
        self.assertFalse((self.root / 'sistema_md.json').exists())
