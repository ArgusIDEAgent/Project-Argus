import { randomBytes } from 'node:crypto';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { ServerManager } from './serverManager';

let server: ServerManager | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel('CodeMind');
  server = new ServerManager(context.extensionPath, context.globalStorageUri.fsPath, output);
  context.subscriptions.push(output, server);

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.text = '$(comment-discussion) CodeMind';
  statusBar.tooltip = 'Open CodeMind Chat';
  statusBar.command = 'codemind.openChat';
  statusBar.show();
  context.subscriptions.push(statusBar);

  context.subscriptions.push(vscode.commands.registerCommand('codemind.checkConnection', async () => {
    if (!server) throw new Error('CodeMind is not active.');
    const reply = await server.hello('Hello');
    void vscode.window.showInformationMessage(reply);
    return reply;
  }));

  let panel: vscode.WebviewPanel | undefined;
  let selectionVersion = 0;

  async function registerFolder(rootPath: string, currentPanel: vscode.WebviewPanel): Promise<void> {
    const version = ++selectionVersion;
    if (!vscode.workspace.isTrusted) {
      await currentPanel.webview.postMessage({ type: 'repository', text: 'Trust this workspace to scan a repository.' });
      return;
    }
    try {
      await currentPanel.webview.postMessage({ type: 'repository', text: 'Connecting to repository...' });
      const repository = await server!.registerRepository(rootPath);
      if (panel !== currentPanel || version !== selectionVersion) return;
      await currentPanel.webview.postMessage({ type: 'repository', text: `${path.basename(repository.rootPath)} - scanning...` });
      const jobId = await server!.indexRepository(repository.id);
      while (panel === currentPanel && version === selectionVersion) {
        const job = await server!.getJob(jobId);
        if (job.state === 'completed') {
          await currentPanel.webview.postMessage({ type: 'repository', text: `${path.basename(repository.rootPath)} - ${job.result?.fileCount ?? 0} files scanned` });
          break;
        }
        if (job.state === 'failed') throw new Error(job.error || 'Repository scan failed.');
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Repository could not be opened.';
      output.appendLine(`Repository registration failed: ${detail}`);
      if (panel === currentPanel && version === selectionVersion) await currentPanel.webview.postMessage({ type: 'repository', text: detail });
    }
  }

  async function selectRepository(): Promise<void> {
    if (!panel) await vscode.commands.executeCommand('codemind.openChat', true);
    if (!panel) return;
    if (!vscode.workspace.isTrusted) {
      void vscode.window.showWarningMessage('Trust this workspace to scan repository files. Chat remains available.');
      return;
    }
    const folders = vscode.workspace.workspaceFolders ?? [];
    const browse = { label: 'Browse for repository...', path: undefined };
    const choice = await vscode.window.showQuickPick(
      [...folders.map(folder => ({ label: folder.name, description: folder.uri.fsPath, path: folder.uri.fsPath })), browse],
      { placeHolder: 'Select a repository for CodeMind' },
    );
    if (!choice) return;
    const rootPath = choice.path ?? (await vscode.window.showOpenDialog({
      canSelectFolders: true, canSelectFiles: false, canSelectMany: false, openLabel: 'Select Repository',
    }))?.[0]?.fsPath;
    if (rootPath && panel) await registerFolder(rootPath, panel);
  }

  context.subscriptions.push(vscode.commands.registerCommand('codemind.selectRepository', selectRepository));
  context.subscriptions.push(vscode.commands.registerCommand('codemind.openChat', (skipAutoSelect?: boolean) => {
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
    panel.onDidDispose(() => { panel = undefined; selectionVersion++; });
    panel.webview.onDidReceiveMessage(async (message: unknown) => {
      if (!panel || !server) return;
      if (isSelectMessage(message)) {
        await selectRepository();
        return;
      }
      if (!isSendMessage(message)) return;
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
    const activeFolder = vscode.window.activeTextEditor &&
      vscode.workspace.getWorkspaceFolder(vscode.window.activeTextEditor.document.uri);
    const folder = activeFolder ?? (vscode.workspace.workspaceFolders?.length === 1 ? vscode.workspace.workspaceFolders[0] : undefined);
    if (!skipAutoSelect && folder) void registerFolder(folder.uri.fsPath, panel);
  }));
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

function isSelectMessage(value: unknown): value is { type: 'selectRepository' } {
  return typeof value === 'object' && value !== null && (value as { type?: unknown }).type === 'selectRepository';
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
    :root {
      --chat-bg: var(--vscode-editor-background);
      --chat-fg: var(--vscode-foreground);
      --user-bg: #007aff;
      --user-fg: #ffffff;
      --server-bg: var(--vscode-editor-inactiveSelectionBackground);
      --server-fg: var(--vscode-foreground);
      --input-bg: var(--vscode-input-background);
      --input-border: var(--vscode-input-border);
    }
    body { 
      margin: 0; 
      padding: 0; 
      display: flex;
      flex-direction: column;
      height: 100vh;
      color: var(--chat-fg); 
      background: var(--chat-bg); 
      font-family: var(--vscode-font-family); 
    }
    .header {
      padding: 12px 20px;
      border-bottom: 1px solid var(--vscode-panel-border);
      background: var(--vscode-sideBar-background);
    }
    h1 { font-size: 16px; margin: 0 0 4px; font-weight: 600; }
    .repository { 
      display: flex; 
      align-items: center; 
      gap: 8px; 
      font-size: 12px;
      color: var(--vscode-descriptionForeground); 
    }
    .repository span { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .repository button { 
      padding: 2px 6px; 
      font-size: 11px;
      border-radius: 4px;
      color: var(--vscode-button-foreground); 
      background: var(--vscode-button-secondaryBackground); 
      border: 1px solid var(--vscode-button-border); 
      cursor: pointer; 
    }
    .repository button:hover { background: var(--vscode-button-secondaryHoverBackground); }
    
    #messages { 
      flex: 1;
      padding: 20px;
      overflow-y: auto; 
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    
    .message-wrapper {
      display: flex;
      flex-direction: column;
      max-width: 85%;
    }
    .message-wrapper.user {
      align-self: flex-end;
      align-items: flex-end;
    }
    .message-wrapper.server {
      align-self: flex-start;
      align-items: flex-start;
    }
    
    .message-author {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 4px;
      padding: 0 4px;
    }
    
    .message { 
      padding: 10px 14px; 
      border-radius: 16px; 
      white-space: pre-wrap; 
      overflow-wrap: anywhere;
      line-height: 1.4;
      font-size: 13px;
    }
    .user .message { 
      background: var(--user-bg); 
      color: var(--user-fg);
      border-bottom-right-radius: 4px;
    }
    .server .message { 
      background: var(--server-bg); 
      color: var(--server-fg);
      border-bottom-left-radius: 4px;
    }
    .error .message { 
      background: var(--vscode-inputValidation-errorBackground);
      border: 1px solid var(--vscode-inputValidation-errorBorder);
    }
    
    form { 
      padding: 14px 20px;
      border-top: 1px solid var(--vscode-panel-border);
      background: var(--vscode-sideBar-background);
      display: flex; 
      gap: 10px; 
    }
    input { 
      flex: 1; 
      min-width: 0;
      padding: 10px 14px; 
      border-radius: 20px;
      font-size: 13px;
      color: var(--vscode-input-foreground); 
      background: var(--input-bg); 
      border: 1px solid var(--input-border); 
      outline: none;
    }
    input:focus {
      border-color: var(--vscode-focusBorder);
    }
    button[type="submit"] { 
      padding: 8px 16px; 
      border-radius: 20px;
      font-weight: 500;
      color: var(--vscode-button-foreground); 
      background: var(--vscode-button-background); 
      border: none; 
      cursor: pointer; 
      transition: background 0.2s;
    }
    button[type="submit"]:hover { background: var(--vscode-button-hoverBackground); }
    button:disabled { opacity: 0.6; cursor: wait; }
  </style>
</head>
<body>
  <div class="header">
    <h1>CodeMind Chat</h1>
    <div class="repository">
      <span id="repository-status">No repository selected</span>
      <button id="select-repository" type="button">Change</button>
    </div>
  </div>
  
  <div id="messages" role="log" aria-live="polite"></div>
  
  <form id="chat-form">
    <input id="message-input" aria-label="Message" placeholder="Send a message..." maxlength="2000" autocomplete="off">
    <button id="send-button" type="submit">Send</button>
  </form>
  
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const messages = document.getElementById('messages');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('message-input');
    const button = document.getElementById('send-button');
    const repositoryStatus = document.getElementById('repository-status');
    document.getElementById('select-repository').addEventListener('click', () => vscode.postMessage({ type: 'selectRepository' }));

    function addMessage(author, text, kind) {
      const wrapper = document.createElement('div');
      wrapper.className = 'message-wrapper ' + kind;
      
      if (author !== 'You') {
        const label = document.createElement('div');
        label.className = 'message-author';
        label.textContent = author;
        wrapper.append(label);
      }
      
      const content = document.createElement('div');
      content.className = 'message';
      content.textContent = text;
      
      wrapper.append(content);
      messages.append(wrapper);
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
      if (message.type === 'repository') { repositoryStatus.textContent = message.text; return; }
      if (message.type === 'reply') addMessage('CodeMind server', message.text, 'server');
      if (message.type === 'error') addMessage('Error', message.text, 'error');
      button.disabled = false;
      input.focus();
    });

  </script>
</body>
</html>`;
}
