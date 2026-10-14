"""Bounded real-corpus qualification using disposable, isolated indexes only.

Downloads public inert source files, never installs/runs repository code, and
never publishes to existing user data. Cached embeddings and local Ollama only.
All corpus/index directories are removed; JSON is returned on stdout for review.
"""
import argparse
import asyncio
from contextlib import ExitStack, closing
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import resource
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from time import perf_counter
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "ai-engine"))
import api
import answers
import config
from fastembed import TextEmbedding
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.identity import repository_id
from knowledge_base.metadata import trace
from knowledge_base.scanner import SECRET_MARKERS, SENSITIVE_NAMES, SENSITIVE_SUFFIXES
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from knowledge_base.verify_resolver_s1 import copy_text, graph_checks
from query_system.benchmark_execution import latency
from query_system.contracts import Evidence, QueryRequest
from query_system.executor import MultiEngineExecutor
from query_system.generation_contracts import GenerationBudget
from query_system.generation_safety import PresentationSafety
from query_system.hybrid_contracts import HybridLimits, HybridQueryRequest
from query_system.local_ollama import LocalOllama
from query_system.orchestration import run_hybrid
from query_system.semantic_engine import existing_api_channels

PINS = {
    "next-typescript": ("vercel/next.js", "2251f43886faac9da45e585a77c07fa2b881f410"),
    "express": ("expressjs/express", "9a3f7ff4120d7920a2d13809ff5ae78648c8a3d6"),
    "flask": ("pallets/flask", "d086db856be187255b8ec61ef409357393020f32"),
}
OPENER = build_opener(ProxyHandler({}))


def progress(event, **values):
    print(json.dumps({"event": event, **values}), file=sys.stderr, flush=True)


def fetch(url, maximum=16 * 1024 * 1024):
    request = Request(url, headers={"User-Agent": "CodeMind-local-qualification", "Accept": "application/vnd.github+json"})
    with OPENER.open(request, timeout=30) as reply:
        raw = reply.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Public source download exceeds its declared budget")
    return raw


def save_inert(root, relative, raw):
    path = PurePosixPath(relative)
    if (path.is_absolute() or ".." in path.parts or any(part in {"node_modules", ".git", "dist", "build", ".next"} for part in path.parts)
            or path.name in SENSITIVE_NAMES or path.name.startswith(".env") or path.name.endswith(SENSITIVE_SUFFIXES)
            or path.suffix not in config.CODE_EXTENSIONS or len(raw) > 1000000
            or b"\0" in raw or any(marker.search(raw) for marker in SECRET_MARKERS)):
        return None
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    target = root / str(path); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return (str(path), len(raw), hashlib.sha256(raw).hexdigest())


def public_corpus(label, root):
    project, commit = PINS[label]; files = []
    if label == "next-typescript":
        prefix = "examples/with-typescript"
        queue = [prefix]
        while queue:
            directory = queue.pop(0)
            entries = json.loads(fetch(f"https://api.github.com/repos/{project}/contents/{directory}?ref={commit}", 1000000))
            if not isinstance(entries, list): raise ValueError("Expected public source directory")
            for entry in entries:
                path = entry["path"]
                if not path.startswith(prefix + "/"): raise ValueError("Foreign public source path")
                if entry["type"] == "dir": queue.append(path)
                elif entry["type"] == "file" and entry["size"] <= 1000000:
                    raw = fetch(f"https://raw.githubusercontent.com/{project}/{commit}/{path}", 1000000)
                    saved = save_inert(root, path[len(prefix)+1:], raw)
                    if saved: files.append(saved)
            if len(files) > 100 or len(queue) > 100: raise ValueError("Public example inventory budget")
    else:
        archive = fetch(f"https://codeload.github.com/{project}/tar.gz/{commit}")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            for member in source:
                if not member.isfile() or member.size > 1000000: continue
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2: continue
                relative = PurePosixPath(*parts[1:]).as_posix()
                if label == "express" and not (relative.startswith(("lib/", "test/")) or "/" not in relative): continue
                if label == "flask" and not (relative.startswith(("src/", "tests/")) or "/" not in relative): continue
                saved = save_inert(root, relative, source.extractfile(member).read(1000001))
                if saved: files.append(saved)
                if len(files) > 400 or sum(item[1] for item in files) > 3 * 1024 * 1024: raise ValueError("Public corpus inventory budget")
    return {"source": f"https://github.com/{project}/tree/{commit}", "commit": commit, "files": files}


def inventory_digest(root, files):
    return hashlib.sha256(json.dumps([(path, hashlib.sha256((root / path).read_bytes()).hexdigest()) for path, _, _ in files], sort_keys=True).encode()).hexdigest()


def verify_graph(store):
    manifest = store.active(); database = store.data_for(manifest) / store.repo_id / "metadata.db"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        snapshot = db.execute("SELECT statistics_json,diagnostics_json,typescript_version FROM graph_snapshots WHERE repository_id=?", (store.repo_id,)).fetchone()
        chunks = [row[0] for row in db.execute("SELECT id FROM code_chunks WHERE repository_id=?", (store.repo_id,))]
        names = db.execute("SELECT qualified_name,relative_path FROM symbols WHERE repository_id=? AND name NOT LIKE '<anonymous:%' ORDER BY relative_path,start_line LIMIT 30", (store.repo_id,)).fetchall()
    # Reuse the S1 canonical verifier, plus every chunk's source trace.
    result = graph_checks({"database": str(database), "repository_id": store.repo_id, "root_path": str(store.root),
        "graph": json.loads(snapshot[0]), "semantic_diagnostics": json.loads(snapshot[1]), "typescript_version": snapshot[2], "parsing": {}})
    for identifier in chunks:
        info = trace(database, store.repo_id, identifier)
        if hashlib.sha256((store.root / info["path"]).read_bytes()).hexdigest() != info["sourceHash"]: raise ValueError("Invalid chunk source hash")
    result.update(chunk_provenance_checked=len(chunks), chunk_provenance_valid=len(chunks), sample_symbols=names)
    return result


def query_cases(label):
    if label == "codemind":
        return [("definition", "Where is verifiedSourcePath defined?"), ("callers", "Who calls verifiedSourcePath?"),
            ("target_list", "Who calls verifiedSourcePath and verifiedPreviewSource?"),
            ("compound", "Where is verifiedSourcePath defined and contract of verifiedPreviewSource"),
            ("comparison_tests", "Compare verifiedSourcePath and verifiedPreviewSource and find tests for both"),
            ("ambiguous", "Where is run defined?"), ("missing", "Where is stabilizationNonexistent defined?"),
            ("unsupported", "How many files are in this repository?"),
            ("semantic", "Find code that verifies source file hashes before opening citations")]
    if label == "flask":
        return [("unsupported_python_graph", "Where is Flask in src/flask/app.py defined?"),
            ("semantic", "Find code that dispatches HTTP requests to registered view functions")]
    if label == "express":
        return [("definition", "Where is createApplication in lib/express.js defined?"),
            ("module", "Overview of lib/application.js"), ("semantic", "Find code that sets up the application and its request router")]
    return [("module", "Overview of pages/index.tsx"), ("semantic", "Find code that renders a list of sample users with navigation links")]


def query_checks(store, channels, label, repeats):
    details = []; checked = valid = 0
    for name, question in query_cases(label):
        durations = []; last = None
        for _ in range(repeats):
            started = perf_counter()
            request = HybridQueryRequest(query=QueryRequest(repository_id=store.repo_id, question=question), allow_generation=False)
            last = asyncio.run(run_hybrid(store, request, channels, answers))
            durations.append((perf_counter() - started) * 1000)
            if last.package:
                with store.pin_view() as view:
                    from query_system.contracts import SnapshotContext
                    context = SnapshotContext.from_pinned_view(view, store.repo_id)
                    database = Path(view["data_dir"]) / store.repo_id / "metadata.db"
                    for record in last.package.evidence.values():
                        checked += 1
                        proof = record.proof
                        current = Evidence.from_trace(trace(database, store.repo_id, proof.evidence_id), context, evidence_type=proof.evidence_type, resolution=proof.resolution)
                        valid += int(current == proof)
        details.append({"case": name, "question": question, "status": last.response.status,
            "intents": last.original.intents if last.original else [],
            "targets": [[last.original.entities[i].name for i in row.entity_indices] for row in last.original.requirements] if last.original else [],
            "operations": [item.engine + "." + item.operation for item in last.response.operations],
            "generations": len(last.response.generations), "latency_ms": latency(durations), "samples": repeats,
            "response_bytes": len(last.response.model_dump_json().encode()),
            "source_paths": sorted({citation.source.file_path for citation in last.package.citations.values()}) if last.package else [],
            "requirement_states": [row.state for row in last.response.presentation.requirements] if last.response.presentation else [],
            "error": last.response.error.model_dump(mode="json") if last.response.error else None})
        progress("queries", corpus=label, case=name, status=last.response.status)
    return {"cases": details, "proofs_checked": checked, "proofs_valid": valid}


def incremental(store, client):
    def publish(): return store.publish(client=client, collection=config.COLLECTION_NAME, index_directory=index_directory, build_graph=build_graph)
    first = store.active()["id"]
    with patch("indexer.embed_texts", side_effect=AssertionError("No-op publication must not embed")):
        unchanged = publish()
    if not unchanged["unchanged"] or unchanged["chunks_embedded"] != 0 or store.active()["id"] != first: raise ValueError("No-op changed identity/vectors")
    files = sorted(store.active()["sources"]["files"])
    source = next((path for path in files if Path(path).suffix in {".tsx", ".ts", ".js", ".py"}), None)
    changed = None
    if source:
        file = store.root / source
        marker = "\n# Disposable incremental qualification\n" if file.suffix == ".py" else "\n// Disposable incremental qualification\n"
        file.write_bytes(file.read_bytes() + marker.encode())
        stale_rejected = not store.matches(first)
        started = perf_counter(); result = publish()
        changed = {"file": source, "stale_old_generation_rejected": stale_rejected, "ms": (perf_counter()-started)*1000,
            "chunks_embedded": result["chunks_embedded"], "chunks_reused": result.get("chunks_reused"), "generation_changed": store.active()["id"] != first}
    document = next((path for path in files if path.endswith(".md") and not path.endswith("README.md")), None)
    if document is None: document = next((path for path in files if path.endswith("README.md")), None)
    renamed = deleted = None
    if document:
        original = store.root / document; replacement = original.with_name(original.stem + ".qualification.md")
        original.rename(replacement); rename_result = publish()
        replacement_path = replacement.relative_to(store.root).as_posix()
        database = store.data_for(store.active()) / store.repo_id / "metadata.db"
        with closing(sqlite3.connect(database)) as db:
            old_chunks = db.execute("SELECT count(*) FROM code_chunks c JOIN files f ON f.id=c.file_id WHERE f.relative_path=?", (document,)).fetchone()[0]
        renamed = {"from": document, "to": replacement_path, "old_chunks_remaining": old_chunks, "chunks_embedded": rename_result["chunks_embedded"]}
        replacement.unlink(); delete_result = publish()
        database = store.data_for(store.active()) / store.repo_id / "metadata.db"
        with closing(sqlite3.connect(database)) as db:
            deleted_chunks = db.execute("SELECT count(*) FROM code_chunks c JOIN files f ON f.id=c.file_id WHERE f.relative_path=?", (replacement_path,)).fetchone()[0]
        deleted = {"path": replacement_path, "chunks_remaining": deleted_chunks, "chunks_embedded": delete_result["chunks_embedded"]}
        if old_chunks or deleted_chunks: raise ValueError("Renamed/deleted evidence survived")
    return {"unchanged_generation_reused": True, "unchanged_chunks_embedded": 0, "edit": changed, "rename": renamed, "delete": deleted}


def run(repeats=5, labels=None):
    if repeats < 5 or repeats > 10: raise ValueError("Use 5–10 qualification repetitions")
    results = {"evaluation": "Disposable real-source profiles; no user indexes modified; not a universal language/scale guarantee",
        "pins": PINS, "repeats": repeats, "profiles": [], "model": config.EMBEDDING_MODEL_NAME, "existing_knowledge_bases_unchanged_by_harness": True}
    model = TextEmbedding(model_name=config.EMBEDDING_MODEL_NAME, cache_dir=str(Path(config.DEFAULT_DATA_DIR) / "models"), local_files_only=True, threads=2)
    with tempfile.TemporaryDirectory(prefix="cm-final-qualification-") as temporary:
        base = Path(temporary)
        for label in labels or ["next-typescript", "express", "flask", "codemind"]:
            root = base / label; root.mkdir(); data = base / (label + "-private")
            progress("prepare", corpus=label)
            profile = {"corpus": label}
            try:
                corpus = {"source": "Inert copy of current CodeMind checkout", "files": copy_text(PROJECT, root)} if label == "codemind" else public_corpus(label, root)
                files = corpus.pop("files"); profile.update(corpus, copied_files=len(files), copied_bytes=sum(item[1] for item in files), original_copy_digest=inventory_digest(root, files))
                repo = repository_id(root); store = SnapshotStore(root, repo, data)
                with ExitStack() as guards:
                    client = FaissClient(data / "faiss"); guards.callback(client.close)
                    def embeds(texts): return [vector.tolist() for vector in model.embed(texts, batch_size=64)]
                    for module in ("api", "indexer"):
                        guards.enter_context(patch(module + ".get_qdrant_client", return_value=client))
                        guards.enter_context(patch(module + ".embed_texts", side_effect=embeds))
                    guards.enter_context(patch("indexer.get_embedding_model", return_value=model))
                    guards.enter_context(patch.object(api, "DEFAULT_DATA_DIR", str(data)))
                    guards.enter_context(patch.dict(api._REGISTERED_REPOS, {repo: {"rootPath": str(root)}}))
                    progress("publish", corpus=label, files=len(files), bytes=profile["copied_bytes"])
                    started = perf_counter(); published = store.publish(client=client, collection=config.COLLECTION_NAME, index_directory=index_directory, build_graph=build_graph)
                    profile.update(repository_id=repo, snapshot_id=store.active()["id"], indexing_ms=(perf_counter()-started)*1000,
                        chunks_embedded=published["chunks_embedded"], indexed_inventory_files=len(store.active()["sources"]["files"]))
                    profile["integrity"] = verify_graph(store)
                    profile["queries"] = query_checks(store, existing_api_channels(api,config.EMBEDDING_MODEL_NAME), label, repeats)
                    profile["incremental"] = incremental(store, client)
                    profile["post_incremental_integrity"] = verify_graph(store)
                    profile["state"] = "completed"
            except Exception as error:
                profile.update(state="failed", error_type=type(error).__name__, error=PresentationSafety(answers).text(str(error)))
                progress("failure", corpus=label, error_type=type(error).__name__, error=str(error))
            profile["python_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
            results["profiles"].append(profile)
            progress("profile_complete", corpus=label, state=profile["state"])
    results["temporary_corpora_and_indexes_cleaned"] = not base.exists()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--corpus", choices=[*PINS, "codemind"], action="append")
    args = parser.parse_args()
    print(json.dumps(run(args.repeats, args.corpus), indent=2))


if __name__ == "__main__": main()
