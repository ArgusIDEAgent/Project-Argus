# Phase 2 — source-evidence foundation

Started 2026-11-26 after real APFS atomic capture and mount cleanup were qualified. This record describes the implemented first slice, not completion of the entire impact-analysis phase.

## Implemented flow

1. Obtain a store-owned `ChangeSnapshot` from `APFSSnapshotCapture` or `atomic_session`.
2. Require `atomic_apfs` provenance and no merge conflicts. Ordinary observed-stability captures fail closed at this boundary.
3. Parse the separate before/after byte tuples using the existing Tree-sitter `parse_source` interface. The syntax stage reads no live source, knowledge-generation lock or model. The captured semantic stage invokes the trusted local TypeScript bridge using captured bytes.
4. Create two existing `EvidencePackage` objects, each with a different capture-derived snapshot identity. The shared package validators are unchanged.
5. Wrap them in `ChangeEvidencePackage`, validating repository/capture/version ownership, source hashes, source file IDs, citations and symbol references.
6. Associate diff ranges with retained declarations. Match declaration-key candidates and exact-file-rename candidates conservatively. Multiple candidates remain ambiguous; unmatched does not prove deletion.
7. At consumption, `validate_against_capture` revalidates the wrapper and regenerates canonical syntax proofs from the original captured bytes. A coherently modified citation cannot change a syntax observation into a resolved relationship.

```python
from change_analysis.atomic_session import atomic_session
from change_analysis.evidence import build_change_evidence, validate_against_capture

with atomic_session('/absolute/path/to/repository') as session:
    captured = session.capture()
# Snapshot source is now in immutable memory; mounted volume is cleaned up.
evidence = build_change_evidence(captured)
evidence = validate_against_capture(evidence, captured)
```

The builder is an internal library boundary for trusted store-owned captures, not a public endpoint accepting caller-supplied provenance. Future API work must resolve capture IDs through the owning session; a caller-provided string saying `atomic_apfs` is not proof of an actual OS snapshot.

## Evidence and budgets

Each version retains an eligible-source manifest, source-range proofs and declaration proofs. Deleted declarations remain cited against the baseline package. All symbol proofs remain `syntax_only`; a textual call or import is not represented as a verified graph edge.

Defaults: at most 200 evidence records per version; 1 MiB serialized budget for each existing EvidencePackage. Ordering is deterministic. Omitted file/declaration records have explicit counts and version-specific limitations. Exclusions and syntax-partial files stay visible. The source parse workload remains bounded by P1's file/byte limits; a whole-job deadline is not implemented in this slice.

Parser distribution versions and syntax schema version are recorded. No knowledge generation is reused, so there is no nested reader lock or embedding dependency. Evidence remains historical; this stage does not assert current workspace freshness.

## Still required for Phase 2 completion

- Resolve calls/imports/exports from both captured versions using the existing semantic resolver with a capture-only input boundary.
- Validate graph nodes and relationship evidence, then compute changed relationships.
- Add bounded caller/callee traversal, cycles and graph omission counts.
- Expand correspondence for edited renames/signatures with explicit ambiguity.
- Add separately qualified framework metadata adapters; currently none are enabled.
- Qualify complete end-to-end graph/source ownership and reader/freshness behavior.

The wrapper deliberately emits no graph findings yet. P2 checklist items remain partial where source-only work does not meet the full requirement. Existing chat, indexing, API routes and test execution remain unchanged.

## Verification

The combined regression command is recorded in `PHASE1.md`. New tests cover disjoint source versions, repository/capture/provenance mismatch, invalid citations, dangling correspondence, deleted baseline source, deterministic budget omissions, captured-byte tampering, ambiguous declarations, exact-file renames, and canonical proof validation. A test forbids Python live-source reads and permits only the trusted TypeScript bridge process during evidence generation and validation.

Live smoke on the actual project snapshot succeeded: 200 records per version, 189 baseline symbol correspondence entries and 390 explicitly omitted file/definition records per version. The baseline EvidencePackage serialized to 401,061 bytes, below its explicit 1 MiB limit. This is a source-evidence smoke result, not graph/impact qualification or a performance benchmark.

Final combined verification: **119 tests and 95 subtests passed**, with one opt-in native APFS test skipped in the unattended suite because its real mounted run had already passed and the mount was cleaned up. The 15 focused source-evidence cases are included in this result. Canonical proof validation and mixed-version rejection passed; resolved graph/impact qualification remains pending.

## Captured relationship and impact slice — 2026-11-26

The captured-byte mode of `knowledge_base/semantic_resolver.cjs` now receives the version inventory and all eligible source strings through stdin. Its compiler host resolves only those strings; it cannot fall through to saved working-tree files or tsconfig files. The pre-existing filesystem mode remains unchanged and its Node resolver fixtures passed. Python invokes the trusted bridge with a 30-second deadline and a 16 MB output check; captured-mode events are limited to 10,000 with an explicit omitted count. No project code is executed.

`change_analysis/structure.py` maps unique TypeScript call/import/export targets to Tree-sitter declarations from the same captured version. It requires contained byte spans and retains a source-backed `graph_relationship` EvidenceRecord for each displayed edge. Ambiguous, dynamic or missing targets stay unresolved. Record budgets reserve capacity for relationships; omitted edge and event counts are reported. Validation re-runs the semantic pass against the original captured bytes and rejects modified endpoints or proofs.

`change_analysis/impact.py` compares relationship multiplicity across versions only when both endpoints have unambiguous correspondence. A possible rename or missing endpoint yields an uncertain count, never a definite added/removed relationship. It traverses reverse CALLS edges to depth two by default with cycle detection, stable ordering, and explicit result caps (200 relationship changes and 100 caller paths). Caller paths are static structure, not runtime reach or defect attribution.

This is still **not Phase 2 completion**. Edited-renames and signature correspondence, framework metadata adapters, matching knowledge-generation reuse, full parser budgets, and a complete real-project graph qualification remain open. The current resolver uses captured source only and no tsconfig, so alias imports requiring project configuration may be unresolved. The packages are historical and do not claim the live workspace is fresh. No API/UI integration is provided here.

Focused fixtures cover cross-file calls/imports, relationship additions and removals, bounded caller chains, dynamic calls, later live edits, wrong-version/tampered proofs, and unmatched renames that must remain uncertain. Node resolver fixtures and existing graph/API regressions passed in the relevant combined run. See the current plan progress log for exact commands and counts.

## Phase 2 completion — 2026-11-27

This completion record supersedes earlier progress entries. The static JS/TS library milestone is implemented and qualified; framework behavior and future UI/test-run workflows have explicit unsupported boundaries.

New implementation:

- `symbol_mapping.py` derives correspondence from captured declarations, using declaration keys (including signature changes), exact file-rename metadata and declaration content with the first declaration-name occurrence normalized. Exact-content candidates can connect preserved declarations across an edited file rename. Simultaneous arbitrary name/body edits remain unresolved. Multiple candidates and multiple old declarations competing for one new declaration stay ambiguous. Correspondence is structural association, never behavior equivalence.
- `validate_against_capture` regenerates correspondence and canonical symbol metadata/changed flags. Tampering with either can no longer alter impact origins or relationship comparison. It also checks the recorded TypeScript resolver version.
- `impact.py` suppresses definitive relationship deltas whenever omitted records, omitted relationships, unresolved references or partial syntax make absence unreliable. It exposes completeness and depth-limited frontier counts, alongside display omissions. Unsupported/dynamic boundaries remain visible. Calls targeting partial syntax are not published as resolved relationships.
- `service.analyze_change(store, capture_id)` resolves the capture through its owning store and returns both evidence and impact with historical freshness. No knowledge-generation reader lock is acquired. A real capture may outlive its mount because source bytes remain immutable in memory. Future P3 routes must use store-owned IDs.
- No framework adapters are enabled. A request for Express or another framework adapter fails explicitly at the service boundary. Route reachability, framework semantics and runtime coverage are not claimed. This satisfies the plan's requirement to enable only qualified framework capabilities; framework expansion requires its own adapter qualification.
- Node's captured semantic process has a 30-second deadline, a 512 MiB V8 heap limit, an event cap and an output-size check. The heap limit is not a total-process RSS guarantee. Source inputs remain bounded by capture file/byte limits; failure produces no completed analysis result.

Use after creating a verified atomic capture:

```python
from change_analysis.service import analyze_change

result = analyze_change(capture_store, capture_id)
# result.evidence, result.impact, result.freshness == 'historical_capture'
```

Real-project qualification command (run as the developer in Terminal, with administrator prompts restricted to Apple mount tools):

```sh
.venv/bin/python -m change_analysis.qualify '/absolute/path/to/repository'
```

On this project, the command created and consumed atomic capture `602708ae12504fe08811f88650edc044` and completed cleanup in 12.817 seconds. Each version contained 35 eligible source files, 154 retained symbols and 40 retained relationships; 431 source/definition records and 371 relationships were omitted per version. Unresolved counts were 1,199 before and 1,202 after. Definitive relationship changes were withheld due to incomplete evidence. No caller paths were retained for this workload. This is a bounded real-project smoke qualification, not a claim of complete project understanding or a latency benchmark. Final mount inventory confirmed no CodeMind mounts remained.

Verification command:

```sh
.venv/bin/python -m pytest knowledge_base/test_change_capture.py knowledge_base/test_atomic_capture.py knowledge_base/test_atomic_session.py knowledge_base/test_change_evidence.py knowledge_base/test_change_impact.py knowledge_base/test_graph.py knowledge_base/test_semantic.py query_system/test_evidence.py ai-engine/tests/test_runtime_api.py -q
node --test knowledge_base/test_semantic_resolver.cjs
```

The combined Python run passed 124 tests and 71 subtests, with one opt-in native APFS test skipped after its earlier mounted qualification and one existing Starlette/httpx deprecation warning. The nine Node resolver fixtures passed. A final focused run covers the last dynamic-boundary and partial-syntax adjustments; its result is recorded below.

Known scope limits: captured tsconfig/package manifests are not available in the source-only capture contract, so configuration-dependent aliases may remain unresolved. Structural correspondence deliberately stops short of arbitrary fuzzy matching. There is no model dependency, framework runtime understanding or automatic freshness claim. P3 provides the API/UI lifecycle; P4 and later phases provide test planning/execution.

Final focused verification: **44 tests and 11 subtests passed** in 21.53 seconds, covering evidence, impact, existing graph and shared evidence validation after the final dynamic/partial-syntax adjustments. Scoped whitespace checks passed. The completed Terminal launcher was removed; Time Machine snapshots remain managed by macOS.
