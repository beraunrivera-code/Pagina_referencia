from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from conversion.app import ConversionApp, persist_md_export


class ExportPortabilityNoticeTests(TestCase):
    def test_background_receipt_preserves_portability_warning(self):
        with patch('conversion.app.md_export_dir', return_value=Path('salida')), \
             patch('conversion.app.export_markdown', return_value={'path':'salida/control.md',
                   'portable_links':False,'link_warning':'Enlaces entre unidades'}):
            receipt = persist_md_export('paquete')
        self.assertFalse(receipt['portable_links'])
        self.assertEqual(receipt['link_warning'],'Enlaces entre unidades')

    def test_warning_is_visible_once_per_destination_not_per_file(self):
        app = SimpleNamespace()
        receipts = [{'package':'paquete','path':'salida/control.md',
                     'portable_links':False,'link_warning':'Enlaces entre unidades'}]
        with patch('conversion.app.messagebox.showwarning') as show:
            self.assertEqual(ConversionApp._export_md(app,'paquete',receipts),Path('salida/control.md'))
            ConversionApp._export_md(app,'paquete',receipts)
        show.assert_called_once()
