"""Word DOCX a Markdown recorriendo el XML en orden; 0 llamadas a IA.

python-docx expone párrafos y tablas, pero ``cell.text`` omite las tablas anidadas,
``paragraph.text`` omite fórmulas OMML y referencias a notas, y no hay API para notas
al pie/finales, comentarios ni encabezados. Este lector recorre ``w:body`` en orden de
documento y conserva cada canal con su marca:

- tablas: rejilla completa (gridSpan/vMerge se propagan; gridBefore/After se rellenan),
  tablas anidadas publicadas aparte con una referencia ``[tabla anidada 1.1]``;
- notas: ``[^1]`` en el texto y ``[^1]: …`` al final; notas finales como ``[^fin1]``;
- OMML: ``[fórmula OMML: E^(2)=(a)/(b)]`` linealizada, sin interpretar;
- listas (numPr) con nivel y viñeta/número; bloques de código en valla ``` propia;
- texto libre neutralizado para Markdown (``md_text``): se conserva, no se reinterpreta.

Revisiones (``w:del``) y códigos de campo no se publican como texto vigente.
"""
from __future__ import annotations

import os
import re

from .local_io import clean_control, md_cell, md_inline, md_text

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MONO = re.compile(r"courier|consolas|mono|menlo|lucida console", re.I)
CODE_STYLE = re.compile(r"code|c[oó]digo|preformat|source|verbatim", re.I)
SKIP = {"delText", "del", "instrText", "fldChar", "rPr", "pPr", "moveFrom", "commentRangeStart",
        "commentRangeEnd", "bookmarkStart", "bookmarkEnd", "proofErr", "lastRenderedPageBreak"}
MAX_DEPTH = 12


def _local(element) -> tuple[str, str]:
    tag = element.tag if isinstance(element.tag, str) else ""
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return namespace, name
    return "", tag


def _attr(element, name, namespace=W):
    return element.get(f"{{{namespace}}}{name}") if element is not None else None


def _child(element, name, namespace=W):
    return element.find(f"{{{namespace}}}{name}") if element is not None else None


def omml(element) -> str:
    """Linealiza OMML conservando estructura: fracciones, potencias, raíces y n-arios."""
    namespace, name = _local(element)
    parts = lambda child: "".join(omml(x) for x in element if _local(x)[1] == child)
    if name == "t":
        return element.text or ""
    if name.endswith("Pr"):
        return ""
    if name == "f":
        return f"({parts('num')})/({parts('den')})"
    if name == "sSup":
        return f"{parts('e')}^({parts('sup')})"
    if name == "sSub":
        return f"{parts('e')}_({parts('sub')})"
    if name == "sSubSup":
        return f"{parts('e')}_({parts('sub')})^({parts('sup')})"
    if name == "rad":
        degree = parts("deg")
        return f"{'√' if not degree else f'root[{degree}]'}({parts('e')})"
    if name == "d":
        properties = _child(element, "dPr", M)
        begin = _attr(_child(properties, "begChr", M), "val", M) or "("
        end = _attr(_child(properties, "endChr", M), "val", M) or ")"
        return begin + ",".join(omml(x) for x in element if _local(x)[1] == "e") + end
    if name == "nary":
        properties = _child(element, "naryPr", M)
        symbol = _attr(_child(properties, "chr", M), "val", M) or "∫"
        return f"{symbol}_({parts('sub')})^({parts('sup')}) {parts('e')}"
    return "".join(omml(x) for x in element)


class _Context:
    def __init__(self, document):
        self.document = document
        self.rels = document.part.rels
        self.numbering = self._numbering()
        self.tables = 0
        self.footnotes: set[str] = set()
        self.endnotes: set[str] = set()

    def _numbering(self) -> dict[tuple[str, str], str]:
        try:
            root = self.document.part.numbering_part.element
        except Exception:
            return {}
        abstract = {}
        for item in root.findall(f"{{{W}}}abstractNum"):
            levels = {_attr(level, "ilvl"): _attr(_child(level, "numFmt"), "val") or "bullet"
                      for level in item.findall(f"{{{W}}}lvl")}
            abstract[_attr(item, "abstractNumId")] = levels
        formats = {}
        for num in root.findall(f"{{{W}}}num"):
            levels = abstract.get(_attr(_child(num, "abstractNumId"), "val"), {})
            for level, fmt in levels.items():
                formats[(_attr(num, "numId"), level)] = fmt
        return formats

    def hyperlink(self, element, text):
        rid = _attr(element, "id", R)
        try:
            relation = self.rels[rid] if rid else None
            url = relation.target_ref if relation is not None and relation.is_external else None
        except KeyError:                      # r:id sin relación: el texto se conserva
            url = None
        if url and re.match(r"^(?:https?|mailto):", url, re.I) and text.strip():
            # Codificado: sin espacios, <>, | ni paréntesis, el destino no necesita <…>
            # y ningún escape posterior del párrafo puede romperlo.
            safe = re.sub(r"[<>\s|()\[\]]", lambda m: "%{:02X}".format(ord(m.group())), url)
            label = text.replace("[", "\\[").replace("]", "\\]")
            return f"[{label}]({safe})"
        return text


def _inline(element, context, depth=0) -> str:
    """Texto de un párrafo o fragmento en orden de documento."""
    out = []
    if depth > MAX_DEPTH:
        return ""
    for child in element:
        namespace, name = _local(child)
        if namespace == W:
            if name == "t":
                out.append(child.text or "")
            elif name == "tab":
                out.append("\t")
            elif name in {"br", "cr"}:
                out.append("\n")
            elif name == "noBreakHyphen":
                out.append("-")
            elif name == "sym":
                code = _attr(child, "char") or ""
                try:
                    out.append(chr(int(code, 16) - (0xF000 if int(code, 16) >= 0xF000 else 0)))
                except ValueError:
                    pass
            elif name == "footnoteReference":
                note = _attr(child, "id")
                context.footnotes.add(note)
                out.append(f"[^{note}]")
            elif name == "endnoteReference":
                note = _attr(child, "id")
                context.endnotes.add(note)
                out.append(f"[^fin{note}]")
            elif name == "hyperlink":
                out.append(context.hyperlink(child, _inline(child, context, depth + 1)))
            elif name in SKIP:
                continue
            else:
                out.append(_inline(child, context, depth + 1))
        elif namespace == M and name in {"oMath", "oMathPara"}:
            formula = omml(child).strip()
            if formula:
                out.append(f" [fórmula OMML: {formula}] ")
        elif namespace == MC and name == "AlternateContent":
            choice = child.find(f"{{{MC}}}Choice")
            if choice is not None:
                out.append(_inline(choice, context, depth + 1))
        else:
            out.append(_inline(child, context, depth + 1))
    return "".join(out)


def _paragraph_kind(paragraph, context):
    """(tipo, nivel, formato): título, lista, código o párrafo."""
    properties = _child(paragraph, "pPr")
    style = _attr(_child(properties, "pStyle"), "val") or ""
    style_name = style
    try:
        style_name = context.document.styles.get_by_id(style, 1).name or style
    except Exception:
        pass
    lowered = style_name.lower()
    match = re.search(r"(?:heading|t[ií]tulo)\s*(\d)", lowered)
    if match or lowered in {"title", "título"}:
        return "heading", int(match.group(1)) if match else 1, None
    numbering = _child(properties, "numPr")
    if numbering is not None:
        level = _attr(_child(numbering, "ilvl"), "val") or "0"
        num_id = _attr(_child(numbering, "numId"), "val")
        if num_id and num_id != "0":
            return "list", int(level) if level.isdigit() else 0, context.numbering.get((num_id, level), "bullet")
    if re.search(r"list|lista|vi[nñ]eta", lowered):
        level = re.search(r"(\d)$", lowered)
        return "list", (int(level.group(1)) - 1) if level else 0, "decimal" if "number" in lowered else "bullet"
    fonts = [_attr(font, "ascii") or "" for font in paragraph.iter(f"{{{W}}}rFonts")]
    if CODE_STYLE.search(style_name) or (fonts and all(MONO.search(f) for f in fonts)):
        return "code", 0, None
    return "paragraph", 0, None


def _cell_text(cell, context, label, nested, depth) -> str:
    pieces = []
    for child in cell:
        _namespace, name = _local(child)
        if name == "p":
            pieces.append(_inline(child, context))
        elif name == "tbl":
            index = sum(1 for item in nested if item[0].startswith(label + ".")) + 1
            nested_label = f"{label}.{index}"
            nested.append((nested_label, _table(child, context, nested_label, depth + 1)))
            pieces.append(f"[tabla anidada {nested_label}]")
        elif name == "sdt":
            content = _child(child, "sdtContent")
            if content is not None:
                pieces.append(_cell_text(content, context, label, nested, depth))
    return "\n".join(piece for piece in pieces if piece.strip())


def _table(table, context, label, depth=0) -> list[str]:
    """Rejilla rectangular: cada fila tiene exactamente el ancho de ``w:tblGrid``."""
    if depth > MAX_DEPTH:
        return [f"[tabla anidada {label}: profundidad > {MAX_DEPTH}, no expandida]"]
    grid = _child(table, "tblGrid")
    width = len(grid.findall(f"{{{W}}}gridCol")) if grid is not None else 0
    nested: list[tuple[str, list[str]]] = []
    rows, above = [], {}
    for row in table.findall(f"{{{W}}}tr"):
        properties = _child(row, "trPr")
        before = int(_attr(_child(properties, "gridBefore"), "val") or 0)
        cells = [""] * before
        for cell in row.findall(f"{{{W}}}tc"):
            cell_properties = _child(cell, "tcPr")
            span = max(1, int(_attr(_child(cell_properties, "gridSpan"), "val") or 1))
            merge = _child(cell_properties, "vMerge")
            column = len(cells)
            if merge is not None and _attr(merge, "val") != "restart":
                text = above.get(column, "")          # continuación vertical: se propaga
            else:
                text = _cell_text(cell, context, label, nested, depth)
            for offset in range(span):
                above[column + offset] = text
                cells.append(text)
        rows.append(cells)
    width = max([width] + [len(cells) for cells in rows]) or 1
    lines = [f"**Tabla {label}**", "",
             "| " + " | ".join(f"Columna {n + 1}" for n in range(width)) + " |",
             "| " + " | ".join("---" for _ in range(width)) + " |"]
    for cells in rows:
        cells = cells + [""] * (width - len(cells))
        lines.append("| " + " | ".join(md_cell(value) for value in cells) + " |")
    for nested_label, nested_lines in nested:
        lines += ["", f"*Tabla {nested_label}: anidada dentro de la tabla {label}.*", ""] + nested_lines
    return lines


def _notes(document, kind, context) -> list[str]:
    """Notas al pie/finales desde su parte XML (python-docx no las expone)."""
    from lxml import etree
    element_name = "footnote" if kind == "footnotes" else "endnote"
    lines = []
    for relation in document.part.rels.values():
        if not relation.reltype.endswith("/" + kind) or relation.is_external:
            continue
        root = etree.fromstring(relation.target_part.blob, etree.XMLParser(resolve_entities=False, no_network=True))
        for note in root.findall(f"{{{W}}}{element_name}"):
            if _attr(note, "type") in {"separator", "continuationSeparator", "continuationNotice"}:
                continue
            text = " ".join(_inline(p, context).strip() for p in note.iter(f"{{{W}}}p"))
            prefix = "" if kind == "footnotes" else "fin"
            lines.append(f"[^{prefix}{_attr(note, 'id')}]: {md_text(' '.join(text.split()))}")
    return lines


def _related_parts(document, suffix, title, context) -> list[str]:
    """Encabezados, pies y comentarios: canales fuera del cuerpo principal."""
    from lxml import etree
    seen, lines = set(), []
    for relation in document.part.rels.values():
        if relation.is_external or not relation.reltype.endswith("/" + suffix):
            continue
        part = relation.target_part
        if part.partname in seen:
            continue
        seen.add(part.partname)
        root = etree.fromstring(part.blob, etree.XMLParser(resolve_entities=False, no_network=True))
        for paragraph in root.iter(f"{{{W}}}p"):
            text = " ".join(_inline(paragraph, context).split())
            if text:
                lines.append("- " + md_text(text))
    return (["", f"## {title}", ""] + list(dict.fromkeys(lines))) if lines else []


def docx_markdown(ruta, dest):
    """Devuelve (markdown, imágenes_guardadas). Las imágenes quedan en ``dest``."""
    import docx
    os.makedirs(dest, exist_ok=True)
    with open(ruta, "rb") as stream:
        document = docx.Document(stream)
    context = _Context(document)
    body = document.element.body
    md: list[str] = []
    code: list[str] = []
    list_level = -1

    def flush_code():
        if code:
            longest = max((len(run) for line in code for run in re.findall(r"`+", line)), default=0)
            fence = "`" * max(3, longest + 1)
            md.extend([fence] + code + [fence])
            code.clear()

    def blocks(container):
        for child in container:
            _namespace, name = _local(child)
            if name == "sdt":
                content = _child(child, "sdtContent")
                if content is not None:
                    yield from blocks(content)
            elif name in {"p", "tbl"}:
                yield child

    for element in blocks(body):
        _namespace, name = _local(element)
        if name == "tbl":
            flush_code()
            list_level = -1
            context.tables += 1
            md.extend([""] + _table(element, context, str(context.tables)) + [""])
            continue
        kind, level, fmt = _paragraph_kind(element, context)
        text = clean_control(_inline(element, context))
        if kind == "code":
            if text.strip():
                code.extend(text.split("\n"))
            continue
        flush_code()
        if not text.strip():
            continue
        if kind == "heading":
            list_level = -1
            md.append("#" * min(6, level + 1) + " " + md_inline(" ".join(text.split())))
            md.append("")
        elif kind == "list":
            level = min(level, list_level + 1)            # saltos 0→5: anidación válida
            list_level = level
            marker = "-" if fmt in {"bullet", "none", None} else "1."
            lines = md_text(text).split("\n")
            md.append("   " * level + marker + " " + lines[0])
            md.extend("   " * level + "   " + line for line in lines[1:])
        else:
            list_level = -1
            md.append(md_text(text))
            md.append("")
    flush_code()
    footnotes = _notes(document, "footnotes", context)
    endnotes = _notes(document, "endnotes", context)
    if footnotes:
        md += ["", "## Notas al pie", ""] + footnotes
    if endnotes:
        md += ["", "## Notas finales", ""] + endnotes
    md += _related_parts(document, "comments", "Comentarios", context)
    md += _related_parts(document, "header", "Encabezados de página", context)
    md += _related_parts(document, "footer", "Pies de página", context)

    images = 0
    for relation in document.part.rels.values():
        if "image" in relation.reltype and not relation.is_external:
            images += 1
            extension = relation.target_ref.rsplit(".", 1)[-1].lower()
            with open(os.path.join(dest, "img%02d.%s" % (images, extension)), "wb") as output:
                output.write(relation.target_part.blob)
    return "\n".join(md), images
