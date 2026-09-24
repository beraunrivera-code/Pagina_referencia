"""Selector visual y procesamiento inicial de una ruta local."""

from __future__ import annotations

import os
from pathlib import Path

from .documents import json_bytes, parse_pages
from .pipeline import NATIVE_KINDS, convert_native, convert_text, detect, inventory, prepare_pdf


def save_selection(root: Path, source: Path, pages: list[int] | None = None) -> dict:
    """Valida y guarda la última selección sin copiar ni modificar el origen."""
    source = source.resolve(strict=True)
    root.mkdir(parents=True, exist_ok=True)
    kind = "folder" if source.is_dir() else detect(source)
    selection = {"path": str(source), "kind": kind}
    if pages:
        selection["pages"] = pages
    destination = root / "seleccion.json"
    temporary = root / ".seleccion.json.tmp"
    temporary.write_bytes(json_bytes(selection))
    os.replace(temporary, destination)
    return selection


def process_selection(root: Path, selection: dict, limit: int = 200) -> dict:
    """Hace solo la acción que el núcleo actual puede respaldar con evidencia."""
    source = Path(selection["path"]).resolve(strict=True)
    kind = selection["kind"]
    if kind == "folder":
        snapshot = inventory(source, limit, root)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / "ultimo_inventario.json"
        temporary = root / ".ultimo_inventario.json.tmp"
        temporary.write_bytes(json_bytes(snapshot))
        os.replace(temporary, destination)
        return {
            "status": "inventariado" if not snapshot["errors"] else "parcial",
            "ruta": str(source),
            "archivos_revisados": snapshot["count"],
            "limite": limit,
            "se_corto_por_limite": snapshot["truncated"],
            "errores": snapshot["errors"],
            "inventario": str(destination),
            "siguiente": "La conversión por lotes se habilitará al incorporar los adaptadores por formato.",
        }
    if kind in {"md", "txt", "text"}:
        result = convert_text(root, source)
        result["seleccion"] = str(source)
        return result
    if kind in NATIVE_KINDS:
        result = convert_native(root, source)
        result["seleccion"] = str(source)
        return result
    if kind == "pdf":
        pages = selection.get("pages") or []
        if not pages:
            return {
                "status": "seleccionado",
                "ruta": str(source),
                "formato": "pdf",
                "siguiente": "Indica páginas con: convertir.bat usar-ruta RUTA --paginas 1-3",
            }
        result = prepare_pdf(root, source, pages)
        result["seleccion"] = str(source)
        result["aviso"] = "PDF preparado localmente; OCR y revisión semántica siguen pendientes."
        return result
    return {
        "status": "seleccionado",
        "ruta": str(source),
        "formato": kind,
        "siguiente": f"La ruta quedó guardada. El adaptador para {kind} todavía está pendiente.",
    }


def use_path(root: Path, source: Path, pages_text: str | None = None, limit: int = 200) -> dict:
    pages = parse_pages(pages_text) if pages_text else None
    return process_selection(root, save_selection(root, source, pages), limit)


def choose_and_process(root: Path, limit: int = 200) -> dict:
    """Abre un selector nativo de Windows y procesa la elección soportada."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog

    window = tk.Tk()
    window.withdraw()
    window.attributes("-topmost", True)
    try:
        folder_choice = messagebox.askyesnocancel(
            "Seleccionar origen",
            "¿Qué deseas seleccionar?\n\nSí = CARPETA completa\nNo = un ARCHIVO\nCancelar = volver al menú",
            parent=window,
        )
        if folder_choice is None:
            return {"status": "cancelado", "mensaje": "No se cambió la selección."}
        if folder_choice:
            chosen = filedialog.askdirectory(title="Selecciona la carpeta de entrada", mustexist=True, parent=window)
        else:
            chosen = filedialog.askopenfilename(
                title="Selecciona el archivo de entrada",
                filetypes=[
                    ("Documentos", "*.pdf *.docx *.xlsx *.xlsm *.pptx *.vsdx *.txt *.md *.html *.dwg *.dxf"),
                    ("Todos los archivos", "*.*"),
                ],
                parent=window,
            )
        if not chosen:
            return {"status": "cancelado", "mensaje": "No se cambió la selección."}
        source = Path(chosen)
        pages = None
        if source.is_file() and detect(source) == "pdf":
            pages_text = simpledialog.askstring(
                "Páginas del PDF",
                "Escribe las páginas que quieres preparar (máximo 20).\nEjemplos: 1-3  o  1,4,7",
                initialvalue="1",
                parent=window,
            )
            if pages_text:
                pages = parse_pages(pages_text)
        return process_selection(root, save_selection(root, source, pages), limit)
    finally:
        window.destroy()
