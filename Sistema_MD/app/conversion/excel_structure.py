"""Estructura de lectura para Excel sin perder la rejilla de auditoría.

El libro no es una sola tabla. Una hoja puede contener catálogos paralelos,
indicadores, una base ancha, notas y zonas auxiliares. Este módulo usa primero
las tablas y celdas combinadas que declara el propio XLSX. Solo cuando no hay
estructura nativa separa regiones por continuidad; esas regiones quedan con
confianza media y una referencia de celdas verificable.

La vista de lectura y la rejilla original son dos productos distintos. La
primera puede dividir una tabla ancha y repetir sus identificadores; la segunda
conserva todas las coordenadas y columnas para auditoría.
"""

from __future__ import annotations

import re
import html
from collections import deque


STRUCTURE_VERSION = 1
MAX_READER_COLUMNS = 9


def _letter(column: int) -> str:
    value = ""
    while column:
        column, rest = divmod(column - 1, 26)
        value = chr(65 + rest) + value
    return value


def _range(min_row: int, min_col: int, max_row: int, max_col: int) -> str:
    return f"{_letter(min_col)}{min_row}:{_letter(max_col)}{max_row}"


def _clean(value: object) -> str:
    text = re.sub(r"<br\s*/?>", " — ", str(value or ""), flags=re.I)
    text = html.unescape(text).replace("|", "&#124;")
    return re.sub(r"\s+", " ", text).strip()


def _heading(value: object, fallback: str) -> str:
    text = _clean(value).replace("_", " ")
    if not text or re.fullmatch(r"Tabla\d+", text, re.I):
        return fallback
    return text


def _helper_header(value: str) -> bool:
    value = _clean(value)
    return bool(re.fullmatch(r"(?:S\d*|x|Columna\d*)", value, re.I))


def _title_from_headers(columns: list[int], headers: dict[int, str]) -> str:
    labels = [_clean(headers.get(column)) for column in columns]
    labels = [label for label in labels if label and not _helper_header(label)]
    if not labels:
        return f"Columnas {_letter(columns[0])}–{_letter(columns[-1])}"
    first, last = labels[0], labels[-1]
    title = first if first == last else f"{first} → {last}"
    return title if len(title) <= 78 else title[:75].rstrip() + "…"


def _key_columns(columns: list[int], headers: dict[int, str]) -> list[int]:
    keys: list[int] = []
    patterns = (r"\b(?:c[oó]digo|id|n[uú]mero|referencia)\b",
                r"\b(?:paquete|t[ií]tulo|nombre|descripci[oó]n)\b")
    for pattern in patterns:
        for column in columns:
            if column not in keys and re.search(pattern, _clean(headers.get(column)), re.I):
                keys.append(column)
                break
    if not keys:
        keys = [next((column for column in columns
                      if not _helper_header(headers.get(column, ""))), columns[0])]
    return keys[:2]


def _chunks(columns: list[int], size: int = MAX_READER_COLUMNS) -> list[list[int]]:
    return [columns[index:index + size] for index in range(0, len(columns), size)]


def _native_groups(spec: dict, rows: dict[int, dict[int, str]], merges: list[dict],
                   headers: dict[int, str]) -> tuple[list[dict], set[tuple[int, int]]]:
    """Devuelve grupos declarados sobre una tabla ancha y celdas visuales consumidas."""
    groups: list[dict] = []
    consumed: set[tuple[int, int]] = set()
    header_row = spec["min_row"]
    for merged in merges:
        if not (header_row - 3 <= merged["max_row"] < header_row):
            continue
        start = max(spec["min_col"], merged["min_col"])
        end = min(spec["max_col"], merged["max_col"])
        if end < start:
            continue
        label = _clean(rows.get(merged["min_row"], {}).get(merged["min_col"]))
        if not label:
            continue
        columns = [column for column in range(start, end + 1)
                   if not _helper_header(headers.get(column, ""))]
        if columns:
            groups.append({"title": label, "columns": columns, "evidence": merged["ref"]})
            for row in range(merged["min_row"], merged["max_row"] + 1):
                for column in range(merged["min_col"], merged["max_col"] + 1):
                    consumed.add((row, column))

    # Algunos libros usan una etiqueta sin combinar para el último bloque.
    label_row = header_row - 2
    starts = []
    for column in range(spec["min_col"], spec["max_col"] + 1):
        label = _clean(rows.get(label_row, {}).get(column))
        if label and len(label) >= 8 and column not in {c for g in groups for c in g["columns"]}:
            starts.append((column, label))
    occupied_starts = sorted((min(g["columns"]), max(g["columns"])) for g in groups)
    last_native = max((max(group["columns"]) for group in groups), default=spec["min_col"] - 1)
    for column, label in starts:
        if column <= last_native or label.lstrip().startswith("*"):
            continue
        next_starts = [a for a, _ in occupied_starts if a > column] + [spec["max_col"] + 1]
        end = min(next_starts) - 1
        columns = [c for c in range(column, end + 1) if not _helper_header(headers.get(c, ""))]
        if columns:
            groups.append({"title": label, "columns": columns,
                           "evidence": f"{_letter(column)}{label_row}"})
            consumed.add((label_row, column))

    # Una segunda fila visual de encabezados no aporta datos distintos a la tabla.
    visual_row = header_row - 1
    present = sum(bool(_clean(rows.get(visual_row, {}).get(column)))
                  for column in range(spec["min_col"], spec["max_col"] + 1))
    if present / max(1, spec["max_col"] - spec["min_col"] + 1) >= .35:
        consumed.update((visual_row, column)
                        for column in range(spec["min_col"], spec["max_col"] + 1))
    return groups, consumed


def _split_table(spec: dict, rows: dict[int, dict[int, str]], merges: list[dict]) -> tuple[list[dict], set]:
    columns = list(range(spec["min_col"], spec["max_col"] + 1))
    headers = {column: _clean(rows.get(spec["min_row"], {}).get(column)) or _letter(column)
               for column in columns}
    keys = _key_columns(columns, headers)
    native, consumed = _native_groups(spec, rows, merges, headers)
    claimed = {column for group in native for column in group["columns"]}
    helpers = [column for column in columns if _helper_header(headers[column])]
    remaining = [column for column in columns if column not in claimed and column not in helpers]

    groups = []
    for chunk in _chunks(remaining):
        groups.append({"title": _title_from_headers(chunk, headers), "columns": chunk,
                       "evidence": "encabezados de la tabla"})
    groups.extend(native)
    groups.sort(key=lambda group: min(group["columns"]))
    if helpers:
        groups.append({"title": "Campos técnicos del libro", "columns": helpers,
                       "evidence": "encabezados auxiliares"})

    blocks = []
    for index, group in enumerate(groups, 1):
        data_columns = group["columns"]
        shown = list(dict.fromkeys(keys + data_columns))
        blocks.append({
            "kind": "table",
            "title": group["title"],
            "source_ref": _range(spec["min_row"], min(data_columns), spec["max_row"], max(data_columns)),
            "confidence": "alta" if group["evidence"] != "encabezados de la tabla" else "media",
            "evidence": group["evidence"],
            "columns": shown,
            "headers": [headers[column] for column in shown],
            "data_rows": list(range(spec["min_row"] + 1, spec["max_row"] + 1)),
            "repeated_columns": [column for column in keys if column not in data_columns],
            "group_index": index,
        })
    covered = {column for group in groups for column in group["columns"]}
    if covered != set(columns):
        raise ValueError("La división de lectura no cubre todas las columnas de la tabla")
    return blocks, consumed


def _connected_regions(rows: dict[int, dict[int, str]], consumed: set[tuple[int, int]]) -> list[set]:
    remaining = {(row, column) for row, cells in rows.items() for column, value in cells.items()
                 if _clean(value) and (row, column) not in consumed}
    regions = []
    while remaining:
        seed = remaining.pop()
        region = {seed}
        queue = deque([seed])
        while queue:
            row, column = queue.popleft()
            for neighbor in ((row - 1, column), (row + 1, column),
                             (row, column - 1), (row, column + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    region.add(neighbor)
                    queue.append(neighbor)
        regions.append(region)
    return sorted(regions, key=lambda region: (min(r for r, _ in region), min(c for _, c in region)))


def _region_block(region: set, rows: dict[int, dict[int, str]]) -> dict:
    min_row, max_row = min(r for r, _ in region), max(r for r, _ in region)
    min_col, max_col = min(c for _, c in region), max(c for _, c in region)
    columns = list(range(min_col, max_col + 1))
    data_rows = list(range(min_row, max_row + 1))
    source_ref = _range(min_row, min_col, max_row, max_col)
    if len(region) == 1:
        return {"kind": "note", "title": "Nota del libro", "source_ref": source_ref,
                "confidence": "alta", "text": rows[min_row][min_col]}
    if len(columns) == 1:
        return {"kind": "list", "title": f"Lista {source_ref}", "source_ref": source_ref,
                "confidence": "media", "items": [rows.get(row, {}).get(min_col, "") for row in data_rows]}
    headers = [_letter(column) for column in columns]
    return {"kind": "table", "title": f"Bloque {source_ref}", "source_ref": source_ref,
            "confidence": "media", "evidence": "región continua de celdas",
            "columns": columns, "headers": headers, "data_rows": data_rows,
            "repeated_columns": []}


def structure_sheet(sheet: dict) -> dict:
    rows = sheet["rows"]
    consumed: set[tuple[int, int]] = set()
    blocks = []
    table_models = []
    for spec in sorted(sheet.get("tables", []), key=lambda item: (item["min_row"], item["min_col"])):
        name = _heading(spec.get("name"), sheet["name"])
        width = spec["max_col"] - spec["min_col"] + 1
        if width > MAX_READER_COLUMNS:
            parts, visual_consumed = _split_table(spec, rows, sheet.get("merges", []))
            blocks.append({"kind": "table_group", "title": name, "source_ref": spec["ref"],
                           "confidence": "alta", "parts": parts, "original_columns": width})
            consumed.update(visual_consumed)
        else:
            columns = list(range(spec["min_col"], spec["max_col"] + 1))
            headers = [_clean(rows.get(spec["min_row"], {}).get(column)) or _letter(column)
                       for column in columns]
            blocks.append({"kind": "table", "title": name, "source_ref": spec["ref"],
                           "confidence": "alta", "evidence": "tabla nativa del XLSX",
                           "columns": columns, "headers": headers,
                           "data_rows": list(range(spec["min_row"] + 1, spec["max_row"] + 1)),
                           "repeated_columns": []})
        table_models.append({"name": name, "ref": spec["ref"], "columns": width,
                             "rows": max(0, spec["max_row"] - spec["min_row"])})
        consumed.update((row, column)
                        for row in range(spec["min_row"], spec["max_row"] + 1)
                        for column in range(spec["min_col"], spec["max_col"] + 1))

    notes = []
    for region in _connected_regions(rows, consumed):
        block = _region_block(region, rows)
        if block["kind"] == "note":
            notes.append(block)
        else:
            blocks.append(block)
    if notes:
        blocks.insert(0, {"kind": "notes", "title": "Notas y controles del archivo",
                          "confidence": "alta", "items": notes})

    return {
        "id": re.sub(r"[^a-z0-9]+", "-", _clean(sheet["name"]).lower()).strip("-") or "hoja",
        "title": sheet["name"],
        "state": sheet["state"],
        "source_ref": sheet["dimension"],
        "stats": {"rows_with_data": len(rows), "columns_with_data": len(sheet["used_columns"]),
                  "nonempty_cells": sum(len(values) for values in rows.values()),
                  "native_tables": len(table_models)},
        "native_tables": table_models,
        "blocks": blocks,
    }


def _md_table(block: dict, rows: dict[int, dict[int, str]]) -> list[str]:
    headers = block["headers"]
    columns = block["columns"]
    output = ["| " + " | ".join(headers) + " |",
              "| " + " | ".join("---" for _ in headers) + " |"]
    for row in block["data_rows"]:
        values = [rows.get(row, {}).get(column, "") for column in columns]
        if any(_clean(value) for value in values):
            output.append("| " + " | ".join(values) + " |")
    return output


def _render_block(block: dict, rows: dict[int, dict[int, str]], level: int = 3) -> list[str]:
    prefix = "#" * level
    output = [f"{prefix} {block['title']}", ""]
    if block["kind"] == "notes":
        output += [f"- `{item['source_ref']}` — {item['text']}" for item in block["items"]]
    elif block["kind"] == "note":
        output.append(block["text"])
    elif block["kind"] == "list":
        output += [f"- {item}" for item in block["items"] if _clean(item)]
    elif block["kind"] == "table_group":
        output.append(f"> Tabla original de {block['original_columns']} columnas (`{block['source_ref']}`). "
                      "Se divide para lectura; los identificadores se repiten y ninguna columna se descarta.")
        output.append("")
        for part in block["parts"]:
            output += _render_block(part, rows, level + 1)
    else:
        repeated = block.get("repeated_columns") or []
        note = f"Fuente: `{block['source_ref']}` · confianza {block['confidence']}"
        if repeated:
            note += " · columnas de identificación repetidas para lectura"
        output += [f"*{note}.*", ""]
        output += _md_table(block, rows)
    output.append("")
    return output


def build_workbook(title: str, sheets: list[dict]) -> tuple[str, dict]:
    sections = [structure_sheet(sheet) for sheet in sheets]
    visible = [section for section in sections if section["state"] == "visible"]
    hidden = [section for section in sections if section["state"] != "visible"]
    model = {
        "schema_version": STRUCTURE_VERSION,
        "kind": "workbook",
        "title": title,
        "method": "XLSX nativo: tablas declaradas y regiones de celdas; sin IA",
        "groups": [
            {"title": "Hojas principales", "sections": visible},
            {"title": "Hojas auxiliares", "sections": hidden},
        ],
        "fidelity": {"reading_view_reorders_hidden_sheets": True,
                     "raw_grid_asset": "derivados/rejilla_original.md",
                     "cell_asset": "derivados/celdas.csv"},
    }
    by_name = {sheet["name"]: sheet for sheet in sheets}
    output = [f"# {title}", "",
              "> **Vista de lectura estructurada.** Se apoya en tablas, rangos y encabezados del XLSX. "
              "La rejilla completa permanece en la vista de auditoría.", "",
              "## Mapa del libro", "",
              f"- Hojas principales: {len(visible)}", f"- Hojas auxiliares/ocultas: {len(hidden)}",
              f"- Tablas nativas reconocidas: {sum(s['stats']['native_tables'] for s in sections)}", ""]
    for group in model["groups"]:
        if not group["sections"]:
            continue
        output += [f"## {group['title']}", ""]
        for section in group["sections"]:
            hidden_mark = "  *(oculta)*" if section["state"] != "visible" else ""
            stats = section["stats"]
            output += [f"## Hoja: {section['title']}{hidden_mark}",
                       f"filas {stats['rows_with_data']} · columnas con dato {stats['columns_with_data']} · "
                       f"{stats['native_tables']} tablas nativas", "",
                       f"> Rango usado `{section['source_ref']}` · {stats['nonempty_cells']} celdas con dato.", ""]
            sheet = by_name[section["title"]]
            for block in section["blocks"]:
                output += _render_block(block, sheet["rows"])
    output += ["## Auditoría y fidelidad", "",
               "- [Rejilla original por hoja](<derivados/rejilla_original.md>)",
               "- [Celdas y coordenadas](<derivados/celdas.csv>)",
               "- [Fórmulas exactas](<derivados/formulas.csv>)", "",
               "La vista de lectura reorganiza la presentación, no certifica que los valores cacheados "
               "estén actualizados ni reproduce estilos, gráficos o macros.", ""]
    return "\n".join(output), model
