"""Pruebas locales de integridad: fixtures sintéticas, sin servicios externos."""

import gc
import json
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile

import conversion
from conversion.documents import parse_markdown, parse_pages, render, validate
from conversion.pipeline import convert_text, detect, import_pages
from conversion.storage import report, verify_artifacts
from support import workspace_temp


class CoreTests(unittest.TestCase):
    def setUp(self):
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        # sqlite3 mantiene referencias cíclicas hasta la recolección en CPython.
        self.addCleanup(gc.collect)
        self.base = Path(temporary.name)
        self.root = self.base / "salidas"
        self.pages = self.base / "paginas"
        self.pages.mkdir()

    def page(self, number, text=None):
        path = self.pages / f"ejemplo_p{number}.md"
        path.write_text(
            text if text is not None else f"## Testigo (pág. {number})\n\nContenido {number}.\n",
            encoding="utf-8",
        )
        return path

    def import_scope(self, expected):
        return import_pages(self.root, self.pages, "ejemplo", expected,
                            "fixture-local", "Ejemplo sintético")

    def document(self, result):
        return json.loads((Path(result["output"]) / "document.json").read_text(encoding="utf-8"))

    def test_zip_signature_identifies_office_formats_despite_false_extensions(self):
        fixtures = (
            ("falso.txt", {"word/document.xml": "<document/>"}, "docx"),
            ("falso.pdf", {"xl/workbook.xml": "<workbook/>"}, "xlsx"),
            ("falso.docx", {"ppt/presentation.xml": "<presentation/>"}, "pptx"),
            ("falso.png", {"visio/document.xml": "<document/>"}, "vsdx"),
            ("generico.xlsx", {"nota.txt": "Texto"}, "zip"),
            ("ambiguo.docx", {"word/document.xml": "<doc/>", "xl/workbook.xml": "<wb/>"}, "zip-ambiguous"),
        )
        for name, members, expected in fixtures:
            with self.subTest(name=name):
                path = self.base / name
                with zipfile.ZipFile(path, "w") as archive:
                    for member, text in members.items():
                        archive.writestr(member, text)
                self.assertEqual(detect(path), expected)
                with self.assertRaises(ValueError):
                    convert_text(self.root, path)
        damaged = self.base / "roto.txt"
        damaged.write_bytes(b"PK\x03\x04archivo truncado")
        self.assertEqual(detect(damaged), "zip-damaged")
        with self.assertRaises(ValueError):
            convert_text(self.root, damaged)
        self.assertFalse(self.root.exists())

    def test_deleted_or_corrupted_output_is_detected_and_recovered(self):
        for name in ("documento.md", "document.json", "verificacion.json", "manifest.json"):
            for damage in ("delete", "alter"):
                with self.subTest(artifact=name, damage=damage):
                    source = self.base / f"{name}-{damage}.txt"
                    source.write_bytes(f"Aguja sintética: {name} {damage}.\n".encode("utf-8"))
                    first = convert_text(self.root, source)
                    folder = Path(first["output"])
                    target = folder / name
                    original = target.read_bytes()
                    if damage == "delete":
                        target.unlink()
                    else:
                        target.write_bytes(b"SALIDA CORRUPTA")
                    self.assertFalse(verify_artifacts(folder))
                    entry = next(row for row in report(self.root) if row["id"] == first["id"])
                    self.assertFalse(entry["integrity_ok"])
                    self.assertNotIn(first["id"], [row["id"] for row in report(self.root, "Aguja")])
                    recovered = convert_text(self.root, source)
                    self.assertFalse(recovered["reused"])
                    self.assertEqual(recovered["id"], first["id"])
                    self.assertNotEqual(recovered["output"], first["output"])
                    self.assertTrue(verify_artifacts(Path(recovered["output"])))
                    if name != "manifest.json":
                        self.assertEqual((Path(recovered["output"]) / name).read_bytes(), original)
                    self.assertTrue(folder.is_dir())
                    if damage == "delete":
                        self.assertFalse(target.exists())
                    else:
                        self.assertEqual(target.read_bytes(), b"SALIDA CORRUPTA")
                    indexed = [row for row in report(self.root) if row["id"] == first["id"]]
                    self.assertEqual(len(indexed), 1)
                    self.assertEqual(indexed[0]["output"], recovered["output"])
                    self.assertTrue(indexed[0]["integrity_ok"])
                    again = convert_text(self.root, source)
                    self.assertTrue(again["reused"])
                    self.assertEqual(again["output"], recovered["output"])

    def test_cache_is_separated_by_converter_version(self):
        source = self.base / "version.txt"
        source.write_bytes(b"Mismo contenido para dos versiones.\n")
        first = convert_text(self.root, source)
        next_version = conversion.__version__ + "+fixture-next"
        # Simula metadatos de otra release en un intérprete aislado; ejecuta
        # las funciones reales sin sustituir lógica ni editar módulos.
        script = (
            "import json, sys; from pathlib import Path; import conversion; "
            "conversion.__version__ = sys.argv[3]; "
            "from conversion.pipeline import convert_text; "
            "print(json.dumps(convert_text(Path(sys.argv[1]), Path(sys.argv[2]))))"
        )
        def run_next_version():
            process = subprocess.run(
                [sys.executable, "-c", script, str(self.root), str(source), next_version],
                cwd=Path(conversion.__file__).resolve().parent.parent,
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)

        changed = run_next_version()
        self.assertFalse(changed["reused"])
        self.assertNotEqual(first["id"], changed["id"])
        self.assertNotEqual(first["output"], changed["output"])
        for result, version in ((first, conversion.__version__), (changed, next_version)):
            manifest = json.loads((Path(result["output"]) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], version)
            self.assertTrue(verify_artifacts(Path(result["output"])))
        repeated = run_next_version()
        self.assertTrue(repeated["reused"])
        self.assertEqual(changed["output"], repeated["output"])
        original = convert_text(self.root, source)
        self.assertTrue(original["reused"])
        self.assertEqual(first["output"], original["output"])

    def test_unchanged_content_is_reused_but_changed_hash_creates_new_output(self):
        source = self.base / "contenido.txt"
        source.write_bytes(b"Contenido original.\n")
        first = convert_text(self.root, source)
        folder = Path(first["output"])
        snapshot = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
        again = convert_text(self.root, source)
        self.assertFalse(first["reused"])
        self.assertTrue(again["reused"])
        self.assertEqual((first["id"], first["output"]), (again["id"], again["output"]))
        self.assertEqual(snapshot, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()})
        source.write_bytes(b"Contenido alterado.\n")
        changed = convert_text(self.root, source)
        self.assertFalse(changed["reused"])
        self.assertNotEqual(first["id"], changed["id"])
        self.assertNotEqual(first["output"], changed["output"])
        self.assertNotEqual(self.document(first)["source_sha256"], self.document(changed)["source_sha256"])
        self.assertTrue(verify_artifacts(folder))
        self.assertTrue(verify_artifacts(Path(changed["output"])))
        self.assertEqual(len(report(self.root)), 2)

    def test_fenced_code_preserves_hashes_in_text_and_tables(self):
        for fence in ("```", "~~~~"):
            with self.subTest(fence=fence):
                code = (f"{fence}text\n# comentario literal\n\n"
                        "| # columna | valor |\n| --- | --- |\n| # dato | 10 |\n"
                        f"{fence}\n")
                text = "# Título real\n\n" + code + "\nTexto con # intacto.\n"
                source = self.base / "codigo.md"
                source.write_bytes(text.encode("utf-8"))
                blocks = parse_markdown(text, 1)
                self.assertEqual("".join(block["text"] for block in blocks), text)
                code_blocks = [block for block in blocks if block["kind"] == "code"]
                self.assertEqual(len(code_blocks), 1)
                self.assertIn(code, code_blocks[0]["text"])
                self.assertEqual([b["text"] for b in blocks if b["kind"] == "heading"],
                                 ["# Título real\n"])
                result = convert_text(self.root, source)
                doc = self.document(result)
                self.assertEqual("".join(b["text"] for b in doc["units"][0]["blocks"]), text)
                markdown = (Path(result["output"]) / "documento.md").read_text(encoding="utf-8")
                self.assertIn(text, markdown)
                self.assertEqual(markdown, render(doc, validate(doc)))

    def test_pages_two_and_ten_are_published_in_numeric_order(self):
        self.page(10)
        self.page(2)
        result = self.import_scope([10, 2])
        doc = self.document(result)
        self.assertEqual(doc["expected_units"], [2, 10])
        self.assertEqual([unit["number"] for unit in doc["units"]], [2, 10])
        markdown = (Path(result["output"]) / "documento.md").read_text(encoding="utf-8")
        for first, second in (("[Página/unidad 2]", "[Página/unidad 10]"),
                              ('<a id="unidad-2">', '<a id="unidad-10">'),
                              ("Contenido 2.", "Contenido 10.")):
            self.assertLess(markdown.index(first), markdown.index(second))

    def test_thirteen_of_thirteen_pages_still_require_semantic_review(self):
        for number in range(1, 14):
            self.page(number)
        result = self.import_scope(list(range(1, 14)))
        self.assertEqual(result["status"], "revisar")
        self.assertTrue(result["structural_ok"])
        self.assertFalse(result["semantic_verified"])
        self.assertEqual((result["received"], result["expected"]), (13, 13))
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["errors"], [])
        stored = json.loads((Path(result["output"]) / "verificacion.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, validate(self.document(result)))
        self.assertEqual(stored["status"], "revisar")
        self.assertFalse(stored["semantic_verified"])
        self.assertTrue(verify_artifacts(Path(result["output"])))

    def test_empty_page_is_reported_as_lost_and_keeps_output_partial(self):
        self.page(1)
        for empty in ("", " \n\t\n", "\ufeff"):
            with self.subTest(content=repr(empty)):
                self.page(2, empty)
                result = self.import_scope([1, 2])
                self.assertEqual(result["status"], "parcial")
                self.assertFalse(result["structural_ok"])
                self.assertEqual(result["missing"], [2])
                self.assertEqual(result["received"], 1)
                self.assertIn({"unit": 2, "reason": "empty"}, self.document(result)["skipped"])

    def test_missing_page_keeps_output_partial_and_records_the_loss(self):
        self.page(1)
        result = self.import_scope([1, 2])
        self.assertEqual(result["status"], "parcial")
        self.assertFalse(result["structural_ok"])
        self.assertFalse(result["semantic_verified"])
        self.assertEqual((result["received"], result["expected"]), (1, 2))
        self.assertEqual(result["missing"], [2])
        self.assertIn({"unit": 2, "reason": "missing"}, self.document(result)["skipped"])
        markdown = (Path(result["output"]) / "documento.md").read_text(encoding="utf-8")
        self.assertIn("[PENDIENTE: esta unidad no fue recibida.]", markdown)


if __name__ == "__main__":
    unittest.main()
