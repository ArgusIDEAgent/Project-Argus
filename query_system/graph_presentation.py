"""Structured, deterministic graph summaries; no database access or generation."""

from pydantic import Field

from query_system.contracts import (
    ContractModel, EngineResult, Evidence, ResultStatus, SnapshotMismatchError,
    UnknownEvidenceError,
)


class GraphEntry(ContractModel):
    canonical_id: str
    label: str
    sources: list[Evidence] = Field(default_factory=list)
    path: list[str] = Field(default_factory=list)


class GraphPresentation(ContractModel):
    status: ResultStatus
    summary: str
    entries: list[GraphEntry]
    warnings: list[str]
    unresolved_items: list[str]


def format_graph_result(operation: str, result: EngineResult,
                        evidence: dict[str, Evidence]) -> GraphPresentation:
    for identifier in result.evidence_ids:
        if identifier not in evidence:
            raise UnknownEvidenceError("Presentation has missing evidence")
        item = evidence[identifier]
        if (item.repository_id, item.snapshot_id) != (result.repository_id, result.snapshot_id):
            raise SnapshotMismatchError("Presentation cannot mix repositories or generations")
    entries = {}
    incoming = operation in ("find_callers", "find_importers", "find_reverse_dependencies", "find_references")
    for finding in result.findings:
        if finding.kind != "graph":
            raise ValueError("Graph presentation requires graph findings")
        if finding.path_node_ids:
            key = tuple(finding.path_node_ids)
            label = " → ".join(finding.path_names)
            identity = finding.path_node_ids[-1]
        elif finding.entity_id:
            key, identity = finding.entity_id, finding.entity_id
            label = finding.entity_name or identity
        else:
            identity = (finding.source_symbol_id or finding.source_node_id) if incoming else (finding.target_symbol_id or finding.target_node_id)
            key = identity
            label = (finding.source_name if incoming else finding.target_name) or identity
        if key not in entries:
            entries[key] = GraphEntry(canonical_id=identity, label=label, path=finding.path_names)
        entry = entries[key]
        seen = {item.evidence_id for item in entry.sources}
        entry.sources.extend(evidence[identifier] for identifier in finding.evidence_ids if identifier not in seen)
    nouns = {
        "find_definition": "definitions", "find_callers": "direct callers",
        "find_callees": "direct callees", "find_imports": "imports",
        "find_importers": "importing modules", "find_dependencies": "direct dependencies",
        "find_reverse_dependencies": "direct dependents", "find_references": "reference owners",
        "find_paths": "static call paths", "find_dependency_paths": "module dependency paths",
    }
    noun = nouns.get(operation, "graph results")
    summary = f"Known {noun}: {len(entries)}"
    if result.status == "ambiguous":
        summary = "Multiple definitions match; select a canonical identity or file scope"
    elif result.status == "not_found":
        summary = f"No verified {noun} found within the requested scope and bounds"
    elif result.status in ("unsupported", "stale_index", "error"):
        summary = result.error.message if result.error else result.status
    elif result.status == "partial":
        summary += "; results are partial"
    warnings = list(result.warnings)
    if result.status == "partial" and result.error:
        warnings.append(result.error.message)
    return GraphPresentation(status=result.status, summary=summary, entries=list(entries.values()),
                             warnings=warnings, unresolved_items=result.unresolved_items)
