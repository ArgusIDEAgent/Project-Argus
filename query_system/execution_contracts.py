"""Phase 6 planning/report wrappers around Phase 1 operations, findings and proofs."""

from typing import Literal

from pydantic import Field, model_validator

from query_system.contracts import (
    ContractModel, EngineError, EngineResult, EntityCandidate, Evidence, IntentRequirement,
    NormalizedQuery, OperationRequest, QueryEntity, RepositoryId, GenerationId, ResultStatus,
)

OperationStatus = Literal["success", "partial", "not_found", "ambiguous", "unsupported", "stale_index", "error", "skipped", "deferred"]


class ExecutionLimits(ContractModel):
    max_operations: int = Field(default=16, ge=1, le=64)
    max_dependency_depth: int = Field(default=8, ge=1, le=16)
    graph_results: int = Field(default=25, ge=1, le=100)
    semantic_candidates: int = Field(default=5, ge=1, le=25)
    rule_findings: int = Field(default=25, ge=1, le=100)
    graph_depth: int = Field(default=3, ge=1, le=5)
    max_concurrent: int = Field(default=3, ge=1, le=8)
    deadline_ms: int = Field(default=30000, ge=1, le=120000)
    max_evidence: int = Field(default=200, ge=1, le=2000)


class OperationBudget(ContractModel):
    execution_ms: int = Field(ge=1)
    max_results: int = Field(ge=1)


class PlannedTarget(ContractModel):
    entity_index: int = Field(ge=0)
    entity: QueryEntity


class PlannedOperation(ContractModel):
    operation_id: str
    requirement_id: str
    request: OperationRequest
    targets: list[PlannedTarget] = Field(default_factory=list, max_length=12)
    target_dependencies: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    required_requirement_ids: list[str] = Field(default_factory=list)
    capabilities: list[str]
    budget: OperationBudget
    expected_output: Literal["graph", "semantic", "rule"]


class RequirementDecision(ContractModel):
    requirement_id: str
    requirement: IntentRequirement
    disposition: Literal["planned", "blocked", "deferred", "limited"]
    operation_ids: list[str] = Field(default_factory=list)
    blocked_status: OperationStatus | None = None
    reason: str | None = None
    target_dependency_ids: list[str] = Field(default_factory=list)


class ExecutionPlan(ContractModel):
    query_id: str
    repository_id: RepositoryId
    snapshot_id: GenerationId
    normalized: NormalizedQuery
    limits: ExecutionLimits
    selections: dict[int, EntityCandidate] = Field(default_factory=dict)
    requirements: list[RequirementDecision]
    operations: list[PlannedOperation]

    @model_validator(mode="after")
    def validate_plan(self):
        if (self.normalized.repository_id, self.normalized.snapshot_id) != (self.repository_id, self.snapshot_id):
            raise ValueError("Plan identity differs from normalized query")
        if len(self.operations) > self.limits.max_operations:
            raise ValueError("Plan exceeds its operation budget")
        operation_ids = {item.operation_id for item in self.operations}
        decisions = {item.requirement_id: item for item in self.requirements}
        if len(operation_ids) != len(self.operations) or len(decisions) != len(self.requirements):
            raise ValueError("Duplicate operation or requirement IDs")
        if [item.requirement for item in self.requirements] != self.normalized.requirements:
            raise ValueError("Every original normalized requirement must be preserved in order")
        for decision in self.requirements:
            if set(decision.operation_ids) - operation_ids or set(decision.target_dependency_ids) - decisions.keys():
                raise ValueError("Requirement refers to a missing operation/dependency")
            if decision.disposition == "planned" and not decision.operation_ids:
                raise ValueError("Planned requirement has no operations")
            if decision.disposition != "planned" and decision.operation_ids:
                raise ValueError("Non-executable requirement cannot have admitted operations")
        graph = {}
        for operation in self.operations:
            if operation.requirement_id not in decisions or operation.operation_id not in decisions[operation.requirement_id].operation_ids:
                raise ValueError("Operation is not owned by its requirement")
            if operation.request.repository_id != self.repository_id or operation.request.snapshot_id != self.snapshot_id:
                raise ValueError("Operation belongs to another repository/generation")
            if set(operation.dependencies) - operation_ids or set(operation.required_requirement_ids) - decisions.keys():
                raise ValueError("Operation has unknown dependencies")
            if set(operation.target_dependencies) - set(operation.required_requirement_ids):
                raise ValueError("Target dependencies must be requirement dependencies")
            if any(target.entity_index >= len(self.normalized.entities) for target in operation.targets):
                raise ValueError("Operation refers to an unknown entity")
            graph[operation.operation_id] = operation.dependencies
        depths = {}
        visiting = set()
        def visit(identifier):
            if identifier in visiting:
                raise ValueError("Execution dependency cycle")
            if identifier not in depths:
                visiting.add(identifier)
                depth = 1 + max((visit(parent) for parent in graph[identifier]), default=0)
                visiting.remove(identifier)
                if depth > self.limits.max_dependency_depth:
                    raise ValueError("Execution dependency depth exceeds its budget")
                depths[identifier] = depth
            return depths[identifier]
        for identifier in graph:
            visit(identifier)
        return self


class OperationOutcome(ContractModel):
    operation_id: str
    requirement_id: str
    status: OperationStatus
    result: EngineResult | None = None
    error: EngineError | None = None
    reason: str | None = None


class RequirementOutcome(ContractModel):
    requirement_id: str
    requirement: IntentRequirement
    status: OperationStatus
    operation_ids: list[str]
    reason: str | None = None


class Clarification(ContractModel):
    requirement_id: str
    entity_indices: list[int]
    reason: str
    candidates: list[EntityCandidate] = Field(default_factory=list, max_length=25)
    source_operation_ids: list[str] = Field(default_factory=list)
    truncated: bool = False


class ExecutionReport(ContractModel):
    query_id: str
    repository_id: RepositoryId
    snapshot_id: GenerationId
    plan: ExecutionPlan
    requirements: list[RequirementOutcome]
    operations: list[OperationOutcome]
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    clarifications: list[Clarification] = Field(default_factory=list)
    unresolved_dependencies: list[str] = Field(default_factory=list)
    resource_limits: list[str] = Field(default_factory=list)
    status: ResultStatus
    execution_ms: int = Field(ge=0)
    peak_concurrency: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self):
        if self.query_id != self.plan.query_id:
            raise ValueError("Report belongs to another query")
        if [item.requirement_id for item in self.requirements] != [item.requirement_id for item in self.plan.requirements]:
            raise ValueError("A requirement disappeared from execution")
        for outcome, decision in zip(self.requirements, self.plan.requirements):
            if outcome.requirement != decision.requirement or outcome.operation_ids != decision.operation_ids:
                raise ValueError("Original requirement or operation ownership changed")
        if [item.operation_id for item in self.operations] != [item.operation_id for item in self.plan.operations]:
            raise ValueError("An operation disappeared from execution")
        if self.peak_concurrency > self.plan.limits.max_concurrent:
            raise ValueError("Report exceeded its concurrency budget")
        for key, proof in self.evidence.items():
            if key != proof.evidence_id or (proof.repository_id, proof.snapshot_id) != (self.repository_id, self.snapshot_id):
                raise ValueError("Evidence registry identity differs from pinned report")
        for outcome, operation in zip(self.operations, self.plan.operations):
            if outcome.requirement_id != operation.requirement_id:
                raise ValueError("Operation outcome changed requirement ownership")
            if outcome.result is None:
                continue
            if outcome.result.engine != operation.request.engine:
                raise ValueError("Result differs from planned engine")
            if outcome.status != outcome.result.status:
                raise ValueError("Operation status differs from the typed engine result")
            if (outcome.result.repository_id, outcome.result.snapshot_id) != (self.repository_id, self.snapshot_id):
                raise ValueError("Result belongs to another repository/generation")
            if set(outcome.result.evidence_ids) - self.evidence.keys():
                raise ValueError("Typed result lacks its supporting evidence")
        if len(self.evidence) > self.plan.limits.max_evidence:
            raise ValueError("Report exceeds the evidence budget")
        for clarification in self.clarifications:
            if clarification.requirement_id not in {item.requirement_id for item in self.plan.requirements}:
                raise ValueError("Clarification belongs to an unknown requirement")
            if any(self.evidence.get(item.evidence.evidence_id) != item.evidence for item in clarification.candidates):
                raise ValueError("Clarification lacks a canonical proof")
            if set(clarification.source_operation_ids) - {item.operation_id for item in self.operations}:
                raise ValueError("Clarification refers to an unknown operation")
        return self
