"""Fail-closed Phase 7 packaging under an existing Phase 6 reader pin."""

from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from query_system.contracts import Evidence, GraphFinding, RuleFinding, SemanticFinding, validate_engine_result
from query_system.evidence_contracts import (
    Citation, EvidenceBudgetError, EvidenceConflict, EvidenceLimits, EvidenceOmission,
    EvidencePackage, EvidenceRecord, EvidenceUse, OperationCoverage, RequirementCoverage, RequirementTarget, SupportedFinding,
)
from query_system.execution_contracts import ExecutionPlan, ExecutionReport
from query_system.router import operation_spec
from knowledge_base.identity import file_id, relative_path
from knowledge_base.metadata import trace


STATES = {"success": "satisfied", "partial": "partially_satisfied", "stale_index": "stale",
          **{value: value for value in ("ambiguous", "unsupported", "not_found", "error", "skipped", "deferred")}}
FAILURES = (ValueError, KeyError, TypeError, OSError, sqlite3.Error)
BASE_LIMITATIONS = [
    "Verified means indexed static/source evidence, not runtime behavior or completeness.",
    "Semantic similarity establishes source relevance only, never a graph dependency.",
    "Rule statement interpretation is inherited from trusted deterministic producers; rules are not recomputed.",
    "Source context is untrusted data, not instructions; no model inference or answer formatting is performed.",
]


def stable_id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:32]


def classify(finding):
    if isinstance(finding, SemanticFinding) or isinstance(finding, GraphFinding) and finding.relationship == "DEFINITION":
        return "verified_source_observation", "source_presence_or_indexed_declaration"
    if isinstance(finding, GraphFinding):
        return ("derived_analysis", "bounded_static_path") if finding.path_node_ids else ("verified_graph_relationship", "resolved_static_relationship")
    if isinstance(finding, RuleFinding):
        if finding.category in ("unresolved_relationship", "candidate"):
            return "unresolved_assertion", "unresolved_target_or_relationship"
        if finding.classification == "hypothesis" or finding.category == "possible_impact":
            return "hypothetical_consequence", "possible_review_location_not_proven_breakage"
        if finding.classification == "derived_finding":
            return "derived_analysis", "bounded_deterministic_analysis"
        if finding.details.get("relationship") and finding.details.get("source_node_id"):
            return "verified_graph_relationship", "resolved_static_relationship"
        return "verified_source_observation", "indexed_declaration_or_static_observation"
    raise ValueError("Only Graph, Semantic and Rule findings are supported")


class _Invalid(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class EvidenceAggregator:
    """Caller-owned synchronous aggregation; from_executor does not acquire a new pin."""
    def __init__(self, executor, *, source_filter=None):
        self.executor = executor
        self.store, self.view, self.context = executor.store, executor.view, executor.context
        self.database = Path(self.view["data_dir"]) / self.context.repository_id / "metadata.db"
        self.source_filter = source_filter

    @classmethod
    def from_executor(cls, executor, *, source_filter=None):
        if not executor._active:
            raise ValueError("Aggregation requires an open executor reader pin")
        return cls(executor, source_filter=source_filter)

    def verify(self, proof):
        if not self.executor._active:
            raise _Invalid("expired_pin", "The reader pin is closed")
        if proof.repository_id != self.context.repository_id:
            raise _Invalid("repository_mismatch", "Evidence belongs to another repository")
        if proof.snapshot_id != self.context.snapshot_id:
            raise _Invalid("generation_mismatch", "Evidence belongs to another published generation")
        if proof.index_snapshot_id not in self.context.ready_stage_ids:
            raise _Invalid("stage_mismatch", "Evidence stage is not ready within this generation")
        info = trace(self.database, self.context.repository_id, proof.evidence_id)
        actual = Evidence.from_trace(info, self.context, evidence_type=proof.evidence_type, resolution=proof.resolution)
        for field, code in (("source_hash", "source_hash"), ("start_line", "source_range"), ("end_line", "source_range"),
                            ("file_id", "canonical_identity"), ("symbol_id", "canonical_identity"), ("file_path", "canonical_identity")):
            if getattr(actual, field) != getattr(proof, field):
                raise _Invalid(code, "Source proof differs from canonical metadata; no location repair performed")
        kinds = {"symbol_definition": {"symbol"}, "code_chunk": {"chunk"}, "source_range": {"symbol", "node"},
                 "call_site": {"relationship"}, "graph_relationship": {"relationship"}}
        if (actual != proof or info["kind"] not in kinds[proof.evidence_type]
                or info["kind"] == "relationship" and info["resolution"] != proof.resolution):
            raise _Invalid("provenance", "Proof type, stage, schema, origin or resolution disagrees with metadata")
        if proof.file_path and proof.file_id != file_id(self.context.repository_id, proof.file_path):
            raise _Invalid("canonical_identity", "File ID differs from canonical repository/path identity")
        if proof.symbol_id:
            with closing(self._connection()) as db:
                row = db.execute("SELECT relative_path FROM symbols WHERE repository_id=? AND id=?", (self.context.repository_id, proof.symbol_id)).fetchone()
            if row is None or row[0] != proof.file_path:
                raise _Invalid("canonical_identity", "Symbol does not map to this canonical file")
        return info

    def _connection(self):
        db = sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db

    def _node(self, identifier):
        with closing(self._connection()) as db:
            row = db.execute("SELECT * FROM graph_nodes WHERE repository_id=? AND id=?", (self.context.repository_id, identifier)).fetchone()
        if row is None:
            raise _Invalid("canonical_identity", "Graph endpoint is not canonical in this repository")
        info = trace(self.database, self.context.repository_id, row["symbol_id"] or row["id"])
        Evidence.from_trace(info, self.context, evidence_type="symbol_definition" if row["symbol_id"] else "source_range", resolution="syntax_only")
        if info.get("symbolId") != row["symbol_id"] or info.get("path") is not None and info["path"] != row["relative_path"]:
            raise _Invalid("canonical_identity", "Graph endpoint source mapping conflicts")
        return dict(row)

    def _graph(self, finding, infos):
        if finding.relationship == "DEFINITION":
            if finding.entity_id not in finding.evidence_ids or infos[finding.entity_id]["kind"] not in ("symbol", "node"):
                raise _Invalid("claim_provenance", "Definition has no canonical definition proof")
            identifier = finding.target_node_id
            if identifier is None:
                with closing(self._connection()) as db:
                    row = db.execute("SELECT id FROM graph_nodes WHERE repository_id=? AND symbol_id=?", (self.context.repository_id, finding.target_symbol_id)).fetchone()
                identifier = row[0] if row else None
            node = self._node(identifier)
            if ((node["symbol_id"] or node["id"]) != finding.entity_id
                    or finding.target_symbol_id is not None and node["symbol_id"] != finding.target_symbol_id
                    or finding.entity_name is not None and node["label"] != finding.entity_name):
                raise _Invalid("canonical_identity", "Definition identity/name differs from canonical declaration")
            return
        edges = [infos[key] for key in finding.evidence_ids if infos[key]["kind"] == "relationship"]
        if not edges or any(edge["resolution"] != "resolved" or edge["relationship"] != finding.relationship for edge in edges):
            raise _Invalid("claim_provenance", "Graph assertion lacks matching resolved static relationship evidence")
        chain = finding.path_node_ids
        if chain:
            if len(edges) != len(chain) - 1 or [(edge["source_id"], edge["target_id"]) for edge in edges] != list(zip(chain, chain[1:])):
                raise _Invalid("claim_provenance", "Graph path is not supported by an ordered contiguous edge chain")
            nodes = [self._node(key) for key in chain]
            if [node["label"] for node in nodes] != finding.path_names:
                raise _Invalid("canonical_identity", "Path names differ from canonical endpoints")
        elif len(edges) != 1:
            raise _Invalid("claim_provenance", "Direct graph assertion must have one relationship proof")
        source, target = self._node(edges[0]["source_id"]), self._node(edges[-1]["target_id"])
        for side, node in (("source", source), ("target", target)):
            for suffix, column in (("node_id", "id"), ("symbol_id", "symbol_id"), ("name", "label")):
                supplied = getattr(finding, side + "_" + suffix)
                if supplied is not None and supplied != node[column]:
                    raise _Invalid("canonical_identity", "Claim endpoint identity/name contradicts indexed relationship")

    def _finding(self, finding, operation, infos):
        if isinstance(finding, GraphFinding):
            self._graph(finding, infos)
        elif isinstance(finding, SemanticFinding):
            if finding.chunk_id not in finding.evidence_ids or infos[finding.chunk_id]["kind"] != "chunk":
                raise _Invalid("claim_provenance", "Semantic relevance lacks its canonical code chunk")
        elif isinstance(finding, RuleFinding):
            if finding.rule_id != operation.request.name:
                raise _Invalid("claim_provenance", "Rule ID differs from executed deterministic operation")
            details = finding.details
            if finding.category == "function_contract" and (details.get("return_behavior") not in (None, "not_inferred") or details.get("exception_behavior") not in (None, "not_indexed")):
                raise _Invalid("claim_provenance", "Runtime return/exception behavior is not established by indexed declarations")
            if details.get("relationship") and details.get("source_node_id"):
                keys = set(GraphFinding.model_fields) - {"kind", "evidence_ids"}
                graph = GraphFinding(**{key: value for key, value in details.items() if key in keys}, evidence_ids=finding.evidence_ids)
                self._graph(graph, infos)
                for side in ("source", "target"):
                    if side + "_path" in details and self._node(getattr(graph, side + "_node_id"))["relative_path"] != details[side + "_path"]:
                        raise _Invalid("canonical_identity", "Rule relationship path contradicts canonical endpoint")
            if details.get("id"):
                with closing(self._connection()) as db:
                    row = db.execute("SELECT * FROM symbols WHERE repository_id=? AND id=?", (self.context.repository_id, details["id"])).fetchone()
                if row is None or details["id"] not in finding.evidence_ids:
                    raise _Invalid("canonical_identity", "Rule declaration has no canonical symbol proof")
                for key in ("name", "kind", "qualified_name", "relative_path", "start_line", "end_line", "return_annotation"):
                    if key in details and details[key] != row[key]:
                        raise _Invalid("canonical_identity", "Rule declaration fields contradict canonical symbol metadata")
                for key, expected in (("parameters", json.loads(row["parameters_json"])), ("modifiers", json.loads(row["modifiers_json"])),
                                      ("exported", bool(row["exported"])), ("default_export", bool(row["default_export"])),
                                      ("declared_async", "async" in json.loads(row["modifiers_json"]))):
                    if key in details and details[key] != expected:
                        raise _Invalid("claim_provenance", "Declared signature/flags contradict indexed metadata")
            if details.get("node_id") and details.get("path"):
                if self._node(details["node_id"])["relative_path"] != details["path"]:
                    raise _Invalid("canonical_identity", "Possible-impact location contradicts canonical node")
                edges = [infos[key] for key in finding.evidence_ids if infos[key]["kind"] == "relationship"]
                chain = details.get("through_path", [])
                if (not edges or edges[0]["source_id"] != details["node_id"]
                        or any(edge["relationship"] != details.get("relationship") or edge["resolution"] != "resolved" for edge in edges)
                        or chain and [(edge["source_id"], edge["target_id"]) for edge in edges] != list(zip(chain, chain[1:]))):
                    raise _Invalid("claim_provenance", "Hypothetical location lacks its claimed static connectivity")
            if details.get("target") and details["target"] not in finding.evidence_ids:
                raise _Invalid("canonical_identity", "Rule target differs from its canonical supporting identity")
            if finding.category == "module_scope" and details.get("path") not in {info.get("path") for info in infos.values() if info["entityId"] in finding.evidence_ids}:
                raise _Invalid("canonical_identity", "Module overview has a mismatched source path")
        else:
            raise _Invalid("unsupported_finding", "Graph RAG is outside Phase 7")

    def aggregate(self, report: ExecutionReport, limits=None):
        limits = EvidenceLimits.model_validate((limits or EvidenceLimits()).model_dump(), strict=True)
        if limits.include_source_context and self.source_filter is None:
            raise ValueError("Source context requires an explicit trusted redaction/sanitization filter")
        # Capture immutable input copies; do not repair or mutate upstream proofs.
        plan = ExecutionPlan.model_validate(report.plan.model_dump(), strict=True)
        state = _Aggregation(self, report, plan, limits)
        if not self.executor._active or not self.store.matches(self.context.snapshot_id):
            return state.fatal("stale", "snapshot_stale", "Reader pin expired or saved sources differ from the generation")
        try:
            report = ExecutionReport.model_validate(report.model_dump(), strict=True)
            if (report.repository_id, report.snapshot_id, plan.repository_id, plan.snapshot_id) != (
                    self.context.repository_id, self.context.snapshot_id, self.context.repository_id, self.context.snapshot_id):
                raise ValueError("Execution identity is outside this pin")
            state.report = report
        except FAILURES:
            return state.fatal("invalid", "execution_mismatch", "Execution envelope, ownership or repository/generation metadata conflicts")
        try:
            state.validate()
            package = state.pack()
        except EvidenceBudgetError:
            raise
        except FAILURES:
            return state.fatal("stale", "snapshot_stale", "Canonical context changed or became unavailable during packaging")
        try:
            for record in package.evidence.values():
                self.verify(record.proof)
            if not self.store.matches(self.context.snapshot_id):
                raise ValueError("Sources changed during aggregation")
        except FAILURES:
            return state.fatal("stale", "snapshot_stale", "Source/provenance changed before package publication; all claims discarded")
        return package


class _Aggregation:
    def __init__(self, owner, report, plan, limits):
        self.owner, self.report, self.plan, self.limits = owner, report, plan, limits
        body = report.model_dump(mode="json", exclude={"execution_ms", "peak_concurrency"})
        body["plan"]["normalized"].pop("normalization_ms", None)
        for operation in body["operations"]:
            if operation.get("result"):
                operation["result"].pop("execution_ms", None)
        self.execution_id = stable_id(body)
        self.package_id = stable_id([self.execution_id, limits.model_dump()])
        self.infos, self.valid, self.claims, self.conflicts, self.omissions = {}, {}, [], [], []
        self.bad = set()
        self.excerpts = {}

    def conflict(self, code, message, keys=(), claims=()):
        owners = [use for key in keys for use in self._owners(key)]
        self.conflicts.append(EvidenceConflict(code=code, message=message, evidence_ids=list(keys), finding_ids=list(claims),
            operation_ids=list(dict.fromkeys(use.operation_id for use in owners if use.operation_id is not None)),
            requirement_ids=list(dict.fromkeys(use.requirement_id for use in owners))))
        self.bad.update(keys)

    def omit(self, reason, requirements, operation_id=None, finding_id=None, keys=()):
        event = EvidenceOmission(reason=reason, requirement_ids=list(requirements), operation_id=operation_id,
                                 finding_id=finding_id, evidence_ids=list(keys))
        if event not in self.omissions:
            self.omissions.append(event)

    def validate(self):
        for key, proof in self.report.evidence.items():
            try:
                self.infos[key] = self.owner.verify(proof)
                self.valid[key] = proof
            except FAILURES as error:
                self.conflict(getattr(error, "code", "provenance"), "Evidence failed canonical source/provenance verification", [key])
        for operation, outcome in zip(self.plan.operations, self.report.operations):
            if not outcome.result:
                continue
            try:
                spec = operation_spec(operation.request.engine, operation.request.name)
                spec.validate(operation.request, self.owner.context)
                validate_engine_result(outcome.result, self.owner.context, self.report.evidence, spec)
                if len(outcome.result.findings) > operation.budget.max_results:
                    raise ValueError("Result exceeded its execution finding budget")
            except FAILURES:
                self.conflict("execution_mismatch", "Operation result is incompatible with its declared capability", outcome.result.evidence_ids)
            for index, finding in enumerate(outcome.result.findings):
                fid = stable_id([operation.operation_id, index, finding.model_dump(mode="json")])
                if set(finding.evidence_ids) & self.bad:
                    continue
                try:
                    self.owner._finding(finding, operation, self.infos)
                    classification, scope = classify(finding)
                    self.claims.append(SupportedFinding(finding_id=fid, operation_id=operation.operation_id,
                        requirement_id=operation.requirement_id, engine=outcome.result.engine, original=finding,
                        classification=classification, authoritative=classification not in ("hypothetical_consequence", "unresolved_assertion"),
                        claim_scope=scope, evidence_record_ids=[stable_id(self.valid[key].model_dump(mode="json")) for key in dict.fromkeys(finding.evidence_ids)],
                        limitations=list(dict.fromkeys([*outcome.result.warnings, *(finding.limitations if isinstance(finding, RuleFinding) else []), *BASE_LIMITATIONS[:2]]))))
                except FAILURES as error:
                    self.conflict(getattr(error, "code", "claim_provenance"), "Claim contradicts or lacks canonical provenance; shared proofs quarantined", finding.evidence_ids, [fid])
        # Never choose one side of a contradiction sharing the same canonical proof.
        self.claims = [claim for claim in self.claims if not set(claim.original.evidence_ids) & self.bad]
        self.valid = {key: proof for key, proof in self.valid.items() if key not in self.bad}

    def _excerpt(self, proof):
        if proof.evidence_id not in self.excerpts:
            path = self.owner.store.root / relative_path(proof.file_path)
            if path.resolve(strict=True) != path or not path.is_file():
                raise ValueError("Source path changed")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != proof.source_hash:
                raise ValueError("Source hash changed")
            # Match trace()'s byte-line convention. str.splitlines() also splits
            # Unicode separators inside strings and can select the wrong code.
            text = b"\n".join(raw.splitlines()[proof.start_line - 1:proof.end_line]).decode("utf-8", "replace")
            filtered = self.owner.source_filter(text)
            if not isinstance(filtered, str):
                raise ValueError("Source filter did not return text")
            self.excerpts[proof.evidence_id] = filtered
        return self.excerpts[proof.evidence_id]

    def pack(self):
        selected, used, counts = [], set(), defaultdict(int)
        grouped = [[claim for claim in self.claims if claim.requirement_id == item.requirement_id] for item in self.plan.requirements]
        # Fair first-evidence admission; original ordering is restored in output.
        for ordinal in range(max(map(len, grouped), default=0)):
            for group in grouped:
                if ordinal >= len(group):
                    continue
                claim = group[ordinal]
                original = claim.original
                detail = original.model_dump(mode="json")
                lists = [detail.get("path_node_ids", []), detail.get("path_names", []), detail.get("evidence_ids", [])]
                if isinstance(original, RuleFinding):
                    lists.extend(value for value in original.details.values() if isinstance(value, list))
                reason = ("findings_per_requirement" if counts[claim.requirement_id] >= self.limits.findings_per_requirement else
                          "relationship_detail" if any(len(value) > self.limits.max_relationship_items for value in lists) else
                          "finding_size" if len(original.model_dump_json().encode()) > self.limits.max_finding_bytes else
                          "evidence_records" if len(used | set(claim.original.evidence_ids)) > self.limits.max_records else None)
                if reason:
                    self.omit(reason, [claim.requirement_id], claim.operation_id, claim.finding_id, claim.original.evidence_ids)
                else:
                    selected.append(claim)
                    used.update(claim.original.evidence_ids)
                    counts[claim.requirement_id] += 1
        selected_ids = {item.finding_id for item in selected}
        selected = [claim for claim in self.claims if claim.finding_id in selected_ids]
        extra = []
        for key in sorted(self.valid):
            if key in used:
                continue
            owners = self._owners(key)
            if not owners:
                continue
            if len(used) >= self.limits.max_records:
                self.omit("evidence_records", [use.requirement_id for use in owners], keys=[key])
            else:
                used.add(key)
                extra.append(key)
        suppressed_context = set()
        while True:
            package = self.assemble(selected, used, suppressed_context)
            if len(package.model_dump_json().encode()) <= self.limits.max_package_bytes:
                return EvidencePackage.model_validate(package.model_dump(), strict=True)
            contextual = next((record for record in reversed(list(package.evidence.values())) if record.source_context is not None and record.proof.evidence_id not in suppressed_context), None)
            if contextual:
                key = contextual.proof.evidence_id
                suppressed_context.add(key)
                self.omit("serialized_size_context", [use.requirement_id for use in contextual.uses], keys=[key])
            elif extra:
                key = extra.pop()
                used.remove(key)
                self.omit("serialized_size", [use.requirement_id for use in self._owners(key)], keys=[key])
            elif selected:
                claim = selected.pop()
                self.omit("serialized_size", [claim.requirement_id], claim.operation_id, claim.finding_id, claim.original.evidence_ids)
                used = {key for item in selected for key in item.original.evidence_ids}
            else:
                raise EvidenceBudgetError("Required coverage/conflict envelope exceeds max_package_bytes; increase the explicit budget")

    def _owners(self, key):
        uses = []
        for outcome in self.report.operations:
            if outcome.result and key in outcome.result.evidence_ids:
                uses.append(EvidenceUse(operation_id=outcome.operation_id, requirement_id=outcome.requirement_id,
                    engine=outcome.result.engine, finding_id=None, role="context"))
        for clarification in self.report.clarifications:
            if any(candidate.evidence.evidence_id == key for candidate in clarification.candidates):
                uses.append(EvidenceUse(operation_id=None, requirement_id=clarification.requirement_id, engine=None, finding_id=None, role="clarification"))
        return uses

    def assemble(self, selected, used, suppressed_context=(), fatal=None):
        # Context allocation is recomputed after each serialized-size eviction;
        # don't publish provisional omissions for context that now fits.
        self.omissions = [event for event in self.omissions if event.reason != "source_context"]
        records, citations = {}, {}
        remaining_context = self.limits.max_context_bytes
        for key in sorted(used):
            proof, info = self.valid[key], self.infos[key]
            rid = stable_id(proof.model_dump(mode="json"))
            uses = self._owners(key)
            for claim in selected:
                if key in claim.original.evidence_ids:
                    uses.append(EvidenceUse(operation_id=claim.operation_id, requirement_id=claim.requirement_id,
                        engine=claim.engine, finding_id=claim.finding_id, role="claim"))
            text, context_status = None, "structural" if not proof.file_path else "not_requested"
            if proof.file_path and self.limits.include_source_context:
                excerpt = self._excerpt(proof)
                if key not in suppressed_context and len(excerpt.encode()) <= remaining_context:
                    text, context_status = excerpt, "included"
                    remaining_context -= len(excerpt.encode())
                else:
                    context_status = "omitted_budget"
                    self.omit("source_context", [use.requirement_id for use in uses], keys=[key])
            provenance = {field: info[field] for field in ("kind", "relationship", "source_id", "target_id", "resolution", "evidence_type") if field in info}
            records[rid] = EvidenceRecord(record_id=rid, proof=proof, provenance=provenance,
                verification="source_verified" if proof.file_path else "structural_metadata_verified", source_context=text,
                context_status=context_status, uses=uses)
            if proof.file_path:
                reference = f"cm7:{self.package_id}:{rid}"
                citations[reference] = Citation(reference=reference, record_id=rid, source=proof)
        findings = [claim.model_copy(update={"citation_ids": [reference for reference, citation in citations.items() if citation.record_id in claim.evidence_record_ids]}) for claim in selected]
        clarifications = []
        for clarification in self.report.clarifications:
            candidates = [candidate for candidate in clarification.candidates if candidate.evidence.evidence_id in used]
            clarifications.append(clarification.model_copy(update={"candidates": candidates, "truncated": clarification.truncated or len(candidates) != len(clarification.candidates)}))
        operations, requirements = [], []
        for operation, outcome in zip(self.plan.operations, self.report.operations):
            owned = [claim for claim in findings if claim.operation_id == operation.operation_id]
            missing = [key for key in (outcome.result.evidence_ids if outcome.result else []) if key not in used]
            missing.extend(event.finding_id or event.reason for event in self.omissions if event.operation_id == operation.operation_id)
            if not outcome.result and outcome.status not in ("success", "not_found"):
                missing.append("execution:" + outcome.status)
            state = STATES[outcome.status]
            conflict = any(operation.operation_id in item.operation_ids for item in self.conflicts)
            if fatal or conflict:
                state = "partially_satisfied" if owned else "stale"
            elif missing and state == "satisfied":
                state = "partially_satisfied"
            operations.append(OperationCoverage(operation_id=operation.operation_id, requirement_id=operation.requirement_id,
                request=operation.request, execution_status=outcome.status, state=state, finding_ids=[item.finding_id for item in owned],
                unresolved_items=outcome.result.unresolved_items if outcome.result else [],
                evidence_record_ids=[key for key, record in records.items() if any(use.operation_id == operation.operation_id for use in record.uses)],
                missing_evidence=list(dict.fromkeys(missing)), limitations=list(dict.fromkeys([*(outcome.result.warnings if outcome.result else []), *([outcome.reason] if outcome.reason else []), *([outcome.error.message] if outcome.error else [])]))))
        for decision, outcome in zip(self.plan.requirements, self.report.requirements):
            owned = [claim for claim in findings if claim.requirement_id == decision.requirement_id]
            children = [item for item in operations if item.requirement_id == decision.requirement_id]
            omissions = [event for event in self.omissions if decision.requirement_id in event.requirement_ids]
            conflict = any(decision.requirement_id in item.requirement_ids for item in self.conflicts)
            state = STATES[outcome.status]
            if fatal or conflict:
                state = "partially_satisfied" if owned else "stale"
            elif state == "satisfied" and (omissions or any(item.state != "satisfied" for item in children)):
                state = "partially_satisfied"
            missing = [key for child in children for key in child.missing_evidence]
            missing.extend(key for event in omissions for key in (event.evidence_ids or [event.reason]))
            missing.extend(key for item in self.conflicts if decision.requirement_id in item.requirement_ids for key in item.evidence_ids)
            requirements.append(RequirementCoverage(requirement_id=decision.requirement_id, original=decision.requirement,
                targets=[RequirementTarget(entity_index=index, reference=next((target.entity for operation in self.plan.operations if operation.requirement_id == decision.requirement_id for target in operation.targets if target.entity_index == index), self.plan.normalized.entities[index]).model_copy(update={"evidence": None, "candidates": []})) for index in decision.requirement.entity_indices],
                execution_status=outcome.status, state=state, operation_ids=decision.operation_ids,
                finding_ids=[item.finding_id for item in owned], evidence_record_ids=[key for key, record in records.items() if any(use.requirement_id == decision.requirement_id for use in record.uses)],
                missing_evidence=list(dict.fromkeys(missing)), limitations=list(dict.fromkeys([*decision.requirement.limitations, *([outcome.reason] if outcome.reason else []), *(text for child in children for text in child.limitations)])),
                clarification_required=any(item.requirement_id == decision.requirement_id for item in clarifications), truncated=bool(omissions)))
        states = [item.state for item in requirements]
        status = ("stale_index" if fatal else "success" if states and all(value == "satisfied" for value in states) else
                  "partial" if findings else "ambiguous" if "ambiguous" in states else "stale_index" if "stale" in states else
                  "error" if "error" in states else "not_found" if states and all(value == "not_found" for value in states) else
                  "unsupported" if states and all(value in ("unsupported", "deferred") for value in states) else "partial")
        # Construct only for internal sizing. Public output is always revalidated.
        return EvidencePackage.model_construct(package_id=self.package_id, query_id=self.plan.query_id, execution_id=self.execution_id,
            repository_id=self.owner.context.repository_id, snapshot_id=self.owner.context.snapshot_id,
            question=self.plan.normalized.original_question, limits=self.limits, evidence=records, findings=findings,
            requirements=requirements, operations=operations, requirement_evidence={item.requirement_id: item.evidence_record_ids for item in requirements},
            citations=citations, conflicts=self.conflicts, missing_evidence=list(dict.fromkeys(key for item in requirements for key in item.missing_evidence)),
            clarifications=clarifications, omissions=self.omissions, resource_limits=list(dict.fromkeys([*self.report.resource_limits, *(event.reason for event in self.omissions)])),
            status=status, validation_status=fatal or ("partial" if self.conflicts or self.omissions else "valid"), limitations=BASE_LIMITATIONS,
            execution_warnings=list(self.report.warnings))

    def fatal(self, status, code, message):
        self.conflicts = [EvidenceConflict(code=code, message=message,
            operation_ids=[item.operation_id for item in self.plan.operations], requirement_ids=[item.requirement_id for item in self.plan.requirements])]
        self.valid, self.infos = {}, {}
        package = self.assemble([], set(), fatal=status)
        if len(package.model_dump_json().encode()) > self.limits.max_package_bytes:
            raise EvidenceBudgetError("Required failure/coverage envelope exceeds max_package_bytes")
        return EvidencePackage.model_validate(package.model_dump(), strict=True)
