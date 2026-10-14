"""Failure/retry and incremental indexing against disposable private stores."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import api
import config
import indexer
from knowledge_base.graph import build_graph
from knowledge_base.metadata import trace
from knowledge_base.scanner import _entry
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient


class RuntimeIndexingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cm-runtime-index-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"; self.root.mkdir()
        self.data = Path(temporary.name) / "private"
        self.file = self.root / "auth.ts"
        self.file.write_text('export function login() { return true; }\n')
        self.repo = config.make_repo_id(str(self.root))
        self.client = FaissClient(self.data / "vectors"); self.addCleanup(self.client.close)
        self.store = SnapshotStore(self.root, self.repo, self.data)
        for name, options in (
            ("indexer.get_qdrant_client", {"return_value": self.client}),
            ("api.get_qdrant_client", {"return_value": self.client}),
            ("indexer.get_embedding_model", {}),
            ("indexer.embed_texts", {"side_effect": lambda texts: [[1.] + [0.] * 383 for _ in texts]}),
            ("api.DEFAULT_DATA_DIR", {"new": str(self.data)}),
        ):
            guard = patch(name, **options); guard.start(); self.addCleanup(guard.stop)
        guard = patch.dict(api._REGISTERED_REPOS, {self.repo: {"rootPath": str(self.root)}})
        guard.start(); self.addCleanup(guard.stop)

    def publish(self):
        return self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
            index_directory=indexer.index_directory, build_graph=build_graph)

    def direct(self):
        return indexer.index_directory(str(self.root), data_dir=str(self.data), build_graph=False)

    def semantic_states(self):
        with closing(sqlite3.connect(self.data / self.repo / "metadata.db")) as db:
            return [row[0] for row in db.execute("SELECT state FROM index_snapshots WHERE stage='semantic'")]

    def test_hash_read_failure_is_reported_and_semantic_stage_is_not_ready(self):
        with patch("indexer._sha256", side_effect=PermissionError("private-read-error")):
            result = self.direct()
        self.assertEqual(result["files_failed"], 1)
        self.assertEqual(self.semantic_states(), ["failed"])
        self.assertEqual(self.direct()["files_failed"], 0)
        self.assertEqual(self.semantic_states(), ["failed", "ready"])

    def test_inventory_file_and_directory_errors_do_not_become_empty_success(self):
        for kind in ("source", "directory"):
            with self.subTest(kind=kind), patch("knowledge_base.scanner._inventory", return_value=[
                _entry("auth.ts" if kind == "source" else "locked", kind, "error", "PermissionError")
            ]):
                with self.assertRaises(OSError): self.direct()

    def test_missing_source_between_discovery_and_hash_is_a_failed_run(self):
        with patch("indexer._sha256", side_effect=FileNotFoundError("gone")):
            self.assertEqual(self.direct()["files_failed"], 1)
        self.assertEqual(self.semantic_states(), ["failed"])

    def test_missing_source_during_read_does_not_mark_semantic_stage_ready(self):
        native = indexer._sha256
        def disappear(path):
            digest = native(path); Path(path).unlink(); return digest
        with patch("indexer._sha256", side_effect=disappear):
            self.assertEqual(self.direct()["files_failed"], 1)
        self.assertEqual(self.semantic_states(), ["failed"])

    def test_empty_and_unsupported_repositories_publish_without_embeddings(self):
        self.file.unlink()
        (self.root / "image.png").write_bytes(b"\0binary")
        with patch("indexer.embed_texts", side_effect=AssertionError("No source to embed")):
            result = self.publish()
            self.assertEqual(result["chunk_count"], 0)
            self.assertTrue(self.store.view()["fresh"])
            self.assertTrue(self.publish()["unchanged"])

    def test_invalid_target_and_storage_paths_fail_without_store_mutation(self):
        for target in (self.root / "absent", self.file):
            with self.subTest(target=target), self.assertRaises(indexer.IndexingError):
                indexer.index_directory(str(target), data_dir=str(self.data))
        with self.assertRaises(ValueError):
            indexer.index_directory(str(self.root), data_dir=str(self.root / "unsafe"))
        self.assertFalse((self.root / "unsafe").exists())

    def test_partial_parser_does_not_replace_valid_generation_and_retry_recovers(self):
        self.publish(); old = self.store.active(); old_db = self.store.data_for(old) / self.repo / "metadata.db"
        saved = old_db.read_bytes()
        self.file.write_text("export function login( {\n")
        with self.assertRaisesRegex(RuntimeError, "pars"):
            self.publish()
        self.assertEqual(self.store.active()["id"], old["id"])
        self.assertEqual(old_db.read_bytes(), saved)
        self.assertEqual(self.store.view()["state"], "failed")
        self.assertEqual(len(list((self.store.path / "generations").iterdir())), 1)
        self.file.write_text('export function login() { return false; }\n')
        self.assertFalse(self.publish()["unchanged"])
        self.assertTrue(self.store.view()["fresh"])

    def test_parser_exception_and_vector_failure_keep_last_valid_snapshot(self):
        self.publish(); old = self.store.active()
        self.file.write_text('export function login() { return false; }\n')
        for name in ("knowledge_base.parser.parse_source", "indexer.embed_texts"):
            with self.subTest(name=name), patch(name, side_effect=RuntimeError("injected interruption")):
                with self.assertRaises(RuntimeError): self.publish()
                self.assertEqual(self.store.active()["id"], old["id"])
        self.assertTrue(self.publish()["activeSnapshotId"])
        self.assertTrue(self.store.view()["fresh"])

    def test_add_modify_rename_delete_then_noop_reuses_unchanged_embeddings(self):
        self.publish()
        stable = self.root / "stable.ts"; stable.write_text('export function stable() { return 7; }\n')
        self.assertEqual(self.publish()["chunks_embedded"], 1)
        self.file.write_text('export function login() { return false; }\n')
        added = self.root / "added.ts"; added.write_text('export function added() { return 8; }\n')
        result = self.publish()
        self.assertEqual(set(result["changed_files"]), {"auth.ts", "added.ts"})
        self.assertEqual(result["chunks_embedded"], 2)
        renamed = self.root / "renamed.ts"; added.rename(renamed)
        result = self.publish()
        self.assertEqual(result["chunks_embedded"], 1)
        self.assertEqual(set(result["changed_files"]), {"added.ts", "renamed.ts"})
        self.file.unlink(); renamed.unlink()
        with patch("indexer.embed_texts", side_effect=AssertionError("Unchanged source must not embed")):
            result = self.publish(); self.assertEqual(result["chunks_embedded"], 0)
            self.assertTrue(self.publish()["unchanged"])
        with closing(sqlite3.connect(self.store.data_for(self.store.active()) / self.repo / "metadata.db")) as db:
            paths = {row[0] for row in db.execute("SELECT relative_path FROM files WHERE status='included'")}
        self.assertEqual(paths, {"stable.ts"})

    def test_missing_metadata_rebuilds_private_generation_and_interruption_recovers(self):
        self.publish(); active = self.store.active()
        (self.store.data_for(active) / self.repo / "metadata.db").unlink()
        self.assertFalse(self.store.view()["fresh"])
        self.assertFalse(self.publish()["unchanged"])
        self.file.write_text('export function login() { return false; }\n')
        with patch("indexer.embed_texts", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.publish()
        self.assertFalse(self.store.view()["fresh"])
        self.publish(); self.assertTrue(self.store.view()["fresh"])

    def test_integrated_sync_answers_and_deleted_source_citations(self):
        self.publish()
        request = api.SearchRequest(repoId=self.repo, query="Where is login defined?")
        with patch.dict("os.environ", {"CODEMIND_LOCAL_MODEL": ""}):
            first = api.ask(request)
            self.assertTrue(first["citations"])
            citation = first["results"][0]
            old = self.store.active()
            self.file.write_text('export function login() { return false; }\n')
            self.publish()
            second = api.ask(request)
            self.assertEqual(second["results"][0]["sourceHash"], hashlib.sha256(self.file.read_bytes()).hexdigest())
            self.assertNotEqual(second["knowledgeSnapshotId"], first["knowledgeSnapshotId"])
            with self.assertRaises(ValueError):
                trace(self.store.data_for(old) / self.repo / "metadata.db", self.repo, citation["entityId"])
            self.file.unlink(); self.publish()
            self.assertEqual(api.ask(request)["citations"], [])

    def test_preexisting_partial_publication_is_not_fresh_or_a_noop_and_reuses_vectors(self):
        self.publish()
        manifest = self.store.active()
        manifest["result"]["graph"]["parsing"]["partial"] = 1
        (self.store.path / "active_snapshot.json").write_text(json.dumps(manifest))
        self.assertFalse(self.store.view()["fresh"])
        with patch("indexer.embed_texts", side_effect=AssertionError("Existing valid vectors can be reused")):
            result = self.publish()
        self.assertFalse(result["unchanged"])
        self.assertEqual(result["chunks_embedded"], 0)
        self.assertTrue(self.store.view()["fresh"])

    def test_standalone_legacy_python_graph_parse_failure_is_explicit_and_retryable(self):
        self.file.unlink()
        source = self.root / "broken.py"; source.write_text("def broken(\n")
        result = indexer.index_directory(str(self.root), data_dir=str(self.data), build_graph=True)
        self.assertEqual(result["files_failed"], 1)
        self.assertEqual(self.semantic_states(), ["failed"])
        source.write_text("def repaired():\n    return True\n")
        result = indexer.index_directory(str(self.root), data_dir=str(self.data), build_graph=True)
        self.assertEqual(result["files_failed"], 0)
        self.assertGreater(result["graph_nodes"], 0)

    def test_legacy_graph_exception_retry_reuses_valid_unchanged_vectors(self):
        self.file.unlink()
        (self.root / "auth.py").write_text("def login():\n    return True\n")
        with patch("indexer.graph_builder.update_graph_for_file", side_effect=RuntimeError("graph unavailable")):
            result = indexer.index_directory(str(self.root), data_dir=str(self.data), build_graph=True)
        self.assertEqual(result["files_failed"], 1)
        with patch("indexer.embed_texts", side_effect=AssertionError("Unchanged embedding input must be reused")):
            result = indexer.index_directory(str(self.root), data_dir=str(self.data), build_graph=True)
        self.assertEqual(result["files_failed"], 0)
        self.assertEqual(result["chunks_embedded"], 0)
        self.assertGreater(result["chunks_reused"], 0)
