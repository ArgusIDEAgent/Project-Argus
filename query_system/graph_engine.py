"""Deterministic graph operations over one existing, pinned knowledge generation."""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from time import monotonic

from pydantic import Field, ValidationError, model_validator

from knowledge_base.graph import KnowledgeGraph
from knowledge_base.identity import relative_path
from knowledge_base.metadata import trace
from query_system.contracts import (
    ContractError, ContractModel, EngineError, EngineResult, Evidence, GraphFinding,
    OperationRequest, OperationSpec, SnapshotContext, StaleIndexError,
    validate_engine_result,
)


class SymbolParameters(ContractModel):
    reference: str | None = Field(default=None, min_length=1)
    file_path: str | None = None
    selected_symbol_id: str | None = Field(default=None, min_length=1)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_target(self):
        if not self.reference and not self.selected_symbol_id:
            raise ValueError("A symbol reference or selected symbol ID is required")
        if self.file_path is not None:
            relative_path(self.file_path)
        return self


class ModuleParameters(ContractModel):
    module_path: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_path(self):
        relative_path(self.module_path)
        return self


class SymbolPathParameters(ContractModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    source_file: str | None = None
    target_file: str | None = None
    max_depth: int = Field(default=3, ge=1, le=10)
    limit: int = Field(default=20, ge=1, le=100)
    max_work: int = Field(default=10000, ge=1, le=50000)

    @model_validator(mode="after")
    def validate_paths(self):
        for path in (self.source_file, self.target_file):
            if path is not None:
                relative_path(path)
        return self


class ModulePathParameters(ContractModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    max_depth: int = Field(default=3, ge=1, le=10)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_paths(self):
        relative_path(self.source)
        relative_path(self.target)
        return self


GRAPH_OPERATIONS = {
    name: OperationSpec("graph", name, model,
                        frozenset({"symbol_definition", "graph_relationship", "source_range"}),
                        5000, "partial")
    for name, model in (
        ("find_definition", SymbolParameters), ("find_callers", SymbolParameters),
        ("find_callees", SymbolParameters), ("find_references", SymbolParameters),
        ("find_imports", ModuleParameters), ("find_importers", ModuleParameters),
        ("find_dependencies", ModuleParameters), ("find_reverse_dependencies", ModuleParameters),
        ("find_paths", SymbolPathParameters), ("find_dependency_paths", ModulePathParameters),
    )
}


class _BudgetExceeded(RuntimeError):
    pass


class _BudgetGraph(KnowledgeGraph):
    """Keep graph algorithms intact; guard their connections and SQL work."""

    def __init__(self, database, repository_id, check):
        super().__init__(database, repository_id)
        self.check = check

    def _connection(self):
        self.check()
        # Override only connection policy. The existing query/traversal methods
        # are reused unchanged; mode=ro cannot recreate a removed index.
        db = sqlite3.connect(Path(self.database).resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            self.check()
            db.execute("PRAGMA query_only=ON")
            def progress():
                try:
                    self.check()
                    return 0
                except _BudgetExceeded:
                    return 1
            db.set_progress_handler(progress, 1000)
            current = db.execute("""SELECT r.last_scanned_at,s.inventory_scanned_at
                FROM repositories r LEFT JOIN graph_snapshots s ON s.repository_id=r.id
                WHERE r.id=?""", (self.repository_id,)).fetchone()
            if not current or current["last_scanned_at"] != current["inventory_scanned_at"]:
                raise StaleIndexError("Graph inventory snapshot is missing or stale")
            graph_stage = db.execute("""SELECT s.state FROM graph_nodes n
                JOIN graph_node_sources p ON p.node_id=n.id AND p.repository_id=n.repository_id
                JOIN index_snapshots s ON s.id=p.snapshot_id AND s.repository_id=p.repository_id
                WHERE n.repository_id=? AND n.kind='Repository' LIMIT 1""",
                (self.repository_id,)).fetchone()
            if not graph_stage or graph_stage["state"] != "ready":
                raise StaleIndexError("Canonical graph stage is not ready")
            return db
        except Exception:
            db.close()
            raise


class GraphQueryEngine:
    """Use inside ``with GraphQueryEngine.pinned(store) as engine``.

    The evidence registry belongs to this pinned engine. execute() returns the
    Phase 1 EngineResult and never selects another active snapshot mid-query.
    """

    def __init__(self, store, view, context):
        self.store = store
        self.context = context
        self.database = Path(view["data_dir"]) / context.repository_id / "metadata.db"
        self.evidence: dict[str, Evidence] = {}
        self._active = False

    @classmethod
    @contextmanager
    def pinned(cls, store):
        with store.pin_view() as view:
            engine = cls(store, view, SnapshotContext.from_pinned_view(view, store.repo_id))
            engine._active = True
            try:
                yield engine
            finally:
                engine._active = False

    async def execute(self, operation: OperationRequest, context: SnapshotContext) -> EngineResult:
        started = monotonic()
        def failure(status, code, message):
            return EngineResult(engine="graph", status=status, repository_id=context.repository_id,
                                snapshot_id=context.snapshot_id, execution_ms=0,
                                error=EngineError(code=code, message=message))
        if not self._active or context != self.context or operation.snapshot_id != context.snapshot_id:
            return failure("stale_index", "stale_index", "Operation is outside its pinned generation")
        if operation.repository_id != context.repository_id:
            return failure("error", "invalid_operation", "Operation belongs to another repository")
        spec = GRAPH_OPERATIONS.get(operation.name)
        if spec is None or operation.engine != "graph":
            return failure("unsupported", "invalid_operation", "This graph operation is unsupported")
        try:
            parameters = spec.validate(operation, context)
        except (ContractError, ValidationError) as error:
            return failure("error", "invalid_operation", str(error))
        run = _Run(self, context, started + spec.max_execution_ms / 1000)
        try:
            if not self.store.matches(context.snapshot_id):
                raise StaleIndexError("Pinned saved-source evidence changed")
            findings, status = run.execute(operation.name, parameters)
            run.check()
            # Source edits during resolution/traversal invalidate the whole result.
            for identifier in run.evidence:
                run.check()
                trace(self.database, context.repository_id, identifier)
            if not self.store.matches(context.snapshot_id):
                raise StaleIndexError("Saved sources changed during graph retrieval")
            result = EngineResult(
                engine="graph", status=status, repository_id=context.repository_id,
                snapshot_id=context.snapshot_id, findings=findings,
                evidence_ids=list(run.evidence), warnings=run.warnings,
                unresolved_items=run.unresolved, execution_ms=int((monotonic() - started) * 1000),
            )
            validate_engine_result(result, context, run.evidence, spec)
            self.evidence.update(run.evidence)
            return result
        except _BudgetExceeded:
            return failure("partial", "budget_exceeded", "Graph execution budget exceeded; retry a smaller scope")
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                return failure("partial", "budget_exceeded", "Graph SQL execution budget exceeded")
            return failure("error", "unavailable", "Graph metadata is unavailable")
        except (StaleIndexError, ValueError, OSError):
            return failure("stale_index", "stale_index", "Source or index provenance is stale or invalid")
        except RuntimeError as error:
            if "budget exceeded" in str(error):
                return failure("partial", "budget_exceeded", str(error))
            return failure("stale_index", "stale_index", "Graph is missing or stale")
        except sqlite3.Error:
            return failure("error", "unavailable", "Graph metadata is unavailable")


class _Run:
    def __init__(self, engine, context, deadline):
        self.engine, self.context, self.deadline = engine, context, deadline
        self.evidence, self.warnings, self.unresolved = {}, [], []
        self.graph = _BudgetGraph(engine.database, context.repository_id, self.check)

    def check(self):
        if monotonic() >= self.deadline:
            raise _BudgetExceeded()

    def read(self, sql, args=()):
        self.check()
        with closing(self.graph._connection()) as db:
            return [dict(row) for row in db.execute(sql, (self.context.repository_id, *args))]

    def add_evidence(self, identifier, evidence_type, resolution):
        self.check()
        if identifier in self.evidence:
            return identifier
        info = trace(self.engine.database, self.context.repository_id, identifier)
        evidence = Evidence.from_trace(info, self.context, evidence_type=evidence_type, resolution=resolution)
        self.evidence[identifier] = evidence
        return identifier

    def definition(self, node):
        identifier = node["symbol_id"] or node["id"]
        evidence_type = "symbol_definition" if node["symbol_id"] else "source_range"
        identifier = self.add_evidence(identifier, evidence_type, "syntax_only")
        return GraphFinding(relationship="DEFINITION", entity_id=identifier,
                            entity_name=node["label"], target_node_id=node["id"],
                            target_symbol_id=node["symbol_id"], evidence_ids=[identifier])

    def symbol(self, parameters):
        reference = parameters.reference or parameters.selected_symbol_id
        clauses, values = [], [reference, reference, reference]
        if parameters.file_path is not None:
            clauses.append("s.relative_path=?")
            values.append(parameters.file_path)
        if parameters.selected_symbol_id is not None:
            clauses.append("s.id=?")
            values.append(parameters.selected_symbol_id)
        extra = "".join(" AND " + clause for clause in clauses)
        rows = self.read("""SELECT n.* FROM symbols s JOIN graph_nodes n ON n.symbol_id=s.id
            AND n.repository_id=s.repository_id WHERE s.repository_id=?
            AND (s.id=? OR s.name=? OR s.qualified_name=?)""" + extra +
            " ORDER BY s.relative_path,s.start_line,s.id LIMIT 101", values)
        if len(rows) == 1:
            node = self.graph.symbol(rows[0]["symbol_id"])
            self.add_evidence(node["symbol_id"], "symbol_definition", "syntax_only")
            return node, [], None
        if not rows:
            self.warnings.append("No indexed symbol matches the supplied identity and scope")
            return None, [], "not_found"
        self.warnings.append("Symbol is ambiguous; select a canonical symbol ID or a repository-relative file")
        return None, [self.definition(row) for row in rows[:parameters.limit]], "ambiguous"

    def module(self, path, limit):
        rows = self.read("""SELECT * FROM graph_nodes WHERE repository_id=? AND kind='Module'
            AND relative_path=? ORDER BY id LIMIT 101""", (path,))
        if not rows:
            rows = self.read("""SELECT * FROM graph_nodes WHERE repository_id=? AND kind='Module'
                AND substr(relative_path,-length(?)-1)='/' || ?
                ORDER BY relative_path,id LIMIT 101""", (path, path))
        if len(rows) == 1:
            self.add_evidence(rows[0]["id"], "source_range", "syntax_only")
            return rows[0], [], None
        if not rows:
            self.warnings.append("No indexed JS/TS module matches that repository-relative path")
            return None, [], "not_found"
        self.warnings.append("Module path is ambiguous; use its full repository-relative path")
        return None, [self.definition(row) for row in rows[:limit]], "ambiguous"

    def node(self, identifier):
        rows = self.read("SELECT * FROM graph_nodes WHERE repository_id=? AND id=? LIMIT 1", (identifier,))
        if not rows:
            raise StaleIndexError("Graph endpoint is missing")
        return rows[0]

    def relationship(self, edge):
        source, target = self.node(edge["source_id"]), self.node(edge["target_id"])
        for node in (source, target):
            self.add_evidence(node["symbol_id"] or node["id"],
                              "symbol_definition" if node["symbol_id"] else "source_range", "syntax_only")
        identifier = self.add_evidence(edge["id"], "graph_relationship", edge["resolution"])
        return GraphFinding(relationship=edge["relationship"],
                            source_node_id=source["id"], target_node_id=target["id"],
                            source_symbol_id=source["symbol_id"], target_symbol_id=target["symbol_id"],
                            source_name=source["label"], target_name=target["label"], evidence_ids=[identifier])

    def unknowns(self, node, relationships, limit):
        kwargs = {"symbol_id": node["symbol_id"]} if node["symbol_id"] else {"module_path": node["relative_path"]}
        unknown = [row for row in self.graph.unresolved_references(**kwargs)
                   if row["relationship"] in relationships]
        for row in unknown[:limit]:
            self.add_evidence(row["id"], "graph_relationship", "unresolved")
            self.unresolved.append(f"{row['expression']}: {row['reason']}")
        if len(unknown) > limit:
            self.warnings.append("Unresolved reference list reached the result limit")

    def execute(self, name, parameters):
        if name in ("find_paths", "find_dependency_paths"):
            return self.paths(name, parameters)
        if isinstance(parameters, SymbolParameters):
            node, candidates, status = self.symbol(parameters)
        else:
            node, candidates, status = self.module(parameters.module_path, parameters.limit)
        if status:
            return candidates, status
        if name == "find_definition":
            return [self.definition(node)], "success"
        if name == "find_callers":
            edges = self.graph.direct_callers(node["symbol_id"])
        elif name == "find_callees":
            edges = self.graph.direct_callees(node["symbol_id"])
        elif name == "find_imports":
            edges = self.graph.imports(node["relative_path"])
        elif name == "find_reverse_dependencies":
            edges = self.graph.reverse_dependencies(node["relative_path"])
        else:
            relationship = {"find_references": "REFERENCES", "find_importers": "IMPORTS",
                            "find_dependencies": "DEPENDS_ON"}[name]
            edges = self.graph._edges(node["id"], relationship,
                                      reverse=name in ("find_references", "find_importers"))
        findings = [self.relationship(edge) for edge in edges[:parameters.limit]]
        if name in ("find_callees", "find_imports", "find_dependencies"):
            self.unknowns(node, {"CALLS"} if name == "find_callees" else {"IMPORTS", "DEPENDS_ON"}, parameters.limit)
        if name in ("find_imports", "find_dependencies"):
            self.warnings.append("Only resolved indexed-module relationships are returned; inferred external imports are excluded")
        if name in ("find_callers", "find_importers", "find_reverse_dependencies", "find_references"):
            self.warnings.append("Incoming relationships cover resolved static evidence; unresolved dynamic targets cannot be attributed here")
        truncated = len(edges) > parameters.limit
        if truncated:
            self.warnings.append("Relationship results reached the configured limit")
        return findings, "partial" if truncated or self.unresolved else "success" if findings else "not_found"

    def paths(self, name, parameters):
        if name == "find_paths":
            source, candidates, status = self.symbol(SymbolParameters(reference=parameters.source, file_path=parameters.source_file))
            if status:
                return candidates, status
            target, candidates, status = self.symbol(SymbolParameters(reference=parameters.target, file_path=parameters.target_file))
            if status:
                return candidates, status
            found = self.graph.symbol_dependency_paths(source["symbol_id"], target["symbol_id"],
                        max_depth=parameters.max_depth, max_paths=parameters.limit + 1, max_work=parameters.max_work)
            paths = found["paths"]
            truncated = found["truncated"] or len(paths) > parameters.limit
            chains = [( [self.graph.symbol(symbol) for symbol in path["symbolIds"]], path["graphEdges"])
                      for path in paths[:parameters.limit]]
            relationship = "CALLS"
        else:
            source, candidates, status = self.module(parameters.source, parameters.limit)
            if status:
                return candidates, status
            target, candidates, status = self.module(parameters.target, parameters.limit)
            if status:
                return candidates, status
            paths = self.graph.dependency_paths(source["relative_path"], target["relative_path"],
                                                max_depth=parameters.max_depth, max_paths=parameters.limit + 1)
            truncated = len(paths) > parameters.limit
            chains = []
            for path in paths[:parameters.limit]:
                nodes = [self.module(part, parameters.limit)[0] for part in path]
                edges = []
                for left, right in zip(nodes, nodes[1:]):
                    rows = self.read("""SELECT id FROM graph_relationships WHERE repository_id=?
                        AND source_id=? AND target_id=? AND relationship='DEPENDS_ON' AND resolution='resolved'
                        ORDER BY id LIMIT 1""", (left["id"], right["id"]))
                    if not rows:
                        raise StaleIndexError("Dependency path lacks supporting graph evidence")
                    edges.append(rows[0]["id"])
                chains.append((nodes, edges))
            relationship = "DEPENDS_ON"
        findings = []
        for nodes, edges in chains:
            identifiers = [self.add_evidence(edge, "graph_relationship", "resolved") for edge in edges]
            for node in nodes:
                self.add_evidence(node["symbol_id"] or node["id"],
                                  "symbol_definition" if node["symbol_id"] else "source_range", "syntax_only")
            findings.append(GraphFinding(relationship=relationship, source_node_id=nodes[0]["id"],
                target_node_id=nodes[-1]["id"], source_symbol_id=nodes[0]["symbol_id"],
                target_symbol_id=nodes[-1]["symbol_id"], source_name=nodes[0]["label"], target_name=nodes[-1]["label"],
                path_node_ids=[node["id"] for node in nodes], path_names=[node["label"] for node in nodes],
                evidence_ids=identifiers))
        self.warnings.append(f"Static paths are limited to depth {parameters.max_depth}; no runtime behaviour or completeness beyond this depth is implied")
        if truncated:
            self.warnings.append("Path search reached its configured path/depth/work bound")
        return findings, "partial" if truncated else "success" if findings else "not_found"
