"""Negative API surface and process-local job/session lifecycle, no live models."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import os
import tempfile
import threading
import time
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
        with patch.object(api, "_search_code", side_effect=AssertionError("retrieval")) as retrieval, \
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
        with patch.object(api, "_search_code", return_value=retrieval) as search, \
                patch.object(api, "answer_question", return_value=answer) as model:
            question = "thanks, show callers of validateToken()"
            intent = self.http.post("/v1/chat/intent", json={"message": question}, headers=self.headers)
            self.assertFalse(intent.json()["handled"])
            reply = self.http.post("/ask", json={"repoId": self.repo, "query": question}, headers=self.headers)
            self.assertEqual(reply.json()["answer"], "technical")
            search.assert_called_once()
            model.assert_called_once()
            self.assertEqual(search.call_args.args[0].query, question)

    def test_location_stream_bypasses_model_and_reports_one_timing(self):
        source = self.root / "auth.py"
        source.write_text("def login(): return True\n")
        retrieval = {"mode": "exact", "results": [{
            "path": source.name, "name": "login", "startLine": 1, "endLine": 1,
            "sourceHash": hashlib.sha256(source.read_bytes()).hexdigest(),
        }]}
        with patch.object(api, "_search_code", return_value=retrieval), \
                patch("answers._request_source_ranking", side_effect=AssertionError("location called model")) as model:
            for question in ("Where is login?", "Can you explain where login is defined?",
                             "Which file defines the login workflow?", "Which function handles the login flow?",
                             "Please find the code for login",
                             "Show me the location of the rule that describes how login works"):
                with self.subTest(question=question):
                    reply = self.http.post("/ask", json={"repoId": self.repo, "query": question},
                                           headers={**self.headers, "accept": "application/x-ndjson"})
                    self.assertEqual(reply.status_code, 200)
                    self.assertEqual(reply.headers["content-type"], "application/x-ndjson")
                    event, = map(json.loads, reply.text.splitlines())
                    self.assertEqual(event["type"], "answer")
                    answer = event["result"]
                    self.assertIn("login (auth.py:1)", answer["answer"])
                    self.assertEqual(answer["answerTiming"]["firstUsefulMs"], answer["answerTiming"]["totalMs"])
            model.assert_not_called()

    def test_explicit_and_mixed_explanations_request_ranking(self):
        source = self.root / "auth.py"
        source.write_text("def login(): return True\n")
        retrieval = {"mode": "exact", "results": [{
            "path": source.name, "name": "login", "startLine": 1, "endLine": 1,
            "sourceHash": hashlib.sha256(source.read_bytes()).hexdigest(),
        }]}
        headers = {**self.headers, "accept": "application/x-ndjson"}
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "test-local"}), \
                patch.object(api, "_search_code", return_value=retrieval), \
                patch("answers._request_source_ranking", return_value={
                    "message": {"content": '{"sources":["source-1"]}'},
                }) as model:
            for query in ("Find out why login succeeds", "What does login do?", "login workflow",
                          "Where is login defined and how does it work?"):
                with self.subTest(query=query):
                    reply = self.http.post("/ask", json={"repoId": self.repo, "query": query}, headers=headers)
                    preview, final = map(json.loads, reply.text.splitlines())
                    self.assertEqual(preview["type"], "evidence")
                    self.assertEqual(final["type"], "answer")
                    self.assertEqual(final["result"]["evidenceSelection"], "local-llm")
                    self.assertEqual(preview["result"]["citations"], final["result"]["citations"])
            self.assertEqual(model.await_count, 4)

    def test_explanation_publishes_verified_evidence_before_ranking_and_rechecks_it(self):
        source = self.root / "auth.py"
        source.write_text("def login(): return True\n")
        retrieval = {"mode": "exact", "results": [{
            "path": source.name, "name": "login", "startLine": 1, "endLine": 1,
            "sourceHash": hashlib.sha256(source.read_bytes()).hexdigest(),
        }]}
        request = api.SearchRequest(repoId=self.repo, query="Explain how login works")

        def edit_during_ranking(_payload):
            source.write_text("def login(): return False\n")
            return {"message": {"content": '{"sources":["source-1"]}'}}

        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "test-local"}), \
                patch("answers._request_source_ranking", side_effect=edit_during_ranking) as model:
            events = api._stream_answer(str(self.root), request, retrieval, api.time.monotonic())
            evidence = json.loads(next(events))
            self.assertEqual(evidence["type"], "evidence")
            self.assertEqual(evidence["result"]["results"][0]["path"], "auth.py")
            self.assertIn("return True", evidence["result"]["answer"])
            model.assert_not_called()

            answer, = map(json.loads, events)
            self.assertEqual(answer["type"], "answer")
            final = answer["result"]
            self.assertEqual(final["results"], [])
            self.assertEqual(final["citations"], [])
            self.assertIn("changed", final["warning"])
            self.assertLessEqual(final["answerTiming"]["firstUsefulMs"], final["answerTiming"]["totalMs"])
            model.assert_awaited_once()

    def test_explanation_skips_model_when_retrieval_used_the_ranking_budget(self):
        source = self.root / "auth.py"
        source.write_text("def login(): return True\n")
        retrieval = {"mode": "exact", "results": [{
            "path": source.name, "name": "login", "startLine": 1, "endLine": 1,
            "sourceHash": hashlib.sha256(source.read_bytes()).hexdigest(),
        }]}
        request = api.SearchRequest(repoId=self.repo, query="Explain how login works")
        started = api.time.monotonic() - api.ANSWER_RANKING_BUDGET_SECONDS
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "test-local"}), \
                patch("answers._request_source_ranking") as model:
            event, = map(json.loads, api._stream_answer(str(self.root), request, retrieval, started))
            model.assert_not_called()
        self.assertEqual(event["type"], "answer")
        self.assertIn("timed out", event["result"]["warning"])
        self.assertTrue(event["result"]["citations"])

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

    def test_leases_release_watch_only_after_last_index_owner_and_keep_disk_storage(self):
        from knowledge_base.storage import RepositoryStorage
        from knowledge_base.synchronization import SyncManager
        api._REGISTERED_REPOS.clear()
        manager = SyncManager(str(self.data), lambda *_: None, audit_interval=3600)
        self.addCleanup(manager.close)
        index_owners = ("a"*32, "b"*32)
        change_owner = "c"*32
        with patch.object(api, "_SYNC_MANAGER", manager):
            for owner in (*index_owners, change_owner):
                reply = self.http.post("/repos/register", headers=self.headers, json={
                    "rootPath": str(self.root), "leaseId": owner, "watch": owner != change_owner})
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.json()["repository"]["leaseId"], owner)
            self.assertTrue(manager.status(self.repo)["watching"])
            handle, _ = manager.revision(self.repo)
            incarnation = api._REGISTERED_REPOS[self.repo]["incarnation"]
            storage = RepositoryStorage(self.root, str(self.data))
            sentinel = storage.path / "retained"
            sentinel.write_text("snapshot contents")
            for owner in index_owners:
                reply = self.http.post(f"/repos/{self.repo}/release", headers=self.headers, json={"leaseId": owner})
                self.assertEqual(reply.json(), {"released": True})
                self.assertEqual(manager.status(self.repo)["watching"], owner == index_owners[0])
            self.assertIn(self.repo, api._REGISTERED_REPOS)
            released = self.http.get(f"/repos/{self.repo}/live-state", params={"leaseId": index_owners[1]}, headers=self.headers)
            self.assertEqual(released.status_code, 404)
            self.assertEqual(self.http.post(f"/repos/{self.repo}/index", params={"leaseId": index_owners[1]},
                                            headers=self.headers).status_code, 404)
            reply = self.http.post(f"/repos/{self.repo}/release", headers=self.headers, json={"leaseId": change_owner})
            self.assertEqual(reply.json(), {"released": True})
            self.assertNotIn(self.repo, api._REGISTERED_REPOS)
            self.assertIsNone(manager.revision(self.repo))
            self.assertEqual(sentinel.read_text(), "snapshot contents")

            self.http.post("/repos/register", headers=self.headers, json={"rootPath": str(self.root), "leaseId": "d"*32})
            self.assertTrue(manager.status(self.repo)["watching"])
            manager.changed(self.repo, handle)
            self.assertFalse(manager.status(self.repo)["invalidated"])
            api._request_auto_refresh(self.repo, incarnation, handle)
            self.assertEqual(api._JOBS, {})
            self.assertEqual(sentinel.read_text(), "snapshot contents")

    def test_sequential_editor_roots_do_not_exhaust_active_repository_capacity(self):
        api._REGISTERED_REPOS.clear()
        for index in range(api._MAX_REPOS + 5):
            root = self.root / f"repo-{index}"
            root.mkdir()
            owner = f"{index:032x}"
            result = self.http.post("/repos/register", headers=self.headers, json={"rootPath": str(root), "leaseId": owner})
            self.assertEqual(result.status_code, 200, result.text)
            repo = result.json()["repository"]["id"]
            reply = self.http.post(f"/repos/{repo}/release", headers=self.headers, json={"leaseId": owner})
            self.assertEqual(reply.json(), {"released": True})
            self.assertEqual(api._REGISTERED_REPOS, {})
        unknown = self.http.post(f"/repos/{repo}/release", headers=self.headers, json={"leaseId": owner})
        self.assertEqual(unknown.json(), {"released": False})
        self.assertEqual(self.http.post(f"/repos/{repo}/release", json={"leaseId": owner}).status_code, 401)

    def test_lease_renewal_and_expiry_reclaim_unclosed_selections(self):
        api._REGISTERED_REPOS.clear()
        first, second = "a"*32, "b"*32
        with patch("api.time.monotonic", return_value=100):
            for owner in (first, second):
                self.http.post("/repos/register", headers=self.headers, json={"rootPath": str(self.root), "leaseId": owner})
        with patch("api.time.monotonic", return_value=150):
            heartbeat = self.http.get(f"/repos/{self.repo}/live-state", headers=self.headers,
                                      params={"leaseId": second, "verify": "false"})
            self.assertEqual(heartbeat.status_code, 200)
        api._expire_repository_leases(100 + api._REPOSITORY_LEASE_SECONDS)
        self.assertEqual(set(api._REGISTERED_REPOS[self.repo]["leases"]), {second})
        api._expire_repository_leases(150 + api._REPOSITORY_LEASE_SECONDS)
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

    def test_running_incremental_keeps_full_successor_and_another_repository_progresses(self):
        other_root = self.root / "other"
        other_root.mkdir()
        other = api.register_repo(api.RegisterRequest(rootPath=str(other_root)))["repository"]["id"]
        lease = "d"*32
        api.register_repo(api.RegisterRequest(rootPath=str(self.root), leaseId=lease))
        started, finish = threading.Event(), threading.Event()
        calls = []

        def store_factory(root, *_):
            store = Mock()

            def publish(**options):
                full = options["full"]
                calls.append((str(root), full))
                if str(root) == str(self.root) and not full:
                    started.set()
                    if not finish.wait(10):
                        raise TimeoutError("fixture was not released")
                return {"files_indexed": 1, "files_skipped": 0, "chunk_count": 1,
                        "graph": {"graph": {}, "graph_snapshot_id": "stage"}, "activeSnapshotId": "a"*32}
            store.publish.side_effect = publish
            return store

        def submit(repo, *, full=False):
            response = self.http.post(f"/repos/{repo}/index", params={"full": full}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            return response.json()["jobId"]

        with (patch.object(api, "SnapshotStore", side_effect=store_factory), patch.object(api, "get_qdrant_client"),
              ThreadPoolExecutor(max_workers=1) as requests):
            incremental = requests.submit(submit, self.repo)
            try:
                self.assertTrue(started.wait(5))
                pending = self.http.post(f"/repos/{self.repo}/refresh", params={"leaseId": lease}, headers=self.headers)
                self.assertEqual(pending.status_code, 200)
                queued_refresh = pending.json()["jobId"]
                self.assertEqual(api.get_job(queued_refresh)["state"], "queued")
                rebuild = submit(self.repo, full=True)
                self.assertEqual(rebuild, queued_refresh)
                self.assertEqual(submit(self.repo, full=True), rebuild)
                self.assertEqual(api.get_job(rebuild)["state"], "queued")
                self.assertEqual(api.get_job(rebuild)["mode"], "full")
                unrelated = submit(other)
                self.assertEqual(api.get_job(unrelated)["state"], "completed")
                self.assertFalse(incremental.done())
            finally:
                finish.set()
            first = incremental.result(timeout=10)
            self.assertNotEqual(first, rebuild)
            end = time.monotonic() + 10
            while api.get_job(rebuild)["state"] in ("queued", "running") and time.monotonic() < end:
                time.sleep(0.02)
            self.assertEqual(api.get_job(rebuild)["state"], "completed")
        self.assertEqual([full for root, full in calls if root == str(self.root)], [False, True])

    def test_queued_upgrade_and_last_release_cancels_only_queued_work(self):
        api._REGISTERED_REPOS.clear()
        owner = "a"*32
        api.register_repo(api.RegisterRequest(rootPath=str(self.root), leaseId=owner))
        tasks = BackgroundTasks()
        queued = api._enqueue_job(self.repo, False, tasks, lease_id=owner)["jobId"]
        upgraded = api._enqueue_job(self.repo, True, tasks, lease_id=owner)["jobId"]
        self.assertEqual(queued, upgraded)
        self.assertEqual(api.get_job(queued)["mode"], "full")
        api.release_repository(self.repo, api.RepositoryLeaseRequest(leaseId=owner))
        self.assertEqual(api.get_job(queued)["state"], "cancelled")
        with patch.object(api, "SnapshotStore") as store:
            api._admit_index_job(queued)
        store.assert_not_called()

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
