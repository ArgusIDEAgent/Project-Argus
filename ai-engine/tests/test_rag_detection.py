"""Legacy structural recognition regressions; no model or storage access."""
import unittest
from unittest.mock import patch

import networkx as nx

from rag_pipeline import _detect_structural_query


class TestImpactDetection(unittest.TestCase):
    def test_what_is_affected(self):
        self.assertEqual(
            _detect_structural_query("What is affected if we modify login?"),
            (True, "login", "impact"),
        )

    def test_reported_queries(self):
        for question, symbol, kind in (
            ("What breaks if I remove parse?", "parse", "impact"),
            ("Change impact of login", "login", "impact"),
            ("What happens if I change generate_token?", "generate_token", "impact"),
            ("Impact of changing validate", "validate", "impact"),
            ("Who calls validateToken?", "validateToken", "callers"),
            ("What functions does processPayment call?", "processPayment", "callees"),
            ("Show dependencies of AuthService", "AuthService", "callers"),
            ("Find callers of login", "login", "callers"),
            ("What is the impact of changing validateToken?", "validateToken", "impact"),
            ("Impact on `login`", "login", "impact"),
        ):
            graph = nx.DiGraph()
            graph.add_node(symbol, name=symbol, type="function")
            for knowledge_graph in (None, graph):
                with self.subTest(question=question, graph=knowledge_graph is not None):
                    self.assertEqual(_detect_structural_query(question, knowledge_graph), (True, symbol, kind))


class TestStructuralCompatibility(unittest.TestCase):
    def test_previously_supported_forms(self):
        for question, kind in (
            ("What functions call login?", "callers"),
            ("Which methods invoke login?", "callers"),
            ("Who uses login?", "callers"),
            ("What depends on login?", "callers"),
            ("Which relies on login?", "callers"),
            ("Callers of login", "callers"),
            ("Upstream login", "callers"),
            ("Called by login", "callees"),
            ("What does login call?", "callees"),
            ("Which functions login invoke?", "callees"),
            ("Callees of login", "callees"),
            ("Downstream login", "callees"),
            ("Impact of modifying login", "impact"),
            ("Modify login", "impact"),
            ("Change login", "impact"),
            ("What happens modify login?", "impact"),
            ("Members of login", "members"),
            ("Methods in class login", "members"),
            ("Attributes of login", "members"),
            ("How is `login` used?", "callers"),
        ):
            with self.subTest(question=question):
                self.assertEqual(_detect_structural_query(question), (True, "login", kind))

    def test_identifiers_quotes_and_case(self):
        for symbol in ("snake_case", "parse2", "_private_2", "AuthService.check2", "auth.Service.check_2", "AuthService::check2", "café2"):
            graph = nx.DiGraph()
            graph.add_node(symbol, name=symbol, type="function")
            for template, kind in (
                ("Who calls {}?", "callers"),
                ("What functions does {} call?", "callees"),
                ("Change impact of {}", "impact"),
                ("Members of {}", "members"),
            ):
                for quote in ("", "`", "'", '"'):
                    question = template.format(quote + symbol + quote)
                    for knowledge_graph in (None, graph):
                        with self.subTest(question=question, graph=knowledge_graph is not None):
                            self.assertEqual(_detect_structural_query(question, knowledge_graph), (True, symbol, kind))
        self.assertEqual(_detect_structural_query("  Please SHOW me CALLERS of validateToken!  "), (True, "validateToken", "callers"))

    def test_missing_ambiguous_malformed_and_ordinary_queries(self):
        for question in (
            "", "   ", "Who calls?", "Impact of changing", "Change impact of", "Show dependencies of",
            "What breaks if I remove?", "What is affected if we modify?", "Callers of the",
            "if we the and or", "Who calls it?", "Who calls the function?", "Who calls 123?",
            "Who calls login or parse?", "Who calls login and parse?", "Impact of login, parse",
            "Who calls `login` and `parse`?", "Impact on `login` or parse",
            "Who calls login and what does parse call?", "Who calls login? Who calls parse?",
            "What happens if I change login/parse?", "Who calls login-name?", "Who calls login..parse?",
            "Who calls AuthService.?", "Who calls ::parse?", "Who calls `login?", 'Who calls `login"?',
            "Who calls login`?", "Who calls `login parse`?", "Who calls login()?", "Who calls login\x00?",
            "Who calls\nlogin?", "Who callslogin?", "What doeslogin call?", "Who calls login???",
            "Compare callers between login and parse", "Explain the ripple consequences of login",
            "How does login work?", "What is dependency injection?", "What is the weather?",
            "How do I change the theme?", "Explain the use of `login` and `parse`",
            "Explain the word `impact`", "The comment says who calls login",
        ):
            with self.subTest(question=question):
                self.assertEqual(_detect_structural_query(question), (False, "", ""))

    def test_graph_validation_and_duplicate_names(self):
        graph = nx.DiGraph()
        graph.add_node("auth.login", name="login", type="function")
        self.assertEqual(_detect_structural_query("Who calls login?", graph), (True, "login", "callers"))
        self.assertEqual(_detect_structural_query("Who calls missing?", graph), (False, "", ""))
        graph.add_node("other.login", name="login", type="function")
        self.assertEqual(_detect_structural_query("Who calls login?", graph), (False, "", ""))
        self.assertEqual(_detect_structural_query("Who calls `auth.login`?", graph), (True, "auth.login", "callers"))
        # Even graph validation cannot choose one of two explicit operands.
        self.assertEqual(_detect_structural_query("Who calls missing or auth.login?", graph), (False, "", ""))
        self.assertEqual(_detect_structural_query("Who calls login?", nx.DiGraph()), (False, "", ""))

    def test_quoted_word_can_be_an_actual_symbol(self):
        self.assertEqual(_detect_structural_query("Who calls `change`?"), (True, "change", "callers"))

    def test_hybrid_understanding_is_independent_of_legacy_detection(self):
        from phase5_fixture import fixture
        from query_system.contracts import QueryRequest
        from query_system.query_understanding import QueryUnderstandingEngine

        owner = fixture()
        self.addCleanup(owner.doCleanups)
        with patch("rag_pipeline._detect_structural_query", side_effect=AssertionError("Legacy detector must not run")), \
                QueryUnderstandingEngine.pinned(owner.store) as engine:
            for question, intent, names in (
                ("Who calls login?", "DIRECT_CALLERS", ["login"]),
                ("What does login call?", "DIRECT_CALLEES", ["login"]),
                ("Impact of changing validateToken", "CHANGE_IMPACT", ["validateToken"]),
                ("Who calls `AuthService.check`?", "DIRECT_CALLERS", ["AuthService.check"]),
                ("Who calls login and entry?", "DIRECT_CALLERS", ["login", "entry"]),
            ):
                with self.subTest(question=question):
                    result = engine.normalize(QueryRequest(repository_id=owner.repo, question=question))
                    self.assertEqual(result.primary_intent, intent)
                    self.assertEqual([entity.name for entity in result.entities], names)
                    self.assertTrue(all(entity.resolution == "resolved" for entity in result.entities))
