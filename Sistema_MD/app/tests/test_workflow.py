from pathlib import Path
from unittest import TestCase

from conversion.workflow import WorkQueue
from support import workspace_temp


class WorkflowTests(TestCase):
    def setUp(self):
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.output = self.base / "memoria"

    def test_folder_becomes_persistent_deduplicated_queue(self):
        source = self.base / "fuentes"
        source.mkdir()
        (source / "uno.md").write_text("# Uno", encoding="utf-8")
        (source / "dos.bin").write_bytes(b"\x00\x01")
        queue = WorkQueue(self.output)
        first = queue.add(source)
        second = queue.add(source)
        self.assertEqual(first["added"], 2)
        self.assertEqual(second["added"], 0)
        self.assertEqual(len(WorkQueue(self.output).items), 2)

    def test_text_processing_reuses_verified_result(self):
        source = self.base / "nota.md"
        source.write_text("# Nota\n\nDato verificable.", encoding="utf-8")
        queue = WorkQueue(self.output)
        queue.add(source)
        first = queue.process(str(source.resolve()))
        second = queue.process(str(source.resolve()))
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(queue.summary()["completed"], 1)

    def test_source_change_is_visible(self):
        source = self.base / "cambio.txt"
        source.write_text("Versión uno", encoding="utf-8")
        queue = WorkQueue(self.output)
        queue.add(source)
        source.write_text("Versión dos", encoding="utf-8")
        result = queue.process(str(source.resolve()))
        self.assertTrue(result["source_changed"])

    def test_unsupported_adapter_does_not_fake_completion(self):
        source = self.base / "modelo.bin"
        source.write_bytes(b"\x00\x01")
        queue = WorkQueue(self.output)
        queue.add(source)
        with self.assertRaisesRegex(ValueError, "todavía no está implementado"):
            queue.process(str(source.resolve()))
        self.assertEqual(queue.summary()["completed"], 0)
