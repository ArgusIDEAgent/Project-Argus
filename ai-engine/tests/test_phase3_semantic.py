"""Real stored generations with fixed vectors; quality uses a separate real-model benchmark."""

import asyncio
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api
import config
from indexer import index_directory
from knowledge_base.benchmark_semantic import FIXTURES
from knowledge_base.graph import build_graph
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.contracts import OperationRequest, validate_engine_result
from query_system.semantic_engine import (
    ChannelResult, SemanticSearchEngine, existing_api_channels,
)


class SemanticEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.data = Path(self.temp.name) / "data"
        for path, _, source in FIXTURES:
            (self.root / path).write_text(source)
        self.repo = config.make_repo_id(str(self.root))
        self.store = SnapshotStore(self.root, self.repo, self.data)
        self.client = FaissClient(self.data / "vectors")
        self.addCleanup(self.client.close)
        api._REGISTERED_REPOS[self.repo] = {"rootPath": str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop, self.repo, None)
        for name, kwargs in (
            ("api.get_qdrant_client", {"return_value": self.client}),
            ("indexer.get_qdrant_client", {"return_value": self.client}),
            ("api.embed_texts", {"side_effect": lambda texts: [[1.] + [0.] * 383 for _ in texts]}),
            ("indexer.embed_texts", {"side_effect": lambda texts: [[1.] + [0.] * 383 for _ in texts]}),
            ("indexer.get_embedding_model", {}),
            ("api.DEFAULT_DATA_DIR", {"new": str(self.data)}),
        ):
            guard = patch(name, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
                           index_directory=index_directory, build_graph=build_graph)
        self.factory = existing_api_channels(api, config.EMBEDDING_MODEL_NAME)

    def operation(self, engine, mode="hybrid_search", **parameters):
        return OperationRequest(engine="semantic", name=mode, repository_id=self.repo,
                                snapshot_id=engine.context.snapshot_id,
                                parameters={"query": "Where are notification emails sent to subscribers?", **parameters})

    def execute(self, mode="hybrid_search", factory=None, **parameters):
        with SemanticSearchEngine.pinned(self.store, factory or self.factory) as engine:
            result = asyncio.run(engine.execute(self.operation(engine, mode, **parameters), engine.context))
            validate_engine_result(result, engine.context, engine.evidence)
            return result, dict(engine.evidence)

    def test_semantic_query_returns_ranked_canonical_evidence(self):
        result, evidence = self.execute("semantic_search", filters={"files": ["auth.ts"]})
        self.assertEqual(result.status, "success")
        self.assertEqual([f.rank for f in result.findings], list(range(1, len(result.findings) + 1)))
        for finding in result.findings:
            self.assertEqual(finding.retrieval_methods, ["semantic"])
            self.assertEqual(finding.retrieval_score, 1.)
            source = evidence[finding.chunk_id]
            self.assertEqual(source.file_path, "auth.ts")
            self.assertEqual(source.source_hash, hashlib.sha256((self.root / "auth.ts").read_bytes()).hexdigest())
            self.assertEqual(source.repository_id, self.repo)
            self.assertEqual(source.snapshot_id, result.snapshot_id)
            self.assertNotEqual(source.index_snapshot_id, source.snapshot_id)
            self.assertEqual(source.source_origin, "disk")
            self.assertIsNotNone(source.symbol_id)

    def test_keyword_and_vector_fusion_preserve_separate_scores(self):
        result, _ = self.execute()
        self.assertEqual(result.status, "success")
        fused = [f for f in result.findings if set(f.retrieval_methods) == {"keyword", "semantic"}]
        self.assertTrue(fused)
        for finding in fused:
            self.assertIsNotNone(finding.keyword_score)
            self.assertEqual(finding.retrieval_score, 1.)
            expected = sum(finding.ranking_signals[method]["rrf"] for method in finding.retrieval_methods)
            self.assertAlmostEqual(finding.fusion_score, expected)

    def test_keyword_only_uses_no_vector_or_embedding_calls(self):
        with patch.object(api, "get_qdrant_client", side_effect=AssertionError("Keyword mode cannot use vectors")), \
             patch.object(api, "embed_texts", side_effect=AssertionError("Keyword mode cannot embed")):
            result, _ = self.execute("keyword_search", query="ACCESS_DENIED")
        self.assertEqual(result.status, "success")
        self.assertTrue(all(f.retrieval_score is None for f in result.findings))
        self.assertTrue(all(f.retrieval_methods == ["keyword"] for f in result.findings))

    def test_filters_and_python_null_symbol_identity(self):
        for filters in ({"files": ["payments.py"]}, {"modules": ["payments"]},
                        {"languages": ["PYTHON"]}, {"symbolTypes": ["FUNCTION"]}):
            with self.subTest(filters=filters):
                result, evidence = self.execute("semantic_search", filters=filters)
                self.assertEqual(result.status, "success")
                if "files" in filters or "modules" in filters:
                    self.assertEqual({e.file_path for e in evidence.values()}, {"payments.py"})
                if "languages" in filters:
                    self.assertTrue(all(e.file_path.endswith(".py") for e in evidence.values()))
                    self.assertTrue(all(e.symbol_id is None for e in evidence.values()))

    def test_invalid_and_unsupported_filters_are_explicit(self):
        for filters in ({"symbolIds": ["unknown"]}, {"directories": ["src"]},
                        {"files": ["../escape.ts"]}, {"languages": [""]}):
            with self.subTest(filters=filters):
                result, _ = self.execute(filters=filters)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.error.code, "invalid_operation")

    def test_empty_results_and_empty_filtered_vector_scope_are_healthy(self):
        for mode, parameters in (("keyword_search", {"query": "zzzzz_missing_token"}),
                                 ("semantic_search", {"filters": {"files": ["absent.ts"]}}),
                                 ("hybrid_search", {"filters": {"languages": ["swift"]}})):
            with self.subTest(mode=mode):
                result, _ = self.execute(mode, **parameters)
                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.error)

    def test_index_unavailable_has_explicit_keyword_fallback(self):
        active = self.store.active()
        self.client.delete_collection(active["collection"])
        result, _ = self.execute("semantic_search", query="Where is ACCESS_DENIED raised?")
        self.assertEqual(result.status, "partial")
        self.assertTrue(result.findings)
        self.assertTrue(all(f.retrieval_methods == ["keyword"] for f in result.findings))
        self.assertTrue(any("Partial retrieval" in warning for warning in result.warnings))
        no_fallback, _ = self.execute("semantic_search", query="zzzzz_missing_token")
        self.assertEqual(no_fallback.status, "error")
        self.assertEqual(no_fallback.error.code, "unavailable")

    def test_semantic_model_failure_and_missing_vectors_are_not_empty_successes(self):
        with patch.object(api, "embed_texts", side_effect=RuntimeError("offline")):
            result, _ = self.execute(query="Where is ACCESS_DENIED raised?")
            self.assertEqual(result.status, "partial")
        active = self.store.active()
        self.client.delete(active["collection"], api.Filter(must=[api.FieldCondition(
            key="repo_id", match=api.MatchValue(value=self.repo))]))
        result, _ = self.execute(query="Where is ACCESS_DENIED raised?")
        self.assertEqual(result.status, "partial")
        self.assertTrue(any("missing vectors" in warning for warning in result.warnings))

    def test_embedding_model_mismatch_never_compares_incompatible_vectors(self):
        factory = lambda view: replace(self.factory(view), embedding_model="wrong-model")
        with patch.object(api, "embed_texts", side_effect=AssertionError("Mismatched models cannot embed")):
            result, _ = self.execute("semantic_search", factory=factory, query="ACCESS_DENIED")
        self.assertEqual(result.status, "partial")
        self.assertTrue(all(f.retrieval_score is None for f in result.findings))
        self.assertTrue(any("model differs" in warning for warning in result.warnings))

    def test_unavailable_keyword_channel_can_return_partial_semantic_results(self):
        factory = lambda view: replace(self.factory(view), keyword_search=lambda *args: ChannelResult("unavailable", [], "FTS offline"))
        result, _ = self.execute(factory=factory)
        self.assertEqual(result.status, "partial")
        self.assertTrue(all(f.retrieval_methods == ["semantic"] for f in result.findings))

    def test_duplicate_channel_hits_are_fused_once(self):
        def factory(view):
            channels = self.factory(view)
            def duplicates(*args):
                found = channels.semantic_search(*args)
                return replace(found, results=found.results + found.results)
            return replace(channels, semantic_search=duplicates)
        normal, _ = self.execute("semantic_search")
        duplicated, evidence = self.execute("semantic_search", factory=factory)
        self.assertEqual([f.chunk_id for f in normal.findings], [f.chunk_id for f in duplicated.findings])
        self.assertEqual(len(evidence), len(duplicated.findings))

    def test_stale_disk_sources_and_mid_query_edits_discard_results(self):
        with SemanticSearchEngine.pinned(self.store, self.factory) as engine:
            (self.root / "auth.ts").write_text("export const changed = true;\n")
            result = asyncio.run(engine.execute(self.operation(engine), engine.context))
            self.assertEqual(result.status, "stale_index")
            self.assertEqual(result.findings, [])
        (self.root / "auth.ts").write_text(next(source for path, _, source in FIXTURES if path == "auth.ts"))
        def factory(view):
            channels = self.factory(view)
            def changed(*args):
                found = channels.semantic_search(*args)
                (self.root / "auth.ts").write_text("export const changed = true;\n")
                return found
            return replace(channels, semantic_search=changed)
        result, _ = self.execute(factory=factory)
        self.assertEqual(result.status, "stale_index")
        self.assertEqual(result.evidence_ids, [])

    def test_invalid_hash_range_and_stage_evidence_are_rejected(self):
        for field, value in (("sourceHash", "0" * 64), ("startLine", 999), ("snapshotId", "wrong-stage")):
            def factory(view):
                channels = self.factory(view)
                def poison(*args):
                    found = channels.semantic_search(*args)
                    return replace(found, results=[{**found.results[0], field: value}, *found.results[1:]])
                return replace(channels, semantic_search=poison)
            with self.subTest(field=field):
                result, _ = self.execute("semantic_search", factory=factory)
                self.assertEqual(result.status, "stale_index")
                self.assertEqual(result.findings, [])

    def test_unready_metadata_cannot_appear_as_empty_results(self):
        with SemanticSearchEngine.pinned(self.store, self.factory) as engine:
            with closing(sqlite3.connect(engine.database)) as db, db:
                db.execute("UPDATE index_snapshots SET state='failed' WHERE stage='semantic'")
            result = asyncio.run(engine.execute(self.operation(engine, filters={"files": ["absent.ts"]}), engine.context))
            self.assertEqual(result.status, "stale_index")

    def test_missing_repository_mixed_generation_and_expired_scope(self):
        with SemanticSearchEngine.pinned(self.store, self.factory) as engine:
            operation = self.operation(engine)
            wrong = operation.model_copy(update={"snapshot_id": "0" * 32})
            self.assertEqual(asyncio.run(engine.execute(wrong, engine.context)).status, "stale_index")
            wrong = operation.model_copy(update={"repository_id": "1" * 24})
            self.assertEqual(asyncio.run(engine.execute(wrong, engine.context)).status, "error")
            api._REGISTERED_REPOS.pop(self.repo)
            missing = asyncio.run(engine.execute(operation, engine.context))
            self.assertEqual(missing.status, "error")
            self.assertIn("Repository", missing.error.message)
        self.assertEqual(asyncio.run(engine.execute(operation, engine.context)).status, "stale_index")

    def test_stable_repeated_retrieval_read_only_no_reindex_and_no_llm(self):
        with SemanticSearchEngine.pinned(self.store, self.factory) as engine:
            before = hashlib.sha256(engine.database.read_bytes()).hexdigest()
            operation = self.operation(engine)
            with patch("answers.urlopen", side_effect=AssertionError("No LLM allowed")), \
                 patch("indexer.index_directory", side_effect=AssertionError("No reindex allowed")), \
                 patch("indexer.embed_texts", side_effect=AssertionError("Stored embeddings cannot rebuild")):
                first = asyncio.run(engine.execute(operation, engine.context))
                second = asyncio.run(engine.execute(operation, engine.context))
            self.assertEqual(first.model_dump(exclude={"execution_ms"}), second.model_dump(exclude={"execution_ms"}))
            self.assertEqual(before, hashlib.sha256(engine.database.read_bytes()).hexdigest())
            self.assertTrue(all(item.index_snapshot_id in engine.context.ready_stage_ids for item in engine.evidence.values()))

    def test_hybrid_feature_search_matches_existing_baseline_order(self):
        with SemanticSearchEngine.pinned(self.store, self.factory) as engine:
            query = "Where are notification emails sent to subscribers?"
            baseline = api._retrieve_pinned(api.SearchRequest(repoId=self.repo, query=query, retrieval="hybrid"), engine.view)
            result = asyncio.run(engine.execute(self.operation(engine, query=query), engine.context))
            self.assertEqual([f.chunk_id for f in result.findings], [row["entityId"] for row in baseline["results"]])
            self.assertEqual([f.fusion_score for f in result.findings], [row["fusionScore"] for row in baseline["results"]])

    def test_search_does_not_route_to_graph_or_expand_relationships(self):
        with patch("knowledge_base.retrieval.HybridRetriever._graph", side_effect=AssertionError("No graph routing")), \
             patch("knowledge_base.retrieval.HybridRetriever._lookup", side_effect=AssertionError("No symbol routing")), \
             patch("knowledge_base.retrieval.HybridRetriever._expand", side_effect=AssertionError("No graph expansion")):
            result, _ = self.execute(query="Explain function authorization", filters={"files": ["auth.ts"]})
        self.assertEqual(result.status, "success")
        self.assertTrue(all(f.kind == "semantic" for f in result.findings))

    def test_poisoned_vector_payload_is_reported_as_stale(self):
        collection = self.store.active()["collection"]
        with self.client._guard(), self.client.db:
            identifier, payload = self.client.db.execute(
                "SELECT chunk_id,payload FROM vectors WHERE collection_name=? ORDER BY chunk_id LIMIT 1", (collection,)).fetchone()
            payload = json.loads(payload)
            payload["file_id"] = "wrong-canonical-file"
            self.client.db.execute("UPDATE vectors SET payload=? WHERE collection_name=? AND chunk_id=?",
                                   (json.dumps(payload), collection, identifier))
        result, _ = self.execute("semantic_search")
        self.assertEqual(result.status, "stale_index")
        self.assertEqual(result.findings, [])

    def test_unsupported_operation_and_execution_budget(self):
        unsupported, _ = self.execute("find_callers")
        self.assertEqual(unsupported.status, "unsupported")
        with SemanticSearchEngine.pinned(self.store, self.factory) as engine:
            with patch("query_system.semantic_engine.monotonic", side_effect=[0., 16.]):
                result = asyncio.run(engine.execute(self.operation(engine), engine.context))
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.error.code, "budget_exceeded")

    def test_channel_cannot_ignore_or_spoof_a_canonical_filter(self):
        def factory(view):
            channels = self.factory(view)
            def ignores_filter(query, filters, limit):
                found = channels.semantic_search(query, {"languages": ["python"]}, limit)
                return replace(found, results=[{**row, "language": "typescript"} for row in found.results])
            return replace(channels, semantic_search=ignores_filter)
        result, _ = self.execute("semantic_search", factory=factory, filters={"languages": ["typescript"]})
        self.assertEqual(result.status, "stale_index")
        self.assertEqual(result.findings, [])


if __name__ == "__main__":
    unittest.main()
