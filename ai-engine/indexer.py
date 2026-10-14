"""
indexer.py — Full-repo, incremental code indexer for CodeMind (Argus).

Recursively walks a target directory, extracts source-aware semantic units,
embeds their structural context via FastEmbed, and persists FAISS vectors plus
SQLite metadata/FTS5. An explicit QDRANT_URL retains remote Qdrant compatibility.
Also builds a NetworkX knowledge graph of code structure (functions, classes,
call relationships) via AST parsing for structural/impact queries.

Incremental indexing:
  - On each run, computes SHA-256 hashes of all eligible files.
  - Compares against a saved state file (index_state.json) stored in a
    separate data directory (NOT inside the target repo).
  - Only re-embeds files that are new or modified; prunes deleted files.
  - Pass --full-reindex to force a clean re-index of every file.

Usage:
  python indexer.py --dir ../my-repo
  python indexer.py --dir ./sample_code --full-reindex
  python indexer.py --dir ./my-repo --no-graph
  python indexer.py --dir ./my-repo --data-dir /tmp/codemind_data
"""

import argparse
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Callable

import networkx as nx
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, FilterSelector, Filter,
    FieldCondition, MatchValue, PayloadSchemaType,
)

import os

from config import (
    COLLECTION_NAME,
    VECTOR_SIZE,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_DATA_DIR,
    EMBEDDING_MODEL_NAME,
    IGNORE_DIRS,
    INDEX_STATE_FILENAME,
    GRAPH_FILENAME,
    MAX_FILE_BYTES,
    EMBED_BATCH,
    REPO_ID_RE,
    get_language_for_file,
    get_index_language,
    get_qdrant_client,
    get_embedding_model,
    embed_texts,
    ensure_private_dir,
    make_repo_id,
    atomic_write_text,
)
import graph_builder

from knowledge_base.identity import chunk_id
from knowledge_base.metadata import SCHEMA_VERSION, SemanticIndex
from knowledge_base.chunking import CHUNKING_VERSION, chunk_source

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────

class IndexingError(Exception):
    """Raised when an indexing operation cannot proceed.

    Replaces former sys.exit(1) calls so callers (such as the FastAPI server)
    can catch and surface the error cleanly.
    """


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _sha256(file_path: str) -> str:
    """Return the hex SHA-256 digest of a file's contents."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


def _rel_posix(abs_path: str, root: str) -> str:
    """Return a forward-slash relative path so Windows and POSIX produce identical keys."""
    return os.path.relpath(abs_path, root).replace(os.sep, "/")


def _point_id(repo_id: str, file_rel_path: str, chunk_index: int) -> str:
    """Generate a deterministic UUID-string for a (repo, file, chunk_index) triple.

    Including repo_id prevents collisions when multiple repos share a
    Qdrant collection — e.g. src/app.py in Repo A won't overwrite
    src/app.py in Repo B.
    """
    return chunk_id(repo_id, file_rel_path, chunk_index)


def _data_dir_for_repo(repo_id: str, base_data_dir: str) -> str:
    """Return the per-repo data directory, creating it if needed."""
    ensure_private_dir(base_data_dir)
    return ensure_private_dir(os.path.join(base_data_dir, repo_id))


def _delete_file_points(client, collection: str, repo_id: str, rel_path: str) -> None:
    client.delete(collection_name=collection, points_selector=FilterSelector(filter=Filter(must=[
        FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
        FieldCondition(key="file_path", match=MatchValue(value=rel_path)),
    ])))


_FINGERPRINT_KEY = "__index_fingerprint__"


def _load_state(state_path: str) -> dict[str, str]:
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Index state unreadable; treating as empty: %s", state_path)
        return {}


def _save_state(state_path: str, state: dict[str, str]) -> None:
    atomic_write_text(state_path, json.dumps(state, indent=2, sort_keys=True))


def _discover_files(root_dir: str) -> list[str]:
    """Recursively find all indexable source files under *root_dir*.

    Returns a list of absolute paths.  Skips directories listed in
    IGNORE_DIRS and files whose extension isn't in CODE_EXTENSIONS.
    Rejects symlinks pointing outside the repo (prevents secret exfiltration).
    """
    root_real = os.path.realpath(root_dir)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.endswith(".egg-info")
                       and not os.path.islink(os.path.join(dirpath, d))]
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if get_language_for_file(full_path) is None:
                continue
            try:
                real = os.path.realpath(full_path)
                if real != root_real and not real.startswith(root_real + os.sep):
                    continue                        # symlink pointing outside the repo
                size = os.path.getsize(full_path)   # raises OSError on broken symlinks
            except OSError:
                continue
            if 0 < size <= MAX_FILE_BYTES:
                found.append(full_path)
    return found


# ─────────────────────────────────────────────────────────────
# Secret redaction (Item 5)
# ─────────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
]
_ASSIGNED_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|secret(?:[_-]?key)?|token|passw(?:or)?d)\b\s*[:=]\s*)(['\"])[^'\"\n]{8,}\2")


def _redact_secrets(text: str) -> str:
    """Mask likely secrets but keep the number of lines unchanged so start_line/end_line stay correct."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: "[REDACTED]" + "\n" * m.group(0).count("\n"), text)
    return _ASSIGNED_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]{m.group(2)}", text)


# ─────────────────────────────────────────────────────────────
# Core indexing logic
# ─────────────────────────────────────────────────────────────

def index_directory(
    target_dir: str,
    *,
    repo_id: str | None = None,
    data_dir: str | None = None,
    collection: str = COLLECTION_NAME,
    full_reindex: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    build_graph: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    canonical_root = str(Path(target_dir).expanduser().resolve())
    if not os.path.isdir(canonical_root):
        raise IndexingError(f"Target directory does not exist: {target_dir}")
    repo_id = repo_id or make_repo_id(canonical_root)
    if not REPO_ID_RE.fullmatch(repo_id):
        raise IndexingError('Invalid repo_id')
    with SemanticIndex(canonical_root, repo_id, data_dir or DEFAULT_DATA_DIR) as metadata:
        return _index_directory(canonical_root, repo_id=repo_id, data_dir=data_dir,
            collection=collection, full_reindex=full_reindex, chunk_size=chunk_size,
            chunk_overlap=chunk_overlap, build_graph=build_graph, on_progress=on_progress, metadata=metadata)


def _index_directory(
    target_dir: str, *, repo_id: str, data_dir: str | None,
    collection: str, full_reindex: bool, chunk_size: int, chunk_overlap: int,
    build_graph: bool, on_progress: Callable[[str], None] | None, metadata: SemanticIndex,
) -> dict:
    """Walk *target_dir*, embed new/modified files, and update the vector store.

    Args:
        target_dir:   Root of the codebase to index.
        repo_id:      Unique repository identifier. Auto-derived from
                      target_dir if not supplied.
        data_dir:     Base directory for index state and graph files.
                      Defaults to ~/.codemind/data/.  State is stored at
                      <data_dir>/<repo_id>/index_state.json, keeping the
                      target repo clean (no VS Code file-watcher loops).
        collection:   Vector collection name.
        full_reindex: When True, ignores saved state and re-indexes every file.
        chunk_size:   Maximum chunk size in characters.
        chunk_overlap: Overlap between adjacent chunks.
        build_graph:  Whether to build the NetworkX knowledge graph.
        on_progress:  Optional callback invoked with status messages.

    Returns:
        Summary dict with keys: files_indexed, files_skipped,
        files_pruned, points_upserted, graph_nodes, files_failed.

    Raises:
        IndexingError: If target_dir does not exist or another
                       unrecoverable issue occurs.
    """

    def _log(msg: str) -> None:
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    target_dir = os.path.abspath(target_dir)
    if not os.path.isdir(target_dir):
        raise IndexingError(f"Target directory does not exist: {target_dir}")

    # --- Resolve repo_id and data paths ---
    if repo_id is None:
        repo_id = make_repo_id(target_dir)

    if not REPO_ID_RE.fullmatch(repo_id):
        raise IndexingError("Invalid repo_id")

    base_data_dir = data_dir or DEFAULT_DATA_DIR
    repo_data_dir = _data_dir_for_repo(repo_id, base_data_dir)

    state_path = os.path.join(repo_data_dir, INDEX_STATE_FILENAME)
    graph_path = os.path.join(repo_data_dir, GRAPH_FILENAME)

    # --- Clients ---
    try:
        client = get_qdrant_client()
        client.get_collections()                    # first real round-trip; the constructor never connects
    except Exception as exc:
        raise IndexingError(f"Cannot open vector store: {exc}") from exc

    collection_created = False
    if not client.collection_exists(collection):
        client.create_collection(collection_name=collection,
                                 vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE))
        collection_created = True
        _log(f"✅ Created {getattr(client,'backend','qdrant')} collection: '{collection}'")
    else:
        size = getattr(client.get_collection(collection).config.params.vectors, "size", None)
        if size is not None and size != VECTOR_SIZE:
            raise IndexingError(f"Collection '{collection}' has vector size {size}, expected {VECTOR_SIZE}. "
                                f"Delete it or pass --collection.")
    payload_indexes = () if getattr(client, "is_embedded", False) else (
        ("repo_id", PayloadSchemaType.KEYWORD), ("file_path", PayloadSchemaType.KEYWORD),
        ("chunk_index", PayloadSchemaType.INTEGER))
    for field, schema in payload_indexes:
        try:
            client.create_payload_index(collection_name=collection, field_name=field, field_schema=schema)
        except Exception:
            pass                                    # index already exists

    # --- Discover files ---
    all_files = [p for p in _discover_files(target_dir) if _rel_posix(p,target_dir) in metadata.entries]
    metadata.prune([_rel_posix(p,target_dir) for p in all_files])
    _log(f"📂 Found {len(all_files)} indexable file(s) under {target_dir}")

    # --- Load previous state and check fingerprint ---
    fingerprint = f"{EMBEDDING_MODEL_NAME}|{chunk_size}|{chunk_overlap}|metadata-v{SCHEMA_VERSION}|chunks-v{CHUNKING_VERSION}|{getattr(client,'backend','qdrant')}"
    prev_state = _load_state(state_path)
    previous_fingerprint = prev_state.pop(_FINGERPRINT_KEY, None)
    needs_full = full_reindex or collection_created or previous_fingerprint != fingerprint
    if not needs_full and prev_state:
        # State may describe a different/cleared store (e.g. server -> embedded).
        count = client.count(collection, exact=True, count_filter=Filter(must=[
            FieldCondition(key="repo_id", match=MatchValue(value=repo_id))])).count
        needs_full = count == 0
    if needs_full:
        if not collection_created:                  # wipe this repo's vectors so deleted files cannot linger
            client.delete(collection_name=collection, points_selector=FilterSelector(filter=Filter(
                must=[FieldCondition(key="repo_id", match=MatchValue(value=repo_id))])))
        prev_state = {}

    kg = None
    if build_graph:
        kg = nx.DiGraph() if needs_full else graph_builder.load_graph(graph_path)

    new_state: dict[str, str] = {}

    # Classify files
    files_to_index: list[str] = []
    skipped = 0
    files_failed = 0

    for abs_path in all_files:
        rel_path = _rel_posix(abs_path, target_dir)
        try:
            file_hash = _sha256(abs_path)
        except OSError as exc:
            _log(f"⚠️  Skipping {rel_path}: {exc}")
            files_failed += 1
            with metadata.db:
                metadata.error(rel_path, type(exc).__name__)
            continue
        new_state[rel_path] = file_hash

        if prev_state.get(rel_path) == file_hash and metadata.has_chunks(rel_path,file_hash):
            skipped += 1  # unchanged
        else:
            files_to_index.append(abs_path)

    # Detect deletions (files in prev_state that are no longer on disk)
    deleted_rel_paths = set(prev_state.keys()) - set(new_state.keys())

    _log(
        f"   ↳ {len(files_to_index)} to index, "
        f"{skipped} unchanged (skipped), "
        f"{len(deleted_rel_paths)} deleted"
    )

    # --- Prune deleted files from the vector store and graph ---
    for rel_path in deleted_rel_paths:
        _delete_file_points(client, collection, repo_id, rel_path)
        if kg is not None and rel_path.endswith(".py"):
            graph_builder.remove_file_from_graph(kg, rel_path)
    if deleted_rel_paths:
        _log(f"🗑️  Pruned vectors for {len(deleted_rel_paths)} deleted file(s)")

    # --- Process new / modified files ---
    total_points = graph_nodes_added = chunks_embedded = chunks_reused = 0

    for abs_path in files_to_index:
        rel_path = _rel_posix(abs_path, target_dir)
        language = get_index_language(abs_path)

        try:                                            # read ONCE as bytes; hash exactly what we index
            with open(abs_path, "rb") as fh:
                raw_bytes = fh.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            _log(f"⚠️  Skipping {rel_path}: {exc}")
            new_state.pop(rel_path, None)               # not recorded => retried next run
            files_failed += 1
            with metadata.db:
                metadata.error(rel_path,type(exc).__name__)
            continue
        if len(raw_bytes) > MAX_FILE_BYTES or b"\x00" in raw_bytes[:8192]:
            _log(f"⚠️  Skipping {rel_path}: binary or larger than {MAX_FILE_BYTES} bytes")
            new_state.pop(rel_path, None)
            _delete_file_points(client, collection, repo_id, rel_path)
            metadata.publish(rel_path, [])
            if kg is not None and rel_path.endswith(".py"):
                graph_builder.remove_file_from_graph(kg, rel_path)
            continue

        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        new_state[rel_path] = file_hash
        raw_code = _redact_secrets(                     # Item 5 (keeps line count unchanged)
            raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n"))

        documents = chunk_source(raw_code,path=rel_path,repo_id=repo_id,language=language,
                                 max_chars=chunk_size,overlap=chunk_overlap)
        if not documents:
            _delete_file_points(client, collection, repo_id, rel_path)
            metadata.publish(rel_path, [])
            if kg is not None and rel_path.endswith(".py"):
                graph_builder.remove_file_from_graph(kg, rel_path)
            continue

        previous_points,next_offset = client.scroll(collection_name=collection,scroll_filter=Filter(must=[
            FieldCondition(key='repo_id',match=MatchValue(value=repo_id)),
            FieldCondition(key='file_path',match=MatchValue(value=rel_path))]),limit=1000,with_vectors=True)
        while next_offset is not None:
            page,next_offset = client.scroll(collection_name=collection,scroll_filter=Filter(must=[
                FieldCondition(key='repo_id',match=MatchValue(value=repo_id)),
                FieldCondition(key='file_path',match=MatchValue(value=rel_path))]),offset=next_offset,limit=1000,with_vectors=True)
            previous_points.extend(page)
        previous_ids = {str(point.id) for point in previous_points}
        reusable = {(point.payload or {}).get('embedding_hash'):point.vector for point in previous_points
                    if point.vector is not None and not needs_full}
        inputs = [doc.embedding_text(rel_path) for doc in documents]
        embedding_hashes = [hashlib.sha256(text.encode()).hexdigest() for text in inputs]
        changed = [i for i,digest in enumerate(embedding_hashes) if digest not in reusable]
        vectors = [reusable.get(digest) for digest in embedding_hashes]
        if changed:
            try:
                get_embedding_model()
            except Exception as exc:
                raise IndexingError(f'Cannot load embedding model: {exc}') from exc
            fresh = embed_texts([inputs[i] for i in changed])
            if len(fresh)!=len(changed): raise IndexingError('Embedding count differs from changed chunk count')
            for i,vector in zip(changed,fresh): vectors[i] = vector
        chunks_embedded += len(changed)
        chunks_reused += len(documents)-len(changed)
        chunks = [(doc.text,doc.start_line,doc.end_line) for doc in documents]
        records = metadata.records(rel_path,file_hash,chunks,collection,contexts=documents)
        total = len(documents)
        for b in range(0, total, EMBED_BATCH):
            points = []
            for idx in range(b, min(b + EMBED_BATCH, total)):
                doc = documents[idx]
                points.append(PointStruct(
                    id=doc.id,
                    vector=vectors[idx],
                    payload={"repo_id": repo_id, "file_path": rel_path, "file_hash": file_hash,
                             "code_snippet": doc.text, "language": language,
                             "chunk_index": idx, "start_line": doc.start_line,
                             "end_line": doc.end_line, "module": doc.module,
                             "symbol_kind": doc.symbol_kind, "symbol_name": doc.name,
                             "qualified_name": doc.qualified_name, "signature": doc.signature,
                             "comments": doc.comments, "chunk_kind": doc.kind,
                             "parent_symbol_key": doc.symbol_key,
                             "embedding_model": EMBEDDING_MODEL_NAME,
                             "embedding_hash": embedding_hashes[idx],
                             "chunk_id": records[idx][0], "file_id": records[idx][2],
                             "symbol_id": records[idx][3], "snapshot_id": metadata.snapshot,
                             "content_hash": records[idx][9], "schema_version": SCHEMA_VERSION,
                             "source_origin": "disk"},
                ))
            client.upsert(collection_name=collection, points=points)
        # Symbol-based IDs survive line shifts. Remove IDs for renamed/deleted
        # units as well as tails; ordinal-only pruning cannot do that.
        stale_ids = previous_ids-{doc.id for doc in documents}
        if stale_ids:
            client.delete(collection_name=collection,points_selector=list(stale_ids))
        metadata.publish(rel_path,records,contexts=documents,embedding_model=EMBEDDING_MODEL_NAME)
        total_points += total

        if kg is not None and rel_path.endswith(".py"):
            try:
                added = graph_builder.update_graph_for_file(kg, rel_path, abs_path)
                if not added:
                    raise IndexingError('Python graph parsing did not produce a module')
                graph_nodes_added += added
            except Exception as exc:                    # one unparsable file must not abort the run
                _log(f"⚠️  Graph update failed for {rel_path}: {exc}")
                files_failed += 1
                new_state.pop(rel_path, None)
                with metadata.db:
                    metadata.error(rel_path, type(exc).__name__)
        _log(f"   ✔ {rel_path}  ({total} chunks)")

    # --- Persist state and graph ---
    if kg is not None:
        graph_builder.save_graph(kg, graph_path)
        summary = graph_builder.get_graph_summary(kg)
        _log(f"\n🔗 Knowledge graph: {summary['total_nodes']} nodes, {summary['total_edges']} edges")
        for ntype, count in sorted(summary['nodes'].items()):
            _log(f"   {ntype}: {count}")

    new_state[_FINGERPRINT_KEY] = fingerprint
    _save_state(state_path, new_state)

    outcome = 'incomplete' if files_failed else 'complete'
    _log(f"\n🚀 Indexing {outcome} — {total_points} point(s) upserted, "
         f"{skipped} file(s) unchanged, "
         f"{len(deleted_rel_paths)} file(s) pruned.")

    return {
        "files_indexed": len(files_to_index),
        "files_skipped": skipped,
        "files_pruned": len(deleted_rel_paths),
        "points_upserted": total_points,
        "graph_nodes": graph_nodes_added,
        "files_failed": files_failed,
        "chunks_embedded": chunks_embedded, "chunks_reused": chunks_reused,
        "metadata_database": str(metadata.database), "snapshot_id": metadata.snapshot, "schema_version": SCHEMA_VERSION,
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMind (Argus) — Index a codebase for local semantic search.",
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Root directory of the codebase to index.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"Directory to store index state and graph files (default: {DEFAULT_DATA_DIR}/<repo_hash>/).",
    )
    parser.add_argument(
        "--repo-id",
        default=None,
        help="Explicit repo identifier. Defaults to a SHA-256 hash of the target directory.",
    )
    parser.add_argument(
        "--full-reindex",
        action="store_true",
        default=False,
        help="Ignore the saved state and re-index every file from scratch.",
    )
    parser.add_argument(
        "--collection",
        default=COLLECTION_NAME,
        help=f"Vector collection name (default: {COLLECTION_NAME}).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Maximum chunk size in characters (default: {DEFAULT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help=f"Overlap between adjacent chunks (default: {DEFAULT_CHUNK_OVERLAP}).",
    )
    parser.add_argument(
        "--no-graph",
        action="store_true",
        default=False,
        help="Skip building the knowledge graph (useful for non-Python repos).",
    )

    args = parser.parse_args()

    try:
        result = index_directory(
            target_dir=args.dir,
            repo_id=args.repo_id,
            data_dir=args.data_dir,
            collection=args.collection,
            full_reindex=args.full_reindex,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            build_graph=not args.no_graph,
            on_progress=lambda msg: print(msg),
        )
        print(f"\nSummary: {json.dumps(result, indent=2)}")
    except IndexingError as exc:
        print(f"❌ {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
