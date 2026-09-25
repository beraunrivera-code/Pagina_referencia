import ctypes
from unittest import TestCase
from unittest.mock import Mock, patch

from conversion.credentials import Credential, ENV_NAMES, KEY_SLOTS, delete_key, get_key, key_location, save_key


class CredentialTests(TestCase):
    def test_all_five_slots_are_unique_for_all_five_apis_and_legacy_is_unchanged(self):
        targets, variables = set(), set()
        self.assertEqual(len(ENV_NAMES), 5)
        self.assertEqual(KEY_SLOTS, (1, 2, 3, 4, 5))
        for provider, variable in ENV_NAMES.items():
            self.assertEqual(key_location(provider), (variable, 'SistemaMD/' + provider))
            for slot in KEY_SLOTS:
                name, target = key_location(provider, slot)
                self.assertNotIn(target, targets)
                self.assertNotIn(name, variables)
                targets.add(target)
                variables.add(name)
        self.assertEqual(key_location('gemini-api', 4), ('GEMINI_API_KEY_4', 'SistemaMD/gemini-api/4'))
        self.assertEqual(key_location('qwen-api', 5), ('DASHSCOPE_API_KEY_5', 'SistemaMD/qwen-api/5'))
        self.assertEqual(len(targets), 25)

    def test_invalid_slots_never_access_the_store(self):
        with patch('conversion.credentials._library') as library:
            for slot in (True, False, 0, 6, -1, '1', 1.0, None):
                with self.subTest(slot=slot), self.assertRaises(ValueError):
                    get_key('openai-api', slot)
                with self.assertRaises(ValueError):
                    save_key('anthropic-api', 'fixture', slot)
                with self.assertRaises(ValueError):
                    delete_key('qwen-api', slot)
        library.assert_not_called()

    def test_environment_precedes_windows_store(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': '  fixture  '}), patch('conversion.credentials._library') as library:
            self.assertEqual(get_key('gemini-api'), 'fixture')
        library.assert_not_called()

    def test_rejects_bad_provider_and_empty_key_without_store(self):
        with patch('conversion.credentials._library') as library:
            for provider, value in [('gemini-cli', 'fixture'), ('gemini-api', '  '), ('deepseek-api', 'x' * 2001),
                                    ('openai-api', 'fixture\nheader'), ('anthropic-api', None),
                                    ('qwen-api', 'fixture\x00secret'), ('qwen-api', 'fixture key')]:
                with self.assertRaises(ValueError):
                    save_key(provider, value)
        library.assert_not_called()

    def test_write_uses_user_credential_not_project_file(self):
        observed = {}
        def fake_write(pointer, flags):
            entry = ctypes.cast(pointer, ctypes.POINTER(Credential)).contents
            observed.update(target=entry.TargetName, kind=entry.Type, persist=entry.Persist,
                            data=ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize))
            return True
        library = Mock()
        library.CredWriteW.side_effect = fake_write
        with patch('conversion.credentials.os.name', 'nt'), patch('conversion.credentials._library', return_value=library):
            save_key('deepseek-api', ' fixture ')
        self.assertEqual(observed, {'target': 'SistemaMD/deepseek-api', 'kind': 1, 'persist': 2, 'data': b'fixture'})

    def test_read_releases_native_buffer(self):
        raw = (ctypes.c_ubyte * 7).from_buffer_copy(b'fixture')
        entry = Credential()
        entry.CredentialBlob, entry.CredentialBlobSize = raw, 7
        def fake_read(target, kind, flags, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(Credential)))[0] = ctypes.pointer(entry)
            return True
        library = Mock()
        library.CredReadW.side_effect = fake_read
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': ''}), patch('conversion.credentials.os.name', 'nt'), patch('conversion.credentials._library', return_value=library):
            self.assertEqual(get_key('deepseek-api'), 'fixture')
        library.CredFree.assert_called_once()

    def test_environment_slot_five_never_reads_another_slot(self):
        for provider, variable in ENV_NAMES.items():
            with self.subTest(provider=provider), patch.dict('os.environ', {
                variable: 'wrong-account', variable + '_5': 'fixture-five',
            }, clear=True), patch('conversion.credentials._library') as library:
                self.assertEqual(get_key(provider, 5), 'fixture-five')
                library.assert_not_called()

    def test_invalid_store_blob_error_never_exposes_bytes_and_still_frees_buffer(self):
        raw = (ctypes.c_ubyte * 15).from_buffer_copy(b'fixture-secret\xff')
        entry = Credential()
        entry.CredentialBlob, entry.CredentialBlobSize = raw, 15
        def fake_read(target, kind, flags, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(Credential)))[0] = ctypes.pointer(entry)
            return True
        library = Mock()
        library.CredReadW.side_effect = fake_read
        with patch.dict('os.environ', {}, clear=True), patch('conversion.credentials.os.name', 'nt'), \
                patch('conversion.credentials._library', return_value=library):
            with self.assertRaises(OSError) as caught:
                get_key('openai-api', 5)
        self.assertNotIn('fixture', str(caught.exception))
        library.CredFree.assert_called_once()

    def test_missing_slot_does_not_fall_back_to_slot_one(self):
        library = Mock()
        library.CredReadW.return_value = False
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'wrong-account'}, clear=True), \
                patch('conversion.credentials.os.name', 'nt'), \
                patch('conversion.credentials._library', return_value=library), \
                patch('conversion.credentials._last_error', return_value=1168):
            self.assertIsNone(get_key('openai-api', 5))
        self.assertEqual(library.CredReadW.call_args.args[:3], ('SistemaMD/openai-api/5', 1, 0))

    def test_replace_uses_exact_slot_and_never_reads_old_key(self):
        writes = []
        def fake_write(pointer, flags):
            entry = ctypes.cast(pointer, ctypes.POINTER(Credential)).contents
            writes.append((entry.TargetName, ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize)))
            return True
        library = Mock()
        library.CredWriteW.side_effect = fake_write
        with patch('conversion.credentials.os.name', 'nt'), patch('conversion.credentials._library', return_value=library):
            save_key('anthropic-api', 'fixture-first', 5)
            save_key('anthropic-api', 'fixture-replaced', 5)
        self.assertEqual(writes, [('SistemaMD/anthropic-api/5', b'fixture-first'),
                                  ('SistemaMD/anthropic-api/5', b'fixture-replaced')])
        library.CredReadW.assert_not_called()

    def test_delete_only_selected_target_and_preserves_environment(self):
        library = Mock()
        library.CredDeleteW.return_value = True
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY_5': 'fixture-env'}, clear=True), \
                patch('conversion.credentials.os.name', 'nt'), \
                patch('conversion.credentials._library', return_value=library):
            self.assertTrue(delete_key('qwen-api', 5))
            self.assertEqual(get_key('qwen-api', 5), 'fixture-env')
        library.CredDeleteW.assert_called_once_with('SistemaMD/qwen-api/5', 1, 0)
        library.CredReadW.assert_not_called()

    def test_delete_missing_is_idempotent_other_errors_are_not_hidden(self):
        library = Mock()
        library.CredDeleteW.return_value = False
        with patch('conversion.credentials.os.name', 'nt'), \
                patch('conversion.credentials._library', return_value=library), \
                patch('conversion.credentials._last_error', return_value=1168):
            self.assertFalse(delete_key('deepseek-api', 2))
        with patch('conversion.credentials.os.name', 'nt'), \
                patch('conversion.credentials._library', return_value=library), \
                patch('conversion.credentials._last_error', return_value=5):
            with self.assertRaises(OSError):
                delete_key('deepseek-api', 2)

    def test_non_windows_delete_does_not_remove_environment(self):
        with patch('conversion.credentials.os.name', 'posix'), patch('conversion.credentials._library') as library:
            with self.assertRaises(OSError):
                delete_key('gemini-api', 1)
            library.assert_not_called()
