"""Typed registry and backwards-compatible rule contract tests."""

from dataclasses import FrozenInstanceError
import unittest

from pydantic import ValidationError

from query_system.contracts import EngineResult, RuleFinding
from query_system.rule_registry import RULES, DependencyParameters, ModuleAnalysisParameters, SymbolAnalysisParameters


class RuleContractTests(unittest.TestCase):
    def test_registry_has_five_explicit_typed_rules(self):
        self.assertEqual(len(RULES), 5)
        for name, definition in RULES.items():
            self.assertEqual(name, definition.rule_id)
            self.assertEqual(definition.operation.engine, "rules")
            self.assertEqual(definition.operation.parameters_model, definition.parameters_model)
            self.assertEqual(definition.supported_languages, frozenset({"javascript", "typescript"}))
            self.assertTrue(definition.required_inputs and definition.required_capabilities)
            self.assertTrue(definition.required_evidence and definition.limitations)
            with self.assertRaises(FrozenInstanceError):
                definition.rule_id = "other"

    def test_parameters_reject_unsupported_or_unbounded_scope(self):
        valid = DependencyParameters(module_path="src/auth.ts", path_target="src/login.ts", max_depth=2)
        self.assertEqual(valid.path_target, "src/login.ts")
        for kwargs in ({}, {"reference": "x", "module_path": "x.ts"}, {"reference": "x", "max_depth": 6},
                       {"reference": "x", "max_nodes": 0}, {"reference": "x", "max_edges": 10001},
                       {"reference": "x", "limit": 101}, {"reference": "x", "max_source_bytes": 1},
                       {"reference": "x", "file_path": "../secret.ts"}, {"reference": "x", "limit": "5"},
                       {"module_path": "x.py", "path_target": "../secret.py"},
                       {"reference": "x", "path_target_file": "x.ts"}, {"reference": "x", "llm": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                DependencyParameters(**kwargs)
        with self.assertRaises(ValidationError):
            SymbolAnalysisParameters(module_path="x.ts")
        with self.assertRaises(ValidationError):
            ModuleAnalysisParameters(reference="x")

    def test_structured_rule_findings_round_trip_and_keep_old_contract(self):
        old = RuleFinding(statement="Known static relationship", classification="verified_observation", evidence_ids=["edge"])
        self.assertIsNone(old.rule_id)
        new = RuleFinding(rule_id="change_impact", category="possible_impact", statement="May require review",
                          classification="hypothesis", details={"path": "a.ts", "breakage": "not_established"},
                          limitations=["Not runtime coverage"], evidence_ids=["edge"])
        result = EngineResult(engine="rules", status="partial", repository_id="a" * 24, snapshot_id="b" * 32,
                              findings=[old, new], evidence_ids=["edge"], execution_ms=0)
        self.assertEqual(EngineResult.model_validate_json(result.model_dump_json()), result)
        with self.assertRaises(ValidationError):
            RuleFinding(statement="Unsupported assertion", classification="certain_breakage", evidence_ids=["edge"])


if __name__ == "__main__":
    unittest.main()
