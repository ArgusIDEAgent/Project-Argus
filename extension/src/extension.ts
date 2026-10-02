import { randomBytes } from 'node:crypto';
import * as vscode from 'vscode';
import { ServerManager } from './serverManager';

let server: ServerManager | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel('CodeMind');
  server = new ServerManager(context.extensionPath, output);
  context.subscriptions.push(output, server);

  context.subscriptions.push(vscode.commands.registerCommand('codemind.checkConnection', async () => {
    if (!server) throw new Error('CodeMind is not active.');
    const reply = await server.hello('Hello');
    void vscode.window.showInformationMessage(reply);
    return reply;
  }));

  let panel: vscode.WebviewPanel | undefined;
  context.subscriptions.push(vscode.commands.registerCommand('codemind.openChat', () => {
    if (panel) {
      panel.reveal();
      return;
    }

    panel = vscode.window.createWebviewPanel(
      'codemindChat',
      'CodeMind Chat',
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [] },
    );
    panel.onDidDispose(() => { panel = undefined; });
    panel.webview.onDidReceiveMessage(async (message: unknown) => {
      if (!isSendMessage(message) || !panel || !server) return;
      const currentPanel = panel;
      try {
        const reply = await server.hello(message.text);
        if (panel === currentPanel) {
          await currentPanel.webview.postMessage({ type: 'reply', text: reply });
        }
      } catch (error) {
        const detail = error instanceof Error ? error.message : 'Unknown server error.';
        output.appendLine(`Chat request failed: ${detail}`);
        if (panel === currentPanel) {
          await currentPanel.webview.postMessage({ type: 'error', text: detail });
        }
      }
    });
    panel.webview.html = chatHtml(panel.webview);
  }));

  try {
    await server.start();
  } catch (error) {
    const detail = error instanceof Error ? error.message : 'Unknown startup error.';
    output.appendLine(`Could not start local server: ${detail}`);
    void vscode.window.showWarningMessage('CodeMind server could not start. Open the CodeMind output for details.');
  }
}

export function deactivate(): void {
  server?.dispose();
  server = undefined;
}

function isSendMessage(value: unknown): value is { type: 'send'; text: string } {
  if (typeof value !== 'object' || value === null) return false;
  const message = value as { type?: unknown; text?: unknown };
  return message.type === 'send' && typeof message.text === 'string' &&
    message.text.trim().length > 0 && message.text.length <= 2000;
}

function chatHtml(webview: vscode.Webview): string {
  const nonce = randomBytes(16).toString('base64');
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <title>CodeMind Chat</title>
  <style nonce="${nonce}">
    body { margin: 0; padding: 20px; color: var(--vscode-foreground); background: var(--vscode-editor-background); font-family: var(--vscode-font-family); }
    h1 { font-size: 18px; margin: 0 0 8px; }
    .subtle { color: var(--vscode-descriptionForeground); margin: 0 0 20px; }
    #messages { min-height: 220px; max-height: calc(100vh - 180px); overflow-y: auto; }
    .message { padding: 10px 12px; margin-bottom: 10px; border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .message strong { display: block; margin-bottom: 4px; }
    .user { background: var(--vscode-input-background); }
    .server { background: var(--vscode-editor-inactiveSelectionBackground); }
    .error { border: 1px solid var(--vscode-errorForeground); }
    form { display: flex; gap: 8px; }
    input { flex: 1; min-width: 0; padding: 8px; color: var(--vscode-input-foreground); background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border); }
    button { padding: 8px 14px; color: var(--vscode-button-foreground); background: var(--vscode-button-background); border: none; cursor: pointer; }
    button:hover { background: var(--vscode-button-hoverBackground); }
    button:disabled { opacity: 0.6; cursor: wait; }
  </style>
</head>
<body>
  <h1>CodeMind Chat</h1>
  <p class="subtle">Phase 1 connection demo. Messages go to a local Node.js server.</p>
  <div id="messages" role="log" aria-live="polite"></div>
  <form id="chat-form">
    <input id="message-input" aria-label="Message" placeholder="Say hello…" maxlength="2000" autocomplete="off">
    <button id="send-button" type="submit">Send</button>
  </form>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const messages = document.getElementById('messages');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('message-input');
    const button = document.getElementById('send-button');

    function addMessage(author, text, kind) {
      const entry = document.createElement('div');
      entry.className = 'message ' + kind;
      const label = document.createElement('strong');
      label.textContent = author;
      const content = document.createElement('span');
      content.textContent = text;
      entry.append(label, content);
      messages.append(entry);
      messages.scrollTop = messages.scrollHeight;
    }

    function send(text) {
      const trimmed = text.trim();
      if (!trimmed) return;
      addMessage('You', trimmed, 'user');
      input.value = '';
      button.disabled = true;
      vscode.postMessage({ type: 'send', text: trimmed });
    }

    form.addEventListener('submit', event => {
      event.preventDefault();
      send(input.value);
    });

    window.addEventListener('message', event => {
      const message = event.data;
      if (message.type === 'reply') addMessage('CodeMind server', message.text, 'server');
      if (message.type === 'error') addMessage('Connection error', message.text, 'error');
      button.disabled = false;
      input.focus();
    });

    send('Hello');
  </script>
</body>
</html>`;
}
