"""Phase 2: Tree-sitter syntax inventory for the Phase 1 file inventory.

Definitions are symbols; syntactic uses are references with no resolved target.
Run: python -m knowledge_base.parser --repo /absolute/path/to/repository
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript
import tree_sitter_typescript

from knowledge_base.scanner import SOURCE_EXTENSIONS, scan_repository


LANGUAGES = {
    ".js": Language(tree_sitter_javascript.language()),
    ".jsx": Language(tree_sitter_javascript.language()),
    ".mjs": Language(tree_sitter_javascript.language()),
    ".cjs": Language(tree_sitter_javascript.language()),
    ".ts": Language(tree_sitter_typescript.language_typescript()),
    ".mts": Language(tree_sitter_typescript.language_typescript()),
    ".cts": Language(tree_sitter_typescript.language_typescript()),
    ".tsx": Language(tree_sitter_typescript.language_tsx()),
}
from knowledge_base.identity import symbol_identity
from knowledge_base.metadata import SCHEMA_VERSION, begin_snapshot, finish_snapshot, publish_syntax

SYNTAX_SCHEMA_VERSION = SCHEMA_VERSION

DEFINITIONS = {
    "function_declaration": "function", "generator_function_declaration": "function",
    "function_signature": "function_overload", "method_definition": "method",
    "method_signature": "method_signature", "abstract_method_signature": "method_signature",
    "arrow_function": "arrow_function", "function_expression": "function_expression",
    "generator_function": "function_expression", "class_declaration": "class",
    "class": "class", "abstract_class_declaration": "class",
    "interface_declaration": "interface", "type_alias_declaration": "type_alias",
    "enum_declaration": "enum",
}
FUNCTION_KINDS = {"function", "function_overload", "method", "method_signature",
                  "arrow_function", "function_expression"}
CLASS_KINDS = {"class", "interface", "enum"}
REFERENCE_KINDS = {"call_expression": "call", "new_expression": "construction",
                   "decorator": "decorator"}


@dataclass
class ParsedFile:
    symbols: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    status: str = "parsed"


def _text(source: bytes, node: Node | None) -> str | None:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace") if node else None


def _name(source: bytes, node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {"identifier", "type_identifier", "property_identifier",
                     "private_property_identifier", "shorthand_property_identifier_pattern"}:
        return _text(source, node)
    if node.type in {"assignment_pattern", "required_parameter", "optional_parameter",
                     "rest_pattern"}:
        return _name(source, node.child_by_field_name("pattern")
                     or node.child_by_field_name("left") or next(iter(node.named_children), None))
    return _text(source, node)


def _span(source: bytes, node: Node) -> dict[str, int]:
    # Tree-sitter points are UTF-8 byte columns. Persist human-facing Unicode columns.
    def point(row: int, byte_col: int) -> tuple[int, int]:
        line_start = source.rfind(b"\n", 0, node.start_byte if row == node.start_point.row else node.end_byte) + 1
        return row + 1, len(source[line_start:line_start + byte_col].decode("utf-8", "replace")) + 1

    start_line, start_column = point(node.start_point.row, node.start_point.column)
    end_line, end_column = point(node.end_point.row, node.end_point.column)
    return dict(start_line=start_line, start_column=start_column,
                end_line=end_line, end_column=end_column,
                start_byte=node.start_byte, end_byte=node.end_byte)


def _parameters(source: bytes, node: Node) -> list[dict[str, Any]]:
    params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
    if not params:
        return []
    children = params.named_children if params.type == "formal_parameters" else [params]
    result = []
    for child in children:
        pattern = child.child_by_field_name("pattern") or child.child_by_field_name("left")
        annotation = child.child_by_field_name("type")
        if pattern is None and child.type == "assignment_pattern":
            pattern = child.child_by_field_name("left")
        result.append({"name": _name(source, pattern or child),
                       "type": _text(source, annotation), "optional": child.type == "optional_parameter",
                       "rest": child.type == "rest_pattern" or _text(source, child).startswith("..."),
                       "span": _span(source, child)})
    return result


def _attached_comment(source: bytes, node: Node) -> str | None:
    if node.parent and node.parent.type == "export_statement":
        node = node.parent
    previous = node.prev_named_sibling
    if previous is None or previous.type != "comment":
        return None
    between = source[previous.end_byte:node.start_byte]
    if between.strip() or between.count(b"\n") > 2:
        return None
    return _text(source, previous)


def _declaration_name(source: bytes, node: Node) -> str | None:
    direct = node.child_by_field_name("name")
    if direct:
        return _name(source, direct)
    if node.type in {"arrow_function", "function_expression", "generator_function", "class"}:
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            return _name(source, parent.child_by_field_name("name"))
        if parent and parent.type == "field_definition":
            return _name(source, parent.child_by_field_name("property"))
        if parent and parent.type == "pair":
            return _name(source, parent.child_by_field_name("key"))
    return None


def _export_context(node: Node) -> tuple[bool, bool]:
    parent = node.parent
    while parent and parent.type in {"lexical_declaration", "variable_declarator"}:
        parent = parent.parent
    if parent and parent.type == "export_statement":
        return True, any(child.type == "default" for child in parent.children)
    return False, False


def parse_source(source: bytes, *, suffix: str, repository_id: str,
                 relative_path: str) -> ParsedFile:
    """Extract syntax, with 1-based Unicode columns and half-open end positions."""
    if suffix not in LANGUAGES:
        raise ValueError(f"Unsupported source extension: {suffix}")
    tree = Parser(LANGUAGES[suffix]).parse(source)
    result = ParsedFile(status="partial" if tree.root_node.has_error else "parsed")
    occurrences: dict[tuple[str | None, str, str, str], int] = defaultdict(int)

    def identifier(kind: str, name: str, parent_id: str | None, signature: str) -> tuple[str, str]:
        key = (parent_id, kind, name, signature)
        occurrences[key] += 1
        return symbol_identity(repository_id, relative_path, parent_id, kind, name, signature, occurrences[key])

    def add_ref(kind: str, node: Node, scope: str | None, expression: str | None,
                metadata: dict[str, Any] | None = None) -> None:
        result.references.append({"kind": kind, "scope_symbol_id": scope,
                                  "expression": expression, "metadata": metadata or {},
                                  **_span(source, node)})

    def visit(node: Node, scope: str | None, qualifier: str | None) -> None:
        if node.type == "ERROR" or node.is_missing:
            result.diagnostics.append({"kind": "syntax_error", "node_type": node.type,
                                       **_span(source, node)})
        if node.type == "comment":
            result.comments.append({"text": _text(source, node), **_span(source, node)})

        if node.type in DEFINITIONS:
            kind = DEFINITIONS[node.type]
            name = _declaration_name(source, node)
            # Anonymous callbacks get an explicit scoped identity. The ordinal is
            # relative to their enclosing declaration, never their line number.
            display = name or "<anonymous>"
            params = _parameters(source, node) if kind in FUNCTION_KINDS else []
            annotation = _text(source, node.child_by_field_name("return_type"))
            signature = (json.dumps([[p["type"], p["optional"], p["rest"]] for p in params]
                                    + [annotation], ensure_ascii=False)
                         if kind in {"function_overload", "method_signature"} else "")
            symbol_id, identity = identifier(kind, display, scope, signature)
            if not name:
                display = f"<anonymous:{occurrences[(scope, kind, '<anonymous>', signature)]}>"
            qualified = f"{qualifier}.{display}" if qualifier else display
            exported, default = _export_context(node)
            modifier_tokens = {"async", "static", "abstract", "readonly", "get", "set", "declare"}
            modifiers = [child.type for child in node.children if child.type in modifier_tokens]
            result.symbols.append({"id": symbol_id, "identity_key": identity, "kind": kind,
                                   "name": display, "qualified_name": qualified,
                                   "parent_symbol_id": scope, "parameters": params,
                                   "return_annotation": annotation,
                                   "modifiers": modifiers, "exported": exported,
                                   "default_export": default,
                                   "doc_comment": _attached_comment(source, node),
                                   **_span(source, node)})
            scope, qualifier = symbol_id, qualified

        if node.type in REFERENCE_KINDS:
            kind = REFERENCE_KINDS[node.type]
            target = node.child_by_field_name("function" if kind == "call" else "constructor")
            if kind == "decorator":
                target = next(iter(node.named_children), None)
            expression = _text(source, target)
            if kind == "call" and target and target.type == "import":
                kind = "dynamic_import"
            add_ref(kind, node, scope, expression,
                    {"syntactic_only": True, "target_node_type": target.type if target else None})

        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            module = _text(source, source_node)
            specifiers = [child for child in node.named_children if child.type == "import_clause"]
            if not specifiers:
                add_ref("import", node, scope, module, {"module": module, "side_effect_only": True})
            for clause in specifiers:
                for child in clause.named_children:
                    if child.type == "named_imports":
                        for spec in child.named_children:
                            imported = _text(source, spec.child_by_field_name("name"))
                            local = _text(source, spec.child_by_field_name("alias")) or imported
                            add_ref("import", spec, scope, local,
                                    {"module": module, "imported": imported, "local": local})
                    elif child.type == "namespace_import":
                        local = _text(source, child.named_children[-1]) if child.named_children else None
                        add_ref("import", child, scope, local,
                                {"module": module, "imported": "*", "local": local})
                    else:
                        local = _text(source, child)
                        add_ref("import", child, scope, local,
                                {"module": module, "imported": "default", "local": local})

        if node.type == "export_statement":
            export_clause = next((child for child in node.named_children
                                  if child.type == "export_clause"), None)
            module = _text(source, node.child_by_field_name("source"))
            if export_clause:
                for spec in export_clause.named_children:
                    local = _text(source, spec.child_by_field_name("name"))
                    exported = _text(source, spec.child_by_field_name("alias")) or local
                    add_ref("export", spec, scope, exported,
                            {"local": local, "exported": exported, "module": module})
            elif node.child_by_field_name("declaration") is None:
                add_ref("export", node, scope, "*" if module else "default",
                        {"module": module, "star": module is not None})

        if node.type in {"extends_clause", "implements_clause", "extends_type_clause"}:
            for child in node.named_children:
                add_ref("inheritance", child, scope, _text(source, child),
                        {"relation": "implements" if node.type == "implements_clause" else "extends"})
        if node.type == "class_heritage" and not any(
                child.type in {"extends_clause", "implements_clause"} for child in node.named_children):
            for child in node.named_children:
                add_ref("inheritance", child, scope, _text(source, child), {"relation": "extends"})

        for child in node.named_children:
            visit(child, scope, qualifier)

    visit(tree.root_node, None, None)
    return result


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, relative_path TEXT NOT NULL,
            identity_key TEXT NOT NULL, parent_symbol_id TEXT, kind TEXT NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT NOT NULL, parameters_json TEXT NOT NULL,
            return_annotation TEXT, modifiers_json TEXT NOT NULL,
            exported INTEGER NOT NULL, default_export INTEGER NOT NULL, doc_comment TEXT,
            start_line INTEGER NOT NULL, start_column INTEGER NOT NULL,
            end_line INTEGER NOT NULL, end_column INTEGER NOT NULL,
            start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL,
            FOREIGN KEY (repository_id, relative_path) REFERENCES files(repository_id, relative_path) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS symbols_file_idx ON symbols(repository_id, relative_path);
        CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols(repository_id, name);
        CREATE TABLE IF NOT EXISTS syntax_references (
            id INTEGER PRIMARY KEY, repository_id TEXT NOT NULL, relative_path TEXT NOT NULL,
            scope_symbol_id TEXT, kind TEXT NOT NULL, expression TEXT,
            metadata_json TEXT NOT NULL, start_line INTEGER NOT NULL, start_column INTEGER NOT NULL,
            end_line INTEGER NOT NULL, end_column INTEGER NOT NULL,
            start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL,
            FOREIGN KEY (repository_id, relative_path) REFERENCES files(repository_id, relative_path) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS syntax_refs_file_idx ON syntax_references(repository_id, relative_path);
        CREATE TABLE IF NOT EXISTS syntax_comments (
            id INTEGER PRIMARY KEY, repository_id TEXT NOT NULL, relative_path TEXT NOT NULL,
            text TEXT NOT NULL, start_line INTEGER NOT NULL, start_column INTEGER NOT NULL,
            end_line INTEGER NOT NULL, end_column INTEGER NOT NULL,
            start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL,
            FOREIGN KEY (repository_id, relative_path) REFERENCES files(repository_id, relative_path) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS parse_diagnostics (
            id INTEGER PRIMARY KEY, repository_id TEXT NOT NULL, relative_path TEXT NOT NULL,
            kind TEXT NOT NULL, detail TEXT, start_line INTEGER, start_column INTEGER,
            end_line INTEGER, end_column INTEGER, start_byte INTEGER, end_byte INTEGER,
            FOREIGN KEY (repository_id, relative_path) REFERENCES files(repository_id, relative_path) ON DELETE CASCADE
        );
    """)
    db.execute("UPDATE repositories SET schema_version=? WHERE schema_version<?",
               (SYNTAX_SCHEMA_VERSION, SYNTAX_SCHEMA_VERSION))


def parse_repository(repo_root: str | Path, *, data_dir: str | Path | None = None,
                     exclusions: tuple[str, ...] = ()) -> dict[str, Any]:
    """Refresh inventory, then replace each file's syntax rows transactionally."""
    inventory = scan_repository(repo_root, data_dir=data_dir, exclusions=exclusions)
    root = Path(inventory["root_path"])
    repo_id = inventory["repository_id"]
    counts = {"parsed": 0, "partial": 0, "error": 0, "symbols": 0,
              "references": 0, "comments": 0}
    with closing(sqlite3.connect(inventory["database"])) as db:
        db.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(db)
        with db:
            snapshot_id = begin_snapshot(db, repo_id, 'syntax', observed_at=inventory['scanned_at'], revision=inventory['git_revision'])
        files = db.execute("SELECT relative_path, content_sha256 FROM files WHERE repository_id=? "
                           "AND status='included' AND extension IN (?,?,?,?,?,?,?,?) "
                           "ORDER BY relative_path", (repo_id, *LANGUAGES)).fetchall()
        for relative_path, expected_hash in files:
            suffix = Path(relative_path).suffix.lower()
            try:
                path = root / relative_path
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(path, flags), "rb") as handle:
                    source = handle.read()
                if hashlib.sha256(source).hexdigest() != expected_hash:
                    raise ValueError("changed_since_inventory")
                if suffix not in SOURCE_EXTENSIONS:
                    raise ValueError("unsupported_extension")
                parsed = parse_source(source, suffix=suffix, repository_id=repo_id,
                                      relative_path=relative_path)
            except (OSError, ValueError) as error:
                parsed = ParsedFile(status="error", diagnostics=[{"kind": "parse_error",
                    "detail": str(error) if isinstance(error, ValueError) else type(error).__name__}])

            with db:
                current_ids = {s['id'] for s in parsed.symbols}
                old_ids = {r[0] for r in db.execute("SELECT id FROM symbols WHERE repository_id=? AND relative_path=?", (repo_id,relative_path))}
                db.executemany("DELETE FROM symbols WHERE id=?", [(sid,) for sid in old_ids-current_ids])
                for table in ("syntax_references", "syntax_comments", "parse_diagnostics"):
                    db.execute(f"DELETE FROM {table} WHERE repository_id=? AND relative_path=?",
                               (repo_id, relative_path))
                db.executemany("""INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET identity_key=excluded.identity_key,
                    parent_symbol_id=excluded.parent_symbol_id,kind=excluded.kind,name=excluded.name,
                    qualified_name=excluded.qualified_name,parameters_json=excluded.parameters_json,
                    return_annotation=excluded.return_annotation,modifiers_json=excluded.modifiers_json,
                    exported=excluded.exported,default_export=excluded.default_export,doc_comment=excluded.doc_comment,
                    start_line=excluded.start_line,start_column=excluded.start_column,end_line=excluded.end_line,
                    end_column=excluded.end_column,start_byte=excluded.start_byte,end_byte=excluded.end_byte""",
                               [(s["id"], repo_id, relative_path, s["identity_key"], s["parent_symbol_id"],
                                 s["kind"], s["name"], s["qualified_name"], json.dumps(s["parameters"]),
                                 s["return_annotation"], json.dumps(s["modifiers"]), int(s["exported"]),
                                 int(s["default_export"]), s["doc_comment"],
                                 *(s[k] for k in ("start_line", "start_column", "end_line", "end_column",
                                                 "start_byte", "end_byte"))) for s in parsed.symbols])
                db.executemany("""INSERT INTO syntax_references
                    (repository_id, relative_path, scope_symbol_id, kind, expression, metadata_json,
                     start_line, start_column, end_line, end_column, start_byte, end_byte)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(repo_id, relative_path, r["scope_symbol_id"], r["kind"], r["expression"],
                      json.dumps(r["metadata"]), *(r[k] for k in ("start_line", "start_column",
                      "end_line", "end_column", "start_byte", "end_byte"))) for r in parsed.references])
                db.executemany("""INSERT INTO syntax_comments
                    (repository_id, relative_path, text, start_line, start_column, end_line,
                     end_column, start_byte, end_byte) VALUES (?,?,?,?,?,?,?,?,?)""",
                    [(repo_id, relative_path, c["text"], *(c[k] for k in ("start_line", "start_column",
                      "end_line", "end_column", "start_byte", "end_byte"))) for c in parsed.comments])
                db.executemany("""INSERT INTO parse_diagnostics
                    (repository_id, relative_path, kind, detail, start_line, start_column,
                     end_line, end_column, start_byte, end_byte) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    [(repo_id, relative_path, d["kind"], d.get("detail", d.get("node_type")),
                      *(d.get(k) for k in ("start_line", "start_column", "end_line",
                        "end_column", "start_byte", "end_byte"))) for d in parsed.diagnostics])
                db.execute("UPDATE files SET parse_status=? WHERE repository_id=? AND relative_path=?",
                           (parsed.status, repo_id, relative_path))
                publish_syntax(db, repo_id, relative_path, snapshot_id, expected_hash)
            counts[parsed.status] += 1
            counts["symbols"] += len(parsed.symbols)
            counts["references"] += len(parsed.references)
            counts["comments"] += len(parsed.comments)
        with db:
            finish_snapshot(db, snapshot_id)
    return {**inventory, "parsing": counts, "syntax_snapshot_id": snapshot_id}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local JavaScript/TypeScript syntax inventory")
    parser.add_argument("--repo", required=True, help="Local repository root")
    parser.add_argument("--data-dir", help="Private data directory outside the repository")
    parser.add_argument("--exclude", action="append", default=[], help="Extra glob to exclude")
    args = parser.parse_args()
    print(json.dumps(parse_repository(args.repo, data_dir=args.data_dir,
                                      exclusions=tuple(args.exclude)), indent=2))


if __name__ == "__main__":
    main()
