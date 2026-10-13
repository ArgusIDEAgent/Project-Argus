"""Phase 7 envelopes; existing findings, proofs and requirements stay unchanged."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from query_system.contracts import (
    ContractModel, EngineName, Evidence, Finding, GenerationId, IntentRequirement,
    OperationRequest, QueryEntity, RepositoryId, ResultStatus,
)
from query_system.execution_contracts import Clarification, OperationStatus

ClaimClass = Literal["verified_source_observation", "verified_graph_relationship", "derived_analysis",
                     "hypothetical_consequence", "unresolved_assertion"]
CoverageState = Literal["satisfied", "partially_satisfied", "ambiguous", "unsupported", "not_found",
                        "stale", "error", "skipped", "deferred"]


class EvidenceLimits(ContractModel):
    max_records: int = Field(default=200, ge=1, le=2000)
    max_context_bytes: int = Field(default=14000, ge=0, le=1000000)
    findings_per_requirement: int = Field(default=25, ge=1, le=100)
    max_relationship_items: int = Field(default=32, ge=1, le=200)
    max_finding_bytes: int = Field(default=16384, ge=256, le=131072)
    max_package_bytes: int = Field(default=262144, ge=4096, le=2097152)
    include_source_context: bool = False


class EvidenceUse(ContractModel):
    operation_id: str | None
    requirement_id: str
    engine: EngineName | None
    finding_id: str | None
    role: Literal["claim", "context", "clarification"]


class EvidenceRecord(ContractModel):
    record_id: str
    proof: Evidence
    provenance: dict[str, JsonValue]
    verification: Literal["source_verified", "structural_metadata_verified"]
    source_context: str | None = None
    context_status: Literal["not_requested", "included", "omitted_budget", "structural"]
    uses: list[EvidenceUse]


class SupportedFinding(ContractModel):
    finding_id: str
    operation_id: str
    requirement_id: str
    engine: EngineName
    original: Finding
    classification: ClaimClass
    authoritative: bool
    claim_scope: str
    evidence_record_ids: list[str] = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class Citation(ContractModel):
    reference: str
    record_id: str
    source: Evidence


class EvidenceConflict(ContractModel):
    code: str
    message: str
    evidence_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    operation_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class EvidenceOmission(ContractModel):
    reason: str
    requirement_ids: list[str]
    operation_id: str | None = None
    finding_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class OperationCoverage(ContractModel):
    operation_id: str
    requirement_id: str
    request: OperationRequest
    execution_status: OperationStatus
    state: CoverageState
    finding_ids: list[str]
    evidence_record_ids: list[str]
    missing_evidence: list[str]
    limitations: list[str]
    unresolved_items: list[str] = Field(default_factory=list)


class RequirementCoverage(ContractModel):
    requirement_id: str
    original: IntentRequirement
    execution_status: OperationStatus
    state: CoverageState
    operation_ids: list[str]
    finding_ids: list[str]
    evidence_record_ids: list[str]
    missing_evidence: list[str]
    limitations: list[str]
    clarification_required: bool
    truncated: bool
    targets: list[RequirementTarget] = Field(default_factory=list)


class RequirementTarget(ContractModel):
    entity_index: int = Field(ge=0)
    reference: QueryEntity
    validation: Literal["input_only"] = "input_only"

    @model_validator(mode="after")
    def no_duplicate_proof(self):
        if self.reference.evidence is not None or self.reference.candidates:
            raise ValueError("Input target hints cannot carry unvalidated source proofs or candidates")
        return self


class EvidencePackage(ContractModel):
    package_id: str
    query_id: str
    execution_id: str
    repository_id: RepositoryId
    snapshot_id: GenerationId
    question: str | None
    limits: EvidenceLimits
    evidence: dict[str, EvidenceRecord]
    findings: list[SupportedFinding]
    requirements: list[RequirementCoverage]
    operations: list[OperationCoverage]
    requirement_evidence: dict[str, list[str]]
    citations: dict[str, Citation]
    conflicts: list[EvidenceConflict]
    missing_evidence: list[str]
    clarifications: list[Clarification]
    omissions: list[EvidenceOmission]
    resource_limits: list[str]
    status: ResultStatus
    validation_status: Literal["valid", "partial", "invalid", "stale"]
    limitations: list[str]
    execution_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def integrity(self):
        records, findings = self.evidence, {item.finding_id: item for item in self.findings}
        requirements = {item.requirement_id: item for item in self.requirements}
        operations = {item.operation_id: item for item in self.operations}
        if len(findings) != len(self.findings) or len(requirements) != len(self.requirements) or len(operations) != len(self.operations):
            raise ValueError("Duplicate finding, requirement or operation identity")
        if len(records) > self.limits.max_records:
            raise ValueError("Evidence record budget exceeded")
        if sum(len((item.source_context or "").encode()) for item in records.values()) > self.limits.max_context_bytes:
            raise ValueError("Source context budget exceeded")
        for key, record in records.items():
            if key != record.record_id or (record.proof.repository_id, record.proof.snapshot_id) != (self.repository_id, self.snapshot_id):
                raise ValueError("Evidence identity differs from package")
            if record.verification != ("source_verified" if record.proof.file_path else "structural_metadata_verified"):
                raise ValueError("Source verification availability was misrepresented")
            if (record.context_status == "included") != (record.source_context is not None):
                raise ValueError("Source context availability was misrepresented")
            for use in record.uses:
                if use.requirement_id not in requirements or use.operation_id is not None and use.operation_id not in operations:
                    raise ValueError("Evidence use has unknown ownership")
                if use.finding_id is not None and (use.finding_id not in findings or key not in findings[use.finding_id].evidence_record_ids):
                    raise ValueError("Evidence use lacks its supported finding")
                if use.operation_id is not None and (use.requirement_id != operations[use.operation_id].requirement_id or use.engine != operations[use.operation_id].request.engine):
                    raise ValueError("Evidence use changed engine/requirement ownership")
        for finding in self.findings:
            if finding.engine == "graph_rag" or finding.original.kind == "rag":
                raise ValueError("Phase 7 does not accept RAG findings")
            if finding.requirement_id not in requirements or finding.operation_id not in operations:
                raise ValueError("Finding has unknown ownership")
            if finding.requirement_id != operations[finding.operation_id].requirement_id or finding.engine != operations[finding.operation_id].request.engine or finding.original.kind != {"graph": "graph", "semantic": "semantic", "rules": "rule"}.get(finding.engine):
                raise ValueError("Finding changed its original engine/requirement mapping")
            if set(finding.evidence_record_ids) - records.keys() or set(finding.citation_ids) - self.citations.keys():
                raise ValueError("Finding has dangling proof or citation")
            if finding.authoritative != (finding.classification not in ("hypothetical_consequence", "unresolved_assertion")):
                raise ValueError("Hypotheses/unresolved assertions cannot be authoritative")
            if finding.original.kind == "semantic" and finding.classification != "verified_source_observation":
                raise ValueError("Semantic relevance cannot become graph or behavior proof")
            if finding.original.kind == "rule" and (finding.original.classification == "hypothesis" or finding.original.category == "possible_impact") and finding.authoritative:
                raise ValueError("Original hypothetical analysis cannot be promoted")
            if {records[key].proof.evidence_id for key in finding.evidence_record_ids} != set(finding.original.evidence_ids):
                raise ValueError("Original finding lost supporting source evidence")
            for reference in finding.citation_ids:
                if self.citations[reference].record_id not in finding.evidence_record_ids:
                    raise ValueError("Finding cites unrelated evidence")
            if set(finding.citation_ids) != {key for key, citation in self.citations.items() if citation.record_id in finding.evidence_record_ids}:
                raise ValueError("Finding lost a backend-owned source citation")
        for key, citation in self.citations.items():
            record = records.get(citation.record_id)
            if (key != citation.reference or key != f"cm7:{self.package_id}:{citation.record_id}"
                    or record is None or citation.source != record.proof or citation.source.file_path is None):
                raise ValueError("Citation is not a package-owned validated source reference")
        if self.requirement_evidence != {item.requirement_id: item.evidence_record_ids for item in self.requirements}:
            raise ValueError("Requirement/evidence mapping differs from coverage")
        for coverage in [*self.requirements, *self.operations]:
            if set(coverage.finding_ids) - findings.keys() or set(coverage.evidence_record_ids) - records.keys():
                raise ValueError("Coverage has dangling findings or evidence")
            attribute = "operation_id" if isinstance(coverage, OperationCoverage) else "requirement_id"
            owner = getattr(coverage, attribute)
            if set(coverage.finding_ids) != {item.finding_id for item in self.findings if getattr(item, attribute) == owner}:
                raise ValueError("Coverage changed finding ownership")
            if set(coverage.evidence_record_ids) != {key for key, record in records.items() if any(getattr(use, attribute) == owner for use in record.uses)}:
                raise ValueError("Coverage changed evidence ownership")
        for coverage in self.requirements:
            if len(coverage.finding_ids) > self.limits.findings_per_requirement or set(coverage.operation_ids) - operations.keys():
                raise ValueError("Requirement exceeds budget or refers to missing operations")
        for clarification in self.clarifications:
            if clarification.requirement_id not in requirements:
                raise ValueError("Unknown clarification requirement")
            if any(not any(record.proof == candidate.evidence for record in records.values()) for candidate in clarification.candidates):
                raise ValueError("Clarification has an unvalidated candidate")
        if len(self.model_dump_json().encode()) > self.limits.max_package_bytes:
            raise ValueError("Serialized package budget exceeded")
        return self


class EvidenceBudgetError(ValueError):
    """The required coverage/diagnostic envelope cannot fit the requested budget."""
