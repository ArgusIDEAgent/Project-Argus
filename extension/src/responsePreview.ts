import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { verifiedSourcePath } from './sourceNavigation';
import { BackendCitation, ResponsePresentation, responsePreviewHtml } from './responsePresentation';

export type PreviewOptions = { rootPath: string; dataDir: string; packet: unknown };
export type VerificationReply = { presentation: ResponsePresentation; source: BackendCitation | null };
const MAX_PACKET_BYTES = 3 * 1024 * 1024;

export function verifyResponsePreview(runtimeRoot: string, options: PreviewOptions, reference?: string): Promise<VerificationReply> {
  const python = path.join(runtimeRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!fs.existsSync(python) || !fs.existsSync(path.join(runtimeRoot, 'query_system', 'presentation_preview.py'))) {
    return Promise.reject(new Error('Phase 9 preview requires this CodeMind checkout and its project virtual environment. Production chat is unchanged.'));
  }
  const input = JSON.stringify(options.packet);
  if (Buffer.byteLength(input) > MAX_PACKET_BYTES) return Promise.reject(new Error('Preview packet is too large.'));
  const args = ['-m', 'query_system.presentation_preview', '--root', fs.realpathSync(options.rootPath), '--data-dir', path.resolve(options.dataDir)];
  if (reference) args.push('--reference', reference);
  return new Promise((resolve, reject) => {
    const child = spawn(python, args, { cwd: runtimeRoot, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
    const chunks: Buffer[] = [];
    let bytes = 0;
    const timer = setTimeout(() => { child.kill(); reject(new Error('Preview verification timed out. No navigation is authorized.')); }, 30000);
    child.stdout.on('data', (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > 2 * 1024 * 1024) { child.kill(); reject(new Error('Preview response exceeds its size limit.')); }
      else chunks.push(chunk);
    });
    child.stderr.resume();  // Never echo untrusted packet/source/exception text.
    child.stdin.on('error', () => { /* process error/close handles authorization failure */ });
    child.on('error', () => { clearTimeout(timer); reject(new Error('Preview verifier unavailable. No navigation is authorized.')); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 || bytes > 2 * 1024 * 1024) { reject(new Error('Preview packet rejected. No navigation is authorized.')); return; }
      try {
        const reply = JSON.parse(Buffer.concat(chunks).toString('utf8')) as VerificationReply;
        if (reply.presentation?.schema_version !== 'phase9-1' || !Array.isArray(reply.presentation.requirements)) throw new Error('Invalid preview');
        resolve(reply);
      } catch { reject(new Error('Malformed preview verifier response.')); }
    });
    child.stdin.end(input);
  });
}

export function verifiedPreviewSource(rootPath: string, reply: VerificationReply, reference: string): string {
  const view = reply.presentation;
  const approved = reply.source;
  const displayed = view.sources[reference];
  if (!approved || approved.reference !== reference || displayed?.state !== 'verified' || JSON.stringify(displayed.citation) !== JSON.stringify(approved)) {
    throw new Error('This citation is unavailable or stale; navigation is not authorized.');
  }
  const proof = approved.source;
  const root = fs.realpathSync(rootPath);
  const rootIdentity = process.platform === 'win32' ? root.toLowerCase() : root;
  const repoId = createHash('sha256').update(rootIdentity).digest('hex').slice(0, 24);
  if (view.repository_id !== repoId || proof.repository_id !== repoId || proof.snapshot_id !== view.snapshot_id ||
      approved.reference !== `cm7:${view.package_id}:${approved.record_id}` || !/^[a-f0-9]{32}$/.test(view.snapshot_id)) {
    throw new Error('Citation belongs to an unauthorized repository or generation.');
  }
  if (typeof proof.file_path !== 'string' || proof.file_path.includes('\\') || proof.file_path.includes('\0') || proof.file_path.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error('Citation has a noncanonical file path.');
  }
  // Match knowledge_base.identity.file_id JSON encoding (including separator space).
  const expectedFile = 'File:' + createHash('sha256').update('[' + JSON.stringify(repoId) + ', ' + JSON.stringify(proof.file_path) + ']').digest('hex');
  if (proof.file_id !== expectedFile || !Number.isSafeInteger(proof.start_line) || !Number.isSafeInteger(proof.end_line) || proof.start_line < 1 || proof.end_line < proof.start_line || !/^[a-f0-9]{64}$/.test(proof.source_hash)) {
    throw new Error('Citation file identity, source hash or range is invalid.');
  }
  const file = verifiedSourcePath(root, proof.file_path, proof.source_hash);
  const raw = fs.readFileSync(file);
  // Canonical metadata uses byte-line boundaries; do not split Unicode separators.
  const lines = raw.toString('utf8').split(/\r\n|\r|\n/);
  if (lines[lines.length - 1] === '') lines.pop();
  if (proof.end_line > lines.length || proof.end_line > 1000000) throw new Error('Citation is outside the saved source range.');
  return file;
}

export async function openVerifiedPreviewSource(rootPath: string, reply: VerificationReply, reference: string): Promise<void> {
  if (!vscode.workspace.isTrusted) throw new Error('Workspace Trust is required for source navigation.');
  const file = verifiedPreviewSource(rootPath, reply, reference);
  const proof = reply.source!.source;
  const document = await vscode.workspace.openTextDocument(file);
  if (document.isDirty || proof.end_line > document.lineCount || createHash('sha256').update(document.getText(), 'utf8').digest('hex') !== proof.source_hash) {
    throw new Error('Editor source is dirty, changed or not byte-compatible with the citation. Navigation refused.');
  }
  // Recheck disk after the asynchronous document open, before showing selection.
  verifiedPreviewSource(rootPath, reply, reference);
  await vscode.window.showTextDocument(document, { viewColumn: vscode.ViewColumn.Beside,
    selection: new vscode.Range(proof.start_line - 1, 0, proof.end_line - 1, document.lineAt(proof.end_line - 1).text.length) });
}

export async function openResponsePreview(runtimeRoot: string, options: PreviewOptions): Promise<vscode.WebviewPanel> {
  if (!vscode.workspace.isTrusted) throw new Error('Workspace Trust is required for verified response preview. Production chat remains available.');
  const panel = vscode.window.createWebviewPanel('codemindResponsePreview', 'CodeMind Response Preview', vscode.ViewColumn.Beside,
    { enableScripts: true, localResourceRoots: [], retainContextWhenHidden: true });
  const sources = new Map<string, string>();
  const suggestions = new Map<string, string>();
  let disposed = false;
  let version = 0;
  let packet = options.packet;
  let latest: VerificationReply;
  panel.onDidDispose(() => { disposed = true; version++; sources.clear(); suggestions.clear(); });
  function show(reply: VerificationReply): void {
    latest = reply;
    sources.clear(); suggestions.clear();
    const sourceKeys: Record<string, string> = {};
    for (const [reference, source] of Object.entries(reply.presentation.sources)) {
      if (source.state !== 'verified') continue;
      const key = randomBytes(12).toString('hex'); sources.set(key, reference); sourceKeys[reference] = key;
    }
    const followUpKeys: Record<number, string> = {};
    reply.presentation.follow_ups.forEach((item, index) => {
      if (item.mode !== 'copy_query' || typeof item.prompt !== 'string') return;
      const key = randomBytes(12).toString('hex'); suggestions.set(key, item.prompt); followUpKeys[index] = key;
    });
    panel.webview.html = responsePreviewHtml(reply.presentation, sourceKeys, followUpKeys);
  }
  const registration = panel.webview.onDidReceiveMessage(async (message: unknown) => {
    if (disposed || !message || typeof message !== 'object') return;
    const action = message as { type?: string; key?: string; depth?: string };
    const turn = ++version;
    try {
      if (action.type === 'previewCopyQuery') {
        const prompt = typeof action.key === 'string' ? suggestions.get(action.key) : undefined;
        if (prompt) await vscode.env.clipboard.writeText(prompt);  // No backend action.
      } else if (action.type === 'previewDepth' && ['quick', 'standard', 'deep'].includes(action.depth || '')) {
        packet = { ...(packet as object), depth: action.depth };
        sources.clear(); suggestions.clear();
        const reply = await verifyResponsePreview(runtimeRoot, { ...options, packet });
        if (!disposed && turn === version) show(reply);
      } else if (action.type === 'previewSource') {
        const reference = typeof action.key === 'string' ? sources.get(action.key) : undefined;
        if (!reference || !vscode.workspace.isTrusted) return;
        const reply = await verifyResponsePreview(runtimeRoot, { ...options, packet }, reference);
        if (disposed || turn !== version) return;
        if (reply.presentation.package_id !== latest.presentation.package_id || reply.presentation.snapshot_id !== latest.presentation.snapshot_id) throw new Error('Preview identity changed.');
        show(reply);  // Stale/unavailable sources immediately become non-clickable.
        await openVerifiedPreviewSource(options.rootPath, reply, reference);
      }
    } catch (error) {
      if (!disposed && turn === version) {
        // A failed source verification cannot leave a still-enabled stale control.
        if (action.type === 'previewSource' && latest) {
          for (const source of Object.values(latest.presentation.sources)) { source.state = 'unavailable'; source.reason = 'Navigation verification failed. Reopen a current packet.'; }
          show(latest);
        }
        await panel.webview.postMessage({ type: 'previewError', text: error instanceof Error ? error.message : 'Preview failed.' });
      }
    }
  });
  panel.onDidDispose(() => registration.dispose());
  try { const reply = await verifyResponsePreview(runtimeRoot, options); if (!disposed) show(reply); }
  catch (error) { panel.dispose(); throw error; }
  return panel;
}

export function registerResponsePreview(context: vscode.ExtensionContext): void {
  context.subscriptions.push(vscode.commands.registerCommand('codemind.previewResponse', async () => {
    try {
      const input = (await vscode.window.showOpenDialog({ canSelectFiles: true, canSelectFolders: false, canSelectMany: false,
        openLabel: 'Open backend Phase 7/8 packet', filters: { JSON: ['json'] } }))?.[0];
      if (!input) return;
      const root = (await vscode.window.showOpenDialog({ canSelectFiles: false, canSelectFolders: true, canSelectMany: false,
        openLabel: 'Authorize repository for preview (no scan)' }))?.[0];
      if (!root) return;
      if (fs.statSync(input.fsPath).size > MAX_PACKET_BYTES) throw new Error('Preview packet is too large.');
      const dataSetting = process.env.CODEMIND_DATA_DIR || path.join(os.homedir(), '.codemind', 'data');
      const dataDir = dataSetting.replace(/^~(?=$|[\\/])/, os.homedir());
      await openResponsePreview(path.resolve(context.extensionPath, '..'), {
        rootPath: fs.realpathSync(root.fsPath), dataDir, packet: JSON.parse(fs.readFileSync(input.fsPath, 'utf8')),
      });
    } catch (error) { void vscode.window.showErrorMessage(error instanceof Error ? error.message : 'Response preview failed.'); }
  }));
}
