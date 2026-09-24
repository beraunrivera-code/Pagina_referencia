import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, Mock
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

from conversion.ui_widgets import NavigationHistory
from conversion.access import launch_login
from support import workspace_temp


class NavigationTests(TestCase):
    def test_history_branches_and_bounds(self):
        history = NavigationHistory(0)
        history.visit(1)
        history.visit(3)
        self.assertEqual(history.move(-1), 1)
        history.visit(2)
        self.assertEqual(history.items, [0, 1, 2])
        self.assertEqual(history.move(99), 2)
        self.assertEqual(history.move(-99), 0)

    def test_login_uses_official_cli_without_prompt_or_shell(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        if os.name != 'nt':
            self.skipTest('Acceso de Windows')
        with patch('conversion.access.cli_binary', return_value=['C:/Program Files/agy.exe']), patch('conversion.access.subprocess.Popen', return_value=SimpleNamespace(pid=42)) as launch:
            result = launch_login(Path(fixture.name), 'antigravity-cli')
        self.assertFalse(result['prompt_sent'])
        self.assertFalse(launch.call_args.kwargs['shell'])
        self.assertEqual(launch.call_args.args[0], ['C:/Program Files/agy.exe'])
        self.assertTrue(launch.call_args.kwargs['cwd'].is_relative_to(Path(fixture.name)))
        with self.assertRaises(ValueError):
            launch_login(Path(fixture.name), 'deepseek-api')

    def test_named_login_profile_is_isolated_outside_project(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        if os.name != 'nt':
            self.skipTest('Acceso de Windows')
        base = Path(fixture.name)
        local = base / 'local-app-data'
        environment = {'LOCALAPPDATA': str(local), 'PATH': 'fixture',
                       'DEEPSEEK_API_KEY': 'secret-fixture'}
        with patch.dict('conversion.providers.os.environ', environment, clear=True), \
                patch('conversion.access.cli_binary', return_value=['gemini.exe']), \
                patch('conversion.access.subprocess.Popen', return_value=SimpleNamespace(pid=43)) as launch:
            result = launch_login(base / 'datos', 'gemini-cli', 'pro-2')
        child = launch.call_args.kwargs['env']
        self.assertEqual(result['profile'], 'pro-2')
        self.assertNotIn('DEEPSEEK_API_KEY', child)
        self.assertTrue(Path(child['GEMINI_CLI_HOME']).is_relative_to(local))
        self.assertTrue(Path(child['GEMINI_CLI_HOME']).is_dir())
        self.assertFalse(Path(child['GEMINI_CLI_HOME']).is_relative_to(base / 'datos'))


class InterfaceTests(TestCase):
    def setUp(self):
        from conversion.app import ConversionApp
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name) / 'datos'
        scope = patch('conversion.app.DATA_ROOT', self.root)
        scope.start()
        self.addCleanup(scope.stop)
        self.app = ConversionApp()
        self.app.withdraw()
        self.app.update()
        self.addCleanup(self.close)

    def close(self):
        for timer in self.app.tk.splitlist(self.app.tk.call('after', 'info')):
            self.app.after_cancel(timer)
        self.app.destroy()

    def test_all_four_tables_have_two_scrollbars(self):
        for tree in (self.app.queue_tree, self.app.result_tree, self.app.health_tree, self.app.failure_tree):
            bars = [w for w in tree.master.winfo_children() if isinstance(w, ttk.Scrollbar)]
            self.assertEqual(len(bars), 2)
            self.assertTrue(tree.cget('yscrollcommand'))
            self.assertTrue(tree.cget('xscrollcommand'))

    def test_batch_controls_plan_confirm_and_pause(self):
        source = self.root.parent / 'nota.txt'
        source.write_text('Nota de prueba', encoding='utf-8')
        self.app.work_queue.add(source)
        from conversion.batches import plan_queue
        plan = plan_queue(self.root)
        self.app._local_result(plan)
        with patch('conversion.app.messagebox.askyesno', return_value=True), patch.object(self.app, '_run') as runner:
            self.app.run_local()
            self.assertEqual(runner.call_args.args[0], 'lote_local')
        self.app.pause_local()
        self.assertTrue(self.app.batch_stop.is_set())
        self.assertEqual(self.app.key_slot_var.get(), '1')

    def test_ai_batch_preview_and_pause_never_dispatch(self):
        self.app.ai_batch_var.set('a' * 64)
        fake = {'id': 'a' * 64, 'title': 'Control', 'state': 'listo', 'status': 'vista_previa',
                'counts': {'listo': 1}, 'spent_calls': 0, 'reported_tokens': 0,
                'unknown_calls': 0, 'items': [{'unit': 1, 'state': 'listo', 'result': None}],
                'notice': 'sin envío', 'report': str(self.root / 'informe.md'),
                'policy': {'provider': 'gemini-api', 'model': 'test', 'key_slot': 1,
                           'cli_profile': 'principal',
                           'call_budget': 1, 'reported_token_budget': 100,
                           'max_tokens': 512, 'timeout': 30}}
        with patch('conversion.app.run_ai_batch', return_value=fake) as run:
            self.app.run_ai_plan(False)
        run.assert_called_once_with(self.root, 'a' * 64)
        self.app.pause_ai()
        self.assertTrue(self.app.ai_stop.is_set())

    def test_actual_tab_back_forward_does_not_send(self):
        with patch('conversion.app.execute_job') as send:
            self.app.tabs.select(1)
            self.app.update()
            self.app.tabs.select(3)
            self.app.update()
            self.app.go_history(-1)
            self.app.update()
            self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 1)
            self.app.go_history(1)
            self.app.update()
            self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 3)
        send.assert_not_called()

    def test_mousewheel_scrolls_settings_without_changing_provider(self):
        # Tk no entrega eventos de ratón a una ventana retirada: control visible real.
        self.app.deiconify()
        self.app.tabs.select(3)
        self.app.update()
        canvas = self.app.settings_canvas
        canvas.configure(scrollregion=(0, 0, 900, 4000))
        canvas.yview_moveto(0)
        provider = self.app.provider_var.get()
        canvas.event_generate('<MouseWheel>', delta=-120)
        self.app.update()
        self.assertGreater(canvas.yview()[0], 0)
        self.assertEqual(self.app.provider_var.get(), provider)

    def test_login_cancel_does_not_launch(self):
        self.app.provider_var.set('antigravity-cli')
        with patch('conversion.app.messagebox.askyesno', return_value=False), patch('conversion.app.launch_login') as launch:
            self.app.connect_provider()
        launch.assert_not_called()

    def test_selected_cli_profile_reaches_login(self):
        from conversion.app import AI_MODE
        self.app.mode_var.set(AI_MODE)
        self.app.provider_var.set('gemini-cli')
        self.app.cli_profile_var.set('pro-3')
        answer = {'notice': 'abierto', 'profile': 'pro-3'}
        with patch('conversion.app.messagebox.askyesno', return_value=True), \
                patch('conversion.app.launch_login', return_value=answer) as launch:
            self.app.connect_provider()
        launch.assert_called_once_with(self.root, 'gemini-cli', 'pro-3')
        self.assertEqual(self.app.access_var.get(), 'abierto')

    def test_api_ignores_stale_cli_profile_in_preview(self):
        self.app.provider_var.set('gemini-api')
        self.app.cli_profile_var.set('pro-3')
        self.app.model_var.set('modelo-prueba')
        self.app.job_var.set(str(self.root / 'encargo'))
        preview = {'unit': 1}
        with patch('conversion.app.execute_job', return_value=preview) as execute, \
                patch('conversion.app.messagebox.showinfo'):
            self.app.run_provider(False)
        self.assertEqual(execute.call_args.kwargs['cli_profile'], 'principal')

    def test_results_overflow_scrolls_both_axes_at_minimum_window(self):
        self.app.geometry('980x650')
        self.app.deiconify()
        self.app.tabs.select(1)
        tree = self.app.result_tree
        for number in range(100):
            tree.insert('', 'end', values=(f'Documento {number}', 'REVISAR', 'INTEGRO', 'hoy'))
        self.app.update()
        tree.event_generate('<MouseWheel>', delta=-120)
        self.app.update()
        self.assertGreater(tree.yview()[0], 0)
        tree.xview_moveto(1)
        self.app.update()
        self.assertGreater(tree.xview()[0], 0)

    def test_help_and_history_open_without_network(self):
        with patch('conversion.app.execute_job') as send:
            self.app.show_help()
            self.app.show_runs()
            self.app.update()
        send.assert_not_called()
