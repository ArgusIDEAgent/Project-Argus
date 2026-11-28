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

    def test_partial_ts_graph_keeps_unaffected_callers_and_impact_until_repair(self):
        from fastapi.testclient import TestClient

        (self.root / "routes.ts").write_text(
            'import { login } from "./auth";\nexport function requireSession() { return login(); }\n')
        (self.root / "entry.ts").write_text(
            'import { requireSession } from "./routes";\nexport function entry() { return requireSession(); }\n')
        broken = self.root / "unfinished.ts"
        valid = 'import { login } from "./auth";\nexport function unfinished() { return login(); }\n'
        broken.write_text(valid)
        (self.root / "bridge.ts").write_text('export { unfinished } from "./unfinished";\n')
        (self.root / "consumer.ts").write_text(
            'import { unfinished } from "./bridge";\nimport { login } from "./auth";\n'
            'export function consumer() { return unfinished() && login(); }\n')
        self.publish()
        before = self.store.active()
        before_db = self.store.data_for(before) / self.repo / "metadata.db"
        saved = before_db.read_bytes()

        with (patch.dict("os.environ", {"CODEMIND_AUTO_SYNC": "0", "CODEMIND_ENABLE_HYBRID": "1"}),
              patch("answers.urlopen", side_effect=AssertionError("Verified calls should be deterministic")),
              TestClient(api.app, base_url="http://127.0.0.1") as http):
            headers = {"x-codemind-session": api._SESSION_TOKEN}

            def ask(question):
                reply = http.post("/ask", headers=headers, json={"repoId": self.repo, "query": question})
                self.assertEqual(reply.status_code, 200, reply.text)
                return reply.json()

            question = "What happens if I modify login?"
            complete = {result["name"] for result in ask(question)["results"]}
            self.assertEqual(complete, {"requireSession", "entry", "unfinished", "consumer"})

            broken.write_text(valid + 'export const missing = ;\n')
            self.assertFalse(self.store.view()["fresh"])
            with patch("api._JOBS", {}):
                queued = http.post(f"/repos/{self.repo}/refresh", headers=headers).json()
                job = http.get(f"/jobs/{queued['jobId']}", headers=headers).json()
                self.assertEqual(job["state"], "completed")
                self.assertEqual(job["result"]["graph"]["state"], "partial")
            partial = self.store.active()
            self.assertNotEqual(partial["id"], before["id"])
            self.assertEqual(before_db.read_bytes(), saved)
            self.assertEqual(partial["result"]["graph"]["excluded_graph_files"], 3)
            synchronization = http.get(f"/repos/{self.repo}/live-state", headers=headers).json()["synchronization"]
            self.assertTrue(synchronization["fresh"])
            self.assertEqual(synchronization["state"], "partial")
            self.assertIn("unfinished.ts:3", synchronization["graphWarning"])
            with patch("knowledge_base.graph._semantic_pass", side_effect=AssertionError("Unchanged partial graph")):
                self.assertTrue(self.publish()["unchanged"])

            callers = ask("Who calls login?")
            self.assertEqual([(row["path"], row["startLine"]) for row in callers["citations"]], [("routes.ts", 2)])
            impact = ask(question)
            self.assertIn("entry → requireSession → login", impact["answer"])
            self.assertEqual([(row["path"], row["startLine"]) for row in impact["citations"]],
                             [("routes.ts", 2), ("entry.ts", 2)])
            self.assertIn("unfinished.ts:3", impact["warning"])
            affected = ask("Who calls unfinished in unfinished.ts?")
            self.assertEqual(affected["citations"], [])
            self.assertIn("unfinished.ts:3", affected["warning"])

            hybrid = http.post("/v1/hybrid/query", headers=headers, json={
                "query": {"repository_id": self.repo, "question": "Who calls login?", "depth": "standard"},
                "allow_generation": False,
            }).json()
            self.assertEqual(hybrid["status"], "partial")
            self.assertTrue(hybrid["presentation"]["sources"])
            self.assertTrue(any("unfinished.ts:3" in item for item in hybrid["presentation"]["limitations"]))

            broken.write_text(valid)
            self.publish()
            self.assertEqual({row["name"] for row in ask(question)["results"]}, complete)
            self.assertIsNone(ask(question)["synchronization"]["graphWarning"])
            self.assertEqual(self.store.view()["state"], "ready")

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

    def test_python_callers_and_impact_through_default_chat_after_refresh(self):
        from fastapi.testclient import TestClient

        (self.root / "tokens.py").write_text("def verify_token(token):\n    return bool(token)\n")
        routes = self.root / "routes.py"
        routes.write_text("from tokens import verify_token as verify\n\ndef require_session(token):\n    return verify(token)\n")
        (self.root / "entry.py").write_text("import routes as routes_api\n\ndef entry(token):\n    return routes_api.require_session(token)\n")
        (self.root / "unrelated.py").write_text("def verify_other(token):\n    return bool(token)\n")
        # Incomplete Python files still allow indexing and lexical/semantic search.
        (self.root / "unfinished.py").write_text("def unfinished(\n")
        result = self.publish()
        self.assertEqual(result["graph"]["coverage"]["python"], {"parsed": 4, "partial": 1, "error": 0})
        with (patch.dict("os.environ", {"CODEMIND_AUTO_SYNC": "0", "CODEMIND_LOCAL_MODEL": "local"}),
              patch("answers.urlopen", side_effect=AssertionError("Verified calls should be deterministic")),
              TestClient(api.app, base_url="http://127.0.0.1") as http):
            headers = {"x-codemind-session": api._SESSION_TOKEN}

            def ask(question):
                response = http.post("/ask", headers=headers, json={"repoId": self.repo, "query": question})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            callers = ask("Who calls verify_token?")
            self.assertEqual(callers["answerMode"], "evidence")
            self.assertEqual([(c["path"], c["startLine"]) for c in callers["citations"]], [("routes.py", 4)])
            self.assertIn("require_session → verify_token", callers["answer"])
            impact = ask("What happens if I modify verify_token?")
            self.assertIn("entry → require_session → verify_token", impact["answer"])
            self.assertEqual([(c["path"], c["startLine"]) for c in impact["citations"]],
                             [("routes.py", 4), ("entry.py", 4)])
            self.assertEqual(ask("What happens if I modify verify_token in tokens.py?")["citations"],
                             impact["citations"])
            # The unchanged entry file's path stops contributing when an
            # intermediate call is removed from the active generation.
            routes.write_text("def require_session(token):\n    return False\n")
            self.publish()
            self.assertEqual(ask("What happens if I modify verify_token?")["citations"], [])

    def test_jwt_answers_cite_complete_definitions_and_verified_call_sites(self):
        from fastapi.testclient import TestClient

        self.file.unlink()
        fixture = Path(__file__).parents[1] / "sample_code" / "auth_service.py"
        source = self.root / fixture.name
        source.write_bytes(fixture.read_bytes())
        (self.root / "routes.py").write_text(
            "from auth_service import verify_token\n\ndef require_session(token):\n    return verify_token(token)\n")
        (self.root / "archive.py").write_text("def archive_old_entries():\n    return 20\n")
        self.publish()
        db = self.store.view()["data_dir"] / self.repo / "metadata.db"

        with (patch.dict("os.environ", {"CODEMIND_AUTO_SYNC": "0", "CODEMIND_LOCAL_MODEL": "local"}),
              TestClient(api.app, base_url="http://127.0.0.1") as http):
            headers = {"x-codemind-session": api._SESSION_TOKEN}

            def ask(question):
                reply = http.post("/ask", headers=headers, json={"repoId": self.repo, "query": question})
                self.assertEqual(reply.status_code, 200, reply.text)
                return reply.json()

            with patch("answers.urlopen", side_effect=AssertionError("Location answers are deterministic")):
                location = ask("Where is JWT authentication implemented?")
            self.assertIn("require_session → verify_token (routes.py:4)", location["answer"])
            self.assertNotIn("archive.py", location["answer"])
            self.assertEqual({item["path"] for item in location["citations"]}, {"auth_service.py", "routes.py"})

            from test_semantic_answers import model_reply
            with patch("answers.urlopen", return_value=model_reply({"sources": ["source-1"]})):
                detail = ask("Explain verify_token")
            self.assertEqual(detail["evidenceSelection"], "local-llm")
            self.assertIn("algorithms=[\"HS256\"]", detail["answer"])
            self.assertIn('except jwt.ExpiredSignatureError:', detail["answer"])
            self.assertIn('raise ValueError("Invalid authentication token")', detail["answer"])
            self.assertEqual([(row["path"], row["startLine"], row["endLine"]) for row in detail["citations"]],
                             [("auth_service.py", 21, 28), ("routes.py", 4, 4)])
            for result in (location, detail):
                self.assertEqual([row["citationId"] for row in result["results"]],
                                 [row["id"] for row in result["citations"]])
                for citation in result["citations"]:
                    proof = trace(db, self.repo, citation["entityId"])
                    for key in ("path", "startLine", "endLine", "sourceHash"):
                        self.assertEqual(proof[key], citation[key])

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
