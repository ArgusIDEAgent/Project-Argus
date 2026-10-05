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
import hashlib
import json
import logging
import networkx as nx
import os
import uuid
from typing import Callable

from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.models import Distance, VectorParams, PointStruct, FilterSelector, Filter, FieldCondition, MatchValue

from config import (
    COLLECTION_NAME,
    VECTOR_SIZE,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_DATA_DIR,
    IGNORE_DIRS,
    INDEX_STATE_FILENAME,
    GRAPH_FILENAME,
    get_language_for_file,
    get_qdrant_client,
    get_embedding_model,
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


def _make_repo_id(target_dir: str) -> str:
    """Derive a stable repo_id from the absolute target directory path."""
    return hashlib.sha256(os.path.abspath(target_dir).encode()).hexdigest()[:24]


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
    repo_data_dir = os.path.join(base_data_dir, repo_id)
    os.makedirs(repo_data_dir, exist_ok=True)
    return repo_data_dir


def _load_state(state_path: str) -> dict[str, str]:
    """Load the previous index state (path → hash map) from disk."""
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(state_path: str, state: dict[str, str]) -> None:
    """Persist the current index state to disk."""
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _discover_files(root_dir: str) -> list[str]:
    """Recursively find all indexable source files under *root_dir*.

    Returns a list of absolute paths.  Skips directories listed in
    IGNORE_DIRS and files whose extension isn't in CODE_EXTENSIONS.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Prune ignored directories *in-place* so os.walk won't descend.
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if get_language_for_file(full_path) is not None:
                found.append(full_path)
    return found


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
        files_pruned, points_upserted, graph_nodes.

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
        repo_id = _make_repo_id(target_dir)

    base_data_dir = data_dir or DEFAULT_DATA_DIR
    repo_data_dir = _data_dir_for_repo(repo_id, base_data_dir)

    state_path = os.path.join(repo_data_dir, INDEX_STATE_FILENAME)
    graph_path = os.path.join(repo_data_dir, GRAPH_FILENAME)

    # --- Load or create knowledge graph ---
    if build_graph:
        kg = graph_builder.load_graph(graph_path) if not full_reindex else nx.DiGraph()
    else:
        kg = None

    # --- Clients ---
    try:
        client = get_qdrant_client()
    except Exception as exc:
        raise IndexingError(f"Cannot connect to Qdrant: {exc}") from exc

    embedding_model = get_embedding_model()

    # --- Ensure collection exists ---
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        _log(f"✅ Created Qdrant collection: '{collection}'")

    # --- Discover files ---
    all_files = _discover_files(target_dir)
    _log(f"📂 Found {len(all_files)} indexable file(s) under {target_dir}")

    # --- Load previous state ---
    prev_state = {} if full_reindex else _load_state(state_path)
    new_state: dict[str, str] = {}

    # Classify files
    files_to_index: list[str] = []
    skipped = 0

    for abs_path in all_files:
        rel_path = os.path.relpath(abs_path, target_dir)
        file_hash = _sha256(abs_path)
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
        client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
                        FieldCondition(key="file_path", match=MatchValue(value=rel_path)),
                    ]
                )
            ),
        )
        if kg is not None and rel_path.endswith(".py"):
            graph_builder.remove_file_from_graph(kg, rel_path)
    if deleted_rel_paths:
        _log(f"🗑️  Pruned vectors for {len(deleted_rel_paths)} deleted file(s)")

    # --- Process new / modified files ---
    total_points = 0
    graph_nodes_added = 0

    for abs_path in files_to_index:
        rel_path = os.path.relpath(abs_path, target_dir)
        language = get_language_for_file(abs_path)

        # Read file
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                raw_code = f.read()
        except Exception as exc:
            _log(f"⚠️  Skipping {rel_path}: {exc}")
            continue

        # Delete any previous vectors for this file (handles modifications)
        # Scoped to repo_id to prevent cross-repo interference.
        client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
                        FieldCondition(key="file_path", match=MatchValue(value=rel_path)),
                    ]
                )
            ),
        )

        # Chunk
        if language is not None:
            splitter = RecursiveCharacterTextSplitter.from_language(
                language=language,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                add_start_index=True,
            )
        else:
            # Fallback: generic text splitter
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                add_start_index=True,
            )

        documents = splitter.create_documents([raw_code])
        if not documents:
            continue

        chunks = [doc.page_content for doc in documents]

        # Embed
        embeddings = list(embedding_model.embed(chunks))

        # Build points — repo_id is included in both the deterministic ID
        # and the payload so queries can filter by repo.
        points = []
        for idx, (doc, vector) in enumerate(zip(documents, embeddings)):
            chunk = doc.page_content
            start_index = doc.metadata.get("start_index", 0)
            start_line = raw_code.count("\n", 0, start_index) + 1
            end_line = start_line + chunk.count("\n")
            points.append(
                PointStruct(
                    id=_point_id(repo_id, rel_path, idx),
                    vector=vector.tolist(),
                    payload={
                        "repo_id": repo_id,
                        "file_path": rel_path,
                        "code_snippet": chunk,
                        "language": language.value if language else "text",
                        "chunk_index": idx,
                        "start_line": start_line,
                        "end_line": end_line,
                    },
                )
            )

        client.upsert(collection_name=collection, points=points)
        total_points += len(points)

        # --- Build knowledge graph for .py files ---
        if kg is not None and rel_path.endswith(".py"):
            n_added = graph_builder.update_graph_for_file(kg, rel_path, abs_path)
            graph_nodes_added += n_added

        graph_tag = f", {graph_nodes_added} graph nodes" if kg is not None and rel_path.endswith(".py") else ""
        _log(f"   ✔ {rel_path}  ({len(chunks)} chunks{graph_tag})")

    # --- Persist state and graph ---
    _save_state(state_path, new_state)

    if kg is not None:
        graph_builder.save_graph(kg, graph_path)
        summary = graph_builder.get_graph_summary(kg)
        _log(f"\n🔗 Knowledge graph: {summary['total_nodes']} nodes, {summary['total_edges']} edges")
        for ntype, count in sorted(summary['nodes'].items()):
            _log(f"   {ntype}: {count}")

    _log(f"\n🚀 Indexing complete — {total_points} point(s) upserted, "
         f"{skipped} file(s) unchanged, "
         f"{len(deleted_rel_paths)} file(s) pruned.")

    return {
        "files_indexed": len(files_to_index),
        "files_skipped": skipped,
        "files_pruned": len(deleted_rel_paths),
        "points_upserted": total_points,
        "graph_nodes": graph_nodes_added,
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
