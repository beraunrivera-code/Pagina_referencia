"""Cola persistente que separa seleccionar, analizar, convertir y verificar."""

from __future__ import annotations

import os
import uuid
from contextlib import closing
from functools import wraps
from pathlib import Path

from .documents import json_bytes, load_json
from .pipeline import NATIVE_KINDS, convert_native, convert_text, file_hash, inventory, prepare_pdf
from .storage import connect, verify_artifacts, output_folder


LOCAL_TEXT = {"md", "txt", "text"}


def serialized(method):
    """Mismo bloqueo SQLite para CLI y GUI; recarga antes de modificar."""
    @wraps(method)
    def run(self, *args, **kwargs):
        with closing(connect(self.root)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.load()
            return method(self, *args, **kwargs)
    return run


def action_for(kind: str) -> tuple[str, str]:
    if kind in LOCAL_TEXT:
        return "listo", "Convertir local"
    if kind in NATIVE_KINDS:
        return "listo", "Convertir local"
    if kind == "pdf":
        return "requiere_paginas", "Preparar PDF"
    if kind == "deferred":
        return "en_nube", "Descargar primero"
    return "pendiente", f"Adaptador {kind} pendiente"


class WorkQueue:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "cola.json"
        self.items: list[dict] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.items = []
            return
        payload = load_json(self.path.read_bytes())
        if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                or not isinstance(payload.get("items"), list)):
            raise ValueError("cola.json no cumple el contrato")
        paths = set()
        for item in payload["items"]:
            if (not isinstance(item, dict)
                    or not all(isinstance(item.get(key), str) and item[key]
                               for key in ("path", "name", "format", "status", "action"))
                    or type(item.get("bytes")) is not int or item["bytes"] < 0
                    or item["path"] in paths):
                raise ValueError("cola.json contiene una entrada inválida o duplicada; se conserva sin sobrescribir")
            paths.add(item["path"])
            if item.get("output"):
                item["output"] = str(output_folder(self.root, item["output"]))
        self.items = payload["items"]

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / (".cola-" + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(json_bytes({"schema_version": 1, "items": self.items}))
        os.replace(temporary, self.path)

    @serialized
    def add(self, source: Path, limit: int = 200) -> dict:
        source = source.resolve(strict=True)
        snapshot = inventory(source, limit, self.root)
        known = {item["path"]: item for item in self.items}
        added = 0
        for row in snapshot["files"]:
            status, action = action_for(row["format"])
            item = known.get(row["path"], {})
            if (item.get("output") and item.get("sha256") == row.get("sha256")
                    and verify_artifacts(Path(item["output"]))):
                continue
            for key in ("output", "reused", "source_changed"):
                item.pop(key, None)
            item.update({"path": row["path"], "name": Path(row["path"]).name,
                         "format": row["format"], "bytes": row["bytes"],
                         "sha256": row.get("sha256"), "status": status, "action": action})
            if row["path"] not in known:
                self.items.append(item)
                known[row["path"]] = item
                added += 1
        self.save()
        return {"status": "analizado" if not snapshot["errors"] else "parcial",
                "added": added, "total": len(self.items), "scanned": snapshot["count"],
                "truncated": snapshot["truncated"], "errors": snapshot["errors"]}

    @serialized
    def remove(self, paths: list[str]) -> int:
        wanted = set(paths)
        before = len(self.items)
        self.items = [item for item in self.items if item["path"] not in wanted]
        self.save()
        return before - len(self.items)

    @serialized
    def clear(self) -> None:
        self.items = []
        self.save()

    def process(self, source_text: str, pages: list[int] | None = None) -> dict:
        self.load()
        matches = [item for item in self.items if item["path"] == source_text]
        if not matches:
            raise ValueError("El archivo no pertenece a la cola actual")
        item = matches[0]
        source = Path(item["path"]).resolve(strict=True)
        current_hash = file_hash(source)
        source_changed = bool(item.get("sha256") and item["sha256"] != current_hash)
        if item["format"] in LOCAL_TEXT:
            result = convert_text(self.root, source)
        elif item["format"] in NATIVE_KINDS:
            result = convert_native(self.root, source)
        elif item["format"] == "pdf":
            if not pages:
                raise ValueError("Indica las páginas del PDF, por ejemplo 1-3")
            result = prepare_pdf(self.root, source, pages)
        else:
            raise ValueError(f"El adaptador {item['format']} todavía no está implementado")
        self._finish(source_text, current_hash, result, source_changed)
        return {**result, "source_changed": source_changed}

    @serialized
    def _finish(self, source_text, current_hash, result, source_changed):
        item = next((item for item in self.items if item["path"] == source_text), None)
        if item is None:
            return  # Una retirada concurrente no vuelve a añadir el archivo.
        item.update(status=result["status"], action="Abrir resultado", sha256=current_hash,
                    output=result["output"], reused=result.get("reused", False),
                    source_changed=source_changed)
        self.save()

    def summary(self) -> dict:
        return {"total": len(self.items),
                "ready": sum(item["status"] in {"listo", "requiere_paginas"} for item in self.items),
                "completed": sum(bool(item.get("output")) for item in self.items),
                "pending_adapters": sum(item["status"] in {"pendiente", "en_nube"} for item in self.items)}
