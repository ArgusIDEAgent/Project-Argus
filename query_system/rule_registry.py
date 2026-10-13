"""Explicit, typed Phase 4 rules; no natural-language intent classification."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from knowledge_base.identity import relative_path
from query_system.contracts import ContractModel, EvidenceType, OperationSpec


class AnalysisLimits(ContractModel):
    limit: int = Field(default=50, ge=1, le=100)
    max_depth: int = Field(default=3, ge=1, le=5)
    max_nodes: int = Field(default=200, ge=1, le=2000)
    max_edges: int = Field(default=1000, ge=1, le=10000)
    max_source_bytes: int = Field(default=8192, ge=128, le=65536)


class TargetParameters(AnalysisLimits):
    reference: str | None = Field(default=None, min_length=1, max_length=4096)
    selected_symbol_id: str | None = Field(default=None, min_length=1, max_length=4096)
    file_path: str | None = None
    module_path: str | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.module_path is not None:
            if self.reference is not None or self.selected_symbol_id is not None or self.file_path is not None:
                raise ValueError("Choose a symbol or a module, not both")
        elif not self.reference and not self.selected_symbol_id:
            raise ValueError("A symbol reference/ID or module path is required")
        for path in (self.file_path, self.module_path):
            if path is not None:
                relative_path(path)
        return self


class SymbolAnalysisParameters(TargetParameters):
    @model_validator(mode="after")
    def require_symbol(self):
        if self.module_path is not None:
            raise ValueError("This rule requires a symbol")
        return self


class ModuleAnalysisParameters(TargetParameters):
    @model_validator(mode="after")
    def require_module(self):
        if self.module_path is None:
            raise ValueError("This rule requires a module path")
        return self


class DependencyParameters(TargetParameters):
    path_target: str | None = Field(default=None, min_length=1, max_length=4096)
    path_target_file: str | None = None

    @model_validator(mode="after")
    def validate_path_target(self):
        if self.path_target_file is not None:
            relative_path(self.path_target_file)
            if self.path_target is None or self.module_path is not None:
                raise ValueError("A target file requires a symbol path target")
        if self.module_path is not None and self.path_target is not None:
            relative_path(self.path_target)
        return self


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    description: str
    required_inputs: tuple[str, ...]
    required_capabilities: frozenset[Literal["symbols", "graph", "source_trace", "dependency_paths", "test_references"]]
    supported_languages: frozenset[Literal["javascript", "typescript"]]
    result_limit: int
    required_evidence: frozenset[EvidenceType]
    limitations: tuple[str, ...]
    parameters_model: type[TargetParameters]
    max_execution_ms: int = 10000

    @property
    def operation(self):
        return OperationSpec("rules", self.rule_id, self.parameters_model,
                             self.required_evidence, self.max_execution_ms, "partial")


STATIC_LIMITATIONS = (
    "JS/TS indexed static evidence only; dynamic targets may be unresolved.",
    "Known test references follow the existing filename convention, not execution or coverage.",
)
_EVIDENCE = frozenset({"symbol_definition", "source_range", "graph_relationship"})
RULES = {
    name: RuleDefinition(name, description, inputs, frozenset(capabilities),
                         frozenset({"javascript", "typescript"}), 100, _EVIDENCE,
                         STATIC_LIMITATIONS + limitations, model)
    for name, description, inputs, capabilities, limitations, model in (
        ("function_contract", "Declared function contract, not inferred runtime behavior",
         ("reference or selected_symbol_id",), ("symbols", "graph", "source_trace"),
         ("Return values and thrown exceptions are not comprehensively indexed.",), SymbolAnalysisParameters),
        ("dependency_summary", "Direct static dependencies and optional bounded paths",
         ("symbol or module",), ("graph", "source_trace", "dependency_paths"),
         ("Module dependencies are derived from resolved imports, not runtime loading.",), DependencyParameters),
        ("reference_test_analysis", "Resolved references, callers and indexed test references",
         ("reference or selected_symbol_id",), ("graph", "source_trace", "test_references"),
         ("Unknown dynamic incoming targets cannot be attributed to a symbol.",), SymbolAnalysisParameters),
        ("change_impact", "Preliminary possible impact through existing bounded static paths",
         ("symbol or module",), ("graph", "source_trace", "dependency_paths", "test_references"),
         ("An impact location is a hypothesis, never a guarantee that a change breaks code.",
          "Reverse discovery uses bounded candidate-to-target path queries, not a new traversal."), TargetParameters),
        ("module_overview", "Indexed definitions, imports, dependents, exports and test references",
         ("module_path",), ("symbols", "graph", "source_trace", "test_references"),
         ("Exported flags and EXPORTS edges are available interfaces, not a complete public API.",), ModuleAnalysisParameters),
    )
}
