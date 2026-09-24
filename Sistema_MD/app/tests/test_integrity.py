import json
import subprocess
import sys
import unittest
from pathlib import Path

from conversion.documents import digest, json_bytes, parse_pages
from conversion.pipeline import convert_text, detect, import_pages
from conversion.storage import publish, verify_artifacts
from support import workspace_temp


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_missing_requested_image_is_visible_in_qa(self):
        pages = self.base / "pages"
        images = self.base / "images"
        pages.mkdir()
        images.mkdir()
        (pages / "DOC_p01.md").write_text("## Testigo (pág. 1)\n> Texto\n", encoding="utf-8")
        result = import_pages(self.base / "out", pages, "DOC", [1], "fixture", "Control", images)
        self.assertEqual(result["received"], 1)
        self.assertEqual(result["status"], "parcial")
        self.assertIn("Falta imagen solicitada de unidad 1", result["errors"])

    def test_missing_image_directory_is_rejected(self):
        with self.assertRaises(OSError):
            import_pages(self.base / "out", self.base, "DOC", [1], "fixture", "Control", self.base / "absent")

    def test_image_cannot_disappear_from_manifest(self):
        image = b"synthetic-png-fixture"
        doc = {"schema_version": 1, "title": "Control", "expected_units": [1],
               "units": [{"number": 1, "image_asset": "imagenes/p1.png", "image_sha256": digest(image),
                          "blocks": [{"id": "b1", "kind": "paragraph", "text": "Control"}]}]}
        result = publish(self.base / "out", doc, {"imagenes/p1.png": image})
        folder = Path(result["output"])
        self.assertTrue(verify_artifacts(folder))
        manifest = json.loads((folder / "manifest.json").read_bytes())
        del manifest["files"]["imagenes/p1.png"]
        (folder / "manifest.json").write_bytes(json_bytes(manifest))
        (folder / "imagenes/p1.png").unlink()
        self.assertFalse(verify_artifacts(folder))
        repaired = publish(self.base / "out", doc, {"imagenes/p1.png": image})
        self.assertFalse(repaired["reused"])
        self.assertTrue(verify_artifacts(Path(repaired["output"])))

    def test_utf8_character_split_at_probe_boundary_is_not_binary(self):
        path = self.base / "español.md"
        path.write_bytes(b"a" * 1023 + "ñ\n".encode("utf-8"))
        self.assertEqual(detect(path), "md")
        result = convert_text(self.base / "out", path)
        self.assertTrue(verify_artifacts(Path(result["output"])))

    def test_empty_text_does_not_publish(self):
        for text in ("", "\ufeff", " \n\t"):
            with self.subTest(text=text):
                path = self.base / "vacio.txt"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    convert_text(self.base / "out", path)
        self.assertFalse((self.base / "out").exists())

    def test_bad_scope_is_rejected(self):
        for invalid in ("", "0", "-1", "3-1", "1,1", "1-3,2", "1-100001", "abc"):
            with self.subTest(scope=invalid):
                with self.assertRaises(ValueError):
                    parse_pages(invalid)
        self.assertEqual(parse_pages("10,2,3-5"), [2, 3, 4, 5, 10])

    def test_three_processes_publish_one_result(self):
        source = self.base / "concurrente.txt"
        source.write_text("Mismo documento de prueba.\n", encoding="utf-8")
        args = [sys.executable, "-m", "conversion", "--salida", str(self.base / "out"),
                "convertir", str(source)]
        processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     cwd=Path(__file__).resolve().parents[1]) for _ in range(3)]
        try:
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stderr.decode("utf-8", errors="replace"))
                results.append(json.loads(stdout))
            self.assertEqual(len({r["id"] for r in results}), 1)
            self.assertEqual(sum(not r["reused"] for r in results), 1)
            self.assertEqual(len(list((self.base / "out" / "documentos").iterdir())), 1)
            self.assertTrue(verify_artifacts(Path(results[0]["output"])))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
