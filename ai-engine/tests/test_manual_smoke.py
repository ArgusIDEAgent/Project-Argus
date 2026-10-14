"""Verify the legacy smoke gate using fakes; never contact a provider."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class ManualSmokeTests(unittest.TestCase):
    def load_utility(self):
        path = Path(__file__).resolve().parents[1] / "manual/legacy_openrouter_smoke.py"
        spec = importlib.util.spec_from_file_location("legacy_openrouter_smoke", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_import_has_no_provider_or_dotenv_side_effects(self):
        with patch.dict(sys.modules, {"dotenv": None, "litellm": None}), patch.dict(os.environ):
            before = dict(os.environ)
            self.load_utility()
            self.assertEqual(dict(os.environ), before)

    def test_missing_opt_in_refuses_even_with_credentials(self):
        utility = self.load_utility()
        with patch.dict(sys.modules, {"dotenv": None, "litellm": None}), \
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-test-credential"}), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                utility.main([])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("External model access refused", output.getvalue())
        self.assertNotIn("fake-test-credential", output.getvalue())

    def test_opt_in_without_credentials_does_not_import_provider(self):
        utility = self.load_utility()
        with patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=Mock()), "litellm": None}), \
                patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(utility.main(["--allow-external-llm"]), 1)

    def test_authorized_path_preserves_fixed_legacy_request_with_fake_provider(self):
        utility = self.load_utility()
        complete = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Fake AST answer"))]))
        with patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=Mock()), "litellm": SimpleNamespace(completion=complete)}), \
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-test-credential"}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(utility.main(["--allow-external-llm"]), 0)
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(complete.call_args.kwargs["model"], "openrouter/cohere/north-mini-code:free")
        self.assertEqual(complete.call_args.kwargs["messages"][-1]["content"], "In one sentence, what is an Abstract Syntax Tree (AST)?")
        self.assertIn("Fake AST answer", output.getvalue())
        self.assertNotIn("fake-test-credential", output.getvalue())

    def test_provider_errors_do_not_print_credentials(self):
        utility = self.load_utility()
        complete = Mock(side_effect=RuntimeError("fake-test-credential"))
        with patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=Mock()), "litellm": SimpleNamespace(completion=complete)}), \
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-test-credential"}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(utility.main(["--allow-external-llm"]), 1)
        self.assertNotIn("fake-test-credential", output.getvalue())
