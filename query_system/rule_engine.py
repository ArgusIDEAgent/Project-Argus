"""Deterministic rules composed from the existing pinned graph and metadata.

No parser, traversal, semantic discovery, embeddings, or answer model is created.
The private Phase 2 read/normalization helpers are a tested compatibility boundary.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from time import monotonic

from knowledge_base.metadata import trace
from knowledge_base.parser import FUNCTION_KINDS, LANGUAGES
from query_system.contracts import (
    ContractError, EngineError, EngineResult, Evidence, OperationRequest, RuleFinding,
    SnapshotContext, StaleIndexError, validate_engine_result,
)
from query_system.graph_engine import GRAPH_OPERATIONS, GraphQueryEngine, _BudgetExceeded, _Run
from query_system.rule_registry import RULES


class _Outcome(Exception):
    def __init__(self, status, message, code=None):
        self.status, self.message, self.code = status, message, code


class RuleBasedAnalysisEngine:
    def __init__(self, store, view, context):
        self.store, self.context = store, context
        self.graph_engine = GraphQueryEngine(store, view, context)
        self.database = self.graph_engine.database
        self.evidence: dict[str, Evidence] = {}
        self._active = False

    @classmethod
    @contextmanager
    def pinned(cls, store):
        with store.pin_view() as view:
            context = SnapshotContext.from_pinned_view(view, store.repo_id)
            engine = cls(store, view, context)
            engine._active = engine.graph_engine._active = True
            try:
                yield engine
            finally:
                engine._active = engine.graph_engine._active = False

    async def execute(self, operation, context):
        started = monotonic()
        def failure(status, message, code=None):
            return EngineResult(engine="rules", status=status, repository_id=context.repository_id,
                snapshot_id=context.snapshot_id, execution_ms=int((monotonic() - started) * 1000),
                warnings=[message], error=EngineError(code=code, message=message) if code else None)
        if not self._active or context != self.context or operation.snapshot_id != context.snapshot_id:
            return failure("stale_index", "Analysis is outside its pinned generation", "stale_index")
        if operation.repository_id != context.repository_id:
            return failure("error", "Requested repository differs from the pinned repository", "invalid_operation")
        rule = RULES.get(operation.name)
        if rule is None or operation.engine != "rules":
            return failure("unsupported", "Rule is not registered", "invalid_operation")
        try:
            parameters = rule.operation.validate(operation, context)
        except ValueError as error:
            return failure("error", str(error), "invalid_operation")
        run = _Analysis(self, rule, parameters, started + rule.max_execution_ms / 1000)
        try:
            if not self.store.matches(context.snapshot_id):
                raise StaleIndexError("Saved sources differ from the pinned generation")
            await run.execute()
            # Recheck every emitted proof, including its canonical mapping/range.
            evidence = {key: run.helper.evidence[key] for finding in run.findings for key in finding.evidence_ids}
            for identifier, previous in evidence.items():
                run.helper.check()
                current = Evidence.from_trace(trace(self.database, context.repository_id, identifier), context,
                    evidence_type=previous.evidence_type, resolution=previous.resolution)
                if current != previous:
                    raise StaleIndexError("Canonical metadata changed during analysis")
            if not self.store.matches(context.snapshot_id):
                raise StaleIndexError("Saved sources changed during analysis")
            run.helper.check()
            result = EngineResult(engine="rules", status=run.status, repository_id=context.repository_id,
                snapshot_id=context.snapshot_id, findings=run.findings, evidence_ids=list(evidence),
                warnings=list(dict.fromkeys(run.warnings)), unresolved_items=list(dict.fromkeys(run.unresolved)),
                execution_ms=int((monotonic() - started) * 1000))
            validate_engine_result(result, context, evidence, rule.operation)
            self.evidence.update(evidence)
            return result
        except _Outcome as outcome:
            return failure(outcome.status, outcome.message, outcome.code)
        except _BudgetExceeded:
            return failure("partial", "Analysis execution budget exceeded; no unchecked findings returned", "budget_exceeded")
        except (ContractError, ValueError, KeyError, TypeError, OSError):
            return failure("stale_index", "Source or metadata evidence is stale, invalid, or mismatched", "stale_index")
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                return failure("partial", "Analysis SQL budget exceeded", "budget_exceeded")
            return failure("error", "Required metadata capability is unavailable", "unavailable")
        except sqlite3.Error:
            return failure("error", "Required metadata capability is unavailable", "unavailable")


class _Analysis:
    def __init__(self, engine, rule, parameters, deadline):
        self.engine, self.rule, self.p = engine, rule, parameters
        self.helper = _Run(engine.graph_engine, engine.context, deadline)
        self.findings, self.unresolved = [], []
        self.warnings = list(rule.limitations)
        self.status = "success"
        self.keys = set()
        self.source_bytes = 0
        self.graph_allowed = None

    def partial(self, message):
        if self.status != "ambiguous":
            self.status = "partial"
        self.warnings.append(message)

    def emit(self, category, statement, details, evidence_ids, classification="verified_observation", limitations=()):
        self.helper.check()
        key = (category, classification, json.dumps(details, sort_keys=True))
        if key in self.keys:
            return
        if len(self.findings) >= min(self.p.limit, self.rule.result_limit):
            self.partial("Analysis reached its finding limit; omitted findings are not negative evidence")
            return
        self.keys.add(key)
        self.findings.append(RuleFinding(rule_id=self.rule.rule_id, category=category, statement=statement,
            classification=classification, details=details, limitations=list(limitations),
            evidence_ids=list(dict.fromkeys(evidence_ids))))

    def read(self, sql, args=()):
        return self.helper.read(sql, args)

    def scoped(self, path):
        if path is not None and Path(path).suffix not in LANGUAGES:
            raise _Outcome("unsupported", "Rule graph/signature capabilities are only indexed for JS/TS")

    def merge(self, evidence):
        for identifier, previous in evidence.items():
            self.helper.check()
            actual = Evidence.from_trace(trace(self.engine.database, self.engine.context.repository_id, identifier),
                self.engine.context, evidence_type=previous.evidence_type, resolution=previous.resolution)
            if actual != previous:
                raise StaleIndexError("Graph supplied mismatched canonical evidence")
            self.helper.evidence[identifier] = actual

    async def graph(self, name, **parameters):
        self.helper.check()
        operation = OperationRequest(engine="graph", name=name,
            repository_id=self.engine.context.repository_id, snapshot_id=self.engine.context.snapshot_id,
            parameters=parameters)
        result = await self.engine.graph_engine.execute(operation, self.engine.context)
        self.helper.check()
        validate_engine_result(result, self.engine.context, self.engine.graph_engine.evidence, GRAPH_OPERATIONS[name])
        if result.status in ("stale_index", "error", "unsupported") or result.error:
            raise _Outcome(result.status, result.error.message if result.error else "Graph capability unavailable",
                           result.error.code if result.error else None)
        self.merge({identifier: self.engine.graph_engine.evidence[identifier] for identifier in result.evidence_ids})
        self.warnings.extend(result.warnings)
        self.unresolved.extend(result.unresolved_items)
        if result.status == "partial":
            self.partial("An existing graph operation returned a bounded or incomplete result")
        return result

    async def resolve(self):
        self.scoped(self.p.module_path or self.p.file_path)
        if self.p.module_path is not None:
            node, candidates, status = self.helper.module(self.p.module_path, self.p.limit)
            if status == "not_found":
                raise _Outcome("not_found", "No indexed module matches this path")
            if status == "ambiguous":
                self.status = "ambiguous"
                for finding in candidates:
                    self.emit("candidate", "Indexed module candidate; select the full path",
                              finding.model_dump(mode="json", exclude={"kind", "evidence_ids"}), finding.evidence_ids)
                return None
            return node
        values = {key: getattr(self.p, key) for key in ("reference", "selected_symbol_id", "file_path")
                  if getattr(self.p, key) is not None}
        result = await self.graph("find_definition", limit=self.p.limit, **values)
        if result.status == "not_found":
            raise _Outcome("not_found", "No indexed symbol matches the supplied identity and scope")
        if result.status == "ambiguous":
            self.status = "ambiguous"
            for finding in result.findings:
                self.emit("candidate", "Indexed symbol candidate; choose a canonical ID or file scope",
                          finding.model_dump(mode="json", exclude={"kind", "evidence_ids"}), finding.evidence_ids)
            return None
        return self.helper.node(result.findings[0].target_node_id)

    def identity(self, node):
        identifier = node["symbol_id"] or node["id"]
        self.helper.add_evidence(identifier, "symbol_definition" if node["symbol_id"] else "source_range", "syntax_only")
        return identifier

    def symbol(self, identifier):
        rows = self.read("SELECT * FROM symbols WHERE repository_id=? AND id=? LIMIT 1", (identifier,))
        if not rows:
            raise StaleIndexError("Canonical symbol metadata is missing")
        row = rows[0]
        self.helper.add_evidence(identifier, "symbol_definition", "syntax_only")
        details = {key: row[key] for key in ("id", "kind", "name", "qualified_name", "relative_path",
                                            "start_line", "end_line")}
        raw = [row["parameters_json"], row["modifiers_json"], row["return_annotation"] or ""]
        size = sum(len(value.encode()) for value in raw)
        if size + self.source_bytes > self.p.max_source_bytes:
            self.partial("Declared source context reached its byte budget; signature details omitted")
            details["signature_status"] = "omitted_by_budget"
            return details
        self.source_bytes += size
        parameters, modifiers = json.loads(raw[0]), json.loads(raw[1])
        if not isinstance(parameters, list) or not isinstance(modifiers, list):
            raise StaleIndexError("Invalid declared signature metadata")
        details.update(parameters=parameters, return_annotation=row["return_annotation"],
                       declared_async="async" in modifiers, modifiers=modifiers,
                       exported=bool(row["exported"]), default_export=bool(row["default_export"]),
                       signature_status="indexed_declaration")
        return details

    def can_use_graph(self):
        if self.graph_allowed is None:
            counts = self.read("""SELECT (SELECT count(*) FROM graph_nodes WHERE repository_id=?) AS nodes,
                (SELECT count(*) FROM relationships WHERE repository_id=?) AS edges""",
                (self.engine.context.repository_id,))[0]
            # Existing methods can materialize adjacency/edge lists. Refuse those
            # calls when the pinned repository footprint exceeds the caller cap.
            self.graph_allowed = counts["nodes"] <= self.p.max_nodes and counts["edges"] <= self.p.max_edges
            if not self.graph_allowed:
                self.partial("Graph footprint exceeds max_nodes/max_edges; relationship/path/test analysis omitted")
        return self.graph_allowed

    def relation(self, finding, category):
        proofs = [self.helper.evidence[key] for key in finding.evidence_ids]
        if any(proof.resolution != "resolved" for proof in proofs if proof.evidence_type == "graph_relationship"):
            raise StaleIndexError("Unverified relationship cannot be a verified observation")
        details = finding.model_dump(mode="json", exclude={"kind", "evidence_ids"})
        if finding.relationship == "EXPORTS":
            info = trace(self.engine.database, self.engine.context.repository_id, finding.evidence_ids[0])
            details["exported_name"] = json.loads(info["metadata_json"]).get("exported_name")
        # Existing endpoint normalization includes canonical source proofs.
        for side in ("source", "target"):
            node = self.helper.node(getattr(finding, side + "_node_id"))
            details[side + "_path"] = node["relative_path"]
        self.emit(category, f"Indexed static {finding.relationship} relationship: {finding.source_name} → {finding.target_name}",
                  details, finding.evidence_ids,
                  "derived_finding" if finding.path_node_ids else "verified_observation")

    async def relations(self, node, name, category):
        if not self.can_use_graph():
            return []
        parameters = {"selected_symbol_id": node["symbol_id"]} if node["symbol_id"] else {"module_path": node["relative_path"]}
        result = await self.graph(name, limit=self.p.limit, **parameters)
        for finding in result.findings:
            self.relation(finding, category)
        return result.findings

    def static_edges(self, node, relationships, category, reverse=False):
        """Reuse existing edge lookup for types with no Phase 2 operation name."""
        if not self.can_use_graph():
            return []
        findings = []
        for relationship in relationships:
            rows = self.helper.graph._edges(node["id"], relationship, reverse=reverse)
            if len(rows) > self.p.limit:
                self.partial("Static relationship results reached their limit")
            for row in rows[:self.p.limit]:
                finding = self.helper.relationship(row)
                self.relation(finding, category)
                findings.append(finding)
        return findings

    def unknowns(self, node):
        if not self.can_use_graph():
            return
        kwargs = {"symbol_id": node["symbol_id"]} if node["symbol_id"] else {"module_path": node["relative_path"]}
        rows = self.helper.graph.unresolved_references(**kwargs)
        if len(rows) > self.p.limit:
            self.partial("Unresolved reference list reached its limit")
        for row in rows[:self.p.limit]:
            self.helper.add_evidence(row["id"], "graph_relationship", "unresolved")
            self.unresolved.append(f"{row['expression']}: {row['reason']}")
            self.partial("Unresolved outgoing relationships remain unsupported; no target is fabricated")
            self.emit("unresolved_relationship", "Indexed relationship has no uniquely resolved target",
                      {key: row[key] for key in ("relationship", "expression", "reason")}, [row["id"]])

    def tests(self, node, report_empty=True):
        if not self.can_use_graph():
            return []
        if node["symbol_id"]:
            symbols = [node["symbol_id"]]
        else:
            rows = self.read("SELECT id FROM symbols WHERE repository_id=? AND relative_path=? ORDER BY start_line,id LIMIT ?",
                             (node["relative_path"], self.p.max_nodes + 1))
            symbols = [row["id"] for row in rows[:self.p.max_nodes]]
            if len(rows) > self.p.max_nodes:
                self.partial("Module test discovery reached its symbol budget")
        found = []
        for symbol in symbols:
            self.helper.check()
            result = self.helper.graph.test_references(symbol, limit=self.p.limit)
            if result["truncated"]:
                self.partial("Indexed test references reached their limit")
            for row in result["references"]:
                finding = self.helper.relationship(row)
                self.relation(finding, "test_reference")
                found.append(finding)
        if not found and report_empty:
            self.partial("No indexed static test references found; this does not prove absence of tests")
            self.emit("test_search", "No known static test references in the indexed naming convention",
                      {"matched": 0, "coverage": "not_measured"}, [self.identity(node)])
        return found

    def exports(self, module):
        if not self.can_use_graph():
            return
        rows = self.helper.graph._edges(module["id"], "EXPORTS")
        if len(rows) > self.p.limit:
            self.partial("Available export interfaces reached their limit")
        for row in rows[:self.p.limit]:
            finding = self.helper.relationship(row)
            self.relation(finding, "public_interface")
        self.unknowns(module)

    async def paths(self, source, target, category, target_file=None):
        if source["symbol_id"]:
            result = await self.graph("find_paths", source=source["symbol_id"], target=target,
                target_file=target_file, max_depth=self.p.max_depth, limit=self.p.limit,
                max_work=min(self.p.max_edges, 50000))
        else:
            result = await self.graph("find_dependency_paths", source=source["relative_path"], target=target,
                                     max_depth=self.p.max_depth, limit=self.p.limit)
        if result.status == "ambiguous":
            self.status = "ambiguous"
            for finding in result.findings:
                self.emit("candidate", "Dependency path target is ambiguous",
                          finding.model_dump(mode="json", exclude={"kind", "evidence_ids"}), finding.evidence_ids)
            return []
        if result.status == "not_found" and category == "dependency_path":
            self.partial("Requested dependency path or target was not found within the specified bound")
        for finding in result.findings:
            self.relation(finding, category)
        return result.findings

    async def impact(self, node):
        self.emit("impact_scope", "Preliminary static impact analysis; consequences are not proven",
                  {"target": node["symbol_id"] or node["id"], "max_depth": self.p.max_depth}, [self.identity(node)])
        direct = await self.relations(node, "find_callers" if node["symbol_id"] else "find_reverse_dependencies", "direct_incoming")
        if node["symbol_id"]:
            direct += self.static_edges(node, ("REFERENCES", "EXTENDS", "IMPLEMENTS"), "direct_incoming", reverse=True)
        for finding in direct:
            self.impact_location(finding)
        # Module dependents remain relevant when the selected target is a symbol.
        if node["symbol_id"]:
            module, _, status = self.helper.module(node["relative_path"], self.p.limit)
            if status:
                raise StaleIndexError("Symbol's canonical module is missing")
            for dependent in await self.relations(module, "find_reverse_dependencies", "module_dependent"):
                self.impact_location(dependent)
            self.emit("exposed_contract", "Indexed declaration is a possible contract, not a compatibility guarantee",
                      self.symbol(node["symbol_id"]), [self.identity(node)])
            self.exports(module)
            await self.explore_incoming(module)
        else:
            self.definitions(node)
            self.exports(node)
        await self.explore_incoming(node)
        for finding in self.tests(node):
            self.impact_location(finding)
        self.unknowns(node)

    async def explore_incoming(self, node):
        if self.can_use_graph() and len(self.findings) < self.p.limit:
            kind = "symbol_id IS NOT NULL" if node["symbol_id"] else "kind='Module'"
            rows = self.read(f"SELECT * FROM graph_nodes WHERE repository_id=? AND {kind} ORDER BY relative_path,label,id LIMIT ?",
                             (self.p.max_nodes + 1,))
            for source in rows[:self.p.max_nodes]:
                if source["id"] == node["id"]:
                    continue
                findings = await self.paths(source, node["symbol_id"] or node["relative_path"], "incoming_path")
                for finding in findings:
                    self.impact_location(finding)
                if findings:
                    for test in self.tests(source, report_empty=False):
                        self.emit("possible_test_impact", "Indexed test reference reaches an incoming static path; review may be needed",
                            {"test_path": self.helper.node(test.source_node_id)["relative_path"],
                             "via_node": source["id"], "target": node["id"]},
                            test.evidence_ids + [identifier for path in findings for identifier in path.evidence_ids], "hypothesis")
                if len(self.findings) >= self.p.limit:
                    self.partial("Impact discovery stopped at the finding limit")
                    break
            self.warnings.append(f"Incoming exploration is bounded to depth {self.p.max_depth}; deeper impact is not excluded")

    def impact_location(self, finding):
        path = self.helper.node(finding.source_node_id)["relative_path"]
        self.emit("possible_impact", "This indexed location may require review after a change; breakage is not established",
                  {"path": path, "node_id": finding.source_node_id, "relationship": finding.relationship,
                   "through_path": finding.path_node_ids}, finding.evidence_ids, "hypothesis",
                  ("Static connectivity does not establish runtime execution, coverage, or compatibility breakage.",))

    def definitions(self, node):
        rows = self.read("SELECT id FROM symbols WHERE repository_id=? AND relative_path=? ORDER BY start_line,id LIMIT ?",
                         (node["relative_path"], self.p.limit + 1))
        if len(rows) > self.p.limit:
            self.partial("Indexed definitions reached the finding limit")
        for row in rows[:self.p.limit]:
            self.emit("definition", "Indexed declaration in this module", self.symbol(row["id"]), [row["id"]])

    async def execute(self):
        node = await self.resolve()
        if node is None:
            return
        identifier = self.identity(node)
        name = self.rule.rule_id
        if name == "function_contract":
            details = self.symbol(node["symbol_id"])
            if details["kind"] not in FUNCTION_KINDS:
                raise _Outcome("unsupported", "Resolved symbol is not an indexed function declaration")
            details.update(return_behavior="not_inferred", exception_behavior="not_indexed")
            self.emit("function_contract", "Declared function signature; no complete return/exception behavior claim",
                      details, [identifier], limitations=("Only indexed declarations are extracted.",))
            self.partial("Complete return-value and exception analysis is unavailable in the current metadata")
        elif name == "dependency_summary":
            self.emit("dependency_scope", "Static dependency summary for the resolved target",
                      {"target": identifier, "max_depth": self.p.max_depth}, [identifier])
            operations = (("find_callees", "direct_dependency"), ("find_callers", "reverse_dependency")) if node["symbol_id"] else (
                ("find_dependencies", "direct_dependency"), ("find_reverse_dependencies", "reverse_dependency"))
            for operation, category in operations:
                await self.relations(node, operation, category)
            if node["symbol_id"]:
                self.static_edges(node, ("REFERENCES", "EXTENDS", "IMPLEMENTS"), "direct_dependency")
                self.static_edges(node, ("REFERENCES", "EXTENDS", "IMPLEMENTS"), "reverse_dependency", reverse=True)
            if self.p.path_target and self.can_use_graph():
                try:
                    self.scoped(self.p.path_target if not node["symbol_id"] else self.p.path_target_file)
                except _Outcome as unsupported:
                    self.partial(unsupported.message + "; requested path omitted")
                else:
                    await self.paths(node, self.p.path_target, "dependency_path", self.p.path_target_file)
            self.unknowns(node)
        elif name == "reference_test_analysis":
            self.emit("reference_scope", "Known indexed references; completeness and test coverage are not asserted",
                      {"symbol_id": identifier}, [identifier])
            await self.relations(node, "find_references", "known_reference")
            callers = await self.relations(node, "find_callers", "resolved_caller")
            if not callers and self.can_use_graph():
                self.emit("caller_search", "No resolved direct callers returned in this indexed scope",
                          {"matched": 0}, [identifier])
            self.tests(node)
            self.unknowns(node)
        elif name == "change_impact":
            await self.impact(node)
        else:
            self.emit("module_scope", "Overview of the indexed module", {"path": node["relative_path"]}, [identifier])
            self.definitions(node)
            for operation, category in (("find_imports", "direct_import"), ("find_dependencies", "direct_dependency"),
                                        ("find_reverse_dependencies", "known_dependent")):
                await self.relations(node, operation, category)
            self.exports(node)
            self.tests(node)
