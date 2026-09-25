"""Regresiones del banco de tortura (tortura/): cada causa raíz reparada queda fijada aquí.

Los archivos patológicos se fabrican con el propio generador del banco. Las pruebas que
necesitan LibreOffice o Tesseract se omiten si la herramienta no está instalada; el
resto corre en cualquier sistema.
"""
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from unittest import TestCase, mock, skipUnless

from conversion import native, ocr, office_legacy, pipeline
from conversion.local_io import ConversionError, clean_control, decode_ooxml_escapes, md_cell, md_text
from support import workspace_temp

TORTURA = Path(__file__).resolve().parents[2] / "tortura"
_spec = importlib.util.spec_from_file_location("generador_tortura", TORTURA / "generar_tortura_multiformato.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)
HAS_SOFFICE = office_legacy.soffice_path() is not None
HAS_TESSERACT = ocr.engine() is not None


def markdown_of(result):
    return (Path(result["output"]) / "documento.md").read_text(encoding="utf-8")


class TextoSeguroTests(TestCase):
    def test_line_starts_never_create_markdown_structure(self):
        for raw, expected in (("| a | b |", "\\| a | b |"), ("# no", "\\# no"), ("> no", "\\> no"),
                              ("```", "\\```"), ("---", "\\---"), ("1. no", "1\\. no"), ("- no", "\\- no")):
            self.assertEqual(md_text(raw), expected)
        self.assertEqual(md_text("texto | normal # sin cambio"), "texto | normal # sin cambio")
        self.assertEqual(md_text("<script>x</script>"), "&lt;script>x&lt;/script>")

    def test_cells_escape_pipes_newlines_and_drop_controls(self):
        self.assertEqual(md_cell("a|b\r\nc\x07\x00d"), "a&#124;b<br>cd")
        self.assertEqual(clean_control("a\x0bb\x0cc\ud800"), "a\nb\nc�")

    def test_ooxml_escapes_decode_and_escaped_underscore_stays_literal(self):
        self.assertEqual(decode_ooxml_escapes("A_x000D_B"), "A\rB")
        self.assertEqual(decode_ooxml_escapes("_x005F_x000D_"), "_x000D_")
        self.assertEqual(decode_ooxml_escapes(42), 42)


class RechazoControladoTests(TestCase):
    """Un archivo dañado produce ConversionError con causa y nunca un paquete parcial."""

    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.base = Path(fixture.name)
        self.library = self.base / "biblioteca"

    def assert_rejected(self, source, fragment):
        with self.assertRaises(ConversionError) as caught:
            pipeline.convert_any(self.library, source)
        self.assertIn(fragment, str(caught.exception))
        documents = self.library / "documentos"
        self.assertFalse(documents.exists() and any(documents.iterdir()))

    def test_empty_truncated_and_garbage_files(self):
        xlsx = gen.excel(self.base)
        truncated = self.base / "truncado.xlsx"
        truncated.write_bytes(xlsx.read_bytes()[:2000])
        empty = self.base / "vacio.docx"
        empty.write_bytes(b"")
        garbage = self.base / "basura.pdf"
        garbage.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 4)
        self.assert_rejected(truncated, "ZIP dañado")
        self.assert_rejected(empty, "vacío")
        self.assert_rejected(garbage, "PDF dañado")

    def test_broken_xml_and_truncated_dxf_and_png(self):
        docx = gen.word(self.base)
        png = gen.imagenes(self.base)[0]
        dxf = gen.cad(self.base)
        rejected = gen.corruptos(self.base, gen.excel(self.base), docx, self.base / "x.pdf", png)
        self.assertIn("corrupto_xml_roto.docx", rejected)
        self.assert_rejected(self.base / "corrupto_xml_roto.docx", "Word (python-docx)")
        self.assert_rejected(self.base / "corrupto_dxf_truncado.dxf", "DXF dañado")
        self.assert_rejected(self.base / "corrupto_png_truncado.png", "Imagen dañada")
        self.assertTrue(dxf.is_file())

    def test_unexpected_engine_exception_becomes_explained_rejection(self):
        source = gen.excel(self.base)
        with mock.patch.object(pipeline, "excel_tres_capas", side_effect=KeyError("xl/styles.xml")):
            self.assert_rejected(source, "El motor Excel (openpyxl) no pudo leer")

    def test_missing_oda_is_controlled(self):
        fake = self.base / "plano.dwg"
        fake.write_bytes(b"AC1032" + b"\0" * 64)
        with mock.patch.object(native, "ODA_EXE", ""):
            self.assert_rejected(fake, "Falta ODA File Converter")

    def test_legacy_without_libreoffice_explains_instead_of_guessing(self):
        source = gen.excel_xlsb(self.base)
        self.assertEqual(pipeline.detect(source), "xlsb")
        with mock.patch.object(office_legacy, "soffice_path", return_value=None):
            self.assert_rejected(source, "requiere LibreOffice")


class CanalesTests(TestCase):
    """Canales que antes desaparecían en silencio."""

    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.base = Path(fixture.name)
        self.library = self.base / "biblioteca"

    def test_word_nested_tables_notes_omml_lists_and_ragged_rows(self):
        text = markdown_of(pipeline.convert_any(self.library, gen.word(self.base)))
        for expected in ("[^1]: Nota al pie con | pipe", "[^fin1]: Nota final", "[tabla anidada 1.1]",
                         "| anidada 0.0 &#124; |", "| nivel 3 | `profunda` |", "[fórmula OMML: E^(2)=(a|b)/(c)]",
                         "|  |  | fila con gridBefore |", "\\| a | b | c |", "\\# Esto no es un título"):
            self.assertIn(expected, text)
        self.assertIn("               1. Nivel 6 de lista mixta", text)
        fences = [line for line in text.splitlines() if line.startswith("````")]
        self.assertEqual(len(fences), 2)          # la valla crece más que el ``` del contenido

    def test_excel_escapes_decoded_and_controls_removed_everywhere(self):
        folder = Path(pipeline.convert_any(self.library, gen.excel(self.base))["output"])
        for name in ("documento.md", "derivados/rejilla_original.md", "derivados/celdas.csv"):
            content = (folder / name).read_text(encoding="utf-8")
            self.assertNotIn("_x000D_", content, name)
            self.assertNotRegex(content, "[\x00-\x08]", name)
        self.assertIn("Retorno<br>escapado", (folder / "documento.md").read_text(encoding="utf-8"))

    def test_powerpoint_hidden_slide_notes_and_escapes(self):
        text = markdown_of(pipeline.convert_any(self.library, gen.powerpoint(self.base)))
        self.assertIn("## Diapositiva 6 *(oculta)*", text)
        self.assertIn("## Diapositiva 5\n*(diapositiva sin contenido)*", text)
        self.assertIn("> Línea 24:", text)                   # cada línea de notas sigue citada
        self.assertIn("> \\| tabla | falsa |", text)
        self.assertNotIn("_x000D_", text)
        self.assertIn("\\| a | b |", text)

    def test_cad_nested_blocks_dimensions_layers_and_real_extents(self):
        text = markdown_of(pipeline.convert_any(self.library, gen.cad(self.base)))
        for expected in ("- perno | M12 (×12)", "- cota: 40 mm | cota", "| congeladas | CONGELADA |",
                         "Texto en capa apagada", "Rótulo en paper space"):
            self.assertIn(expected, text)
        self.assertNotIn("100000000000000000000", text)

    def test_cad_lengths_follow_bulges_closure_and_arc_wraparound(self):
        import ezdxf
        drawing = ezdxf.new()
        space = drawing.modelspace()
        space.add_lwpolyline([(0, 0, 0, 0, 1.0), (2, 0, 0, 0, 0)], format="xyseb", dxfattribs={"layer": "SEMI"})
        space.add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True, dxfattribs={"layer": "CUADRADO"})
        space.add_arc((0, 0), 1, 350, 10, dxfattribs={"layer": "ARCO"})
        space.add_polyline3d([(0, 0, 0), (0, 0, 5)], dxfattribs={"layer": "VERTICAL"})
        space.add_polyline2d([(0, 0, 0, 0, -1.0), (4, 0, 0, 0, 0)], format="xyseb", dxfattribs={"layer": "P2D"})
        source = self.base / "medidas.dxf"
        drawing.saveas(source)
        measured = native.dwg_medir(str(source), str(self.base / "cad"))["largo_capa"]
        self.assertAlmostEqual(measured["SEMI"], math.pi, places=6)
        self.assertAlmostEqual(measured["CUADRADO"], 4.0, places=6)
        self.assertAlmostEqual(measured["ARCO"], math.radians(20), places=6)
        self.assertAlmostEqual(measured["VERTICAL"], 5.0, places=6)
        self.assertAlmostEqual(measured["P2D"], 2 * math.pi, places=6)    # semicírculo de radio 2

    def test_image_without_ocr_is_published_with_explicit_pending(self):
        ocr.engine.cache_clear()
        with mock.patch.object(ocr, "tesseract_path", return_value=None):
            ocr.engine.cache_clear()
            text = markdown_of(pipeline.convert_any(self.library, gen.imagenes(self.base)[0]))
        ocr.engine.cache_clear()
        self.assertIn("![Imagen original, página 1](imagenes/pagina_001.png)", text)
        self.assertIn("Tesseract no está instalado", text)

    @skipUnless(HAS_TESSERACT, "Tesseract no instalado")
    def test_ocr_reads_images_multipage_tiff_and_scanned_pdf_page(self):
        paths = gen.imagenes(self.base)
        tiff = markdown_of(pipeline.convert_any(self.library, paths[2]))
        self.assertIn("TIFF PAGINA 1", tiff)
        self.assertIn("TIFF PAGINA 2", tiff)
        pdf = markdown_of(pipeline.convert_any(self.library, gen.pdf(self.base)))
        self.assertIn("INFORME ESCANEADO", pdf)
        self.assertIn("Texto OCR candidato", pdf)

    @skipUnless(HAS_SOFFICE, "LibreOffice no instalado")
    def test_legacy_office_is_identified_by_content_and_converted(self):
        sources = gen.legado(self.base, {"xls": gen.excel(self.base), "doc": gen.word(self.base)})
        for name, kind in (("stress_excel.xls", "xls"), ("stress_word.doc", "doc")):
            liar = self.base / (name + ".bin")            # la extensión no decide
            shutil.copy2(sources[name], liar)
            self.assertEqual(pipeline.detect(liar), kind)
        result = pipeline.convert_any(self.library, sources["stress_excel.xls"])
        document = json.loads((Path(result["output"]) / "document.json").read_text(encoding="utf-8"))
        self.assertEqual((document["input_kind"], document["source_kind"]), ("xlsx", "xls"))
        self.assertTrue(any("LibreOffice" in warning for warning in document["producer_warnings"]))
        self.assertIn("Cálculos ocultos *(oculta)*", markdown_of(result))


@skipUnless(HAS_SOFFICE and HAS_TESSERACT, "El banco completo necesita LibreOffice y Tesseract")
class BancoDeTorturaTests(TestCase):
    """Extremo a extremo: generar → convertir cada archivo aislado → validar reglas A–F y X."""

    def test_suite_has_zero_validator_errors(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        base = Path(fixture.name)
        suite, output, report = base / "suite", base / "salida", base / "validacion.json"
        for command in ([sys.executable, str(TORTURA / "generar_tortura_multiformato.py"), "--salida", str(suite)],
                        [sys.executable, str(TORTURA / "ejecutar_tortura.py"), "--suite", str(suite), "--salida", str(output)],
                        [sys.executable, str(TORTURA / "validar_salidas_md.py"), "--salida", str(output), "--json", str(report)]):
            completed = subprocess.run(command, capture_output=True, text=True, timeout=1200)
            self.assertEqual(completed.returncode, 0, completed.stdout[-3000:] + completed.stderr[-2000:])
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["errores"], 0)
