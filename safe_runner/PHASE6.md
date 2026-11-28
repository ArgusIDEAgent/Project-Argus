# Phase 6 — authorized existing Node tests

Implemented and qualified 2026-11-27 within the documented native Node adapter.
Phase 6 now includes separately authorized preparation of supported third-party
dependencies. See [DEPENDENCY_PREPARATION.md](DEPENDENCY_PREPARATION.md) for the
enforced registry, archive, lockfile, lifecycle and compatibility policy.
Subsequent Phase 7 and current release qualification are recorded in PHASE7.md
and PHASE8.md; the verification below is the earlier Phase 6 record.

## Workflow and exact authorization

Analyze Change and Review test plan reuse the atomic capture and static catalog.
Select checks, click **Review environment preparation**, review the exact package
URLs/integrities and network policy, and accept the native VS Code confirmation.
Preparation verifies/downloads pinned archives and installs them in the offline
sandbox without executing package code or lifecycle scripts.

Then click **Review and run selected files** and accept a separate native
confirmation. All tests/hooks in each selected file may execute. Selection itself
never authorizes execution. Commands, environment, mounts and reporter code are
adapter-owned. No arbitrary temporary test is accepted.

Each authorization binds repository/capture/digest, complete plan and selected
checks, profiles, manifests/lockfile, dependencies, installer, temporary-test hash
set (empty), materialization, image/runtime/platform and enforcement policy.
Authorizations expire after 60 seconds; preparation receipts after ten minutes.
Worker validation repeats before every launch. A changed identity requires renewed
review. Duplicate submissions return the same operation while the ticket is
retained; historical results are never silently rerun. Saves/selection changes
revoke unused authorization and cancel active work. Backend restart loses all
tickets, receipts and dependency archives. There is no host execution fallback.

Both actions require Workspace Trust and explicit native confirmation. The API's
trust field is a local-client assertion; isolation and the authenticated session
are the real security boundary. Code running as the developer's OS user is already
trusted infrastructure.

## Supported environment and dependency preparation

Root-package native JS Node tests use the digest-pinned Linux ARM64 Node 24.22.3
image and registry-archive-v1 installer. npm itself is never invoked. The captured
npm lockfile v2/v3 must completely describe a bounded flat tree with exact version
constraints. Small pure-JavaScript packages from public registry.npmjs.org are
supported with SHA-512 integrity; no dependency or version is resolved implicitly.

Ranges, nested layouts, private registries, git/file aliases, native/platform
builds, optional/peer dependencies, workspaces, package binaries and install hooks
are explicitly unsupported. Their preparation fails closed with a reason. This
is a scoped adapter, not general npm ecosystem compatibility. Source TS execution,
Vitest and baseline attribution remain later-phase work.

The supported runtime matcher accepts >=N with N <=24; captured version hints may
also name 24/v24 or 24.22.3/v24.22.3. Other constraints need a qualified matcher.
No host credentials, developer node_modules or repository directories are mounted.

Immutable verified archives are retained only in bounded session memory and
expanded anew for each file. Runs can mutate disposable copies without modifying
reusable dependencies or developer files. Only separately approved preparation
performs restricted registry downloads; all containers remain offline.

## Reports, lifecycle and limits

The trusted reporter emits bounded structured observations. A final summary alone
is insufficient: Node's successful empty-file wrapper is not a declared test.
Empty and skipped/TODO-only files return zero_tests. Assertion failure, setup
failure, timeout, cancellation, memory/output limits and cleanup failure remain
distinct. Counts distinguish selected static checks, discovered declarations and
actual runtime tests; none of these counts proves coverage.

Results retain capture/plan/review provenance and historical/stale labels.
Bounded console output is sanitized and escaped as plain text. Captured evidence
is available through the plan's source buttons. Untrusted reports are observations,
not proof of correctness or that a change introduced a regression.

One global operation is admitted (at most one per repository); no waiting queue.
At most eight checks/four files per authorization, 128 source files and 4 MiB
combined source/compressed dependencies. P5's 12-second container wall time,
64 KiB output, 256 MiB memory and 48-process limits remain enforced. Dependency
download/expansion bounds are documented separately. Eight runs/eight tickets
and a five-second reaper bound retention; results expire after 15 minutes.
Closing/cancelling signals process-tree cleanup. Orphan recovery retains its
journal unless absence is verified. Future-dated attestations fail closed.

The global capability endpoint still reports execution_available=false: isolation
qualification alone does not authorize a plan. The plan-specific review endpoint
is the eligibility gate. Execution is disabled by default. Set
CODEMIND_CHANGE_EXECUTION=1 explicitly to allow review/launch; unset or any other
value disables both. Analysis/planning/chat remain available. See PHASE8.md.

## Verification

Run the commands in DEPENDENCY_PREPARATION.md. The live qualifier uses disposable
synthetic atomic captures, authenticated fixture HTTP routes, the production route
implementation, real restricted registry downloads and real VM containers.
Native APFS capture is qualified separately. Controller tests cover native
confirmation/trust/save races; this is not a claim of one automated editor plus
administrator-prompt end-to-end test.

No developer-selected repository plan was executed during qualification.
Results are recorded in qualification-report.json and qualification-phase6-report.json.
Structured report behavior follows the [Node test runner event API](https://nodejs.org/download/release/v24.14.0/docs/api/test.html).
