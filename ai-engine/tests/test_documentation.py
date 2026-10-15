"""Documentation API against a disposable published Python knowledge snapshot."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
from documentation import DocumentationError, DocumentationService, _safe
import test_phase7 as phase7


class DocumentationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.owner = phase7.Phase7Tests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)
        self.repo = self.owner.repo
        self.root = self.owner.root
        self.service = DocumentationService(self.root, self.repo, self.owner.data)
        for guard in (patch.object(api, "_SESSION_TOKEN", "docs-test-token"),
                      patch.dict(os.environ, {"CODEMIND_ENABLE_DOCS": "1"})):
            guard.start(); self.addCleanup(guard.stop)
        self.http = TestClient(api.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.addCleanup(self.http.close)
        self.headers = {"x-codemind-session": "docs-test-token"}

    def get(self, path):
        return self.http.get(path, headers=self.headers)

    def sync(self):
        return self.http.post("/docs/sync", json={"repoId": self.repo, "mode": "incremental"}, headers=self.headers)

    def test_real_snapshot_initial_incremental_restart_and_navigation(self):
        empty = self.get(f"/docs/sections?repoId={self.repo}")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["status"]["sectionCount"], 0)
        self.assertIsNone(self.get(f"/docs/overview?repoId={self.repo}").json())
        first = self.sync()
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body["outcome"], "success")
        self.assertGreater(len(body["updated"]), 0)
        self.assertEqual(body["snapshotId"], self.owner.store.active()["id"])
        self.assertEqual(body["overview"]["freshness"], "current")
        self.assertTrue(all(item["sourceHash"] for item in body["overview"]["evidence"]))
        stored = self.service.path.read_bytes()
        second = self.sync().json()
        self.assertEqual(second["outcome"], "no_changes")
        self.assertEqual(second["updated"], [])
        self.assertEqual(second["reusedCount"], body["status"]["sectionCount"])
        self.assertEqual(stored, self.service.path.read_bytes())

        restarted = DocumentationService(self.root, self.repo, self.owner.data)
        self.assertEqual(restarted.list()["status"]["sectionCount"], body["status"]["sectionCount"])
        section_id = body["sections"][0]["sectionId"]
        opened = self.get(f"/docs/section/{section_id}?repoId={self.repo}")
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json()["sectionId"], section_id)

        self.owner.auth.write_text(self.owner.original.replace("> 0", "> 2"))
        stale = self.get(f"/docs/sections?repoId={self.repo}").json()
        self.assertGreater(stale["status"]["staleCount"], 0)
        self.assertEqual(self.sync().status_code, 409)
        self.owner.publish()
        changed = self.sync().json()
        self.assertEqual(changed["outcome"], "success")
        self.assertGreater(len(changed["updated"]), 0)
        self.assertGreater(changed["reusedCount"], 0)
        self.assertEqual(changed["status"]["staleCount"], 0)
        self.assertEqual(self.sync().json()["outcome"], "no_changes")

        (self.root / "entry.ts").unlink()
        self.owner.publish()
        deleted = self.sync().json()
        self.assertGreater(len(deleted["removed"]), 0)
        self.assertEqual(deleted["status"]["staleCount"], 0)
        self.assertEqual(self.get(f"/docs/section/{deleted['removed'][0]}?repoId={self.repo}").status_code, 404)

        (self.root / "login.ts").rename(self.root / "renamed-login.ts")
        self.owner.publish()
        renamed = self.sync().json()
        self.assertEqual(renamed["outcome"], "success")
        self.assertGreater(len(renamed["removed"]), 0)
        self.assertTrue(any("renamed-login.ts" in row["title"] for row in renamed["sections"]))

    def test_auth_identity_invalid_requests_and_scope_gate(self):
        self.assertEqual(self.http.post("/docs/sync", json={"repoId": self.repo}).status_code, 401)
        self.assertEqual(self.http.post("/docs/sync", json={"repoId": "../bad"}, headers=self.headers).status_code, 422)
        self.assertEqual(self.http.post("/docs/sync", json={"repoId": self.repo, "mode": "wrong"}, headers=self.headers).status_code, 422)
        self.assertEqual(self.get("/docs/sections?repoId=" + "f" * 24).status_code, 404)
        self.assertEqual(self.get("/docs/section/" + "a" * 40 + f"?repoId={self.repo}").status_code, 404)
        self.assertEqual(self.get(f"/docs/section/../secret?repoId={self.repo}").status_code, 404)
        with patch.dict(os.environ, {"CODEMIND_ENABLE_DOCS": "0"}):
            self.assertEqual(self.sync().status_code, 503)
            self.assertEqual(self.get(f"/docs/sections?repoId={self.repo}").status_code, 503)

    def test_failed_generation_preserves_prior_documents_and_resource_limits(self):
        self.assertEqual(self.sync().status_code, 200)
        before = self.service.path.read_bytes()
        with patch.object(DocumentationService, "_plans", side_effect=DocumentationError("failed", "generation failed")):
            self.assertEqual(self.sync().status_code, 500)
        self.assertEqual(before, self.service.path.read_bytes())
        with patch("documentation.MAX_SOURCE_BYTES", 1):
            self.assertEqual(self.sync().status_code, 429)
        self.assertEqual(before, self.service.path.read_bytes())

    def test_missing_snapshot_and_concurrent_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = DocumentationService(self.root, self.repo, Path(directory))
            with self.assertRaisesRegex(DocumentationError, "Index the repository"):
                missing.sync("incremental")
        self.service.directory.mkdir(parents=True, exist_ok=True)
        from filelock import FileLock
        with FileLock(str(self.service.directory / "sync.lock"), timeout=0):
            with self.assertRaisesRegex(DocumentationError, "already running"):
                self.service.sync("incremental")

    def test_partial_progress_and_invalid_persisted_reference(self):
        with patch("documentation.MAX_DOCUMENTS", 1):
            first = self.sync().json()
        self.assertEqual(first["outcome"], "partial")
        self.assertEqual(len(first["updated"]), 1)
        self.assertGreater(first["pendingCount"], 0)
        self.assertEqual(self.sync().json()["outcome"], "success")
        stored = json.loads(self.service.path.read_text())
        section = next(iter(stored["documents"].values()))
        section["sources"][0]["path"] = "../outside"
        self.service.path.write_text(json.dumps(stored))
        self.assertEqual(self.get(f"/docs/sections?repoId={self.repo}").status_code, 500)

    def test_redaction_no_model_and_invalid_generated_reference(self):
        secret = "abcdefghijklmnopqrstuvwx"
        self.assertNotIn(secret, _safe(f"API_KEY='{secret}'"))
        with patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("model must not run")):
            self.assertEqual(self.sync().status_code, 200)
        before = self.service.path.read_bytes()
        original = self.service._plans

        def invalid_plan(view):
            plans, truncated = original(view)
            plans[0]["sources"][0]["path"] = "../outside"
            return plans, truncated

        with patch.object(DocumentationService, "_plans", side_effect=invalid_plan):
            self.assertEqual(self.sync().status_code, 500)
        self.assertEqual(before, self.service.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
