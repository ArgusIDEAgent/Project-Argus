"""
graph_builder.py — AST-based knowledge graph for CodeMind (Argus).

Parses Python source files using the built-in `ast` module to extract
structural relationships (functions, classes, method calls, imports) and
stores them in a lightweight NetworkX directed graph.

Graph node types:
  - module   : a Python file
  - class    : a class definition
  - function : a top-level function
  - method   : a function defined inside a class

Graph edge types:
  - calls    : function/method A calls function/method B
  - contains : class contains method, or module contains function/class
  - imports  : module imports a symbol

Persistence:
  The graph is saved as a JSON node-link file (.codemind_graph.json)
  using NetworkX's built-in node_link_data / node_link_graph serializers.
"""

from __future__ import annotations

import ast
import json
import os
from typing import Any

import networkx as nx


# ─────────────────────────────────────────────────────────────
# AST Visitor — extracts structure from a single Python file
# ─────────────────────────────────────────────────────────────

class _CodeStructureVisitor(ast.NodeVisitor):
    """Walk a Python AST and collect nodes (functions, classes) and edges (calls)."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        # module_prefix is used to build qualified names, e.g. "auth_service"
        self.module_name = os.path.splitext(rel_path.replace(os.sep, "."))[0]

        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []

        # Stack tracks the current scope for qualified-name building.
        self._scope_stack: list[str] = [self.module_name]

    @property
    def _current_scope(self) -> str:
        return self._scope_stack[-1]

    # ── Module ────────────────────────────────────────────────

    def visit_Module(self, node: ast.Module) -> None:
        self.nodes.append({
            "id": self.module_name,
            "type": "module",
            "name": self.module_name,
            "file_path": self.rel_path,
            "lineno": 1,
            "end_lineno": getattr(node, "end_lineno", None),
            "docstring": ast.get_docstring(node) or "",
        })
        self.generic_visit(node)

    # ── Classes ───────────────────────────────────────────────

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = f"{self._current_scope}.{node.name}"
        self.nodes.append({
            "id": qualified,
            "type": "class",
            "name": node.name,
            "file_path": self.rel_path,
            "lineno": node.lineno,
            "end_lineno": getattr(node, "end_lineno", None),
            "docstring": ast.get_docstring(node) or "",
            "bases": [_name_from_node(b) for b in node.bases],
        })
        # module/class ──contains──▶ class
        self.edges.append({
            "source": self._current_scope,
            "target": qualified,
            "type": "contains",
        })

        self._scope_stack.append(qualified)
        self.generic_visit(node)
        self._scope_stack.pop()

    # ── Functions / Methods ───────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node)

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = f"{self._current_scope}.{node.name}"
        # Determine if this is a method (parent scope is a class)
        parent_node_data = next(
            (n for n in self.nodes if n["id"] == self._current_scope), None
        )
        is_method = parent_node_data is not None and parent_node_data["type"] == "class"

        self.nodes.append({
            "id": qualified,
            "type": "method" if is_method else "function",
            "name": node.name,
            "file_path": self.rel_path,
            "lineno": node.lineno,
            "end_lineno": getattr(node, "end_lineno", None),
            "docstring": ast.get_docstring(node) or "",
        })
        # parent ──contains──▶ function/method
        self.edges.append({
            "source": self._current_scope,
            "target": qualified,
            "type": "contains",
        })

        # Extract calls made *inside* this function body
        self._scope_stack.append(qualified)
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee_name = _name_from_call(child)
                if callee_name:
                    self.edges.append({
                        "source": qualified,
                        "target": callee_name,  # may be unresolved short name
                        "type": "calls",
                    })
        # Visit nested definitions (closures, nested classes)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.visit(child)
        self._scope_stack.pop()

    # ── Imports ───────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.edges.append({
                "source": self.module_name,
                "target": alias.name,
                "type": "imports",
            })

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            self.edges.append({
                "source": self.module_name,
                "target": full,
                "type": "imports",
            })


# ─────────────────────────────────────────────────────────────
# AST helper utilities
# ─────────────────────────────────────────────────────────────

def _name_from_node(node: ast.expr) -> str:
    """Best-effort extraction of a name string from an AST expression node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        value = _name_from_node(node.value)
        return f"{value}.{node.attr}" if value else node.attr
    if isinstance(node, ast.Constant):
        return str(node.value)
    return ""


def _name_from_call(call_node: ast.Call) -> str:
    """Extract the callee name from a Call node.

    Handles:
        foo()           → "foo"
        self.bar()      → "bar"       (strips self/cls)
        module.func()   → "module.func"
    """
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        value_name = _name_from_node(func.value)
        if value_name in ("self", "cls"):
            return func.attr
        return f"{value_name}.{func.attr}" if value_name else func.attr
    return ""


# ─────────────────────────────────────────────────────────────
# Public API — graph construction
# ─────────────────────────────────────────────────────────────

def parse_file_structure(file_path: str, rel_path: str) -> tuple[list[dict], list[dict]]:
    """Parse a Python file and return (nodes, edges) describing its structure.

    Args:
        file_path: Absolute path to the .py file.
        rel_path:  Relative path (used as the canonical identifier in the graph).

    Returns:
        (nodes, edges) where each node/edge is a dict with the attributes
        described in the module docstring.
    """
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        print(f"⚠️  AST parse failed for {rel_path}: {exc}")
        return [], []

    visitor = _CodeStructureVisitor(rel_path)
    visitor.visit(tree)
    return visitor.nodes, visitor.edges


def _resolve_call_edges(graph: nx.DiGraph) -> None:
    """Resolve short callee names to qualified names where possible.

    When the AST visitor sees `generate_token(...)` inside `login_user`,
    it records the edge target as the short name "generate_token".  This
    function tries to match it against actual graph nodes — first by
    checking siblings in the same module, then by searching all nodes.
    """
    edges_to_fix: list[tuple[str, str, dict]] = []

    for u, v, data in graph.edges(data=True):
        if data.get("type") != "calls":
            continue
        if v in graph.nodes:
            continue  # already resolved

        # The caller's module prefix
        caller_data = graph.nodes.get(u, {})
        caller_file = caller_data.get("file_path", "")
        module_prefix = os.path.splitext(caller_file.replace(os.sep, "."))[0]

        # Try: module_prefix.short_name (sibling in same file)
        candidate = f"{module_prefix}.{v}"
        if candidate in graph.nodes:
            edges_to_fix.append((u, v, {**data, "_resolved": candidate}))
            continue

        # Try: any node whose short name matches
        matches = [
            nid for nid, ndata in graph.nodes(data=True)
            if ndata.get("name") == v
        ]
        if len(matches) == 1:
            edges_to_fix.append((u, v, {**data, "_resolved": matches[0]}))

    for u, v, data in edges_to_fix:
        resolved = data.pop("_resolved")
        graph.remove_edge(u, v)
        graph.add_edge(u, resolved, **data)


def update_graph_for_file(
    graph: nx.DiGraph,
    rel_path: str,
    file_path: str,
) -> int:
    """Remove old data for *rel_path*, re-parse, and insert into *graph*.

    Only processes `.py` files. Returns the number of nodes added.
    """
    if not rel_path.endswith(".py"):
        return 0

    remove_file_from_graph(graph, rel_path)

    nodes, edges = parse_file_structure(file_path, rel_path)

    for node in nodes:
        node_id = node.pop("id")
        graph.add_node(node_id, **node)

    for edge in edges:
        graph.add_edge(edge["source"], edge["target"], type=edge["type"])

    # Resolve short call names now that new nodes are in the graph
    _resolve_call_edges(graph)

    return len(nodes)


def remove_file_from_graph(graph: nx.DiGraph, rel_path: str) -> int:
    """Remove all nodes (and their edges) belonging to *rel_path*.

    Returns the number of nodes removed.
    """
    to_remove = [
        nid for nid, data in graph.nodes(data=True)
        if data.get("file_path") == rel_path
    ]
    graph.remove_nodes_from(to_remove)
    return len(to_remove)


# ─────────────────────────────────────────────────────────────
# Public API — persistence
# ─────────────────────────────────────────────────────────────

def load_graph(path: str) -> nx.DiGraph:
    """Load a knowledge graph from a JSON node-link file, or return empty."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return nx.node_link_graph(data, directed=True)
    return nx.DiGraph()


def save_graph(graph: nx.DiGraph, path: str) -> None:
    """Persist the knowledge graph as a JSON node-link file."""
    data = nx.node_link_data(graph)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────────
# Public API — graph queries
# ─────────────────────────────────────────────────────────────

def find_node(graph: nx.DiGraph, symbol_name: str) -> str | None:
    """Find a node ID by exact qualified name or by unqualified short name.

    Returns the node ID, or None if not found.
    """
    # Exact match first
    if symbol_name in graph.nodes:
        return symbol_name

    # Short-name match (e.g., "generate_token" → "auth_service.generate_token")
    matches = [
        nid for nid, data in graph.nodes(data=True)
        if data.get("name") == symbol_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Ambiguous — prefer functions/methods over modules
        non_module = [m for m in matches if graph.nodes[m].get("type") != "module"]
        if len(non_module) == 1:
            return non_module[0]
        # Still ambiguous — return first match
        return matches[0]
    return None


def get_callers(graph: nx.DiGraph, symbol_name: str) -> list[dict]:
    """Return all nodes that call *symbol_name* (upstream / predecessors via 'calls' edges)."""
    node_id = find_node(graph, symbol_name)
    if node_id is None:
        return []

    callers = []
    for pred in graph.predecessors(node_id):
        edge_data = graph.edges[pred, node_id]
        if edge_data.get("type") == "calls":
            callers.append({
                "id": pred,
                "relationship": "calls",
                "direction": "caller",
                **graph.nodes[pred],
            })
    return callers


def get_callees(graph: nx.DiGraph, symbol_name: str) -> list[dict]:
    """Return all nodes that *symbol_name* calls (downstream / successors via 'calls' edges)."""
    node_id = find_node(graph, symbol_name)
    if node_id is None:
        return []

    callees = []
    for succ in graph.successors(node_id):
        edge_data = graph.edges[node_id, succ]
        if edge_data.get("type") == "calls":
            node_data = graph.nodes.get(succ, {})
            callees.append({
                "id": succ,
                "relationship": "calls",
                "direction": "callee",
                **node_data,
            })
    return callees


def get_class_members(graph: nx.DiGraph, class_name: str) -> list[dict]:
    """Return all methods/attributes contained by *class_name*."""
    node_id = find_node(graph, class_name)
    if node_id is None:
        return []

    members = []
    for succ in graph.successors(node_id):
        edge_data = graph.edges[node_id, succ]
        if edge_data.get("type") == "contains":
            members.append({
                "id": succ,
                "relationship": "member_of",
                **graph.nodes[succ],
            })
    return members


def get_impact_chain(
    graph: nx.DiGraph,
    symbol_name: str,
    *,
    depth: int = 2,
) -> list[dict]:
    """Return the transitive set of callers up to *depth* hops.

    This answers "what breaks if I change this symbol?" by walking
    upstream through the call graph.
    """
    node_id = find_node(graph, symbol_name)
    if node_id is None:
        return []

    visited: set[str] = set()
    frontier = {node_id}
    results: list[dict] = []

    for current_depth in range(1, depth + 1):
        next_frontier: set[str] = set()
        for nid in frontier:
            for pred in graph.predecessors(nid):
                edge_data = graph.edges[pred, nid]
                if edge_data.get("type") == "calls" and pred not in visited:
                    visited.add(pred)
                    next_frontier.add(pred)
                    results.append({
                        "id": pred,
                        "depth": current_depth,
                        "calls": nid,
                        **graph.nodes.get(pred, {}),
                    })
        frontier = next_frontier
        if not frontier:
            break

    return results


def format_graph_context(
    target_symbol: str,
    relationships: list[dict],
    query_type: str,
) -> str:
    """Format graph query results as a readable text block for the LLM prompt.

    Args:
        target_symbol: The symbol being queried.
        relationships: List of relationship dicts from query functions.
        query_type:    One of "callers", "callees", "impact", "members".

    Returns:
        A formatted string suitable for injection into the RAG prompt.
    """
    if not relationships:
        return f"No structural relationships found for `{target_symbol}`."

    lines = [f"Structural analysis for `{target_symbol}` ({query_type}):\n"]

    for rel in relationships:
        node_type = rel.get("type", "symbol")
        file_path = rel.get("file_path", "unknown")
        lineno = rel.get("lineno", "?")
        name = rel.get("name", rel.get("id", "?"))

        if query_type == "callers":
            lines.append(f"  • {name} ({node_type} in {file_path}:{lineno}) ──calls──▶ {target_symbol}")
        elif query_type == "callees":
            lines.append(f"  • {target_symbol} ──calls──▶ {name} ({node_type} in {file_path}:{lineno})")
        elif query_type == "impact":
            depth = rel.get("depth", "?")
            calls_target = rel.get("calls", target_symbol)
            calls_name = calls_target.rsplit(".", 1)[-1] if "." in calls_target else calls_target
            lines.append(f"  • [depth {depth}] {name} ({node_type} in {file_path}:{lineno}) ──calls──▶ {calls_name}")
        elif query_type == "members":
            lines.append(f"  • {name} ({node_type} at line {lineno})")
        else:
            lines.append(f"  • {name} ({node_type} in {file_path}:{lineno})")

    return "\n".join(lines)


def get_graph_summary(graph: nx.DiGraph) -> dict[str, int]:
    """Return a summary of the graph's contents by node/edge type."""
    node_types: dict[str, int] = {}
    for _, data in graph.nodes(data=True):
        t = data.get("type", "unknown")
        node_types[t] = node_types.get(t, 0) + 1

    edge_types: dict[str, int] = {}
    for _, _, data in graph.edges(data=True):
        t = data.get("type", "unknown")
        edge_types[t] = edge_types.get(t, 0) + 1

    return {"nodes": node_types, "edges": edge_types, "total_nodes": graph.number_of_nodes(), "total_edges": graph.number_of_edges()}
