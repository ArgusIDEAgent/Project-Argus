'use strict';
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');
const { ServerManager } = require('../out/serverManager');
const { HybridChatSession, hybridEnabled, editorQueryContext } = require('../out/hybridChat');

async function fixture(root) {
  const child = spawn(path.join(root, '.venv/bin/python'), [path.join(root, 'ai-engine/tests/phase10_server_fixture.py')], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
  let diagnostics = ''; child.stderr.on('data', data => { diagnostics += data.toString(); });
  const options = await new Promise((resolve, reject) => {
    let output = ''; const timer = setTimeout(() => { child.kill(); reject(new Error('Hybrid fixture timeout: ' + diagnostics)); }, 30000);
    child.stdout.on('data', data => { output += data.toString(); if (output.includes('\n')) { clearTimeout(timer); try { resolve(JSON.parse(output.split('\n')[0])); } catch (error) { reject(error); } } });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('exit', code => { if (!output) { clearTimeout(timer); reject(new Error('Hybrid fixture failed: ' + code + ' ' + diagnostics)); } });
  });
  return { options, close: () => new Promise(resolve => { child.once('exit', resolve); child.stdin.end(); }) };
}

function metrics(options) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: '127.0.0.1', port: options.port, path: '/test/phase10/metrics', headers: { 'x-codemind-session': options.token } }, reply => {
      let body = ''; reply.on('data', data => { body += data; }); reply.on('end', () => { try { resolve(JSON.parse(body)); } catch (error) { reject(error); } });
    }).on('error', reject);
  });
}

async function run() {
  const root = path.resolve(__dirname, '../..');
  const settings = vscode.workspace.getConfiguration('codemind');
  const saved = {};
  for (const key of ['hybridEnabled', 'hybridDepth', 'hybridAllowGeneration', 'checkReuseOnSave']) saved[key] = settings.inspect(key)?.globalValue;
  assert.equal(settings.inspect('hybridEnabled').defaultValue, false);
  const owner = await fixture(root), options = owner.options;
  const oldPort = process.env.CODEMIND_PORT, oldToken = process.env.CODEMIND_SESSION_TOKEN;
  process.env.CODEMIND_PORT = String(options.port); process.env.CODEMIND_SESSION_TOKEN = options.token;
  const output = vscode.window.createOutputChannel('CodeMind Phase10 test');
  const server = new ServerManager(root, options.rootPath, output);
  const panel = vscode.window.createWebviewPanel('codemind-phase10-test', 'CodeMind Hybrid Integration', vscode.ViewColumn.One, { enableScripts: true });
  const messages = [];
  const session = new HybridChatSession(server, message => { messages.push(message); panel.webview.html = message.html || panel.webview.html; return panel.webview.postMessage(message); });
  const selected = { id: options.repo, rootPath: options.rootPath };
  try {
    await server.start();
    await settings.update('hybridEnabled', false, vscode.ConfigurationTarget.Global);
    await settings.update('checkReuseOnSave', false, vscode.ConfigurationTarget.Global);
    assert.equal(hybridEnabled(), false);
    assert.equal((await server.ask(options.repo, 'login')).answer, 'Legacy fixture answer');
    await settings.update('hybridEnabled', true, vscode.ConfigurationTarget.Global);
    await session.ask(selected, 'Who calls login?', () => true);
    let reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(reply.html.includes('CodeMind Hybrid Answer')); assert.ok(reply.html.includes('req-000'));
    assert.ok(reply.html.includes('data-source-key='));
    assert.ok(!reply.html.includes('command:'));
    const initial = await metrics(options);
    assert.equal(initial.query, 1); assert.equal(initial.model, 0);
    const first = /data-source-key="([^"]+)"/.exec(reply.html)[1];
    await session.handle({ type: 'hybridSource', key: reply.key, actionKey: 'invented', path: '/etc/passwd' }, selected);
    await session.handle({ type: 'hybridSource', key: reply.key, actionKey: first }, { ...selected, id: 'f'.repeat(24) });
    assert.equal((await metrics(options)).source, 0);
    for (const depth of ['quick', 'standard', 'deep']) {
      await session.handle({ type: 'hybridDepth', key: reply.key, depth }, selected);
      reply = messages.findLast(message => message.type === 'hybridResults');
      assert.ok(reply.html.includes('data-depth="' + depth + '" disabled'));
    }
    const action = /data-source-key="([^"]+)"/.exec(reply.html)[1];
    await session.handle({ type: 'hybridSource', key: reply.key, actionKey: action }, selected);
    const editor = vscode.window.activeTextEditor;
    assert.ok(fs.realpathSync(editor.document.uri.fsPath).startsWith(options.rootPath + path.sep), JSON.stringify(messages.filter(message => message.type === 'error')));
    assert.ok(editor.selection.start.line >= 0);
    const context = editorQueryContext(options.rootPath);
    assert.equal(context.active_file, path.relative(options.rootPath, editor.document.uri.fsPath));
    assert.equal(context.cursor.line, editor.selection.active.line + 1);
    assert.deepEqual(editorQueryContext(options.rootPath, 'Who calls login?'), {});
    assert.ok(editorQueryContext(options.rootPath, 'Explain this function').cursor);
    const afterDepth = await metrics(options);
    assert.equal(afterDepth.query, 1); assert.equal(afterDepth.model, 0); assert.equal(afterDepth.presentation, 3); assert.equal(afterDepth.source, 1);
    // Real transport -> orchestrator -> validated mocked local response -> chat renderer.
    await session.ask(selected, 'Where is `login` defined and explain `login`', () => true);
    reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(reply.html.includes('req-001')); assert.ok(reply.html.includes('Local model: success'), (reply.html.match(/Local model:[^<]+/) || []).join('') + ' ' + JSON.stringify(await metrics(options)));
    const explained = await metrics(options); assert.equal(explained.query, 2); assert.equal(explained.model, 1);
    await session.handle({ type: 'hybridDepth', key: reply.key, depth: 'quick' }, selected);
    assert.equal((await metrics(options)).model, 1);
    reply = messages.findLast(message => message.type === 'hybridResults');
    const savedLogin = fs.readFileSync(path.join(options.rootPath, 'login.ts'));
    fs.appendFileSync(path.join(options.rootPath, 'login.ts'), '// stale fixture\n');
    await session.handle({ type: 'hybridSource', key: reply.key, actionKey: /data-source-key="([^"]+)"/.exec(reply.html)[1] }, selected);
    reply = messages.findLast(message => message.type === 'hybridResults');
    assert.ok(/Stale|stale_index/.test(reply.html)); assert.ok(!reply.html.includes('data-source-key='));
    await settings.update('hybridEnabled', false, vscode.ConfigurationTarget.Global); session.clear();
    fs.writeFileSync(path.join(options.rootPath, 'login.ts'), savedLogin);
    const beforeRollback = await metrics(options);
    await session.handle({ type: 'hybridDepth', key: reply.key, depth: 'deep' }, selected);
    assert.equal((await metrics(options)).presentation, beforeRollback.presentation);
    assert.equal((await server.ask(options.repo, 'login')).answer, 'Legacy fixture answer');
    assert.equal(vscode.workspace.getConfiguration('codemind').get('checkReuseOnSave'), false);
    console.log('Phase 10 real VS Code host + authenticated loopback API: opt-in, saved settings, deterministic/model routing, depths, verified editor navigation, stale guard, rollback PASS');
  } finally {
    session.clear(); panel.dispose(); server.dispose(); output.dispose();
    await vscode.commands.executeCommand('workbench.action.closeAllEditors');
    for (const [key, value] of Object.entries(saved)) await settings.update(key, value, vscode.ConfigurationTarget.Global);
    if (oldPort === undefined) delete process.env.CODEMIND_PORT; else process.env.CODEMIND_PORT = oldPort;
    if (oldToken === undefined) delete process.env.CODEMIND_SESSION_TOKEN; else process.env.CODEMIND_SESSION_TOKEN = oldToken;
    await owner.close();
  }
}
module.exports = { run };
