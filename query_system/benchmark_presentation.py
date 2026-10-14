"""Formatting/serialization measurements only; isolated existing fixture, no real LLM."""

import argparse
import json
from statistics import median
from time import perf_counter
from unittest.mock import patch

from query_system.benchmark_execution import latency
from query_system.benchmark_generation import fixture_owner
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.graph_rag import GraphRagGenerator
from query_system.presentation_contracts import PresentationResult
from query_system.response_presentation import format_response


def run_fixture(repeats=5):
    if repeats < 1:
        raise ValueError("Positive repetition count required")
    owner, helpers = fixture_owner()
    try:
        import asyncio
        import answers
        cases, checks, citations, details = [], 0, 0, []
        with owner.owner.session() as executor:
            for label, question in [("location", "Where is login defined?"), ("callers", "Who calls login?"),
                ("callees", "What does login call?"), ("path", "Dependency path from entry to validateToken"),
                ("dependencies", "Dependencies of login"), ("feature", "Find code that checks access"),
                ("contract", "Contract of validateToken"), ("impact", "Impact of changing login"),
                ("module", "Overview of auth.ts"), ("tests", "What tests cover login?")]:
                normalized = executor.understand(owner.owner.request(question))
                package = EvidenceAggregator.from_executor(executor).aggregate(asyncio.run(executor.execute(executor.plan(normalized))))
                cases.append((label, package, None))
            support = owner.package(executor)
            for kind in ("function", "workflow", "behavior", "comparison"):
                # Prepare protocol fixtures before timing. This is a mocked
                # producer, never an actual local/remote model invocation.
                result = GraphRagGenerator(executor, answers, client=helpers.FakeLocal()).generate(owner.request(executor, support, kind), support)
                cases.append((kind, support, result))
            snapshot = executor.context.snapshot_id
            with patch.object(executor, "execute", side_effect=AssertionError("retrieval during formatting")), patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("LLM during formatting")), patch("sqlite3.connect", side_effect=AssertionError("DB during formatting")):
                for label, package, result in cases:
                    for depth in ("quick", "standard", "deep"):
                        times, serialization, sizes = [], [], []
                        reference = None
                        for _ in range(repeats):
                            start = perf_counter(); view = format_response(package, result, depth=depth)
                            times.append((perf_counter() - start) * 1000)
                            start = perf_counter(); payload = view.model_dump_json()
                            serialization.append((perf_counter() - start) * 1000)
                            sizes.append(len(payload.encode()))
                            PresentationResult.model_validate_json(payload); checks += 1
                            assert len(view.requirements) == len(result.coverage if result else package.requirements)
                            assert view.snapshot_id == snapshot
                            assert all(package.citations.get(key) == value.citation for key, value in view.sources.items())
                            assert reference is None or reference == payload
                            reference = payload; citations += len(view.sources)
                        details.append({"workload": label, "depth": depth, "runs": repeats, "status": view.status,
                            "formatting": latency(times), "serialization": latency(serialization),
                            "response_bytes": {"median": median(sizes), "max": max(sizes)},
                            "requirement_count": len(view.requirements), "item_count": len(view.items), "source_count": len(view.sources)})
        return {"evaluation": "Synthetic existing-producer fixtures; pure formatting, no model/retrieval/DB calls during measurements",
            "generation_id": snapshot, "workloads": len(cases), "depths": 3, "repeats": repeats, "runs": checks,
            "schema_validity": 1.0, "citation_checks": citations, "citation_map_validity": 1.0,
            "coverage_preservation": True, "deterministic_repeats": True, "actual_model_calls": 0,
            "retrieval_calls_during_formatting": 0, "response_bytes_max": max(item["response_bytes"]["max"] for item in details),
            "formatting_median_of_case_medians_ms": round(median(item["formatting"]["median_ms"] for item in details), 3),
            "serialization_median_of_case_medians_ms": round(median(item["serialization"]["median_ms"] for item in details), 3),
            "details": details,
            "limitations": ["Schema/citation checks are not factual-accuracy metrics.", "Fixture preparation and fresh source-navigation verification are excluded from these timings.", "Mock explanations measure presentation compatibility, not answer quality."]}
    finally:
        owner.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run_fixture(args.repeats), indent=2))


if __name__ == "__main__":
    main()
