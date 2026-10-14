"""Authenticated existing-contract integration for final stabilization."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_phase10_integration as phase10
from hybrid_api import AnswerCache
from fastapi import HTTPException


class StabilizationApiTests(unittest.TestCase):
    def setUp(self):
        self.owner = phase10.HybridApiTests(); self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)

    def test_followup_uses_only_a_backend_owned_verified_previous_target(self):
        first = self.owner.query("Where is login defined?").json()
        with patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("deterministic follow-up")):
            reply = self.owner.query("Who calls it?", previous_answer_token=first["navigation_token"])
        self.assertEqual(reply.status_code, 200, reply.text)
        body = reply.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual([op["operation"] for op in body["operations"]], ["find_callers"])
        self.assertTrue(body["presentation"]["sources"])

    def test_semantic_candidates_and_multiple_previous_targets_are_not_promoted(self):
        for question in ("Find code that checks access", "Who calls login and who calls entry?", "Compare duplicate and entry"):
            with self.subTest(question=question):
                first = self.owner.query(question).json()
                reply = self.owner.query("Who calls it?", previous_answer_token=first["navigation_token"])
                self.assertEqual(reply.status_code, 200, reply.text)
                self.assertEqual(reply.json()["operations"], [])
                self.assertTrue(reply.json()["presentation"]["clarifications"])

    def test_unknown_expired_and_client_forged_context_is_rejected(self):
        self.assertEqual(self.owner.query("Who calls it?", previous_answer_token="a" * 48).status_code, 404)
        self.assertEqual(self.owner.query("Who calls it?", previous_answer_token="../outside").status_code, 422)
        self.assertEqual(self.owner.query("Who calls it?", conversation={"previous_target": {"symbol_id": "forged"}}).status_code, 422)

    def test_relationship_lists_execute_existing_operations_for_both_canonical_targets(self):
        reply = self.owner.query("Who calls login and entry?").json()
        self.assertEqual([op["operation"] for op in reply["operations"]], ["find_callers", "find_callers"])
        self.assertEqual(reply["generations"], [])

    def test_exact_five_minute_expiry_and_process_restart_fail_closed(self):
        dummy = SimpleNamespace(package=SimpleNamespace(model_dump_json=lambda: "{}"), generations=[],
                                response=SimpleNamespace(model_dump_json=lambda: "{}"))
        cache = AnswerCache(); clock = [100.]
        with patch("hybrid_api.monotonic", side_effect=lambda: clock[0]):
            token = cache.put(self.owner.owner.repo, self.owner.owner.fixture.root, dummy)
            clock[0] = 399.999
            self.assertIsNotNone(cache.get(token, self.owner.owner.repo, self.owner.owner.fixture.root))
            clock[0] = 400.
            with self.assertRaises(HTTPException): cache.get(token, self.owner.owner.repo, self.owner.owner.fixture.root)
        with self.assertRaises(HTTPException): AnswerCache().get(token, self.owner.owner.repo, self.owner.owner.fixture.root)

    def test_a_new_snapshot_does_not_reuse_old_followup_authority(self):
        first = self.owner.query("Where is login defined?").json()
        file = self.owner.owner.fixture.root / "login.ts"
        file.write_text(file.read_text() + "// private stale-context regression\n")
        self.owner.owner.fixture.publish()
        reply = self.owner.query("Who calls it?", previous_answer_token=first["navigation_token"])
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["operations"], [])
        self.assertTrue(reply.json()["presentation"]["clarifications"])

