import json
import unittest
from pathlib import Path

from conversion.jobs import import_answer, prepare_job
from conversion.storage import publish
from conversion.documents import digest
from support import workspace_temp


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        doc = {"schema_version": 1, "title": "Control sintético", "input_kind": "pdf_prepared",
               "expected_units": [2], "units": [{"number": 2, "image_asset": "imagenes/p0002.png",
                   "image_sha256": digest(b"synthetic-fixture"),
                   "blocks": [{"id": "p2-b1", "kind": "paragraph", "text": "Control\n"}]}]}
        package = publish(self.root / "out", doc, {"imagenes/p0002.png": b"synthetic-fixture"})
        task = prepare_job(self.root / "out", Path(package["output"]), 2)
        self.job = Path(task["folder"])
        self.answer = {"job_id": task["job_id"], "unit": 2,
                       "blocks": [{"kind": "paragraph", "text": "Dato 165 mm"},
                                  {"kind": "paragraph", "text": "Segundo párrafo"}],
                       "warnings": ["Control sintético, no inferencia"]}

    def submit(self):
        path = self.root / "answer.json"
        path.write_text(json.dumps(self.answer), encoding="utf-8")
        return import_answer(self.root / "out", self.job, path, "test-fixture")

    def test_response_is_structural_not_semantic_and_keeps_paragraphs(self):
        result = self.submit()
        self.assertTrue(result["structural_ok"])
        self.assertFalse(result["semantic_verified"])
        self.assertEqual(result["status"], "revisar")
        markdown = (Path(result["output"]) / "documento.md").read_text(encoding="utf-8")
        self.assertIn("Dato 165 mm\n\nSegundo párrafo", markdown)
        self.assertIn("Control sintético, no inferencia", result["warnings"])

    def test_wrong_job_rejected(self):
        self.answer["job_id"] = "another-job"
        with self.assertRaises(ValueError):
            self.submit()

    def test_wrong_unit_rejected(self):
        self.answer["unit"] = 3
        with self.assertRaises(ValueError):
            self.submit()

    def test_empty_blocks_rejected(self):
        self.answer["blocks"] = []
        with self.assertRaises(ValueError):
            self.submit()

    def test_model_cannot_add_resource_path(self):
        self.answer["blocks"][0]["path"] = "../../elsewhere"
        with self.assertRaises(ValueError):
            self.submit()

    def test_job_tampering_rejected(self):
        (self.job / "pagina.png").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.submit()

    def test_same_page_reuses_deterministic_job_without_rewriting(self):
        before = (self.job / "paquete.json").stat().st_mtime_ns
        second = prepare_job(self.root / "out", next((self.root / "out" / "documentos").iterdir()), 2)
        self.assertEqual(second["job_id"], self.job.name)
        self.assertTrue(second["reused"])
        self.assertEqual((self.job / "paquete.json").stat().st_mtime_ns, before)

    def test_duplicate_keys_are_rejected_at_every_depth(self):
        for duplicate in ('"blocks": [], ', '"warnings": [], '):
            with self.subTest(duplicate=duplicate):
                path = self.root / "answer.json"
                path.write_text("{" + duplicate + json.dumps(self.answer)[1:], encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicada"):
                    import_answer(self.root / "out", self.job, path, "fixture")
        path.write_text(json.dumps(self.answer).replace('"text": "Dato 165 mm"',
                         '"text": "Dato 166 mm", "text": "Dato 165 mm"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicada"):
            import_answer(self.root / "out", self.job, path, "fixture")


if __name__ == "__main__":
    unittest.main()
