"""Phase 0B: verify the existing snapshot-aware retrieval components together."""

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api
import config
from indexer import index_directory
from knowledge_base.graph import KnowledgeGraph, build_graph
from knowledge_base.metadata import trace
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.contracts import (
    EngineResult, Evidence, GraphFinding, QueryRequest, SemanticFinding,
    SnapshotContext, StaleIndexError, validate_engine_result,
)


class Phase0BTests(unittest.TestCase):
    """Exercise the Phase 0 acceptance path without adding production code."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.data = Path(self.temp.name) / "data"

        self.auth = self.root / "auth.ts"
        self.auth.write_text(
            "export function validateToken(token: string) {\n"
            "  // Reject invalid authentication tokens.\n"
            "  if (!token) throw new Error(\"TOKEN_INVALID\");\n"
            "  return token.length > 0;\n"
            "}\n"
        )
        (self.root / "login.ts").write_text(
            'import {validateToken} from "./auth";\n'
            "export function login(token: string) { return validateToken(token); }\n"
        )
        (self.root / "session.ts").write_text(
            'import {validateToken} from "./auth";\n'
            "export function refreshSession(token: string) {\n"
            "  return validateToken(token);\n"
            "}\n"
        )

        self.repo = config.make_repo_id(str(self.root))
        self.store = SnapshotStore(self.root, self.repo, self.data)
        self.client = FaissClient(self.data / "vectors")
        self.addCleanup(self.client.close)
        self.addCleanup(patch.stopall)

        api._REGISTERED_REPOS[self.repo] = {"rootPath": str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop, self.repo)

        for owner in ("api", "indexer"):
            patch(owner + ".get_qdrant_client", return_value=self.client).start()
            patch(owner + ".embed_texts", side_effect=lambda texts: [[1.0] + [0.0] * 383 for _ in texts]).start()
        patch("api.DEFAULT_DATA_DIR", str(self.data)).start()
        patch("indexer.get_embedding_model").start()
        patch.dict("os.environ", {"CODEMIND_AUTO_SYNC": "0", "CODEMIND_LOCAL_MODEL": ""}).start()

        self.publish()

    def publish(self):
        return self.store.publish(
            client=self.client,
            collection=config.COLLECTION_NAME,
            index_directory=index_directory,
            build_graph=build_graph,
        )

    def database(self):
        active = self.store.active()
        return self.store.data_for(active) / self.repo / "metadata.db"

    def ready_snapshots(self, database):
        with sqlite3.connect(database) as db:
            return {row[0] for row in db.execute(
                "SELECT id FROM index_snapshots WHERE repository_id=? AND state='ready'",
                (self.repo,),
            )}

    def assert_in_pinned_generation(self, database, view, *evidence):
        self.assertEqual(Path(database).parent.parent, self.store.data_for(view["manifest"]))
        ready = self.ready_snapshots(database)
        self.assertTrue(evidence)
        self.assertTrue(all(item["snapshotId"] in ready for item in evidence))

    def request(self, query, **options):
        return api.SearchRequest(repoId=self.repo, query=query, **options)

    def retrieve_pinned(self, query, **options):
        request = self.request(query, **options)
        with self.store.pin_view() as view:
            result = api._retrieve_pinned(request, view)
            return result, view

    def test_definition_source_callers_and_related_code_share_snapshot(self):
        result, view = self.retrieve_pinned("Where is validateToken defined?")
        self.assertTrue(view["fresh"])
        self.assertEqual(result["knowledgeSnapshotId"], view["snapshotId"])
        self.assertEqual(result["status"], "found")

        definition = result["results"][0]
        self.assertEqual(definition["symbol"], "validateToken")
        self.assertEqual(definition["symbol_id"], definition["symbolId"])
        self.assertTrue(definition["source_verified"])

        database = self.database()
        source = trace(database, self.repo, definition["entityId"])
        self.assertEqual(source["kind"], "symbol")
        self.assertEqual(source["path"], "auth.ts")
        self.assertEqual(source["symbolId"], definition["symbolId"])
        self.assertEqual(source["snapshotId"], definition["snapshotId"])
        self.assertEqual(source["sourceHash"], definition["sourceHash"])
        self.assert_in_pinned_generation(database, view, source)
        lines = self.auth.read_text().splitlines()
        self.assertEqual("\n".join(lines[source["startLine"] - 1:source["endLine"]]),
                         "\n".join(lines[0:5]))
        self.assertEqual(hashlib.sha256(self.auth.read_bytes()).hexdigest(), source["sourceHash"])

        graph = KnowledgeGraph(database, self.repo)
        callers = graph.direct_callers(definition["symbolId"])
        self.assertEqual({row["other_label"] for row in callers}, {"login", "refreshSession"})
        self.assertTrue(callers)
        for caller in callers:
            evidence = trace(database, self.repo, caller["id"])
            self.assertEqual(evidence["kind"], "relationship")
            self.assertEqual(evidence["relationship"], "CALLS")
            self.assertEqual(evidence["resolution"], "resolved")
            self.assertEqual(evidence["path"], caller["evidence_file"])
            with sqlite3.connect(database) as db:
                target = db.execute(
                    "SELECT id FROM graph_nodes WHERE repository_id=? AND symbol_id=?",
                    (self.repo, definition["symbolId"]),
                ).fetchone()
            self.assertIsNotNone(target)
            self.assertEqual(evidence["target_id"], target[0])
            self.assert_in_pinned_generation(database, view, evidence)

        related, related_view = self.retrieve_pinned(
            "Where are authentication tokens validated?", retrieval="hybrid", limit=5
        )
        self.assertTrue(related_view["fresh"])
        self.assertEqual(related["knowledgeSnapshotId"], view["snapshotId"])
        self.assertTrue(related["results"])
        self.assertTrue(any("semantic" in row["retrievalMethods"] for row in related["results"]))
        self.assertTrue(any(row["path"] in {"auth.ts", "login.ts", "session.ts"} for row in related["results"]))
        related_traces = []
        for item in related["results"]:
            self.assertTrue(item["source_verified"])
            traced = trace(database, self.repo, item["entityId"])
            related_traces.append(traced)
            self.assertEqual(item["snapshotId"], traced["snapshotId"])
            self.assertEqual(traced["sourceHash"], item["sourceHash"])
            self.assertEqual(traced["path"], item["path"])
        self.assert_in_pinned_generation(database, view, *related_traces)

    def test_missing_symbol_and_empty_query_results_are_explicit(self):
        missing, view = self.retrieve_pinned("Where is doesNotExist defined?")
        self.assertTrue(view["fresh"])
        self.assertEqual(missing["status"], "not_found")
        self.assertEqual(missing["results"], [])

        empty, empty_view = self.retrieve_pinned("zzzz_no_match_123", retrieval="keyword")
        self.assertTrue(empty_view["fresh"])
        self.assertEqual(empty["status"], "not_found")
        self.assertEqual(empty["results"], [])

    def test_ambiguous_symbol_is_not_guessed(self):
        (self.root / "other.ts").write_text(
            "export function validateToken(token: string) { return false; }\n"
        )
        self.publish()
        result, view = self.retrieve_pinned("Where is validateToken defined?")
        self.assertTrue(view["fresh"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual({item["path"] for item in result["results"]}, {"auth.ts", "other.ts"})
        database = self.database()
        traces = [trace(database, self.repo, item["entityId"]) for item in result["results"]]
        self.assert_in_pinned_generation(database, view, *traces)
        self.assertTrue(all(item["snapshotId"] == traced["snapshotId"]
                            for item, traced in zip(result["results"], traces)))
        with self.store.pin_view() as current_view:
            self.assertEqual(current_view["snapshotId"], view["snapshotId"])

    def test_stale_source_evidence_is_rejected(self):
        result, view = self.retrieve_pinned("Where is validateToken defined?")
        definition = result["results"][0]
        database = self.database()
        old_hash = definition["sourceHash"]
        self.auth.write_text(self.auth.read_text().replace("token.length > 0", "token.length > 1"))

        self.assertNotEqual(hashlib.sha256(self.auth.read_bytes()).hexdigest(), old_hash)
        with self.assertRaises(ValueError):
            trace(database, self.repo, definition["entityId"])
        with self.assertRaises(RuntimeError):
            KnowledgeGraph(database, self.repo).symbol("validateToken")
        with self.store.pin_view() as stale_view:
            self.assertFalse(stale_view["fresh"])
            self.assertEqual(stale_view["snapshotId"], view["snapshotId"])

    def test_phase1_contracts_bind_existing_evidence_to_pinned_generation(self):
        with self.store.pin_view() as view:
            context = SnapshotContext.from_pinned_view(view, self.repo)
            context.validate_request(QueryRequest(
                repository_id=self.repo, question="Who calls validateToken?",
                snapshot_id=context.snapshot_id,
            ))
            database = view["data_dir"] / self.repo / "metadata.db"
            definition = api._retrieve_pinned(self.request("validateToken"), view)["results"][0]
            symbol = trace(database, self.repo, definition["entityId"])
            callers = KnowledgeGraph(database, self.repo).direct_callers(symbol["symbolId"])
            self.assertTrue(callers)
            edge = trace(database, self.repo, callers[0]["id"])
            call_evidence = Evidence.from_trace(
                edge, context, evidence_type="graph_relationship", resolution=edge["resolution"],
            )
            graph_result = EngineResult(
                engine="graph", status="success", repository_id=self.repo,
                snapshot_id=context.snapshot_id, execution_ms=1,
                evidence_ids=[call_evidence.evidence_id],
                findings=[GraphFinding(relationship="CALLS", source_symbol_id="caller",
                                       target_symbol_id=symbol["symbolId"],
                                       evidence_ids=[call_evidence.evidence_id])],
            )
            validate_engine_result(graph_result, context, {call_evidence.evidence_id: call_evidence})

            retrieved = api._retrieve_pinned(self.request(
                "Where are authentication tokens validated?", retrieval="semantic", limit=5,
            ), view)
            self.assertEqual(retrieved["knowledgeSnapshotId"], context.snapshot_id)
            self.assertTrue(retrieved["results"])
            top = retrieved["results"][0]
            chunk = trace(database, self.repo, top["entityId"])
            semantic_evidence = Evidence.from_trace(
                chunk, context, evidence_type="code_chunk", resolution="not_applicable",
            )
            semantic_result = EngineResult(
                engine="semantic", status="success", repository_id=self.repo,
                snapshot_id=context.snapshot_id, execution_ms=1,
                evidence_ids=[semantic_evidence.evidence_id],
                findings=[SemanticFinding(chunk_id=chunk["entityId"], rank=1,
                                          retrieval_score=top["confidence"],
                                          evidence_ids=[semantic_evidence.evidence_id])],
            )
            validate_engine_result(semantic_result, context,
                                   {semantic_evidence.evidence_id: semantic_evidence})
            self.assertNotEqual(edge["snapshotId"], chunk["snapshotId"])
            self.assertIn(edge["snapshotId"], context.ready_stage_ids)
            self.assertIn(chunk["snapshotId"], context.ready_stage_ids)

        self.auth.write_text(self.auth.read_text().replace("token.length > 0", "token.length > 1"))
        with self.store.pin_view() as stale_view, self.assertRaises(StaleIndexError):
            SnapshotContext.from_pinned_view(stale_view, self.repo)


if __name__ == "__main__":
    unittest.main()
