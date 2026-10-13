"""Compare the typed engine and current retrieval baseline on one pinned fixture.

Only --fixture provisions new, temporary test data with the existing indexer.
Existing user repositories, stored embeddings and active pointers are untouched.
"""

import argparse
import asyncio
from contextlib import ExitStack, closing, redirect_stdout
import io
import json
import math
from pathlib import Path
import sqlite3
from statistics import median
import sys
import tempfile
from time import monotonic
from unittest.mock import patch

from knowledge_base.benchmark_semantic import FIXTURES, QUESTIONS
from knowledge_base.metadata import trace
from query_system.contracts import OperationRequest
from query_system.semantic_engine import SemanticSearchEngine, existing_api_channels


def retrieval_metrics(relevant, ranked):
    if not relevant:
        raise ValueError("Retrieval metrics require a nonempty relevance label")
    hits = set(ranked[:5]) & relevant
    rank = next((index for index, identifier in enumerate(ranked[:5], 1) if identifier in relevant), None)
    return len(hits) / len(relevant), 1 / rank if rank else 0., rank


async def evaluate(engine, baseline, questions=QUESTIONS, repeats=3):
    if repeats < 1:
        raise ValueError("Benchmark repeats must be positive")
    relevant_sets = []
    with closing(sqlite3.connect(engine.database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for question, path, name in questions:
            relevant = {row[0] for row in db.execute("""SELECT c.id FROM code_chunks c
                JOIN chunk_context x ON x.id=c.id WHERE c.repository_id=? AND x.path=? AND x.qualified_name=?""",
                (engine.context.repository_id, path, name))}
            if not relevant:
                raise ValueError(f"Benchmark label has no indexed source: {path}::{name}")
            relevant_sets.append(relevant)
    methods = {name: {"latency_ms": [], "recall": [], "mrr": [], "valid": 0, "emitted": 0,
                      "statuses": {}} for name in ("baseline", "engine")}
    details, identical_order = [], True
    for number, ((question, path, name), relevant) in enumerate(zip(questions, relevant_sets)):
        first = {}
        for repetition in range(repeats):
            # Alternate order to reduce loaded-model/cache advantage for one path.
            order = ("baseline", "engine") if (number + repetition) % 2 == 0 else ("engine", "baseline")
            observed = {}
            for method in order:
                started = monotonic()
                if method == "baseline":
                    result = baseline(question)
                    if result["knowledgeSnapshotId"] != engine.context.snapshot_id:
                        raise ValueError("Baseline switched repository generations")
                    rows = result["results"]
                    status = result["status"]
                else:
                    operation = OperationRequest(engine="semantic", name="hybrid_search",
                        repository_id=engine.context.repository_id, snapshot_id=engine.context.snapshot_id,
                        parameters={"query": question, "limit": 5})
                    result = await engine.execute(operation, engine.context)
                    if result.snapshot_id != engine.context.snapshot_id:
                        raise ValueError("Engine switched repository generations")
                    rows = [{"entityId": finding.chunk_id,
                             "path": engine.evidence[finding.chunk_id].file_path,
                             "startLine": engine.evidence[finding.chunk_id].start_line,
                             "endLine": engine.evidence[finding.chunk_id].end_line,
                             "sourceHash": engine.evidence[finding.chunk_id].source_hash,
                             "snapshotId": engine.evidence[finding.chunk_id].index_snapshot_id,
                             "fileId": engine.evidence[finding.chunk_id].file_id,
                             "symbolId": engine.evidence[finding.chunk_id].symbol_id}
                            for finding in result.findings]
                    status = result.status
                stats = methods[method]
                stats["latency_ms"].append((monotonic() - started) * 1000)
                stats["statuses"][status] = stats["statuses"].get(status, 0) + 1
                ranked = [row["entityId"] for row in rows]
                observed[method] = ranked
                recall, mrr, rank = retrieval_metrics(relevant, ranked)
                if repetition == 0:
                    stats["recall"].append(recall)
                    stats["mrr"].append(mrr)
                    first[method] = rank
                for row in rows:
                    stats["emitted"] += 1
                    try:
                        info = trace(engine.database, engine.context.repository_id, row["entityId"])
                        valid = info["snapshotId"] in engine.context.ready_stage_ids and all(
                            row.get(field) == info.get(field) for field in (
                                "path", "startLine", "endLine", "sourceHash", "snapshotId", "fileId", "symbolId"))
                    except (ValueError, OSError, sqlite3.Error):
                        valid = False
                    stats["valid"] += int(valid)
            identical_order &= observed["baseline"] == observed["engine"]
        details.append({"question": question, "expected": path + "::" + name, "first_relevant_rank": first})
    report = {
        "evaluation": "isolated real-model fixture; not a general accuracy claim",
        "model": engine.channels.embedding_model,
        "repository_id": engine.context.repository_id, "generation_id": engine.context.snapshot_id,
        "source_digest": engine.view["manifest"]["sources"]["digest"],
        "pipeline_version": engine.view["manifest"].get("pipelineVersion"),
        "questions": len(questions), "repeats_per_question": repeats, "limit": 5,
        "identical_result_order": identical_order, "details": details,
    }
    for method, stats in methods.items():
        latencies = sorted(stats["latency_ms"])
        report[method] = {
            "recall_at_5": sum(stats["recall"]) / len(questions),
            "mrr_at_5": sum(stats["mrr"]) / len(questions),
            "median_latency_ms": round(median(latencies), 3),
            "p95_latency_ms": round(latencies[math.ceil(.95 * len(latencies)) - 1], 3),
            "evidence_validity": stats["valid"] / stats["emitted"] if stats["emitted"] else None,
            "emitted_evidence": stats["emitted"], "invalid_evidence": stats["emitted"] - stats["valid"],
            "statuses": stats["statuses"],
        }
    return report


def run_fixture(repeats=3):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-engine"))
    import api
    import config
    from indexer import index_directory
    from knowledge_base.graph import build_graph
    from knowledge_base.synchronization import SnapshotStore
    from knowledge_base.vector_store import FaissClient
    # Load the configured model before timing either retrieval path.
    config.get_embedding_model()
    with tempfile.TemporaryDirectory(prefix="cm-semantic-benchmark-") as folder, ExitStack() as stack:
        root, data = Path(folder) / "repo", Path(folder) / "data"
        root.mkdir()
        for path, _, source in FIXTURES:
            (root / path).write_text(source)
        repo = config.make_repo_id(str(root))
        client = FaissClient(data / "vectors")
        stack.callback(client.close)
        api._REGISTERED_REPOS[repo] = {"rootPath": str(root)}
        stack.callback(api._REGISTERED_REPOS.pop, repo, None)
        stack.enter_context(patch.object(api, "DEFAULT_DATA_DIR", str(data)))
        stack.enter_context(patch.object(api, "get_qdrant_client", return_value=client))
        stack.enter_context(patch("indexer.get_qdrant_client", return_value=client))
        store = SnapshotStore(root, repo, data)
        with redirect_stdout(io.StringIO()):
            store.publish(client=client, collection=config.COLLECTION_NAME,
                          index_directory=index_directory, build_graph=build_graph)
        with SemanticSearchEngine.pinned(store, existing_api_channels(api, config.EMBEDDING_MODEL_NAME)) as engine:
            baseline = lambda question: api._retrieve_pinned(
                api.SearchRequest(repoId=repo, query=question, retrieval="hybrid", limit=5), engine.view)
            # Warm both paths on the same first query before collecting samples.
            asyncio.run(evaluate(engine, baseline, questions=QUESTIONS[:1], repeats=1))
            return asyncio.run(evaluate(engine, baseline, repeats=repeats))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true", required=True,
                        help="Provision temporary labeled test data using the existing indexer")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(run_fixture(args.repeats), indent=2))


if __name__ == "__main__":
    main()
