from pathlib import Path
from unittest import TestCase

from conversion.diagnostics import (export_failure_report, list_failures,
                                    record_failure, repair_safe, run_diagnostics, resolve_failure)
from support import workspace_temp


class DiagnosticTests(TestCase):
    def setUp(self):
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "memoria"

    def test_repeated_failure_is_grouped_by_signature(self):
        first = record_failure(self.root, "leer_pdf", FileNotFoundError("ausente.pdf"))
        second = record_failure(self.root, "leer_pdf", FileNotFoundError("ausente.pdf"))
        self.assertEqual(first["signature"], second["signature"])
        self.assertEqual(second["occurrences"], 2)
        self.assertEqual(len(list_failures(self.root)), 1)

    def test_report_contains_diagnosis_and_remedy(self):
        record_failure(self.root, "convertir", ValueError("rango inválido"))
        result = export_failure_report(self.root)
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("Causa probable", text)
        self.assertIn("Blindaje/siguiente prueba", text)

    def test_health_never_calls_external_provider(self):
        result = run_diagnostics(self.root)
        self.assertEqual(result["external_calls"], 0)
        self.assertTrue(any(check["name"] == "Consumo externo" and check["ok"]
                            for check in result["checks"]))

    def test_safe_repair_only_rebuilds_derived_infrastructure(self):
        result = repair_safe(self.root)
        self.assertEqual(result["sources_modified"], 0)
        self.assertEqual(result["external_calls"], 0)
        self.assertTrue(Path(result["index"]).exists())

    def test_resolution_keeps_evidence_when_failure_recurs(self):
        issue = record_failure(self.root, 'leer', ValueError('ejemplo'))
        resolve_failure(self.root, issue['signature'], 'Causa verificada', 'Prueba testigo pasa')
        self.assertEqual(list_failures(self.root)[0]['status'], 'resuelto')
        record_failure(self.root, 'leer', ValueError('ejemplo'))
        result = list_failures(self.root)[0]
        self.assertEqual(result['status'], 'abierto')
        self.assertEqual(len(result['resolutions']), 1)
        self.assertEqual(result['occurrences'], 2)
