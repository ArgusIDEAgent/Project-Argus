# Phase 4: deterministic, limitation-aware rules

This is a standalone engine, not a production API replacement or query router.
The root README, knowledge base, Graph Query Engine, Semantic Search Engine,
stored embeddings, dependencies, `/retrieve`, and `/ask` remain unchanged.

## Integration audit

| Existing source / interface | Available evidence | Reuse and limits |
| --- | --- | --- |
| `contracts.py`: `OperationSpec`, `EngineResult`, `RuleFinding`, `Evidence`, `validate_engine_result` | Typed operations, outcomes, classifications and canonical evidence | Backward-compatible optional rule ID, category, details and limitations added to `RuleFinding`; validation does not prove runtime behavior |
| `knowledge_base/synchronization.py`: `SnapshotStore.pin_view()`, `matches()` | Published generation, saved-source inventory/digest | Exactly one pin; a graph engine is bound to that same view, never separately selects the active generation |
| `graph_engine.py`: `execute()` | Definition, callers/callees, references, imports/dependents, symbol/module paths | Existing operations execute unchanged; shared private `_Run` read/normalization helpers supply module resolution, read-only SQL and evidence conversion |
| `knowledge_base/graph.py`: `KnowledgeGraph.test_references()`, `_edges()`, `unresolved_references()` | Static test references, `EXPORTS`, `EXTENDS`, `IMPLEMENTS`, outgoing `REFERENCES`, unresolved reasons | Existing graph methods, using Phase 2's read-only budgeted connection; no new traversal implementation |
| `knowledge_base/metadata.py`: `trace()` | Repository/file/symbol mappings, ready stage, source hash and byte/line range | Every returned proof is traced again before publication; distinct ready stage IDs within one generation are legitimate |
| `symbols` metadata, populated by `knowledge_base/parser.py` | Names, kinds, parent IDs, parameter names/annotations/optional/rest flags and spans, return annotation, modifiers, export flags | Only declared JS/TS facts; no complete inferred return values, default-value model, exception/throw inventory or runtime contract analysis |
| Phase 3 semantic engine and existing retrieval validation | Ranked, verified code discovery; similarity is not dependency evidence | Inspected but not invoked: the five rules do not need semantic discovery |

Graph edge types include inventory `CONTAINS`/`DEFINES`, resolved static
`CALLS`, `REFERENCES`, `IMPORTS`, import-derived `DEPENDS_ON`, `EXPORTS`,
`EXTENDS`, `IMPLEMENTS`, inferred external imports and explicit unresolved rows.
Only resolved relationships become positive connectivity findings. Unresolved
targets are reported without inventing endpoints; inferred external imports are
not presented as verified dependencies on indexed modules.

## Rule registry and supported operations

`rule_registry.py` contains five frozen typed definitions with descriptions,
input models, required capabilities, supported languages, evidence requirements,
limits and known limitations. No natural-language classification is performed.

| Rule ID | Inputs | Findings |
| --- | --- | --- |
| `function_contract` | Symbol reference or canonical ID; optional file scope | Declared name/location, parameters, return annotation, async/export modifiers; return behavior is `not_inferred`, exception behavior is `not_indexed` |
| `dependency_summary` | Symbol or module; optional `path_target` and symbol target file | Direct/reverse calls, references, inheritance/interface relationships, or module dependencies; optional bounded existing paths |
| `reference_test_analysis` | Symbol reference or ID | Known references, resolved callers, existing static test-reference lookup, unresolved outgoing relationships |
| `change_impact` | Symbol or module | Direct incoming relationships, module dependents, bounded incoming call/module paths, declarations/exports, direct and path-linked test references, possible review locations |
| `module_overview` | Module path | Indexed declarations, direct imports/dependencies, dependents, resolved exports including aliases/re-exports, and test references to indexed declarations |

All results use `engine="rules"` and Phase 1's typed envelope. Individual findings
retain rule ID, category, structured details and canonical evidence IDs.
Observed indexed relationships are `verified_observation`; multi-edge path
connectivity is `derived_finding`; possible change/test consequences are
`hypothesis`. No result claims that changing a function necessarily breaks code.

```python
from query_system.contracts import OperationRequest
from query_system.rule_engine import RuleBasedAnalysisEngine

with RuleBasedAnalysisEngine.pinned(store) as engine:
    operation = OperationRequest(
        engine="rules", name="change_impact",
        repository_id=engine.context.repository_id,
        snapshot_id=engine.context.snapshot_id,
        parameters={"reference": "validateToken", "max_depth": 3, "limit": 50},
    )
    result = await engine.execute(operation, engine.context)
    sources = [engine.evidence[key] for key in result.evidence_ids]
```

## Outcomes and language limits

- `success`: requested supported static facts returned within their bounds, not
  a runtime-completeness guarantee.
- `partial`: missing comprehensive contract analysis, no indexed test references,
  unresolved relationships, missing bounded paths or a resource/capability limit.
- `not_found`: no matching primary symbol/module in this indexed scope.
- `ambiguous`: candidate definitions remain ambiguous even at `limit=1`; select
  a canonical symbol ID or full repository-relative path. Ambiguity is preserved
  even when other parts of the analysis are partial.
- `unsupported`: unregistered rule, explicitly scoped non-JS/TS language or a
  non-function target for function-contract analysis.
- `stale_index`: changed source hash/range/mapping, unready stage, mismatched
  generation, incompatible graph proof or expired pin. Findings are discarded.
- `error`: invalid input or unavailable required metadata; queries never recreate
  a missing database.

JS, JSX, TS and TSX use the existing syntax/checker capabilities. Type annotations
are absent where not declared; overloads can remain ambiguous. Class methods,
callbacks and dynamic dispatch inherit existing resolution limitations. Exported
variables not modeled as canonical symbols can remain unresolved. Export flags
and `EXPORTS` edges are available interfaces, not a complete visibility/API model.
Python semantic chunks do not give Python a resolved graph/signature capability;
explicit Python and other-language scopes return `unsupported`.

Test matching uses the **existing graph naming convention**, not a new test
framework detector. Module test lookup examines references to indexed declarations;
an import-only test can appear as a module dependent but is not declared to cover
those symbols. Missing references never prove absence of tests or execution.
Exceptions mentioned in source (for example `throw new Error`) are not inferred
as a complete exception contract from constructor connectivity.

## Resource and performance limits

- Findings: default 50, maximum 100 for the entire analysis.
- Path depth: default 3, maximum 5; cycles use existing cycle-safe path routines.
- Graph footprint: default 200 nodes/1,000 edges, maxima 2,000/10,000. Counts
  include the pinned repository's structural nodes and unified relationships.
  When exceeded, relationship/path/test calls are skipped and identity/declaration
  facts return `partial`. This conservative guard bounds existing methods that
  materialize edge lists/adjacency before slicing; it is not a new graph index.
- Signature context: cumulative 8,192 UTF-8 bytes by default, maximum 65,536;
  parameters/modifiers/return text is omitted explicitly if over budget. No source
  bodies are loaded for new interpretation. Existing provenance hashing still
  reads saved files and is not constrained by this context-output budget.
- Analysis time: 10 seconds, with existing SQLite progress interruption and
  cooperative checks. Each graph operation retains its own 5-second budget.
  Existing module path search retains its fixed 50,000-work bound; symbol path
  work is limited to `max_edges` per path query. Native Python traversal and file
  hashing are not forcibly preempted mid-call.
- Reverse discovery enumerates bounded canonical candidates and invokes existing
  candidate-to-target path operations for symbols and modules. It can be more
  expensive than a dedicated reverse traversal; none is introduced in this phase.
  A finding/work/depth cap never establishes absence of deeper impact.

This engine relies on existing private graph normalization helpers. That coupling
is covered by integration tests and documented rather than refactoring the verified
production engines outside Phase 4 scope. Execution time is telemetry and is
excluded from deterministic repeated-result comparisons.

## Verification

`test_phase4_rules.py` uses real temporary managed SQLite/FAISS generations and
the unchanged existing indexer. Fixture vectors are fixed; queries never generate
embeddings or rebuild a user repository. Coverage includes all five rules, JS/TS
declarations, inheritance/interfaces, export aliases, indirect module and test
impact, missing callers/tests/metadata, duplicate and ambiguous symbols, cycles,
depth/node/edge/finding/context/time limits, unresolved/unsupported capabilities,
source edits/hashes/ranges, mixed generations, valid differing stage IDs, mocked
invalid graph proof, stable repeats, unchanged database content and no LLM calls.
`test_rules.py` covers registry/input strictness and old/new contract compatibility.

Verified on 2026-10-13: 11 shared contract/registry tests, 59 knowledge-base
tests and 159 AI-engine tests passed (229 total, zero failures). The AI-engine
run includes Phase 0B, graph, semantic and 28 new rule integration tests.
Extension compilation and VS Code integration exited successfully. Existing
resource/dependency warnings and intentional mocked outage logs remain; they
are not assertion failures. Whitespace checks include the new untracked files.

A three-repeat smoke measurement on the same small pinned fixture, excluding
fixture publication, produced these medians: function contract 29.017 ms,
dependency summary 85.034 ms, reference/test analysis 73.175 ms, change impact
582.949 ms, module overview 89.057 ms. Maximum measured impact latency was
595.894 ms. These are local synthetic-fixture observations, not performance
guarantees for larger repositories; contract/reference/impact results remained
explicitly partial where the fixture has unavailable or unresolved capabilities.

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_semantic query_system.test_rules -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run test:integration
git diff --check -- .
```
