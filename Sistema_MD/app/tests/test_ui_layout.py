"""Regresiones de geometría: encabezado y estado a 125/150 % en ventana mínima."""
from pathlib import Path
import tkinter as tk
from unittest import TestCase
from unittest.mock import patch

from support import workspace_temp


class LayoutTests(TestCase):
    def exercise_layout(self, percent, geometry):
        from conversion.app import ConversionApp

        class ScaledApp(ConversionApp):
            def _configure_styles(self):
                # Tk usa píxeles por punto (96/72 a 100 %), no porcentaje Windows.
                self.tk.call("tk", "scaling", (96 / 72) * percent / 100)
                super()._configure_styles()

        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        with patch("conversion.app.DATA_ROOT", Path(fixture.name) / "datos"):
            app = ScaledApp()
            try:
                app.geometry(geometry)
                app.update()
                header = next(child for child in app.winfo_children()
                              if isinstance(child, tk.Frame) and child.cget("bg") == "#17233c")
                footer = next(child for child in app.winfo_children()
                              if isinstance(child, tk.Frame) and child.cget("bg") == "#dfe6ef")
                subtitle = header.winfo_children()[1]
                status = next(child for child in footer.winfo_children()
                              if isinstance(child, tk.Label))
                self.assertTrue(subtitle.winfo_ismapped(), "El subtítulo debe estar visible")
                self.assertGreaterEqual(subtitle.winfo_height(), subtitle.winfo_reqheight(),
                                        "El subtítulo se está recortando verticalmente")
                self.assertLessEqual(subtitle.winfo_y() + subtitle.winfo_height(), header.winfo_height())
                self.assertTrue(status.winfo_ismapped(), "El notebook no debe ocultar el pie de estado")
                self.assertGreaterEqual(status.winfo_height(), status.winfo_reqheight())
                self.assertLessEqual(footer.winfo_rooty() + footer.winfo_height(),
                                     app.winfo_rooty() + app.winfo_height())
                self.assertGreaterEqual(app.process_button.winfo_height(),
                                        app.process_button.winfo_reqheight())
                app.tabs.select(1)
                app.update()
                for button in app.result_action_buttons + [app.delete_result_button]:
                    self.assertTrue(button.winfo_ismapped())
                    self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
                    self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                         app.winfo_rootx() + app.winfo_width())
                    self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), footer.winfo_rooty())
            finally:
                for timer in app.tk.splitlist(app.tk.call("after", "info")):
                    app.after_cancel(timer)
                app.destroy()

    def test_minimum_window_at_125_percent(self):
        self.exercise_layout(125, "980x650")

    def test_minimum_window_at_150_percent(self):
        self.exercise_layout(150, "980x650")

    def test_normal_window_at_125_percent(self):
        self.exercise_layout(125, "1180x760")

    def test_normal_window_at_150_percent(self):
        self.exercise_layout(150, "1180x760")
