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

## 3. Non-Goals (OUT of scope)

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
| Vector DB | Qdrant (Docker, loopback, API key) | One collection `codemind_codebase`, cosine, 384-dim, filtered by `repo_id` |
| Embeddings | FastEmbed — `BAAI/bge-small-en-v1.5` | Local, CPU-only |
| Chunking | LangChain Text Splitters — `RecursiveCharacterTextSplitter.from_language` | 1200 chars, 150 overlap |
| Code analysis | Python `ast` → NetworkX `DiGraph` | JSON node-link file per repo |
| LLM | LiteLLM → OpenRouter, free-tier model | Model is configuration (`CODEMIND_LLM_MODEL`), intentionally not locked here |
| State & config | python-dotenv; state in `~/.codemind/data/` | Outside target repos, so file watchers never loop |
| Testing | `@vscode/test-electron` (extension), pytest (engine, F7) | |

## 5. Scope-Change Protocol

1. A **scope change** is any request that adds, removes or materially reshapes a feature in §2–§3, swaps a component in §4, or restates the goal in §1.
2. The assistant flags it as **"⚠️ Scope change"** *before writing code*, says what it displaces and roughly what it costs, and waits for an explicit **"Confirmed: scope change"**.
3. On confirmation, §2/§3/§4 and the log below are updated in the same commit as the code. Without confirmation, the code is wrong, not the README.

## Quick Start *(living section — not covered by the lock)*

**Prerequisites:** Python 3.10+, Docker, Node.js, VS Code ≥ 1.93, a free [OpenRouter](https://openrouter.ai/) API key.

```bash
# 1. Install
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Qdrant — loopback only, API-key protected
export QDRANT_API_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
docker run -d --name qdrant \
  -p 127.0.0.1:6333:6333 -p 127.0.0.1:6334:6334 \
  -e QDRANT__SERVICE__API_KEY="$QDRANT_API_KEY" \
  -v qdrant_storage:/qdrant/storage qdrant/qdrant

# 3. Configure ai-engine/.env (see ai-engine/.env.example)
#    OPENROUTER_API_KEY=...    QDRANT_API_KEY=<same value as above>

# 4. Try the engine from the CLI
cd ai-engine
python indexer.py --dir ./sample_code            # add --full-reindex to rebuild
python search.py --query "How does authentication work?"
python graph_query.py --stats
python rag_pipeline.py --query "Where is JWT authentication implemented?"

# 5. Run the engine for the extension (127.0.0.1:8000)
python api.py
```

**Extension:** `cd extension && npm install && npm run compile`, open the repo root in VS Code, press **F5**, then run **CodeMind: Open Chat** in the Extension Development Host. Details: [`extension/README.md`](extension/README.md).

## Scope Change Log *(append-only)*

| # | Date | Change | Confirmed by |
|---|------|--------|--------------|
| 0 | 2026-10-09 | Baseline v1.0, derived from a review of the repository. Versus the original vision: **dropped** Jira bug-history, Git history/blame and the graphical dependency graph; **added** F7 (Evidence) and the Definition of Done; Docs-generation and Reuse-detection (built into the extension, absent from the vision) declared OUT. | Owner — on approval of this file |
