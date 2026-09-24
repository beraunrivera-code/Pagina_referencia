from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from conversion import semantic_judgments as sj
from conversion.documents import json_bytes, load_json
from support import workspace_temp


class SemanticTests(TestCase):
    def setUp(self):
        temporary = workspace_temp()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = {"query": "responsabilidad de diseño", "status": "derivados_disponibles",
                      "external_calls": 0, "hits": [
                          {"document_id": str(i) * 64, "unit": 1, "block_id": "p1-b1",
                           "excerpt": f"Responsabilidad de diseño: control sintético {i}",
                           "markdown": "C:/ruta-privada/documento.md", "semantic_verified": False,
                           "excerpt_truncated": i == 1, "status": "revisar"} for i in (1, 2)]}

    def payload(self, values=(0, 2)):
        return json_bytes({"model": sj.DEFAULT_MODEL, "answers": {
            f"r{i}": {"type": "score", "score": value, "confidence": 1.0,
                      "legend": {str(j): text for j, text in enumerate(sj.LEVELS)},
                      "probabilities": {str(j): float(j == value) for j in range(3)}}
            for i, value in enumerate(values)}, "usage": {"input_tokens": 80, "output_tokens": 15}})

    def send(self, **kwargs):
        return sj.rerank(self.root, self.local, send=True,
                         expected_id=sj.preview(self.local)["id"], **kwargs)

    def test_preview_is_local_and_no_key_or_write(self):
        with patch.object(sj, "_key", side_effect=AssertionError("no leer clave")), \
                patch.object(sj, "_transport", side_effect=AssertionError("no red")):
            result = sj.rerank(self.root, self.local)
        self.assertEqual(result["external_calls"], 0)
        self.assertEqual(result["ranking"]["status"], "vista_previa")
        self.assertFalse((self.root / "juicios_textuales").exists())
        payload = json_bytes(result["ranking"]["preview"]["body"])
        self.assertNotIn(b"ruta-privada", payload)
        self.assertIn(b"candidates[0].text", payload)

    def test_exact_preview_required(self):
        with patch.object(sj, "_transport") as transport:
            with self.assertRaises(ValueError):
                sj.rerank(self.root, self.local, send=True)
        transport.assert_not_called()

    def test_rank_keeps_all_provenance_and_never_changes_fidelity(self):
        original = deepcopy(self.local)
        with patch.object(sj, "_key", return_value="secret-fixture"), \
                patch.object(sj, "_transport", return_value=self.payload()) as transport:
            result = self.send()
        self.assertEqual(self.local, original)
        self.assertEqual([h["document_id"] for h in result["hits"]], ["2" * 64, "1" * 64])
        self.assertEqual(len(result["hits"]), 2)
        self.assertFalse(any(h["semantic_verified"] for h in result["hits"]))
        self.assertTrue(result["hits"][1]["excerpt_truncated"])
        transport.assert_called_once()
        for path in (self.root / "juicios_textuales").rglob("*.json"):
            self.assertNotIn(b"secret-fixture", path.read_bytes())

    def test_cache_reuse_does_not_need_key_or_send(self):
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=self.payload()):
            self.send()
        with patch.object(sj, "_key", side_effect=AssertionError("no clave")), \
                patch.object(sj, "_transport", side_effect=AssertionError("no segundo cobro")):
            result = self.send()
        self.assertTrue(result["ranking"]["cached"])
        self.assertEqual(result["external_calls"], 0)

    def test_receipt_precedes_network_and_records_unknown(self):
        def interrupted(body, key, timeout):
            receipt = next((self.root / "juicios_textuales").rglob("recibo.json"))
            value = load_json(receipt.read_bytes())
            self.assertEqual(value["status"], "enviado_o_incierto")
            self.assertIsNone(value["usage"]["input_tokens"])
            raise sj.JudgmentError("transporte_incierto")
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", side_effect=interrupted) as transport:
            result = self.send()
            again = self.send()
        self.assertEqual(result["hits"], self.local["hits"])
        self.assertEqual(again["ranking"]["status"], "bloqueado")
        self.assertIsNone(result["ranking"]["receipt"]["usage"]["input_tokens"])
        transport.assert_called_once()

    def test_invalid_answer_keeps_usage_before_validation(self):
        payload = load_json(self.payload())
        payload["answers"].pop("r1")
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=json_bytes(payload)):
            result = self.send()
        self.assertEqual(result["ranking"]["status"], "fallo")
        self.assertEqual(result["ranking"]["receipt"]["usage"]["input_tokens"], 80)
        self.assertEqual(result["hits"], self.local["hits"])

    def test_malformed_json_preserves_unknown_not_zero(self):
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=b'{bad'):
            result = self.send()
        self.assertIsNone(result["ranking"]["receipt"]["usage"]["input_tokens"])

    def test_http_failure_is_not_retried(self):
        for code in ("http_401", "http_429", "http_529"):
            with self.subTest(code=code):
                self.local["query"] = code
                with patch.object(sj, "_key", return_value="fixture"), \
                        patch.object(sj, "_transport", side_effect=sj.JudgmentError(code)) as transport:
                    self.assertEqual(self.send()["ranking"]["status"], "fallo")
                    self.assertEqual(self.send()["ranking"]["status"], "bloqueado")
                transport.assert_called_once()

    def test_cache_hash_mismatch_blocks_resend(self):
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=self.payload()):
            self.send()
        next((self.root / "juicios_textuales").rglob("respuesta.json")).write_bytes(b"alterado")
        with patch.object(sj, "_transport") as transport:
            self.assertEqual(self.send()["ranking"]["status"], "bloqueado")
        transport.assert_not_called()

    def test_model_version_and_modified_input_invalidate_preview(self):
        before = sj.preview(self.local)["id"]
        self.local["hits"][0]["excerpt"] += " modificado"
        self.assertNotEqual(before, sj.preview(self.local)["id"])
        self.assertNotEqual(sj.preview(self.local)["id"], sj.preview(self.local, "jev-1.12.0")["id"])
        with self.assertRaises(ValueError):
            sj.preview(self.local, "jev-latest")

    def test_duplicate_or_oversized_candidates_rejected_without_silent_cut(self):
        self.local["hits"].append(deepcopy(self.local["hits"][0]))
        with self.assertRaises(ValueError):
            sj.preview(self.local)
        self.local["hits"].pop()
        self.local["hits"][0]["excerpt"] = "a" * 5001
        with self.assertRaises(ValueError):
            sj.preview(self.local)

    def test_empty_results_do_not_send(self):
        self.local["hits"] = []
        with patch.object(sj, "_key", side_effect=AssertionError("no clave")), patch.object(sj, "_transport") as transport:
            result = self.send()
        self.assertEqual(result["external_calls"], 0)
        transport.assert_not_called()

    def test_bad_probability_model_and_nan_rejected(self):
        plan = sj.preview(self.local)
        for field, value in (("score", float("nan")), ("confidence", True), ("probabilities", {"0": 1})):
            raw = load_json(self.payload())
            raw["answers"]["r0"][field] = value
            with self.assertRaises(sj.JudgmentError):
                sj._scores(raw, plan)
        raw = load_json(self.payload())
        raw["model"] = "otro"
        with self.assertRaises(sj.JudgmentError):
            sj._scores(raw, plan)

    def test_redirects_are_rejected(self):
        with self.assertRaises(sj.JudgmentError):
            sj._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_swapped_cache_never_reuses_foreign_receipt(self):
        import shutil
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=self.payload()):
            self.send()
        first = self.root / "juicios_textuales" / sj.preview(self.local)["id"]
        self.local["query"] = "Otra consulta"
        second = self.root / "juicios_textuales" / sj.preview(self.local)["id"]
        shutil.copytree(first, second)
        with patch.object(sj, "_transport") as transport:
            result = self.send()
        self.assertEqual(result["ranking"]["status"], "bloqueado")
        transport.assert_not_called()

    def test_receipt_fsynced_before_network(self):
        calls = []
        def transport(*args):
            self.assertGreaterEqual(len(calls), 4)  # solicitud y recibo, antes y después de rename
            return self.payload()
        with patch.object(sj.os, "fsync", side_effect=lambda fd: calls.append(fd)), \
                patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", side_effect=transport):
            self.assertEqual(self.send()["ranking"]["status"], "reordenado")

    def test_extreme_integer_rejected_and_usage_preserved(self):
        payload = load_json(self.payload())
        payload["answers"]["r0"]["score"] = 10 ** 400
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=json_bytes(payload)):
            result = self.send()
        self.assertEqual(result["ranking"]["status"], "fallo")
        self.assertEqual(result["ranking"]["receipt"]["usage"]["input_tokens"], 80)
        self.assertEqual(result["hits"], self.local["hits"])

    def test_truncated_http_falls_back_and_blocks_retry(self):
        from http.client import IncompleteRead
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj.request, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.side_effect = IncompleteRead(b"partial")
            result = self.send()
            again = self.send()
        self.assertEqual(result["ranking"]["status"], "fallo")
        self.assertEqual(result["hits"], self.local["hits"])
        self.assertEqual(again["ranking"]["status"], "bloqueado")
        opener.return_value.open.assert_called_once()

    def test_recovered_identifier_is_distinct_and_valid(self):
        original_id = sj.preview(self.local)["id"]
        self.local["hits"][0]["document_id"] += "-recuperado-deadbeef"
        preview = sj.preview(self.local)
        self.assertNotEqual(preview["id"], original_id)
        self.assertTrue(preview["body"]["state"]["candidates"][0]["document_id"].endswith("-recuperado-deadbeef"))
        with patch.object(sj, "_key", return_value="fixture"), patch.object(sj, "_transport", return_value=self.payload()):
            self.assertEqual(self.send()["ranking"]["status"], "reordenado")
