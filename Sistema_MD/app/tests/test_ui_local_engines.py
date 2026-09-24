"""Motores desde la app: navegación y contratos con workers simulados, cero IA."""
from pathlib import Path
from tkinter import ttk
from unittest import TestCase
from unittest.mock import patch

from conversion.app import ConversionApp, LOCAL_ENGINE_CHOICES, persist_operation_exports
from conversion.local_engines import LocalEngineError
from conversion.pipeline import convert_text
from support import workspace_temp


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class UiLocalEngineTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name) / "datos"
        self.destination = Path(fixture.name) / "MD"
        for target, value in (("conversion.app.DATA_ROOT", self.root),
                              ("conversion.app.md_export_dir", lambda: self.destination)):
            scope = patch(target, value)
            scope.start()
            self.addCleanup(scope.stop)
        settings = patch("conversion.local_engines.engine_configuration",
                         return_value={"python": None, "models_path": None})
        settings.start()
        self.addCleanup(settings.stop)
        self.app = ConversionApp()
        self.app.withdraw()
        self.app.update()
        self.addCleanup(self.close)

    def close(self):
        self.app.progress.stop()
        for timer in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(timer)
        self.app.destroy()

    def source(self, suffix=".txt"):
        path = self.root.parent / ("Control" + suffix)
        path.write_text("Contenido de control local", encoding="utf-8")
        self.app.work_queue.add(path)
        self.app.refresh_queue()
        self.app.queue_tree.selection_set(str(path.resolve()))
        return path

    def choose(self, engine):
        self.app.local_engine_var.set(next(label for label, name in LOCAL_ENGINE_CHOICES.items() if name == engine))

    def test_existing_native_action_still_uses_current_workflow(self):
        source = self.source()
        self.assertEqual(self.app.local_engine_var.get(), "Nativo actual")
        with patch.object(self.app, "_run") as runner, \
                patch.object(self.app.work_queue, "process", return_value={}) as native, \
                patch("conversion.local_engines.convert_with_engine") as engine:
            self.app.process_selected()
            runner.call_args.args[1]()
        native.assert_called_once_with(str(source.resolve()), None)
        engine.assert_not_called()

    def test_docling_empty_cancel_and_invalid_scope_do_not_start_worker(self):
        self.source(".pdf")
        self.choose("docling")
        for answer in (None, "", "1-21", "x", "0"):
            with self.subTest(answer=answer), \
                    patch("conversion.app.simpledialog.askstring", return_value=answer) as ask, \
                    patch("conversion.app.messagebox.showerror"), patch.object(self.app, "_run") as runner:
                self.app.process_selected()
            self.assertEqual(ask.call_args.kwargs["initialvalue"], "")
            runner.assert_not_called()

    def test_docling_forwards_exact_scope_and_remains_local(self):
        source = self.source(".pdf")
        self.choose("docling")
        fake = {"output": str(self.root / "fixture"), "status": "revisar"}
        with patch("conversion.app.simpledialog.askstring", return_value="2,5-6"), \
                patch.object(self.app, "_run") as runner, \
                patch("conversion.local_engines.convert_with_engine", return_value=fake) as engine, \
                patch.object(self.app.work_queue, "_finish") as finish, \
                patch("conversion.app.execute_job") as send:
            self.app.process_selected()
            self.assertEqual(runner.call_args.args[0], "procesar_documento")
            self.assertFalse(runner.call_args.kwargs.get("external", False))
            result = runner.call_args.args[1]()
        engine.assert_called_once_with(self.root, source.resolve(), "docling", pages=[2, 5, 6])
        finish.assert_called_once()
        self.assertEqual(result["local_engine"], "docling")
        send.assert_not_called()

    def test_markitdown_queue_pending_extension_can_use_explicit_engine(self):
        source = self.source(".epub")
        self.choose("markitdown")
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.local_engines.convert_with_engine", side_effect=LocalEngineError("engine_missing")) as engine, \
                patch.object(self.app.work_queue, "_finish") as finish, \
                patch("conversion.app.execute_job") as send:
            self.app.process_selected()
            with self.assertRaisesRegex(LocalEngineError, "no está instalado"):
                runner.call_args.args[1]()
        engine.assert_called_once_with(self.root, source.resolve(), "markitdown", pages=None)
        finish.assert_not_called()
        self.assertNotIn("output", self.app.work_queue.items[0])
        send.assert_not_called()

    def test_engine_failure_never_falls_back_to_native_or_api(self):
        self.source()
        self.choose("markitdown")
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.local_engines.convert_with_engine", side_effect=LocalEngineError("python_missing")), \
                patch.object(self.app.work_queue, "process") as native, \
                patch.object(self.app.work_queue, "_finish") as finish, \
                patch("conversion.app.execute_job") as send:
            self.app.process_selected()
            with self.assertRaisesRegex(LocalEngineError, "Falta el Python"):
                runner.call_args.args[1]()
        native.assert_not_called()
        finish.assert_not_called()
        send.assert_not_called()

    def test_published_result_reaches_queue_library_and_markdown_export(self):
        source = self.source()
        self.choose("markitdown")
        published = convert_text(self.root, source)
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.local_engines.convert_with_engine", return_value=published):
            self.app.process_selected()
            result = runner.call_args.args[1]()
        # La exportación ocurre en el contrato de worker, antes del callback de GUI.
        result = persist_operation_exports("procesar_documento", result)
        self.assertTrue((self.destination / "Control.md").is_file())
        self.app.work_queue.load()
        self.assertEqual(self.app.work_queue.items[0]["output"], published["output"])
        with patch("conversion.app.messagebox.showinfo"), patch("conversion.app.run_ai_batch") as send:
            self.app._local_engine_processed(result)
        self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 1)
        self.assertIn(published["output"], self.app.result_paths.values())
        self.assertTrue(self.app.result_tree.selection())
        send.assert_not_called()

    def test_probe_is_local_worker_operation_not_link_or_api(self):
        status = {"engines": [{"engine": "docling", "status": "python_missing"}], "external_calls": 0}
        with patch("conversion.local_engines.probe_engines", return_value=status) as probe, \
                patch.object(self.app, "_run") as runner, patch("webbrowser.open") as browser:
            self.app.show_local_engines()
            result = runner.call_args.args[1]()
            self.assertEqual(result, status)
            self.assertFalse(runner.call_args.kwargs.get("external", False))
        probe.assert_called_once_with()
        browser.assert_not_called()

    def test_configuration_cancel_does_not_write_or_start_worker(self):
        with patch("conversion.local_engines.configure_engine", create=True) as configure, \
                patch("conversion.local_engines.convert_with_engine") as convert:
            dialog = self.app.configure_local_engine()
            next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button)
                 and widget.cget("text") == "Cerrar").invoke()
        configure.assert_not_called()


        convert.assert_not_called()

    def test_configuration_validates_missing_paths_before_save(self):
        with patch("conversion.local_engines.configure_engine", create=True) as configure:
            dialog = self.app.configure_local_engine()
            next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button)
                 and widget.cget("text") == "Guardar rutas").invoke()
        configure.assert_not_called()

    def test_configuration_saves_explicit_paths_without_running_engine(self):
        executable, models = self.root.parent / "python.exe", self.root.parent / "modelos"
        with patch("conversion.local_engines.configure_engine", return_value={}) as configure, \
                patch("conversion.local_engines.convert_with_engine") as convert:
            dialog = self.app.configure_local_engine()
            entries = [widget for widget in descendants(dialog)
                       if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox)]
            entries[0].delete(0, "end")
            entries[1].delete(0, "end")
            entries[0].insert(0, str(executable))
            entries[1].insert(0, str(models))
            next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button)
                 and widget.cget("text") == "Guardar rutas").invoke()
        configure.assert_called_once_with("docling", executable, models)
        convert.assert_not_called()

    def test_configuration_reopens_with_saved_paths_without_worker(self):
        existing = {"python": str(self.root.parent / "python.exe"),
                    "models_path": str(self.root.parent / "modelos")}
        with patch("conversion.local_engines.engine_configuration", return_value=existing) as read, \
                patch("conversion.local_engines.convert_with_engine") as convert:
            dialog = self.app.configure_local_engine()
        entries = [widget for widget in descendants(dialog)
                   if isinstance(widget, ttk.Entry) and not isinstance(widget, ttk.Combobox)]
        self.assertEqual(entries[0].get(), existing["python"])
        self.assertEqual(entries[1].get(), existing["models_path"])
        read.assert_called_once_with("docling")
        convert.assert_not_called()

    def test_engine_controls_and_configuration_buttons_fit_minimum_window(self):
        self.app.geometry("980x650")
        self.app.deiconify()
        self.app.update()
        for control in (self.app.local_engine_selector, self.app.local_engine_config_button, self.app.process_button):
            self.assertTrue(control.winfo_ismapped())
            self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())
            self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                 self.app.winfo_rootx() + self.app.winfo_width())
        dialog = self.app.configure_local_engine()
        dialog.geometry("640x420")
        self.app.update()
        for control in descendants(dialog):
            if isinstance(control, ttk.Button):
                self.assertTrue(control.winfo_ismapped())
                self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())
                self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                     dialog.winfo_rootx() + dialog.winfo_width())
                self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                     dialog.winfo_rooty() + dialog.winfo_height())

    def test_changed_source_does_not_mark_queue_finished(self):
        self.source()
        self.choose("markitdown")
        result = {"output": str(self.root / "fixture"), "status": "revisar", "source_sha256": "f" * 64}
        with patch.object(self.app, "_run") as runner, \
                patch("conversion.local_engines.convert_with_engine", return_value=result), \
                patch.object(self.app.work_queue, "_finish") as finish:
            self.app.process_selected()
            with self.assertRaisesRegex(ValueError, "fuente cambió"):
                runner.call_args.args[1]()
        finish.assert_not_called()

    def test_engine_selection_does_not_mutate_provider_or_model(self):
        self.source()
        self.app.provider_var.set("anthropic-api")
        self.app.model_var.set("model-control")
        self.choose("docling")
        with patch("conversion.app.execute_job") as send:
            self.app._queue_selection_changed()
        self.assertEqual(self.app.provider_var.get(), "anthropic-api")
        self.assertEqual(self.app.model_var.get(), "model-control")
        self.assertIn("docling", self.app.queue_hint_var.get())
        send.assert_not_called()

    def test_export_folder_selection_saves_explicit_location_without_moving(self):
        from conversion.app import APP_ROOT
        with patch("conversion.app.filedialog.askdirectory", return_value=str(self.destination)), \
                patch("conversion.app.messagebox.askyesno", return_value=True), \
                patch("conversion.runtime_paths.configure_export_root") as configure:
            self.app.choose_export_root()
        configure.assert_called_once_with(APP_ROOT, self.root, self.destination)
        self.assertIn("No se movió", self.app.status_var.get())
        self.assertFalse(self.destination.exists())

    def test_cancel_export_folder_does_not_modify_configuration(self):
        with patch("conversion.app.filedialog.askdirectory", return_value=str(self.destination)), \
                patch("conversion.app.messagebox.askyesno", return_value=False), \
                patch("conversion.runtime_paths.configure_export_root") as configure:
            self.app.choose_export_root()
        configure.assert_not_called()


class LocalEngineLayoutTests(TestCase):
    def check_scale(self, percent):
        class ScaledApp(ConversionApp):
            def _configure_styles(self):
                self.tk.call("tk", "scaling", (96 / 72) * percent / 100)
                super()._configure_styles()

        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        with patch("conversion.app.DATA_ROOT", Path(fixture.name) / "datos"), \
                patch("conversion.local_engines.engine_configuration", return_value={"python": None, "models_path": None}):
            app = ScaledApp()
            try:
                app.geometry("980x650")
                app.update()
                for control in (app.local_engine_selector, app.local_engine_config_button, app.process_button):
                    self.assertTrue(control.winfo_ismapped())
                    self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())
                    self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                         app.winfo_rootx() + app.winfo_width())
                dialog = app.configure_local_engine()
                dialog.geometry("640x420")
                app.update()
                for control in descendants(dialog):
                    if isinstance(control, (ttk.Button, ttk.Entry, ttk.Label)):
                        self.assertTrue(control.winfo_ismapped())
                        self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())
                        self.assertLessEqual(control.winfo_rootx() + control.winfo_width(),
                                             dialog.winfo_rootx() + dialog.winfo_width())
                        self.assertLessEqual(control.winfo_rooty() + control.winfo_height(),
                                             dialog.winfo_rooty() + dialog.winfo_height())
            finally:
                for timer in app.tk.splitlist(app.tk.call("after", "info")):
                    app.after_cancel(timer)
                app.destroy()

    def test_controls_fit_at_125_percent(self):
        self.check_scale(125)

    def test_controls_fit_at_150_percent(self):
        self.check_scale(150)
