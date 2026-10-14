"""S1 graph identities, namespace relationships and pinned canonical provenance."""
import asyncio
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-engine"))
import config
from indexer import index_directory
from knowledge_base.graph import KnowledgeGraph, build_graph
from knowledge_base.metadata import audit, trace
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.contracts import OperationRequest, validate_engine_result
from query_system.graph_engine import GraphQueryEngine


FILES = {
    "dep.ts": "export function run(value: string) { return value; }\r\n",
    "barrel.ts": "export * as lib from './dep';\n",
    "use.ts": "import {lib} from './barrel';\nexport function outer() {\n  function inner() { return lib.run('é'); }\n  return inner();\n}\n",
    "entry.js": "import * as app from './use';\nexport function entry() { return app.outer(); }\n",
}


class ResolverS1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cm-s1-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo with spaces"; self.root.mkdir()
        self.data = Path(self.temp.name) / "private"
        for relative, source in FILES.items():
            (self.root / relative).write_bytes(source.encode())

    def test_namespace_members_nested_and_javascript_edges_use_canonical_symbols(self):
        built = build_graph(self.root, data_dir=self.data)
        graph = KnowledgeGraph(built["database"], built["repository_id"])
        target = graph.symbol("run")
        self.assertEqual({item["other_label"] for item in graph.direct_callers(target["symbol_id"])}, {"outer.inner"})
        self.assertEqual({item["other_label"] for item in graph.direct_callees(graph.symbol("entry")["symbol_id"])}, {"outer"})
        self.assertEqual(graph.dependency_paths("entry.js", "dep.ts", max_depth=3), [["entry.js", "use.ts", "barrel.ts", "dep.ts"]])
        with closing(sqlite3.connect(built["database"])) as db:
            identities = [row[0] for row in db.execute("SELECT id FROM relationships")]
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            # A namespace export is a module, not a made-up function symbol.
            self.assertIn(("EXPORTS", "lib", "export_target_not_indexed"), db.execute("SELECT relationship,expression,reason FROM graph_unresolved").fetchall())
        for identifier in identities:
            info = trace(built["database"], built["repository_id"], identifier)
            if info.get("path"):
                self.assertEqual(info["sourceHash"], hashlib.sha256((self.root / info["path"]).read_bytes()).hexdigest())
        self.assertEqual(audit(built["database"])["dangling_references"], [])

    def test_repeated_build_preserves_symbol_node_edge_identity_and_observations(self):
        def rows(database):
            with closing(sqlite3.connect(database)) as db:
                return {table: db.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()
                        for table in ("symbols", "graph_nodes", "graph_relationships", "graph_unresolved")}
        first = build_graph(self.root, data_dir=self.data)
        before = rows(first["database"])
        second = build_graph(self.root, data_dir=self.data)
        self.assertEqual(before, rows(second["database"]))
        self.assertEqual(first["graph"], second["graph"])
        self.assertEqual(first["semantic_diagnostics"], second["semantic_diagnostics"])

    def test_namespace_callers_keep_one_published_pin_hash_ranges_and_ready_stage_ids(self):
        repo = config.make_repo_id(str(self.root))
        store = SnapshotStore(self.root, repo, self.data)
        client = FaissClient(self.data / "vectors"); self.addCleanup(client.close)
        # Fixed vectors only test publication/provenance, never retrieval quality.
        with patch("indexer.get_qdrant_client", return_value=client), patch("indexer.get_embedding_model"), patch("indexer.embed_texts", side_effect=lambda texts: [[1.] + [0.] * 383 for _ in texts]):
            store.publish(client=client, collection=config.COLLECTION_NAME, index_directory=index_directory, build_graph=build_graph)
        with patch.object(store, "pin_view", wraps=store.pin_view) as pins, GraphQueryEngine.pinned(store) as engine:
            operation = OperationRequest(engine="graph", name="find_callers", repository_id=repo,
                snapshot_id=engine.context.snapshot_id, parameters={"reference": "run"})
            result = asyncio.run(engine.execute(operation, engine.context))
            validate_engine_result(result, engine.context, engine.evidence)
            self.assertEqual(result.status, "success")
            self.assertEqual({item.source_name for item in result.findings}, {"outer.inner"})
            self.assertEqual(pins.call_count, 1)
            proofs = list(engine.evidence.values())
            self.assertTrue(proofs)
            for proof in proofs:
                self.assertEqual((proof.repository_id, proof.snapshot_id), (repo, engine.context.snapshot_id))
                self.assertIn(proof.index_snapshot_id, engine.context.ready_stage_ids)
                self.assertEqual(proof.source_hash, hashlib.sha256((self.root / proof.file_path).read_bytes()).hexdigest())
                if proof.evidence_type == "call_site":
                    self.assertEqual((proof.file_path, proof.start_line, proof.end_line), ("use.ts", 3, 3))
            self.assertGreater(len({proof.index_snapshot_id for proof in proofs}), 1)
        self.assertTrue(store.matches(result.snapshot_id))
        self.assertEqual(store.active()["id"], result.snapshot_id)
