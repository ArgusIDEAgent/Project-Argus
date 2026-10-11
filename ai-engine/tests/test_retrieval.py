"""Regression tests for exact code lookup and degraded local search."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api
from indexer import IndexingError
from lexical_search import search_exact_code


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "crack_detection_functions.c"
        self.source.write_text('return strcasecmp(extension, ".jpeg") == 0 ||\n')
        self.repo_id = "a" * 24
        api._REGISTERED_REPOS[self.repo_id] = {"rootPath": str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop, self.repo_id)

    def test_c_identifier_is_found_without_qdrant(self):
        with patch.object(api, "get_qdrant_client", side_effect=AssertionError("Qdrant should not be called")):
            result = api.search_code(api.SearchRequest(repoId=self.repo_id, query="strcasecmp", limit=5))
        self.assertEqual(result["mode"], "lexical")
        self.assertEqual(len(result["results"]), 1)
        match = result["results"][0]
        self.assertEqual((match["path"], match["startLine"]), (self.source.name, 1))
        self.assertEqual(match["sourceHash"], hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_copied_expression_matches_with_different_spacing(self):
        matches = search_exact_code(str(self.root), self.repo_id,
                                    'strcasecmp(extension, ".jpeg")  == 0 ||', 5)
        self.assertEqual(len(matches), 1)

    def test_missing_term_reports_semantic_unavailability(self):
        with patch.object(api, "get_qdrant_client", side_effect=OSError("connection refused")):
            result = api.search_code(api.SearchRequest(repoId=self.repo_id, query="not_in_source", limit=5))
        self.assertEqual(result["results"], [])
        self.assertIn("unavailable", result["warning"])

    def test_index_job_still_opens_repo_for_local_search(self):
        job_id = "test-job"
        api._JOBS[job_id] = {"id": job_id, "repoId": self.repo_id,
                             "state": api.JobState.QUEUED, "createdAt": 0}
        self.addCleanup(api._JOBS.pop, job_id)
        with patch.object(api, "index_directory", side_effect=IndexingError("Cannot connect to Qdrant: unavailable")):
            api._run_index_job(job_id, self.repo_id, str(self.root))
        self.assertEqual(api._JOBS[job_id]["state"], api.JobState.COMPLETED)
        self.assertEqual(api._JOBS[job_id]["result"]["semantic"]["state"], "degraded")
        self.assertEqual(api._JOBS[job_id]["result"]["fileCount"], 1)


if __name__ == "__main__":
    unittest.main()
