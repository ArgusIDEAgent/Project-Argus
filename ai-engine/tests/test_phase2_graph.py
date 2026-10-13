"""Phase 2 graph engine against real managed SQLite/FAISS generations."""

import asyncio
from contextlib import closing
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
from knowledge_base.metadata import trace as canonical_trace
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.contracts import OperationRequest, validate_engine_result
from query_system.graph_engine import GraphQueryEngine
from query_system.graph_presentation import format_graph_result


class GraphEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.data = Path(self.temp.name) / "data"
        self.files = {
            "auth.ts": 'export function validateToken(token: string) {\n  return token.length > 0;\n}\nexport function sharedName() { return 1; }\nexport function standalone() { return true; }\n',
            "login.ts": 'import {validateToken} from "./auth";\nexport const checker = validateToken;\nexport function login(token: string) { validateToken(token); return validateToken(token); }\nexport class AuthService { check(token: string) { return validateToken(token); } }\n',
            "session.ts": 'import {validateToken} from "./auth";\nexport function refreshSession(token: string) { return validateToken(token); }\n',
            "entry.ts": 'import {login} from "./login";\nimport {refreshSession} from "./session";\nexport function entry() { return login("ok") && refreshSession("ok"); }\n',
            "other.ts": 'export function sharedName() { return 2; }\n',
            "dynamic.ts": 'export function invoke(fn: any) { return fn(); }\n',
            "cycle_a.ts": 'import {cycleB} from "./cycle_b";\nexport function cycleA() { return cycleB(); }\n',
            "cycle_b.ts": 'import {cycleA} from "./cycle_a";\nexport function cycleB() { return cycleA(); }\n',
        }
        for path, source in self.files.items():
            (self.root / path).write_text(source)
        self.repo = config.make_repo_id(str(self.root))
        self.store = SnapshotStore(self.root, self.repo, self.data)
        self.client = FaissClient(self.data / "vectors")
        self.addCleanup(self.client.close)
        for name, kwargs in (
            ("indexer.get_qdrant_client", {"return_value": self.client}),
            ("indexer.embed_texts", {"side_effect": lambda texts: [[1.] + [0.] * 383 for _ in texts]}),
            ("indexer.get_embedding_model", {}),
        ):
            guard = patch(name, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)
        self.publish()

    def publish(self):
        self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
                           index_directory=index_directory, build_graph=build_graph)

    def operation(self, engine, name, **parameters):
        return OperationRequest(engine="graph", name=name, repository_id=self.repo,
                                snapshot_id=engine.context.snapshot_id, parameters=parameters)

    def execute(self, name, **parameters):
        with GraphQueryEngine.pinned(self.store) as engine:
            result = asyncio.run(engine.execute(self.operation(engine, name, **parameters), engine.context))
            validate_engine_result(result, engine.context, engine.evidence)
            presentation = format_graph_result(name, result, engine.evidence)
            return result, dict(engine.evidence), presentation

    def test_definition_and_exact_source_identity(self):
        result, evidence, presentation = self.execute("find_definition", reference="validateToken")
        self.assertEqual(result.status, "success")
        finding = result.findings[0]
        source = evidence[finding.evidence_ids[0]]
        self.assertEqual(source.symbol_id, finding.entity_id)
        self.assertEqual((source.file_path, source.start_line, source.end_line), ("auth.ts", 1, 3))
        self.assertEqual(source.source_hash, hashlib.sha256((self.root / "auth.ts").read_bytes()).hexdigest())
        self.assertEqual(source.source_origin, "disk")
        self.assertGreater(source.schema_version, 0)
        self.assertEqual(presentation.entries[0].label, "validateToken")
        self.assertEqual(len(finding.entity_id), 64)

    def test_callers_group_call_sites_and_callees_preserve_direction(self):
        callers, evidence, presentation = self.execute("find_callers", reference="validateToken")
        self.assertEqual(callers.status, "success")
        self.assertEqual({f.source_name for f in callers.findings}, {"login", "refreshSession", "AuthService.check"})
        self.assertTrue(all(f.target_name == "validateToken" for f in callers.findings))
        self.assertEqual(len(presentation.entries), 3)
        login = next(entry for entry in presentation.entries if entry.label == "login")
        self.assertEqual(len(login.sources), 2)
        self.assertTrue(all(item.resolution == "resolved" for item in login.sources))
        callees, _, _ = self.execute("find_callees", reference="login")
        self.assertEqual({f.target_name for f in callees.findings}, {"validateToken"})
        self.assertTrue(all(f.source_name == "login" for f in callees.findings))

    def test_missing_symbol_and_empty_relationships(self):
        for name, reference in (("find_definition", "absent"), ("find_callers", "standalone"),
                                ("find_callees", "standalone")):
            with self.subTest(name=name, reference=reference):
                result, _, presentation = self.execute(name, reference=reference)
                self.assertEqual(result.status, "not_found")
                self.assertEqual(result.findings, [])
                self.assertIn("No verified", presentation.summary)

    def test_duplicate_names_remain_ambiguous_even_at_limit_one(self):
        result, evidence, _ = self.execute("find_definition", reference="sharedName", limit=1)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(len(result.findings), 1)
        result, evidence, _ = self.execute("find_callers", reference="sharedName")
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual({item.file_path for item in evidence.values()}, {"auth.ts", "other.ts"})
        self.assertTrue(all(f.relationship == "DEFINITION" for f in result.findings))

    def test_file_scope_canonical_id_qualified_name_and_editor_selection(self):
        scoped, _, _ = self.execute("find_definition", reference="sharedName", file_path="other.ts")
        self.assertEqual(scoped.status, "success")
        selected = scoped.findings[0].entity_id
        for parameters in ({"reference": selected}, {"selected_symbol_id": selected},
                           {"reference": "sharedName", "selected_symbol_id": selected}):
            with self.subTest(parameters=parameters):
                result, _, _ = self.execute("find_definition", **parameters)
                self.assertEqual(result.findings[0].entity_id, selected)
        qualified, _, _ = self.execute("find_definition", reference="AuthService.check")
        self.assertEqual(qualified.status, "success")
        conflict, _, _ = self.execute("find_definition", reference="validateToken", selected_symbol_id=selected)
        self.assertEqual(conflict.status, "not_found")

    def test_modules_imports_dependencies_and_reverse_direction(self):
        expected = {
            "find_imports": ("login.ts", "IMPORTS", {"auth.ts"}),
            "find_importers": ("auth.ts", "IMPORTS", {"login.ts", "session.ts"}),
            "find_dependencies": ("login.ts", "DEPENDS_ON", {"auth.ts"}),
            "find_reverse_dependencies": ("auth.ts", "DEPENDS_ON", {"login.ts", "session.ts"}),
        }
        for name, (module, relationship, neighbors) in expected.items():
            with self.subTest(name=name):
                result, evidence, presentation = self.execute(name, module_path=module)
                self.assertEqual(result.status, "success")
                self.assertEqual({entry.label for entry in presentation.entries}, neighbors)
                self.assertTrue(all(f.relationship == relationship for f in result.findings))
                self.assertTrue(all(f.source_symbol_id is None and f.target_symbol_id is None for f in result.findings))
                self.assertTrue(all(f.source_node_id and f.target_node_id for f in result.findings))
                self.assertTrue(all(item.snapshot_id == result.snapshot_id for item in evidence.values()))

    def test_verified_references_use_existing_reference_edges(self):
        result, evidence, _ = self.execute("find_references", reference="validateToken")
        self.assertEqual(result.status, "success")
        self.assertTrue(result.findings)
        self.assertTrue(all(f.relationship == "REFERENCES" for f in result.findings))
        self.assertTrue(all(evidence[f.evidence_ids[0]].resolution == "resolved" for f in result.findings))

    def test_unresolved_callee_does_not_create_a_target(self):
        result, evidence, _ = self.execute("find_callees", reference="invoke")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.findings, [])
        self.assertTrue(result.unresolved_items)
        unknown = [item for item in evidence.values() if item.evidence_type == "graph_relationship"]
        self.assertTrue(unknown)
        self.assertTrue(all(item.resolution == "unresolved" for item in unknown))

    def test_symbol_and_module_paths_are_source_grounded(self):
        result, evidence, presentation = self.execute("find_paths", source="entry", target="validateToken")
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.findings), 3)
        self.assertEqual(len(presentation.entries), 2)
        self.assertTrue(all(f.path_names[0] == "entry" and f.path_names[-1] == "validateToken" for f in result.findings))
        for finding in result.findings:
            self.assertEqual(len(finding.evidence_ids), len(finding.path_node_ids) - 1)
            self.assertTrue(all(evidence[i].resolution == "resolved" for i in finding.evidence_ids))
        modules, _, _ = self.execute("find_dependency_paths", source="entry.ts", target="auth.ts")
        self.assertEqual(modules.status, "success")
        self.assertEqual(len(modules.findings), 2)
        self.assertTrue(all(f.relationship == "DEPENDS_ON" for f in modules.findings))

    def test_cycles_and_traversal_limits(self):
        cycle, _, _ = self.execute("find_paths", source="cycleA", target="cycleB", max_depth=3)
        self.assertEqual(cycle.status, "success")
        self.assertEqual(cycle.findings[0].path_names, ["cycleA", "cycleB"])
        module_cycle, _, _ = self.execute("find_dependency_paths", source="cycle_a.ts", target="cycle_b.ts")
        self.assertEqual(module_cycle.status, "success")
        self.assertEqual(module_cycle.findings[0].path_names, ["cycle_a.ts", "cycle_b.ts"])
        bounded, _, _ = self.execute("find_paths", source="entry", target="validateToken", max_depth=1)
        self.assertEqual(bounded.status, "partial")
        self.assertEqual(bounded.findings, [])
        for parameters in ({"limit": 1}, {"max_work": 1}):
            with self.subTest(parameters=parameters):
                limited, _, _ = self.execute("find_paths", source="entry", target="validateToken", **parameters)
                self.assertEqual(limited.status, "partial")
        direct, _, _ = self.execute("find_callers", reference="validateToken", limit=1)
        self.assertEqual(direct.status, "partial")
        self.assertEqual(len(direct.findings), 1)
        module_paths, _, _ = self.execute("find_dependency_paths", source="entry.ts", target="auth.ts", limit=1)
        self.assertEqual(module_paths.status, "partial")

    def test_unsupported_and_invalid_parameters(self):
        unsupported, _, _ = self.execute("execute_shell", command="pwd")
        self.assertEqual(unsupported.status, "unsupported")
        for name, parameters in (("find_definition", {"reference": "x", "limit": 0}),
                                  ("find_dependencies", {"module_path": "../escape.ts"}),
                                  ("find_paths", {"source": "a", "target": "b", "max_depth": 100}),
                                  ("find_definition", {"reference": "x", "unexpected": True})):
            with self.subTest(name=name, parameters=parameters):
                result, _, _ = self.execute(name, **parameters)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.error.code, "invalid_operation")

    def test_stale_source_and_mid_query_edit_discard_findings(self):
        with GraphQueryEngine.pinned(self.store) as engine:
            (self.root / "auth.ts").write_text(self.files["auth.ts"].replace("> 0", "> 1"))
            result = asyncio.run(engine.execute(self.operation(engine, "find_definition", reference="validateToken"), engine.context))
            self.assertEqual(result.status, "stale_index")
            self.assertEqual(result.findings, [])
        (self.root / "auth.ts").write_text(self.files["auth.ts"])
        def changed(*args):
            info = canonical_trace(*args)
            (self.root / "auth.ts").write_text(self.files["auth.ts"].replace("> 0", "> 2"))
            return info
        with patch("query_system.graph_engine.trace", side_effect=changed):
            result, _, _ = self.execute("find_definition", reference="validateToken")
        self.assertEqual(result.status, "stale_index")
        self.assertEqual(result.evidence_ids, [])

    def test_mixed_generation_repository_and_expired_pin_are_rejected(self):
        with GraphQueryEngine.pinned(self.store) as engine:
            operation = self.operation(engine, "find_definition", reference="validateToken")
            wrong = operation.model_copy(update={"snapshot_id": "0" * 32})
            self.assertEqual(asyncio.run(engine.execute(wrong, engine.context)).status, "stale_index")
            wrong_context = engine.context.model_copy(update={"snapshot_id": "1" * 32})
            self.assertEqual(asyncio.run(engine.execute(operation, wrong_context)).status, "stale_index")
            wrong_repo = operation.model_copy(update={"repository_id": "2" * 24})
            self.assertEqual(asyncio.run(engine.execute(wrong_repo, engine.context)).status, "error")
        self.assertEqual(asyncio.run(engine.execute(operation, engine.context)).status, "stale_index")

    def test_distinct_stage_ids_are_valid_in_one_pinned_generation(self):
        result, evidence, _ = self.execute("find_callers", reference="validateToken")
        self.assertEqual(result.status, "success")
        self.assertGreater(len({item.index_snapshot_id for item in evidence.values()}), 1)
        self.assertEqual({item.snapshot_id for item in evidence.values()}, {result.snapshot_id})
        self.assertEqual({item.repository_id for item in evidence.values()}, {self.repo})

    def test_deterministic_repetition_read_only_and_no_llm_or_embeddings(self):
        with GraphQueryEngine.pinned(self.store) as engine:
            before = hashlib.sha256(engine.database.read_bytes()).hexdigest()
            operation = self.operation(engine, "find_callers", reference="validateToken")
            with patch("answers.urlopen", side_effect=AssertionError("Graph queries cannot call LLMs")), \
                 patch("indexer.embed_texts", side_effect=AssertionError("Graph queries cannot embed")):
                first = asyncio.run(engine.execute(operation, engine.context))
                second = asyncio.run(engine.execute(operation, engine.context))
            self.assertEqual(first.model_dump(exclude={"execution_ms"}), second.model_dump(exclude={"execution_ms"}))
            self.assertEqual(before, hashlib.sha256(engine.database.read_bytes()).hexdigest())
            self.assertEqual(format_graph_result(operation.name, first, engine.evidence),
                             format_graph_result(operation.name, second, engine.evidence))

    def test_execution_budget_is_explicit(self):
        with GraphQueryEngine.pinned(self.store) as engine:
            operation = self.operation(engine, "find_definition", reference="validateToken")
            with patch("query_system.graph_engine.monotonic", side_effect=[0., 6.]):
                result = asyncio.run(engine.execute(operation, engine.context))
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.error.code, "budget_exceeded")
            self.assertEqual(result.findings, [])
            presentation = format_graph_result(operation.name, result, engine.evidence)
            self.assertIn("budget", presentation.warnings[0])

    def test_missing_index_cannot_be_recreated_by_a_query(self):
        with GraphQueryEngine.pinned(self.store) as engine:
            engine.database.unlink()
            result = asyncio.run(engine.execute(self.operation(engine, "find_definition", reference="validateToken"), engine.context))
            self.assertEqual(result.status, "error")
            self.assertEqual(result.error.code, "unavailable")
            self.assertFalse(engine.database.exists())

    def test_unready_stage_and_invalid_source_range_are_rejected(self):
        for corruption in ("failed_stage", "bad_line"):
            with self.subTest(corruption=corruption), GraphQueryEngine.pinned(self.store) as engine:
                operation = self.operation(engine, "find_callers", reference="validateToken")
                with closing(sqlite3.connect(engine.database)) as db, db:
                    if corruption == "failed_stage":
                        db.execute("UPDATE index_snapshots SET state='failed' WHERE stage='graph'")
                    else:
                        db.execute("UPDATE relationships SET start_line=999 WHERE relationship='CALLS'")
                result = asyncio.run(engine.execute(operation, engine.context))
                self.assertEqual(result.status, "stale_index")
                self.assertEqual(result.findings, [])
                with closing(sqlite3.connect(engine.database)) as db, db:
                    if corruption == "failed_stage":
                        db.execute("UPDATE index_snapshots SET state='ready' WHERE stage='graph'")

    def test_module_ambiguity_and_exact_repository_paths(self):
        for directory in ("a", "b"):
            (self.root / directory).mkdir()
            (self.root / directory / "deps.ts").write_text("export const value = 1;\n")
        self.publish()
        ambiguous, _, presentation = self.execute("find_dependencies", module_path="deps.ts")
        self.assertEqual(ambiguous.status, "ambiguous")
        self.assertEqual({entry.label for entry in presentation.entries}, {"a/deps.ts", "b/deps.ts"})
        scoped, _, _ = self.execute("find_dependencies", module_path="a/deps.ts")
        self.assertEqual(scoped.status, "not_found")

    def test_failed_graph_stage_cannot_appear_as_a_healthy_empty_result(self):
        with GraphQueryEngine.pinned(self.store) as engine:
            with closing(sqlite3.connect(engine.database)) as db, db:
                db.execute("UPDATE index_snapshots SET state='failed' WHERE stage='graph'")
            for reference in ("standalone", "absent"):
                result = asyncio.run(engine.execute(self.operation(engine, "find_callers", reference=reference), engine.context))
                self.assertEqual(result.status, "stale_index")
                self.assertEqual(result.findings, [])


if __name__ == "__main__":
    unittest.main()
