"""Eliminación coherente de resultados: índice + carpeta, con marcha atrás.

La Papelera real se prueba aparte (es de Windows y deja rastro en la del usuario); aquí se
inyecta el descarte para comprobar el CONTRATO: si el descarte falla, nada cambia.
"""

import shutil
import sqlite3
from pathlib import Path
from unittest import TestCase

from conversion.diagnostics import repair_safe
from conversion.pipeline import convert_text
from conversion.storage import delete_documents, pending_batches, prune_missing, publish, report
from support import workspace_temp


def _descartar(folder: Path) -> None:
    shutil.rmtree(folder)


def _papelera_rota(folder: Path) -> None:
    raise OSError("La Papelera rechazó la carpeta")


class EliminarResultados(TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.path / "out"

    def _publicar(self, nombre: str, texto: str) -> dict:
        fuente = self.temp.path / nombre
        fuente.write_text(texto, encoding="utf-8")
        return convert_text(self.root, fuente)

    def _filas(self, tabla: str) -> int:
        # ``with sqlite3.connect()`` solo confirma la transacción, NO cierra: en Windows el
        # archivo queda tomado y la limpieza de la carpeta de prueba falla (WinError 32).
        db = sqlite3.connect(self.root / "indice.sqlite")
        try:
            return db.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        finally:
            db.close()

    def test_elimina_uno_y_deja_el_resto(self):
        a = self._publicar("a.txt", "Documento A.\n")
        b = self._publicar("b.txt", "Documento B.\n")
        resultado = delete_documents(self.root, [a["id"]], discard=_descartar)
        self.assertEqual(resultado["deleted"], [a["id"]])
        self.assertEqual(resultado["failed"], [])
        self.assertFalse(Path(a["output"]).exists())
        self.assertTrue(Path(b["output"]).exists())
        self.assertEqual([r["id"] for r in report(self.root)], [b["id"]])
        self.assertEqual(self._filas("origins"), 1)

    def test_elimina_varios(self):
        ids = [self._publicar(f"d{n}.txt", f"Documento {n}.\n")["id"] for n in range(4)]
        resultado = delete_documents(self.root, ids[:3], discard=_descartar)
        self.assertEqual(sorted(resultado["deleted"]), sorted(ids[:3]))
        self.assertEqual([r["id"] for r in report(self.root)], [ids[3]])

    def test_si_la_papelera_falla_no_cambia_nada(self):
        a = self._publicar("a.txt", "Documento A.\n")
        resultado = delete_documents(self.root, [a["id"]], discard=_papelera_rota)
        self.assertEqual(resultado["deleted"], [])
        self.assertEqual(len(resultado["failed"]), 1)
        self.assertTrue(Path(a["output"]).exists(), "la carpeta debe seguir en disco")
        filas = report(self.root)
        self.assertEqual([r["id"] for r in filas], [a["id"]], "la fila debe seguir en el índice")
        self.assertTrue(filas[0]["integrity_ok"])

    def test_fila_sin_carpeta_se_limpia_sin_tocar_disco(self):
        """El caso «DAÑADO»: alguien movió la carpeta a mano."""
        a = self._publicar("a.txt", "Documento A.\n")
        shutil.rmtree(a["output"])
        self.assertFalse(report(self.root)[0]["integrity_ok"])
        resultado = delete_documents(self.root, [a["id"]], discard=_papelera_rota)
        self.assertEqual(resultado["deleted"], [a["id"]])
        self.assertEqual(report(self.root), [])

    def test_identificador_invalido_no_borra_nada(self):
        a = self._publicar("a.txt", "Documento A.\n")
        resultado = delete_documents(self.root, ["..\\..\\Windows"], discard=_descartar)
        self.assertEqual(resultado["deleted"], [])
        self.assertEqual(len(resultado["failed"]), 1)
        self.assertTrue(Path(a["output"]).exists())

    def test_reparacion_da_de_baja_y_vuelve_a_registrar(self):
        a = self._publicar("a.txt", "Documento A.\n")
        b = self._publicar("b.txt", "Documento B.\n")
        guardado = self.temp.path / "papelera_simulada"
        shutil.move(a["output"], guardado)                  # «eliminado» por fuera
        self.assertEqual(prune_missing(self.root), [a["id"]])
        self.assertEqual([r["id"] for r in report(self.root)], [b["id"]])
        shutil.move(str(guardado), a["output"])             # restaurado desde la Papelera
        reparado = repair_safe(self.root)
        self.assertEqual(reparado["folders_adopted"], 1)
        self.assertEqual(sorted(r["id"] for r in report(self.root)), sorted([a["id"], b["id"]]))
        self.assertTrue(all(r["integrity_ok"] for r in report(self.root)))

    def test_sin_lotes_de_ia_no_hay_dependencias(self):
        a = self._publicar("a.txt", "Documento A.\n")
        self.assertEqual(pending_batches(self.root, [a["id"]]), {})

    def test_report_informa_el_tipo_de_paquete(self):
        self._publicar("a.txt", "Documento A.\n")
        self.assertEqual(report(self.root)[0]["kind"], "text")

    def test_eliminar_usa_la_carpeta_recuperada_del_indice(self):
        original = self._publicar("a.txt", "Documento A.\n")
        (Path(original["output"]) / "documento.md").write_text("PAQUETE DAÑADO", encoding="utf-8")
        recovered = self._publicar("a.txt", "Documento A.\n")
        self.assertIn("-recuperado-", recovered["output"])
        discarded = []

        def discard(folder):
            discarded.append(folder)
            _descartar(folder)

        result = delete_documents(self.root, [recovered["id"]], discard=discard)
        self.assertEqual(result["deleted"], [recovered["id"]])
        self.assertEqual(discarded, [Path(recovered["output"])])
        self.assertTrue(Path(original["output"]).exists(), "la copia dañada no seleccionada se conserva")
        self.assertFalse(Path(recovered["output"]).exists())
        self.assertEqual(report(self.root), [])

    def test_descarte_que_no_retira_carpeta_no_borra_indice(self):
        package = self._publicar("a.txt", "Documento A.\n")
        result = delete_documents(self.root, [package["id"]], discard=lambda folder: None)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual([r["id"] for r in report(self.root)], [package["id"]])

    def test_no_elimina_homonimos_ni_paginas_no_seleccionadas(self):
        one = self._publicar("informe.txt", "Contrato de la obra A.\n")
        other_dir = self.temp.path / "otra-obra"
        other_dir.mkdir()
        other_source = other_dir / "informe.txt"
        other_source.write_text("Contrato de la obra B.\n", encoding="utf-8")
        two = convert_text(self.root, other_source)
        page = publish(self.root, {"schema_version": 1, "title": "informe",
                       "input_kind": "structured_response", "expected_units": [1],
                       "units": [{"number": 1, "blocks": [{"id": "p1-b1",
                                  "kind": "paragraph", "text": "Página interna no seleccionada."}]}]})
        self.assertEqual({row["title"] for row in report(self.root)}, {"informe"})
        result = delete_documents(self.root, [one["id"]], discard=_descartar)
        self.assertEqual(result["deleted"], [one["id"]])
        remaining = report(self.root)
        self.assertEqual({row["id"] for row in remaining}, {two["id"], page["id"]})
        self.assertTrue(all(row["integrity_ok"] for row in remaining))

    def test_id_no_registrado_no_elimina_carpeta_huerfana(self):
        package = self._publicar("a.txt", "Documento A.\n")
        db = sqlite3.connect(self.root / "indice.sqlite")
        try:
            db.execute("DELETE FROM documents WHERE id=?", (package["id"],))
            db.commit()
        finally:
            db.close()
        result = delete_documents(self.root, [package["id"]], discard=_descartar)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertTrue(Path(package["output"]).exists())
