"""
indexer.py — Full-repo, incremental code indexer for CodeMind (Argus).

Recursively walks a target directory, chunks source files using language-aware
splitters, embeds the chunks via FastEmbed, and upserts them into Qdrant.
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
import bisect
import hashlib
import json
import logging
import re
import uuid
from typing import Callable

import networkx as nx
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, FilterSelector, Filter,
    FieldCondition, MatchValue, PayloadSchemaType, Range,
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
    get_qdrant_client,
    get_embedding_model,
    embed_texts,
    ensure_private_dir,
    make_repo_id,
    atomic_write_text,
)
import graph_builder

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

# Fixed namespace UUID for deterministic point ID generation.
_NAMESPACE_UUID = uuid.UUID("a3f1b2c4-d5e6-7890-abcd-ef1234567890")


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
    name = f"{repo_id}::{file_rel_path}::{chunk_index}"
    return str(uuid.uuid5(_NAMESPACE_UUID, name))


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
    """Walk *target_dir*, embed new/modified files, and upsert into Qdrant.

    Args:
        target_dir:   Root of the codebase to index.
        repo_id:      Unique repository identifier. Auto-derived from
                      target_dir if not supplied.
        data_dir:     Base directory for index state and graph files.
                      Defaults to ~/.codemind/data/.  State is stored at
                      <data_dir>/<repo_id>/index_state.json, keeping the
                      target repo clean (no VS Code file-watcher loops).
        collection:   Qdrant collection name.
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
        raise IndexingError(f"Cannot connect to Qdrant: {exc}") from exc

    collection_created = False
    if not client.collection_exists(collection):
        client.create_collection(collection_name=collection,
                                 vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE))
        collection_created = True
        _log(f"✅ Created Qdrant collection: '{collection}'")
    else:
        size = getattr(client.get_collection(collection).config.params.vectors, "size", None)
        if size is not None and size != VECTOR_SIZE:
            raise IndexingError(f"Collection '{collection}' has vector size {size}, expected {VECTOR_SIZE}. "
                                f"Delete it or pass --collection.")
    for field, schema in (("repo_id", PayloadSchemaType.KEYWORD), ("file_path", PayloadSchemaType.KEYWORD),
                          ("chunk_index", PayloadSchemaType.INTEGER)):
        try:
            client.create_payload_index(collection_name=collection, field_name=field, field_schema=schema)
        except Exception:
            pass                                    # index already exists

    # --- Warm up embedding model ---
    try:
        get_embedding_model()          # warm-up: raises IndexingError instead of a raw traceback
    except Exception as exc:
        raise IndexingError(f"Cannot load embedding model: {exc}") from exc

    # --- Discover files ---
    all_files = _discover_files(target_dir)
    _log(f"📂 Found {len(all_files)} indexable file(s) under {target_dir}")

    # --- Load previous state and check fingerprint ---
    fingerprint = f"{EMBEDDING_MODEL_NAME}|{chunk_size}|{chunk_overlap}|v2"
    prev_state = _load_state(state_path)
    previous_fingerprint = prev_state.pop(_FINGERPRINT_KEY, None)
    needs_full = full_reindex or collection_created or previous_fingerprint != fingerprint
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

    for abs_path in all_files:
        rel_path = _rel_posix(abs_path, target_dir)
        try:
            file_hash = _sha256(abs_path)
        except OSError as exc:
            _log(f"⚠️  Skipping {rel_path}: {exc}")
            continue
        new_state[rel_path] = file_hash

        if prev_state.get(rel_path) == file_hash:
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

    # --- Prune deleted files from Qdrant and graph ---
    for rel_path in deleted_rel_paths:
        _delete_file_points(client, collection, repo_id, rel_path)
        if kg is not None and rel_path.endswith(".py"):
            graph_builder.remove_file_from_graph(kg, rel_path)
    if deleted_rel_paths:
        _log(f"🗑️  Pruned vectors for {len(deleted_rel_paths)} deleted file(s)")

    # --- Process new / modified files ---
    total_points = graph_nodes_added = files_failed = 0

    for abs_path in files_to_index:
        rel_path = _rel_posix(abs_path, target_dir)
        language = get_language_for_file(abs_path)

        try:                                            # read ONCE as bytes; hash exactly what we index
            with open(abs_path, "rb") as fh:
                raw_bytes = fh.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            _log(f"⚠️  Skipping {rel_path}: {exc}")
            new_state.pop(rel_path, None)               # not recorded => retried next run
            files_failed += 1
            continue
        if len(raw_bytes) > MAX_FILE_BYTES or b"\x00" in raw_bytes[:8192]:
            _log(f"⚠️  Skipping {rel_path}: binary or larger than {MAX_FILE_BYTES} bytes")
            new_state.pop(rel_path, None)
            _delete_file_points(client, collection, repo_id, rel_path)
            if kg is not None and rel_path.endswith(".py"):
                graph_builder.remove_file_from_graph(kg, rel_path)
            continue

        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        new_state[rel_path] = file_hash
        raw_code = _redact_secrets(                     # Item 5 (keeps line count unchanged)
            raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n"))

        splitter = RecursiveCharacterTextSplitter.from_language(
            language=language, chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True)
        documents = splitter.create_documents([raw_code])
        if not documents:
            _delete_file_points(client, collection, repo_id, rel_path)
            if kg is not None and rel_path.endswith(".py"):
                graph_builder.remove_file_from_graph(kg, rel_path)
            continue

        line_starts = [0] + [m.end() for m in re.finditer(r"\n", raw_code)]
        vectors = embed_texts([d.page_content for d in documents])   # embed FIRST: a failure leaves old vectors intact
        total = len(documents)
        for b in range(0, total, EMBED_BATCH):
            points = []
            for idx in range(b, min(b + EMBED_BATCH, total)):
                doc = documents[idx]
                start_line = bisect.bisect_right(line_starts, max(doc.metadata.get("start_index", 0), 0))
                points.append(PointStruct(
                    id=_point_id(repo_id, rel_path, idx),
                    vector=vectors[idx],
                    payload={"repo_id": repo_id, "file_path": rel_path, "file_hash": file_hash,
                             "code_snippet": doc.page_content, "language": language.value,
                             "chunk_index": idx, "start_line": start_line,
                             "end_line": start_line + doc.page_content.count("\n")},
                ))
            client.upsert(collection_name=collection, points=points)
        # file shrank? drop stale tail chunks (ids are deterministic, so the upsert already overwrote the rest)
        client.delete(collection_name=collection, points_selector=FilterSelector(filter=Filter(must=[
            FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
            FieldCondition(key="file_path", match=MatchValue(value=rel_path)),
            FieldCondition(key="chunk_index", range=Range(gte=total)),
        ])))
        total_points += total

        if kg is not None and rel_path.endswith(".py"):
            try:
                graph_nodes_added += graph_builder.update_graph_for_file(kg, rel_path, abs_path)
            except Exception as exc:                    # one unparsable file must not abort the run
                _log(f"⚠️  Graph update failed for {rel_path}: {exc}")
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

    _log(f"\n🚀 Indexing complete — {total_points} point(s) upserted, "
         f"{skipped} file(s) unchanged, "
         f"{len(deleted_rel_paths)} file(s) pruned.")

    return {
        "files_indexed": len(files_to_index),
        "files_skipped": skipped,
        "files_pruned": len(deleted_rel_paths),
        "points_upserted": total_points,
        "graph_nodes": graph_nodes_added,
        "files_failed": files_failed,
    }


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMind (Argus) — Index a codebase into Qdrant for semantic search.",
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
        help=f"Qdrant collection name (default: {COLLECTION_NAME}).",
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
