'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { createHash } = require('node:crypto');
const Module = require('node:module');
const vm = require('node:vm');
let enabled = true, trusted = true, document, opened = 0;
const vscode = {
  workspace: { get isTrusted() { return trusted; }, getConfiguration: () => ({ get: (key, fallback) => key === 'hybridEnabled' ? enabled : fallback }),
    openTextDocument: async () => document },
  window: { showTextDocument: async () => { opened++; } },
  ViewColumn: { Beside: 2 }, Range: class { constructor(...args) { this.args = args; } },
};
const native = Module._load;
let HybridChatSession, verifiedPreviewSource, openVerifiedPreviewSource, ServerManager;
try {
  Module._load = function(name, ...args) { return name === 'vscode' ? vscode : native.call(this, name, ...args); };
  ({ HybridChatSession } = require('../out/hybridChat'));
  ({ verifiedPreviewSource, openVerifiedPreviewSource } = require('../out/responsePreview'));
  ({ ServerManager } = require('../out/serverManager'));
} finally { Module._load = native; }
const { renderResponseBody } = require('../out/responsePresentation');

function presentation(repo = 'repo') {
  return { schema_version: 'phase9-1', repository_id: repo, snapshot_id: 'b'.repeat(32), package_id: 'package', depth: 'standard', status: 'partial',
    items: {}, requirements: [], sources: {}, clarifications: [], follow_ups: [], limitations: [], model_status: null, fallback_status: null };
}
function response() { return { schema_version: 'hybrid-v1', snapshot_id: 'b'.repeat(32), navigation_token: 'a'.repeat(48), presentation: presentation() }; }
const selected = { id: 'repo', rootPath: '/unused' };

test('unknown malformed forged and untrusted actions cannot dispatch backend requests', async () => {
  let calls = 0;
  const session = new HybridChatSession({ hybridSource: async () => { calls++; } }, async () => true);
  for (const message of [null, 3, 'ask', {}, { type: 'shell' }, { type: 'hybridSource', key: {}, path: '../secret' },
    { type: 'hybridDepth', key: 'forged', depth: 'deep' }]) await session.handle(message, selected);
  trusted = false;
  try { await session.handle({ type: 'hybridSource', key: 'forged' }, selected); } finally { trusted = true; }
  assert.equal(calls, 0);
});

test('missing generation wrong schema and wrong repository query responses fail before rendering', async () => {
  for (const mutate of [value => { value.presentation = null; }, value => { value.schema_version = 'other'; },
    value => { value.presentation.repository_id = 'foreign'; }, value => { value.presentation.snapshot_id = 'c'.repeat(32); }]) {
    const value = response(); mutate(value); const messages = [];
    const server = { hybridReadiness: async () => ({ enabled: true, snapshot_ready: true }), hybridQuery: async () => value };
    const session = new HybridChatSession(server, async message => { messages.push(message); return true; });
    await assert.rejects(session.ask(selected, 'login', () => true), /unavailable|mismatch/);
    assert.equal(messages.length, 0);
  }
});

test('depth replies from a different repository are rejected even with matching package and generation', async () => {
  const messages = [];
  const server = { hybridReadiness: async () => ({ enabled: true, snapshot_ready: true }), hybridQuery: async () => response(),
    hybridPresentation: async () => ({ presentation: presentation('foreign'), source: null }) };
  const session = new HybridChatSession(server, async message => { messages.push(message); return true; });
  await session.ask(selected, 'login', () => true);
  await session.handle({ type: 'hybridDepth', key: messages[0].key, depth: 'deep' }, selected);
  assert.ok(messages.some(message => message.type === 'error' && /generation|repository/i.test(message.text)));
  assert.ok(!messages.filter(message => message.html).some(message => message.html.includes('repository foreign')));
});

test('expired backend handle disables source controls and does not trigger another query', async () => {
  const messages = []; let queryCalls = 0;
  const value = response(); const reference = 'cm7:package:record';
  value.presentation.sources[reference] = { state: 'verified', reason: 'checked', citation: { source: { file_path: 'auth.ts', start_line: 1, end_line: 1 } } };
  const server = { hybridReadiness: async () => ({ enabled: true, snapshot_ready: true }),
    hybridQuery: async () => { queryCalls++; return value; }, hybridPresentation: async () => { throw new Error('Handle expired; ask again.'); } };
  const session = new HybridChatSession(server, async message => { messages.push(message); return true; });
  await session.ask(selected, 'login', () => true);
  assert.ok(messages[0].html.includes('data-source-key='));
  await session.handle({ type: 'hybridDepth', key: messages[0].key, depth: 'deep' }, selected);
  const latest = messages.filter(message => message.html).at(-1);
  assert.ok(!latest.html.includes('data-source-key='));
  assert.ok(messages.some(message => message.type === 'error' && /expired/.test(message.text)));
  assert.equal(queryCalls, 1);
});

test('unknown citation mappings render unavailable text instead of invented source anchors', () => {
  const view = presentation();
  view.items.one = { item_id: 'one', text: 'Verified-looking prose', classification: 'model_interpretation', authoritative: false,
    citation_ids: ['invented'], details: [], limitations: [] };
  view.requirements = [{ requirement_id: 'req-000', order: 0, title: 'Explanation', state: 'partial', original: { intent: 'FUNCTION_EXPLANATION', depends_on: [] },
    supporting_requirement_ids: [], sections: [{ title: 'Details', item_ids: ['one'] }], operations: [], limitations: [], hidden_items: 0 }];
  const html = renderResponseBody(view);
  assert.ok(!html.includes('source-0'));
  assert.ok(/unavailable/i.test(html));
  assert.ok(html.includes('NON-AUTHORITATIVE'));
});

test('source navigation validates mappings identity current bytes and editor state offline', async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-runtime-source-'));
  try {
    const root = fs.realpathSync(temporary), source = 'export function login() { return true; }\n';
    const file = path.join(root, 'auth.ts'); fs.writeFileSync(file, source);
    const sha = value => createHash('sha256').update(value).digest('hex');
    const repo = sha(process.platform === 'win32' ? root.toLowerCase() : root).slice(0, 24);
    const view = presentation(repo), reference = 'cm7:package:record';
    const citation = { reference, record_id: 'record', source: { repository_id: repo, snapshot_id: view.snapshot_id, file_path: 'auth.ts',
      file_id: 'File:' + sha('[' + JSON.stringify(repo) + ', "auth.ts"]'), source_hash: sha(source), start_line: 1, end_line: 1 } };
    view.sources[reference] = { citation, state: 'verified', reason: 'checked' };
    const reply = { presentation: view, source: citation };
    assert.equal(verifiedPreviewSource(root, reply, reference), file);
    for (const mutate of [value => { value.source = null; }, value => { value.source.reference = 'invented'; },
      value => { value.presentation.repository_id = 'f'.repeat(24); }, value => { value.source.source.end_line = 999; },
      value => { value.source.source.file_path = '../secret.ts'; }, value => { value.presentation.sources[reference].state = 'stale'; }]) {
      const bad = structuredClone(reply); mutate(bad); assert.throws(() => verifiedPreviewSource(root, bad, reference));
    }
    document = { isDirty: false, lineCount: 1, getText: () => source, lineAt: () => ({ text: source.trimEnd() }) };
    opened = 0; await openVerifiedPreviewSource(root, reply, reference); assert.equal(opened, 1);
    document.isDirty = true; await assert.rejects(openVerifiedPreviewSource(root, reply, reference), /dirty/);
    document.isDirty = false; trusted = false;
    try { await assert.rejects(openVerifiedPreviewSource(root, reply, reference), /Trust/); } finally { trusted = true; }
    fs.appendFileSync(file, '// changed\n'); assert.throws(() => verifiedPreviewSource(root, reply, reference), /changed/);
    fs.unlinkSync(file); assert.throws(() => verifiedPreviewSource(root, reply, reference));
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});

test('backend transport reports malformed JSON primitives errors disconnects and timeouts safely', async () => {
  const listener = http.createServer((request, reply) => {
    if (request.url === '/timeout') return;
    if (request.url === '/disconnect') { request.socket.destroy(); return; }
    reply.writeHead(request.url === '/error' ? 500 : 200, { 'content-type': 'application/json' });
    reply.end(({ '/null': 'null', '/array': '[]', '/invalid': '{bad', '/error': '{"error":"Safe backend error"}' })[request.url] || '{"reply":"ready"}');
  });
  await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
  const oldPort = process.env.CODEMIND_PORT, oldToken = process.env.CODEMIND_SESSION_TOKEN;
  process.env.CODEMIND_PORT = String(listener.address().port); process.env.CODEMIND_SESSION_TOKEN = 'fake-runtime-token';
  const server = new ServerManager('/unused', '/unused', { appendLine() {} });
  try {
    assert.equal(await server.hello('hello'), 'ready');
    for (const route of ['/null', '/array', '/invalid']) await assert.rejects(server.request('GET', route), /invalid response/i);
    await assert.rejects(server.request('GET', '/error'), /Safe backend error/);
    await assert.rejects(server.request('GET', '/disconnect'));
    await assert.rejects(server.request('GET', '/timeout', undefined, 20), /did not respond/);
  } finally {
    server.dispose(); listener.closeAllConnections(); await new Promise(resolve => listener.close(resolve));
    if (oldPort === undefined) delete process.env.CODEMIND_PORT; else process.env.CODEMIND_PORT = oldPort;
    if (oldToken === undefined) delete process.env.CODEMIND_SESSION_TOKEN; else process.env.CODEMIND_SESSION_TOKEN = oldToken;
  }
});

test('Electron runner refuses a missing local runtime before any download request', async () => {
  let calls = 0; const diagnostics = [];
  const context = { require(name) {
    if (name === 'node:fs') return { existsSync: () => false, mkdtempSync: () => '/tmp/fake-profile', rmSync() {} };
    if (name === '@vscode/test-electron') return { runTests: async () => { calls++; } };
    return require(name);
  }, __dirname, process: { env: {}, exitCode: 0 }, console: { error: value => diagnostics.push(String(value)) } };
  await vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'runTest.js'), 'utf8'), context);
  assert.equal(calls, 0);
  assert.equal(context.process.exitCode, 1);
  assert.ok(diagnostics.some(value => /local VS Code|CODEMIND_TEST_VSCODE/.test(value)));
});
