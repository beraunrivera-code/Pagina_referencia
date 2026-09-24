"""Exportación del Markdown con su nombre real a una carpeta legible."""

from pathlib import Path
from unittest import TestCase

from conversion.pipeline import convert_text
from conversion.storage import export_markdown, report, safe_filename
from support import workspace_temp


class ExportarMarkdown(TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.path / "out"
        self.destino = self.temp.path / "MD legible"

    def _publicar(self, nombre: str, texto: str) -> dict:
        fuente = self.temp.path / nombre
        fuente.write_text(texto, encoding="utf-8")
        return convert_text(self.root, fuente)

    def test_usa_el_titulo_como_nombre(self):
        a = self._publicar("Sesion IV Tacticas.txt", "# Tácticas\n\nContenido.\n")
        salida = export_markdown(self.root, a["id"], self.destino)
        self.assertTrue(salida["created"])
        self.assertEqual(Path(salida["path"]).name, "Sesion IV Tacticas.md")
        self.assertIn("Contenido.", Path(salida["path"]).read_text(encoding="utf-8"))

    def test_exportar_dos_veces_no_duplica(self):
        a = self._publicar("doc.txt", "Contenido estable.\n")
        primero = export_markdown(self.root, a["id"], self.destino)
        segundo = export_markdown(self.root, a["id"], self.destino)
        self.assertEqual(primero["path"], segundo["path"])
        self.assertFalse(segundo["created"])
        self.assertEqual(len(list(self.destino.glob("*.md"))), 1)

    def test_reprocesar_sobrescribe_con_la_ultima_version(self):
        """Procesar dos veces el mismo documento deja UN solo .md, con lo último."""
        fuente = self.temp.path / "informe.txt"
        fuente.write_text("Versión uno.\n", encoding="utf-8")
        primero = export_markdown(self.root, convert_text(self.root, fuente)["id"], self.destino)
        fuente.write_text("Versión dos, corregida.\n", encoding="utf-8")
        segundo = export_markdown(self.root, convert_text(self.root, fuente)["id"], self.destino)
        self.assertEqual(primero["path"], segundo["path"], "debe ser el MISMO archivo")
        self.assertTrue(segundo.get("overwritten"))
        self.assertEqual(len(list(self.destino.glob("*.md"))), 1, "no debe aparecer « (2)»")
        self.assertIn("Versión dos, corregida.", Path(segundo["path"]).read_text(encoding="utf-8"))

    def test_nunca_pisa_un_archivo_distinto(self):
        a = self._publicar("doc.txt", "Contenido nuevo.\n")
        self.destino.mkdir(parents=True)
        ajeno = self.destino / "doc.md"
        ajeno.write_text("ARCHIVO AJENO QUE YA ESTABA\n", encoding="utf-8")
        salida = export_markdown(self.root, a["id"], self.destino)
        self.assertEqual(Path(salida["path"]).name, "doc (2).md")
        self.assertEqual(ajeno.read_text(encoding="utf-8"), "ARCHIVO AJENO QUE YA ESTABA\n")

    def test_enlaces_del_paquete_quedan_relativos_al_destino(self):
        a = self._publicar("doc.txt", "Texto.\n")
        salida = export_markdown(self.root, a["id"], self.destino)
        texto = Path(salida["path"]).read_text(encoding="utf-8")
        self.assertNotIn("](document.json)", texto)
        self.assertNotIn("](verificacion.json)", texto)
        self.assertNotIn("file:///", texto)
        self.assertIn("](<../out/documentos/", texto)
        self.assertTrue(salida["portable_links"])

    def test_el_paquete_original_no_cambia(self):
        a = self._publicar("doc.txt", "Texto.\n")
        export_markdown(self.root, a["id"], self.destino)
        self.assertTrue(report(self.root)[0]["integrity_ok"])

    def test_importado_desde_el_destino_no_se_duplica(self):
        """El flujo de la otra PC: el motor deja el .md en la carpeta y el programa lo importa."""
        self.destino.mkdir(parents=True)
        original = self.destino / "Sesion IV__7a4cc80e.md"
        original.write_text("# Del motor\n\nContenido.\n", encoding="utf-8")
        importado = convert_text(self.root, original)
        salida = export_markdown(self.root, importado["id"], self.destino)
        self.assertEqual(Path(salida["path"]), original.resolve())
        self.assertFalse(salida["created"])
        self.assertEqual(len(list(self.destino.glob("*.md"))), 1, "no debe aparecer un « (2)»")

    def test_nombres_imposibles_en_windows(self):
        self.assertEqual(safe_filename('Plan: fase 1/2 <borrador> "v2"?'), "Plan fase 1 2 borrador v2")
        self.assertEqual(safe_filename("CON"), "documento")
        self.assertEqual(safe_filename("CON.txt"), "documento")
        self.assertEqual(safe_filename("LPT1.informe"), "documento")
        self.assertEqual(safe_filename("   ...   "), "documento")
        self.assertLessEqual(len(safe_filename("x" * 400)), 150)
