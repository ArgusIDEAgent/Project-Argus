"""
rag_pipeline.py — Graph-augmented RAG pipeline for CodeMind (Argus).

Combines two retrieval strategies:
  1. **Semantic retrieval**: Top-K vector-similar code chunks from Qdrant.
  2. **Structural retrieval**: Call/dependency relationships from the
     NetworkX knowledge graph (callers, callees, impact chains).

When a user asks a structural question ("What depends on generate_token?"),
both the graph relationships and the relevant code are injected into the
LLM prompt for a grounded, structure-aware answer.

Usage:
  python rag_pipeline.py --query "Where is JWT authentication implemented?"
  python rag_pipeline.py --query "What depends on generate_token?"
  python rag_pipeline.py --query "What is the impact of modifying verify_token?"
  python rag_pipeline.py --query "How does login work?" --top-k 5
"""

import argparse
import os
import re
import sys

from dotenv import load_dotenv
from litellm import completion

from config import (
    COLLECTION_NAME,
    DEFAULT_TOP_K,
    GRAPH_FILENAME,
    get_qdrant_client,
    get_embedding_model,
)
import graph_builder

# Load API keys from .env
load_dotenv()

DEFAULT_MODEL = "openrouter/cohere/north-mini-code:free"
DEFAULT_RAG_TOP_K = 3  # Fewer chunks than raw search — keeps the prompt focused


# ─────────────────────────────────────────────────────────────
# Structural query detection
# ─────────────────────────────────────────────────────────────

# Patterns that indicate the user is asking a structural/dependency question.
_STRUCTURAL_PATTERNS: list[tuple[str, str]] = [
    # (regex pattern, query_type)
    (r"\b(?:what|which|who)\s+(?:functions?|methods?|code)?\s*(?:calls?|invokes?|uses?)\s+[`'\"]?(\w+)", "callers"),
    (r"\b(?:what|which|who)\s+(?:depends?\s+on|relies?\s+on)\s+[`'\"]?(\w+)", "callers"),
    (r"\b(?:callers?|called\s+by|upstream)\s+(?:of\s+)?[`'\"]?(\w+)", "callers"),
    (r"\b(?:what|which)\s+(?:does|functions?|methods?)\s+[`'\"]?(\w+)[`'\"]?\s+(?:call|invoke|use)", "callees"),
    (r"\b(?:callees?|downstream)\s+(?:of\s+)?[`'\"]?(\w+)", "callees"),
    (r"\b(?:impact|affect|break|change|modif)\w*\s+(?:of\s+)?(?:changing\s+|modifying\s+)?[`'\"]?(\w+)", "impact"),
    (r"\b(?:what\s+(?:happens|breaks|is\s+affected)|change.impact)\s+(?:if\s+(?:I|we)\s+)?(?:change|modify|remove|delete)\s+[`'\"]?(\w+)", "impact"),
    (r"\b(?:members?|methods?|attributes?)\s+(?:of|in)\s+(?:class\s+)?[`'\"]?(\w+)", "members"),
]


def _detect_structural_query(question: str) -> tuple[bool, str, str]:
    """Detect whether a question is asking about code structure.

    Returns:
        (is_structural, target_symbol, query_type)
        where query_type is one of: "callers", "callees", "impact", "members"
    """
    q_lower = question.lower()

    for pattern, query_type in _STRUCTURAL_PATTERNS:
        match = re.search(pattern, q_lower)
        if match:
            # Extract the symbol name from the first capture group
            symbol = match.group(1).strip("\"'`")
            return True, symbol, query_type

    # Fallback: check for backtick-quoted symbols with structural keywords
    structural_keywords = {
        "depend", "call", "invoke", "use", "impact", "affect",
        "break", "upstream", "downstream", "caller", "callee",
    }
    if any(kw in q_lower for kw in structural_keywords):
        # Try to find a backtick-quoted symbol
        backtick_match = re.search(r"`(\w+)`", question)
        if backtick_match:
            symbol = backtick_match.group(1)
            # Default to "callers" for generic structural queries
            if any(kw in q_lower for kw in ("impact", "affect", "break", "change", "modify")):
                return True, symbol, "impact"
            return True, symbol, "callers"

    return False, "", ""


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


def _retrieve_chunks_by_file(
    file_paths: list[str],
    *,
    collection: str = COLLECTION_NAME,
) -> list[dict]:
    """Retrieve all chunks belonging to specific files from Qdrant.

    Used to pull code for files referenced by graph relationships
    (not by cosine similarity).
    """
    from qdrant_client.models import Filter, FieldCondition, MatchAny

    client = get_qdrant_client()

    if not file_paths:
        return []

    try:
        results = client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="file_path", match=MatchAny(any=file_paths))]
            ),
            limit=50,
        )
        points = results[0] if results else []
    except Exception:
        return []

    return [
        {
            "file_path": pt.payload.get("file_path", "unknown"),
            "code_snippet": pt.payload.get("code_snippet", ""),
            "language": pt.payload.get("language", "text"),
            "score": 0.0,  # Not from cosine search
        }
        for pt in points
    ]


def _build_context_block(chunks: list[dict]) -> str:
    """Assemble retrieved chunks into a numbered, structured context string."""
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
    "own codebase. Answer the user's question using ONLY the provided context.\n\n"
    "You may receive two kinds of context:\n"
    "1. **Structural Context** — relationships from a knowledge graph showing "
    "which functions call which, class membership, and dependencies.\n"
    "2. **Code Context** — actual source code snippets from the codebase.\n\n"
    "If both are provided, synthesize them: use the structural relationships "
    "to explain the architecture and the code context to ground your answer "
    "in the actual implementation. Always reference source file paths and "
    "line numbers when available. If the context does not contain enough "
    "information to answer, say so."
)


def ask_codemind(
    question: str,
    *,
    top_k: int = DEFAULT_RAG_TOP_K,
    collection: str = COLLECTION_NAME,
    model: str = DEFAULT_MODEL,
    graph_path: str | None = None,
) -> str:
    """Run the full Retrieve → Augment → Generate pipeline.

    For structural questions, augments the prompt with knowledge-graph
    relationships in addition to vector-retrieved code chunks.
    """
    print(f"\n👤 Developer: {question}")

    # 1. DETECT structural intent
    is_structural, target_symbol, query_type = _detect_structural_query(question)

    graph_context_text = ""
    graph_file_chunks: list[dict] = []

    if is_structural and graph_path:
        kg = graph_builder.load_graph(graph_path)
        if kg.number_of_nodes() > 0:
            print(f"🔗 Structural query detected: {query_type}('{target_symbol}')")

            # Query the knowledge graph
            if query_type == "callers":
                relationships = graph_builder.get_callers(kg, target_symbol)
            elif query_type == "callees":
                relationships = graph_builder.get_callees(kg, target_symbol)
            elif query_type == "impact":
                relationships = graph_builder.get_impact_chain(kg, target_symbol, depth=3)
            elif query_type == "members":
                relationships = graph_builder.get_class_members(kg, target_symbol)
            else:
                relationships = []

            if relationships:
                graph_context_text = graph_builder.format_graph_context(
                    target_symbol, relationships, query_type
                )
                # Also fetch code chunks for files involved in the relationships
                related_files = list({
                    r.get("file_path", "")
                    for r in relationships
                    if r.get("file_path")
                })
                # Add the target symbol's own file
                target_node = graph_builder.find_node(kg, target_symbol)
                if target_node:
                    target_file = kg.nodes[target_node].get("file_path", "")
                    if target_file and target_file not in related_files:
                        related_files.append(target_file)

                graph_file_chunks = _retrieve_chunks_by_file(
                    related_files, collection=collection
                )
                print(f"   ↳ Found {len(relationships)} relationship(s), "
                      f"fetched {len(graph_file_chunks)} chunk(s) from related files")

    # 2. RETRIEVE — standard vector search
    semantic_chunks = _retrieve_context(question, top_k=top_k, collection=collection)

    # Merge: graph-related chunks first (deduplicated), then semantic chunks
    seen_snippets: set[str] = set()
    all_chunks: list[dict] = []

    for chunk in graph_file_chunks + semantic_chunks:
        snippet_key = chunk["code_snippet"][:100]  # dedupe on first 100 chars
        if snippet_key not in seen_snippets:
            seen_snippets.add(snippet_key)
            all_chunks.append(chunk)

    if not all_chunks and not graph_context_text:
        msg = "I couldn't find any relevant code in the indexed codebase for that question."
        print(f"\n🤖 CodeMind: {msg}")
        return msg

    # Show which files were retrieved
    file_list = ", ".join(dict.fromkeys(c["file_path"] for c in all_chunks))
    print(f"📎 Retrieved {len(all_chunks)} chunk(s) from: {file_list}")

    # 3. AUGMENT — build the prompt
    prompt_parts: list[str] = []

    if graph_context_text:
        prompt_parts.append(f"### Structural Context (Knowledge Graph)\n\n{graph_context_text}")

    if all_chunks:
        code_block = _build_context_block(all_chunks)
        prompt_parts.append(f"### Code Context\n\n{code_block}")

    prompt_parts.append(f"### Question\n\n{question}")

    user_prompt = "\n\n".join(prompt_parts)

    print("🧠 CodeMind is thinking...\n")

    # 4. GENERATE
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

def _find_graph_file() -> str | None:
    """Auto-detect the graph file in common locations."""
    candidates = [
        GRAPH_FILENAME,
        os.path.join("sample_code", GRAPH_FILENAME),
        os.path.join("..", GRAPH_FILENAME),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMind (Argus) — Ask questions about your codebase (graph-augmented RAG).",
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
    parser.add_argument(
        "--graph-file",
        default=None,
        help="Path to the knowledge graph JSON file (default: auto-detect).",
    )
    parser.add_argument(
        "--no-graph",
        action="store_true",
        default=False,
        help="Disable graph-augmented retrieval (pure vector search only).",
    )

    args = parser.parse_args()

    if args.top_k < 1:
        print("❌ --top-k must be at least 1.")
        sys.exit(1)

    # Resolve graph path
    graph_path = None
    if not args.no_graph:
        graph_path = args.graph_file or _find_graph_file()
        if graph_path:
            print(f"📊 Using knowledge graph: {graph_path}")
        else:
            print("ℹ️  No knowledge graph found — using vector search only.")

    ask_codemind(
        question=args.query,
        top_k=args.top_k,
        collection=args.collection,
        model=args.model,
        graph_path=graph_path,
    )


if __name__ == "__main__":
    main()
