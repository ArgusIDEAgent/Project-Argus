"""Summarize preserved Phase 3 measurements; never infer human review or rollout."""
import argparse
import json
from pathlib import Path

from query_system.qualify_phase3 import DATASET, PROJECT, latency, save


def ratio(rows):
    return {"correct": sum(bool(value) for value in rows), "total": len(rows),
            "rate": sum(bool(value) for value in rows)/len(rows) if rows else None}


def assess(corpora, extension, model, node=None, target=None, react=None, cnn=None):
    labels = {case["id"]: case for case in json.loads(DATASET.read_text())}
    profiles = [json.loads((corpora / (name + ".json")).read_text()) for name in ("react", "cnn", "node", "target")]
    if node: profiles[2] = json.loads(node.read_text())
    if target: profiles[3] = json.loads(target.read_text())
    if react: profiles[0] = json.loads(react.read_text())
    if cnn: profiles[1] = json.loads(cnn.read_text())
    profiles.insert(0, json.loads(extension.read_text()))
    model_report = json.loads(model.read_text())
    details, missing, stale = [], [], []
    for profile in profiles[:-1]:
        for row in profile.get("queries", []):
            label = labels[row["id"]]
            if row["question"] != label["question"]:
                stale.append(row["id"]); continue
            details.append((label, row))
    missing = sorted(set(labels) - {label["id"] for label, _ in details})
    metrics = {
        "intent_only": ratio([[intent for intent, _ in row["actual_rows"]] == [intent for intent, _ in label["rows"]]
            if label["rows"] else row["status"] == "unsupported" for label, row in details]),
        "intent_and_requirement_rows": ratio([row["rows_correct"] for _, row in details]),
        "target_resolution_and_path": ratio([row["canonical_target_paths_correct"] for label, row in details if label["rows"] and label["family"] != "C" and label["family"] != "H"]),
        "comparison_operands": ratio([row["rows_correct"] for label, row in details if any(intent == "CODE_COMPARISON" for intent, _ in label["rows"])]),
        "original_requirement_count": ratio([len(row["actual_rows"]) == len(label["rows"]) for label, row in details if label["rows"]]),
        "engine_operations": ratio([row["operations"] == label["operations"] for label, row in details if "operations" in label]),
        "semantic_routing": ratio([row["operations"] == ["semantic.hybrid_search"] for label, row in details if label["rows"] and label["rows"][0][0] == "FEATURE_DISCOVERY"]),
        "ambiguity": ratio([any(item["resolution"] == "ambiguous" for item in row["canonical_targets"]) and not any(item["name"] in {"isInside", "readSource"} and item["symbol_id"] for item in row["canonical_targets"]) for label, row in details if label.get("resolution") == "ambiguous"]),
        "unsupported": ratio([row["status"] == "unsupported" for label, row in details if not label["rows"] or label.get("resolution") == "unsupported"]),
        "nontrivial_dependency_plan": ratio([row.get("dependency_plan_correct", False) for label, row in details if label.get("dependencies")]),
        "llm_necessity_probe": ratio([row["llm_necessity"]["correct"] for _, row in details if "llm_necessity" in row]),
    }
    models = model_report["ollama"]["details"]
    positive = [item for item in models if item["case"].split(".")[0] in {"function", "workflow", "behavior", "comparison", "compound"}]
    attempted = [item for item in positive if item["transport"]]
    successful = [item for item in positive if any(g["model_status"] == "success" for g in item["response"]["generations"])]
    warm = [item["response"]["timings"]["total_ms"] for item in attempted if item["resident_before"]]
    cold = [item["response"]["timings"]["total_ms"] for item in attempted if not item["resident_before"]]
    model_metrics = {"positive_tasks": len(positive), "attempted": len(attempted), "accepted": len(successful),
        "acceptance": ratio([item in successful for item in positive]),
        "warm_ms": latency(warm) if warm else None, "warm_samples": len(warm),
        "cold_ms": latency(cold) if cold else None, "cold_samples": len(cold),
        "fallback": ratio([any(g["fallback_status"] != "not_used" for g in item["response"]["generations"]) for item in positive]),
        "timeouts": ratio([any(g["model_status"] == "timeout" for g in item["response"]["generations"]) for item in positive]),
        "structured_coverage_rejections": sum(any(g["failure_category"] == "missing_requirement_coverage" for g in item["response"]["generations"]) for item in positive),
        "human_review": "pending", "factuality_rate": None,
        "note": "Latency of rejected/fallback results is not latency of a successful explanation; small warm/cold cohorts do not qualify all tasks."}
    by_family = {}
    for label, row in details:
        by_family.setdefault(label["family"], []).extend(row["samples_ms"])
    return {"classification": "Development-ready", "metrics": metrics, "missing_labels": missing, "stale_label_inputs": stale,
        "by_family_latency": {family: {**latency(samples), "samples": len(samples), "max_ms": max(samples)} for family, samples in by_family.items()},
        "model": model_metrics,
        "profiles": [{key: profile.get(key) for key in ("profile", "state", "copied_files", "copied_lines", "copied_bytes", "indexing_ms", "publication_stages", "initial_embedding_work", "python_peak_rss_bytes", "original_sources_unchanged")} for profile in profiles],
        "limitations": ["Hand-labeled development cases, not held-out employee sampling; percentages have small denominators.",
            "Generation necessity is an injected transport probe, not a real-model quality score.",
            "Human usefulness and independent factuality remain unqualified until actually reviewed.",
            "Full mixed-corpus semantic precision is unmeasured; known-positive file recall is not universal recall."]}


def review_packet(model):
    report = json.loads(model.read_text())
    lines = ["# Phase 3 human review — ORIGINAL model outputs", "", "Status: pending. No assistant assessment counts as human approval.",
        "", "Please score each function/workflow/behavior/comparison family from 1–5 on directness, finding the relevant code, following the flow, distinguishing facts from hypotheses, communicating unknowns, and actionable next steps.",
        "A fallback is not a successful model explanation. For each assertion classify: supported, qualified, hypothetical, unsupported, contradicted, or not assessable. Note omitted inputs/outputs/errors and any misleading inference. Rate both the model output and the actual fallback separately.",
        "", "The ≥4/5 employee-usefulness gate needs your documented scores; citation membership alone is not factual review. These are original frozen outputs, not corrected rewrites. Never use expired disposable citations for navigation.", "",
        "Raw machine evidence: " + str(model.resolve()), "", "## Source checklist (review aid, not model output)", "",
        "- sourceNavigation.ts:7–31: lexical containment helper; three required string inputs; canonical real path on success; NUL U+0000, symlink containment, regular file, strictly >5 MiB, SHA-256 mismatch, and uncaught filesystem errors.",
        "- responsePreview.ts:54–94: validates citation/repository/generation/file identity and bounds; checks Workspace Trust and dirty editor/hash, rechecks saved disk then selects source. A static call path is not a runtime trace.",
        "- A hash mismatch does not establish whether the file or expected metadata changed. Size limits are bytes. No performance/equivalence claim follows from static code.", ""]
    for item in report["ollama"]["details"]:
        if item["case"].split(".")[0] not in {"function", "workflow", "behavior", "comparison", "compound"}: continue
        lines.extend(["## " + item["case"], "", item["question"], "", "Backend diagnostics: `" + json.dumps(item["response"]["generations"]) + "`", ""])
        for call in item["transport"]:
            raw = call.get("original_response_redacted")
            if raw is None:
                lines.extend(["No response body was received (transport timeout/failure); no original prose is invented.", ""])
                continue
            try: content = json.loads(raw).get("message", {}).get("content", raw)
            except (ValueError, AttributeError): content = raw
            lines.extend(["Original model text (redactions only):", "", "````json", content, "````", ""])
        for generation in item["generations"]:
            lines.extend(["Released answer / deterministic fallback:", ""])
            for section in generation["sections"]:
                for claim in section["claims"]: lines.append("- [" + claim["classification"] + "] " + claim["text"])
        lines.extend(["", "Reviewer / date: ___", "Six scores: ___ / ___ / ___ / ___ / ___ / ___", "Assertion classifications, corrections, missing edge cases: ___", "Accept / accept with corrections / reject: ___", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpora", type=Path, required=True)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--react", type=Path)
    parser.add_argument("--cnn", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "assessment.json", assess(args.corpora, args.extension, args.model, args.node, args.target, args.react, args.cnn))
    (args.output / "HUMAN_REVIEW.md").write_text(review_packet(args.model))


if __name__ == "__main__": main()
