"""Recorridos de conexiones y autorización simulados: cero red ni login real."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest import TestCase
from unittest.mock import Mock, patch

from conversion.app import AI_MODE, LOCAL_MODE, ConversionApp
from conversion.connection_settings import load_settings, save_settings
from conversion.credentials import ENV_NAMES
from conversion.providers import profiles_for
from support import workspace_temp


class UiConnectionsTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name) / "datos"
        scope = patch("conversion.app.DATA_ROOT", self.root)
        scope.start()
        self.addCleanup(scope.stop)
        self.apps = []
        self.addCleanup(self.close)
        self.app = self.new_app()

    def new_app(self):
        app = ConversionApp()
        app.withdraw()
        app.update()
        self.apps.append(app)
        return app

    def close(self):
        for app in reversed(self.apps):
            app.progress.stop()
            for timer in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(timer)
            app.destroy()

    def test_default_local_blocks_page_batch_login_and_official_page(self):
        self.assertEqual(self.app.mode_var.get(), LOCAL_MODE)
        self.assertIn("GPT CODE", self.app.title())
        self.app.provider_var.set("gemini-cli")
        self.app.model_var.set("control")
        self.app.job_var.set(str(self.root / "job"))
        self.app.ai_batch_var.set("batch")
        with patch("conversion.app.execute_job") as page, patch("conversion.app.run_ai_batch") as batch, \
                patch("conversion.app.launch_login") as login, patch("webbrowser.open") as browser:
            self.app.run_provider(True)
            self.app.run_ai_plan(True)
            self.app.connect_provider()
            self.app.open_provider_help()
        for action in (page, batch, login, browser):
            action.assert_not_called()
        self.assertIn("bloqueado por modo Local", self.app.status_var.get())

    def test_guard_covers_any_external_worker_without_blocking_local_work(self):
        callback = Mock()
        with patch("conversion.app.threading.Thread") as worker:
            self.app._run("typesafe_relevancia", callback, external=True)
            worker.assert_not_called()
            self.app._run("conversion_local", callback)
            worker.assert_called_once()
        callback.assert_not_called()

    def test_persistence_is_explicit_and_permission_never_survives_reopen(self):
        self.app.provider_var.set("openai-api")
        self.app.model_var.set("modelo-control")
        self.app.key_slot_var.set("5")
        self.app.mode_var.set(AI_MODE)
        self.assertFalse((self.root / "conexiones.json").exists())
        self.app.save_connection_selection()
        reopened = self.new_app()
        self.assertEqual(reopened.provider_var.get(), "openai-api")
        self.assertEqual(reopened.model_var.get(), "modelo-control")
        self.assertEqual(reopened.key_slot_var.get(), "5")
        self.assertEqual(reopened.mode_var.get(), LOCAL_MODE)
        self.assertIn("no verificados", reopened.access_var.get())

    def test_models_and_slots_are_per_provider_budgets_stay_untouched(self):
        self.app.model_var.set("gemini-control")
        self.app.key_slot_var.set("4")
        self.app.ai_calls_var.set("7")
        self.app.ai_token_budget_var.set("1300")
        self.app.provider_var.set("anthropic-api")
        self.assertEqual(self.app.model_var.get(), "")
        self.app.model_var.set("claude-control")
        self.app.key_slot_var.set("5")
        self.app.provider_var.set("gemini-api")
        self.assertEqual(self.app.model_var.get(), "gemini-control")
        self.assertEqual(self.app.key_slot_var.get(), "4")
        self.app.provider_var.set("anthropic-api")
        self.assertEqual(self.app.model_var.get(), "claude-control")
        self.assertEqual(self.app.key_slot_var.get(), "5")
        self.assertEqual(self.app.ai_calls_var.get(), "7")
        self.assertEqual(self.app.ai_token_budget_var.get(), "1300")

    def test_profiles_follow_provider_and_fab_not_offered_to_other_clis(self):
        control = self.app.connection_controls["Perfil de cuenta CLI"]
        for provider in ("gemini-cli", "codex-cli", "claude-cli", "antigravity-cli"):
            self.app.provider_var.set(provider)
            self.assertEqual(tuple(control.cget("values")), tuple(profiles_for(provider)))
            self.assertEqual("fab5" in control.cget("values"), provider == "antigravity-cli")
        self.app.cli_profile_var.set("fab5")
        self.assertIn("no se copian", self.app.connection_hint_var.get())

    def test_all_api_fifth_slots_reach_save_and_page_preview(self):
        for provider in ("gemini-api", "deepseek-api", "openai-api", "anthropic-api", "qwen-api"):
            self.app.provider_var.set(provider)
            self.app.key_slot_var.set("5")
            self.app.model_var.set("control")
            self.app.job_var.set(str(self.root / "job"))
            with patch("conversion.app.simpledialog.askstring", return_value="fixture-secret"), \
                    patch("conversion.app.save_key") as save, \
                    patch("conversion.app.execute_job", return_value={"unit": 1}) as execute, \
                    patch("conversion.app.messagebox.showinfo"):
                self.app.configure_key()
                self.app.run_provider(False)
            save.assert_called_once_with(provider, "fixture-secret", 5)
            self.assertEqual(execute.call_args.kwargs["key_slot"], 5)
            self.assertFalse(execute.call_args.kwargs.get("send", False))

    def test_all_api_fifth_slots_reach_batch_planning(self):
        self.app.result_tree.insert("", "end", iid="fixture", values=("Doc", "revisar", "ok", "hoy"))
        self.app.result_tree.selection_set("fixture")
        self.app.result_paths["fixture"] = str(self.root / "fixture")
        for provider in ("gemini-api", "deepseek-api", "openai-api", "anthropic-api", "qwen-api"):
            self.app.provider_var.set(provider)
            self.app.key_slot_var.set("5")
            self.app.model_var.set("control")
            with patch.object(self.app, "_run") as runner, patch("conversion.app.plan_ai_batch") as plan:
                self.app.ai_from_result()
                runner.call_args.args[1]()
            self.assertEqual(plan.call_args.kwargs["key_slot"], 5)
            self.assertEqual(plan.call_args.args[2], provider)

    def test_mode_change_during_work_is_refused(self):
        self.app.busy = True
        self.app.mode_var.set(AI_MODE)
        self.app._mode_changed()
        self.assertEqual(self.app.mode_var.get(), LOCAL_MODE)

    def test_typesafe_open_in_ai_then_local_cannot_send(self):
        from conversion.semantic_ui import open_ranking_dialog
        self.app.mode_var.set(AI_MODE)
        local = {"query": "control", "hits": [{"unit": 1, "excerpt": "control", "markdown": "local.md"}]}
        preview = dict(candidates=1, privacy_notice="control", cost_notice="control", notice="control",
                       endpoint="https://api.typesafe.ai/control", body={}, id="fixture")
        with patch("conversion.semantic_ui.preview", return_value=preview):
            dialog = open_ranking_dialog(self.app, self.root, local)
        self.app.mode_var.set(LOCAL_MODE)
        buttons = [widget for frame in dialog.winfo_children() for widget in frame.winfo_children()
                   if isinstance(widget, ttk.Button)]
        send = next(button for button in buttons if button.cget("text").startswith("Enviar estos"))
        with patch("conversion.semantic_ui.messagebox.askyesno", return_value=True), \
                patch("conversion.semantic_ui.rerank") as dispatch, patch("conversion.app.threading.Thread") as worker:
            send.invoke()
        dispatch.assert_not_called()
        worker.assert_not_called()
        self.assertIn("bloqueado", self.app.status_var.get())

    def test_corrupt_preferences_do_not_restore_mode_or_model(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "conexiones.json").write_text('{"send": true, "model":"bad"}', encoding="utf-8")
        reopened = self.new_app()
        self.assertEqual(reopened.mode_var.get(), LOCAL_MODE)
        self.assertEqual(reopened.model_var.get(), "")
        self.assertIn("No se pudieron cargar", reopened.access_var.get())

    def test_data_folder_selection_only_applies_on_next_start(self):
        from conversion.app import APP_ROOT
        target = self.root.parent / "otra-biblioteca"
        with patch("conversion.app.filedialog.askdirectory", return_value=str(target)), \
                patch("conversion.app.messagebox.askyesno", return_value=True), \
                patch("conversion.runtime_paths.configure_data_root") as configure:
            self.app.choose_data_root()
        configure.assert_called_once_with(APP_ROOT, target)
        self.assertIn("próxima apertura", self.app.status_var.get())
        from conversion.app import DATA_ROOT
        self.assertEqual(DATA_ROOT, self.root)
        self.assertFalse(target.exists())

    def test_cancel_data_folder_does_not_modify_configuration(self):
        with patch("conversion.app.filedialog.askdirectory", return_value=str(self.root)), \
                patch("conversion.app.messagebox.askyesno", return_value=False), \
                patch("conversion.runtime_paths.configure_data_root") as configure:
            self.app.choose_data_root()
        configure.assert_not_called()
