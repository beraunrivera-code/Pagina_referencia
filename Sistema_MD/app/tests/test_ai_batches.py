import base64
import json
import shutil
from contextlib import closing
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from support import workspace_temp
from conversion.ai_batches import (ai_batch_status, database, plan_ai_batch,
                                   run_ai_batch)
from conversion.documents import digest, load_json
from conversion.jobs import prepare_job
from conversion.provider_errors import ProviderAttemptError, ProviderFault
from conversion.providers import execute_job
from conversion.storage import publish, verify_artifacts

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class AIBatchTests(TestCase):
    def setUp(self):
        fixture = workspace_temp()
        self.addCleanup(fixture.cleanup)
        self.base = Path(fixture.name)
        self.root = self.base / "datos"
        units, assets = [], {}
        for number in range(1, 4):
            name = f"imagenes/p{number:04d}.png"
            assets[name] = PNG
            units.append({"number": number, "image_asset": name, "image_sha256": digest(PNG),
                          "blocks": [{"id": f"p{number}-b1", "kind": "paragraph",
                                      "text": f"Embebido {number}\n"}]})
        doc = {"schema_version": 1, "title": "Tres páginas", "input_kind": "pdf_prepared",
               "expected_units": [1, 2, 3], "units": units}
        self.package = Path(publish(self.root, doc, assets)["output"])

    @staticmethod
    def payload(provider, model, contents, max_tokens, timeout, *, key=None, usage=100):
        job = load_json(contents["job.json"])
        answer = {"job_id": job["job_id"], "unit": job["unit"],
                  "blocks": [{"kind": "paragraph", "text": f"Leído {job['unit']}"}],
                  "warnings": []}
        result = {"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": json.dumps(answer)}]}}]}
        if usage is not None:
            result["usageMetadata"] = {"totalTokenCount": usage}
        return result

    def plan(self, **kwargs):
        return plan_ai_batch(self.root, self.package, "gemini-api", "modelo-prueba", **kwargs)

    def test_plan_is_deterministic_and_never_reads_key_or_calls_transport(self):
        with patch("conversion.providers.get_key", side_effect=AssertionError("clave")), \
                patch("conversion.providers._api", side_effect=AssertionError("transporte")):
            first = self.plan()
            second = self.plan()
            preview = run_ai_batch(self.root, first["id"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual([i["job_id"] for i in first["items"]],
                         [i["job_id"] for i in second["items"]])
        self.assertEqual(preview["status"], "vista_previa")
        self.assertEqual(preview["external_calls"], 0)
        self.assertTrue(Path(preview["report"]).is_file())

    def test_plan_records_explicit_cli_profile_without_contacting_provider(self):
        with patch("conversion.providers.cli_binary", side_effect=AssertionError("no ejecutar CLI")):
            plan = plan_ai_batch(self.root, self.package, "gemini-cli", "modelo-prueba",
                                 pages=[1], cli_profile="pro-3")
        self.assertEqual(plan["policy"]["cli_profile"], "pro-3")
        self.assertEqual(plan["external_calls"], 0)

    def test_success_saves_each_page_and_builds_one_ordered_markdown(self):
        plan = self.plan(call_budget=3, reported_token_budget=1000)
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload) as transport:
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(result["status"], "finalizado")
        self.assertEqual(result["counts"], {"terminado": 3})
        self.assertEqual(result["spent_calls"], 3)
        self.assertEqual(result["reported_tokens"], 300)
        self.assertEqual(result["external_calls"], 3)
        combined = Path(result["combined"]["output"])
        self.assertTrue(verify_artifacts(combined))
        text = (combined / "documento.md").read_text(encoding="utf-8")
        self.assertLess(text.index("Leído 1"), text.index("Leído 2"))
        self.assertLess(text.index("Leído 2"), text.index("Leído 3"))
        self.assertIn("Resultado unido", Path(result["report"]).read_text(encoding="utf-8"))

    def test_call_budget_stops_then_larger_new_plan_reuses_before_sending(self):
        small = self.plan(call_budget=1, reported_token_budget=1000)
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload) as first_transport:
            stopped = run_ai_batch(self.root, small["id"], send=True)
        self.assertEqual(first_transport.call_count, 1)
        diagnostic = {"status": stopped["status"], "counts": stopped["counts"],
            "items": [{"unit": item["unit"], "state": item["state"],
                       "phase": (item.get("result") or {}).get("phase"),
                       "code": (item.get("result") or {}).get("code")}
                      for item in stopped["items"]]}
        self.assertEqual(stopped["status"], "presupuesto_llamadas_agotado",
                         json.dumps(diagnostic, ensure_ascii=True))
        larger = self.plan(call_budget=3, reported_token_budget=1000)
        self.assertNotEqual(small["id"], larger["id"])
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload) as second_transport:
            finished = run_ai_batch(self.root, larger["id"], send=True)
        self.assertEqual(second_transport.call_count, 2)
        self.assertEqual(finished["status"], "finalizado")
        self.assertEqual(finished["counts"]["reutilizado"], 1)
        self.assertEqual(finished["spent_calls"], 2)

    def test_transient_publication_lock_keeps_single_call_and_reported_tokens(self):
        plan = self.plan(call_budget=1, reported_token_budget=1000)
        original_rename = Path.rename
        publication_attempts = []
        def transient_lock(source, target):
            if source.parent == self.root / "pendientes":
                publication_attempts.append((source, target))
                if len(publication_attempts) == 1:
                    failure = PermissionError(13, "bloqueo transitorio sintético")
                    failure.winerror = 5
                    raise failure
            return original_rename(source, target)
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload) as transport, \
                patch.object(Path, "rename", transient_lock):
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertGreaterEqual(len(publication_attempts), 2)
        self.assertEqual(publication_attempts[0], publication_attempts[1])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["status"], "presupuesto_llamadas_agotado", result["items"])
        self.assertEqual(result["spent_calls"], 1)
        self.assertEqual(result["reported_tokens"], 100)
        self.assertEqual(result["unknown_calls"], 0)
        self.assertEqual(result["counts"], {"terminado": 1, "listo": 2})
        first = result["items"][0]["result"]
        self.assertTrue(verify_artifacts(Path(first["output"])))
        folder = Path(first["run_folder"])
        self.assertTrue((folder / "respuesta.json").is_file())
        receipt = load_json((folder / "recibo.json").read_bytes())
        self.assertEqual(receipt["usage"]["totalTokenCount"], 100)

    def test_reported_token_budget_can_overshoot_once_but_never_starts_next(self):
        plan = self.plan(call_budget=3, reported_token_budget=50)
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload) as transport:
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["reported_tokens"], 100)
        self.assertEqual(result["status"], "presupuesto_tokens_reportados_agotado")
        self.assertEqual(result["counts"], {"terminado": 1, "listo": 2})

    def test_unknown_usage_stops_and_is_never_zero(self):
        plan = self.plan(call_budget=3, reported_token_budget=1000)
        no_usage = lambda *args, **kwargs: self.payload(*args, **kwargs, usage=None)
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=no_usage) as transport:
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["status"], "requiere_revision")
        self.assertEqual(result["counts"], {"consumo_desconocido": 1, "listo": 2})
        self.assertEqual(result["reported_tokens"], 0)
        self.assertEqual(result["unknown_calls"], 1)
        with patch("conversion.providers._api", side_effect=AssertionError("no reintentar")):
            self.assertEqual(run_ai_batch(self.root, plan["id"], send=True)["external_calls"], 0)

    def test_transport_failure_stops_and_reservation_blocks_retry(self):
        plan = self.plan()
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=ProviderFault("network_timeout")) as transport:
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["external_calls"], 1)
        self.assertEqual(result["counts"], {"requiere_revision": 1, "listo": 2})
        self.assertEqual(result["unknown_calls"], 1)
        with patch("conversion.providers._api", side_effect=AssertionError("no reintentar")):
            run_ai_batch(self.root, plan["id"], send=True)

    def test_preparation_failure_is_not_counted_as_a_provider_call(self):
        plan = self.plan(pages=[1])
        attempt = self.root / "ejecuciones" / ("f" * 32)
        attempt.mkdir(parents=True)
        preview = {"signature": plan["items"][0]["job_id"]}
        failure = ProviderAttemptError("gemini-api", "preparacion", "OSError", attempt)
        with patch("conversion.ai_batches.execute_job", side_effect=[preview, failure]):
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(result["external_calls"], 0)
        self.assertEqual(result["spent_calls"], 0)
        self.assertEqual(result["unknown_calls"], 0)

    def test_crash_after_provider_response_recovers_without_second_call(self):
        plan = self.plan(pages=[1], call_budget=1)
        item = plan["items"][0]
        with closing(database(self.root)) as db, db:
            db.execute("UPDATE ai_items SET state='en_curso' WHERE batch=?", (plan["id"],))
        job = self.root / "encargos" / item["job_id"]
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload):
            execute_job(self.root, job, "gemini-api", "modelo-prueba", send=True, max_tokens=8192)
        with patch("conversion.providers.get_key", side_effect=AssertionError("reuso sin clave")), \
                patch("conversion.providers._api", side_effect=AssertionError("no segundo envío")):
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(result["status"], "finalizado")
        self.assertEqual(result["spent_calls"], 1)
        self.assertEqual(result["external_calls"], 0)

    def test_missing_key_is_blocked_without_spending_and_can_resume(self):
        plan = self.plan(pages=[1])
        with patch("conversion.providers.get_key", return_value=None), patch("conversion.providers._api") as transport:
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(result["status"], "bloqueado")
        self.assertEqual(result["spent_calls"], 0)
        transport.assert_not_called()
        with patch("conversion.providers.get_key", return_value="fixture"), \
                patch("conversion.providers._api", side_effect=self.payload):
            result = run_ai_batch(self.root, plan["id"], send=True)
        self.assertEqual(result["status"], "finalizado")

    def test_moved_memory_resolves_local_paths_and_preview_is_offline(self):
        plan = self.plan(pages=[2])
        moved = self.base / "movida"
        shutil.copytree(self.root, moved)
        with patch("conversion.providers.get_key", side_effect=AssertionError("offline")):
            result = run_ai_batch(moved, plan["id"])
        self.assertTrue(Path(result["source_package"]).is_relative_to(moved))
        self.assertTrue(Path(result["report"]).is_relative_to(moved))
