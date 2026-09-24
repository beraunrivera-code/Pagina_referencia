"""El cierre espera la exportación del trabajador, sin ejecutar diálogos finales."""
from pathlib import Path
from threading import Thread
from unittest import TestCase
from unittest.mock import Mock, patch

from conversion.pipeline import convert_text
from conversion.storage import verify_artifacts
from support import workspace_temp


class CloseExportTests(TestCase):
    def setUp(self):
        from conversion.app import ConversionApp
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = fixture.path / "datos"
        self.destination = fixture.path / "MD"
        self.patch_root = patch("conversion.app.DATA_ROOT", self.root)
        self.patch_destination = patch("conversion.app.md_export_dir", return_value=self.destination)
        self.patch_root.start()
        self.patch_destination.start()
        self.addCleanup(self.patch_root.stop)
        self.addCleanup(self.patch_destination.stop)
        self.app = ConversionApp()
        self.app.withdraw()
        self.app.update()
        self.addCleanup(self.close)

    def close(self):
        self.app.progress.stop()
        for timer in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(timer)
        self.app.destroy()

    def document(self, name):
        source = self.root.parent / (name + ".txt")
        source.write_text("Contenido testigo de " + name, encoding="utf-8")
        return convert_text(self.root, source)

    def worker_event(self, operation, result, callback=None):
        # Capturar el worker para solicitar cierre antes de que complete, sin dormir.
        with patch("conversion.app.threading.Thread") as factory:
            self.app._run(operation, lambda: result, callback)
        worker = Thread(target=factory.call_args.kwargs["target"])
        # Ningún método de presentación puede usarse desde este hilo.
        with patch.object(self.app.status_var, "set", side_effect=AssertionError("Tk en worker")), \
                patch.object(self.app, "_export_md", side_effect=AssertionError("exportador UI en worker")):
            worker.start()
            worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "el trabajador local debe terminar")
        return self.app.events.get_nowait()

    def close_on_event(self, event, expected_names):
        self.app.closing = True
        self.app.events.put(event)

        def verify_before_destroy():
            for name in expected_names:
                self.assertTrue((self.destination / (name + ".md")).is_file(),
                                "No cerrar antes de guardar el MD legible")

        with patch.object(self.app, "destroy", side_effect=verify_before_destroy) as destroy:
            self.app._drain_events()
        destroy.assert_called_once()

    def test_close_after_single_conversion_keeps_named_markdown(self):
        result = self.document("Uno")
        callback = Mock()
        event = self.worker_event("procesar_documento", result, callback)
        self.assertEqual(event[0], "success")
        self.assertNotIn("_ui_md_exports", result, "no modificar el resultado del núcleo")
        self.close_on_event(event, ["Uno"])
        callback.assert_not_called()
        self.assertTrue(verify_artifacts(Path(result["output"])))

    def test_close_after_local_batch_exports_only_finished_packages(self):
        first, second, pending = (self.document(name) for name in ("Uno", "Dos", "Pendiente"))
        result = {"items": [{"state": "terminado", "result": first},
                            {"state": "terminado", "result": second},
                            {"state": "listo", "result": pending}]}
        callback = Mock()
        event = self.worker_event("lote_local", result, callback)
        self.close_on_event(event, ["Uno", "Dos"])
        self.assertFalse((self.destination / "Pendiente.md").exists())
        callback.assert_not_called()

    def test_close_after_ai_batch_exports_combined_without_provider_calls(self):
        combined = self.document("Unido")
        result = {"combined": combined, "items": []}
        callback = Mock()
        with patch("conversion.app.run_ai_batch", side_effect=AssertionError("no API")):
            event = self.worker_event("lote_ia", result, callback)
        self.close_on_event(event, ["Unido"])
        callback.assert_not_called()

    def test_export_failure_preserves_package_and_records_diagnostic(self):
        from conversion.diagnostics import list_failures
        result = self.document("Conservado")
        with patch("conversion.app.export_markdown", side_effect=PermissionError("destino fixture bloqueado")):
            event = self.worker_event("procesar_documento", result, Mock())
        self.assertEqual(event[0], "success", "no deshacer una conversión por fallar su copia legible")
        self.assertTrue(verify_artifacts(Path(result["output"])))
        self.assertTrue(any(item["operation"] == "exportar_md" for item in list_failures(self.root)))
        self.assertIsNone(event[2]["_ui_md_exports"][0]["path"])
        self.close_on_event(event, [])

    def test_normal_callback_reuses_worker_export_without_a_second_attempt(self):
        from conversion.storage import export_markdown
        result = self.document("UnaCopia")
        with patch("conversion.app.export_markdown", wraps=export_markdown) as export:
            event = self.worker_event("procesar_documento", result, self.app._processed)
            self.app.events.put(event)
            with patch("conversion.app.messagebox.showinfo"):
                self.app._drain_events()
        export.assert_called_once()
        self.assertTrue((self.destination / "UnaCopia.md").is_file())

    def test_failed_worker_export_is_not_silently_retried_in_callback(self):
        result = self.document("SinCopia")
        with patch("conversion.app.export_markdown", side_effect=PermissionError("bloqueado")) as export:
            event = self.worker_event("procesar_documento", result, self.app._processed)
            self.app.events.put(event)
            with patch("conversion.app.messagebox.showinfo"):
                self.app._drain_events()
        export.assert_called_once()
        self.assertTrue(verify_artifacts(Path(result["output"])))

    def test_unrelated_operation_does_not_export(self):
        with patch("conversion.app.export_markdown") as export:
            event = self.worker_event("consultar_memoria", {"hits": []})
        self.assertEqual(event[0], "success")
        export.assert_not_called()

    def test_one_failed_batch_export_does_not_skip_other_finished_packages(self):
        from conversion.storage import export_markdown
        first, second = self.document("Bloqueado"), self.document("Disponible")
        result = {"items": [{"state": "terminado", "result": first},
                            {"state": "terminado", "result": second}]}

        def export(root, identifier, destination):
            if identifier == first["id"]:
                raise PermissionError("destino de una pieza bloqueado")
            return export_markdown(root, identifier, destination)

        with patch("conversion.app.export_markdown", side_effect=export):
            event = self.worker_event("lote_local", result)
        self.assertEqual(event[0], "success")
        self.assertIsNone(event[2]["_ui_md_exports"][0]["path"])
        self.close_on_event(event, ["Disponible"])
        self.assertTrue(verify_artifacts(Path(first["output"])))
        self.assertTrue(verify_artifacts(Path(second["output"])))
