'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { test } = require('node:test');
const { Registry } = require('../src/registry');
const { prepareGraph } = require('../src/graphBuilder');
const { prepareSemantic } = require('../src/semanticStore');
const { searchCode } = require('../src/retrievalService');
const { all } = require('../src/graphStore');
const { ModelProvider } = require('../src/modelProvider');

const unavailable = { async identity() { throw new Error('offline'); } };
const noModel = { async available() { throw new Error('offline'); }, async generate() { throw new Error('offline'); } };

async function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-docs-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repo = path.join(root, 'repo');
  fs.cpSync(path.join(__dirname, 'fixtures', 'phase2'), repo, { recursive: true });
  fs.writeFileSync(path.join(repo, 'README.md'), '# Setup\n\nInstall dependencies before running tests.\n');
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'add', '.']);
  execFileSync('git', ['-C', repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
    'commit', '-qm', 'Fixture']);
  const registry = await Registry.open(path.join(root, 'state'));
  const repository = await registry.register(repo);
  async function refresh() {
    const id = await registry.createJob(repository.id);
    await registry.updateJob(id, 'running');
    const inventory = await registry.scan(repository.id);
    inventory.graph = await prepareGraph(registry, repository.id, inventory);
    inventory.semantic = await prepareSemantic(registry, repository.id, inventory, unavailable);
    await registry.updateJob(id, 'completed', inventory);
    return (await registry.job(id)).result;
  }
  await refresh();
  return { repo, registry, repository, refresh };
}

test('baseline docs have all section types, graph evidence, and searchable chunks', async t => {
  const { registry, repository } = await fixture(t);
  const result = await registry.syncDocs(repository.id, 'baseline', noModel, unavailable);
  const sections = (await registry.docSections(repository.id)).sections;
  assert.deepEqual(new Set(sections.map(item => item.kind)), new Set([
    'system_overview', 'module_purpose', 'api_flow', 'data_flow', 'setup', 'symbol_summary',
  ]));
  assert.equal(result.updated.length, sections.length);
  assert.equal(result.overview.generationReason, 'model_unavailable');
  assert.equal(result.overview.sourceCommit, (await registry.status(repository.id)).index.commitHash);
  assert.ok(result.overview.evidence.some(item => item.path === 'api/login.ts'));
  const setup = await registry.docSection(repository.id, sections.find(item => item.kind === 'setup').sectionId);
  assert.match(setup.contentMarkdown, /Install dependencies before running tests/);
  const graph = await registry.withDb(db => all(db,
    "SELECT type FROM graph_edges WHERE repo_id = ? AND provenance = 'documentation'", [repository.id]));
  assert.ok(['DOCUMENTS', 'GENERATED_FROM', 'DOCUMENTED_BY'].every(type => graph.some(row => row.type === type)));
  const found = await searchCode(registry, unavailable, { repoId: repository.id, query: 'Project overview' });
  assert.ok(found.results.some(item => item.docKind === 'system_overview' && item.docSectionId === result.overview.sectionId));
  const unchanged = await registry.syncDocs(repository.id, 'incremental', noModel, unavailable);
  assert.deepEqual(unchanged.updated, []);
});

test('route changes update linked docs and deleted routes lose live evidence', async t => {
  const { repo, registry, repository, refresh } = await fixture(t);
  await registry.syncDocs(repository.id, 'baseline', noModel, unavailable);
  const before = (await registry.docSections(repository.id)).sections;
  const originalModule = before.find(item => item.kind === 'module_purpose' && item.title === 'Module: common');
  const originalRoute = before.find(item => item.kind === 'api_flow');
  fs.writeFileSync(path.join(repo, 'api', 'login.ts'), "import { authenticate } from '../core/auth';\n\nrouter.post('/api/sign-in', login);\n\nexport function login(req: Request) {\n  return authenticate(req.user);\n}\n");
  const job = await refresh();
  assert.ok(job.docs.staleCount > 0);
  const stale = await registry.staleDocs(repository.id);
  assert.ok(stale.sections.some(item => item.sectionId === originalRoute.sectionId));
  const result = await registry.syncDocs(repository.id, 'incremental', noModel, unavailable);
  assert.ok(result.updated.length > 0);
  assert.ok(!result.updated.includes(originalModule.sectionId));
  assert.equal((await registry.docSection(repository.id, originalRoute.sectionId)).freshness, 'stale');
  assert.deepEqual((await registry.docSection(repository.id, originalRoute.sectionId)).evidence, []);
  assert.ok((await registry.docSections(repository.id)).sections.some(item => item.kind === 'api_flow' && item.title.includes('sign-in')));
  const module = await registry.docSection(repository.id, originalModule.sectionId);
  assert.ok(module.evidence.length > 0);
  assert.equal(module.freshness, 'current');
  const apiModule = before.find(item => item.kind === 'module_purpose' && item.title === 'Module: api');
  assert.ok((await registry.docSection(repository.id, apiModule.sectionId)).evidence.length > 0);
});

test('user-authored content is preserved with a separate proposed replacement', async t => {
  const { repo, registry, repository, refresh } = await fixture(t);
  await registry.syncDocs(repository.id, 'baseline', noModel, unavailable);
  const target = (await registry.docSections(repository.id)).sections.find(item =>
    item.kind === 'symbol_summary' && item.title === 'Symbols: auth.ts');
  await registry.withDb(db => db.run("UPDATE doc_sections SET generated = 0, content_markdown = '# My notes' WHERE section_id = ?", [target.sectionId]), true);
  fs.appendFileSync(path.join(repo, 'core', 'auth.ts'), '\nexport function authorize() { return true; }\n');
  await refresh();
  const result = await registry.syncDocs(repository.id, 'incremental', noModel, unavailable);
  assert.ok(result.affected.includes(target.sectionId));
  const section = await registry.docSection(repository.id, target.sectionId);
  assert.equal(section.contentMarkdown, '# My notes');
  assert.ok(section.proposedMarkdown.includes('authorize'));
  assert.equal(section.generated, false);
});

test('repository deletion cascades docs, links, nodes, and semantic chunks', async t => {
  const { registry, repository } = await fixture(t);
  await registry.syncDocs(repository.id, 'baseline', noModel, unavailable);
  await registry.remove(repository.id);
  const remaining = await registry.withDb(db => ({
    sections: all(db, 'SELECT * FROM doc_sections WHERE repo_id = ?', [repository.id]),
    links: all(db, 'SELECT * FROM doc_links'),
    nodes: all(db, "SELECT * FROM graph_nodes WHERE repo_id = ? AND type = 'doc_section'", [repository.id]),
    chunks: all(db, 'SELECT * FROM semantic_chunks WHERE repo_id = ?', [repository.id]),
  }));
  assert.deepEqual(remaining, { sections: [], links: [], nodes: [], chunks: [] });
});

test('opening a Phase 3 database adds docs without losing graph or semantic data', async t => {
  const { registry, repository } = await fixture(t);
  const before = await registry.status(repository.id);
  await registry.withDb(db => db.run('DROP TABLE doc_links; DROP TABLE doc_sections; PRAGMA user_version = 4;'), true);
  const reopened = await Registry.open(registry.dataDir);
  const after = await reopened.status(repository.id);
  assert.equal(after.index.revision, before.index.revision);
  assert.equal(after.graph.nodeCount, before.graph.nodeCount);
  assert.equal(after.semantic.chunkCount, before.semantic.chunkCount);
  assert.deepEqual(after.docs, { sectionCount: 0, currentCount: 0, staleCount: 0,
    affectedCount: 0, generatedCount: 0 });
});

test('text generation provider stays on loopback and rejects missing local models', async () => {
  assert.throws(() => new ModelProvider({ url: 'https://example.com' }), /loopback/);
  assert.throws(() => new ModelProvider({ model: 'remote-cloud' }), /local text model/);
});

test('local model adapter sends bounded facts to Ollama generation', async t => {
  const server = http.createServer(async (request, response) => {
    response.setHeader('content-type', 'application/json');
    if (request.url === '/api/tags') {
      response.end(JSON.stringify({ models: [{ name: 'llama3.2:latest', digest: 'local' }] }));
      return;
    }
    assert.equal(request.url, '/api/generate');
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks));
    assert.equal(body.stream, false);
    assert.match(body.prompt, /Indexed fact/);
    response.end(JSON.stringify({ response: '# Generated\nIndexed fact.' }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const provider = new ModelProvider({ url: `http://127.0.0.1:${server.address().port}` });
  await provider.available();
  assert.match(await provider.generate('Overview', 'Indexed fact.'), /Generated/);
});

test('documentation jobs exclude concurrent index refreshes', async t => {
  const { registry, repository } = await fixture(t);
  let entered;
  let release;
  const started = new Promise(resolve => { entered = resolve; });
  const blocked = new Promise(resolve => { release = resolve; });
  const model = { async available() {}, async generate() { entered(); await blocked; return '# Verified'; } };
  const job = registry.syncDocs(repository.id, 'baseline', model, unavailable);
  await started;
  await assert.rejects(registry.createJob(repository.id), /already running/);
  release();
  const result = await job;
  assert.ok(result.updated.length);
  assert.equal((await registry.job(result.jobId)).state, 'completed');
});

test('onboarding evaluation questions resolve to graph-backed documentation', async t => {
  const { registry, repository } = await fixture(t);
  await registry.syncDocs(repository.id, 'baseline', noModel, unavailable);
  const questions = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'evals', 'phase4-questions.json'), 'utf8'));
  const listing = (await registry.docSections(repository.id)).sections;
  for (const question of questions) {
    const candidates = listing.filter(item => item.kind === question.kind);
    assert.ok(candidates.length, question.question);
    const sections = await Promise.all(candidates.map(item => registry.docSection(repository.id, item.sectionId)));
    assert.ok(sections.some(item => item.evidence.some(evidence => evidence.path === question.sourcePath)), question.question);
    assert.ok(sections.every(item => item.contentMarkdown.includes('#') && item.generationReason === 'model_unavailable'));
  }
});
