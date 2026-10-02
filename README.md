# CodeMind

CodeMind is a planned VS Code assistant that explains how a codebase works, traces dependencies, and investigates development failures using source-linked evidence from code, Git history, and related records.

**Status:** Phase 1 working slice. The VS Code extension opens a chat panel, starts a local Node.js server, and exchanges a Hello message. Code indexing, retrieval, history, and model answers are planned next. See the [technical architecture](docs/ARCHITECTURE.md), [build plan](docs/BUILD_PLAN.md), and [investor demo brief](docs/INVESTOR_DEMO.md).

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

The extension and service now contain the Phase 1 implementation; the fixture and evaluation directories remain scaffolds.

## Run the Phase 1 demo

1. Run `npm install` in `extension/`.
2. Open this project folder in VS Code and press **F5** to launch **Run CodeMind Extension**.
3. In the Extension Development Host, run **CodeMind: Open Chat** from the Command Palette.
4. The panel sends `Hello` automatically and displays the local server's reply. Send another message to repeat the round trip.

The server uses an ephemeral `127.0.0.1` port and a per-session token. It starts with extension activation and stops when the extension host shuts down. See [extension/README.md](extension/README.md) for troubleshooting.

## Delivery target

Planning assumption: two full-time engineers, one design/product contributor, and a 12-week investor demo target. A solo build will take longer. Dates and targets are estimates until Phase 0 measures the sample repository and confirms the stack.
