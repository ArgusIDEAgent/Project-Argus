"""Pinned metadata-only query understanding; no answering-engine dispatch."""

from contextlib import closing
import hashlib
from pathlib import Path
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_fixture import fixture
from query_system.contracts import EditorSelection, QueryContext, QueryRequest
from query_system.query_understanding import QueryUnderstandingEngine, classify_conversational_intent
from query_system.benchmark_understanding import DATASET, evaluate, target_matches


class UnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.repo = self.fixture.store, self.fixture.repo

    def request(self, question, **kwargs):
        return QueryRequest(repository_id=self.repo, question=question, **kwargs)

    def normalize(self, question, **kwargs):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            return engine.normalize(self.request(question, **kwargs))

    def test_conversational_boundary_and_technical_regressions(self):
        static = {
            "hi": "greeting", "hello": "greeting", "hey": "greeting", "good morning": "greeting",
            "thanks": "thanks", "thank you": "thanks", "bye": "farewell", "how are you?": "acknowledgment",
            "what can you do?": "capabilities", "help": "capabilities", "what's the weather?": "out_of_scope",
        }
        for question, category in static.items():
            with self.subTest(question=question):
                reply = classify_conversational_intent(question)
                self.assertIsNotNone(reply)
                self.assertEqual(reply.category, category)
        technical = {
            "hi, explain login()": ["login"],
            "hello, find authentication": ["authentication"],
            "thanks, show callers of validateToken()": ["validateToken"],
            "what does hello() do?": ["hello"],
            "find the function named hi()": ["hi"],
            "compare hello() and goodbye()": ["hello", "goodbye"],
            "Where is sharedName defined?": ["sharedName"],
            "Where is the logic that rejects expired sessions?": [],
            "Find login, show its callers, and explain login": ["login"],
        }
        for question, targets in technical.items():
            with self.subTest(question=question):
                self.assertIsNone(classify_conversational_intent(question))
                result = self.normalize(question)
                self.assertTrue(result.requirements)
                for target in targets:
                    self.assertIn(target, [entity.name for entity in result.entities])
        self.assertEqual(self.normalize("Where is sharedName defined?").status, "ambiguous")
        self.assertEqual(self.normalize("Where is the logic that rejects expired sessions?").primary_intent, "FEATURE_DISCOVERY")
        self.assertGreaterEqual(len(self.normalize("Find login, show its callers, and explain login").requirements), 3)

    def test_resolved_symbol_location_preserves_canonical_proof(self):
        result = self.normalize("Where is validateToken defined?")
        self.assertEqual(result.status, "success")
        entity = result.entities[0]
        self.assertEqual(entity.resolution, "resolved")
        self.assertEqual(entity.file_path, "auth.ts")
        self.assertEqual(entity.symbol_id, entity.evidence.symbol_id)
        self.assertEqual(entity.evidence.snapshot_id, result.snapshot_id)
        self.assertNotEqual(entity.evidence.index_snapshot_id, result.snapshot_id)
        self.assertEqual(entity.evidence.source_hash, hashlib.sha256((self.fixture.root / "auth.ts").read_bytes()).hexdigest())

    def test_callers_callees_contracts_and_impact_are_not_executed(self):
        for question, expected in (("Who calls login?", "DIRECT_CALLERS"), ("What does login call?", "DIRECT_CALLEES"),
                                   ("Contract of validateToken", "FUNCTION_CONTRACT"), ("Impact of changing login", "CHANGE_IMPACT")):
            with self.subTest(question=question):
                result = self.normalize(question)
                self.assertEqual(result.primary_intent, expected)
                self.assertEqual(result.entities[0].resolution, "resolved")
                self.assertTrue(result.requirements[0].required_capabilities)

    def test_feature_discovery_does_not_require_known_symbol(self):
        result = self.normalize("Where is the logic that rejects expired sessions?")
        self.assertEqual(result.primary_intent, "FEATURE_DISCOVERY")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.entities[0].kind, "feature")
        self.assertIsNone(result.entities[0].symbol_id)
        self.assertEqual(result.entities[0].name, result.normalized_question)

    def test_qualified_methods_and_snake_case_names(self):
        result = self.normalize("Who calls `AuthService.check`?")
        self.assertEqual(result.entities[0].kind, "method")
        self.assertEqual(result.entities[0].file_path, "service.ts")
        self.assertEqual(result.entities[0].resolution, "resolved")
        snake = self.normalize("Where is snake_case defined?")
        self.assertEqual(snake.entities[0].name, "snake_case")
        self.assertEqual(snake.entities[0].resolution, "resolved")

    def test_duplicate_symbols_preserve_candidates_even_at_limit_one(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            engine.max_candidates = 1
            result = engine.normalize(self.request("Where is sharedName defined?"))
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.entities[0].symbol_id)
        self.assertEqual(len(result.entities[0].candidates), 1)
        self.assertTrue(result.entities[0].candidates_truncated)
        scoped = self.normalize("Where is sharedName defined?", active_file="other.ts")
        self.assertEqual(scoped.entities[0].file_path, "other.ts")

    def test_missing_symbols_never_receive_invented_ids(self):
        result = self.normalize("Where is missingFunction defined?")
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(result.entities[0].resolution, "not_found")
        self.assertIsNone(result.entities[0].symbol_id)
        self.assertIsNone(result.entities[0].evidence)

    def test_ambiguous_file_context_and_explicit_file_scope(self):
        result = self.normalize("Where is duplicate in duplicate.ts defined?")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual({candidate.file_path for candidate in result.entities[0].candidates}, {"one/duplicate.ts", "two/duplicate.ts"})
        scoped = self.normalize("Where is duplicate in one/duplicate.ts defined?")
        self.assertEqual(scoped.entities[0].resolution, "resolved")
        self.assertEqual(scoped.entities[0].file_path, "one/duplicate.ts")

    def test_compound_questions_share_verified_target_without_answering(self):
        result = self.normalize("Find the login function, show its callers, and tell me what might be affected if I change it.")
        self.assertEqual(result.intents, ["SYMBOL_LOCATION", "DIRECT_CALLERS", "CHANGE_IMPACT"])
        self.assertEqual([item.entity_indices for item in result.requirements], [[0], [0], [0]])
        self.assertEqual(result.entities[0].resolution, "resolved")
        self.assertEqual(result.requirements[-1].support, "partial")

    def test_repeated_intents_and_separately_scoped_duplicate_names(self):
        result = self.normalize("Who calls login and who calls entry?")
        self.assertEqual(result.intents, ["DIRECT_CALLERS"])
        self.assertEqual(len(result.requirements), 2)
        self.assertEqual([r.entity_indices for r in result.requirements], [[0], [1]])
        scoped = self.normalize("Find sharedName in auth.ts and find sharedName in other.ts")
        targets = [entity for entity in scoped.entities if entity.name == "sharedName"]
        self.assertEqual({entity.file_path for entity in targets}, {"auth.ts", "other.ts"})
        self.assertTrue(all(entity.resolution == "resolved" for entity in targets))
        compound = self.normalize("Explain login and describe the workflow through entry")
        self.assertEqual(compound.intents, ["FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION"])
        feature = self.normalize("Find code that sends notification emails and show its callers")
        self.assertEqual(feature.intents, ["FEATURE_DISCOVERY", "DIRECT_CALLERS"])
        self.assertEqual(feature.status, "partial")
        self.assertEqual(feature.requirements[1].support, "unresolved")
        self.assertEqual(feature.entities[0].kind, "feature")
        self.assertIsNone(feature.entities[0].symbol_id)

    def test_selected_symbol_and_contextual_followups(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            initial = engine.normalize(self.request("Where is login defined?"))
            context = QueryContext(repository_id=self.repo, snapshot_id=engine.context.snapshot_id,
                                   previous_target=initial.entities[0], previous_intents=initial.intents)
            for question, intent in (("Who calls this function?", "DIRECT_CALLERS"),
                                     ("What about its dependencies?", "DEPENDENCY_PATH"),
                                     ("Where is it defined?", "SYMBOL_LOCATION"), ("What tests cover it?", "TEST_DISCOVERY")):
                with self.subTest(question=question):
                    result = engine.normalize(self.request(question), context)
                    self.assertEqual(result.primary_intent, intent)
                    self.assertEqual(result.entities[0].symbol_id, initial.entities[0].symbol_id)
                    self.assertIn("previous_target", result.context_used)
            selected = engine.normalize(self.request("Who calls this function?", selected_symbol_id=initial.entities[0].symbol_id))
            self.assertIn("selected_symbol", selected.context_used)

    def test_insufficient_and_stale_conversation_context_never_guesses(self):
        empty = self.normalize("Who calls this function?")
        self.assertEqual(empty.status, "unresolved")
        self.assertIsNone(empty.entities[0].symbol_id)
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            initial = engine.normalize(self.request("Where is login defined?"))
            context = QueryContext(repository_id=self.repo, snapshot_id="f" * 32, previous_target=initial.entities[0])
            result = engine.normalize(self.request("Where is it defined?"), context)
            self.assertEqual(result.status, "unresolved")
            self.assertNotIn("previous_target", result.context_used)
            context = context.model_copy(update={"repository_id": "e" * 24, "snapshot_id": engine.context.snapshot_id})
            result = engine.normalize(self.request("Who calls this function?"), context)
            self.assertEqual(result.status, "unresolved")

    def test_previous_intent_is_reused_only_when_unique_and_current_intent_is_absent(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            initial = engine.normalize(self.request("Who calls login?"))
            context = QueryContext(repository_id=self.repo, snapshot_id=engine.context.snapshot_id,
                                   previous_target=initial.entities[0], previous_intents=initial.intents)
            result = engine.normalize(self.request("What about it?"), context)
            self.assertEqual(result.primary_intent, "DIRECT_CALLERS")
            self.assertIn("previous_intents", result.context_used)
            context.previous_intents = ["DIRECT_CALLERS", "CHANGE_IMPACT"]
            result = engine.normalize(self.request("What about it?"), context)
            self.assertEqual(result.status, "unresolved")
            self.assertEqual(result.primary_intent, "UNRESOLVED")

    def test_current_editor_context_takes_precedence_over_previous_selection(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            initial = engine.normalize(self.request("Where is login defined?"))
            context = QueryContext(repository_id=self.repo, snapshot_id=engine.context.snapshot_id,
                                   selected_symbol_id=initial.entities[0].symbol_id)
            selection = EditorSelection(start={"line": 3, "column": 1}, end={"line": 3, "column": 10})
            result = engine.normalize(self.request("Who calls this function?", active_file="auth.ts", selection=selection), context)
            self.assertEqual(result.entities[0].evidence.symbol_id, result.entities[0].symbol_id)
            self.assertEqual(result.entities[0].file_path, "auth.ts")
            self.assertNotEqual(result.entities[0].symbol_id, initial.entities[0].symbol_id)
            cursor = engine.normalize(self.request("Where is it defined?", active_file="auth.ts", cursor={"line":3,"column":5}))
            self.assertEqual(cursor.entities[0].symbol_id, result.entities[0].symbol_id)
            self.assertIn("cursor", cursor.context_used)

    def test_declared_class_contract_and_file_callers_are_unsupported(self):
        for question in ("Contract of class Service", "Who calls auth.ts?"):
            result = self.normalize(question)
            self.assertEqual(result.requirements[0].support, "unsupported")
            self.assertEqual(result.status, "unsupported")

    def test_labeled_corpus_and_canonical_metric_detects_wrong_identity(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            dataset = json.loads(DATASET.read_text())
            report = evaluate(engine, dataset, repeats=1)
            self.assertGreaterEqual(report["overall"]["queries"], 60)
            for metric in ("intent_accuracy", "entity_resolution_accuracy", "ambiguity_accuracy", "description_preservation"):
                self.assertEqual(report["overall"][metric], 1., metric)
            self.assertEqual(report["overall"]["support_correct"], report["overall"]["support_labels"])
            entity = engine.normalize(self.request("Where is login defined?")).entities[0]
            poisoned = entity.model_copy(update={"symbol_id": "e" * 64})
            self.assertFalse(target_matches(engine, {"name":"login","resolution":"resolved","path":"login.ts"}, [poisoned]))

    def test_resolution_budget_never_returns_unchecked_ids(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine, patch("query_system.graph_engine.monotonic", return_value=float("inf")):
            result = engine.normalize(self.request("Where is login defined?"))
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.error.code, "budget_exceeded")
            self.assertFalse(result.entities)

    def test_editor_selection_resolves_or_keeps_nested_ambiguity(self):
        selection = EditorSelection(start={"line": 3, "column": 1}, end={"line": 3, "column": 10})
        result = self.normalize("Who calls this function?", active_file="auth.ts", selection=selection)
        self.assertEqual(result.entities[0].resolution, "resolved")
        self.assertEqual(result.entities[0].selection, selection)
        nested = EditorSelection(start={"line": 2, "column": 25}, end={"line": 2, "column": 26})
        result = self.normalize("Where is it defined?", active_file="nested.ts", selection=nested)
        self.assertEqual(result.status, "ambiguous")
        self.assertGreater(len(result.entities[0].candidates), 1)
        invalid = EditorSelection(start={"line": 3, "column": 99999}, end={"line": 3, "column": 100000})
        result = self.normalize("Who calls this function?", active_file="auth.ts", selection=invalid)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.entities[0].symbol_id)

    def test_python_static_capability_does_not_reject_compound_discovery(self):
        result = self.normalize("Find the code in legacy.py and show its callers")
        self.assertEqual(result.intents, ["FEATURE_DISCOVERY", "DIRECT_CALLERS"])
        self.assertEqual(result.requirements[0].support, "supported")
        self.assertEqual(result.requirements[1].support, "unsupported")
        self.assertEqual(result.status, "partial")
        python = self.normalize("Who calls validate_token in legacy.py?")
        self.assertEqual(python.entities[0].resolution, "unsupported")
        self.assertIsNone(python.entities[0].symbol_id)

    def test_workflow_and_behavior_requirements_are_limitation_aware(self):
        workflow = self.normalize("Explain the login workflow")
        self.assertEqual(workflow.primary_intent, "WORKFLOW_EXPLANATION")
        self.assertEqual(workflow.requirements[0].support, "partial")
        behavior = self.normalize("Why does login fail?")
        self.assertEqual(behavior.primary_intent, "BEHAVIOR_INVESTIGATION")
        self.assertTrue(any("runtime" in item for item in behavior.requirements[0].limitations))

    def test_stale_source_and_cross_generation_requests_are_rejected(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            wrong = engine.normalize(self.request("Where is login defined?", snapshot_id="e" * 32))
            self.assertEqual(wrong.status, "stale_index")
            (self.fixture.root / "auth.ts").write_text("// changed\n" + self.fixture.files["auth.ts"])
            changed = engine.normalize(self.request("Who calls login?"))
            self.assertEqual(changed.status, "stale_index")
            self.assertFalse(changed.entities)

    def test_repeated_normalization_is_read_only_and_no_engine_is_invoked(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            before = hashlib.sha256(engine.database.read_bytes()).hexdigest()
            with patch("query_system.graph_engine.GraphQueryEngine.execute", side_effect=AssertionError("Graph answer")), \
                 patch("query_system.semantic_engine.SemanticSearchEngine.execute", side_effect=AssertionError("Semantic answer")), \
                 patch("query_system.rule_engine.RuleBasedAnalysisEngine.execute", side_effect=AssertionError("Rule answer")), \
                 patch("answers.answer_question", side_effect=AssertionError("LLM")), \
                 patch("config.embed_texts", side_effect=AssertionError("Embeddings")):
                first = engine.normalize(self.request("Who calls login?"))
                second = engine.normalize(self.request("Who calls login?"))
            self.assertEqual(first.model_dump(exclude={"normalization_ms"}), second.model_dump(exclude={"normalization_ms"}))
            self.assertEqual(hashlib.sha256(engine.database.read_bytes()).hexdigest(), before)
        self.assertEqual(engine.normalize(self.request("Where is login defined?")).status, "stale_index")

    def test_missing_metadata_and_failed_stages_are_explicit(self):
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            with closing(sqlite3.connect(engine.database)) as db, db:
                db.execute("UPDATE index_snapshots SET state='failed' WHERE stage='graph'")
            result = engine.normalize(self.request("Where is login defined?"))
            self.assertEqual(result.status, "stale_index")
        self.fixture.publish()
        with QueryUnderstandingEngine.pinned(self.store) as engine:
            engine.database.unlink()
            result = engine.normalize(self.request("Where is login defined?"))
            self.assertEqual(result.status, "error")
            self.assertFalse(engine.database.exists())


if __name__ == "__main__":
    unittest.main()
