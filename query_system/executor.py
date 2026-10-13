"""Bounded multi-engine execution under one reader pin; no answer generation."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import threading
from time import monotonic
from types import SimpleNamespace

from knowledge_base.metadata import trace
from query_system.contracts import EngineError, EngineResult, EntityCandidate, Evidence, SnapshotContext, StaleIndexError, validate_engine_result
from query_system.execution_contracts import (
    Clarification, ExecutionPlan, ExecutionReport, OperationOutcome, RequirementOutcome,
)
from query_system.graph_engine import GraphQueryEngine, SymbolParameters, _Run
from query_system.router import build_plan, operation_spec
from query_system.rule_engine import RuleBasedAnalysisEngine
from query_system.semantic_engine import SemanticSearchEngine
from query_system.query_understanding import QueryUnderstandingEngine


class _ResourceExceeded(ValueError):
    pass


def existing_engines(channels_factory=None):
    """Create a private engine per operation, bound directly to the supplied view."""
    def factory(name, store, view, context):
        if name == "graph":
            engine = GraphQueryEngine(store, view, context)
        elif name == "rules":
            engine = RuleBasedAnalysisEngine(store, view, context)
            engine.graph_engine._active = True
        elif name == "semantic":
            if channels_factory is None:
                raise RuntimeError("No existing semantic channels were supplied")
            engine = SemanticSearchEngine(store, view, context, channels_factory(view))
        else:
            raise ValueError("Unsupported engine")
        engine._active = True
        return engine
    return factory


class MultiEngineExecutor:
    def __init__(self, store, view, context, factory):
        self.store, self.view, self.context, self.factory = store, view, context, factory
        self.database = Path(view["data_dir"]) / context.repository_id / "metadata.db"
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="codemind-query")
        self._active, self._executing = False, False
        self._counter_lock = threading.Lock()
        self._running = self._peak = 0

    @classmethod
    @contextmanager
    def pinned(cls, store, channels_factory=None, *, engine_factory=None):
        with store.pin_view() as view:
            engine = cls(store, view, SnapshotContext.from_pinned_view(view, store.repo_id),
                         engine_factory or existing_engines(channels_factory))
            engine._active = True
            try:
                yield engine
            finally:
                # A timeout/caller cancellation must not release the generation
                # while synchronous workers are still reading it. No hard cancel.
                engine.pool.shutdown(wait=True, cancel_futures=False)
                engine._active = False

    def plan(self, normalized, limits=None, selections=None):
        return build_plan(normalized, self.context, limits, selections)

    def understand(self, request, conversation=None):
        understanding = QueryUnderstandingEngine(self.store, self.view, self.context)
        understanding._active = self._active
        try:
            return understanding.normalize(request, conversation)
        finally:
            understanding._active = False

    def _verify(self, proof):
        info = trace(self.database, self.context.repository_id, proof.evidence_id)
        actual = Evidence.from_trace(info, self.context, evidence_type=proof.evidence_type, resolution=proof.resolution)
        kinds = {"symbol_definition": {"symbol"}, "graph_relationship": {"relationship"},
                 "code_chunk": {"chunk"}, "source_range": {"node", "symbol"}, "call_site": {"relationship"}}
        if (actual != proof or info["kind"] not in kinds[proof.evidence_type]
                or info["kind"] == "relationship" and info["resolution"] != proof.resolution):
            raise StaleIndexError("Canonical proof mapping/type/resolution differs")
        return actual

    def _target(self, entity):
        proof = self._verify(entity.evidence)
        if (entity.file_path, entity.file_id) != (proof.file_path, proof.file_id):
            raise StaleIndexError("Canonical target scope differs")
        if entity.symbol_id:
            if entity.symbol_id != proof.symbol_id or entity.entity_id != proof.evidence_id:
                raise StaleIndexError("Canonical target identity differs")
            reader = _Run(SimpleNamespace(database=self.database), self.context, monotonic() + 5)
            node, _, _ = reader.symbol(SymbolParameters(selected_symbol_id=entity.symbol_id))
            actual_kind = "class" if node["kind"] == "Class" else "method" if node["kind"] == "Method" else "symbol" if node["kind"] in ("Type", "Interface") else "function"
            if entity.kind != actual_kind:
                raise StaleIndexError("Target language capability/type was misrepresented")

    def _worker(self, operation):
        engine = None
        with self._counter_lock:
            self._running += 1
            self._peak = max(self._peak, self._running)
        try:
            for target in operation.targets:
                if target.entity.resolution == "resolved":
                    self._target(target.entity)
            spec = operation_spec(operation.request.engine, operation.request.name)
            spec.validate(operation.request, self.context)
            engine = self.factory(operation.request.engine, self.store, self.view, self.context)
            result = asyncio.run(engine.execute(operation.request, self.context))
            result = EngineResult.model_validate(result.model_dump(), strict=True)
            evidence = {key: engine.evidence[key] for key in result.evidence_ids}
            validate_engine_result(result, self.context, evidence, spec)
            if len(result.findings) > operation.budget.max_results:
                raise _ResourceExceeded("engine_result_limit")
            for proof in evidence.values():
                self._verify(proof)
            return result, evidence, monotonic()
        finally:
            if engine is not None:
                engine._active = False
                if getattr(engine, "graph_engine", None) is not None:
                    engine.graph_engine._active = False
            with self._counter_lock:
                self._running -= 1

    async def execute(self, plan):
        if self._executing:
            raise ValueError("A pinned executor runs one plan at a time")
        self._executing = True
        self._peak = 0
        try:
            return await self._execute(plan)
        finally:
            self._executing = False

    async def _execute(self, plan):
        started = monotonic()
        plan = ExecutionPlan.model_validate(plan.model_dump(), strict=True)
        state = _Report(self, plan, started)
        if not self._active or (plan.repository_id, plan.snapshot_id) != (self.context.repository_id, self.context.snapshot_id):
            return state.fatal("stale_index", "Plan is outside its pinned generation")
        # Reject tampered names/parameters/budgets/DAGs, not only SQL strings.
        expected = build_plan(plan.normalized, self.context, plan.limits, plan.selections)
        if expected != plan:
            raise ValueError("Plan differs from the validated declarative policy")
        if not self.store.matches(self.context.snapshot_id):
            return state.fatal("stale_index", "Saved sources differ from the pinned generation")
        if plan.normalized.status in ("stale_index", "error"):
            return state.fatal(plan.normalized.status, "Normalization rejected this question")
        deadline = started + plan.limits.deadline_ms / 1000
        pending = list(plan.operations)
        loop = asyncio.get_running_loop()
        while pending:
            if monotonic() >= deadline:
                state.resources.append("overall_deadline")
                for operation in pending:
                    state.failure(operation, "skipped", "Overall deadline reached before dispatch", "budget_exceeded")
                break
            runnable = []
            semantic_admitted = False
            for operation in list(pending):
                parents = state.parent_statuses(operation.required_requirement_ids)
                if any(status is None for status in parents):
                    continue
                if (any(next(item for item in plan.requirements if item.requirement_id == parent).disposition != "planned"
                        for parent in operation.required_requirement_ids)
                        or any(status not in ("success", "partial", "not_found") for status in parents) or any(
                        state.outcomes[dependency].error is not None for dependency in operation.dependencies)):
                    state.failure(operation, "skipped", "A required dependency failed, was deferred, or needs clarification")
                    pending.remove(operation)
                    continue
                if len(runnable) >= plan.limits.max_concurrent:
                    continue
                if operation.request.engine == "semantic":
                    if semantic_admitted:
                        continue
                    semantic_admitted = True
                runnable.append(operation)
            if not runnable:
                if pending:
                    for operation in pending:
                        state.failure(operation, "skipped", "No executable dependency frontier")
                break
            submitted = monotonic()
            futures = {operation.operation_id: loop.run_in_executor(self.pool, self._worker, operation) for operation in runnable}
            drain = asyncio.gather(*futures.values(), return_exceptions=True)
            try:
                _, unfinished = await asyncio.wait(list(futures.values()), timeout=max(0., deadline - monotonic()))
                timed_out = {identifier for identifier, future in futures.items() if future in unfinished}
                if unfinished:
                    state.resources.append("overall_deadline")
                    # Keep the pin alive and drain. Late findings are discarded.
                    await asyncio.shield(drain)
            except asyncio.CancelledError:
                # A caller can cancel repeatedly. Retain the one-plan guard and
                # pin until all synchronous workers finish, then propagate it.
                while not drain.done():
                    try:
                        await asyncio.shield(drain)
                    except asyncio.CancelledError:
                        continue
                raise
            # Stable plan-order commits make evidence caps/rank output independent
            # of worker completion order. Waves do not reorder requirements.
            for operation in runnable:
                pending.remove(operation)
                future = futures[operation.operation_id]
                try:
                    result, evidence, finished = future.result()
                    if operation.operation_id in timed_out or finished > min(deadline, submitted + operation.budget.execution_ms / 1000):
                        state.resources.append("operation_deadline")
                        state.failure(operation, "partial", "Cooperative operation deadline exceeded; late findings discarded", "budget_exceeded")
                    else:
                        state.accept(operation, result, evidence)
                except _ResourceExceeded as error:
                    state.resources.append(str(error))
                    state.failure(operation, "partial", "Engine exceeded its configured result budget", "budget_exceeded")
                except (StaleIndexError, ValueError, KeyError, TypeError, OSError):
                    state.failure(operation, "stale_index", "Engine result or canonical target/evidence is invalid", "stale_index")
                except Exception as error:
                    state.failure(operation, "error", f"Engine unavailable ({type(error).__name__})", "unavailable")
        if not self.store.matches(self.context.snapshot_id):
            return state.fatal("stale_index", "Saved sources changed during execution; all findings discarded")
        if monotonic() >= deadline:
            state.resources.append("overall_deadline")
        report = state.finish()
        try:
            # Includes canonical candidates added by clarification generation.
            for proof in report.evidence.values():
                self._verify(proof)
            if not self.store.matches(self.context.snapshot_id):
                raise StaleIndexError("Saved sources changed before publication")
        except (ValueError, KeyError, TypeError, OSError):
            return state.fatal("stale_index", "Canonical evidence changed before report publication")
        report.execution_ms = int((monotonic() - started) * 1000)
        if monotonic() >= deadline and "overall_deadline" not in report.resource_limits:
            report.resource_limits.append("overall_deadline")
            if report.status == "success":
                report.status = "partial"
        return report


class _Report:
    def __init__(self, executor, plan, started):
        self.executor, self.plan, self.started = executor, plan, started
        self.outcomes, self.evidence = {}, {}
        self.resources, self.clarifications = [], []

    def failure(self, operation, status, reason, code=None):
        self.outcomes[operation.operation_id] = OperationOutcome(operation_id=operation.operation_id,
            requirement_id=operation.requirement_id, status=status, reason=reason,
            error=EngineError(code=code, message=reason) if code else None)

    def accept(self, operation, result, evidence):
        if len(self.evidence.keys() | evidence.keys()) > self.plan.limits.max_evidence:
            self.resources.append("returned_evidence")
            self.failure(operation, "partial", "Evidence limit reached; entire unsupportable operation payload omitted", "budget_exceeded")
            return
        if any(key in self.evidence and self.evidence[key] != value for key, value in evidence.items()):
            raise StaleIndexError("Operations disagree on canonical proof")
        self.evidence.update(evidence)
        self.outcomes[operation.operation_id] = OperationOutcome(operation_id=operation.operation_id,
            requirement_id=operation.requirement_id, status=result.status, result=result, error=result.error)

    def decision_status(self, decision):
        if decision.disposition != "planned":
            return decision.blocked_status
        values = [self.outcomes.get(identifier) for identifier in decision.operation_ids]
        if any(value is None for value in values):
            return None
        statuses = [value.status for value in values]
        if any(value.error for value in values) and any(value.result and value.result.findings for value in values):
            return "partial"
        if all(status == "success" for status in statuses):
            return "success"
        if all(status == "not_found" for status in statuses):
            return "not_found"
        if any(status in ("success", "partial") for status in statuses):
            return "partial"
        return statuses[0]

    def parent_statuses(self, ids):
        return [self.decision_status(next(item for item in self.plan.requirements if item.requirement_id == identifier)) for identifier in ids]

    def add_candidate(self, clarification, candidate):
        if monotonic() >= self.started + self.plan.limits.deadline_ms / 1000:
            self.resources.append("clarification_deadline")
            clarification.truncated = True
            return
        key = candidate.evidence.evidence_id
        if key not in self.evidence and len(self.evidence) >= self.plan.limits.max_evidence:
            self.resources.append("clarification_evidence")
            clarification.truncated = True
            return
        self.executor._verify(candidate.evidence)
        if key in self.evidence and self.evidence[key] != candidate.evidence:
            raise StaleIndexError("Candidate proof conflicts with engine evidence")
        if candidate.entity_id not in {item.entity_id for item in clarification.candidates}:
            if len(clarification.candidates) >= 25:
                clarification.truncated = True
            else:
                self.evidence[key] = candidate.evidence
                clarification.candidates.append(candidate)

    def clarify(self, decision):
        clarification = Clarification(requirement_id=decision.requirement_id,
            entity_indices=decision.requirement.entity_indices, reason=decision.reason or "Choose a verified canonical target")
        for index in clarification.entity_indices:
            entity = self.plan.normalized.entities[index]
            for candidate in entity.candidates:
                self.add_candidate(clarification, candidate)
            if decision.requirement.interpretation == "ambiguous_local_reference" and entity.evidence:
                self.add_candidate(clarification, EntityCandidate(entity_id=entity.entity_id, name=entity.name,
                    kind=entity.kind, file_path=entity.file_path, symbol_id=entity.symbol_id, evidence=entity.evidence))
        # Semantic findings supply candidates only. Even one hit is not selected.
        for parent in decision.target_dependency_ids:
            for operation in self.plan.operations:
                if operation.requirement_id != parent or operation.request.engine != "semantic":
                    continue
                outcome = self.outcomes.get(operation.operation_id)
                if not outcome or not outcome.result or outcome.status not in ("success", "partial"):
                    continue
                clarification.source_operation_ids.append(operation.operation_id)
                for finding in outcome.result.findings:
                    if monotonic() >= self.started + self.plan.limits.deadline_ms / 1000:
                        self.resources.append("clarification_deadline")
                        clarification.truncated = True
                        break
                    proof = self.evidence[finding.chunk_id]
                    if not proof.symbol_id:
                        continue
                    reader = _Run(SimpleNamespace(database=self.executor.database), self.executor.context, monotonic() + 5)
                    node, _, _ = reader.symbol(SymbolParameters(selected_symbol_id=proof.symbol_id))
                    if node is None:
                        continue
                    definition = Evidence.from_trace(trace(self.executor.database, self.executor.context.repository_id, proof.symbol_id),
                        self.executor.context, evidence_type="symbol_definition", resolution="syntax_only")
                    kind = "class" if node["kind"] == "Class" else "method" if node["kind"] == "Method" else "symbol" if node["kind"] in ("Interface", "Type") else "function"
                    self.add_candidate(clarification, EntityCandidate(entity_id=proof.symbol_id, symbol_id=proof.symbol_id,
                        name=node["label"], kind=kind, file_path=proof.file_path, evidence=definition))
        self.clarifications.append(clarification)

    def fatal(self, status, reason):
        self.evidence, self.clarifications = {}, []
        for operation in self.plan.operations:
            self.failure(operation, status, reason, "stale_index" if status == "stale_index" else "unavailable")
        return self.finish(fatal_status=status, fatal_reason=reason)

    def finish(self, fatal_status=None, fatal_reason=None):
        requirements = []
        for decision in self.plan.requirements:
            status = fatal_status or self.decision_status(decision)
            reason = fatal_reason or decision.reason
            if decision.disposition == "limited":
                self.resources.append(decision.reason)
            if not fatal_status and decision.disposition == "blocked" and status in ("ambiguous", "not_found"):
                try:
                    self.clarify(decision)
                except (ValueError, KeyError, TypeError, OSError):
                    status, reason = "stale_index", "Clarification candidate provenance is invalid"
            requirements.append(RequirementOutcome(requirement_id=decision.requirement_id, requirement=decision.requirement,
                status=status, operation_ids=decision.operation_ids, reason=reason))
        statuses = [item.status for item in requirements]
        if fatal_status:
            overall = fatal_status
        elif any(outcome.result and outcome.result.findings for outcome in self.outcomes.values()):
            overall = "success" if all(status == "success" for status in statuses) and not self.resources else "partial"
        elif "ambiguous" in statuses:
            overall = "ambiguous"
        elif "stale_index" in statuses:
            overall = "stale_index"
        elif "error" in statuses:
            overall = "error"
        elif all(status in ("unsupported", "deferred") for status in statuses):
            overall = "unsupported"
        elif statuses and all(status == "not_found" for status in statuses):
            overall = "not_found"
        else:
            overall = "partial"
        return ExecutionReport(query_id=self.plan.query_id, repository_id=self.executor.context.repository_id,
            snapshot_id=self.executor.context.snapshot_id, plan=self.plan, requirements=requirements,
            operations=[self.outcomes[item.operation_id] for item in self.plan.operations], evidence=self.evidence,
            clarifications=self.clarifications, resource_limits=list(dict.fromkeys(self.resources)), status=overall,
            unresolved_dependencies=[item.requirement_id for item in requirements if item.status in ("skipped", "ambiguous", "deferred")],
            execution_ms=int((monotonic() - self.started) * 1000), peak_concurrency=self.executor._peak,
            warnings=["No natural-language answers or evidence aggregation performed; semantic similarity is not a graph dependency",
                      "Timeouts are cooperative: synchronous workers drain under the pin; wall time may exceed the deadline"])
