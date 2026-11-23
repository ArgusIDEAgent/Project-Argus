"""Real snapshots: ranking and scan reuse preserve every release-time guard."""
import json
import os
from unittest.mock import patch
import unittest

import knowledge_base.synchronization as synchronization
import query_system.orchestration as orchestration
import test_phase10_integration as phase10
from test_phase8_generation import FakeLocal, valid_answer


class AnswerEfficiencyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.owner = phase10.HybridIntegrationTests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)

    def test_deterministic_request_observes_repository_at_entry_and_release(self):
        with patch.object(synchronization, "observe_sources", wraps=synchronization.observe_sources) as observations:
            run = self.owner.run_question("Where is login defined?")
        self.assertEqual(run.response.status, "success")
        self.assertEqual(observations.call_count, 2)

    def test_generation_has_an_unconditional_pre_model_checkpoint(self):
        with patch.object(synchronization, "observe_sources", wraps=synchronization.observe_sources) as observations:
            run = self.owner.run_question("Explain login")
        self.assertEqual(run.response.status, "success")
        self.assertEqual(observations.call_count, 3)

    def test_unrelated_same_size_same_mtime_edit_cannot_escape_final_validation(self):
        path = self.owner.owner.fixture.root / "service.ts"
        original = path.read_bytes()
        stamp = path.stat()
        def changed(context, *_):
            path.write_bytes(original.replace(b"return value;", b"return VALUE;"))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            return valid_answer(context)
        fake = FakeLocal(changed)
        run = self.owner.run_question("Explain login", fake)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(path.stat().st_size, stamp.st_size)
        self.assertEqual(path.stat().st_mtime_ns, stamp.st_mtime_ns)
        self.assertEqual(run.response.status, "stale_index")
        self.assertFalse(run.response.presentation.items)
        self.assertFalse(self.owner.owner.store.matches(run.response.snapshot_id))

    def test_late_inventory_changes_reject_even_a_completed_deterministic_answer(self):
        for change in ("add", "delete", "ignore"):
            with self.subTest(change=change):
                # Restore each mutation before the next independent query.
                root = self.owner.owner.fixture.root
                file = root / ("unindexed.txt" if change == "add" else ".gitignore" if change == "ignore" else "service.ts")
                before = file.read_bytes() if file.exists() else None
                compose = orchestration.compose_presentation
                def mutate(*args):
                    view = compose(*args)
                    if change == "delete": file.unlink()
                    else: file.write_text("new content\n")
                    return view
                try:
                    with patch.object(orchestration, "compose_presentation", side_effect=mutate):
                        run = self.owner.run_question("Where is login defined?")
                    self.assertEqual(run.response.status, "stale_index")
                    self.assertFalse(run.response.presentation.items)
                finally:
                    if before is None: file.unlink(missing_ok=True)
                    else: file.write_bytes(before)

    def test_relevant_target_edit_is_still_rejected_inside_the_generation_stage(self):
        def changed(context, *_):
            path = self.owner.owner.fixture.root / "login.ts"
            path.write_text(path.read_text() + "// changed\n")
            return valid_answer(context)
        run = self.owner.run_question("Explain login", FakeLocal(changed))
        self.assertEqual(run.response.status, "stale_index")
        self.assertFalse(run.response.presentation.items)

    def test_benchmark_echoes_do_not_displace_implementation_and_remain_searchable(self):
        root = self.owner.owner.fixture.root
        benchmark = root / "benchmarks" / "results.json"
        benchmark.parent.mkdir()
        question = "Find code that verifies source file hashes before opening citations"
        benchmark.write_text(json.dumps([{"question": question}] * 100))
        (root / "verify.ts").write_text("// verifies source file hashes before opening citations\nexport function verifyHash(hash: string) { return hash.length > 0; }\n")
        self.owner.owner.fixture.publish()
        run = self.owner.run_question(question)
        paths = [source.citation.source.file_path for source in run.response.presentation.sources.values()]
        self.assertIn("verify.ts", paths)
        self.assertNotIn("benchmarks/results.json", paths)
        self.assertTrue(benchmark.exists())
        requested = self.owner.run_question("Find benchmark results for source file hashes")
        requested_paths = [source.citation.source.file_path for source in requested.response.presentation.sources.values()]
        self.assertIn("benchmarks/results.json", requested_paths)

    def test_descriptive_architecture_question_runs_discovery_without_fabricated_symbols(self):
        run = self.owner.run_question("How does a question move from the VS Code chat to the final answer?")
        self.assertEqual(run.original.primary_intent, "FEATURE_DISCOVERY")
        self.assertEqual([operation.operation for operation in run.response.operations], ["hybrid_search"])
        self.assertTrue(all(entity.kind == "feature" for entity in run.original.entities))
