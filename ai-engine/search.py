"""
search.py — Semantic code search for CodeMind (Argus).

Embeds a natural-language query via FastEmbed and retrieves the top-K most
similar code chunks from Qdrant.

Usage:
  python search.py --query "How does authentication work?"
  python search.py --query "JWT token validation" --top-k 3
  python search.py --query "JWT token validation" --repo-id <repo_id>
  python search.py --query "JWT token validation" --repo-dir /path/to/repo
"""

import argparse
import os
import sys

from qdrant_client.models import Filter, FieldCondition, MatchValue

from config import (
    COLLECTION_NAME,
    DEFAULT_TOP_K,
    embed_texts,
    get_qdrant_client,
    latest_repo_id,
    make_repo_id,
    sanitize_text,
)


def search_codebase(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    collection: str = COLLECTION_NAME,
    repo_id: str | None = None,
) -> list[dict]:
    """Search the indexed codebase and return the top-K matching chunks.

    When *repo_id* is provided the search is scoped to that repository so
    that chunks from unrelated repositories (which may share relative file
    paths) cannot leak into the results.

    Returns a list of dicts with keys: file_path, code_snippet, language, score.
    """
    client = get_qdrant_client()

    print(f"\n🔍 Searching for: '{sanitize_text(query)}' (top {top_k})")
    if repo_id:
        print(f"📦 Repository: {repo_id}")

    # Convert the natural-language query into a vector (batched helper)
    query_vector = embed_texts([query])[0]

    query_filter = None
    if repo_id:
        query_filter = Filter(
            must=[FieldCondition(key="repo_id", match=MatchValue(value=repo_id))]
        )

    # Search Qdrant for the most similar code chunks
    search_results = client.query_points(
        collection_name=collection,
        query=query_vector,
        query_filter=query_filter,
        limit=top_k,
    ).points

    if not search_results:
        print("❌ No relevant code found.")
        return []

    # Format and display results
    results: list[dict] = []
    print(f"\n✅ Found {len(search_results)} result(s):\n")

    for rank, point in enumerate(search_results, start=1):
        payload = point.payload
        score = point.score

        result = {
            "rank": rank,
            "file_path": payload.get("file_path", "unknown"),
            "code_snippet": payload.get("code_snippet", ""),
            "language": payload.get("language", "text"),
            "score": score,
            "start_line": payload.get("start_line", 1),
            "end_line": payload.get("end_line", 1),
        }
        results.append(result)

        # Pretty-print each result (untrusted repository content is sanitised)
        print(f"  [{rank}] {sanitize_text(result['file_path'])}  (score: {score:.4f})")
        print(f"  {'─' * 50}")
        # Indent the snippet for readability
        for line in result["code_snippet"].splitlines():
            print(f"      {sanitize_text(line)}")
        print(f"  {'─' * 50}\n")

    return results


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMind (Argus) — Semantic code search over an indexed codebase.",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Natural-language search query.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Number of results to return (default: {DEFAULT_TOP_K}).",
    )
    parser.add_argument(
        "--collection",
        default=COLLECTION_NAME,
        help=f"Qdrant collection name (default: {COLLECTION_NAME}).",
    )
    parser.add_argument(
        "--repo-id",
        default=None,
        help="Repository id to scope the search to (default: most recently indexed repo).",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="Repository directory; converted to a repo id (ignored if --repo-id is given).",
    )

    args = parser.parse_args()

    if args.top_k < 1:
        print("❌ --top-k must be at least 1.")
        sys.exit(1)
    if args.top_k > 50:
        print("❌ --top-k must be 50 or fewer.")
        sys.exit(1)

    # Resolve which repository to search.
    repo_id = args.repo_id
    if repo_id is None and args.repo_dir:
        repo_id = make_repo_id(os.path.abspath(args.repo_dir))
    if repo_id is None:
        repo_id = latest_repo_id()

    if repo_id is None:
        print("No indexed repository found. Run: python indexer.py --dir <path>")
        sys.exit(1)

    search_codebase(
        query=args.query,
        top_k=args.top_k,
        collection=args.collection,
        repo_id=repo_id,
    )


if __name__ == "__main__":
    main()
