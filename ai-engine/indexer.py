"""
indexer.py — Full-repo, incremental code indexer for CodeMind (Argus).

Recursively walks a target directory, chunks source files using language-aware
splitters, embeds the chunks via FastEmbed, and upserts them into Qdrant.

Incremental indexing:
  - On each run, computes SHA-256 hashes of all eligible files.
  - Compares against a saved state file (.codemind_index_state.json).
  - Only re-embeds files that are new or modified; prunes deleted files.
  - Pass --full-reindex to force a clean re-index of every file.

Usage:
  python indexer.py --dir ../my-repo
  python indexer.py --dir ./sample_code --full-reindex
"""

import argparse
import hashlib
import json
import os
import sys
import uuid

from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.models import Distance, VectorParams, PointStruct, FilterSelector, Filter, FieldCondition, MatchValue

from config import (
    COLLECTION_NAME,
    VECTOR_SIZE,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    IGNORE_DIRS,
    INDEX_STATE_FILENAME,
    get_language_for_file,
    get_qdrant_client,
    get_embedding_model,
)


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


def _point_id(file_rel_path: str, chunk_index: int) -> str:
    """Generate a deterministic UUID-string for a (file, chunk_index) pair.

    Using uuid5 means re-indexing the same file produces the same IDs,
    making upserts idempotent.
    """
    name = f"{file_rel_path}::{chunk_index}"
    return str(uuid.uuid5(_NAMESPACE_UUID, name))


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
    collection: str = COLLECTION_NAME,
    full_reindex: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    state_file: str | None = None,
) -> None:
    """Walk *target_dir*, embed new/modified files, and upsert into Qdrant."""

    target_dir = os.path.abspath(target_dir)
    if not os.path.isdir(target_dir):
        print(f"❌ Target directory does not exist: {target_dir}")
        sys.exit(1)

    state_path = state_file or os.path.join(target_dir, INDEX_STATE_FILENAME)

    # --- Clients ---
    client = get_qdrant_client()
    embedding_model = get_embedding_model()

    # --- Ensure collection exists ---
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"✅ Created Qdrant collection: '{collection}'")

    # --- Discover files ---
    all_files = _discover_files(target_dir)
    print(f"📂 Found {len(all_files)} indexable file(s) under {target_dir}")

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

    print(
        f"   ↳ {len(files_to_index)} to index, "
        f"{skipped} unchanged (skipped), "
        f"{len(deleted_rel_paths)} deleted"
    )

    # --- Prune deleted files from Qdrant ---
    for rel_path in deleted_rel_paths:
        client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="file_path", match=MatchValue(value=rel_path))]
                )
            ),
        )
    if deleted_rel_paths:
        print(f"🗑️  Pruned vectors for {len(deleted_rel_paths)} deleted file(s)")

    # --- Process new / modified files ---
    total_points = 0

    for abs_path in files_to_index:
        rel_path = os.path.relpath(abs_path, target_dir)
        language = get_language_for_file(abs_path)

        # Read file
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                raw_code = f.read()
        except Exception as exc:
            print(f"⚠️  Skipping {rel_path}: {exc}")
            continue

        # Delete any previous vectors for this file (handles modifications)
        client.delete(
            collection_name=collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="file_path", match=MatchValue(value=rel_path))]
                )
            ),
        )

        # Chunk
        if language is not None:
            splitter = RecursiveCharacterTextSplitter.from_language(
                language=language,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        else:
            # Fallback: generic text splitter
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

        chunks = splitter.split_text(raw_code)
        if not chunks:
            continue

        # Embed
        embeddings = list(embedding_model.embed(chunks))

        # Build points
        points = []
        for idx, (chunk, vector) in enumerate(zip(chunks, embeddings)):
            points.append(
                PointStruct(
                    id=_point_id(rel_path, idx),
                    vector=vector.tolist(),
                    payload={
                        "file_path": rel_path,
                        "code_snippet": chunk,
                        "language": language.value if language else "text",
                        "chunk_index": idx,
                    },
                )
            )

        client.upsert(collection_name=collection, points=points)
        total_points += len(points)
        print(f"   ✔ {rel_path}  ({len(chunks)} chunks)")

    # --- Persist state ---
    _save_state(state_path, new_state)

    print(f"\n🚀 Indexing complete — {total_points} point(s) upserted, "
          f"{skipped} file(s) unchanged, "
          f"{len(deleted_rel_paths)} file(s) pruned.")


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
        "--state-file",
        default=None,
        help="Path to the index state JSON file (default: <dir>/.codemind_index_state.json).",
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

    args = parser.parse_args()

    index_directory(
        target_dir=args.dir,
        collection=args.collection,
        full_reindex=args.full_reindex,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        state_file=args.state_file,
    )


if __name__ == "__main__":
    main()
