# CodeMind (Argus) — AI Developer Knowledge Assistant

> **Status: LOCKED · v1.0.** This file is the project's contract: the code is changed to match it, never the reverse. Anything that adds, removes or reshapes what §1–§4 describe is a **scope change** and needs explicit owner confirmation first ([§5](#5-scope-change-protocol)).
>
> Live status → [`progress.md`](progress.md) · AI coding rules → [`CLAUDE.md`](CLAUDE.md)

## 1. The Singular Goal

**A VS Code assistant, backed by a local Python engine, that answers plain-English questions about a developer's own repository — grounded in vector-retrieved code and an AST-derived call graph, with citations to exact files and lines.**

*Why:* onboarding into a mature codebase is slow because architecture, dependencies and internal APIs live in people's heads. This tool answers from the code itself, inside the editor.

## 2. Core Scope (IN)

| ID | Feature | Done when… |
|----|---------|------------|
| **F1** | **Repository indexing.** Register a local repo → chunk → embed → store in Qdrant. Incremental (hash-based), prunes deleted files, refreshes on file save and on git HEAD/branch change. | Saving one file re-embeds only that file; deleting a file removes its vectors. |
| **F2** | **Semantic code search.** Natural-language query → top-K chunks with file path, line range and score. | A query returns relevant chunks and "Open" lands on the exact lines. |
| **F3** | **AST knowledge graph (Python).** Modules, classes, functions, methods; `calls` / `contains` / `imports` edges; persisted per repo; updated incrementally. | A cross-file call is an edge between two real nodes, and re-indexing one file does not lose edges coming from other files. |
| **F4** | **Structural queries.** Callers, callees, change-impact (transitive callers) and class members, shown as a text tree. | "What breaks if I change X?" lists affected symbols with `file:line`. |
| **F5** | **Grounded Q&A (graph-augmented RAG).** Answers built from retrieved chunks plus graph facts, with `file:line` citations. Covers "explain this module/function". | Every answer cites real code, or says the context is insufficient instead of guessing. |
| **F6** | **VS Code extension.** Chat panel exposing F1–F5, open-source-at-line, automatic refresh. | The demo script below runs end-to-end from the editor. |
| **F7** | **Evidence.** pytest suite for the engine core and a golden-question evaluation on a demo repo. | `pytest` is green and an evaluation table (retrieval hit@k, structural correctness) is committed. |

**Quality bars (apply to all of the above)**

- **Local-first.** Embeddings, vectors and the graph stay on the machine. The only user data that leaves it is the question and the retrieved snippets inside the LLM request.
- **Safe by default.** Engine binds to loopback only, every route requires the session token, secrets are redacted before embedding, repository content is treated as untrusted inside prompts, and the webview runs under a strict CSP.
- **Python-only structure.** F3/F4 cover Python only; other languages in `config.CODE_EXTENSIONS` still get F1, F2 and vector-only F5.
- **$0 to run.** Local embeddings, local Qdrant, free-tier LLM.

### Definition of Done — the evaluation demo (≤ 5 minutes)

1. Select a repository in VS Code → status shows it as indexed.
2. Ask *"Where is JWT authentication implemented?"* → grounded answer with a clickable `file:line` citation.
3. Ask *"What happens if I modify `verify_token`?"* → impact chain from the graph, explained in plain English.
4. Edit and save a file → automatic refresh → ask again; the answer reflects the change.
5. Show the green `pytest` run and the evaluation table.

If a feature is not on this path and not in the table above, it is not part of the project.

### Approved change-analysis foundation (2026-11-25)

The owner authorized Phase 1 of `CHANGE_ANALYSIS_IMPLEMENTATION_PLAN.md`: an opt-in Python library capturing saved JS/TS source against HEAD. This narrowly extends the Git-refresh-only restriction below. It does not replace F1–F7 or enable a new UI, API, structural analyzer, model call or test runner. The Python-only structural contract is unchanged by source capture.

`change_analysis.CaptureStore` captures one explicitly selected folder, including a folder inside a larger Git root. It records separate baseline/saved bytes, staging metadata, exclusions, conflicts and bounded content-based diffs. Captures are immutable Python values in bounded session memory; no source checkout, developer-file/index writes or persisted capture artifacts occur. See `change_analysis/PHASE1.md` for support, limits, verification and consistency limitations.

The 2026-11-26 owner request additionally authorizes the APFS atomic capture adapter and the initial Phase 2 source-evidence layer. `python -m change_analysis.atomic_session /path/to/repository` runs as the developer, delegates only mount/unmount authorization to Apple tools in Terminal, validates the read-only snapshot identity, and cleans up its private mount. Time Machine manages snapshot retention. The initially supported atomic layout has a contained `.git` directory; external metadata, linked worktrees and Git config includes fail closed. No resident privileged service is installed.

Phase 2 derives bounded syntax and resolved call/import/export evidence from captured bytes, with conservative correspondence and caller impact. Framework behavior remains unsupported. See `change_analysis/PHASE2.md`.

Phase 3 adds **CodeMind: Analyze Change**, an authenticated session job API, deterministic Simple/Detailed/Flow presentations and read-only captured-source navigation. Explicitly initiated atomic capture uses a dedicated Terminal for Apple administrator prompts. Saves and branch changes mark old results stale. Model wording remains disabled. See `change_analysis/PHASE3.md` for limits, setup and verification. This owner-approved workflow extends the earlier Git-refresh-only restriction; execution adapters and automatic source edits remain later/outside scope.

Phase 4 adds static Node test discovery and reviewable scenario/check plans to Analyze Change. Allowlisted package/lock/runtime metadata is captured atomically; configuration and scripts are never evaluated. Plans retain assertion sources or explicit unknown expectations. Review selection cannot execute tests. See `test_analysis/PHASE4.md`.

Phase 5 adds an internal isolation broker qualified on a dedicated Colima VZ Linux VM with no host directory sharing, a pinned Node image, no container network and enforced resource limits. `GET /v1/isolation/capabilities` reports its qualification separately from product execution availability. Only fixed qualification fixtures run in P5; review plans cannot execute. See `safe_runner/PHASE5.md` for setup, controls, evidence and how to stop the VM.

Phase 6 adds separately confirmed environment preparation and exact-plan execution for native root-package Node tests with an empty third-party dependency set and captured npm v2/v3 lockfile. Runs require current isolation qualification and native Workspace Trust-gated review dialogs, use disposable captured bytes, and report structured historical observations. Third-party package installation remains unsupported, so P6.3 is not complete. See `safe_runner/PHASE6.md`.

Phase 6 dependency completion adds the bounded registry-archive-v1 adapter: separately approved exact public npm tarball downloads with SHA-512 verification, offline sandbox extraction without lifecycle scripts, immutable retained archives and fresh dependency copies per run. Captured npm v2/v3 lockfiles must describe exact flat pure-JavaScript package trees. Unsupported ranges, private registries, native builds, optional/peer dependencies and layouts fail closed. This supersedes the empty-dependency-only limitation above. See `safe_runner/DEPENDENCY_PREPARATION.md` for the Phase 6 record and `safe_runner/PHASE8.md` for current qualification.

## 3. Non-Goals (OUT of scope)

Phase 7 completion: Vitest 5.1.2/Vite 8.4.0 now have a separately pinned immutable
toolchain, broker-owned JS-only configuration, independent JSON report handling
and local isolation/lifecycle qualification. This supersedes the earlier Vitest
gap below. Supported projects must match the exact framework lock graph and have
no custom configs/plugins or additional dependencies. See `safe_runner/VITEST.md`.
Phase 8 adds local release qualification, private summary export, retention cleanup
and independent execution opt-in. Execution defaults off; explicitly set
`CODEMIND_CHANGE_EXECUTION=1` to permit plan-specific reviews and launches after
current qualification. The supported macOS ARM64 release matrix, retained gates,
benchmarks, setup and limitations are in `safe_runner/PHASE8.md` and
`safe_runner/release-report.json`. Broader platform/application certification is
not claimed.

Owner-approved Phase 7 extension: native Node paired baseline/current retries,
conservative historical attribution and one reviewed literal-call temporary
template. Exact temporary bytes are shown and hashed into execution authorization;
developer source is never modified. Vitest is supported through its separately
qualified pinned profile; Phase 7 is complete within the documented compatibility
matrix. See `safe_runner/PHASE7.md` and `safe_runner/VITEST.md`.

Deliberately excluded. Do not build, stub or scaffold any of these.

- **Jira integration / bug-history lookup.** *(In the original vision; dropped.)*
- **Git commit-history and blame in answers.** Git is used only to detect HEAD / branch / working-tree changes for refresh. *(In the original vision; dropped.)*
- **Multi-language AST or call graph** (tree-sitter, JS/TS/Java graphs). Structure is Python-only.
- **Documentation generation and sync** (the "Docs" panel and `/docs/*`).
- **Reuse detection** ("Check for Reusable Code", reuse-on-save, use/ignore feedback).
- **Graphical dependency-graph canvas.** F4's text tree is the deliverable.
- **Changing code.** No generation, auto-fix, refactoring or agentic edits — the assistant reads and explains.
- **Perfect call resolution.** Static and name-based, best-effort: no type inference, dynamic dispatch or runtime tracing.
- **Multi-user, team, cloud or hosted deployment**, and any auth beyond the local session token.
- **Other front ends and distribution:** other IDEs, a web UI, Marketplace publishing. A locally installed VSIX is enough.
- **Engine process management.** The extension does not launch or supervise the engine; it is started manually (`python api.py`).
- **Model training, fine-tuning, or any dependency on a paid LLM.**

*Promotion rule:* an item moves from OUT to IN only through a scope change (§5), and not before F1–F7 are Done.

## 4. Tech Stack (final)

```
VS Code extension (TypeScript) ──HTTP + session token──▶ FastAPI engine (127.0.0.1:8000)
                                                          ├─ indexer ────────▶ Qdrant (vectors)
                                                          ├─ graph_builder ──▶ NetworkX graph (JSON)
                                                          └─ rag_pipeline ───▶ LiteLLM ──▶ OpenRouter
```

| Layer | Choice | Notes |
|---|---|---|
| Front end | VS Code extension, TypeScript, webview chat panel (VS Code ≥ 1.93) | No bundler or UI framework; Node's built-in `http` client |
| Engine API | FastAPI + Uvicorn on `127.0.0.1:8000` | `x-codemind-session` token on every route; background indexing jobs |
| Vector DB | Repository-local SQLite vector records + FAISS | Cosine, 384 dimensions; immutable collections per repository snapshot; remote Qdrant disabled in the active application |
| Embeddings | FastEmbed — `BAAI/bge-small-en-v1.5` | Local, CPU-only |
| Chunking | LangChain Text Splitters — `RecursiveCharacterTextSplitter.from_language` | 1200 chars, 150 overlap |
| Code analysis | Python `ast` → NetworkX `DiGraph` | JSON node-link file per repo |
| LLM | LiteLLM → OpenRouter, free-tier model | Model is configuration (`CODEMIND_LLM_MODEL`), intentionally not locked here |
| State & config | python-dotenv; repository knowledge/cache in `<repo>/.codemind/` | Read-only published SQLite/FAISS generations, persistent query cache, storage excluded from scanners/watchers; machine runtime token/model downloads remain in `~/.codemind/data/` |
| Testing | `@vscode/test-electron` (extension), pytest (engine, F7) | |

## 5. Scope-Change Protocol

1. A **scope change** is any request that adds, removes or materially reshapes a feature in §2–§3, swaps a component in §4, or restates the goal in §1.
2. The assistant flags it as **"⚠️ Scope change"** *before writing code*, says what it displaces and roughly what it costs, and waits for an explicit **"Confirmed: scope change"**.
3. On confirmation, §2/§3/§4 and the log below are updated in the same commit as the code. Without confirmation, the code is wrong, not the README.

## Quick Start *(living section — not covered by the lock)*

The current application runs a local Python engine and a VS Code extension.
Each selected repository stores its index in `<repo>/.codemind/` using SQLite
and FAISS. FastEmbed generates embeddings locally. Ollama is optional for
model-assisted answers.

### 1. Install the engine and extension dependencies

Use Python 3.14, Node.js 22 or newer with npm, Git, and VS Code 1.93 or newer.
These commands use a macOS or Linux shell. Run them from the CodeMind checkout.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix extension ci
```

Keep the virtual environment at `.venv`. The engine launcher and VS Code's
development tools use that path.

### 2. Set up Ollama for model-assisted answers

On macOS, install and open the Ollama app. For a CLI-only installation, start
`ollama serve` in a separate terminal. CodeMind connects to
`127.0.0.1:11434`. [Ollama macOS setup](https://docs.ollama.com/macos) ·
[Ollama CLI reference](https://docs.ollama.com/cli)

```sh
ollama pull llama3:latest
ollama list
```

`llama3:latest` is the default. Set `CODEMIND_LOCAL_MODEL` to an installed local
model to use another one. For evidence-only answers, start the engine with
`CODEMIND_LOCAL_MODEL= .venv/bin/python start_codemind.py`.

### 3. Start the CodeMind engine

```sh
.venv/bin/python start_codemind.py
```

Keep this terminal open. The engine listens on `127.0.0.1:8000` by default
and creates a session token under `~/.codemind/data/`. The first index uses
internet access to download FastEmbed's embedding model into that directory.
Later indexes reuse the model files. The repository's SQLite/FAISS generations
stay inside its own `.codemind/` directory. If you used an older Qdrant setup, remove
`QDRANT_URL` from your environment and `ai-engine/.env` before starting.

### 4. Build and install the VS Code extension

In another terminal at the checkout root:

```sh
npm --prefix extension run package:vsix
code --install-extension ./codemind-extension-0.4.1.vsix
```

If the `code` command is missing on macOS, use **Shell Command: Install 'code'
command in PATH** in the VS Code Command Palette. You can also install the
generated file via **Extensions: Install from VSIX...**. Reload VS Code if
upgrading an installed copy. [VS Code CLI reference](https://code.visualstudio.com/docs/configure/command-line)

### 5. Check the complete flow

1. Open a trusted local repository in VS Code. To use the bundled demo,
   run `code ai-engine/sample_code`. If VS Code shows **Restricted Mode**,
   use **Manage** to trust the demo folder.
2. Run **CodeMind: Check Connection** and look for **Python AI Engine connected**.
   Then run **CodeMind: Open Chat**. A single open workspace folder is selected
   automatically. Use **CodeMind: Select Repository** to choose another folder.
   Wait for the repository status to show a file count and **semantic search**.
3. In the demo, ask **Where is JWT authentication implemented?** Select **Open**
   on the `verify_token` card to jump to `auth_service.py:21`. If you set up
   Ollama, ask **How does JWT authentication work?** to try a model-assisted answer.
4. Run **CodeMind: Show Repository Storage** to inspect the index path,
   active snapshot and freshness. Saving source triggers an incremental refresh.

For connection or indexing errors, open the **CodeMind** output channel and
check the engine terminal. For a separate engine instance, export a distinct
`CODEMIND_PORT` and a private absolute `CODEMIND_DATA_DIR` in the environments
that start both the engine and VS Code. Use matching values in both processes.
See [engine settings](ai-engine/.env.example) and [test commands](TESTING.md).

## Scope Change Log *(append-only)*

| # | Date | Change | Confirmed by |
|---|------|--------|--------------|
| 11 | 2026-11-27 | Phase 8 local release gates, held-out fixtures, measurements, fresh installed VSIX validation, private run-summary export and default-off execution. Correct Phase 7 redaction identity and callback classification. | Owner requested safe Phase 8 implementation and prerequisite fixes in chat |
| 10 | 2026-11-27 | Complete Phase 7 with separately pinned and qualified Vitest toolchain/profile, static discovery, structured reports and paired attribution. Unsupported configurations fail closed; P8 untouched. | Owner requested safe completion of remaining Vitest requirements in chat |
| 9 | 2026-11-27 | Controlled Node literal templates and paired baseline/current retries with conservative attribution; Vitest qualification remains open. | Owner requested safe Phase 7 implementation in chat |
| 8 | 2026-11-27 | Complete P6 dependency preparation with separately authorized pinned public-registry downloads, offline bounded archive extraction and immutable dependency reuse. No general npm/scripts/native builds; P7 untouched. | Owner requested completion of remaining Phase 6 dependency requirements in chat |
| 7 | 2026-11-27 | Exact capture/plan/profile/policy authorization, separately confirmed empty-dependency preparation and isolated existing Node tests with structured historical results. External package preparation remains unsupported. | Owner requested safe Phase 6 implementation in chat |
| 6 | 2026-11-27 | Internal isolated runner foundation and fixed adversarial qualification in a dedicated local VM. Authenticated capability inspection only; product execution stays disabled pending Phase 6 authorization. | Owner requested Phase 5 implementation in chat |
| 5 | 2026-11-27 | Atomic test metadata capture, static Node profiles/catalog, bounded scenario plans and review selection. Product execution remains unavailable. | Owner requested Phase 4 implementation in chat |
| 4 | 2026-11-27 | Analyze Change command, authenticated jobs, deterministic explanations and captured-source navigation. Uses qualified APFS capture with explicit Terminal authorization. | Owner requested Phase 3 implementation in chat |
| 3 | 2026-11-26 | Qualified administrator-assisted APFS capture; began Phase 2 with versioned JS/TS syntax evidence and symbol correspondence. Existing chat/indexing and execution availability unchanged. | Owner requested safe atomic setup, Phase 1 completion and Phase 2 implementation in chat |
| 2 | 2026-11-25 | Phase 1 saved JS/TS change capture against HEAD, isolated from existing chat/indexing. Git baseline reads are permitted for this library; later analysis/UI/execution phases remain deferred. | Owner explicitly requested Phase 1 implementation in chat |
| 1 | 2026-11-20 | Repository-local persistence: reuse `.codemind` snapshots and cache across sessions; extension-triggered atomic publication of read-only generations; preserve historical external data. Replaces the outside-repository storage contract and active remote-vector storage. | Owner explicitly requested implementation in chat |
| 0 | 2026-10-09 | Baseline v1.0, derived from a review of the repository. Versus the original vision: **dropped** Jira bug-history, Git history/blame and the graphical dependency graph; **added** F7 (Evidence) and the Definition of Done; Docs-generation and Reuse-detection (built into the extension, absent from the vision) declared OUT. | Owner — on approval of this file |
