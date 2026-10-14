"""Isolated Phase 10 end-to-end measurements; never changes a user index/model."""
import argparse
import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sqlite3
from statistics import median
import sys
import tempfile
from time import perf_counter
import tracemalloc
from unittest.mock import patch

from query_system.benchmark_execution import latency
from query_system.contracts import QueryRequest
from query_system.generation_contracts import GenerationBudget
from query_system.hybrid_contracts import HybridLimits, HybridQueryRequest, HybridResponse
from query_system.orchestration import run_hybrid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "ai-engine"))
sys.path.insert(0, str(PROJECT / "ai-engine/tests"))
import api
import answers
import config
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.scanner import EXCLUDED_DIRS, SENSITIVE_NAMES, SENSITIVE_SUFFIXES, SECRET_MARKERS
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.semantic_engine import existing_api_channels
from test_phase8_generation import FakeLocal

WORKLOADS = [
    ("graph", "Who calls login?"), ("semantic", "Find code that checks access"),
    ("rules", "Contract of validateToken"),
    ("multi_engine", "Who calls login and contract of entry"),
    ("explanation", "Explain `login`"),
    ("compound", "Where is `login` defined and explain `login`"),
]

def request(repo, question):
    return HybridQueryRequest(query=QueryRequest(repository_id=repo, question=question),
        limits=HybridLimits(generation=GenerationBudget(num_ctx=32768, max_context_bytes=24000)))

def sample(store, repo, question, channels, client):
    tick = perf_counter()
    run = asyncio.run(run_hybrid(store, request(repo, question), channels, answers, client=client))
    payload = run.response.model_dump_json(); HybridResponse.model_validate_json(payload)
    sources = list(run.response.presentation.sources.values()) if run.response.presentation else []
    valid = sum(source.state == "verified" and source.citation.source.repository_id == repo and
        source.citation.source.snapshot_id == run.response.snapshot_id and run.package.citations.get(source.citation.reference) == source.citation for source in sources)
    return run, {"status": run.response.status, "stage_ms": run.response.timings.model_dump(),
        "wall_ms": (perf_counter() - tick) * 1000, "response_bytes": len(payload.encode()),
        "citation_checks": len(sources), "valid_citations": valid,
        "fallback": any(result.fallback_status not in ("not_used", "not_needed") for _, result in run.generations),
        "truncated": bool(run.package and run.package.omissions) or any(result.context and result.context.omissions for _, result in run.generations),
        "model_attempts": sum(item.model_attempts for item in run.response.generations)}

def fixture_benchmark(repeats):
    import test_phase6_execution as phase6
    owner = phase6.ExecutionTests(); owner.setUp()
    try:
        details, citations, valid = [], 0, 0
        tracemalloc.start()
        for label, question in WORKLOADS:
            samples = [sample(owner.store, owner.repo, question, owner.channels, FakeLocal())[1] for _ in range(repeats)]
            citations += sum(item["citation_checks"] for item in samples); valid += sum(item["valid_citations"] for item in samples)
            details.append({"workload": label, "question": question, "samples": repeats,
                "latency": {stage: latency([item["stage_ms"][stage] for item in samples]) for stage in samples[0]["stage_ms"]},
                "wall_latency": latency([item["wall_ms"] for item in samples]),
                "response_bytes_median": median(item["response_bytes"] for item in samples),
                "response_bytes_max": max(item["response_bytes"] for item in samples),
                "fallback_rate": sum(item["fallback"] for item in samples) / repeats,
                "truncation_rate": sum(bool(item["truncated"]) for item in samples) / repeats,
                "statuses": sorted({item["status"] for item in samples}), "model_attempts": sum(item["model_attempts"] for item in samples)})
        _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
        return {"evaluation": "Synthetic end-to-end, fixed retrieval vectors and mocked local generation; not production estimates",
            "runs": repeats * len(WORKLOADS), "repeats_per_workload": repeats,
            "generation_id": owner.store.active()["id"], "citation_checks": citations, "valid_citations": valid,
            "python_allocations_peak_bytes": peak, "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
            "memory_notes": "RSS includes imports/native FAISS/runtime; Python tracemalloc excludes native/model memory. Ollama memory is not measured.",
            "details": details}
    finally: owner.doCleanups()

def copy_sources(destination, mode):
    """Copy inert real text only; never execute repository scripts or follow links."""
    paths = []
    for current, directories, files in os.walk(PROJECT, followlinks=False):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRS | config.IGNORE_DIRS | {"backend", "benchmarks", "artifacts"} and not (Path(current) / name).is_symlink()]
        for name in files:
            source = Path(current) / name; relative = source.relative_to(PROJECT)
            if mode == "small" and str(relative) not in {"extension/src/sourceNavigation.ts", "extension/package.json", "extension/README.md"}: continue
            if mode == "multilingual-diagnostic" and source.suffix in (".ts", ".js", ".cjs", ".tsx", ".jsx") and str(relative) != "extension/src/sourceNavigation.ts": continue
            if source.is_symlink() or name in SENSITIVE_NAMES or name.startswith(".env") or name.endswith(SENSITIVE_SUFFIXES): continue
            if source.suffix not in config.CODE_EXTENSIONS or source.stat().st_size > config.MAX_FILE_BYTES: continue
            raw = source.read_bytes()
            if b"\0" in raw[:8192] or any(marker.search(raw) for marker in SECRET_MARKERS): continue
            try: raw.decode("utf-8")
            except UnicodeDecodeError: continue
            target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
            paths.append((relative.as_posix(), len(raw), hashlib.sha256(raw).hexdigest()))
    return paths

REAL_CASES = [
    ("definition", "Where is `verifiedSourcePath` defined?", "SYMBOL_LOCATION", "graph", "find_definition", "extension/src/sourceNavigation.ts"),
    ("callers", "Who calls `verifiedSourcePath`?", "DIRECT_CALLERS", "graph", "find_callers", "extension/src/responsePreview.ts"),
    ("callees", "What does `verifiedSourcePath` call?", "DIRECT_CALLEES", "graph", "find_callees", "extension/src/sourceNavigation.ts"),
    ("contract", "Contract of `verifiedSourcePath`", "FUNCTION_CONTRACT", "rules", "function_contract", "extension/src/sourceNavigation.ts"),
    ("impact", "Impact of changing `verifiedSourcePath`", "CHANGE_IMPACT", "rules", "change_impact", "extension/src/sourceNavigation.ts"),
    ("tests", "What tests cover `verifiedSourcePath`?", "TEST_DISCOVERY", "rules", "reference_test_analysis", None),
    ("module", "Overview of extension/src/sourceNavigation.ts", "MODULE_OVERVIEW", "rules", "module_overview", "extension/src/sourceNavigation.ts"),
    ("feature", "Find code that verifies source file hashes before opening citations", "FEATURE_DISCOVERY", "semantic", "hybrid_search", "extension/src/sourceNavigation.ts"),
    ("feature_docs", "Find code that renders documentation markdown", "FEATURE_DISCOVERY", "semantic", "hybrid_search", "extension/src/extension.ts"),
    ("function", "Explain `verifiedSourcePath`", "FUNCTION_EXPLANATION", "graph", "find_definition", "extension/src/sourceNavigation.ts"),
    ("workflow", "Explain the workflow from `openVerifiedPreviewSource` to `verifiedSourcePath`", "WORKFLOW_EXPLANATION", "graph", "find_paths", "extension/src/sourceNavigation.ts"),
    ("behavior", "Why might `verifiedSourcePath` fail?", "BEHAVIOR_INVESTIGATION", "rules", "function_contract", "extension/src/sourceNavigation.ts"),
    ("comparison", "Compare `escapeDisplay` and `renderResponseBody`", "CODE_COMPARISON", "graph", "find_definition", "extension/src/responsePresentation.ts"),
    ("compound", "Where is `verifiedSourcePath` defined and explain `verifiedSourcePath`", "SYMBOL_LOCATION", "graph", "find_definition", "extension/src/sourceNavigation.ts"),
    ("ambiguous", "Where is `run` defined?", "SYMBOL_LOCATION", None, None, None),
    ("missing", "Where is `phase10Nonexistent` defined?", "SYMBOL_LOCATION", None, None, None),
    ("unsupported_statistics", "How many files are in this repository?", "REPOSITORY_STATISTICS", None, None, None),
]

def real_evaluation(real_local=False):
    from fastembed import TextEmbedding
    # Fail/skip rather than downloading a new model. Existing public cache only.
    try:
        model = TextEmbedding(model_name=config.EMBEDDING_MODEL_NAME, cache_dir=str(Path(config.DEFAULT_DATA_DIR) / "models"), local_files_only=True, threads=2)
        list(model.embed(["warm existing cached model"]))
    except Exception as error:
        return {"evaluation": "Real CodeMind source copies", "state": "skipped", "reason": "cached_embedding_model_unavailable", "error_type": type(error).__name__}
    results = []
    for mode in ("small", "target-sized", "multilingual-diagnostic"):
        with tempfile.TemporaryDirectory(prefix="cm-phase10-real-") as temporary, ExitStack() as stack:
            root, data = Path(temporary) / "repo", Path(temporary) / "private"; root.mkdir()
            paths = copy_sources(root, mode)
            repo = config.make_repo_id(str(root)); store = SnapshotStore(root, repo, data)
            client = FaissClient(data / "faiss"); stack.callback(client.close)
            for module in ("api", "indexer"):
                stack.enter_context(patch(module + ".get_qdrant_client", return_value=client))
                stack.enter_context(patch(module + ".embed_texts", side_effect=lambda texts: [vector.tolist() for vector in model.embed(texts, batch_size=64)]))
            stack.enter_context(patch("indexer.get_embedding_model", return_value=model))
            stack.enter_context(patch.object(api, "DEFAULT_DATA_DIR", str(data)))
            stack.enter_context(patch.dict(api._REGISTERED_REPOS, {repo: {"rootPath": str(root)}}))
            before = perf_counter()
            try:
                store.publish(client=client, collection=config.COLLECTION_NAME, index_directory=index_directory, build_graph=build_graph)
            except RuntimeError as error:
                results.append({"repository": mode, "state": "blocked", "copied_files": len(paths), "copied_bytes": sum(item[1] for item in paths),
                    "reason": "Existing knowledge-base graph snapshot publication failed; no hybrid answers evaluated or legacy graph substituted.",
                    "failure": str(error)[-3500:], "indexing_ms": (perf_counter() - before) * 1000})
                continue
            indexing_ms = (perf_counter() - before) * 1000
            channels = existing_api_channels(api, config.EMBEDDING_MODEL_NAME)
            cases, citation_checks, valid_citations = [], 0, 0
            for label, question, intent, engine, operation, target in REAL_CASES:
                if mode == "small" and label not in ("definition", "callees", "contract", "impact", "tests", "module", "feature", "function", "behavior", "missing", "unsupported_statistics"): continue
                fake = FakeLocal(); run, measured = sample(store, repo, question, channels, fake)
                view = run.response.presentation
                paths_returned = [source.citation.source.file_path for source in view.sources.values()] if view else []
                citation_checks += measured["citation_checks"]; valid_citations += measured["valid_citations"]
                classified = run.original.requirements[0].intent if run.original and run.original.requirements else None
                cases.append({"case": label, "question": question, "expected_intent": intent, "actual_intent": classified,
                    "intent_correct": classified == intent, "expected_engine": engine, "expected_operation": operation,
                    "operation_correct": any(item.engine == engine and item.operation == operation for item in run.response.operations) if engine else not run.response.operations,
                    "expected_source": target, "expected_source_present": target in paths_returned if target else None,
                    "coverage": [item.state for item in view.requirements] if view else [],
                    "sources": sorted(set(paths_returned)), "operations": [item.model_dump() for item in run.response.operations],
                    "generation": [item.model_dump() for item in run.response.generations], **measured})
            database = store.data_for(store.active()) / repo / "metadata.db"
            with sqlite3.connect(database) as db:
                symbols = db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
                unresolved = db.execute("SELECT COUNT(*) FROM relationships WHERE resolution='unresolved'").fetchone()[0]
                ambiguous = db.execute("SELECT name,COUNT(*) FROM symbols GROUP BY name HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC LIMIT 5").fetchall()
                languages = db.execute("SELECT language,COUNT(*) FROM chunk_context GROUP BY language").fetchall()
            real_smoke = {"state": "not_requested"}
            if real_local and mode == "small":
                real_smoke = local_smoke(store, repo, channels)
            results.append({"repository": mode, "state": "completed", "source_origin": "Isolated copy of actual current CodeMind text; dependencies/generated files/artifacts/sensitive files excluded. Small corpus selects sourceNavigation plus extension docs/config; diagnostic corpus excludes other JS/TS to isolate the recorded graph-builder failure.",
                "copied_files": len(paths), "copied_bytes": sum(item[1] for item in paths), "source_manifest_digest": hashlib.sha256(json.dumps(paths, sort_keys=True).encode()).hexdigest(),
                "documentation_config_files": sum(Path(item[0]).suffix in (".md", ".json", ".yml", ".yaml", ".toml") for item in paths),
                "languages": languages, "symbols": symbols, "unresolved_relationships": unresolved, "ambiguous_symbol_examples": ambiguous,
                "generation_id": store.active()["id"], "repository_id": repo, "indexing_ms": indexing_ms,
                "questions": len(cases), "intent_correct": sum(item["intent_correct"] for item in cases),
                "operation_correct": sum(item["operation_correct"] for item in cases), "citation_checks": citation_checks, "valid_citations": valid_citations,
                "cases": cases, "real_local_smoke": real_smoke})
    return {"evaluation": "Real-source corpora, existing cached BGE/FAISS, mock explanations separated from real-local smoke",
        "state": "partial" if any(item["state"] == "blocked" for item in results) else "completed", "repositories": results,
        "human_reviewed_factual_accuracy": "Not measured: no independent human reviewer. Citation validity is not prose factuality.",
        "limitations": ["Related corpora from one project, not a general external-repository benchmark.", "Original target-sized snapshot may fail in the existing graph builder; diagnostic exclusions are NOT a fix or a target-size pass.", "Temporary indexes are built only for evaluation; original user indexes remain unchanged.", "One sample per labeled real query; no real-corpus p95 claim.", "Mock model answers test transport/grounding compatibility, not semantic explanation accuracy."]}

def local_smoke(store, repo, channels):
    from urllib.request import Request, build_opener, ProxyHandler
    from query_system.local_ollama import LocalOllama
    model = os.environ.get("CODEMIND_LOCAL_MODEL", "llama3:latest")
    try:
        with build_opener(ProxyHandler({})).open(Request("http://127.0.0.1:11434/api/tags"), timeout=2) as reply:
            installed = json.loads(reply.read(65536))["models"]
        configured = next(item for item in installed if item.get("name") == model and item.get("size", 0) > 0 and "cloud" not in model.lower())
    except Exception:
        return {"state": "skipped", "reason": "configured_local_completion_model_unavailable", "model": model}
    run, measured = sample(store, repo, "Explain `verifiedSourcePath`", channels, LocalOllama(answers))
    return {"state": "completed", "model": model, "digest": configured.get("digest"), "generation": [item.model_dump() for item in run.response.generations],
        "claims": [{"text": item.text, "classification": item.classification, "authoritative": item.authoritative} for item in run.response.presentation.items.values()] if run.response.presentation else [],
        "factual_review": "No independent human review; model interpretation explicitly non-authoritative.", **measured}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--real-repositories", action="store_true")
    parser.add_argument("--real-local", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1: parser.error("Positive repetitions required")
    print(json.dumps(real_evaluation(args.real_local) if args.real_repositories else fixture_benchmark(args.repeats), indent=2))

if __name__ == "__main__": main()
