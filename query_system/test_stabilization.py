"""Final qualification regressions: target lists and bounded transport reads."""
import unittest
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request

from query_system.generation_contracts import GenerationBudget, PreparedContext
from query_system.local_ollama import LOCAL_CHAT_URL, LocalModelFailure, LocalOllama
from query_system.query_understanding import parse_question


class StabilizationParsingTests(unittest.TestCase):
    def test_explicit_relationship_lists_preserve_every_target(self):
        for question, intent, names in (
            ("Who calls login and entry?", "DIRECT_CALLERS", ["login", "entry"]),
            ("Who calls verifiedSourcePath and verifiedPreviewSource?", "DIRECT_CALLERS", ["verifiedSourcePath", "verifiedPreviewSource"]),
            ("Find tests for login and entry", "TEST_DISCOVERY", ["login", "entry"]),
            ("Callees of `AuthService.check` and `snake_case`", "DIRECT_CALLEES", ["AuthService.check", "snake_case"]),
        ):
            with self.subTest(question=question):
                result = parse_question(question)
                self.assertEqual([(row.intent, [result.entities[i].name for i in row.entity_indices]) for row in result.requirements], [(intent, names)])

    def test_relationship_list_then_independent_request_and_explicit_scopes(self):
        result = parse_question("Who calls auth.ts::sharedName and other.ts::sharedName and find tests for login")
        self.assertEqual([row.intent for row in result.requirements], ["DIRECT_CALLERS", "TEST_DISCOVERY"])
        self.assertEqual([result.entities[i].file_path for i in result.requirements[0].entity_indices], ["auth.ts", "other.ts"])
        self.assertEqual([result.entities[i].name for i in result.requirements[1].entity_indices], ["login"])


class StabilizationTransportTests(unittest.TestCase):
    def test_trickling_response_checks_deadline_between_available_packets(self):
        clock = [0.]
        payload = json.dumps({"done": True, "message": {"content": "{}"}}).encode()
        class Drip:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return LOCAL_CHAT_URL
            def read(self, _limit):
                # read(n) can wait for an entire slow body despite per-socket activity.
                clock[0] += 100
                return payload
            def read1(self, _limit):
                clock[0] += 1
                return b"x"
        answers = SimpleNamespace(os=os, Request=Request)
        context = PreparedContext(context_id="c", package_id="p", sources=[], facts=[], omissions=[], gaps={}, core_ready={},
            messages=[], input_bytes=0, source_bytes=0)
        client = LocalOllama(answers)
        with patch("query_system.local_ollama.monotonic", side_effect=lambda: clock[0]), patch.object(client.opener, "open", return_value=Drip()):
            with self.assertRaises(LocalModelFailure) as raised:
                client.generate(context, GenerationBudget(), 5.)
        self.assertEqual(raised.exception.status, "timeout")
        self.assertLessEqual(clock[0], 6.)

