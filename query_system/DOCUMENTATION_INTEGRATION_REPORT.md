# Documentation integration and repair — 2026-10-15

**Classification: Opt-in-ready for development, not approved for shipped activation.** The existing Docs controls now reach real Python documentation operations when `CODEMIND_ENABLE_DOCS=1`. The locked root README still places Docs and `/docs/*` outside product scope. The default remains off; owner scope approval is required before enabling or advertising this as a shipped feature.

## 1. Original root causes

- The active FastAPI `/docs/overview` returned a fixed 404, while `/docs/sync` returned empty arrays and a zero status without doing work.
- The old Node documentation planner/store used its own graph and SQLite-like data path; it was neither started by the Python launcher nor connected to published Python snapshots.
- `extension/server/` is ignored by Git and excluded from VSIX packaging. Its `sql.js` dependency is not declared in the active extension runtime. Forwarding requests to it would still use the wrong index.
- The buttons were wired to extension commands, but the backend responses made them appear ineffective. The locked root README also conflicts with exposing them as shipped functionality.

## 2–4. Architecture decision and reused components

| Option | Decision |
| --- | --- |
| A. Python-native integration | Selected: use the active FastAPI process and published Python snapshot metadata. |
| B. Packaged Node worker | Rejected: independent store/index, packaging gap, and missing dependency add complexity without a verified advantage. |
| C. Minimal replacement | Applied within A: deterministic bounded document planning, not a port of the entire Node service. |

The service reuses `SnapshotStore.pin_view/view`, canonical `repository_id`/`file_id`/`relative_path`, the published `metadata.db` files/symbols/relationship tables, the existing redaction helpers, FastAPI session-token authentication, and extension `ServerManager`. It creates no second graph, vector collection, remote model path, or repository index.

## 5. Files changed

- `ai-engine/documentation.py`: snapshot-backed planning, persistence, read/list/sync, validation and limits.
- `ai-engine/api.py`: real documentation routes, scope gate, and job-status integration.
- `extension/src/serverManager.ts`, `extension/src/extension.ts`: typed results, button states, result handling, warnings and verified source navigation.
- `extension/README.md`, `extension/package.json`: opt-in startup/packaging guidance and `test:docs`.
- `ai-engine/tests/test_documentation.py`, `ai-engine/tests/phase10_server_fixture.py`, `extension/test/runtime.test.js`, `docsSuite.js`, `docsSuiteEntry.js`, `runDocsTest.js`: transport, disposable backend and VS Code-host verification.
- This report. The root README and ignored historical Node server were not modified.

## 6. Active API contracts

All routes require the existing `x-codemind-session` token and `CODEMIND_ENABLE_DOCS=1`.

| Route | Result |
| --- | --- |
| `GET /docs/sections?repoId=…` | Repository/snapshot identity, synchronization state, last outcome/warning, counts and document summaries. Genuine empty list before first sync. |
| `GET /docs/overview?repoId=…` | Overview document or JSON `null` if none exists. |
| `GET /docs/section/{40-hex-id}?repoId=…` | One repository-scoped document or 404. |
| `GET /docs/stale?repoId=…` | Listing filtered to stale document summaries. |
| `POST /docs/sync` | `{"repoId":"24-hex","mode":"baseline"|"incremental"}`; returns `success`, `no_changes`, or `partial`, updated/removed IDs, reused/pending counts, warning, status and overview. |

Invalid input is 422, unregistered repository 404, missing/stale snapshot 409, concurrent/resource limit 429, corrupt storage 500, and disabled scope gate 503. A failed request is never presented as success.

## 7–10. Generation, incrementality, storage and provenance

The flow is selected registered repository → pinned published generation → read-only metadata → canonical file and SHA-256 verification against the snapshot and live bytes → deterministic Markdown plans → atomic repository-scoped JSON → extension UI. A sync does **not** reindex or call Ollama. It emits a project overview, per-top-level-module summaries and per-file symbol summaries, with bounded verified static relationships. These are metadata summaries, not inferred runtime workflows or function behavior.

Stable document IDs derive from repository, kind and key. Every generated source/dependency reference is validated against the pinned manifest before storage. A facts fingerprint includes text facts and **all** source dependency hashes, including dependencies beyond the 30 visible references. Unchanged records retain their content and timestamps; an unchanged sync does not rewrite storage. Changed records replace their old version atomically. Removed source documents are pruned only after an untruncated full plan. `partial` preserves completed records and reports pending work; errors preserve the prior file.

Storage is `<CODEMIND_DATA_DIR>/<repo-id>/documentation/documents-v1.json` (default data directory is external to source repositories). It records version, root/repository identity, content, kind, generation ID, source revision, source/dependency hashes, creation/update times, last successful sync, last outcome and warning. Files are written through a temporary file plus `fsync` and `os.replace`; sync uses a file lock. Reads validate identity and canonical source references. Current/stale is recomputed against the active published snapshot rather than trusting a persisted label. Internal graph/symbol/vector stage IDs are not compared for equality.

## 11. VS Code control path

The webview Docs and Sync Docs buttons post `docsOverview` and `docsSync`; their existing extension commands call `ServerManager`, which sends authenticated loopback HTTP requests to FastAPI. The overview transport accepts JSON `null` only for that route, fixing the former empty-state rejection; other null responses still fail validation. FastAPI's string error detail now reaches the UI. The extension displays loading, genuine empty state, outcome counts, no-change, partial warning and backend/connection errors. It rejects an invalid sync result instead of showing success. Repository selection still works when the gated endpoint is unavailable. Source controls are offered only for current documents; clicking performs the existing canonical path, hash, containment and editor-state verification before opening.

## 12–13. Git, VSIX and startup

The new Python module is ordinary trackable source outside ignored `extension/server/`; no new dependency was added (`filelock` is already declared). `npm run package:vsix` built `codemind-extension-0.4.1.vsix` successfully (11 files): it contains compiled `out/extension.js`, `serverManager.js` and `sourceNavigation.js`, but no Node server, Python runtime, virtual environment or generated data. The packager initially rejected three README links outside the package; those are now plain project-path references. It still emits non-blocking missing repository/LICENSE metadata warnings. A clean installation therefore still needs the separately installed local Python engine and its existing requirements. The old Node implementation remains untouched and is not in the runtime path. An installed VSIX in a separate normal profile was **not** tested.

For an approved development environment, install the root `requirements.txt` into the project virtual environment, then from the project root run `CODEMIND_ENABLE_DOCS=1 .venv/bin/python start_codemind.py`. Compile/package/install the extension using `extension/README.md`. Select a trusted repository, publish its Python knowledge snapshot, then use Docs/Sync Docs. Without the flag, the controls report the scope gate; chat, search and repository selection remain available.

## 14–15. Verification and real-host coverage

- Focused Python documentation integration: 6/6 passing. Cases include empty/initial/no-change/incremental, modified/deleted/renamed source, persistence via new service instance, auth/identity/scope, missing/stale snapshot, failure recovery, resource/concurrency limits, partial progress, corrupt/generated references, redaction and operation without Ollama.
- Existing Python AI-engine regression: 372 passing before the final generated-reference validator; the changed documentation path then passed its 6 focused tests. Knowledge-base: 76 passing; query-system: 72 passing. Node/extension headless suite: 37 passing. Existing Electron integration suite passed after the transport change. `git diff --check` passed.
- `npm run test:docs` passed in a real VS Code Extension Development Host against a disposable published Python snapshot: command invocation, genuine empty state, Sync Docs persistence, reopened overview, unchanged result and verified editor source. The final run also rejects navigation after the disposable source changes.
- The host test invokes the same registered commands as the buttons; it does **not** automate literal DOM clicks. Backend modified-source sync/re-publish and service re-instantiation are tested in Python, but the entire modify → reindex → button → process-restart sequence was not run as one Electron scenario. A normal installed-VSIX profile was not tested.

## 16. Performance and resource controls

On a three-file disposable fixture: overview 28.36 ms; first sync 80.27 ms; unchanged sync 76.96 ms; traced peak 1.14 MiB; five documents generated, then five reused. These are fixture observations, not large-repository budgets. Work is capped at 500 verified files, 12 MB of source bytes, 10,000 symbols, 300 relationships, 120 changed documents per sync and 30 visible source references per document. Oversize/partial outcomes are explicit. There is no LLM latency because generation is deterministic.

## 17–18. Known limitations and unresolved defects

- This is factual metadata documentation, not rich purpose/behavior/workflow prose. No claim is made that missing purpose, exception branches or data flow have been inferred.
- The first 500 files are inspected for an oversized repository; repeated sync does not traverse the remainder. It remains `partial` until limits or design change. The overview explicitly labels its bounded pass.
- Progress is loading/terminal status, not per-document streaming or hard cancellation. The extension HTTP timeout remains bounded but long for sync.
- Generated document contents are not a substitute for source review; verified links are limited to displayed references.
- Literal webview button clicks, separate installed-VSIX startup, backend process restart and target-sized repository performance remain unverified as a single end-to-end path.
- No known failing regression remains in the tested opt-in path. These unverified conditions are release qualification gaps, not evidence of success.

## 19–20. Scope decision and readiness

The locked root README §3 explicitly excludes Docs and `/docs/*`; §5 requires owner confirmation before promotion. This work is isolated behind `CODEMIND_ENABLE_DOCS=1`, leaves the default off, and does not change the locked README. **Approval needed:** the owner must explicitly confirm the scope change to move documentation generation/sync into the shipped product, resolve its priority relative to the existing F1–F7 promotion rule, and authorize the corresponding contract/release update. Only then should the gate default or public product documentation be changed.

**Opt-in-ready** describes the tested development path only. Shipped activation is **blocked by scope approval** and the release-qualification gaps above; this report does not classify it as a production candidate.
