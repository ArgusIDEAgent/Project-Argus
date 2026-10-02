'use strict';

const assert = require('node:assert/strict');
const vscode = require('vscode');

async function run() {
  await vscode.commands.executeCommand('codemind.openChat');
  const reply = await vscode.commands.executeCommand('codemind.checkConnection');
  assert.equal(reply, 'Hello from the CodeMind server! You said: Hello');
}

module.exports = { run };
