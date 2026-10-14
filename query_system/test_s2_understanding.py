"""S2 lexical regressions: explicit roles and descriptions, no index/model calls."""
import unittest

from query_system.contracts import NormalizedQuery
from query_system.query_understanding import parse_question


def rows(question):
    query = parse_question(question)
    return query, [(row.intent, [query.entities[i].name for i in row.entity_indices]) for row in query.requirements]


class StabilizationRecognitionTests(unittest.TestCase):
    def test_requested_comparison_families_preserve_order_and_both_operands(self):
        for question, expected in [
            ("Compare login and entry", [("CODE_COMPARISON", ["login", "entry"])]),
            ("Compare login and entry and find tests for both", [("CODE_COMPARISON", ["login", "entry"]), ("TEST_DISCOVERY", ["login", "entry"])]),
            ("Compare login and entry, and compare validateToken and standalone", [("CODE_COMPARISON", ["login", "entry"]), ("CODE_COMPARISON", ["validateToken", "standalone"])]),
            ("Find callers of login and compare entry and validateToken", [("DIRECT_CALLERS", ["login"]), ("CODE_COMPARISON", ["entry", "validateToken"])]),
            ("Compare login with entry and explain both", [("CODE_COMPARISON", ["login", "entry"]), ("FUNCTION_EXPLANATION", ["login", "entry"])]),
        ]:
            with self.subTest(question=question):
                query, actual = rows(question)
                self.assertEqual(actual, expected)
                self.assertEqual([row.order for row in query.requirements], list(range(len(expected))))

    def test_comparison_dependency_summaries_are_not_a_path_between_the_two_inputs(self):
        query, actual = rows("Compare login and entry and explain their dependencies")
        self.assertEqual(actual, [("CODE_COMPARISON", ["login", "entry"]), ("FUNCTION_EXPLANATION", ["login", "entry"]),
                                  ("DEPENDENCY_PATH", ["login"]), ("DEPENDENCY_PATH", ["entry"])])
        self.assertTrue(all(not row.depends_on for row in query.requirements))

    def test_quoted_qualified_and_individually_scoped_comparison_operands(self):
        query, actual = rows("Compare `AuthService.check` with `snake_case`")
        self.assertEqual(actual, [("CODE_COMPARISON", ["AuthService.check", "snake_case"])])
        for question in ("Compare `sharedName` in auth.ts and `sharedName` in other.ts", "Compare `auth.ts::sharedName` and `other.ts::sharedName`"):
            query, actual = rows(question)
            self.assertEqual(actual, [("CODE_COMPARISON", ["sharedName", "sharedName"])])
            self.assertEqual([query.entities[i].file_path for i in query.requirements[0].entity_indices], ["auth.ts", "other.ts"])
        query, actual = rows("Compare src/and/token.ts::validateToken and AuthService.and.check")
        self.assertEqual(actual, [("CODE_COMPARISON", ["validateToken", "AuthService.and.check"])])

    def test_descriptive_discovery_does_not_manufacture_exact_symbols_or_file_scopes(self):
        for question in ("Find the code that handles login", "Where is authentication implemented?",
                         "Find the function responsible for email validation", "Show the code that processes uploaded files",
                         "Which part of the repository handles payment retries?", "Find code that verifies source file hashes before opening citations"):
            with self.subTest(question=question):
                query, actual = rows(question)
                self.assertEqual(actual, [("FEATURE_DISCOVERY", [question])])
                self.assertEqual([(e.kind, e.file_path) for e in query.entities], [("feature", None)])

    def test_conjunctions_and_quoted_prose_inside_descriptions_are_not_instructions(self):
        for text in ('Find code that handles login and payment retries', 'Find code that handles login, uploads, and retries',
                     'Find code that matches "compare login and entry" and retries', 'Find "refund  eligibility and retries"'):
            query, actual = rows(text)
            self.assertEqual(actual, [("FEATURE_DISCOVERY", [text])])

    def test_multiple_descriptions_keep_separate_text_and_repeated_intents(self):
        query, actual = rows("Find code that checks access and find code that sends mail")
        self.assertEqual(actual, [("FEATURE_DISCOVERY", ["Find code that checks access"]), ("FEATURE_DISCOVERY", ["find code that sends mail"])])
        self.assertEqual(query.intents, ["FEATURE_DISCOVERY"])

    def test_exact_operations_and_true_missing_symbols_do_not_become_discovery(self):
        for text, intent, name in (("Where is validateToken defined?", "SYMBOL_LOCATION", "validateToken"),
                                   ("Find function login", "SYMBOL_LOCATION", "login"),
                                   ("Find login function", "SYMBOL_LOCATION", "login"),
                                   ("Who calls AuthService.check?", "DIRECT_CALLERS", "AuthService.check"),
                                   ("Find callers of src/auth/token.ts::validateToken", "DIRECT_CALLERS", "validateToken"),
                                   ("Where is missingFunction defined?", "SYMBOL_LOCATION", "missingFunction"),
                                   ("Find callers of function code", "DIRECT_CALLERS", "code")):
            query, actual = rows(text)
            self.assertEqual(actual, [(intent, [name])])
            self.assertFalse(any(e.kind == "feature" for e in query.entities))

    def test_singular_comparison_reference_remains_ambiguous_but_plural_uses_inputs(self):
        query, _ = rows("Compare login and entry and show its callers")
        self.assertEqual(query.requirements[1].interpretation, "ambiguous_local_reference")
        query, _ = rows("Compare login and entry and show their callers")
        self.assertEqual(query.requirements[1].interpretation, "comparison_input_reference")
        self.assertEqual(query.requirements[1].depends_on, [])

    def test_unsupported_compound_request_is_preserved_without_replacing_targets(self):
        query, actual = rows("Compare login and entry and deploy server")
        self.assertEqual(actual, [("CODE_COMPARISON", ["login", "entry"]), ("UNRESOLVED", [])])
        self.assertEqual(query.requirements[1].interpretation, "unrecognized_requirement")

    def test_recognition_caps_have_explicit_warnings_and_valid_roundtrips(self):
        query, _ = rows(" and ".join(f"Compare function{i}A and function{i}B" for i in range(9)))
        self.assertEqual(query.status, "partial")
        self.assertTrue(query.warnings)
        self.assertLessEqual(len(query.entities), 12)
        self.assertLessEqual(len(query.requirements), 12)
        self.assertEqual(NormalizedQuery.model_validate_json(query.model_dump_json()), query)
