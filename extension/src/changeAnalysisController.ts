import { randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { ServerManager, Repository, ChangeJob } from './serverManager';
import { renderTestPlan, TestPlanReply } from './testPlanPresentation';
import { renderChange, escapeChange, ChangePresentation } from './changeAnalysisPresentation';

// Shell arguments originate from the authenticated local engine, never webview messages.
export function shellQuote(value: string): string { return "'" + value.replace(/'/g, "'\\''") + "'"; }

export class ChangeAnalysisController implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private job?: ChangeJob;
  private repo?: Repository;
  private terminal?: vscode.Terminal;
  private timer?: NodeJS.Timeout;
  private watcher?: vscode.FileSystemWatcher;
  private gitTimer?: NodeJS.Timeout;
  private disposables: vscode.Disposable[] = [];
  private actions = new Map<string, string>();
  private sourceText = new Map<string, string>();
  private mode = 'simple';
  private presentation?: ChangePresentation;
  private plan?: TestPlanReply;
  private checkActions = new Map<string, string>();
  private planBusy = false;
  private epoch = 0;
  private changed = false;
  private busy = false;
  private gitIdentity = '';

  constructor(private server: ServerManager, private selected: () => { id: string; rootPath: string } | undefined) {
    this.disposables.push(vscode.commands.registerCommand('codemind.analyzeChange', () => this.start()));
    this.disposables.push(vscode.commands.registerCommand('codemind.reviewTestPlan', () => this.loadPlan()));
    this.disposables.push(vscode.workspace.registerTextDocumentContentProvider('codemind-change', {
      provideTextDocumentContent: uri => this.sourceText.get(uri.toString()) ?? 'Captured source expired. Run Analyze Change again.',
    }));
    this.disposables.push(vscode.workspace.onDidSaveTextDocument(document => {
      if (document.uri.scheme === 'file' && this.contains(document.uri.fsPath)) void this.stale();
    }));
  }

  private contains(file: string): boolean {
    if (!this.repo) return false;
    const relative = path.relative(this.repo.rootPath, file);
    return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative) &&
      !/(^|[/\\])(\.codemind|node_modules)([/\\]|$)/.test(relative);
  }

  private stopWatch(): void {
    this.watcher?.dispose(); this.watcher = undefined;
    if (this.gitTimer) clearInterval(this.gitTimer);
    this.gitTimer = undefined;
  }

  private async stale(): Promise<void> {
    if (!this.job || !this.repo || this.changed) return;
    const job = this.job; const repo = this.repo;
    this.changed = true;
    job.stale_for_workspace = true;
    try { await this.server.changeAction(job.id, repo.id, 'stale'); } catch { /* historical panel still labels stale */ }
    if (this.job !== job) return;
    if (job.state === 'completed') await this.present();
    else this.status('Saved files or Git state changed. This analysis is stale; start Analyze Change again.');
  }

  async start(): Promise<void> {
    if (this.busy) return;
    if (!vscode.workspace.isTrusted) { void vscode.window.showWarningMessage('Trust this workspace before analyzing saved changes.'); return; }
    this.busy = true;
    const epoch = ++this.epoch;
    try {
      if (this.job && this.repo) { try { await this.server.changeAction(this.job.id, this.repo.id, 'cancel'); } catch { /* expired jobs do not prevent a new capture */ } }
      if (this.timer) clearTimeout(this.timer);
      this.stopWatch(); this.actions.clear(); this.changed = false; this.mode = 'simple'; this.plan = undefined; this.presentation = undefined; this.checkActions.clear();
      const selected = this.selected();
      let root = selected?.rootPath ?? (vscode.workspace.workspaceFolders?.length === 1 ? vscode.workspace.workspaceFolders[0].uri.fsPath : undefined);
      if (!root) root = (await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, canSelectMany: false, openLabel: 'Analyze saved changes' }))?.[0]?.fsPath;
      if (!root || epoch !== this.epoch) return;
      const repository = await this.server.registerRepository(root);
      if (epoch !== this.epoch) return;
      this.repo = repository;
      if (!this.panel) {
        this.panel = vscode.window.createWebviewPanel('codemind.changeAnalysis', 'CodeMind: Analyze Change', vscode.ViewColumn.Beside,
          { enableScripts: true, localResourceRoots: [] });
        this.panel.onDidDispose(() => { this.panel = undefined; this.stop(); });
        this.panel.webview.onDidReceiveMessage(message => { void this.message(message).catch(error => this.status(String(error))); });
      }
      this.panel.reveal();
      this.job = await this.server.startChange(this.repo.id, randomBytes(20).toString('hex'));
      if (epoch !== this.epoch) { await this.server.changeAction(this.job.id, this.repo.id, 'cancel'); return; }
      this.watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root, '**/*'));
      for (const event of [this.watcher.onDidChange, this.watcher.onDidCreate, this.watcher.onDidDelete])
        event(uri => { if (this.contains(uri.fsPath)) void this.stale(); });
      let gitRoot = root;
      while (!fs.existsSync(path.join(gitRoot, '.git')) && path.dirname(gitRoot) !== gitRoot) gitRoot = path.dirname(gitRoot);
      const git = path.join(gitRoot, '.git');
      const identity = () => {
        try {
          const head = fs.readFileSync(path.join(git, 'HEAD'), 'utf8');
          const ref = head.startsWith('ref: ') ? head.slice(5).trim() : '';
          return head + (ref && !ref.includes('..') ? (fs.existsSync(path.join(git, ref)) ? fs.readFileSync(path.join(git, ref), 'utf8') : fs.readFileSync(path.join(git, 'packed-refs'), 'utf8')) : '');
        } catch { return 'git-state-unavailable'; }
      };
      this.gitIdentity = identity();
      this.gitTimer = setInterval(() => { if (identity() !== this.gitIdentity) void this.stale(); }, 1500);
      const descriptor = this.job.terminal;
      this.terminal = vscode.window.createTerminal({ name: 'CodeMind atomic capture', cwd: descriptor.cwd, shellPath: '/bin/zsh' });
      this.terminal.show();
      this.terminal.sendText([descriptor.executable, ...descriptor.args].map(shellQuote).join(' '));
      this.status('Preparing atomic capture. Follow the administrator prompts in the CodeMind Terminal. Unsaved buffers are excluded.');
      this.poll(epoch);
    } catch (error) { this.status(String(error)); }
    finally { this.busy = false; }
  }

  private poll(epoch: number): void {
    this.timer = setTimeout(() => { void (async () => {
      if (epoch !== this.epoch || !this.job || !this.repo || !this.panel) return;
      try {
        const next = await this.server.changeStatus(this.job.id, this.repo.id);
        if (epoch !== this.epoch) return;
        this.job = next;
        if (next.state === 'completed') await this.present();
        else if (next.state === 'failed' || next.state === 'cancelled') this.status(next.error ?? (this.changed ? 'Analysis cancelled because the workspace changed. Run Analyze Change again.' : 'Analysis cancelled. Capture cleanup may still be finishing in Terminal.'));
        else { if (next.state === 'running') this.status('Analyzing captured before/after source…'); this.poll(epoch); }
      } catch (error) { this.status(String(error)); }
    })(); }, 1000);
  }

  private async present(): Promise<void> {
    if (!this.repo || !this.job || !this.panel) return;
    const job = this.job; const repo = this.repo; const mode = this.mode;
    const result = await this.server.changeExplanation(job.id, repo.id, mode);
    if (this.job !== job || this.mode !== mode) return;
    result.stale_for_workspace ||= this.changed;
    this.presentation = result;
    this.render();
  }

  private render(): void {
    if (!this.presentation) return;
    this.presentation.stale_for_workspace ||= this.changed;
    let body = renderChange(this.presentation, this.actions);
    if (this.plan && this.plan.capture_id === this.presentation.capture_id) {
      this.plan.stale_for_workspace ||= this.changed || this.presentation.stale_for_workspace;
      body += renderTestPlan(this.plan, this.actions, this.checkActions);
    }
    this.html(body);
  }

  private async loadPlan(selection?: string[]): Promise<void> {
    if (!this.job || !this.repo || this.job.state !== 'completed' || this.planBusy) return;
    const job = this.job; const repo = this.repo; const epoch = this.epoch;
    this.planBusy = true;
    try {
      const plan = await this.server.changeTestPlan(job.id, repo.id, selection);
      if (this.job !== job || this.repo !== repo || this.epoch !== epoch) return;
      this.plan = plan; this.render();
    } finally { this.planBusy = false; }
  }

  private async message(message: unknown): Promise<void> {
    if (!message || typeof message !== 'object') return;
    const input = message as { type?: string; value?: string };
    if (input.type === 'plan') {
      await this.loadPlan();
    } else if (input.type === 'check' && input.value && this.plan && !this.plan.stale_for_workspace && !this.changed) {
      const check = this.checkActions.get(input.value);
      if (!check) return;
      const selected = new Set(this.plan.selected_check_ids);
      if (selected.has(check)) selected.delete(check); else selected.add(check);
      await this.loadPlan([...selected]);
    } else if (input.type === 'cancel' && this.job && this.repo) {
      await this.server.changeAction(this.job.id, this.repo.id, 'cancel');
      this.status('Cancellation requested. Allow Terminal to finish snapshot cleanup.');
    } else if (input.type === 'mode' && ['simple', 'detailed', 'flow'].includes(input.value ?? '')) {
      this.mode = input.value!; await this.present();
    } else if (input.type === 'source' && input.value && this.job && this.repo) {
      const reference = this.actions.get(input.value);
      if (!reference) return;
      const job = this.job; const repo = this.repo; const epoch = this.epoch;
      const captured = await this.server.changeSource(job.id, repo.id, reference);
      if (this.job !== job || this.repo !== repo || this.epoch !== epoch || !this.panel) return;
      const uri = vscode.Uri.from({ scheme: 'codemind-change', authority: captured.capture_id,
        path: `/${captured.version}/${captured.path}`, query: `reference=${encodeURIComponent(reference)}` });
      if (this.sourceText.size >= 20) this.sourceText.delete(this.sourceText.keys().next().value!);
      this.sourceText.set(uri.toString(), captured.content);
      // VS Code content providers are read-only. Never navigate to live files.
      const document = await vscode.workspace.openTextDocument(uri);
      await vscode.window.showTextDocument(document, { preview: true,
        selection: new vscode.Range(captured.start_line - 1, 0, captured.end_line - 1, 0) });
    }
  }

  private status(message: string): void { this.actions.clear(); this.checkActions.clear(); this.html(`<p>${escapeChange(message)}</p>`); }
  private html(body: string): void {
    if (!this.panel) return;
    const nonce = randomBytes(18).toString('hex');
    this.panel.webview.html = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'"><style nonce="${nonce}">body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);padding:18px}li{margin:8px 0}button{margin:4px}section{margin:20px 0}</style></head><body><h1>Analyze Change</h1><nav>${['simple','detailed','flow'].map(mode => `<button data-mode="${mode}">${mode}</button>`).join('')}<button id="plan">Review test plan</button><button id="cancel">Cancel</button></nav>${body}<script nonce="${nonce}">const api=acquireVsCodeApi();document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;if(b.dataset.source)api.postMessage({type:'source',value:b.dataset.source});if(b.dataset.mode)api.postMessage({type:'mode',value:b.dataset.mode});if(b.id==='cancel')api.postMessage({type:'cancel'});if(b.id==='plan')api.postMessage({type:'plan'});if(b.dataset.check)api.postMessage({type:'check',value:b.dataset.check});});</script></body></html>`;
  }
  private stop(): void {
    ++this.epoch; if (this.timer) clearTimeout(this.timer); this.stopWatch(); this.actions.clear(); this.checkActions.clear();
    if (this.repo && this.job) void this.server.changeAction(this.job.id, this.repo.id, 'cancel').catch(() => {});
  }
  dispose(): void { this.stop(); this.panel?.dispose(); for (const d of this.disposables) d.dispose(); this.sourceText.clear(); }
}
