# Phase 6: deterministic router and multi-engine executor

## Scope and compatibility audit

This is a standalone Python integration layer, not a new production endpoint.
It plans the ordered requirements emitted by Phase 5 and dispatches only the
existing Graph, Semantic and Rule-Based engines. It returns typed findings and
clarifications, never an LLM-written answer. Root README, dependencies, stored
embeddings, knowledge-base implementations, and `/retrieve` and `/ask` are unchanged.

| Existing source | Reused interface | Integration responsibility |
| --- | --- | --- |
| `query_system/contracts.py` | `NormalizedQuery`, `IntentRequirement`, `QueryEntity`, `EntityCandidate` | Preserve ordered requirements, repeated intents, local references and canonical selections |
| `query_system/contracts.py` | `OperationRequest`, `OperationSpec.validate`, `EngineResult`, `Evidence`, `validate_engine_result` | Reuse existing operation/result/proof models; validate registered parameters and evidence |
| `query_system/query_understanding.py` | `QueryUnderstandingEngine.normalize` | Normalize within the executor's existing pin, with optional existing bounded `QueryContext` |
| `knowledge_base/synchronization.py` | `SnapshotStore.pin_view`, `SnapshotStore.matches` | One reader scope for normalization, planning, workers, candidates and publication |
| `knowledge_base/metadata.py` | `trace` | Verify saved source hashes, ranges, canonical IDs and provenance |
| `query_system/graph_engine.py` | `GRAPH_OPERATIONS`, `GraphQueryEngine.execute` | Existing graph queries, symbol and module paths; no new traversal |
| `query_system/semantic_engine.py` | `SEMANTIC_OPERATIONS`, `SemanticSearchEngine.execute`, `existing_api_channels` | Existing keyword/vector retrieval and RRF; query embeddings only |
| `query_system/rule_registry.py`, `query_system/rule_engine.py` | `RULES`, `RuleBasedAnalysisEngine.execute` | Existing bounded deterministic analyses and their limitations |

The engines expose asynchronous entry points but currently perform synchronous
work. The executor uses bounded thread workers with a private engine instance per
operation, bound to the same supplied view/context. It does **not** invoke their
separate `pinned()` scopes. Existing constructors and internal `_active` lifecycle
flags (including the rules engine's private graph engine) are compatibility
dependencies covered by integration tests. Existing `_Run.symbol` is reused for
canonical target kind verification and conversion of semantic hits to definition
candidates, not for a new symbol resolver. No existing engine was changed.

## Declarative policy

| Phase 5 intent | Engine / operation | Gate or limitation |
| --- | --- | --- |
| `SYMBOL_LOCATION` | graph / `find_definition` | Canonical JS/TS symbol; file-only definition lookup is unsupported |
| `DIRECT_CALLERS` | graph / `find_callers` | Canonical JS/TS symbol |
| `DIRECT_CALLEES` | graph / `find_callees` | Canonical JS/TS symbol |
| `DEPENDENCY_PATH` | graph / `find_paths` | Exactly two canonical symbols |
| `DEPENDENCY_PATH` | graph / `find_dependency_paths` | Exactly two canonical files/modules; mixed endpoints unsupported |
| `DEPENDENCY_PATH` | rules / `dependency_summary` | One canonical symbol or file/module; direct dependency summary |
| `FEATURE_DISCOVERY` | semantic / `hybrid_search` | Natural-language description; exact file filter if Phase 5 supplied one |
| `FUNCTION_CONTRACT` | rules / `function_contract` | Canonical function/method |
| `CHANGE_IMPACT` | rules / `change_impact` | Canonical symbol or file/module |
| `MODULE_OVERVIEW` | rules / `module_overview` | Canonical file/module, or a symbol's verified containing file |
| `TEST_DISCOVERY` | rules / `reference_test_analysis` | Canonical symbol; static test references, not runtime coverage |
| `FUNCTION_EXPLANATION`, `WORKFLOW_EXPLANATION`, `BEHAVIOR_INVESTIGATION`, `CODE_COMPARISON` | deferred | No Phase 6 deterministic answering operation |
| `UNRESOLVED` | clarification | No inferred intent or arbitrary execution |

Unknown operations/parameters are never generated from query text. A modified
plan is rejected before dispatch unless it exactly matches the declarative policy
compiled from its validated normalized input, limits and explicit selections.
Malformed contracts, incompatible input snapshots, and invalid selections raise
validation errors before dispatch. Expired execution sessions return stale reports.

## Plan, target gates and report

`execution_contracts.py` adds planning/report envelopes, not competing findings
or replacement engine results. An `ExecutionPlan` includes a stable content-derived
query ID, repository/generation, original normalized input, limits, selections,
all original requirement decisions, and admitted operations. Every operation has
a unique ID, owning requirement, existing `OperationRequest`, verified targets or
target dependencies, required parent requirements, operation dependencies,
capabilities, budget, and expected typed finding kind. Missing references,
duplicate IDs, operation cycles and excessive DAG depth are rejected.

Repeated intents remain separate. All requirements are retained even if a whole
requirement cannot fit the operation budget. Failed, deferred, ambiguous or
resource-limited parent requirements skip their dependents, rather than returning
empty success. An incomplete parent with valid findings and no fatal/budget error
can support further operations on an already verified canonical target. A healthy
`not_found` relationship lookup does not erase its known canonical target.

Unresolved natural-language targets are blocked until a caller explicitly selects
an `EntityCandidate`. Semantic findings can supply traced canonical definitions
in a clarification, but **even a single hit is not automatically selected**. After
selection, rebuild the plan in the same session; the chosen symbol must pass
metadata verification and cannot escape an explicitly requested file scope.
Ambiguous names offer existing definition candidates; missing targets remain
`not_found`; unsupported languages/capabilities remain unsupported. Semantic
similarity never stands in for a verified graph dependency. Symbol-less semantic
chunks cannot supply a canonical graph target.

The `ExecutionReport` contains the plan, unchanged requirements, ordered
per-operation `EngineResult` payloads/statuses, evidence registry, clarification
requests, unresolved dependencies, resource events, overall status, elapsed time
and peak concurrency. Successful independent branches survive provider failures
or invalid evidence in another branch. No Phase 7 synthesis, cross-engine finding
deduplication, new ranking policy, or natural-language explanation is performed.

## Snapshot and concurrency guarantees

All operations and returned proofs belong to one requested repository and one
published generation. Different ready internal graph/symbol/vector stage IDs are
legitimate and remain visible on their proofs. Canonical target mappings are
verified before dispatch; result envelopes, proof types, saved hashes, ranges and
relationships are checked before acceptance and again before publication,
including new clarification proofs. A saved-source change invalidates the entire
report. A bad provider payload invalidates its branch without repinning.

Ready independent graph/rule operations run concurrently. Semantic provider work
is conservatively limited to one operation per wave because an externally supplied
embedding provider is not guaranteed thread-safe. Dependencies run in later waves.
Stable plan-order commits keep result ordering and evidence-cap admission
independent of worker completion order. Wave barriers intentionally trade some
throughput for deterministic admission. Results are deterministic for fixed
normalized inputs, indexes and provider results; timings/deadline admission are not.

Default limits: 16 operations, dependency depth 8, graph results 25, semantic
candidates 5, rule findings 25, graph depth 3, concurrency 3, deadline 30 seconds,
and 200 returned proofs. Strict bounded `ExecutionLimits` rejects invalid values.
Each operation also retains its registered engine budget (graph 5s, rules 10s,
semantic 15s), bounded by the overall deadline. Existing traversal/analysis work
guards remain in effect. A result exceeding its finding budget is rejected. If
an entire operation's proof set cannot fit the global evidence cap, its payload
is omitted with an explicit partial/budget outcome, not half-sourced findings.

Timeouts are **cooperative**, not hard cancellation. Late findings are discarded;
synchronous workers are drained before releasing the reader pin. Caller
cancellation also drains workers before the session can be reused, even after
repeated cancellation. Consequently wall time can exceed the requested deadline,
and a permanently blocked provider can keep the pin open indefinitely. Avoid using
this in-process executor as a hard-latency isolation boundary. No production
process or thread is forcibly terminated.

## Usage

```python
from query_system.contracts import QueryRequest
from query_system.executor import MultiEngineExecutor
from query_system.semantic_engine import existing_api_channels

# api/config are existing loaded modules; store is an existing registered snapshot.
channels = existing_api_channels(api, config.EMBEDDING_MODEL_NAME)
with MultiEngineExecutor.pinned(store, channels) as executor:
    normalized = executor.understand(QueryRequest(
        repository_id=store.repo_id, question="Who calls validateToken?"))
    plan = executor.plan(normalized)
    report = await executor.execute(plan)
```

Keep the synchronous pin scope around the entire awaited operation. The optional
`engine_factory` is a trusted Python injection point for tests/in-process callers,
not query data, plugin loading, or arbitrary executable instructions. A session
executes one plan at a time and cannot execute after its pin scope exits.

## Verification and latency benchmark

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_rules query_system.test_semantic query_system.test_understanding query_system.test_execution -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run test:integration
.venv/bin/python -m query_system.benchmark_execution --fixture --repeats 3
git diff --check -- .
```

`benchmark_execution.py` compares sequential (one worker) and concurrent (three
worker limit) execution on the same nine labeled workloads, using one temporary
Phase 5 fixture and pin. Three repeats per mode give 54 timed runs. Query vectors
are fixed test vectors: this measures orchestration latency and proof validity,
not real embedding-model retrieval quality. Timed work includes planning and
execution, excludes normalization/index creation/warm-up, and alternates mode
order. Expected partial rule outcomes are preserved, not relabeled as success.
The script exits unsuccessfully if labels, deterministic results or proofs fail.
Saved results are in `benchmarks/phase6_results.json`; no user repository is indexed
and no user embedding collection is refreshed.

Verified on 2026-10-13 in the project virtual environment: 24 shared/unit tests,
59 knowledge-base tests, and 206 AI-engine tests passed (289 total, zero failures).
This includes Phase 0B through Phase 5 and 31 new Phase 6 tests. Extension
TypeScript compilation and the VS Code integration script passed with exit 0,
with no reported skipped connection check. Existing SQLite resource warnings,
Starlette deprecation notices and deliberately mocked offline-provider logs remain;
they were not changed as part of Phase 6. Whitespace checks passed for tracked and
new files.

The saved 54-run benchmark matched all nine workload labels and produced identical
findings/proofs across modes and repeats. All 312 proof checks were valid, under
one pin and one published generation (with legitimate differing stage IDs).
Planning+execution median/p95 was 73.720/873.042 ms sequential and
57.965/943.291 ms concurrent. The concurrent p95 was higher, so this fixture does
not establish an across-the-board performance improvement or production SLA.
