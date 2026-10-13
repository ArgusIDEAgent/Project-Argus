"""Phase 1 contracts. A knowledge snapshot is a published generation, not a stage ID.

Requests describe client intent. SnapshotContext must be created on the server
inside SnapshotStore.pin_view(); it is the authority for engine results.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from knowledge_base.identity import relative_path


RepositoryId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{24}$")]
GenerationId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
StageSnapshotId = Annotated[str, StringConstraints(min_length=1)]
SourceHash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Depth = Literal["quick", "standard", "deep"]
EngineName = Literal["graph", "semantic", "rules", "graph_rag"]
ResultStatus = Literal["success", "partial", "not_found", "ambiguous", "unsupported", "stale_index", "error"]
EvidenceType = Literal["symbol_definition", "call_site", "graph_relationship", "code_chunk", "source_range"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EditorPosition(ContractModel):
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class EditorSelection(ContractModel):
    start: EditorPosition
    end: EditorPosition

    @model_validator(mode="after")
    def ordered(self):
        if (self.end.line, self.end.column) < (self.start.line, self.start.column):
            raise ValueError("Selection end precedes start")
        return self


class QueryRequest(ContractModel):
    repository_id: RepositoryId
    question: str = Field(min_length=1, max_length=2000)
    snapshot_id: GenerationId | None = None
    active_file: str | None = None
    cursor: EditorPosition | None = None
    selection: EditorSelection | None = None
    selected_symbol_id: str | None = None
    session_id: str | None = None
    depth: Depth = "standard"

    @model_validator(mode="after")
    def validate_editor_context(self):
        if not self.question.strip():
            raise ValueError("Question is empty")
        if self.active_file is not None:
            relative_path(self.active_file)
        if (self.cursor or self.selection) and not self.active_file:
            raise ValueError("Editor position requires an active file")
        return self


Intent = Literal[
    "SYMBOL_LOCATION", "DIRECT_CALLERS", "DIRECT_CALLEES", "DEPENDENCY_PATH",
    "FEATURE_DISCOVERY", "FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION",
    "MODULE_OVERVIEW", "CHANGE_IMPACT", "BEHAVIOR_INVESTIGATION",
    "CODE_COMPARISON", "TEST_DISCOVERY",
    "FUNCTION_CONTRACT",
    "UNRESOLVED",
]


EntityKind = Literal["function", "method", "class", "symbol", "module", "file", "selection", "feature", "api_path"]


class EntityCandidate(ContractModel):
    entity_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: EntityKind
    file_path: str
    symbol_id: str | None = None
    evidence: Evidence

    @model_validator(mode="after")
    def canonical_mapping(self):
        if self.file_path != self.evidence.file_path:
            raise ValueError("Candidate scope differs from its canonical source proof")
        if self.kind not in ("file", "module") and (
                self.symbol_id != self.evidence.symbol_id or self.entity_id != self.evidence.evidence_id):
            raise ValueError("Candidate symbol identity differs from its canonical proof")
        return self


class QueryEntity(ContractModel):
    kind: EntityKind
    name: str = Field(min_length=1)
    symbol_id: str | None = None
    file_path: str | None = None
    file_id: str | None = None
    entity_id: str | None = None
    resolution: Literal["resolved", "ambiguous", "not_found", "unresolved_text", "unsupported"] = "unresolved_text"
    origin: Literal["text", "active_file", "selected_symbol", "selection", "previous_target"] = "text"
    selection: EditorSelection | None = None
    evidence: Evidence | None = None
    candidates: list[EntityCandidate] = Field(default_factory=list, max_length=25)
    candidates_truncated: bool = False
    reason: str | None = None


class IntentRequirement(ContractModel):
    intent: Intent
    order: int = Field(ge=0)
    entity_indices: list[int] = Field(default_factory=list, max_length=12)
    depends_on: list[int] = Field(default_factory=list, max_length=12)
    required_capabilities: list[str] = Field(default_factory=list)
    support: Literal["supported", "partial", "unsupported", "unresolved"] = "unresolved"
    limitations: list[str] = Field(default_factory=list)
    interpretation: str | None = None


class NormalizedQuery(ContractModel):
    intents: list[Intent] = Field(min_length=1, max_length=12)
    entities: list[QueryEntity] = Field(default_factory=list, max_length=12)
    change_type: str | None = None
    scope: Literal["repository", "active_file", "selected_symbol", "selected_directory"] = "repository"
    requires_explanation: bool = False
    depth: Depth = "standard"
    original_question: str | None = None
    normalized_question: str | None = None
    repository_id: RepositoryId | None = None
    snapshot_id: GenerationId | None = None
    primary_intent: Intent | None = None
    secondary_intents: list[Intent] = Field(default_factory=list)
    requirements: list[IntentRequirement] = Field(default_factory=list, max_length=12)
    status: Literal["success", "partial", "ambiguous", "unresolved", "not_found", "unsupported", "stale_index", "error"] = "success"
    context_used: list[str] = Field(default_factory=list)
    unresolved_references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    legacy_plan: dict[str, JsonValue] = Field(default_factory=dict)
    normalization_ms: int = Field(default=0, ge=0)
    error: EngineError | None = None

    @model_validator(mode="after")
    def validate_requirements(self):
        if self.primary_intent is not None and self.primary_intent != self.intents[0]:
            raise ValueError("Primary intent must be the first recognized intent")
        if self.primary_intent is not None and self.secondary_intents != self.intents[1:]:
            raise ValueError("Secondary intents must preserve recognized order")
        for index, requirement in enumerate(self.requirements):
            if requirement.order != index or requirement.intent not in self.intents:
                raise ValueError("Intent requirements must have consecutive order and recognized intents")
            if any(value < 0 or value >= len(self.entities) for value in requirement.entity_indices):
                raise ValueError("Requirement refers to an unknown entity")
            if any(value < 0 or value >= index for value in requirement.depends_on):
                raise ValueError("Target dependencies must point to earlier requirements")
        return self


class QueryContext(ContractModel):
    """One caller-owned, bounded turn of context; never persistent conversation memory."""
    repository_id: RepositoryId
    snapshot_id: GenerationId
    active_file: str | None = Field(default=None, max_length=4096)
    selected_symbol_id: str | None = Field(default=None, max_length=4096)
    selection: EditorSelection | None = None
    previous_target: QueryEntity | None = None
    previous_intents: list[Intent] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def valid_context(self):
        if self.active_file is not None:
            relative_path(self.active_file)
        if self.selection is not None and self.active_file is None:
            raise ValueError("Context selection requires an active file")
        if self.previous_target is not None:
            if self.previous_target.resolution != "resolved" or self.previous_target.evidence is None:
                raise ValueError("Previous target must be a previously source-verified resolved entity")
            if len(self.previous_target.model_dump_json()) > 16384:
                raise ValueError("Previous target exceeds the bounded context size")
            proof = self.previous_target.evidence
            if (self.previous_target.symbol_id != proof.symbol_id or self.previous_target.file_id != proof.file_id
                    or self.previous_target.file_path != proof.file_path):
                raise ValueError("Previous target identity differs from its canonical source proof")
        return self


class SnapshotContext(ContractModel):
    """Server-issued generation and its ready internal stage snapshots."""

    repository_id: RepositoryId
    snapshot_id: GenerationId
    ready_stage_ids: frozenset[StageSnapshotId]
    indexed_files: frozenset[str] = frozenset()

    @classmethod
    def from_pinned_view(cls, view: dict, repository_id: str) -> SnapshotContext:
        if not re.fullmatch(r"[a-f0-9]{24}", repository_id):
            raise RepositoryMismatchError("Invalid repository identity")
        manifest = view.get("manifest")
        if (not view.get("fresh") or not view.get("managed") or not manifest
                or manifest.get("repoId") != repository_id
                or view.get("snapshotId") != manifest.get("id")):
            raise StaleIndexError("No fresh pinned generation for this repository")
        database = Path(view["data_dir"]) / repository_id / "metadata.db"
        if not database.is_file():
            raise StaleIndexError("Pinned generation has no metadata database")
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            ready = frozenset(row[0] for row in db.execute(
                "SELECT id FROM index_snapshots WHERE repository_id=? AND state='ready'",
                (repository_id,),
            ))
        if not ready:
            raise StaleIndexError("Pinned generation has no ready index snapshots")
        return cls(repository_id=repository_id, snapshot_id=manifest["id"],
                   ready_stage_ids=ready,
                   indexed_files=frozenset(manifest["sources"]["files"]))

    def validate_request(self, request: QueryRequest) -> None:
        if request.repository_id != self.repository_id:
            raise RepositoryMismatchError("Request belongs to another repository")
        if request.snapshot_id is not None and request.snapshot_id != self.snapshot_id:
            raise SnapshotMismatchError("Requested generation differs from the pinned generation")
        if request.active_file is not None and request.active_file not in self.indexed_files:
            raise RepositoryMismatchError("Active file is outside the indexed repository evidence")


class Evidence(ContractModel):
    evidence_id: str = Field(min_length=1)
    repository_id: RepositoryId
    snapshot_id: GenerationId
    index_snapshot_id: StageSnapshotId
    evidence_type: EvidenceType
    file_id: str | None = None
    symbol_id: str | None = None
    file_path: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    resolution: Literal["resolved", "inferred", "unresolved", "syntax_only", "not_applicable"]
    source_hash: SourceHash | None = None
    source_origin: Literal["disk"] | None = None
    schema_version: int | None = Field(default=None, ge=1)

    @classmethod
    def from_trace(cls, info: dict, context: SnapshotContext, *, evidence_type: EvidenceType,
                   resolution: Literal["resolved", "inferred", "unresolved", "syntax_only", "not_applicable"]):
        """Normalize a successful metadata.trace() result from the pinned generation."""
        if info.get("repository_id") != context.repository_id:
            raise RepositoryMismatchError("Traced entity belongs to another repository")
        if info["snapshotId"] not in context.ready_stage_ids:
            raise StaleIndexError("Traced entity is not ready in the pinned generation")
        if info.get("path") is not None and info["path"] not in context.indexed_files:
            raise RepositoryMismatchError("Traced source is outside the indexed repository evidence")
        return cls(evidence_id=info["entityId"], repository_id=context.repository_id,
                   snapshot_id=context.snapshot_id, index_snapshot_id=info["snapshotId"],
                   evidence_type=evidence_type, file_id=info.get("fileId"),
                   symbol_id=info.get("symbolId"), file_path=info.get("path"),
                   start_line=info.get("startLine"), end_line=info.get("endLine"),
                   resolution=resolution, source_hash=info.get("sourceHash"),
                   source_origin=info.get("sourceOrigin"), schema_version=info.get("schemaVersion"))

    @model_validator(mode="after")
    def validate_location(self):
        if self.file_path is not None:
            relative_path(self.file_path)
            if None in (self.file_id, self.start_line, self.end_line, self.source_hash):
                raise ValueError("Located evidence needs file identity, range and source hash")
            if self.end_line < self.start_line:
                raise ValueError("Evidence line range is reversed")
        elif any(value is not None for value in (self.file_id, self.start_line, self.end_line, self.source_hash)):
            raise ValueError("Source location fields require a file path")
        return self


class GraphFinding(ContractModel):
    kind: Literal["graph"] = "graph"
    relationship: str = Field(min_length=1)
    source_symbol_id: str | None = Field(default=None, min_length=1)
    target_symbol_id: str | None = Field(default=None, min_length=1)
    source_node_id: str | None = Field(default=None, min_length=1)
    target_node_id: str | None = Field(default=None, min_length=1)
    entity_id: str | None = Field(default=None, min_length=1)
    entity_name: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    path_node_ids: list[str] = Field(default_factory=list)
    path_names: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph_identity(self):
        if self.entity_id is None and not (
            (self.source_symbol_id or self.source_node_id)
            and (self.target_symbol_id or self.target_node_id)
        ):
            raise ValueError("Graph finding requires canonical entity or endpoint identities")
        if len(self.path_node_ids) != len(self.path_names):
            raise ValueError("Graph path identities and names must align")
        return self


class SemanticFinding(ContractModel):
    kind: Literal["semantic"] = "semantic"
    chunk_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    retrieval_score: float | None = Field(default=None, allow_inf_nan=False)
    keyword_score: float | None = Field(default=None, allow_inf_nan=False)
    fusion_score: float | None = Field(default=None, allow_inf_nan=False)
    retrieval_methods: list[Literal["semantic", "keyword"]] = Field(default_factory=list)
    ranking_signals: dict[str, JsonValue] = Field(default_factory=dict)
    name: str | None = None
    evidence_ids: list[str] = Field(min_length=1)


class RuleFinding(ContractModel):
    kind: Literal["rule"] = "rule"
    statement: str = Field(min_length=1)
    classification: Literal["verified_observation", "derived_finding", "hypothesis"]
    rule_id: str | None = None
    category: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)


class RagFinding(ContractModel):
    kind: Literal["rag"] = "rag"
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


Finding = Annotated[GraphFinding | SemanticFinding | RuleFinding | RagFinding, Field(discriminator="kind")]


class EngineError(ContractModel):
    code: Literal["invalid_operation", "unavailable", "budget_exceeded", "stale_index", "internal"]
    message: str = Field(min_length=1)


class EngineResult(ContractModel):
    engine: EngineName
    status: ResultStatus
    repository_id: RepositoryId
    snapshot_id: GenerationId
    findings: list[Finding] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    execution_ms: int = Field(ge=0)
    error: EngineError | None = None

    @model_validator(mode="after")
    def validate_envelope(self):
        kinds = {"graph": "graph", "semantic": "semantic", "rules": "rule", "graph_rag": "rag"}
        if any(finding.kind != kinds[self.engine] for finding in self.findings):
            raise ValueError("Finding type does not match engine")
        if any(set(finding.evidence_ids) - set(self.evidence_ids) for finding in self.findings):
            raise ValueError("Finding refers to evidence missing from result")
        if self.status == "success" and not self.findings:
            raise ValueError("Successful result needs findings")
        if self.status == "not_found" and self.findings:
            raise ValueError("Not-found result cannot contain findings")
        if self.status in ("error", "stale_index") and self.error is None:
            raise ValueError("Error and stale results need a typed error")
        return self


class ContractError(ValueError):
    """A query or engine result violates the shared contract."""


class RepositoryMismatchError(ContractError):
    pass


class SnapshotMismatchError(ContractError):
    pass


class StaleIndexError(ContractError):
    pass


class UnknownEvidenceError(ContractError):
    pass


class InvalidOperationError(ContractError):
    pass


class OperationRequest(ContractModel):
    engine: EngineName
    name: str = Field(min_length=1)
    repository_id: RepositoryId
    snapshot_id: GenerationId
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True)
class OperationSpec:
    """An engine declares each supported operation using a Pydantic input model."""

    engine: EngineName
    name: str
    parameters_model: type[BaseModel]
    evidence_types: frozenset[EvidenceType]
    max_execution_ms: int
    fallback_status: ResultStatus

    def validate(self, operation: OperationRequest, context: SnapshotContext) -> BaseModel:
        if operation.engine != self.engine or operation.name != self.name:
            raise InvalidOperationError("Operation is not registered for this engine")
        if operation.repository_id != context.repository_id:
            raise RepositoryMismatchError("Operation belongs to another repository")
        if operation.snapshot_id != context.snapshot_id:
            raise SnapshotMismatchError("Operation is not bound to the pinned generation")
        if self.max_execution_ms < 1:
            raise InvalidOperationError("Operation has no positive execution budget")
        if set(operation.parameters) - set(self.parameters_model.model_fields):
            raise InvalidOperationError("Operation has unknown parameters")
        return self.parameters_model.model_validate(operation.parameters, strict=True)


class QueryEngine(Protocol):
    async def execute(self, operation: OperationRequest, context: SnapshotContext) -> EngineResult: ...


def validate_engine_result(result: EngineResult, context: SnapshotContext,
                           evidence: dict[str, Evidence], spec: OperationSpec | None = None) -> None:
    """Check cross-engine identity and evidence membership, not claim correctness."""
    if result.repository_id != context.repository_id:
        raise RepositoryMismatchError("Result belongs to another repository")
    if result.snapshot_id != context.snapshot_id:
        raise SnapshotMismatchError("Result belongs to another generation")
    if spec is not None and result.engine != spec.engine:
        raise InvalidOperationError("Result came from a different engine")
    for evidence_id in result.evidence_ids:
        item = evidence.get(evidence_id)
        if item is None or item.evidence_id != evidence_id:
            raise UnknownEvidenceError("Result refers to unknown evidence")
        if item.repository_id != context.repository_id:
            raise RepositoryMismatchError("Evidence belongs to another repository")
        if item.snapshot_id != context.snapshot_id:
            raise SnapshotMismatchError("Evidence belongs to another generation")
        if item.index_snapshot_id not in context.ready_stage_ids:
            raise StaleIndexError("Evidence stage is not ready in the pinned generation")
        if spec is not None and item.evidence_type not in spec.evidence_types:
            raise InvalidOperationError("Result uses evidence unsupported by the operation")
