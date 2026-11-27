# Phase 3 — Analyze Change experience

Implemented 2026-11-27. The deterministic workflow is available through the VS Code command **CodeMind: Analyze Change** and authenticated engine routes. Optional model wording remains disabled; all output is derived from retained source evidence.

## Prerequisite review and Phase 2 correction

The existing Phase 2 integrity checks, conservative relationship deltas and historical capture ownership were retained. A presentation blocker was corrected: alphabetical record selection could consume the evidence budget before a changed file was included. Changed eligible files now precede unchanged files, with the same explicit omission counts. A regression fixture verifies that a changed `z.ts` wins over unchanged `a.ts` under a one-record budget.

## User flow

1. Start the existing Python engine and compile/reload the extension. Run **CodeMind: Analyze Change**. The controller uses the selected CodeMind repository, a single open workspace folder, or a folder picker. Workspace Trust is required.
2. The engine validates repository registration, creates an idempotent session job and a private capture request. The controller opens a dedicated Terminal and invokes the engine's Python capture module using individually quoted arguments.
3. Enter the administrator password only in Terminal when Apple's mount/unmount tools prompt. CodeMind runs as the developer. The APFS source, HEAD and index are captured together; publication occurs only after mount cleanup.
4. The panel polls preparing/running/completed/failed/cancelled state, then renders saved changes, before/after declarations, structural flow, caller impact, citations, uncertainty and omitted counts.
5. Simple, Detailed and Flow views reuse the retained analysis. Detailed adds symbol correspondence and relationship changes. Model generation is not invoked. Unresolved or missing evidence cannot become verified prose.
6. Source buttons carry opaque controller keys. The authenticated server resolves a package-owned citation to captured before/after bytes. VS Code opens those bytes through a read-only content provider labeled by capture and version, including deleted baseline code.
7. Saves/file events and Git HEAD/ref changes mark results stale. Pending work is cancelled; completed results retain historical provenance. The backend also compares current saved manifests, HEAD and staging before explanation/source responses, so missed UI events do not imply freshness. A later edit after any check is still possible: all views identify their evidence as historical.

## Interfaces and ownership

- `POST /v1/change-analyses` — registered `repo_id` plus idempotency key; no arbitrary capture/source path from the webview.
- `GET /v1/change-analyses/{id}?repo_id=...` — status and bounded job identity.
- `POST /v1/change-analyses/{id}/explanations` — repository and supported presentation mode.
- `POST /v1/change-analyses/{id}/source` — repository and owned citation reference; returns captured text.
- `POST /v1/change-analyses/{id}/stale` and `/cancel` — lifecycle actions.

Every route inherits the existing application session-token authentication. Every ID is checked against the registered repository and its current root. Request models reject extra fields. Source traversal strings are not interpreted as paths. Jobs and captured source disappear across engine restart; stale Terminal requests do not regain authorization.

The filesystem handoff is a same-user internal transport, not an untrusted public capture upload. Private directories use owner-only permissions; request nonces bind results to jobs, input is bounded JSON/base64 rather than pickle, and source hashes/ownership/size are checked. An adversarial process controlling the same OS account is outside this boundary, as it is for the existing local engine session token. Published captures cannot be supplied by webview messages.

## Budgets and cleanup

One active job per repository, at most two active preparations/analyses, a single analysis worker, and at most four retained jobs. Finished historical jobs are evicted first when capacity is needed. Idempotency applies while a job is retained. Source/analysis memory expires after 15 minutes; a five-second reaper releases expired data and completed private artifacts. Source navigation then returns an explicit expiry error. The controller retains at most 20 source-provider entries; already-open VS Code documents may retain their displayed text.

Cancellation prevents analysis publication immediately. A running trusted parser may finish its bounded pass before its thread exits; it is not force-killed by P3. During capture, cancellation is checked before and after capture so snapshot cleanup can finish safely. Leave the capture Terminal available for a pending unmount password prompt. Closing it during cleanup can leave a mount requiring manual attention; this is an existing administrator-assisted capture limitation, not a qualified unattended daemon.

After successful handoff the engine removes the raw private artifact. After cancellation the worker removes its artifact directory following mount cleanup. Shutdown cancels requests and releases retained results. Startup reconciles owned requests from dead engines, invalidates them and removes raw artifacts when no worker owns cleanup. It never deletes or unmounts unrelated OS snapshots. Native mounts still use the existing atomic-session cleanup; Time Machine manages snapshot retention.

The source capture's 32 MB byte and inventory limits remain in force; handoff JSON is capped at 48 MB. Presentation lists and limitations have explicit display bounds. `CODEMIND_CHANGE_ANALYSIS=0` disables new analysis jobs independently of other engine features.

## Security and supported boundary

Repository strings are escaped as text before HTML insertion. The webview has `default-src 'none'`, nonce-only scripts/styles and no local resource roots. Webview messages accept only mode names, cancellation and known source-action keys. Terminal commands originate from the authenticated engine's fixed capture descriptor and are quoted for the explicitly selected zsh shell. No password is read by the extension or transmitted to the engine.

Atomic analysis retains P1's macOS/APFS contained-Git-layout requirements. Unsupported layouts report capture failure. No test runner or repository test command is invoked. Optional local-model wording is deliberately deferred; deterministic output is always the available fallback. P4 provides test discovery/planning.

## Qualification

Real atomic capture through authenticated FastAPI routes completed with capture `a1bbd00a750e4efab402cae41a120794`. Simple/Detailed/Flow presentations and captured-source reads succeeded with `model_status=disabled_deterministic`; source bytes remained identical after stale marking. Mount inventory afterward contained no CodeMind mounts. That run used the initial Detailed layout; subsequent Detailed correspondence sections were covered by the mode-reuse API checks.

A real local VS Code extension-host smoke run activated the extension, invoked the command, opened its Terminal/panel and exercised the job/explanation client against a fixture HTTP API. It exited 0. This host fixture deliberately used `/usr/bin/true` for its Terminal descriptor; the separate atomic API qualification above exercised the real capture. It does not claim a single unattended Electron-plus-sudo test. VS Code emitted unrelated platform/agent-host warnings during shutdown.

Automated API tests cover authentication, repository ownership, nonce mismatch, changed-byte handoff rejection, duplicate starts, active-job conflicts, history eviction, expiry, cancellation races, captured navigation after deletion and freshness detection without UI events. Controller/render tests cover escaping, CSP, shell quoting, mode reuse, stale results and read-only provider navigation. Relevant existing chat/API/storage/Phase 2 regressions were run. Exact final counts are recorded below.

Final checks:

```sh
.venv/bin/python -m pytest ai-engine/tests/test_change_api.py knowledge_base/test_change_evidence.py knowledge_base/test_change_impact.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
cd extension
npm test
npm run test:change-integration
```

The relevant combined Python suite passed **65 tests and 57 subtests** (one existing Starlette/httpx deprecation warning). The extension/Node suite passed **51 tests**, with TypeScript compilation successful. The local VS Code host smoke exited **0**. The final API-only recheck following the freshness-lock correction is recorded below. Shared Phase 2 evidence and API/storage/chat regressions are included in the combined result; the entire repository/release suite was not run.

No test-execution or dependency-preparation endpoints were added. Phase 4 remains the next implementation stage. Changes are local and uncommitted; package compilation is not VSIX release qualification.

Final API recheck: **9 tests passed** in 4.22 seconds after moving saved-state freshness reads outside the job lock so cancellation/status requests are not blocked by that read. Source and explanation still revalidate ownership/expiry before serving retained evidence.

## Phase 4 prerequisite corrections — 2026-11-27

Late captured-source replies are bound to the originating controller epoch/job before navigation. Binary inspected bytes no longer inflate retained capture size and break the private transport. Phase 4 extends freshness checks to the allowlisted captured metadata. Regression evidence is in `test_analysis/PHASE4.md`. Earlier source-only capture descriptions are superseded by the explicit metadata policy.
