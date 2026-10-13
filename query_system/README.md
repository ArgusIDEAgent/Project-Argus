# Hybrid query contracts: Phase 1

Phase 6 adds a standalone deterministic planner and executor. See
[PHASE6.md](PHASE6.md) for the compatibility audit, routing table, one-pin lifecycle,
explicit semantic-candidate selection, resource limits and verification commands.
It reuses the existing engines and does not alter the production API routes.

Phase 7 adds standalone evidence aggregation and validation under the same
executor pin. See [PHASE7.md](PHASE7.md) for provenance checks, claim classes,
requirement coverage, canonical deduplication, budgets and backend-owned citations.
It does not invoke an LLM or switch `/retrieve` or `/ask` to new contracts.

`contracts.py` defines the boundary between CodeMind's existing knowledge base and
the planned graph, semantic, rules, and Graph RAG engines. It does not change the
current `/retrieve` or `/ask` response formats.

## Identity and snapshot rules

- `QueryRequest` contains client input. Its repository, file, selection, and
  symbol fields are hints until the server validates them.
- The server creates `SnapshotContext` inside `SnapshotStore.pin_view()` with
  `SnapshotContext.from_pinned_view(view, repository_id)`. It requires a fresh
  managed generation and reads ready stage IDs from that generation's metadata.
- `snapshot_id` in a query, operation, evidence record, or engine result means
  the **published generation ID** (`knowledgeSnapshotId` in the current API).
- `index_snapshot_id` in `Evidence` means the internal ready stage ID returned
  by `metadata.trace()`. Graph, symbol, and vector evidence may have different
  stage IDs within one generation. This does not imply mixed source versions.
- `SnapshotContext.validate_request()` checks repository and generation IDs and
  requires an active file to be listed in the indexed generation. A selected
  symbol ID remains unresolved until an engine verifies it against metadata.
- Call `metadata.trace()` for each canonical entity, then
  `Evidence.from_trace(info, context, ...)`. The trace checks saved source hash,
  range, and metadata provenance. The evidence model records those checks; it
  does not prove that a finding's interpretation is correct.

## Engine boundary

Each engine implements `QueryEngine.execute(operation, context)` and declares
its operations with `OperationSpec`. A specification names a Pydantic parameter
model, accepted evidence types, execution budget, and fallback status.
`OperationSpec.validate()` rejects unregistered operations, unexpected or
ill-typed parameters, and mismatched repositories or generations.

An engine returns `EngineResult` with a typed graph, semantic, rule, or RAG
finding. Results distinguish `success`, `partial`, `not_found`, `ambiguous`,
`unsupported`, `stale_index`, and `error`. Use `validate_engine_result()` before
combining results from multiple engines. It checks the pinned generation, ready
stage membership, evidence IDs, and the operation's allowed evidence types.

The operation's runtime budget and natural-language claim support need executor
and evidence-layer enforcement in later phases. These contracts do not execute
queries, authorize unindexed symbols, or validate model-written claims.

## Deterministic graph engine: Phase 2

`GraphQueryEngine.pinned(store)` opens a `SnapshotStore.pin_view()` scope and
creates the server context from that exact generation. Use the engine while the
scope is open; an expired scope or incompatible context produces `stale_index`.
The engine never independently selects the current active pointer during an
operation. Its `evidence` registry supplies canonical evidence for result IDs.

```python
from query_system.contracts import OperationRequest
from query_system.graph_engine import GraphQueryEngine
from query_system.graph_presentation import format_graph_result

with GraphQueryEngine.pinned(store) as engine:
    operation = OperationRequest(
        engine="graph", name="find_callers",
        repository_id=engine.context.repository_id,
        snapshot_id=engine.context.snapshot_id,
        parameters={"reference": "validateToken", "limit": 50},
    )
    result = await engine.execute(operation, engine.context)
    presentation = format_graph_result(operation.name, result, engine.evidence)
```

Supported operations:

| Operation | Parameters / existing graph capability |
| --- | --- |
| `find_definition` | `reference`, optional `file_path` / `symbol()` and `trace()` |
| `find_callers`, `find_callees` | Symbol identity / `direct_callers()`, `direct_callees()` |
| `find_references` | Symbol identity / existing reverse `REFERENCES` edges via `_edges()` |
| `find_imports`, `find_importers` | `module_path` / `imports()` and reverse `IMPORTS` via `_edges()` |
| `find_dependencies`, `find_reverse_dependencies` | `module_path` / direct `DEPENDS_ON` via `_edges()` and `reverse_dependencies()` |
| `find_paths` | `source`, `target`, optional file scopes / `symbol_dependency_paths()` |
| `find_dependency_paths` | Source and target module paths / `dependency_paths()` |

Symbol operations also accept `selected_symbol_id`; it is validated against
canonical metadata and any explicit reference/file constraints. Ambiguous names
return definition candidates, including their evidence and canonical IDs.
Unsupported operations return `unsupported`; invalid parameters return a typed
error. Empty resolved results use `not_found`. Unresolved outgoing calls/imports
produce `partial` with traced unresolved evidence and reasons. Dynamic incoming
targets cannot be assigned to a requested symbol and are not counted.

Graph findings now support definition entity IDs and canonical graph node IDs
alongside optional symbol IDs. Module nodes retain null symbol IDs. Existing
Phase 1 symbol-to-symbol findings remain valid. Evidence also retains the source
origin and schema version provided by `trace()`. No fields are fabricated for
structural nodes lacking a source location.

Source provenance is checked before publication of a result, including a final
saved-source digest check. Stale or invalid evidence discards the result.
Connections use SQLite `mode=ro` and `query_only`. The inherited graph query and
traversal algorithms remain unchanged. Queries use a 5-second budget with SQL
interruption and cooperative checks around existing graph work; native Python
traversals are bounded but are not forcibly preempted mid-loop.

Direct results are capped at 100 (default 50), paths at 100 (default 20), and
depth at 10 (default 3). Symbol paths have configurable work bounds up to 50,000
edges; the existing module path method retains its fixed 50,000-work guard.
Direct graph methods may materialize matches before the engine applies its
result cap. Result/path limits produce explicit partial results; path summaries
always state the depth bound. Inferred external-package imports are excluded
from verified module lookups. References are static references, not test coverage.

`graph_presentation.py` formats validated findings without graph access or LLM
calls. Repeated call sites are grouped by canonical caller/callee identity;
path variants retain their own evidence. Execution time is telemetry and may
differ across otherwise identical deterministic results.

Verification:

```sh
.venv/bin/python -m unittest query_system.test_contracts -v
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -p test_phase2_graph.py -v
```

## Semantic search engine: Phase 3

`SemanticSearchEngine.pinned(store, channels_factory)` binds search to one existing
published generation. The engine implements `semantic_search`, `keyword_search`,
and `hybrid_search`; operation parameters are `query`, `limit` (1–25), and `filters`.
It uses the existing retrieval channels, `plan_query()` for keyword extraction,
and `HybridRetriever._finish()` for the current RRF, duplicate elimination,
contextual ordering, and source checks. It does not select graph operations,
expand dependencies, register repositories, or call an answer model.

```python
from query_system.semantic_engine import SemanticSearchEngine, existing_api_channels

# api/config are the already-loaded engine modules; the repository is registered.
factory = existing_api_channels(api, config.EMBEDDING_MODEL_NAME)
with SemanticSearchEngine.pinned(store, factory) as engine:
    operation = OperationRequest(
        engine="semantic", name="hybrid_search",
        repository_id=engine.context.repository_id,
        snapshot_id=engine.context.snapshot_id,
        parameters={"query": "Where is refund eligibility checked?", "limit": 5},
    )
    result = await engine.execute(operation, engine.context)
    sources = [engine.evidence[identifier] for identifier in result.evidence_ids]
```

The API bridge reuses `_semantic_search()` and `_keyword_search()` with the pinned
database and vector collection, without changing `/retrieve` or `/ask`. It checks
registration and vector membership and distinguishes an empty filtered scope from
an unavailable collection. These internal provider/finalization interfaces are a
compatibility dependency covered by the integration tests. A `RetrievalChannels`
factory can supply existing channels directly with the same pinned-view contract.

Supported filter keys are `files`, `modules`, `languages`, and `symbolTypes`, each
with at most 25 values. Files and modules are exact normalized repository-relative
values; languages and symbol types are normalized to lowercase. Repository scope
comes from the operation and pinned context. Canonical symbol-ID and directory
filters are unsupported and produce errors rather than being ignored.

Every semantic finding carries the canonical chunk ID, final rank, retrieval
methods, and the existing ranking signals. Cosine similarity (`retrieval_score`),
BM25 (`keyword_score`), and RRF (`fusion_score`) stay separate. Keyword-only hits
have no cosine score. Evidence retains canonical source IDs, file/symbol mappings,
hashes, ranges, published generation, internal stage ID, and source provenance.
Similarity never produces a verified graph relationship. Python chunks retain
null canonical symbol IDs where the knowledge base has no resolved symbol.

Healthy empty searches return `not_found`. Unsupported operations and invalid
filters are explicit. Missing registration or unavailable channels with no usable
fallback return `error`. An unavailable semantic channel may return verified
keyword evidence as `partial`; loss of the keyword channel in a hybrid search may
likewise return partial semantic evidence. An explicitly requested keyword search
can succeed normally. Stale sources, invalid provider evidence, failed stages,
wrong generations, or vector/metadata inconsistencies discard results.

The engine verifies stored/query model names before comparing vectors and checks
the saved-source digest before and after retrieval. It consumes stored embeddings
and only embeds incoming queries through the existing provider. Its 15-second
budget is cooperative around the existing synchronous provider calls; it does not
forcibly cancel a model inference or vector request mid-call. The current baseline's
0.5 similarity floor and ranking policy are preserved.

### Labeled benchmark

`benchmark_semantic.py` reuses the existing 12 JS/TS/Python feature-location labels
from `knowledge_base.benchmark_semantic`. `--fixture` provisions one isolated,
temporary test repository through the existing indexer and configured BGE model;
it neither refreshes user repositories nor rebuilds their stored embeddings.
The baseline and typed engine share one pinned snapshot, model, top-five limit,
and dataset. Model loading and fixture creation are excluded from timed retrieval.
Each query runs three times with alternating baseline/engine order after warm-up.

The saved `benchmarks/phase3_results.json` reports Recall@5, MRR@5, median/p95
latency, citation validity, per-query ranks, and snapshot identity. Both paths
achieved Recall@5 = 1.0, MRR@5 = 1.0, and evidence validity = 1.0 on this small
fixture. Result ordering was identical. Median latency was 33.262 ms for the
baseline and 42.765 ms for the typed engine. These fixture results do not establish
retrieval quality for larger or different repositories.

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_semantic -v
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -p test_phase3_semantic.py -v
.venv/bin/python -m query_system.benchmark_semantic --fixture --repeats 3
```

## Deterministic rule engine: Phase 4

`RuleBasedAnalysisEngine.pinned(store)` implements five explicit rule operations
with one pinned generation and no LLM, semantic discovery or indexing calls.
See [PHASE4.md](PHASE4.md) for the interface audit, registry, operation parameters,
evidence guarantees, partial/unsupported outcomes, resource limits and tests.

## Query understanding: Phase 5

Use `QueryUnderstandingEngine` inside `pinned(store)`, then call
`normalize(request, conversation=None)` on that engine. It returns an enriched
Phase 1 `NormalizedQuery` using deterministic recognition and
read-only canonical entity resolution. It never dispatches an answering engine.
See [PHASE5.md](PHASE5.md) for the architecture audit, intent/context contracts,
compound requirements, capability limits and the 96-query labeled benchmark.
