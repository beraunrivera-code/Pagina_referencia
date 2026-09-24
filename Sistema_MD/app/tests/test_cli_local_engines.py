import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from conversion.__main__ import main


class EngineCommandTests(TestCase):
    def test_help_survives_missing_saved_data_folder(self):
        with patch('conversion.__main__.data_root', side_effect=ValueError('ausente')) as data, redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as outcome:
                main(['--help'])
            self.assertEqual(outcome.exception.code, 0)
            data.assert_not_called()

    def test_probe_does_not_require_data_folder(self):
        with patch('conversion.__main__.data_root', side_effect=ValueError('ausente')), \
             patch('conversion.local_engines.probe_engines', return_value={'engines':[], 'external_calls':0}) as probe, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['motores-locales']), 0)
        probe.assert_called_once_with()
        self.assertEqual(json.loads(output.getvalue())['external_calls'], 0)

    def test_conversion_dispatches_into_common_library(self):
        with patch('conversion.local_engines.convert_with_engine', return_value={'status':'ok'}) as convert, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--salida','biblioteca','convertir-motor-local','documento.pdf',
                                   '--motor','docling','--paginas','1-2','--timeout','90']),0)
        convert.assert_called_once_with(Path('biblioteca'), Path('documento.pdf'), 'docling', pages=[1,2], timeout=90)

    def test_configure_does_not_download_or_convert(self):
        with patch('conversion.local_engines.configure_engine', return_value={'external_calls':0}) as configure, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(main(['configurar-motor-local','markitdown','--python','motor/python.exe']),0)
        configure.assert_called_once_with('markitdown',Path('motor/python.exe'),None)
