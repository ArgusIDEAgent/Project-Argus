"""Phase 9 display projections; canonical proofs and original requirements are reused."""

from typing import Literal

from pydantic import Field, model_validator

from query_system.contracts import ContractModel, Depth, GenerationId, IntentRequirement, RepositoryId
from query_system.evidence_contracts import Citation, EvidencePackage
from query_system.execution_contracts import Clarification
from query_system.generation_contracts import GenerationResult


class PreviewInput(ContractModel):
    package: EvidencePackage
    generation: GenerationResult | None = None
    depth: Depth = "standard"


class PresentationItem(ContractModel):
    item_id: str
    origin: Literal["finding", "generation"]
    text: str
    classification: str
    authoritative: bool
    citation_ids: list[str]
    details: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PresentationSection(ContractModel):
    title: str
    item_ids: list[str]


class OperationDisplay(ContractModel):
    operation_id: str
    execution_status: str
    coverage_state: str
    limitations: list[str]


class RequirementDisplay(ContractModel):
    requirement_id: str
    order: int
    original: IntentRequirement
    title: str
    state: str
    supporting_requirement_ids: list[str]
    target_context: list[str] = Field(default_factory=list)
    sections: list[PresentationSection]
    operations: list[OperationDisplay]
    limitations: list[str]
    hidden_items: int = Field(ge=0)


class PresentationSource(ContractModel):
    citation: Citation
    state: Literal["requires_verification", "verified", "stale", "unavailable", "unauthorized"] = "requires_verification"
    reason: str = "Revalidate canonical provenance and saved source before navigation."


class FollowUp(ContractModel):
    requirement_id: str
    label: str
    prompt: str | None
    engine: str | None
    operation: str | None
    target_id: str | None
    citation_id: str | None = None
    mode: Literal["copy_query", "inspect_source"]


class PresentationResult(ContractModel):
    schema_version: Literal["phase9-1"] = "phase9-1"
    presentation_id: str
    query_id: str
    package_id: str
    support_query_id: str
    repository_id: RepositoryId
    snapshot_id: GenerationId
    depth: Depth
    status: str
    items: dict[str, PresentationItem]
    requirements: list[RequirementDisplay]
    sources: dict[str, PresentationSource]
    clarifications: list[Clarification]
    follow_ups: list[FollowUp]
    limitations: list[str]
    model_status: str | None
    fallback_status: str | None

    @model_validator(mode="after")
    def links(self):
        if len({item.requirement_id for item in self.requirements}) != len(self.requirements):
            raise ValueError("Repeated requirements must have distinct display IDs")
        if [item.order for item in self.requirements] != list(range(len(self.requirements))):
            raise ValueError("Original requirement order changed")
        used = set()
        for requirement in self.requirements:
            for section in requirement.sections:
                if set(section.item_ids) - self.items.keys():
                    raise ValueError("Section refers to missing display items")
                used.update(section.item_ids)
        if used != self.items.keys():
            raise ValueError("Display catalog contains unowned items")
        for key, item in self.items.items():
            if key != item.item_id or set(item.citation_ids) - self.sources.keys():
                raise ValueError("Display item changed identity or has missing citations")
            if item.authoritative and item.classification in ("hypothetical_consequence", "unresolved_assertion", "model_interpretation", "unknown"):
                raise ValueError("Presentation cannot promote uncertainty")
        for key, source in self.sources.items():
            citation = source.citation
            if key != citation.reference or not key.startswith(f"cm7:{self.package_id}:") or (citation.source.repository_id, citation.source.snapshot_id) != (self.repository_id, self.snapshot_id):
                raise ValueError("Presentation citation lost canonical ownership")
        requirements = {item.requirement_id for item in self.requirements}
        for follow_up in self.follow_ups:
            if follow_up.requirement_id not in requirements or follow_up.citation_id is not None and follow_up.citation_id not in self.sources:
                raise ValueError("Follow-up has unknown ownership")
        return self
