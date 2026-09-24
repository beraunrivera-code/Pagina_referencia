"""Aplicación de escritorio del sistema de conversión y memoria documental."""

from __future__ import annotations

import json
import os
import queue as queue_module
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .documents import parse_pages
from .diagnostics import export_failure_report, list_failures, record_failure, repair_safe, run_diagnostics
from .pipeline import NATIVE_KINDS
from .storage import (delete_documents, export_index, export_markdown, pending_batches, report)
from .workflow import LOCAL_TEXT, WorkQueue
from .retrieval import consult
from .providers import PROVIDERS, profiles_for, execute_job, provider_status
from .connection_settings import default_connection, load_settings, save_settings, validate_connection
from .credentials import ENV_NAMES, save_key
from .jobs import prepare_job
from .ui_widgets import CollapsibleSection, NavigationHistory, scroll_table, wheel_on_canvas, text_view
from .access import launch_login, OFFICIAL_HELP
from .batches import plan_queue, run_batch, batch_status
from .ai_batches import plan_ai_batch, run_ai_batch, ai_batch_status
from .visor import write_view, open_in_browser, render_markdown


APP_ROOT = Path(__file__).resolve().parents[1]
from .runtime_paths import data_root, export_root
DATA_ROOT = data_root(APP_ROOT)
PAGE_LABELS = ("Trabajo y cola", "Biblioteca", "Diagnóstico", "IA y conexiones")
LOCAL_MODE = "Local (sin envíos)"
AI_MODE = "Con IA (envío explícito)"
LOCAL_ENGINE_CHOICES = {
    "Nativo actual": "native",
    "Docling · estructura PDF/imágenes": "docling",
    "MarkItDown · documentos ligeros": "markitdown",
}
PAGE_HELP = (
    "Agrega fuentes y conviértelas localmente. Los originales se conservan.",
    "Busca, lee y exporta resultados. Íntegro no significa fidelidad certificada.",
    "Revisa entorno y fallos. La salud técnica no mide la calidad documental.",
    "Opcional: prepara → revisa → envía. Navegar o preparar no consume IA.",
)


def md_export_dir() -> Path:
    """Carpeta ÚNICA donde caen los .md con su nombre real.

    Se lee de ``datos/exportacion.json`` ({"carpeta_md": "..."}) para poder cambiarla sin tocar
    código; si no existe se crea apuntando a ``MD`` junto al programa.
    """
    return export_root(APP_ROOT, DATA_ROOT)


def persist_md_export(package) -> dict:
    """Exportación y diagnóstico de disco, sin acceder a Tk ni mostrar diálogos.

    Fallar la copia legible no invalida el paquete ya publicado. El recibo permite
    mostrar el resultado sin repetir la exportación desde el callback gráfico.
    """
    receipt = {"package": str(Path(str(package))), "path": None}
    try:
        exported = export_markdown(DATA_ROOT, Path(str(package)).name, md_export_dir())
        receipt["path"] = str(exported["path"])
        for key in ("portable_links", "link_warning"):
            if key in exported:
                receipt[key] = exported[key]
    except Exception as exc:
        receipt["error"] = type(exc).__name__
        try:
            failure = record_failure(DATA_ROOT, "exportar_md", exc, {"package": str(package)})
            receipt["signature"] = failure["signature"]
        except Exception:
            # Si también falla el registro, no fingir que existe ni descartar el paquete.
            receipt["signature"] = "sin-registro"
    return receipt


def persist_operation_exports(operation: str, result):
    """Completa las copias MD antes de notificar éxito/cierre al hilo gráfico."""
    if operation not in {"procesar_documento", "lote_local", "lote_ia"} or not isinstance(result, dict):
        return result
    packages = []
    if operation == "procesar_documento" and result.get("output"):
        packages.append(result["output"])
    elif operation == "lote_local":
        packages.extend(item["result"]["output"] for item in result.get("items", [])
                        if item.get("state") == "terminado" and (item.get("result") or {}).get("output"))
    elif operation == "lote_ia" and (result.get("combined") or {}).get("output"):
        packages.append(result["combined"]["output"])
    enriched = dict(result)
    enriched["_ui_md_exports"] = [persist_md_export(package)
                                   for package in dict.fromkeys(str(Path(str(p))) for p in packages)]
    return enriched


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class ConversionApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Sistema MD · GPT CODE · Conexiones")
        self.geometry("1180x760")
        self.minsize(980, 650)
        self.configure(bg="#eef2f7")
        self.events: queue_module.Queue = queue_module.Queue()
        self.busy = False
        self.batch_stop = threading.Event()
        self.ai_stop = threading.Event()
        self.result_paths: dict[str, str] = {}
        self._result_rows: list[dict] = []
        self.work_queue = WorkQueue(DATA_ROOT)
        self.navigation = NavigationHistory(0)
        self.navigating = False
        self.connection_settings, self.connection_notice = load_settings(DATA_ROOT)
        self._active_provider = self.connection_settings["selected_provider"]
        self._last_mode = LOCAL_MODE
        self._configure_styles()
        self._build_ui()
        self._build_navigation()
        self.closing = False
        self._ai_progress_id = None
        self.refresh_all()
        self.after(100, self._drain_events)
        self.after(1500, self._tick_ai_progress)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def close_app(self):
        """Cerrar nunca se niega: si hay trabajo, se PAUSA y se cierra al terminar la pieza actual.

        Antes respondía «espera a que termine» sin decir cuánto faltaba: con un lote de IA de
        decenas de páginas la ventana parecía colgada y no dejaba salir. Los lotes guardan cada
        página antes de seguir y se reanudan, así que pausar no pierde nada.
        """
        if not self.busy:
            self.destroy()
            return
        if getattr(self, "closing", False):
            return
        if messagebox.askyesno(
                "Hay un trabajo en curso",
                "¿Pausar y cerrar?\n\nSe termina la página o el archivo actual (ya guardado lo anterior) "
                "y la ventana se cierra sola. El lote se reanuda después desde donde quedó.\n\n"
                "«No» = seguir trabajando."):
            self.closing = True
            self.batch_stop.set()
            self.ai_stop.set()
            self.status_var.set("Cerrando: termina la pieza actual y sale…")

    def report_callback_exception(self, exc_type, exc, traceback):
        failure = self._record("interfaz", exc)
        messagebox.showerror("Error registrado", f"{failure['message']}\n{failure['remedy']}")

    @staticmethod
    def _record(operation, exc):
        try:
            return record_failure(DATA_ROOT, operation, exc)
        except Exception as logging_error:
            return {"signature": "sin-registro", "message": str(exc),
                    "remedy": f"No se pudo guardar el diagnóstico: {logging_error}"}

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#eef2f7")
        style.configure("App.TLabel", background="#eef2f7", foreground="#34425a")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#17233c", foreground="#ffffff",
                        font=("Segoe UI Semibold", 19))
        style.configure("Subtitle.TLabel", background="#17233c", foreground="#b8c5da",
                        font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#ffffff", foreground="#61708a",
                        font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background="#ffffff", foreground="#17233c",
                        font=("Segoe UI Semibold", 18))
        style.configure("Section.TLabel", background="#ffffff", foreground="#17233c",
                        font=("Segoe UI Semibold", 12))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10),
                        foreground="#ffffff", background="#2f6fed", padding=(14, 9))
        style.map("Primary.TButton", background=[("active", "#255dcc")])
        style.configure("TButton", font=("Segoe UI", 10), padding=(11, 8))
        style.configure("Section.TButton", anchor="w", font=("Segoe UI Semibold", 11),
                        padding=(10, 10), background="#e8eef8", foreground="#17233c")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#485a73",
                        font=("Segoe UI", 10))
        style.configure("TCheckbutton", background="#ffffff")
        style.configure("TRadiobutton", background="#ffffff")
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=30,
                        background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), padding=7)
        style.configure("TNotebook", background="#eef2f7", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI Semibold", 10), padding=(18, 10))

    def _build_ui(self) -> None:
        # La altura natural sigue a la fuente/DPI. Un alto fijo recortaba el subtítulo.
        header = tk.Frame(self, bg="#17233c")
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Sistema MD · GPT CODE", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", padx=28, pady=(8, 0))
        subtitle = ttk.Label(header, text="Tu biblioteca documental · Trabajo local primero · Envíos de IA bajo tu control",
                             style="Subtitle.TLabel", justify="left")
        subtitle.grid(row=1, column=0, columnspan=2, sticky="ew", padx=29, pady=(1, 4))
        header.bind("<Configure>", lambda event: subtitle.configure(wraplength=max(240, event.width - 58)))
        self.mode_var = tk.StringVar(value=LOCAL_MODE)
        self.mode_selector = ttk.Combobox(header, textvariable=self.mode_var,
                                         values=(LOCAL_MODE, AI_MODE), state="readonly", width=27)
        self.mode_selector.grid(row=0, column=1, sticky="e", padx=(0, 28), pady=(8, 0))
        self.mode_selector.bind("<<ComboboxSelected>>", self._mode_changed)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.mode_selector.bind(sequence, lambda _event: "break")
        self.mode_hint_var = tk.StringVar(value="Sin envíos a proveedores. Cambiar a IA no envía documentos.")

        # Reservar el pie ANTES del notebook expandible: pack reparte en orden.
        # Su altura también es natural para que el estado siga siendo legible con DPI alto.
        footer = tk.Frame(self, bg="#dfe6ef")
        footer.pack(fill="x", side="bottom")
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Listo · ninguna llamada externa")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=120)
        self.progress.grid(row=0, column=1, padx=16, pady=7, sticky="e")
        status = tk.Label(footer, textvariable=self.status_var, bg="#dfe6ef", fg="#34425a",
                          font=("Segoe UI", 9), justify="left", anchor="w")
        status.grid(row=0, column=0, sticky="ew", padx=20, pady=7)
        footer.bind("<Configure>", lambda event: status.configure(
            wraplength=max(240, event.width - self.progress.winfo_reqwidth() - 72)))

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(6, 6))
        self.home = ttk.Frame(self.tabs, style="App.TFrame")
        self.results = ttk.Frame(self.tabs, style="App.TFrame")
        self.diagnostics = ttk.Frame(self.tabs, style="App.TFrame")
        self.settings = ttk.Frame(self.tabs, style="App.TFrame")
        for page, label in zip((self.home, self.results, self.diagnostics, self.settings), PAGE_LABELS):
            self.tabs.add(page, text=label)
        self._build_home()
        self._build_results()
        self._build_diagnostics()
        self._build_settings()

    def _panel(self, parent: tk.Widget, padding: int = 16) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=padding)
        return frame

    def _build_navigation(self):
        menu = tk.Menu(self)
        files = tk.Menu(menu, tearoff=False)
        files.add_command(label="Agregar archivos…", command=self.choose_files)
        files.add_command(label="Agregar carpeta…", command=self.choose_folder)
        files.add_command(label="Abrir memoria", command=lambda: self._open_path(DATA_ROOT))
        files.add_separator()
        # Destructiva y de alcance total: separada del resto y con puntos suspensivos (confirma).
        files.add_command(label="Vaciar resultados…", command=self.clear_all_results)
        files.add_separator()
        files.add_command(label="Salir", command=self.close_app)
        menu.add_cascade(label="Archivo", menu=files)
        view = tk.Menu(menu, tearoff=False)
        for i, label in enumerate(PAGE_LABELS):
            view.add_command(label=label, command=lambda page=i: self.tabs.select(page))
        menu.add_cascade(label="Ver", menu=view)
        menu.add_command(label="Ayuda", command=self.show_help)
        self.configure(menu=menu)
        bar = ttk.Frame(self)
        bar.pack(fill="x", before=self.tabs, padx=18, pady=(6, 0))
        self.back_button = ttk.Button(bar, text="← Atrás", command=lambda: self.go_history(-1))
        self.forward_button = ttk.Button(bar, text="Adelante →", command=lambda: self.go_history(1))
        self.back_button.pack(side="left")
        self.forward_button.pack(side="left", padx=6)
        ttk.Button(bar, text="Inicio", command=lambda: self.tabs.select(0)).pack(side="left")
        ttk.Button(bar, text="Ayuda", command=self.show_help).pack(side="right")
        self.location_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.location_var, style="App.TLabel").pack(side="left", padx=16)
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.bind("<Alt-Left>", lambda event: self.go_history(-1))
        self.bind("<Alt-Right>", lambda event: self.go_history(1))
        self.bind("<F1>", lambda event: self.show_help())
        self.bind("<Control-f>", self.focus_search)
        self._tab_changed()

    def _tab_changed(self, event=None):
        page = self.tabs.index(self.tabs.select())
        self.location_var.set(PAGE_LABELS[page])
        if not self.navigating:
            self.navigation.visit(page)
        self.navigating = False
        self.back_button.configure(state="normal" if self.navigation.position else "disabled")
        self.forward_button.configure(state="normal" if self.navigation.position < len(self.navigation.items) - 1 else "disabled")

    def focus_search(self, _event=None):
        self.tabs.select(1)
        self.query_entry.focus_set()
        self.query_entry.selection_range(0, "end")
        return "break"

    def go_history(self, delta):
        page = self.navigation.move(delta)
        if self.tabs.index(self.tabs.select()) != page:
            self.navigating = True
            self.tabs.select(page)
        else:
            self._tab_changed()
        return "break"

    def show_help(self):
        popup = tk.Toplevel(self)
        popup.title("Ayuda · Sistema MD")
        popup.geometry("820x570")
        ttk.Button(popup, text="Guía de uso", command=lambda: self._open_path(APP_ROOT.parent / "INICIO_RAPIDO.md")).pack(side="bottom", pady=8)
        text_view(popup, "1. Trabajo y cola: agrega archivos o una carpeta, selecciona un documento y procesa localmente. Para varios archivos, planifica el lote, revisa y ejecútalo.\n\n"
                  "2. Biblioteca: busca fragmentos (Ctrl+F), filtra títulos o pendientes de revisión y abre la lectura con doble clic o Intro. Las tablas tienen barras vertical/horizontal y rueda.\n\n"
                  "3. IA avanzada es opcional. Despliega Conexión para guardar una clave API o abrir el acceso oficial del CLI. Nunca compartas claves en el chat.\n\n"
                  "4. Elige un resultado con imagen → crea página o lote → revisa vista previa → pulsa Enviar. Preparar no envía documentos. Cancelar la confirmación evita el envío; cerrar después de enviarlo no anula el consumo.\n\n"
                  "5. Diagnóstico: doble clic en un control o fallo para leerlo completo. Historial IA muestra estados y consumo comunicado, no saldo.\n\n"
                  "6. TypeSafe está en Biblioteca: escribe una consulta → TypeSafe · revisar… → lee los fragmentos → confirma solo si quieres enviarlos. Ordena relevancia; no extrae imágenes ni certifica fidelidad.\n\n"
                  "Atrás/Adelante (Alt+flechas) cambia de pantalla; NO deshace conversiones ni repite envíos.\n\n"
                  "Si modificaste el programa mientras estaba abierto, ciérralo cuando no esté trabajando y vuelve a abrir convertir.bat.")

    def _build_home(self) -> None:
        cards = ttk.Frame(self.home, style="App.TFrame")
        cards.pack(fill="x", pady=(0, 12))
        self.card_vars = {name: tk.StringVar(value="0") for name in
                          ("En cola", "Listos", "Convertidos", "Adaptadores pendientes")}
        for column, (name, variable) in enumerate(self.card_vars.items()):
            card = self._panel(cards, 14)
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
            cards.columnconfigure(column, weight=1)
            ttk.Label(card, text=name.upper(), style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=variable, style="CardValue.TLabel").pack(anchor="w", pady=(2, 0))

        source_panel = self._panel(self.home)
        source_panel.pack(fill="x", pady=(0, 12))
        ttk.Label(source_panel, text="1. Selecciona las fuentes", style="Section.TLabel").pack(anchor="w")
        ttk.Label(source_panel, text="Los originales se leen; nunca se sobrescriben. Las rutas quedan en una cola reutilizable.",
                  background="#ffffff", foreground="#61708a").pack(anchor="w", pady=(2, 10))
        actions = ttk.Frame(source_panel, style="Panel.TFrame")
        actions.pack(fill="x")
        ttk.Button(actions, text="＋ Agregar archivos", style="Primary.TButton",
                   command=self.choose_files).pack(side="left")
        ttk.Button(actions, text="＋ Agregar carpeta", command=self.choose_folder).pack(side="left", padx=8)
        ttk.Button(actions, text="Quitar selección", command=self.remove_selected).pack(side="left")
        ttk.Button(actions, text="Vaciar cola", command=self.clear_queue).pack(side="right")

        queue_panel = self._panel(self.home, 14)
        queue_panel.pack(fill="both", expand=True)
        heading = ttk.Frame(queue_panel, style="Panel.TFrame")
        heading.pack(fill="x", pady=(0, 8))
        self.local_engine_var = tk.StringVar(value="Nativo actual")
        self.local_engine_selector = ttk.Combobox(heading, textvariable=self.local_engine_var,
                                                 values=tuple(LOCAL_ENGINE_CHOICES), state="readonly", width=34)
        self.local_engine_selector.pack(side="left", fill="x", expand=True, padx=(0, 8))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.local_engine_selector.bind(sequence, lambda _event: "break")
        self.local_engine_selector.bind("<<ComboboxSelected>>", lambda _event: self._queue_selection_changed())
        self.local_engine_config_button = ttk.Button(heading, text="Configurar…", command=self.configure_local_engine)
        self.local_engine_config_button.pack(side="left", padx=(0, 8))
        self.process_button = ttk.Button(heading, text="Convertir con motor local", style="Primary.TButton",
                                         command=self.process_selected)
        self.process_button.pack(side="right")
        batch_actions = ttk.Frame(queue_panel, style="Panel.TFrame")
        batch_actions.pack(fill="x", pady=(0, 6))
        ttk.Button(batch_actions, text="Planificar lote nativo", command=self.plan_local).pack(side="left")
        ttk.Button(batch_actions, text="Ejecutar / reanudar nativo", command=self.run_local).pack(side="left", padx=6)
        ttk.Button(batch_actions, text="Pausar tras archivo", command=self.pause_local).pack(side="left")
        ttk.Button(batch_actions, text="Estado del lote", command=self.show_local).pack(side="left", padx=6)
        self.queue_hint_var = tk.StringVar()
        ttk.Label(queue_panel, textvariable=self.queue_hint_var, style="Hint.TLabel",
                  wraplength=840).pack(fill="x", pady=(0, 6))
        columns = ("name", "format", "size", "status", "action")
        queue_table = ttk.Frame(queue_panel)
        queue_table.pack(fill="both", expand=True)
        self.queue_tree = ttk.Treeview(queue_table, columns=columns, show="headings", selectmode="extended")
        labels = {"name": "Documento", "format": "Formato", "size": "Tamaño",
                  "status": "Estado", "action": "Siguiente acción"}
        widths = {"name": 310, "format": 90, "size": 90, "status": 130, "action": 240}
        for column in columns:
            self.queue_tree.heading(column, text=labels[column])
            self.queue_tree.column(column, width=widths[column], anchor="w", stretch=False)
        scroll_table(self.queue_tree)
        self.queue_tree.bind("<Double-1>", lambda _event: self.process_selected())
        self.queue_tree.bind("<Return>", lambda _event: self.process_selected())
        self.queue_tree.bind("<<TreeviewSelect>>", lambda _event: self._queue_selection_changed())

    def _queue_selection_changed(self):
        selected = self.queue_tree.selection()
        self.process_button.configure(state="normal" if len(selected) == 1 and not self.busy else "disabled")
        self.local_engine_selector.configure(state="disabled" if self.busy else "readonly")
        self.local_engine_config_button.configure(state="disabled" if self.busy else "normal")
        if not self.queue_tree.get_children():
            self.queue_hint_var.set("Tu cola está vacía. Empieza con Agregar archivos o Agregar carpeta.")
        elif len(selected) == 1:
            item = next((item for item in self.work_queue.items if item["path"] == selected[0]), None)
            if item:
                engine = LOCAL_ENGINE_CHOICES.get(self.local_engine_var.get(), "native")
                self.queue_hint_var.set(f"Siguiente acción nativa: {item['action']}" if engine == "native" else
                    f"Motor elegido: {engine}. Conversión local explícita; sin fallback ni envíos API. "
                    "Requiere entorno instalado. Solo este archivo; los botones de lote siguen siendo nativos.")
        elif selected:
            self.queue_hint_var.set(f"{len(selected)} seleccionados. Procesa uno o usa un lote para varios archivos.")
        else:
            self.queue_hint_var.set("Selecciona un documento para procesar. Los lotes se planifican antes de ejecutarse.")

    def _build_results(self) -> None:
        panel = self._panel(self.results)
        panel.pack(fill="both", expand=True)
        bar = ttk.Frame(panel, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 12))
        ttk.Label(bar, text="Biblioteca documental", style="Section.TLabel").pack(side="left")
        ttk.Button(bar, text="Actualizar", command=self.refresh_results).pack(side="right")
        ttk.Button(bar, text="Abrir índice", style="Primary.TButton", command=self.open_index).pack(side="right", padx=8)
        search_bar = ttk.Frame(panel, style="Panel.TFrame")
        search_bar.pack(fill="x", pady=(0, 12))
        self.query_var = tk.StringVar()
        ttk.Label(search_bar, text="Buscar contenido", style="Hint.TLabel").pack(side="left", padx=(0, 8))
        query_entry = self.query_entry = ttk.Entry(search_bar, textvariable=self.query_var)
        query_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        query_entry.bind("<Return>", lambda _event: self.search_memory())
        ttk.Button(search_bar, text="Consultar memoria", command=self.search_memory).pack(side="right")
        ttk.Button(search_bar, text="TypeSafe · revisar…", command=self.preview_typesafe).pack(side="right", padx=(0, 6))
        filters = ttk.Frame(panel, style="Panel.TFrame")
        filters.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="Filtrar título", style="Hint.TLabel").pack(side="left", padx=(0, 8))
        self.title_filter_var = tk.StringVar()
        ttk.Entry(filters, textvariable=self.title_filter_var).pack(side="left", fill="x", expand=True)
        self.result_filter_var = tk.StringVar(value="Todos")
        state_filter = ttk.Combobox(filters, textvariable=self.result_filter_var,
                                   values=("Todos", "Por revisar", "Dañados"), state="readonly", width=16)
        state_filter.pack(side="left", padx=(8, 0))
        state_filter.bind("<<ComboboxSelected>>", lambda _event: self._apply_result_filter())
        self.title_filter_var.trace_add("write", self._schedule_result_filter)
        self._filter_timer = None
        self.library_hint_var = tk.StringVar()
        ttk.Label(panel, textvariable=self.library_hint_var, style="Hint.TLabel",
                  wraplength=850).pack(fill="x", pady=(0, 8))
        columns = ("title", "status", "integrity", "updated")
        # La barra de acciones se empaqueta ANTES que la tabla y con side="bottom": pack talla
        # la cavidad en el orden de las llamadas, así que el widget expandible debe ir el
        # ÚLTIMO. Al revés, la tabla se quedaba con todo el alto y los botones caían a 2 px
        # (medido a 1200x700), grises y sin texto legible.
        # Windows UX Guidelines: toda orden de un elemento está en su menú contextual, pero
        # NINGUNA vive solo ahí. Por eso cada una tiene también botón y tecla.
        acciones = ttk.Frame(panel, style="Panel.TFrame")
        acciones.pack(side="bottom", fill="x", pady=(10, 0))
        self.result_action_buttons = []
        for label, command, primary in (
                ("Abrir lectura", self.open_selected_result, True),
                ("Ver estructura", self.show_structure, False),
                ("Mostrar carpeta", self.reveal_selected_result, False),
                ("Abrir con…", self.open_with_selected_result, False)):
            button = ttk.Button(acciones, text=label, command=command,
                                style="Primary.TButton" if primary else "TButton")
            button.pack(side="left", padx=(0, 6))
            self.result_action_buttons.append(button)
        self.delete_result_button = ttk.Button(acciones, text="Eliminar…", command=self.delete_selected_results)
        self.delete_result_button.pack(side="right")
        # Una lista muestra DOCUMENTOS, no las piezas internas con que se procesaron: un lote
        # de IA publica cada página como paquete propio (structured_response) y 53 de 64 filas
        # eran eso. Se ocultan sin borrar nada; el contador dice cuántas hay y el interruptor
        # las enseña (un dato que no se ve, no existe).
        filtro = ttk.Frame(panel, style="Panel.TFrame")
        filtro.pack(side="bottom", fill="x", pady=(8, 0))
        self.show_pages_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filtro, text="Mostrar también las páginas sueltas de IA",
                        variable=self.show_pages_var, command=self._apply_result_filter).pack(side="left")
        self.hidden_pages_var = tk.StringVar(value="")
        ttk.Label(filtro, textvariable=self.hidden_pages_var, background="#ffffff",
                  foreground="#61708a").pack(side="right")
        result_table = ttk.Frame(panel)
        result_table.pack(fill="both", expand=True)
        self.result_tree = ttk.Treeview(result_table, columns=columns, show="headings", selectmode="extended")
        for column, label, width in [("title", "Documento", 480), ("status", "Estado", 120),
                                     ("integrity", "Integridad", 120), ("updated", "Actualizado", 210)]:
            self.result_tree.heading(column, text=label)
            self.result_tree.column(column, width=width, anchor="w", stretch=False)
        scroll_table(self.result_tree)
        self.result_tree.tag_configure("damaged", foreground="#9b1c1c")
        self.result_tree.tag_configure("review", foreground="#795400")
        self.result_tree.bind("<<TreeviewSelect>>", lambda _event: self._result_selection_changed())
        self.result_tree.bind("<Double-1>", lambda _event: self.open_selected_result())
        # Reflejos del Explorador: Intro abre, Supr elimina, F5 actualiza, Ctrl+A todo,
        # clic derecho / tecla de menú / Shift+F10 = menú contextual.
        self.result_tree.bind("<Return>", lambda _event: self.open_selected_result())
        self.result_tree.bind("<Delete>", lambda _event: self.delete_selected_results())
        self.result_tree.bind("<F5>", lambda _event: self.refresh_results())
        self.result_tree.bind("<Control-a>", self._select_all_results)
        self.result_tree.bind("<Control-A>", self._select_all_results)
        self.result_tree.bind("<Button-3>", self._results_context_menu)
        self.result_tree.bind("<App>", self._results_context_menu)
        self.result_tree.bind("<Shift-F10>", self._results_context_menu)
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Abrir", font=("Segoe UI", 9, "bold"), command=self.open_selected_result)
        menu.add_command(label="Abrir con…", command=self.open_with_selected_result)
        menu.add_command(label="Mostrar en carpeta", command=self.reveal_selected_result)
        menu.add_command(label="Ver estructura", command=self.show_structure)
        menu.add_separator()
        menu.add_command(label="Copiar ruta del MD", command=self.copy_selected_result_path)
        menu.add_separator()
        menu.add_command(label="Eliminar", command=self.delete_selected_results)
        self.results_menu = menu

    def _schedule_result_filter(self, *_args):
        if self._filter_timer:
            self.after_cancel(self._filter_timer)
        self._filter_timer = self.after(180, self._apply_result_filter)

    def _apply_result_filter(self):
        if self._filter_timer:
            self.after_cancel(self._filter_timer)
        self._filter_timer = None
        self.refresh_results(reload=False)

    def _result_selection_changed(self):
        count = len(self.result_tree.selection())
        for button in self.result_action_buttons:
            button.configure(state="normal" if count == 1 else "disabled")
        self.delete_result_button.configure(state="normal" if count and not self.busy else "disabled")
        if hasattr(self, "ai_selection_var"):
            selected = self.result_tree.selection()
            title = self.result_tree.set(selected[0], "title") if count == 1 else ""
            self.ai_selection_var.set(f"Documento seleccionado: {title}" if title else
                                      "Selecciona un solo documento en Biblioteca para preparar su lectura visual.")

    def _build_diagnostics(self) -> None:
        top = self._panel(self.diagnostics)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="Diagnóstico y mejora continua", style="Section.TLabel").pack(anchor="w")
        ttk.Label(top, text="Cada error se agrupa por firma, cuenta repeticiones y conserva la siguiente prueba.",
                  background="#ffffff", foreground="#61708a").pack(anchor="w", pady=(2, 10))
        buttons = ttk.Frame(top, style="Panel.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Ejecutar diagnóstico", style="Primary.TButton",
                   command=self.run_health).pack(side="left")
        ttk.Button(buttons, text="Reparación segura", command=self.safe_repair).pack(side="left", padx=8)
        ttk.Button(buttons, text="Exportar FALLOS.md", command=self.export_failures).pack(side="left")

        split = ttk.Panedwindow(self.diagnostics, orient="horizontal")
        split.pack(fill="both", expand=True)
        health_panel = self._panel(split, 12)
        failure_panel = self._panel(split, 12)
        split.add(health_panel, weight=1)
        split.add(failure_panel, weight=1)
        ttk.Label(health_panel, text="Salud del sistema", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        health_table = ttk.Frame(health_panel)
        health_table.pack(fill="both", expand=True)
        self.health_tree = ttk.Treeview(health_table, columns=("check", "state", "detail"), show="headings")
        for column, label, width in [("check", "Control", 160), ("state", "Estado", 75), ("detail", "Detalle", 310)]:
            self.health_tree.heading(column, text=label)
            self.health_tree.column(column, width=width, anchor="w", stretch=False)
        scroll_table(self.health_tree)
        ttk.Label(failure_panel, text="Fallos acumulados", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        failure_table = ttk.Frame(failure_panel)
        failure_table.pack(fill="both", expand=True)
        self.failure_tree = ttk.Treeview(failure_table, columns=("id", "operation", "count", "state", "message"), show="headings")
        for column, label, width in [("id", "Firma", 110), ("operation", "Operación", 120),
                                     ("count", "Veces", 55), ("state", "Estado", 90), ("message", "Síntoma", 270)]:
            self.failure_tree.heading(column, text=label)
            self.failure_tree.column(column, width=width, anchor="w", stretch=False)
        scroll_table(self.failure_tree)
        self.health_tree.bind("<Double-1>", lambda event: self.show_row(self.health_tree))
        self.failure_tree.bind("<Double-1>", lambda event: self.show_failure())

    def _build_settings(self) -> None:
        canvas = tk.Canvas(self.settings, background="#ffffff", highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.settings, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        panel = self._panel(canvas, 22)
        window = canvas.create_window((0, 0), window=panel, anchor="nw")
        panel.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.settings_canvas = canvas
        wheel = wheel_on_canvas(self, canvas)
        ttk.Label(panel, text="Conexiones guardadas · envío siempre explícito", style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Programa abierto: " + str(APP_ROOT), style="Hint.TLabel",
                  wraplength=820, justify="left").pack(anchor="w", pady=(5, 0))
        ttk.Label(panel, textvariable=self.mode_hint_var, style="Hint.TLabel",
                  wraplength=820).pack(anchor="w", pady=(5, 0))
        ttk.Label(panel, text=PAGE_HELP[3], style="Hint.TLabel", wraplength=820).pack(anchor="w", pady=(5, 8))
        tk.Label(panel, text="Enviar consume cuota. Una clave guardada no demuestra acceso ni saldo.",
                 bg="#fff3d6", fg="#775000", font=("Segoe UI", 10), padx=12, pady=8,
                 wraplength=790, justify="left").pack(fill="x", pady=(0, 12))
        self.ai_selection_var = tk.StringVar(value="Selecciona un documento en Biblioteca para preparar su lectura visual.")
        ttk.Label(panel, textvariable=self.ai_selection_var, style="Hint.TLabel",
                  wraplength=820).pack(anchor="w")
        ttk.Button(panel, text="Elegir en Biblioteca", command=lambda: self.tabs.select(1)).pack(anchor="w", pady=(6, 12))
        self.settings_sections = {}
        for key, title, expanded in (
                ("connection", "1. Conexión y modelo", True),
                ("page", "2. Una página: preparar y revisar", False),
                ("batch", "3. Varias páginas: lote y límites", False),
                ("memory", "Memoria y reglas de consumo", False)):
            section = CollapsibleSection(panel, title, expanded=expanded)
            section.pack(fill="x", pady=(0, 10))
            self.settings_sections[key] = section
        connection = self.settings_sections["connection"].body
        page_panel = self.settings_sections["page"].body
        batch_panel = self.settings_sections["batch"].body
        memory = self.settings_sections["memory"].body
        current = self.connection_settings["connections"].get(self._active_provider, default_connection())
        self.provider_var = tk.StringVar(value=self._active_provider)
        self.key_slot_var = tk.StringVar(value=str(current["key_slot"]))
        self.cli_profile_var = tk.StringVar(value=current["cli_profile"])
        self.model_var = tk.StringVar(value=current["model"])
        self.job_var = tk.StringVar()
        self.tokens_var = tk.StringVar(value="8192")
        self.ai_batch_var = tk.StringVar()
        self.ai_calls_var = tk.StringVar(value="20")
        self.ai_token_budget_var = tk.StringVar(value="200000")
        def fields(parent, definitions):
            widgets = {}
            controls = ttk.Frame(parent, style="Panel.TFrame")
            controls.pack(fill="x", pady=(0, 8))
            for i, (label, variable, options) in enumerate(definitions):
                ttk.Label(controls, text=label, background="#ffffff", wraplength=310).grid(
                    row=i, column=0, sticky="w", pady=5)
                if options:
                    widget = ttk.Combobox(controls, textvariable=variable, values=options, state="readonly")
                    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                        widget.bind(sequence, lambda event: wheel(event) or "break")
                else:
                    widget = ttk.Entry(controls, textvariable=variable)
                widget.grid(row=i, column=1, sticky="ew", padx=(12, 0))
                widgets[label] = widget
            controls.columnconfigure(1, weight=1)
            return widgets

        self.connection_controls = fields(connection, [
            ("Proveedor", self.provider_var, PROVIDERS),
            ("Perfil de clave API (1 a 5)", self.key_slot_var, ("1", "2", "3", "4", "5")),
            ("Perfil de cuenta CLI", self.cli_profile_var, profiles_for(self._active_provider)),
            ("Modelo exacto (obligatorio)", self.model_var, None),
            ("Tokens máximos de salida (solo API)", self.tokens_var, None),
        ])
        fields(page_panel, [("Carpeta del encargo", self.job_var, None)])
        fields(batch_panel, [
            ("ID del lote IA", self.ai_batch_var, None),
            ("Máximo de invocaciones del lote", self.ai_calls_var, None),
            ("Tope de tokens reportados", self.ai_token_budget_var, None),
        ])
        actions = ttk.Frame(connection, style="Panel.TFrame")
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Guardar selección", command=self.save_connection_selection).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Guardar clave", command=self.configure_key).pack(side="left")
        ttk.Button(actions, text="Detectar proveedores", command=self.show_providers).pack(side="left", padx=6)
        access = ttk.Frame(connection, style="Panel.TFrame")
        access.pack(fill="x", pady=8)
        ttk.Button(access, text="Conectar / iniciar sesión", command=self.connect_provider).pack(side="left")
        ttk.Button(access, text="Página oficial / claves", command=self.open_provider_help).pack(side="left", padx=6)
        ttk.Button(access, text="Historial IA", command=self.show_runs).pack(side="left")
        self.access_var = tk.StringVar(value=self.connection_notice)
        ttk.Label(connection, textvariable=self.access_var, wraplength=760, background="#ffffff").pack(anchor="w", pady=8)
        self.connection_hint_var = tk.StringVar()
        ttk.Label(connection, textvariable=self.connection_hint_var, wraplength=760,
                  style="Hint.TLabel", justify="left").pack(anchor="w", pady=(0, 8))
        self._refresh_connection_controls()
        self.provider_var.trace_add("write", self._provider_changed)
        self.cli_profile_var.trace_add("write", lambda *_args: self._refresh_connection_controls())
        actions2 = ttk.Frame(page_panel, style="Panel.TFrame")
        actions2.pack(fill="x", pady=(4, 8))
        ttk.Button(actions2, text="Elegir encargo", command=self.choose_job).pack(side="left", padx=(0, 6))
        ttk.Button(actions2, text="Crear desde resultado", command=self.job_from_result).pack(side="left")
        ttk.Button(actions2, text="Vista previa", command=lambda: self.run_provider(False)).pack(side="left", padx=6)
        ttk.Button(actions2, text="Enviar a IA", style="Primary.TButton", command=lambda: self.run_provider(True)).pack(side="left")
        batch_ai = ttk.Frame(batch_panel, style="Panel.TFrame")
        batch_ai.pack(fill="x", pady=(4, 8))
        ttk.Button(batch_ai, text="Crear lote IA desde resultado", command=self.ai_from_result).pack(side="left")
        ttk.Button(batch_ai, text="Vista previa del lote", command=lambda: self.run_ai_plan(False)).pack(side="left", padx=6)
        dispatch = ttk.Frame(batch_panel, style="Panel.TFrame")
        dispatch.pack(fill="x", pady=(4, 8))
        ttk.Button(dispatch, text="Enviar / reanudar lote", style="Primary.TButton",
                   command=lambda: self.run_ai_plan(True)).pack(side="left")
        ttk.Button(dispatch, text="Pausar tras página", command=self.pause_ai).pack(side="left", padx=6)
        ttk.Label(batch_panel, text="El límite de tokens se conoce después de cada respuesta. Un CLI puede hacer varias llamadas internas.",
                  style="Hint.TLabel", wraplength=760).pack(anchor="w")
        ttk.Label(memory, text="Consultar, convertir localmente y abrir derivados: sin llamadas externas.\n"
                  "No hay rotación automática de cuentas. Pro no equivale a saldo API.\n"
                  "Claude y Codex deben consultar primero esta biblioteca.\n\nCarpeta de memoria:",
                  style="Hint.TLabel", wraplength=760, justify="left").pack(anchor="w")
        ttk.Label(memory, text=str(DATA_ROOT), style="Hint.TLabel", wraplength=760).pack(anchor="w", pady=8)
        ttk.Button(memory, text="Abrir carpeta de memoria", command=lambda: self._open_path(DATA_ROOT)).pack(anchor="w")
        ttk.Button(memory, text="Carpeta de datos…", command=self.choose_data_root).pack(anchor="w", pady=(8, 0))
        ttk.Button(memory, text="Carpeta MD…", command=self.choose_export_root).pack(anchor="w", pady=(8, 0))

    def choose_export_root(self):
        if self.busy:
            self.status_var.set("Termina o pausa el trabajo antes de cambiar la carpeta MD.")
            return
        folder = filedialog.askdirectory(title="Carpeta para próximas exportaciones MD")
        if not folder:
            return
        if not messagebox.askyesno("Cambiar carpeta de exportación MD",
                f"Nueva carpeta: {folder}\n\nSe usará para las próximas exportaciones. "
                "Los MD anteriores no se moverán ni se borrarán. ¿Guardar esta ubicación?", default="no"):
            return
        from .runtime_paths import configure_export_root
        try:
            configure_export_root(APP_ROOT, DATA_ROOT, Path(folder))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Carpeta MD no configurada", str(exc))
            return
        self.status_var.set("Carpeta MD guardada para próximas exportaciones. No se movió ningún archivo existente.")

    def choose_data_root(self):
        if self.busy:
            self.status_var.set("Termina o pausa el trabajo antes de cambiar la carpeta de datos.")
            return
        folder = filedialog.askdirectory(title="Carpeta de datos para la próxima apertura", initialdir=DATA_ROOT)
        if not folder:
            return
        if not messagebox.askyesno("Cambiar carpeta al volver a abrir",
                f"Nueva carpeta: {folder}\n\nSe usará en la próxima apertura. "
                "Los datos actuales no se moverán ni se borrarán; una carpeta vacía mostrará una biblioteca vacía. "
                "Las sesiones y credenciales siguen perteneciendo a esta cuenta de Windows.\n\n¿Guardar esta ubicación?",
                default="no"):
            return
        from .runtime_paths import configure_data_root
        try:
            configure_data_root(APP_ROOT, Path(folder))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Carpeta no configurada", str(exc))
            return
        self.status_var.set("Carpeta guardada para la próxima apertura. Esta sesión conserva sus datos actuales; no se movió nada.")

    def _mode_changed(self, _event=None):
        if self.busy:
            self.mode_var.set(self._last_mode)
            self.status_var.set("Termina o pausa el trabajo actual antes de cambiar de modo.")
            return
        self._last_mode = self.mode_var.get() if self.mode_var.get() in (LOCAL_MODE, AI_MODE) else LOCAL_MODE
        self.mode_var.set(self._last_mode)
        self.mode_hint_var.set("Sin envíos a proveedores. Cambiar a IA no envía documentos." if self._last_mode == LOCAL_MODE
                               else "IA habilitada: cada envío sigue exigiendo revisión y confirmación.")
        self.status_var.set("Modo de sesión: " + self._last_mode + " · no se ha enviado nada")

    def _allow_external(self, action="Enviar a IA") -> bool:
        """Guardia UI común, incluida una vista previa TypeSafe abierta anteriormente.

        No es un aislamiento de red del sistema operativo. Toda acción externa de
        esta interfaz debe atravesarla; las conversiones locales no la necesitan.
        """
        if self.mode_var.get() == AI_MODE:
            return True
        notice = f"{action} bloqueado por modo Local. Elige «{AI_MODE}» y revisa el envío."
        self.status_var.set(notice)
        self.access_var.set(notice)
        return False

    def _current_connection(self, provider=None):
        provider = provider or self.provider_var.get()
        return validate_connection(provider, {
            "model": self.model_var.get().strip(),
            "key_slot": int(self.key_slot_var.get()),
            "cli_profile": self.cli_profile_var.get() if provider not in ENV_NAMES else "principal",
        })

    def _provider_changed(self, *_args):
        provider = self.provider_var.get()
        if provider not in PROVIDERS or provider == self._active_provider:
            return
        try:
            self.connection_settings["connections"][self._active_provider] = self._current_connection(self._active_provider)
        except ValueError:
            # No guardar un modelo/perfil inválido bajo otro proveedor al cambiar.
            self.access_var.set("La selección anterior era inválida y no se guardó. Revisa los campos del proveedor.")
        self._active_provider = provider
        current = self.connection_settings["connections"].get(provider, default_connection())
        self.model_var.set(current["model"])
        self.key_slot_var.set(str(current["key_slot"]))
        self.cli_profile_var.set(current["cli_profile"])
        self._refresh_connection_controls()
        self.access_var.set("Proveedor cambiado. Modelo y cuenta propios recuperados; revisa antes de guardar o enviar.")

    def _refresh_connection_controls(self):
        provider = self.provider_var.get()
        if provider not in PROVIDERS:
            return
        is_api = provider in ENV_NAMES
        self.connection_controls["Perfil de clave API (1 a 5)"].configure(state="readonly" if is_api else "disabled")
        self.connection_controls["Perfil de cuenta CLI"].configure(
            values=profiles_for(provider), state="disabled" if is_api else "readonly")
        if is_api:
            hint = ("Cinco espacios de clave por proveedor, sin rotación automática. «Guardar clave» usa Windows; "
                    "«Guardar selección» recuerda modelo y perfil, nunca secretos ni permiso. "
                    "Una clave presente no verifica acceso, visión, saldo ni cuota independiente.")
        elif self.cli_profile_var.get().startswith("fab"):
            hint = ("Cuenta Windows heredada de Fábrica. La sesión sigue perteneciendo a fab1–fab5; "
                    "no se copian sus tokens. El envío exige el puente seguro y su validación local. "
                    "Iniciar sesión una vez no demuestra que siga vigente ni que tenga cuota.")
        else:
            principal = ("Principal usa un perfil propio de Sistema MD y requiere su primer inicio de sesión. "
                         if provider in {"codex-cli", "claude-cli"} else
                         "Principal puede usar la sesión existente del CLI; no se ha verificado aquí. ")
            hint = ("La sesión la conserva el CLI oficial para el perfil elegido, cuando lo admite. "
                    + principal + "Los perfiles adicionales no copian tokens. "
                    "Ejecutable localizado no significa autenticación verificada ni saldo disponible.")
        self.connection_hint_var.set(hint)

    def save_connection_selection(self):
        try:
            provider = self.provider_var.get()
            connection = self._current_connection(provider)
            candidate = {"version": 1, "selected_provider": provider,
                         "connections": {**self.connection_settings["connections"], provider: connection}}
            save_settings(DATA_ROOT, candidate)
            self.connection_settings = candidate
        except (OSError, ValueError) as exc:
            self.access_var.set("No se guardó la selección: " + str(exc))
            return
        self.access_var.set("Selección guardada: proveedor, modelo y cuenta. No guarda claves ni permiso; al abrir vuelve a Local.")

    def configure_key(self):
        provider = self.provider_var.get()
        if provider not in ENV_NAMES:
            self.connect_provider()
            return
        value = simpledialog.askstring("Clave API", "Se guardará para tu usuario en Windows, fuera del proyecto.", show="*")
        if value:
            slot = int(self.key_slot_var.get())
            save_key(provider, value, slot)
            messagebox.showinfo("Clave guardada", "Guardada en Windows. No se ha enviado nada al proveedor.")

    def connect_provider(self):
        if self.busy:
            return
        if not self._allow_external("Conectar / iniciar sesión"):
            return
        provider = self.provider_var.get()
        if provider in ENV_NAMES:
            self.configure_key()
        elif messagebox.askyesno("Acceso oficial", "Se abrirá el CLI oficial en una ventana de terminal para que inicies sesión. No se enviará ningún documento. Completa el acceso, cierra esa ventana y vuelve aquí. ¿Abrir?"):
            result = launch_login(DATA_ROOT, provider, self.cli_profile_var.get())
            self.access_var.set(result["notice"])

    def open_provider_help(self):
        if not self._allow_external("Abrir página del proveedor"):
            return
        import webbrowser
        webbrowser.open(OFFICIAL_HELP[self.provider_var.get()])

    def show_row(self, tree):
        selected = tree.selection()
        if selected:
            popup = tk.Toplevel(self)
            popup.title("Detalle completo")
            popup.geometry("800x500")
            text_view(popup, "\n\n".join(str(value) for value in tree.item(selected[0], "values")))

    def show_failure(self):
        selected = self.failure_tree.selection()
        if not selected:
            return
        signature = self.failure_tree.item(selected[0], "values")[0]
        failure = next((row for row in list_failures(DATA_ROOT) if row['signature'] == signature), None)
        if failure:
            popup = tk.Toplevel(self)
            popup.title("Fallo · " + signature)
            popup.geometry("850x550")
            text_view(popup, f"{failure['message']}\n\nCausa probable: {failure['probable_cause']}\n\nSiguiente acción: {failure['remedy']}\n\nEstado: {failure['status']} · Apariciones: {failure['occurrences']}\n\nCorrecciones registradas:\n" + "\n".join(str(fix) for fix in failure['resolutions']))

    def show_runs(self):
        from .providers import run_history
        result = run_history(DATA_ROOT)
        popup = tk.Toplevel(self)
        popup.title("Historial de IA · registros locales")
        popup.geometry("850x570")
        text = result['notice'] + "\n\n"
        for row in result['runs']:
            tokens = row['total_tokens'] if row['total_tokens'] is not None else 'sin dato (no significa cero)'
            text += f"{row['provider']} / {row['model']}\nEstado: {row['state']} · Tokens comunicados: {tokens}\nError: {row['error_code'] or 'sin error registrado'}\nCarpeta: {row['folder']}\n\n"
        text_view(popup, text if result['runs'] else text + "No hay ejecuciones registradas aquí.")

    def show_providers(self):
        status = provider_status()
        lines = []
        for row in status["providers"]:
            lines.append(f"{row['provider']}: {row['detail']}")
            if row.get("key_slots"):
                lines.append("  Claves: " + " · ".join(
                    f"{slot['slot']}: {'configurada' if slot['configured_locally'] else 'sin configurar'}"
                    for slot in row["key_slots"]))
        messagebox.showinfo("Disponibilidad local", "\n".join(lines) + "\n\n" + status['notice'])

    def choose_job(self):
        folder = filedialog.askdirectory(title="Selecciona un encargo que contenga paquete.json",
                                         initialdir=DATA_ROOT / "encargos")
        if folder:
            self.job_var.set(folder)

    def job_from_result(self):
        selected = self.result_tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona un resultado", "En Biblioteca elige un PDF preparado y vuelve aquí.")
            return
        number = simpledialog.askinteger("Página", "Página que quieres enviar a IA", minvalue=1)
        if number:
            folder = Path(self.result_paths[selected[0]])
            self._run("preparar_encargo", lambda: prepare_job(DATA_ROOT, folder, number),
                      lambda result: self.job_var.set(result["folder"]))

    def run_provider(self, send):
        if self.busy:
            return
        if send and not self._allow_external():
            return
        provider, model = self.provider_var.get(), self.model_var.get().strip()
        if not model or not self.job_var.get().strip():
            messagebox.showinfo("Falta configuración", "Indica el modelo exacto y elige un encargo antes de continuar.")
            return
        try:
            slot = int(self.key_slot_var.get()) if provider in ENV_NAMES else 1
            cli_profile = self.cli_profile_var.get() if provider not in ENV_NAMES else "principal"
            folder = Path(self.job_var.get())
            tokens = int(self.tokens_var.get())
            if tokens < 1:
                raise ValueError("Los tokens máximos deben ser mayores que cero.")
        except ValueError:
            messagebox.showerror("Configuración inválida", "Revisa los valores numéricos: perfil de clave y tokens positivos.")
            return
        preview = execute_job(DATA_ROOT, folder, provider, model, max_tokens=tokens,
                              key_slot=slot, cli_profile=cli_profile)
        if send and not self._confirm_action("Enviar una página",
                f"Proveedor: {provider}\nPerfil API: {slot}\nPerfil CLI: {cli_profile}\n"
                f"Modelo: {model}\nPágina: {preview['unit']}\n\n"
                "Consume cuota del proveedor. Los CLI pueden hacer varias llamadas internas y no tienen tope de tokens garantizado.",
                "Enviar esta página"):
            return
        if not send:
            messagebox.showinfo("Vista previa", f"Página {preview['unit']} → {provider} / {model}\nNo se ha enviado nada.")
            return
        self._run("enviar_ia", lambda: execute_job(
                  DATA_ROOT, folder, provider, model, send=True, max_tokens=tokens,
                  key_slot=slot, cli_profile=cli_profile),
                  self._provider_result, external=True)

    def _provider_result(self, result):
        import json
        messagebox.showinfo("Respuesta guardada",
                f"Estado: {result['status']}\nReutilizada: {result['response_reused']}\n"
                f"Consumo comunicado: {json.dumps(result['usage'], ensure_ascii=False)}\nFidelidad pendiente de contraste.")

    def ai_from_result(self):
        if self.busy:
            return
        selected = self.result_tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona un resultado",
                                "En Biblioteca elige un PDF preparado con imágenes y vuelve aquí.")
            return
        try:
            provider, model = self.provider_var.get(), self.model_var.get().strip()
            slot = int(self.key_slot_var.get()) if provider in ENV_NAMES else 1
            cli_profile = self.cli_profile_var.get() if provider not in ENV_NAMES else "principal"
            calls, budget = int(self.ai_calls_var.get()), int(self.ai_token_budget_var.get())
            tokens = int(self.tokens_var.get())
            folder = Path(self.result_paths[selected[0]])
        except ValueError as exc:
            messagebox.showerror("Configuración inválida", str(exc))
            return
        self._run("planificar_lote_ia", lambda: plan_ai_batch(
            DATA_ROOT, folder, provider, model, call_budget=calls,
            reported_token_budget=budget, max_tokens=tokens, key_slot=slot,
            cli_profile=cli_profile), self._ai_planned)

    def _ai_planned(self, result):
        self.ai_batch_var.set(result["id"])
        self.settings_sections["batch"].set_expanded(True)
        self.tabs.select(3)
        self._ai_batch_result(result)

    def _export_combined(self, result) -> Path | None:
        """Al cerrar un lote de IA, el documento UNIDO es el .md que al usuario le importa."""
        combined = result.get("combined") or {}
        if not combined.get("output"):
            return None
        md = self._export_md(combined["output"], result.get("_ui_md_exports"))
        if md:
            self.status_var.set(f"MD guardado · {md}")
        return md

    def run_ai_plan(self, send):
        if self.busy:
            return
        if send and not self._allow_external("Enviar lote IA"):
            return
        ident = self.ai_batch_var.get().strip()
        if not ident:
            messagebox.showinfo("Falta el lote", "Primero crea el lote IA desde un resultado preparado.")
            return
        try:
            preview = run_ai_batch(DATA_ROOT, ident)
        except Exception as exc:
            failure = self._record("vista_previa_lote_ia", exc)
            messagebox.showerror("Lote no disponible", f"{failure['message']}\nFirma: {failure['signature']}")
            return
        if not send:
            self._ai_batch_result(preview)
            return
        policy = preview["policy"]
        if not self._confirm_action("Enviar lote página a página",
                f"Documento: {preview['title']}\nPáginas: {len(preview['items'])}\n"
                f"Proveedor/modelo: {policy['provider']} / {policy['model']}\n"
                f"Perfil API: {policy['key_slot']} · Perfil CLI: {policy['cli_profile']}\n"
                f"Máximo: {policy['call_budget']} invocaciones y {policy['reported_token_budget']} tokens reportados\n\n"
                "Cada página se guarda antes de continuar. Un CLI puede hacer llamadas internas y el tope de tokens se conoce después de responder.",
                "Enviar / reanudar este lote"):
            return
        self.ai_stop.clear()
        self._ai_progress_id = ident
        self._run("lote_ia", lambda: run_ai_batch(DATA_ROOT, ident, send=True, stop=self.ai_stop),
                  self._ai_batch_result, external=True)

    def pause_ai(self):
        self.ai_stop.set()
        self.status_var.set("Pausa IA solicitada: termina la página actual y no empieza la siguiente.")

    def _ai_batch_result(self, result):
        self.refresh_all()
        md_unido = self._export_combined(result)
        popup = tk.Toplevel(self)
        popup.title("Lote IA · control de consumo")
        popup.geometry("900x580")
        tokens = result["reported_tokens"]
        lines = [(f"MD GUARDADO EN:\n{md_unido}\n\n" if md_unido else ""),
                 f"{result['title']}\n\nEstado: {result.get('status', result['state'])}\n",
                 f"Páginas: {result['counts']}\nInvocaciones atribuidas: {result['spent_calls']}\n",
                 f"Tokens reportados: {tokens}\nConsumo desconocido: {result['unknown_calls']}\n\n",
                 result["notice"] + "\n\n"]
        for item in result["items"]:
            lines.append(f"Página {item['unit']}: {item['state']}")
            data = item.get("result") or {}
            if data.get("reported_tokens") is not None:
                lines.append(f" · {data['reported_tokens']} tokens")
            if data.get("error"):
                lines.append(f"\n  {data['error']}\n  Firma: {data.get('signature', 'sin firma')}")
            lines.append("\n")
        report_path = Path(result.get("report") or DATA_ROOT / "lotes_ia")
        ttk.Button(popup, text="Abrir informe MD", command=lambda: self._open_path(report_path)).pack(anchor="w", padx=12, pady=6)
        text_view(popup, "".join(lines))

    def _run(self, operation: str, function, success=None, external=False) -> None:
        if external and not self._allow_external(operation):
            return
        if self.busy:
            self.status_var.set("Hay un trabajo en curso; espera a que termine.")
            return
        self.busy = True
        self._queue_selection_changed()
        self._result_selection_changed()
        self.progress.start(20)
        self.status_var.set(f"Trabajando: {operation}…")

        def worker() -> None:
            try:
                result = function()
                result = persist_operation_exports(operation, result)
                self.events.put(("success_external" if external else "success", operation, result, success))
            except Exception as exc:
                failure = self._record(operation, exc)
                self.events.put(("error", operation, failure, None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, operation, payload, callback = self.events.get_nowait()
                self.busy = False
                self.progress.stop()
                self._ai_progress_id = None
                if getattr(self, "closing", False):
                    self.destroy()                     # paquete y exportación intentada antes del evento
                    return
                # Restablecer antes del callback: también si falla su presentación o el
                # trabajador devolvió error. Si el callback inicia otro trabajo, _run
                # vuelve a deshabilitar las acciones de acuerdo con el nuevo busy.
                self._queue_selection_changed()
                self._result_selection_changed()
                if kind in {"success", "success_external"}:
                    try:
                        suffix = " · revisar recibo de consumo" if kind == "success_external" else " · 0 llamadas externas"
                        self.status_var.set(f"Completado: {operation}" + suffix)
                        if callback:
                            callback(payload)
                        self.refresh_all()
                    except Exception as exc:
                        failure = record_failure(DATA_ROOT, f"actualizar_interfaz:{operation}", exc)
                        self.status_var.set(f"Error mapeado: {failure['signature']}")
                        messagebox.showerror("Error diagnosticado", f"{failure['message']}\nFirma: {failure['signature']}")
                else:
                    self.status_var.set(f"Error mapeado: {payload['signature']} · {operation}")
                    self.refresh_failures()
                    messagebox.showerror("Error diagnosticado",
                                         f"{payload['message']}\n\nFirma: {payload['signature']}\n"
                                         f"Siguiente acción: {payload['remedy']}")
        except queue_module.Empty:
            pass
        self.after(100, self._drain_events)

    def _tick_ai_progress(self) -> None:
        """Avance visible del lote de IA: «página 7 de 14». Un trabajo largo sin avance parece
        colgado. Lectura de solo lectura y con espera mínima: jamás congela la ventana."""
        ident = getattr(self, "_ai_progress_id", None)
        if self.busy and ident:
            try:
                import sqlite3
                db = sqlite3.connect((DATA_ROOT / "indice.sqlite").resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=0.2)
                try:
                    estados = dict(db.execute(
                        "SELECT state,COUNT(*) FROM ai_items WHERE batch=? GROUP BY state", (ident,)).fetchall())
                finally:
                    db.close()
                total = sum(estados.values())
                hechas = total - estados.get("listo", 0) - estados.get("en_curso", 0)
                if total and not getattr(self, "closing", False):
                    self.status_var.set(f"Lectura visual: página {min(hechas + 1, total)} de {total} · "
                                        "Pausar en IA avanzada, o cerrar la ventana para pausar y salir")
            except Exception:
                pass                                    # el avance es cortesía: nunca rompe el trabajo
        self.after(1500, self._tick_ai_progress)

    def choose_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Selecciona uno o varios documentos")
        if paths:
            self._run("analizar_archivos", lambda: [self.work_queue.add(Path(path), 1) for path in paths])

    def choose_folder(self) -> None:
        path = filedialog.askdirectory(title="Selecciona una carpeta", mustexist=True)
        if path:
            self._run("analizar_carpeta", lambda: self.work_queue.add(Path(path), 200), self._folder_result)

    def _folder_result(self, result: dict) -> None:
        if result.get("truncated"):
            messagebox.showwarning("Carpeta limitada", "Se analizaron los primeros 200 archivos. El límite evita bloquear el equipo.")

    def remove_selected(self) -> None:
        if self.busy:
            return
        paths = list(self.queue_tree.selection())
        if paths:
            self.work_queue.remove(paths)
            self.refresh_queue()

    def clear_queue(self) -> None:
        if self.busy:
            return
        if self.work_queue.items and messagebox.askyesno("Vaciar cola", "¿Quitar todas las rutas de la cola? Los originales y resultados no se borran."):
            self.work_queue.clear()
            self.refresh_queue()

    def plan_local(self):
        self._run("planificar_lote", lambda: plan_queue(DATA_ROOT), self._local_result)

    def run_local(self):
        if self.busy:
            return
        plan = batch_status(DATA_ROOT)
        if not messagebox.askyesno("Lote local, sin IA", f"Rutas: {plan['routes']}\nEstados: {plan['counts']}\n\n"
                "Procesar hasta 20 archivos; PDF, CAD y Office antiguo quedan pendientes. No describe imágenes ni certifica fidelidad. ¿Continuar?"):
            return
        self.batch_stop.clear()
        self._run("lote_local", lambda: run_batch(DATA_ROOT, plan["id"], stop=self.batch_stop), self._local_result)

    def pause_local(self):
        self.batch_stop.set()
        self.status_var.set("Pausa solicitada: se conserva el archivo actual y no empieza el siguiente.")

    def show_local(self):
        self._run("estado_lote", lambda: batch_status(DATA_ROOT), self._local_result)

    def _local_result(self, result):
        self.refresh_all()
        # Cada archivo TERMINADO del lote deja su .md con nombre en la carpeta legible.
        exportados = 0
        for row in result.get("items", []):
            data = row.get("result") or {}
            if (row.get("state") == "terminado" and data.get("output")
                    and self._export_md(data["output"], result.get("_ui_md_exports"))):
                exportados += 1
        window = tk.Toplevel(self)
        window.title("Plan y diagnóstico del lote local")
        window.geometry("850x500")
        lines = [(f"{exportados} MD guardado(s) en:\n{md_export_dir()}\n\n" if exportados else ""),
                 f"LOTE {result['id'][:12]}\n\n", f"Rutas: {result['routes']}\nEstados: {result['counts']}\n\n",
                 "Sin llamadas a IA. Terminado significa paquete íntegro, no fidelidad certificada.\n\n"]
        for row in result['items']:
            lines.append(f"{row['number']+1}. {Path(row['path']).name}\n   {row['route']} · {row['state']} — {row['reason']}\n")
            data = row.get('result') or {}
            if data.get('error'):
                lines.append(f"   ERROR: {data['error']}\n   Firma: {data['signature']}\n")
        ttk.Button(window, text="Abrir informes MD del lote", command=lambda: self._open_path(DATA_ROOT / 'lotes')).pack(anchor='w', padx=12, pady=6)
        text_view(window, ''.join(lines))

    def configure_local_engine(self):
        """Configura rutas de un entorno existente; nunca instala ni abre otra herramienta."""
        if self.busy:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Motores locales · configuración")
        dialog.geometry("790x490")
        dialog.minsize(640, 420)
        dialog.transient(self)
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        buttons = ttk.Frame(panel)
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(panel, text="Conectar un motor instalado", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        description = ttk.Label(panel, text="Los archivos se convierten desde esta aplicación y llegan a Biblioteca. "
                  "Este panel solo guarda rutas locales: no instala paquetes, descarga modelos ni abre aplicaciones externas.",
                  wraplength=710, justify="left")
        description.pack(anchor="w", fill="x", pady=(8, 14))
        fields = ttk.Frame(panel)
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        selected = LOCAL_ENGINE_CHOICES.get(self.local_engine_var.get(), "native")
        engine_var = tk.StringVar(value=selected if selected != "native" else "docling")
        python_var, models_var = tk.StringVar(), tk.StringVar()
        ttk.Label(fields, text="Motor").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Combobox(fields, textvariable=engine_var, values=("docling", "markitdown"),
                     state="readonly").grid(row=0, column=1, sticky="ew", padx=12)
        ttk.Label(fields, text="Python del motor").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Entry(fields, textvariable=python_var).grid(row=1, column=1, sticky="ew", padx=12)
        ttk.Label(fields, text="Modelos locales\n(Docling)").grid(row=2, column=0, sticky="w", pady=8)
        models_entry = ttk.Entry(fields, textvariable=models_var)
        models_entry.grid(row=2, column=1, sticky="ew", padx=12)

        def choose_python():
            path = filedialog.askopenfilename(parent=dialog, title="Python del entorno del motor",
                                             filetypes=(("Ejecutable Python", "python*.exe"), ("Todos", "*")))
            if path:
                python_var.set(path)

        def choose_models():
            path = filedialog.askdirectory(parent=dialog, title="Carpeta de modelos Docling ya descargados")
            if path:
                models_var.set(path)

        ttk.Button(fields, text="Elegir…", command=choose_python).grid(row=1, column=2)
        models_button = ttk.Button(fields, text="Elegir…", command=choose_models)
        models_button.grid(row=2, column=2)
        status_var = tk.StringVar(value="Guardar rutas no demuestra que el motor esté listo. Usa Comprobar motores.")
        status_label = ttk.Label(panel, textvariable=status_var, wraplength=710, justify="left")
        status_label.pack(anchor="w", fill="x", pady=14)
        panel.bind("<Configure>", lambda event: [label.configure(wraplength=max(260, event.width - 36))
                                                 for label in (description, status_label)])

        def selected_engine(*_args):
            is_docling = engine_var.get() == "docling"
            models_entry.configure(state="normal" if is_docling else "disabled")
            models_button.configure(state="normal" if is_docling else "disabled")
            try:
                from .local_engines import engine_configuration
                current = engine_configuration(engine_var.get())
            except (OSError, ValueError, ImportError) as exc:
                python_var.set("")
                models_var.set("")
                status_var.set("No se pudo recuperar la configuración. Se conserva sin modificar: " + str(exc))
                return
            python_var.set(str(current.get("python") or ""))
            models_var.set(str(current.get("models_path") or "") if is_docling else "")
            status_var.set("Rutas recuperadas. Guardar o ver una ruta no verifica instalación ni activos; usa Comprobar motores.")

        engine_var.trace_add("write", selected_engine)
        selected_engine()

        def save():
            if not python_var.get().strip():
                status_var.set("Selecciona el ejecutable Python del entorno; no se adivina ni se instala otro.")
                return
            if engine_var.get() == "docling" and not models_var.get().strip():
                status_var.set("Selecciona los modelos locales de Docling. La conversión no los descargará.")
                return
            try:
                from .local_engines import configure_engine
                configure_engine(engine_var.get(), Path(python_var.get().strip()),
                                 Path(models_var.get().strip()) if engine_var.get() == "docling" and models_var.get().strip() else None)
            except (OSError, ValueError, ImportError) as exc:
                status_var.set("No se guardó la configuración: " + str(exc))
                return
            status_var.set("Rutas guardadas. Comprueba el motor; no se ha convertido ni enviado ningún documento.")

        ttk.Button(buttons, text="Cerrar", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Guardar rutas", command=save).pack(side="right", padx=8)
        ttk.Button(buttons, text="Comprobar motores", command=self.show_local_engines).pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        return dialog

    def show_local_engines(self):
        from .local_engines import probe_engines

        def shown(result):
            popup = tk.Toplevel(self)
            popup.title("Motores locales · evidencia de disponibilidad")
            popup.geometry("860x570")
            labels = {"python_missing": "Falta configurar el Python del motor",
                      "no_instalado": "Motor no instalado en ese entorno",
                      "presente_sin_control": "Paquete presente; conversión de control pendiente",
                      "condicional_pendiente": "Evaluación condicional pendiente; no integrado",
                      "worker_failed": "No se pudo comprobar el entorno"}
            lines = ["DISPONIBILIDAD DE MOTORES", "Sin llamadas API ni descarga de modelos.",
                     "Presencia no equivale a fidelidad ni demuestra aislamiento de red del sistema operativo.", ""]
            for row in result.get("engines", []):
                lines.extend([str(row.get("engine", "Motor")).upper(),
                              labels.get(row.get("status"), str(row.get("status", "No comprobado")))])
                if row.get("python"):
                    lines.append("Python: " + str(row["python"]))
                if row.get("version"):
                    lines.append("Versión detectada: " + str(row["version"]))
                lines.append("Activos locales: " + ("listos" if row.get("models_ready") is True else "no comprobados"))
                lines.append("Prueba de conversión: " + ("aprobada" if row.get("control_passed") is True else "pendiente"))
                lines.append("")
            lines.extend(["DETALLE TÉCNICO", json.dumps(result, ensure_ascii=False, indent=2)])
            text_view(popup, "\n".join(lines))

        self._run("comprobar_motores_locales", probe_engines, shown)

    def process_with_local_engine(self, source_text, engine):
        """Una selección explícita; cualquier error detiene, nunca cambia de motor/API."""
        source = Path(source_text)
        pages = None
        if engine == "docling" and source.suffix.lower() == ".pdf":
            value = simpledialog.askstring("Docling · páginas explícitas",
                "Indica las páginas que quieres convertir localmente, por ejemplo 1-3 o 2,5.\n"
                "Máximo 20 páginas por esta operación. Vacío o Cancelar no convierte nada.",
                initialvalue="")
            if not value or not value.strip():
                return
            try:
                pages = parse_pages(value)
                if len(pages) > 20:
                    raise ValueError("Selecciona como máximo 20 páginas por operación.")
            except ValueError as exc:
                messagebox.showerror("Rango inválido", str(exc))
                return

        def work():
            from .local_engines import convert_with_engine
            from .pipeline import file_hash
            self.work_queue.load()
            item = next((row for row in self.work_queue.items if row["path"] == source_text), None)
            if item is None:
                raise ValueError("El archivo ya no está en la cola; vuelve a seleccionarlo.")
            current_hash = file_hash(source)
            changed = bool(item.get("sha256") and item["sha256"] != current_hash)
            result = convert_with_engine(DATA_ROOT, source, engine, pages=pages)
            if not isinstance(result, dict) or not result.get("output") or not result.get("status"):
                raise ValueError("El motor no publicó un resultado; la cola se conserva sin marcar convertido.")
            if result.get("source_sha256", current_hash) != current_hash or file_hash(source) != current_hash:
                raise ValueError("La fuente cambió durante el trabajo; revisa el resultado antes de actualizar la cola.")
            self.work_queue._finish(source_text, current_hash, result, changed)
            return {**result, "source_changed": changed, "local_engine": engine}

        self._run("procesar_documento", work, self._local_engine_processed)

    def _local_engine_processed(self, result):
        exported = self._export_md(result["output"], result.get("_ui_md_exports"))
        self.refresh_all()
        output = Path(result["output"]).resolve()
        for identifier, path in self.result_paths.items():
            if Path(path).resolve() == output and self.result_tree.exists(identifier):
                self.result_tree.selection_set(identifier)
                self.result_tree.see(identifier)
                break
        self.tabs.select(1)
        messagebox.showinfo("Conversión local guardada",
            f"Motor: {result['local_engine']}\nEstado: {result['status']}\n"
            + (f"MD: {exported}\n" if exported else "Paquete conservado; revisa el diagnóstico de exportación.\n")
            + "Resultado integrado en Biblioteca. Sin llamadas API. La fidelidad requiere contraste con la fuente.")

    def process_selected(self) -> None:
        if self.busy:
            return
        selected = list(self.queue_tree.selection())
        if len(selected) != 1:
            messagebox.showinfo("Selecciona uno", "Selecciona exactamente un documento para procesarlo.")
            return
        path = selected[0]
        engine = LOCAL_ENGINE_CHOICES.get(self.local_engine_var.get())
        if engine not in {"native", "docling", "markitdown"}:
            messagebox.showerror("Motor inválido", "Elige uno de los motores mostrados; no se elegirá otro automáticamente.")
            return
        if engine != "native":
            self.process_with_local_engine(path, engine)
            return
        item = next(entry for entry in self.work_queue.items if entry["path"] == path)
        if item["format"] not in LOCAL_TEXT | NATIVE_KINDS | {"pdf"}:
            messagebox.showinfo("Adaptador pendiente", item["action"])
            return
        pages = None
        if item["format"] == "pdf":
            # Por defecto se propone el documento ENTERO. Antes el cuadro venía con "1" y un
            # tope de 20: pulsar Aceptar convertía una sola página de un PDF de cientos.
            try:
                import fitz
                with fitz.open(path) as pdf:
                    total = pdf.page_count
            except Exception:
                total = 0
            sugerido = "1-%d" % total if total else "1"
            detalle = ("El PDF tiene %d páginas. Aceptar las prepara localmente, sin IA.\n"
                       "Para un tramo: 1-20 o 1,4,7" % total) if total else \
                      "Indica las páginas. Ejemplos: 1-20 o 1,4,7"
            value = simpledialog.askstring("Páginas del PDF", detalle, initialvalue=sugerido)
            if not value:
                return
            try:
                pages = parse_pages(value)
                if total and pages[-1] > total:
                    raise ValueError("El PDF solo tiene %d páginas" % total)
            except ValueError as exc:
                messagebox.showerror("Rango inválido", str(exc))
                return
        self._run("procesar_documento", lambda: self.work_queue.process(path, pages), self._processed)

    def _paginas_para_vision(self, folder: Path) -> tuple[list[int], list[int]]:
        """(todas las páginas con imagen, las que el paso local NO puede leer).

        Una página escaneada o gráfica guarda su contenido DENTRO de la imagen: el texto
        embebido trae poco o nada. Mismo umbral calibrado que el aviso de prepare_pdf.
        """
        try:
            doc = json.loads((folder / "document.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [], []
        if doc.get("input_kind") != "pdf_prepared":
            return [], []
        todas, pobres = [], []
        for unit in doc.get("units", []):
            if not unit.get("image_asset"):
                continue
            todas.append(unit["number"])
            graficos = int(unit.get("images", 0)) + int(unit.get("drawings", 0))
            if int(unit.get("embedded_characters", 0)) < 150 or graficos >= 6:
                pobres.append(unit["number"])
        return todas, pobres

    def _processed(self, result: dict) -> None:
        base = (f"Estado: {result['status']}\nReutilizado: {'sí' if result.get('reused') else 'no'}\n"
                "Consumo externo: 0 llamadas")
        folder = Path(str(result.get("output") or ""))
        # El resultado local se conserva incluso si se cancela o falla una fase de IA.
        md = self._export_md(folder, result.get("_ui_md_exports"))
        todas, pobres = self._paginas_para_vision(folder)
        provider = self.provider_var.get()
        model = self.model_var.get().strip()
        if not todas or not model:
            aviso = ("\n\nEste PDF conserva imágenes pendientes de lectura visual. "
                     "Puedes seleccionar un proveedor y modelo explícitos en IA avanzada. "
                     "La extracción local no certifica fidelidad.") if todas else ""
            messagebox.showinfo("Trabajo local terminado", base + (f"\n\nMD guardado en:\n{md}" if md else "") + aviso)
            return
        paginas = self._choose_visual_pages(todas, pobres, provider, model)
        if not paginas:
            self.status_var.set("Resultado local conservado · lectura visual cancelada · 0 llamadas externas")
            return
        try:
            slot = int(self.key_slot_var.get()) if provider in ENV_NAMES else 1
            cli_profile = self.cli_profile_var.get() if provider not in ENV_NAMES else "principal"
            tokens = int(self.tokens_var.get())
            llamadas = int(self.ai_calls_var.get())
            presupuesto = int(self.ai_token_budget_var.get())
            if min(tokens, llamadas, presupuesto) < 1:
                raise ValueError("Los límites del lote deben ser mayores que cero.")
        except ValueError as exc:
            messagebox.showerror("Configuración inválida", str(exc))
            return
        # Preparar el alcance nunca autoriza envío, cambios de presupuesto ni purga de intentos.
        self._run("planificar_lectura_visual", lambda: plan_ai_batch(
            DATA_ROOT, folder, provider, model, pages=paginas, call_budget=llamadas,
            reported_token_budget=presupuesto, max_tokens=tokens, key_slot=slot,
            cli_profile=cli_profile), self._ai_planned)

    def _choose_visual_pages(self, pages, candidates, provider, model):
        """Selección de alcance sin efectos. Cancelar/Escape/cerrar siempre devuelven None."""
        answer = {"pages": None}
        box = tk.Toplevel(self)
        box.title("Lectura visual opcional · primero prepara una vista previa")
        box.transient(self)
        box.resizable(False, False)
        body = ttk.Frame(box, style="Panel.TFrame", padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="El resultado local ya está guardado", style="Section.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"Proveedor: {provider}\nModelo: {model}\n\n"
                  "Elige las páginas que quieres preparar. Todavía no se enviará nada.\n"
                  "Las páginas sugeridas se detectan por poco texto o gráficos; no es una evaluación de fidelidad.",
                  style="Hint.TLabel", wraplength=520, justify="left").pack(anchor="w", pady=12)
        choice = tk.StringVar(value="")
        ttk.Radiobutton(body, text=f"Todas las páginas con imagen ({len(pages)})",
                        variable=choice, value="all").pack(anchor="w", pady=4)
        if candidates:
            ttk.Radiobutton(body, text=f"Solo páginas sugeridas para revisar ({len(candidates)})",
                            variable=choice, value="candidates").pack(anchor="w", pady=4)
        ttk.Label(body, text="Máximo 200 páginas por plan. Si eliges más, se preparan las primeras 200; el resto queda pendiente.",
                  style="Hint.TLabel", wraplength=520).pack(anchor="w", pady=10)
        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.pack(fill="x", pady=(8, 0))

        def prepare():
            selected = pages if choice.get() == "all" else candidates if choice.get() == "candidates" else []
            if selected:
                answer["pages"] = list(selected[:200])
                box.destroy()

        cancel = ttk.Button(actions, text="Cancelar · conservar local", command=box.destroy)
        cancel.pack(side="right")
        accept = ttk.Button(actions, text="Preparar vista previa", command=prepare, state="disabled")
        accept.pack(side="right", padx=8)
        choice.trace_add("write", lambda *_args: accept.configure(state="normal" if choice.get() else "disabled"))
        box.bind("<Escape>", lambda _event: box.destroy())
        box.protocol("WM_DELETE_WINDOW", box.destroy)
        box.grab_set()
        cancel.focus_set()
        self.wait_window(box)
        return answer["pages"]

    def refresh_all(self) -> None:
        self.refresh_queue()
        self.refresh_results()
        self.refresh_failures()

    def refresh_queue(self) -> None:
        if not self.busy:
            self.work_queue.load()
        selected = set(self.queue_tree.selection()) if hasattr(self, "queue_tree") else set()
        self.queue_tree.delete(*self.queue_tree.get_children())
        for item in self.work_queue.items:
            self.queue_tree.insert("", "end", iid=item["path"], values=(
                item["name"], item["format"].upper(), human_size(item["bytes"]),
                item["status"].replace("_", " ").title(), item["action"]))
        for item in selected:
            if self.queue_tree.exists(item):
                self.queue_tree.selection_add(item)
        summary = self.work_queue.summary()
        for name, key in [("En cola", "total"), ("Listos", "ready"),
                          ("Convertidos", "completed"), ("Adaptadores pendientes", "pending_adapters")]:
            self.card_vars[name].set(str(summary[key]))
        self._queue_selection_changed()

    def refresh_results(self, *, reload=True) -> None:
        # Actualizar/F5 y el final de una operación renuevan la integridad en disco.
        # Escribir un filtro solo consulta esta instantánea, nunca relee todos los
        # recursos del catálogo en el hilo gráfico por cada pulsación.
        if reload:
            rows = report(DATA_ROOT)
            self._result_rows = rows
        else:
            rows = self._result_rows
        selected = self.result_tree.selection()
        position = self.result_tree.yview()[0]
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_paths.clear()
        ver_paginas = self.show_pages_var.get()
        ocultas = 0
        query = self.title_filter_var.get().strip().casefold()
        filter_state = self.result_filter_var.get()
        for row in rows:
            ident = row["id"]
            if row.get("kind") == "structured_response" and not ver_paginas:
                ocultas += 1
                continue
            if query and query not in row["title"].casefold():
                continue
            if filter_state == "Dañados" and row["integrity_ok"]:
                continue
            if filter_state == "Por revisar" and "revis" not in row["status"].casefold():
                continue
            self.result_paths[ident] = row["output"]
            tags = ("damaged",) if not row["integrity_ok"] else (("review",) if "revis" in row["status"].casefold() else ())
            self.result_tree.insert("", "end", iid=ident, tags=tags, values=(
                row["title"], row["status"].upper(), "ÍNTEGRO" if row["integrity_ok"] else "DAÑADO",
                row["updated"].replace("T", " ")[:19]))
        self.hidden_pages_var.set(
            f"{ocultas} página(s) suelta(s) de IA oculta(s)" if ocultas else "")
        for ident in selected:
            if self.result_tree.exists(ident):
                self.result_tree.selection_add(ident)
        self.result_tree.yview_moveto(position)
        visible = len(self.result_tree.get_children())
        self.library_hint_var.set(
            "Tu biblioteca está vacía. Agrega y procesa documentos en Trabajo y cola." if not rows else
            "No hay documentos con estos filtros. Borra el título o cambia el estado a Todos." if not visible else
            f"{visible} documento(s) visibles · Integridad de la última actualización, no fidelidad. "
            "Actualizar vuelve a comprobar; doble clic abre la lectura.")
        self._result_selection_changed()

    def refresh_failures(self) -> None:
        self.failure_tree.delete(*self.failure_tree.get_children())
        for item in list_failures(DATA_ROOT):
            self.failure_tree.insert("", "end", values=(item["signature"], item["operation"],
                                                         item["occurrences"], item["status"], item["message"]))

    def run_health(self) -> None:
        self._run("diagnostico_general", lambda: run_diagnostics(DATA_ROOT), self._show_health)

    def search_memory(self):
        query = self.query_var.get().strip()
        if query:
            self._run("consultar_memoria", lambda: consult(DATA_ROOT, query), self._show_excerpts)
        else:
            self.library_hint_var.set("Escribe palabras o una frase para consultar el contenido de los documentos.")
            self.query_entry.focus_set()

    def _show_excerpts(self, result):
        popup = tk.Toplevel(self)
        popup.title("Fragmentos de la memoria")
        popup.geometry("850x580")
        frame = ttk.Frame(popup)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=("Segoe UI", 11), padx=16, pady=16)
        scroll_table(text)
        text.insert("end", result["notice"] + "\n\n")
        for hit in result["hits"]:
            text.insert("end", f"{hit['title']} · unidad {hit['unit']} · {hit['status']}\n"
                        f"{hit['excerpt']}\nFuente: {hit['markdown']}\n\n")
        if not result["hits"]:
            text.insert("end", "No hay coincidencias en derivados íntegros. Prueba términos más concretos.")
        text.insert("end", f"\nTexto entregado: {result['content_characters']} caracteres. Nativos leídos: 0.")
        text.configure(state="disabled")

    def preview_typesafe(self):
        """Consulta derivados localmente y muestra el envío; nunca autoriza una llamada."""
        query = self.query_var.get().strip()
        if not query:
            self.library_hint_var.set("Escribe una consulta antes de revisar los fragmentos para TypeSafe.")
            self.query_entry.focus_set()
            return
        from .semantic_ui import open_ranking_dialog
        self._run("vista_previa_typesafe", lambda: consult(DATA_ROOT, query, limit=5, max_chars=6000),
                  lambda result: open_ranking_dialog(self, DATA_ROOT, result))

    def _show_health(self, result: dict) -> None:
        self.health_tree.delete(*self.health_tree.get_children())
        for check in result["checks"]:
            self.health_tree.insert("", "end", values=(check["name"], "OK" if check["ok"] else "ATENCIÓN", check["detail"]))

    def safe_repair(self) -> None:
        self._run("reparacion_segura", lambda: repair_safe(DATA_ROOT),
                  lambda result: messagebox.showinfo("Reparación segura", "\n".join(result["actions"])))

    def export_failures(self) -> None:
        self._run("exportar_fallos", lambda: export_failure_report(DATA_ROOT),
                  lambda result: self._open_path(Path(result["path"])))

    def open_index(self) -> None:
        try:
            result = export_index(DATA_ROOT)
            self._open_path(Path(result["path"]))
        except Exception as exc:
            failure = record_failure(DATA_ROOT, "abrir_indice", exc)
            messagebox.showerror("Error diagnosticado", f"{failure['message']}\nFirma: {failure['signature']}")

    # --- Órdenes sobre los resultados (menú contextual + botones + teclas) -----------------

    def _select_all_results(self, _event=None):
        self.result_tree.selection_set(self.result_tree.get_children())
        return "break"

    def _results_context_menu(self, event):
        """Como el Explorador: el clic derecho sobre una fila NO seleccionada la selecciona;
        sobre una ya seleccionada conserva la selección múltiple."""
        row = self.result_tree.identify_row(event.y) if getattr(event, "y", None) is not None else ""
        if row and row not in self.result_tree.selection():
            self.result_tree.selection_set(row)
            self.result_tree.focus(row)
        if not self.result_tree.selection():
            return "break"
        cuantos = len(self.result_tree.selection())
        # Con varias filas solo aplica Eliminar; las demás se ven grises (son estándar y se
        # esperan ahí). «Eliminar» es siempre el último ítem: se fija por índice, no por texto.
        for etiqueta in ("Abrir", "Abrir con…", "Mostrar en carpeta", "Ver estructura", "Copiar ruta del MD"):
            self.results_menu.entryconfigure(etiqueta, state="disabled" if cuantos > 1 else "normal")
        self.results_menu.entryconfigure(
            self.results_menu.index("end"),
            label="Eliminar" if cuantos == 1 else f"Eliminar {cuantos} resultados")
        x = getattr(event, "x_root", 0) or self.winfo_pointerx()
        y = getattr(event, "y_root", 0) or self.winfo_pointery()
        try:
            self.results_menu.tk_popup(x, y)
        finally:
            self.results_menu.grab_release()
        return "break"

    def _export_md(self, package, receipts=None) -> Path | None:
        """Copia el .md del paquete a la carpeta legible con su título como nombre (idempotente).

        Para el usuario «el MD» es ESE archivo, no el ``documento.md`` interno de una carpeta de
        64 caracteres. Si la carpeta de destino no está disponible se informa y no se pierde nada.
        """
        identity = str(Path(str(package)))
        receipt = next((item for item in receipts or [] if item.get("package") == identity), None)
        if receipt is None:
            receipt = persist_md_export(package)
        if receipt.get("path"):
            if receipt.get("portable_links") is False and receipt.get("link_warning"):
                warned = getattr(self, "_export_link_warnings", set())
                key = str(Path(receipt["path"]).parent)
                if key not in warned:
                    messagebox.showwarning("MD guardado: revisar enlaces al trasladarlo", receipt["link_warning"])
                    warned.add(key)
                    self._export_link_warnings = warned
            return Path(receipt["path"])
        self.status_var.set(
            f"Paquete conservado; no se pudo exportar el MD ({receipt.get('error', 'error')}) "
            f"· diagnóstico {receipt.get('signature', 'sin-registro')}")
        return None

    def _single_result_md(self) -> Path | None:
        selected = self.result_tree.selection()
        if len(selected) != 1:
            messagebox.showinfo("Selecciona un resultado", "Esta orden trabaja sobre un solo documento.")
            return None
        folder = Path(self.result_paths[selected[0]])
        md = folder / "documento.md"
        if not md.is_file():
            messagebox.showerror("Resultado dañado",
                                 "No existe documento.md en este resultado.\n"
                                 "Diagnóstico → Reparación segura lo da de baja del índice.")
            return None
        # Mostrar en carpeta / Abrir con… / Copiar ruta trabajan sobre el MD CON NOMBRE.
        return self._export_md(folder) or md

    def reveal_selected_result(self) -> None:
        """Abre el Explorador con el documento.md ya seleccionado."""
        md = self._single_result_md()
        if md is None:
            return
        import subprocess
        subprocess.Popen('explorer /select,"%s"' % md)      # la coma va pegada a la ruta
        self.status_var.set(f"Mostrado en carpeta · {md}")

    def open_with_selected_result(self) -> None:
        """Diálogo «Abrir con» de Windows sobre el documento.md: él elige con qué programa.

        ``os.startfile`` respeta la asociación de la PC, y aquí ``.md`` cae en un editor de
        código. Se lanza en proceso aparte para no congelar la ventana mientras elige.
        """
        md = self._single_result_md()
        if md is None:
            return
        import subprocess
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(md)])
        self.status_var.set(f"Abrir con… · {md.name}")

    def copy_selected_result_path(self) -> None:
        md = self._single_result_md()
        if md is None:
            return
        self.clipboard_clear()
        self.clipboard_append(str(md))
        self.status_var.set(f"Ruta copiada · {md}")

    def delete_selected_results(self) -> None:
        self._delete_results(list(self.result_tree.selection()), confirmar=True)

    def clear_all_results(self) -> None:
        """Alcance total explícito, incluidas páginas ocultas y filtros activos."""
        todos = [row["id"] for row in report(DATA_ROOT)]
        self._delete_results(todos, confirmar=True, all_results=True)

    def _delete_results(self, identifiers: list[str], confirmar: bool, all_results=False) -> None:
        if self.busy:
            self.status_var.set("Hay un trabajo en curso; elimina cuando termine.")
            return
        if not identifiers:
            messagebox.showinfo("Nada seleccionado", "Selecciona uno o varios resultados de la tabla.")
            return
        # Un mismo título no prueba parentesco. Nunca ampliar una selección por nombre.
        dependientes = pending_batches(DATA_ROOT, identifiers)
        if dependientes and not messagebox.askyesno(
                "Un lote de IA depende de esto",
                f"{len(dependientes)} de estos resultados alimentan un lote de IA sin terminar.\n"
                "Si los eliminas, ese lote ya no podrá reanudarse.\n\n¿Eliminar de todos modos?",
                icon="warning", default="no"):
            return
        if confirmar and not self._confirm_action(
                "Vaciar biblioteca completa" if all_results else "Eliminar resultados",
                f"Se enviarán a la Papelera {len(identifiers)} resultados.\n"
                + ("Incluye TODOS los documentos y páginas ocultas, aunque los filtros no los muestren.\n" if all_results else
                   "Solo se eliminan los resultados elegidos; otros con el mismo título se conservan.\n") +
                "Los documentos originales no se tocan. Para gestionar páginas ocultas, activa su filtro en Biblioteca.",
                f"Eliminar {len(identifiers)} resultados"):
            return
        self._run("eliminar_resultados", lambda: delete_documents(DATA_ROOT, identifiers), self._deleted)

    def _deleted(self, result: dict) -> None:
        hechos, fallidos = len(result["deleted"]), len(result["failed"])
        mensaje = f"{hechos} resultado(s) a la Papelera · se recuperan restaurándolos y con Reparación segura"
        if fallidos:
            mensaje += f" · {fallidos} NO se pudieron eliminar"
            messagebox.showwarning("Eliminación parcial", "\n".join(
                f"{item['id'][:12]}…  {item['error']}" for item in result["failed"][:8]))
        self.status_var.set(mensaje)

    def _confirm_action(self, title: str, detail: str, action_label: str) -> bool:
        """Confirmación cuyo botón NOMBRA la acción (guía de Microsoft): «Sí/Aceptar» se pulsa
        sin leer; «Eliminar 64 resultados» obliga a saber qué se acepta. Cancelar es el foco."""
        answer = {"ok": False}
        box = tk.Toplevel(self)
        box.title(title)
        box.transient(self)
        box.resizable(False, False)
        ttk.Label(box, text=detail, padding=(18, 16), wraplength=460, justify="left").pack()
        row = ttk.Frame(box, padding=(18, 0, 18, 16))
        row.pack(fill="x")

        def accept():
            answer["ok"] = True
            box.destroy()

        cancel = ttk.Button(row, text="Cancelar", command=box.destroy)
        cancel.pack(side="right")
        ttk.Button(row, text=action_label, command=accept).pack(side="right", padx=8)
        box.bind("<Escape>", lambda _event: box.destroy())
        cancel.focus_set()
        box.grab_set()
        self.wait_window(box)
        return answer["ok"]

    def open_selected_result(self) -> None:
        """Genera vista.html DENTRO del paquete y la abre en el NAVEGADOR.

        🔴 14-sep: antes abría `documento.md` con la aplicación asociada y, como `.md` no
        tiene asociación elegida, Windows lo mandaba a Antigravity IDE — que muestra el
        código fuente del Markdown. Gerardino: «no se ve bien, no sé el orden ni la
        estructura». El enganche va AQUÍ y no en `_open_path` a propósito: ese método
        abre también la guía, el índice y los informes, y desviarlo los rompería todos.
        """
        selected = self.result_tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona un resultado", "Selecciona un documento de la tabla.")
            return
        folder = Path(self.result_paths[selected[0]])
        try:
            vista = write_view(folder)          # 0 llamadas a IA: es un renderizador
            via = open_in_browser(vista)
            self.status_var.set("Vista abierta (%s) · %s" % (via, vista.name))
        except Exception as exc:
            failure = record_failure(DATA_ROOT, "vista_html", exc, {"folder": str(folder)})
            self.refresh_failures()
            self.status_var.set("Vista no disponible (%s); se abre documento.md" % failure["signature"])
            self._open_path(folder / "documento.md")     # el comportamiento de antes, intacto

    def show_structure(self) -> None:
        """La «ventanita»: la ESTRUCTURA del documento dentro del programa.

        Pedida por Gerardino: «que me abra una ventanita y vea cómo está ordenado, sobre
        todo la estructura». Usa el árbol que Tk ya trae: cero instalaciones. Doble clic
        en un nodo abre esa sección en el navegador.
        """
        selected = self.result_tree.selection()
        if not selected:
            messagebox.showinfo("Selecciona un resultado", "Selecciona un documento de la tabla.")
            return
        folder = Path(self.result_paths[selected[0]])
        try:
            texto = (folder / "documento.md").read_text(encoding="utf-8")
            datos = render_markdown(texto, folder=folder)
        except Exception as exc:
            failure = record_failure(DATA_ROOT, "estructura", exc, {"folder": str(folder)})
            self.refresh_failures()
            messagebox.showerror("Error diagnosticado", failure["message"])
            return

        popup = tk.Toplevel(self)
        popup.title("Estructura · %s" % folder.name[:12])
        popup.geometry("760x560")
        resumen = "%d secciones · %d hojas (%d ocultas) · %d tablas (%d anchas) · %d dudas" % (
            len(datos.toc), datos.hojas, datos.hojas_ocultas, datos.tablas,
            datos.tablas_anchas, sum(datos.marks.values()))
        ttk.Label(popup, text=resumen, padding=(12, 8)).pack(anchor="w")
        # El botón se lleva su sitio ANTES de que el árbol expandible talle la cavidad; si no,
        # queda en 1 px y no se ve NUNCA, ni con la ventana en su tamaño original. El árbol
        # pedía 20 filas de 30 px = 600 px dentro de un popup de 560.
        acciones = ttk.Frame(popup)
        acciones.pack(side="bottom", anchor="e", padx=12, pady=(0, 12))
        marco = ttk.Frame(popup, padding=(12, 0, 12, 12))
        marco.pack(fill="both", expand=True)
        arbol = ttk.Treeview(marco, columns=("detalle",), show="tree headings", height=12)
        arbol.heading("#0", text="Sección")
        arbol.heading("detalle", text="Detalle")
        arbol.column("#0", width=430, stretch=True)
        arbol.column("detalle", width=250, stretch=False)
        barra = ttk.Scrollbar(marco, orient="vertical", command=arbol.yview)
        arbol.configure(yscrollcommand=barra.set)
        arbol.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        padres = {1: ""}
        for t in datos.toc:
            etiqueta = ("    " * (t["nivel"] - 1)) + t["texto"]
            detalle = t["stats"] or ("oculta" if t["oculta"] else "")
            if t["oculta"]:
                detalle = ("oculta · " + detalle) if detalle else "oculta"
            padre = padres.get(t["nivel"] - 1, "")
            nodo = arbol.insert(padre, "end", iid=t["id"], text=etiqueta, values=(detalle,))
            padres[t["nivel"]] = nodo
        if datos.mark_items:
            dudas = arbol.insert("", "end", iid="__dudas__", text="Dudas del motor",
                                 values=("%d" % len(datos.mark_items),))
            for it in datos.mark_items[:200]:
                arbol.insert(dudas, "end", iid=it["id"],
                             text="    " + it["texto"], values=(it["seccion"][:40],))

        def abrir(_evento=None):
            sel = arbol.selection()
            if not sel or sel[0].startswith("__"):
                return
            try:
                vista = write_view(folder)
                open_in_browser(vista, sel[0])
                self.status_var.set("Vista abierta en %s" % sel[0])
            except Exception as exc:
                record_failure(DATA_ROOT, "vista_html", exc, {"folder": str(folder)})
                self.refresh_failures()

        arbol.bind("<Double-1>", abrir)
        ttk.Button(acciones, text="Abrir en el navegador", command=abrir).pack()

    def _open_path(self, path: Path) -> None:
        try:
            path = path.resolve(strict=True)
            os.startfile(path)  # Windows: usa la aplicación asociada por el usuario.
        except Exception as exc:
            failure = record_failure(DATA_ROOT, "abrir_ruta", exc, {"path": str(path)})
            self.refresh_failures()
            messagebox.showerror("Error diagnosticado",
                                 f"{failure['message']}\n\nFirma: {failure['signature']}\n{failure['remedy']}")


def main() -> None:
    try:
        app = ConversionApp()
        app.mainloop()
    except Exception as exc:
        failure = record_failure(DATA_ROOT, "inicio_aplicacion", exc)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Sistema MD no pudo iniciar",
                                 f"{failure['message']}\n\nFirma: {failure['signature']}\n{failure['remedy']}")
            root.destroy()
        except Exception:
            raise
        raise SystemExit(1)


if __name__ == "__main__":
    main()
