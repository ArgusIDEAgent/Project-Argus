# Phase 2 — source-evidence foundation

Started 2026-11-26 after real APFS atomic capture and mount cleanup were qualified. This record describes the implemented first slice, not completion of the entire impact-analysis phase.

## Implemented flow

1. Obtain a store-owned `ChangeSnapshot` from `APFSSnapshotCapture` or `atomic_session`.
2. Require `atomic_apfs` provenance and no merge conflicts. Ordinary observed-stability captures fail closed at this boundary.
3. Parse the separate before/after byte tuples using the existing Tree-sitter `parse_source` interface. No live source reads, knowledge-generation locks, embeddings, model calls or repository processes are used.
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

The combined regression command is recorded in `PHASE1.md`. New tests cover disjoint source versions, repository/capture/provenance mismatch, invalid citations, dangling correspondence, deleted baseline source, deterministic budget omissions, captured-byte tampering, ambiguous declarations, exact-file renames, and canonical proof validation. A test forbids filesystem reads and subprocess creation during evidence generation and validation after adapter loading.

Live smoke on the actual project snapshot succeeded: 200 records per version, 189 baseline symbol correspondence entries and 390 explicitly omitted file/definition records per version. The baseline EvidencePackage serialized to 401,061 bytes, below its explicit 1 MiB limit. This is a source-evidence smoke result, not graph/impact qualification or a performance benchmark.

Final combined verification: **119 tests and 95 subtests passed**, with one opt-in native APFS test skipped in the unattended suite because its real mounted run had already passed and the mount was cleaned up. The 15 focused source-evidence cases are included in this result. Canonical proof validation and mixed-version rejection passed; resolved graph/impact qualification remains pending.
