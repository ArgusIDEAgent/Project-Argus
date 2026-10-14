"""Pure deterministic display projection. No model, retrieval, filesystem or DB calls."""

import json

from query_system.contracts import EngineResult, GraphFinding, RuleFinding
from query_system.evidence_aggregation import stable_id
from query_system.evidence_contracts import EvidencePackage
from query_system.generation_contracts import GenerationResult
from query_system.graph_presentation import format_graph_result
from query_system.presentation_contracts import (
    FollowUp, OperationDisplay, PresentationItem, PresentationResult, PresentationSection,
    PresentationSource, RequirementDisplay,
)
from query_system.router import ROUTING_POLICY, operation_spec

TEMPLATES = {
    "SYMBOL_LOCATION": "Symbol location", "DIRECT_CALLERS": "Known direct callers",
    "DIRECT_CALLEES": "Known direct callees", "DEPENDENCY_PATH": "Static dependencies and paths",
    "FEATURE_DISCOVERY": "Feature-location candidates (similarity, not dependency)",
    "FUNCTION_CONTRACT": "Indexed function contract", "CHANGE_IMPACT": "Potential change impact",
    "MODULE_OVERVIEW": "Indexed module overview", "TEST_DISCOVERY": "Indexed test/reference discovery",
    "FUNCTION_EXPLANATION": "Function explanation", "WORKFLOW_EXPLANATION": "Static workflow explanation",
    "BEHAVIOR_INVESTIGATION": "Possible behavioral causes (not verified root cause)",
    "CODE_COMPARISON": "Comparison of explicit targets",
}
DEPTH_ITEMS = {"quick": 1, "standard": 8, "deep": 25}
BASE_LIMITATIONS = [
    "Static/source evidence does not prove runtime execution, root cause, equivalence or performance.",
    "Model prose is an interpretation, not fully fact-checked; similarity is not a graph dependency.",
    "Depth changes display detail only. No model inference or retrieval is performed.",
]


def _unique(values):
    return list(dict.fromkeys(value for value in values if value))


def _finding_item(finding, package, depth):
    original = finding.original
    details = []
    if isinstance(original, GraphFinding):
        operation = next(item for item in package.operations if item.operation_id == finding.operation_id)
        result = EngineResult(engine="graph", repository_id=package.repository_id, snapshot_id=package.snapshot_id,
            status="success", findings=[original], evidence_ids=original.evidence_ids, execution_ms=0)
        existing = format_graph_result(operation.request.name, result, {item.proof.evidence_id: item.proof for item in package.evidence.values()})
        text = "; ".join(item.label for item in existing.entries)
        if original.relationship != "DEFINITION":
            text = "Static " + original.relationship + ": " + text
        if depth != "quick":
            details = ["Relationship: " + original.relationship]
            if original.path_names:
                details.append("Indexed path direction: " + " → ".join(original.path_names))
            elif original.source_name and original.target_name:
                details.append(original.source_name + " → " + original.target_name)
    elif isinstance(original, RuleFinding):
        text = original.statement
        if depth != "quick":
            keys = ("name", "kind", "parameters", "return_annotation", "declared_async", "exported", "return_behavior", "exception_behavior", "relationship", "source_path", "target_path", "path_names")
            data = original.details if depth == "deep" else {key: original.details[key] for key in keys if key in original.details}
            serialized = json.dumps(data, ensure_ascii=False, sort_keys=True)
            details = [serialized[:6000]] if data else []
            if len(serialized) > 6000:
                details.append("Display detail truncated; original producer finding is unchanged.")
    else:
        text = "Candidate " + str(original.rank) + ": " + (original.name or original.chunk_id) + " (similarity/keyword relevance, not a dependency)"
        if depth != "quick":
            details = ["Retrieval methods: " + "/".join(original.retrieval_methods),
                "Ranking signals: " + json.dumps({"retrieval": original.retrieval_score, "keyword": original.keyword_score, "fusion": original.fusion_score}, sort_keys=True)]
    return PresentationItem(item_id=finding.finding_id, origin="finding", text=text,
        classification=finding.classification, authoritative=finding.authoritative, citation_ids=finding.citation_ids,
        details=details, limitations=finding.limitations)


def _check_generation(package, generation):
    if (generation.package_id, generation.support_query_id, generation.repository_id, generation.snapshot_id) != (package.package_id, package.query_id, package.repository_id, package.snapshot_id):
        raise ValueError("Generated answer is not owned by the supplied support package")
    if any(package.citations.get(key) != value for key, value in generation.citations.items()):
        raise ValueError("Generated citations differ from the backend map")
    facts = {fact.fact_id: fact for fact in generation.context.facts} if generation.context else {}
    approved = {source.record_id for source in generation.context.sources} if generation.context else set()
    known = {finding.finding_id: finding for finding in package.findings}
    for coverage in generation.coverage:
        if set(coverage.supporting_requirement_ids) - {item.requirement_id for item in package.requirements}:
            raise ValueError("Generation has an unknown supporting requirement")
    for section in generation.sections:
        for claim in section.claims:
            if set(claim.record_ids) - (package.evidence.keys() & approved) or set(claim.citation_ids) != {key for key, value in package.citations.items() if value.record_id in claim.record_ids}:
                raise ValueError("Generated claim lost its exact record/citation mapping")
            if claim.verification == "backend_fact":
                fact = facts.get(claim.fact_id)
                if fact is None or claim.finding_id != fact.finding_id or (claim.text, claim.classification, set(claim.record_ids)) != (fact.statement, fact.classification, set(fact.record_ids)):
                    raise ValueError("Generated catalog fact changed")
                if fact.finding_id:
                    finding = known.get(fact.finding_id)
                    if finding is None or fact.classification != finding.classification or set(fact.record_ids) != set(finding.evidence_record_ids):
                        raise ValueError("Generated fact differs from existing accepted producer data")
                else:
                    if len(fact.record_ids) != 1:
                        raise ValueError("Invalid source observation")
                    proof = package.evidence[fact.record_ids[0]].proof
                    if fact.fact_id != "source:" + fact.record_ids[0] or proof.file_path is None or fact.classification != "verified_source_observation":
                        raise ValueError("Source observation differs from canonical proof")


def _follow_ups(requirement, items, package):
    refs = _unique([ref for item in items for ref in item.citation_ids])
    sources = [package.citations[ref].source for ref in refs]
    eligible = {target.reference.symbol_id for coverage in package.requirements for target in coverage.targets
                if target.reference.resolution == "resolved" and target.reference.kind in ("function", "method")}
    symbol = next((source for source in sources if source.symbol_id in eligible and source.evidence_type == "symbol_definition" and source.file_path.rsplit(".", 1)[-1] in ("js", "jsx", "ts", "tsx")), None)
    result = []
    if symbol:
        # The canonical ID is explicit context, not a semantic guess. Suggestions
        # only copy a query; they never dispatch an action in this phase.
        for intent, label, prompt in (("DIRECT_CALLERS", "Show direct callers", "Who calls"),
                                      ("DEPENDENCY_PATH", "Explore dependencies", "Dependencies of"),
                                      ("TEST_DISCOVERY", "Find indexed tests", "What tests cover")):
            engine, operation = ROUTING_POLICY[intent]
            if intent == "DEPENDENCY_PATH":
                engine, operation = "rules", "dependency_summary"  # Existing single-target dispatch.
            operation_spec(engine, operation)
            if requirement.original.intent != intent:
                target = next(target.reference for coverage in package.requirements for target in coverage.targets if target.reference.symbol_id == symbol.symbol_id)
                if "`" in target.name or "`" in symbol.file_path:
                    continue
                result.append(FollowUp(requirement_id=requirement.requirement_id, label=label,
                    prompt=prompt + " `" + target.name + "` in `" + symbol.file_path + "`" + ("?" if intent != "DEPENDENCY_PATH" else ""),
                    engine=engine, operation=operation, target_id=symbol.symbol_id, mode="copy_query"))
    if refs:
        result.append(FollowUp(requirement_id=requirement.requirement_id, label="Inspect verified source",
            prompt=None, engine=None, operation=None, target_id=None, citation_id=refs[0], mode="inspect_source"))
    return result[:3]


def format_response(package: EvidencePackage, generation: GenerationResult | None = None, *, depth="standard") -> PresentationResult:
    """Backend-owned inputs only. Deserialization validates shape, not authentication.

    Fresh navigation authority is deliberately separate from this pure projection.
    """
    if depth not in DEPTH_ITEMS:
        raise ValueError("Unsupported answer depth")
    package = EvidencePackage.model_validate(package.model_dump(), strict=True)
    if generation:
        generation = GenerationResult.model_validate(generation.model_dump(), strict=True)
        _check_generation(package, generation)
    catalog = {finding.finding_id: _finding_item(finding, package, depth) for finding in package.findings}
    if generation:
        for section in generation.sections:
            for claim in section.claims:
                catalog[claim.claim_id] = PresentationItem(item_id=claim.claim_id, origin="generation", text=claim.text,
                    classification=claim.classification, authoritative=claim.authoritative,
                    citation_ids=claim.citation_ids, limitations=[])
    requirements = []
    coverage_items = generation.coverage if generation else package.requirements
    for coverage in coverage_items:
        rid = f"generation-req-{coverage.order:03d}" if generation else coverage.requirement_id
        support = coverage.supporting_requirement_ids if generation else [coverage.requirement_id]
        original = coverage.original
        findings = [finding.finding_id for finding in package.findings if finding.requirement_id in support]
        claims = list(coverage.claim_ids) if generation else []
        # Explanation text is not regenerated or paraphrased at any depth.
        primary = claims if claims else findings
        chosen = primary[:DEPTH_ITEMS[depth]]
        sections = [PresentationSection(title=TEMPLATES.get(original.intent, "Unsupported/clarification required"), item_ids=chosen)]
        if generation and depth != "quick":
            extra = [key for key in findings if key not in chosen][:DEPTH_ITEMS[depth]]
            if extra:
                sections.append(PresentationSection(title="Supporting accepted findings", item_ids=extra))
        displayed = {key for section in sections for key in section.item_ids}
        operations = [operation for operation in package.operations if operation.requirement_id in support]
        limits = _unique([*coverage.limitations,
            *(limit for operation in operations for limit in [*operation.limitations, *operation.unresolved_items, *operation.missing_evidence]),
            *(limit for key in [*primary, *findings] for limit in catalog[key].limitations)])
        if not generation and coverage.truncated:
            limits.append("Evidence was truncated before presentation; analysis coverage is incomplete.")
        for omission in package.omissions:
            if set(omission.requirement_ids) & set(support):
                limits.append("Evidence omitted: " + omission.reason)
        hidden = len(set([*primary, *findings]) - displayed)
        if hidden:
            limits.append(f"{hidden} existing items hidden at {depth} display depth; no additional retrieval was performed.")
        requirements.append(RequirementDisplay(requirement_id=rid, order=coverage.order if generation else original.order,
            original=original, title=TEMPLATES.get(original.intent, "Unsupported/clarification required"), state=coverage.state,
            supporting_requirement_ids=support, sections=sections,
            target_context=_unique([target.reference.name + (" · " + target.reference.file_path if target.reference.file_path else "")
                for coverage_item in package.requirements if coverage_item.requirement_id in support for target in coverage_item.targets]),
            operations=[OperationDisplay(operation_id=item.operation_id, execution_status=item.execution_status,
                coverage_state=item.state, limitations=_unique([*item.limitations, *item.unresolved_items, *item.missing_evidence])) for item in operations],
            limitations=_unique(limits), hidden_items=hidden))
    used = {key for requirement in requirements for section in requirement.sections for key in section.item_ids}
    items = {key: catalog[key] for key in sorted(used)}
    refs = _unique([ref for item in items.values() for ref in item.citation_ids])
    # Include candidate sources for non-executable clarification, without choosing.
    for clarification in package.clarifications:
        refs.extend(ref for ref, citation in package.citations.items() if any(citation.source == candidate.evidence for candidate in clarification.candidates) and ref not in refs)
    sources = {ref: PresentationSource(citation=package.citations[ref]) for ref in sorted(refs)}
    suggestions = []
    for requirement in requirements:
        owned = [items[key] for section in requirement.sections for key in section.item_ids]
        if requirement.state in ("satisfied", "partially_satisfied", "addressed", "partial"):
            suggestions.extend(_follow_ups(requirement, owned, package))
    limitations = _unique([*BASE_LIMITATIONS, *package.limitations, *package.resource_limits,
        *([*generation.limitations, *(["Generation failure: " + generation.failure_category] if generation.failure_category else []),
           *("Unsupported generated assertion: " + item.reason for item in generation.unsupported_assertions)] if generation else [])])
    status = generation.status if generation else package.status
    if package.validation_status in ("invalid", "stale"):
        status = "error" if package.validation_status == "invalid" else "stale_index"
    elif status == "success" and (any(item.state not in ("satisfied", "addressed") for item in requirements) or generation and generation.fallback_status != "not_used"):
        status = "partial"
    identity = [package.package_id, generation.generation_id if generation else None, depth]
    result = PresentationResult(presentation_id=stable_id(identity), query_id=generation.query_id if generation else package.query_id,
        support_query_id=package.query_id, package_id=package.package_id, repository_id=package.repository_id, snapshot_id=package.snapshot_id,
        depth=depth, status=status, items=items, requirements=requirements, sources=sources,
        clarifications=package.clarifications, follow_ups=suggestions[:6], limitations=limitations,
        model_status=generation.model_status if generation else None, fallback_status=generation.fallback_status if generation else None)
    if len(result.model_dump_json().encode()) > 1048576:
        raise ValueError("Presentation size budget exceeded; no incomplete mandatory envelope released")
    return result
