"""Negative API surface and process-local job/session lifecycle, no live models."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

import api


class RuntimeApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cm-runtime-api-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"; self.root.mkdir()
        self.data = Path(temporary.name) / "private"
        self.repo = api.make_repo_id(str(self.root))
        for guard in (
            patch.object(api, "DEFAULT_DATA_DIR", str(self.data)),
            patch.object(api, "_REGISTRY_FILE", str(self.data / "repos.json")),
            patch.object(api, "_SESSION_TOKEN", "runtime-test-token"),
            patch.object(api, "_SYNC_MANAGER", None),
            patch.dict(api._REGISTERED_REPOS, {self.repo: {"rootPath": str(self.root)}}, clear=True),
            patch.dict(api._JOBS, {}, clear=True),
            patch.dict(os.environ, {"CODEMIND_AUTO_SYNC": "0", "CODEMIND_ENABLE_HYBRID": "1"}),
        ):
            guard.start(); self.addCleanup(guard.stop)
        self.http = TestClient(api.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.addCleanup(self.http.close)
        self.headers = {"x-codemind-session": "runtime-test-token"}

    def test_legacy_request_validation_returns_safe_typed_errors(self):
        valid = {"repoId": self.repo, "query": "login"}
        for route in ("/retrieve", "/ask", "/search/code"):
            for change in ({"query": ""}, {"query": "x" * 2001}, {"repoId": "../outside"},
                           {"limit": 26}, {"retrieval": "remote"}, {"filters": {"files": ["../secret"]}},
                           {"filters": {"files": ["x.ts"] * 26}}):
                with self.subTest(route=route, change=change):
                    reply = self.http.post(route, json={**valid, **change}, headers=self.headers)
                    self.assertEqual(reply.status_code, 422)
                    self.assertEqual(reply.json(), {"error": "Invalid request parameters."})
            self.assertEqual(self.http.post(route, json={}, headers=self.headers).status_code, 422)
            self.assertEqual(self.http.post(route, content=b'{bad-json', headers=self.headers).status_code, 422)

    def test_static_chat_bypasses_legacy_retrieval_and_model(self):
        with patch.object(api, "search_code", side_effect=AssertionError("retrieval")) as retrieval, \
                patch.object(api, "answer_question", side_effect=AssertionError("model")) as model, \
                patch("hybrid_api.SnapshotStore", side_effect=AssertionError("snapshot access")):
            for question in ("hi", "hello", "hey", "good morning", "thanks", "thank you", "bye",
                             "how are you?", "what can you do?", "help", "what's the weather?"):
                with self.subTest(question=question):
                    intent = self.http.post("/v1/chat/intent", json={"message": question}, headers=self.headers)
                    self.assertEqual(intent.status_code, 200)
                    self.assertTrue(intent.json()["handled"])
                    reply = self.http.post("/ask", json={"repoId": self.repo, "query": question}, headers=self.headers)
                    self.assertEqual(reply.status_code, 200)
                    self.assertEqual(reply.json()["answerMode"], "static")
                    self.assertEqual(reply.json()["results"], [])
                    self.assertEqual(reply.json()["citations"], [])
            hybrid = self.http.post("/v1/hybrid/query", json={
                "query": {"repository_id": self.repo, "question": "hi"}}, headers=self.headers)
            self.assertEqual(hybrid.status_code, 200)
            self.assertEqual(hybrid.json()["static_response"]["category"], "greeting")
            self.assertIsNone(hybrid.json()["presentation"])
            self.assertEqual(hybrid.json()["operations"], [])
            self.assertEqual(hybrid.json()["generations"], [])
            retrieval.assert_not_called()
            model.assert_not_called()

    def test_mixed_chat_reaches_existing_legacy_path_without_losing_query(self):
        retrieval = {"mode": "test", "results": [], "warning": None}
        answer = {**retrieval, "answer": "technical", "answerMode": "evidence"}
        with patch.object(api, "search_code", return_value=retrieval) as search, \
                patch.object(api, "answer_question", return_value=answer) as model:
            question = "thanks, show callers of validateToken()"
            intent = self.http.post("/v1/chat/intent", json={"message": question}, headers=self.headers)
            self.assertFalse(intent.json()["handled"])
            reply = self.http.post("/ask", json={"repoId": self.repo, "query": question}, headers=self.headers)
            self.assertEqual(reply.json()["answer"], "technical")
            search.assert_called_once()
            model.assert_called_once()
            self.assertEqual(search.call_args.args[0].query, question)

    def test_hybrid_invalid_depth_budgets_unknown_options_and_extra_fields(self):
        valid = {"query": {"repository_id": self.repo, "question": "Who calls login?"}}
        for change in (
            {"query": {**valid["query"], "depth": "maximum"}},
            {"query": {**valid["query"], "question": "x" * 2001}},
            {"limits": {"execution": {"max_operations": 65}}},
            {"limits": {"execution": {"deadline_ms": 0}}},
            {"limits": {"generation": {"num_ctx": -1}}},
            {"limits": {"deadline_ms": 120001}},
            {"tools": ["shell"]}, {"model": "remote"}, {"allow_generation": "yes"},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.http.post("/v1/hybrid/query", json={**valid, **change}, headers=self.headers).status_code, 422)
        handle = {"repository_id": self.repo, "navigation_token": "a" * 48}
        for route in ("/v1/hybrid/source", "/v1/hybrid/presentation"):
            self.assertEqual(self.http.post(route, json={**handle, "depth": "bad", "reference": "x"}, headers=self.headers).status_code, 422)

    def test_authentication_covers_existing_routes_and_shutdown_state(self):
        for method, route, body in (
            ("POST", "/ask", {"repoId": self.repo, "query": "login"}),
            ("POST", "/retrieve", {"repoId": self.repo, "query": "login"}),
            ("POST", f"/repos/{self.repo}/index", None),
            ("POST", f"/repos/{self.repo}/refresh", None),
            ("GET", "/jobs/absent", None),
            ("GET", "/v1/hybrid/readiness?repoId=" + self.repo, None),
            ("POST", "/v1/hybrid/query", {"query": {"repository_id": self.repo, "question": "login"}}),
        ):
            for token in (None, "wrong"):
                with self.subTest(route=route, token=token):
                    headers = {} if token is None else {"x-codemind-session": token}
                    self.assertEqual(self.http.request(method, route, json=body, headers=headers).status_code, 401)
        with patch.object(api, "_SESSION_TOKEN", ""):
            self.assertEqual(self.http.post("/hello", headers=self.headers).status_code, 401)

    def test_unknown_repositories_and_handles_do_not_access_storage(self):
        for route in ("/ask", "/retrieve"):
            reply = self.http.post(route, json={"repoId": "f" * 24, "query": "login"}, headers=self.headers)
            self.assertEqual(reply.status_code, 404)
        for route in ("/v1/hybrid/source", "/v1/hybrid/presentation"):
            body = {"repository_id": self.repo, "navigation_token": "a" * 48, "depth": "standard"}
            if route.endswith("source"): body["reference"] = "invented"
            self.assertEqual(self.http.post(route, json=body, headers=self.headers).status_code, 404)

    def test_registration_persistence_failure_rolls_back_memory(self):
        api._REGISTERED_REPOS.clear()
        with patch.object(api, "_save_registry", side_effect=OSError("private-error")):
            reply = self.http.post("/repos/register", json={"rootPath": str(self.root)}, headers=self.headers)
        self.assertEqual(reply.status_code, 500)
        self.assertEqual(reply.json(), {"error": "Internal engine error."})
        self.assertNotIn(self.repo, api._REGISTERED_REPOS)

    def test_shutdown_revokes_session_and_restart_rotates_generated_token(self):
        with patch.dict(os.environ, {"CODEMIND_SESSION_TOKEN": ""}):
            with TestClient(api.app, base_url="http://127.0.0.1") as http:
                token = api._SESSION_TOKEN
                self.assertEqual(http.post("/hello", headers={"x-codemind-session": token}).status_code, 200)
            self.assertEqual(api._SESSION_TOKEN, "")
            with TestClient(api.app, base_url="http://127.0.0.1") as http:
                self.assertNotEqual(api._SESSION_TOKEN, token)
                self.assertEqual(http.post("/hello", headers={"x-codemind-session": token}).status_code, 401)

    def test_job_queue_running_completion_and_duplicate_admission(self):
        tasks = BackgroundTasks()
        first = api._enqueue_job(self.repo, False, tasks)
        self.assertEqual(first["state"], "queued")
        self.assertEqual(api._enqueue_job(self.repo, False, tasks)["jobId"], first["jobId"])
        self.assertEqual(len(tasks.tasks), 1)
        def complete(**options):
            self.assertEqual(api.get_job(first["jobId"])["state"], "running")
            return {"files_indexed": 1, "files_skipped": 0, "chunk_count": 1,
                "graph": {"graph": {}, "graph_snapshot_id": "stage"}, "activeSnapshotId": "a" * 32}
        store = Mock(); store.publish.side_effect = complete
        with patch.object(api, "SnapshotStore", return_value=store), patch.object(api, "get_qdrant_client"):
            api._run_index_job(first["jobId"], self.repo, str(self.root))
        self.assertEqual(api.get_job(first["jobId"])["state"], "completed")
        self.assertEqual(api.get_job(first["jobId"])["result"]["fileCount"], 1)

    def test_failed_and_timed_out_jobs_are_failed_with_safe_errors_and_retry(self):
        for error in (RuntimeError("private-error"), TimeoutError("private-timeout")):
            with self.subTest(error=type(error).__name__):
                job = api._enqueue_job(self.repo, False, BackgroundTasks())
                store = Mock(); store.publish.side_effect = error
                with patch.object(api, "SnapshotStore", return_value=store), patch.object(api, "get_qdrant_client"):
                    api._run_index_job(job["jobId"], self.repo, str(self.root))
                reply = api.get_job(job["jobId"])
                self.assertEqual(reply["state"], "failed")
                self.assertNotIn("private-", reply["error"])
                store.failed.assert_called_once_with(error)
                self.assertIn("completedAt", api._JOBS[job["jobId"]])

    def test_capacity_eviction_and_process_restart_make_job_status_unavailable(self):
        for number in range(api._MAX_JOBS):
            identifier = "finished-" + str(number)
            api._JOBS[identifier] = {"id": identifier, "repoId": self.repo,
                "state": api.JobState.COMPLETED, "createdAt": number}
        api._enqueue_job(self.repo, False, BackgroundTasks())
        self.assertEqual(len(api._JOBS), api._MAX_JOBS)
        self.assertEqual(self.http.get("/jobs/finished-0", headers=self.headers).status_code, 404)
        api._JOBS.clear()
        self.assertEqual(self.http.get("/jobs/finished-1", headers=self.headers).status_code, 404)
