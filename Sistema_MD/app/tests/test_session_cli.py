import json
from pathlib import Path
import subprocess
from unittest import TestCase
from unittest.mock import patch
from support import workspace_temp
from conversion.session_cli import session_command, session_input, run_session_cli
from conversion.providers import cli_environment, profiles_for, decode_response
from conversion.access import launch_login


class SessionCliTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.folder = Path(fixture.name)
        (self.folder / 'solicitud.md').write_text('DATO PRIVADO de control', encoding='utf-8')
        (self.folder / 'pagina.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (self.folder / 'respuesta.schema.json').write_text('{"type":"object"}', encoding='utf-8')

    def test_commands_do_not_carry_document_or_unrestricted_permissions(self):
        for provider in ('codex-cli', 'claude-cli'):
            command = session_command(provider, 'modelo-test', self.folder, ['cli.exe'])
            self.assertNotIn('DATO PRIVADO', ' '.join(command))
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
            self.assertNotIn('--dangerously-skip-permissions', command)
            self.assertIn(b'DATO PRIVADO', session_input(provider, self.folder))

    def test_isolated_profiles_do_not_touch_existing_global_config_or_inherit_keys(self):
        with patch.dict('os.environ', {'LOCALAPPDATA': str(self.folder), 'ANTHROPIC_API_KEY': 'secret',
                                     'CODEX_HOME': 'global-config', 'CLAUDECODE': '1'}, clear=True):
            for provider, variable in [('codex-cli','CODEX_HOME'),('claude-cli','CLAUDE_CONFIG_DIR')]:
                env = cli_environment(provider, 'cuenta-5')
                self.assertTrue(Path(env[variable]).is_relative_to(self.folder))
                self.assertNotIn('ANTHROPIC_API_KEY', env)
                self.assertNotIn('CLAUDECODE', env)
        self.assertFalse((self.folder / 'SistemaMD').exists())

    def test_fab_profiles_are_not_offered_as_generic_cli_profiles(self):
        self.assertIn('fab5', profiles_for('antigravity-cli'))
        for provider in ('codex-cli', 'claude-cli', 'gemini-cli'):
            self.assertNotIn('fab5', profiles_for(provider))
            self.assertIn('cuenta-5', profiles_for(provider))

    def test_codex_finishes_with_usage_and_structured_output_without_shell(self):
        def run(command, **kw):
            self.assertFalse(kw['shell'])
            self.assertIn(b'DATO PRIVADO', kw['input'])
            kw['stdout'].write(b'{"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":3}}\n')
            (self.folder / 'final.json').write_text('{"answer":42}', encoding='utf-8')
            return subprocess.CompletedProcess(command, 0)
        with patch('conversion.session_cli.subprocess.run', side_effect=run):
            result = run_session_cli('codex-cli','model',self.folder,30,'cuenta-1',
                                     binary=['codex.exe'], environment={'CODEX_HOME':str(self.folder/'perfil')})
        answer, usage = decode_response('codex-cli', result)
        self.assertEqual(answer, {'answer':42})
        self.assertEqual(usage['total_tokens'],10)

    def test_timeout_is_one_attempt_not_replayed(self):
        with patch('conversion.session_cli.subprocess.run', side_effect=subprocess.TimeoutExpired('cli',10)) as run:
            with self.assertRaises(ValueError):
                run_session_cli('codex-cli','model',self.folder,10,'cuenta-1',
                                binary=['codex.exe'],environment={'CODEX_HOME':str(self.folder/'perfil')})
        self.assertEqual(run.call_count,1)

    def test_login_only_uses_official_auth_command_no_prompt(self):
        with patch('conversion.access.cli_binary', return_value=['claude.exe']), \
             patch('conversion.access.cli_environment', return_value={'CLAUDE_CONFIG_DIR':str(self.folder/'perfil')}), \
             patch('conversion.access.subprocess.Popen') as popen:
            popen.return_value.pid=123
            result=launch_login(self.folder,'claude-cli','cuenta-5')
        self.assertEqual(popen.call_args.args[0],['claude.exe','auth','login'])
        self.assertFalse(result['prompt_sent'])

    def test_failed_claude_preserves_usage_before_rejection(self):
        from conversion.providers import usage_from_payload, reported_tokens
        def run(command, **kw):
            payload={'type':'result','subtype':'error_max_turns','is_error':True,'total_cost_usd':0.0123,
                     'usage':{'input_tokens':10,'output_tokens':20,'cache_creation_input_tokens':30,'cache_read_input_tokens':40}}
            kw['stdout'].write(json.dumps(payload).encode()+b'\n')
            return subprocess.CompletedProcess(command, 1)
        with patch('conversion.session_cli.subprocess.run',side_effect=run):
            result=run_session_cli('claude-cli','model',self.folder,30,'cuenta-1',binary=['claude.exe'],
                                   environment={'CLAUDE_CONFIG_DIR':str(self.folder/'profile')})
        self.assertEqual(reported_tokens(usage_from_payload('claude-cli',result)),100)
        self.assertEqual(result['total_cost_usd'],0.0123)
        with self.assertRaises(ValueError):
            decode_response('claude-cli',result)

    def test_alternate_provider_selectors_are_removed(self):
        with patch.dict('os.environ',{'LOCALAPPDATA':str(self.folder),'CLAUDE_CODE_USE_FOUNDRY':'1',
                'ANTHROPIC_FOUNDRY_RESOURCE':'other-backend','GOOGLE_GENAI_USE_VERTEXAI':'1'},clear=True):
            env=cli_environment('claude-cli','cuenta-1')
        self.assertNotIn('CLAUDE_CODE_USE_FOUNDRY',env)
        self.assertNotIn('ANTHROPIC_FOUNDRY_RESOURCE',env)
        self.assertNotIn('GOOGLE_GENAI_USE_VERTEXAI',env)

    def test_failed_codex_keeps_reported_consumption_without_success(self):
        from conversion.providers import usage_from_payload, reported_tokens
        def run(command, **kw):
            kw['stdout'].write(b'{"type":"turn.failed","usage":{"input_tokens":7,"output_tokens":3,"cached_input_tokens":5}}\n')
            return subprocess.CompletedProcess(command, 1)
        with patch('conversion.session_cli.subprocess.run',side_effect=run):
            result=run_session_cli('codex-cli','model',self.folder,30,'cuenta-1',binary=['codex.exe'],
                                   environment={'CODEX_HOME':str(self.folder/'profile')})
        self.assertEqual(reported_tokens(usage_from_payload('codex-cli',result)),10)
        with self.assertRaises(ValueError):
            decode_response('codex-cli',result)
