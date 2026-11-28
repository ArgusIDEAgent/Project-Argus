"""Benchmark the real repository pipeline using deterministic embeddings.

Run with the project's installed dependencies. Each run takes a source
checkout and creates a fresh fixture directory, then writes its JSON to stdout.

    .venv/bin/python knowledge_base/benchmark_repository.py /path/to/checkout /tmp/unique-fixture

Use the same script, file count and machine for both revisions. The embedding
stub isolates index, filesystem, metadata and FAISS costs from model inference.
"""
import argparse
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import platform
import re
import resource
import statistics
import subprocess
import sys
import time

import numpy as np


def embed(texts):
    vectors = []
    for text in texts:
        vector = np.zeros(384, dtype="float32")
        for token in re.findall(r"\w+", text.casefold()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            weight = 24 if re.fullmatch(r"featurekey\d+", token) else 1
            for shift in (0, 2, 4, 6):
                vector[int.from_bytes(digest[shift:shift+2], "big") % len(vector)] += weight
        vector[0] += 0.01
        vectors.append(vector.tolist())
    return vectors

def module_path(number):
    return Path(f"group_{number//30:04d}") / f"unit_{number:04d}.py"

def create_module(number):
    key = f"featurekey{number:04d}"
    return "\n\n".join(
        f'def {key}_{kind}(value):\n    """Apply {key} {kind} to a value."""\n    return value + {number + ordinal}\n'
        for ordinal, kind in enumerate(("lookup", "compute", "finish"))
    )

def timed(call):
    start = time.perf_counter()
    value = call()
    return value, (time.perf_counter() - start) * 1000

def summary(samples):
    return {"median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(sorted(samples)[int((len(samples)-1)*0.95)], 3),
            "sample_count": len(samples)}

def run(args):
    checkout = args.checkout.resolve()
    if args.files < 3:
        raise SystemExit("--files must be at least 3")
    args.work.mkdir()
    root = args.work.resolve() / "fixture"
    root.mkdir()
    os.environ.update(CODEMIND_DATA_DIR=str(root.parent / "runtime"),
                      CODEMIND_LOCAL_MODEL="", HF_HUB_OFFLINE="1", LITELLM_LOCAL_MODEL_COST_MAP="True")
    os.environ.pop("QDRANT_URL", None)
    sys.path[:0] = [str(checkout), str(checkout / "ai-engine")]

    from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue, PointStruct
    import api
    import indexer
    from knowledge_base.storage import RepositoryStorage
    from knowledge_base.vector_store import FaissClient

    logging.disable(logging.CRITICAL)

    indexer.get_embedding_model = lambda: object()
    indexer.embed_texts = api.embed_texts = embed
    api._SYNC_MANAGER = None

    for number in range(args.files):
        path = root / module_path(number)
        path.parent.mkdir(exist_ok=True)
        path.write_text(create_module(number))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                    "commit", "-qm", "Create benchmark fixture"], check=True)

    key = api.register_repo(api.RegisterRequest(rootPath=str(root)))["repository"]["id"]
    store = api.SnapshotStore(root, key)
    def publish():
        with RepositoryStorage(root).scope():
            return store.publish(client=api.get_qdrant_client(), collection=api.COLLECTION_NAME,
                                 index_directory=api.index_directory, build_graph=api.build_graph,
                                 on_progress=lambda _: None)

    print("index starting", file=sys.stderr, flush=True)
    initial, index_ms = timed(publish)
    print("index finished", round(index_ms), file=sys.stderr, flush=True)
    poll_parameters = {"verify": False} if "verify" in inspect.signature(api.live_state).parameters else {}
    polls = [timed(lambda: api.live_state(key, **poll_parameters))[1] for _ in range(14)]

    samples, results = [], []
    for n in np.linspace(0, args.files-1, 14, dtype=int).tolist():
        request = api.SearchRequest(repoId=key, query=f"Where is featurekey{n:04d} implemented?", retrieval="semantic")
        reply, sample = timed(lambda: api.search_code(request))
        expected = module_path(n).as_posix()
        results.append({"expected": expected, "paths": [row["path"] for row in reply["results"]]})
        samples.append(sample)

    edited = args.files // 2
    path = root / module_path(edited)
    path.write_text(path.read_text().replace(f"return value + {edited}", "return value + 700001", 1))
    (root / module_path(args.files - 1)).unlink()
    print("refresh starting", file=sys.stderr, flush=True)
    refresh, refresh_ms = timed(publish)
    print("refresh finished", round(refresh_ms), file=sys.stderr, flush=True)

    removed = module_path(args.files - 1).as_posix()
    manifest = store.active()
    metadata = store.data_for(manifest) / key / "metadata.db"
    with RepositoryStorage(root).scope():
        fresh = store.view()["fresh"]
        live_vectors = api.get_qdrant_client().count(manifest["collection"]).count
        pruned = api.get_qdrant_client().count(manifest["collection"], count_filter=Filter(must=[
            FieldCondition(key="file_path", match=MatchValue(value=removed))])).count == 0

    journal = FaissClient(root.parent / "vector")
    rng = np.random.default_rng(4021)
    vector_count = 20000
    vectors = rng.normal(size=(vector_count, 384)).astype("float32")
    journal.create_collection("vectors", {"size": 384})
    journal.upsert("vectors", [PointStruct(id=i, vector=vector.tolist(), payload={
        "repo_id": "vector-fixture", "file_path": f"unit/{i//4}", "language": "python"})
        for i, vector in enumerate(vectors)])
    common = Filter(must=[FieldCondition(key="repo_id", match=MatchValue(value="vector-fixture"))])
    rare = Filter(must=[FieldCondition(key="file_path", match=MatchAny(any=["unit/4", "unit/9"]))])
    vector_report = {}
    for name, query_filter in (("common", common), ("rare", rare)):
        observations = [timed(lambda vector=vector: journal.query_points("vectors", vector, query_filter, limit=5))
                        for vector in vectors[16:32]]
        vector_report[name] = {**summary([sample for _, sample in observations]),
                               "ids": [[point.id for point in result.points] for result, _ in observations]}
    journal.close()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "fixture": {"files": args.files, "initial_chunks": initial["chunk_count"],
                    "vector_fixture_count": vector_count, "embedding": "deterministic 384-dimensional token vectors",
                    "platform": platform.platform()},
        "status": summary(polls),
        "cold_semantic": {**summary(samples), "recall_at_5": sum(row["expected"] in row["paths"] for row in results)/len(results),
                          "queries": results},
        "initial_index_ms": round(index_ms, 3), "refresh_ms": round(refresh_ms, 3),
        "refresh": {**{field: refresh[field] for field in ("files_indexed", "files_pruned", "files_skipped", "chunks_embedded",
                     "chunks_reused", "chunk_count")}, "fresh": fresh, "vector_count": live_vectors, "pruned": pruned},
        "vector_queries": vector_report,
        "active_snapshot_bytes": sum(path.stat().st_size for path in metadata.parent.iterdir() if path.is_file()),
        "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
    }
    assert fresh and pruned and live_vectors == refresh["chunk_count"] == initial["chunk_count"] - 3
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("--files", type=int, default=1500)
    print(json.dumps(run(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
