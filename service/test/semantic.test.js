'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { test } = require('node:test');
const { Registry } = require('../src/registry');
const { prepareGraph } = require('../src/graphBuilder');
const { prepareSemantic } = require('../src/semanticStore');
const { searchCode } = require('../src/retrievalService');
const { analyzeReuse, recordFeedback } = require('../src/reuseAnalyzer');
const { calibrateScores } = require('../src/semanticCalibration');
const { parseCode } = require('../src/codeParser');
const { chunksForFile } = require('../src/chunker');
const { EmbeddingProvider, normalize } = require('../src/embeddingProvider');
const { all } = require('../src/graphStore');
const http = require('node:http');

class TestProvider {
  constructor() { this.model = 'fixture-v1'; this.count = 0; }
  async identity() { if (this.offline) throw new Error('offline'); return this.model; }
  async embed(texts) {
    this.count += texts.length;
    return texts.map(text => normalize([/email|address|lowercase|toLowerCase/.test(text) ? 1 : 0,
      /clamp|bounds|minimum|maximum/.test(text) ? 1 : 0, /JSON|Json|json/.test(text) ? 1 : 0,
      /add|sum|left/.test(text) ? 1 : 0, 0.1]));
  }
}

async function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-semantic-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo');
  fs.cpSync(path.join(__dirname, 'fixtures', 'phase3'), repo, { recursive: true });
  const git = (...args) => execFileSync('git', ['-C', repo, ...args], { stdio: 'pipe' });
  git('init'); git('add', '.');
  git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'Add normalization and numeric utilities');
  const registry = await Registry.open(path.join(root, 'state'));
  const repository = await registry.register(repo);
  const provider = new TestProvider();
  async function refresh() {
    const id = await registry.createJob(repository.id);
    await registry.updateJob(id, 'running');
    const inventory = await registry.scan(repository.id);
    inventory.graph = await prepareGraph(registry, repository.id, inventory);
    inventory.semantic = await prepareSemantic(registry, repository.id, inventory, provider);
    await registry.updateJob(id, 'completed', inventory);
    return (await registry.job(id)).result;
  }
  await refresh();
  return { repo, registry, repository, provider, refresh };
}

test('hybrid search joins graph entities and incrementally refreshes vectors', async t => {
  const { registry, repository, provider, refresh, repo } = await fixture(t);
  let result = await searchCode(registry, provider, { repoId: repository.id, query: 'normalizeEmail' });
  assert.equal(result.mode, 'hybrid');
  assert.equal(result.results[0].name, 'normalizeEmail');
  assert.ok(result.results[0].revision);
  const calls = provider.count;
  const unchanged = await refresh();
  assert.equal(provider.count, calls);
  assert.equal(unchanged.semantic.reembeddedCount, 0);
  const persisted = await registry.withDb(db => all(db, 'SELECT metadata_json FROM semantic_chunks'));
  assert.ok(persisted.some(row => JSON.parse(row.metadata_json).kind === 'document'));
  assert.ok(persisted.some(row => JSON.parse(row.metadata_json).kind === 'commit'));
  assert.ok(persisted.every(row => !('text' in JSON.parse(row.metadata_json))));
  fs.appendFileSync(path.join(repo, 'utilities.js'), '\nexport function twice(value) { return value * 2; }\n');
  const changed = await refresh();
  assert.equal(changed.semantic.reembeddedCount, 1);
  provider.model = 'fixture-v2';
  const changedModel = await refresh();
  assert.equal(changedModel.semantic.reembeddedCount, changedModel.semantic.chunkCount);
  fs.renameSync(path.join(repo, 'duplicates.js'), path.join(repo, 'renamed.js'));
  await refresh();
  result = await searchCode(registry, provider, { repoId: repository.id, query: 'sum' });
  assert.ok(result.results.some(row => row.path === 'renamed.js'));
  assert.ok(result.results.every(row => row.path !== 'duplicates.js'));
  fs.unlinkSync(path.join(repo, 'renamed.js'));
  await refresh();
  assert.equal((await registry.withDb(db => all(db, "SELECT * FROM graph_nodes WHERE path = 'renamed.js'"))).length, 0);
  const reopened = await Registry.open(registry.dataDir);
  assert.equal((await reopened.status(repository.id)).semantic.state, 'ready');
});

test('duplicates require compatible bodies and signatures; ignore feedback and cleanup persist', async t => {
  const { registry, repository, provider, refresh, repo } = await fixture(t);
  const search = await searchCode(registry, provider, { repoId: repository.id, query: 'sum' });
  const source = search.results.find(row => row.name === 'sum');
  const result = await analyzeReuse(registry, provider, { repoId: repository.id, entityId: source.entityId });
  assert.ok(result.results.some(row => row.name === 'add' && row.path === 'utilities.js' && row.classification === 'REUSE'));
  assert.ok(!result.results.some(row => row.name === 'add' && row.path === 'duplicates.js' && row.classification === 'REUSE'));
  const target = result.results.find(row => row.classification === 'REUSE');
  const sameName = await analyzeReuse(registry, provider, { repoId: repository.id, entityId: target.entityId });
  assert.ok(!sameName.results.some(row => row.path === 'duplicates.js' && row.name === 'add'));
  const edges = await registry.withDb(db => all(db, "SELECT * FROM graph_edges WHERE type = 'SIMILAR_TO'"));
  assert.ok(edges.length);
  await recordFeedback(registry, { repoId: repository.id, entityId: target.entityId, queryHash: result.queryHash, decision: 'ignore' });
  assert.ok(!(await analyzeReuse(registry, provider, { repoId: repository.id, entityId: source.entityId })).results.some(row => row.entityId === target.entityId));
  fs.writeFileSync(path.join(repo, 'utilities.js'), 'export function add(left, right) { return left / right; }');
  const stale = await searchCode(registry, provider, { repoId: repository.id, query: 'normalizeEmail' });
  assert.ok(stale.results.every(row => row.path !== 'utilities.js'));
  assert.ok(stale.staleCount > 0);
  await refresh();
  assert.equal((await registry.withDb(db => all(db, "SELECT * FROM graph_edges WHERE type = 'SIMILAR_TO'"))).length, 0);
});

test('provider failure preserves lexical search, recovery retries, and requests stay repository scoped', async t => {
  const { registry, repository, provider, refresh } = await fixture(t);
  provider.offline = true;
  assert.equal((await refresh()).semantic.state, 'degraded');
  const result = await searchCode(registry, provider, { repoId: repository.id, query: 'normalizeEmail' });
  assert.equal(result.mode, 'lexical');
  assert.equal(result.results[0].name, 'normalizeEmail');
  await assert.rejects(searchCode(registry, provider, { repoId: repository.id, query: 'test', contextEntityId: 'a'.repeat(40) }), /Context entity/);
  await assert.rejects(searchCode(registry, provider, { repoId: repository.id, query: 'test', limit: -1 }), /Invalid search/);
  provider.offline = false;
  assert.equal((await refresh()).semantic.state, 'ready');
  await registry.remove(repository.id);
  assert.equal((await registry.withDb(db => all(db, 'SELECT * FROM semantic_chunks'))).length, 0);
  assert.equal((await registry.withDb(db => all(db, 'SELECT * FROM semantic_embeddings'))).length, 0);
});

test('large symbols remain single bounded chunks with exact source ranges', async () => {
  const source = 'export function large(value) {\n' + 'value += 1;\n'.repeat(1000) + 'return value;\n}\nexport function small() { return 2; }';
  const facts = await parseCode({ repoId: 'a'.repeat(24), filePath: 'large.js', language: 'javascript', source, contentHash: 'hash', revision: 'test' });
  const chunks = chunksForFile('a'.repeat(24), facts, source);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].summarized, true);
  assert.equal(chunks[0].endLine, 1003);
  assert.ok(chunks[0].text.length <= 6000);
  assert.ok(!chunks[0].text.includes('function small'));
});

test('calibration rejects noisy labels and selects a threshold with high precision', () => {
  const metrics = calibrateScores([{ label: 'EXTEND', score: 0.83 }, { label: 'REUSE', score: 0.91 },
    { label: 'DISTINCT', score: 0.72 }, { label: 'DISTINCT', score: 0.51 }]);
  assert.equal(metrics.precision, 1);
  assert.equal(metrics.recall, 1);
  assert.ok(metrics.extend > 0.72 && metrics.extend <= 0.83);
  assert.throws(() => calibrateScores([{ label: 'EXTEND', score: 0.5 }, { label: 'DISTINCT', score: 0.8 }]), /do not support/);
});

test('embedding vectors and endpoints are validated', () => {
  assert.throws(() => new EmbeddingProvider({ url: 'https://example.com' }), /loopback/);
  assert.throws(() => normalize([NaN, 1]), /Invalid/);
  assert.throws(() => normalize([0, 0]), /Empty/);
  assert.deepEqual(normalize([3, 4]), [0.6, 0.8]);
});

test('local provider uses the batched embedding API and rejects malformed results', async t => {
  let malformed = false;
  const server = http.createServer(async (request, response) => {
    response.setHeader('content-type', 'application/json');
    if (request.url === '/api/tags') { response.end(JSON.stringify({ models: [{ name: 'nomic-embed-text:latest', digest: 'digest' }] })); return; }
    const buffers = [];
    for await (const buffer of request) buffers.push(buffer);
    const body = JSON.parse(Buffer.concat(buffers));
    assert.equal(body.truncate, false);
    assert.ok(body.input[0].startsWith('search_query: '));
    response.end(JSON.stringify({ embeddings: malformed ? [[0, 0]] : body.input.map(() => [3, 4]) }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const provider = new EmbeddingProvider({ url: `http://127.0.0.1:${server.address().port}` });
  assert.match(await provider.identity(), /digest/);
  assert.deepEqual(await provider.embed(['query'], 'query'), [[0.6, 0.8]]);
  malformed = true;
  await assert.rejects(provider.embed(['query'], 'query'), /Empty/);
});

test('an edit during embedding does not publish a mixed snapshot', async t => {
  const { registry, repository, provider, refresh, repo } = await fixture(t);
  const before = (await registry.status(repository.id)).semantic.revision;
  fs.appendFileSync(path.join(repo, 'utilities.js'), '\nexport function thrice(value) { return value * 3; }');
  const embed = provider.embed.bind(provider);
  provider.embed = async texts => {
    const result = await embed(texts);
    fs.appendFileSync(path.join(repo, 'utilities.js'), '\n// concurrent edit');
    return result;
  };
  await assert.rejects(refresh(), /Source changed/);
  assert.equal((await registry.status(repository.id)).semantic.revision, before);
});
