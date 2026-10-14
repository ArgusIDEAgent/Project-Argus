"""Separate mocked evaluation and opt-in real local Ollama smoke; no user indexing."""

import argparse
import json
from pathlib import Path
from statistics import median
import sys

from query_system.benchmark_execution import latency
from query_system.generation_contracts import GenerationBudget, ModelAnswer
from query_system.graph_rag import GraphRagGenerator
from query_system.local_ollama import LOCAL_CHAT_URL, LocalModelFailure, LocalOllama


def fixture_owner():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ai-engine"))
    sys.path.insert(0, str(root / "ai-engine" / "tests"))
    import test_phase8_generation as helpers
    owner = helpers.GenerationTests()
    owner.setUp()
    return owner, helpers


def metrics(result):
    claims = [claim for section in result.sections for claim in section.claims]
    return {"model_status": result.model_status, "status": result.status, "failure_category": result.failure_category,
        "fallback": result.fallback_status, "preparation_ms": round(result.preparation_ms, 3), "model_ms": round(result.model_ms, 3),
        "total_ms": round(result.total_ms, 3), "input_bytes": result.context.input_bytes if result.context else 0,
        "source_bytes": result.context.source_bytes if result.context else 0, "model_calls": result.model_calls,
        "context_truncated": bool(result.context and result.context.omissions),
        "coverage": [item.state for item in result.coverage], "claim_count": len(claims), "citation_count": len(result.citations),
        "unverified_interpretations": sum(claim.verification == "citation_linked_not_fact_checked" for claim in claims),
        "claims": [{"classification": claim.classification, "authoritative": claim.authoritative, "text": claim.text,
                    "citations": claim.citation_ids} for claim in claims]}


def mocked(repeats=3):
    if repeats < 1:
        raise ValueError("Positive repetitions required")
    owner, helpers = fixture_owner()
    try:
        import answers
        samples, schema_checks, citations, valid_citations = [], 0, 0, 0
        with owner.owner.session() as executor:
            package = owner.package(executor)
            for kind in ("function", "workflow", "behavior", "comparison"):
                request = owner.request(executor, package, kind)
                for _ in range(repeats):
                    result = GraphRagGenerator(executor, answers, client=helpers.FakeLocal()).generate(request, package)
                    type(result).model_validate_json(result.model_dump_json()); schema_checks += 1
                    for reference, citation in result.citations.items():
                        citations += 1
                        valid_citations += int(package.citations.get(reference) == citation)
                    samples.append({"task": kind, **metrics(result)})
            probes = {}
            request = owner.request(executor, package)
            def invalid_fact(context, *_):
                body = json.loads(helpers.valid_answer(context))
                body["sections"][0]["claims"][-1]["text"] = "This function always succeeds and is O(1)."
                return json.dumps(body)
            for name, callback in (("unavailable", lambda *_: (_ for _ in ()).throw(LocalModelFailure("offline", "unavailable"))),
                                   ("malformed", lambda *_: "invalid-json"), ("invented_fact", invalid_fact)):
                result = GraphRagGenerator(executor, answers, client=helpers.FakeLocal(callback)).generate(request, package)
                probes[name] = metrics(result)
            if any(item["model_status"] != "success" for item in samples) or any(item["fallback"] != "evidence_summary" for item in probes.values()):
                raise AssertionError("Mocked benchmark acceptance gate failed")
            return {"evaluation": "Mocked local model, synthetic fixture; not real model quality or full factual verification",
                "repository_id": package.repository_id, "generation_id": package.snapshot_id,
                "workloads": 4, "repeats": repeats, "runs": len(samples), "backend_result_schema_validity": schema_checks / len(samples),
                "model_schema_and_grounding_acceptance": sum(item["model_status"] == "success" for item in samples) / len(samples),
                "citation_checks": citations, "citation_validity": valid_citations / citations if citations else None,
                "coverage_preservation": all(len(item["coverage"]) == 1 for item in samples),
                "fallback_frequency": sum(item["fallback"] != "not_used" for item in samples) / len(samples),
                "latency": {key: latency([item[key] for item in samples]) for key in ("preparation_ms", "model_ms", "total_ms")},
                "input_bytes": {"median": median(item["input_bytes"] for item in samples), "max": max(item["input_bytes"] for item in samples)},
                "negative_probes": probes, "details": samples}
    finally:
        owner.doCleanups()


def real_smoke():
    owner, _ = fixture_owner()
    try:
        import answers
        class CapturedLocal(LocalOllama):
            last_output = None

            def generate(self, *args):
                self.last_output = super().generate(*args)
                return self.last_output

        client = CapturedLocal(answers)
        model = answers.os.environ.get("CODEMIND_LOCAL_MODEL", "llama3:latest")
        # One bounded read-only service check; no downloads, model/provider change,
        # or request to an embedding-only/cloud model. Use project configuration.
        try:
            request = answers.Request("http://127.0.0.1:11434/api/tags")
            with client.opener.open(request, timeout=2) as reply:
                available = json.loads(reply.read(65536))["models"]
        except Exception as error:
            return {"evaluation": "Real local Ollama smoke", "state": "skipped", "reason": "local_service_unavailable", "error_type": type(error).__name__, "model": model}
        installed = next((item for item in available if item.get("name") == model), None)
        if not installed or "cloud" in model.lower() or installed.get("size", 0) <= 0 or "completion" not in installed.get("capabilities", ["completion"]):
            return {"evaluation": "Real local Ollama smoke", "state": "skipped", "reason": "configured_local_completion_model_not_installed", "model": model}
        with owner.owner.session() as executor:
            package = owner.package(executor)
            request = owner.request(executor, package, budget=GenerationBudget(deadline_ms=60000, num_predict=800))
            generator = GraphRagGenerator(executor, answers, client=client)
            result = generator.generate(request, package)
            model_schema_validity = None
            model_output = None
            if client.last_output is not None:
                try:
                    ModelAnswer.model_validate_json(client.last_output)
                    model_schema_validity = True
                except ValueError:
                    model_schema_validity = False
                # Never persist raw unredacted model content in benchmark artifacts.
                safe = generator.safety.text(client.last_output)
                try:
                    model_output = json.loads(safe)
                except ValueError:
                    model_output = safe
            return {"evaluation": "One real local Ollama generation; not a general quality guarantee",
                "state": "completed", "endpoint": LOCAL_CHAT_URL, "model": model, "digest": installed.get("digest"),
                "repository_id": package.repository_id, "generation_id": package.snapshot_id,
                "backend_result_schema_validity": True, "model_schema_validity": model_schema_validity,
                "grounding_validation_accepted": result.model_status == "success", "model_output_redacted": model_output,
                "citation_validity": all(package.citations.get(key) == value for key, value in result.citations.items()),
                "raw_prose_factuality": "Requires manual review; citation membership is not full factual verification", **metrics(result)}
    finally:
        owner.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", required=True)
    parser.add_argument("--real-local", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(real_smoke() if args.real_local else mocked(args.repeats), indent=2))


if __name__ == "__main__":
    main()
