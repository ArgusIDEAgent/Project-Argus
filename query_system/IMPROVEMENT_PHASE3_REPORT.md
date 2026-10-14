# CodeMind improvement Phase 3 — real-world release qualification

Date: 2026-10-14. Classification: **Development-ready**.

Phase 3 implementation and measurement are complete with failed release gates.
This is not an opt-in/production approval: accepted workflow prose contains
source-contradicted assertions, warm-model p95 exceeds the approved budget,
comparison explanations remain unqualified, and independent human review is
pending. No production-default rollout was performed.

## 1. Baseline

Inspected the supplied original `REPO_AUDIT_REPORT.md`, Phase 1/2 reports, existing
stabilization reports/tools, current dirty working tree, actual implementation
and installed tools. The baseline preserves all Phase 1/2 work and the supplied
audit. Current checkout is `main`, baseline HEAD `a62f743`; its directory name
ends in a literal space. The old audit's different test count is not substituted.

Fresh baseline: **500 Python tests, 675 subtests passed**, two existing warnings,
183.45 s; **37 Node tests passed**, 1.765 s, including TypeScript compilation.
Installed VS Code executed the Phase 9/10 host suites, exit 0. Its optional
ordinary-engine connection smoke skipped because the user's normal engine was
not running; this was not counted as a passing live-engine check.

## 2. Compatibility boundaries

Preserved Phase 0–10 architecture, public API/schema/storage formats, 384-vector
dimension, `BAAI/bge-small-en-v1.5`, pipeline version, local Ollama inference,
provenance, redaction and legacy entry points. Root locked README, runtime
dependency lists and extension contribution/default settings are unchanged.
Both hybrid gates remain default-off. No new dependency/model/provider, external
corpus download, user-index rebuild, command removal, commit or deployment.

`/ask` uses the current local Python answer path; opt-in `/v1/hybrid` composes the
existing normalizer, graph/rules/semantic engines, generator and formatter. The
retained Node backend is an inert CommonJS qualification corpus here, not a
replacement launch path. Historical OpenRouter/NetworkX utilities were not run.

## 3. Phase 3 files

- `query_system/query_understanding.py`: narrow return-contract and descriptive
  ML-question routing corrections.
- `query_system/graph_rag.py`: compact prompt metadata without removing canonical
  package/context authority, preserve required excerpts, concise task guidance.
- `query_system/local_ollama.py`: schema coverage cardinality guidance; backend
  distinct-order/grounding validation remains mandatory.
- `query_system/test_phase3_understanding.py`,
  `ai-engine/tests/test_phase3_qualification.py`: parsing, schema, bounded real
  workflow context, exclusive evidence output, local model reuse and timeout
  review-packet regressions.
- `ai-engine/tests/stabilization_live_server.py`,
  `extension/test/stabilizationRealSuite.js`: actual-host scenarios and explicit
  test-only model/graph fault injection; no production failure switch.
- `query_system/qualify_phase3.py`, `query_system/assess_phase3.py`: reproducible
  local-only measurement, resources, source labels, model bodies and review packet.
- `query_system/benchmarks/phase3*`, `PHASE3_REPRODUCTION.md`: immutable input/run
  versions, raw results, independent assistant claim review and reproduction.
- This report. Pre-existing Phase 1/2 changes remain separate and preserved.

## 4. Hardware and real repositories

Apple M4, 10 CPU cores, 16 GiB unified memory; macOS 27.0; Python 3.14.6,
Node 26.7.0, TypeScript resolver 5.9.3. Installed VS Code and cached embeddings
were reused. Only configured installed `llama3:latest` was used; digest
`365c0bd3c000a25d28ddbf732fe1c6add414de7275464c4e4d1c3b5fcb5d8ad1`.
Other installed models were not selected, replaced, downloaded or unloaded.

| Frozen profile | Files / physical lines | Bytes | Initial chunks | Publication |
| --- | ---: | ---: | ---: | ---: |
| CodeMind extension source/config/docs, small | 9 / 1,720 | 96,497 | 266 | 16.39 s |
| StudyTrackerWeb React frontend | 5 / 2,142 | 70,814 | 123 | 7.76 s |
| CNN crack-detection Python source/README | 2 / 448 | 13,617 | 21 | 1.60 s |
| Retained CodeMind Node backend | 16 / 2,719 | 142,521 | 505 | 30.68 s |
| Mixed CodeMind target, fresh process | 190 / 36,338 | 2,086,542 | 4,063 | 269.42 s |

Physical lines include documentation/configuration/lockfiles, not just code LOC.
An inventory/hash-matched code-only lower bound is **152 unchanged copied source
files / 27,931 lines**, excluding the three qualification tools edited later.
Excluded dependencies, generated/binary files, secrets, datasets and model
weights are not inflated into repository counts. CNN training and copied
application code were never executed. No local Next.js corpus was qualified.
Larger/enterprise and concurrent-user scales remain unmeasured after observed
target/resource failures; this is not an all-framework support claim.

## 5. Language/capability matrix

JS/JSX/TS: modern syntax/symbol/graph/static rules and semantic retrieval measured.
CommonJS Node and ESM/namespace TS imports exercised against real source; existing
resolver regression suites retain additional alias/dynamic/ambiguous cases.
React JSX indexing and real frontend queries pass. Python/CNN: real semantic
chunks/retrieval/provenance pass; modern Python structural graphs, algorithm
extraction and free-form ML explanation are not implemented/qualified. C, Java,
notebooks, Next.js-specific behavior and other languages are not claimed.

The CNN source independently shows image size 128, batch 32, epochs 15, seed 42,
Conv2D filters 32/64/128/256, Adam/binary-crossentropy and an adaptive learning-rate
callback. These are source facts, not executed accuracy or evidence of a
hyperparameter-search experiment. Retrieval alone cannot make that explanation.

## 6. Scan, synchronization and failure safety

All measured profiles published complete private snapshots with source inventory,
symbols/imports/graph and real cached-model embeddings. Every graph/chunk proof
was checked through the existing canonical trace/hash verifiers. For example,
the extension has 1,067/1,067 canonical graph/source checks and 266/266 chunk
checks; the full target separately checks every stored chunk, not only hits.

Real-copy no-op, edit, rename and delete checks pass: no-op embeds zero chunks
and preserves generation; edits reject old source authority; renamed/deleted
evidence disappears. The target edit embedded one changed chunk and reused
unchanged chunks. Additional real Node publication uses a labeled synthetic add,
malformed edit, recovery and deletion to exercise failed staging and last-valid
generation retention; results are in `phase3_node_final/node.json`.

Original React/CNN/Node bytes are unchanged. The target's original-source
comparison correctly detects this task's concurrent authorized edits to its own
qualification tools/tests; those are not writes by the benchmark into source.
The frozen private corpus remains unchanged during questions. No user registry,
published snapshot or index was an indexing/repair target. Private staging data
and copies were cleaned by their owning TemporaryDirectory contexts.

## 7. Ground truth and understanding

`phase3_cases.json` contains **41 source-inspected developer questions**, families
A–K: location, calls, discovery, contracts/explanation, workflow, impact/tests,
comparison, ML/configuration, behavior, unsupported/ambiguous and compounds.
Frozen annotations retain repository identity, source SHA-256, anchor lines,
expected rows/targets/operations/facts, capability limits, LLM need and difficulty.
These labels came from reading source, not generating truth from CodeMind output.

Measured: intent **41/41**; exact requirement/operand rows **41/41**; canonical
target resolution/path **34/34**; comparison operands **4/4**; original requirement
counts **40/40** (statistics has a separate unsupported guard). No canonical
target was selected by semantic similarity. These small, correlated development
denominators do not establish ≥98% held-out deterministic accuracy.

## 8. Routing, ambiguity and planning

Expected engine/operation sequence **31/31**, semantic routing **6/6**, ambiguity
**2/2**, unsupported guards **2/2**, nontrivial inherited dependency plan **1/1**.
Compound targets and original rows are retained, including comparison plus tests
for both operands and independently targeted requests. The one dependent-plan
case is not adequate statistical coverage of arbitrary dependency planning.

The actual preparation/LLM-necessity decision is instrumented with an explicitly
injected unavailable transport: **39/41**. `G1` and `K4` need explanations but
cannot fit complete comparison target evidence within existing limits; the model
is correctly withheld, leaving a capability/completeness failure rather than
invented synthesis. This probe is not real-inference quality evidence. Adding
deterministic cases does not erase those two explanation failures.

## 9. Independently labeled graph accuracy

Three complete scoped direct-call labels from inspected extension navigation
implementations match **3/3**, no unexpected edges (precision/recall 100% within
that scope). Real CommonJS source adds complete small scopes: readSource's
repository callee **1/1**, chunksForFile's caller **1/1**, semanticStore relative
imports **3/3**, chunker reverse dependencies **5/5**, with no unexpected edges.
Dependency-path orientation is checked against inspected callers/callees.

External libraries, dynamic calls and unresolved references remain explicitly
unresolved/inferred, not fabricated implementations. Whole-repository graph
precision/recall is not estimated from these 13 scoped edge observations.

## 10. Semantic relevance

Complete small-profile implementation-file labels: source-hash navigation
discovery returns precision **3/4**, recall **3/4**; it misses `hybridChat.ts` and
includes presentation-only rendering. React log-loading discovery: **1/1** file
precision and recall. These are unique-file judgments over returned chunks,
not symbol-level precision@5 or a broad semantic accuracy estimate.

The mixed target returns only one of four known-positive extension files for
that navigation query: known-positive recall **1/4**. Full mixed-corpus relevance
was not exhaustively judged, so its precision is unmeasured. Low semantic recall
remains a failed quality concern; no labels were fabricated to make it pass.

Historical label corrections are preserved separately: `extension.ts` contains
a real hash-verification delegation, and unqualified Node `readSource` is
genuinely ambiguous with `registry.js`. Corrected positive questions specify the
file; the unqualified ambiguity remains a negative. Old run JSON is untouched.

## 11. Actual Ollama results

Latest cohort: **13 positive tasks, all actually attempted; 7 accepted**, six
fallbacks (**46.15%**), four timeouts (**30.77%**). Comparison repetitions fail
validation; none is a successful comparison explanation. Missing, ambiguous and
source-truncated probes withhold inference; the explicit short-deadline probe
reports timeout. Injected invalid generated citations are rejected separately.

Cold model: **n=1, 30.342 s**, timeout; historical baseline cold observation was
24.679 s. Warm resident model: **n=12, median 18.120 s, p95 30.672 s**. The approved
30 s warm budget fails. First-of-family prompt preparation/prefill is not dropped
from warm-model results merely because later prompts benefit from cache.

Successful repeated workflow transport: 1,654 prompt tokens, 324 output tokens,
about 27.21 s decoding; total pipeline about 28.65 s. Function repeat: 186 output
tokens, about 15.87 s decoding. Original bodies, prompts/context, byte counts,
token/load/prefill/decode telemetry and all stage timings are retained. Timeout
responses have no body; none was invented. Server logs corroborate ~29.8–29.9 s
client cancellation and prompt/decode work; they do not prove the cause of the
older historical timeout. Synchronous/cooperative deadlines can overshoot; no
hard cancellation/watchdog guarantee or relaxed limit is claimed.

## 12. Factual grounding — failed

All original outputs remain in `phase3_verified/extension.json`; corrections are
separate in `phase3_assistant_claim_review.json`. Assistant review covers all
seven accepted claim observations: **5/7** supported/properly qualified,
**2/7** source-contradicted. These repeated model-claim units are not independent
atomic assertions or a population factuality estimate, and are not human review.

Critical example: the accepted workflow reverses the absolute/NUL-path guard and
claims the helper receives the same argument tuple as its caller. Source instead
rejects invalid paths and passes root plus proof path/hash fields. The narrative
also obscures the pre-open verification/recheck. Citation validity did not catch
these prose errors. Function/behavior answers omit edge cases; the accepted
compound output mostly repeats location instead of explaining behavior.

The ≥95% grounding/no-critical-hallucination gate **fails**. No correctness claim
is inferred from schema validity or an authoritative-looking citation. Generation
remains non-authoritative, default hybrid remains off, and the existing
generation-disabled deterministic path/fallback is retained.

## 13. Presentation and employee usefulness

The existing formatter presents source locations, contracts, calls, static paths,
impact/test findings, comparison support, limitations and verified navigation.
Quick/Standard/Deep projections vary detail, not evidence confidence or canonical
identity; depth changes do not request another explanation. Available host and
regression checks cover those behaviors. No extra formatting model/provider.

There is no qualified ML hyperparameter/tuning explanation layout; semantic
discovery must not masquerade as extracted model facts. Rejected comparison
output and misleading workflow prose are not “useful” simply because rendered.

The immutable original-output packet is `phase3_review/HUMAN_REVIEW.md`, prepared
and queued in the file panel for the user. Six 1–5 dimensions: directness, locating code, following flow,
facts versus hypotheses, unknowns and actionable next steps. Rate each family
and fallback separately. Independent human scores/acceptance are pending;
**no ≥4/5 human score is fabricated**. Assistant source corrections are separate.

## 14. Actual VS Code

Installed real Electron/VS Code ran Phase 9/10 plus the fresh private-source
suite, **exit 0**. Latest log records **22 real-host checks plus three explicit
fault-injected host branches**. These are programmatic extension-host/webview,
authenticated transport and editor-selection tests, not headless VS Code mocks
or an independent employee usability study.

Coverage maps to lookup, semantic discovery, callers, explanation/workflow/impact/
comparison displays, compounds, ambiguity, missing/statistics, depths, canonical
opening, stale source, in-flight repository switch, rollback, backend restart and
lost-handle rejection, forged/foreign actions and hostile query markup. Partial
graph failure and model-unavailable/timeout rendering are fault-injected in the
ephemeral server; real model timeouts are separately measured above. Expiry's
exact time boundary is covered by existing cache tests; restarted lost handles
are the actual-host case, not a claim that the editor waited five minutes.

## 15. Security and provenance

**11/11** explicit real-snapshot negative/authority checks pass: foreign repository,
stale generation, foreign proof, mixed generation, forged hash, invalid range,
traversal, legitimate internal stage acceptance, injected model citation refusal,
secret redaction and canonical proof recount. Internal stage UUIDs are admitted
under their published generation; they are not incorrectly required to equal it.

A real indexed malicious source comment containing Markdown-looking override,
fabricated citation and script text reached actual Ollama: one transport call,
model success, **no accepted override**, old-generation refusal, private source
restored. This is a bounded source-comment/model-boundary observation, not proof
of universal resistance or a standalone Markdown-document generation feature.

Full regressions retain auth, malformed/oversized requests, source/path/symlink/
hash/range checks, redaction, stale/restart/cache expiry, corrupted/missing storage,
failure/retry and cooperative budget tests. No accepted forged citation or
cross-repository/mixed-generation evidence was observed in these measured cases.
The critical remaining failure is **source-contradicted generated prose**; valid
provenance does not authorize production qualification.

## 16. Performance, memory and limits

Approved provisional budgets are unchanged: deterministic p95 ≤2 s; warm model
p95 ≤30 s; publication ≤300 s for ≤3 MiB; Python peak RSS ≤2 GiB.

Five repetitions per deterministic case retain median/p95, sample/outlier,
response size and understanding/planning/execution/aggregation/preparation/
generation/formatting/validation timing. Latest isolated target maximum per-query
p95 is **995.51 ms**, under 2 s; no LLM success is inferred from those disabled-
generation runs. Real explanation timings and failed cases are separate.

Target publications: **354.16 s**, **300.50 s** (failures), then **269.42 s** in a
fresh worker with no competing qualification job. Different frozen revisions
and overlapping earlier work prevent treating this as a controlled speedup or
stable production p95. Fresh target embedding work is **245.63 s of 269.42 s**;
graph construction is 3.01 s. Embedding is the measured dominant cost.

The original five-profile worker reached **2.381 GiB**, failing the RSS gate.
It constructed multiple ONNX sessions and its process-wide peak was cumulative;
that is not isolated per-repository production attribution. The harness now
reuses one cached session like the application. The subsequent React/CNN/Node
sequence peaks below **0.8 GiB**; fresh target peak is **0.968 GiB**. Earlier
failures remain recorded; production batch 64 is **not** qualified by benchmark-
only batch 8 / two-thread measurements. No resource ceiling was increased.

Ollama runner snapshot is approximately **5.09 GiB RSS**, separately reported
resident/unified-GPU allocation **5,561,647,103 bytes**; do not sum these as
independent allocations on Apple unified memory. Five-second OS samples retain
CPU/RSS, free-memory output, VM counters and raw pressure levels (1 and 2 were
observed). Overall free percentage varied, including automatic model expiry;
this is not proof of whole-machine peak or load stability. No hard process
resource sandbox, enterprise-scale budget, or cancellation guarantee is claimed.

## 17. Defects reproduced and fixed

| Reproduction | Minimal change / after evidence |
| --- | --- |
| Return-value question routed to location; CNN question picked symbol `CNN` | Existing contract/semantic routes; five initially failing parsing assertions pass |
| Real navigation workflow loses all required context under default budget | Remove repeated prompt identities/provenance, intern coverage limitations; full verified context/package retained; failing real-source context regression now passes within unchanged cap |
| Real model repeats order 0 coverage | Schema min/max coverage rows and uniqueness guidance; strict backend check retained; latest duplicate-coverage rejection count 0, not a claim of factual correctness |
| Multiple benchmark ONNX sessions confound cumulative RSS | Shared local-only benchmark session plus fresh-process target attribution; original RSS failure retained |
| Human packet crashes when timeout has no body | Explicit absent-body record, no invented output; regression passes |

Task guidance is concise and evidence-only; all original model failures/outputs
remain available. No benchmark threshold, assertion, ambiguity resolution or
prose validator was weakened to manufacture a pass. Model hallucination and
semantic recall failures remain unresolved qualification failures, not hidden.

## 18. Coverage added

Fifteen new Python methods: eight understanding cases, one schema method with
two order-count subcases, and six qualification-tool/context methods. Existing
tests/assertions remain. Source-derived context regression uses real copied TS
implementations with fake generation only for the unit assertion; it is not
counted as actual Ollama quality. Actual source/model/host data is separate.

New benchmark probes cover source labels, all timing stages, live original model
responses, necessity decisions, complete scoped CommonJS labels, genuine indexed
injection, real add/failure/recovery and safe artifact output. No coverage package
was installed; no coverage-percentage increase is invented from method counts.

## 19. Final full regressions

| Completed final check | Actual result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **515 passed**, 677 subtests, two existing warnings; 205.20 s |
| Console pytest: both new modules | **15 passed**, two subtests; 1.81 s |
| `npm --prefix extension test` | **37 passed**, no failures/skips; TypeScript compilation passed; Node 1.537 s |
| Actual VS Code, latest model/source/fault cohort | **Exit 0**; Phase 9/10 and real-source suites passed; 22 checks + three fault branches logged |
| Final real Node add/malformed/recover/delete | Passed; malformed staging rejected, last valid generation retained, zero recovery/no-op/deletion embeddings, no deleted rows |
| `git diff --check` | Passed |

The complete Python suite covers the existing AI-engine, knowledge-base,
query-system, Phases 0–10, resolver, generation, presentation and runtime tests;
subtests are not added to the method count. The two existing warnings remain
Starlette/httpx deprecation and embedded-Qdrant payload-index behavior. No
assertion was suppressed or existing test skipped to obtain these results.

Raw logs include `/tmp/codemind-phase3-baseline-{python,node,vscode}.log`,
`/tmp/codemind-phase3-final-verified-python.log`,
`/tmp/codemind-phase3-node-verified.log`,
`/tmp/codemind-phase3-console-verified.log`, the before/after parsing/context/schema
logs, and persistent `phase3_verified/vscode.log`. Intermediate 512-test runs
are not substituted for the final 515-method suite. Consolidated machine metrics
are `phase3_assessment/assessment.json`; no final ground-truth case is missing
or silently matched to a stale question version.

## 20. Remaining limitations

Real prose factuality fails; comparison and large comparison context fail;
generation-disabled deterministic findings remain useful but do not complete an
explanation requirement. Semantic discovery misses relevant implementation
files. Python static/ML explanation and Next.js-specific coverage are absent.
Only a few correlated symbols and two relevance questions are labeled. Sampling
does not establish general accuracy, employee usefulness, broad injection
resistance, large-scale indexing, concurrent model performance or production
batch-memory behavior. Jobs/answer handles remain process-local and deadlines
cooperative, as documented in Phase 2.

## 21. Gate decisions

| Gate | Decision |
| --- | --- |
| Mandatory regression suite | **Pass**, 515 Python + 37 Node, compile and available real host |
| Invalid citations / cross-repository / mixed-generation evidence | No accepted violation in measured probes; bounded scope, not universal proof |
| No dropped original requirements / wrong canonical exact targets | Pass on labeled rows/targets; explanation completeness separately fails |
| ≥98% deterministic, adequate independent set | Observed 41/41 rows and 34/34 targets; adequate held-out sample **not established** |
| ≥95% routing | 31/31 operation labels, 6/6 semantic labels; small sample; two explanation-admission gaps remain |
| ≥95% grounded real prose, no critical hallucination | **Fail**, critical source contradictions; assistant review 5/7 |
| ≥4/5 documented human usefulness | **Pending**, no independent scores |
| Approved performance/resources | Fresh bounded target passes; historical failures and warm-model p95 failure prevent general acceptance |
| Actual extension scenarios | Available real-host checks pass; injected outage branches and expiry distinction explicitly scoped |
| Production-default approval | **Not granted, not requested by this work** |

## 22. Prioritized follow-ups

Keep model-backed hybrid explanations experimental and default-off. Prefer the
existing generation-disabled evidence path for controlled development. Obtain
human review of all task families, not only behavior; broaden independent
source/relevance labels before interpreting percentages as acceptance.

Evaluate shorter evidence-grounded answers/prompt overhead under the existing
deadline with preserved before/after factual review; never trade truth for latency.
Investigate file-level semantic recall on held-out questions without leaking
these labels into tuning. For indexing, embedding dominates the measured time:
preserve demonstrated no-op/reuse, then separately evaluate bounded batching or
session/CPU tuning within approved limits. No unmeasured improvement, model swap,
larger memory cap, storage redesign, new verifier or production rollout is approved.

## 23. Reproduction and rollback

Exact commands, run meanings, immutable cohort versions and artifact paths are
in `benchmarks/PHASE3_REPRODUCTION.md`. Canonical regressions remain
`.venv/bin/python -m pytest -q` and `npm --prefix extension test`.
`assess_phase3` consolidates the stated file overlays and generates an explicitly
pending human packet; it cannot create human approval.

Rollback: keep both existing hybrid gates off; generation can be disabled for
controlled opt-in tests through the existing request/setting. Save/review the
Phase 3-only patch before reverting its three production modules and test/tool
additions. Do not reset the dirty working tree or revert Phase 1/2 work. No schema,
pipeline-version or user-index migration needs reversal. No rollback command,
destructive cleanup, commit or rollout was executed.

## 24. Readiness

**Development-ready** only. The requested implementation, source-grounded
qualification artifacts and negative findings are delivered. Real-model
qualification is incomplete/failed, human acceptance is pending, and the evidence
does not authorize a production-default hybrid migration. Stop before rollout.
