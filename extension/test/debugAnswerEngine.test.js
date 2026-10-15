'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { debugAnswerEngineFooter, debugLegacyAnswerEngineFooter, debugStaticAnswerEngineFooter } = require('../out/debugAnswerEngine');

function view(items = [], order = 0) {
  return { items: Object.fromEntries(items.map(([id, origin]) => [id, { origin }])),
    requirements: [{ order, sections: [{ item_ids: items.map(([id]) => id) }] }] };
}
const op = (engine, ids, status = 'success') => ({ operation_id: engine, engine, status, finding_ids: ids });
const gen = (model_status, fallback_status) => ({ original_orders: [0], model_status, fallback_status });

test('visible Phase 6 findings attribute graph, semantic, and rules once in fixed order', () => {
  const shown = view([['g', 'finding'], ['s', 'finding'], ['r', 'finding']]);
  assert.match(debugAnswerEngineFooter(view([['g', 'finding']]), [op('graph', ['g'])]), /Answer by: Graph Query/);
  assert.match(debugAnswerEngineFooter(view([['s', 'finding']]), [op('semantic', ['s'])]), /Answer by: Semantic Search/);
  assert.match(debugAnswerEngineFooter(view([['r', 'finding']]), [op('rules', ['r'])]), /Answer by: Rule-Based/);
  assert.match(debugAnswerEngineFooter(shown, [op('rules', ['r']), op('graph', ['g']), op('semantic', ['s']), op('graph', ['g'])]),
    /Answer by: Graph Query \+ Semantic Search \+ Rule-Based/);
});

test('successful generation and deterministic fallback use actual Phase 8 status', () => {
  const shown = view([['g', 'finding'], ['model', 'generation']]);
  assert.match(debugAnswerEngineFooter(shown, [op('graph', ['g'])], [gen('success', 'not_used')]),
    /Answer by: Graph Query \+ Graph RAG \+ Local LLM/);
  const fallback = debugAnswerEngineFooter(shown, [op('graph', ['g'])], [gen('unavailable', 'evidence_summary')]);
  assert.match(fallback, /Answer by: Graph Query \+ Graph RAG \(deterministic fallback\)/);
  assert.doesNotMatch(fallback, /Local LLM/);
  const generatedOnly = view([['model', 'generation']]);
  const sourced = { ...gen('success', 'not_used'), claim_operation_ids: { model: ['rule-op', 'graph-op'] } };
  assert.match(debugAnswerEngineFooter(generatedOnly,
    [{ ...op('rules', ['hidden']), operation_id: 'rule-op' }, { ...op('graph', ['hidden']), operation_id: 'graph-op' }], [sourced]),
    /Answer by: Graph Query \+ Rule-Based \+ Graph RAG \+ Local LLM/);
});

test('failed, unsupported, hidden, and missing metadata remain unknown', () => {
  for (const [shown, operations, generations] of [
    [view(), [op('graph', [], 'error')], []],
    [view(), [op('rules', [], 'unsupported')], []],
    [view([['shown', 'finding']]), [op('graph', ['hidden'])], []],
    [view([['shown', 'finding']]), [op('graph', undefined)], []],
    [view([['model', 'generation']]), [], []],
    [undefined, undefined, undefined],
  ]) assert.match(debugAnswerEngineFooter(shown, operations, generations), /Answer by: Unknown/);
});

test('footer is a separate final display element and contains no source content', () => {
  const footer = debugAnswerEngineFooter(view([['g', 'finding']]), [op('graph', ['g'])]);
  assert.ok(footer.startsWith('<footer class="debug-answer-engine"'));
  assert.ok(footer.endsWith('</footer>'));
  assert.doesNotMatch(footer, /citation|source|prompt/i);
});

test('legacy answer uses returned retrieval methods and successful model status', () => {
  const reply = { answerMode: 'local-llm', results: [
    { retrievalMethods: ['semantic', 'keyword'] }, { retrievalMethods: ['semantic'] },
  ] };
  assert.match(debugLegacyAnswerEngineFooter(reply), /Answer by: Keyword Search \+ Semantic Search \+ Local LLM/);
  assert.equal((debugLegacyAnswerEngineFooter(reply).match(/Semantic Search/g) || []).length, 1);
  assert.doesNotMatch(debugLegacyAnswerEngineFooter(reply), /Graph RAG/);
});

test('legacy deterministic fallback and absent execution metadata do not claim a model answer', () => {
  assert.match(debugLegacyAnswerEngineFooter({ answerMode: 'evidence', results: [{ retrievalMethods: ['keyword'] }] }),
    /Answer by: Keyword Search \+ Evidence Summary/);
  assert.match(debugLegacyAnswerEngineFooter({ answerMode: 'evidence', results: [] }), /Answer by: Unknown/);
  assert.match(debugLegacyAnswerEngineFooter({ results: [{}] }), /Answer by: Unknown/);
});

test('static responses are attributed without claiming retrieval or generation', () => {
  assert.match(debugLegacyAnswerEngineFooter({ answerMode: 'static', results: [] }), /Answer by: Static Response/);
  const footer = debugStaticAnswerEngineFooter();
  assert.match(footer, /Answer by: Static Response/);
  assert.doesNotMatch(footer, /Graph|Semantic|Rule-Based|LLM/);
});
