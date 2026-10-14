"""Real-question regressions; labels derived from inspected source, not parser output."""
import unittest
import json
import os
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

from query_system.query_understanding import parse_question
from query_system.generation_contracts import GenerationBudget, PreparedContext
from query_system.local_ollama import LocalOllama, LOCAL_CHAT_URL


def requirements(question):
    normalized = parse_question(question)
    return [(row.intent, [normalized.entities[i].name for i in row.entity_indices]) for row in normalized.requirements]


class Phase3UnderstandingTests(unittest.TestCase):
    def test_return_question_uses_existing_contract(self):
        self.assertEqual(requirements("What does verifiedSourcePath return?"),
                         [("FUNCTION_CONTRACT", ["verifiedSourcePath"])])

    def test_return_question_qualified_target(self):
        self.assertEqual(requirements("What does sourceNavigation.ts::verifiedSourcePath return?"),
                         [("FUNCTION_CONTRACT", ["verifiedSourcePath"])])

    def test_return_question_compound_preserves_both(self):
        self.assertEqual(requirements("What does verifiedSourcePath return and who calls verifiedPreviewSource?"),
            [("FUNCTION_CONTRACT", ["verifiedSourcePath"]), ("DIRECT_CALLERS", ["verifiedPreviewSource"])])

    def test_call_return_named_target_is_not_contract(self):
        self.assertEqual(requirements("Who calls returnValue?"), [("DIRECT_CALLERS", ["returnValue"])])

    def test_ml_description_not_symbol_named_cnn(self):
        question = "Which CNN architecture and hyperparameters are used?"
        self.assertEqual(requirements(question), [("FEATURE_DISCOVERY", [question])])

    def test_algorithm_description(self):
        question = "Which algorithm is used for classification?"
        self.assertEqual(requirements(question), [("FEATURE_DISCOVERY", [question])])

    def test_explicit_architecture_symbol_still_exact(self):
        self.assertEqual(requirements("Where is `architecture` defined?"), [("SYMBOL_LOCATION", ["architecture"])])

    def test_ml_description_compound_keeps_following_requirement(self):
        self.assertEqual(requirements("Which CNN architecture and hyperparameters are used and find function check_folder"),
            [("FEATURE_DISCOVERY", ["Which CNN architecture and hyperparameters are used"]), ("SYMBOL_LOCATION", ["check_folder"])])


class Phase3ModelSchemaTests(unittest.TestCase):
    def test_generation_schema_cannot_request_duplicate_single_order_coverage(self):
        for orders in ({0: True}, {0: True, 1: True}):
            with self.subTest(orders=orders):
                client = LocalOllama(SimpleNamespace(os=os, Request=Request))
                context = PreparedContext(context_id="c", package_id="p", sources=[], facts=[], omissions=[], gaps={},
                    core_ready=orders, messages=[], input_bytes=0, source_bytes=0)
                captured = []
                class Reply:
                    def __init__(self): self.done = False
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def geturl(self): return LOCAL_CHAT_URL
                    def read1(self, limit):
                        if self.done: return b""
                        self.done = True
                        return json.dumps({"done": True, "message": {"content": "{}"}}).encode()
                def opened(request, timeout):
                    captured.append(json.loads(request.data)); return Reply()
                with patch.object(client.opener, "open", side_effect=opened):
                    client.generate(context, GenerationBudget(), monotonic() + 30)
                schema = captured[0]["format"]["properties"]["coverage"]
                self.assertEqual(schema["minItems"], len(orders))
                self.assertEqual(schema["maxItems"], len(orders))
                self.assertTrue(schema["uniqueItems"])
                self.assertEqual(captured[0]["options"], {"temperature": 0, "num_predict": 350, "num_ctx": 8192})


if __name__ == "__main__": unittest.main()
