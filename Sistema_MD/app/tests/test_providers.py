import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import shutil
from unittest import TestCase
from unittest.mock import patch

from conversion.documents import digest
from conversion.jobs import prepare_job
from conversion.providers import execute_job, decode_response, _api, _cli, cli_environment
from conversion.storage import publish
from conversion.providers import run_history, _http
from conversion.provider_errors import ProviderFault, ProviderAttemptError
from conversion.diagnostics import record_failure
from urllib.error import HTTPError
from support import workspace_temp


class ProviderTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name) / 'datos'
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        doc = {'schema_version': 1, 'title': 'Testigo', 'expected_units': [1],
               'units': [{'number': 1, 'image_asset': 'imagenes/p1.png', 'image_sha256': digest(image),
                          'blocks': [{'id': 'p1-b1', 'kind': 'paragraph', 'text': 'dato'}]}]}
        package = publish(self.root, doc, {'imagenes/p1.png': image})
        task = prepare_job(self.root, Path(package['output']), 1)
        self.job = Path(task['folder'])
        self.answer = {'job_id': task['job_id'], 'unit': 1, 'blocks': [{'kind': 'paragraph', 'text': 'dato'}], 'warnings': []}
        self.payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(self.answer)}]}}],
                        'usageMetadata': {'totalTokenCount': 100}}

    def execute(self, **kwargs):
        return execute_job(self.root, self.job, 'gemini-api', 'modelo-prueba', **kwargs)

    def test_vision_model_list_admits_the_published_one_and_blocks_the_blind_one(self):
        # 2026-09-13: el adaptador exigía 'deepseek-v4-flash-vision-exp', que ya no aparece en
        # /models, así que la vía de imagen de DeepSeek era inalcanzable. Medido contra una
        # página de control: 'deepseek-flash' acierta los 5 campos; 'deepseek-v4-pro' devuelve
        # contenido vacío. La lista vive en VISION_MODELS y un modelo sin comprobar no pasa.
        for modelo in ('deepseek-flash', 'deepseek-v4-flash-vision-exp'):
            result = execute_job(self.root, self.job, 'deepseek-api', modelo)
            self.assertEqual(result['status'], 'vista_previa')
            self.assertEqual(result['external_calls'], 0)
        with self.assertRaises(ValueError):
            execute_job(self.root, self.job, 'deepseek-api', 'deepseek-v4-pro')

    def test_empty_content_from_reasoning_budget_is_named_not_a_json_error(self):
        # El modelo gasta el presupuesto razonando y devuelve content vacío con finish_reason
        # 'stop': sin este control el fallo salía como JSON inválido y mandaba a buscar donde no era.
        payload = {'choices': [{'finish_reason': 'stop',
                                'message': {'content': '', 'reasoning_content': 'x' * 4106}}],
                   'usage': {'total_tokens': 500}}
        with self.assertRaises(ValueError) as caso:
            decode_response('deepseek-api', payload)
        self.assertIn('max-tokens', str(caso.exception))

    def test_preview_never_reads_credentials_or_sends(self):
        with patch('conversion.providers.get_key', side_effect=AssertionError('clave')), patch('conversion.providers._api') as transport:
            result = self.execute()
        self.assertEqual(result['external_calls'], 0)
        transport.assert_not_called()

    def test_explicit_slot_is_sent_and_cached_across_slots_without_another_charge(self):
        with patch('conversion.providers.get_key', return_value='fixture-slot-3') as key, patch('conversion.providers._api', return_value=self.payload) as transport:
            result = self.execute(send=True, key_slot=3)
            key.assert_called_once_with('gemini-api', 3)
            self.assertEqual(transport.call_args.kwargs['key'], 'fixture-slot-3')
        receipt = json.loads((Path(result['run_folder'])/'recibo.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['key_slot'], 3)
        with patch('conversion.providers.get_key', side_effect=AssertionError('No leer clave')):
            self.assertTrue(self.execute(send=True, key_slot=4)['response_reused'])

    def test_quota_does_not_rotate_or_retry_even_with_another_slot(self):
        with patch('conversion.providers.get_key', return_value='fixture'), patch('conversion.providers._api', side_effect=ProviderFault('http_429')) as transport:
            with self.assertRaises(ProviderAttemptError):
                self.execute(send=True, key_slot=2)
            with self.assertRaises(ValueError):
                self.execute(send=True, key_slot=3)
            self.assertEqual(transport.call_count, 1)

    def test_saved_response_reused_without_second_provider_call(self):
        with patch('conversion.providers.get_key', return_value='secret-fixture'), patch('conversion.providers._api', return_value=self.payload) as transport:
            first = self.execute(send=True)
            second = self.execute(send=True)
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(second['response_reused'])
        self.assertEqual(second['external_calls'], 0)
        self.assertFalse(first['semantic_verified'])
        self.assertEqual(second['usage']['totalTokenCount'], 100)
        for file in Path(first['run_folder']).glob('*.json'):
            self.assertNotIn('secret-fixture', file.read_text(encoding='utf-8'))

    def test_uncertain_failure_is_not_retried(self):
        with patch('conversion.providers.get_key', return_value='key'), patch('conversion.providers._api', side_effect=TimeoutError) as transport:
            with self.assertRaisesRegex(ValueError, 'revisión'):
                self.execute(send=True)
            with self.assertRaisesRegex(ValueError, 'previo'):
                self.execute(send=True)
        self.assertEqual(transport.call_count, 1)

    def test_failed_response_keeps_usage_and_exact_phase(self):
        payload = {'candidates': [{'finishReason': 'MAX_TOKENS'}], 'usageMetadata': {'totalTokenCount': 320}}
        with patch('conversion.providers.get_key', return_value='key'), patch('conversion.providers._api', return_value=payload) as transport:
            with self.assertRaises(ProviderAttemptError) as failure:
                self.execute(send=True)
            self.assertEqual(failure.exception.code, 'incomplete_response')
            self.assertEqual(failure.exception.phase, 'decodificacion')
            with self.assertRaisesRegex(ValueError, 'previo'):
                self.execute(send=True)
        row = run_history(self.root)['runs'][0]
        self.assertEqual(row['total_tokens'], 320)
        self.assertEqual(row['state'], 'requiere_revision')
        self.assertEqual(transport.call_count, 1)

    def test_equivalent_errors_group_across_attempt_folders(self):
        first = record_failure(self.root, 'enviar_ia', ProviderAttemptError('gemini-api', 'transporte', 'http_429', self.root / 'uno'))
        second = record_failure(self.root, 'enviar_ia', ProviderAttemptError('gemini-api', 'transporte', 'http_429', self.root / 'dos'))
        self.assertEqual(first['signature'], second['signature'])
        self.assertEqual(second['occurrences'], 2)
        self.assertIn('cuota', second['remedy'])

    def test_http_error_does_not_leak_provider_body(self):
        for code in (401, 403, 429):
            failure = HTTPError('https://example.test', code, 'secret-fixture', {}, None)
            with patch('conversion.providers.request.build_opener') as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(ProviderFault) as caught:
                    _http('https://example.test', {'Authorization': 'secret-fixture'}, {}, 10)
            self.assertEqual(caught.exception.code, f'http_{code}')
            self.assertNotIn('secret-fixture', str(caught.exception))

    def test_timeout_history_has_unknown_not_zero_consumption(self):
        with patch('conversion.providers.get_key', return_value='key'), patch('conversion.providers._api', side_effect=ProviderFault('network_timeout')):
            with self.assertRaises(ProviderAttemptError):
                self.execute(send=True)
        row = run_history(self.root)['runs'][0]
        self.assertIsNone(row['total_tokens'])
        self.assertEqual(row['error_code'], 'network_timeout')

    def test_cached_answer_on_another_pc_needs_no_credentials(self):
        with patch('conversion.providers.get_key', return_value='key'), patch('conversion.providers._api', return_value=self.payload):
            first = self.execute(send=True)
        moved = self.root.parent / 'otra-pc'
        shutil.copytree(self.root, moved)
        with patch('conversion.providers.get_key', side_effect=AssertionError('No pedir clave')), patch('conversion.providers._api') as transport:
            result = execute_job(moved, moved / 'encargos' / self.job.name, 'gemini-api', 'modelo-prueba', send=True)
        self.assertTrue(result['response_reused'])
        self.assertTrue(result['reused'])
        self.assertEqual(result['id'], first['id'])
        self.assertEqual(result['external_calls'], 0)
        self.assertTrue(Path(result['output']).is_relative_to(moved))
        transport.assert_not_called()

    def test_missing_key_does_not_reserve_an_attempt(self):
        with patch('conversion.providers.get_key', return_value=None):
            with self.assertRaisesRegex(ValueError, 'clave'):
                self.execute(send=True)
        self.assertFalse((self.root / 'ejecuciones').exists())

    def test_two_callers_only_dispatch_once(self):
        def attempt():
            try:
                return self.execute(send=True)
            except ValueError:
                return None
        with patch('conversion.providers.get_key', return_value='key'), patch('conversion.providers._api', return_value=self.payload) as transport:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(any(results))

    def test_tampered_job_fails_before_contact(self):
        (self.job / 'pagina.png').write_bytes(b'alterado')
        with patch('conversion.providers._api') as transport:
            with self.assertRaises(ValueError):
                self.execute(send=True)
        transport.assert_not_called()

    def test_truncation_and_failure_flags_rejected(self):
        for provider, payload in [('deepseek-api', {'choices': [{'finish_reason': 'length'}]}),
                                  ('gemini-api', {'candidates': [{'finishReason': 'MAX_TOKENS'}]}),
                                  ('antigravity-cli', {'status': 'ERROR', 'response': self.answer}),
                                  ('gemini-cli', {'error': {'message': 'fallo'}})]:
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                decode_response(provider, payload)

    def test_all_four_formats_decode_and_preserve_usage(self):
        variants = [('gemini-api', self.payload),
                    ('deepseek-api', {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}], 'usage': {'total_tokens': 12}}),
                    ('gemini-cli', {'response': '```json\n' + json.dumps(self.answer) + '\n```', 'stats': {'model': {}}}),
                    ('antigravity-cli', {'status': 'SUCCESS', 'structured_output': self.answer, 'usage': {'total_tokens': 10}})]
        for provider, payload in variants:
            with self.subTest(provider=provider):
                answer, usage = decode_response(provider, payload)
                self.assertEqual(answer, self.answer)
                self.assertIsNotNone(usage)

    def test_api_uses_image_and_header_not_url_secret(self):
        from conversion.jobs import load_job
        _, contents = load_job(self.job)
        for provider in ('gemini-api', 'deepseek-api'):
            with patch('conversion.providers.get_key', return_value='secret'), patch('conversion.providers._http', return_value={}) as http:
                _api(provider, 'model', contents, 512, 30)
                url, headers, body, timeout = http.call_args.args
                self.assertNotIn('secret', url)
                self.assertIn('secret', str(headers))
                self.assertIn(base64.b64encode(contents['pagina.png']).decode(), json.dumps(body))

    def test_cli_uses_minimal_directory_and_keeps_permissions(self):
        folder = self.root / 'cli-test'
        folder.mkdir()
        def fake_run(command, **kwargs):
            self.assertFalse(kwargs['shell'])
            self.assertEqual(kwargs['cwd'], folder)
            self.assertNotIn('--dangerously-skip-permissions', command)
            self.assertNotIn('--yolo', command)
            kwargs['stdout'].write(json.dumps({'status': 'SUCCESS', 'structured_output': self.answer}).encode())
            return subprocess.CompletedProcess(command, 0)
        with patch('conversion.providers.cli_binary', return_value=['agy.exe']), patch('conversion.providers.subprocess.run', side_effect=fake_run):
            self.assertEqual(_cli('antigravity-cli', 'model', folder, 60)['status'], 'SUCCESS')

    def test_cli_profile_is_process_isolated_and_does_not_inherit_secrets(self):
        profile_root = self.root / 'perfiles'
        environment = {
            'LOCALAPPDATA': str(profile_root), 'PATH': 'fixture-path',
            'GEMINI_API_KEY': 'secret-fixture', 'NORMAL_SETTING': 'kept',
        }
        with patch.dict('conversion.providers.os.environ', environment, clear=True):
            gemini = cli_environment('gemini-cli', 'pro-1')
            with self.assertRaises(ValueError):
                cli_environment('antigravity-cli', 'pro-2')
            claude = cli_environment('claude-cli', 'cuenta-5')
        self.assertNotIn('GEMINI_API_KEY', gemini)
        self.assertEqual(gemini['NORMAL_SETTING'], 'kept')
        self.assertEqual(Path(gemini['GEMINI_CLI_HOME']).name, 'pro-1')
        self.assertEqual(Path(claude['CLAUDE_CONFIG_DIR']).name, 'cuenta-5')
        self.assertNotIn('GEMINI_API_KEY', claude)

    def test_cli_result_is_cached_across_profiles_without_another_call(self):
        payload = {'response': json.dumps(self.answer), 'stats': {'total_tokens': 7}}
        with patch('conversion.providers.cli_binary', return_value=['gemini.exe']), \
                patch('conversion.providers._cli', return_value=payload) as transport:
            first = execute_job(self.root, self.job, 'gemini-cli', 'modelo-prueba',
                                send=True, cli_profile='pro-1')
            second = execute_job(self.root, self.job, 'gemini-cli', 'modelo-prueba',
                                 send=True, cli_profile='pro-2')
        self.assertEqual(transport.call_count, 1)
        self.assertFalse(first['response_reused'])
        self.assertTrue(second['response_reused'])
        receipt = json.loads((Path(first['run_folder']) / 'recibo.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['cli_profile'], 'pro-1')

    def test_api_rejects_cli_profile_even_in_preview(self):
        with self.assertRaisesRegex(ValueError, 'perfil CLI'):
            self.execute(cli_profile='pro-1')
