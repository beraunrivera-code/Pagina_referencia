# -*- coding: utf-8 -*-
"""Tests del visor: documento.md -> vista.html.

Lo que vigilan: que no se cuele HTML del documento, que una tabla sin separador y con filas
desiguales salga cuadrada, que las hojas ocultas vayan plegadas, que ninguna imagen apunte
fuera del paquete, y que vista.html no rompa la integridad del paquete.
"""
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import workspace_temp                                              # noqa: E402
from conversion.visor import (render_markdown, write_view, build_view,
                              open_in_browser, VIEW_NAME)                       # noqa: E402
from conversion.pipeline import convert_text                                     # noqa: E402
from conversion.storage import verify_artifacts, report                          # noqa: E402


class RenderTests(unittest.TestCase):

    def test_escapa_html_pero_deja_br_y_u(self):
        r = render_markdown("<script>alert(1)</script> y <nombre> con x<br>y y <u>sub</u>")
        self.assertNotIn("<script", r.html)
        self.assertIn("&lt;script", r.html)
        self.assertIn("&lt;nombre&gt;", r.html)      # pseudo-etiqueta: TEXTO, no etiqueta
        self.assertIn("<br>", r.html)
        self.assertIn("<u>sub</u>", r.html)

    def test_titulos_ids_unicos_y_toc(self):
        r = render_markdown("# Uno\n\n## Dos\n\n## Dos\n\n#### Cuatro\n")
        self.assertEqual([t["id"] for t in r.toc], ["uno", "dos", "dos-2", "cuatro"])
        self.assertEqual([t["nivel"] for t in r.toc], [1, 2, 2, 4])

    def test_tabla_sin_separador_y_filas_desiguales_sale_cuadrada(self):
        # el 99,7 % de las tablas del corpus vienen asi: sin |---| y con filas desiguales
        r = render_markdown("| a | b | c |\n| x |\n| 1 | 2 | 3 |\n")
        self.assertEqual(r.html.count("<tr>"), 3)
        for fila in r.html.split("<tr>")[1:]:
            self.assertEqual(fila.count("<td"), 3)   # rellenada al ancho maximo
        self.assertEqual(r.tablas, 1)

    def test_tabla_ancha_lleva_letras_de_excel(self):
        r = render_markdown("| " + " | ".join(str(i) for i in range(1, 16)) + " |\n")
        self.assertIn("tabla ancha", r.html)
        self.assertIn(">A</th>", r.html)
        self.assertIn(">O</th>", r.html)             # la columna 15
        self.assertEqual(r.tablas_anchas, 1)

    def test_columna_vacia_se_marca_para_poder_ocultarla(self):
        r = render_markdown("| a |  | c |\n| d |  | f |\n")
        self.assertIn('class="v cv"', r.html)

    def test_hojas_ocultas_plegadas_y_visibles_abiertas(self):
        r = render_markdown(
            "## Hoja: AUX\nfilas 2 · columnas usadas 3 de 45\n\n| a | b | c |\n\n"
            "## Hoja: Oculta1  *(oculta)*\n\n| x | y |\n")
        self.assertEqual(r.hojas, 2)
        self.assertEqual(r.hojas_ocultas, 1)
        self.assertIn('<details class="hoja" open>', r.html)
        self.assertIn('<details class="hoja oculta">', r.html)   # sin open = plegada
        self.assertTrue(any(t["oculta"] for t in r.toc))

    def test_marcas_resaltadas_contadas_y_no_dentro_de_codigo(self):
        r = render_markdown("Dato [verificar] y celda [ilegible] pero `[verificar]` no.")
        self.assertEqual(sum(r.marks.values()), 2)
        self.assertIn('<mark class="verificar" id="m1">', r.html)
        self.assertIn('<mark class="ilegible" id="m2">', r.html)
        self.assertEqual(len(r.mark_items), 2)

    def test_cita_con_tabla_dentro_y_figura(self):
        r = render_markdown("> 🖼️ **Imagen 1 — Diagrama**\n> | a | b |\n")
        self.assertIn('<blockquote class="figura">', r.html)
        self.assertIn("<table>", r.html)

    def test_preambulo_no_se_pinta_pero_el_ancla_queda(self):
        r = render_markdown(
            "# T\n\nEstado: **revisar**\n\n## Índice\n\n- [P1](#unidad-1)\n\n---\n\n"
            '<a id="unidad-1"></a>\n\n## Página/unidad 1\n\nTexto.\n')
        self.assertNotIn("Índice", r.html)           # la ficha lo muestra desde el JSON
        self.assertIn('data-unidad="unidad-1"', r.html)

    def test_titulo_sin_linea_vacia_no_es_absorbido_por_lista(self):
        r = render_markdown("- uno\n## Dos\ntexto\n")
        self.assertIn("<h2", r.html)
        self.assertEqual([item["texto"] for item in r.toc], ["Dos"])


class VistaTests(unittest.TestCase):

    def setUp(self):
        self.temp = workspace_temp()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _paquete(self, texto="# T\n\n| a | b |\n| c | d |\n"):
        fuente = self.base / "f.md"
        fuente.write_text(texto, encoding="utf-8")
        return Path(convert_text(self.base / "out", fuente)["output"])

    def test_imagenes_ausente_fuera_y_remota_no_generan_img(self):
        carpeta = self.base / "paq"
        (carpeta / "imagenes").mkdir(parents=True)
        (carpeta / "imagenes" / "p1.png").write_bytes(b"x")
        r = render_markdown(
            "![ok](imagenes/p1.png)\n\n![no](falta.png)\n\n"
            "![fuera](../../x.png)\n\n![red](https://e.com/a.png)\n", folder=carpeta)
        self.assertIn('<img src="imagenes/p1.png"', r.html)
        self.assertNotIn('src="../', r.html)         # jamas una ruta fuera del paquete
        self.assertNotIn('src="https', r.html)
        self.assertEqual(len(r.imagenes_faltan), 3)

    def test_carpeta_vecina_con_mismo_prefijo_sigue_fuera_del_paquete(self):
        carpeta = self.base / "paq"
        vecina = self.base / "paq-secreta"
        carpeta.mkdir(); vecina.mkdir()
        (vecina / "dato.png").write_bytes(b"privado")
        r = render_markdown("![no](../paq-secreta/dato.png)\n", folder=carpeta)
        self.assertNotIn("<img", r.html)
        self.assertIn("imagen fuera del paquete", r.html)

    def test_vista_dentro_del_paquete_y_no_rompe_integridad(self):
        carpeta = self._paquete()
        vista = write_view(carpeta)
        self.assertEqual(vista.parent, carpeta)
        self.assertEqual(vista.name, VIEW_NAME)
        self.assertTrue(verify_artifacts(carpeta))          # el manifiesto no cambia
        self.assertTrue(report(self.base / "out")[0]["integrity_ok"])

    def test_no_reescribe_si_el_sello_coincide(self):
        carpeta = self._paquete()
        vista = write_view(carpeta)
        antes = vista.stat().st_mtime_ns
        write_view(carpeta)
        self.assertEqual(vista.stat().st_mtime_ns, antes)

    def test_vista_autocontenida_y_oscura(self):
        carpeta = self._paquete()
        h = build_view(carpeta)
        self.assertIn("--bg:#151a23", h)
        self.assertNotIn("<link", h)
        self.assertNotIn('src="http', h)
        self.assertIn("<title>", h)
        self.assertIn("Content-Security-Policy", h)

    def test_dos_escrituras_simultaneas_no_comparten_temporal(self):
        carpeta = self._paquete()
        barrera = Barrier(2)
        original = build_view

        def construir(folder):
            result = original(folder)
            barrera.wait(timeout=5)
            return result

        with patch("conversion.visor.build_view", side_effect=construir):
            with ThreadPoolExecutor(max_workers=2) as pool:
                vistas = list(pool.map(lambda _: write_view(carpeta), range(2)))
        self.assertEqual(vistas[0], vistas[1])
        self.assertTrue(vistas[0].is_file())

    def test_apertura_con_ancla_conserva_el_salto_en_fallback(self):
        carpeta = self._paquete()
        vista = write_view(carpeta)
        with patch("conversion.visor.winreg", None), patch("conversion.visor.os.startfile") as abrir:
            via = open_in_browser(vista, "seccion dos")
        self.assertEqual(via, "asociacion")
        self.assertEqual(abrir.call_args.args[0], vista.as_uri() + "#seccion%20dos")

    def test_rechaza_si_el_manifiesto_ya_declara_la_vista(self):
        carpeta = self._paquete()
        man = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
        man.setdefault("files", {})[VIEW_NAME] = "x"
        (carpeta / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        with self.assertRaises(ValueError):
            write_view(carpeta)


if __name__ == "__main__":
    unittest.main()
