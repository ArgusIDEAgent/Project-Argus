# Phase 1: saved-state capture

Implemented 2026-11-25 and qualified for administrator-assisted macOS/APFS atomic capture on 2026-11-26. This is a library milestone, not the Analyze Change UI or a released execution feature. Earlier blocker entries below are historical and superseded by the final atomic qualification record.

## Current process and integration decision

The extension's save watcher schedules repository refresh through `serverManager.ts`. The engine indexes the saved repository and publishes an immutable knowledge generation through `SnapshotStore`. Its `changed_files` compares consecutive indexing generations. The engine's Git dirty fingerprint hashes porcelain status text, so repeated edits to an already-dirty file need not change that fingerprint. Neither identity is a saved working-tree versus HEAD capture.

Phase 1 adds a separate `CaptureStore`. It reuses repository identity and scanner exclusion policy, but does not invoke indexing, embeddings, graph building or models. Existing API routes, watchers, knowledge stores and caches are unchanged. Future P2 consumers receive separate version-specific source tuples; they must not assume a knowledge snapshot matches these bytes.

## Contract and use

```python
from change_analysis import CaptureStore

with CaptureStore('/absolute/path/to/selected/folder') as captures:
    snapshot = captures.capture()
    # snapshot.before / snapshot.after contain frozen SourceVersion values.
    # SourceVersion.data is immutable bytes, or None with an exclusion reason.
    # snapshot.changes has half-open, one-based line ranges.
    # snapshot.staging is (path, index mode, index object ID, stage).
    # snapshot.conflicts blocks definitive analysis when nonempty.
    # snapshot.consistency == 'observed_stable'; this is not atomic capture.
    # capture(require_atomic=True) fails with atomic_capture_unavailable.
    # get/release accept only IDs owned by this selected-folder session.
    captures.release(snapshot.id)
```

The digest includes schema version, limits, repository/folder identity, HEAD, staging and versioned manifests. Timestamp and random capture ID are not content identity. Before/after SHA-256 values describe raw saved/object bytes without clean/smudge transformations. The all-added mode requires a verified unborn branch. Invalid Git reads fail closed with a typed `CaptureError.code`.

Capture sequence: validate selected boundary and directory identities → record HEAD/index/eligible and ignored inventory → read bounded baseline objects and saved bytes with file identity/timestamp receipts → recheck HEAD/boundary → repeat the complete observation → compare → perform a third full observation after diff computation → publish only if all observations and receipts agree. Concurrent session operations are serialized. Staging changes, new/deleted eligible paths, content changes and file identity/timestamp changes observed between passes cause a retry. Selected-folder, Git-root or Git-metadata directory replacement invalidates the session; callers must reopen it. Parent directory symlinks are rejected during boundary validation. Receipts are internal race-detection data and do not change content digests when saved bytes are unchanged. At most three attempts occur by default. No partial result is published on failure.

## Support and exclusions

- Local Git repositories and linked worktrees; one selected folder, including a nested folder in a larger repository. Git paths never widen this scope.
- Saved `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`, `.mts`, `.cts` files; UTF-8 text only. Raw bytes and line endings are retained.
- Staged/unstaged combinations use final saved bytes. Index object IDs are metadata, not the after version. Files tracked in HEAD remain eligible after index removal even if now ignored.
- Eligible untracked files; ignored files/directories are represented as exclusions. Ignored directory contents are not recursively listed. Tracked files remain eligible despite ignore rules.
- Deletions preserve baseline bytes. Exact-content deleted-to-added matches become rename candidates, including multiple candidates. Edited renames remain additions/deletions with an explicit notice; no unsupported correspondence claim is made.
- Binary, non-UTF-8, oversized, sensitive-name, unsupported-extension and reserved-directory entries remain visible without retained source bytes. Excluded entries have status `unanalyzed` and may be unchanged: they are not asserted to be proven changes. The digest does not identify unread excluded contents.
- Symlink source is excluded. Saved eligible files use directory-relative no-follow opens at every component; non-regular files are not read. Selected symlink roots are rejected.
- Submodule Git object/index references are reported without traversal. A dirty submodule working tree and its internal HEAD are not analyzed; staged reference metadata is labeled separately.
- Merge conflicts produce a capture with `definitive=False` and explicit conflicted paths. Future analysis consumers must honor this gate.
- Unsaved buffers cannot be observed by this Python library. Every capture contains a visible-to-consumers notice that only saved bytes were captured; the future UI must render it.
- POSIX implementation (directory FDs and `O_NOFOLLOW`); qualified on this macOS environment. Windows is not qualified. No repository program execution is provided on any OS.

## Safety and limits

Git uses argument arrays, no shell, optional locks disabled, filesystem monitor/hooks disabled, no external diff/textconv/filter commands, no lazy object fetch and no replacement objects. Ambient `GIT_*` routing/config injection is removed. Global/system Git config is excluded; local Git config is still parsed by Git. No source checkout or index writes occur.

Default budgets: 1 MB/file, 32 MB combined before/after bytes per capture, 10,000 inventory entries, 8 MB per Git output, 10 seconds per Git command, 10,000 combined lines per file diff, three capture attempts and two retained captures per store. Oversized files/diffs get explicit limitations. Total/inventory/output budgets fail capture rather than silently omit eligible files. This is not a whole-job deadline: many bounded Git object reads can still take time. Defaults are initial capture limits, not runner or performance guarantees.

Captures live in private process memory; no capture files are persisted. `release`/`close` discard store-owned references; external consumers retaining a snapshot retain its bytes. Failed attempts leave no session artifact or source copy. Multiple stores/caller-held references require future service-level admission and retention policy. No model/network request receives source content.

Consistency is explicitly `observed_stable`, not an atomic multi-file filesystem snapshot. File identity, mode, size, modification time and change time are compared across observations, detecting the qualified same-byte replacement and change-and-revert cases. Detected saves/HEAD/index movement cannot publish mixed observations, but changes after the last check and arbitrary adversarial ABA races cannot be ruled out on an ordinary filesystem. `capture(require_atomic=True)` raises `CaptureError` with code `atomic_capture_unavailable` before source observation; it never silently downgrades to repeated reads. The `definitive` flag is a merge-conflict gate only, not proof of atomicity or completeness. Consumers must treat captures as historical and revalidate freshness when presenting them as current. No claim of transactional filesystem capture or protection against a malicious process controlling the host is made.

## Scope reconciliation and deferred Phase 0 work

The owner's explicit Phase 1 request authorizes the narrow README scope addition. Initial capture contracts, exclusions and budgets are fixed here. Broader P0 work (runner threat model, preparation API, global job limits, benchmark workload, feature flags and full release baseline) remains open; Phase 0 is not marked complete. This library is inert unless explicitly imported/called, so chat behavior and execution availability do not change. Rollback is removal of the standalone package and its tests/docs; no data migration is involved.

P2 must provide versioned evidence, ambiguous symbol correspondence and structural impact. P3 must implement authenticated API lifecycle, rendering of notices/conflicts and stale-result handling. No runner, authorization endpoint, execution workspace or test execution is implemented here.

## Verification

Baseline before implementation:

` .venv/bin/python -m pytest knowledge_base/test_scanner.py knowledge_base/test_validation_runtime.py ai-engine/tests/test_repository_storage.py -q `

Result: **32 passed, 27 subtests passed**.

Capture qualification uses real temporary Git repositories in `knowledge_base/test_change_capture.py`. Fixtures cover saved/staged disagreement, index integrity, baseline retention, untracked files, ambiguous renames, unborn/non-Git roots, nested scope, exclusions, symlink parents, submodule references, merge conflicts, repeated saves, HEAD movement, hostile helpers/environment, budgets, session ownership, immutable values, concurrent capacity, unusual paths and linked worktrees. Test fixtures run no models and no external network calls.

Post-change regression command:

` .venv/bin/python -m pytest knowledge_base/test_change_capture.py knowledge_base/test_scanner.py knowledge_base/test_validation_runtime.py ai-engine/tests/test_repository_storage.py ai-engine/tests/test_runtime_api.py -q `

Result: **71 passed, 84 subtests passed** (28 capture cases). One existing Starlette/httpx deprecation warning; no failures. Added deadline/unavailable-Git and invalid-budget cases verify typed failures and subprocess cleanup.

A real read-only capture of this selected project succeeded: 324 baseline entries, 368 saved/excluded entries, 392,790 retained bytes, no conflicts. These counts are a dated smoke observation, not a benchmark. The current Git root equals the selected project; a larger-root scenario is covered separately by temporary repository fixtures. Existing untracked implementation-plan content was preserved and its progress record updated. Existing source and index are not changed by capture.

Review checks: scoped Git diff whitespace check uses `core.whitespace=cr-at-eol` to preserve the README's existing CRLF format. No live model, VS Code UI, runner or full release qualification was run.

## Follow-up safety review — 2026-11-25

The owner requested Phase 1 remediation before Phase 2 if safety issues remained. Three new real-Git regression fixtures failed against the original implementation: replacing a file with identical bytes between observations, editing during diff computation, and replacing the selected repository directory at the same pathname. These were confirmed defects, separate from the fundamental absence of an atomic filesystem backend.

Implemented fixes:

- Retain and compare file stat receipts across full observations, including a same-content rewrite/replacement.
- Re-observe content, HEAD, index and inventory after diff computation, before publication.
- Bind the session to selected-folder, Git-root and Git-directory device/inode identities; reject replacement or parent symlink redirection.
- Expose the consistency guarantee in the frozen snapshot contract and fail closed when atomic capture is required.

Changed files: `change_analysis/capture.py`, `change_analysis/contracts.py`, `knowledge_base/test_change_capture.py`, this report and the implementation-plan progress record. Existing user changes, chat/indexing behavior and capture storage ownership are preserved.

The regression command above now passes **81 tests and 84 subtests**, including **38 capture cases**, in 16.35 seconds. The same existing Starlette/httpx deprecation warning remains. Added tests cover same-byte in-place rewrites, parent symlink replacement, Git-directory replacement, post-diff HEAD/index/inventory changes, and fail-closed atomic requests. All failure fixtures assert that no capture was published.

Real-project smoke: `observed_stable`, zero conflicts, 392,790 retained bytes, 324 before entries and 368 after/excluded entries. The Git index SHA-256 was identical before and after capture. An atomic request returned `atomic_capture_unavailable` as designed.

Decision: **Phase 2 was not started.** The concrete reproduced Phase 1 defects are fixed, but the original strict atomic-consistency acceptance criterion remains open. Satisfying it requires a qualified atomic source-snapshot backend; repeated reads and advisory locks cannot provide that guarantee against arbitrary writers. The criterion has not been weakened or checked off. Broader Phase 0 work remains open as previously documented.

## Atomic backend qualification attempt — 2026-11-26

The owner requested completion of the atomic gate before Phase 2. Live platform checks established:

- macOS Darwin 27 on arm64; selected source resides on APFS Data volume `disk3s5`, mounted at `/System/Volumes/Data`.
- Current effective UID is 501. `sudo -n -l` reports that a password is required; no unattended administrator authority is available.
- `diskutil apfs listSnapshots /System/Volumes/Data` reports no snapshots.
- Native `fs_snapshot_list` with `ATTR_CMN_NAME | ATTR_CMN_RETURNED_ATTRS` returned zero snapshots. An initial probe without returned attributes returned EINVAL; that malformed probe is not evidence of denied privileges.
- Native `fs_snapshot_create` on the Data volume, with a unique `codemind-capability-probe-<uuid>` name, returned **-1 / EPERM / Operation not permitted**. No snapshot was created, and no source, Git index, mount, backup setting or retained snapshot was changed. Cleanup was scoped only to the generated probe name and would have run only on successful creation.
- This version of `diskutil` does not expose an APFS `snapshot` creation verb. Listing snapshots does not imply authority to create them.

Apple's native snapshot API documentation states that snapshot operations require superuser privileges and an additional entitlement:
https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/fs_snapshot_create.2
The live creation denial is the decisive capability result for this session; successful listing does not qualify creation or mounting.

**Decision:** Phase 1's strict atomic gate remains blocked by platform capability, not resolved by changing the consistency label. No Phase 2 implementation was started. No new atomic-backend implementation is claimed from this denied probe. Existing observed-stability capture remains available, and atomic requests continue to fail closed.

**Required next step:** provision a supported privileged/appropriately entitled snapshot helper, with an explicit interface for owned snapshot creation, read-only access, identity verification and cleanup. It must be qualified with real concurrent source/HEAD/index edits, selected-folder containment, linked-worktree/Git-metadata placement, retention and cleanup failures before the atomic criterion can be checked off. Installing an arbitrary unsigned helper or disabling macOS protections is not a remedy. Full tests were not repeated because this investigation changed documentation only; the last implemented-code result remains 81 tests and 84 subtests passed.

## Atomic capture completed — Apple-tool route, 2026-11-26

The native custom-process API denial remains true, but Apple’s `tmutil localsnapshot` successfully created snapshots without elevation. The owner entered administrator authorization in Terminal for Apple’s `mount_apfs`. A custom entitled daemon was unnecessary for this qualified route. No SIP changes, passwordless sudo configuration, backup-setting changes or permanent privileged service were installed. CodeMind never runs as root.

New entry points:

```python
from change_analysis.atomic_session import atomic_session

# Run from a normal-user Terminal. sudo prompts only for Apple mount/unmount.
with atomic_session('/absolute/path/to/repository') as session:
    snapshot = session.capture()
    assert snapshot.consistency == 'atomic_apfs'
# Captured bytes remain immutable; private mount is now unmounted/removed.
```

From the project root, a reusable qualification command is:

```sh
.venv/bin/python -m change_analysis.atomic_session '/absolute/path/to/repository'
```

`APFSSnapshotCapture(live_root, mountpoint, snapshot_uuid)` also accepts an already mounted snapshot. That API borrows the mount and never unmounts it; the caller owns its lifetime. `atomic_session` owns its temporary mount and always attempts cleanup, including on capture errors. Cleanup failures retain the mountpoint and report its path. Unexpected replacement mounts are left untouched.

Enforcement:

- Kernel `statfs64` must identify an APFS snapshot mount with read-only and noexec flags, a matching source device, and an exact mountpoint. Listing alone is insufficient.
- Snapshot name and UUID must match the source volume’s native catalog before and after reading. Snapshot XID participates in capture identity.
- Source, HEAD, index and objects are read from the same mounted filesystem. The returned repository identity is the original selected folder, not a temporary mount path.
- Initial atomic support requires a contained `.git` directory, with no metadata symlinks, object alternates, Git config includes or worktree overrides. Nested selected folders are supported. Linked worktrees/external Git metadata remain supported only by the older observed-stability path, not by this atomic adapter.
- No source checkout or developer-index mutation occurs. A capture is historical state at the snapshot boundary, not a promise that the live workspace remains unchanged or that a multi-file application edit was logically complete.
- Time Machine snapshots cover its included APFS volumes. This adapter never deletes or thins backups. Snapshot storage/retention is managed by Time Machine; the existing byte budgets bound retained capture memory, not volume-level snapshots. This is an explicit user-invoked workflow, not snapshot-on-save automation.
- `nosuid`/`nodev` were requested in manual qualification commands but not reported by the OS for these snapshot mounts. They are not claimed as enforced controls. Kernel read-only/noexec flags are verified; no repository code is executed.

Real qualification evidence:

1. Snapshot `E9D2873A-71D8-4BF3-8232-1BFCB14BA630` was verified and used to capture this project: atomic provenance, 392,790 retained bytes, zero conflicts.
2. A disposable repository had committed `x=1`, staged `x=2` and saved `x=3` when snapshot `7E0229E0-A0C2-446F-A90F-A715189E104C` was created. Live saved contents, index and HEAD were subsequently changed to `x=99`. Capturing the snapshot still returned the original HEAD, baseline `x=1` and saved `x=3`; the live index stayed byte-identical during capture. Attempted snapshot writing failed with EROFS. The mounted-snapshot qualification suite passed all 11 cases at that point.
3. The reusable interactive module created snapshot `6C4E7665-37AC-4163-8E74-84C95F4882CC`, captured the real project and exited 0 after unmount/removal. The two earlier qualification mounts were also unmounted and their directories removed. Native mount inventory confirmed no CodeMind mounts remained.
4. Automated lifecycle cases verify normal cleanup, capture-body failure, mount failure, unmount failure and unexpected mount replacement. Failure paths do not recursively delete mount contents or delete backups.

**Decision:** P1 is complete within this supported macOS/APFS, administrator-assisted layout. Unsupported layouts fail closed. The source capture remains bounded and separate from knowledge generations. Phase 2 implementation has begun; see `PHASE2.md`. Prior entries saying the atomic gate is blocked describe the earlier custom-API route and do not describe the now-qualified Apple-tool path.

### Final combined verification, 2026-11-26

`.venv/bin/python -m pytest knowledge_base/test_atomic_session.py knowledge_base/test_atomic_capture.py knowledge_base/test_change_evidence.py knowledge_base/test_change_capture.py knowledge_base/test_scanner.py knowledge_base/test_validation_runtime.py ai-engine/tests/test_repository_storage.py ai-engine/tests/test_runtime_api.py query_system/test_evidence.py -q`

Result: **119 passed, 95 subtests passed, 1 skipped** in 21.07 seconds. The skip is the opt-in real APFS fixture, already run successfully while its snapshot was mounted; it is not a missing live qualification. One existing Starlette/httpx deprecation warning remains. Later negative cases additionally cover `config.worktree` includes and non-regular Git metadata. Scoped whitespace checks and Python compilation passed.

All private qualification mount directories, the disposable source repository and the completed Terminal launch scripts were removed. Time Machine retains/manages the three snapshots; no backup deletion was attempted. No further administrator interaction is pending for this completed qualification.
