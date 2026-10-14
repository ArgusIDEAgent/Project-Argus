'use strict';
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const vscode = require('vscode');
const { ServerManager } = require('../out/serverManager');
const { HybridChatSession } = require('../out/hybridChat');

async function fixture(project) {
  const child = spawn(path.join(project, '.venv/bin/python'), [path.join(project, 'ai-engine/tests/stabilization_live_server.py')],
    { cwd: project, env: { ...process.env, CODEMIND_AUTO_SYNC: '0' }, stdio: ['pipe', 'pipe', 'pipe'] });
  let diagnostics = '';
  child.stderr.on('data', value => { diagnostics += value.toString(); });
  const options = await new Promise((resolve, reject) => {
    let output = ''; const timer = setTimeout(() => { child.kill(); reject(new Error('Live fixture did not start: ' + diagnostics)); }, 30000);
    child.stdout.on('data', value => { output += value.toString(); if (output.includes('\n')) { clearTimeout(timer); try { resolve(JSON.parse(output.split('\n')[0])); } catch (error) { reject(error); } } });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('exit', code => { if (!output) { clearTimeout(timer); reject(new Error('Live fixture failed: ' + code + ' ' + diagnostics)); } });
  });
  return { options, close: () => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('Live fixture did not stop')); }, 15000);
    child.once('exit', () => { clearTimeout(timer); resolve(); }); child.stdin.end();
  }) };
}

async function run() {
  if (!process.env.CODEMIND_QUAL_REPO_ROOT) return;
  const project = path.resolve(__dirname, '../..');
  const settings = vscode.workspace.getConfiguration('codemind');
  const saved = Object.fromEntries(['hybridEnabled', 'hybridAllowGeneration', 'hybridDepth'].map(key => [key, settings.inspect(key)?.globalValue]));
  const oldPort = process.env.CODEMIND_PORT, oldToken = process.env.CODEMIND_SESSION_TOKEN;
  const output = vscode.window.createOutputChannel('CodeMind real qualification');
  const panel = vscode.window.createWebviewPanel('codemind-real-test', 'CodeMind real qualification', vscode.ViewColumn.One, { enableScripts: true });
  let owner, server, session; const messages = [];
  try {
    owner = await fixture(project);
    const options = owner.options;
    process.env.CODEMIND_PORT = String(options.port); process.env.CODEMIND_SESSION_TOKEN = options.token;
    server = new ServerManager(project, options.rootPath, output);
    await server.start();
    await settings.update('hybridEnabled', true, vscode.ConfigurationTarget.Global);
    await settings.update('hybridAllowGeneration', false, vscode.ConfigurationTarget.Global);
    session = new HybridChatSession(server, async message => { messages.push(message); if (message.html) panel.webview.html = message.html; return panel.webview.postMessage(message); });
    const selected = { id: options.repo, rootPath: options.rootPath };
    await session.ask(selected, 'Where is verifiedSourcePath defined?', () => true);
    let reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(reply.html.includes('data-source-key='));
    for (const depth of ['quick', 'standard', 'deep']) {
      await session.handle({ type: 'hybridDepth', key: reply.key, depth }, selected);
      reply = messages.findLast(message => message.type === 'hybridResults');
      assert.ok(reply.html.includes('data-depth="' + depth + '" disabled'));
    }
    await session.handle({ type: 'hybridSource', key: reply.key, actionKey: /data-source-key="([^"]+)"/.exec(reply.html)[1] }, selected);
    assert.equal(fs.realpathSync(vscode.window.activeTextEditor.document.uri.fsPath), fs.realpathSync(path.join(options.rootPath, 'extension/src/sourceNavigation.ts')));
    assert.equal(vscode.window.activeTextEditor.selection.start.line, 11);
    await session.ask(selected, 'Who calls it?', () => true);
    reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(reply.html.includes('Known direct callers'));
    assert.ok(reply.html.includes('verifiedPreviewSource'));
    const before = messages.length;
    await session.handle({ type: 'hybridSource', key: reply.key, actionKey: 'invented' }, selected);
    await session.handle({ type: 'hybridDepth', key: reply.key, depth: 'deep' }, { ...selected, id: 'f'.repeat(24) });
    assert.equal(messages.length, before);
    await session.ask(selected, 'Who calls verifiedSourcePath and verifiedPreviewSource?', () => true);
    reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(reply.html.includes('verifiedSourcePath')); assert.ok(reply.html.includes('verifiedPreviewSource'));
    await assert.rejects(session.ask(selected, 'How many files are in this repository?', () => true), /statistics/);
    // Actual optional Ollama bridge; acceptance/fallback is visible, not a mocked producer.
    await settings.update('hybridAllowGeneration', true, vscode.ConfigurationTarget.Global);
    await session.ask(selected, 'Where is verifiedSourcePath defined and explain verifiedSourcePath', () => true);
    reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(reply.html.includes('req-001'));
    assert.ok(/Local model:/.test(reply.html));
    assert.ok(!reply.html.includes('command:'));
    const authority = await server.hybridQuery({ repository_id: selected.id, question: 'Where is verifiedSourcePath defined?', depth: 'standard' }, false);
    session.clear(); await settings.update('hybridEnabled', false, vscode.ConfigurationTarget.Global);
    const cleared = messages.length;
    await session.handle({ type: 'hybridDepth', key: reply.key, depth: 'deep' }, selected);
    assert.equal(messages.length, cleared);
    // Restart only the ephemeral server; original engine/index remains untouched.
    server.dispose(); const oldOwner = owner; owner = undefined; await oldOwner.close(); owner = await fixture(project);
    process.env.CODEMIND_PORT = String(owner.options.port); process.env.CODEMIND_SESSION_TOKEN = owner.options.token;
    server = new ServerManager(project, options.rootPath, output); await server.start();
    await assert.rejects(server.hybridPresentation(selected.id, authority.navigation_token, 'quick'), /ask the question again/i);
    console.log('Final real CodeMind VS Code + authenticated read-only backend: depths, both targets, context, canonical source opening, unsupported requests, real Ollama/fallback, repository scope, rollback and ephemeral restart PASS');
  } finally {
    session?.clear(); server?.dispose(); if (owner) await owner.close();
    panel.dispose(); output.dispose(); await vscode.commands.executeCommand('workbench.action.closeAllEditors');
    for (const [key, value] of Object.entries(saved)) await settings.update(key, value, vscode.ConfigurationTarget.Global);
    if (oldPort === undefined) delete process.env.CODEMIND_PORT; else process.env.CODEMIND_PORT = oldPort;
    if (oldToken === undefined) delete process.env.CODEMIND_SESSION_TOKEN; else process.env.CODEMIND_SESSION_TOKEN = oldToken;
  }
}
module.exports = { run };
