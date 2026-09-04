"""
search.py — Semantic code search for CodeMind (Argus).

Embeds a natural-language query via FastEmbed and retrieves the top-K most
similar code chunks from Qdrant.

Usage:
  python search.py --query "How does authentication work?"
  python search.py --query "JWT token validation" --top-k 3
"""

import argparse
import sys

from config import (
    COLLECTION_NAME,
    DEFAULT_TOP_K,
    get_qdrant_client,
    get_embedding_model,
)


def search_codebase(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    collection: str = COLLECTION_NAME,
) -> list[dict]:
    """Search the indexed codebase and return the top-K matching chunks.

    Returns a list of dicts with keys: file_path, code_snippet, language, score.
    """
    client = get_qdrant_client()
    embedding_model = get_embedding_model()

    print(f"\n🔍 Searching for: '{query}' (top {top_k})")

    # Convert the natural-language query into a vector
    query_vector = list(embedding_model.embed([query]))[0].tolist()

    # Search Qdrant for the most similar code chunks
    search_results = client.query_points(
        collection_name=collection,
        query=query_vector,
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
        }
        results.append(result)

        # Pretty-print each result
        print(f"  [{rank}] {result['file_path']}  (score: {score:.4f})")
        print(f"  {'─' * 50}")
        # Indent the snippet for readability
        for line in result["code_snippet"].splitlines():
            print(f"      {line}")
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

    args = parser.parse_args()

    if args.top_k < 1:
        print("❌ --top-k must be at least 1.")
        sys.exit(1)

    search_codebase(
        query=args.query,
        top_k=args.top_k,
        collection=args.collection,
    )


if __name__ == "__main__":
    main()