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
"""

import argparse
import os
import sys

from config import GRAPH_FILENAME
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
        print(f"\n❌ No {mode} found for '{symbol}'.")
        return

    formatted = format_graph_context(symbol, results, mode)
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
        help="Path to the graph JSON file (default: auto-detect in current or parent dir).",
    )

    args = parser.parse_args()

    # --- Locate the graph file ---
    graph_path = args.graph_file
    if graph_path is None:
        # Try current directory, then common locations
        candidates = [
            GRAPH_FILENAME,
            os.path.join("sample_code", GRAPH_FILENAME),
            os.path.join("..", GRAPH_FILENAME),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                graph_path = candidate
                break

    if graph_path is None or not os.path.exists(graph_path):
        print(f"❌ Knowledge graph not found. Run `python indexer.py --dir <path>` first.")
        sys.exit(1)

    graph = load_graph(graph_path)
    print(f"📊 Loaded graph from {graph_path} ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")

    # --- Handle --stats ---
    if args.stats:
        summary = get_graph_summary(graph)
        print(f"\n  Node types:")
        for ntype, count in sorted(summary["nodes"].items()):
            print(f"    {ntype}: {count}")
        print(f"\n  Edge types:")
        for etype, count in sorted(summary["edges"].items()):
            print(f"    {etype}: {count}")
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
            print(f"  📄 {filepath}")
            for nid, data in sorted(nodes, key=lambda x: x[1].get("lineno", 0)):
                ntype = data.get("type", "?")
                lineno = data.get("lineno", "?")
                print(f"     {ntype:10s}  {nid}  (line {lineno})")
            print()
        return

    # --- Handle --symbol queries ---
    symbol = args.symbol
    node_id = find_node(graph, symbol)

    if node_id is None:
        print(f"\n❌ Symbol '{symbol}' not found in the graph.")
        # Suggest similar names
        suggestions = [
            data.get("name", nid)
            for nid, data in graph.nodes(data=True)
            if symbol.lower() in nid.lower() or symbol.lower() in data.get("name", "").lower()
        ]
        if suggestions:
            print(f"   Did you mean: {', '.join(suggestions[:5])}?")
        sys.exit(1)

    node_data = graph.nodes[node_id]
    print(f"\n🔎 Found: {node_id} ({node_data.get('type', '?')} in {node_data.get('file_path', '?')}:{node_data.get('lineno', '?')})")

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
