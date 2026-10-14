"""S2 pinned resolution, Phase 6 operation selection and candidate safeguards."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_phase6_execution as phase6
from query_system.contracts import QueryContext
from query_system.benchmark_s2 import DATASET, evaluate
from query_system.execution_contracts import ExecutionPlan
from query_system.query_understanding import QueryUnderstandingEngine


class StabilizationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.owner = phase6.ExecutionTests(); self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)

    def test_comparison_canonical_ids_and_scoped_duplicate_pairs_are_exact(self):
        with self.owner.session() as executor:
            for question, names, paths in (
                ("Compare login and entry", ["login", "entry"], ["login.ts", "entry.ts"]),
                ("Compare AuthService.check with snake_case", ["AuthService.check", "snake_case"], ["service.ts"] * 2),
                ("Compare `auth.ts::sharedName` and `other.ts::sharedName`", ["sharedName"] * 2, ["auth.ts", "other.ts"]),
            ):
                query = executor.understand(self.owner.request(question))
                targets = [query.entities[i] for i in query.requirements[0].entity_indices]
                self.assertEqual([item.name for item in targets], names)
                self.assertEqual([item.file_path for item in targets], paths)
                self.assertTrue(all(item.symbol_id == item.evidence.symbol_id for item in targets))
                for item in targets:
                    named = executor.understand(self.owner.request(f"Where is `{item.name}` in {item.file_path} defined?"))
                    self.assertEqual(item.symbol_id, named.entities[0].symbol_id)

    def test_comparison_test_discovery_executes_both_known_inputs_without_waiting_for_deferred_prose(self):
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Compare login and entry and find tests for both"))
            plan = executor.plan(query)
            self.assertEqual([item.disposition for item in plan.requirements], ["deferred", "planned"])
            self.assertEqual([op.request.name for op in plan.operations], ["reference_test_analysis"] * 2)
            self.assertTrue(all(not op.dependencies for op in plan.operations))
            self.assertEqual(ExecutionPlan.model_validate_json(plan.model_dump_json()), plan)
            report = asyncio.run(executor.execute(plan))
            self.assertTrue(all(item.result is not None for item in report.operations))

    def test_plural_dependency_summaries_select_existing_rules_not_a_false_dependency_path(self):
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Compare login and entry and explain their dependencies"))
            plan = executor.plan(query)
            self.assertEqual([op.request.name for op in plan.operations], ["dependency_summary"] * 2)
            self.assertEqual(len(plan.requirements), 4)
            self.assertTrue(all(not op.dependencies for op in plan.operations))

    def test_descriptions_plan_semantic_discovery_without_exact_symbol_lookup(self):
        with self.owner.session() as executor, patch("query_system.query_understanding._Run.symbol", side_effect=AssertionError("description cannot require a symbol")):
            for text in ("Find the function responsible for email validation", "Show the code that processes uploaded files", "Find code that verifies source file hashes before opening citations"):
                query = executor.understand(self.owner.request(text)); plan = executor.plan(query)
                self.assertEqual(query.status, "success")
                self.assertEqual([(op.request.engine, op.request.name) for op in plan.operations], [("semantic", "hybrid_search")])
                self.assertEqual(plan.operations[0].request.parameters["query"], text)
                self.assertEqual(plan.operations[0].request.parameters["filters"], {})

    def test_multiple_features_and_explicit_file_scope_use_existing_semantic_parameters(self):
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Find code that checks access and find code that sends mail"))
            plan = executor.plan(query)
            self.assertEqual(len(plan.operations), 2)
            self.assertNotEqual(plan.operations[0].request.parameters["query"], plan.operations[1].request.parameters["query"])
            scoped = executor.plan(executor.understand(self.owner.request("Find code that checks access in auth.ts")))
            self.assertEqual(scoped.operations[0].request.parameters["filters"], {"files": ["auth.ts"]})

    def test_discovery_candidates_and_single_hit_are_never_implicitly_promoted(self):
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Find the function responsible for token validation and show its callers"))
            self.assertEqual(query.requirements[1].depends_on, [0])
            report = asyncio.run(executor.execute(executor.plan(query)))
            self.assertEqual(len(report.operations), 1)
            self.assertEqual(report.plan.operations[0].request.engine, "semantic")
            self.assertEqual(report.requirements[1].status, "ambiguous")
            self.assertTrue(report.clarifications[0].candidates)
            self.assertTrue(all(entity.symbol_id is None for entity in query.entities))

    def test_exact_ambiguity_and_missing_operands_never_use_semantic_scores(self):
        with self.owner.session() as executor:
            for text, resolution in (("Compare duplicate and entry", "ambiguous"), ("Compare noSuchFunction and entry", "not_found")):
                query = executor.understand(self.owner.request(text))
                targets = [query.entities[i] for i in query.requirements[0].entity_indices]
                self.assertEqual(targets[0].resolution, resolution)
                self.assertIsNone(targets[0].symbol_id)
                self.assertEqual(targets[1].name, "entry")
                self.assertEqual(executor.plan(query).operations, [])
            singular = executor.plan(executor.understand(self.owner.request("Compare login and entry and show its callers")))
            self.assertEqual(singular.operations, [])

    def test_unsupported_request_does_not_reuse_a_previous_conversation_intent(self):
        with QueryUnderstandingEngine.pinned(self.owner.store) as engine:
            previous = engine.normalize(self.owner.request("Who calls login?"))
            context = QueryContext(repository_id=self.owner.repo, snapshot_id=engine.context.snapshot_id,
                                   previous_target=previous.entities[0], previous_intents=previous.intents)
            followup = engine.normalize(self.owner.request("What about it?"), context)
            self.assertEqual(followup.primary_intent, "DIRECT_CALLERS")
            unsupported = engine.normalize(self.owner.request("Deploy server"), context)
            self.assertEqual(unsupported.intents, ["UNRESOLVED"])
            self.assertEqual(unsupported.entities, [])

    def test_labeled_evaluation_and_canonical_proofs_match_one_pinned_generation(self):
        with self.owner.session() as executor:
            result = evaluate(executor, QueryUnderstandingEngine, json.loads(DATASET.read_text()))
            self.assertEqual(result["metrics"]["questions"], 41)
            for metric, value in result["metrics"].items():
                if metric != "questions":
                    self.assertEqual(value, 0 if metric == "incorrect_ambiguity_rate" else 1, metric)
            self.assertGreater(result["proofs_checked"], 0)
            self.assertEqual(result["proofs_checked"], result["valid_proofs"])
