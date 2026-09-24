from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import subprocess
import sys
import json
import shutil

from conversion.pipeline import convert_text, prepare_pdf
from conversion.retrieval import consult
from conversion.workflow import WorkQueue
from conversion.diagnostics import run_diagnostics
from conversion.storage import report, export_index, output_folder
from support import workspace_temp


class MemoryTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.base = Path(fixture.name)
        self.root = self.base / 'memoria'
        self.source = self.base / 'nota.md'
        self.source.write_text('# Testigo\n\nHYE - Responsabilidades de diseño.\n' * 100, encoding='utf-8')

    def test_bounded_query_needs_no_native_file(self):
        convert_text(self.root, self.source)
        self.source.unlink()
        answer = consult(self.root, 'HYE', max_chars=200)
        self.assertTrue(answer['hits'])
        self.assertLessEqual(answer['content_characters'], 200)
        self.assertEqual(answer['native_files_read'], 0)
        self.assertFalse(answer['hits'][0]['semantic_verified'])

    def test_corrupt_derivative_excluded(self):
        result = convert_text(self.root, self.source)
        (Path(result['output']) / 'documento.md').write_text('HYE alterado', encoding='utf-8')
        answer = consult(self.root, 'HYE')
        self.assertFalse(answer['hits'])
        self.assertEqual(len(answer['excluded_damaged']), 1)

    def test_copied_memory_uses_local_packages_even_if_original_still_exists(self):
        queue = WorkQueue(self.root)
        queue.add(self.source)
        old = queue.process(str(self.source.resolve()))
        moved = self.base / 'otra-pc'
        shutil.copytree(self.root, moved)
        before = (moved / 'indice.sqlite').read_bytes()
        result = consult(moved, 'HYE')
        self.assertTrue(Path(result['hits'][0]['markdown']).is_relative_to(moved))
        self.assertTrue(report(moved)[0]['integrity_ok'])
        self.assertEqual(before, (moved / 'indice.sqlite').read_bytes())
        self.assertTrue(Path(WorkQueue(moved).items[0]['output']).is_relative_to(moved))
        self.assertEqual(export_index(moved)['documents'], 1)
        reused = convert_text(moved, self.source)
        self.assertTrue(reused['reused'])
        self.assertNotEqual(old['output'], reused['output'])
        # No rescatar una copia local dañada leyendo silenciosamente la ubicación vieja.
        (Path(reused['output']) / 'documento.md').write_text('dañado', encoding='utf-8')
        self.assertFalse(consult(moved, 'HYE')['hits'])
        self.assertTrue(report(self.root)[0]['integrity_ok'])

    def test_output_location_rejects_path_injection(self):
        with self.assertRaises(ValueError):
            output_folder(self.root, '../secrets')
        folder = output_folder(self.root, 'Z:\\antigua\\documentos\\' + 'a' * 64)
        self.assertEqual(folder, self.root.resolve() / 'documentos' / ('a' * 64))

    def test_readding_same_source_preserves_result(self):
        queue = WorkQueue(self.root)
        queue.add(self.source)
        processed = queue.process(str(self.source.resolve()))
        queue.add(self.source)
        self.assertEqual(queue.items[0]['output'], processed['output'])
        self.assertEqual(queue.items[0]['status'], 'revisar')
        self.source.write_text('Fuente modificada', encoding='utf-8')
        queue.add(self.source)
        self.assertNotIn('output', queue.items[0])

    def test_two_stale_instances_merge_on_write(self):
        first, second = WorkQueue(self.root), WorkQueue(self.root)
        other = self.base / 'otra.md'
        other.write_text('Segunda fuente', encoding='utf-8')
        first.add(self.source)
        second.add(other)
        self.assertEqual(len(WorkQueue(self.root).items), 2)

    def test_processes_do_not_drop_queue_entries(self):
        files = []
        for number in range(3):
            source = self.base / f'concurrente-{number}.txt'
            source.write_text(str(number), encoding='utf-8')
            files.append(source)
        processes = [subprocess.Popen([sys.executable, '-m', 'conversion', '--salida',
                     str(self.root), 'encolar', str(source)], stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE) for source in files]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, (stdout, stderr))
        self.assertEqual(len(WorkQueue(self.root).items), 3)

    def test_invalid_queue_is_preserved_and_diagnosed(self):
        self.root.mkdir()
        path = self.root / 'cola.json'
        raw = json.dumps({'schema_version': 1, 'items': [{'path': 'incompleto'}]})
        path.write_text(raw, encoding='utf-8')
        with self.assertRaises(ValueError):
            WorkQueue(self.root)
        checks = run_diagnostics(self.root)['checks']
        self.assertTrue(any(c['name'] == 'Cola persistente' and not c['ok'] for c in checks))
        self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_pdf_cache_skips_renderer_but_checks_corruption(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('PyMuPDF no instalado')
        source = self.base / 'ejemplo.pdf'
        with fitz.open() as doc:
            doc.new_page().insert_text((50, 50), 'Texto testigo HYE')
            doc.save(source)
        first = prepare_pdf(self.root, source, [1])
        with patch('fitz.open', side_effect=AssertionError('No debe renderizar otra vez')):
            second = prepare_pdf(self.root, source, [1])
        self.assertTrue(second['reused'])
        moved = self.base / 'copia-pdf'
        shutil.copytree(self.root, moved)
        with patch('fitz.open', side_effect=AssertionError('No renderizar tras mudanza')):
            copied = prepare_pdf(moved, source, [1])
        self.assertTrue(copied['reused'])
        self.assertTrue(Path(copied['output']).is_relative_to(moved))
        (Path(first['output']) / 'documento.md').write_text('daño', encoding='utf-8')
        third = prepare_pdf(self.root, source, [1])
        self.assertFalse(third['reused'])
        self.assertNotEqual(first['output'], third['output'])
