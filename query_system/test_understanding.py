"""Pure recognition, backwards-compatible contracts and strict input bounds."""

from dataclasses import asdict
import unittest

from pydantic import ValidationError

from knowledge_base.retrieval import plan_query
from query_system.contracts import IntentRequirement, NormalizedQuery, QueryContext, QueryRequest
from query_system.query_understanding import parse_question


class RecognitionTests(unittest.TestCase):
    def test_core_taxonomy_and_legacy_compatibility(self):
        for question, intents in (
            ("Where is validateToken defined?", ["SYMBOL_LOCATION"]),
            ("Who calls login?", ["DIRECT_CALLERS"]),
            ("What does login call?", ["DIRECT_CALLEES"]),
            ("What does login do?", ["FUNCTION_EXPLANATION"]),
            ("Contract of validateToken", ["FUNCTION_CONTRACT"]),
            ("Impact of changing validateToken", ["CHANGE_IMPACT"]),
            ("Explain the login workflow", ["WORKFLOW_EXPLANATION"]),
            ("Why does login fail?", ["BEHAVIOR_INVESTIGATION"]),
            ("Module overview of auth.ts", ["MODULE_OVERVIEW"]),
            ("What tests cover login?", ["TEST_DISCOVERY"]),
            ("Dependency path from entry to validateToken", ["DEPENDENCY_PATH"]),
            ("Where is the code that rejects expired sessions?", ["FEATURE_DISCOVERY"]),
        ):
            with self.subTest(question=question):
                result = parse_question(question)
                self.assertEqual(result.intents, intents)
                self.assertEqual(result.legacy_plan, asdict(plan_query(question)))
        old = NormalizedQuery(intents=["CODE_COMPARISON"], entities=[])
        self.assertEqual(NormalizedQuery.model_validate_json(old.model_dump_json()), old)

    def test_preserves_exact_identifiers_paths_and_quoted_whitespace(self):
        question = '  Who calls `AuthService.check` in service.ts?  '
        result = parse_question(question)
        self.assertEqual(result.original_question, question)
        self.assertIn('`AuthService.check`', result.normalized_question)
        self.assertEqual(result.entities[0].name, "AuthService.check")
        self.assertEqual(result.entities[0].file_path, "service.ts")
        self.assertEqual(parse_question("Where is snake_case defined?").entities[0].name, "snake_case")
        unicode = parse_question("Where is `ｆoo` defined?")
        self.assertEqual(unicode.entities[0].name, "ｆoo")
        self.assertIn("`ｆoo`", unicode.normalized_question)
        self.assertIn('"refund  eligibility"', parse_question('Find "refund  eligibility"').normalized_question)

    def test_compound_target_order_and_dependencies(self):
        result = parse_question("Find the login function, show its callers, and tell me what might be affected if I change it.")
        self.assertEqual(result.intents, ["SYMBOL_LOCATION", "DIRECT_CALLERS", "CHANGE_IMPACT"])
        self.assertEqual([r.entity_indices for r in result.requirements], [[0], [0], [0]])
        self.assertEqual([r.depends_on for r in result.requirements], [[], [0], [1]])

    def test_pasted_comments_and_quoted_instructions_are_not_intents(self):
        result = parse_question('The comment says "Who calls missingFunction?"; where is `login` defined?')
        self.assertEqual(result.intents, ["SYMBOL_LOCATION"])
        self.assertEqual([item.name for item in result.entities], ["login"])
        escaped = parse_question('The string says "who calls \\"fake\\""; where is `login` defined?')
        self.assertEqual(escaped.intents, ["SYMBOL_LOCATION"])
        result = parse_question('```ts\n// show callers and change impact\nfunction fake() {}\n```\nWhere is login defined?')
        self.assertEqual(result.intents, ["SYMBOL_LOCATION"])
        self.assertEqual([item.name for item in result.entities], ["login"])

    def test_malformed_inputs_and_structural_contracts(self):
        for question in ("", "   ", "x" * 2001):
            with self.subTest(question=question), self.assertRaises(ValidationError):
                QueryRequest(repository_id="a" * 24, question=question)
        with self.assertRaises(ValueError):
            parse_question("who\x00calls login")
        with self.assertRaises(ValidationError):
            QueryContext(repository_id="a" * 24, snapshot_id="b" * 32, previous_intents=["invalid"])
        with self.assertRaises(ValidationError):
            NormalizedQuery(intents=["SYMBOL_LOCATION"], requirements=[IntentRequirement(
                intent="SYMBOL_LOCATION", order=0, entity_indices=[99])])
        with self.assertRaises(ValidationError):
            NormalizedQuery(intents=["SYMBOL_LOCATION"], requirements=[IntentRequirement(
                intent="SYMBOL_LOCATION", order=0, depends_on=[0])])

    def test_generic_pronoun_followup_is_not_forced_into_feature_search(self):
        self.assertEqual(parse_question("What about it?").intents, ["UNRESOLVED"])

    def test_recognition_limits_remain_explicit_when_a_description_is_added(self):
        result = parse_question("Find code in " + ", ".join(f"file{i}.ts" for i in range(20)))
        self.assertEqual(result.status, "partial")
        self.assertLessEqual(len(result.entities), 12)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
