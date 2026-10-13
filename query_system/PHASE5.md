# Phase 5: deterministic query understanding

This standalone, read-only normalizer describes developer questions. It does not
route, dispatch, retrieve answers, run rules, infer graph relationships, rewrite
questions with an LLM, or modify an index. No Phase 6 work is implemented.

## Architecture audit and compatibility

- Phase 1's `QueryRequest`, `QueryEntity`, `NormalizedQuery`, `SnapshotContext`
  and `Evidence` remain the public contracts. New fields are optional for old
  constructions; old JSON round-trips and engine operation types remain valid.
  Three small typed structures are necessary for information the old contracts
  could not safely express: `EntityCandidate` (canonical ambiguity proof),
  `IntentRequirement` (ordered target/capability requirements), and `QueryContext`
  (one bounded caller-owned turn). None is a new knowledge-base adapter.
- `knowledge_base.retrieval.plan_query()` is called unchanged. Its full existing
  plan is retained in `legacy_plan`. The Phase 5 front end adds multi-intent and
  context recognition rather than copying the baseline planner. The current
  production planner's NFKC/quote behavior is not changed; Phase 5 independently
  preserves exact identifiers instead of overwriting them with normalized legacy
  reference strings.
- Existing Phase 2 `_Run.symbol()`/`module()` resolution, `SymbolParameters`,
  read-only budgeted connections and `metadata.trace()` are reused with a metadata
  handle, **without instantiating or executing `GraphQueryEngine`**. These are
  private compatibility interfaces covered by integration tests. Canonical file
  resolution uses indexed graph file definitions or a traced existing chunk.
- `GRAPH_OPERATIONS`, `SEMANTIC_OPERATIONS` and Phase 4 `RULES` are inspected as
  capability declarations. The existing `answers.answer_question` RAG capability
  is described, never imported for execution by the normalizer. No answering
  engine's `execute()` or answer-generation function is called.

## Taxonomy and typed output

The twelve requested intents are supported: `SYMBOL_LOCATION`, `DIRECT_CALLERS`,
`DIRECT_CALLEES`, `DEPENDENCY_PATH`, `FEATURE_DISCOVERY`, `FUNCTION_EXPLANATION`,
`WORKFLOW_EXPLANATION`, `FUNCTION_CONTRACT`, `CHANGE_IMPACT`, `MODULE_OVERVIEW`,
`TEST_DISCOVERY`, `BEHAVIOR_INVESTIGATION`. Existing `CODE_COMPARISON` is retained
but explicitly lacks a dedicated operation. `UNRESOLVED` is a clarification
sentinel for pronoun-only follow-ups without a unique intent; it is not a route.

The returned `NormalizedQuery` contains the original and normalized questions,
repository/published generation, ordered primary/secondary intents, extracted
entities, canonical IDs/proofs or ambiguity candidates, requirements, target
dependencies, capabilities/limitations, context used, unresolved references,
explicit status and normalization time. Different ready internal stage IDs in
entity proofs are valid within the same published generation.

Patterns use case-insensitive developer-language cues while code names remain
case-sensitive and unchanged. Backticks, quoted identifiers, CamelCase,
snake_case, qualified names, relative paths and textual symbol IDs are preserved.
Whitespace is normalized outside protected quoted/code spans. Quoted prose,
pasted fenced code and comments do not manufacture intent instructions. Escaped
quotes are recognized. A potential name never becomes a canonical ID without
successful pinned lookup and source verification.

Resolution distinguishes `resolved`, `ambiguous`, `not_found`, `unresolved_text`
and `unsupported`. Ambiguity is never resolved by choosing the first row; even
a one-candidate output limit preserves ambiguity and reports truncation. Basename
file ambiguity is retained and symbol candidates remain restricted to its valid
file scopes. Explicit paths qualify symbols; the active file is used only when
no explicit textual file scope is supplied. Selections/cursors are checked against
saved-source bounds and all containing declarations are retained if nested scopes
remain ambiguous. Evidence describes disk content, not unsaved editor buffers.

Feature discovery keeps the natural-language description without requiring an
existing symbol. An unformatted unknown workflow description can remain a discovery
target, rather than inventing a workflow function. Missing explicitly named symbols
remain missing. Interfaces/type aliases remain generic symbols, not fabricated
function/class declarations.

## Context and compounds

`QueryContext` holds only repository/generation, an active file, selected symbol,
editor selection, one previously resolved/proven target and up to twelve previous
intents. It is passed explicitly by the caller, not saved in a conversation database.
Previous identities must agree with their canonical proofs and are verified again.

Target precedence is explicit: current selected symbol; current selection/cursor;
fresh contextual selected symbol/selection; verified previous target; then active
module only for an explicit module overview. Explicit textual targets take priority
over pronouns/context. A generic follow-up can reuse a previous intent only if it
is unique, fresh and the current question has no recognized intent. Multiple
previous intents require clarification. Cross-repository/generation context is
ignored with a warning; a context-dependent question stays unresolved rather than
guessing. Explicit fresh textual targets can still be normalized independently.

Compound clauses retain multiple requirements, including repeated instances of
one intent with different targets. Primary/secondary taxonomy labels are unique
in first-recognition order; requirement order is preserved separately. Local target
inheritance refers to earlier requirements using `depends_on`. Independently scoped
duplicates stay independent. A singular pronoun after multiple local targets is
ambiguous. These are target-reference requirements, not an execution DAG or router.

```python
from query_system.contracts import QueryContext, QueryRequest
from query_system.query_understanding import QueryUnderstandingEngine

with QueryUnderstandingEngine.pinned(store) as engine:
    first = engine.normalize(QueryRequest(
        repository_id=engine.context.repository_id,
        question="Where is login defined?",
    ))
    # Only carry a uniquely resolved target, not an arbitrary ambiguity candidate.
    context = QueryContext(
        repository_id=engine.context.repository_id,
        snapshot_id=engine.context.snapshot_id,
        previous_target=first.entities[0], previous_intents=first.intents,
    )
    followup = engine.normalize(QueryRequest(
        repository_id=engine.context.repository_id,
        question="Who calls this function?",
    ), context)
```

## Capability and resource limitations

Capability support describes declared interfaces, not live index/model health or
an executed answer. JS/TS canonical symbols and graph/static rules are stronger
than Python. Python files can have verified chunk/file evidence and semantic
discovery support but not invented resolved graph symbols. File/module targets
do not satisfy operations requiring a canonical function/method. Function contract
analysis rejects non-functions; runtime behavior, workflow explanation, coverage,
complete contracts and guaranteed impact remain partial. Static connectivity does
not prove runtime behavior; semantic similarity does not prove dependency.

Each requirement has `supported`, `partial`, `unsupported` or `unresolved` support.
One unsupported/unresolved portion does not discard supported compound portions.
Aggregate outcomes distinguish success, partial, ambiguity, unresolved, unsupported,
stale index and errors. Success means the query is understood, not answered.
Malformed input fails the shared strict request/planner validation boundary.
Stale/mismatched source or canonical proof clears all returned identities.

Limits: 2,000 question characters; twelve extracted entities and requirements;
twenty-five ambiguity candidates per entity; one previous target capped at 16 KiB;
4,096-character contextual file/symbol references; five-second cooperative lookup
budget with existing SQLite progress interruption. Existing source/digest hashing
reads saved files and is not forcibly preempted. Queries can still be costly in
large repositories. No persistent cache, new parser/index, dependency or LLM is
introduced. Arbitrary unseen English phrasing is not guaranteed to be understood.

## Labeled development benchmark

`benchmarks/phase5_queries.json` contains 96 hand-labeled questions across 17
categories. Labels include canonical source expectations, duplicates, compounds,
follow-ups, misleading text and unsupported requests. Independent metadata gold
lookups validate canonical IDs, not merely a same-file hit. Entity resolution
accuracy is correctness over 93 labeled references; auxiliary file qualifiers are
not extra denominator entries. Functional descriptions are measured separately.
All ambiguity candidates are checked against the gold identities/file scopes.

`benchmark_understanding.py --fixture` reuses temporary test fixtures and the
unchanged existing indexer with fixed test vectors. Only isolated test data is
published; no user repository is rebuilt. Normalization is metadata-only. Answer
engine, embedding and LLM entry points are guarded against calls. Three repeated
queries share one pin; fixture publication, pin construction, context seeding and
post-query benchmark proof checks are outside timed normalization.

The saved `benchmarks/phase5_results.json` reports category-specific results:

| Category | Queries | Exact intent accuracy | Labeled entity accuracy |
| --- | ---: | ---: | ---: |
| Symbol location | 6 | 100% | 100% |
| Direct callers | 6 | 100% | 100% |
| Direct callees | 6 | 100% | 100% |
| Dependency paths | 6 | 100% | 100% |
| Feature discovery | 6 | 100% | N/A; descriptions preserved |
| Function explanation | 6 | 100% | 100% |
| Workflow explanation | 6 | 100% | 100% |
| Function contract | 6 | 100% | 100% |
| Change impact | 6 | 100% | 100% |
| Module overview | 6 | 100% | 100% |
| Test discovery | 6 | 100% | 100% |
| Behavior investigation | 6 | 100% | 100% |
| Compound | 6 | 100% | 100% |
| Ambiguity/missing targets | 6 | 100% | 100% |
| Context | 6 | 100% | 100% |
| Misleading text | 3 | 100% | 100% |
| Unsupported | 3 | 100% | 100% |

All 96 questions were stable across three repeats; all four labeled ambiguities,
eight descriptions and two explicit unsupported-capability labels were handled
correctly. All 288 checked canonical proofs were valid in one pinned generation.
Median normalization latency was 17.809 ms; p95 was 23.392 ms on the small fixture.
This is a development/regression corpus, **not held-out general accuracy**; its
patterns were used during implementation and these results do not guarantee unseen
phrasing or production-repository performance.

## Verification

Unit/integration coverage includes all requested intents, compounds/repeated
intents, independently scoped duplicates, quoted/qualified/Unicode identifiers,
feature descriptions, missing names, ambiguous files/symbols/nested selections,
source bounds, pronouns/context precedence, unique previous intent, stale/cross-
generation context, unsupported capabilities, malformed inputs, stable repetition,
unchanged planner behavior, read-only database content, missing metadata, unready
stages, budgets, guarded no-dispatch/no-LLM behavior and canonical benchmark metrics.

Verified on 2026-10-13: 18 shared/unit tests, 59 knowledge-base tests and 181
AI-engine tests passed (258 total, zero failures). This includes 29 new Phase 5
tests and all existing Phase 0B–4 regressions. Extension compilation and VS Code
integration passed. Existing resource/dependency warnings and deliberate mocked
outage logs remain, without assertion failures. Whitespace checks also include
the new untracked files. Existing production APIs/engines, stored user embeddings,
root README and dependencies remain unchanged.

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_rules query_system.test_semantic query_system.test_understanding -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run test:integration
.venv/bin/python -m query_system.benchmark_understanding --fixture --repeats 3
git diff --check -- .
```
