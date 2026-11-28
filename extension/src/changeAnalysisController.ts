import { randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { ServerManager, Repository, ChangeJob, CapturedRun } from './serverManager';
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
  private leaseTimer?: NodeJS.Timeout;
  private retiring = Promise.resolve();
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
  private run?: CapturedRun;
  private runTimer?: NodeJS.Timeout;
  private executionBusy = false;

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
    if (this.leaseTimer) clearInterval(this.leaseTimer);
    this.leaseTimer = undefined;
  }

  private async stale(): Promise<void> {
    if (!this.job || !this.repo || this.changed) return;
    const job = this.job; const repo = this.repo;
    this.changed = true;
    if (this.run) this.run.stale_for_workspace = true;
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
      if (!this.panel) {
        this.panel = vscode.window.createWebviewPanel('codemind.changeAnalysis', 'CodeMind: Analyze Change', vscode.ViewColumn.Beside,
          { enableScripts: true, localResourceRoots: [] });
        this.panel.onDidDispose(() => { this.panel = undefined; this.stop(); });
        this.panel.webview.onDidReceiveMessage(message => { void this.message(message).catch(error => this.status(String(error))); });
      }
      this.panel.reveal();
      this.status('Starting change analysis. Saved files only; connecting to the engine…');
      const previousRepo = this.repo;
      const previousJob = this.job;
      this.repo = undefined;
      this.job = undefined;
      if (previousRepo) await this.releaseCapture(previousRepo, previousJob, this.run);
      if (epoch !== this.epoch) return;
      if (this.timer) clearTimeout(this.timer);
      this.stopWatch(); this.actions.clear(); this.changed = false; this.mode = 'simple'; this.plan = undefined; this.presentation = undefined; this.checkActions.clear();
      if (this.runTimer) clearTimeout(this.runTimer);
      this.run = undefined;
      this.executionBusy = false;
      const selected = this.selected();
      let root = selected?.rootPath ?? (vscode.workspace.workspaceFolders?.length === 1 ? vscode.workspace.workspaceFolders[0].uri.fsPath : undefined);
      if (!root) root = (await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, canSelectMany: false, openLabel: 'Analyze saved changes' }))?.[0]?.fsPath;
      if (!root || epoch !== this.epoch) return;
      const repository = await this.server.acquireRepository(root, false);
      if (epoch !== this.epoch) {
        await this.releaseCapture(repository);
        return;
      }
      let job: ChangeJob;
      try {
        job = await this.server.startChange(repository.id, randomBytes(20).toString('hex'));
      } catch (error) {
        await this.releaseCapture(repository);
        throw error;
      }
      if (epoch !== this.epoch) {
        await this.releaseCapture(repository, job);
        return;
      }
      this.repo = repository;
      this.job = job;
      const { id, leaseId } = repository;
      if (leaseId) {
        this.leaseTimer = setInterval(() => { void this.server.liveState(id, false, leaseId).catch(() => {}); }, 15_000);
      }
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
      const disabled = this.changed || this.plan.stale_for_workspace || !this.plan.selected_check_ids.length || this.executionBusy || !vscode.workspace.isTrusted;
      body += `<section><h2>Isolated execution</h2><p>Review and prepare the pinned dependency environment first, then separately authorize selected Node test files. Every test and hook in each file may run.</p><button id="prepare-run" ${disabled ? 'disabled' : ''}>Review environment preparation</button><button id="execute-run" ${disabled ? 'disabled' : ''}>Review and run selected files</button><button id="compare-run" ${disabled ? 'disabled' : ''}>Review paired baseline/current retries</button><button id="template-run" ${disabled ? 'disabled' : ''}>Preview literal temporary check</button><button id="remove-template">Remove temporary check</button>`;
      if (this.run) body += `<p>${escapeChange(this.run.kind)}: ${escapeChange(this.run.state)} · ${this.run.selected} selected · ${this.run.discovered} statically discovered · ${this.run.executed} runtime tests executed. Historical capture${this.run.stale_for_workspace ? ' — stale for current workspace' : ''}.</p><p>${escapeChange(this.run.attribution)}</p><pre>${escapeChange(JSON.stringify(this.run.comparison_result ?? null))}</pre>${this.run.error ? `<p>${escapeChange(this.run.error)}</p>` : ''}${this.run.results.map(result => `<h3>${escapeChange(result.path)} ${escapeChange(result.version ?? '')} attempt ${result.attempt ?? 1}: ${escapeChange(result.outcome)}</h3><p>Cleanup: ${result.cleanup_complete ? 'complete' : 'FAILED'}</p><pre>${escapeChange(result.console)}</pre>`).join('')}<button id="export-run">Export private summary</button><button id="cancel-run">Cancel execution</button>`;
      body += '</section>';
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
    if (input.type === 'prepare-run' || input.type === 'execute-run' || input.type === 'compare-run') {
      await this.execute(input.type === 'prepare-run' ? 'prepare' : input.type === 'compare-run' ? 'compare' : 'run');
    } else if (input.type === 'template-run') {
      await this.temporaryPreview();
    } else if (input.type === 'remove-template' && this.job && this.repo && !this.executionBusy) {
      await this.server.temporaryCheck(this.job.id,this.repo.id); this.render();
    } else if (input.type === 'export-run' && this.run && this.job && this.repo) {
      const job=this.job,repo=this.repo,run=this.run,epoch=this.epoch;
      const summary=await this.server.exportRun(job.id,repo.id,run.id);
      if(this.epoch!==epoch || this.job!==job || this.run?.id!==run.id)return;
      const destination=await vscode.window.showSaveDialog({filters:{JSON:['json']},saveLabel:'Export private run summary'});
      if(destination && this.epoch===epoch && this.job===job && this.run?.id===run.id)
        await vscode.workspace.fs.writeFile(destination,Buffer.from(JSON.stringify(summary,null,2)+'\n'));
    } else if (input.type === 'cancel-run' && this.run && this.job && this.repo) {
      const job = this.job, repo = this.repo, run = this.run, epoch = this.epoch;
      const result = await this.server.capturedRun(job.id, repo.id, run.id, true);
      if (this.epoch === epoch && this.job === job && this.repo === repo && this.run?.id === run.id) { this.run = result; this.render(); }
    } else if (input.type === 'plan') {
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

  private async temporaryPreview(): Promise<void> {
    if (!vscode.workspace.isTrusted || this.changed || !this.job || !this.repo || this.executionBusy) return;
    const job=this.job,repo=this.repo,epoch=this.epoch;
    const module_path=await vscode.window.showInputBox({prompt:'Captured ESM module path, for example src/value.js'});
    if (!module_path) return;
    const export_name=await vscode.window.showInputBox({prompt:'Named exported function to call'});
    if (!export_name) return;
    const literals=await vscode.window.showInputBox({prompt:'JSON object with arguments array and your expected result',value:'{"arguments":[],"expected":null}'});
    if (!literals || literals.length>4000 || this.job!==job || this.epoch!==epoch || this.changed || !vscode.workspace.isTrusted) return;
    const data=JSON.parse(literals) as {arguments:unknown[];expected:unknown};
    const result=await this.server.temporaryCheck(job.id,repo.id,{module_path,export_name,arguments:data.arguments,expected:data.expected});
    if (this.job===job && this.epoch===epoch && !this.changed) {
      await vscode.window.showInformationMessage('Temporary check preview; review exact source again in preparation/run authorization.',{modal:true,detail:JSON.stringify(result,null,2)});
      this.render();
    }
  }

  private async execute(kind: 'prepare' | 'run' | 'compare'): Promise<void> {
    if (!vscode.workspace.isTrusted || this.changed || this.plan?.stale_for_workspace || !this.plan?.selected_check_ids.length || !this.job || !this.repo || this.executionBusy) return;
    const job = this.job, repo = this.repo, plan = this.plan, epoch = this.epoch;
    const current = () => this.epoch === epoch && this.job === job && this.repo === repo && this.plan === plan && !this.changed && vscode.workspace.isTrusted;
    this.executionBusy = true;
    try {
      const review = await this.server.executionReview(job.id, repo.id, kind);
      if (!current() || review.capture_id !== plan.capture_id || review.plan_id !== plan.plan_id) return;
      const action = kind === 'prepare' ? 'Authorize isolated preparation' : 'Run these captured test files';
      const accepted = await vscode.window.showWarningMessage(kind === 'prepare' ? 'Prepare the isolated Node environment?' : 'Execute the reviewed captured tests?',
        { modal: true, detail: `${review.notice}\nAdapter: ${review.adapter ?? 'node:test'}\nToolchain: ${JSON.stringify(review.toolchain ?? null)}\nCapture: ${review.capture_id}\nPlan: ${review.plan_id}\nDependencies: ${review.dependency_identity}\nCommands: ${JSON.stringify(kind === 'prepare' ? review.preparation : kind === 'compare' ? review.paired_commands : review.commands)}\nComparison: ${JSON.stringify(review.comparison)}\nExecution plan: ${review.execution_plan_id}\nExact temporary source/hash: ${review.temporary_source?.source_hash ?? 'none'}\n${review.temporary_source?.source ?? ''}\nRuntime: ${JSON.stringify(review.identity)}\nLimits: ${JSON.stringify(review.limits)}\nAuthorization expires in 60 seconds.` }, action);
      if (accepted !== action || !current()) return;
      const run = await this.server.submitRun(job.id, repo.id, review);
      if (!current()) { await this.server.capturedRun(job.id, repo.id, run.id, true); return; }
      this.run = run;
      const poll = async () => {
        if (this.epoch !== epoch || this.job !== job || !this.panel || this.run?.id !== run.id) return;
        try {
          const result = await this.server.capturedRun(job.id, repo.id, run.id);
          if (this.epoch !== epoch || this.run?.id !== run.id) return;
          result.stale_for_workspace ||= this.changed;
          this.run = result; this.render();
          if (['queued', 'running'].includes(result.state)) this.runTimer = setTimeout(() => { void poll(); }, 1000);
          else { this.executionBusy = false; this.render(); }
        } catch (error) { this.executionBusy = false; this.status(String(error)); }
      };
      this.render(); void poll();
    } finally {
      if (!this.run || !['queued', 'running'].includes(this.run.state)) this.executionBusy = false;
    }
  }

  private status(message: string): void { this.actions.clear(); this.checkActions.clear(); this.html(`<p>${escapeChange(message)}</p>`); }
  private html(body: string): void {
    if (!this.panel) return;
    const nonce = randomBytes(18).toString('hex');
    body += `<script nonce="${nonce}">document.addEventListener('click',event=>{const b=event.target.closest('button');if(b&&!b.disabled&&['prepare-run','execute-run','compare-run','template-run','remove-template','cancel-run','export-run'].includes(b.id))api.postMessage({type:b.id});});</script>`;
    this.panel.webview.html = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'"><style nonce="${nonce}">body{font:var(--vscode-font-size) var(--vscode-font-family);color:var(--vscode-foreground);padding:18px}li{margin:8px 0}button{margin:4px}section{margin:20px 0}</style></head><body><h1>Analyze Change</h1><nav>${['simple','detailed','flow'].map(mode => `<button data-mode="${mode}">${mode}</button>`).join('')}<button id="plan">Review test plan</button><button id="cancel">Cancel</button></nav>${body}<script nonce="${nonce}">const api=acquireVsCodeApi();document.addEventListener('click',event=>{const b=event.target.closest('button');if(!b)return;if(b.dataset.source)api.postMessage({type:'source',value:b.dataset.source});if(b.dataset.mode)api.postMessage({type:'mode',value:b.dataset.mode});if(b.id==='cancel')api.postMessage({type:'cancel'});if(b.id==='plan')api.postMessage({type:'plan'});if(b.dataset.check)api.postMessage({type:'check',value:b.dataset.check});});</script></body></html>`;
  }
  private async releaseCapture(repo: Repository, job?: ChangeJob, run?: CapturedRun): Promise<void> {
    if (job) {
      if (run) await this.server.capturedRun(job.id, repo.id, run.id, true).catch(() => {});
      await this.server.changeAction(job.id, repo.id, 'cancel').catch(() => {});
    }
    if (repo.leaseId) await this.server.releaseRepository(repo);
  }

  private stop(): void {
    if (this.runTimer) clearTimeout(this.runTimer);
    ++this.epoch;
    if (this.timer) clearTimeout(this.timer);
    this.stopWatch();
    this.actions.clear();
    this.checkActions.clear();
    const repo = this.repo;
    const job = this.job;
    const run = this.run;
    this.repo = undefined;
    this.job = undefined;
    this.run = undefined;
    if (repo) this.retiring = this.releaseCapture(repo, job, run);
  }
  async close(): Promise<void> { this.stop(); await this.retiring; }
  dispose(): void { this.stop(); this.panel?.dispose(); for (const d of this.disposables) d.dispose(); this.sourceText.clear(); }
}
