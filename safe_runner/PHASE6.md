# Phase 6 — authorized existing Node tests

Implemented 2026-11-27 for root-package native Node tests with an empty third-party
dependency set. Network/package installation is **not implemented or qualified**;
P6.3's broader dependency-preparation requirement remains open. Tests requiring
external packages, native builds, workspaces or unsupported runtime constraints
remain planning-only. This is not general npm/Vitest execution or P8 certification.

## Workflow and authorization

Analyze Change and Review test plan reuse the existing atomic capture and static
catalog. Select checks, click **Review environment preparation**, and accept the
native VS Code modal. Preparation runs only the trusted runtime-check adapter in
the sandbox, never repository scripts. Its receipt binds the exact source/plan,
selection, profile, captured manifest/lock bytes, empty dependency identity,
materialization, image/runtime/policy and preparation adapter hash.

Then click **Review and run selected files** and accept a separate native modal.
The reviewed commands run all tests and hooks in each selected captured file;
selection is per static check but execution is per deduplicated file. Commands,
mounts, environment, Node options and reporter code cannot come from webview input.
No arbitrary temporary test is accepted. The temporary-test hash set is empty.
Both actions require Workspace Trust. The backend's trust field is a local-client
assertion; the authenticated session and isolated broker remain the actual security
boundary. A local OS user already controlling the engine can invoke its API.

Review authorizations expire after 60 seconds; preparation receipts after ten
minutes. One launch consumes a ticket; duplicate submissions return that same
operation while its ticket is retained. No automatic retries rerun repository
tests. Exact identity/freshness checks repeat in the worker and immediately before
container start, including between files. Saves/selection changes revoke unused
authorization and cancel active work. A save racing after launch cannot alter the
immutable input; the result remains a historical observation. Engine restart loses
all tickets and receipts. A fresh capture/review is required afterward.

The global capability endpoint still reports `execution_available=false` because
isolation qualification alone does not authorize a plan. The review endpoint is
the plan-specific eligibility gate. `CODEMIND_CHANGE_EXECUTION=0` disables review
and launch while preserving planning/status/cancellation.

## Supported preparation

The first adapter requires captured root `package.json` and an npm lockfile v2/v3
with a packages map containing no third-party packages or dependency declarations.
Nonempty dependencies/devDependencies/optionalDependencies/peerDependencies,
workspaces and package import maps fail closed. No package-manager command is run,
so package scripts/lifecycle hooks, credentials, developer node_modules and shared
dependency caches cannot be used. The empty set is pinned by captured metadata
hashes, runtime/platform/image and policy identities. There are no reusable mutable
dependency artifacts for test programs to modify.

The image is the P5 digest-pinned Node 24.22.3 Linux ARM64 runtime. The supported
engine constraint is `>=N` where N <=24; captured version-file hints may specify
24/v24 or 24.22.3/v24.22.3. Other constraints need another qualified matcher.
Missing/excluded dependency metadata remains unsupported. TypeScript execution,
nested-package orchestration, network downloads, native builds and lifecycle
scripts are unavailable.

## Results and limits

The trusted Node reporter emits bounded JSON lines. The parser requires a
file-specific summary of declared tests and a final summary. Node's successful
file-wrapper result for an empty file is not proof that tests ran. Empty and
skipped/TODO-only files return `zero_tests`. Assertion failures, setup failures,
timeout, cancellation, memory/output limits and cleanup failure remain distinct.
Exit code zero alone cannot pass. Counts show selected static checks, discovered
static declarations and actual runtime tests separately; they are not coverage.

Console output is capped/sanitized by P5, escaped as plain text in the extension,
and retained with the run's capture/plan/review identity. Captured evidence remains
available through the existing plan source buttons. Only console artifacts are
exported. Reports are observations of untrusted test programs, not proofs of
application correctness or resistance to a test deliberately falsifying reports.
No baseline run occurs; failures are not called newly introduced regressions.

One global isolated operation is admitted at a time (therefore at most one per
repository); there is no waiting queue. At most eight checks and four unique files
are authorized per run. P5 limits remain 128 input files / 4 MiB, 12 seconds and
64 KiB output per container, 256 MiB memory, 48 processes and offline network.
At most eight runs and eight short-lived tickets are retained, with a five-second
reaper and 15-minute run expiry. Closing/cancelling signals the broker; P5 handles
process-tree termination, owned-container removal and orphan reconciliation.

## Phase 5 corrections and verification

Orphan reconciliation now verifies that no matching container remains before
discarding the journal. A future-dated qualification attestation fails closed.
The enforcement digest now includes authorization/classification sources, so
changing them invalidates prior qualification. During the final pre-launch check,
only a journal owned by the current engine PID may coexist with qualification;
ordinary capability probes still refuse a pending journal.

Verification commands:

```sh
.venv/bin/python -m pytest knowledge_base/test_authorized_execution.py knowledge_base/test_safe_runner.py knowledge_base/test_test_planning.py ai-engine/tests/test_change_api.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
npm --prefix extension test
.venv/bin/python -m safe_runner.qualify
.venv/bin/python -m safe_runner.qualify_execution
```

The live P6 qualifier uses disposable synthetic repositories and authenticated
fixture HTTP routes with the production route implementation and real VM broker.
It checks separate preparation/run authorization, duplicate-submit idempotency,
passing/assertion/setup/zero/skipped/timeout reports, unchanged source and save
invalidation. Its synthetic atomic marker is explicitly test-only; it does not
replace the earlier native APFS qualification. Native VS Code confirmation is
covered by controller tests, not a claim of automated administrator-prompt/editor
end-to-end qualification. No selected developer repository plan was executed.

Structured-report behavior follows the [Node test runner event API](https://nodejs.org/download/release/v24.14.0/docs/api/test.html),
and was verified against the actual pinned Node runtime.

Final verification: **87 Python tests and 57 subtests passed**, **55 extension
tests passed**, all **13 P5 live isolation fixtures passed**, and **nine P6 live
checks passed** (six report fixtures, HTTP preparation/run, save invalidation).
The final code/policy identity is recorded in `qualification-report.json` and
`qualification-phase6-report.json`. No owned fixture containers remain. The only
Python warning is the existing Starlette/httpx deprecation. The VSIX packages
successfully with the existing missing-repository/license manifest warnings.
