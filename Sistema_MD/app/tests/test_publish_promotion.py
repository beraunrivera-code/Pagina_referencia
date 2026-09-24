"""Control determinista del bloqueo Windows observado al publicar un paquete."""
import errno
from contextlib import closing
from pathlib import Path
import sqlite3
from unittest import TestCase
from unittest.mock import patch

from conversion import storage
from support import workspace_temp


def windows_error(code):
    failure = PermissionError(errno.EACCES, "Bloqueo de promoción simulado")
    failure.winerror = code
    return failure


class PublishPromotionTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name) / "datos"
        self.doc = {"schema_version": 1, "title": "Control de promoción", "expected_units": [1],
                    "units": [{"number": 1, "blocks": [{"id": "p1-b1", "kind": "paragraph",
                                                           "text": "Contenido inmutable.\n"}]}]}

    def test_transient_win5_32_33_promotes_same_complete_stage_once(self):
        original = Path.rename
        observed = []
        errors = iter([5, 32, 33])
        def transient(stage, final):
            self.assertTrue(storage.verify_artifacts(stage))
            self.assertFalse(final.exists())
            observed.append((stage, final, (stage / "documento.md").read_bytes()))
            code = next(errors, None)
            if code is not None:
                raise windows_error(code)
            return original(stage, final)
        with (patch.object(Path, "rename", autospec=True, side_effect=transient) as rename,
              patch("time.sleep") as sleep,
              patch.object(storage, "validate", wraps=storage.validate) as validate,
              patch.object(storage, "render", wraps=storage.render) as render):
            result = storage.publish(self.root, self.doc)
        self.assertEqual(rename.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1, 0.2])
        self.assertEqual(len(set(observed)), 1)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(render.call_count, 1)
        self.assertTrue(storage.verify_artifacts(Path(result["output"])))
        self.assertEqual(list((self.root / "pendientes").iterdir()), [])
        self.assertTrue(storage.publish(self.root, self.doc)["reused"])

    def test_permanent_windows_error_fails_and_preserves_stage_not_database_row(self):
        failure = windows_error(5)
        with patch.object(Path, "rename", autospec=True, side_effect=failure) as rename, patch("time.sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                storage.publish(self.root, self.doc)
        self.assertIs(caught.exception, failure)
        self.assertEqual(rename.call_count, 5)
        self.assertEqual(sum(call.args[0] for call in sleep.call_args_list), 0.75)
        stages = list((self.root / "pendientes").iterdir())
        self.assertEqual(len(stages), 1)
        self.assertTrue(storage.verify_artifacts(stages[0]))
        self.assertEqual(list((self.root / "documentos").iterdir()), [])
        with closing(sqlite3.connect(self.root / "indice.sqlite")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_destination_created_during_error_is_never_overwritten(self):
        protected = []
        def competing_writer(stage, final):
            final.mkdir()
            marker = final / "otro-proceso.txt"
            marker.write_text("NO TOCAR", encoding="utf-8")
            protected.append(marker)
            raise windows_error(5)
        with patch.object(Path, "rename", autospec=True, side_effect=competing_writer) as rename, patch("time.sleep") as sleep:
            with self.assertRaises(FileExistsError):
                storage.publish(self.root, self.doc)
        self.assertEqual(rename.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(protected[0].read_text(), "NO TOCAR")
        self.assertEqual(list(protected[0].parent.iterdir()), protected)
        stages = list((self.root / "pendientes").iterdir())
        self.assertEqual(len(stages), 1)
        self.assertTrue(storage.verify_artifacts(stages[0]))

    def test_destination_created_between_attempts_blocks_second_rename(self):
        protected = []
        def blocked(stage, final):
            protected.append(final)
            raise windows_error(32)
        def competing_writer(_delay):
            protected[0].mkdir()
        with patch.object(Path, "rename", autospec=True, side_effect=blocked) as rename, patch("time.sleep", side_effect=competing_writer):
            with self.assertRaises(FileExistsError):
                storage.publish(self.root, self.doc)
        self.assertEqual(rename.call_count, 1)
        self.assertTrue(protected[0].is_dir())
        self.assertEqual(list(protected[0].iterdir()), [])

    def test_non_windows_permission_or_disk_error_not_retried(self):
        for failure in (PermissionError(errno.EACCES, "Permiso permanente"), OSError(errno.ENOSPC, "Disco lleno")):
            with self.subTest(error=type(failure).__name__):
                with patch.object(Path, "rename", autospec=True, side_effect=failure) as rename, patch("time.sleep") as sleep:
                    with self.assertRaises(OSError) as caught:
                        storage.publish(self.root, self.doc)
                self.assertIs(caught.exception, failure)
                self.assertEqual(rename.call_count, 1)
                sleep.assert_not_called()

    def test_missing_stage_is_not_retried(self):
        # Simula indisponibilidad de stage sin borrarlo: ni copia ni conversión nuevas.
        real_is_dir = Path.is_dir
        state = {"promotion_started": False}
        def failed_promotion(stage, final):
            state["promotion_started"] = True
            raise windows_error(5)
        def not_available(path):
            if state["promotion_started"] and path.parent == self.root / "pendientes":
                return False
            return real_is_dir(path)
        with (patch.object(Path, "rename", autospec=True, side_effect=failed_promotion) as rename,
              patch.object(Path, "is_dir", autospec=True, side_effect=not_available), patch("time.sleep") as sleep):
            with self.assertRaises(PermissionError):
                storage.publish(self.root, self.doc)
        self.assertEqual(rename.call_count, 1)
        sleep.assert_not_called()
