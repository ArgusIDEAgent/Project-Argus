"""Isolated real-source context and qualification-tool regressions; no real model."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import answers
import test_phase6_execution as phase6
from test_phase8_generation import FakeLocal
from query_system.contracts import QueryRequest
from query_system.hybrid_contracts import HybridQueryRequest
from query_system.orchestration import run_hybrid
from query_system.qualify_phase3 import cached_embedding_model, lexical, save
from query_system.assess_phase3 import review_packet


class Phase3QualificationTests(unittest.TestCase):
    def test_human_packet_marks_transport_timeout_without_inventing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            save(path, {"ollama": {"details": [{"case": "function", "question": "Explain example",
                "response": {"generations": [{"model_status": "timeout"}]},
                "transport": [{"request_bytes": 123}], "generations": []}]}})
            packet = review_packet(path)
            self.assertIn("No response body was received", packet)
            self.assertIn("timeout", packet)

    def test_benchmark_reuses_one_local_only_embedding_session(self):
        cached_embedding_model.cache_clear()
        self.addCleanup(cached_embedding_model.cache_clear)
        with patch("fastembed.TextEmbedding") as constructor:
            self.assertIs(cached_embedding_model(), cached_embedding_model())
        self.assertEqual(constructor.call_count, 1)
        self.assertIs(constructor.call_args.kwargs["local_files_only"], True)
        self.assertEqual(constructor.call_args.kwargs["threads"], 2)

    def test_evidence_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            save(path, {"original": True})
            with self.assertRaises(FileExistsError): save(path, {"original": False})
            self.assertEqual(json.loads(path.read_text()), {"original": True})

    def test_nonfinite_measurement_is_not_saved_as_json_number(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): save(Path(folder) / "bad.json", {"ms": float("nan")})

    def test_lexical_failures_remain_explicit(self):
        results = lexical([{"id": "negative", "question": "Who calls login?", "rows": [["DIRECT_CALLEES", ["login"]]]}])
        self.assertFalse(results[0]["matches"])

    def test_real_workflow_fits_unchanged_default_context_budget(self):
        owner = phase6.ExecutionTests(); owner.setUp(); self.addCleanup(owner.doCleanups)
        project = Path(__file__).resolve().parents[2]
        for name in ("sourceNavigation.ts", "responsePreview.ts"):
            path = owner.fixture.root / "extension/src" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((project / "extension/src" / name).read_bytes())
        owner.fixture.publish()
        model = FakeLocal()
        run = asyncio.run(run_hybrid(owner.store, HybridQueryRequest(query=QueryRequest(repository_id=owner.repo,
            question="Explain the workflow from openVerifiedPreviewSource to verifiedSourcePath")), owner.channels, answers, client=model))
        self.assertTrue(run.generations)
        result = run.generations[0][1]
        self.assertTrue(result.context.core_ready[0], result.context.gaps)
        self.assertEqual(model.calls, 1)
        self.assertLessEqual(result.context.input_bytes, 8192 - 350 - 512)
        self.assertEqual(result.model_status, "success", result.failure_category)
        self.assertTrue(any(fact.data.get("path_node_ids") for fact in result.context.facts))
        payload = json.loads(result.context.messages[1]["content"])
        self.assertEqual({item["record_id"] for item in payload["sources"]}, {item.record_id for item in result.context.sources})
        self.assertEqual({item["fact_id"] for item in payload["facts"]}, {item.fact_id for item in result.context.facts})
        for coverage in payload["coverage"]:
            self.assertTrue(all(0 <= index < len(payload["coverage_limitations"]) for index in coverage["limitation_indices"]))
        self.assertTrue(all("provenance" not in source["data"] for source in payload["sources"]))
        self.assertTrue(all("provenance" in source.data for source in result.context.sources))


if __name__ == "__main__": unittest.main()
