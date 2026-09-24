"""Navegación y desplazamiento compartidos; ningún gesto ejecuta conversiones."""
import tkinter as tk
from tkinter import ttk


class NavigationHistory:
    def __init__(self, initial):
        self.items, self.position = [initial], 0

    def visit(self, page):
        if self.items[self.position] != page:
            self.items = self.items[:self.position + 1] + [page]
            self.position += 1

    def move(self, delta):
        self.position = max(0, min(len(self.items) - 1, self.position + delta))
        return self.items[self.position]


def scroll_table(tree):
    """El llamador empaqueta tree.master; barras siempre visibles en ambos ejes."""
    parent = tree.master
    tree.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)


def wheel_on_canvas(window, canvas):
    """Rueda limitada a descendientes del canvas; no roba eventos a otras tablas."""
    def wheel(event):
        child = event.widget
        while child is not None and child != canvas:
            child = getattr(child, "master", None)
        if child is None or canvas.yview() == (0.0, 1.0):
            return
        delta = getattr(event, "delta", 0)
        units = (-max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)) if delta else (-1 if event.num == 4 else 1)
        canvas.yview_scroll(units, "units")
        return "break"
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        window.bind(sequence, wheel, add="+")
    return wheel


class CollapsibleSection(ttk.Frame):
    """Opciones avanzadas accesibles por teclado, sin reconstruir ni perder valores."""
    def __init__(self, parent, title, *, expanded=False):
        super().__init__(parent, style="Panel.TFrame")
        self.title = title
        self.expanded = False
        self.button = ttk.Button(self, command=self.toggle, style="Section.TButton")
        self.button.pack(fill="x")
        self.body = ttk.Frame(self, style="Panel.TFrame", padding=(10, 8))
        self.set_expanded(expanded)

    def set_expanded(self, expanded):
        self.expanded = bool(expanded)
        self.button.configure(text=("▾  " if self.expanded else "▸  ") + self.title)
        if self.expanded:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()

    def toggle(self):
        self.set_expanded(not self.expanded)


def text_view(parent, text, *, height=18):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    widget = tk.Text(frame, wrap="word", height=height, font=("Segoe UI", 10), padx=12, pady=12)
    scroll_table(widget)
    widget.insert("end", text)
    widget.configure(state="disabled")
    return widget
