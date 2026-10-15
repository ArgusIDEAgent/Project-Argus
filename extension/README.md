# CodeMind Extension

Offline regression command: `npm test` in this directory (or
`npm --prefix extension test` from the project root). It compiles TypeScript and
runs the Node resolver/rendering/session/transport/navigation tests using existing
dependencies. It launches no VS Code process and downloads nothing.
`npm run test:integration` retains real Electron/editor suites; use
`CODEMIND_TEST_VSCODE` for an existing binary. No local binary means explicitly
not executed, with no automatic download. See the project-root `TESTING.md`.

## Phase 10 development hybrid chat (default off)

Start the existing launcher from the project root with
`CODEMIND_ENABLE_HYBRID=1 .venv/bin/python start_codemind.py`. Enable
`codemind.hybridEnabled` in VS Code, open CodeMind Chat and select a repository
with a current published snapshot. The ordinary chat still uses `/ask` when this
setting is false. Search, reuse and documentation commands keep their existing
routes. No dependencies or model installation are introduced.

Quick/Standard/Deep change display only. Source clicks require fresh backend
authorization and the existing hash/range/containment/editor checks. Old answers
expire after five minutes or restart: ask again. Explanation failures preserve
independent deterministic findings; turn off `codemind.hybridAllowGeneration`
for deterministic support only. Editor context is sent for `this`/`here`/`current`/
`selected` questions, because the existing normalizer treats active-file context
as a scope. Named queries otherwise search the whole selected repository.

Rollback: disable `codemind.hybridEnabled`; optionally restart the backend without
`CODEMIND_ENABLE_HYBRID=1`. Old hybrid controls are disabled, settings/indexes remain
intact, and legacy chat remains available. There is no automatic production-default
migration. See project-root `query_system/PHASE10.md` for Phase 10 verification and limitations:
the actual target-sized knowledge-base build is currently blocked by an existing
TypeScript resolver failure; fixture opt-in verification is not production readiness.

## Phase 9 response preview (separate from production chat)

**CodeMind: Preview Validated Response (Phase 9)** opens an existing Phase 7/8
backend packet and asks you to authorize its repository folder without scanning
or indexing it. The preview supports Quick, Standard and Deep display, explicit
coverage/uncertainty, shared citations and copy-only follow-up investigations.
It never sends another query or invokes a model. Current chat message schemas and
`/ask`/`/retrieve` remain unchanged.

This development preview requires Workspace Trust, the CodeMind checkout with
its `.venv` beside `extension/`, and the existing snapshot store selected by
`CODEMIND_DATA_DIR` (default `~/.codemind/data`). The packet shape is
`{"package": <EvidencePackage>, "generation": <GenerationResult or null>, "depth": "standard"}`.
See project-root `query_system/PHASE9.md` for backend export and validation
details. Imported JSON is not authenticated; provenance verification authorizes
canonical source navigation, not arbitrary supplied prose. Invalid/stale sources
become non-clickable; unsaved editor changes also block navigation.

Preview is not automatically connected to production chat and is not packaged as
a standalone Python runtime inside the VSIX. Existing chat remains independently
available if the preview runtime is absent.

Tests: `npm run compile`, `node --test test/presentation.test.js`, and
`npm run test:integration` (includes real VS Code file opening/range selection).

## Existing chat

VS Code interface for the local CodeMind service. The chat webview opens from the Command Palette or the CodeMind status-bar button in any VS Code window. It opens before the service starts, so connection errors appear in the panel instead of hiding the chat. Select a local Git repository to start the Phase 1 inventory and automatic refresh.

The minimum declared VS Code version is 1.93. Chat remains available in Restricted Mode; scanning needs Workspace Trust.

## Development

Start the Python AI engine first:

```sh
cd ai-engine && python api.py
```

Then, from `extension/`, run `npm install && npm run compile`. Open the project root in VS Code, press F5, then run **CodeMind: Open Chat** in the Extension Development Host. The normal VS Code window needs an installed VSIX; F5 installs nothing into your normal profile. **CodeMind: Check Connection** sends a Hello request without opening the panel. The **CodeMind** output channel shows engine connection and errors.

To use CodeMind in any normal VS Code window, run this from `extension/` and then reload VS Code:

```sh
npm run package:vsix
code --install-extension ../codemind-extension-0.4.1.vsix --force
```

Run `npm run test:integration` to launch an Extension Development Host and verify that the extension starts and receives the Hello reply. If VS Code is not at the default macOS path, set `CODEMIND_TEST_VSCODE` to an existing executable; the test runner does not download one.

After selecting and indexing a repository, chat messages search its code. **CodeMind: Search Code** and **CodeMind: Check for Reusable Code** expose search and intent checks directly. Saved new symbols receive reuse checks while the panel is open. Results can open source, insert a reference, or record an ignore decision. Server requests stay on loopback; the engine writes its session token to `~/.codemind/data/session_token`, and the extension sends it as the `x-codemind-session` header.

### Documentation integration (scope gated)

The Docs and Sync Docs controls use the standalone Python engine, which must be installed and started separately from the VSIX. The implementation is off by default because the root README's locked product scope currently excludes documentation generation. In an approved development environment, install the root `requirements.txt` into the project's `.venv`, then launch from the project root with `CODEMIND_ENABLE_DOCS=1 python3 start_codemind.py`. Restart the engine after changing the flag. Select a trusted repository and wait for its published knowledge snapshot before using Sync Docs. Generated records are stored outside the repository under the configured CodeMind data directory. Without the flag, documentation requests explain that scope approval is pending; normal repository selection and chat continue working.

The implementation needs no Node documentation server or `sql.js`. `npm run test:docs` exercises the VS Code commands against a disposable Python snapshot when a local VS Code executable is available. The installed VSIX packages the compiled extension client; it does not bundle Python, the project's virtual environment, or generated documentation.
