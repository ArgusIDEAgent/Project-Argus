"""Integrated failure branches and source authority across sync/restart boundaries."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import api
import test_phase10_integration as phase10
from fastapi import HTTPException
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.presentation_contracts import PreviewInput
from query_system import presentation_preview
from query_system.response_presentation import format_response


class RuntimeHybridTests(unittest.TestCase):
    def setUp(self):
        self.api = phase10.HybridApiTests(); self.api.setUp(); self.addCleanup(self.api.doCleanups)
        self.owner = self.api.owner

    def packet(self):
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Where is login defined?"))
            return EvidenceAggregator.from_executor(executor).aggregate(asyncio.run(executor.execute(executor.plan(query))))

    def test_compound_failure_blocks_only_dependents_and_keeps_independent_verified_results(self):
        def factory(name, *args):
            engine = self.owner.native_factory(name, *args)
            if name == "graph":
                native = engine.execute
                async def execute(request, context):
                    if request.name == "find_definition": raise RuntimeError("injected graph error")
                    return await native(request, context)
                engine.execute = execute
            return engine
        with self.owner.session(factory) as executor, \
                patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("Deterministic plan")):
            query = executor.understand(self.owner.request("Find login, show its callers; contract of entry"))
            self.assertEqual([row.intent for row in query.requirements], ["SYMBOL_LOCATION", "DIRECT_CALLERS", "FUNCTION_CONTRACT"])
            self.assertEqual(query.requirements[1].depends_on, [0])
            report = asyncio.run(executor.execute(executor.plan(query)))
            self.assertEqual([operation.status for operation in report.operations[:2]], ["error", "skipped"])
            self.assertIn(report.operations[2].status, ("success", "partial"))
            package = EvidenceAggregator.from_executor(executor).aggregate(report)
            view = format_response(package)
            self.assertEqual(view.status, "partial")
            self.assertTrue(view.items)
            self.assertNotIn(view.requirements[2].state, ("error", "stale"))
            self.assertTrue(all(record.proof.index_snapshot_id in executor.context.ready_stage_ids for record in package.evidence.values()))

    def test_repeated_targets_comparison_ambiguity_and_discovery_remain_separate(self):
        for question, expected in (
            ("Who calls login and who calls entry?", ["login", "entry"]),
            ("Compare login and entry", ["login", "entry"]),
        ):
            with self.subTest(question=question), self.owner.session() as executor:
                normalized = executor.understand(self.owner.request(question))
                self.assertEqual([entity.name for entity in normalized.entities], expected)
                plan = executor.plan(normalized)
                self.assertEqual(len(plan.requirements), len(normalized.requirements))
                if question.startswith("Compare"): self.assertEqual(plan.requirements[0].disposition, "deferred")
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Where is duplicate defined?"))
            self.assertEqual(executor.plan(query).operations, [])
            query = executor.understand(self.owner.request("Find code that checks access and show its callers"))
            plan = executor.plan(query)
            self.assertTrue(any(operation.request.engine == "semantic" for operation in plan.operations))
            self.assertFalse(any(operation.request.name == "find_callers" for operation in plan.operations))

    def test_preview_rejects_invalid_links_before_any_navigation(self):
        packet = self.packet()
        for mutate in (
            lambda value: value["package"]["citations"].clear(),
            lambda value: value["package"]["evidence"].clear(),
            lambda value: value["package"]["requirement_evidence"].clear(),
        ):
            body = PreviewInput(package=packet).model_dump(); mutate(body)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): PreviewInput.model_validate(body)

    def test_preview_cli_bad_packets_fail_with_no_navigation_or_untrusted_echo(self):
        for raw in (b'{"secret":"do-not-echo"', b'[]', b'{}', b'x' * (presentation_preview.MAX_INPUT_BYTES + 1)):
            with self.subTest(length=len(raw)), \
                    patch.object(sys, "argv", ["preview", "--root", str(self.owner.fixture.root), "--data-dir", str(self.owner.fixture.data)]), \
                    patch.object(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw))), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(presentation_preview.main(), 1)
            error = json.loads(output.getvalue())
            self.assertEqual(set(error), {"error"})
            self.assertNotIn("do-not-echo", output.getvalue())

    def test_preview_depths_then_source_deletion_rejects_every_old_reference(self):
        packet = self.packet(); reference = next(iter(packet.citations))
        for depth in ("quick", "standard", "deep"):
            view, source = presentation_preview.verify_preview(self.owner.store, PreviewInput(package=packet, depth=depth), reference)
            self.assertEqual(view.depth, depth)
            self.assertIsNotNone(source)
        (self.owner.fixture.root / "login.ts").unlink()
        view, source = presentation_preview.verify_preview(self.owner.store, PreviewInput(package=packet), reference)
        self.assertIsNone(source)
        self.assertEqual(view.status, "stale_index")
        self.assertTrue(all(row.state != "verified" for row in view.sources.values()))

    def test_api_handle_expiry_and_new_backend_cache_refuse_old_navigation(self):
        response = self.api.query("Where is login defined?").json()
        token = response["navigation_token"]; reference = next(iter(response["presentation"]["sources"]))
        body = {"repository_id": self.owner.repo, "navigation_token": token, "reference": reference}
        # Router endpoint closes over the existing process-local cache. Advance
        # its clock, then prove a fresh router/cache cannot regain authority.
        with patch("hybrid_api.monotonic", return_value=float("inf")):
            reply = self.api.http.post("/v1/hybrid/source", json=body, headers=self.api.headers)
        self.assertEqual(reply.status_code, 404)
        from hybrid_api import create_hybrid_router
        fresh = create_hybrid_router(api)
        with self.assertRaises(HTTPException) as error:
            fresh.answer_cache.get(token, self.owner.repo, self.owner.fixture.root)
        self.assertEqual(error.exception.status_code, 404)

    def test_modern_legacy_ask_and_hybrid_routes_do_not_dispatch_networkx_rag(self):
        import rag_pipeline
        with patch.object(rag_pipeline, "ask_codemind", side_effect=AssertionError("Legacy NetworkX answering must not run")), \
                patch.dict("os.environ", {"CODEMIND_LOCAL_MODEL": ""}):
            reply = self.api.http.post("/ask", json={"repoId": self.owner.repo, "query": "Who calls login?"}, headers=self.api.headers)
            self.assertEqual(reply.status_code, 200)
            self.assertTrue(reply.json()["citations"])
            self.assertEqual(self.api.query("Who calls login?").status_code, 200)
        for failure in (None, ValueError("bad snapshot")):
            with self.subTest(failure=failure), patch.object(rag_pipeline, "repository_view", return_value={"managed": True}, side_effect=failure), \
                    patch.object(rag_pipeline, "_retrieve_context", side_effect=AssertionError("No vector retrieval")), \
                    patch.object(rag_pipeline, "completion", side_effect=AssertionError("No provider call")):
                message = rag_pipeline.ask_codemind("Who calls login?", repo_id=self.owner.repo)
                self.assertIn("snapshot", message.lower())
