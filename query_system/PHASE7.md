# Phase 7: evidence aggregation and validation

## Scope and compatibility audit

Phase 7 is standalone, synchronous, backend-owned packaging of a Phase 6
`ExecutionReport`. It performs no retrieval, traversal, rule execution, query
rewriting, inference, answer formatting or production API dispatch. Phases 1–6,
knowledge-base implementations, dependencies, stored user embeddings, root README,
and `/retrieve`/`/ask` remain unchanged. Existing uncommitted Phase 6 files were
preserved. Test/benchmark indexes are isolated temporary fixtures, not user indexes.

| Existing interface | Reuse / minimal extension |
| --- | --- |
| `query_system/contracts.py`: `Finding`, `GraphFinding`, `SemanticFinding`, `RuleFinding` | Original typed findings remain inside normalized wrappers; scores, methods, details and limitations are not rewritten |
| `query_system/contracts.py`: `Evidence`, `Evidence.from_trace`, `SnapshotContext`, `validate_engine_result` | Original canonical proofs; published generation and ready internal stage validation |
| `query_system/contracts.py`: `IntentRequirement`, `QueryEntity`, `OperationRequest` | Original ordered/repeated requirements, target hints and operation inputs retained |
| `query_system/execution_contracts.py`: `ExecutionPlan`, `ExecutionReport`, `Clarification` | Typed Phase 6 ownership/status checks, existing candidate shape and exact operation/requirement IDs |
| `query_system/router.py`: `operation_spec` | Validate already executed capabilities and parameters; no routing decision is changed |
| `knowledge_base/metadata.py`: `trace` | Saved hashes, line/byte bounds, canonical mappings, source origin/schema and index readiness |
| `knowledge_base/identity.py`: `file_id`, `relative_path` | Canonical repository/file identity and contained relative paths |
| `knowledge_base/synchronization.py`: `SnapshotStore.matches` | Whole saved-source digest check before and after packaging |
| Existing snapshot metadata | Parameterized SQLite `mode=ro`, `query_only` identity/endpoint/signature lookups; no graph traversal |
| `ai-engine/answers.py`: `evidence_for`, `answer_question` | Inspected but not invoked: the legacy source-range deduplication/clipping and model call cannot provide the new canonical package guarantees |
| `ai-engine/config.py`: `sanitize_text`; `ai-engine/indexer.py`: `_redact_secrets` | Optional caller-supplied excerpt filter can reuse existing redaction/sanitization, without invoking the answering pipeline |

Only new envelope models in `evidence_contracts.py` were necessary. No existing
contract fields or engine result schemas were changed. `evidence_aggregation.py`
consumes reports, produces packages, and verifies them in the executor's already
open reader scope. Its use of the Phase 6 `_active` flag is a lifecycle
compatibility dependency, covered by expired-scope tests.

## Normalization, source verification and deduplication

`EvidenceAggregator.from_executor(executor)` does not acquire another pin or read
the active-generation pointer. Keep that executor scope open until aggregation
finishes. Every accepted record belongs to its requested repository and published
generation; differing ready symbol/graph/vector stage IDs are allowed and retained.
Engine envelopes and declared evidence types are checked against their registered
operations. `trace` is re-run for canonical entity IDs; hashes, ranges, file/symbol
mapping, evidence kind, origin, schema and relationship resolution must match
exactly. Incorrect locations are rejected, not repaired or replaced with nearby
code. A whole-source change or final verification failure discards the package's
findings/citations, even if some earlier checks succeeded.

Graph claims additionally require matching canonical relationship types, endpoint
IDs/names and, for paths, an ordered contiguous resolved edge chain. Definition
proofs cannot establish dependencies. Known rule declaration fields are checked
against indexed signatures, flags and source metadata. Scope/location/path claims
must match their canonical nodes. Return/exception behavior unavailable to the
current declaration engine is not promoted into a verified runtime assertion.
No new graph traversal or rule algorithm is implemented.

Records deduplicate by the complete canonical proof identity/provenance (including
entity ID, repository, generation, stage, evidence type, file/symbol, hash, range
and resolution). Identical names/text, or merely overlapping ranges, do not merge
different entities. Separate chunks, definitions and relationship IDs retain their
own provenance. A single retained record can have multiple operation/requirement/
engine uses and supported finding IDs. Findings themselves are not collapsed
across independent requirements; semantic ranking stays in the original finding.

Source-located records say `source_verified`. Canonical structural metadata with
no source location says `structural_metadata_verified` and receives no fabricated
file, range, excerpt or source citation. Mandatory verification fields describe
checks at publication time, not a permanent guarantee after files change.

## Claims, coverage and conflicts

| Classification | Meaning / authority |
| --- | --- |
| `verified_source_observation` | Canonical declaration or code-chunk/source presence; semantic relevance is not verification of feature behavior |
| `verified_graph_relationship` | Resolved indexed static relationship, with matching endpoint/provenance checks |
| `derived_analysis` | Deterministic producer finding or bounded static path; retains producer limitations |
| `hypothetical_consequence` | Possible impact/review location; never authoritative runtime breakage |
| `unresolved_assertion` | Unresolved static reference or candidate; never authoritative target/relationship |

Only the first three classes are authoritative, within their explicit static/source
claim scope. `original` retains the original typed finding and classification;
the normalized classification never turns a hypothesis into an observation.
Producer warnings, rule limitations, unresolved items, errors and reasons remain
available. The Phase 6 global warnings are retained as `execution_warnings`
(execution history, not a claim about whether Phase 7 aggregation ran).

Every original requirement and admitted operation retains explicit coverage:
`satisfied`, `partially_satisfied`, `ambiguous`, `unsupported`, `not_found`, `stale`,
`error`, `skipped` or `deferred`. Original execution status is separate. Coverage
includes retained findings/records, missing evidence, limitations, truncation and
clarification needs. Repeated intents and distinct targets keep their IDs.
Target references retain labels, canonical-ID hints and scopes, but are marked
`input_only` and contain no duplicate unvalidated proofs; only the evidence registry
and validated findings supply authoritative source records. Clarification candidates
are retained only when their canonical proofs are validated and admitted.
Aggregation never selects candidates or runs a dependent operation.

Incompatible source/provenance or claim identity produces explicit conflicts and
quarantines the disputed canonical proofs. All claims sharing a disputed proof
are excluded; the aggregator does not choose one side. Unrelated valid claims
survive, including within a partly affected operation. Input envelope/repository/
generation inconsistency fails closed for the whole package. Failed/skipped
dependencies remain failures/skips, not empty success. An independent provider
failure is not hidden by another engine's evidence.

`status` represents coverage/result completeness. `validation_status` is about
the package validation: `valid` means retained proofs and provenance passed,
not that every requirement succeeded. A valid package can have partial, error,
ambiguous or deferred coverage. Conflicts/truncation yield `partial`; incompatible
execution envelopes yield `invalid`; expired pins/changed saved sources yield
`stale`. Consumers must inspect coverage/status as well as validation status.

## Budgets and citation map

Default strict `EvidenceLimits`:

| Limit | Default |
| --- | ---: |
| Canonical evidence records | 200 |
| Total included UTF-8 source context | 14,000 bytes |
| Findings per requirement | 25 |
| Relationship/path/list detail items per finding | 32 |
| Original finding serialized size | 16,384 bytes |
| Complete serialized package | 262,144 bytes |

Admission is deterministic: first-findings round-robin across requirements,
then optional context/candidate records. Output restores original finding order.
Oversized path/detail findings are omitted atomically, not cut into falsely complete
paths. Record caps never retain a claim without its complete proof set. Context
and overall-size omissions carry explicit events and downgrade affected successful
coverage. Serialized-size enforcement removes optional context first and then
whole findings with orphan proofs; no verified-but-unsourced claim remains.
If the mandatory requirement/operation/conflict envelope itself cannot fit,
`EvidenceBudgetError` requests a larger explicit budget instead of silently dropping
requirements. The byte limit covers `model_dump_json().encode('utf-8')`, including
coverage, diagnostics, citation metadata and input target hints.

Metadata-only is the default: no raw source excerpts are exported implicitly.
Optional `include_source_context=True` requires an explicit trusted filter (for
example existing redaction plus sanitization). The aggregator reads the exact
validated line range and includes the whole filtered excerpt only if it fits;
it does not clip a line and leave a misleading citation range. Canonical hashes
refer to original bytes, not redacted text. The filter affects excerpts only;
original findings/metadata remain untrusted data and need appropriate filtering
at any future prompt boundary. This phase builds no prompt and invokes no model.

Backend-only citation references have the form
`cm7:<package_id>:<record_id>`. Every map entry contains the exact retained canonical
source proof and its record ID; located supported findings receive references to
their own supporting records. Package validation rejects unknown/foreign/mismatched
references and dangling or changed locations. Structural records have no source
citations. Package/execution IDs exclude timing telemetry and derive from execution
content and limits; they are stable identities, not signatures or authentication.
No model-generated citation ID is accepted or generated. This opaque map is ready
for future RAG/formatter consumers, without implementing either consumer.

## Use within the existing pin

```python
from query_system.evidence_aggregation import EvidenceAggregator

with MultiEngineExecutor.pinned(store, channels) as executor:
    normalized = executor.understand(request)
    report = await executor.execute(executor.plan(normalized))
    package = EvidenceAggregator.from_executor(executor).aggregate(report)
    payload = package.model_dump_json()
```

Optional redacted context, with already-loaded existing AI-engine utilities:

```python
limits = EvidenceLimits(include_source_context=True)
filter_source = lambda text: sanitize_text(_redact_secrets(text))
aggregator = EvidenceAggregator.from_executor(executor, source_filter=filter_source)
package = aggregator.aggregate(report, limits)
```

The input is a backend-owned Phase 6 report, not arbitrary LLM-authored assertions.
Pydantic deserialization validates package shape/reference integrity, not current
disk provenance. Fresh provenance validation requires `aggregate` under a live pin.
Free-text rule meaning, runtime outcomes and logical contradictions between arbitrary
statements cannot be independently proved here; trust remains in the existing
deterministic producers and the explicitly recorded static-analysis limitations.
Verification uses synchronous filesystem/SQLite work and has no hard latency
guarantee. These limitations require no production change in Phase 7.

## Verification and benchmark

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_rules query_system.test_semantic query_system.test_understanding query_system.test_execution query_system.test_evidence -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run test:integration
.venv/bin/python -m query_system.benchmark_evidence --fixture --repeats 3
git diff --check -- .
```

The benchmark precomputes nine Phase 6 reports, then measures aggregation only,
with three repeats and one shared published pin. It includes verification and
serialized-budget enforcement, but excludes query normalization, graph traversal,
retrieval, fixture creation and external proof/citation recount. Four invalid
input probes measure generation/hash/range/symbol rejection separately. Fixed
query vectors and temporary indexed fixtures do not measure production retrieval
quality or establish a production latency guarantee. Saved metrics are in
`benchmarks/phase7_results.json`.

Created: `evidence_contracts.py`, `evidence_aggregation.py`, `test_evidence.py`,
`benchmark_evidence.py`, `benchmarks/phase7_results.json`, this report, and
`ai-engine/tests/test_phase7_evidence.py`. Updated: query-system README only.
No Phase 8 implementation or existing production behavior change was made.

Verified on 2026-10-13 in the project virtual environment: 30 query-system tests,
59 knowledge-base tests and 234 AI-engine tests passed (323 total, zero failures),
including 34 new Phase 7 tests. VS Code extension compilation and integration
passed with exit 0 and no reported skipped connection check. Tracked and new-file
whitespace checks passed. Existing SQLite resource warnings, Starlette deprecation
notices and intentionally mocked offline-provider logs remain unchanged.

Saved aggregation-only benchmark: 27 timed runs across nine workloads, under one
published pin, with median/p95 overhead 26.264/68.323 ms. All 153 retained proof
checks and 153 citation checks passed; all four invalid-input probes were rejected.
Requirement/operation coverage was preserved and repeated packages were identical.
Package size median/min/max was 16,371/6,166/80,858 UTF-8 bytes. These synthetic
metadata-only measurements are not real-model retrieval quality or production
latency guarantees; optional excerpt handling has separate integration tests.
