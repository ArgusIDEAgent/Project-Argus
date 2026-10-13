"""Contract validation independent of graph, vector and answer implementations."""

import asyncio
import unittest

from pydantic import BaseModel, ConfigDict, ValidationError

from query_system.contracts import (
    EditorSelection, EngineResult, Evidence, GraphFinding, InvalidOperationError,
    NormalizedQuery, OperationRequest, OperationSpec, QueryRequest, RagFinding,
    RepositoryMismatchError, RuleFinding, SemanticFinding, SnapshotContext,
    SnapshotMismatchError, StaleIndexError,
    UnknownEvidenceError, validate_engine_result,
)


REPOSITORY = "a" * 24
GENERATION = "b" * 32
STAGE = "stage-graph"
SOURCE_HASH = "c" * 64


class SearchParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str
    limit: int


class MockEngine:
    def __init__(self, result):
        self.result = result

    async def execute(self, operation, context):
        assert operation.snapshot_id == context.snapshot_id
        return self.result


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.context = SnapshotContext(
            repository_id=REPOSITORY, snapshot_id=GENERATION,
            ready_stage_ids=frozenset({STAGE, "stage-semantic"}),
            indexed_files=frozenset({"src/auth.ts", "src/login.ts"}),
        )
        self.graph_evidence = Evidence(
            evidence_id="call:1", repository_id=REPOSITORY, snapshot_id=GENERATION,
            index_snapshot_id=STAGE, evidence_type="graph_relationship",
            file_id="file:1", symbol_id=None, file_path="src/login.ts",
            start_line=2, end_line=2, resolution="resolved", source_hash=SOURCE_HASH,
        )
        self.semantic_evidence = Evidence(
            evidence_id="chunk:1", repository_id=REPOSITORY, snapshot_id=GENERATION,
            index_snapshot_id="stage-semantic", evidence_type="code_chunk",
            file_id="file:2", symbol_id="symbol:1", file_path="src/auth.ts",
            start_line=1, end_line=5, resolution="not_applicable", source_hash=SOURCE_HASH,
        )
        self.evidence = {item.evidence_id: item for item in (self.graph_evidence, self.semantic_evidence)}

    def test_client_request_and_normalized_intent_are_strict(self):
        request = QueryRequest(
            repository_id=REPOSITORY, question="Who calls validateToken?",
            active_file="src/auth.ts", cursor={"line": 3, "column": 7},
            selection={"start": {"line": 2, "column": 1}, "end": {"line": 3, "column": 8}},
            depth="deep",
        )
        self.context.validate_request(request)
        normalized = NormalizedQuery(
            intents=["DIRECT_CALLERS"], entities=[{"kind": "function", "name": "validateToken"}],
            scope="selected_symbol", depth=request.depth,
        )
        self.assertEqual(normalized.entities[0].name, "validateToken")
        for values in (
            {"repository_id": REPOSITORY, "question": "   "},
            {"repository_id": REPOSITORY, "question": "q", "active_file": "../escape.ts"},
            {"repository_id": REPOSITORY, "question": "q", "cursor": {"line": 1, "column": 1}},
            {"repository_id": REPOSITORY, "question": "q", "unexpected": True},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                QueryRequest.model_validate(values)
        with self.assertRaises(ValidationError):
            EditorSelection(start={"line": 2, "column": 1}, end={"line": 1, "column": 1})
        with self.assertRaises(SnapshotMismatchError):
            self.context.validate_request(QueryRequest(
                repository_id=REPOSITORY, question="q", snapshot_id="d" * 32,
            ))
        with self.assertRaises(RepositoryMismatchError):
            self.context.validate_request(QueryRequest(
                repository_id=REPOSITORY, question="q", active_file="secret.env",
            ))

    def test_graph_and_semantic_engines_share_one_result_contract(self):
        graph = EngineResult(
            engine="graph", status="success", repository_id=REPOSITORY,
            snapshot_id=GENERATION, execution_ms=3, evidence_ids=["call:1"],
            findings=[GraphFinding(relationship="CALLS", source_symbol_id="login",
                                   target_symbol_id="validateToken", evidence_ids=["call:1"])],
        )
        semantic = EngineResult(
            engine="semantic", status="success", repository_id=REPOSITORY,
            snapshot_id=GENERATION, execution_ms=5, evidence_ids=["chunk:1"],
            findings=[SemanticFinding(chunk_id="chunk:1", rank=1,
                                      retrieval_score=0.72, evidence_ids=["chunk:1"])],
        )
        operation = OperationRequest(engine="graph", name="find_callers",
                                     repository_id=REPOSITORY, snapshot_id=GENERATION,
                                     parameters={"query": "validateToken", "limit": 5})
        spec = OperationSpec(engine="graph", name="find_callers", parameters_model=SearchParameters,
                             evidence_types=frozenset({"graph_relationship"}),
                             max_execution_ms=100, fallback_status="partial")
        self.assertEqual(spec.validate(operation, self.context).limit, 5)
        for result in (graph, semantic):
            with self.subTest(engine=result.engine):
                returned = asyncio.run(MockEngine(result).execute(operation, self.context))
                parsed = EngineResult.model_validate_json(returned.model_dump_json())
                validate_engine_result(parsed, self.context, self.evidence)
        validate_engine_result(graph, self.context, self.evidence, spec)
        with self.assertRaises(InvalidOperationError):
            validate_engine_result(semantic, self.context, self.evidence, spec)
        with self.assertRaises(ValidationError):
            SearchParameters.model_validate({"query": "x", "limit": "5"})
        with self.assertRaises(InvalidOperationError):
            spec.validate(operation.model_copy(update={"name": "unknown"}), self.context)
        with self.assertRaises(InvalidOperationError):
            spec.validate(operation.model_copy(update={"parameters": {
                "query": "x", "limit": 1, "unsafe": True,
            }}), self.context)
        with self.assertRaises(SnapshotMismatchError):
            spec.validate(operation.model_copy(update={"snapshot_id": "d" * 32}), self.context)

    def test_mixed_generations_and_unknown_or_unready_evidence_are_rejected(self):
        result = EngineResult(
            engine="graph", status="success", repository_id=REPOSITORY,
            snapshot_id=GENERATION, execution_ms=1, evidence_ids=["call:1"],
            findings=[GraphFinding(relationship="CALLS", source_symbol_id="a",
                                   target_symbol_id="b", evidence_ids=["call:1"])],
        )
        with self.assertRaises(SnapshotMismatchError):
            validate_engine_result(result.model_copy(update={"snapshot_id": "d" * 32}),
                                   self.context, self.evidence)
        with self.assertRaises(RepositoryMismatchError):
            validate_engine_result(result.model_copy(update={"repository_id": "e" * 24}),
                                   self.context, self.evidence)
        with self.assertRaises(UnknownEvidenceError):
            validate_engine_result(result, self.context, {})
        with self.assertRaises(StaleIndexError):
            validate_engine_result(result, self.context, {
                "call:1": self.graph_evidence.model_copy(update={"index_snapshot_id": "failed-stage"})
            })
        with self.assertRaises(RepositoryMismatchError):
            Evidence.from_trace({"repository_id": "e" * 24}, self.context,
                                evidence_type="graph_relationship", resolution="resolved")
        with self.assertRaises(ValidationError):
            EngineResult(engine="graph", status="success", repository_id=REPOSITORY,
                         snapshot_id=GENERATION, execution_ms=1, evidence_ids=["call:1"],
                         findings=[SemanticFinding(chunk_id="chunk:1", rank=1,
                                                   retrieval_score=0.5, evidence_ids=["call:1"])])
        with self.assertRaises(ValidationError):
            Evidence(evidence_id="bad", repository_id=REPOSITORY, snapshot_id=GENERATION,
                     index_snapshot_id=STAGE, evidence_type="source_range", file_path="src/a.ts",
                     start_line=3, end_line=2, resolution="resolved", source_hash=SOURCE_HASH)

    def test_rule_and_rag_findings_require_evidence(self):
        rule = EngineResult(engine="rules", status="success", repository_id=REPOSITORY,
                            snapshot_id=GENERATION, execution_ms=2, evidence_ids=["call:1"],
                            findings=[RuleFinding(statement="A caller uses the result",
                                                  classification="derived_finding",
                                                  evidence_ids=["call:1"])])
        rag = EngineResult(engine="graph_rag", status="success", repository_id=REPOSITORY,
                           snapshot_id=GENERATION, execution_ms=7, evidence_ids=["chunk:1"],
                           findings=[RagFinding(explanation="This function checks a token",
                                                evidence_ids=["chunk:1"])])
        validate_engine_result(rule, self.context, self.evidence)
        validate_engine_result(rag, self.context, self.evidence)
        with self.assertRaises(ValidationError):
            RagFinding(explanation="Unsupported claim", evidence_ids=[])

    def test_graph_module_and_definition_identities_are_explicit(self):
        module = GraphFinding(relationship="IMPORTS", source_node_id="module:one",
                              target_node_id="module:two", evidence_ids=["edge:one"])
        self.assertIsNone(module.source_symbol_id)
        self.assertIsNone(module.target_symbol_id)
        definition = GraphFinding(relationship="DEFINITION", entity_id="symbol:one",
                                  evidence_ids=["symbol:one"])
        self.assertEqual(definition.entity_id, "symbol:one")
        with self.assertRaises(ValidationError):
            GraphFinding(relationship="CALLS", evidence_ids=["edge:one"])


if __name__ == "__main__":
    unittest.main()
