# Phase 8 — local release qualification

Release scope: **opt-in local macOS ARM64 release**, qualified on 2026-11-27.
The retained machine-readable record is [release-report.json](release-report.json).
Qualification is evidence for the recorded host, runtime, policy and fixtures;
it is not certification of arbitrary repositories or other platforms.

## Changes and prerequisite corrections

Phase 7 failure identity now uses SHA-256 fingerprints of the original structured
failure before display redaction. Two different secret-bearing errors can no
longer become an identical paired failure merely because both were redacted.
Missing fingerprints prevent attribution. Callback exceptions are `test_error`,
separate from assertion failures and loader/setup errors; they remain
inconclusive for regression attribution. All attempts remain visible.

Analyze Change creates its status panel before awaiting backend registration.
The controller test checks acknowledgement scheduling below 300 ms while
registration is deliberately blocked; it does not measure screen paint.
Run summaries can be exported through a native Save dialog. No destination path
is accepted from the webview. Closing the backend clears run references and
expiry also removes temporary literal checks.

## Supported configuration

| Area | Qualified scope |
| --- | --- |
| Capture | macOS APFS read-only snapshot; contained Git metadata; saved JS/TS and allowlisted metadata |
| Isolation | Apple Silicon macOS, dedicated Colima VZ `codemind-runner`, Linux ARM64, no host shares/agent forwarding, cgroup v2, seccomp/runc |
| Node | Pinned Node 24.22.3 image, root npm v2/v3 lock, native JS tests; bounded flat exact pure-JS dependencies |
| Vitest | Pinned 5.1.2/Vite 8.4.0 image and exact recorded framework graph; native ESM JS; broker-owned config |
| Comparison | One unchanged existing check file, matching captured environment, two attempts on each version |
| Temporary check | Reviewed literal Node call/equality template; no automatic defect claim or source write |

Intel Macs, Windows, Linux hosts, other VM configurations/images and missing or
expired attestations are unavailable for execution. Unsupported host checks run
before Docker probing. Custom configs/plugins, arbitrary TS transpilation,
browser/UI execution, private/native dependencies, arbitrary generated programs,
monorepo workspaces and broad npm scripts remain unsupported. The UI held-out
case is explicitly `could_not_verify`, not a successful runtime check.

## Setup and use

1. Install the local backend dependencies and APFS capture prerequisites in
   [PHASE1.md](../change_analysis/PHASE1.md) and
   [PHASE3.md](../change_analysis/PHASE3.md). Administrator authorization happens
   only in the dedicated Terminal opened for Apple snapshot mount/unmount tools.
   Never enter an administrator password into chat or an extension input.
2. Prepare the dedicated VM and pinned Node image using [PHASE5.md](PHASE5.md).
   Vitest preparation and its separately pinned toolchain are in [VITEST.md](VITEST.md).
   Do not substitute a shared VM with developer directories mounted.
3. After installation or enforcement/runtime changes, qualify the current images:

   ```sh
   .venv/bin/python -m safe_runner.qualify
   .venv/bin/python -m safe_runner.qualify --vitest
   .venv/bin/python -m safe_runner.qualify_vitest
   ```

   Attestations expire after 24 hours and bind the runtime/image/enforcement
   identity. Runtime or source changes require requalification. A capability
   result does not authorize any developer check.
4. Execution is **off by default**. To opt in, set
   `CODEMIND_CHANGE_EXECUTION=1` in the backend environment, then restart the
   backend. Unset it or use `0` to disable new reviews and launches independently
   of chat, capture, explanations and plans. Status/export/cancel remain usable.
   Changing the environment of a running process requires restarting it.
5. Build with `npm --prefix extension run package:vsix`, install
   `codemind-extension-0.4.1.vsix` in VS Code, and reload the window. Select a
   trusted repository, save changes and run **CodeMind: Analyze Change**. Review
   its captured citations and test plan. Preparation and execution each require
   a separate native confirmation bound to that exact plan and capture.

The release process uses disposable fixed fixtures, never tests from the selected
developer repository. A successful run reports observations, not full coverage
or proof of application correctness. Execution isolation itself is independent
of whether the model is running. Change explanations are deterministic/model-free.

## Privacy, retention and export

Captured source, plans, temporary literal arguments/expected values, prepared
archive bytes and bounded run output live in backend session memory. Jobs and
results expire after 15 minutes, with a five-second reaper; preparation receipts
expire after 10 minutes and review tickets after 60 seconds. There are at most
eight retained runs and eight review tickets. Shutdown cancels active runs and
drops references; Python memory disposal is not a secure erasure guarantee.

The administrator-assisted capture worker also serializes captured bytes into an
owner-private temporary handoff directory (0700, output file 0600) after unmounting.
The backend deletes that handoff after consuming it; cancellation/expiry also
cleans it when no worker owns cleanup. An interrupted backend can leave a private
handoff until startup reconciliation or the worker's cancellation cleanup.
These temporary files are not securely erased, and this is not a claim that source
never touches local disk. No reusable source cache is created by the runner.

Containers use new ordinary copies in private tmpfs, no host mounts or writable
dependency cache, no network and no persistent Docker logs. All selected-file
tests/hooks can read the captured materialization. Container cleanup verifies
absence; failures remain explicit and an orphan journal permits reconciliation.
Local qualification records and policy/image identities persist in the private
runner state directory. Apple/Time Machine manages APFS snapshot retention;
CodeMind unmounts its private read-only mount rather than deleting system snapshots.

The separately approved preparation downloader contacts only integrity-pinned
public npm tarball URLs and sends no source or credentials. The registry sees
package requests and connection metadata. See
[DEPENDENCY_PREPARATION.md](DEPENDENCY_PREPARATION.md) for bounds and cancellation.
Console display strips control sequences and common named secrets, but arbitrary
test output can still contain private data. Redaction is not a general secret
detector. **Export private summary** omits console, failure names/messages,
failure fingerprints, literal source/expectations, errors, URLs and host roots.
It retains relative source paths and opaque capture/plan identities, which are
still private project metadata. The chosen JSON file persists until you delete it;
inspect it before sharing. Export never uploads anything.

## Verification and measurements

The release record includes the full Python and extension suites, real VS Code
navigation regressions, real APFS capture through the authenticated API, both
images' isolation gates, Vitest lifecycle gates, current Node preparation/paired
workflows and independent held-out defect fixtures. The single Python skip needs
a manually mounted disposable APFS fixture; real native APFS/API qualification
passed separately. Model use was disabled for release fixture workflows.

The installed-package gate installs the actual VSIX into a fresh temporary
extensions directory/profile and drives real commands, webviews, plan rendering
and chat opening against a fixture API. Native APFS/API and VM product execution
are separately qualified. This is not one continuous automated editor/password/
real-repository execution test. Timing polling in the harness waits for actual
tab registration rather than assuming synchronous editor presentation.

The benchmark records hardware, workload bytes, first and repeated structural
analysis, explanation/planning, first/subsequent container materializations,
registry download, process/compiler high-water memory and enforced disk bounds.
VM boot, image preparation, large-repository throughput, GUI paint, interactive
password time and guest peak RSS are not measured. Small synthetic fixture
measurements must not be generalized to a large repository or LLM response time.

Reproduce after qualifying the current policy:

```sh
.venv/bin/python -m pytest -q
npm --prefix extension test
npm --prefix extension run test:integration
.venv/bin/python -m safe_runner.qualify_execution --dependencies
.venv/bin/python -m safe_runner.qualify_phase7
.venv/bin/python -m safe_runner.release_checks
.venv/bin/python -m safe_runner.release_checks --bench
npm --prefix extension run package:vsix
npm --prefix extension run test:installed-release
```

The installed-package test uses the existing `/Applications/Visual Studio Code.app`
runtime and temporary fixture profile. It does not install into the user's normal
profile or download a test editor. Native capture qualification is
`.venv/bin/python -m change_analysis.qualify_phase3 --fixture --test-plan` and may
open a Terminal administrator prompt.

## Troubleshooting and rollback

- **503 disabled:** explicitly opt in only when desired. Analysis and planning
  should still work. Execution availability is plan-specific, not a global badge.
- **Unqualified/identity changed:** stop execution, verify the dedicated VM and
  rerun its current qualification. Never bypass the attestation or fall back to
  a host shell. Qualification failure keeps execution unavailable.
- **409 stale/expired selection:** save, analyze again and review/prepare again.
  Do not reuse an old ticket after edits, selection changes or a backend restart.
- **Cleanup failed/orphan:** cancel, keep the journal, restore the dedicated VM
  and allow reconciliation to verify absence before another run. Do not delete
  the journal to make a failure disappear. Crash, disk exhaustion, persistent
  descendants and injected cleanup failures are covered by the isolation gates.
- **Zero tests/setup error:** inspect captured imports and supported profile.
  No test execution or failed setup is never reported as a passing check.
- **Cancel:** running containers have a five-second cleanup grace period;
  failures are explicit. Downloads check cancellation between operations; OS DNS
  may exceed the ordinary two-second socket timeout. No subsequent launch skips
  the source/authorization check.

Rollback execution by unsetting `CODEMIND_CHANGE_EXECUTION` and restarting the
backend; reload the extension when updating its code. Keep the previous package
until the replacement is verified. Existing chat, indexing, analysis and planning
do not require the execution opt-in. Broader runtime adapters and application
coverage claims are deferred rather than waived into this release.
