"""Contrato de cinco API con transporte simulado. Nunca usa claves ni cuentas reales."""
import base64
import json
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from conversion.api_adapters import (API_PROVIDERS, ENDPOINTS, QWEN_VISION_MODELS,
    api_request, decode_api, usage_for_api, validate_api_model)
from conversion.provider_errors import ProviderFault

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
CONTENTS = {'solicitud.md': b'Convierte esta pagina.', 'respuesta.schema.json': b'{"type":"object"}', 'pagina.png': PNG}
ANSWER = {'job_id': 'fixture', 'unit': 1, 'blocks': [], 'warnings': []}
TEXT = json.dumps(ANSWER)
MODELS = {'gemini-api': 'model', 'deepseek-api': 'deepseek-flash', 'openai-api': 'gpt-4.1',
          'anthropic-api': 'claude-sonnet-4-5', 'qwen-api': 'qwen3-vl-plus'}


def payload_for(provider, text=TEXT):
    if provider == 'gemini-api':
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': text}]}}],
                'usageMetadata': {'totalTokenCount': 42}}
    if provider in ('deepseek-api', 'qwen-api'):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}],
                'usage': {'total_tokens': 42}}
    if provider == 'openai-api':
        return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                'status': 'completed', 'content': [{'type': 'output_text', 'text': text}]}],
                'usage': {'input_tokens': 30, 'output_tokens': 12, 'total_tokens': 42}}
    return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}],
            'usage': {'input_tokens': 10, 'output_tokens': 12,
                      'cache_creation_input_tokens': 15, 'cache_read_input_tokens': 5}}


class ApiAdapterTests(TestCase):
    def request(self, provider, **kwargs):
        values = dict(key='fixture-secret', http=Mock(return_value={'id': 'raw'}))
        values.update(kwargs)
        return api_request(provider, MODELS[provider], CONTENTS, 1024, 60, **values)

    def test_five_contracts_send_one_png_and_headers_only_secret(self):
        self.assertEqual(len(API_PROVIDERS), 5)
        for provider in API_PROVIDERS:
            with self.subTest(provider=provider):
                transport = Mock(return_value={'id': 'raw'})
                with patch('builtins.open', side_effect=AssertionError('No file IO')), \
                        patch('conversion.credentials.get_key', side_effect=AssertionError('No credential IO')):
                    self.assertEqual(self.request(provider, http=transport), {'id': 'raw'})
                transport.assert_called_once()
                url, headers, body, timeout = transport.call_args.args
                self.assertEqual(url, ENDPOINTS[provider].format(model=MODELS[provider]))
                self.assertNotIn('fixture-secret', url + json.dumps(body))
                self.assertIn('fixture-secret', str(headers))
                self.assertEqual(timeout, 60)
                self.assertIn(base64.b64encode(PNG).decode(), json.dumps(body))
                self.assertIn('JSON', json.dumps(body))
                self.assertNotIn('tools', body)
                self.assertNotIn('previous_response_id', body)

    def test_openai_uses_responses_json_and_disables_stored_response(self):
        transport = Mock()
        self.request('openai-api', http=transport)
        body = transport.call_args.args[2]
        self.assertEqual(body['text'], {'format': {'type': 'json_object'}})
        self.assertFalse(body['store'])
        self.assertFalse(body['stream'])
        self.assertEqual(body['max_output_tokens'], 1024)
        self.assertEqual([part['type'] for part in body['input'][0]['content']], ['input_text', 'input_image'])
        self.assertNotIn('max_tokens', body)

    def test_anthropic_uses_messages_version_base64_and_no_fake_response_format(self):
        transport = Mock()
        self.request('anthropic-api', http=transport)
        _, headers, body, _ = transport.call_args.args
        self.assertEqual(headers['anthropic-version'], '2023-06-01')
        self.assertEqual(headers['x-api-key'], 'fixture-secret')
        self.assertNotIn('Authorization', headers)
        self.assertEqual(body['max_tokens'], 1024)
        content = body['messages'][0]['content']
        self.assertEqual(content[0]['source']['type'], 'base64')
        self.assertEqual(content[0]['source']['media_type'], 'image/png')
        self.assertNotIn('response_format', body)

    def test_qwen_region_fixed_and_thinking_disabled_only_for_supported_hybrid_models(self):
        for model in QWEN_VISION_MODELS:
            transport = Mock()
            api_request('qwen-api', model, CONTENTS, 1024, 60, key='fixture', http=transport)
            url, _, body, _ = transport.call_args.args
            self.assertEqual(url, 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions')
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            if model.startswith('qwen3-vl-'):
                self.assertIs(body['enable_thinking'], False)
            else:
                self.assertNotIn('enable_thinking', body)

    def test_qwen_rejects_text_embedding_coding_and_unreviewed_models_before_transport(self):
        transport = Mock()
        for model in ('qwen-plus', 'qwen3-vl-embedding', 'qwen-coder', 'unreviewed-vl',
                      'qwen3-vl-235b-a22b-thinking', 'qwen-vl-max-latest'):
            with self.subTest(model=model), self.assertRaises(ValueError):
                api_request('qwen-api', model, CONTENTS, 1024, 60, key='fixture', http=transport)
        transport.assert_not_called()

    def test_model_does_not_allow_url_path_or_header_injection(self):
        for provider in API_PROVIDERS:
            for model in ('../model', 'name?key=fixture', '', None, 'model\nheader', 'x' * 121):
                with self.subTest(provider=provider, model=model), self.assertRaises(ValueError):
                    validate_api_model(provider, model)
        with self.assertRaises(ValueError):
            validate_api_model('claude-cli', 'model')

    def test_invalid_inputs_never_send_or_leak_keys(self):
        transport = Mock()
        for key in ('', '  ', None, 'fixture\nAuthorization: secret', 'fixture secret', 'x' * 2001):
            with self.subTest(key_type=type(key).__name__), self.assertRaises(ValueError) as caught:
                self.request('openai-api', key=key, http=transport)
            self.assertNotIn('secret', str(caught.exception))
        for contents in ({}, {'solicitud.md': b'\xff', **{k:v for k,v in CONTENTS.items() if k != 'solicitud.md'}},
                         {**CONTENTS, 'pagina.png': b'not PNG'}, {**CONTENTS, 'respuesta.schema.json': b'[]'},
                         {**CONTENTS, 'respuesta.schema.json': b'{"duplicate":1,"duplicate":2}'}):
            with self.assertRaises(ValueError):
                api_request('openai-api', 'gpt-4.1', contents, 1024, 60, key='fixture', http=transport)
        for tokens, timeout in ((True, 60), (255, 60), (16385, 60), (1024, float('nan')), (1024, True)):
            with self.assertRaises(ValueError):
                api_request('openai-api', 'gpt-4.1', CONTENTS, tokens, timeout, key='fixture', http=transport)
        transport.assert_not_called()

    def test_anthropic_base64_image_limit_checked_before_send(self):
        transport = Mock()
        oversized = {**CONTENTS, 'pagina.png': b'\x89PNG\r\n\x1a\n' + b'a' * 7_500_000}
        with self.assertRaisesRegex(ValueError, '10 MB'):
            api_request('anthropic-api', MODELS['anthropic-api'], oversized, 1024, 60, key='fixture', http=transport)
        transport.assert_not_called()

    def test_transport_failures_are_safe_no_retry_or_fallback(self):
        cases = [(HTTPError('https://example.test', 401, 'fixture-secret', {}, None), 'http_401'),
                 (HTTPError('https://example.test', 429, 'fixture-secret', {}, None), 'http_429'),
                 (URLError('fixture-secret'), 'network_timeout'),
                 (TimeoutError('fixture-secret'), 'network_timeout'),
                 (ConnectionResetError('fixture-secret'), 'network_timeout'),
                 (ValueError('fixture-secret invalid JSON'), 'invalid_response')]
        for provider in API_PROVIDERS:
            for error, code in cases:
                with self.subTest(provider=provider, code=code):
                    transport = Mock(side_effect=error)
                    with self.assertRaises(ProviderFault) as caught:
                        self.request(provider, http=transport)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn('fixture-secret', str(caught.exception))
                    transport.assert_called_once()

    def test_decode_all_five_contracts_preserves_usage(self):
        for provider in API_PROVIDERS:
            with self.subTest(provider=provider):
                answer, usage = decode_api(provider, payload_for(provider))
                self.assertEqual(answer, ANSWER)
                self.assertEqual(usage.get('total_tokens', usage.get('totalTokenCount')), 42)

    def test_json_fences_supported_but_arrays_duplicates_nan_and_comments_rejected(self):
        for provider in API_PROVIDERS:
            self.assertEqual(decode_api(provider, payload_for(provider, '```json\n' + TEXT + '\n```'))[0], ANSWER)
            for invalid in ('', '[]', 'null', '{"a":1,"a":2}', '{"a":NaN}', 'fixture-secret not JSON', TEXT + '\nexplanation'):
                with self.subTest(provider=provider), self.assertRaises(ProviderFault) as caught:
                    decode_api(provider, payload_for(provider, invalid))
                self.assertEqual(caught.exception.code, 'invalid_response')
                self.assertNotIn('fixture-secret', str(caught.exception))

    def test_truncation_preserves_usage_for_receipt_but_is_never_accepted(self):
        for provider in API_PROVIDERS:
            payload = payload_for(provider)
            if provider == 'gemini-api':
                payload['candidates'][0]['finishReason'] = 'MAX_TOKENS'
            elif provider in ('deepseek-api', 'qwen-api'):
                payload['choices'][0]['finish_reason'] = 'length'
            elif provider == 'openai-api':
                payload['status'] = 'incomplete'
                payload['incomplete_details'] = {'reason': 'max_output_tokens'}
            else:
                payload['stop_reason'] = 'max_tokens'
            with self.subTest(provider=provider), self.assertRaises(ProviderFault) as caught:
                decode_api(provider, payload)
            self.assertEqual(caught.exception.code, 'incomplete_response')
            self.assertIsNotNone(usage_for_api(provider, payload))

    def test_openai_reads_output_array_not_sdk_convenience_property(self):
        payload = payload_for('openai-api')
        payload['output_text'] = '{"wrong":"SDK-only"}'
        payload['output'].insert(0, {'type': 'reasoning', 'summary': []})
        self.assertEqual(decode_api('openai-api', payload)[0], ANSWER)
        payload['output'][1]['status'] = 'in_progress'
        with self.assertRaises(ProviderFault) as caught:
            decode_api('openai-api', payload)
        self.assertEqual(caught.exception.code, 'incomplete_response')

    def test_refusals_and_tool_calls_are_not_publishable(self):
        for provider in ('openai-api', 'anthropic-api'):
            payload = payload_for(provider)
            if provider == 'openai-api':
                payload['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'fixture-secret'}]
            else:
                payload['stop_reason'] = 'refusal'
            with self.assertRaises(ProviderFault) as caught:
                decode_api(provider, payload)
            self.assertEqual(caught.exception.code, 'refused_response')
            self.assertNotIn('fixture-secret', str(caught.exception))
        for provider in ('deepseek-api', 'qwen-api'):
            payload = payload_for(provider)
            payload['choices'][0]['message']['tool_calls'] = [{'name': 'unexpected'}]
            with self.assertRaises(ProviderFault):
                decode_api(provider, payload)

    def test_malformed_remote_envelopes_have_safe_classification(self):
        cases = [('gemini-api', {'candidates': 'fixture-secret'}),
                 ('gemini-api', {'candidates': [{'finishReason': 'STOP', 'content': None}]}),
                 ('deepseek-api', {'choices': [{'finish_reason': 'stop', 'message': []}]}),
                 ('qwen-api', {'choices': [None]}),
                 ('openai-api', {'status': 'completed', 'output': [None]}),
                 ('anthropic-api', {'stop_reason': 'end_turn', 'content': 'fixture-secret'})]
        for provider, payload in cases:
            with self.subTest(provider=provider), self.assertRaises(ProviderFault) as caught:
                decode_api(provider, payload)
            self.assertEqual(caught.exception.code, 'invalid_response')
            self.assertNotIn('fixture-secret', str(caught.exception))
        for provider in API_PROVIDERS:
            with self.assertRaises(ProviderFault):
                decode_api(provider, {'error': {'message': 'fixture-secret'}})

    def test_anthropic_total_includes_cache_once_without_mutating_raw_payload(self):
        payload = payload_for('anthropic-api')
        payload['usage']['cache_creation'] = {'ephemeral_5m_input_tokens': 15, 'ephemeral_1h_input_tokens': 0}
        usage = usage_for_api('anthropic-api', payload)
        self.assertEqual(usage['total_tokens'], 42)
        self.assertNotIn('total_tokens', payload['usage'])

    def test_anthropic_missing_or_invalid_counters_do_not_become_zero(self):
        for name in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'):
            for value in (None, True, -1, 1.0, '1'):
                payload = payload_for('anthropic-api')
                payload['usage'][name] = value
                payload['usage']['total_tokens'] = 999
                self.assertNotIn('total_tokens', usage_for_api('anthropic-api', payload))
            payload = payload_for('anthropic-api')
            del payload['usage'][name]
            self.assertNotIn('total_tokens', usage_for_api('anthropic-api', payload))
        for provider in API_PROVIDERS:
            self.assertIsNone(usage_for_api(provider, {}))
            self.assertIsNone(usage_for_api(provider, None))
