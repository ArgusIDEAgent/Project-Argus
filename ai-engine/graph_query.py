"""
graph_query.py — Standalone CLI for querying the CodeMind knowledge graph.

Queries the NetworkX knowledge graph directly (no LLM call) to inspect
structural relationships: callers, callees, impact chains, class members,
and a full listing of all indexed symbols.

Usage:
  python graph_query.py --symbol generate_token --mode callers
  python graph_query.py --symbol generate_token --mode callees
  python graph_query.py --symbol generate_token --mode impact --depth 3
  python graph_query.py --symbol AuthService --mode members
  python graph_query.py --list-all
  python graph_query.py --stats
  python graph_query.py --symbol generate_token --repo-id <repo_id>
  python graph_query.py --symbol generate_token --repo-dir /path/to/repo
"""

import argparse
import os
import sys

from config import find_graph_file, make_repo_id, sanitize_text
from graph_builder import (
    load_graph,
    find_node,
    get_callers,
    get_callees,
    get_impact_chain,
    get_class_members,
    format_graph_context,
    get_graph_summary,
)


def _print_results(symbol: str, results: list[dict], mode: str) -> None:
    """Pretty-print query results."""
    if not results:
        print(f"\n❌ No {mode} found for '{sanitize_text(symbol)}'.")
        return

    formatted = sanitize_text(format_graph_context(symbol, results, mode))
    print(f"\n{formatted}")
    print(f"\n  Total: {len(results)} relationship(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMind (Argus) — Query the codebase knowledge graph.",
    )

    # Mutually exclusive: either query a symbol or list everything
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--symbol",
        help="Symbol name to query (e.g., 'generate_token', 'AuthService').",
    )
    group.add_argument(
        "--list-all",
        action="store_true",
        default=False,
        help="List all nodes in the knowledge graph.",
    )
    group.add_argument(
        "--stats",
        action="store_true",
        default=False,
        help="Print a summary of the graph (node/edge counts by type).",
    )

    parser.add_argument(
        "--mode",
        choices=["callers", "callees", "impact", "members"],
        default="callers",
        help="Query mode (default: callers).",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        help="Max traversal depth for impact analysis (default: 2).",
    )
    parser.add_argument(
        "--graph-file",
        default=None,
        help="Path to the graph JSON file (default: resolve from --repo-id/--repo-dir).",
    )
    parser.add_argument(
        "--repo-id",
        default=None,
        help="Repository id whose graph to load (default: most recently indexed repo).",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="Repository directory; converted to a repo id (ignored if --repo-id is given).",
    )

    args = parser.parse_args()

    # --- Locate the graph file ---
    repo_id = args.repo_id
    if repo_id is None and args.repo_dir:
        repo_id = make_repo_id(os.path.abspath(args.repo_dir))

    graph_path = args.graph_file or find_graph_file(repo_id)

    if graph_path is None or not os.path.exists(graph_path):
        print(
            "❌ Knowledge graph not found. Run `python indexer.py --dir <path>` first, "
            "or pass --repo-id / --repo-dir to select an indexed repository."
        )
        sys.exit(1)

    graph = load_graph(graph_path)
    print(f"📊 Loaded graph from {sanitize_text(graph_path)} ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")

    # --- Handle --stats ---
    if args.stats:
        summary = get_graph_summary(graph)
        print(f"\n  Node types:")
        for ntype, count in sorted(summary["nodes"].items()):
            print(f"    {sanitize_text(str(ntype))}: {count}")
        print(f"\n  Edge types:")
        for etype, count in sorted(summary["edges"].items()):
            print(f"    {sanitize_text(str(etype))}: {count}")
        print(f"\n  Total: {summary['total_nodes']} nodes, {summary['total_edges']} edges")
        return

    # --- Handle --list-all ---
    if args.list_all:
        print(f"\n📋 All symbols in the knowledge graph:\n")
        # Group by file
        by_file: dict[str, list[tuple[str, dict]]] = {}
        for nid, data in graph.nodes(data=True):
            fp = data.get("file_path", "unknown")
            by_file.setdefault(fp, []).append((nid, data))

        for filepath, nodes in sorted(by_file.items()):
            print(f"  📄 {sanitize_text(filepath)}")
            for nid, data in sorted(nodes, key=lambda x: x[1].get("lineno", 0)):
                ntype = data.get("type", "?")
                lineno = data.get("lineno", "?")
                print(f"     {sanitize_text(str(ntype)):10s}  {sanitize_text(str(nid))}  (line {lineno})")
            print()
        return

    # --- Handle --symbol queries ---
    symbol = args.symbol
    node_id = find_node(graph, symbol)

    if node_id is None:
        print(f"\n❌ Symbol '{sanitize_text(symbol)}' not found in the graph.")
        # Suggest similar names
        suggestions = [
            data.get("name", nid)
            for nid, data in graph.nodes(data=True)
            if symbol.lower() in nid.lower() or symbol.lower() in data.get("name", "").lower()
        ]
        if suggestions:
            print(f"   Did you mean: {', '.join(sanitize_text(str(s)) for s in suggestions[:5])}?")
        sys.exit(1)

    node_data = graph.nodes[node_id]
    print(
        f"\n🔎 Found: {sanitize_text(str(node_id))} "
        f"({sanitize_text(str(node_data.get('type', '?')))} in "
        f"{sanitize_text(str(node_data.get('file_path', '?')))}:{node_data.get('lineno', '?')})"
    )

    if args.mode == "callers":
        results = get_callers(graph, symbol)
        _print_results(symbol, results, "callers")

    elif args.mode == "callees":
        results = get_callees(graph, symbol)
        _print_results(symbol, results, "callees")

    elif args.mode == "impact":
        results = get_impact_chain(graph, symbol, depth=args.depth)
        _print_results(symbol, results, "impact")

    elif args.mode == "members":
        results = get_class_members(graph, symbol)
        _print_results(symbol, results, "members")


if __name__ == "__main__":
    main()
