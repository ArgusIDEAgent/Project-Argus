import { randomBytes } from 'node:crypto';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { renderResponseBody, ResponsePresentation } from './responsePresentation';
import { openVerifiedPreviewSource } from './responsePreview';
import { HybridQueryInput, ServerManager } from './serverManager';
import { debugAnswerEngineFooter, debugStaticAnswerEngineFooter } from './debugAnswerEngine';

export function hybridEnabled(): boolean {
  return vscode.workspace.getConfiguration('codemind').get<boolean>('hybridEnabled', false) === true;
}

export function editorQueryContext(rootPath: string, question?: string): Partial<HybridQueryInput> {
  // Phase 5 treats active_file as an explicit scope, not a weak relevance hint.
  // Repository-wide named questions must not be narrowed to an unrelated tab.
  if (question !== undefined && !/\b(?:this|here|current|selected)\b/i.test(question)) return {};
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.uri.scheme !== 'file') return {};
  const relative = path.relative(rootPath, editor.document.uri.fsPath);
  if (!relative || relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative)) return {};
  const context: Partial<HybridQueryInput> = { active_file: relative.split(path.sep).join('/') };
  if (editor.document.isDirty) return context; // Saved snapshot only; never send unsaved text/ranges.
  context.cursor = { line: editor.selection.active.line + 1, column: editor.selection.active.character + 1 };
  if (!editor.selection.isEmpty) context.selection = {
    start: { line: editor.selection.start.line + 1, column: editor.selection.start.character + 1 },
    end: { line: editor.selection.end.line + 1, column: editor.selection.end.character + 1 },
  };
  return context;
}

type Answer = { repoId: string; rootPath: string; token: string; view: ResponsePresentation;
  operations?: { operation_id: string; engine: string; status: string; finding_ids?: string[] }[];
  generations?: { original_orders: number[]; model_status: string; fallback_status: string;
    claim_operation_ids?: Record<string, string[]> }[];
  sources: Map<string, string>; copies: Map<string, string>; version: number };

export class HybridChatSession {
  private answers = new Map<string, Answer>();
  private epoch = 0;
  private previous?: { repoId: string; rootPath: string; token: string };
  constructor(private server: ServerManager, private send: (message: unknown) => Thenable<boolean>) {}

  clear(): void { this.epoch++; this.answers.clear(); this.previous = undefined; }

  private async show(key: string, answer: Answer): Promise<void> {
    answer.sources.clear(); answer.copies.clear();
    const sourceKeys: Record<string, string> = {}, copyKeys: Record<number, string> = {};
    for (const [reference, source] of Object.entries(answer.view.sources)) {
      if (source.state !== 'verified' || !answer.token) continue;
      const action = randomBytes(12).toString('hex'); answer.sources.set(action, reference); sourceKeys[reference] = action;
    }
    answer.view.follow_ups.forEach((item, index) => {
      if (item.mode !== 'copy_query' || !item.prompt) return;
      const action = randomBytes(12).toString('hex'); answer.copies.set(action, item.prompt); copyKeys[index] = action;
    });
    const html = renderResponseBody(answer.view, sourceKeys, copyKeys, 'hybrid', 'hybrid-' + key + '-');
    const debug = vscode.workspace.getConfiguration('codemind').get<boolean>('debug.showAnswerEngine', false);
    await this.send({ type: 'hybridResults', key, html: html + (debug ? debugAnswerEngineFooter(answer.view, answer.operations, answer.generations) : '') });
  }

  async ask(selected: { id: string; rootPath: string }, question: string, current: () => boolean): Promise<void> {
    const epoch = this.epoch;
    const valid = () => epoch === this.epoch && current() && hybridEnabled();
    if (!valid()) return;
    const readiness = await this.server.hybridReadiness(selected.id).catch(error => { if (!valid()) return undefined; throw error; });
    if (!valid() || !readiness) return;
    if (!readiness.enabled) throw new Error('Hybrid backend is disabled. Enable CODEMIND_ENABLE_HYBRID=1 or turn off codemind.hybridEnabled to use legacy chat.');
    if (!readiness.snapshot_ready) throw new Error('Hybrid mode needs a current indexed snapshot. Wait for synchronization or refresh the selected repository.');
    const settings = vscode.workspace.getConfiguration('codemind');
    const depth = settings.get<'quick' | 'standard' | 'deep'>('hybridDepth', 'standard');
    const previous = this.previous?.repoId === selected.id && this.previous.rootPath === selected.rootPath
      && /\b(?:it|its|their|them|that|this)\b/i.test(question) ? this.previous.token : undefined;
    let response;
    try {
      response = await this.server.hybridQuery({ repository_id: selected.id, question, depth, ...editorQueryContext(selected.rootPath, question) }, settings.get('hybridAllowGeneration', true), previous);
    } catch (error) {
      if (!valid()) return;
      if (previous) this.previous = undefined; // Expired/restarted authority must not trap future retries.
      throw error;
    }
    if (!valid()) return;
    if (response.schema_version === 'hybrid-v1' && response.static_response) {
      const debug = vscode.workspace.getConfiguration('codemind').get<boolean>('debug.showAnswerEngine', false);
      await this.send({ type: 'reply', text: response.static_response.text,
        ...(debug ? { debugFooter: debugStaticAnswerEngineFooter() } : {}) });
      return;
    }
    if (response.schema_version !== 'hybrid-v1' || !response.presentation) throw new Error(response.error?.message || 'Hybrid response unavailable; legacy mode remains available.');
    if (response.presentation.repository_id !== selected.id || response.presentation.snapshot_id !== response.snapshot_id) throw new Error('Hybrid response repository/generation mismatch.');
    const key = randomBytes(12).toString('hex');
    const answer: Answer = { repoId: selected.id, rootPath: selected.rootPath, token: response.navigation_token || '', view: response.presentation,
      operations: response.operations, generations: response.generations,
      sources: new Map(), copies: new Map(), version: 0 };
    this.previous = answer.token ? { repoId: answer.repoId, rootPath: answer.rootPath, token: answer.token } : undefined;
    this.answers.set(key, answer);
    if (this.answers.size > 8) {
      const old = this.answers.keys().next().value!; this.answers.delete(old);
      await this.send({ type: 'hybridExpired', key: old });
    }
    await this.show(key, answer);
  }

  async handle(message: unknown, selected: { id: string; rootPath: string } | undefined): Promise<boolean> {
    if (!message || typeof message !== 'object') return false;
    const action = message as { type?: string; key?: string; actionKey?: string; depth?: string };
    if (!['hybridSource', 'hybridDepth', 'hybridCopyQuery'].includes(action.type || '')) return false;
    if (!hybridEnabled() || !selected || !vscode.workspace.isTrusted) return true;
    const answer = typeof action.key === 'string' ? this.answers.get(action.key) : undefined;
    if (!answer || answer.repoId !== selected.id || answer.rootPath !== selected.rootPath) return true;
    const version = ++answer.version;
    try {
      if (action.type === 'hybridCopyQuery') {
        const prompt = typeof action.actionKey === 'string' ? answer.copies.get(action.actionKey) : undefined;
        if (prompt) await vscode.env.clipboard.writeText(prompt);
        return true;
      }
      if (!answer.token) throw new Error('Navigation/display handle is unavailable; ask again.');
      if (action.type === 'hybridDepth') {
        if (!['quick', 'standard', 'deep'].includes(action.depth || '')) return true;
        const reply = await this.server.hybridPresentation(selected.id, answer.token, action.depth!);
        if (version !== answer.version || !this.answers.has(action.key!) || !hybridEnabled()) return true;
        if (reply.presentation.schema_version !== 'phase9-1' || reply.presentation.repository_id !== answer.repoId || reply.presentation.package_id !== answer.view.package_id || reply.presentation.snapshot_id !== answer.view.snapshot_id) throw new Error('Display repository/generation changed.');
        answer.view = reply.presentation; await this.show(action.key!, answer);
      } else {
        const reference = typeof action.actionKey === 'string' ? answer.sources.get(action.actionKey) : undefined;
        if (!reference) return true;
        const reply = await this.server.hybridSource(selected.id, answer.token, reference, answer.view.depth);
        if (version !== answer.version || !this.answers.has(action.key!) || !hybridEnabled()) return true;
        if (reply.presentation.schema_version !== 'phase9-1' || reply.presentation.repository_id !== answer.repoId || reply.presentation.package_id !== answer.view.package_id || reply.presentation.snapshot_id !== answer.view.snapshot_id) throw new Error('Source repository/generation changed.');
        answer.view = reply.presentation; await this.show(action.key!, answer);
        await openVerifiedPreviewSource(selected.rootPath, reply, reference);
      }
    } catch (error) {
      if (!this.answers.has(action.key!) || !hybridEnabled()) return true;
      for (const source of Object.values(answer.view.sources)) { source.state = 'unavailable'; source.reason = 'Hybrid source/display authorization failed; ask again.'; }
      if (this.answers.has(action.key!)) await this.show(action.key!, answer);
      await this.send({ type: 'error', text: error instanceof Error ? error.message : 'Hybrid action failed.' });
    }
    return true;
  }
}
