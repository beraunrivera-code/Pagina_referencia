"""Recuperación tras errores y filtros sin releer todos los paquetes por pulsación."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from support import workspace_temp


class UiRecoveryTests(TestCase):
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
        source = self.root.parent / "control.txt"
        source.write_text("Control local sin IA", encoding="utf-8")
        self.app.work_queue.add(source, 1)
        self.app.refresh_queue()
        self.app.queue_tree.selection_set(str(source.resolve()))
        self.rows = [dict(id=ident, title=title, status="revisar", integrity_ok=ok,
                         output=str(self.root / ident), updated="2026-09-20T10:00:00", kind=kind)
                     for ident, title, ok, kind in (
                         ("a", "Contrato", True, "text"),
                         ("b", "Manual", False, "text"),
                         ("c", "Página", True, "structured_response"))]
        with patch("conversion.app.report", return_value=self.rows):
            self.app.refresh_results()
        self.app.result_tree.selection_set("a")
        self.app.update()
        self.app._queue_selection_changed()
        self.app._result_selection_changed()

    def close(self):
        self.app.progress.stop()
        for timer in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(timer)
        self.app.destroy()

    def start_operation(self):
        with patch("conversion.app.threading.Thread"):
            self.app._run("control", lambda: None)
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.process_button.instate(["disabled"]))
        self.assertTrue(self.app.delete_result_button.instate(["disabled"]))

    def dispatch(self, kind, callback=None):
        failure = {"signature": "fixture", "message": "fallo controlado", "remedy": "reintentar"}
        self.app.events.put((kind, "control", failure, callback))
        with patch("conversion.app.messagebox.showerror"), \
                patch("conversion.app.record_failure", return_value=failure), \
                patch.object(self.app, "refresh_all"), \
                patch.object(self.app, "refresh_failures"):
            self.app._drain_events()

    def assert_recovered(self):
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.process_button.instate(["!disabled"]))
        self.assertTrue(self.app.delete_result_button.instate(["!disabled"]))

    def test_worker_error_restores_selected_actions(self):
        self.start_operation()
        self.dispatch("error")
        self.assert_recovered()

    def test_success_restores_actions_even_without_a_refresh(self):
        self.start_operation()
        self.dispatch("success", Mock())
        self.assert_recovered()

    def test_callback_exception_does_not_leave_actions_disabled(self):
        self.start_operation()
        self.dispatch("success", Mock(side_effect=ValueError("fallo de presentación")))
        self.assert_recovered()

    def test_callback_starting_next_job_keeps_actions_disabled(self):
        self.start_operation()
        with patch("conversion.app.threading.Thread"):
            self.dispatch("success", lambda _result: self.app._run("siguiente", lambda: None))
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.process_button.instate(["disabled"]))
        self.assertTrue(self.app.delete_result_button.instate(["disabled"]))

    def test_filters_reuse_snapshot_without_integrity_scan(self):
        with patch("conversion.app.report", side_effect=AssertionError("no releer paquetes al filtrar")):
            self.app.title_filter_var.set("contrato")
            self.app._apply_result_filter()
            self.assertEqual(self.app.result_tree.get_children(), ("a",))
            self.app.title_filter_var.set("")
            self.app.result_filter_var.set("Dañados")
            self.app._apply_result_filter()
            self.assertEqual(self.app.result_tree.get_children(), ("b",))
            self.app.result_filter_var.set("Todos")
            self.app.show_pages_var.set(True)
            self.app._apply_result_filter()
            self.assertEqual(self.app.result_tree.get_children(), ("a", "b", "c"))

    def test_explicit_refresh_updates_integrity_snapshot(self):
        updated = [dict(row, integrity_ok=False) for row in self.rows]
        with patch("conversion.app.report", return_value=updated) as read:
            self.app.refresh_results()
        read.assert_called_once()
        self.app.result_filter_var.set("Dañados")
        with patch("conversion.app.report", side_effect=AssertionError("usar comprobación reciente")):
            self.app._apply_result_filter()
        self.assertEqual(self.app.result_tree.get_children(), ("a", "b"))
        self.assertEqual(self.app.result_tree.set("a", "integrity"), "DAÑADO")

    def test_completed_operation_refreshes_snapshot(self):
        self.start_operation()
        self.app.events.put(("success", "datos_modificados", {}, None))
        with patch("conversion.app.report", return_value=self.rows[:1]) as read:
            self.app._drain_events()
        read.assert_called_once()
        self.assertEqual(self.app.result_tree.get_children(), ("a",))
