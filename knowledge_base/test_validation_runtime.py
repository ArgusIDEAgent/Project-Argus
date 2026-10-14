"""Operational validation-tool behavior using disposable fixtures and fakes."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_base import validation


class ValidationRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cm-validation-tests-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        platform = patch.object(validation.platform, "platform", return_value="test-platform")
        platform.start(); self.addCleanup(platform.stop)

    def main(self, *arguments):
        with patch.object(sys, "argv", ["validation", *arguments]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result: validation.main()
        return result.exception.code

    def test_invalid_cli_arguments_and_output_paths(self):
        for arguments in (
            (), ("--sizes", "-1"), ("--sizes", "0,0"), ("--sizes", "1001"),
            ("--sizes", "1,,2"), ("--sizes", "oops"), ("--worker", "python"),
            ("--output", str(Path(__file__).parent)),
        ):
            with self.subTest(arguments=arguments): self.assertEqual(self.main(*arguments), 2)
        file = self.base / "existing.txt"; file.write_text("preserve")
        self.assertEqual(self.main("--output", str(file)), 2)
        self.assertEqual(file.read_text(), "preserve")

    def test_existing_reports_are_never_overwritten(self):
        output = self.base / "reports"; output.mkdir()
        report = output / "quality-report.json"; report.write_text("preserve")
        self.assertEqual(self.main("--output", str(output)), 2)
        self.assertEqual(report.read_text(), "preserve")

    def test_programmatic_profile_arguments_are_validated_before_work(self):
        for language, size in (("python", 0), ("javascript", -1), ("typescript", 1001), ("javascript", True), ("javascript", "1")):
            with self.subTest(language=language, size=size), self.assertRaises(ValueError):
                validation.run_case(language, size, True)

    def test_structural_run_is_partial_and_cleans_up_temporary_directory(self):
        roots = []
        native = validation.prepare_fixture
        def capture(root, *args): roots.append(root); return native(root, *args)
        with patch.object(validation, "prepare_fixture", side_effect=capture):
            result = validation.run_case("javascript", structural_only=True)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["retrieval"])
        self.assertIsNone(result["citations"])
        self.assertTrue(roots and all(not root.exists() for root in roots))

    def test_failed_quality_gate_does_not_become_success(self):
        with patch.object(validation, "evaluate_graph", return_value={"passed": False}):
            result = validation.run_case("javascript", structural_only=True)
        self.assertEqual(result["status"], "failed")

    def test_missing_fixture_and_underlying_index_error_clean_up(self):
        roots = []
        native = validation.prepare_fixture
        def capture(root, *args): roots.append(root); return native(root, *args)
        with patch.object(validation, "FIXTURES", self.base / "missing"):
            with self.assertRaises(FileNotFoundError): validation.run_case("javascript", structural_only=True)
        with patch.object(validation, "prepare_fixture", side_effect=capture), \
                patch.object(validation, "build_graph", side_effect=RuntimeError("index unavailable")):
            with self.assertRaisesRegex(RuntimeError, "index unavailable"):
                validation.run_case("javascript", structural_only=True)
        self.assertTrue(roots and all(not root.exists() for root in roots))

    def test_full_run_with_fake_local_vectors_and_retrieval_failure_cleans_registration(self):
        import api, config
        roots = []
        def fixture(root, *_):
            root.mkdir(); (root / "auth.ts").write_text('export function login() { return true; }\n'); roots.append(root)
        previous = dict(api._REGISTERED_REPOS)
        with patch.object(validation, "prepare_fixture", side_effect=fixture), \
                patch.object(config, "embed_texts", side_effect=lambda texts: [[1.] + [0.] * 383 for _ in texts]), \
                patch("indexer.embed_texts", side_effect=lambda texts: [[1.] + [0.] * 383 for _ in texts]), \
                patch("indexer.get_embedding_model"), \
                patch.object(validation, "evaluate_graph", return_value={"passed": True}), \
                patch.object(validation, "fixture_expectations", return_value={"questions": [["login", "auth.ts", "login", 1, 1]]}), \
                patch.object(api, "search_code", side_effect=RuntimeError("retrieval unavailable")):
            with self.assertRaisesRegex(RuntimeError, "retrieval unavailable"): validation.run_case("typescript")
        self.assertEqual(api._REGISTERED_REPOS, previous)
        self.assertTrue(roots and all(not root.exists() for root in roots))

    def test_worker_failure_writes_failed_report_and_exit_code(self):
        output = self.base / "worker"
        with patch.object(validation, "run_case", side_effect=RuntimeError("injected index failure")):
            self.assertEqual(self.main("--output", str(output), "--sizes", "0", "--worker", "javascript"), 1)
        self.assertEqual(json.loads((output / "case.json").read_text())["status"], "failed")

    def test_worker_structural_success_remains_partial(self):
        output = self.base / "worker"
        self.assertEqual(self.main("--output", str(output), "--sizes", "0", "--worker", "javascript", "--structural-only"), 0)
        self.assertEqual(json.loads((output / "case.json").read_text())["status"], "partial")

    def worker(self, body, code=0):
        def run(command, **_):
            folder = Path(command[command.index("--output") + 1]); folder.mkdir()
            language = command[command.index("--worker") + 1]
            report = {"language": language, "extra_files": 0, **body} if isinstance(body, dict) else body
            (folder / "case.json").write_text(json.dumps(report))
            return SimpleNamespace(returncode=code, stdout="", stderr="")
        return run

    def test_malformed_worker_formats_produce_failed_report_instead_of_crashing(self):
        for index, body in enumerate(([], {}, {"status": "unknown"}, {"status": "passed"}, {"status": "partial"},
                                     {"status": "passed", "quality": {"passed": True}, "performance": {}})):
            output = self.base / str(index)
            with self.subTest(body=body), patch.object(validation.subprocess, "run", side_effect=self.worker(body)), \
                    patch.object(validation, "dependency_versions", return_value={}), patch.object(validation, "implementation_fingerprints", return_value={}):
                self.assertEqual(self.main("--output", str(output), "--sizes", "0", "--skip-regressions"), 1)
                self.assertEqual(json.loads((output / "quality-report.json").read_text())["status"], "failed")

    def test_worker_exit_status_and_regression_failures_propagate(self):
        for code in (0, 7):
            output = self.base / str(code)
            with self.subTest(code=code), patch.object(validation.subprocess, "run", side_effect=self.worker({"status": "failed", "error": "fixture failure"}, code)), \
                    patch.object(validation, "regression_tests", return_value={"query_system": {"passed": False, "tests": 1, "exit_code": 1, "log": "test.log"}}):
                self.assertEqual(self.main("--output", str(output), "--sizes", "0"), 1)
                self.assertEqual(json.loads((output / "quality-report.json").read_text())["status"], "failed")

    def test_regression_runner_includes_all_suites_and_reports_process_failures(self):
        for index, response in enumerate((SimpleNamespace(returncode=1, stdout="", stderr="Ran 2 tests\nFAILED"),
                                           SimpleNamespace(returncode=0, stdout="", stderr="no tests reported"),
                                           subprocess.TimeoutExpired("test", 300), OSError("cannot start"))):
            output = self.base / str(index); output.mkdir()
            option = {"side_effect": response} if isinstance(response, BaseException) else {"return_value": response}
            with self.subTest(index=index), patch.object(validation.subprocess, "run", **option):
                result = validation.regression_tests(output)
                self.assertEqual(set(result), {"knowledge_base", "engine", "query_system"})
                self.assertTrue(all(not item["passed"] for item in result.values()))
                self.assertTrue(all((output / item["log"]).is_file() for item in result.values()))

    def test_successful_regression_runner_records_real_count(self):
        with patch.object(validation.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="Ran 3 tests in 0.01s\nOK")):
            result = validation.regression_tests(self.base)
        self.assertTrue(all(item["passed"] and item["tests"] == 3 for item in result.values()))

    def test_missing_worker_report_and_process_timeout_fail_the_parent(self):
        for index, error in enumerate((FileNotFoundError("worker unavailable"), subprocess.TimeoutExpired("worker", 300))):
            output = self.base / str(index)
            with self.subTest(index=index), patch.object(validation.subprocess, "run", side_effect=error):
                self.assertEqual(self.main("--output", str(output), "--sizes", "0", "--skip-regressions"), 1)
            self.assertEqual(json.loads((output / "quality-report.json").read_text())["status"], "failed")
