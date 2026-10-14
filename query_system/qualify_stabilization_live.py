"""Read-only real-snapshot/Ollama qualification; never indexes or repairs caches."""
import argparse
import asyncio
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import sqlite3
from contextlib import closing
from time import perf_counter
from unittest.mock import patch

from query_system.qualify_stabilization import (
    PROJECT, api, answers, config, FaissClient, GenerationBudget, HybridLimits,
    HybridQueryRequest, LocalOllama, PresentationSafety, QueryRequest, SnapshotStore,
    existing_api_channels, progress, query_checks, repository_id, run_hybrid,
)
from query_system.benchmark_execution import latency


def vector_digest(client, collection):
    digest = hashlib.sha256()
    for row in client.db.execute("SELECT chunk_id,embedding,payload FROM vectors WHERE collection_name=? ORDER BY chunk_id", (collection,)):
        for value in row: digest.update(value if isinstance(value, bytes) else value.encode())
    return digest.hexdigest()


def graph_labels(store):
    """Complete direct-call labels for three inspected navigation implementations."""
    expected = {
        ("extension/src/sourceNavigation.ts", "verifiedSourcePath", "extension/src/sourceNavigation.ts", "isInside"),
        ("extension/src/responsePreview.ts", "verifiedPreviewSource", "extension/src/sourceNavigation.ts", "verifiedSourcePath"),
        ("extension/src/responsePreview.ts", "openVerifiedPreviewSource", "extension/src/responsePreview.ts", "verifiedPreviewSource"),
    }
    manifest = store.active(); database = store.data_for(manifest) / store.repo_id / "metadata.db"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        actual = {tuple(row) for row in db.execute("""SELECT a.relative_path,a.label,b.relative_path,b.label
            FROM graph_relationships e JOIN graph_nodes a ON a.id=e.source_id JOIN graph_nodes b ON b.id=e.target_id
            WHERE e.repository_id=? AND e.relationship='CALLS' AND e.resolution='resolved'
            AND a.relative_path IN ('extension/src/sourceNavigation.ts','extension/src/responsePreview.ts')
            AND a.label IN ('verifiedSourcePath','verifiedPreviewSource','openVerifiedPreviewSource')""", (store.repo_id,))}
    return {"label_scope": "All resolved repository-owned direct calls from the three named implementations; not external/dynamic calls",
        "expected": sorted(expected), "actual": sorted(actual), "precision": len(expected & actual)/len(actual) if actual else 0.,
        "recall": len(expected & actual)/len(expected), "missing": sorted(expected-actual), "unexpected": sorted(actual-expected)}


class CapturedLocal(LocalOllama):
    def __init__(self):
        super().__init__(answers)
        self.calls, self.last_output = [], None
        native, owner = self.opener, self
        class Recorder:
            def open(self, request, timeout):
                body = json.loads(request.data)
                item = {"request_bytes": len(request.data), "message_bytes": sum(len(m["content"].encode()) for m in body["messages"]),
                    "schema_bytes": len(json.dumps(body["format"]).encode()), "options": body["options"], "timeout_s": timeout}
                owner.calls.append(item)
                reply = native.open(request, timeout=timeout)
                class Reply:
                    parts = []
                    def __enter__(self): return self
                    def __exit__(self, *args):
                        reply.close()
                        raw = b"".join(self.parts); item["response_bytes"] = len(raw)
                        try:
                            value = json.loads(raw)
                            item.update({key: value.get(key) for key in ("load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration", "total_duration", "done_reason")})
                        except ValueError: pass
                    def geturl(self): return reply.geturl()
                    def read1(self, limit):
                        raw = reply.read1(limit); self.parts.append(raw); return raw
                return Reply()
        self.opener = Recorder()

    def generate(self, *args):
        output = super().generate(*args)
        self.last_output = PresentationSafety(answers).text(output)
        return output


def local_status(path):
    client = LocalOllama(answers)
    with client.opener.open(answers.Request("http://127.0.0.1:11434/api/" + path), timeout=3) as reply:
        return json.loads(reply.read(65537))


def model_checks(store, channels):
    model = os.environ.get("CODEMIND_LOCAL_MODEL", "llama3:latest")
    try:
        installed = next(item for item in local_status("tags")["models"] if item["name"] == model)
    except Exception as error:
        return {"state": "unavailable", "model": model, "error_type": type(error).__name__, "human_review": "pending"}
    cases = [("function", "Explain verifiedSourcePath", True),
        ("workflow", "Explain the workflow from openVerifiedPreviewSource to verifiedSourcePath", True),
        ("behavior", "Why might verifiedSourcePath fail?", True),
        ("comparison", "Compare verifiedSourcePath with isInside", True),
        ("partial", "Where is verifiedSourcePath defined and explain verifiedSourcePath", True),
        ("truncated", "Explain openResponsePreview", False),
        ("missing", "Explain stabilizationNonexistent", False),
        ("stale", "Explain verifiedSourcePath", False)]
    details = []
    for name, question, positive in cases:
        resident = [item.get("name") for item in local_status("ps").get("models", [])]
        client = CapturedLocal()
        query = QueryRequest(repository_id=store.repo_id, question=question, snapshot_id="f" * 32 if name == "stale" else None)
        budget = GenerationBudget(max_source_bytes=400) if name == "truncated" else GenerationBudget()
        tick = perf_counter()
        run = asyncio.run(run_hybrid(store, HybridQueryRequest(query=query, limits=HybridLimits(generation=budget)), channels, answers, client=client))
        item = {"case": name, "question": question, "positive": positive, "resident_before": model in resident,
            "status": run.response.status, "snapshot_id": run.response.snapshot_id, "wall_ms": (perf_counter()-tick)*1000,
            "stage_ms": run.response.timings.model_dump(), "transport": client.calls,
            "generations": [value.model_dump(mode="json") for value in run.response.generations],
            "output_redacted": client.last_output, "safe_explanation_claims": [], "review_sources": [],
            "citations_valid": all(run.package.citations.get(reference) == citation for _, result in run.generations for reference, citation in result.citations.items()) if run.package else None}
        for _, generation in run.generations:
            item["safe_explanation_claims"].extend(claim.model_dump(mode="json") for section in generation.sections for claim in section.claims)
            if generation.context:
                item["review_sources"].extend(source.model_dump(mode="json") for source in generation.context.sources)
                item["context"] = {"input_bytes": generation.context.input_bytes, "source_bytes": generation.context.source_bytes,
                    "omissions": generation.context.omissions, "core_ready": generation.context.core_ready}
        details.append(PresentationSafety(answers).tree(item))
        progress("local_model", case=name, status=item["status"], model=[value["model_status"] for value in item["generations"]])
    positives = [item for item in details if item["positive"]]
    accepted = sum(any(value["model_status"] == "success" for value in item["generations"]) for item in positives)
    warm = [item["wall_ms"] for item in positives if item["resident_before"]]
    return {"state": "completed", "model": model, "digest": installed.get("digest"), "details": details,
        "accepted_positive_explanations": accepted, "positive_cases": len(positives), "acceptance_rate": accepted / len(positives),
        "warm_end_to_end_latency_ms": latency(warm) if warm else None,
        "cold_samples": [item["wall_ms"] for item in details if not item["resident_before"]],
        "human_review": {"state": "pending", "reviewer": None, "supported_claim_rate": None, "hallucination_rate": None,
            "note": "Citation membership and backend validation do not establish prose factuality."}}


def run(repeats=5, real_local=False):
    root = PROJECT; repo = repository_id(root); data = Path(config.DEFAULT_DATA_DIR)
    store = SnapshotStore(root, repo, data); view = store.view()
    if not view["fresh"]:
        return {"state": "unavailable", "error": view["error"], "snapshot_id": view["snapshotId"], "existing_data_unchanged_by_harness": True}
    if not (data / "faiss/vectors.sqlite3").is_file(): raise ValueError("Existing vector store required")
    from fastembed import TextEmbedding
    import faiss
    model = TextEmbedding(model_name=config.EMBEDDING_MODEL_NAME, cache_dir=str(data / "models"), local_files_only=True, threads=2)
    with ExitStack() as stack:
        client = FaissClient(data / "faiss"); client.db.execute("PRAGMA query_only=ON"); stack.callback(client.close)
        stack.enter_context(patch.object(faiss, "write_index", side_effect=AssertionError("Read-only qualification cannot repair vector caches")))
        stack.enter_context(patch.object(api, "get_qdrant_client", return_value=client))
        stack.enter_context(patch.object(config, "get_embedding_model", return_value=model))
        stack.enter_context(patch.dict(api._REGISTERED_REPOS, {repo: {"rootPath": str(root)}}))
        before = vector_digest(client, view["manifest"]["collection"])
        result = {"state": "completed", "repository_id": repo, "snapshot_id": view["snapshotId"],
            "snapshot_inventory_files": len(view["manifest"]["sources"]["files"]), "graph_relationship_labels": graph_labels(store),
            "queries": query_checks(store, existing_api_channels(api,config.EMBEDDING_MODEL_NAME), "codemind", repeats)}
        if real_local: result["ollama"] = model_checks(store, existing_api_channels(api,config.EMBEDDING_MODEL_NAME))
        result["stored_vectors_unchanged"] = before == vector_digest(client, view["manifest"]["collection"])
        result["same_generation_still_active"] = (store.active() or {}).get("id") == view["snapshotId"] and store.matches(view["snapshotId"])
        result["no_index_or_publication_requested"] = True
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-local", action="store_true")
    parser.add_argument("--repeats", type=int, default=5, choices=range(5, 11))
    args = parser.parse_args(); print(json.dumps(run(args.repeats, args.real_local), indent=2))


if __name__ == "__main__": main()
