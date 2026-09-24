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
from .local_io import md_cell

from .runtime_paths import find_oda
ODA_EXE = find_oda()

# Formatos que `pipeline.detect()` devuelve y que estos motores cubren. "office-legacy" (xls/doc/ppt
# en contenedor OLE) NO está: el origen tampoco tenía ficha para ellos y no se inventa una.
KINDS = frozenset({"docx", "xlsx", "pptx", "vsdx", "dwg", "dxf"})

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


def excel_tres_capas(ruta, dest):
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
    from .excel_structure import build_workbook
    os.makedirs(dest, exist_ok=True)
    raw_md = ["# %s" % os.path.basename(ruta), ""]
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
            raw_md.extend(["## Hoja: %s%s" % (hf.title, "  *(oculta)*" if hf.sheet_state != "visible" else ""), ""])
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
                    if value is not None:
                        raw.writerow([hf.title, c.coordinate, c.data_type, value, cached_value])
                    if c.data_type == "f":
                        count += 1
                        formula = value if isinstance(value, str) else getattr(value, "text", str(value))
                        formulas.writerow([hf.title, c.coordinate, formula, cached_value])
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
                raw_md.append("")
                continue
            cols = sorted(usadas)
            raw_md.extend([
                "filas %d · columnas usadas %d de %d" % (len(filas_hoja), len(cols), ancho_hoja), "",
                "| " + " | ".join(openpyxl.utils.get_column_letter(c) for c in cols) + " |",
                "| " + " | ".join("---" for _ in cols) + " |"])
            for row in sorted(filas_hoja):
                raw_md.append("| " + " | ".join(filas_hoja[row].get(c, "") for c in cols) + " |")
            raw_md.append("")
    title = os.path.basename(ruta)
    md, structure = build_workbook(title, sheet_records)
    with io.open(os.path.join(dest, "estructura.json"), "w", encoding="utf-8") as stream:
        json.dump(structure, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    with io.open(os.path.join(dest, "rejilla_original.md"), "w", encoding="utf-8") as stream:
        stream.write("\n".join(raw_md))
    # 🖼️ CANAL QUE FALTABA: un Excel puede llevar el diagrama como IMAGEN dentro de una hoja
    # (medido el 13-sep: «PROCEDIMIENTO FLUJO LAP» tiene una hoja «Flujograma» que openpyxl
    # devolvia VACIA — el flujograma era una imagen). Viven en xl/media/ dentro del zip.
    imgs = []
    with zipfile.ZipFile(ruta) as z:
        for n in z.namelist():
            if n.startswith("xl/media/") and n.split(".")[-1].lower() in ("png", "jpg", "jpeg", "gif", "emf"):
                f = os.path.join(dest, os.path.basename(n))
                with io.open(f, "wb") as salida: salida.write(z.read(n))
                imgs.append(f)
    return md, count, imgs, structure


def docx_texto(ruta, dest):
    import docx
    os.makedirs(dest, exist_ok=True)
    from docx.text.paragraph import Paragraph
    with open(ruta, "rb") as stream:
        d = docx.Document(stream)
    md = []
    tables = 0
    for element in d.iter_inner_content():
        if isinstance(element, Paragraph):
            t = element.text.strip()
            if not t: continue
            est = (element.style.name or "").lower()
            if "heading 1" in est or "título 1" in est: md.append("## " + t)
            elif "heading 2" in est or "título 2" in est: md.append("### " + t)
            elif "heading" in est or "título" in est: md.append("#### " + t)
            else: md.append(t)
        else:
            tables += 1
            md.append("\n**Tabla %d**\n" % tables)
            width = len(element.columns)
            md.append("| " + " | ".join(f"Columna {n+1}" for n in range(width)) + " |")
            md.append("| " + " | ".join("---" for _ in range(width)) + " |")
            for fila in element.rows:
                md.append("| " + " | ".join(md_cell(c.text) for c in fila.cells) + " |")
    n = 0
    for rel in d.part.rels.values():                       # imágenes embebidas
        if "image" in rel.reltype and not rel.is_external:
            n += 1
            with io.open(os.path.join(dest, "img%02d.%s" % (n, rel.target_ref.split(".")[-1])), "wb") as salida:
                salida.write(rel.target_part.blob)
    return "\n\n".join(md), n


def _texto_cad(s):
    """Limpia los códigos de formato de AutoCAD de un texto: `\\fRomanD|b1|…;\\W.8;1` -> `1`,
    `%%C` -> `Ø`, `%%D` -> `°`, `\\P` -> salto. Sin esto el MD sale lleno de ruido ilegible."""
    import re as _re
    s = str(s or "")
    s = s.replace("%%C", "Ø").replace("%%c", "Ø").replace("%%D", "°").replace("%%d", "°")
    s = s.replace("%%P", "±").replace("%%p", "±").replace("%%%", "%")
    s = _re.sub(r"\\[fF][^;]*;", "", s)          # fuente
    s = _re.sub(r"\\[A-Za-z][^\\;]*;", "", s)    # ancho, altura, color, seguimiento…
    s = s.replace("\\P", " ").replace("\\~", " ")
    s = _re.sub(r"[{}]", "", s)
    return _re.sub(r"\s+", " ", s).strip()[:300]


def dwg_medir(ruta, dest):
    """Un DWG no se convierte a prosa: se MIDE. Textos, bloques, capas y **la GEOMETRÍA** (el canal
    que un extractor de solo textos descarta: ahí vive el metrado lineal). Se leen el model space
    y los layouts: 6 planos daban CERO porque sus ~3.740 entidades vivían en el paper space.
    """
    import ezdxf, subprocess, tempfile, shutil, math, collections
    os.makedirs(dest, exist_ok=True)
    if ruta.lower().endswith(".dxf"):
        # Desviación única respecto al original: un DXF ya es lo que ODA produce; se lee directo.
        d = ezdxf.readfile(ruta)
    else:
        if not os.path.isfile(ODA_EXE):
            raise RuntimeError("Falta ODA File Converter. Instálalo por su vía oficial o indica su ejecutable con SISTEMA_MD_ODA.")
        # ODA convierte por CARPETA, no por archivo
        ent = tempfile.mkdtemp(prefix="dwg_in_"); sal = tempfile.mkdtemp(prefix="dwg_out_")
        try:
            shutil.copy2(ruta, os.path.join(ent, os.path.basename(ruta)))
            subprocess.run([ODA_EXE, ent, sal, "ACAD2018", "DXF", "0", "1"], capture_output=True, timeout=900)
            dxfs = [f for f in os.listdir(sal) if f.lower().endswith(".dxf")]
            if not dxfs: raise RuntimeError("ODA no produjo DXF (¿archivo dañado o versión no soportada?)")
            d = ezdxf.readfile(os.path.join(sal, dxfs[0]))
        finally:
            # ezdxf ya cargó el DXF en memoria: los dos temporales se borran aquí, pase lo que pase
            shutil.rmtree(ent, ignore_errors=True)
            shutil.rmtree(sal, ignore_errors=True)
    espacios = [d.modelspace()]
    for lay in d.layouts:
        try:
            if lay.name.lower() != "model": espacios.append(lay)
        except Exception: pass
    textos, bloques, largo_capa, tipos = [], collections.Counter(), collections.Counter(), collections.Counter()
    n_ent = 0
    for esp in espacios:
        for e in esp:
            n_ent += 1
            t = e.dxftype(); tipos[t] += 1
            capa = getattr(e.dxf, "layer", "")
            try:
                if t == "TEXT": textos.append((capa, _texto_cad(e.dxf.text)))
                elif t == "MTEXT":
                    # 🔴 `e.text` devuelve el MTEXT con los códigos de formato de AutoCAD dentro:
                    # `\fRomanD|b1|i0|c0|p2|;\W.8;1` cuando el texto real es «1». `plain_text()`
                    # los quita; si la versión de ezdxf no lo trae, se limpia a mano.
                    try: bruto = e.plain_text()
                    except Exception: bruto = str(e.text)
                    textos.append((capa, _texto_cad(bruto)))
                elif t == "INSERT":
                    bloques[str(e.dxf.name)] += 1
                    for a in getattr(e, "attribs", []):
                        textos.append((capa, "%s = %s" % (a.dxf.tag, _texto_cad(a.dxf.text))))
                # 🔑 GEOMETRÍA: lo que el extractor viejo tiraba
                elif t == "LINE":
                    p, q = e.dxf.start, e.dxf.end
                    largo_capa[capa] += math.dist((p[0], p[1], p[2]), (q[0], q[1], q[2]))
                elif t in ("LWPOLYLINE", "POLYLINE"):
                    pts = [tuple(x[:2]) for x in e.get_points()] if t == "LWPOLYLINE" else \
                          [tuple(v.dxf.location[:2]) for v in e.vertices]
                    largo_capa[capa] += sum(math.dist(pts[i], pts[i+1]) for i in range(len(pts)-1))
                elif t == "ARC":
                    largo_capa[capa] += abs(e.dxf.end_angle - e.dxf.start_angle) * math.pi / 180 * e.dxf.radius
                elif t == "CIRCLE":
                    largo_capa[capa] += 2 * math.pi * e.dxf.radius
            except Exception:
                continue
    capas = [c.dxf.name for c in d.layers]
    xrefs = []
    try:
        for b in d.blocks:
            if b.block.dxf.flags & 4: xrefs.append(b.name)   # 4 = bloque externo (xref)
    except Exception: pass
    ext = None
    try:
        ext = (d.header.get("$EXTMIN"), d.header.get("$EXTMAX"))
    except Exception: pass
    # detalle a CSV: el dato fino se consulta, no se lee
    import csv as _csv
    with io.open(os.path.join(dest, "dwg_textos.csv"), "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f, delimiter=";"); w.writerow(["capa", "texto"]); w.writerows(textos)
    with io.open(os.path.join(dest, "dwg_geometria.csv"), "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f, delimiter=";"); w.writerow(["capa", "longitud_unidades_dibujo"])
        w.writerows([(c, round(v, 3)) for c, v in largo_capa.most_common()])
    return {"entidades": n_ent, "espacios": len(espacios), "textos": textos, "bloques": bloques,
            "largo_capa": largo_capa, "tipos": tipos, "capas": capas, "xrefs": xrefs,
            "extension": ext, "unidad_declarada": d.header.get("$INSUNITS")}


def dwg_md(ruta, nombre, dest):
    m = dwg_medir(ruta, dest)
    md = ["# %s" % os.path.splitext(nombre)[0], "",
          "> 🖼️ **Plano CAD medido, no transcrito.** El dibujo no se convierte a texto: se cataloga "
          "lo que el archivo declara. El detalle fino vive en `dwg_textos.csv` y `dwg_geometria.csv`.", "",
          "| Dato | Valor |", "|---|---|",
          "| Entidades | %d |" % m["entidades"],
          "| Espacios leídos | %d (model + layouts) |" % m["espacios"],
          "| Textos y atributos | %d |" % len(m["textos"]),
          "| Bloques insertados | %d (%d nombres distintos) |" % (sum(m["bloques"].values()), len(m["bloques"])),
          "| Capas | %d |" % len(m["capas"]),
          "| Xrefs | %s |" % (", ".join(m["xrefs"]) if m["xrefs"] else "ninguna"),
          "| `$INSUNITS` declarado | %s ⚠️ *una cabecera declara una intención; la unidad se deduce del dato* |" % m["unidad_declarada"],
          ""]
    if m["extension"] and all(m["extension"]):
        (a, b) = m["extension"]
        md += ["| Extensión del dibujo | X %.1f a %.1f · Y %.1f a %.1f |" % (a[0], b[0], a[1], b[1]), ""]
    if m["bloques"]:
        md += ["## Bloques por nombre (lo que un PDF no puede contener)", "", "| Bloque | Veces |", "|---|---:|"]
        md += ["| %s | %d |" % (n, c) for n, c in m["bloques"].most_common(25)]
        md += [""]
    if m["largo_capa"]:
        md += ["## Longitud por capa (unidades de dibujo)", "",
               "> El canal que un extractor de solo textos descarta: aquí vive el metrado lineal.", "",
               "| Capa | Longitud |", "|---|---:|"]
        md += ["| %s | %.2f |" % (c, v) for c, v in m["largo_capa"].most_common(25)]
        md += [""]
    largos = [t for _, t in m["textos"] if len(t.strip()) > 12]
    if largos:
        md += ["## Textos del plano (primeros 60; el resto en el CSV)", ""]
        md += ["- %s" % t.strip() for t in largos[:60]] + [""]
    md += ["## Testigo", "> «%s»" % (largos[0].strip()[:90] if largos else nombre)]
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
    md = []; visual = 0; visuales = set()
    for i, s in enumerate(pr.slides, 1):
        md.append("## Diapositiva %d" % i)
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
                etiquetas[sh.shape_id] = sh.text_frame.text.strip().splitlines()[0][:60]
        conectores = []
        for sh in walk(s.shapes):
            if sh.has_text_frame and sh.text_frame.text.strip():
                md.append(sh.text_frame.text.strip())
            elif sh.has_table:
                md.extend(["", "| " + " | ".join(f"Columna {n+1}" for n in range(len(sh.table.columns))) + " |",
                           "| " + " | ".join("---" for _ in sh.table.columns) + " |"])
                md.extend("| " + " | ".join(md_cell(c.text) for c in row.cells) + " |" for row in sh.table.rows)
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
        if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
            md.append("> **Notas del orador:** " + s.notes_slide.notes_text_frame.text.strip())
        md.append("")
    return "\n".join(md), visual, visuales
