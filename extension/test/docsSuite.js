'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vscode = require('vscode');
const { ServerManager } = require('../out/serverManager');
const { verifiedSourcePath } = require('../out/sourceNavigation');

async function waitFor(test, label, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await test()) return;
    await new Promise(resolve => setTimeout(resolve, 350));
  }
  throw new Error('Timed out waiting for ' + label);
}

async function run() {
  const repo = process.env.CODEMIND_DOCS_TEST_REPO;
  const root = process.env.CODEMIND_DOCS_TEST_ROOT;
  assert.ok(repo && root, 'Documentation fixture identity is required');
  assert.equal(vscode.workspace.isTrusted, true);
  assert.equal(vscode.workspace.workspaceFolders?.[0]?.uri.fsPath, root);
  const output = vscode.window.createOutputChannel('CodeMind Docs E2E');
  const server = new ServerManager(output);
  try {
    await server.start();
    const empty = await server.docSections(repo);
    assert.equal(empty.status.sectionCount, 0);
    assert.equal(await server.docsOverview(repo), null);
    await vscode.commands.executeCommand('codemind.openChat');
    await waitFor(async () => {
      const state = await server.liveState(repo);
      if (state.indexState === 'failed') throw new Error('Extension indexing failed');
      return state.indexState === 'completed';
    }, 'extension repository selection');
    await new Promise(resolve => setTimeout(resolve, 500));
    await vscode.commands.executeCommand('codemind.openDocsOverview');
    assert.equal((await server.docSections(repo)).status.sectionCount, 0);
    await vscode.commands.executeCommand('codemind.syncDocs');
    const listing = await server.docSections(repo);
    assert.ok(listing.status.sectionCount > 0, 'Sync Docs command must persist real documents');
    const overview = await server.docsOverview(repo);
    assert.ok(overview && overview.contentMarkdown.includes('Indexed files'));
    assert.ok(overview.evidence.length > 0);
    const source = overview.evidence[0];
    const real = verifiedSourcePath(root, source.path, source.sourceHash);
    const document = await vscode.workspace.openTextDocument(real);
    const editor = await vscode.window.showTextDocument(document);
    assert.equal(fs.realpathSync(vscode.window.activeTextEditor.document.uri.fsPath), real);
    const unchanged = await server.syncDocs(repo, 'incremental');
    assert.equal(unchanged.outcome, 'no_changes');
    assert.equal(unchanged.updated.length, 0);
    // Saving must still refresh the selected repository after retired reuse
    // tracking is removed. Exercise the actual editor event and HTTP job flow.
    const beforeSave = await server.liveState(repo);
    assert.ok(await editor.edit(edit => edit.insert(document.positionAt(document.getText().length),
      '\n// Saved editor refresh probe.\n')));
    assert.ok(await document.save());
    await waitFor(async () => {
      const state = await server.liveState(repo);
      if (state.indexState === 'failed') throw new Error('Save-triggered refresh failed');
      return state.indexState === 'completed' && state.jobId !== beforeSave.jobId;
    }, 'save-triggered repository refresh');
    console.log('Real editor save -> automatic repository refresh PASS');
    fs.appendFileSync(real, '\n// Changed after documentation generation.\n');
    assert.throws(() => verifiedSourcePath(root, source.path, source.sourceHash));
    assert.ok((await server.docSections(repo)).status.staleCount > 0);
    console.log('Documentation VS Code command -> Python snapshot -> persisted docs -> verified editor source PASS');
  } finally {
    server.dispose(); output.dispose();
  }
}

module.exports = { run };
