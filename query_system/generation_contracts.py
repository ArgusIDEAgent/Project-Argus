"""Phase 8 generation-only envelopes; no changes to Phases 1–7."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from query_system.contracts import ContractModel, GenerationId, IntentRequirement, NormalizedQuery, QueryRequest, RepositoryId, ResultStatus
from query_system.evidence_contracts import Citation, ClaimClass

ExplanationType = Literal["function", "workflow", "behavior", "comparison"]


class GenerationBudget(ContractModel):
    max_records: int = Field(default=16, ge=1, le=64)
    max_facts: int = Field(default=20, ge=1, le=100)
    max_source_bytes: int = Field(default=8000, ge=0, le=64000)
    max_context_bytes: int = Field(default=20000, ge=2048, le=65536)
    num_ctx: int = Field(default=8192, ge=4096, le=65536)
    num_predict: int = Field(default=350, ge=64, le=2048)
    deadline_ms: int = Field(default=30000, ge=1, le=120000)
    max_response_bytes: int = Field(default=65536, ge=2048, le=262144)
    max_claims: int = Field(default=24, ge=1, le=64)


class GenerationRequest(ContractModel):
    query: QueryRequest
    normalized: NormalizedQuery
    package_id: str
    explanation_type: ExplanationType
    # Explicit original-requirement order -> supporting Phase 7 requirement IDs.
    bindings: dict[int, list[str]]
    budget: GenerationBudget = Field(default_factory=GenerationBudget)

    @model_validator(mode="after")
    def question_identity(self):
        if self.normalized.repository_id != self.query.repository_id or self.normalized.original_question != self.query.question:
            raise ValueError("Original query differs from normalized identity/question")
        if self.query.snapshot_id is not None and self.query.snapshot_id != self.normalized.snapshot_id:
            raise ValueError("Query and normalized generation differ")
        if set(self.bindings) != {item.order for item in self.normalized.requirements} or any(not values or len(values) != len(set(values)) for values in self.bindings.values()):
            raise ValueError("Every original requirement needs explicit unique support bindings")
        return self


class PreparedSource(ContractModel):
    record_id: str
    data: dict[str, JsonValue]
    excerpt: str | None


class PreparedFact(ContractModel):
    fact_id: str
    finding_id: str | None
    requirement_id: str
    record_ids: list[str]
    classification: ClaimClass
    statement: str
    data: dict[str, JsonValue]
    limitations: list[str]


class PreparedContext(ContractModel):
    context_id: str
    package_id: str
    sources: list[PreparedSource]
    facts: list[PreparedFact]
    omissions: list[str]
    gaps: dict[int, list[str]]
    core_ready: dict[int, bool]
    messages: list[dict[str, str]]
    input_bytes: int
    source_bytes: int
    token_accounting: Literal["conservative_byte_admission_not_exact_tokenization"] = "conservative_byte_admission_not_exact_tokenization"


class ModelClaim(ContractModel):
    requirement_orders: list[int] = Field(min_length=1, max_length=12)
    mode: Literal["fact", "interpretation", "hypothesis", "unknown"]
    fact_id: str | None = None
    record_ids: list[str] = Field(default_factory=list, max_length=64)
    text: str | None = Field(default=None, min_length=1, max_length=1600)


class ModelSection(ContractModel):
    title: Literal["source_facts", "explanation", "possible_causes", "comparison", "limitations"]
    claims: list[ModelClaim] = Field(min_length=1, max_length=64)


class ModelCoverage(ContractModel):
    order: int = Field(ge=0)
    state: Literal["addressed", "partial", "unsupported"]


class ModelAnswer(ContractModel):
    sections: list[ModelSection] = Field(min_length=1, max_length=8)
    coverage: list[ModelCoverage] = Field(min_length=1, max_length=12)


class AnswerClaim(ContractModel):
    claim_id: str
    requirement_orders: list[int]
    classification: ClaimClass | Literal["model_interpretation", "unknown"]
    authoritative: bool
    text: str
    finding_id: str | None
    fact_id: str | None
    record_ids: list[str]
    citation_ids: list[str]
    verification: Literal["backend_fact", "citation_linked_not_fact_checked", "unknown"]


class AnswerSection(ContractModel):
    title: str
    claims: list[AnswerClaim]


class AnswerCoverage(ContractModel):
    order: int
    original: IntentRequirement
    supporting_requirement_ids: list[str]
    state: Literal["addressed", "partial", "unsupported", "ambiguous", "stale", "error"]
    claim_ids: list[str]
    limitations: list[str]


class UnsupportedAssertion(ContractModel):
    reason: str
    requirement_orders: list[int]


class GenerationResult(ContractModel):
    generation_id: str
    package_id: str
    query_id: str
    support_query_id: str
    repository_id: RepositoryId
    snapshot_id: GenerationId
    explanation_type: ExplanationType
    sections: list[AnswerSection]
    coverage: list[AnswerCoverage]
    citations: dict[str, Citation]
    unsupported_assertions: list[UnsupportedAssertion]
    status: ResultStatus
    model_status: Literal["success", "disabled", "unavailable", "timeout", "invalid_output", "validation_failed", "blocked", "error"]
    fallback_status: Literal["not_used", "evidence_summary", "no_safe_evidence"]
    failure_category: str | None
    context: PreparedContext | None
    model_calls: int = Field(ge=0, le=1)
    preparation_ms: float = Field(ge=0)
    model_ms: float = Field(ge=0)
    total_ms: float = Field(ge=0)
    limitations: list[str]

    @model_validator(mode="after")
    def answer_integrity(self):
        orders = {item.order for item in self.coverage}
        if len(orders) != len(self.coverage):
            raise ValueError("Original requirement coverage must not disappear or repeat")
        all_claims = [claim for section in self.sections for claim in section.claims]
        ids = {item.claim_id for item in all_claims}
        if len(ids) != len(all_claims):
            raise ValueError("Duplicate answer claim IDs")
        for claim in all_claims:
            if set(claim.requirement_orders) - orders or set(claim.citation_ids) - self.citations.keys():
                raise ValueError("Answer claim has unknown requirement or citation")
            if claim.authoritative and (claim.verification != "backend_fact" or claim.classification in ("model_interpretation", "hypothetical_consequence", "unresolved_assertion", "unknown")):
                raise ValueError("Model prose or uncertainty cannot be authoritative")
            if claim.verification == "backend_fact" and claim.fact_id is None:
                raise ValueError("Backend fact needs its approved catalog identity")
        for coverage in self.coverage:
            if set(coverage.claim_ids) != {claim.claim_id for claim in all_claims if coverage.order in claim.requirement_orders}:
                raise ValueError("Coverage differs from actual answer claims")
        for reference, citation in self.citations.items():
            if (reference != citation.reference or not reference.startswith(f"cm7:{self.package_id}:")
                    or (citation.source.repository_id, citation.source.snapshot_id) != (self.repository_id, self.snapshot_id)):
                raise ValueError("Answer citations differ from approved package identity")
        return self
