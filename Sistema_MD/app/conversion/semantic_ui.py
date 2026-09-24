"""Vista previa explícita para TypeSafe; no modifica ni convierte documentos."""
import json
from tkinter import messagebox, ttk
import tkinter as tk

from .semantic_judgments import preview, rerank, DEFAULT_MODEL
from .ui_widgets import text_view


def readable_result(result):
    ranking = result["ranking"]
    receipt = ranking.get("receipt", {})
    usage = receipt.get("usage", {})
    lines = ["RESULTADOS · " + ranking["status"], ranking.get("notice", ""),
             f"Llamadas en este intento: {result['external_calls']}. Caché: {'sí' if ranking.get('cached') else 'no'}.",
             "Tokens reportados: entrada " + str(usage.get("input_tokens") if usage.get("input_tokens") is not None else "desconocidos")
             + " · salida " + str(usage.get("output_tokens") if usage.get("output_tokens") is not None else "desconocidos"), ""]
    for index, hit in enumerate(result["hits"], 1):
        relevance = hit.get("relevance")
        score = f" · relevancia {relevance['score']:.2f}/2" if relevance else " · orden local conservado"
        lines.extend([f"{index}. {hit.get('title', 'Documento')} · unidad {hit['unit']}" + score,
                      hit["excerpt"], "Fuente: " + hit["markdown"],
                      "Fragmento parcial: " + ("sí" if hit.get("excerpt_truncated") else "no"), ""])
    lines.extend(["Relevancia NO certifica fidelidad, integridad ni vigencia.",
                  "Identificador del recibo: " + str(receipt.get("id", "sin recibo"))])
    return "\n".join(lines)


def open_ranking_dialog(parent, root, local):
    plan = preview(local)
    dialog = tk.Toplevel(parent)
    dialog.title("TypeSafe · Revisar antes de enviar")
    dialog.geometry("860x660")
    dialog.minsize(650, 440)
    dialog.transient(parent)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    heading = ttk.Frame(dialog, padding=16)
    heading.pack(fill="x")
    ttk.Label(heading, text="Ordenar fragmentos por relevancia", font=("Segoe UI Semibold", 15)).pack(anchor="w")
    ttk.Label(heading, text=f"{plan['candidates']} fragmentos · {DEFAULT_MODEL} · máximo 1 llamada",
              wraplength=760).pack(anchor="w", pady=(6, 3))
    ttk.Label(heading, text=plan["privacy_notice"] + "\n" + plan["cost_notice"],
              wraplength=760).pack(anchor="w")
    status = tk.StringVar(value="Vista previa local: todavía no se ha enviado nada.")
    ttk.Label(heading, textvariable=status, wraplength=760).pack(anchor="w", pady=(10, 0))
    heading.bind("<Configure>", lambda event: [label.configure(wraplength=max(280, event.width - 32))
                 for label in heading.winfo_children() if isinstance(label, ttk.Label)])
    buttons = ttk.Frame(dialog, padding=12)
    buttons.pack(side="bottom", fill="x")
    tabs = ttk.Notebook(dialog)
    tabs.pack(fill="both", expand=True, padx=12)
    readable = ttk.Frame(tabs)
    technical = ttk.Frame(tabs)
    tabs.add(readable, text="Fragmentos a enviar")
    tabs.add(technical, text="Solicitud técnica")
    lines = ["CONSULTA: " + local["query"], "", plan["notice"], ""]
    for index, hit in enumerate(local["hits"], 1):
        lines.extend([f"{index}. {hit.get('title', 'Documento')} · unidad {hit['unit']}", hit["excerpt"],
                      "Fragmento parcial: " + ("sí" if hit.get("excerpt_truncated") else "no"), ""])
    lines.append("Se envían consulta, fragmentos e identificadores. Los títulos y rutas locales no se incluyen como campos en la solicitud.")
    text_view(readable, "\n".join(lines))
    text_view(technical, plan["endpoint"] + "\n\n" + plan["notice"] + "\n\n"
              + json.dumps(plan["body"], ensure_ascii=False, indent=2))

    def finished(result):
        # El usuario puede cerrar el panel mientras la tarea conserva su recibo.
        if not dialog.winfo_exists():
            return
        ranking = result["ranking"]
        status.set("Estado: " + ranking["status"] + " · llamadas en este intento: " + str(result["external_calls"]))
        output = tk.Toplevel(dialog)
        output.title("TypeSafe · Resultado y evidencia")
        output.geometry("850x600")
        text_view(output, readable_result(result))
        send_button.configure(state="disabled")
        close_button.configure(text="Cerrar panel")

    def send():
        if parent.busy:
            status.set("Hay otra tarea en curso. Espera; aún no se envió esta consulta.")
            return
        if not parent._allow_external("Enviar a TypeSafe"):
            status.set("Bloqueado por modo Local. Cambia a Con IA en la ventana principal y revisa antes de enviar.")
            return
        if not messagebox.askyesno("Autorizar una llamada a TypeSafe",
                f"¿Enviar los {plan['candidates']} fragmentos mostrados y la consulta a api.typesafe.ai?\n\n"
                "Puede consumir saldo API. El modelo solo ordena la relevancia; no certifica fidelidad.\n"
                "No se reintentará automáticamente si falla.", parent=dialog, default="no"):
            return
        send_button.configure(state="disabled")
        close_button.configure(text="Cerrar panel")
        status.set("Enviando una solicitud. Cerrar este panel no cancela el envío. Se guarda el recibo y no se reintenta.")
        parent._run("typesafe_relevancia", lambda: rerank(root, local, send=True,
                    expected_id=plan["id"]), finished, external=True)

    close_button = ttk.Button(buttons, text="Cerrar sin enviar", command=dialog.destroy)
    close_button.pack(side="left")
    send_button = ttk.Button(buttons, text="Enviar estos fragmentos a TypeSafe…", command=send)
    send_button.pack(side="right")
    if not plan["candidates"]:
        send_button.configure(state="disabled")
        status.set("No hay candidatos. Prueba términos más concretos en la consulta local.")
    return dialog
