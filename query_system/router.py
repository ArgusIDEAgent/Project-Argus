"""Declarative deterministic Phase 6 policy. No engine is executed here."""

import hashlib
import json
from pathlib import Path

from knowledge_base.parser import LANGUAGES
from query_system.contracts import EntityCandidate, NormalizedQuery, OperationRequest, QueryEntity, SnapshotContext
from query_system.execution_contracts import (
    ExecutionLimits, ExecutionPlan, OperationBudget, PlannedOperation, PlannedTarget, RequirementDecision,
)
from query_system.graph_engine import GRAPH_OPERATIONS
from query_system.rule_registry import RULES
from query_system.semantic_engine import SEMANTIC_OPERATIONS


ROUTING_POLICY = {
    "SYMBOL_LOCATION": ("graph", "find_definition"), "DIRECT_CALLERS": ("graph", "find_callers"),
    "DIRECT_CALLEES": ("graph", "find_callees"), "FEATURE_DISCOVERY": ("semantic", "hybrid_search"),
    "FUNCTION_CONTRACT": ("rules", "function_contract"), "CHANGE_IMPACT": ("rules", "change_impact"),
    "MODULE_OVERVIEW": ("rules", "module_overview"), "TEST_DISCOVERY": ("rules", "reference_test_analysis"),
    "DEPENDENCY_PATH": ("graph", "find_paths"),
}
DEFERRED_INTENTS = frozenset({"FUNCTION_EXPLANATION", "WORKFLOW_EXPLANATION", "BEHAVIOR_INVESTIGATION", "CODE_COMPARISON"})


def operation_spec(engine, name):
    if engine == "graph":
        return GRAPH_OPERATIONS[name]
    if engine == "semantic":
        return SEMANTIC_OPERATIONS[name]
    if engine == "rules":
        return RULES[name].operation
    raise ValueError("Only existing deterministic engines are permitted")


def _proof_scope(entity, context):
    proof = entity.evidence
    if entity.resolution != "resolved" or proof is None:
        return False
    if ((proof.repository_id, proof.snapshot_id) != (context.repository_id, context.snapshot_id)
            or proof.index_snapshot_id not in context.ready_stage_ids or proof.file_path not in context.indexed_files):
        raise ValueError("Target proof belongs to an incompatible pinned scope")
    if entity.file_id != proof.file_id or entity.file_path != proof.file_path or entity.symbol_id != proof.symbol_id:
        raise ValueError("Target mapping differs from canonical proof")
    if entity.symbol_id and entity.entity_id != entity.symbol_id:
        raise ValueError("Target canonical symbol identity differs")
    return True


def _selected(entity, candidate, context):
    if entity.candidates and candidate.entity_id not in {item.entity_id for item in entity.candidates}:
        raise ValueError("Selection is not a supplied ambiguity candidate")
    if entity.file_path and entity.file_path != candidate.file_path:
        raise ValueError("Selection conflicts with the explicit target file scope")
    value = QueryEntity(kind=candidate.kind, name=candidate.name, resolution="resolved", symbol_id=candidate.symbol_id,
        entity_id=candidate.entity_id, file_id=candidate.evidence.file_id, file_path=candidate.file_path,
        evidence=candidate.evidence, origin="selected_symbol")
    _proof_scope(value, context)
    return value


def build_plan(query: NormalizedQuery, context: SnapshotContext, limits=None, selections=None) -> ExecutionPlan:
    query = NormalizedQuery.model_validate(query.model_dump(), strict=True)
    limits = ExecutionLimits.model_validate((limits or ExecutionLimits()).model_dump(), strict=True)
    if (query.repository_id, query.snapshot_id) != (context.repository_id, context.snapshot_id):
        raise ValueError("Normalized query is outside the pinned repository/generation")
    selections = {index: EntityCandidate.model_validate(value.model_dump(), strict=True) for index, value in (selections or {}).items()}
    if any(index < 0 or index >= len(query.entities) for index in selections):
        raise ValueError("Selection belongs to an unknown entity")
    canonical = query.model_dump(mode="json", exclude={"normalization_ms"})
    identity = {"query": canonical, "limits": limits.model_dump(), "selections": {str(k): v.model_dump(mode="json") for k, v in sorted(selections.items())}}
    query_id = "query-" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    operations, decisions, depths = [], [], {}
    for requirement in query.requirements:
        rid = f"req-{requirement.order:03d}"
        parents = [f"req-{index:03d}" for index in requirement.depends_on]
        decision = RequirementDecision(requirement_id=rid, requirement=requirement, disposition="blocked",
                                      blocked_status="ambiguous", reason="A uniquely verified target is required", target_dependency_ids=parents)
        decisions.append(decision)
        depths[rid] = 1 + max((depths[parent] for parent in parents), default=0)
        if depths[rid] > limits.max_dependency_depth:
            decision.disposition, decision.blocked_status, decision.reason = "limited", "partial", "Dependency depth limit reached"
            continue
        if query.status in ("stale_index", "error"):
            decision.blocked_status, decision.reason = query.status, "Normalization rejected the input scope"
            continue
        intent = requirement.intent
        if intent in DEFERRED_INTENTS:
            decision.disposition, decision.blocked_status = "deferred", "deferred"
            decision.reason = "No Phase 6 deterministic explanation/workflow/behavior/comparison operation is implemented"
            continue
        if intent not in ROUTING_POLICY:
            decision.blocked_status, decision.reason = "ambiguous", "Intent requires clarification"
            continue
        entities = [(_index, _selected(query.entities[_index], selections[_index], context) if _index in selections else query.entities[_index])
                    for _index in requirement.entity_indices]
        if requirement.interpretation == "ambiguous_local_reference":
            chosen = [(index, entity) for index, entity in entities if index in selections]
            if len(chosen) != 1:
                decision.reason = "A singular local reference points to multiple targets"
                continue
            entities = chosen
        if intent != "FEATURE_DISCOVERY":
            if not entities:
                decision.reason = "No target was supplied"
                continue
            blocked = next((entity for _, entity in entities if not _proof_scope(entity, context)), None)
            if blocked is not None:
                decision.blocked_status = "not_found" if blocked.resolution == "not_found" else "unsupported" if blocked.resolution == "unsupported" else "ambiguous"
                decision.reason = blocked.reason or "Choose a canonical target; discovery scores are not target selection"
                continue
            if any(entity.file_path and Path(entity.file_path).suffix not in LANGUAGES for _, entity in entities):
                decision.blocked_status, decision.reason = "unsupported", "Graph/static rule capabilities are JS/TS only"
                continue
        engine, name = ROUTING_POLICY[intent]
        requests = []
        if intent == "FEATURE_DISCOVERY":
            filters = {}
            feature = next((query.entities[index] for index, _ in entities if query.entities[index].kind == "feature"), None)
            if feature and feature.file_path:
                if feature.file_path not in context.indexed_files:
                    decision.reason = "The requested semantic file scope is missing or ambiguous"
                    continue
                filters = {"files": [feature.file_path]}
            parameters = {"query": feature.name if feature else query.normalized_question or query.original_question,
                          "limit": limits.semantic_candidates, "filters": filters}
            requests = [(parameters, entities)]
        elif intent == "DEPENDENCY_PATH" and len(entities) >= 2:
            if len(entities) != 2:
                decision.reason = "A dependency path requires exactly two endpoints"
                continue
            source, target = entities[0][1], entities[1][1]
            if source.symbol_id and target.symbol_id:
                parameters = {"source": source.symbol_id, "target": target.symbol_id, "max_depth": limits.graph_depth,
                              "limit": limits.graph_results, "max_work": 10000}
            elif source.kind in ("module", "file") and target.kind in ("module", "file"):
                name = "find_dependency_paths"
                parameters = {"source": source.file_path, "target": target.file_path, "max_depth": limits.graph_depth, "limit": limits.graph_results}
            else:
                decision.blocked_status, decision.reason = "unsupported", "Mixed symbol/module endpoints require clarification"
                continue
            requests = [(parameters, entities)]
        else:
            if intent == "DEPENDENCY_PATH":
                engine, name = "rules", "dependency_summary"
            for index, entity in entities:
                module = entity.kind in ("file", "module")
                if module and intent in ("SYMBOL_LOCATION", "DIRECT_CALLERS", "DIRECT_CALLEES", "FUNCTION_CONTRACT", "TEST_DISCOVERY"):
                    decision.blocked_status, decision.reason = "unsupported", "The declared operation requires a canonical symbol, not a file/module"
                    requests = []
                    break
                if intent == "FUNCTION_CONTRACT" and entity.kind not in ("function", "method"):
                    decision.blocked_status, decision.reason = "unsupported", "Function contracts do not analyze non-functions"
                    requests = []
                    break
                if intent == "MODULE_OVERVIEW" or module:
                    parameters = {"module_path": entity.file_path, "limit": limits.rule_findings, "max_depth": limits.graph_depth}
                else:
                    parameters = {"selected_symbol_id": entity.symbol_id, "limit": limits.graph_results if engine == "graph" else limits.rule_findings}
                    if engine == "rules":
                        parameters["max_depth"] = limits.graph_depth
                requests.append((parameters, [(index, entity)]))
        if not requests:
            continue
        if len(operations) + len(requests) > limits.max_operations:
            decision.disposition, decision.blocked_status, decision.reason = "limited", "partial", "Operation limit reached; entire requirement explicitly deferred by resource budget"
            continue
        dependencies = [oid for parent in decisions[:-1] if parent.requirement_id in parents for oid in parent.operation_ids]
        for ordinal, (parameters, targets) in enumerate(requests):
            spec = operation_spec(engine, name)
            request = OperationRequest(engine=engine, name=name, repository_id=context.repository_id,
                                       snapshot_id=context.snapshot_id, parameters=parameters)
            spec.validate(request, context)
            oid = f"{query_id}:{rid}:op-{ordinal:02d}"
            max_results = limits.semantic_candidates if engine == "semantic" else limits.graph_results if engine == "graph" else limits.rule_findings
            operations.append(PlannedOperation(operation_id=oid, requirement_id=rid, request=request,
                targets=[PlannedTarget(entity_index=index, entity=entity) for index, entity in targets],
                dependencies=dependencies, target_dependencies=parents, required_requirement_ids=parents,
                capabilities=[f"{engine}.{name}"], budget=OperationBudget(execution_ms=min(spec.max_execution_ms, limits.deadline_ms), max_results=max_results),
                expected_output={"graph":"graph", "semantic":"semantic", "rules":"rule"}[engine]))
            decision.operation_ids.append(oid)
        decision.disposition, decision.blocked_status, decision.reason = "planned", None, None
    return ExecutionPlan(query_id=query_id, repository_id=context.repository_id, snapshot_id=context.snapshot_id,
        normalized=query, limits=limits, selections=selections, requirements=decisions, operations=operations)
