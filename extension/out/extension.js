"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
exports.chatHtml = chatHtml;
const node_crypto_1 = require("node:crypto");
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
const vscode = __importStar(require("vscode"));
const serverManager_1 = require("./serverManager");
const sourceNavigation_1 = require("./sourceNavigation");
let server;
async function activate(context) {
    const output = vscode.window.createOutputChannel('CodeMind');
    server = new serverManager_1.ServerManager(context.extensionPath, context.globalStorageUri.fsPath, output);
    context.subscriptions.push(output, server);
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBar.text = '$(comment-discussion) CodeMind';
    statusBar.tooltip = 'Open CodeMind Chat';
    statusBar.command = 'codemind.openChat';
    statusBar.show();
    context.subscriptions.push(statusBar);
    context.subscriptions.push(vscode.commands.registerCommand('codemind.checkConnection', async () => {
        if (!server)
            throw new Error('CodeMind is not active.');
        const reply = await server.hello('Hello');
        void vscode.window.showInformationMessage(reply);
        return reply;
    }));
    let panel;
    let selectionVersion = 0;
    let repositoryWatcher;
    let vscodeWatcher;
    let refreshTimer;
    let gitStateTimer;
    let selectedRepository;
    let lastGitState;
    let refreshing = false;
    let refreshAgain = false;
    const pendingSymbols = new Map();
    const resultActions = new Map();
    const docSourceActions = new Map();
    async function showResults(result, title) {
        const selected = selectedRepository;
        if (!panel || !selected)
            return;
        resultActions.clear();
        const results = result.results.map(item => {
            const key = (0, node_crypto_1.randomBytes)(12).toString('hex');
            resultActions.set(key, { result: item, repoId: selected.id, queryHash: result.queryHash });
            return { ...item, key, canIgnore: Boolean(result.queryHash) };
        });
        await panel.webview.postMessage({ type: 'results', title, results, warning: result.warning });
    }
    async function updateDocsStatus(selected, known) {
        if (!panel || !server)
            return;
        const status = known ?? (await server.docSections(selected.id)).status;
        if (selectedRepository === selected)
            await panel.webview.postMessage({ type: 'docsStatus', status });
    }
    async function showDoc(section) {
        const selected = selectedRepository;
        if (!panel || !selected || !server)
            return;
        const listing = await server.docSections(selected.id);
        if (selectedRepository !== selected)
            return;
        docSourceActions.clear();
        const seen = new Set();
        const evidence = (section?.evidence || []).filter(item => {
            if (!item.path || !item.sourceHash || seen.has(item.entityId))
                return false;
            seen.add(item.entityId);
            return true;
        }).map(item => {
            const key = (0, node_crypto_1.randomBytes)(12).toString('hex');
            docSourceActions.set(key, { repoId: selected.id, path: item.path, sourceHash: item.sourceHash,
                startLine: item.startLine, endLine: item.endLine });
            return { key, path: item.path, name: item.name, startLine: item.startLine };
        });
        await panel.webview.postMessage({ type: 'docs', section, sections: listing.sections, evidence });
        await updateDocsStatus(selected, listing.status);
    }
    async function openIndexedSource(selected, source) {
        if (!vscode.workspace.isTrusted || !source.path)
            return;
        const file = (0, sourceNavigation_1.verifiedSourcePath)(selected.rootPath, source.path, source.sourceHash);
        const document = await vscode.workspace.openTextDocument(file);
        if (document.isDirty)
            throw new Error('Save this document and refresh before opening an indexed source.');
        await vscode.window.showTextDocument(document, { viewColumn: vscode.ViewColumn.Beside,
            selection: new vscode.Range(Math.max(0, source.startLine - 1), 0, Math.max(0, source.endLine - 1), 0) });
    }
    function indexedLabel(rootPath, result) {
        if (!result)
            return `${path.basename(rootPath)} - scan complete`;
        const revision = result.commitHash === 'unborn' ? 'unborn' : result.commitHash.slice(0, 7);
        return `${path.basename(rootPath)} - ${result.fileCount} files - ${revision}${result.dirtyFingerprint === 'clean' ? '' : ' (dirty)'}`;
    }
    function stopWatching() {
        repositoryWatcher?.close();
        repositoryWatcher = undefined;
        vscodeWatcher?.dispose();
        vscodeWatcher = undefined;
        if (refreshTimer)
            clearTimeout(refreshTimer);
        refreshTimer = undefined;
        if (gitStateTimer)
            clearInterval(gitStateTimer);
        gitStateTimer = undefined;
        selectedRepository = undefined;
        lastGitState = undefined;
        pendingSymbols.clear();
        resultActions.clear();
        docSourceActions.clear();
    }
    async function runRefresh() {
        const selected = selectedRepository;
        if (!selected || !server || !vscode.workspace.isTrusted)
            return;
        if (refreshing) {
            refreshAgain = true;
            return;
        }
        refreshing = true;
        try {
            const jobId = await server.refreshRepository(selected.id);
            for (;;) {
                const job = await server.getJob(jobId);
                if (job.state === 'completed') {
                    if (selectedRepository === selected && job.result)
                        lastGitState = `${job.result.commitHash}:${job.result.branch}`;
                    if (selectedRepository === selected && panel) {
                        await panel.webview.postMessage({ type: 'repository',
                            text: indexedLabel(selected.rootPath, job.result) + (job.result?.semantic?.state === 'degraded' ? ' - lexical search' : ' - semantic search') });
                        await updateDocsStatus(selected, job.result?.docs);
                        const checks = [...pendingSymbols];
                        pendingSymbols.clear();
                        for (const [filePath, previous] of checks) {
                            if (selectedRepository !== selected)
                                break;
                            const graph = await server.graphFile(selected.id, filePath);
                            for (const node of graph.nodes.filter(node => node.type === 'symbol' &&
                                ['function', 'method', 'class', 'component', 'ml_entry'].includes(node.kind) && !previous.has(node.id)).slice(0, 3)) {
                                const result = await server.reuse(selected.id, { entityId: node.id });
                                if (selectedRepository === selected && result.results.length)
                                    await showResults(result, 'Reuse suggestions');
                            }
                        }
                    }
                    break;
                }
                if (job.state === 'failed')
                    throw new Error(job.error || 'Repository refresh failed.');
                await new Promise(resolve => setTimeout(resolve, 300));
            }
        }
        catch (error) {
            output.appendLine(`Repository refresh failed: ${error instanceof Error ? error.message : error}`);
        }
        finally {
            refreshing = false;
            if (refreshAgain) {
                refreshAgain = false;
                scheduleRefresh();
            }
        }
    }
    function scheduleRefresh() {
        if (!selectedRepository)
            return;
        if (refreshTimer)
            clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => { refreshTimer = undefined; void runRefresh(); }, 450);
    }
    function startVscodeWatcher(rootPath) {
        try {
            vscodeWatcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(rootPath, '**/*'));
            vscodeWatcher.onDidChange(scheduleRefresh);
            vscodeWatcher.onDidCreate(scheduleRefresh);
            vscodeWatcher.onDidDelete(scheduleRefresh);
        }
        catch (error) {
            output.appendLine(`Repository file watcher unavailable: ${error instanceof Error ? error.message : error}`);
        }
    }
    function watchRepository(repository, commitHash, branch) {
        stopWatching();
        selectedRepository = repository;
        lastGitState = `${commitHash}:${branch}`;
        try {
            repositoryWatcher = fs.watch(repository.rootPath, { recursive: true }, (_event, filename) => {
                const changed = filename?.toString().split(path.sep).join('/') || '';
                if (changed.startsWith('.git/') && !/^(\.git\/(HEAD|index|packed-refs|refs\/|logs\/HEAD))/.test(changed))
                    return;
                if (/(^|\/)(node_modules|build|dist|\.venv|venv|__pycache__)(\/|$)/.test(changed))
                    return;
                scheduleRefresh();
            });
            repositoryWatcher.on('error', error => {
                output.appendLine(`Repository watcher failed: ${error.message}`);
                repositoryWatcher?.close();
                repositoryWatcher = undefined;
                startVscodeWatcher(repository.rootPath);
            });
        }
        catch (error) {
            output.appendLine(`Repository watcher unavailable: ${error instanceof Error ? error.message : error}`);
            startVscodeWatcher(repository.rootPath);
        }
        gitStateTimer = setInterval(() => {
            if (selectedRepository !== repository || !server)
                return;
            void server.liveState(repository.id).then(state => {
                if (selectedRepository !== repository)
                    return;
                const current = `${state.commitHash}:${state.branch}`;
                if (current !== lastGitState) {
                    lastGitState = current;
                    scheduleRefresh();
                }
            }).catch(error => output.appendLine(`Git state check failed: ${error instanceof Error ? error.message : error}`));
        }, 3000);
    }
    context.subscriptions.push({ dispose: stopWatching });
    context.subscriptions.push(vscode.workspace.onDidSaveTextDocument(async (document) => {
        const selected = selectedRepository;
        if (selected && document.uri.scheme === 'file' &&
            document.uri.fsPath.startsWith(selected.rootPath + path.sep)) {
            if (vscode.workspace.getConfiguration('codemind').get('checkReuseOnSave', true)) {
                const filePath = path.relative(selected.rootPath, document.uri.fsPath).split(path.sep).join('/');
                const previous = await server.graphFile(selected.id, filePath).catch(() => ({ nodes: [] }));
                if (selectedRepository !== selected)
                    return;
                if (!pendingSymbols.has(filePath))
                    pendingSymbols.set(filePath, new Set(previous.nodes.map(node => node.id)));
            }
            scheduleRefresh();
        }
    }));
    async function registerFolder(rootPath, currentPanel) {
        const version = ++selectionVersion;
        stopWatching();
        await currentPanel.webview.postMessage({ type: 'docsStatus', status: {
                sectionCount: 0, currentCount: 0, staleCount: 0, affectedCount: 0, generatedCount: 0,
            } });
        if (!vscode.workspace.isTrusted) {
            await currentPanel.webview.postMessage({ type: 'repository', text: 'Trust this workspace to scan a repository.' });
            return;
        }
        try {
            await currentPanel.webview.postMessage({ type: 'repository', text: 'Connecting to repository...' });
            const repository = await server.registerRepository(rootPath);
            if (panel !== currentPanel || version !== selectionVersion)
                return;
            await currentPanel.webview.postMessage({ type: 'repository', text: `${path.basename(repository.rootPath)} - scanning...` });
            const jobId = await server.indexRepository(repository.id);
            while (panel === currentPanel && version === selectionVersion) {
                const job = await server.getJob(jobId);
                if (panel !== currentPanel || version !== selectionVersion)
                    break;
                if (job.state === 'completed') {
                    await currentPanel.webview.postMessage({ type: 'repository', text: indexedLabel(repository.rootPath, job.result) +
                            (job.result?.semantic?.state === 'degraded' ? ' - lexical search' : ' - semantic search') });
                    watchRepository(repository, job.result?.commitHash || 'unborn', job.result?.branch || 'HEAD');
                    await updateDocsStatus(repository, job.result?.docs);
                    scheduleRefresh();
                    break;
                }
                if (job.state === 'failed')
                    throw new Error(job.error || 'Repository scan failed.');
                await new Promise(resolve => setTimeout(resolve, 300));
            }
        }
        catch (error) {
            const detail = error instanceof Error ? error.message : 'Repository could not be opened.';
            output.appendLine(`Repository registration failed: ${detail}`);
            if (panel === currentPanel && version === selectionVersion)
                await currentPanel.webview.postMessage({ type: 'repository', text: detail });
        }
    }
    async function selectRepository() {
        if (!panel)
            await vscode.commands.executeCommand('codemind.openChat', true);
        if (!panel)
            return;
        if (!vscode.workspace.isTrusted) {
            void vscode.window.showWarningMessage('Trust this workspace to scan repository files. Chat remains available.');
            return;
        }
        const folders = vscode.workspace.workspaceFolders ?? [];
        const browse = { label: 'Browse for repository...', path: undefined };
        const choice = await vscode.window.showQuickPick([...folders.map(folder => ({ label: folder.name, description: folder.uri.fsPath, path: folder.uri.fsPath })), browse], { placeHolder: 'Select a repository for CodeMind' });
        if (!choice)
            return;
        const rootPath = choice.path ?? (await vscode.window.showOpenDialog({
            canSelectFolders: true, canSelectFiles: false, canSelectMany: false, openLabel: 'Select Repository',
        }))?.[0]?.fsPath;
        if (rootPath && panel)
            await registerFolder(rootPath, panel);
    }
    context.subscriptions.push(vscode.commands.registerCommand('codemind.selectRepository', selectRepository));
    async function runSearch(reuse, suppliedQuery) {
        if (!panel)
            await vscode.commands.executeCommand('codemind.openChat');
        const selected = selectedRepository;
        if (!selected || !vscode.workspace.isTrusted) {
            void vscode.window.showInformationMessage('Select a trusted repository and wait for indexing to finish.');
            return;
        }
        const query = suppliedQuery || await vscode.window.showInputBox({ prompt: reuse ? 'Describe the code you intend to write' : 'Search repository code' });
        if (!query?.trim())
            return;
        try {
            const result = reuse ? await server.reuse(selected.id, { query }) : await server.search(selected.id, query);
            if (selectedRepository === selected)
                await showResults(result, reuse ? 'Reuse suggestions' : 'Code search');
        }
        catch (error) {
            if (panel)
                await panel.webview.postMessage({ type: 'error', text: error instanceof Error ? error.message : 'Search failed.' });
        }
        finally {
            if (panel)
                await panel.webview.postMessage({ type: 'requestDone' });
        }
    }
    context.subscriptions.push(vscode.commands.registerCommand('codemind.searchCode', () => runSearch(false)));
    context.subscriptions.push(vscode.commands.registerCommand('codemind.checkReuse', () => runSearch(true)));
    async function openDocsOverview() {
        if (!panel)
            await vscode.commands.executeCommand('codemind.openChat', true);
        if (!selectedRepository)
            await selectRepository();
        const selected = selectedRepository;
        if (!selected || !vscode.workspace.isTrusted)
            return;
        try {
            await showDoc(await server.docsOverview(selected.id));
        }
        catch (error) {
            await panel?.webview.postMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not open documentation.' });
        }
    }
    async function syncDocs() {
        if (!panel)
            await vscode.commands.executeCommand('codemind.openChat', true);
        if (!selectedRepository)
            await selectRepository();
        const selected = selectedRepository;
        if (!selected || !vscode.workspace.isTrusted)
            return;
        try {
            await panel?.webview.postMessage({ type: 'docsBusy', busy: true });
            const listing = await server.docSections(selected.id);
            const result = await server.syncDocs(selected.id, listing.status.sectionCount ? 'incremental' : 'baseline');
            if (selectedRepository === selected) {
                await updateDocsStatus(selected, result.status);
                await showDoc(result.overview);
            }
        }
        catch (error) {
            await panel?.webview.postMessage({ type: 'error', text: error instanceof Error ? error.message : 'Documentation sync failed.' });
        }
        finally {
            await panel?.webview.postMessage({ type: 'docsBusy', busy: false });
        }
    }
    context.subscriptions.push(vscode.commands.registerCommand('codemind.openDocsOverview', openDocsOverview));
    context.subscriptions.push(vscode.commands.registerCommand('codemind.syncDocs', syncDocs));
    context.subscriptions.push(vscode.commands.registerCommand('codemind.openChat', (skipAutoSelect) => {
        if (panel) {
            panel.reveal();
            return;
        }
        panel = vscode.window.createWebviewPanel('codemindChat', 'CodeMind Chat', vscode.ViewColumn.Beside, { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [] });
        panel.onDidDispose(() => { panel = undefined; selectionVersion++; stopWatching(); });
        panel.webview.onDidReceiveMessage(async (message) => {
            if (!panel || !server)
                return;
            const action = message;
            if (action?.type === 'search' || action?.type === 'reuse') {
                if (typeof action.text === 'string' && action.text.length <= 2000)
                    await runSearch(action.type === 'reuse', action.text);
                return;
            }
            if (action?.type === 'docsOverview') {
                await openDocsOverview();
                return;
            }
            if (action?.type === 'docsSync') {
                await syncDocs();
                return;
            }
            if (action?.type === 'docSection' && typeof action.key === 'string' && /^[a-f0-9]{40}$/.test(action.key)) {
                const selected = selectedRepository;
                if (selected && vscode.workspace.isTrusted) {
                    try {
                        await showDoc(await server.docSection(selected.id, action.key));
                    }
                    catch (error) {
                        await panel.webview.postMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not open section.' });
                    }
                }
                return;
            }
            if (action?.type === 'docSource' && typeof action.key === 'string') {
                const selected = selectedRepository;
                const source = docSourceActions.get(action.key);
                if (selected && source?.repoId === selected.id) {
                    try {
                        await openIndexedSource(selected, source);
                    }
                    catch (error) {
                        await panel.webview.postMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not open source.' });
                    }
                }
                return;
            }
            if (action?.type === 'resultAction') {
                const entry = typeof action.key === 'string' ? resultActions.get(action.key) : undefined;
                const selected = selectedRepository;
                if (!entry || !selected || entry.repoId !== selected.id || !vscode.workspace.isTrusted)
                    return;
                try {
                    if (action.action === 'openDoc' && entry.result.docSectionId) {
                        await showDoc(await server.docSection(selected.id, entry.result.docSectionId));
                    }
                    else if (action.action === 'ignore' && entry.queryHash) {
                        await server.feedback(selected.id, entry.queryHash, entry.result.entityId, 'ignore');
                        resultActions.delete(action.key);
                        await panel.webview.postMessage({ type: 'ignored', key: action.key });
                    }
                    else if (action.action === 'open' || action.action === 'use') {
                        if (!entry.result.path)
                            return;
                        await openIndexedSource(selected, entry.result);
                        if (action.action === 'use') {
                            if (entry.queryHash)
                                await server.feedback(selected.id, entry.queryHash, entry.result.entityId, 'use');
                            await panel.webview.postMessage({ type: 'useReference', text: `${entry.result.name} (${entry.result.path}:${entry.result.startLine})` });
                        }
                    }
                }
                catch (error) {
                    await panel.webview.postMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not open result.' });
                }
                return;
            }
            if (isSelectMessage(message)) {
                await selectRepository();
                return;
            }
            if (!isSendMessage(message))
                return;
            const currentPanel = panel;
            try {
                if (selectedRepository && !/^(hi|hello)$/i.test(message.text.trim())) {
                    await runSearch(false, message.text);
                    return;
                }
                const reply = await server.hello(message.text);
                if (panel === currentPanel) {
                    await currentPanel.webview.postMessage({ type: 'reply', text: reply });
                }
            }
            catch (error) {
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
        if (!skipAutoSelect && folder)
            void registerFolder(folder.uri.fsPath, panel);
    }));
}
function deactivate() {
    server?.dispose();
    server = undefined;
}
function isSendMessage(value) {
    if (typeof value !== 'object' || value === null)
        return false;
    const message = value;
    return message.type === 'send' && typeof message.text === 'string' &&
        message.text.trim().length > 0 && message.text.length <= 2000;
}
function isSelectMessage(value) {
    return typeof value === 'object' && value !== null && value.type === 'selectRepository';
}
function chatHtml(webview) {
    const nonce = (0, node_crypto_1.randomBytes)(16).toString('base64');
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
    .search-tools { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .search-tools button, .result-actions button { color: var(--vscode-button-foreground); background: var(--vscode-button-background); border: 0; padding: 5px 8px; cursor: pointer; }
    .code-result { border-bottom: 1px solid var(--vscode-panel-border); padding: 10px 0; overflow-wrap: anywhere; }
    .result-actions { display: flex; gap: 8px; margin-top: 6px; }
    .docs-status { margin-top: 6px; font-size: 11px; color: var(--vscode-descriptionForeground); }
    .docs-view { width: 100%; line-height: 1.5; overflow-wrap: anywhere; }
    .docs-view h2 { font-size: 16px; margin: 12px 0 6px; }
    .docs-view h3 { font-size: 14px; margin: 10px 0 4px; }
    .docs-view p, .docs-view ul, .docs-view pre { margin: 4px 0 8px; }
    .docs-view pre { white-space: pre-wrap; background: var(--vscode-textCodeBlock-background); padding: 8px; }
    .docs-nav, .docs-sources { display: flex; flex-wrap: wrap; gap: 6px; border-top: 1px solid var(--vscode-panel-border); padding-top: 8px; margin-top: 12px; }
    .docs-nav button, .docs-sources button { color: var(--vscode-textLink-foreground); background: transparent; border: 1px solid var(--vscode-panel-border); padding: 4px 6px; cursor: pointer; max-width: 100%; overflow-wrap: anywhere; text-align: left; }
    .docs-meta { font-size: 11px; color: var(--vscode-descriptionForeground); }
  </style>
</head>
<body>
  <div class="header">
    <h1>CodeMind Chat</h1>
    <div class="repository">
      <span id="repository-status">No repository selected</span>
      <button id="select-repository" type="button">Change</button>
    </div>
    <div id="docs-status" class="docs-status">Docs not generated</div>
    <div class="search-tools"><button id="search-code" type="button">Search Code</button><button id="check-reuse" type="button">Check Reuse</button><button id="open-docs" type="button">Docs</button><button id="sync-docs" type="button">Sync Docs</button></div>
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
    const docsStatus = document.getElementById('docs-status');
    const syncDocsButton = document.getElementById('sync-docs');
    document.getElementById('select-repository').addEventListener('click', () => vscode.postMessage({ type: 'selectRepository' }));
    document.getElementById('search-code').addEventListener('click', () => vscode.postMessage({ type: 'search', text: input.value }));
    document.getElementById('check-reuse').addEventListener('click', () => vscode.postMessage({ type: 'reuse', text: input.value }));
    document.getElementById('open-docs').addEventListener('click', () => vscode.postMessage({ type: 'docsOverview' }));
    syncDocsButton.addEventListener('click', () => vscode.postMessage({ type: 'docsSync' }));

    function renderMarkdown(markdown, host) {
      let list = null;
      let code = null;
      for (const line of markdown.split('\\n')) {
        if (line.startsWith(String.fromCharCode(96, 96, 96))) {
          if (code) { host.append(code); code = null; } else code = document.createElement('pre');
          list = null;
          continue;
        }
        if (code) { code.textContent += line + '\\n'; continue; }
        if (!line.trim()) { list = null; continue; }
        const heading = /^(#{1,3})\\s+(.+)$/.exec(line);
        if (heading) {
          const node = document.createElement(heading[1].length === 1 ? 'h2' : 'h3');
          node.textContent = heading[2]; host.append(node); list = null; continue;
        }
        if (line.startsWith('- ')) {
          if (!list) { list = document.createElement('ul'); host.append(list); }
          const item = document.createElement('li'); item.textContent = line.slice(2); list.append(item); continue;
        }
        const paragraph = document.createElement('p'); paragraph.textContent = line; host.append(paragraph); list = null;
      }
      if (code) host.append(code);
    }

    function showDocs(message) {
      for (const old of messages.querySelectorAll('.docs-view')) old.remove();
      const view = document.createElement('section'); view.className = 'docs-view';
      if (message.section) {
        const meta = document.createElement('div'); meta.className = 'docs-meta';
        meta.textContent = message.section.freshness + ' · ' + (message.section.sourceCommit || 'uncommitted') +
          ' · ' + message.section.generationReason;
        view.append(meta);
        renderMarkdown(message.section.contentMarkdown, view);
        if (message.section.proposedMarkdown) {
          const proposal = document.createElement('details');
          const summary = document.createElement('summary'); summary.textContent = 'Proposed update for user-authored section';
          proposal.append(summary); renderMarkdown(message.section.proposedMarkdown, proposal); view.append(proposal);
        }
      } else {
        const title = document.createElement('h2'); title.textContent = 'Documentation overview'; view.append(title);
        const empty = document.createElement('p'); empty.textContent = 'No documentation generated yet. Sync Docs to create it.'; view.append(empty);
      }
      if (message.evidence.length) {
        const sources = document.createElement('div'); sources.className = 'docs-sources';
        for (const item of message.evidence) {
          const control = document.createElement('button'); control.type = 'button';
          control.textContent = item.name + ' · ' + item.path + ':' + item.startLine;
          control.title = 'Open indexed source';
          control.addEventListener('click', () => vscode.postMessage({ type: 'docSource', key: item.key }));
          sources.append(control);
        }
        view.append(sources);
      }
      if (message.sections.length) {
        const nav = document.createElement('nav'); nav.className = 'docs-nav';
        for (const item of message.sections) {
          const control = document.createElement('button'); control.type = 'button';
          control.textContent = item.title + (item.freshness === 'current' ? '' : ' · stale');
          control.title = 'Open documentation section';
          control.addEventListener('click', () => vscode.postMessage({ type: 'docSection', key: item.sectionId }));
          nav.append(control);
        }
        view.append(nav);
      }
      messages.append(view); messages.scrollTop = messages.scrollHeight;
    }

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
      vscode.postMessage({ type: 'search', text: trimmed });
    }

    form.addEventListener('submit', event => {
      event.preventDefault();
      send(input.value);
    });

    window.addEventListener('message', event => {
      const message = event.data;
      if (message.type === 'repository') { repositoryStatus.textContent = message.text; return; }
      if (message.type === 'docsStatus') {
        const status = message.status;
        docsStatus.textContent = status.sectionCount === 0 ? 'Docs not generated' :
          status.staleCount ? status.staleCount + ' documentation sections need sync' :
          status.affectedCount ? status.affectedCount + ' documentation sections reflect uncommitted changes' :
          status.currentCount + ' documentation sections current';
        return;
      }
      if (message.type === 'docsBusy') { syncDocsButton.disabled = Boolean(message.busy); return; }
      if (message.type === 'docs') { showDocs(message); return; }
      if (message.type === 'ignored') { document.getElementById(message.key)?.remove(); return; }
      if (message.type === 'useReference') { input.value = message.text; input.focus(); return; }
      if (message.type === 'results') {
        for (const old of messages.querySelectorAll('.code-result')) old.remove();
        addMessage('CodeMind', message.title + (message.results.length ? '' : ': No confident matches.') + (message.warning ? '\\n' + message.warning : ''), 'server');
        for (const result of message.results) {
          const item = document.createElement('div');
          item.className = 'code-result'; item.id = result.key;
          const title = document.createElement('strong'); title.textContent = (result.classification ? result.classification + ': ' : '') + result.name;
          const location = document.createElement('div'); location.textContent = result.path ? result.path + ':' + result.startLine + ' - ' + result.endLine : result.docSectionId ? 'Documentation' : 'Commit';
          const reason = document.createElement('div'); reason.textContent = result.reason;
          if (typeof result.confidence === 'number') reason.textContent += ' Match score: ' + result.confidence.toFixed(2);
          const context = document.createElement('div'); context.textContent = result.docSectionId ? result.docKind :
            'Callers: ' + (result.callers.map(x => x.name).join(', ') || 'none indexed') + ' | Tests: ' + (result.tests.map(x => x.name).join(', ') || 'none indexed');
          const actions = document.createElement('div'); actions.className = 'result-actions';
          if (result.docSectionId) {
            const control = document.createElement('button'); control.textContent = 'Open Docs';
            control.addEventListener('click', () => vscode.postMessage({ type: 'resultAction', key: result.key, action: 'openDoc' }));
            actions.append(control);
          }
          for (const [action, label] of [['open', 'Open'], ['use', 'Use Reference'], ['ignore', 'Ignore']]) {
            if (result.docSectionId) continue;
            if (action === 'ignore' && !result.canIgnore) continue;
            const control = document.createElement('button'); control.textContent = label;
            control.disabled = !result.path;
            control.addEventListener('click', () => vscode.postMessage({ type: 'resultAction', key: result.key, action })); actions.append(control);
          }
          item.append(title, location, reason, context, actions); messages.append(item);
        }
        messages.scrollTop = messages.scrollHeight;
      }
      if (message.type === 'reply') addMessage('CodeMind server', message.text, 'server');
      if (message.type === 'error') addMessage('Error', message.text, 'error');
      button.disabled = false;
      input.focus();
    });

  </script>
</body>
</html>`;
}
//# sourceMappingURL=extension.js.map