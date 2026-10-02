'use strict';

const assert = require('node:assert/strict');
const vscode = require('vscode');

async function run() {
  assert.equal(vscode.workspace.workspaceFolders, undefined);
  await vscode.commands.executeCommand('codemind.openChat');
  assert.ok(vscode.window.tabGroups.all.some(group => group.tabs.some(tab => tab.label === 'CodeMind Chat')));
  const reply = await vscode.commands.executeCommand('codemind.checkConnection');
  assert.equal(typeof reply, 'string');
  assert.ok(reply.length > 0);
}

module.exports = { run };
