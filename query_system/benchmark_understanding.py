"""Labeled Phase 5 development benchmark on one temporary pinned generation."""

import argparse
from contextlib import ExitStack, closing
import json
import math
from pathlib import Path
import sqlite3
from statistics import median
import sys
from time import perf_counter
from unittest.mock import patch

from knowledge_base.metadata import trace
from query_system.contracts import Evidence, QueryContext, QueryRequest
from query_system.query_understanding import QueryUnderstandingEngine

DATASET = Path(__file__).with_name("benchmarks") / "phase5_queries.json"


def target_matches(engine, target, entities):
    matches = [entity for entity in entities if ("name" not in target or entity.name == target["name"])
               and entity.resolution == target["resolution"] and ("kind" not in target or entity.kind == target["kind"])]
    with closing(sqlite3.connect(engine.database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for entity in matches:
            if target["resolution"] == "resolved":
                if entity.file_path != target["path"] or entity.evidence is None:
                    continue
                if target.get("kind") in ("file", "module"):
                    expected = db.execute("SELECT id FROM files WHERE repository_id=? AND relative_path=?",
                                          (engine.context.repository_id, target["path"])).fetchone()[0]
                    if entity.file_id != expected:
                        continue
                else:
                    expected = db.execute("SELECT id FROM symbols WHERE repository_id=? AND relative_path=? AND qualified_name=?",
                        (engine.context.repository_id, target["path"], target.get("canonical_name", target.get("name")))).fetchall()
                    if len(expected) != 1 or entity.symbol_id != expected[0][0]:
                        continue
            elif target["resolution"] == "ambiguous":
                if {candidate.file_path for candidate in entity.candidates} != set(target["candidates"]) or entity.symbol_id is not None:
                    continue
                if target.get("kind") != "file":
                    expected = {row[0] for row in db.execute("SELECT id FROM symbols WHERE repository_id=? AND qualified_name=?",
                                (engine.context.repository_id, target["name"]))}
                    if {candidate.symbol_id for candidate in entity.candidates} != expected:
                        continue
            elif entity.symbol_id is not None or entity.entity_id is not None:
                continue
            return True
    return False


def evaluate(engine, dataset, repeats=3):
    if len(dataset) < 60 or repeats < 1:
        raise ValueError("At least 60 labels and positive repetitions are required")
    counts, details, all_latencies = {}, [], []
    valid_proofs, proof_count, snapshots = 0, 0, set()
    for label in dataset:
        context = None
        if label.get("previous_target"):
            initial = engine.normalize(QueryRequest(repository_id=engine.context.repository_id,
                                      question=f"Where is {label['previous_target']} defined?"))
            context = QueryContext(repository_id=engine.context.repository_id, snapshot_id=engine.context.snapshot_id,
                                   previous_target=initial.entities[0], previous_intents=initial.intents)
            if label.get("stale_context"):
                context = context.model_copy(update={"snapshot_id": "f" * 32})
        request = QueryRequest(repository_id=engine.context.repository_id, question=label["question"],
                               active_file=label.get("active_file"))
        samples, stable = [], True
        previous = None
        for _ in range(repeats):
            started = perf_counter()
            result = engine.normalize(request, context)
            samples.append((perf_counter() - started) * 1000)
            comparable = result.model_dump(exclude={"normalization_ms"})
            stable &= previous is None or comparable == previous
            previous = comparable
            snapshots.add(result.snapshot_id)
            for entity in result.entities:
                for proof in ([entity.evidence] if entity.evidence else []) + [candidate.evidence for candidate in entity.candidates]:
                    proof_count += 1
                    actual = Evidence.from_trace(trace(engine.database, engine.context.repository_id, proof.evidence_id),
                        engine.context, evidence_type=proof.evidence_type, resolution=proof.resolution)
                    valid_proofs += int(actual == proof and proof.snapshot_id == engine.context.snapshot_id)
        stats = counts.setdefault(label["category"], {"queries": 0, "intent_correct": 0, "primary_correct": 0,
            "targets": 0, "target_correct": 0, "ambiguities": 0, "ambiguity_correct": 0,
            "descriptions": 0, "description_correct": 0, "support_labels": 0, "support_correct": 0,
            "stable_queries": 0, "latencies": []})
        correct = result.intents == label["intents"]
        stats["queries"] += 1
        stats["intent_correct"] += int(correct)
        stats["primary_correct"] += int(result.primary_intent == label["intents"][0])
        stats["stable_queries"] += int(stable)
        target_correct = []
        for target in label.get("targets", []):
            matched = target_matches(engine, target, result.entities)
            target_correct.append(matched)
            stats["targets"] += 1
            stats["target_correct"] += int(matched)
            if target["resolution"] == "ambiguous":
                stats["ambiguities"] += 1
                stats["ambiguity_correct"] += int(matched and result.status == "ambiguous")
        if label.get("feature"):
            stats["descriptions"] += 1
            stats["description_correct"] += int(any(entity.kind == "feature" and entity.name == result.normalized_question
                                                    for entity in result.entities))
        if label.get("support"):
            stats["support_labels"] += 1
            stats["support_correct"] += int(result.requirements[0].support == label["support"])
        stats["latencies"].extend(samples)
        all_latencies.extend(samples)
        details.append({"category": label["category"], "question": label["question"], "expected_intents": label["intents"],
            "actual_intents": result.intents, "intent_correct": correct, "target_correct": target_correct,
            "status": result.status, "stable": stable})
    def summary(stats):
        latency = sorted(stats["latencies"])
        report = {key: value for key, value in stats.items() if key != "latencies"}
        report.update(intent_accuracy=stats["intent_correct"] / stats["queries"],
            primary_intent_accuracy=stats["primary_correct"] / stats["queries"],
            entity_resolution_accuracy=stats["target_correct"] / stats["targets"] if stats["targets"] else None,
            ambiguity_accuracy=stats["ambiguity_correct"] / stats["ambiguities"] if stats["ambiguities"] else None,
            description_preservation=stats["description_correct"] / stats["descriptions"] if stats["descriptions"] else None,
            median_latency_ms=round(median(latency), 3), p95_latency_ms=round(latency[math.ceil(.95 * len(latency))-1], 3))
        return report
    total = {key: sum(stats[key] for stats in counts.values()) for key in next(iter(counts.values())) if key != "latencies"}
    total["latencies"] = all_latencies
    return {"evaluation": "Hand-labeled development corpus and synthetic fixture; not a held-out general accuracy guarantee",
        "repository_id": engine.context.repository_id, "generation_id": engine.context.snapshot_id,
        "repeats": repeats, "one_pinned_generation": snapshots == {engine.context.snapshot_id},
        "evidence_validity": valid_proofs / proof_count if proof_count else None,
        "proofs_checked": proof_count, "overall": summary(total),
        "by_category": {category: summary(stats) for category, stats in sorted(counts.items())}, "details": details}


def run_fixture(repeats=3):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-engine" / "tests"))
    from phase5_fixture import fixture
    owner = fixture()
    try:
        with QueryUnderstandingEngine.pinned(owner.store) as engine, ExitStack() as guards:
            for function in ("query_system.graph_engine.GraphQueryEngine.execute", "query_system.semantic_engine.SemanticSearchEngine.execute",
                             "query_system.rule_engine.RuleBasedAnalysisEngine.execute", "answers.answer_question", "config.embed_texts"):
                guards.enter_context(patch(function, side_effect=AssertionError("Benchmark must not dispatch answering engines")))
            dataset = json.loads(DATASET.read_text())
            engine.normalize(QueryRequest(repository_id=owner.repo, question=dataset[0]["question"]))
            return evaluate(engine, dataset, repeats)
    finally:
        owner.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run_fixture(args.repeats), indent=2))


if __name__ == "__main__":
    main()
