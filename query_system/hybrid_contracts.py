"""Versioned integration envelopes reuse all existing phase contracts."""

from typing import Literal
from pydantic import Field

from query_system.contracts import ContractModel, ConversationalReply, EngineError, GenerationId, QueryRequest, ResultStatus
from query_system.evidence_contracts import EvidenceLimits
from query_system.execution_contracts import ExecutionLimits
from query_system.generation_contracts import GenerationBudget
from query_system.presentation_contracts import PresentationResult


class HybridLimits(ContractModel):
    execution: ExecutionLimits = Field(default_factory=ExecutionLimits)
    evidence: EvidenceLimits = Field(default_factory=EvidenceLimits)
    generation: GenerationBudget = Field(default_factory=GenerationBudget)
    deadline_ms: int = Field(default=60000, ge=1000, le=120000)
    max_generation_calls: int = Field(default=2, ge=0, le=4)


class HybridQueryRequest(ContractModel):
    query: QueryRequest
    previous_answer_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{48}$")
    allow_generation: bool = True
    limits: HybridLimits = Field(default_factory=HybridLimits)


class PipelineTiming(ContractModel):
    understanding_ms: float = 0.
    planning_ms: float = 0.
    execution_ms: float = 0.
    aggregation_ms: float = 0.
    llm_preparation_ms: float = 0.
    llm_generation_ms: float = 0.
    generation_total_ms: float = 0.
    formatting_ms: float = 0.
    validation_ms: float = 0.
    total_ms: float = 0.


class OperationDiagnostic(ContractModel):
    operation_id: str
    requirement_id: str
    engine: str
    operation: str
    status: str
    supporting: bool
    finding_ids: list[str] = Field(default_factory=list)


class GenerationDiagnostic(ContractModel):
    task: str
    original_orders: list[int]
    model_status: str
    fallback_status: str
    failure_category: str | None
    model_attempts: int
    claim_operation_ids: dict[str, list[str]] = Field(default_factory=dict)


class HybridResponse(ContractModel):
    schema_version: Literal["hybrid-v1"] = "hybrid-v1"
    status: ResultStatus
    snapshot_id: GenerationId | None
    presentation: PresentationResult | None
    static_response: ConversationalReply | None = None
    navigation_token: str | None = None
    error: EngineError | None = None
    operations: list[OperationDiagnostic] = Field(default_factory=list)
    generations: list[GenerationDiagnostic] = Field(default_factory=list)
    timings: PipelineTiming = Field(default_factory=PipelineTiming)
    limitations: list[str] = Field(default_factory=list)


class HybridHandleRequest(ContractModel):
    repository_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    navigation_token: str = Field(pattern=r"^[a-f0-9]{48}$")


class HybridSourceRequest(HybridHandleRequest):
    reference: str = Field(min_length=1, max_length=256)
    depth: str = Field(default="standard", pattern=r"^(quick|standard|deep)$")


class HybridPresentationRequest(HybridHandleRequest):
    depth: str = Field(pattern=r"^(quick|standard|deep)$")
