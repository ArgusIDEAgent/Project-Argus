"""Canonical source expansions for the default answer path."""

import sqlite3

from knowledge_base.metadata import trace
from knowledge_base.semantic import matches_filters

_PROOF_FIELDS = ("entityId", "fileId", "symbolId", "snapshotId", "sourceOrigin", "schemaVersion",
                 "path", "startLine", "endLine", "sourceHash")


def _source(proof, item, **display):
    return {**item, **{key: proof[key] for key in _PROOF_FIELDS}, **display}


def attach_answer_context(db, database, repo_id, item, filters):
    """Attach a complete declaration and bounded, independently citable call sites."""
    if item.get("sourceOrigin") == "current_disk" or not item.get("name"):
        return
    # A stored chunk may predate canonical Python symbols. A unique, same-file
    # definition containing its indexed lines can still identify its owner.
    symbols = db.execute("""SELECT s.id,s.qualified_name,s.kind,n.id AS node_id FROM symbols s
        JOIN files f ON f.repository_id=s.repository_id AND f.relative_path=s.relative_path
        LEFT JOIN graph_nodes n ON n.symbol_id=s.id AND n.repository_id=s.repository_id
        WHERE s.repository_id=? AND s.relative_path=? AND f.parse_status='parsed'
          AND (s.id=? OR (s.qualified_name=? AND s.start_line<=? AND s.end_line>=?))
        LIMIT 2""", (repo_id, item["path"], item.get("symbolId"), item["name"],
                    item["startLine"], item["endLine"])).fetchall()
    if len(symbols) != 1:
        return
    symbol = symbols[0]
    definition = trace(database, repo_id, symbol["id"])
    if (definition["sourceHash"] != item["sourceHash"] or definition["path"] != item["path"]
            or not definition["startLine"] <= item["startLine"] <= item["endLine"] <= definition["endLine"]):
        return
    item["_answerDefinition"] = _source(definition, item, name=symbol["qualified_name"],
        symbolType=symbol["kind"], reason="Verified source declaration")
    calls = []
    # Explicit file/module or symbol-type filters scope the evidence selection.
    if not symbol["node_id"] or any(filters.get(key) for key in ("files", "modules", "symbolTypes")):
        return
    rows = db.execute("""SELECT e.id, n.label, f.language FROM graph_relationships e
        JOIN graph_nodes n ON n.id=e.source_id
        JOIN files f ON f.repository_id=e.repository_id AND f.relative_path=e.evidence_file
        WHERE e.repository_id=? AND e.target_id=? AND e.resolution='resolved' AND e.relationship='CALLS'
        ORDER BY e.evidence_file,e.evidence_line,e.id LIMIT 4""", (repo_id, symbol["node_id"])).fetchall()
    for row in rows:
        if not matches_filters({"language": row["language"]}, filters):
            continue
        try:
            proof = trace(database, repo_id, row["id"])
        except (OSError, ValueError, sqlite3.Error):
            continue
        if proof.get("resolution") != "resolved" or proof.get("relationship") != "CALLS":
            continue
        calls.append(_source(proof, {}, name=row["label"], language=row["language"],
            callers=[], tests=[], reason="Verified static call site", revision="latest",
            graphPath=[row["label"], symbol["qualified_name"]], retrievalMethods=["graph"]))
    item["_answerCalls"] = calls
