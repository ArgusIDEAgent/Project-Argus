"""Generation schemas, privacy and loopback transport without a model service."""

import json
import os
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from pydantic import ValidationError

from query_system.generation_contracts import GenerationBudget, ModelAnswer, PreparedContext, PreparedFact, PreparedSource
from query_system.generation_safety import PresentationSafety
from query_system.local_ollama import LOCAL_CHAT_URL, LocalModelFailure, LocalOllama, _NoRedirect


class GenerationUnitTests(unittest.TestCase):
    def setUp(self):
        self.answers = SimpleNamespace(os=os, Request=Request, sanitize_text=lambda text: text.replace("\x1b", ""),
            _redact_secrets=lambda text: text.replace("BASESECRET", "[REDACTED]"), _explanation_only=lambda text: text.strip())
        self.context = PreparedContext(context_id="c", package_id="p", sources=[], facts=[], omissions=[], gaps={}, core_ready={},
            messages=[{"role": "system", "content": "trusted"}, {"role": "user", "content": "{}"}], input_bytes=9, source_bytes=0)
        self.budget = GenerationBudget()

    def test_nested_metadata_and_sensitive_parameter_defaults_are_masked(self):
        safety = PresentationSafety(self.answers)
        raw = {"token": "raw-value", "parameters": [{"name": "password", "type": "string", "default": "plain-password"}],
               "comments": "BASESECRET", "numeric": 2, "text": 'api_key="plain-api-key"'}
        value = safety.tree(raw)
        self.assertEqual(value["token"], "[REDACTED]")
        self.assertEqual(value["parameters"][0]["default"], "[REDACTED]")
        self.assertEqual(value["parameters"][0]["type"], "string")
        self.assertNotIn("plain-api-key", value["text"])
        self.assertEqual(value["comments"], "[REDACTED]")
        self.assertEqual(raw["token"], "raw-value")

    def test_env_bearer_and_jwt_presentation_redaction(self):
        safety = PresentationSafety(self.answers)
        for text, secret in (("TOKEN=really-secret-value", "really-secret-value"), ("Bearer abcdefghijklmnop", "abcdefghijklmnop"),
                             ("eyJabcdefghijk.abcdefghijk.abcdefghijkl", "eyJabcdefghijk.abcdefghijk.abcdefghijkl")):
            self.assertNotIn(secret, safety.text(text))

    def test_strict_generation_limits_and_extra_output_fields(self):
        for values in ({"max_records": 0}, {"num_ctx": "8192"}, {"num_predict": 4096}, {"deadline_ms": 0}, {"max_source_bytes": -1}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                GenerationBudget(**values)
        with self.assertRaises(ValidationError):
            ModelAnswer.model_validate({"sections": [], "coverage": [], "citations": ["invented"]})

    def reply(self, **changes):
        payload = {"done": True, "message": {"content": "{}"}, "eval_count": 1, "prompt_eval_count": 1, **changes}
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.geturl.return_value = LOCAL_CHAT_URL
        reply.read.return_value = json.dumps(payload).encode()
        return reply

    def test_local_request_reuses_configuration_and_has_no_tools(self):
        client = LocalOllama(self.answers)
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "configured-local-model"}), patch.object(client.opener, "open", return_value=self.reply()) as opened:
            self.assertEqual(client.generate(self.context, self.budget, monotonic()+5), "{}")
        request = opened.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, LOCAL_CHAT_URL)
        self.assertEqual(body["model"], "configured-local-model")
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)
        self.assertEqual(body["options"]["num_predict"], 350)
        self.assertEqual(body["options"]["num_ctx"], 8192)
        self.assertEqual(body["format"], ModelAnswer.model_json_schema())
        self.assertEqual(opened.call_count, 1)

    def test_disabled_remote_named_models_and_expired_deadlines_do_not_open(self):
        for model, category in (("", "model_disabled"), ("gpt-oss:120b-cloud", "unapproved_model"), ("https://remote/model", "unapproved_model")):
            client = LocalOllama(self.answers)
            with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": model}), patch.object(client.opener, "open") as opened:
                with self.assertRaises(LocalModelFailure) as raised:
                    client.generate(self.context, self.budget, monotonic()+5)
                self.assertEqual(raised.exception.category, category)
                opened.assert_not_called()
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "local"}), patch.object(client.opener, "open") as opened:
            with self.assertRaises(LocalModelFailure):
                client.generate(self.context, self.budget, monotonic()-1)
            opened.assert_not_called()

    def test_structured_format_constrains_facts_separately_from_prose(self):
        self.context.sources = [PreparedSource(record_id="r", data={}, excerpt="source")]
        self.context.facts = [PreparedFact(fact_id="f", finding_id=None, requirement_id="q", record_ids=["r"],
            classification="verified_source_observation", statement="Canonical fact.", data={}, limitations=[])]
        self.context.core_ready = {0: True}
        client = LocalOllama(self.answers)
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "local"}), patch.object(client.opener, "open", return_value=self.reply()) as opened:
            client.generate(self.context, self.budget, monotonic()+5)
        variants = json.loads(opened.call_args.args[0].data)["format"]["$defs"]["ModelClaim"]["anyOf"]
        self.assertEqual(variants[0]["properties"]["fact_id"]["enum"], ["f"])
        self.assertEqual(variants[1]["properties"]["fact_id"], {"type": "null"})
        self.assertEqual(variants[1]["properties"]["record_ids"]["items"]["enum"], ["r"])

    def test_redirects_and_proxy_configuration_cannot_select_remote_transport(self):
        with self.assertRaises(LocalModelFailure):
            _NoRedirect().redirect_request(Request(LOCAL_CHAT_URL), None, 302, "redirect", {}, "https://remote.invalid/")
        with patch.dict(os.environ, {"HTTP_PROXY": "http://remote.invalid:8888", "HTTPS_PROXY": "http://remote.invalid:8888"}):
            client = LocalOllama(self.answers)
        self.assertTrue(all(not getattr(handler, "proxies", {}) for handler in client.opener.handlers))

    def test_timeout_http_errors_and_malformed_response_categories(self):
        client = LocalOllama(self.answers)
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "local"}):
            for error, expected in ((TimeoutError("timeout"), "timeout"), (OSError("offline"), "unavailable"),
                                    (HTTPError(LOCAL_CHAT_URL, 503, "offline", {}, None), "unavailable")):
                with patch.object(client.opener, "open", side_effect=error), self.assertRaises(LocalModelFailure) as raised:
                    client.generate(self.context, self.budget, monotonic()+5)
                self.assertEqual(raised.exception.status, expected)
            for response in (self.reply(done_reason="length"), self.reply(message={"content": "{}", "tool_calls": [{"name": "exec"}]}), self.reply(done=False), self.reply(eval_count=9999)):
                with patch.object(client.opener, "open", return_value=response), self.assertRaises(LocalModelFailure):
                    client.generate(self.context, self.budget, monotonic()+5)

    def test_response_byte_limit_is_enforced(self):
        client = LocalOllama(self.answers)
        reply = self.reply()
        reply.read.return_value = b"x" * (self.budget.max_response_bytes+1)
        with patch.dict(os.environ, {"CODEMIND_LOCAL_MODEL": "local"}), patch.object(client.opener, "open", return_value=reply), self.assertRaises(LocalModelFailure) as raised:
            client.generate(self.context, self.budget, monotonic()+5)
        self.assertEqual(raised.exception.category, "response_budget")
        reply.read.assert_called_once_with(self.budget.max_response_bytes+1)


if __name__ == "__main__":
    unittest.main()
