# Final stabilization and real-world readiness

Classification: **Development-ready — restricted opt-in evaluation only. Not production-qualified.**
Measured on 2026-10-14. Critical explanation-quality and resource gates remain failed.
No production-default migration, model-family change, dependency change, or user-index rebuild was performed.

## 1. Initial audit and baseline

Before edits: **433 Python tests** (60 query-system, 62 knowledge-base, 311 AI-engine),
26 Node tests, extension compilation, actual Phase 9/10 VS Code host integrations,
and scoped `git diff --check` passed. AI-engine baseline: 158.978 seconds.
Evidence: `benchmarks/stabilization_baseline.json`.

Inspected contracts, parsing/symbol resolution, router/executor, graph/semantic/rule
interfaces, provenance aggregation, context generation, local transport, authenticated
API/cache lifecycle, extension transport/session/navigation, and existing qualification tests.
Existing S1/S2 changes were present before this task and were preserved.

External commits occurred during qualification (observed HEADs include f7cbecd,
66763b4 and 47c117f); this agent did not commit, reset or rewrite history.
A live repository revision change invalidated an earlier read-only snapshot run;
the existing guard correctly rejected it. Final qualification uses frozen disposable
source copies, not a rebuilt user knowledge base.

### Acceptance gates declared before final qualification

| Gate | Required result |
| --- | --- |
| Regressions | All baseline tests plus additions pass; locked README/dependencies/default gates unchanged |
| Bounded labeled understanding | 100% ordered intent/target/requirement and router agreement |
| Provenance/security | 100% checked proofs; zero accepted stale/forged/cross-repository/traversing evidence in tested probes |
| Labeled graph | 100% precision/recall on explicitly labeled supported edges |
| Feature retrieval | Recall@5 >= 0.80; precision@5 >= 0.60 with complete relevance labels, otherwise unmeasured |
| Deterministic real queries | Median <= 3 s, p95 <= 5 s, >= 5 samples/workload |
| Semantic real queries | Warm p95 <= 15 s, >= 5 samples/workload |
| Useful local generation | >= 80% accepted explanations over >= 5 real tasks; warm end-to-end p95 <= 60 s |
| Human review | Accepted sampled supported claims, no critical unsupported authoritative claims; citations alone do not pass |
| Fallback | Invalid/deterministic work does not call model; successful independent branches survive failure |
| Indexing | Zero critical crashes; <= 300 s per <= 3 MiB corpus; no-op embeds zero chunks |
| Resources | Python peak RSS <= 2 GiB, excluding separately reported Ollama; response <= existing 10 MiB limit |
| Lifecycle | Two-request admission/429; 300 s expiry; restart invalidates authority; cleared sessions reject late answers |
| Actual VS Code | Depths, compound routing, canonical navigation, failure visibility, switching, rollback and restart |

## 2. Defects identified and fixed

| Reproduction / cause | Minimal correction | Verification |
| --- | --- | --- |
| `Who calls login and entry?` lost the second operand: conjunction splitting and target extraction handled only one relationship target | Reuse scoped operand extraction for known caller/callee/test target lists; preserve independent commands, duplicate scoped names and existing caps | Parsing regressions, API executes both existing graph operations, real CodeMind question, actual VS Code |
| Phase 5 context existed but was absent from Phase 10 HTTP/extension flow | Optional opaque previous-answer token; server derives only a unique original canonical target and revalidates generation; no semantic candidate promotion or client proofs | Follow-up, ambiguity/multiple candidates, forged/unknown tokens, changed snapshot, real host |
| Clear during readiness/query could submit or render abandoned work | Per-session epoch checks before/after await; clear context and ignore stale errors | Three Node session tests and rollback host checks |
| A slow response body could defer deadline checks until the whole body completed | Bounded `read1` packets, checking deadline and bytes between packets | Deterministic trickle-clock reproduction; existing transport/size/error tests |
| Workflow context evicted the required path before optional facts | Retain first supplied verified path per binding and its evidence; prioritize target excerpts; discard optional facts first, retaining existing limits | New default-budget workflow regression plus all Phase 8 tests; real workflow still exceeds its envelope and is correctly blocked |
| Expired/restarted handle lacked an explicit next action | Add “Ask the question again.”; unchanged TTL and cache budgets | Exact 399.999/400-second boundary, restart API/host tests |

No new retrieval engine, adapter, graph store, routing pipeline, dependency or model
family was introduced. No Phase 6 redesign, Phase 7 proof relaxation, embedding
pipeline rebuild or default budget increase. A two-sentence prompt clarification was
tried, measured ineffective against fact-only output, and reverted.
Existing per-answer expiry and API HTTP-error translation were inspected and already correct.

Original negative regressions were observed before fixes: relationship parsing,
pending-session handling, context transport and required-path context selection.
The workflow regression originally failed with missing relationship and target-source gaps;
it now passes without widening budgets. This is not a claim that all real workflows fit.

## 3. Complete modified-file list

Paths below are relative to this project directory, including changes now captured
by external commits; a clean current diff is not the full task inventory.

Production files:

- `query_system/query_understanding.py`
- `query_system/hybrid_contracts.py`
- `query_system/orchestration.py`
- `query_system/local_ollama.py`
- `query_system/graph_rag.py`
- `ai-engine/hybrid_api.py`
- `extension/src/hybridChat.ts`
- `extension/src/serverManager.ts`

Tests and qualification tooling:

- `query_system/test_stabilization.py`
- `query_system/test_generation.py` (existing transport mocks use packet reads)
- `ai-engine/tests/test_stabilization.py`
- `ai-engine/tests/stabilization_live_server.py`
- `extension/test/stabilization.test.js`
- `extension/test/stabilizationRealSuite.js`
- `extension/test/hybridSuite.js`
- `extension/test/suite.js`
- `query_system/qualify_stabilization.py`
- `query_system/qualify_stabilization_live.py`

Reports/data:

- `query_system/STABILIZATION_REPORT.md`
- `query_system/STABILIZATION_HUMAN_REVIEW.md`
- `query_system/benchmarks/stabilization_baseline.json`
- `query_system/benchmarks/stabilization_corpora_results.json`
- `query_system/benchmarks/stabilization_batch16_diagnostic.json`
- `query_system/benchmarks/stabilization_queries.json`
- `query_system/benchmarks/stabilization_query_results.json`
- `query_system/benchmarks/stabilization_retrieval_labels.json`
- `query_system/benchmarks/stabilization_ollama_results.json` (initial run retained)
- `query_system/benchmarks/stabilization_vscode_results.json` (earlier compact host result)
- `query_system/benchmarks/stabilization_final_results.json` (complete final model + host evidence)
- `query_system/benchmarks/stabilization_regression_results.json` (final suite record)

Generated extension `out/` compilation artifacts are build output, not a new dependency
or source change. Earlier S1 resolver/tooling/results and S2 tests/benchmarks/docs are
pre-existing work, not attributed to this stabilization pass.

## 4. Real-repository compatibility matrix

Only inert, eligible source subsets were indexed; upstream application code,
package scripts and dependency installs were never executed. Public source was
downloaded at pinned commits; repository content was not sent to a remote model.

| Corpus | Pin / scope | Files / bytes | Outcome |
| --- | --- | --- | --- |
| Next.js | 2251f43886faac9da45e585a77c07fa2b881f410; with-typescript example | 14 / 9,083 | TS/TSX definitions/imports, semantic retrieval, partial module overview and incremental checks pass |
| Express | 9a3f7ff4120d7920a2d13809ff5ae78648c8a3d6; lib/tests/root text | 106 / 630,174 | CommonJS, nested functions, supported static resolution, semantic and incremental checks pass; external/dynamic references remain unresolved |
| Flask | d086db856be187255b8ec61ef409357393020f32; src/tests/root text | 81 / 574,978 | Python semantic retrieval/provenance/incrementals pass; exact Python graph queries explicitly unsupported |
| CodeMind | Frozen eligible working-source copy, digest in results | 168 / 1,837,709 | Mixed Python/TS/JS, graph definitions/callers, compound operations, ambiguity, semantics and incrementals pass with honest partial states |
| CodeMind extension | Final frozen implementation; same private snapshot for queries, model and VS Code | 9 / 265 chunks | Actual authenticated host flow passes; explanation quality fails |

All four corpus publications completed without the S1 resolver crash.
Initial inventory: **19,486/19,486 symbol/relationship provenance checks** and
**9,483/9,483 chunk provenance checks** valid; post-incremental checks also pass,
with no dangling references/foreign-key errors. These counts include unresolved
records whose provenance is valid, not a claim that all relationships resolved.

Every no-op reused its generation and embedded zero chunks; each private source edit
embedded one changed chunk. Private document renames removed old-path chunks;
deletions left zero deleted chunks and embedded zero chunks. New path-based IDs on
rename may require re-embedding: this is not claimed as content-addressed cross-file reuse.
Old-generation evidence after edits was rejected. Temporary copies and indexes were
cleaned; only reports remain. Existing user stores/published snapshots were not rebuilt.

Limits: this is small through target-sized eligible source, **not enterprise-scale
qualification**, whole Next.js monorepo support, runtime JSX call flow, Python call
graphs or complete dynamic/external dependency resolution. Generated/binary/large/
sensitive exclusions are not language-support successes.

## 5. Query-understanding and routing accuracy

The declared **46-question** labeled development set reports 100% intent accuracy,
ordered target preservation, requirement preservation, clarification-decision
accuracy and router-operation agreement; incorrect ambiguity rate 0%.
All 71 traced entity/candidate proofs validate. Dataset and raw outcomes are retained.
This is bounded fixture accuracy, not an estimate for arbitrary natural language.

Real queries exercise definitions, callers, two-target lists, compound graph/rules,
comparison + test discovery, missing symbols, unsupported statistics and semantic
feature discovery. Deterministic workloads call the model zero times. Partial rule
coverage and deferred explanation requirements remain visible, not counted as complete.

The four positive implementation-file label sets retrieve a mean **58.3%** of
declared relevant files within the top five chunks (Next 33.3%, Express 50%, Flask
100%, CodeMind 50%). The 80% gate is not met. Other returned files may be relevant:
semantic precision is **unmeasured**, not falsely computed from positive-only labels.
Observed benchmark/test noise warrants broader relevance judgments before ranking changes.
No retrieval-quality fix or universal recall claim is made.

## 6. Real Ollama results and factual review

Configured installed model: `llama3:latest`; digest retained in the final artifact.
Temperature 0, num_ctx 8192, num_predict 350; loopback-only, no pulls, no remote inference.

Final five positive tasks:

| Task | End-to-end | Result |
| --- | --- | --- |
| Function | 19.869 s, nonresident before call | Fact-only output; rejected as no usable explanation; safe evidence summary |
| Workflow | 0.468 s | Default complete envelope cannot fit; zero model calls, insufficient-target-evidence fallback |
| Behavior | 15.046 s | Structurally accepted hypothesis, explicitly non-authoritative; factual corrections still required |
| Qualified two-target comparison | 15.244 s | Unknown-only output; rejected as no usable explanation |
| Definition + explanation | 16.225 s | Fact-only explanation rejected; deterministic definition preserved |

Backend-accepted explanations: **1/5 (20%)**, below 80%. This acceptance is structural,
not human-verified correctness. Four of five positive tasks use fallback (80%);
three of four attempted positive calls fail usefulness validation (75%).
Zero native transport timeouts in this final cohort. Truncated, missing, ambiguous
and stale negative probes make zero model calls; stale request returns stale_index.

Final warm positive-task median/p95: **15.145/16.225 s** (four samples, including the
blocked workflow; not repeated per-task performance or successful-answer statistics).
Nonresident function: model transport 19.463 s, load 5.662 s, prompt 1,437 tokens/
8.497 s, output 107 tokens/5.220 s; preparation 63 ms. Prompt-message bytes 5,854,
schema bytes 3,195, request bytes 9,557. Warm behavior: preparation 45 ms,
model transport 14.743 s, 1,391 prompt / 151 output tokens, request 9,333 bytes.
Other per-case timings, response sizes, outputs and omissions are in the artifact.

Earlier direct cold/warm function probes were 23.480/9.111 s and also fact-only.
The historical reported timeout was **not reproduced**, so its exact historical
cause is unproven; do not describe it as eliminated by changing a timeout.
The packet-read correction addresses a separately reproduced deadline defect.
Synchronous reads can still outlast cooperative deadlines; no hard cancellation claim.

Human decision: **accepted with corrections, pending source verification**.
Every requested source condition was checked against actual source lines 5–31;
original model output is preserved, corrections recorded separately in
`STABILIZATION_HUMAN_REVIEW.md`. The size guard is explicit (>5 MiB), NUL is U+0000,
filesystem resolution/stat/read exceptions can also fail, and a hash mismatch
does not prove which input changed. The corrected explanation remains non-authoritative;
there is no independent final human acceptance of the corrected prose. Other task
families are not covered by that review. Final behavior output likewise retains
NUL ambiguity/changed-source inference and must not be presented as verified fact.
Supported-prose and hallucination rates are **unmeasured**, not zero.

The initial unqualified comparison label was genuinely ambiguous (`isInside` and
`activate.isInside`). Final positive uses `extension/src/sourceNavigation.ts::isInside`;
the original ambiguous query is retained as a negative probe. Earlier results remain intact.

## 7. Security and provenance validation

Existing contracts/services reused: `SnapshotStore.pin_view()`,
`MultiEngineExecutor.pinned()`, symbol resolution, `metadata.trace()`,
`KnowledgeGraph` retrieval, existing keyword/vector/RRF channels,
`EvidenceAggregator`, `GraphRagGenerator`, `PresentationSafety`, and canonical
extension navigation. Semantic similarity is never promoted to graph dependency.

Final frozen repository: a19355069e36770585f8899e;
generation: e0b9ff2902564f3384a9db5964ebb56b.
**290/290 query evidence checks valid**, vector digest unchanged, generation still
active at final check. The actual host consumes that same private snapshot.
The stale negative deliberately requests a different generation and is rejected.
Temporary path-derived IDs are historical evidence, not current navigation authority.

Exactly labeled direct-call scope: three inspected implementations yield 3/3
expected resolved edges, no unexpected edges, precision/recall 100%; not a general
call-graph accuracy estimate. Corpus queries add 535/535 valid proofs across four profiles.

Regression probes cover prompt injection as untrusted data; secret redaction;
invented citations/facts; malformed output; stale hashes; path traversal/symlinks;
cross-repository tokens/proofs; arbitrary model/tools; oversized input/source/context;
changed generation during work; missing vector index and keyword fallback.
No tested unsafe evidence was accepted. This is automated probe coverage, not a
comprehensive security audit or proof that arbitrary model prose is factual.
Redaction, authentication, localhost restrictions and proof checks were not weakened.

## 8. Indexing and end-to-end performance

Environment: macOS 27.0, 16 GiB physical RAM, Python 3.14.6, Node 26.7.0,
TypeScript resolver 5.9.3, cached BAAI/bge-small-en-v1.5. No dependency upgrade.

| Initial publication | Time | Python process peak RSS |
| --- | --- | --- |
| Next subset | 1.461 s | 0.485 GiB |
| Express subset | 289.665 s | 3.310 GiB cumulative high-water |
| Flask subset | 141.267 s | 3.310 GiB cumulative high-water |
| CodeMind subset | 301.153 s | 3.310 GiB cumulative high-water |

RSS is process-cumulative across profiles, not isolated per-corpus allocation;
it excludes separate Ollama and child-process memory and is not whole-machine peak.
The 2 GiB Python gate fails, and CodeMind exceeds 300 s (by 1.153 s).
No claim of a statistically stable throughput threshold from a single build.

A separate Express-only benchmark batch size 16 observed **1.243 GiB** Python peak
versus default-run 3.310 GiB, but **353.708 s** indexing fails the time gate.
This diagnostic has no production configuration effect; timing comparison is not
fully controlled. Smaller-batch tuning needs approval under the user's explicit
keep-production-configuration restriction, and further tradeoff measurements.

Across the four original corpora, maximum warm deterministic p95 was **1.197 s**,
maximum semantic p95 **0.675 s**, five samples per workload. Both latency gates pass
for these declared workloads, not enterprise-scale/concurrent model workloads.
Final isolated extension max deterministic p95 0.660 s, semantic p95 0.230 s.
Largest final deterministic response 35,095 bytes, below 10 MiB. Cold embedding/index
startup is included in publication timing; no clean per-query cold embedding-load
distribution or Ollama RSS measurement is available.

Private incremental edits embed one chunk (CodeMind ~13.05 s, Express ~27.75 s,
Flask ~1.41 s, Next ~0.26 s); all no-op updates embed zero.
Lifecycle tests retain 2 concurrent admissions/429 rather than unbounded queues;
300-second handles remain process-local, nonpersistent and invalid on restart.
Clearing the UI suppresses abandoned results but does not terminate already-started
backend/HTTP work. No hard timeout, multiprocess cache or concurrency load-scale claim.

## 9. Complete regression test results

**443 Python tests passed, zero failed**: query-system 63 (0.136 s), knowledge-base
62 (9.912 s), AI-engine 318 (185.443 s). **29 Node tests passed, zero failed**
(9 resolver, 17 presentation, 3 session; 4.496 s). Scoped \`git diff --check\` passes.
Final counts add 10 Python and 3 Node regressions to the baseline, not duplicate
imported TestCase discovery. Focused generation verification also passed 28 tests.
Evidence: \`benchmarks/stabilization_regression_results.json\`.
The final actual VS Code run passed compilation, Phase 9, Phase 10 and the
real frozen-source suite (exit 0). One pre-existing optional `checkConnection`
smoke reports SKIP because the user's ordinary engine is not running; the isolated
authenticated host suites do run. The remote-provider manual `ai-engine/test_ai.py`
is deliberately not executed: it is not an automated regression and would violate
local-only inference. No remote-service credentials or repository data are used.

Expected injected offline/vector/watcher/index failures appear in test logs but
are successful negative assertions. SQLite ResourceWarnings occurred before these
changes as well as in the final run; their allocation/ownership has not been
attributed, so zero-leak qualification is not claimed. Existing Starlette/host
deprecations and the local-Qdrant payload-index notice are retained, not suppressed.

### Reproduction commands

From this exact project directory (its filesystem name ends with a space):

```sh
.venv/bin/python -m unittest discover -s query_system -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
node --test knowledge_base/test_semantic_resolver.cjs extension/test/presentation.test.js extension/test/stabilization.test.js
npm --prefix extension run compile
.venv/bin/python -m query_system.qualify_stabilization_live --isolated --real-local --vscode --output /tmp/codemind-final-qualification-new.json
git diff --check -- .
```

The live report output path must not already exist. For corpus qualification,
`python -m query_system.qualify_stabilization` downloads pinned public inert files,
uses only cached local embeddings, builds temporary private indexes, emits JSON on
stdout, then removes copies/indexes. Optional `--corpus express --embedding-batch-size 16`
is diagnostic only. Pin hashes/dataset remain fixed; temporary repository/generation
IDs and machine-dependent timings vary on rerun.

Failure history retained, not erased: pre-fix failing regressions; an initial Node
test synchronization mistake corrected with an explicit entered-promise; stale live
snapshot rejection after external revision change; benchmark-only redaction failure
from integer keys corrected to string keys; an oversized tool capture truncated
(earlier compact host result retained); final full artifact persisted before printing
a compact result. The prompt experiment did not fix quality. These are distinguished
from current application failures and from intentionally injected failure tests.

## 10. Remaining limitations and failed qualification gates

| Severity | Gate / limitation | Required remediation / approval |
| --- | --- | --- |
| High | Only 20% structurally accepted explanations; fact-only/unknown-only output; workflow cannot fit default envelope | Further measured context/schema/prompt optimization within existing safety gates; rerun all five positives >=80%; no budget increase or weakened usefulness gate by stealth |
| High | Human review requires corrections; no accepted corrected-text review or family-wide factual benchmark | Reviewer confirms corrected sample; independently source-review representative function/workflow/behavior/comparison outputs; report actual supported-claim/unsupported-claim rates |
| High | Mean labeled implementation-file recall 58.3%; precision unmeasured | Complete relevance judgments, broader real labels, then evaluate minimal existing-retriever tuning without benchmark leakage |
| High | Peak Python RSS 3.310 GiB >2 GiB; CodeMind 301.153 s >300 s | Approve production embedding-batch/config tuning if desired; benchmark memory/time tradeoffs separately before deployment; batch16 diagnostic is not a passing fix |
| Medium | Moderately large / enterprise-scale and concurrent model load not qualified | Larger authorized inert corpora, isolated peak-memory attribution and repeated cold/warm/load measurements |
| Medium | Python graph unsupported; JSX runtime/dynamic/external calls incomplete | Keep unsupported/ambiguous states explicit; extending analyzers is separate scope, not an accuracy claim |
| Medium | Cooperative deadlines/process-local 5-minute handles; no hard cancellation/persistent navigation | Re-ask after expiry/restart; separate architectural approval before durable/shared authority or process-isolated cancellation |
| Low / investigate | Existing SQLite ResourceWarnings and upstream test/host deprecations | Attribute allocation sites before changing ownership; do not treat passing tests as proof of zero leaks |

The 46-query fixture, known graph labels, safety probes, provenance, deterministic
latency and actual host pass; they do **not** override the failed gates.
No automatic approval for model-family replacement, new dependencies, semantic
reranking redesign, default hybrid enablement or user-store rebuild has been inferred.

## 11. Run the full application locally

Use the existing environment and installed local model. From the project root:

```sh
CODEMIND_ENABLE_HYBRID=1 .venv/bin/python start_codemind.py
npm --prefix extension run compile
```

Open the existing extension development workflow (F5 in the extension project) or
installed current extension. In VS Code explicitly set `codemind.hybridEnabled=true`,
select the trusted repository, and wait for a current published snapshot.
Use Quick/Standard/Deep; `codemind.hybridAllowGeneration=false` permits deterministic
evaluation without model calls. Explanations require the existing local Ollama service
and configured installed model. Do not pull another family just to run this report.

These are run instructions, not authorization to rebuild an existing knowledge base.
If readiness reports a stale user snapshot, stop and obtain separate refresh approval.
Hybrid queries do not repair/reindex it. Readiness is not a model-quality guarantee.
503: check backend gate; 429: retry after bounded work finishes; expired handle:
ask again; ambiguous symbol: use a path-qualified name. Do not treat fallback as a
successful model explanation.

## 12. Rollback instructions

Set `codemind.hybridEnabled=false` immediately; session authority/actions clear and
subsequent chat uses the existing legacy route. Optionally restart without
`CODEMIND_ENABLE_HYBRID=1`. Existing `/retrieve`, `/ask`, graph, semantic, rules,
indexing, model defaults and stored knowledge bases remain available as before.
No data deletion or history reset is required. Do not revert external commits or
pre-existing S1/S2 work indiscriminately. No production setting was changed by this task.

## 13. Final readiness classification

**Development-ready**, for restricted opt-in evaluation with explicit limitations.
Not Production-candidate or Production-qualified. Real-source deterministic retrieval,
provenance, safe navigation and graceful fallback work through actual VS Code.
Useful explanation accuracy, retrieval-quality adjudication and resource budgets
do not meet the predeclared production gates. The full definition of done is
therefore **not achieved**; the exact remaining work and approvals are above.
The stabilization results are preserved without enabling hybrid by default.
