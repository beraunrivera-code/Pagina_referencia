"""No perder homónimos, ediciones manuales ni propiedad en exportaciones paralelas."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from conversion.pipeline import convert_text, import_pages
from conversion import storage
from conversion.storage import export_markdown
from support import workspace_temp


class ExportacionSegura(TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.path / "datos"
        self.destination = self.temp.path / "MD"

    def _source(self, directory, text):
        source = self.temp.path / directory / "informe.txt"
        source.parent.mkdir(exist_ok=True)
        source.write_text(text, encoding="utf-8")
        return source

    def _export(self, source):
        package = convert_text(self.root, source)
        return export_markdown(self.root, package["id"], self.destination)

    def test_homonimos_de_fuentes_distintas_no_se_sobrescriben(self):
        first = self._export(self._source("obra-a", "Contrato A, firmado.\n"))
        previous = Path(first["path"]).read_bytes()
        second = self._export(self._source("obra-b", "Contrato B, diferente.\n"))
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).read_bytes(), previous)
        self.assertIn("Contrato B", Path(second["path"]).read_text(encoding="utf-8"))

    def test_edicion_manual_de_exportado_no_se_pierde_al_actualizar(self):
        source = self._source("obra", "Versión inicial.\n")
        first = self._export(source)
        Path(first["path"]).write_text("NOTA MANUAL A CONSERVAR", encoding="utf-8")
        source.write_text("Versión corregida.\n", encoding="utf-8")
        second = self._export(source)
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).read_text(encoding="utf-8"), "NOTA MANUAL A CONSERVAR")

    def test_registro_v1_no_acredita_propiedad_por_titulo(self):
        source = self._source("obra", "Contenido nuevo.\n")
        package = convert_text(self.root, source)
        self.destination.mkdir()
        previous = self.destination / "informe.md"
        previous.write_text("EXPORTACIÓN ANTIGUA DE OTRA FUENTE", encoding="utf-8")
        (self.root / "exportados.json").write_text(
            json.dumps({str(previous).casefold(): "informe"}), encoding="utf-8")
        result = export_markdown(self.root, package["id"], self.destination)
        self.assertNotEqual(Path(result["path"]), previous)
        self.assertEqual(previous.read_text(encoding="utf-8"), "EXPORTACIÓN ANTIGUA DE OTRA FUENTE")

    def test_exportaciones_paralelas_conservan_todos_los_registros(self):
        packages = [convert_text(self.root, self._source(f"obra-{n}", f"Contrato número {n}.\n"))
                    for n in range(8)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(lambda p: export_markdown(self.root, p["id"], self.destination), packages))
        self.assertEqual(len({row["path"] for row in outputs}), 8)
        for package, first in zip(packages, outputs):
            repeated = export_markdown(self.root, package["id"], self.destination)
            self.assertEqual(repeated["path"], first["path"])
            self.assertFalse(repeated["created"])

    def test_registro_danado_no_se_sustituye_por_uno_vacio(self):
        source = self._source("obra", "Contenido.\n")
        package = convert_text(self.root, source)
        registry = self.root / "exportados.json"
        registry.write_text('{"registro": truncado', encoding="utf-8")
        before = registry.read_bytes()
        with self.assertRaises(ValueError):
            export_markdown(self.root, package["id"], self.destination)
        self.assertEqual(registry.read_bytes(), before)
        self.assertEqual(list(self.destination.glob("*.md")), [])

    def test_paginas_de_documentos_distintos_en_la_misma_carpeta(self):
        pages = self.temp.path / "paginas"
        pages.mkdir()
        (pages / "a_p01.md").write_text("Documento A.\n", encoding="utf-8")
        (pages / "b_p01.md").write_text("Documento B.\n", encoding="utf-8")
        first = import_pages(self.root, pages, "a", [1], "local", "Informe")
        second = import_pages(self.root, pages, "b", [1], "local", "Informe")
        one = export_markdown(self.root, first["id"], self.destination)
        before = Path(one["path"]).read_bytes()
        two = export_markdown(self.root, second["id"], self.destination)
        self.assertNotEqual(one["path"], two["path"])
        self.assertEqual(Path(one["path"]).read_bytes(), before)

    def test_creacion_externa_entre_comprobacion_y_publicacion_no_se_pisa(self):
        source = self._source("obra", "Contenido del programa.\n")
        package = convert_text(self.root, source)
        publish_new = storage._publish_new_export

        def race(temporary, target):
            if target.name == "informe.md":
                target.write_text("ARCHIVO CREADO POR OTRO PROCESO", encoding="utf-8")
            publish_new(temporary, target)

        with patch.object(storage, "_publish_new_export", side_effect=race):
            result = export_markdown(self.root, package["id"], self.destination)
        self.assertEqual(Path(result["path"]).name, "informe (2).md")
        self.assertEqual((self.destination / "informe.md").read_text(encoding="utf-8"),
                         "ARCHIVO CREADO POR OTRO PROCESO")
        self.assertEqual(list(self.destination.glob(".exportando-*")), [])

    def test_error_al_guardar_registro_se_informa_y_conserva_registro_anterior(self):
        source = self._source("obra", "Primera versión.\n")
        first = self._export(source)
        registry = self.root / "exportados.json"
        before = registry.read_bytes()
        source.write_text("Segunda versión.\n", encoding="utf-8")
        package = convert_text(self.root, source)
        replace = storage.os.replace

        def fail_registry(src, dest):
            if Path(dest) == registry:
                raise OSError("Disco sin espacio para el registro")
            return replace(src, dest)

        with patch.object(storage.os, "replace", side_effect=fail_registry):
            with self.assertRaisesRegex(OSError, "Disco sin espacio"):
                export_markdown(self.root, package["id"], self.destination)
        self.assertEqual(registry.read_bytes(), before)
        self.assertIn("Segunda versión", Path(first["path"]).read_text(encoding="utf-8"))
        self.assertEqual(list(self.root.glob(".exportados-*")), [])
        # Registro desfasado tras el corte: otra actualización preserva el archivo
        # no reconocido y usa una copia nueva, en vez de asumir propiedad.
        source.write_text("Tercera versión.\n", encoding="utf-8")
        third = self._export(source)
        self.assertNotEqual(first["path"], third["path"])

    def test_no_exporta_dentro_de_un_paquete_inmutable(self):
        source = self._source("obra", "Contenido.\n")
        package = convert_text(self.root, source)
        with self.assertRaisesRegex(ValueError, "paquetes inmutables"):
            export_markdown(self.root, package["id"], Path(package["output"]))
        self.assertTrue(storage.verify_artifacts(Path(package["output"])))

    def test_exporta_por_id_estable_un_paquete_recuperado(self):
        source = self._source("obra", "Contenido recuperado.\n")
        original = convert_text(self.root, source)
        (Path(original["output"]) / "documento.md").write_text("DAÑADO", encoding="utf-8")
        recovered = convert_text(self.root, source)
        self.assertIn("-recuperado-", recovered["output"])
        result = export_markdown(self.root, recovered["id"], self.destination)
        self.assertIn("Contenido recuperado", Path(result["path"]).read_text(encoding="utf-8"))
