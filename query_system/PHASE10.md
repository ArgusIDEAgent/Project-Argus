# Phase 10 — end-to-end integration and controlled rollout

## Status and qualification

**Development-ready, default off.** The opt-in workflow passed authenticated
loopback API + actual VS Code host verification, including editor navigation and
rollback. **Full Phase 10 qualification remains partial**, not production-ready:
the existing knowledge-base TypeScript resolver fails to publish the target-sized
real corpus. A real configured Ollama call timed out and fell back safely.
No independent human factual/readability review or general real-repository quality
gate has passed. No default migration, parser/router redesign or graph repair was
performed. These limitations must not be hidden by the synthetic test results.

## Modified-file inventory

New production integration files:

- `query_system/hybrid_contracts.py`: versioned integration envelopes/budgets/telemetry.
- `query_system/orchestration.py`: bounded composition of existing Phases 5–9.
- `ai-engine/hybrid_api.py`: gated routes and bounded server-owned answer handles.
- `extension/src/hybridChat.ts`: opt-in chat session, scoped editor context/actions.

Modified production integration surfaces:

- `ai-engine/api.py`: include the new router only; legacy implementations untouched.
- `extension/src/serverManager.ts`: typed authenticated loopback hybrid methods.
- `extension/src/extension.ts`: gated chat branch, opaque action delegation/rollback.
- `extension/src/responsePresentation.ts`: reuse renderer, optional title/anchor prefix;
  Phase 9 defaults unchanged.
- `extension/package.json`: three settings only; no new dependencies.

Tests/evaluation:

- `ai-engine/tests/test_phase10_integration.py` (new).
- `ai-engine/tests/phase10_server_fixture.py` (new isolated HTTP fixture).
- `extension/test/hybridSuite.js` (new actual extension-host verification).
- `extension/test/suite.js` (invoke Phase 10 after existing Phase 9 suite).
- `extension/test/presentation.test.js` (escaping/IPC/default-setting regressions).
- `query_system/benchmark_hybrid.py` (new reproducible evaluation harness).
- `query_system/benchmarks/phase10_mocked_results.json` (new measured artifact).
- `query_system/benchmarks/phase10_real_results.json` (new successes AND failures).

Documentation: new `PHASE10_INTEGRATION_AUDIT.md` and this report; scoped
`query_system/README.md` and `extension/README.md` additions.
The locked root README, dependencies/lockfiles, stored user indexes, graph builder,
retrieval/indexing and Phase 5–8 implementations are unchanged.

## Compatibility map and architecture

| Boundary | Existing interface used directly |
| --- | --- |
| Request → understanding | Phase 1 `QueryRequest`, `MultiEngineExecutor.understand()` |
| Understanding → routing/execution | Phase 5 `NormalizedQuery`, Phase 6 `plan()` / `execute()` |
| Execution → accepted evidence | Phase 7 `EvidenceAggregator.from_executor().aggregate()` |
| Evidence → optional explanation | Phase 8 `GraphRagGenerator.generate(GenerationRequest, package)` |
| Accepted results → display | Phase 9 `format_response()` / `PresentationResult` |
| Display → chat | Existing `renderResponseBody()` + `ServerManager` loopback transport |
| Source click → editor | Existing Phase 7 canonical verifier + Phase 9 `openVerifiedPreviewSource()` |

Entry point: `await run_hybrid(store, HybridQueryRequest, channels, answers)`.
`existing_api_channels(api, config.EMBEDDING_MODEL_NAME)` binds existing keyword,
vector and reciprocal-rank fusion retrieval to the executor's published generation.
The question acquires **one `SnapshotStore.pin_view()` through executor.pinned**.
Understanding, planning, operations, aggregation, generation and final validation
all use that authority. Different ready indexing-stage IDs remain legitimate;
repository and published generation IDs must match everywhere.

Phase 6 deliberately defers explanation intents. Integration appends a bounded
projection of existing definitions, contracts, caller/callee and workflow-path
requirements, preserving original requirements first and unchanged. Equivalent
resolved support is deduplicated; already requested compatible deterministic work
is reused. The existing 12-requirement cap applies to the combined support plan;
admission omissions are explicit. This does not modify Phase 5 or Phase 6 policy.
Original dependencies on deferred requirements remain blocked, not silently rerouted.

Explanation requirements are grouped by existing function/workflow/behavior/
comparison task type. Each group has explicit original-to-support bindings and
group-local consecutive orders. Display composition restores original identities,
dependencies, failed branches and one unified Phase 7 citation namespace. It adds
no factual statements. Equivalent work can share one call; mixed task types can
use separate bounded calls. Successful independent deterministic branches survive
model failure. Depth changes use cached projections only, never new retrieval/LLM.

Final validation checks canonical proofs, current source digest and the active
generation again. A publication during the question invalidates release, even if
the old immutable generation still matches identical source bytes. Stale current
answers have no released items. No model tool/file/command access exists.

## Versioned API and budgets

All routes inherit existing session authentication and loopback TrustedHost checks.
Only registered canonical repository IDs are accepted; root paths, client-provided
proofs, arbitrary models and tools are not part of this API.

| Route | Contract and behavior |
| --- | --- |
| `GET /v1/hybrid/readiness?repoId=...` | enabled flag, current published snapshot/stages, optional model configuration; not a model-installation/liveness guarantee |
| `POST /v1/hybrid/query` | `HybridQueryRequest` → strict `HybridResponse`, schema `hybrid-v1` |
| `POST /v1/hybrid/presentation` | repository ID + opaque navigation token + depth → revalidated cached Phase 9 projection |
| `POST /v1/hybrid/source` | same handle + package-owned citation reference + depth → presentation + approved canonical citation or null |

Minimal query JSON:

```json
{"query":{"repository_id":"<registered 24-hex ID>","question":"Who calls login?","depth":"standard"},"allow_generation":true}
```

`query` may include the existing pinned-generation/editor/explicit-symbol context
fields. `limits` reuses existing execution, evidence and generation budgets;
overall deadline defaults to 60 seconds (1–120 seconds), maximum generation attempts
defaults to 2 (0–4). Query length is capped at 2,000 characters. Internal stage
timings and operation/support diagnostics are returned. Result states retain
`success`, `partial`, `ambiguous`, `not_found`, `unsupported`, `stale_index`, `error`;
clarification candidates and requirement states remain in the Phase 9 presentation.
HTTP validation/auth/registry/gate/admission errors remain 422/401/404/503/429.

Two concurrent query/display/source validations are admitted; other requests get
429. Cache: 32 answers, 8 MiB serialized-size admission estimate, five-minute TTL,
process-local only. Cached data is deep-copied and bound to repository and canonical
root. Tokens are not authentication replacements. Restart/eviction/expiry requires
asking again. The extension retains at most eight answer action registries.
Cache limits describe serialized bytes, not exact Python heap consumption.
Deadlines are cooperative: synchronous HTTP, hashing and preparation may outlast
the requested budget; this is not a hard-cancel or isolation guarantee.

## Enablement, readiness and rollback

From the project root:

```sh
CODEMIND_ENABLE_HYBRID=1 .venv/bin/python start_codemind.py
npm --prefix extension run compile
```

Use the existing F5 Extension Development Host or existing packaging workflow.
Set `codemind.hybridEnabled: true`, open CodeMind Chat, select a trusted repository
and wait for a current published snapshot. Both backend gate and extension flag
must be enabled. No automatic model pulls or index rebuilds occur in hybrid query
execution. The already configured, installed local completion model is optional
for deterministic work; explanations use only the existing loopback Ollama bridge.
Missing/unavailable/malformed/timeout responses fall back under Phase 8 validation.

`codemind.hybridDepth` defaults to `standard`; `codemind.hybridAllowGeneration`
defaults to true only within opt-in mode. Search/reuse/docs retain old routes.
`checkReuseOnSave` and other saved settings retain their meanings/defaults.
Named questions remain repository-wide. Editor context is sent only for deictic
`this`/`here`/`current`/`selected` questions: Phase 5 treats active_file as a scope,
not a weak hint. Unsaved editor text is never sent; cursor/selection ranges are
omitted for dirty documents. Unsupported contextual wording may need clarification.

Rollback: set `codemind.hybridEnabled: false`. Cached frontend actions are cleared
and old hybrid controls disabled; the next chat question uses the old `/ask` route.
Optionally restart without `CODEMIND_ENABLE_HYBRID=1`. No files/indexes/settings are
deleted. Model failure does not automatically retry against legacy generation;
fallback is explicit, and legacy mode is available through the flag.

Troubleshooting: 503 → enable backend gate or disable extension flag; missing
snapshot → existing synchronization/indexing must succeed first; 404 handle → ask
again; stale source → refresh/wait and ask again; 429 → retry after active bounded
work finishes; model timeout → deterministic fallback or disable generation.
Readiness verifies snapshot availability, not general repository quality or model
daemon/installation health. Target-sized resolver failure needs separate KB repair.

## Verification and security

The new integration suite covers all 20 requested scenario families using known
ground-truth JS/TS fixtures: nine deterministic operations, four explanation
types, compound routing, ambiguity/missing/unsupported, partial execution,
insufficient evidence and unavailable/timed-out generation. Tests assert engine,
operation and call counts, not just nonempty answers. Additional checks cover:

- One pin, legitimate stage differences, wrong requested generation, active
  publication/source changes during generation, missing metadata and final fail-closed release.
- Equivalent batching, mixed-task composition, explicit generation opt-out/call
  budget, pure depth projections, preservation of independent engine successes.
- Unknown/cross-repository/expired handles; invalid client path/hash/model/tool
  fields; changed/deleted/symlink source navigation; oversized source exclusion.
- Secret redaction and repository-comment prompt injection kept as untrusted user
  data, invented citations/malformed JSON/offline/timeouts rejected to fallback.
- Bounded concurrency and evidence/cache admission; HTML escaping, per-answer
  anchors and actual webview opaque-action delegation/disabled rollback controls.

Existing Phase 7/8 regressions also cover mixed repository/generation envelopes,
forged citations, source redaction failures, graph-claim validation and injection
boundaries. Renderer regressions cover hostile text in all display fields. This
is automated security coverage, not a penetration-test certification or proof
that every adversarial documentation phrasing has been tested. No repository
scripts/commands execute as a consequence of retrieved/model text.

The actual VS Code host test runs an isolated ephemeral-port authenticated backend
with real retrieval, executor, aggregation, formatter and editor opening. Fixed
vectors and a controlled Phase 8 model producer isolate transport/provenance tests.
It exercises default-off legacy transport, saved settings, deterministic no-model
queries, explanation transport, all depths without extra query/model calls,
unauthorized actions, canonical navigation, stale controls and rollback. Separately,
the actual chat JavaScript is executed in a small DOM harness for action forwarding.
This is not a human UX study or a claim that VS Code's full DOM was browser-automated.

Regression commands (project `.venv`, no new dependencies):

```sh
.venv/bin/python -m unittest discover -s query_system -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run compile
node --test extension/test/presentation.test.js
npm --prefix extension run test:integration
git diff --check -- .
```

Shared-contract suite: 50 passed. Knowledge-base suite: 59 passed. Final full
AI-engine suite: **302 passed**, including 27 new Phase 10 tests and existing
Phase 0B–9/AI regressions, in 150.080 seconds. **411 unique Python tests passed,
zero failed** across the three suites.
Renderer/IPC: 17 passed, zero failed. TypeScript compilation and actual Phase 9/10
VS Code integration passed. Expected injected-offline logs, Starlette deprecation
and existing SQLite ResourceWarnings were observed; no dependency changes made.

## Real-source evaluation and grounding

See `benchmarks/phase10_real_results.json` for unfiltered case outcomes/failures.
Copies are inert text from this actual checkout, excluding dependencies, generated
files, sensitive files and result artifacts. All indexing is temporary/private;
the only reused user resource is the public cached BGE model, loaded local-only.
No original repository/index is republished. Three corpus attempts:

| Corpus | Coverage and outcome |
| --- | --- |
| Small source-navigation component | 3 actual files, 8,316 bytes; TS + docs/config; 11 labeled queries, 46/46 citation checks valid |
| Original target-sized CodeMind copy | 151 actual files, 1,677,469 bytes; publication **blocked** by existing TypeScript resolver `decl.parent` failure |
| Explicit multilingual diagnostic subset | 110 files, 1,410,131 bytes; TS/Python/Markdown/JSON, 21 docs/config files; other JS/TS removed only to isolate blocker; 17 labeled queries, 67/67 citation checks valid |

The diagnostic subset is **not** a target-sized or ambiguous-JS-name pass. It has
only two canonical graph symbols, 18 unresolved relationships and 2,301 retrieval
chunks (1,929 Python, 256 Markdown, 113 JSON, 3 TS). Requests for excluded symbols
correctly remain missing, not invented comparisons/paths/callers. Real ambiguous
graph-name qualification remains blocked; ambiguity behavior is verified only in
ground-truth fixtures. Corpus includes test/benchmark text and related labels;
retrieval-quality generalization must not be inferred from these diagnostics.

Recorded raw intent matches: 10/11 small, 16/17 diagnostic. The unsupported
statistics family is intentionally not a registered Phase 5 intent (normalizes
UNRESOLVED); the integration reports unsupported with zero operations/model calls.
Operation matches: 10/11 and 14/17 respectively. Natural feature phrasing containing
"source file" unexpectedly produces ambiguity instead of semantic execution.
The alternative documentation feature query returns source-valid candidates but
misses the excluded expected file; citation validity alone is not feature recall.
These are capability/qualification gaps, not successful quality metrics.

Agent source review of `verifiedSourcePath` at lines 12–31 confirms containment,
SHA-256, regular-file/size and missing/changed-source checks, and direct `isInside`
calls. Accepted static findings describe only recorded declarations/relationships;
external API/dynamic calls remain unresolved and partial, never guessed. Display
labels keep static relationships, relevance and model interpretations distinct.
This is source inspection, not independent human-reviewed factual accuracy.
Readability is automated coverage/escaping/template verification only; numerous
inherited caveats and duplicated fallback/support details can be verbose.

One real local `llama3:latest` attempt on this real component timed out at roughly
30 seconds (31.873 seconds full pipeline), yielding explicit deterministic
evidence-summary fallback; 3/3 additional citations verified. No generated factual
accuracy score is available. Human prose review and successful real-model
explanation quality remain unmeasured. No remote model, pull or provider change.

## Latency and memory evaluation

`benchmark_hybrid --repeats 20` measured 120 fresh-question runs (one pin each)
across six workloads; 500/500 citation checks valid. Full per-stage medians/p95,
response sizes, truncation and fallback counts are in
`benchmarks/phase10_mocked_results.json`. Measurements include final provenance
verification, but exclude fixture preparation/indexing and actual HTTP/VS Code
rendering. The host also ran other verification work: contention affects these
numbers. Fixed vectors and mocked model are **not** production performance estimates.

| Workload | Total median / empirical p95 ms | Max response bytes | Mock model attempts across 20 runs |
| --- | --- | --- | --- |
| Graph | 665.468 / 1113.993 | 6,403 | 0 |
| Semantic | 629.956 / 1010.821 | 11,567 | 0 |
| Rules | 304.185 / 409.547 | 7,160 | 0 |
| Multi-engine deterministic | 413.529 / 478.314 | 11,190 | 0 |
| Explanation | 853.048 / 1055.961 | 17,731 | 20 |
| Compound | 881.037 / 1042.291 | 19,365 | 20 |

Twenty samples give a coarse empirical p95, not a stable SLA. Fallback/truncation
rates were 0 on these admitted positive protocol workloads; negative probes and
real timeout are separate. Rule/explanation partial statuses reflect inherited
static scope/coverage, not hidden benchmark failures. Stage telemetry separates
understanding, planning, execution, aggregation, LLM preparation/model/total,
formatting, final validation and total time; stages need not add exactly because
generation-total includes post-validation and pin/setup overhead is in total.

Peak traced Python allocations: 3,457,898 bytes; process peak RSS: 159,744,000 bytes
including imports/native runtime. Tracemalloc perturbs timing and excludes native
allocation. Real index process was observed around 1.2 GiB RSS while embedding;
that is an observation, not a per-query memory benchmark. Ollama/GPU memory is
not measured. Real-query cases have single samples, so no real-corpus p95 claim.

Reproduce with:

```sh
.venv/bin/python -m query_system.benchmark_hybrid --repeats 20
.venv/bin/python -m query_system.benchmark_hybrid --real-repositories --real-local
```

The second command can take minutes and intentionally records existing publication
failures rather than bypassing the graph builder or substituting a legacy graph.

## Remaining gaps and required approvals

1. **Target-sized/real ambiguous-name qualification:** existing resolver crashes
   at `knowledge_base/semantic_resolver.cjs:99` because `decl.parent` is undefined.
   Repair requires a separate knowledge-base scope approval; no repair is included.
2. **Understanding coverage:** mixed "and ... and" comparisons can lose an endpoint;
   use `Explain \`login\`; compare \`login\` versus \`entry\``. Feature phrasing with
   file terminology can produce clarification instead of semantic execution. Parser
   expansion requires a separately scoped Phase 5 change.
3. **Local-model quality/performance:** successful real explanations, independent
   factual review, broad feature recall and representative real workload p95/memory
   remain gates before opt-in qualification beyond suitable current snapshots.
4. Existing JS/TS-only graph resolution, incomplete dynamic/external relationships,
   cooperative cancellation, support/operation caps and short-lived handles remain
   visible limitations. No statistics engine, remote tools or generalized agent.
5. Production-default migration requires explicit separate approval after the real
   gates pass. This work enables development opt-in; it does not claim full Phase
   10 completion where the above verification requirements remain blocked.
