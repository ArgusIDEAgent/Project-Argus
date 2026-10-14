'use strict';
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');
const { verifyResponsePreview, verifiedPreviewSource, openVerifiedPreviewSource, openResponsePreview } = require('../out/responsePreview');
const { responsePreviewHtml } = require('../out/responsePresentation');

async function fixture(runtimeRoot) {
  const child = spawn(path.join(runtimeRoot, '.venv/bin/python'), [path.join(runtimeRoot, 'ai-engine/tests/phase9_preview_fixture.py')], { cwd: runtimeRoot, stdio: ['pipe', 'pipe', 'pipe'] });
  child.stderr.resume();
  const options = await new Promise((resolve, reject) => {
    let output = ''; const timer = setTimeout(() => { child.kill(); reject(new Error('Fixture timed out')); }, 20000);
    child.stdout.on('data', data => { output += data.toString(); const end = output.indexOf('\n'); if (end >= 0) { clearTimeout(timer); try { resolve(JSON.parse(output.slice(0, end))); } catch (error) { reject(error); } } });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('exit', code => { if (!output) { clearTimeout(timer); reject(new Error('Fixture failed: ' + code)); } });
  });
  return { options, close: () => new Promise(resolve => { child.once('exit', resolve); child.stdin.end(); }) };
}

async function run() {
  const runtimeRoot = path.resolve(__dirname, '../..');
  assert.ok((await vscode.commands.getCommands(true)).includes('codemind.previewResponse'));
  const owner = await fixture(runtimeRoot);
  let panel;
  try {
    const options = owner.options;
    const reply = await verifyResponsePreview(runtimeRoot, options);
    assert.equal(reply.presentation.requirements.length, 2);
    assert.ok(Object.values(reply.presentation.sources).every(source => source.state === 'verified'));
    const reference = Object.keys(reply.presentation.sources).find(ref => reply.presentation.sources[ref].citation.source.file_path === 'login.ts');
    assert.ok(reference);
    const approved = await verifyResponsePreview(runtimeRoot, options, reference);
    assert.equal(approved.source.reference, reference);
    const file = verifiedPreviewSource(options.rootPath, approved, reference);
    assert.equal(file, fs.realpathSync(path.join(options.rootPath, 'login.ts')));
    assert.throws(() => verifiedPreviewSource(options.rootPath, approved, 'invented'), /unavailable|stale/);
    for (const [field, value] of [['file_id', 'invented'], ['end_line', 100000], ['snapshot_id', 'f'.repeat(32)], ['file_path', '../outside.ts']]) {
      const bad = structuredClone(approved);
      bad.source.source[field] = value;
      bad.presentation.sources[reference].citation.source[field] = value;
      assert.throws(() => verifiedPreviewSource(options.rootPath, bad, reference));
    }
    // Real VS Code document opening and range selection, not just path assertions.
    await openVerifiedPreviewSource(options.rootPath, approved, reference);
    const editor = vscode.window.activeTextEditor;
    assert.equal(editor.document.uri.fsPath, file);
    assert.equal(editor.selection.start.line, approved.source.source.start_line - 1);
    assert.equal(editor.selection.end.line, approved.source.source.end_line - 1);

    // Unsaved editor text must be rejected even while saved-byte metadata is valid.
    const edit = new vscode.WorkspaceEdit(); edit.insert(editor.document.uri, new vscode.Position(0, 0), '// unsaved\n');
    await vscode.workspace.applyEdit(edit);
    await assert.rejects(openVerifiedPreviewSource(options.rootPath, approved, reference), /dirty|changed/);
    await vscode.commands.executeCommand('workbench.action.files.revert');

    panel = await openResponsePreview(runtimeRoot, options);
    assert.ok(panel.webview.html.includes('CodeMind Response Preview'));
    assert.ok(panel.webview.html.includes('req-000')); assert.ok(panel.webview.html.includes('req-001'));
    assert.ok(panel.webview.html.includes('data-source-key='));
    assert.ok(vscode.window.tabGroups.all.some(group => group.tabs.some(tab => tab.label === 'CodeMind Response Preview')));

    for (const depth of ['quick', 'standard', 'deep']) {
      const result = await verifyResponsePreview(runtimeRoot, { ...options, packet: { ...options.packet, depth } });
      assert.equal(result.presentation.depth, depth);
      const html = responsePreviewHtml(result.presentation, {}, {});
      assert.ok(html.includes('Coverage and uncertainty')); assert.ok(!html.includes('href="command:'));
    }
    const before = fs.readFileSync(file);
    fs.appendFileSync(file, '// changed\n');
    assert.throws(() => verifiedPreviewSource(options.rootPath, approved, reference), /changed/);
    const stale = await verifyResponsePreview(runtimeRoot, options, reference);
    assert.equal(stale.source, null); assert.equal(stale.presentation.status, 'stale_index');
    const html = responsePreviewHtml(stale.presentation, { [reference]: 'old-token' }, {});
    assert.ok(!html.includes('data-source-key=')); assert.ok(html.includes('not navigable'));
    fs.writeFileSync(file, before);
    const unauthorized = await verifyResponsePreview(runtimeRoot, { ...options, rootPath: options.dataDir, dataDir: options.rootPath }, reference);
    assert.equal(unauthorized.source, null);
    assert.equal(unauthorized.presentation.sources[reference].state, 'unauthorized');
    console.log('Phase 9 VS Code preview, three depths, canonical navigation, dirty/stale/invalid/unauthorized rejection: PASS');
  } finally {
    panel?.dispose();
    await vscode.commands.executeCommand('workbench.action.closeAllEditors');
    await owner.close();
  }
}
module.exports = { run };
