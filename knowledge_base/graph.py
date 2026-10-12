"""Phase 3: persistent, evidence-bearing semantic graph for JS/TS repositories.

Tree-sitter supplies definitions and occurrence spans; the TypeScript checker
supplies cross-file binding. Unmapped occurrences remain explicitly unresolved.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

from knowledge_base.parser import LANGUAGES, parse_repository


from knowledge_base.identity import entity_id
from knowledge_base.metadata import SCHEMA_VERSION, begin_snapshot, finish_snapshot, publish_graph

GRAPH_SCHEMA_VERSION = SCHEMA_VERSION
FUNCTION_KINDS = {"function", "function_overload", "method", "method_signature",
                  "arrow_function", "function_expression"}
CLASS_KINDS = {"class", "interface", "type_alias", "enum"}


def _id(kind: str, *parts: str) -> str:
    return entity_id(kind, *parts)


def _schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, kind TEXT NOT NULL,
            label TEXT NOT NULL, relative_path TEXT, symbol_id TEXT, metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS graph_nodes_repo_kind ON graph_nodes(repository_id, kind);
        CREATE INDEX IF NOT EXISTS graph_nodes_symbol ON graph_nodes(symbol_id);
        CREATE TABLE IF NOT EXISTS graph_relationships (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
            target_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
            relationship TEXT NOT NULL, resolution TEXT NOT NULL
                CHECK(resolution IN ('resolved', 'inferred', 'unresolved')),
            evidence_type TEXT NOT NULL, evidence_file TEXT, evidence_line INTEGER,
            evidence_column INTEGER, evidence_start_byte INTEGER, evidence_end_byte INTEGER,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS graph_edges_source ON graph_relationships(source_id, relationship);
        CREATE INDEX IF NOT EXISTS graph_edges_target ON graph_relationships(target_id, relationship);
        CREATE TABLE IF NOT EXISTS graph_unresolved (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
            relationship TEXT NOT NULL, resolution TEXT NOT NULL DEFAULT 'unresolved'
                CHECK(resolution='unresolved'), expression TEXT, reason TEXT NOT NULL,
            evidence_file TEXT NOT NULL, evidence_line INTEGER,
            evidence_start_byte INTEGER NOT NULL, evidence_end_byte INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS graph_snapshots (
            repository_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
            inventory_scanned_at TEXT NOT NULL, built_at TEXT NOT NULL,
            typescript_version TEXT NOT NULL, schema_version INTEGER NOT NULL,
            statistics_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(graph_unresolved)")}
    if "resolution" not in columns:
        db.execute("ALTER TABLE graph_unresolved ADD COLUMN resolution TEXT NOT NULL DEFAULT 'unresolved'")


def _semantic_pass(root: Path, files: list[str], configs: list[str]) -> dict[str, Any]:
    bridge = Path(__file__).with_name("semantic_resolver.cjs")
    process = subprocess.run(
        ["node", str(bridge)], input=json.dumps({"root": str(root), "files": files,
                                                   "configs": configs}), text=True,
        capture_output=True, timeout=180, check=False,
        env={**os.environ, "NODE_OPTIONS": ""},
    )
    if process.returncode:
        raise RuntimeError("TypeScript semantic pass failed: " + process.stderr[-3000:])
    return json.loads(process.stdout)


def _symbol_target(symbols_by_file: dict[str, list[sqlite3.Row]],
                   descriptor: dict | None) -> sqlite3.Row | None:
    if not descriptor or not descriptor.get("file"):
        return None
    start, end = descriptor["startByte"], descriptor["endByte"]
    name = descriptor.get("name")
    matches = []
    for symbol in symbols_by_file.get(descriptor["file"], []):
        if symbol["start_byte"] >= start and symbol["end_byte"] <= end or (
                symbol["start_byte"] <= start and symbol["end_byte"] >= end):
            if symbol["name"] == name or name in {"default", "__function"} and symbol["name"].startswith("<anonymous"):
                matches.append(symbol)
    if not matches:
        return None
    # Prefer the declaration itself over nested symbols with a coincident name.
    return min(matches, key=lambda item: (abs(item["start_byte"] - start)
                                          + abs(item["end_byte"] - end),
                                          item["end_byte"] - item["start_byte"]))


def _source_symbol(symbols_by_file: dict[str, list[sqlite3.Row]], file: str,
                   offset: int) -> sqlite3.Row | None:
    candidates = [symbol for symbol in symbols_by_file.get(file, [])
                  if symbol["start_byte"] <= offset < symbol["end_byte"]
                  and symbol["kind"] in FUNCTION_KINDS | CLASS_KINDS]
    return min(candidates, key=lambda item: item["end_byte"] - item["start_byte"]) if candidates else None


def build_graph(repo_root: str | Path, *, data_dir: str | Path | None = None,
                exclusions: tuple[str, ...] = (), incremental: bool = False) -> dict[str, Any]:
    """Scan, parse, semantically resolve, and atomically replace one graph snapshot."""
    inventory = parse_repository(repo_root, data_dir=data_dir, exclusions=exclusions, incremental=incremental)
    root = Path(inventory["root_path"])
    repo_id = inventory["repository_id"]
    db_path = inventory["database"]
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        _schema(db)
        file_rows = db.execute("SELECT relative_path, kind, status, extension, parse_status, "
                               "content_sha256 FROM files WHERE repository_id=? ORDER BY relative_path",
                               (repo_id,)).fetchall()
        included = [row for row in file_rows if row["status"] == "included"]
        source_files = [row["relative_path"] for row in included
                        if row["extension"] in LANGUAGES and row["parse_status"] == "parsed"]
        configs = [row["relative_path"] for row in included
                   if Path(row["relative_path"]).name.startswith("tsconfig")
                   and row["extension"] == ".json"]
        symbols = db.execute("SELECT * FROM symbols WHERE repository_id=?", (repo_id,)).fetchall()
        symbols_by_file: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for symbol in symbols:
            symbols_by_file[symbol["relative_path"]].append(symbol)

        semantic = _semantic_pass(root, source_files, configs)
        # Refuse to publish relationships if the disk changed after scanning.
        for row in included:
            if row["content_sha256"]:
                path = root / row["relative_path"]
                try:
                    current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                    safe_path = path.resolve(strict=True) == path
                except OSError as error:
                    raise RuntimeError(f"File unavailable during graph build: {row['relative_path']}") from error
                if not safe_path or current_hash != row["content_sha256"]:
                    raise RuntimeError(f"File changed during graph build: {row['relative_path']}")

        nodes: dict[str, tuple] = {}
        edges: dict[str, tuple] = {}
        unresolved: dict[str, tuple] = {}

        def node(kind: str, label: str, relative_path: str | None = None,
                 symbol_id: str | None = None, metadata: dict | None = None) -> str:
            key = _id(kind, repo_id, symbol_id or relative_path or label)
            nodes[key] = (key, repo_id, kind, label, relative_path, symbol_id,
                          json.dumps(metadata or {}, sort_keys=True))
            return key

        def edge(source_id: str, target_id: str, relationship: str, resolution: str,
                 evidence_type: str, file: str | None = None, line: int | None = None,
                 column: int | None = None, start: int | None = None,
                 end: int | None = None, metadata: dict | None = None) -> None:
            key = _id("rel", source_id, target_id, relationship, file or "", str(start))
            edges[key] = (key, repo_id, source_id, target_id, relationship, resolution,
                          evidence_type, file, line, column, start, end,
                          json.dumps(metadata or {}, sort_keys=True))

        def unresolved_ref(source_id: str, relationship: str, expression: str | None,
                           reason: str, file: str, start: int, end: int,
                           line: int | None) -> None:
            key = _id("unresolved", source_id, relationship, file, str(start), reason)
            unresolved[key] = (key, repo_id, source_id, relationship, "unresolved", expression, reason,
                               file, line, start, end)

        repo_node = node("Repository", root.name or str(root), metadata={"root": str(root)})
        directory_nodes = {"": repo_node}
        file_nodes = {}
        module_nodes = {}
        symbol_nodes = {}
        for row in included:
            file = row["relative_path"]
            parent = ""
            parts = Path(file).parts[:-1]
            for part in parts:
                current = f"{parent}/{part}".lstrip("/")
                if current not in directory_nodes:
                    directory_nodes[current] = node("Directory", part, current)
                    edge(directory_nodes[parent], directory_nodes[current], "CONTAINS",
                         "resolved", "inventory", current)
                parent = current
            file_nodes[file] = node("File", Path(file).name, file,
                                    metadata={"sha256": row["content_sha256"],
                                              "kind": row["kind"], "parse_status": row["parse_status"]})
            edge(directory_nodes[parent], file_nodes[file], "CONTAINS", "resolved", "inventory", file)
            if row["extension"] in LANGUAGES and row["parse_status"] == "parsed":
                module_nodes[file] = node("Module", file, file)
                edge(file_nodes[file], module_nodes[file], "DEFINES", "resolved", "inventory", file)

        for symbol in symbols:
            if symbol["relative_path"] not in module_nodes:
                continue
            kind = ("Class" if symbol["kind"] == "class" else
                    "Method" if symbol["kind"] in {"method", "method_signature"} else
                    "Interface" if symbol["kind"] == "interface" else
                    "Type" if symbol["kind"] in {"type_alias", "enum"} else "Function")
            symbol_nodes[symbol["id"]] = node(kind, symbol["qualified_name"],
                                               symbol["relative_path"], symbol["id"],
                                               {"syntax_kind": symbol["kind"]})
        for symbol in symbols:
            target = symbol_nodes.get(symbol["id"])
            if not target:
                continue
            parent_id = symbol["parent_symbol_id"]
            source = symbol_nodes.get(parent_id) if parent_id else module_nodes[symbol["relative_path"]]
            if source:
                edge(source, target, "DEFINES", "resolved", "syntax_definition",
                     symbol["relative_path"], symbol["start_line"], symbol["start_column"],
                     symbol["start_byte"], symbol["end_byte"])

        references = db.execute("SELECT relative_path, kind, start_byte, start_line, start_column, scope_symbol_id "
                                "FROM syntax_references WHERE repository_id=?", (repo_id,)).fetchall()
        ref_lookup = {(r["relative_path"], r["kind"], r["start_byte"]): r for r in references}

        for event in semantic["events"]:
            file = event["file"]
            module = module_nodes.get(file)
            if not module:
                continue
            kind = event["kind"]
            start, end = event["startByte"], event["endByte"]
            ref_kind = {"call": "call", "construction": "construction",
                        "inheritance": "inheritance"}.get(kind)
            ref = ref_lookup.get((file, ref_kind, start)) if ref_kind else None
            scope = ref["scope_symbol_id"] if ref else None
            enclosing = _source_symbol(symbols_by_file, file, start)
            source_node = symbol_nodes.get(scope) or (symbol_nodes.get(enclosing["id"]) if enclosing else None) or module
            line = ref["start_line"] if ref else None
            if line is None:
                # Byte offsets are from the same snapshot, so line lookup is exact.
                content = (root / file).read_bytes()
                line = content.count(b"\n", 0, start) + 1

            if kind == "import":
                specifier = event["specifier"]
                target_file = (event.get("targetModule") or {}).get("file")
                if target_file in module_nodes:
                    target_node = module_nodes[target_file]
                    edge(module, target_node, "IMPORTS", "resolved", "semantic_module_resolution",
                         file, line, None, start, end, {"specifier": specifier,
                                                       "reexport": bool(event.get("reexport")),
                                                       "dynamic": bool(event.get("dynamic")),
                                                       "commonjs": bool(event.get("commonjs"))})
                    edge(module, target_node, "DEPENDS_ON", "resolved", "derived_import",
                         file, line, None, start, end, {"specifier": specifier})
                elif not specifier.startswith((".", "/")) and not event.get("alias"):
                    external = node("ExternalDependency", specifier,
                                    metadata={"specifier": specifier, "installed": "unverified"})
                    edge(module, external, "IMPORTS", "inferred", "package_specifier",
                         file, line, None, start, end, {"specifier": specifier})
                else:
                    unresolved_ref(module, "IMPORTS", specifier, "local_module_not_indexed_or_missing",
                                   file, start, end, line)
                continue

            if kind == "unresolved":
                unresolved_ref(source_node, event["relationship"], None, event["reason"],
                               file, start, end, line)
                continue

            target = _symbol_target(symbols_by_file, event.get("target"))
            target_node = symbol_nodes.get(target["id"]) if target else None
            if kind == "export":
                if target_node:
                    edge(module, target_node, "EXPORTS", "resolved", "semantic_export",
                         file, line, None, start, end, {"exported_name": event["exportedName"]})
                else:
                    unresolved_ref(module, "EXPORTS", event["exportedName"],
                                   "export_target_not_indexed", file, start, end, line)
                continue
            if kind == "inheritance":
                source_class = _symbol_target(symbols_by_file, event["sourceDeclaration"])
                source_node = symbol_nodes.get(source_class["id"]) if source_class else module
                relationship = event["relationship"]
            elif kind == "call":
                relationship = "CALLS"
            elif kind in {"construction", "reference"}:
                relationship = "REFERENCES"
            else:
                continue

            if target_node and target["relative_path"] in module_nodes:
                edge(source_node, target_node, relationship, "resolved", "semantic_reference",
                     file, line, ref["start_column"] if ref else None, start, end,
                     {"expression": event.get("expression"), "construct": kind})
            elif kind != "reference":
                unresolved_ref(source_node, relationship, event.get("expression"),
                               "target_not_uniquely_indexed", file, start, end, line)

        stats = {"nodes": len(nodes), "relationships": len(edges),
                 "resolved": sum(row[5] == "resolved" for row in edges.values()),
                 "inferred": sum(row[5] == "inferred" for row in edges.values()),
                 "unresolved": len(unresolved)}
        with db:
            snapshot_id = begin_snapshot(db, repo_id, 'graph', observed_at=inventory['scanned_at'], revision=inventory['git_revision'])
            db.execute("DELETE FROM graph_relationships WHERE repository_id=?", (repo_id,))
            db.execute("DELETE FROM graph_unresolved WHERE repository_id=?", (repo_id,))
            db.execute("DELETE FROM graph_nodes WHERE repository_id=?", (repo_id,))
            db.executemany("INSERT INTO graph_nodes VALUES (?,?,?,?,?,?,?)", nodes.values())
            db.executemany("INSERT INTO graph_relationships VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           edges.values())
            db.executemany("""INSERT INTO graph_unresolved
                (id, repository_id, source_id, relationship, resolution, expression, reason,
                 evidence_file, evidence_line, evidence_start_byte, evidence_end_byte)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           unresolved.values())
            publish_graph(db, repo_id, snapshot_id)
            for diagnostic in semantic['diagnostics']:
                db.execute("INSERT INTO index_errors(repository_id,snapshot_id,stage,kind,detail) VALUES (?,?,'graph','typescript_diagnostic',?)",
                           (repo_id,snapshot_id,json.dumps(diagnostic)))
            finish_snapshot(db, snapshot_id)
            db.execute("""INSERT INTO graph_snapshots VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(repository_id) DO UPDATE SET
                inventory_scanned_at=excluded.inventory_scanned_at, built_at=excluded.built_at,
                typescript_version=excluded.typescript_version, schema_version=excluded.schema_version,
                statistics_json=excluded.statistics_json, diagnostics_json=excluded.diagnostics_json""",
                (repo_id, inventory["scanned_at"], datetime.now(timezone.utc).isoformat(),
                 semantic["typescriptVersion"], GRAPH_SCHEMA_VERSION,
                 json.dumps(stats), json.dumps(semantic["diagnostics"])))
            db.execute("UPDATE repositories SET schema_version=? WHERE id=?",
                       (GRAPH_SCHEMA_VERSION, repo_id))
    return {**inventory, "graph": stats, "semantic_diagnostics": semantic["diagnostics"],
            "typescript_version": semantic["typescriptVersion"], "graph_snapshot_id": snapshot_id}


class KnowledgeGraph:
    """Read-only graph queries; rejects snapshots stale relative to the inventory."""

    def __init__(self, database: str | Path, repository_id: str):
        self.database = str(database)
        self.repository_id = repository_id

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        try:
            current = db.execute("""SELECT r.root_path, r.last_scanned_at, s.inventory_scanned_at
                FROM repositories r LEFT JOIN graph_snapshots s ON s.repository_id=r.id
                WHERE r.id=?""", (self.repository_id,)).fetchone()
        except sqlite3.Error as error:
            db.close()
            raise RuntimeError('Graph is not indexed yet') from error
        if not current or current["last_scanned_at"] != current["inventory_scanned_at"]:
            db.close()
            raise RuntimeError("Graph is missing or stale; rebuild it after scanning")
        root = Path(current["root_path"])
        for row in db.execute("""SELECT relative_path, content_sha256 FROM files
                WHERE repository_id=? AND status='included' AND content_sha256 IS NOT NULL""",
                (self.repository_id,)):
            file = root / row["relative_path"]
            try:
                resolved = file.resolve(strict=True)
                if resolved != file or (
                        hashlib.sha256(file.read_bytes()).hexdigest() != row["content_sha256"]):
                    raise RuntimeError("Graph is stale; a source file changed")
            except OSError as error:
                db.close()
                raise RuntimeError("Graph is stale; a source file is unavailable") from error
            except RuntimeError:
                db.close()
                raise
        return db

    def symbol(self, name: str) -> dict[str, Any]:
        with closing(self._connection()) as db:
            rows = db.execute("""SELECT n.* FROM graph_nodes n JOIN symbols s ON s.id=n.symbol_id
                WHERE n.repository_id=? AND (s.name=? OR s.qualified_name=? OR s.id=?)""",
                (self.repository_id, name, name, name)).fetchall()
        if len(rows) != 1:
            raise ValueError(f"Expected one symbol for {name!r}; found {len(rows)}")
        return dict(rows[0])

    def _edges(self, node_id: str, relationship: str, *, reverse: bool = False,
               include_inferred: bool = False) -> list[dict[str, Any]]:
        column = "target_id" if reverse else "source_id"
        with closing(self._connection()) as db:
            rows = db.execute(f"""SELECT e.*, n.kind AS other_kind, n.label AS other_label, s.kind AS other_symbol_kind,
                n.relative_path AS other_path FROM graph_relationships e
                JOIN graph_nodes n ON n.id=e.{'source_id' if reverse else 'target_id'}
                LEFT JOIN symbols s ON s.id=n.symbol_id
                WHERE e.repository_id=? AND e.{column}=? AND e.relationship=?
                AND (e.resolution='resolved' OR ?=1)
                ORDER BY e.evidence_file, e.evidence_line, n.label""",
                (self.repository_id, node_id, relationship, int(include_inferred))).fetchall()
        return [dict(row) for row in rows]

    def direct_callers(self, symbol_id: str) -> list[dict[str, Any]]:
        with closing(self._connection()) as db:
            row = db.execute("SELECT id FROM graph_nodes WHERE repository_id=? AND symbol_id=?",
                             (self.repository_id, symbol_id)).fetchone()
        return self._edges(row[0], "CALLS", reverse=True) if row else []

    def direct_callees(self, symbol_id: str) -> list[dict[str, Any]]:
        with closing(self._connection()) as db:
            row = db.execute("SELECT id FROM graph_nodes WHERE repository_id=? AND symbol_id=?",
                             (self.repository_id, symbol_id)).fetchone()
        return self._edges(row[0], "CALLS") if row else []

    def imports(self, module_path: str, *, include_inferred: bool = False) -> list[dict[str, Any]]:
        return self._edges(_id("Module", self.repository_id, module_path), "IMPORTS",
                           include_inferred=include_inferred)

    def reverse_dependencies(self, module_path: str) -> list[dict[str, Any]]:
        return self._edges(_id("Module", self.repository_id, module_path), "DEPENDS_ON", reverse=True)

    def unresolved_references(self, *, symbol_id: str | None = None,
                              module_path: str | None = None) -> list[dict[str, Any]]:
        """Expose missing targets and reasons without fabricating graph edges."""
        if symbol_id and module_path:
            raise ValueError("Choose a symbol or module, not both")
        with closing(self._connection()) as db:
            if symbol_id:
                node = db.execute("SELECT id FROM graph_nodes WHERE repository_id=? AND symbol_id=?",
                                  (self.repository_id, symbol_id)).fetchone()
                node_id = node[0] if node else ""
            elif module_path:
                node_id = _id("Module", self.repository_id, module_path)
            else:
                node_id = None
            rows = db.execute("""SELECT * FROM graph_unresolved WHERE repository_id=?
                AND (? IS NULL OR source_id=?) ORDER BY evidence_file, evidence_line""",
                (self.repository_id, node_id, node_id)).fetchall()
        return [dict(row) for row in rows]

    def dependency_paths(self, source_path: str, target_path: str,
                         max_depth: int = 5, max_paths: int = 100) -> list[list[str]]:
        if max_depth < 1 or max_depth > 20:
            raise ValueError("max_depth must be between 1 and 20")
        if max_paths < 1 or max_paths > 1000:
            raise ValueError("max_paths must be between 1 and 1000")
        start = _id("Module", self.repository_id, source_path)
        goal = _id("Module", self.repository_id, target_path)
        with closing(self._connection()) as db:
            adjacency: dict[str, list[str]] = defaultdict(list)
            names = {row["id"]: row["relative_path"] for row in db.execute(
                "SELECT id, relative_path FROM graph_nodes WHERE repository_id=? AND kind='Module'",
                (self.repository_id,))}
            for row in db.execute("""SELECT source_id, target_id FROM graph_relationships
                WHERE repository_id=? AND relationship='DEPENDS_ON' AND resolution='resolved'""",
                (self.repository_id,)):
                adjacency[row["source_id"]].append(row["target_id"])
        if start not in names or goal not in names:
            return []
        queue = deque([(start, [start])])
        paths = []
        examined = 0
        while queue:
            examined += 1
            if examined > 50_000:
                raise RuntimeError("Dependency path search budget exceeded; lower max_depth")
            current, path = queue.popleft()
            if current == goal and len(path) > 1:
                paths.append([names[item] for item in path])
                if len(paths) >= max_paths:
                    break
                continue
            if len(path) - 1 >= max_depth:
                continue
            for neighbor in adjacency[current]:
                if neighbor not in path:
                    queue.append((neighbor, path + [neighbor]))
        return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a verified local code graph")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(build_graph(args.repo, data_dir=args.data_dir,
                                 exclusions=tuple(args.exclude)), indent=2))


if __name__ == "__main__":
    main()
