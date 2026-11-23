"""Question routing and source roles must improve relevance without hiding files."""
import unittest

from query_system.query_understanding import parse_question
from knowledge_base.source_ranking import rank_sources, source_priority


class AnswerMethodTests(unittest.TestCase):
    def test_descriptive_questions_do_not_invent_symbols_from_product_names(self):
        for question in (
            "How does a question move from the VS Code chat to the final answer?",
            "Explain how CodeMind processes a question from chat input through retrieval to the displayed response",
            "Describe how a repository is indexed and how its answers are assembled",
        ):
            with self.subTest(question=question):
                query = parse_question(question)
                self.assertEqual(query.intents, ["FEATURE_DISCOVERY"])
                self.assertTrue(all(entity.kind == "feature" for entity in query.entities))

    def test_code_shaped_names_inside_explanation_questions_are_exact_targets(self):
        from knowledge_base.retrieval import plan_query
        for question, target in (
            ("Explain how answer_question prepares repository evidence", "answer_question"),
            ("Explain how HybridRetriever.retrieve chooses evidence", "HybridRetriever.retrieve"),
            ("Explain how run_hybrid builds the final answer", "run_hybrid"),
        ):
            with self.subTest(question=question):
                plan = plan_query(question)
                self.assertEqual((plan.intent, plan.reference), ("explain", target))

    def test_explicit_targets_keep_the_existing_canonical_routes(self):
        for question, intent, target in (
            ("Explain how `login` works", "FUNCTION_EXPLANATION", "login"),
            ("Who calls verifiedSourcePath?", "DIRECT_CALLERS", "verifiedSourcePath"),
            ("Where is `VS` defined?", "SYMBOL_LOCATION", "VS"),
            ("Explain verifiedSourcePath", "FUNCTION_EXPLANATION", "verifiedSourcePath"),
        ):
            with self.subTest(question=question):
                query = parse_question(question)
                self.assertEqual(query.primary_intent, intent)
                self.assertIn(target, [entity.name for entity in query.entities])

    def test_implementation_beats_matching_benchmark_and_repeated_file_chunks(self):
        paths = ["query_system/benchmarks/results.json", "src/verify.ts", "src/verify.ts", "src/open.ts"]
        items = [{"path": path, "rank": index} for index, path in enumerate(paths)]
        ranked = rank_sources(items, "Find code that verifies source hashes", score=lambda item: -item["rank"])
        self.assertEqual([row["path"] for row in ranked], ["src/verify.ts", "src/open.ts", "src/verify.ts", paths[0]])
        self.assertEqual(len(ranked), len(items))

    def test_requested_roles_are_searchable_and_code_about_docs_is_implementation(self):
        paths = ["src/index.ts", "tests/index.test.ts", "docs/README.md", "benchmarks/results.json", "package.json"]
        for question, expected in (
            ("Find tests for repository indexing", paths[1]),
            ("Show documentation for repository indexing", paths[2]),
            ("Find benchmark results for retrieval", paths[3]),
            ("Find configuration for repository indexing", paths[4]),
            ("Find code that renders documentation markdown", paths[0]),
        ):
            with self.subTest(question=question):
                self.assertEqual(min(paths, key=lambda path: source_priority(path, question)), expected)
