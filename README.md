# CodeMind

CodeMind is a planned VS Code assistant that explains how a codebase works, traces dependencies, and investigates development failures using source-linked evidence from code, Git history, and related records.

**Status:** Phases 0-4 of the PDF blueprint are implemented. The extension opens chat from any VS Code window, indexes a selected Git repository, and provides AST graph traversal, local semantic search, duplicate/reuse suggestions, and graph-linked documentation sync. Search and source navigation are active; generative repository answers remain planned. Semantic search requires a local Ollama embedding model and falls back visibly to lexical search when unavailable. See the [Phase 4 contract](docs/PHASE_4_CONTRACT.md), [Phase 3 contract](docs/PHASE_3_CONTRACT.md), [Phase 0 contract](docs/PHASE_0_CONTRACT.md), [Phase 1 contract](docs/PHASE_1_CONTRACT.md), [Phase 2 contract](docs/PHASE_2_CONTRACT.md), and [technical architecture](docs/ARCHITECTURE.md).

## Product promise

Ask a question such as “What calls this API endpoint?” or “Have we seen this failure before?” and receive an answer with clickable file, line, revision, and commit references. CodeMind must distinguish confirmed links from inferred ones and show when indexed data is stale.

## First supported workflow

1. Open a TypeScript/JavaScript repository in VS Code.
2. CodeMind indexes the repository and updates changed files.
3. Ask about an endpoint or function in the assistant view.
4. Inspect a traced path with citations to the exact source revision.
5. Run a command that fails; CodeMind captures its output, finds relevant code and similar retained failures, then suggests a reviewable fix.

The first investor release is a **working, local-first vertical slice**, not a claim of support for every language or of automatic error-free fixes.

## Planned components

- `extension/`: VS Code interface, workspace events, terminal events, citations, and review UI.
- `service/`: local indexing, retrieval, query orchestration, and model adapters.
- `fixtures/`: consent-safe sample application, issue records, commits, and reproducible failure.
- `evals/`: fixed questions and scoring harness for retrieval, answers, citations, and latency.
- `docs/`: architecture, build gates, setup, demo, and operations notes.

The extension and service contain the Phase 0-4 implementation. Semantic fixtures and the real-model evaluation runner live under `service/test/fixtures/phase3` and `service/scripts`; Phase 4 onboarding questions live in `evals/phase4-questions.json`.

## Run the local service

1. Run `npm install` in `extension/`.
2. Open this project folder in VS Code and press **F5** to launch **Run CodeMind Extension**.
3. In the Extension Development Host, run **CodeMind: Open Chat** from the Command Palette.
4. Open a trusted Git repository in the Development Host or use **Select Repository** in chat. The header shows the number of source files scanned. Send `Hello` to verify the local reply. Indexing also builds the graph; inspect it through the [Phase 2 graph API](docs/PHASE_2_CONTRACT.md).

For chat in normal VS Code windows and other repositories, [install the packaged extension](extension/README.md) into your VS Code profile and reload VS Code. The server uses an ephemeral `127.0.0.1` port and a per-session token. It starts on the first request and stops when the extension host shuts down.

## Delivery target

Planning assumption: two full-time engineers, one design/product contributor, and a 12-week investor demo target. A solo build will take longer. Dates and targets are estimates until Phase 0 measures the sample repository and confirms the stack.
