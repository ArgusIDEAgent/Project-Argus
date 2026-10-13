"""Five deterministic rules over real, isolated, managed JS/TS snapshots."""

import asyncio
from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.metadata import trace
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.contracts import OperationRequest, validate_engine_result
from query_system.rule_engine import RuleBasedAnalysisEngine
from query_system.rule_registry import RULES


class RuleEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cm-phase4-")
        self.addCleanup(self.temp.cleanup)
        self.root, self.data = Path(self.temp.name) / "repo", Path(self.temp.name) / "data"
        self.root.mkdir()
        self.files = {
            "auth.ts": 'export async function validateToken(token: string, strict?: boolean, ...tags: string[]): Promise<boolean> {\n  if (strict && !token) throw new Error("empty");\n  return token.length > 0;\n}\nexport function standalone() { return true; }\nexport function sharedName() { return 1; }\nexport class Service {}\n',
            "login.ts": 'import {validateToken} from "./auth";\nexport const checker = validateToken;\nexport function login(token: string) { return validateToken(token); }\n',
            "entry.ts": 'import {login} from "./login";\nexport function entry() { return login("ok"); }\n',
            "other.ts": 'export function sharedName() { return 2; }\n',
            "dynamic.ts": 'export function invoke(fn: any) { return fn(); }\n',
            "cycle_a.ts": 'import {cycleB} from "./cycle_b";\nexport function cycleA() { return cycleB(); }\n',
            "cycle_b.ts": 'import {cycleA} from "./cycle_a";\nexport function cycleB() { return cycleA(); }\n',
            "plain.js": 'export function ping(value) { return value; }\n',
            "barrel.ts": 'export {validateToken as verify} from "./auth";\n',
            "consumer.ts": 'import {verify} from "./barrel";\nexport const selected = verify;\n',
            "classes.ts": 'import {Service} from "./auth";\nexport class Child extends Service {}\nexport interface Runner {}\nexport class Worker implements Runner {}\n',
            "tests/auth.test.ts": 'import {validateToken} from "../auth";\nexport function checkToken() { return validateToken("ok"); }\n',
            "tests/login.test.ts": 'import {login} from "../login";\nexport function checkLogin() { return login("ok"); }\n',
            "legacy.py": 'def validate_token(token):\n    return bool(token)\n',
        }
        for path, source in self.files.items():
            file = self.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source)
        self.repo = config.make_repo_id(str(self.root))
        self.client = FaissClient(self.data / "vectors")
        self.addCleanup(self.client.close)
        for name, kwargs in (("indexer.get_qdrant_client", {"return_value": self.client}),
                             ("indexer.embed_texts", {"side_effect": lambda texts: [[1.] + [0.] * 383 for _ in texts]}),
                             ("indexer.get_embedding_model", {})):
            mock = patch(name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.store = SnapshotStore(self.root, self.repo, self.data)
        self.publish()

    def publish(self):
        self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
                           index_directory=index_directory, build_graph=build_graph)

    def operation(self, engine, name, **parameters):
        return OperationRequest(engine="rules", name=name, repository_id=self.repo,
                                snapshot_id=engine.context.snapshot_id, parameters=parameters)

    def run_rule(self, name, **parameters):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            result = asyncio.run(engine.execute(self.operation(engine, name, **parameters), engine.context))
            validate_engine_result(result, engine.context, engine.evidence, RULES[name].operation)
            return result, dict(engine.evidence)

    def category(self, result, name):
        return [finding for finding in result.findings if finding.category == name]

    def test_function_contract_reports_declared_facts_not_runtime_behavior(self):
        result, evidence = self.run_rule("function_contract", reference="validateToken")
        self.assertEqual(result.status, "partial")
        contract = self.category(result, "function_contract")[0]
        self.assertEqual(contract.rule_id, "function_contract")
        self.assertEqual(contract.classification, "verified_observation")
        self.assertTrue(contract.details["declared_async"])
        self.assertEqual(contract.details["return_annotation"], ": Promise<boolean>")
        self.assertEqual([p["name"] for p in contract.details["parameters"]], ["token", "strict", "tags"])
        self.assertTrue(contract.details["parameters"][1]["optional"])
        self.assertTrue(contract.details["parameters"][2]["rest"])
        self.assertEqual(contract.details["exception_behavior"], "not_indexed")
        self.assertEqual(contract.details["return_behavior"], "not_inferred")
        source = evidence[contract.evidence_ids[0]]
        self.assertEqual((source.file_path, source.start_line, source.end_line), ("auth.ts", 1, 4))
        self.assertEqual(source.source_hash, hashlib.sha256((self.root / "auth.ts").read_bytes()).hexdigest())

    def test_javascript_contract_has_no_fabricated_return_annotation(self):
        result, _ = self.run_rule("function_contract", reference="ping")
        details = self.category(result, "function_contract")[0].details
        self.assertIsNone(details["return_annotation"])
        self.assertFalse(details["declared_async"])
        self.assertIsNone(details["parameters"][0]["type"])

    def test_dependency_summary_symbol_direction_and_optional_paths(self):
        result, evidence = self.run_rule("dependency_summary", reference="entry", path_target="validateToken", max_depth=2)
        self.assertEqual(result.status, "success")
        direct = self.category(result, "direct_dependency")
        self.assertEqual({f.details["target_name"] for f in direct}, {"login"})
        paths = self.category(result, "dependency_path")
        self.assertEqual(paths[0].details["path_names"], ["entry", "login", "validateToken"])
        self.assertTrue(all(evidence[key].resolution == "resolved" for f in paths for key in f.evidence_ids))

    def test_module_dependency_summary_has_direct_reverse_and_paths(self):
        result, _ = self.run_rule("dependency_summary", module_path="login.ts", path_target="auth.ts")
        self.assertEqual(result.status, "partial")  # Exported const checker has no canonical symbol.
        self.assertTrue(any("export_target_not_indexed" in item for item in result.unresolved_items))
        self.assertEqual({f.details["target_path"] for f in self.category(result, "direct_dependency")}, {"auth.ts"})
        self.assertIn("entry.ts", {f.details["source_path"] for f in self.category(result, "reverse_dependency")})
        self.assertEqual(self.category(result, "dependency_path")[0].details["path_names"], ["login.ts", "auth.ts"])

    def test_reference_and_test_rule_preserves_known_static_relationships(self):
        result, _ = self.run_rule("reference_test_analysis", reference="validateToken")
        self.assertEqual(result.status, "partial")  # Built-in Error constructor is not indexed.
        self.assertTrue(any("Error" in item for item in result.unresolved_items))
        self.assertTrue(self.category(result, "known_reference"))
        self.assertIn("login", {f.details["source_name"] for f in self.category(result, "resolved_caller")})
        tests = self.category(result, "test_reference")
        self.assertEqual({f.details["source_path"] for f in tests}, {"tests/auth.test.ts"})
        self.assertTrue(any("coverage" in warning for warning in result.warnings))

    def test_dependency_summary_reuses_inheritance_and_interface_edges(self):
        child, _ = self.run_rule("dependency_summary", reference="Child")
        self.assertIn("EXTENDS", {f.details["relationship"] for f in self.category(child, "direct_dependency")})
        base, _ = self.run_rule("dependency_summary", reference="Service")
        self.assertIn("Child", {f.details["source_name"] for f in self.category(base, "reverse_dependency")})
        worker, _ = self.run_rule("dependency_summary", reference="Worker")
        self.assertIn("IMPLEMENTS", {f.details["relationship"] for f in self.category(worker, "direct_dependency")})

    def test_missing_callers_and_tests_are_not_absence_or_coverage_claims(self):
        result, _ = self.run_rule("reference_test_analysis", reference="standalone")
        self.assertEqual(result.status, "partial")
        self.assertEqual(self.category(result, "caller_search")[0].details["matched"], 0)
        self.assertEqual(self.category(result, "test_search")[0].details["coverage"], "not_measured")

    def test_unresolved_relationships_are_explicit_without_fabricated_target(self):
        result, evidence = self.run_rule("reference_test_analysis", reference="invoke")
        self.assertEqual(result.status, "partial")
        unknown = self.category(result, "unresolved_relationship")
        self.assertTrue(unknown and result.unresolved_items)
        self.assertNotIn("target_id", unknown[0].details)
        self.assertTrue(all(evidence[key].resolution == "unresolved" for key in unknown[0].evidence_ids))

    def test_symbol_change_impact_distinguishes_connectivity_and_consequences(self):
        result, evidence = self.run_rule("change_impact", reference="validateToken", max_depth=3)
        possible = self.category(result, "possible_impact")
        self.assertTrue(possible)
        self.assertTrue(all(f.classification == "hypothesis" for f in possible))
        self.assertIn("entry.ts", {f.details["path"] for f in possible})
        self.assertIn("consumer.ts", {f.details["path"] for f in possible})
        self.assertTrue(self.category(result, "module_dependent"))
        self.assertTrue(self.category(result, "exposed_contract"))
        indirect = self.category(result, "possible_test_impact")
        self.assertIn("tests/login.test.ts", {f.details["test_path"] for f in indirect})
        self.assertTrue(all(evidence[key].snapshot_id == result.snapshot_id for f in possible for key in f.evidence_ids))
        self.assertTrue(all("breakage is not established" in f.statement for f in possible))

    def test_module_change_impact_reuses_dependency_paths(self):
        result, _ = self.run_rule("change_impact", module_path="auth.ts", max_depth=2)
        possible = self.category(result, "possible_impact")
        self.assertIn("entry.ts", {f.details["path"] for f in possible})
        self.assertTrue(self.category(result, "test_reference"))
        self.assertTrue(self.category(result, "definition"))

    def test_module_overview_has_definitions_dependents_exports_and_tests(self):
        result, _ = self.run_rule("module_overview", module_path="auth.ts")
        self.assertEqual(result.status, "success")
        self.assertIn("validateToken", {f.details["name"] for f in self.category(result, "definition")})
        self.assertIn("login.ts", {f.details["source_path"] for f in self.category(result, "known_dependent")})
        self.assertTrue(self.category(result, "public_interface"))
        self.assertTrue(self.category(result, "test_reference"))
        barrel, _ = self.run_rule("module_overview", module_path="barrel.ts")
        self.assertIn("validateToken", {f.details["target_name"] for f in self.category(barrel, "public_interface")})
        self.assertIn("verify", {f.details["exported_name"] for f in self.category(barrel, "public_interface")})
        self.assertTrue(self.category(barrel, "direct_import"))

    def test_duplicate_symbols_remain_ambiguous_at_limit_one(self):
        result, _ = self.run_rule("function_contract", reference="sharedName", limit=1)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(len(self.category(result, "candidate")), 1)
        resolved, _ = self.run_rule("function_contract", reference="sharedName", file_path="other.ts")
        self.assertEqual(resolved.status, "partial")

    def test_selected_symbol_id_and_missing_targets(self):
        initial, _ = self.run_rule("function_contract", reference="validateToken")
        identifier = initial.findings[0].details["id"]
        selected, _ = self.run_rule("function_contract", selected_symbol_id=identifier)
        self.assertEqual(selected.findings[0].details["id"], identifier)
        missing, _ = self.run_rule("function_contract", reference="missingFunction")
        self.assertEqual(missing.status, "not_found")
        module, _ = self.run_rule("module_overview", module_path="missing.ts")
        self.assertEqual(module.status, "not_found")
        path, _ = self.run_rule("dependency_summary", reference="entry", path_target="missingFunction")
        self.assertEqual(path.status, "partial")

    def test_secondary_path_ambiguity_and_unsupported_capabilities_keep_verified_facts(self):
        ambiguous, _ = self.run_rule("dependency_summary", reference="validateToken", path_target="sharedName")
        self.assertEqual(ambiguous.status, "ambiguous")
        self.assertEqual(len(self.category(ambiguous, "candidate")), 2)
        unsupported, _ = self.run_rule("dependency_summary", module_path="auth.ts", path_target="legacy.py")
        self.assertEqual(unsupported.status, "partial")
        self.assertTrue(self.category(unsupported, "reverse_dependency"))
        self.assertTrue(any("requested path omitted" in warning for warning in unsupported.warnings))

    def test_unsupported_language_and_nonfunction_symbol_are_explicit(self):
        result, _ = self.run_rule("function_contract", reference="validate_token", file_path="legacy.py")
        self.assertEqual(result.status, "unsupported")
        module, _ = self.run_rule("module_overview", module_path="legacy.py")
        self.assertEqual(module.status, "unsupported")
        nonfunction, _ = self.run_rule("function_contract", reference="Service")
        self.assertEqual(nonfunction.status, "unsupported")

    def test_cycles_depth_and_finding_limits_are_bounded(self):
        cycle, _ = self.run_rule("change_impact", reference="cycleA", max_depth=3, limit=20)
        self.assertLessEqual(len(cycle.findings), 20)
        for path in self.category(cycle, "incoming_path"):
            nodes = path.details["path_node_ids"]
            self.assertEqual(len(nodes), len(set(nodes)))
            self.assertLessEqual(len(nodes), 4)
        limited, _ = self.run_rule("change_impact", reference="validateToken", limit=1)
        self.assertEqual(limited.status, "partial")
        self.assertEqual(len(limited.findings), 1)
        shallow, _ = self.run_rule("dependency_summary", reference="entry", path_target="validateToken", max_depth=1)
        self.assertEqual(shallow.status, "partial")
        self.assertFalse(self.category(shallow, "dependency_path"))

    def test_node_edge_footprint_limits_skip_existing_materializing_queries(self):
        for limits in ({"max_nodes": 1}, {"max_edges": 1}):
            with self.subTest(limits=limits), patch("knowledge_base.graph.KnowledgeGraph.direct_callers", side_effect=AssertionError("Unbounded graph call")):
                result, _ = self.run_rule("change_impact", reference="validateToken", **limits)
                self.assertEqual(result.status, "partial")
                self.assertFalse(self.category(result, "possible_impact"))
                self.assertTrue(any("footprint" in warning for warning in result.warnings))

    def test_signature_context_budget_omits_without_inventing_facts(self):
        result, _ = self.run_rule("function_contract", reference="validateToken", max_source_bytes=128)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.findings[0].details["signature_status"], "omitted_by_budget")
        self.assertNotIn("parameters", result.findings[0].details)

    def test_repeated_results_are_stable_with_no_llm_embeddings_or_semantic_calls(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            before = hashlib.sha256(engine.database.read_bytes()).hexdigest()
            operation = self.operation(engine, "module_overview", module_path="auth.ts")
            with patch("answers.urlopen", side_effect=AssertionError("LLM")), \
                 patch("config.embed_texts", side_effect=AssertionError("Embedding")), \
                 patch("indexer.index_directory", side_effect=AssertionError("Indexing")), \
                 patch("query_system.semantic_engine.SemanticSearchEngine.execute", side_effect=AssertionError("Semantic discovery")):
                first = asyncio.run(engine.execute(operation, engine.context))
                second = asyncio.run(engine.execute(operation, engine.context))
            self.assertEqual(first.model_dump(exclude={"execution_ms"}), second.model_dump(exclude={"execution_ms"}))
            self.assertEqual(hashlib.sha256(engine.database.read_bytes()).hexdigest(), before)

    def test_one_pin_with_legitimate_different_internal_stage_ids(self):
        with patch.object(self.store, "pin_view", wraps=self.store.pin_view) as pins:
            result, evidence = self.run_rule("reference_test_analysis", reference="validateToken")
        self.assertEqual(pins.call_count, 1)
        self.assertEqual({source.snapshot_id for source in evidence.values()}, {result.snapshot_id})
        self.assertGreater(len({source.index_snapshot_id for source in evidence.values()}), 1)
        self.assertTrue(all(source.index_snapshot_id != result.snapshot_id for source in evidence.values()))

    def test_mixed_generation_repository_and_expired_scope_are_rejected(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            operation = self.operation(engine, "function_contract", reference="validateToken")
            wrong = operation.model_copy(update={"snapshot_id": "f" * 32})
            self.assertEqual(asyncio.run(engine.execute(wrong, engine.context)).status, "stale_index")
            wrong_context = engine.context.model_copy(update={"snapshot_id": "e" * 32})
            self.assertEqual(asyncio.run(engine.execute(operation, wrong_context)).status, "stale_index")
            wrong_repo = operation.model_copy(update={"repository_id": "d" * 24})
            self.assertEqual(asyncio.run(engine.execute(wrong_repo, engine.context)).status, "error")
        self.assertEqual(asyncio.run(engine.execute(operation, engine.context)).status, "stale_index")

    def test_source_hash_mismatch_and_midquery_edits_discard_all_findings(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            (self.root / "auth.ts").write_text("// changed\n" + self.files["auth.ts"])
            result = asyncio.run(engine.execute(self.operation(engine, "function_contract", reference="validateToken"), engine.context))
            self.assertEqual(result.status, "stale_index")
            self.assertFalse(result.findings)
        (self.root / "auth.ts").write_text(self.files["auth.ts"])
        def changing(*args, **kwargs):
            info = trace(*args, **kwargs)
            (self.root / "auth.ts").write_text("// changed\n" + self.files["auth.ts"])
            return info
        with RuleBasedAnalysisEngine.pinned(self.store) as engine, patch("query_system.rule_engine.trace", side_effect=changing):
            result = asyncio.run(engine.execute(self.operation(engine, "function_contract", reference="validateToken"), engine.context))
            self.assertEqual(result.status, "stale_index")
            self.assertFalse(result.findings)

    def test_missing_metadata_does_not_create_a_database(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            engine.database.unlink()
            result = asyncio.run(engine.execute(self.operation(engine, "function_contract", reference="validateToken"), engine.context))
            self.assertIn(result.status, ("error", "stale_index"))
            self.assertFalse(engine.database.exists())

    def test_failed_stage_and_invalid_canonical_range_are_rejected(self):
        for mutation in ("UPDATE index_snapshots SET state='failed' WHERE stage='graph'",
                         "UPDATE symbols SET end_line=999999 WHERE name='validateToken'",
                         "DELETE FROM symbol_sources WHERE symbol_id IN (SELECT id FROM symbols WHERE name='validateToken')"):
            with self.subTest(mutation=mutation):
                self.publish()
                with RuleBasedAnalysisEngine.pinned(self.store) as engine:
                    with closing(sqlite3.connect(engine.database)) as db, db:
                        db.execute(mutation)
                    result = asyncio.run(engine.execute(self.operation(engine, "function_contract", reference="validateToken"), engine.context))
                    self.assertEqual(result.status, "stale_index")
                    self.assertFalse(result.findings)

    def test_mocked_cross_generation_graph_evidence_is_rejected(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            original = engine.graph_engine.execute
            async def wrong_generation(*args):
                result = await original(*args)
                return result.model_copy(update={"snapshot_id": "f" * 32})
            with patch.object(engine.graph_engine, "execute", side_effect=wrong_generation):
                result = asyncio.run(engine.execute(self.operation(engine, "function_contract", reference="validateToken"), engine.context))
            self.assertEqual(result.status, "stale_index")
            self.assertFalse(result.findings)

    def test_mocked_graph_proof_with_wrong_hash_cannot_pass_the_bridge(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            original = engine.graph_engine.execute
            async def wrong_hash(*args):
                result = await original(*args)
                identifier = result.evidence_ids[0]
                engine.graph_engine.evidence[identifier] = engine.graph_engine.evidence[identifier].model_copy(
                    update={"source_hash": "f" * 64})
                return result
            with patch.object(engine.graph_engine, "execute", side_effect=wrong_hash):
                result = asyncio.run(engine.execute(self.operation(engine, "function_contract", reference="validateToken"), engine.context))
            self.assertEqual(result.status, "stale_index")
            self.assertFalse(result.findings)

    def test_execution_budget_is_explicit(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine, patch.dict(RULES, {
                "module_overview": replace(RULES["module_overview"], max_execution_ms=1)}), \
                patch("query_system.rule_engine.monotonic", side_effect=[0., 0., 1., 1., 1.]):
            result = asyncio.run(engine.execute(self.operation(engine, "module_overview", module_path="auth.ts"), engine.context))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.error.code, "budget_exceeded")
        self.assertFalse(result.findings)

    def test_invalid_parameters_and_unknown_rules_return_typed_outcomes(self):
        with RuleBasedAnalysisEngine.pinned(self.store) as engine:
            for params in ({"reference": "x", "limit": 0}, {"module_path": "../escape.ts"},
                           {"reference": "x", "llm": True}, {"reference": "x", "module_path": "auth.ts"}):
                with self.subTest(params=params):
                    result = asyncio.run(engine.execute(self.operation(engine, "function_contract", **params), engine.context))
                    self.assertEqual(result.status, "error")
                    self.assertEqual(result.error.code, "invalid_operation")
            unknown = asyncio.run(engine.execute(self.operation(engine, "new_rule", reference="x"), engine.context))
            self.assertEqual(unknown.status, "unsupported")


if __name__ == "__main__":
    unittest.main()
