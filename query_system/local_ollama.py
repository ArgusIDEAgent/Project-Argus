"""Narrow bridge to answers.py's configured local Ollama facilities, never /ask."""

import json
import re
from copy import deepcopy
from http.client import HTTPException
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from query_system.generation_contracts import ModelAnswer

LOCAL_CHAT_URL = "http://127.0.0.1:11434/api/chat"


class LocalModelFailure(ValueError):
    def __init__(self, category, status):
        self.category, self.status = category, status
        super().__init__(category)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalModelFailure("redirect_refused", "error")


class LocalOllama:
    """Inject the already loaded answers module; no endpoint/provider argument."""
    def __init__(self, answers):
        self.answers = answers
        # Same stdlib HTTP transport as answers.urlopen, with the mandatory new
        # redirect/proxy restrictions. No global opener or production mutation.
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def generate(self, context, budget, deadline):
        model = self.answers.os.environ.get("CODEMIND_LOCAL_MODEL", "llama3:latest")
        if not model:
            raise LocalModelFailure("model_disabled", "disabled")
        # A local service must not be asked to use an explicitly cloud-tagged model.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", model) or "cloud" in model.lower() or ":" in model and "//" in model:
            raise LocalModelFailure("unapproved_model", "blocked")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise LocalModelFailure("deadline", "timeout")
        schema = ModelAnswer.model_json_schema()
        if context.core_ready:
            # The backend requires exactly one coverage row per original order.
            # The unconstrained array let the real model repeat order 0 even for
            # a one-requirement task. Guide cardinality without trusting it:
            # distinct-order validation remains mandatory after generation.
            count = len(context.core_ready)
            schema["properties"]["coverage"].update(minItems=count, maxItems=count, uniqueItems=True)
        if context.sources and context.facts:
            claim = schema["$defs"]["ModelClaim"]["properties"]
            claim["record_ids"]["items"]["enum"] = [source.record_id for source in context.sources]
            claim["fact_id"]["anyOf"][0]["enum"] = [fact.fact_id for fact in context.facts]
            claim["requirement_orders"]["items"]["enum"] = list(context.core_ready)
            schema["$defs"]["ModelCoverage"]["properties"]["order"]["enum"] = list(context.core_ready)
            # Schema guidance complements, never replaces, backend validation.
            base = schema["$defs"]["ModelClaim"]
            verified = [fact for fact in context.facts if fact.classification not in ("hypothetical_consequence", "unresolved_assertion")]
            variants = []
            for modes in (["fact"], ["interpretation", "hypothesis"], ["unknown"]):
                if modes == ["fact"] and not verified:
                    continue
                variant = deepcopy(base)
                properties = variant["properties"]
                properties["mode"] = {"type": "string", "enum": modes}
                properties["fact_id"] = {"type": "string", "enum": [fact.fact_id for fact in verified]} if modes == ["fact"] else {"type": "null"}
                properties["text"] = {"anyOf": [{"type": "null"}, {"type": "string", "enum": [fact.statement for fact in verified]}]} if modes == ["fact"] else {"type": "string", "minLength": 1, "maxLength": 1600}
                properties["record_ids"]["minItems"] = 0 if modes == ["unknown"] else 1
                variant["required"] = ["requirement_orders", "mode", "fact_id", "record_ids", "text"]
                variants.append(variant)
            schema["$defs"]["ModelClaim"] = {"anyOf": variants}
        payload = {"model": model, "stream": False, "format": schema,
                   "messages": context.messages,
                   "options": {"temperature": 0, "num_predict": budget.num_predict, "num_ctx": budget.num_ctx}}
        request = self.answers.Request(LOCAL_CHAT_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=remaining) as reply:
                if reply.geturl() != LOCAL_CHAT_URL:
                    raise LocalModelFailure("unexpected_response_url", "error")
                parts, size = [], 0
                while True:
                    if monotonic() >= deadline:
                        raise LocalModelFailure("deadline", "timeout")
                    # read(n) waits for the entire body even when a peer keeps a
                    # socket alive with small packets. read1 returns available
                    # data so cooperative deadline/byte checks can run between reads.
                    part = reply.read1(min(65536, budget.max_response_bytes + 1 - size))
                    if not part:
                        break
                    parts.append(part); size += len(part)
                    if size > budget.max_response_bytes:
                        raise LocalModelFailure("response_budget", "invalid_output")
                raw = b"".join(parts)
            if len(raw) > budget.max_response_bytes:
                raise LocalModelFailure("response_budget", "invalid_output")
            if monotonic() > deadline:
                raise LocalModelFailure("deadline", "timeout")
            response = json.loads(raw)
            if not isinstance(response, dict) or not isinstance(response.get("message"), dict):
                raise LocalModelFailure("malformed_response", "invalid_output")
            if response.get("done_reason") == "length":
                raise LocalModelFailure("output_token_limit", "invalid_output")
            if response.get("message", {}).get("tool_calls"):
                raise LocalModelFailure("tool_call_refused", "invalid_output")
            if response.get("done") is not True or not isinstance(response.get("message", {}).get("content"), str):
                raise LocalModelFailure("malformed_response", "invalid_output")
            if response.get("prompt_eval_count", 0) > budget.num_ctx or response.get("eval_count", 0) > budget.num_predict:
                raise LocalModelFailure("reported_token_budget", "invalid_output")
            return response["message"]["content"]
        except LocalModelFailure:
            raise
        except HTTPError as error:
            raise LocalModelFailure(f"http_{error.code}", "unavailable") from error
        except TimeoutError as error:
            raise LocalModelFailure("socket_timeout", "timeout") from error
        except URLError as error:
            status = "timeout" if isinstance(error.reason, TimeoutError) else "unavailable"
            raise LocalModelFailure("socket_timeout" if status == "timeout" else "connection_unavailable", status) from error
        except OSError as error:
            raise LocalModelFailure("connection_unavailable", "unavailable") from error
        except HTTPException as error:
            raise LocalModelFailure("http_protocol_error", "invalid_output") from error
        except (ValueError, KeyError, TypeError) as error:
            raise LocalModelFailure("malformed_response", "invalid_output") from error
