# CodeMind Extension

VS Code interface for the local CodeMind service. The chat webview opens from the Command Palette or the CodeMind status-bar button in any VS Code window. It opens before the service starts, so connection errors appear in the panel instead of hiding the chat. Select a local Git repository to start the Phase 1 inventory and automatic refresh.

The minimum declared VS Code version is 1.93. Chat remains available in Restricted Mode; scanning needs Workspace Trust.

## Development

From `extension/`, run `npm install` and `npm run compile`. The compile script copies the service into `extension/server/`. Open the project root in VS Code, press F5, then run **CodeMind: Open Chat** in the Extension Development Host. The normal VS Code window needs an installed VSIX; F5 installs nothing into your normal profile. **CodeMind: Check Connection** sends a Hello request without opening the panel. The **CodeMind** output channel shows server startup and errors.

To use CodeMind in any normal VS Code window, run this from `extension/` and then reload VS Code:

```sh
npm run package:vsix
code --install-extension ../codemind-extension-0.4.0.vsix --force
```

Run `npm run test:integration` to launch an Extension Development Host and verify that the extension starts its server and receives the Hello reply. On non-macOS systems, set `CODEMIND_VSCODE_EXECUTABLE` to a VS Code executable or allow the test runner to download one.

Run `ollama pull nomic-embed-text` and keep Ollama running for semantic retrieval. After selecting and indexing a repository, chat messages search its code. **CodeMind: Search Code** and **CodeMind: Check for Reusable Code** expose search and intent checks directly. Saved new symbols receive reuse checks while the panel is open. Results can open source, insert a reference, or record an ignore decision. **CodeMind: Sync Documentation** builds and updates graph-linked docs; **CodeMind: Open Documentation Overview** opens their overview and source references. For readable local-model prose, install `llama3.2`; factual Markdown works without it. Generative chat answers remain planned. Server requests stay on loopback and require a random session token. See `docs/PHASE_4_CONTRACT.md` in the project for setup and limits.
