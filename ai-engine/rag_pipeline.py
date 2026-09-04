"""
rag_pipeline.py — Full RAG pipeline for CodeMind (Argus).

Retrieves the top-K most relevant code chunks from Qdrant, assembles them
into a structured multi-chunk context block, and sends the augmented prompt
to an LLM via LiteLLM for a grounded answer.

Usage:
  python rag_pipeline.py --query "Where is JWT authentication implemented?"
  python rag_pipeline.py --query "How does login work?" --top-k 5
  python rag_pipeline.py --query "What algorithm signs tokens?" --model openrouter/cohere/north-mini-code:free
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from litellm import completion

from config import (
    COLLECTION_NAME,
    DEFAULT_TOP_K,
    get_qdrant_client,
    get_embedding_model,
)

# Load API keys from .env
load_dotenv()

DEFAULT_MODEL = "openrouter/cohere/north-mini-code:free"
DEFAULT_RAG_TOP_K = 3  # Fewer chunks than raw search — keeps the prompt focused


# ─────────────────────────────────────────────────────────────
# Retrieval helpers
# ─────────────────────────────────────────────────────────────

def _retrieve_context(
    question: str,
    *,
    top_k: int = DEFAULT_RAG_TOP_K,
    collection: str = COLLECTION_NAME,
) -> list[dict]:
    """Retrieve the top-K code chunks most relevant to *question*.

    Returns a list of dicts: {file_path, code_snippet, language, score}.
    """
    client = get_qdrant_client()
    embedding_model = get_embedding_model()

    query_vector = list(embedding_model.embed([question]))[0].tolist()

    search_results = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=top_k,
    ).points

    return [
        {
            "file_path": pt.payload.get("file_path", "unknown"),
            "code_snippet": pt.payload.get("code_snippet", ""),
            "language": pt.payload.get("language", "text"),
            "score": pt.score,
        }
        for pt in search_results
    ]


def _build_context_block(chunks: list[dict]) -> str:
    """Assemble retrieved chunks into a numbered, structured context string.

    Example output:
        [1] auth_service.py
        ```python
        def generate_token(...)
        ```

        [2] user_model.py
        ```python
        class User:
        ```
    """
    sections: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        lang = chunk["language"]
        section = (
            f"[{idx}] {chunk['file_path']}\n"
            f"```{lang}\n"
            f"{chunk['code_snippet']}\n"
            f"```"
        )
        sections.append(section)
    return "\n\n".join(sections)


# ─────────────────────────────────────────────────────────────
# RAG pipeline
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are CodeMind, an AI developer assistant that understands the user's "
    "own codebase. Answer the user's question using ONLY the provided code "
    "context sections below. If multiple sections are relevant, synthesize "
    "them into a coherent answer. Always reference the source file path(s). "
    "If the context does not contain enough information to answer, say so."
)


def ask_codemind(
    question: str,
    *,
    top_k: int = DEFAULT_RAG_TOP_K,
    collection: str = COLLECTION_NAME,
    model: str = DEFAULT_MODEL,
) -> str:
    """Run the full Retrieve → Augment → Generate pipeline."""

    print(f"\n👤 Developer: {question}")

    # 1. RETRIEVE
    chunks = _retrieve_context(question, top_k=top_k, collection=collection)

    if not chunks:
        msg = "I couldn't find any relevant code in the indexed codebase for that question."
        print(f"\n🤖 CodeMind: {msg}")
        return msg

    context_block = _build_context_block(chunks)

    # Show which files were retrieved
    file_list = ", ".join(dict.fromkeys(c["file_path"] for c in chunks))  # unique, ordered
    print(f"📎 Retrieved {len(chunks)} chunk(s) from: {file_list}")

    # 2. AUGMENT
    user_prompt = (
        f"### Code Context\n\n"
        f"{context_block}\n\n"
        f"### Question\n\n"
        f"{question}"
    )

    print("🧠 CodeMind is thinking...\n")

    # 3. GENERATE
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    answer = response.choices[0].message.content

    print("🤖 CodeMind:")
    print(answer)
    print("─" * 50)

    return answer


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMind (Argus) — Ask questions about your codebase (full RAG).",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Natural-language question about the codebase.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_RAG_TOP_K,
        help=f"Number of context chunks to retrieve (default: {DEFAULT_RAG_TOP_K}).",
    )
    parser.add_argument(
        "--collection",
        default=COLLECTION_NAME,
        help=f"Qdrant collection name (default: {COLLECTION_NAME}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LiteLLM model string (default: {DEFAULT_MODEL}).",
    )

    args = parser.parse_args()

    if args.top_k < 1:
        print("❌ --top-k must be at least 1.")
        sys.exit(1)

    ask_codemind(
        question=args.query,
        top_k=args.top_k,
        collection=args.collection,
        model=args.model,
    )


if __name__ == "__main__":
    main()
