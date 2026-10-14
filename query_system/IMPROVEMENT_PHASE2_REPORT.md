# CodeMind improvement Phase 2 — runtime reliability

Date: 2026-10-14. Scope: Phase 2 of the post-audit improvement roadmap only.
Final classification is recorded after the verification table below. No Phase 3
qualification or production-default migration is included.

## 1. Initial baseline

The baseline was recorded before production edits in
`IMPROVEMENT_PHASE2_BASELINE.md`. The original QA report was supplied during
inspection as `REPO_AUDIT_REPORT.md` and was read without modification. Existing
Phase 1 work was uncommitted; its modified/untracked files were preserved.

| Current baseline | Actual result |
| --- | --- |
| Complete pytest suite | 456 passed, 585 subtests, two existing warnings; 176.40 s |
| Node resolver/presentation/session suites | 29 passed, zero skipped; 1.571 s |
| Phase 1 structural fix | Named regression and full detector matrix pass in baseline |
| Canonical Python | Root `.venv`, Python 3.14.6, pytest 9.1.1 |

The supplied audit's 806-test/Python 3.14.8 snapshot and its additional test files
are not the current checkout. No old count or coverage percentage is substituted
for a fresh measurement. Current Git root is the project directory; its actual
filesystem name ends with a space.

## 2. Files created and modified in Phase 2

| File | Phase 2 change |
| --- | --- |
| ai-engine/indexer.py | Account for hash/read/legacy graph failures and retry state |
| knowledge_base/metadata.py | Reject unreadable inventory; mark errored semantic stages failed |
| knowledge_base/synchronization.py | Reject incomplete parsing before publication; invalidate old partial manifests without unnecessary embedding regeneration |
| ai-engine/api.py | Roll back failed registration persistence; revoke session at shutdown/startup failure |
| knowledge_base/validation.py | Validate profile/worker reports and outputs; propagate worker/process/regression errors; include query-system regressions |
| extension/package.json | Offline `npm test` command using existing dependencies |
| extension/test/runtime.test.js | Eight new offline behavior tests |
| extension/src/hybridChat.ts | Validate repository/schema as well as package/generation on action replies |
| extension/src/responsePresentation.ts | Missing citation mapping displays unavailable text instead of a fabricated anchor |
| extension/src/serverManager.ts | Reject non-object JSON response envelopes |
| extension/test/runTest.js | Require an existing local VS Code runtime; never silently download one |
| ai-engine/tests/test_runtime_indexing.py | 14 isolated indexing/synchronization/recovery tests |
| ai-engine/tests/test_runtime_api.py | Nine API/security/job/session tests |
| ai-engine/tests/test_runtime_hybrid.py | Seven routing/preview/restart/legacy compatibility tests |
| knowledge_base/test_validation_runtime.py | 14 operational tooling tests |
| ai-engine/LEGACY_RAG.md | Entry-point/dependency/support map |
| ai-engine/rag_pipeline.py | Legacy-only module documentation; Phase 1 detector logic preserved |
| ai-engine/.env.example | Test-runtime documentation reflects offline refusal behavior |
| TESTING.md, extension/README.md, knowledge_base/VALIDATION.md | Offline/real-host commands, validation suites and local-runtime requirements |
| query_system/IMPROVEMENT_PHASE2_BASELINE.md | Pre-implementation evidence |
| query_system/IMPROVEMENT_PHASE2_REPORT.md | This completion report |

The root README, audit report, Phase 1 report, runtime dependency lists and all
pre-existing Phase 1 implementation/test files remain present. Phase 1's smoke
utility relocation is not a new Phase 2 deletion. No commit, publication or
environment/data cleanup was performed.

## 3. Indexing and synchronization reliability

Confirmed failures were reproduced before their fixes:

- A PermissionError/FileNotFoundError during hash classification was logged but
  returned `files_failed=0`. Hash failures now increment the existing failure
  count and record a failed semantic stage.
- Semantic inventory filtered out unreadable entries, including directories,
  allowing empty-looking success. Inventory errors now fail before semantic
  publication. Per-file read errors already counted failures, but the context
  manager still marked the stage ready; its recorded error flag now determines
  the stage outcome even when processing returns a summary normally.
- A malformed TypeScript file produced `parsing.partial=1` and a fresh, ready
  published generation. Incomplete/error parsing now prevents selecting that
  staging generation. The previous immutable database/pointer remain intact;
  failed staging data is retired by the existing collector and retry works.
- Existing manifests describing incomplete parsing are not fresh and cannot
  take the unchanged fast path. Valid cached vectors can still be reused while
  rebuilding graph/metadata authority; this does not force re-embedding merely
  because a previous parsing result was incomplete.
- Standalone legacy Python graph parsing can return zero nodes or raise while
  semantic chunks were written. Those failures now count as incomplete indexing,
  invalidate semantic readiness, and leave the file eligible for retry. A retry
  after a graph-service exception reuses unchanged valid embedding inputs.

Tests cover valid/empty/unsupported sources, invalid target/storage paths,
missing files at different processing stages, file/directory permission errors,
parser/vector failures, interruption, metadata loss and recovery. Incremental
tests add, modify, rename, delete and change multiple files, then assert no-op
publication and zero embedding calls for unchanged files. Graph failure retries
also assert reuse. Tests use private temporary repositories and FAISS stores.

Integrated synchronization verifies initial answer → source edit → publication
→ updated answer/hash/generation → deletion → rejection of previous citations.
Existing one-pin/internal-stage, concurrent-reader, collection retention,
mid-query generation-change and watcher-recovery suites remain passing checks.
No storage schema, embedding model, identity format or pipeline-version bump was
introduced, and no user's stored index was rebuilt.

## 4. API security and error handling

Two reproduced runtime defects were fixed:

1. Registry persistence failure returned 500 but left an in-memory registration.
   Registration now updates a copy and restores the previous entry on failure.
2. Lifespan shutdown retained session authority in process memory. The token is
   now revoked in `finally`, including startup/cleanup failures; watcher cleanup
   cannot leave the session active. Generated tokens still rotate normally on
   restart. Startup token files are not deleted as part of cleanup.

Negative tests exercise malformed JSON, missing fields, invalid repository IDs,
oversized questions, traversal filters, excessive filter counts/limits, invalid
retrieval/depth/generation/execution budgets, unknown hybrid fields/options,
missing/wrong/revoked tokens, unregistered repositories and unknown handles.
Legacy and hybrid validation retain existing safe `{error: ...}` responses.
Legacy extra-field compatibility is preserved; strict hybrid contracts reject
unknown options. This phase does not add a new HTTP body-buffering policy.

Job tests observe queued → running → completed, duplicate admission, failed
publication, underlying TimeoutError → existing `failed` state, safe error text,
retry eligibility, finished-job capacity eviction/404 and an empty job registry
after simulated process restart. Existing indexing/refresh and `/ask`, `/retrieve`,
hybrid/readiness/source/presentation contracts are exercised by regression tests.

Job storage remains process-local. There is no independent server wall-clock
watchdog or time-based job TTL: completed records are pruned by the existing
50-record retention policy, and process exit loses job status. The extension's
existing 15-minute polling timeout does not hard-cancel backend indexing. This
report does not describe an injected TimeoutError as proof of hard cancellation.
Persistent jobs or a hard cancellation/watchdog design require separate approval.

## 5. Knowledge-base validation tooling

Operational tests cover `run_case`, `main` and regression execution, including
invalid languages/sizes/types, CLI arguments, output paths/existing reports,
missing fixtures, quality failures, indexing/retrieval failures, malformed worker
JSON/shape/measurements/status, inconsistent exit codes, absent workers,
timeouts, failed regressions, missing test counts and temporary cleanup.
The full-path retrieval-failure test uses fake local embeddings and real private
storage, and verifies registration cleanup. Structural success remains partial.

The fixes validate programmatic profile bounds before work; reject file-valued
output paths safely; require a worker's identity, mode/status and measured gate
fields before accepting success; and convert invalid results into explicit
failed report rows. Regression launch/timeout/no-test failures cannot become a
passing gate. The regression runner now covers knowledge-base, AI-engine **and
query-system** suites, using canonical Python and local provider metadata.

A real CLI smoke run used `--sizes 0 --structural-only --skip-regressions` in
`/tmp/codemind-phase2-validation.Ckjg04`; it exited 0 with status **partial**.
Retrieval/citation/update gates were unmeasured, not passing. A second real CLI
run including all regression subprocesses is recorded in the final table.
No real user index, remote service or answer model was used by these checks.

## 6. Offline extension setup and real host tests

`npm --prefix extension test` (or `npm test` inside extension) compiles and runs
existing resolver/presentation/session tests plus eight new runtime tests. It
uses installed TypeScript/Node dependencies, mock VS Code APIs and loopback test
servers. It neither launches VS Code nor downloads dependencies.

New tests verify malformed/forged/untrusted messages, bad hybrid envelopes,
foreign-repository replies, expired handles, missing citations, canonical source
identity/paths/ranges/hashes, missing/changed/dirty source, Workspace Trust,
HTTP errors/disconnects/timeouts/primitives, and no-runtime Electron refusal.
Existing tests cover Quick/Standard/Deep output, ambiguity, unsupported/partial
answers, classification/uncertainty, citation mapping and escaping hostile text.

Four failures were reproduced and fixed: foreign-repository action replies could
replace the display; unknown citation IDs produced a `source-0` anchor; the HTTP
client accepted JSON null/arrays as successful envelopes; and the host runner
could invoke runtime acquisition when no local binary existed. The last behavior
is now an explicit not-executed error before invoking test-electron.

Real Electron suites remain in place. The installed local macOS VS Code binary
executed the Phase 9/10 suites successfully; final evidence is in section 11.
No new dependency or runtime was installed. The optional ordinary-engine check
was skipped because the user's normal backend was not running. The separate
real-source stabilization suite was not activated: no qualification root or
published user snapshot was supplied. Neither skip is reported as a passed test.

## 7. Router and presentation results

No Phase 5–9 routing, normalization, orchestration, contracts or Python
presentation implementation changed. Existing policy and failure tests plus
new integrated tests preserve the intent-to-engine mappings, repeated intents
with distinct targets, comparison operands, ambiguity, discovery candidates,
operation limits/deadlines and deterministic queries without Ollama.

A compound question is normalized, its definition operation deliberately fails,
its dependent callers branch is skipped, and an independent contract branch
retains verified evidence in a partial package/display. Tests also verify that
internal syntax/graph/semantic stage IDs are admitted only under the same pinned
published generation.

Malformed preview packets and missing evidence/citation mappings fail before
navigation, without echoing untrusted input. Valid preview packets support all
three depths; changed/deleted source invalidates all old reference authority.
Expired handles return 404; a new router/cache cannot recover an old token.
Existing model-failure tests preserve deterministic findings and valid citations
while marking unavailable/timeout/invalid-output generation and fallback status.
Generated prose remains non-authoritative; expired references are not clickable.

## 8. Locked README versus command scope

All eight manifest commands remain contributed and registered. Seven
are registered in `extension.ts`; preview registration is in `responsePreview.ts`
and called by activation. “Used” below means a reachable code/UI invocation,
not measured user adoption; no usage telemetry was collected.

| Command | Purpose and registration/functionality | Reachability and experimental status | Scope decision and evidence |
| --- | --- | --- | --- |
| codemind.openChat | Registered; opens chat, including Restricted Mode | Palette/status bar; ordinary chat, hybrid branch explicitly opt-in | In scope, F6; extension.ts and real host activation tests |
| codemind.checkConnection | Registered; authenticated `/hello` handshake | Palette; normal diagnostic; does not supervise/start engine | In scope, F6 support; serverManager.ts, runtime transport and host tests |
| codemind.selectRepository | Registered; registration/index/refresh flow | Palette/chat selection; requires trust for scanning | In scope, F1/F6; extension.ts, API indexing and host suites |
| codemind.searchCode | Registered; `/search/code` search and verified source opening | Palette/chat search action; normal feature | In scope, F2/F6; extension.ts, retrieval and source-navigation tests |
| codemind.checkReuse | Registered; calls `/analysis/reuse`, which currently returns `not_implemented` and no results | Palette plus default-enabled save-check path; no actual reuse analysis in current Python backend | Explicitly out of scope, README §3; extension.ts, api.py reuse stubs, registration/settings tests |
| codemind.openDocsOverview | Registered; requests `/docs/overview`, which currently returns 404 | Palette/chat Docs button; no generated overview in current backend | Explicitly out of scope, README §3; extension.ts, api.py documentation placeholders |
| codemind.syncDocs | Registered; calls `/docs/sync`, which currently returns no updates/overview | Palette/chat sync button; current backend is a no-op placeholder | Explicitly out of scope, README §3; extension.ts, api.py documentation placeholders |
| codemind.previewResponse | Registered; verifies exported packets using checkout Python, then permits only canonical navigation | Palette; explicitly Phase 9 developer preview, not standalone packaged runtime functionality | Requires clarification: useful F6/F7 diagnostic, but separate user-facing preview is not explicit in the locked demo; responsePreview.ts, preview CLI/offline/real host tests |

Recommendation requiring a separate owner decision: retain Docs/reuse explicitly
as approved experimental scope, or authorize removing/hiding commands and related
UI/save hooks. Do not remove only the manifest entries while leaving handlers or
automatic calls. Decide whether preview is a supported product feature or a
developer-only diagnostic. No command was removed, disabled or reclassified in
the product by this phase.

The locked stack also describes Qdrant, Python-only NetworkX structure and an
OpenRouter answer path, whereas the implemented modern path uses local FAISS,
JS/TS snapshot graphs and local Ollama. Those pre-existing architectural decisions
were not reversed or silently ratified here. README reconciliation needs explicit
owner approval separately; this phase preserves the supplied current architecture.

## 9. Legacy compatibility map

`ai-engine/LEGACY_RAG.md` records every discovered entry point:
`rag_pipeline.main → ask_codemind`, direct `ask_codemind` import, standalone
indexer/NetworkX graph updates, read-only `graph_query` CLI, snapshot-aware search
CLI, and the separately gated manual OpenRouter utility. Normal `/ask` uses
`answers`; hybrid uses existing orchestration/GraphRagGenerator/LocalOllama.
Neither dispatches the legacy NetworkX answer function.

Compatibility tests make legacy answering raise if modern routes accidentally
call it, and separately verify that managed/invalid snapshots refuse legacy
retrieval/model execution. Phase 1 structural detection and its exact tuple
contract still pass. No NetworkX graph was merged into the modern pinned graph,
no second answering pipeline was added, and no retained CLI was deleted.

Standalone legacy indexing is not an atomic modern publication API; errors are
now explicit, but protection of published generations is supplied by SnapshotStore.
Use registered-repository indexing for the normal application. Deprecation here
documents unsupported snapshot mixing; it does not remove old functionality.

## 10. Virtual environment audit

| Exact path relative to this project | Observation | Action/savings |
| --- | --- | --- |
| .venv | Present; 477,056 KiB allocated, about 466 MiB; canonical Python 3.14.6 | Keep; used by launcher, tests and preview/host child processes |
| ai-engine/venv | Absent | No removal candidate; zero reclaimable bytes verified |
| extension/venv | Absent | No removal candidate; zero reclaimable bytes verified |

References were checked in launch/test scripts, package configuration and docs.
The root locked README still has an older generic `venv` quick start; current
launcher, TESTING.md and setup docs use root `.venv`. The audit's historical
495 MB potential cleanup cannot be claimed on the current disk.

No destructive command was executed. If redundant environments reappear, first
resolve exact paths, inspect pyvenv.cfg and script references, inventory unique
dependencies and local artifacts, verify the canonical launcher/tests, measure
each directory, then obtain approval to archive or remove those specific paths.
Never delete `.venv` or assume an audit's old path still identifies expendable data.

## 11. Integrated verification

| Final check | Actual result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **500 passed**, 675 subtests passed, two existing warnings; 199.48 s |
| Validation runner: knowledge-base unittest subprocess | **76 passed**, exit 0; 7.032 s |
| Validation runner: AI-engine unittest subprocess | **361 passed**, exit 0; 188.246 s |
| Validation runner: query-system unittest subprocess | **63 passed**, exit 0; 0.086 s |
| `npm --prefix extension test` | **37 passed**, zero failed/skipped; Node test duration 1.582 s; TypeScript compile succeeded |
| Local VS Code `test:integration` | **Exit 0**; actual Phase 9 preview/navigation and Phase 10 authenticated hybrid/editor suites printed PASS |
| Final indexing-focused regressions | **14 passed**, six subtests; 4.57 s |
| Expanded runtime plus Phase 1 detector selection, before last two standalone tests | **50 passed**, 403 subtests; 10.49 s |
| `git diff --check -- .` | Passed |

The real validation runner used `--sizes 0 --structural-only` with regressions
enabled, writing `/tmp/codemind-phase2-tool-regressions.YcNsxG/quality-report.json`
and Markdown/logs. Its JS/TS cases are **partial** and all three regression gates
passed (500 tests total). Exit 0 means no measured gate failed; it does not mean
embedding/retrieval/citation/update quality was measured by a structural-only run.

The final pytest suite includes all AI-engine, knowledge-base, query-system,
stabilization, router/presentation and new runtime tests without weakening
assertions. Both original and final suites retain their existing warnings.

Raw logs: `/tmp/codemind-phase2-baseline-python.log`, `-baseline-node.log`,
`-indexing-before.log`, `-indexing-after.log`, `-api-before.log`, `-api-after.log`,
`-validation-before.log`, `-validation-after.log`, `-extension-before.log`,
`-extension-after.log`, `-all-new-focused.log`, `-indexing-final-focused.log`,
`-legacy-index-before.log`, `-final-verified-python.log`, `-final-node.log` and
`-final-vscode.log` (same `/tmp/codemind-phase2` prefix). Validation-runner logs
are also retained in its private output directory. Failed pre-fix logs are
diagnostic reproduction evidence, not the final suite outcome.

The five required integrated scenarios are exercised by runtime indexing tests
(A), compound failure and existing execution suites (B), existing Phase 8/10
model-unavailable/invalid-output fallback tests (C), offline and actual editor
navigation tests (D), and API/cache/session restart tests (E). Fixtures are
disposable and model replies/failures are mocked; no production qualification
claim is based on them.

## 12. Performance and coverage review

Phase 2 adds **44 Python test methods** and **eight Node tests**. It targets
operational branches rather than a coverage percentage: unreadable discovery,
partial parsing, semantic readiness, failed publication/retry, cached-vector
reuse, registry rollback, session teardown, job lifecycle, CLI/report failure
propagation, wire envelopes and source authority. Existing assertions remain.

Before the last two standalone-index regressions, a full run passed 498 tests,
675 subtests, in 180.20 s; final results are above. Timings are local regression
observations with compilation, filesystem activity and isolated process overlap,
not application latency/load/indexing budgets. No real-corpus accuracy benchmark,
coverage instrumentation dependency or new performance target was introduced.
No claim is made that the audit's historical 87.2% coverage increased by a specific
amount. Exact unchanged-source embedding assertions, not timing, establish reuse.

## 13. Remaining limitations

- Jobs and answer handles remain process-local; hard indexing cancellation,
  persistent jobs, time-based job expiry and multi-process recovery are not added.
- Real-source stabilization/production-corpus/model-quality qualification was not
  rerun. Optional normal-engine connection checks can skip without that engine.
- Static parsing failures block a new managed publication until source/parser
  recovery; the last valid snapshot remains stored but stale evidence is refused.
- Missing repository/license metadata still affects plain VSIX packaging. The
  already verified base-URL packaging workaround is retained in TESTING.md.
- Existing Starlette/httpx and embedded-Qdrant payload-index warnings remain
  visible. No dependency migration was attempted to suppress them.
- In-process pytest network guards do not constitute an operating-system network
  sandbox. Fixture subprocesses use local/fake services and private data; no
  remote model was invoked. No source was sent to an external service.
- Scope/stack mismatches and documentation/reuse placeholders are documented,
  not implemented or removed under a runtime-reliability request.

## 14. Separate approval decisions

Owner decisions remain for README/product scope reconciliation, removal or
promotion of Docs/reuse/preview features, retirement of legacy CLIs, persistent
jobs or a hard cancellation design, and any future dependency/runtime installation
or environment removal. None was necessary to implement the verified Phase 2
corrections. No new dependency, command removal, locked README edit or cleanup
was performed while waiting for those decisions.

## 15. Readiness assessment

**Complete with documented limitations.** All applicable mandatory regression
checks pass. Confirmed indexing/publication, registry/session, validation-tool and
extension failure-path defects are fixed. API contracts, pinned-generation/source
authority, embedding/schema formats, existing functionality and default-off
hybrid mode are preserved. Offline tests have one reproducible command; actual
VS Code integration remains available and passed on the installed runtime.

Locked README/scope decisions, process-local recovery limits and unexecuted
real-source qualification remain explicit above. No new dependency, removal of
registered commands, virtual-environment deletion, user-repository modification,
published-snapshot rewrite or production migration was performed. No Phase 3
work was started.
