"""Minimal Phases 5–9 integration, not a new understanding/routing/retrieval engine."""

from collections import defaultdict
from dataclasses import dataclass, field
from time import monotonic, perf_counter
import re

from query_system.contracts import EngineError, IntentRequirement, NormalizedQuery
from query_system.evidence_aggregation import EvidenceAggregator, FAILURES, stable_id
from query_system.evidence_contracts import EvidencePackage
from query_system.executor import MultiEngineExecutor
from query_system.generation_contracts import GenerationRequest, GenerationResult
from query_system.generation_safety import PresentationSafety
from query_system.graph_rag import GraphRagGenerator
from query_system.hybrid_contracts import GenerationDiagnostic, HybridQueryRequest, HybridResponse, OperationDiagnostic, PipelineTiming
from query_system.presentation_contracts import PresentationResult
from query_system.presentation_preview import _scope_unavailable
from query_system.response_presentation import DEPTH_ITEMS, format_response

EXPLANATIONS = {"FUNCTION_EXPLANATION": "function", "WORKFLOW_EXPLANATION": "workflow",
                "BEHAVIOR_INVESTIGATION": "behavior", "CODE_COMPARISON": "comparison"}
STATISTICS = re.compile(r"(?is)^\s*(?:how many|count(?: all)?|total(?: number)? of|number of)\b.*\b(?:files|functions|classes|lines|modules|symbols|methods)\b")


@dataclass
class HybridRun:
    response: HybridResponse
    package: EvidencePackage | None = None
    original: NormalizedQuery | None = None
    query_id: str | None = None
    bindings: dict[int, list[str]] = field(default_factory=dict)
    generations: list[tuple[list[int], GenerationResult]] = field(default_factory=list)


def _project_support(original):
    """Append declared existing operations, with original rows unchanged and first."""
    rows = [row.model_copy(deep=True) for row in original.requirements]
    bindings = defaultdict(list)
    def key_for(intent, indices):
        return intent, tuple((original.entities[index].entity_id or original.entities[index].symbol_id, original.entities[index].file_path)
            if original.entities[index].resolution == "resolved" else ("unresolved", index) for index in indices)
    seen = {key_for(row.intent, row.entity_indices): row.order for row in rows if row.intent not in EXPLANATIONS
            and all(original.requirements[parent].intent not in EXPLANATIONS for parent in row.depends_on)}
    wanted = []
    for row in original.requirements:
        if row.intent not in EXPLANATIONS:
            continue
        targets = row.entity_indices
        for index in targets:
            wanted.append((row.order, "SYMBOL_LOCATION", [index], row.depends_on))
        if row.intent == "WORKFLOW_EXPLANATION" and len(targets) == 2:
            wanted.append((row.order, "DEPENDENCY_PATH", targets, row.depends_on))
        for index in targets:
            # These are supplied support operations, not a reclassification.
            wanted.extend((row.order, intent, [index], row.depends_on) for intent in
                ("FUNCTION_CONTRACT", "DIRECT_CALLEES", *( ["DIRECT_CALLERS"] if row.intent == "FUNCTION_EXPLANATION" else [])))
    # Fair essential-source admission before optional contract/relationship detail.
    wanted.sort(key=lambda item: (item[1] != "SYMBOL_LOCATION", item[1] != "DEPENDENCY_PATH", item[0]))
    omitted = []
    for owner, intent, targets, parents in wanted:
        key = key_for(intent, targets)
        if key in seen:
            index = seen[key]
        elif len(rows) < 12:
            index = len(rows)
            seen[key] = index
            rows.append(IntentRequirement(intent=intent, order=index, entity_indices=list(targets), depends_on=list(parents)))
        else:
            omitted.append(owner)
            continue
        rid = f"req-{index:03d}"
        if rid not in bindings[owner]:
            bindings[owner].append(rid)
    for row in original.requirements:
        if row.intent in EXPLANATIONS and not bindings[row.order]:
            bindings[row.order] = [f"req-{row.order:03d}"]  # Explicit insufficient evidence, never an empty hidden binding.
    intents = list(dict.fromkeys(row.intent for row in rows)) or list(original.intents)
    projection = original.model_copy(update={"requirements": rows, "intents": intents,
        "primary_intent": intents[0], "secondary_intents": intents[1:]})
    return NormalizedQuery.model_validate(projection.model_dump(), strict=True), dict(bindings), sorted(set(omitted))


def _generation_request(request, original, orders, package, bindings, remaining_ms):
    rows = []
    local = {order: index for index, order in enumerate(orders)}
    for order in orders:
        row = original.requirements[order]
        rows.append(row.model_copy(update={"order": local[order], "depends_on": [local[parent] for parent in row.depends_on if parent in local]}))
    intents = list(dict.fromkeys(row.intent for row in rows))
    normalized = original.model_copy(update={"requirements": rows, "intents": intents,
        "primary_intent": intents[0], "secondary_intents": intents[1:]})
    budget = request.limits.generation.model_copy(update={"deadline_ms": min(request.limits.generation.deadline_ms, max(1, remaining_ms))})
    return GenerationRequest(query=request.query, normalized=normalized, package_id=package.package_id,
        explanation_type=EXPLANATIONS[rows[0].intent], bindings={local[order]: bindings[order] for order in orders}, budget=budget)


def compose_presentation(run, depth):
    """Combine existing projections; one package namespace, original requirements."""
    package, original = run.package, run.original
    view = format_response(package, depth=depth)
    view.query_id = run.query_id
    view.requirements = view.requirements[:len(original.requirements)]
    replacements = {}
    for orders, generation in run.generations:
        partial = format_response(package, generation, depth=depth)
        view.items.update(partial.items)
        view.sources.update(partial.sources)
        view.limitations.extend(partial.limitations)
        for local, order in enumerate(orders):
            display = partial.requirements[local]
            display.requirement_id, display.order = f"req-{order:03d}", order
            display.original = original.requirements[order]
            replacements[order] = display
        for suggestion in partial.follow_ups:
            local = int(suggestion.requirement_id.rsplit("-", 1)[1])
            view.follow_ups.append(suggestion.model_copy(update={"requirement_id": f"req-{orders[local]:03d}"}))
    for order, display in replacements.items():
        view.requirements[order] = display
    for order, support in run.bindings.items():
        display = view.requirements[order]
        if order not in replacements:
            # Generation disabled/budgeted: show existing support, not invented prose.
            ids = [finding.finding_id for finding in package.findings if finding.requirement_id in support]
            display.sections[0].item_ids = ids[:DEPTH_ITEMS[depth]]
            display.supporting_requirement_ids = support
            display.hidden_items = max(0, len(ids) - DEPTH_ITEMS[depth])
            display.limitations.append("Explanation deferred: optional generation disabled, insufficient time, or generation-call budget reached.")
        targets = [original.entities[index] for index in display.original.entity_indices]
        if targets and any(target.resolution != "resolved" for target in targets):
            display.state = "not_found" if any(target.resolution == "not_found" for target in targets) else "unsupported" if any(target.resolution == "unsupported" for target in targets) else "ambiguous"
        display.target_context = [target.name + (" · " + target.file_path if target.file_path else "") for target in targets]
    used = {key for req in view.requirements for section in req.sections for key in section.item_ids}
    # Base projection may hide support rows at Quick depth; get their existing items
    # from its catalog without introducing new facts.
    if used - view.items.keys():
        support_view = format_response(package, depth="deep")
        view.items.update({key: support_view.items[key] for key in used - view.items.keys() if key in support_view.items})
    view.items = {key: view.items[key] for key in sorted(used)}
    reqids = {req.requirement_id for req in view.requirements}
    support_owner = {rid: f"req-{order:03d}" for order, values in run.bindings.items() for rid in values}
    clarifications = []
    for item in package.clarifications:
        rid = item.requirement_id if item.requirement_id in reqids else support_owner.get(item.requirement_id)
        if rid:
            clarifications.append(item.model_copy(update={"requirement_id": rid}))
    view.clarifications = clarifications
    references = {ref for item in view.items.values() for ref in item.citation_ids}
    references.update(ref for ref, citation in package.citations.items() if any(citation.source == candidate.evidence for item in clarifications for candidate in item.candidates))
    view.sources = {ref: view.sources[ref] for ref in sorted(references)}
    view.follow_ups = [item for item in view.follow_ups if item.requirement_id in reqids and (item.citation_id is None or item.citation_id in view.sources)][:6]
    states = [req.state for req in view.requirements]
    view.status = ("success" if states and all(state in ("satisfied", "addressed") for state in states) else
        "partial" if view.items else "ambiguous" if "ambiguous" in states else "stale_index" if "stale" in states else
        "error" if "error" in states else "not_found" if states and all(state == "not_found" for state in states) else "unsupported")
    if original.warnings:
        view.limitations.extend(original.warnings)
        if view.status == "success": view.status = "partial"
    model_states = [result.model_status for _, result in run.generations]
    fallback_states = [result.fallback_status for _, result in run.generations]
    view.model_status = "/".join(dict.fromkeys(model_states)) if model_states else None
    view.fallback_status = "/".join(dict.fromkeys(fallback_states)) if fallback_states else None
    view.limitations = list(dict.fromkeys([*view.limitations, *run.response.limitations]))
    view.presentation_id = stable_id([package.package_id, run.query_id, depth, [result.generation_id for _, result in run.generations]])
    return PresentationResult.model_validate(view.model_dump(), strict=True)


async def run_hybrid(store, request: HybridQueryRequest, channels, answers, *, client=None, engine_factory=None):
    request = HybridQueryRequest.model_validate(request.model_dump(), strict=True)
    start = perf_counter(); deadline = monotonic() + request.limits.deadline_ms / 1000
    timing = PipelineTiming()
    run = HybridRun(HybridResponse(status="error", snapshot_id=None, presentation=None, timings=timing))
    try:
        with MultiEngineExecutor.pinned(store, channels, engine_factory=engine_factory) as executor:
            executor.context.validate_request(request.query)
            run.response.snapshot_id = executor.context.snapshot_id
            before = perf_counter(); original = executor.understand(request.query)
            timing.understanding_ms = (perf_counter() - before) * 1000
            run.original = original
            before = perf_counter()
            run.query_id = executor.plan(original, request.limits.execution).query_id
            if STATISTICS.search(request.query.question):
                run.response.status = "unsupported"
                run.response.error = EngineError(code="invalid_operation", message="Repository statistics are not an implemented hybrid capability.")
                return run
            projection, run.bindings, omitted = _project_support(original)
            if omitted:
                run.response.limitations.append("Support admission reached the existing 12-requirement bound; affected original orders: " + str(omitted))
            remaining = max(1, int((deadline - monotonic()) * 1000))
            limits = request.limits.execution.model_copy(update={"deadline_ms": min(request.limits.execution.deadline_ms, remaining)})
            plan = executor.plan(projection, limits)
            timing.planning_ms = (perf_counter() - before) * 1000
            before = perf_counter(); report = await executor.execute(plan)
            timing.execution_ms = (perf_counter() - before) * 1000
            run.response.operations = [OperationDiagnostic(operation_id=item.operation_id, requirement_id=item.requirement_id,
                engine=item.request.engine, operation=item.request.name, status=outcome.status,
                supporting=int(item.requirement_id.rsplit("-", 1)[1]) >= len(original.requirements)) for item, outcome in zip(plan.operations, report.operations)]
            before = perf_counter()
            verifier = EvidenceAggregator.from_executor(executor, source_filter=PresentationSafety(answers).text)
            run.package = verifier.aggregate(report, request.limits.evidence)
            timing.aggregation_ms = (perf_counter() - before) * 1000
            groups = defaultdict(list)
            for row in original.requirements:
                if row.intent in EXPLANATIONS:
                    groups[EXPLANATIONS[row.intent]].append(row.order)
            calls = 0
            for kind, orders in groups.items():
                remaining = int((deadline - monotonic()) * 1000)
                if not request.allow_generation or calls >= request.limits.max_generation_calls or remaining <= 0:
                    continue
                generation_request = _generation_request(request, original, orders, run.package, run.bindings, remaining)
                result = GraphRagGenerator(executor, answers, client=client).generate(generation_request, run.package)
                calls += result.model_calls
                run.generations.append((orders, result))
                timing.llm_preparation_ms += result.preparation_ms
                timing.llm_generation_ms += result.model_ms
                timing.generation_total_ms += result.total_ms
                run.response.generations.append(GenerationDiagnostic(task=kind, original_orders=orders, model_status=result.model_status,
                    fallback_status=result.fallback_status, failure_category=result.failure_category, model_attempts=result.model_calls))
            before = perf_counter(); view = compose_presentation(run, request.query.depth)
            timing.formatting_ms = (perf_counter() - before) * 1000
            before = perf_counter()
            if (store.active() or {}).get("id") != executor.context.snapshot_id or not store.matches(executor.context.snapshot_id) or run.package.validation_status in ("invalid", "stale"):
                _scope_unavailable(view, "stale", "Saved source/provenance changed during this request; no current answer is released.")
                view.items = {}
                for requirement in view.requirements:
                    for section in requirement.sections: section.item_ids = []
            else:
                for source in view.sources.values():
                    verifier.verify(source.citation.source)
                    source.state, source.reason = "verified", "Validated under the question's pinned generation; rechecked before navigation."
                if (store.active() or {}).get("id") != executor.context.snapshot_id or not store.matches(executor.context.snapshot_id):
                    _scope_unavailable(view, "stale", "Saved source changed during final verification; ask again.")
                    view.items = {}
                    for requirement in view.requirements:
                        for section in requirement.sections: section.item_ids = []
            if monotonic() > deadline:
                view.limitations.append("Cooperative overall deadline exceeded; synchronous work may outlast the requested deadline.")
                if view.status == "success": view.status = "partial"
            timing.validation_ms = (perf_counter() - before) * 1000
            run.response.presentation = PresentationResult.model_validate(view.model_dump(), strict=True)
            run.response.status = run.response.presentation.status
    except (RuntimeError, *FAILURES) as error:
        run.response.presentation = None
        run.response.status = "stale_index" if isinstance(error, (OSError, ValueError)) else "error"
        run.response.error = EngineError(code="stale_index" if run.response.status == "stale_index" else "unavailable", message="Hybrid request cannot establish current canonical repository evidence. Refresh/check the registered snapshot.")
    finally:
        timing.total_ms = (perf_counter() - start) * 1000
    return run
