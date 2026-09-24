from pathlib import Path
from unittest import TestCase

from conversion.selector import save_selection, use_path
from support import workspace_temp


class SelectorTests(TestCase):
    def setUp(self):
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_folder_selection_creates_bounded_inventory(self):
        source, output = self.base / "origen", self.base / "salida"
        source.mkdir()
        for number in range(3):
            (source / f"file-{number}.txt").write_text(str(number), encoding="utf-8")
        result = use_path(output, source, limit=2)
        self.assertEqual(result["status"], "inventariado")
        self.assertEqual(result["archivos_revisados"], 2)
        self.assertTrue(result["se_corto_por_limite"])
        self.assertTrue(Path(result["inventario"]).exists())

    def test_text_file_selection_converts_it(self):
        source = self.base / "nota.md"
        source.write_text("# Título\n\nContenido", encoding="utf-8")
        result = use_path(self.base / "salida", source)
        self.assertIn(result["status"], {"revisar", "completo"})
        self.assertEqual(result["seleccion"], str(source.resolve()))

    def test_unsupported_file_is_saved_without_false_conversion(self):
        source = self.base / "modelo.bin"
        source.write_bytes(b"\x00\x01\x02")
        output = self.base / "salida"
        result = use_path(output, source)
        self.assertEqual(result["status"], "seleccionado")
        self.assertTrue((output / "seleccion.json").exists())

    def test_missing_path_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            save_selection(self.base / "salida", self.base / "no-existe")
