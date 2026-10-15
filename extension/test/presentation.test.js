'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const { escapeDisplay, renderResponseBody, responsePreviewHtml } = require('../out/responsePresentation');

function view() {
  const proof = { file_path: 'login.ts', start_line: 3, end_line: 3 };
  return { schema_version: 'phase9-1', repository_id: 'repo', snapshot_id: 'snapshot', package_id: 'package', depth: 'standard', status: 'partial',
    items: { finding: { item_id: 'finding', text: 'Static CALLS: entry → login', classification: 'verified_graph_relationship', authoritative: true,
      citation_ids: ['cm7:package:record'], details: ['ordered path'], limitations: ['static only'] } },
    requirements: [{ requirement_id: 'req-000', order: 0, original: { intent: 'DIRECT_CALLERS', depends_on: [] },
      title: 'Known direct callers', state: 'partially_satisfied', supporting_requirement_ids: ['req-000'],
      sections: [{ title: 'Known direct callers', item_ids: ['finding'] }], limitations: ['partial evidence'], hidden_items: 0,
      operations: [{ operation_id: 'op-000', execution_status: 'partial', coverage_state: 'partially_satisfied', limitations: ['operation limit'] }] }],
    sources: { 'cm7:package:record': { citation: { reference: 'cm7:package:record', record_id: 'record', source: proof }, state: 'verified', reason: 'checked' } },
    clarifications: [], follow_ups: [], limitations: ['no runtime proof'], model_status: null, fallback_status: null };
}

test('escapes all five HTML-significant characters', () => {
  assert.equal(escapeDisplay(`<>&"'`), '&lt;&gt;&amp;&quot;&#39;');
});

test('escaping covers source, model prose, headings, metadata, limitations and follow-ups', () => {
  const result = view();
  const hostile = '</script><img src=x onerror="alert(1)">';
  result.items.finding.text = hostile;
  result.items.finding.details = [hostile]; result.requirements[0].title = hostile;
  result.sources['cm7:package:record'].citation.source.file_path = hostile;
  result.limitations = [hostile]; result.follow_ups = [{ label: hostile, prompt: hostile, mode: 'copy_query' }];
  const html = responsePreviewHtml(result, { 'cm7:package:record': 'opaque' }, { 0: 'copy-opaque' });
  assert.ok(!html.includes(hostile)); assert.ok(html.includes('&lt;/script&gt;&lt;img'));
  assert.equal((html.match(/<script nonce=/g) || []).length, 1);
  assert.ok(!html.includes('href="command:')); assert.ok(!html.includes('href="https:'));
});

test('quick standard and deep use supplied details and preserve uncertainty', () => {
  for (const depth of ['quick', 'standard', 'deep']) {
    const result = view(); result.depth = depth;
    if (depth === 'quick') result.items.finding.details = [];
    const html = renderResponseBody(result);
    assert.ok(html.includes('Partial coverage'));
    assert.ok(html.includes('operation limit')); assert.ok(html.includes('partial evidence'));
    assert.equal(html.includes('ordered path'), depth !== 'quick');
  }
});

test('every template family is renderable without generated text', () => {
  for (const title of ['Symbol location', 'Known direct callers', 'Known direct callees', 'Static dependencies and paths',
    'Feature-location candidates', 'Indexed function contract', 'Potential change impact', 'Indexed module overview',
    'Indexed test/reference discovery', 'Function explanation', 'Static workflow explanation', 'Possible behavioral causes', 'Comparison of explicit targets']) {
    const result = view(); result.requirements[0].title = title;
    assert.ok(renderResponseBody(result).includes(title));
  }
});

test('shared sources display once and retain per-item citation anchors', () => {
  const result = view(); result.items.second = { ...result.items.finding, item_id: 'second' };
  result.requirements[0].sections[0].item_ids.push('second');
  const html = renderResponseBody(result, { 'cm7:package:record': 'opaque' });
  assert.equal((html.match(/data-source-key=/g) || []).length, 1);
  assert.equal((html.match(/href="#source-1"/g) || []).length, 2);
});

test('invalid stale unauthorized and pending sources have no navigation controls', () => {
  for (const state of ['requires_verification', 'stale', 'unavailable', 'unauthorized']) {
    const result = view(); result.sources['cm7:package:record'].state = state;
    const html = renderResponseBody(result, { 'cm7:package:record': 'opaque' });
    assert.ok(!html.includes('data-source-key=')); assert.ok(html.includes('not navigable'));
  }
});

test('all explicit operation and coverage outcomes remain visible', () => {
  for (const state of ['success', 'partial', 'ambiguous', 'not_found', 'unsupported', 'stale_index', 'error', 'deferred', 'skipped']) {
    const result = view(); result.status = state; result.requirements[0].state = state;
    assert.ok(renderResponseBody(result).length > 0);
  }
});

test('hypotheses similarity and model interpretations have distinct visible labels', () => {
  for (const [classification, label] of [['hypothetical_consequence', 'HYPOTHESIS'], ['model_interpretation', 'not fact-checked'], ['unresolved_assertion', 'UNRESOLVED']]) {
    const result = view(); result.items.finding.classification = classification; result.items.finding.authoritative = false;
    const html = renderResponseBody(result); assert.ok(html.includes(label)); assert.ok(html.includes('NON-AUTHORITATIVE'));
  }
  const result = view(); result.items.finding.text = 'Candidate: similarity/keyword relevance, not a dependency';
  assert.ok(renderResponseBody(result).includes('not a dependency'));
});

test('model failures and deterministic fallback are not hidden', () => {
  const result = view(); result.model_status = 'unavailable'; result.fallback_status = 'evidence_summary';
  const html = renderResponseBody(result);
  assert.ok(html.includes('Local model: unavailable')); assert.ok(html.includes('fallback: evidence_summary'));
  assert.ok(html.includes('Static CALLS'));
});

test('compound repeated intents retain separate IDs dependencies and failed branches', () => {
  const result = view(); result.requirements.push({ ...structuredClone(result.requirements[0]), requirement_id: 'req-001', order: 1, state: 'error', original: { intent: 'DIRECT_CALLERS', depends_on: [0] } });
  const html = renderResponseBody(result);
  assert.ok(html.includes('req-000')); assert.ok(html.includes('req-001')); assert.ok(html.includes('Target dependencies: 1'));
  assert.ok(html.includes('Failed operation/result')); assert.ok(html.includes('Partial coverage'));
});

test('clarification does not choose or execute candidates', () => {
  const result = view(); result.clarifications = [{ requirement_id: 'req-000', reason: 'Choose a scope', truncated: true,
    candidates: [{ name: 'duplicate', file_path: 'one.ts', entity_id: 'one' }, { name: 'duplicate', file_path: 'two.ts', entity_id: 'two' }] }];
  const html = renderResponseBody(result);
  assert.ok(html.includes('no target selected')); assert.ok(html.includes('one.ts')); assert.ok(html.includes('two.ts'));
  assert.ok(html.includes('Candidate list is truncated')); assert.ok(!html.includes('data-candidate-key'));
});

test('repeated body formatting is deterministic', () => {
  const result = view(); assert.equal(renderResponseBody(result), renderResponseBody(result));
});

test('webview sends only opaque keys and depth, never paths tools or retrieval messages', () => {
  const html = responsePreviewHtml(view(), { 'cm7:package:record': 'opaque-key' }, {});
  const script = /<script nonce="[^"]+">([\s\S]*?)<\/script>/.exec(html)[1];
  const messages = []; let handler;
  vm.runInNewContext(script, { acquireVsCodeApi: () => ({ postMessage: value => messages.push(value) }),
    document: { addEventListener: (_, callback) => { handler = callback; } }, window: { addEventListener: () => {} } });
  handler({ target: { closest: () => ({ dataset: { sourceKey: 'opaque-key' } }) } });
  assert.equal(JSON.stringify(messages), '[{"type":"previewSource","key":"opaque-key"}]');
  assert.ok(!script.includes('fetch(')); assert.ok(!script.includes("type:'ask'")); assert.ok(!script.includes('innerHTML'));
});

test('follow-ups copy supported queries without creating backend actions', () => {
  const result = view(); result.follow_ups = [{ label: 'Show direct callers', prompt: 'Who calls `login`?', mode: 'copy_query' }];
  const html = renderResponseBody(result, {}, { 0: 'opaque-copy' });
  assert.ok(html.includes('Copy query: Show direct callers')); assert.ok(html.includes('data-follow-up-key="opaque-copy"'));
  assert.ok(!html.includes('data-search')); assert.ok(!html.includes('command:'));
});

test('hybrid rendering shares escaping and has per-answer citation anchor namespaces', () => {
  const result = view(); result.items.finding.text = '<img src=x onerror="run()">';
  const first = renderResponseBody(result, { 'cm7:package:record': 'opaque' }, {}, 'hybrid', 'answer-one-');
  const second = renderResponseBody(result, {}, {}, 'hybrid', 'answer-two-');
  assert.ok(first.includes('CodeMind Hybrid Answer'));
  assert.ok(first.includes('&lt;img')); assert.ok(!first.includes('<img'));
  assert.ok(first.includes('href="#answer-one-source-1"'));
  assert.ok(second.includes('id="answer-two-source-1"'));
  assert.ok(!first.includes('<script')); assert.ok(!first.includes('href="command:'));
});

test('existing saved settings and dependencies remain compatible with default-off opt-in', () => {
  const manifest = require('../package.json');
  const settings = manifest.contributes.configuration.properties;
  assert.equal(settings['codemind.hybridEnabled'].default, false);
  assert.equal(settings['codemind.checkReuseOnSave'].default, true);
  assert.deepEqual(settings['codemind.hybridDepth'].enum, ['quick', 'standard', 'deep']);
  assert.deepEqual(manifest.dependencies, {});
});

test('actual chat webview forwards only opaque hybrid actions, disables them on rollback, and starts a new chat', () => {
  const Module = require('node:module');
  const native = Module._load;
  let html;
  try {
    Module._load = function(name, ...args) { return name === 'vscode' ? {} : native.call(this, name, ...args); };
    html = require('../out/extension').chatHtml({});
  } finally { Module._load = native; }
  const script = /<script nonce="[^"]+">([\s\S]*?)<\/script>/.exec(html)[1];
  const nodes = new Map(), posts = [], controls = [];
  let receive;
  function element() { return { dataset: {}, children: [], listeners: {}, disabled: false, focus() {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...values) { this.children.push(...values); },
    insertAdjacentHTML(_position, value) { this.insertedHtml = (this.insertedHtml || '') + value; },
    replaceChildren(...values) { this.children.splice(0, this.children.length, ...values); },
    querySelectorAll() { return controls; } }; }
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    createElement: element, querySelector() { return nodes.get('answer'); } };
  vm.runInNewContext(script, { acquireVsCodeApi: () => ({ postMessage: value => posts.push(value) }), document,
    window: { addEventListener(_, callback) { receive = callback; } }, clearTimeout() {}, setTimeout() {} });
  const answer = element(); answer.dataset.answerKey = 'opaque-answer'; nodes.set('answer', answer);
  const control = element(); control.dataset = { sourceKey: 'opaque-source' }; control.closest = () => answer;
  controls.push(control);
  const click = nodes.get('messages').listeners.click;
  click({ target: { closest: () => control } });
  control.dataset = { depth: 'deep' }; click({ target: { closest: () => control } });
  control.dataset = { followUpKey: 'opaque-copy' }; click({ target: { closest: () => control } });
  assert.equal(JSON.stringify(posts), '[{"type":"hybridSource","key":"opaque-answer","actionKey":"opaque-source"},{"type":"hybridDepth","key":"opaque-answer","depth":"deep"},{"type":"hybridCopyQuery","key":"opaque-answer","actionKey":"opaque-copy"}]');
  receive({ data: { type: 'hybridResults', key: 'opaque-answer', html: '<section>Host-escaped content</section>' } });
  assert.equal(answer.innerHTML, '<section>Host-escaped content</section>');
  nodes.get('messages').querySelectorAll = () => [answer];
  receive({ data: { type: 'hybridRollback' } });
  assert.ok(control.disabled); click({ target: { closest: () => control } });
  assert.equal(posts.length, 3);
  const messages = nodes.get('messages'); messages.append(element());
  nodes.get('message-input').value = 'old draft'; nodes.get('send-button').disabled = true;
  receive({ data: { type: 'newChat' } });
  assert.equal(messages.children.length, 0);
  assert.equal(nodes.get('message-input').value, '');
  assert.equal(nodes.get('send-button').disabled, false);
  receive({ data: { type: 'reply', text: 'Hi!', debugFooter: '<footer>Answer by: Static Response</footer>' } });
  assert.equal(messages.children.at(-1).children.at(-1).textContent, 'Hi!');
  assert.match(messages.insertedHtml, /Answer by: Static Response/);
});

test('repository comparison starts a new chat only for a different path', () => {
  const Module = require('node:module');
  const native = Module._load;
  let isDifferentRepository;
  try {
    Module._load = function(name, ...args) { return name === 'vscode' ? {} : native.call(this, name, ...args); };
    ({ isDifferentRepository } = require('../out/extension'));
  } finally { Module._load = native; }
  assert.equal(isDifferentRepository(undefined, '/repo/one'), false);
  assert.equal(isDifferentRepository('/repo/one', '/repo/one'), false);
  assert.equal(isDifferentRepository('/repo/one', '/repo/two'), true);
});

test('chat answer footer appears once after the answer and source cards', () => {
  const Module = require('node:module');
  const native = Module._load;
  let html;
  try {
    Module._load = function(name, ...args) { return name === 'vscode' ? {} : native.call(this, name, ...args); };
    html = require('../out/extension').chatHtml({});
  } finally { Module._load = native; }
  const script = /<script nonce="[^"]+">([\s\S]*?)<\/script>/.exec(html)[1];
  const nodes = new Map(); let receive;
  function element() { return { children: [], listeners: {}, focus() {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...values) { this.children.push(...values); },
    insertAdjacentHTML(_, value) { this.children.push({ html: value }); },
    querySelectorAll() { return []; } }; }
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    createElement: element };
  vm.runInNewContext(script, { acquireVsCodeApi: () => ({ postMessage() {} }), document,
    window: { addEventListener(_, callback) { receive = callback; } }, clearTimeout() {}, setTimeout() {} });
  const footer = '<footer class="debug-answer-engine">Answer by: Semantic Search + Local LLM</footer>';
  receive({ data: { type: 'results', answer: 'Explanation.\n\nSources:\n[1] auth.ts:1-2', results: [{
    key: 'source', path: 'auth.ts', startLine: 1, endLine: 2, name: 'validateToken',
    reason: 'Verified evidence', callers: [], tests: [], canIgnore: false,
  }], warning: null, debugFooter: footer } });
  const children = nodes.get('messages').children;
  assert.equal(children.length, 3);
  assert.equal(children[1].className, 'code-result');
  assert.equal(children[2].html, footer);
  assert.ok(children[0].children.some(item => item.textContent.includes('Sources:')));
  assert.ok(!children[0].children.some(item => item.textContent?.includes('Answer by:')));
});
