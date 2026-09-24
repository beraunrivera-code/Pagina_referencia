"""Publicar un encargo completo pese a bloqueos Windows breves, nunca pisarlo."""

import base64
import errno
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from conversion import jobs
from conversion.documents import digest
from conversion.storage import publish, verify_artifacts
from support import workspace_temp


def windows_error(code):
    error = PermissionError(errno.EACCES, "Bloqueo de publicación simulado")
    error.winerror = code
    return error


class JobPublicationTests(TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.path / "datos"
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        doc = {"schema_version": 1, "title": "Página sintética", "expected_units": [1],
               "units": [{"number": 1, "image_asset": "imagenes/p1.png", "image_sha256": digest(image),
                          "blocks": [{"id": "p1-b1", "kind": "paragraph", "text": "Texto de control."}]}]}
        package = publish(self.root, doc, {"imagenes/p1.png": image})
        self.package = Path(package["output"])

    def test_bloqueo_transitorio_publica_el_mismo_stage_completo(self):
        original_rename = Path.rename
        observed = []

        def transient(stage, target):
            self.assertFalse(target.exists(), "el destino no debe mostrar un paquete parcial")
            job, payloads = jobs.load_job(stage)
            self.assertEqual(set(payloads), {"pagina.png", "solicitud.md", "respuesta.schema.json", "job.json"})
            observed.append((stage, target, job["job_id"]))
            if len(observed) == 1:
                raise windows_error(5)
            return original_rename(stage, target)

        with patch.object(Path, "rename", autospec=True, side_effect=transient):
            result = jobs.prepare_job(self.root, self.package, 1)
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[0], observed[1], "no recrea otro encargo ni fragmenta publicación")
        self.assertEqual(jobs.load_job(Path(result["folder"]))[0]["job_id"], result["job_id"])
        self.assertEqual(result["external_calls"], 0)
        self.assertEqual(list((self.root / "pendientes").glob("encargo-*")), [])
        self.assertTrue(verify_artifacts(self.package))
        self.assertTrue(jobs.prepare_job(self.root, self.package, 1)["reused"])

    def test_comparticion_y_bloqueo_windows_admiten_reintento_acotado(self):
        original_rename = Path.rename
        codes = [32, 33]
        calls = []

        def transient(stage, target):
            calls.append(stage)
            if codes:
                raise windows_error(codes.pop(0))
            return original_rename(stage, target)

        with patch.object(Path, "rename", autospec=True, side_effect=transient):
            result = jobs.prepare_job(self.root, self.package, 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(set(calls)), 1)
        jobs.load_job(Path(result["folder"]))

    def test_permiso_persistente_no_se_disfraza_de_exito(self):
        with patch.object(Path, "rename", autospec=True, side_effect=windows_error(5)) as rename:
            with self.assertRaises(PermissionError):
                jobs.prepare_job(self.root, self.package, 1)
        self.assertEqual(rename.call_count, 5)
        self.assertEqual(list((self.root / "encargos").iterdir()), [])
        self.assertEqual(list((self.root / "pendientes").glob("encargo-*")), [])
        self.assertTrue(verify_artifacts(self.package))

    def test_otros_errores_no_se_reintentan(self):
        for error in (OSError(errno.ENOSPC, "Sin espacio"), PermissionError(errno.EACCES, "Sin permiso")):
            with self.subTest(error=error), patch.object(Path, "rename", autospec=True, side_effect=error) as rename:
                with self.assertRaises(OSError):
                    jobs.prepare_job(self.root, self.package, 1)
                self.assertEqual(rename.call_count, 1)

    def test_destino_creado_durante_el_bloqueo_se_conserva(self):
        protected = []

        def race(stage, target):
            target.mkdir()
            sentinel = target / "ajeno.txt"
            sentinel.write_text("NO REEMPLAZAR", encoding="utf-8")
            protected.append(sentinel)
            raise windows_error(5)

        with patch.object(Path, "rename", autospec=True, side_effect=race) as rename:
            with self.assertRaises(OSError):
                jobs.prepare_job(self.root, self.package, 1)
        self.assertEqual(rename.call_count, 1)
        self.assertEqual(protected[0].read_text(encoding="utf-8"), "NO REEMPLAZAR")
        self.assertEqual(set(path.name for path in protected[0].parent.iterdir()), {"ajeno.txt"})
        self.assertTrue(verify_artifacts(self.package))

    def test_limpieza_bloqueada_no_oculta_error_original_y_conserva_stage(self):
        denied = windows_error(5)
        with patch.object(Path, "rename", autospec=True, side_effect=denied), \
                patch.object(jobs.shutil, "rmtree", side_effect=OSError("Limpieza bloqueada")), \
                self.assertWarnsRegex(RuntimeWarning, "conservado para diagnóstico"):
            with self.assertRaises(PermissionError) as caught:
                jobs.prepare_job(self.root, self.package, 1)
        self.assertIs(caught.exception, denied)
        stages = list((self.root / "pendientes").glob("encargo-*"))
        self.assertEqual(len(stages), 1)
        jobs.load_job(stages[0])
        self.assertEqual(list((self.root / "encargos").iterdir()), [])
