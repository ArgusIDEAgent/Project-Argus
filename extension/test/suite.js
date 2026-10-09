'use strict';

const assert = require('node:assert/strict');
const vscode = require('vscode');
const vm = require('node:vm');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');

async function run() {
  assert.equal(vscode.workspace.workspaceFolders, undefined);
  await vscode.commands.executeCommand('codemind.openChat');
  assert.ok(vscode.window.tabGroups.all.some(group => group.tabs.some(tab => tab.label === 'CodeMind Chat')));
  try {
    const reply = await vscode.commands.executeCommand('codemind.checkConnection');
    assert.equal(typeof reply, 'string');
    assert.ok(reply.length > 0);
  } catch (error) {
    if (/not running/.test(error instanceof Error ? error.message : String(error))) {
      console.log('SKIP: engine not running');
    } else {
      throw error;
    }
  }
  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes('codemind.searchCode'));
  assert.ok(commands.includes('codemind.checkReuse'));
  assert.ok(commands.includes('codemind.openDocsOverview'));
  assert.ok(commands.includes('codemind.syncDocs'));
  const html = require('../out/extension').chatHtml({});
  const script = /<script nonce="[^"]+">([\s\S]*?)<\/script>/.exec(html);
  assert.ok(script);
  new vm.Script(script[1]);
  const sourceRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-source-'));
  const outsideRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-outside-'));
  try {
    const file = path.join(sourceRoot, 'source.js');
    fs.writeFileSync(file, 'export const value = 1;');
    const expected = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    const { verifiedSourcePath } = require('../out/sourceNavigation');
    assert.equal(verifiedSourcePath(sourceRoot, 'source.js', expected), fs.realpathSync(file));
    assert.throws(() => verifiedSourcePath(sourceRoot, '../outside.js', expected), /outside/);
    assert.throws(() => verifiedSourcePath(sourceRoot, 'source.js', '0'.repeat(64)), /changed/);
    fs.writeFileSync(path.join(outsideRoot, 'secret.js'), 'private');
    fs.symlinkSync(path.join(outsideRoot, 'secret.js'), path.join(sourceRoot, 'linked.js'));
    assert.throws(() => verifiedSourcePath(sourceRoot, 'linked.js', expected), /outside/);
  } finally {
    fs.rmSync(sourceRoot, { recursive: true, force: true });
    fs.rmSync(outsideRoot, { recursive: true, force: true });
  }
}

module.exports = { run };
