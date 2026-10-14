"""Existing pinned producers -> presentation and fresh read-only navigation authority."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import answers
import test_phase6_execution as phase6
import test_phase8_generation as phase8
from knowledge_base.identity import repository_id
from knowledge_base.synchronization import SnapshotStore
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.evidence_contracts import EvidenceLimits
from query_system.generation_contracts import GenerationBudget
from query_system.graph_rag import GraphRagGenerator
from query_system.presentation_contracts import PreviewInput
from query_system.presentation_preview import verify_preview
from query_system.response_presentation import format_response


class PresentationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.owner = phase6.ExecutionTests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)

    def retrieve(self, executor, question, limits=None):
        normalized = executor.understand(self.owner.request(question))
        report = asyncio.run(executor.execute(executor.plan(normalized)))
        return EvidenceAggregator.from_executor(executor).aggregate(report, limits)

    def test_real_all_deterministic_templates_preserve_verified_findings(self):
        examples = [("Where is login defined?", "SYMBOL_LOCATION"), ("Who calls login?", "DIRECT_CALLERS"),
            ("What does login call?", "DIRECT_CALLEES"), ("Dependency path from entry to validateToken", "DEPENDENCY_PATH"),
            ("Dependencies of login", "DEPENDENCY_PATH"), ("Find code that checks access", "FEATURE_DISCOVERY"),
            ("Contract of validateToken", "FUNCTION_CONTRACT"), ("Impact of changing login", "CHANGE_IMPACT"),
            ("Overview of auth.ts", "MODULE_OVERVIEW"), ("What tests cover login?", "TEST_DISCOVERY")]
        with self.owner.session() as executor:
            for question, intent in examples:
                with self.subTest(question=question):
                    package = self.retrieve(executor, question)
                    self.assertTrue(package.findings)
                    view = format_response(package)
                    self.assertEqual(view.requirements[0].original.intent, intent)
                    for item in view.items.values():
                        original = next(finding for finding in package.findings if finding.finding_id == item.item_id)
                        self.assertEqual(item.classification, original.classification)
                        self.assertEqual(item.citation_ids, original.citation_ids)
                        self.assertEqual(item.authoritative, original.authoritative)

    def test_all_four_existing_grounded_explanation_types_are_rendered_without_model_calls(self):
        helper = phase8.GenerationTests(); helper.owner = self.owner
        with self.owner.session() as executor:
            package = helper.package(executor)
            for kind in ("function", "workflow", "behavior", "comparison"):
                generation = GraphRagGenerator(executor, answers, client=phase8.FakeLocal()).generate(helper.request(executor, package, kind), package)
                before = generation.model_dump_json()
                with patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("LLM")), patch.object(executor, "execute", side_effect=AssertionError("retrieval")):
                    for depth in ("quick", "standard", "deep"):
                        view = format_response(package, generation, depth=depth)
                        self.assertTrue(view.items)
                        self.assertEqual(view.requirements[0].original, generation.coverage[0].original)
                        self.assertEqual(view.model_status, generation.model_status)
                self.assertEqual(generation.model_dump_json(), before)

    def test_compound_repeated_requirements_keep_targets_order_and_source_ownership(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Who calls login and who calls entry?")
        view = format_response(package, depth="deep")
        self.assertEqual([item.requirement_id for item in view.requirements], ["req-000", "req-001"])
        self.assertEqual([item.original.intent for item in view.requirements], ["DIRECT_CALLERS"] * 2)
        self.assertEqual(len(view.requirements), 2)
        self.assertIn("login", " ".join(view.requirements[0].target_context))
        self.assertIn("entry", " ".join(view.requirements[1].target_context))
        self.assertEqual(view.requirements[1].state, package.requirements[1].state)
        self.assertTrue(view.requirements[0].sections[0].item_ids)

    def test_shared_citations_are_deduplicated_without_dropping_repeated_claims(self):
        with self.owner.session() as executor:
            query = self.owner.typed_requirements(executor, ["SYMBOL_LOCATION"] * 2, [[0], [0]])
            package = EvidenceAggregator.from_executor(executor).aggregate(asyncio.run(executor.execute(executor.plan(query))))
        view = format_response(package)
        self.assertEqual(len(view.sources), 1)
        self.assertEqual(len(view.items), 2)
        self.assertNotEqual(view.requirements[0].sections[0].item_ids, view.requirements[1].sections[0].item_ids)

    def test_ambiguous_missing_unsupported_and_deferred_are_explicit(self):
        with self.owner.session() as executor:
            for question, state in (("Where is duplicate defined?", "ambiguous"), ("Where is noSuchFunction defined?", "not_found"),
                                    ("Explain login", "deferred"), ("Contract of Service", "unsupported")):
                with self.subTest(question=question):
                    package = self.retrieve(executor, question)
                    view = format_response(package)
                    self.assertEqual(view.requirements[0].state, state)
                    self.assertNotEqual(view.status, "success")
                    if state == "ambiguous":
                        self.assertTrue(view.clarifications)
                        self.assertGreaterEqual(len(view.clarifications[0].candidates), 2)
                    self.assertFalse(view.follow_ups)

    def test_dependency_order_and_failed_independent_branch_remain_visible(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Find the login function, show its callers, and tell me what might be affected if I change it.")
            view = format_response(package, depth="quick")
        self.assertEqual(len(view.requirements), 3)
        self.assertEqual(view.requirements[1].original.depends_on, [0])
        self.assertTrue(all(item.sections[0].item_ids for item in view.requirements))
        self.assertNotEqual(view.requirements[2].state, "satisfied")

    def test_truncation_at_quick_depth_preserves_critical_uncertainty(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Who calls login?", EvidenceLimits(findings_per_requirement=1))
        view = format_response(package, depth="quick")
        self.assertEqual(view.status, "partial")
        self.assertIn("truncated", " ".join(view.requirements[0].limitations))

    def test_fallback_keeps_existing_verified_findings_and_marks_explanation_incomplete(self):
        helper = phase8.GenerationTests(); helper.owner = self.owner
        with self.owner.session() as executor:
            package = helper.package(executor)
            generation = GraphRagGenerator(executor, answers, client=phase8.FakeLocal(lambda *_: "not-json")).generate(helper.request(executor, package), package)
            view = format_response(package, generation)
        self.assertEqual(view.model_status, "validation_failed")
        self.assertEqual(view.fallback_status, "evidence_summary")
        self.assertEqual(view.status, "partial")
        self.assertTrue(view.items)
        self.assertTrue(view.sources)

    def test_fresh_citation_authority_uses_existing_metadata_and_one_pin_no_engine_dispatch(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Where is login defined?")
        reference = next(iter(package.citations))
        with patch.object(self.owner.store, "pin_view", wraps=self.owner.store.pin_view) as pins, patch("query_system.executor.MultiEngineExecutor.execute", side_effect=AssertionError("retrieval")), patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("LLM")):
            view, source = verify_preview(self.owner.store, PreviewInput(package=package), reference)
        self.assertEqual(pins.call_count, 1)
        self.assertEqual(source, package.citations[reference])
        self.assertEqual(view.sources[reference].state, "verified")

    def test_stale_unknown_reference_and_unauthorized_repository_never_authorize_navigation(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Where is login defined?")
        packet = PreviewInput(package=package); reference = next(iter(package.citations))
        _, source = verify_preview(self.owner.store, packet, "invented")
        self.assertIsNone(source)
        other = self.owner.fixture.data
        foreign = SnapshotStore(other, repository_id(other), self.owner.fixture.root)
        view, source = verify_preview(foreign, packet, reference)
        self.assertIsNone(source)
        self.assertEqual(view.sources[reference].state, "unauthorized")
        file = self.owner.fixture.root / "login.ts"
        file.write_text(file.read_text() + "// changed\n")
        view, source = verify_preview(self.owner.store, packet, reference)
        self.assertIsNone(source)
        self.assertEqual(view.status, "stale_index")
        self.assertTrue(all(item.state == "stale" for item in view.sources.values()))

    def test_invalid_proof_or_finding_is_excluded_without_losing_independent_result(self):
        with self.owner.session() as executor:
            query = self.owner.typed_requirements(executor, ["SYMBOL_LOCATION"] * 2, [[0], [1]])
            package = EvidenceAggregator.from_executor(executor).aggregate(asyncio.run(executor.execute(executor.plan(query))))
        finding = package.findings[0]
        package.findings[0].original.entity_name = "invented declaration"
        view, _ = verify_preview(self.owner.store, PreviewInput(package=package))
        self.assertNotIn(finding.finding_id, view.items)
        self.assertTrue(view.items)
        self.assertEqual(view.status, "partial")
        self.assertEqual(view.requirements[1].state, "satisfied")

    def test_existing_follow_up_queries_resolve_same_canonical_symbol(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Where is login defined?")
            view = format_response(package)
            for suggestion in view.follow_ups:
                if suggestion.mode != "copy_query": continue
                normalized = executor.understand(self.owner.request(suggestion.prompt))
                self.assertTrue(any(entity.symbol_id == suggestion.target_id for entity in normalized.entities), suggestion.prompt)
                plan = executor.plan(normalized)
                self.assertTrue(any(operation.request.name == suggestion.operation for operation in plan.operations), suggestion.prompt)

    def test_read_only_cli_verifies_real_packet_without_api_or_model_service(self):
        with self.owner.session() as executor:
            package = self.retrieve(executor, "Where is login defined?")
        result = subprocess.run([sys.executable, "-m", "query_system.presentation_preview", "--root", str(self.owner.fixture.root),
            "--data-dir", str(self.owner.fixture.data), "--reference", next(iter(package.citations))],
            input=PreviewInput(package=package).model_dump_json(), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(result.stdout)
        self.assertIsNotNone(reply["source"])
        self.assertEqual(reply["presentation"]["snapshot_id"], package.snapshot_id)

    def test_failed_provider_branch_keeps_independent_success_and_error_details(self):
        blocked = []
        def factory(name, *args):
            engine = self.owner.native_factory(name, *args)
            execute = engine.execute
            async def failing(operation, context):
                if operation.parameters.get("selected_symbol_id") == blocked[0]:
                    raise RuntimeError("injected provider failure")
                return await execute(operation, context)
            engine.execute = failing
            return engine
        with self.owner.session(factory) as executor:
            query = self.owner.typed_requirements(executor, ["SYMBOL_LOCATION"] * 2, [[0], [1]])
            blocked.append(query.entities[0].symbol_id)
            package = EvidenceAggregator.from_executor(executor).aggregate(asyncio.run(executor.execute(executor.plan(query))))
        view = format_response(package, depth="quick")
        self.assertEqual(view.requirements[0].state, package.requirements[0].state)
        self.assertIn(view.requirements[0].state, ("error", "stale"))
        self.assertEqual(view.requirements[0].operations[0].execution_status, "error")
        self.assertTrue(view.requirements[0].operations[0].limitations)
        self.assertEqual(view.requirements[1].state, "satisfied")
        self.assertTrue(view.requirements[1].sections[0].item_ids)
        self.assertEqual(view.status, "partial")


if __name__ == "__main__":
    unittest.main()
