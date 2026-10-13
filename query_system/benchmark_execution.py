"""Phase 6 synthetic execution latency benchmark; no user indexing or LLM."""

import argparse
import asyncio
from contextlib import ExitStack
import json
import math
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

from query_system.contracts import QueryRequest
from query_system.execution_contracts import ExecutionLimits
from query_system.executor import MultiEngineExecutor
from query_system.semantic_engine import existing_api_channels


WORKLOADS = [
    ("definition", "Where is login defined?", ["success"]),
    ("callers", "Who calls login?", ["success"]),
    ("callees", "What does login call?", ["success"]),
    ("path", "Dependency path from entry to validateToken", ["success"]),
    ("contract", "Contract of validateToken", ["partial"]),
    ("module", "Overview of auth.ts", ["success"]),
    ("semantic_clarification", "Find code that validates a token and show its callers", ["success", "ambiguous"]),
    ("compound_sequential", "Find the login function, show its callers, and tell me what might be affected if I change it.", ["success", "success", "partial"]),
    ("independent", "Who calls login and what does entry call?", ["success"] * 2),
]


def latency(values):
    values = sorted(values)
    return {"median_ms": round(median(values), 3), "p95_ms": round(values[math.ceil(.95 * len(values)) - 1], 3)}


def fingerprint(report):
    return {
        "requirements": [(item.requirement.intent, item.status) for item in report.requirements],
        "operations": [(item.status, item.result.model_dump(exclude={"execution_ms"}) if item.result else None) for item in report.operations],
        "evidence": report.evidence,
        "candidates": [[candidate.entity_id for candidate in item.candidates] for item in report.clarifications],
    }


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
            pins = guards.enter_context(patch.object(owner.store, "pin_view", wraps=owner.store.pin_view))
            channels = existing_api_channels(api, config.EMBEDDING_MODEL_NAME)
            with MultiEngineExecutor.pinned(owner.store, channels) as executor:
                samples = {mode: {key: [] for key in ("planning", "execution", "total")} for mode in ("sequential", "concurrent")}
                proofs, valid, stages, identities, details = 0, 0, set(), set(), []
                for label, question, expected in WORKLOADS:
                    query = executor.understand(QueryRequest(repository_id=owner.repo, question=question))
                    limits = {"sequential": ExecutionLimits(max_concurrent=1), "concurrent": ExecutionLimits(max_concurrent=3)}
                    for mode in limits:  # Untimed warm-up; excludes fixture/model initialization.
                        asyncio.run(executor.execute(executor.plan(query, limits[mode])))
                    reference = None
                    stable, outcomes_match, peak = True, True, {}
                    for repeat in range(repeats):
                        for mode in (["sequential", "concurrent"] if repeat % 2 == 0 else ["concurrent", "sequential"]):
                            start = perf_counter()
                            plan = executor.plan(query, limits[mode])
                            planned = perf_counter()
                            report = asyncio.run(executor.execute(plan))
                            end = perf_counter()
                            for key, duration in (("planning", planned-start), ("execution", end-planned), ("total", end-start)):
                                samples[mode][key].append(duration * 1000)
                            comparable = fingerprint(report)
                            stable &= reference is None or comparable == reference
                            reference = comparable
                            outcomes_match &= [item.status for item in report.requirements] == expected
                            outcomes_match &= len(report.requirements) == len(query.requirements)
                            identities.add((report.repository_id, report.snapshot_id))
                            peak[mode] = max(peak.get(mode, 0), report.peak_concurrency)
                            for proof in report.evidence.values():
                                proofs += 1
                                valid += int(executor._verify(proof) == proof)
                                stages.add(proof.index_snapshot_id)
                    details.append({"workload": label, "question": question, "expected_requirement_statuses": expected,
                                    "actual_requirement_statuses": [item.status for item in report.requirements],
                                    "outcomes_match": outcomes_match, "stable_across_modes_and_repeats": stable, "peak_concurrency": peak})
                return {
                    "evaluation": "Synthetic development fixture with fixed query vectors; not real-model retrieval quality or production performance",
                    "timing": "Planning plus execution; normalization, fixture indexing, warm-up and external proof recount excluded",
                    "repository_id": owner.repo, "generation_id": executor.context.snapshot_id,
                    "indexing_stage_ids": sorted(stages), "pin_count": pins.call_count,
                    "one_pinned_generation": identities == {(owner.repo, executor.context.snapshot_id)},
                    "workloads": len(WORKLOADS), "repeats": repeats, "timed_runs": len(WORKLOADS) * repeats * 2,
                    "proofs_checked": proofs, "evidence_validity": valid / proofs if proofs else None,
                    "outcomes_matched": all(item["outcomes_match"] for item in details),
                    "deterministic_results": all(item["stable_across_modes_and_repeats"] for item in details),
                    "latency": {mode: {key: latency(values) for key, values in groups.items()} for mode, groups in samples.items()},
                    "details": details,
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
    if not (report["one_pinned_generation"] and report["outcomes_matched"] and report["deterministic_results"] and report["evidence_validity"] == 1.0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
