'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const Module = require('node:module');
const native = Module._load;
const configuration = { get(key, fallback) { return key === 'hybridEnabled' ? true : fallback; } };
Module._load = function(name, ...args) {
  return name === 'vscode' ? { workspace: { getConfiguration: () => configuration, isTrusted: true }, window: {} } : native.call(this, name, ...args);
};
const { HybridChatSession } = require('../out/hybridChat');
Module._load = native;
const selected = { id: 'repo', rootPath: '/unused' };
function response() { return { schema_version: 'hybrid-v1', snapshot_id: 'snapshot', navigation_token: 'a'.repeat(48),
  presentation: { schema_version: 'phase9-1', repository_id: 'repo', snapshot_id: 'snapshot', package_id: 'package', depth: 'standard', status: 'success',
    items: {}, requirements: [], sources: {}, clarifications: [], follow_ups: [], limitations: [], model_status: null, fallback_status: null } }; }

test('cleared session cannot submit an old request after readiness finishes', async () => {
  let release, calls = 0; const messages = [];
  const server = { hybridReadiness: () => new Promise(resolve => { release = resolve; }), hybridQuery: async () => { calls++; return response(); } };
  const session = new HybridChatSession(server, async value => { messages.push(value); return true; });
  const pending = session.ask(selected, 'Who calls login?', () => true);
  session.clear(); release({ enabled: true, snapshot_ready: true }); await pending;
  assert.equal(calls, 0); assert.equal(messages.length, 0);
});

test('cleared session cannot render a late query result', async () => {
  let release, started; const messages = [];
  const entered = new Promise(resolve => { started = resolve; });
  const server = { hybridReadiness: async () => ({ enabled: true, snapshot_ready: true }), hybridQuery: () => new Promise(resolve => { release = resolve; started(); }) };
  const session = new HybridChatSession(server, async value => { messages.push(value); return true; });
  const pending = session.ask(selected, 'Who calls login?', () => true);
  await entered; session.clear(); release(response()); await pending;
  assert.equal(messages.length, 0);
});

test('one-turn context is opaque, repository scoped, and cleared on failed authority', async () => {
  const calls = []; let fail = false;
  const server = { hybridReadiness: async () => ({ enabled: true, snapshot_ready: true }),
    hybridQuery: async (...args) => { calls.push(args); if (fail) throw new Error('Handle expired; ask again.'); return response(); } };
  const session = new HybridChatSession(server, async () => true);
  await session.ask(selected, 'Where is login defined?', () => true);
  await session.ask(selected, 'Who calls it?', () => true);
  assert.equal(calls[1][2], 'a'.repeat(48));
  assert.equal(calls[1][0].previous_target, undefined);
  fail = true;
  await assert.rejects(session.ask(selected, 'Who calls it?', () => true), /expired/);
  fail = false;
  await session.ask(selected, 'Who calls it?', () => true);
  assert.equal(calls.at(-1)[2], undefined);
});

