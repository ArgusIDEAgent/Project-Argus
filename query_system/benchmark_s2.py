"""S2 labeled development evaluation; existing test fixture, no answer generation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from knowledge_base.metadata import trace
from query_system.contracts import Evidence, QueryContext, QueryRequest
from query_system.query_understanding import QueryUnderstandingEngine

DATASET = Path(__file__).with_name("benchmarks") / "s2_queries.json"


def evaluate(executor, engine_type, dataset):
    engine = engine_type(executor.store, executor.view, executor.context); engine._active = True
    details, checked, valid = [], 0, 0
    try:
        for label in dataset:
            context = None
            if label.get("previous"):
                initial = engine.normalize(QueryRequest(repository_id=executor.context.repository_id, question=f"Where is {label['previous']} defined?"))
                context = QueryContext(repository_id=executor.context.repository_id, snapshot_id=executor.context.snapshot_id,
                    previous_target=initial.entities[0], previous_intents=label.get("previous_intents", initial.intents))
            query = engine.normalize(QueryRequest(repository_id=executor.context.repository_id, question=label["question"]), context)
            plan = executor.plan(query)
            actual = [[row.intent, [query.entities[i].name for i in row.entity_indices]] for row in query.requirements]
            expected_intents = list(dict.fromkeys(row[0] for row in label["rows"]))
            bad_targets = any(entity.kind != "feature" and entity.resolution in ("ambiguous", "not_found", "unresolved_text") for entity in query.entities)
            ambiguity = query.status == "ambiguous" or any(row.disposition == "blocked" and row.blocked_status == "ambiguous" for row in plan.requirements)
            clarification = ambiguity or bad_targets or any(row.intent == "UNRESOLVED" for row in query.requirements)
            operations = [op.request.engine + "." + op.request.name for op in plan.operations]
            for entity in query.entities:
                for proof in ([entity.evidence] if entity.evidence else []) + [candidate.evidence for candidate in entity.candidates]:
                    checked += 1
                    current = Evidence.from_trace(trace(executor.database, executor.context.repository_id, proof.evidence_id), executor.context,
                        evidence_type=proof.evidence_type, resolution=proof.resolution)
                    valid += int(proof == current)
            details.append({"category": label["category"], "question": label["question"], "expected_rows": label["rows"], "actual_rows": actual,
                "status": query.status, "intent_correct": query.intents == expected_intents,
                "requirements_preserved": [row[0] for row in actual] == [row[0] for row in label["rows"]],
                "targets_preserved": actual == label["rows"], "expected_clarification": label["clarify"],
                "clarification": clarification, "incorrect_ambiguity": ambiguity and not label["clarify"],
                "expected_operations": label["operations"], "operations": operations, "router_agrees": operations == label["operations"]})
    finally:
        engine._active = False
    def metrics(items):
        need = sum(item["expected_clarification"] for item in items)
        clear = len(items) - need
        return {"questions": len(items),
            "intent_accuracy": sum(item["intent_correct"] for item in items) / len(items),
            "target_preservation": sum(item["targets_preserved"] for item in items) / len(items),
            "requirement_preservation": sum(item["requirements_preserved"] for item in items) / len(items),
            "correct_clarification_rate": sum(item["clarification"] and item["expected_clarification"] for item in items) / need if need else None,
            "clarification_decision_accuracy": sum(item["clarification"] == item["expected_clarification"] for item in items) / len(items),
            "incorrect_ambiguity_rate": sum(item["incorrect_ambiguity"] for item in items) / clear if clear else None,
            "router_operation_agreement": sum(item["router_agrees"] for item in items) / len(items)}
    return {"metrics": metrics(details), "by_category": {category: metrics([item for item in details if item["category"] == category]) for category in sorted({item["category"] for item in details})},
        "proofs_checked": checked, "valid_proofs": valid, "details": details}


def run(baseline_source=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-engine/tests"))
    import test_phase6_execution as phase6
    owner = phase6.ExecutionTests(); owner.setUp()
    try:
        dataset = json.loads(DATASET.read_text())
        with patch.object(owner.store, "pin_view", wraps=owner.store.pin_view) as pins, owner.session() as executor, patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("No LLM in understanding evaluation")):
            result = {"evaluation": "41 hand-labeled development queries over the existing synthetic fixture; not general language understanding",
                "repository_id": owner.repo, "generation_id": executor.context.snapshot_id,
                "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "one_pinned_generation": True}
            if baseline_source is not None:
                namespace = {"__name__": "query_system._s2_pre_fix"}
                exec(compile(baseline_source, "<pre-S2 query_understanding.py>", "exec"), namespace)
                result["baseline_source_sha256"] = hashlib.sha256(baseline_source.encode()).hexdigest()
                result["before"] = evaluate(executor, namespace["QueryUnderstandingEngine"], dataset)
            result["after"] = evaluate(executor, QueryUnderstandingEngine, dataset)
            result["reader_pins"] = pins.call_count
            assert pins.call_count == 1
            return result
    finally:
        owner.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-stdin", action="store_true", help="Compare trusted pre-S2 normalizer source supplied on stdin; never query/retrieved text")
    args = parser.parse_args()
    print(json.dumps(run(sys.stdin.read() if args.baseline_stdin else None), indent=2))


if __name__ == "__main__": main()
