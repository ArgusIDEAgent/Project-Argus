# CodeMind Extension

Phase 1 VS Code interface. It opens a chat webview and sends each message through the extension host to the local Node.js server. Opening the panel sends `Hello` automatically so the connection is visible immediately.

The minimum declared VS Code version is 1.93 because the planned terminal shell integration API became available in that release. Workspace Trust disables the extension in untrusted workspaces.

## Development

From `extension/`, run `npm install` and `npm run compile`. The compile script copies the dependency-free service into `extension/server/` so the extension can start it. Open the project root in VS Code, press F5, then run **CodeMind: Open Chat** in the Extension Development Host. **CodeMind: Check Connection** sends a Hello request without opening the panel. The **CodeMind** output channel shows server startup and errors.

Run `npm run test:integration` to launch an Extension Development Host and verify that the extension starts its server and receives the Hello reply. On non-macOS systems, set `CODEMIND_VSCODE_EXECUTABLE` to a VS Code executable or allow the test runner to download one.

The chat currently returns a greeting for each message; it does not call an LLM. Server requests stay on `127.0.0.1` and require a random token created at extension activation.
