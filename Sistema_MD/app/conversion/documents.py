"""Contrato interno y exportación reproducible, sin llamadas a modelos."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA_VERSION = 1
KINDS = {"heading", "paragraph", "table", "quote", "code", "list", "markdown"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def load_json(data: str | bytes) -> object:
    """Rechaza pérdidas silenciosas por claves duplicadas y constantes no JSON."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Clave JSON duplicada: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Constante JSON inválida: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)


def read_stable(path: Path) -> bytes:
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"La fuente cambió mientras se leía: {path}")
    if len(data) != after.st_size:
        raise ValueError(f"Lectura incompleta: {path}")
    return data


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:-(\d+))?\s*", part)
        if not match:
            raise ValueError("Páginas: usa 1-13 o 1,3,7-10")
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start or end > 100000:
            raise ValueError("Rango de páginas inválido")
        pages.extend(range(start, end + 1))
    if len(pages) != len(set(pages)):
        raise ValueError("El alcance contiene páginas duplicadas")
    return sorted(pages)


def parse_markdown(text: str, unit: int) -> list[dict]:
    """Preserva cada carácter; solo clasifica bloques sintácticos existentes.

    No deduce jerarquías visuales ni modifica tablas. La semántica de un Markdown
    heredado permanece pendiente de revisión contra su fuente.
    """
    lines = text.splitlines(keepends=True)
    blocks: list[dict] = []
    current: list[str] = []
    fence: tuple[str, int] | None = None

    def flush() -> None:
        if not current:
            return
        raw = "".join(current)
        stripped = raw.lstrip()
        heading = re.match(r"^(#{1,6})[ \t]+", raw)
        kind = "paragraph"
        if heading:
            kind = "heading"
        elif re.match(r"^\s*(`{3,}|~{3,})", raw):
            kind = "code"
        elif stripped.startswith(">"):
            kind = "quote"
        elif stripped.startswith("|"):
            kind = "table"
        elif re.match(r"^\s*(?:[-*+] |\d+[.)] )", raw):
            kind = "list"
        block = {"id": f"p{unit}-b{len(blocks)+1}", "kind": kind, "text": raw}
        if heading:
            block["level"] = len(heading[1])
        blocks.append(block)
        current.clear()

    for line in lines:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            current.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]:
                fence = None
            continue
        if marker:
            flush()
            fence = (marker[1][0], len(marker[1]))
            current.append(line)
        elif re.match(r"^#{1,6}[ \t]+", line):
            flush()
            current.append(line)
            flush()
        else:
            current.append(line)
            if not line.strip():
                flush()
    flush()
    return blocks


def validate(doc: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = list(doc.get("producer_warnings", []))
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append("Versión de contrato no soportada")
    # H01: el contrato se exige aquí, no se delega en que el productor se porte bien.
    # render() hace doc["title"]: sin esta guarda, un documento sin título pasa la
    # validación y revienta al publicarse.
    title = doc.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("Título ausente o vacío")
    expected = doc.get("expected_units", [])
    if not expected or any(type(n) is not int or n < 1 for n in expected):
        errors.append("Alcance vacío o inválido")
    if len(set(expected)) != len(expected):
        errors.append("Alcance duplicado")
    seen: set[int] = set()
    block_ids: set[str] = set()
    for unit in doc.get("units", []):
        n = unit.get("number")
        if type(n) is not int or n in seen or n not in expected:
            errors.append(f"Unidad inesperada/duplicada: {n}")
        seen.add(n)
        blocks = unit.get("blocks", [])
        content = "".join(b.get("text", "") for b in blocks)
        if not content.strip():
            errors.append(f"Unidad {n} vacía")
        for block in blocks:
            ident = block.get("id")
            if not ident or ident in block_ids or block.get("kind") not in KINDS:
                errors.append(f"Bloque inválido/duplicado en {n}")
            # H01: un encabezado solo admite niveles 1-6. Se comprueba únicamente si el
            # productor declaró el nivel: las respuestas de IA entregan kind+text sin él
            # y ese flujo es legítimo.
            if block.get("kind") == "heading" and "level" in block:
                level = block["level"]
                if type(level) is not int or not 1 <= level <= 6:
                    errors.append(f"Encabezado fuera de rango en unidad {n}: level={level!r}")
            block_ids.add(ident)
        if re.search(r"\[(?:ilegible|verificar|pendiente)", content, re.I):
            warnings.append(f"Unidad {n}: el motor declaró dudas")
        if doc.get("input_kind") == "legacy_pages":
            # Un testigo es una señal estructural, nunca una prueba de fidelidad.
            witness = re.search(r"(?m)^## Testigo\s*\(pág\.\s*(\d+)\)", content)
            if not witness or int(witness[1]) != n:
                warnings.append(f"Unidad {n}: testigo ausente o de otra página")
        if re.search(r"!\[[^\]]*\]\([^)]+\)", content):
            warnings.append(f"Unidad {n}: revisar recursos enlazados en el Markdown heredado")
        if re.search(r"<script\b|javascript:", content, re.I):
            warnings.append(f"Unidad {n}: contenido activo; no renderizar HTML sin saneamiento")
    missing = sorted(set(expected) - seen)
    errors.extend(f"Falta unidad {n}" for n in missing)
    errors.extend(f"Falta imagen solicitada de unidad {n}" for n in doc.get("missing_images", []))
    # Ninguna importación de salida ajena se autopromueve a fidelidad validada.
    semantic = False
    status = "parcial" if errors else "revisar"
    return {"status": status, "structural_ok": not errors,
            "semantic_verified": semantic, "expected": len(expected),
            "received": len(seen), "missing": missing, "errors": errors,
            "warnings": warnings}


def render(doc: dict, qa: dict) -> str:
    title = str(doc["title"]).replace("\n", " ").replace("\r", " ")
    out = [f"# {title}\n\n", f"Estado: **{qa['status']}** · cobertura: {qa['received']}/{qa['expected']} unidades.\n\n",
           "Fidelidad contra el original: **pendiente de revisión**.\n\n",
           "[Control y procedencia](document.json) · [Verificación](verificacion.json)\n\n",
           "## Índice\n\n"]
    out += [f"- [Página/unidad {n}](#unidad-{n})\n" for n in doc["expected_units"]]
    by_number = {u["number"]: u for u in doc["units"]}
    for n in doc["expected_units"]:
        out.append(f'\n---\n\n<a id="unidad-{n}"></a>\n\n## Página/unidad {n}\n\n')
        unit = by_number.get(n)
        if unit is None:
            out.append("[PENDIENTE: esta unidad no fue recibida.]\n")
            continue
        # Nombres locales construidos por el sistema, nunca rutas del modelo.
        if unit.get("image_asset"):
            out.append(f"![Referencia visual de la página {n}]({unit['image_asset']})\n\n")
        out.extend(b["text"] for b in unit["blocks"])
        out.append("\n")
    return "".join(out)
