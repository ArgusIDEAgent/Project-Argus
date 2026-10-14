# Final stabilization and real-world readiness

Status: qualification in progress. No production-default migration is approved.

## 1. Initial audit and baseline

Baseline taken before code edits: 433 Python tests (60 shared query, 62 knowledge
base, 311 AI-engine); 26 Node tests; extension compilation; actual Phase 9/10 VS
Code host integrations; and `git diff --check` all passed. Full AI-engine baseline:
158.978 seconds. Pre-existing S1/S2 working-tree changes are preserved.
See `benchmarks/stabilization_baseline.json`.

Audit inspected contracts, parsing/resolution, routing/execution, provenance,
source verification, context/generation, authenticated API/cache lifecycle,
extension transport/session/navigation, existing tests and qualification harnesses.

Confirmed reproductions:

- A caller/test-reference question joining two names with `and` silently omits
  the second target. S2 comparison handling itself remains intact.
- `HybridChatSession.clear()` during pending readiness still permits submission
  and rendering of the old request. Existing action-version checks do not cover ask.
- Phase 5 supports explicit previous context, but Phase 10/extension transport does
  not carry backend-owned conversation context between questions.
- The installed `llama3:latest` responds to real source in 23.480 seconds cold and
  9.111 seconds warm (20.588/6.220 seconds inside model transport). Both outputs
  contain only an indexed-source fact, with no explanation, and are correctly
  rejected as `no_usable_explanation`. No current timeout was reproduced in these
  two calls. The old timeout is not assumed still present.

Ruled out: answer expiry already scopes to the supplied key; API HTTP errors
already expose actionable error strings. These require no production correction.

## Predeclared acceptance criteria

These are qualification gates, not statements that they have passed. Report all
failures/unmeasured gates; do not substitute fixture results for real-corpus data.

| Gate | Required result |
| --- | --- |
| Regression/backward compatibility | All baseline tests plus additions pass; root README/dependencies/default gates unchanged |
| Labeled deterministic understanding | 100% ordered intent/target/requirement and engine agreement on the declared bounded set |
| Provenance/navigation/security | 100% canonical checks; zero accepted forged/stale/cross-repository/traversing evidence or secret-leak probes |
| Real supported graph relationships | 100% precision/recall on explicitly labeled supported edges; retain dynamic/external ambiguity unresolved |
| Labeled feature retrieval | Recall@5 >= 0.80, precision@5 >= 0.60 where complete relevance labels exist; otherwise explicitly unmeasured |
| Deterministic real query latency | Median <= 3 s, p95 <= 5 s over >= 5 warm samples per measured workload |
| Semantic real query latency | Warm p95 <= 15 s over >= 5 samples; cold query/model-load time recorded separately |
| Useful local generation | >= 80% accepted explanations over >= 5 real tasks; warm end-to-end p95 <= 60 s; cold latency reported separately |
| Human factual review | Human reviewer accepts all sampled supported claims; zero unsupported authoritative claims or critical hallucinations |
| Fallback and failures | No model call for deterministic/invalid evidence; unrelated successful branches survive failures |
| Indexing/incremental | Zero critical crashes; <= 5 min/corpus <= 3 MiB eligible source; no-op embeds 0 chunks; edits/renames/deletes limited to copies |
| Resource envelope | Measured query/index Python process peak RSS <= 2 GiB, excluding separately reported Ollama; response <= existing 10 MiB extension limit |
| Lifecycle/concurrency | Existing 2-request admission/429; 300 s handles expire safely; restart invalidates old authority; cleared sessions cannot release old answers |
| Actual VS Code real corpus | All depths, compound routing, verified navigation, ambiguity/fallback, switching/rollback/restart checks pass |

The user approved disposable isolated indexes from inert real-source copies only.
Original repositories and existing published knowledge bases may not be modified,
deleted or rebuilt. Temporary corpus/index data must be cleaned; results retained.

## 2–13. Final results

Completed results, full changed-file inventory, compatibility matrix, real local
model/factual-review outcomes, security/performance/regressions, failed gates,
launch/rollback instructions and final readiness classification will replace this
section after qualification. Citation validity is not human factual review.
