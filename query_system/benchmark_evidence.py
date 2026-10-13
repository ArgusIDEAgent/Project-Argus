"""Measure Phase 7 only, on precomputed Phase 6 reports under one test pin."""

import argparse
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

from query_system.benchmark_execution import WORKLOADS, latency
from query_system.contracts import QueryRequest
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.executor import MultiEngineExecutor
from query_system.semantic_engine import existing_api_channels


def run_fixture(repeats=3):
    if repeats < 1:
        raise ValueError("Positive repetition count required")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ai-engine"))
    sys.path.insert(0, str(root / "ai-engine" / "tests"))
    import api
    import config
    from phase5_fixture import fixture
    owner = fixture()
    try:
        with ExitStack() as guards:
            guards.enter_context(patch.dict(api._REGISTERED_REPOS, {owner.repo: {"rootPath": str(owner.root)}}))
            guards.enter_context(patch.object(api, "get_qdrant_client", return_value=owner.client))
            guards.enter_context(patch.object(api, "embed_texts", side_effect=lambda texts: [[1.] + [0.] * 383 for _ in texts]))
            guards.enter_context(patch.object(api, "DEFAULT_DATA_DIR", str(owner.data)))
            guards.enter_context(patch("answers.answer_question", side_effect=AssertionError("LLM forbidden")))
            guards.enter_context(patch("answers.urlopen", side_effect=AssertionError("Ollama forbidden")))
            pins = guards.enter_context(patch.object(owner.store, "pin_view", wraps=owner.store.pin_view))
            with MultiEngineExecutor.pinned(owner.store, existing_api_channels(api, config.EMBEDDING_MODEL_NAME)) as executor:
                aggregator = EvidenceAggregator.from_executor(executor)
                samples, sizes, details, stages = [], [], [], set()
                proofs = valid_proofs = citations = valid_citations = 0
                reference_report = None
                for label, question, expected in WORKLOADS:
                    query = executor.understand(QueryRequest(repository_id=owner.repo, question=question))
                    report = asyncio.run(executor.execute(executor.plan(query)))  # Excluded from timing.
                    reference_report = reference_report or report
                    aggregator.aggregate(report)  # Untimed warm-up.
                    previous, stable, covered, local_samples, local_sizes = None, True, True, [], []
                    for _ in range(repeats):
                        start = perf_counter()
                        package = aggregator.aggregate(report)
                        duration = (perf_counter() - start) * 1000
                        samples.append(duration); local_samples.append(duration)
                        serialized = package.model_dump_json()
                        size = len(serialized.encode())
                        sizes.append(size); local_sizes.append(size)
                        stable &= previous is None or previous == serialized
                        previous = serialized
                        covered &= [item.execution_status for item in package.requirements] == expected
                        covered &= len(package.requirements) == len(report.requirements) and len(package.operations) == len(report.operations)
                        for record in package.evidence.values():
                            proofs += 1
                            info = aggregator.verify(record.proof)
                            valid_proofs += int(record.proof.snapshot_id == executor.context.snapshot_id and info["entityId"] == record.proof.evidence_id)
                            stages.add(record.proof.index_snapshot_id)
                        for reference, citation in package.citations.items():
                            citations += 1
                            record = package.evidence[citation.record_id]
                            valid_citations += int(citation.source == record.proof and reference == f"cm7:{package.package_id}:{record.record_id}" and citation.source.file_path is not None)
                    details.append({"workload": label, "latency": latency(local_samples), "median_package_bytes": median(local_sizes),
                        "coverage_preserved": covered, "deterministic_packages": stable, "validation_status": package.validation_status,
                        "findings": len(package.findings), "records": len(package.evidence), "conflicts": len(package.conflicts)})
                invalid_checks = {}
                for name in ("generation", "hash", "range", "symbol"):
                    invalid = reference_report.model_copy(deep=True)
                    key = next(iter(invalid.evidence))
                    proof = invalid.evidence[key]
                    if name == "generation":
                        invalid.snapshot_id = "f" * 32
                    else:
                        update = {"source_hash": "0" * 64} if name == "hash" else {"symbol_id": "not-canonical"} if name == "symbol" else {"start_line": proof.start_line + 100, "end_line": proof.end_line + 100}
                        invalid.evidence[key] = proof.model_copy(update=update)
                    rejected = aggregator.aggregate(invalid)
                    invalid_checks[name] = bool(rejected.conflicts) and all(record.proof.evidence_id != key for record in rejected.evidence.values())
                return {
                    "evaluation": "Synthetic development fixture, fixed query vectors; not a production guarantee",
                    "timing": "Phase 7 aggregate() only, including canonical verification and serialization-budget enforcement; normalization, execution/retrieval and proof/citation recount excluded",
                    "source_context": "Metadata-only default; optional redacted excerpts are tested separately",
                    "repository_id": owner.repo, "generation_id": executor.context.snapshot_id, "pin_count": pins.call_count,
                    "indexing_stage_ids": sorted(stages), "workloads": len(WORKLOADS), "repeats": repeats, "timed_runs": len(samples),
                    "latency": latency(samples), "package_bytes": {"median": median(sizes), "min": min(sizes), "max": max(sizes)},
                    "proofs_checked": proofs, "evidence_validity": valid_proofs / proofs,
                    "citations_checked": citations, "citation_validity": valid_citations / citations,
                    "invalid_evidence_checks": invalid_checks, "invalid_evidence_rejection": sum(invalid_checks.values()) / len(invalid_checks),
                    "coverage_preserved": all(item["coverage_preserved"] for item in details),
                    "deterministic_packages": all(item["deterministic_packages"] for item in details), "details": details,
                }
    finally:
        owner.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    report = run_fixture(args.repeats)
    print(json.dumps(report, indent=2))
    if not (report["evidence_validity"] == report["citation_validity"] == report["invalid_evidence_rejection"] == 1.0
            and report["coverage_preserved"] and report["deterministic_packages"] and all(item["conflicts"] == 0 for item in report["details"])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
