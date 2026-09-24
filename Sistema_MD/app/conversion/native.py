"""Motores locales portados (0 tokens, 0 llamadas a IA) para Word, Excel, PowerPoint, Visio y CAD.

ORIGEN — clonado, no reinventado, el 2026-09-14 desde
``Desktop/CLAUDE CODE/HERRAMIENTAS/SISTEMA_CONVERSION/convertir.py`` (el despachador que ese mismo
día produjo 399 MD reales a 0 tokens). Se PORTÓ en vez de importarlo para que este programa no
dependa de una ruta del Escritorio que puede moverse. Función por función:

    como()               <- convertir.py::como              (la extensión miente: copia con la real)
    limpiar_temporales() <- convertir.py::limpiar_temporales
    excel_tres_capas()   <- convertir.py::excel_tres_capas  (hojas + formulas.csv + imágenes de xl/media)
    docx_texto()         <- convertir.py::docx_texto        (párrafos por estilo + tablas + imágenes)
    _texto_cad()         <- convertir.py::_texto_cad        (limpia los códigos de formato de AutoCAD)
    dwg_medir()          <- convertir.py::dwg_medir         (ODA -> DXF -> ezdxf; textos, bloques, GEOMETRÍA)
    dwg_md()             <- convertir.py::dwg_md
    visio_grafo()        <- convertir.py::visio_grafo       (el flujo sale de los <Connect> del XML)
    visio_md()           <- convertir.py::visio_md
    pptx_texto()         <- convertir.py::pptx_texto        (texto + notas; marca diapositivas visuales)

NO se portó ``render_pptx`` (exporta diapositivas a PNG por COM para que las vea un motor de visión):
esta fase no describe imágenes. Dos desviaciones respecto al original, ambas sin cambiar resultados:
(1) ``dwg_medir`` lee un ``.dxf`` directamente con ezdxf sin pasar por ODA (ODA solo hace falta para
el binario DWG); (2) los tres ``io.open(...).write(...)`` sin cerrar se envuelven en ``with`` (bajo
unittest emitían ResourceWarning y en Windows un handle abierto puede bloquear la lectura posterior).

Revisión Codex posterior [2026-09-14]: Excel ya no recorta 400 filas ni altera comillas/barras;
lectura streaming y CSV de coordenadas, rechazo explícito >1M celdas. Word conserva la secuencia
párrafo/tabla. Office usa streams en vez de copias por extensión. PPT incluye tablas y texto
de grupos. Visio no recorta textos sueltos a 40. Las limitaciones visuales y CAD siguen declaradas.
Ninguna función de este módulo llama a un modelo.
"""

from __future__ import annotations

import io
import os
from .local_io import clean_control, decode_ooxml_escapes, md_cell, md_text

from .runtime_paths import find_oda
ODA_EXE = find_oda()


def _oda_command(argumentos, exe=None):
    """Orden y entorno para ODA. En Linux sin $DISPLAY el Qt de ODA aborta con el plugin
    «xcb»: se envuelve en ``xvfb-run -a`` (pantalla virtual). Sin xvfb-run se intenta el
    plugin ``offscreen`` de Qt, que no todas las versiones de ODA incluyen."""
    exe = exe or ODA_EXE
    orden = [exe, *argumentos]
    entorno = None
    if os.name != "nt" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        import shutil
        xvfb = shutil.which("xvfb-run")
        if xvfb:
            orden = [xvfb, "-a", *orden]
        else:
            entorno = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return orden, entorno

# Medios embebidos que se conservan como imágenes del paquete (capa 3 del ADN de Excel y
# anexos de Word). Formatos vectoriales y de mapa de bits habituales en ``xl/media/``.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
                              ".emf", ".wmf", ".svg", ".webp"})

# Formatos que `pipeline.detect()` devuelve y que estos motores cubren. XLS/DOC/PPT (OLE) y XLSB
# pasan por una copia OOXML hecha con LibreOffice (``office_legacy``); las imágenes, por OCR local.
# Un OLE no identificado ("office-legacy": msg, vsd…) sigue sin ficha y no se inventa una.
IMAGE_KINDS = frozenset({"png", "jpg", "tif", "gif", "webp"})
LEGACY_KINDS = frozenset({"xls", "xlsb", "doc", "ppt"})
KINDS = frozenset({"docx", "xlsx", "pptx", "vsdx", "dwg", "dxf"}) | IMAGE_KINDS | LEGACY_KINDS
IMAGE_MAX_PIXELS = 60_000_000         # por página: una "bomba" de descompresión no llega a memoria
IMAGE_MAX_PAGES = 200


def imagen_ocr(ruta, dest):
    """Imagen (también TIFF multipágina) a paquete: cada página en PNG + texto OCR candidato.

    Devuelve (markdown, páginas, páginas_sin_texto). Una imagen dañada o truncada se rechaza
    con ConversionError: nunca se publica una página parcialmente decodificada.
    """
    from PIL import Image, ImageSequence, UnidentifiedImageError
    from . import ocr
    from .local_io import ConversionError
    os.makedirs(dest, exist_ok=True)
    try:
        with Image.open(ruta) as imagen:
            imagen.verify()                      # CRC/estructura, sin decodificar píxeles
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ConversionError(f"Imagen dañada o ilegible: {error}") from None
    md, sin_texto, paginas = [], [], 0
    motor = ocr.describe()
    try:
        with Image.open(ruta) as imagen:
            for numero, cuadro in enumerate(ImageSequence.Iterator(imagen), 1):
                if numero > IMAGE_MAX_PAGES:
                    raise ConversionError(f"Más de {IMAGE_MAX_PAGES} páginas en una imagen: requiere un alcance explícito")
                ancho, alto = cuadro.size
                if ancho * alto > IMAGE_MAX_PIXELS:
                    raise ConversionError(f"Página {numero}: {ancho}×{alto} px supera {IMAGE_MAX_PIXELS // 1_000_000} MP")
                cuadro.load()                    # aquí aflora "image file is truncated"
                rgb = cuadro.convert("RGB")      # CMYK, paleta, 16 bits, transparencia
                salida = io.BytesIO()
                rgb.save(salida, format="PNG")
                nombre = "pagina_%03d.png" % numero
                with io.open(os.path.join(dest, nombre), "wb") as archivo:
                    archivo.write(salida.getvalue())
                paginas = numero
                texto = ocr.ocr_png(salida.getvalue())
                md += ["## Página %d" % numero, "", "![Imagen original, página %d](imagenes/%s)" % (numero, nombre), ""]
                if texto:
                    md += ["*Texto OCR candidato (%s) — sin verificar contra la imagen:*" % motor, "", md_text(texto), ""]
                else:
                    sin_texto.append(numero)
                    motivo = "sin texto reconocible" if ocr.engine() else "Tesseract no está instalado"
                    md += ["[PENDIENTE: OCR de la página %d — %s; revisar la imagen.]" % (numero, motivo), ""]
    except ConversionError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ConversionError(f"Imagen dañada o truncada: {error}") from None
    return "\n".join(md), paginas, sin_texto

# ------------------------------------------------------------------ MECÁNICO (0 tokens)
_TEMPORALES = []


def como(ruta, ext_real):
    """🔴 openpyxl / python-docx / python-pptx validan por EXTENSION, no por contenido: rechazan
    un Excel llamado .pptx con "does not support .pptx file format". Y aqui los nombres estan
    cruzados de verdad (medido: PROCEDIMIENTO FLUJO LAP.pptx ES un Excel). Cuando el formato real
    no coincide con la extension, se trabaja sobre una COPIA con la extension correcta — el
    nativo no se toca jamas."""
    if ruta.lower().endswith("." + ext_real): return ruta
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="conv_")
    c = os.path.join(d, "archivo." + ext_real)
    shutil.copy2(ruta, c); _TEMPORALES.append(d)
    return c


def limpiar_temporales():
    import shutil
    for d in _TEMPORALES:
        try: shutil.rmtree(d, ignore_errors=True)
        except Exception: pass
    _TEMPORALES.clear()


def excel_tres_capas(ruta, dest, titulo=None):
    """Lectura estructurada + rejilla íntegra + fórmulas/imágenes; sin IA.

    La versión anterior juntaba todas las zonas de una hoja en una sola tabla.
    Ahora las tablas declaradas por Excel mandan; si no existen, se conservan
    regiones continuas con confianza media. La rejilla anterior no se elimina:
    queda como ``rejilla_original.md`` y ``celdas.csv``.
    """
    import csv
    import json
    import openpyxl
    import zipfile
    from contextlib import ExitStack, closing
    from openpyxl.utils.cell import range_boundaries
    from .excel_structure import build_workbook, hidden_mark
    os.makedirs(dest, exist_ok=True)
    titulo = titulo or os.path.basename(ruta)     # copia convertida: se muestra el nombre real
    raw_md = ["# %s" % titulo, ""]
    sheet_records = []
    count = 0
    visited = 0
    with ExitStack() as stack:
        # Metadatos ricos solo para ZIP acotados. El fallback streaming conserva datos y
        # declara menor confianza, en vez de arriesgar memoria sin límite.
        with zipfile.ZipFile(ruta) as archive:
            rich = (os.path.getsize(ruta) <= 30_000_000
                    and sum(item.file_size for item in archive.infolist()) <= 120_000_000)
        streams = [stack.enter_context(open(ruta, "rb")) for _ in range(2)]
        wf = stack.enter_context(closing(openpyxl.load_workbook(
            streams[0], data_only=False, read_only=not rich)))
        wv = stack.enter_context(closing(openpyxl.load_workbook(streams[1], data_only=True, read_only=True)))
        output = stack.enter_context(open(os.path.join(dest, "formulas.csv"), "w", encoding="utf-8", newline=""))
        cells = stack.enter_context(open(os.path.join(dest, "celdas.csv"), "w", encoding="utf-8", newline=""))
        formulas = csv.writer(output, delimiter=";")
        raw = csv.writer(cells, delimiter=";")
        formulas.writerow(["hoja", "celda", "formula", "valor_cacheado"])
        raw.writerow(["hoja", "celda", "tipo", "valor_original", "valor_cacheado"])
        for hf in wf.worksheets:
            hv = wv[hf.title]
            if hasattr(hf, "reset_dimensions"):
                hf.reset_dimensions()
            hv.reset_dimensions()
            raw_md.extend(["## Hoja: %s%s" % (hf.title, hidden_mark(hf.sheet_state)), ""])
            filas_hoja = {}                      # fila -> {indice_de_columna: texto}
            usadas = set()
            ancho_hoja = 0
            value_rows = iter(hv.iter_rows())
            for row_f in hf.iter_rows():
                row_v = next(value_rows, ())
                # ReadOnlyWorksheet usa EmptyCell sin atributo ``column``. La tupla
                # mantiene el orden de columnas, por lo que el índice es la fuente fiable.
                cached_by_column = {column: cell for column, cell in enumerate(row_v, 1)}
                visited += len(row_f)
                ancho_hoja = max(ancho_hoja, len(row_f))
                if visited > 1_000_000:
                    raise ValueError("Excel >1.000.000 celdas: no se publica una conversión recortada")
                celdas = {}
                # H03: en hojas grandes ``hf`` también se abre en solo lectura y los huecos
                # llegan como EmptyCell, que solo expone ``value`` y ``data_type``: no tiene
                # ``column``, ``coordinate`` ni ``row``. El índice de la tupla es la fuente
                # fiable, igual que ya se hacía para los valores en cached_by_column.
                numero_fila = None
                for indice, c in enumerate(row_f, 1):
                    if numero_fila is None:
                        numero_fila = getattr(c, "row", None)
                    cached = cached_by_column.get(indice)
                    cached_value = cached.value if cached is not None else None
                    value = c.value
                    if c.data_type != "f":
                        # openpyxl deja «_x000D_» literal; Excel lo escribe así para «\r».
                        value = decode_ooxml_escapes(value)
                    cached_value = decode_ooxml_escapes(cached_value)
                    if value is not None:
                        raw.writerow([hf.title, c.coordinate, c.data_type,
                                      clean_control(value) if isinstance(value, str) else value,
                                      clean_control(cached_value) if isinstance(cached_value, str) else cached_value])
                    if c.data_type == "f":
                        count += 1
                        formula = value if isinstance(value, str) else getattr(value, "text", str(value))
                        formulas.writerow([hf.title, c.coordinate, formula,
                                           clean_control(cached_value) if isinstance(cached_value, str) else cached_value])
                        value = cached_value if cached_value is not None else f"[SIN CACHÉ] {formula}"
                    if value is None:
                        continue
                    texto = md_cell(value)
                    if not texto.strip():
                        continue
                    celdas[indice] = texto
                    usadas.add(indice)
                if celdas and numero_fila is not None:
                    filas_hoja[numero_fila] = celdas
            tables = []
            for table in getattr(hf, "tables", {}).values():
                min_col, min_row, max_col, max_row = range_boundaries(table.ref)
                tables.append({"name": table.displayName, "ref": table.ref,
                               "min_col": min_col, "min_row": min_row,
                               "max_col": max_col, "max_row": max_row})
            merges = []
            merged_cells = getattr(hf, "merged_cells", None)
            for merged in getattr(merged_cells, "ranges", []):
                merges.append({"ref": str(merged), "min_col": merged.min_col,
                               "min_row": merged.min_row, "max_col": merged.max_col,
                               "max_row": merged.max_row})
            # Hermano de H03: una hoja sin <dimension> declarada hace que openpyxl lance
            # ValueError en solo lectura ("Worksheet is unsized"). El propio mensaje indica
            # la salida: recalcular recorriendo las filas.
            dimension = "A1:A1"
            if hasattr(hf, "calculate_dimension"):
                try:
                    dimension = hf.calculate_dimension()
                except ValueError:
                    try:
                        dimension = hf.calculate_dimension(force=True)
                    except (ValueError, TypeError):
                        dimension = "A1:A1"
            sheet_records.append({"name": hf.title, "state": hf.sheet_state,
                                  "dimension": dimension, "rows": filas_hoja,
                                  "used_columns": sorted(usadas), "tables": tables,
                                  "merges": merges, "rich_metadata": rich})
            if not filas_hoja:
                raw_md.extend(["*(hoja sin celdas con datos)*", ""])
                continue
            cols = sorted(usadas)
            raw_md.extend([
                "filas %d · columnas usadas %d de %d" % (len(filas_hoja), len(cols), ancho_hoja), "",
                "| " + " | ".join(openpyxl.utils.get_column_letter(c) for c in cols) + " |",
                "| " + " | ".join("---" for _ in cols) + " |"])
            for row in sorted(filas_hoja):
                raw_md.append("| " + " | ".join(filas_hoja[row].get(c, "") for c in cols) + " |")
            raw_md.append("")
    md, structure = build_workbook(titulo, sheet_records)
    with io.open(os.path.join(dest, "estructura.json"), "w", encoding="utf-8") as stream:
        json.dump(structure, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    with io.open(os.path.join(dest, "rejilla_original.md"), "w", encoding="utf-8") as stream:
        stream.write("\n".join(raw_md))
    # 🖼️ CANAL QUE FALTABA: un Excel puede llevar el diagrama como IMAGEN dentro de una hoja
    # (medido el 13-sep: «PROCEDIMIENTO FLUJO LAP» tiene una hoja «Flujograma» que openpyxl
    # devolvia VACIA — el flujograma era una imagen). Viven en xl/media/ dentro del zip.
    # Capa 3: cada medio se aísla; el empaquetado lo ubica en imagenes/ por su extensión.
    imgs = []
    with zipfile.ZipFile(ruta) as z:
        for n in z.namelist():
            base = os.path.basename(n)
            if n.startswith("xl/media/") and base and os.path.splitext(base)[1].lower() in IMAGE_EXTENSIONS:
                f = os.path.join(dest, base)
                if os.path.exists(f):                  # subcarpetas de media con el mismo nombre
                    f = os.path.join(dest, "media%02d_%s" % (len(imgs) + 1, base))
                with io.open(f, "wb") as salida: salida.write(z.read(n))
                imgs.append(f)
    return md, count, imgs, structure


def docx_texto(ruta, dest):
    """Párrafos, listas, tablas (también anidadas), notas, fórmulas OMML e imágenes.

    El recorrido vive en ``word_docx`` porque python-docx omite canales enteros
    (tablas dentro de celdas, notas al pie, OMML); ver ese módulo.
    """
    from .word_docx import docx_markdown
    return docx_markdown(ruta, dest)


def _texto_cad(s):
    """Limpia los códigos de formato de AutoCAD de un texto: `\\fRomanD|b1|…;\\W.8;1` -> `1`,
    `%%C` -> `Ø`, `%%D` -> `°`, `\\P` -> salto. Sin esto el MD sale lleno de ruido ilegible.
    No recorta: una nota de especificaciones larga es dato, no ruido."""
    import re as _re
    s = str(s or "")
    s = s.replace("%%C", "Ø").replace("%%c", "Ø").replace("%%D", "°").replace("%%d", "°")
    s = s.replace("%%P", "±").replace("%%p", "±").replace("%%%", "%")
    s = _re.sub(r"\\[fF][^;]*;", "", s)          # fuente
    s = _re.sub(r"\\[A-Za-z][^\;]*;", "", s)    # ancho, altura, color, seguimiento…
    s = s.replace("\\P", " ").replace("\\~", " ")
    s = _re.sub(r"[{}]", "", s)
    return _re.sub(r"\s+", " ", clean_control(s)).strip()


CAD_MAX_ENTIDADES = 500_000      # tras expandir bloques: un MINSERT/recursión no agota memoria
CAD_MAX_PROFUNDIDAD = 16


def _largo_polilinea(puntos, bulges, cerrada):
    """Longitud con arcos: un bulge b sobre una cuerda c da un arco de ángulo 4·atan|b|."""
    import math
    total = 0.0
    tramos = len(puntos) if cerrada and len(puntos) > 2 else len(puntos) - 1
    for i in range(max(0, tramos)):
        p, q = puntos[i], puntos[(i + 1) % len(puntos)]
        cuerda = math.dist(p, q)
        b = bulges[i] if bulges and i < len(bulges) else 0.0
        if b:
            theta = 4 * math.atan(abs(b))
            total += cuerda * theta / (2 * math.sin(theta / 2)) if math.sin(theta / 2) else cuerda
        else:
            total += cuerda
    return total


def _texto_cota(e):
    """Texto visible de una DIMENSION: «<>» es la medida real; vacío = solo la medida."""
    bruto = str(e.dxf.get("text", "") or "")
    if bruto.strip() == ".":                   # texto suprimido por el dibujante
        return ""
    try:
        medida = e.get_measurement()
        medida = float(medida if isinstance(medida, (int, float)) else abs(medida))
        valor = ("%.4f" % medida).rstrip("0").rstrip(".")
    except Exception:
        valor = "?"
    texto = bruto.replace("<>", valor) if "<>" in bruto else (bruto or valor)
    return _texto_cad(texto)


def _leer_cad(ruta):
    import ezdxf
    from .local_io import ConversionError
    if ruta.lower().endswith(".dxf"):
        # Desviación única respecto al original: un DXF ya es lo que ODA produce; se lee directo.
        try:
            return ezdxf.readfile(ruta)
        except ezdxf.DXFStructureError as error:
            raise ConversionError(f"DXF dañado o truncado: {error}. No se publica una medición parcial.") from None
    import subprocess, tempfile, shutil
    if not os.path.isfile(ODA_EXE):
        raise ConversionError("Falta ODA File Converter. Instálalo por su vía oficial o indica su ejecutable con SISTEMA_MD_ODA.")
    # ODA convierte por CARPETA, no por archivo
    ent = tempfile.mkdtemp(prefix="dwg_in_"); sal = tempfile.mkdtemp(prefix="dwg_out_")
    try:
        shutil.copy2(ruta, os.path.join(ent, os.path.basename(ruta)))
        orden, entorno = _oda_command([ent, sal, "ACAD2018", "DXF", "0", "1"])
        subprocess.run(orden, capture_output=True, timeout=900, env=entorno)
        dxfs = [f for f in os.listdir(sal) if f.lower().endswith(".dxf")]
        if not dxfs:
            raise ConversionError("ODA no produjo DXF (¿archivo dañado o versión no soportada?)")
        try:
            return ezdxf.readfile(os.path.join(sal, dxfs[0]))
        except ezdxf.DXFStructureError as error:
            raise ConversionError(f"ODA produjo un DXF ilegible: {error}") from None
    finally:
        # ezdxf ya cargó el DXF en memoria: los dos temporales se borran aquí, pase lo que pase
        shutil.rmtree(ent, ignore_errors=True)
        shutil.rmtree(sal, ignore_errors=True)


def dwg_medir(ruta, dest):
    """Un DWG no se convierte a prosa: se MIDE. Textos, bloques, capas y **la GEOMETRÍA** (el canal
    que un extractor de solo textos descarta: ahí vive el metrado lineal). Se leen el model space
    y los layouts: 6 planos daban CERO porque sus ~3.740 entidades vivían en el paper space.
    Los bloques se expanden (también anidados) con su transformación: el texto y la geometría
    de un INSERT dentro de otro INSERT existen en el plano aunque no estén en el model space.
    """
    import math, collections
    os.makedirs(dest, exist_ok=True)
    d = _leer_cad(ruta)
    espacios = [d.modelspace()]
    for lay in d.layouts:
        try:
            if lay.name.lower() != "model": espacios.append(lay)
        except Exception: pass
    textos, bloques, largo_capa, tipos = [], collections.Counter(), collections.Counter(), collections.Counter()
    estado = {"entidades": 0, "truncado": False, "sin_expandir": 0, "ilegibles": collections.Counter()}

    def recorrer(entidades, profundidad, origen, capa_padre):
        for e in entidades:
            if estado["entidades"] >= CAD_MAX_ENTIDADES:
                estado["truncado"] = True
                return
            estado["entidades"] += 1
            t = e.dxftype(); tipos[t] += 1
            capa = getattr(e.dxf, "layer", "") or ""
            if capa == "0" and capa_padre:          # capa 0 dentro de un bloque hereda la del INSERT
                capa = capa_padre
            try:
                if t in ("TEXT", "ATTRIB"): textos.append((capa, origen, _texto_cad(e.dxf.text)))
                elif t == "MTEXT":
                    # 🔴 `e.text` devuelve el MTEXT con los códigos de formato de AutoCAD dentro:
                    # `\fRomanD|b1|i0|c0|p2|;\W.8;1` cuando el texto real es «1». `plain_text()`
                    # los quita; si la versión de ezdxf no lo trae, se limpia a mano.
                    try: bruto = e.plain_text()
                    except Exception: bruto = str(e.text)
                    textos.append((capa, origen, _texto_cad(bruto)))
                elif t == "DIMENSION":
                    # Texto de la cota (con su medida real); su bloque gráfico no es geometría del plano.
                    texto = _texto_cota(e)
                    if texto: textos.append((capa, origen, "cota: " + texto))
                elif t == "INSERT":
                    nombre = str(e.dxf.name)
                    bloques[nombre] += 1
                    for a in getattr(e, "attribs", []):
                        textos.append((capa, origen, "%s = %s" % (a.dxf.tag, _texto_cad(a.dxf.text))))
                    if profundidad >= CAD_MAX_PROFUNDIDAD:
                        estado["sin_expandir"] += 1
                        continue
                    try:
                        virtuales = list(e.virtual_entities())
                    except Exception:
                        estado["sin_expandir"] += 1      # bloque sin definición / transformación no uniforme
                        continue
                    recorrer(virtuales, profundidad + 1, origen + " › " + nombre, capa)
                # 🔑 GEOMETRÍA: lo que el extractor viejo tiraba
                elif t == "LINE":
                    p, q = e.dxf.start, e.dxf.end
                    largo_capa[capa] += math.dist((p[0], p[1], p[2]), (q[0], q[1], q[2]))
                elif t == "LWPOLYLINE":
                    puntos = [tuple(x[:2]) for x in e.get_points("xyb")]
                    bulges = [x[2] for x in e.get_points("xyb")]
                    largo_capa[capa] += _largo_polilinea(puntos, bulges, e.closed)
                elif t == "POLYLINE":
                    tres_d = e.is_3d_polyline
                    # Vec3 acelerado de ezdxf no admite rebanadas: `location[:2]` lanzaba TypeError y
                    # el `except` de abajo dejaba toda POLYLINE en longitud 0 sin aviso.
                    puntos = [tuple(v.dxf.location)[:3 if tres_d else 2] for v in e.vertices]
                    bulges = [] if tres_d else [v.dxf.get("bulge", 0.0) for v in e.vertices]
                    largo_capa[capa] += _largo_polilinea(puntos, bulges, e.is_closed)
                elif t == "ARC":
                    barrido = (e.dxf.end_angle - e.dxf.start_angle) % 360 or 360
                    largo_capa[capa] += barrido * math.pi / 180 * e.dxf.radius
                elif t == "CIRCLE":
                    largo_capa[capa] += 2 * math.pi * e.dxf.radius
            except Exception:
                # Una entidad ilegible no detiene la medición, pero se declara: el silencio
                # escondió durante meses que toda POLYLINE medía 0.
                estado["ilegibles"][t] += 1

    for esp in espacios:
        recorrer(esp, 0, "model" if esp.name.lower() == "model" else "layout " + esp.name, "")
    capas = [c.dxf.name for c in d.layers]
    estados_capa = {"congeladas": [c.dxf.name for c in d.layers if c.is_frozen()],
                    "apagadas": [c.dxf.name for c in d.layers if c.is_off()],
                    "bloqueadas": [c.dxf.name for c in d.layers if c.is_locked()]}
    xrefs = []
    try:
        for b in d.blocks:
            if b.block.dxf.flags & 4: xrefs.append(b.name)   # 4 = bloque externo (xref)
    except Exception: pass
    ext = None
    try:
        ext = (d.header.get("$EXTMIN"), d.header.get("$EXTMAX"))
        # Sin actualizar, la cabecera trae ±1e20: no es una extensión, es "desconocida".
        if not ext[0] or not ext[1] or any(abs(v) >= 1e19 for v in (*ext[0][:2], *ext[1][:2])) \
                or ext[0][0] > ext[1][0] or ext[0][1] > ext[1][1]:
            from ezdxf import bbox
            caja = bbox.extents(d.modelspace(), fast=True)
            ext = (tuple(caja.extmin), tuple(caja.extmax)) if caja.has_data else None
    except Exception:
        ext = None
    # detalle a CSV: el dato fino se consulta, no se lee
    import csv as _csv
    with io.open(os.path.join(dest, "dwg_textos.csv"), "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f, delimiter=";"); w.writerow(["capa", "texto", "origen"])
        w.writerows((capa, texto, origen) for capa, origen, texto in textos)
    with io.open(os.path.join(dest, "dwg_geometria.csv"), "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f, delimiter=";"); w.writerow(["capa", "longitud_unidades_dibujo"])
        w.writerows([(c, round(v, 3)) for c, v in largo_capa.most_common()])
    return {"entidades": estado["entidades"], "espacios": len(espacios), "textos": textos, "bloques": bloques,
            "largo_capa": largo_capa, "tipos": tipos, "capas": capas, "estados_capa": estados_capa,
            "xrefs": xrefs, "extension": ext, "unidad_declarada": d.header.get("$INSUNITS"),
            "truncado": estado["truncado"], "sin_expandir": estado["sin_expandir"],
            "ilegibles": estado["ilegibles"]}


def dwg_md(ruta, nombre, dest):
    import re as _re
    m = dwg_medir(ruta, dest)
    celda = lambda valor: md_cell(valor)
    md = ["# %s" % md_text(os.path.splitext(nombre)[0]), "",
          "> 🖼️ **Plano CAD medido, no transcrito.** El dibujo no se convierte a texto: se cataloga "
          "lo que el archivo declara. El detalle fino vive en `dwg_textos.csv` y `dwg_geometria.csv`.", "",
          "| Dato | Valor |", "|---|---|",
          "| Entidades | %d |" % m["entidades"],     # incluye las de bloques expandidos
          "| Espacios leídos | %d (model + layouts) |" % m["espacios"],
          "| Textos, atributos y cotas | %d |" % len(m["textos"]),
          "| Bloques insertados | %d (%d nombres distintos) |" % (sum(m["bloques"].values()), len(m["bloques"])),
          "| Capas | %d |" % len(m["capas"]),
          "| Xrefs | %s |" % (celda(", ".join(m["xrefs"])) if m["xrefs"] else "ninguna"),
          "| `$INSUNITS` declarado | %s ⚠️ *una cabecera declara una intención; la unidad se deduce del dato* |" % m["unidad_declarada"]]
    if m["extension"]:
        (a, b) = m["extension"]
        md.append("| Extensión del dibujo | X %.1f a %.1f · Y %.1f a %.1f |" % (a[0], b[0], a[1], b[1]))
    else:
        md.append("| Extensión del dibujo | no declarada ni calculable |")
    md.append("")
    if m["truncado"] or m["sin_expandir"]:
        md += ["> ⚠️ **Medición incompleta:** %s%s." % (
            "se alcanzó el tope de %d entidades" % CAD_MAX_ENTIDADES if m["truncado"] else "",
            (" · %d bloques sin expandir (profundidad o definición ausente)" % m["sin_expandir"]) if m["sin_expandir"] else ""), ""]
    if m["ilegibles"]:
        md += ["> ⚠️ **Entidades no medidas por error de lectura:** %s. Su texto/longitud no está en las tablas." % (
            ", ".join("%s ×%d" % (tipo, n) for tipo, n in m["ilegibles"].most_common())), ""]
    estados = m["estados_capa"]
    if any(estados.values()):
        md += ["## Capas con estado especial", "", "| Estado | Capas |", "|---|---|"]
        md += ["| %s | %s |" % (clave, celda(", ".join(valor))) for clave, valor in estados.items() if valor]
        md += ["", "> El contenido de estas capas se cataloga igual: ocultar una capa no la borra del plano.", ""]
    if m["bloques"]:
        md += ["## Bloques por nombre (lo que un PDF no puede contener)", "", "| Bloque | Veces |", "|---|---:|"]
        md += ["| %s | %d |" % (celda(n), c) for n, c in m["bloques"].most_common(25)]
        md += [""]
    if m["largo_capa"]:
        md += ["## Longitud por capa (unidades de dibujo)", "",
               "> El canal que un extractor de solo textos descarta: aquí vive el metrado lineal.", "",
               "| Capa | Longitud |", "|---|---:|"]
        md += ["| %s | %.2f |" % (celda(c), v) for c, v in m["largo_capa"].most_common(25)]
        md += [""]
    # Textos con palabras (no cotas sueltas «1.20»), sin repetir: cada uno con sus apariciones.
    conteo = {}
    for _capa, _origen, texto in m["textos"]:
        if _re.search(r"[^\W\d_]{2,}", texto):
            conteo[texto] = conteo.get(texto, 0) + 1
    if conteo:
        md += ["## Textos del plano (%d distintos%s; todo en el CSV)" % (
            len(conteo), ", primeros 120" if len(conteo) > 120 else ""), ""]
        md += ["- %s%s" % (md_text(t.strip())[:400], " (×%d)" % n if n > 1 else "") for t, n in list(conteo.items())[:120]]
        md += [""]
    testigo = next(iter(conteo), nombre)
    md += ["## Testigo", "> «%s»" % md_text(testigo.strip()[:90])]
    return "\n".join(md), m["entidades"]


def visio_grafo(ruta, dest):
    """Reconstruye el FLUJO de un Visio desde el XML. 0 tokens y sin adivinar nada.

    🔑 En un Visio el contenido NO son las formas: son las CONEXIONES. Un flujograma leído como
    texto suelto da cajas sin orden y pierde justo lo único que interesa. El dato está
    estructurado en `visio/pages/pageN.xml` (medido el 14-sep sobre un archivo real):

        <Connect FromSheet='6' FromCell='BeginX' ToSheet='5'/>   el conector 6 SALE de la forma 5
        <Connect FromSheet='4' FromCell='EndX'   ToSheet='5'/>   el conector 4 ENTRA en la forma 5

    Así que por cada conector: su BeginX da el origen y su EndX el destino. El texto del propio
    conector es la etiqueta de la flecha («Sí», «No»), que en un diagrama de decisión ES el dato.
    """
    import zipfile, re, html
    os.makedirs(dest, exist_ok=True)
    paginas = []
    with zipfile.ZipFile(ruta) as z:
        nombres = sorted(n for n in z.namelist() if re.match(r"visio/pages/page\d+\.xml", n))
        for n in nombres:
            x = z.read(n).decode("utf-8", "replace")
            # texto de cada forma, por ID
            texto = {}
            for m in re.finditer(r'<Shape\b[^>]*\bID=["\'](\d+)["\'][^>]*>(.*?)</Shape>', x, re.S):
                t = " ".join(re.findall(r"<Text[^>]*>(.*?)</Text>", m.group(2), re.S))
                t = re.sub(r"<[^>]+>", " ", t)
                # el XML guarda «>» como &gt;: sin esto, una condición sale «Desviaciones &gt; 15 dc»
                t = html.unescape(t)
                t = re.sub(r"\s+", " ", t).strip()
                if t: texto[m.group(1)] = t
            # extremos de cada conector
            desde, hasta = {}, {}
            for c in re.finditer(r"<Connect\b[^>]*/>", x):
                s = c.group(0)
                f = re.search(r"FromSheet=['\"](\d+)['\"]", s)
                celda = re.search(r"FromCell=['\"]([^'\"]+)['\"]", s)
                t = re.search(r"ToSheet=['\"](\d+)['\"]", s)
                if not (f and celda and t): continue
                if celda.group(1).startswith("Begin"): desde[f.group(1)] = t.group(1)
                elif celda.group(1).startswith("End"): hasta[f.group(1)] = t.group(1)
            aristas = []
            for conector in sorted(set(desde) | set(hasta), key=int):
                o, d = desde.get(conector), hasta.get(conector)
                if not (o and d): continue        # conector suelto: se declara, no se inventa
                etiqueta = texto.get(conector, "")
                aristas.append((texto.get(o, "[forma %s sin texto]" % o),
                                etiqueta,
                                texto.get(d, "[forma %s sin texto]" % d)))
            sueltas = [t for i, t in texto.items() if i not in desde and i not in hasta
                       and i not in {v for v in list(desde.values()) + list(hasta.values())}]
            paginas.append({"pagina": n.split("/")[-1], "aristas": aristas,
                            "formas": len(texto), "sueltas": sueltas})
    return paginas


def visio_md(ruta, nombre, dest):
    """El MD de un Visio: el flujo como lista de pasos, no como cajas sueltas."""
    paginas = visio_grafo(ruta, dest)
    md = ["# %s" % os.path.splitext(nombre)[0], ""]
    total_ar = 0
    for p in paginas:
        if not p["aristas"] and not p["sueltas"]: continue
        md.append("## %s" % p["pagina"])
        md.append("")
        if p["aristas"]:
            md.append("> 🖼️ **Diagrama — %d formas, %d conexiones.** Flujo reconstruido desde los "
                      "conectores del archivo (no descrito por una IA):" % (p["formas"], len(p["aristas"])))
            for o, e, d in p["aristas"]:
                md.append("> - %s %s→ %s" % (o, ("—«%s»— " % e) if e else "", d))
            total_ar += len(p["aristas"])
        if p["sueltas"]:
            md.append("")
            md.append("**Textos sin conexión en esta página** (títulos, notas, leyenda):")
            for t in p["sueltas"]: md.append("- %s" % t)
        md.append("")
    if not total_ar:
        md.append("⚠️ **Sin conectores legibles**: este archivo no declara conexiones entre sus "
                  "formas, así que NO se reconstruye ningún flujo. Los textos de arriba son lo "
                  "único que hay; el diagrama sigue siendo el original.")
    primera = next((a[0] for p in paginas for a in p["aristas"]), nombre)
    md.append("## Testigo")
    md.append("> «%s»" % primera[:90])
    return "\n".join(md), total_ar


def _pptx_connector_description(shape, start, end):
    """Extremos geométricos no equivalen a una dirección de flujo.

    Solo se interpreta la decoración explícita de ambos extremos. Si falta alguna,
    su estilo podría ser heredado de layout/master/tema: se conserva la relación
    geométrica y queda pendiente. headEnd decora el inicio; tailEnd, el final.
    Flechas interpretadas no certifican etiquetas ni significado del diagrama.
    """
    from pptx.oxml.ns import qn
    line = shape._element.spPr.find(qn("a:ln"))
    decorations = []
    for tag in ("headEnd", "tailEnd"):
        element = line.find(qn("a:" + tag)) if line is not None else None
        decorations.append(element.get("type", "none") if element is not None else None)
    head, tail = decorations
    arrows = {"triangle", "arrow", "stealth"}
    known = arrows | {"none"}
    detail = "headEnd=%s; tailEnd=%s" % (head or "no resuelto", tail or "no resuelto")
    if head in known and tail in known:
        if head in arrows and tail in arrows:
            relation = "%s ↔ %s" % (start, end)
        elif head in arrows:
            relation = "%s → %s" % (end, start)
        elif tail in arrows:
            relation = "%s → %s" % (start, end)
        else:
            return "%s — %s (relación geométrica, sin flechas explícitas; %s)" % (start, end, detail)
        return "%s (flechas explícitas; %s; significado del flujo pendiente de cotejo)" % (relation, detail)
    return "%s — %s (relación geométrica; dirección no determinada; %s)" % (start, end, detail)


def pptx_texto(ruta, dest):
    from pptx import Presentation
    os.makedirs(dest, exist_ok=True)
    # Tipos que SÍ llevan contenido: imagen(13), grupo(6), gráfico(3), tabla(19), SmartArt(21),
    # objeto incrustado(7), marco multimedia(16). Una autoforma suelta (1) o una línea (5) suelen
    # ser DECORACIÓN: medido el 13-sep, el motor gastó tokens describiendo «hexágonos entrelazados
    # y un semicírculo verde oliva» de una portada. Se manda al motor si hay contenido de verdad
    # o si hay MUCHAS autoformas juntas (un flujograma dibujado a mano son cajas y flechas).
    CON_CONTENIDO = {3, 6, 7, 13, 16, 19, 21}
    with open(ruta, "rb") as stream:
        pr = Presentation(stream)
    # DrawingML usa el mismo escape «_xHHHH_» que SpreadsheetML (python-pptx escribe «\r» así).
    texto = decode_ooxml_escapes
    md = []; visual = 0; visuales = set()
    for i, s in enumerate(pr.slides, 1):
        # show="0": diapositiva oculta en la presentación; se publica, marcada como en Excel.
        oculta = " *(oculta)*" if s._element.get("show") in {"0", "false"} else ""
        md.append("## Diapositiva %d%s" % (i, oculta))
        sueltas = 0
        def walk(shapes):
            for shape in shapes:
                yield shape
                if int(shape.shape_type or 0) == 6:
                    yield from walk(shape.shapes)
        # H02: un conector guarda en el XML a qué figuras se pega (stCxn/endCxn). Antes se
        # descartaba como «forma suelta» y la relación desaparecía sin dejar aviso. Se mapea
        # cada figura con texto por su shape_id para poder nombrar los extremos.
        etiquetas = {}
        for sh in walk(s.shapes):
            if sh.has_text_frame and sh.text_frame.text.strip():
                etiquetas[sh.shape_id] = texto(sh.text_frame.text).strip().splitlines()[0][:60]
        conectores = []
        for sh in walk(s.shapes):
            if sh.has_text_frame and sh.text_frame.text.strip():
                # Texto de la forma como prosa literal: un «| a |» o «```» no crea tablas ni código.
                md.extend([md_text(texto(sh.text_frame.text).strip()), ""])
            elif sh.has_table:
                md.extend(["", "| " + " | ".join(f"Columna {n+1}" for n in range(len(sh.table.columns))) + " |",
                           "| " + " | ".join("---" for _ in sh.table.columns) + " |"])
                md.extend("| " + " | ".join(md_cell(texto(c.text)) for c in row.cells) + " |" for row in sh.table.rows)
                md.append("")                  # GFM: sin línea en blanco, el texto siguiente sería otra fila
            elif sh.shape_type is not None:
                visual += 1
                try: tipo = int(sh.shape_type)
                except (TypeError, ValueError): tipo = 0
                cnv = getattr(getattr(sh._element, "nvCxnSpPr", None), "cNvCxnSpPr", None)
                if cnv is not None:
                    # Conector: la señal dura es el nodo del XML, no el número de tipo.
                    ini = getattr(getattr(cnv, "stCxn", None), "id", None)
                    fin = getattr(getattr(cnv, "endCxn", None), "id", None)
                    if ini is not None and fin is not None:
                        conectores.append((sh, ini, fin))
                        # La relación se conserva, pero el cotejo visual y las etiquetas
                        # de flujo no quedan certificados por conocer dos IDs.
                        visuales.add(i)
                    else:
                        visuales.add(i)   # extremos sueltos: se marca pendiente, no se tira
                elif tipo in CON_CONTENIDO: visuales.add(i)   # necesita OJOS
                else: sueltas += 1
        for sh, ini, fin in conectores:
            origen = etiquetas.get(ini, "figura %s" % ini)
            destino = etiquetas.get(fin, "figura %s" % fin)
            md.append("> **Conector:** %s [figuras %s, %s; conector %s]" % (
                _pptx_connector_description(sh, origen, destino), ini, fin, sh.shape_id))
        if sueltas >= 4: visuales.add(i)         # muchas formas sueltas = diagrama dibujado a mano
        if md[-1].startswith("## Diapositiva "):
            md.append("*(diapositiva sin contenido)*")
        if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
            # Cada línea dentro de la cita: antes solo la primera quedaba citada.
            notas = md_text(texto(s.notes_slide.notes_text_frame.text).strip()).split("\n")
            md.append("> **Notas del orador:** " + notas[0])
            md.extend((">" + (" " + linea if linea.strip() else "")) for linea in notas[1:])
        md.append("")
    return "\n".join(md), visual, visuales
