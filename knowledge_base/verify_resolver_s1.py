"""Read-only S1 qualification over real sources and private temporary indexes.

No repository code is executed; only the existing inventory/parser/resolver/index
pipeline runs. Optional publication uses the existing cached embedding model.
"""
import argparse
import asyncio
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

from knowledge_base.graph import build_graph
from knowledge_base.metadata import audit, trace
from knowledge_base.scanner import EXCLUDED_DIRS, SECRET_MARKERS, SENSITIVE_NAMES, SENSITIVE_SUFFIXES
from knowledge_base.synchronization import observe_sources

PROJECT = Path(__file__).resolve().parents[1]
RESOLVER = PROJECT / "knowledge_base/semantic_resolver.cjs"


def copy_text(source, destination):
    copied = []
    for current, directories, files in os.walk(source, followlinks=False):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRS | {"benchmarks", "artifacts", "backend"} and not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or name in SENSITIVE_NAMES or name.startswith(".env") or name.endswith(SENSITIVE_SUFFIXES): continue
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".md", ".py", ".yaml", ".yml", ".toml"}: continue
            if path.stat().st_size > 1000000: continue
            raw = path.read_bytes()
            if b"\0" in raw[:8192] or any(marker.search(raw) for marker in SECRET_MARKERS): continue
            try: raw.decode("utf8")
            except UnicodeDecodeError: continue
            relative = path.relative_to(source)
            target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append((relative.as_posix(), len(raw), hashlib.sha256(raw).hexdigest()))
    return copied


def freeze_checkout(source, destination):
    """Honor the actual checkout's inventory/ignore rules, then freeze its bytes."""
    observed = observe_sources(source)
    for relative, digest in observed["files"].items():
        path = source / relative
        target = destination / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Checkout changed while freezing qualification input")
    if observe_sources(source) != observed: raise ValueError("Checkout changed while freezing qualification input")
    # Git evaluates copied .gitignore rules; no user history or hooks are copied.
    subprocess.run(["git", "init", "-q", str(destination)], check=True, capture_output=True)
    return {"files": len(observed["files"]), "observed_source_digest": observed["digest"],
            "source_git_revision": observed["revision"]}


def reproduce_legacy(root, database):
    """Replay only the pre-S1 owner calculation, with read-only node diagnostics."""
    source = RESOLVER.read_text()
    source, changes = re.subn(r"const owners = new Set\(declarations\.map\(\(decl\) => \{[\s\S]*?\n  \}\)\);",
        'const owners = new Set(declarations.map((decl) => `${decl.getSourceFile().fileName}:${decl.parent.pos}:${decl.parent.end}`));', source)
    if changes != 1: raise ValueError("Cannot identify the S1 owner calculation for controlled baseline replay")
    source = source.replace("return declaration(symbol);", """if (symbol) {
    const target = symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
    if (target.declarations?.some(decl => ts.isSourceFile(decl))) {
      console.error(JSON.stringify({usageFile: relative(node.getSourceFile().fileName),
        expression: node.getText(), symbol: symbol.getName(), declarations: target.declarations.map(decl =>
          ({kind: ts.SyntaxKind[decl.kind], file: relative(decl.getSourceFile().fileName), parent: decl.parent ? ts.SyntaxKind[decl.parent.kind] : null}))}));
    }
  }
  return declaration(symbol);""")
    with closing(sqlite3.connect(database)) as db:
        files = [row[0] for row in db.execute("SELECT relative_path FROM files WHERE status='included' AND parse_status='parsed' ORDER BY relative_path")]
        configs = [row[0] for row in db.execute("SELECT relative_path FROM files WHERE status='included' AND extension='.json' AND relative_path LIKE '%tsconfig%'")]
    reply = subprocess.run(["node", "-e", source], cwd=RESOLVER.parent,
        input=json.dumps({"root": str(root), "files": files, "configs": configs}),
        text=True, capture_output=True, timeout=180, check=False, env={**os.environ, "NODE_OPTIONS": ""})
    return {"exit_code": reply.returncode, "crash_reproduced": reply.returncode != 0 and "Cannot read properties of undefined (reading 'pos')" in reply.stderr,
            "diagnostic_and_stack": reply.stderr[-5000:]}


def graph_checks(built):
    database, repo = built["database"], built["repository_id"]
    with closing(sqlite3.connect(database)) as db:
        identifiers = [row[0] for row in db.execute("SELECT id FROM symbols UNION SELECT id FROM relationships")]
        language_counts = db.execute("SELECT language,COUNT(*) FROM files WHERE status='included' AND kind!='directory' GROUP BY language").fetchall()
        duplicates = db.execute("SELECT name,COUNT(*) FROM symbols GROUP BY name HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC LIMIT 8").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    valid = 0
    for identifier in identifiers:
        info = trace(database, repo, identifier)
        if info.get("path"):
            raw = (Path(built["root_path"]) / info["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != info["sourceHash"]: raise AssertionError("Current source differs from canonical proof")
        valid += 1
    integrity = audit(database)
    if foreign_keys or integrity["dangling_references"]: raise AssertionError("Graph metadata integrity failed")
    return {"graph": built["graph"], "typescript_version": built["typescript_version"],
        "semantic_diagnostics": built["semantic_diagnostics"], "parsing": built["parsing"],
        "provenance_checked": len(identifiers), "provenance_valid": valid, "foreign_key_errors": foreign_keys,
        "dangling_references": integrity["dangling_references"], "languages": language_counts,
        "ambiguous_symbol_names": duplicates}


def published_checks(root, data):
    sys.path.insert(0, str(PROJECT / "ai-engine"))
    import config
    from fastembed import TextEmbedding
    from indexer import index_directory
    from knowledge_base.synchronization import SnapshotStore
    from knowledge_base.vector_store import FaissClient
    from query_system.contracts import QueryRequest
    from query_system.evidence_aggregation import EvidenceAggregator
    from query_system.executor import MultiEngineExecutor
    model = TextEmbedding(model_name=config.EMBEDDING_MODEL_NAME, cache_dir=str(Path(config.DEFAULT_DATA_DIR) / "models"), local_files_only=True, threads=2)
    repo = config.make_repo_id(str(root)); store = SnapshotStore(root, repo, data)
    tick = perf_counter()
    with ExitStack() as guards:
        client = FaissClient(data / "faiss"); guards.callback(client.close)
        guards.enter_context(patch("indexer.get_qdrant_client", return_value=client))
        guards.enter_context(patch("config.get_embedding_model", return_value=model))
        guards.enter_context(patch("indexer.get_embedding_model", return_value=model))
        published = store.publish(client=client, collection=config.COLLECTION_NAME, index_directory=index_directory, build_graph=build_graph)
        active = store.active(); database = store.data_for(active) / repo / "metadata.db"
        with patch.object(store, "pin_view", wraps=store.pin_view) as pins, MultiEngineExecutor.pinned(store) as executor:
            query = executor.understand(QueryRequest(repository_id=repo, question="Who calls `verifiedSourcePath`?"))
            report = asyncio.run(executor.execute(executor.plan(query)))
            package = EvidenceAggregator.from_executor(executor).aggregate(report)
            if not package.findings or package.validation_status not in {"valid", "partial"}: raise AssertionError("Real-source caller evidence was not accepted")
            proofs = [record.proof for record in package.evidence.values()]
            if any(proof.repository_id != repo or proof.snapshot_id != active["id"] or proof.index_snapshot_id not in executor.context.ready_stage_ids for proof in proofs):
                raise AssertionError("Pinned repository/generation/stage mismatch")
            stage_ids = sorted({proof.index_snapshot_id for proof in proofs})
            callers = sorted({finding.original.source_name for finding in package.findings})
            pins_used = pins.call_count
        if pins_used != 1 or not store.matches(active["id"]): raise AssertionError("Publication/pin source consistency failed")
        with patch("indexer.embed_texts", side_effect=AssertionError("Unchanged snapshot must not re-embed")):
            unchanged = store.publish(client=client, collection=config.COLLECTION_NAME, index_directory=index_directory, build_graph=build_graph)
        if not unchanged["unchanged"] or unchanged["chunks_embedded"] != 0 or store.active()["id"] != active["id"]:
            raise AssertionError("Unchanged snapshot identity or embedding reuse failed")
        return {"state": "passed", "repository_id": repo, "generation_id": active["id"],
            "pin_count": pins_used, "ready_evidence_stage_ids": stage_ids, "accepted_evidence_records": len(proofs),
            "callers": callers, "publication_ms": (perf_counter() - tick) * 1000,
            "files_indexed": published["files_indexed"], "chunks_embedded": published["chunks_embedded"],
            "unchanged_republication": unchanged["unchanged"], "unchanged_chunks_embedded": unchanged["chunks_embedded"],
            "graph_provenance": graph_checks({**published["graph"], "database": str(database)})}


def run(publish=False):
    results = []
    with tempfile.TemporaryDirectory(prefix="cm-s1-real-") as temporary:
        base = Path(temporary)
        profiles = [("CodeMind-affected-checkout", PROJECT),
                    ("CodeMind-extension-source", PROJECT / "extension"),
                    ("vscode-test-electron-installed-release", PROJECT / "extension/node_modules/@vscode/test-electron/out")]
        for label, source in profiles:
            if label == "CodeMind-affected-checkout":
                root, copied = source, None
            else:
                root = base / label; root.mkdir()
                copied = copy_text(source, root)
            before = perf_counter()
            built = build_graph(root, data_dir=base / (label + "-graph"))
            checks = graph_checks(built)
            result = {"profile": label, "state": "passed", "source": "Actual affected checkout, read-only" if copied is None else "Isolated inert copy of real source text",
                "copied_files": len(copied) if copied is not None else None, "build_and_verification_ms": (perf_counter() - before) * 1000, **checks}
            if label == "CodeMind-affected-checkout":
                result["legacy_reproduction"] = reproduce_legacy(root, built["database"])
                if not result["legacy_reproduction"]["crash_reproduced"]: raise AssertionError("Original repository crash did not reproduce with pre-S1 ownership")
                if publish:
                    frozen = base / "frozen-checkout"; frozen.mkdir()
                    result["frozen_input"] = freeze_checkout(root, frozen)
                    result["published_snapshot"] = published_checks(frozen, base / "published")
            results.append(result)
    return {"task": "Stabilization S1", "state": "passed", "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
        "profiles": results, "embedding_scope": "Fresh temporary affected-repository publication only" if publish else "No embedding calls",
        "limitations": ["Installed vscode-test is a source-release corpus, not an independent Git checkout.", "Source/graph/provenance qualification does not measure semantic-search quality, LLM factuality or production performance."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published-snapshot", action="store_true", help="Also publish the affected checkout into temporary private FAISS using the existing cached model")
    args = parser.parse_args()
    print(json.dumps(run(args.published_snapshot), indent=2))


if __name__ == "__main__": main()
