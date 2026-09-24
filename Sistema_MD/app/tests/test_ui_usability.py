"""Recorridos de usuario aislados: biblioteca, formularios y consentimiento sin red."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest import TestCase
from unittest.mock import patch

from support import workspace_temp


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class UsabilityTests(TestCase):
    def setUp(self):
        from conversion.app import ConversionApp
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name) / "datos"
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

    def rows(self):
        return [dict(id=ident, title=title, status=status, integrity_ok=ok,
                     output=str(self.root / ident), updated="2026-09-20T10:00:00", kind=kind)
                for ident, title, status, ok, kind in (
                    ("a", "Contrato de obra", "revisar", True, "text"),
                    ("b", "Manual", "revisar", False, "text"),
                    ("c", "Contrato de obra", "revisar", True, "structured_response"))]

    def test_empty_states_and_contextual_actions(self):
        self.assertIn("cola está vacía", self.app.queue_hint_var.get())
        self.assertIn("biblioteca está vacía", self.app.library_hint_var.get())
        self.assertTrue(self.app.process_button.instate(["disabled"]))
        self.assertTrue(all(button.instate(["disabled"]) for button in self.app.result_action_buttons))
        self.assertTrue(self.app.delete_result_button.instate(["disabled"]))

    def test_library_filters_are_local_and_keep_hidden_pages_explicit(self):
        with patch("conversion.app.report", return_value=self.rows()), patch("conversion.app.execute_job") as send:
            self.app.refresh_results()
            self.assertEqual(self.app.result_tree.get_children(), ("a", "b"))
            self.assertIn("1 página", self.app.hidden_pages_var.get())
            self.app.title_filter_var.set("CONTRATO")
            self.app._apply_result_filter()
            self.assertEqual(self.app.result_tree.get_children(), ("a",))
            self.app.title_filter_var.set("")
            self.app.result_filter_var.set("Dañados")
            self.app.refresh_results()
            self.assertEqual(self.app.result_tree.get_children(), ("b",))
            self.app.result_filter_var.set("Por revisar")
            self.app.show_pages_var.set(True)
            self.app.refresh_results()
            self.assertEqual(len(self.app.result_tree.get_children()), 3)
        send.assert_not_called()

    def test_selection_updates_actions_and_ai_context(self):
        with patch("conversion.app.report", return_value=self.rows()):
            self.app.refresh_results()
        self.app.result_tree.selection_set("a")
        self.app._result_selection_changed()
        self.assertIn("Contrato de obra", self.app.ai_selection_var.get())
        self.assertTrue(all(button.instate(["!disabled"]) for button in self.app.result_action_buttons))
        self.app.result_tree.selection_set(("a", "b"))
        self.app._result_selection_changed()
        self.assertTrue(all(button.instate(["disabled"]) for button in self.app.result_action_buttons))
        self.assertTrue(self.app.delete_result_button.instate(["!disabled"]))

    def test_same_title_is_not_destructive_relationship(self):
        with patch("conversion.app.report", return_value=self.rows()):
            self.app.refresh_results()
            self.app.result_tree.selection_set("a")
            with patch("conversion.app.pending_batches", return_value=[]), \
                    patch.object(self.app, "_confirm_action", return_value=True), \
                    patch("conversion.app.delete_documents", return_value={}) as delete, \
                    patch.object(self.app, "_run") as runner:
                self.app.delete_selected_results()
                runner.call_args.args[1]()
        delete.assert_called_once_with(self.root, ["a"])

    def test_delete_cancel_preserves_selection_and_never_runs(self):
        with patch("conversion.app.pending_batches", return_value=[]), \
                patch.object(self.app, "_confirm_action", return_value=False), \
                patch.object(self.app, "_run") as run:
            self.app._delete_results(["a"], confirmar=True)
        run.assert_not_called()

    def test_sections_preserve_values_and_keyboard_search_navigates(self):
        self.app.model_var.set("modelo-de-control")
        section = self.app.settings_sections["connection"]
        section.toggle()
        self.assertFalse(section.expanded)
        section.button.invoke()
        self.assertTrue(section.expanded)
        self.assertEqual(self.app.model_var.get(), "modelo-de-control")
        self.app.focus_search()
        self.app.update()
        self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 1)
        self.assertEqual(self.app.location_var.get(), "Biblioteca")

    def test_every_primary_library_action_visible_at_minimum_size(self):
        self.app.geometry("980x650")
        self.app.tabs.select(1)
        self.app.deiconify()
        self.app.update()
        for button in self.app.result_action_buttons + [self.app.delete_result_button]:
            self.assertTrue(button.winfo_ismapped())
            self.assertGreater(button.winfo_height(), 24)
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                 self.app.winfo_rootx() + self.app.winfo_width())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                 self.app.winfo_rooty() + self.app.winfo_height())

    def test_combobox_wheel_scrolls_without_changing_provider(self):
        self.app.geometry("980x650")
        self.app.tabs.select(3)
        for section in self.app.settings_sections.values():
            section.set_expanded(True)
        self.app.deiconify()
        self.app.update()
        provider = self.app.provider_var.get()
        combo = next(widget for widget in descendants(self.app.settings_sections["connection"])
                     if isinstance(widget, ttk.Combobox))
        self.app.settings_canvas.yview_moveto(0)
        combo.event_generate("<MouseWheel>", delta=-120)
        self.app.update()
        self.assertEqual(self.app.provider_var.get(), provider)
        self.assertGreater(self.app.settings_canvas.yview()[0], 0)
        self.app.settings_canvas.yview_moveto(1)
        self.app.update()
        self.assertAlmostEqual(self.app.settings_canvas.yview()[1], 1.0)

    def _visual_choice(self, action):
        captured = {}

        def handle():
            box = next(widget for widget in self.app.winfo_children() if isinstance(widget, tk.Toplevel))
            children = list(descendants(box))
            accept = next(widget for widget in children if isinstance(widget, ttk.Button)
                          and widget.cget("text") == "Preparar vista previa")
            captured["initially_disabled"] = accept.instate(["disabled"])
            if action == "cancel":
                next(widget for widget in children if isinstance(widget, ttk.Button)
                     and widget.cget("text").startswith("Cancelar")).invoke()
            elif action == "close":
                box.destroy()
            else:
                next(widget for widget in children if isinstance(widget, ttk.Radiobutton)
                     and widget.cget("value") == action).invoke()
                accept.invoke()

        self.app.after(50, handle)
        result = self.app._choose_visual_pages([1, 2, 3], [2], "gemini-api", "control")
        self.assertTrue(captured["initially_disabled"])
        return result

    def test_visual_dialog_cancel_and_close_return_no_scope(self):
        self.assertIsNone(self._visual_choice("cancel"))
        self.assertIsNone(self._visual_choice("close"))

    def test_visual_dialog_requires_named_scope(self):
        self.assertEqual(self._visual_choice("all"), [1, 2, 3])
        self.assertEqual(self._visual_choice("candidates"), [2])

    def test_cancel_after_local_conversion_never_plans_or_sends(self):
        self.app.model_var.set("control")
        with patch.object(self.app, "_export_md", return_value=self.root / "local.md") as export, \
                patch.object(self.app, "_paginas_para_vision", return_value=([1, 2], [2])), \
                patch.object(self.app, "_choose_visual_pages", return_value=None), \
                patch.object(self.app, "_run") as run, patch("conversion.app.run_ai_batch") as send:
            self.app._processed({"status": "revisar", "output": str(self.root / "paquete")})
        export.assert_called_once()
        run.assert_not_called()
        send.assert_not_called()
        self.assertIn("0 llamadas", self.app.status_var.get())

    def test_visual_choice_only_prepares_and_preserves_user_budgets(self):
        self.app.model_var.set("control")
        self.app.ai_calls_var.set("7")
        self.app.ai_token_budget_var.set("14000")
        with patch.object(self.app, "_export_md"), \
                patch.object(self.app, "_paginas_para_vision", return_value=([1, 2], [2])), \
                patch.object(self.app, "_choose_visual_pages", return_value=[2]), \
                patch.object(self.app, "_run") as run, \
                patch("conversion.app.plan_ai_batch", return_value={}) as plan, \
                patch("conversion.app.run_ai_batch") as send:
            self.app._processed({"status": "revisar", "output": str(self.root / "paquete")})
            self.assertEqual(run.call_args.args[0], "planificar_lectura_visual")
            self.assertFalse(run.call_args.kwargs.get("external", False))
            run.call_args.args[1]()
        self.assertEqual(plan.call_args.kwargs["pages"], [2])
        self.assertEqual(plan.call_args.kwargs["call_budget"], 7)
        self.assertEqual(plan.call_args.kwargs["reported_token_budget"], 14000)
        send.assert_not_called()

    def test_missing_model_never_chooses_one_or_sends(self):
        with patch.object(self.app, "_export_md"), \
                patch.object(self.app, "_paginas_para_vision", return_value=([1, 2], [2])), \
                patch("conversion.app.messagebox.showinfo"), \
                patch.object(self.app, "_choose_visual_pages") as choose, \
                patch.object(self.app, "_run") as run:
            self.app._processed({"status": "revisar", "output": str(self.root / "paquete")})
        choose.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.app.model_var.get(), "")

    def test_page_send_cancel_is_preview_only(self):
        from conversion.app import AI_MODE
        self.app.mode_var.set(AI_MODE)
        self.app.model_var.set("control")
        self.app.job_var.set(str(self.root / "encargo"))
        with patch("conversion.app.execute_job", return_value={"unit": 1}) as execute, \
                patch.object(self.app, "_confirm_action", return_value=False), \
                patch.object(self.app, "_run") as run:
            self.app.run_provider(True)
        execute.assert_called_once()
        self.assertFalse(execute.call_args.kwargs.get("send", False))
        run.assert_not_called()

    def test_invalid_number_is_explained_without_execution(self):
        from conversion.app import AI_MODE
        self.app.mode_var.set(AI_MODE)
        self.app.model_var.set("control")
        self.app.job_var.set(str(self.root / "encargo"))
        self.app.tokens_var.set("no es número")
        with patch("conversion.app.messagebox.showerror") as error, \
                patch("conversion.app.execute_job") as execute:
            self.app.run_provider(True)
        error.assert_called_once()
        execute.assert_not_called()

    def test_batch_send_cancel_is_preview_only(self):
        from conversion.app import AI_MODE
        self.app.mode_var.set(AI_MODE)
        self.app.ai_batch_var.set("control")
        preview = dict(title="Documento", items=[{"unit": 1}], policy=dict(
            provider="gemini-api", model="control", key_slot=1, cli_profile="principal",
            call_budget=1, reported_token_budget=1000))
        with patch("conversion.app.run_ai_batch", return_value=preview) as execute, \
                patch.object(self.app, "_confirm_action", return_value=False), \
                patch.object(self.app, "_run") as run:
            self.app.run_ai_plan(True)
        execute.assert_called_once_with(self.root, "control")
        run.assert_not_called()

    def test_clear_all_confirmation_declares_hidden_scope(self):
        with patch("conversion.app.report", return_value=self.rows()), \
                patch("conversion.app.pending_batches", return_value=[]), \
                patch.object(self.app, "_confirm_action", return_value=False) as confirmation, \
                patch.object(self.app, "_run") as run:
            self.app.clear_all_results()
        self.assertIn("TODOS", confirmation.call_args.args[1])
        self.assertIn("páginas ocultas", confirmation.call_args.args[1])
        run.assert_not_called()
