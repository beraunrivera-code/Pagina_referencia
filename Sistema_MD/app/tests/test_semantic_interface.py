"""Contrato entre botones, CLI y TypeSafe; todas las llamadas externas están simuladas."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tkinter import ttk
from unittest import TestCase
from unittest.mock import patch

from conversion.semantic_ui import open_ranking_dialog, readable_result
from support import workspace_temp


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


def local_result():
    return {"query": "contrato", "external_calls": 0, "hits": [
        {"document_id": "a" * 64, "unit": 1, "block_id": "b1", "title": "Control sintético",
         "excerpt": "El contrato define al responsable.", "markdown": "C:/privado/control.md"}]}


class SemanticUITests(TestCase):
    def setUp(self):
        from conversion.app import ConversionApp
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "datos"
        scope = patch("conversion.app.DATA_ROOT", self.root)
        scope.start()
        self.addCleanup(scope.stop)
        self.app = ConversionApp()
        self.app.withdraw()
        self.app.update()
        self.addCleanup(self.close)

    def close(self):
        for timer in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(timer)
        self.app.destroy()

    def test_preview_button_calls_valid_local_contract(self):
        self.app.query_var.set("contrato")
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.semantic_judgments._transport") as transport, \
                patch("conversion.semantic_judgments._key") as key:
            self.app.preview_typesafe()
            # Ejecutar también el callback de trabajo descubre errores de firma, no solo de mock.
            result = runner.call_args.args[1]()
            self.assertEqual(result["query"], "contrato")
            dialog_callback = runner.call_args.args[2]
            dialog_callback(result)
            self.app.update()
        key.assert_not_called()
        transport.assert_not_called()

    def test_empty_query_never_starts(self):
        with patch.object(self.app, "_run") as runner:
            self.app.preview_typesafe()
        runner.assert_not_called()
        self.assertIn("Escribe una consulta", self.app.library_hint_var.get())

    def test_cancel_preview_does_not_read_key_or_send(self):
        with patch("conversion.semantic_judgments._key") as key, \
                patch("conversion.semantic_ui.rerank") as rerank:
            dialog = open_ranking_dialog(self.app, self.root, local_result())
            next(w for w in children(dialog) if isinstance(w, ttk.Button)
                 and w.cget("text") == "Cerrar sin enviar").invoke()
        key.assert_not_called()
        rerank.assert_not_called()

    def test_no_on_confirmation_never_schedules_send(self):
        from conversion.app import AI_MODE
        self.app.mode_var.set(AI_MODE)
        dialog = open_ranking_dialog(self.app, self.root, local_result())
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.semantic_ui.messagebox.askyesno", return_value=False) as confirm:
            next(w for w in children(dialog) if isinstance(w, ttk.Button)
                 and w.cget("text").startswith("Enviar estos")).invoke()
        runner.assert_not_called()
        self.assertEqual(confirm.call_args.kwargs["default"], "no")

    def test_explicit_confirmation_schedules_exact_preview_once(self):
        from conversion.app import AI_MODE
        self.app.mode_var.set(AI_MODE)
        from conversion.semantic_judgments import preview
        local = local_result()
        dialog = open_ranking_dialog(self.app, self.root, local)
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.semantic_ui.rerank") as rerank, \
                patch("conversion.semantic_ui.messagebox.askyesno", return_value=True):
            button = next(w for w in children(dialog) if isinstance(w, ttk.Button)
                          and w.cget("text").startswith("Enviar estos"))
            button.invoke()
            self.assertTrue(button.instate(["disabled"]))
            runner.call_args.args[1]()
            self.assertTrue(runner.call_args.kwargs["external"])
        rerank.assert_called_once_with(self.root, local, send=True, expected_id=preview(local)["id"])

    def test_local_mode_blocks_before_confirmation_and_keeps_send_available(self):
        dialog = open_ranking_dialog(self.app, self.root, local_result())
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.semantic_ui.messagebox.askyesno") as confirm:
            button = next(w for w in children(dialog) if isinstance(w, ttk.Button)
                          and w.cget("text").startswith("Enviar estos"))
            button.invoke()
        confirm.assert_not_called()
        runner.assert_not_called()
        self.assertFalse(button.instate(["disabled"]))
        self.assertIn("bloqueado", self.app.status_var.get())


class SemanticCLITests(TestCase):
    def setUp(self):
        from conversion.pipeline import convert_text
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "datos"
        source = Path(temporary.name) / "Control.md"
        source.write_text("# Contrato\n\nEl contrato define al responsable.", encoding="utf-8")
        convert_text(self.root, source)

    def test_cli_preview_is_local_and_contains_candidate(self):
        from conversion.__main__ import main
        output = io.StringIO()
        with redirect_stdout(output), patch("conversion.semantic_judgments._key") as key, \
                patch("conversion.semantic_judgments._transport") as transport:
            code = main(["--salida", str(self.root), "reordenar-typesafe", "contrato"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertGreater(result["ranking"]["preview"]["candidates"], 0)
        self.assertEqual(result["external_calls"], 0)
        key.assert_not_called()
        transport.assert_not_called()

    def test_cli_missing_confirmation_never_sends(self):
        from conversion.__main__ import main
        with redirect_stdout(io.StringIO()), patch("conversion.semantic_judgments._transport") as transport:
            code = main(["--salida", str(self.root), "reordenar-typesafe", "contrato", "--enviar"])
        self.assertEqual(code, 1)
        transport.assert_not_called()

    def test_result_is_readable_with_source_and_fallback(self):
        result = local_result()
        result["ranking"] = {"status": "fallo", "notice": "Sin reintento"}
        text = readable_result(result)
        self.assertIn("orden local conservado", text)
        self.assertIn("Fuente: C:/privado/control.md", text)
        self.assertIn("NO certifica fidelidad", text)
