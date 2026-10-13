"""Focused semantic input, score, and benchmark-metric contracts."""

import unittest

from pydantic import ValidationError

from query_system.benchmark_semantic import retrieval_metrics
from query_system.contracts import SemanticFinding
from query_system.semantic_engine import SearchParameters


class SemanticUnitTests(unittest.TestCase):
    def test_filter_validation_and_normalization(self):
        parameters = SearchParameters(query="Find auth", filters={"languages": ["TypeScript"], "symbolTypes": ["FUNCTION"]})
        self.assertEqual(parameters.filters, {"languages": ["typescript"], "symbolTypes": ["function"]})
        for values in ({"query": "   "}, {"query": "x", "filters": {"symbolIds": ["id"]}},
                       {"query": "x", "filters": {"files": ["/private/source.ts"]}},
                       {"query": "x", "limit": 26}, {"query": "x", "filters": {"files": [1]}}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                SearchParameters.model_validate(values)

    def test_distinct_ranking_fields_and_finite_scores(self):
        finding = SemanticFinding(chunk_id="canonical", rank=1, keyword_score=2.5,
                                  fusion_score=1 / 61, retrieval_methods=["keyword"], evidence_ids=["canonical"])
        self.assertIsNone(finding.retrieval_score)
        for field in ("retrieval_score", "keyword_score", "fusion_score"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                SemanticFinding(chunk_id="canonical", rank=1, evidence_ids=["canonical"], **{field: float("inf")})
        with self.assertRaises(ValidationError):
            SemanticFinding(chunk_id="canonical", rank=1, retrieval_methods=["CALLS"], evidence_ids=["canonical"])

    def test_recall_and_reciprocal_rank_measure_labeled_code(self):
        self.assertEqual(retrieval_metrics({"a", "b"}, ["x", "a", "z"]), (.5, .5, 2))
        self.assertEqual(retrieval_metrics({"a"}, []), (0., 0., None))
        self.assertEqual(retrieval_metrics({"a"}, ["x"] * 5 + ["a"]), (0., 0., None))
        self.assertEqual(retrieval_metrics({"a"}, ["a", "a"]), (1., 1., 1))
        with self.assertRaises(ValueError):
            retrieval_metrics(set(), ["x"])


if __name__ == "__main__":
    unittest.main()
