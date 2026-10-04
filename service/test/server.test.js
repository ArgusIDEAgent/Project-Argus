'use strict';

const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { after, before, test } = require('node:test');
const { GitService } = require('../src/gitService');

const token = randomBytes(32).toString('hex');
let child;
let port;
let testRoot;
let serverErrors = '';
const headers = { 'content-type': 'application/json', 'x-codemind-session': token };

async function request(method, route, body) {
  const response = await fetch(`http://127.0.0.1:${port}${route}`, {
    method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return { status: response.status, body: await response.json() };
}

async function runJob(repoId, kind = 'refresh') {
  const queued = await request('POST', `/repos/${repoId}/${kind}`);
  assert.equal(queued.status, 202, JSON.stringify(queued.body));
  for (let attempt = 0; attempt < 100; attempt++) {
    const job = await request('GET', `/jobs/${queued.body.jobId}`);
    if (job.body.state === 'completed' || job.body.state === 'failed') {
      assert.equal(job.body.state, 'completed', `${job.body.error || ''} ${serverErrors}`);
      return job.body.result;
    }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.fail('Index job timed out.');
}

before(async () => {
  testRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'codemind-service-test-'));
  child = spawn(process.execPath, [path.join(__dirname, '..', 'src', 'server.js')], {
    env: { ...process.env, CODEMIND_SESSION_TOKEN: token, CODEMIND_DATA_DIR: path.join(testRoot, 'state'),
      CODEMIND_EMBEDDING_URL: 'http://127.0.0.1:1', CODEMIND_MODEL_URL: 'http://127.0.0.1:1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stderr.on('data', chunk => { serverErrors += chunk.toString(); });
  port = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Server did not start.')), 5000);
    let output = '';
    child.once('error', reject);
    child.once('exit', (code) => reject(new Error(`Server exited with ${code}.`)));
    child.stdout.on('data', (chunk) => {
      output += chunk.toString();
      const newline = output.indexOf('\n');
      if (newline < 0) return;
      clearTimeout(timeout);
      resolve(JSON.parse(output.slice(0, newline)).port);
    });
  });
});

after(() => {
  child?.kill();
  fs.rmSync(testRoot, { recursive: true, force: true });
});

test('requires a session token', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/health`);
  assert.equal(response.status, 401);
});

test('Phase 3 HTTP APIs search, check reuse, record feedback, and queue refresh', async () => {
  const repo = path.join(testRoot, 'phase3 repo');
  fs.cpSync(path.join(__dirname, 'fixtures', 'phase3'), repo, { recursive: true });
  execFileSync('git', ['init', '-q', repo]);
  const registration = await request('POST', '/repos/register', { rootPath: repo });
  const repoId = registration.body.repository.id;
  await runJob(repoId);
  const result = await request('POST', '/search/code', { repoId, query: 'sum' });
  assert.equal(result.status, 200);
  assert.equal(result.body.mode, 'lexical');
  assert.equal(result.body.results[0].name, 'sum');
  const reuse = await request('POST', '/analysis/reuse', { repoId, entityId: result.body.results[0].entityId });
  assert.equal(reuse.status, 200);
  assert.equal(reuse.body.classification, 'REUSE');
  const entityId = reuse.body.results[0].entityId;
  assert.equal((await request('POST', '/analysis/reuse/feedback', { repoId, entityId, queryHash: reuse.body.queryHash, decision: 'ignore' })).status, 200);
  assert.equal((await request('POST', '/search/code', { repoId, query: '', limit: 0 })).status, 400);
  assert.equal((await request('POST', '/analysis/reuse/calibrate', { repoId, examples: [] })).status, 400);
  const unauthenticated = await fetch(`http://127.0.0.1:${port}/search/code`, { method: 'POST', body: '{}' });
  assert.equal(unauthenticated.status, 401);
  assert.equal((await request('POST', '/embeddings/refresh', { repoId })).status, 202);
  await runJob(repoId);
});

test('Phase 4 HTTP APIs sync graph-linked docs and expose freshness', async () => {
  const repo = path.join(testRoot, 'phase4 repo');
  fs.cpSync(path.join(__dirname, 'fixtures', 'phase2'), repo, { recursive: true });
  execFileSync('git', ['init', '-q', repo]);
  const registration = await request('POST', '/repos/register', { rootPath: repo });
  const repoId = registration.body.repository.id;
  await runJob(repoId);
  assert.equal((await request('GET', `/docs/overview?repoId=${repoId}`)).status, 404);
  const synced = await request('POST', '/docs/sync', { repoId, mode: 'baseline' });
  assert.equal(synced.status, 200, JSON.stringify(synced.body));
  assert.ok(synced.body.updated.length > 0);
  assert.equal(synced.body.overview.generationReason, 'model_unavailable');
  const overview = await request('GET', `/docs/overview?repoId=${repoId}`);
  assert.equal(overview.status, 200);
  assert.ok(overview.body.evidence.length > 0);
  const section = await request('GET', `/docs/section/${overview.body.sectionId}?repoId=${repoId}`);
  assert.equal(section.status, 200);
  const list = await request('GET', `/docs/sections?repoId=${repoId}`);
  assert.ok(list.body.sections.some(item => item.kind === 'api_flow'));
  const stale = await request('GET', `/docs/stale?repoId=${repoId}`);
  assert.equal(stale.status, 200);
  assert.equal(stale.body.status.sectionCount, list.body.sections.length);
  assert.equal((await request('GET', '/docs/stale?repoId=bad')).status, 400);
  assert.equal((await request('POST', '/docs/sync', { repoId, mode: 'invalid' })).status, 400);
});

test('reports health with the session token', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/health`, {
    headers: { 'x-codemind-session': token },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { status: 'ok', service: 'codemind', version: 1, schemaVersion: 5 });
});

test('replies to a hello message', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/hello`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-codemind-session': token },
    body: JSON.stringify({ message: 'Hello' }),
  });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(typeof body.reply, 'string');
  assert.ok(body.reply.length > 0);
});

test('rejects an empty message', async () => {
  const response = await fetch(`http://127.0.0.1:${port}/hello`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-codemind-session': token },
    body: JSON.stringify({ message: '  ' }),
  });
  assert.equal(response.status, 400);
});

test('registers and scans a different repository without modifying it', async () => {
  const repo = path.join(testRoot, 'other repo');
  fs.mkdirSync(path.join(repo, 'src'), { recursive: true });
  fs.mkdirSync(path.join(repo, 'node_modules'), { recursive: true });
  fs.mkdirSync(path.join(repo, 'secrets'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'src', 'app.ts'), 'export const value = 1;\n');
  fs.writeFileSync(path.join(repo, 'root.js'), 'module.exports = 1;\n');
  fs.writeFileSync(path.join(repo, 'node_modules', 'ignored.js'), 'module.exports = 1;\n');
  fs.writeFileSync(path.join(repo, '.env'), 'secret=example\n');
  fs.writeFileSync(path.join(repo, 'secrets', 'creds.py'), 'password = "example"\n');
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'test@example.com']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'CodeMind Test']);
  execFileSync('git', ['-C', repo, 'add', '--', '.']);
  execFileSync('git', ['-C', repo, 'commit', '-m', 'fixture']);

  const headers = { 'content-type': 'application/json', 'x-codemind-session': token };
  const registered = await fetch(`http://127.0.0.1:${port}/repos/register`, {
    method: 'POST', headers, body: JSON.stringify({ rootPath: repo }),
  });
  assert.equal(registered.status, 200);
  const { repository } = await registered.json();
  assert.equal(repository.rootPath, fs.realpathSync(repo));
  assert.match(repository.id, /^[a-f0-9]{24}$/);

  const queued = await fetch(`http://127.0.0.1:${port}/repos/${repository.id}/index`, { method: 'POST', headers });
  const queuedBody = await queued.json();
  assert.equal(queued.status, 202, `${JSON.stringify(queuedBody)} ${serverErrors}`);
  const { jobId } = queuedBody;
  let job;
  for (let attempt = 0; attempt < 50; attempt++) {
    const response = await fetch(`http://127.0.0.1:${port}/jobs/${jobId}`, { headers });
    job = await response.json();
    if (job.state === 'completed' || job.state === 'failed') break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(job.state, 'completed', job.error);
  assert.equal(job.result.fileCount, 2);
  assert.match(job.result.revision, /^[a-f0-9]{40,64}:clean$/);
  assert.equal(job.result.files, undefined);

  const statusResponse = await fetch(`http://127.0.0.1:${port}/repos/${repository.id}/status`, { headers });
  assert.equal(statusResponse.status, 200);
  const status = await statusResponse.json();
  assert.equal(status.index.fileCount, 2);
  const inventory = await (await fetch(`http://127.0.0.1:${port}/repos/${repository.id}/files`, { headers })).json();
  assert.deepEqual(inventory.files.map(file => file.path), ['root.js', 'src/app.ts']);
  assert.equal(fs.existsSync(path.join(testRoot, 'state', 'metadata.sqlite')), true);
  assert.equal(fs.existsSync(path.join(testRoot, 'state', 'worktrees')), true);
  assert.equal(execFileSync('git', ['-C', repo, 'status', '--porcelain']).toString(), '');

  const scopedRegistration = await fetch(`http://127.0.0.1:${port}/repos/register`, {
    method: 'POST', headers, body: JSON.stringify({ rootPath: path.join(repo, 'src') }),
  });
  assert.equal(scopedRegistration.status, 200);
  const { repository: scoped } = await scopedRegistration.json();
  assert.equal(scoped.rootPath, fs.realpathSync(path.join(repo, 'src')));
  assert.notEqual(scoped.id, repository.id);
  const scopedJobResponse = await fetch(`http://127.0.0.1:${port}/repos/${scoped.id}/index`, { method: 'POST', headers });
  const { jobId: scopedJobId } = await scopedJobResponse.json();
  let scopedJob;
  for (let attempt = 0; attempt < 50; attempt++) {
    scopedJob = await (await fetch(`http://127.0.0.1:${port}/jobs/${scopedJobId}`, { headers })).json();
    if (scopedJob.state === 'completed' || scopedJob.state === 'failed') break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(scopedJob.state, 'completed', scopedJob.error);
  assert.equal(scopedJob.result.fileCount, 1);

  const removed = await fetch(`http://127.0.0.1:${port}/repos/${repository.id}`, { method: 'DELETE', headers });
  assert.equal(removed.status, 200);
  assert.equal((await fetch(`http://127.0.0.1:${port}/repos/${scoped.id}`, { method: 'DELETE', headers })).status, 200);
  assert.equal((await fetch(`http://127.0.0.1:${port}/repos/${repository.id}/status`, { headers })).status, 404);
  assert.equal(execFileSync('git', ['-C', repo, 'status', '--porcelain']).toString(), '');
});

test('tracks dirty edits, renames, branch switches, and exact diff hunks', async () => {
  const repo = path.join(testRoot, 'phase1 repo');
  fs.mkdirSync(path.join(repo, 'src'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'src', 'a.js'), 'const a = 1;\n');
  fs.writeFileSync(path.join(repo, 'src', 'b.py'), 'b = 1\n');
  fs.writeFileSync(path.join(repo, '.env'), 'SECRET=x\n');
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'test@example.com']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'CodeMind Test']);
  execFileSync('git', ['-C', repo, 'add', '.']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'initial']);
  const initialHash = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD']).toString().trim();
  const baseBranch = execFileSync('git', ['-C', repo, 'branch', '--show-current']).toString().trim();
  const registered = await request('POST', '/repos/register', { rootPath: repo });
  assert.equal(registered.status, 200);
  const id = registered.body.repository.id;
  const initial = await runJob(id, 'index');
  assert.equal(initial.fileCount, 2);
  assert.equal(initial.scannedCount, 2);
  assert.equal(initial.commitHash, initialHash);
  assert.equal(initial.dirtyFingerprint, 'clean');
  const inventory = await request('GET', `/repos/${id}/files`);
  assert.deepEqual(inventory.body.files.map(file => file.path), ['src/a.js', 'src/b.py']);
  assert.match(inventory.body.files[0].contentHash, /^[a-f0-9]{64}$/);
  assert.equal(inventory.body.files[0].lastModifiedCommit, initialHash);
  const allFiles = await request('GET', `/repos/${id}/files?includeExcluded=true`);
  assert.equal(allFiles.body.files.find(file => file.path === '.env').excludedReason, 'excluded_path');
  assert.equal(allFiles.body.files.find(file => file.path === '.env').contentHash, null);
  const clean = await runJob(id);
  assert.equal(clean.scannedCount, 0);
  assert.equal(clean.changedCount, 0);

  fs.writeFileSync(path.join(repo, 'src', 'a.js'), 'const a = 2;\n');
  fs.writeFileSync(path.join(repo, 'src', 'new.js'), 'export const n = 3;\n');
  const beforeRefresh = await request('GET', `/repos/${id}/changes?from=${initialHash}`);
  assert.equal(beforeRefresh.status, 200);
  assert.deepEqual(beforeRefresh.body.changes.map(change => change.path), ['src/a.js', 'src/new.js']);
  assert.deepEqual(beforeRefresh.body.changes.find(change => change.path === 'src/a.js').hunks,
    [{ oldStart: 1, oldLines: 1, newStart: 1, newLines: 1 }]);
  assert.deepEqual(beforeRefresh.body.changes.find(change => change.path === 'src/new.js').hunks,
    [{ oldStart: 0, oldLines: 0, newStart: 1, newLines: 1 }]);
  const dirty = await runJob(id);
  assert.equal(dirty.scannedCount, 2);
  assert.deepEqual(dirty.changes.map(change => change.status), ['modified', 'added']);
  assert.match(dirty.dirtyFingerprint, /^[a-f0-9]{64}$/);
  assert.equal((await runJob(id)).scannedCount, 0);
  execFileSync('git', ['-C', repo, 'add', 'src/a.js', 'src/new.js']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'add code']);
  const committed = await runJob(id);
  assert.equal(committed.dirtyFingerprint, 'clean');
  assert.equal(committed.changedCount, 0);

  execFileSync('git', ['-C', repo, 'mv', 'src/b.py', 'src/renamed.py']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'rename module']);
  const renamed = await runJob(id);
  assert.deepEqual(renamed.changes.map(change => [change.status, change.oldPath, change.path]),
    [['renamed', 'src/b.py', 'src/renamed.py']]);
  assert.ok(!((await request('GET', `/repos/${id}/files`)).body.files.some(file => file.path === 'src/b.py')));

  const base = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD']).toString().trim();
  execFileSync('git', ['-C', repo, 'checkout', '-qb', 'feature']);
  fs.writeFileSync(path.join(repo, 'src', 'feature.ts'), 'export const feature = true;\n');
  execFileSync('git', ['-C', repo, 'add', 'src/feature.ts']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'feature']);
  const feature = execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD']).toString().trim();
  const onFeature = await runJob(id);
  assert.equal(onFeature.branch, 'feature');
  assert.equal(onFeature.fileCount, 4);
  const comparison = await request('GET', `/repos/${id}/changes?from=${base}&to=${feature}`);
  assert.deepEqual(comparison.body.changes.map(change => change.path), ['src/feature.ts']);
  assert.equal(comparison.body.changes[0].hunks[0].newLines, 1);
  const git = new GitService(repo);
  assert.equal(await git.mergeBase(base, feature), base);
  assert.ok((await git.branches()).some(item => item.name === 'feature'));
  assert.equal((await git.log())[0].hash, feature);
  assert.match(await git.show(feature, 'src/feature.ts'), /feature = true/);
  assert.match(await git.blame(feature, 'src/feature.ts'), /author CodeMind Test/);
  assert.ok((await git.worktrees()).some(item => item.worktree === fs.realpathSync(repo)));
  assert.deepEqual((await git.changedFiles(base, feature)).map(change => change.path), ['src/feature.ts']);
  assert.ok((await git.commitFiles()).some(([hash, paths]) => hash === feature && paths.includes('src/feature.ts')));
  execFileSync('git', ['-C', repo, 'checkout', '-q', baseBranch]);
  const switched = await runJob(id);
  assert.equal(switched.branch, baseBranch);
  assert.equal(switched.fileCount, 3);
  assert.deepEqual(switched.changes.map(change => change.status), ['deleted']);
  const history = await request('GET', `/repos/${id}/history?path=src%2Fa.js`);
  assert.equal(history.status, 200);
  assert.equal(history.body.commits[0].subject, 'add code');
  assert.equal((await request('GET', `/repos/${id}/history?path=..%2Foutside`)).status, 400);
  assert.equal((await request('GET', `/repos/${id}/changes?from=--bad`)).status, 400);
});

test('reports staged source files in a repository without commits', async () => {
  const repo = path.join(testRoot, 'unborn repo');
  fs.mkdirSync(repo);
  fs.writeFileSync(path.join(repo, 'start.js'), 'export const started = true;\n');
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'add', 'start.js']);
  const registered = await request('POST', '/repos/register', { rootPath: repo });
  const id = registered.body.repository.id;
  const changes = await request('GET', `/repos/${id}/changes`);
  assert.equal(changes.status, 200);
  assert.deepEqual(changes.body.changes.map(change => change.path), ['start.js']);
  assert.equal(changes.body.changes[0].hunks[0].newLines, 1);
  const indexed = await runJob(id, 'index');
  assert.equal(indexed.commitHash, 'unborn');
  assert.equal(indexed.fileCount, 1);
});

test('large inventory refresh reads only changed source files', async () => {
  const repo = path.join(testRoot, 'large repo');
  fs.mkdirSync(repo);
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'test@example.com']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'CodeMind Test']);
  for (let index = 0; index < 200; index++) fs.writeFileSync(path.join(repo, `file${index}.js`), `export const n = ${index};\n`);
  execFileSync('git', ['-C', repo, 'add', '.']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'large fixture']);
  const registered = await request('POST', '/repos/register', { rootPath: repo });
  const id = registered.body.repository.id;
  assert.equal((await runJob(id, 'index')).scannedCount, 200);
  fs.writeFileSync(path.join(repo, 'file17.js'), 'export const n = 201;\n');
  const refreshed = await runJob(id);
  assert.equal(refreshed.scannedCount, 1);
  assert.equal(refreshed.changedCount, 1);
  assert.equal((await runJob(id)).scannedCount, 0);
});

test('builds and incrementally repairs a cross-layer code graph', async () => {
  const repo = path.join(testRoot, 'phase2 repo');
  fs.cpSync(path.join(__dirname, 'fixtures', 'phase2'), repo, { recursive: true });
  fs.writeFileSync(path.join(repo, 'core', 'unrelated.ts'), 'export function authenticate() { return false; }\n');
  execFileSync('git', ['init', '-q', repo]);
  execFileSync('git', ['-C', repo, 'config', 'user.email', 'test@example.com']);
  execFileSync('git', ['-C', repo, 'config', 'user.name', 'CodeMind Test']);
  execFileSync('git', ['-C', repo, 'add', '.']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'graph fixture']);
  const registered = await request('POST', '/repos/register', { rootPath: repo });
  const id = registered.body.repository.id;
  const indexed = await runJob(id, 'index');
  assert.equal(indexed.graph.parsedCount, 10);
  assert.equal(indexed.graph.parseErrors, 0);
  const indexedStatus = await request('GET', `/repos/${id}/status`);
  assert.equal(indexedStatus.body.graph.revision, indexedStatus.body.index.revision);
  const graphFile = async filePath => request('GET', `/graph/file/${encodeURIComponent(filePath)}?repoId=${id}`);
  const web = await graphFile('web/LoginPage.tsx');
  const api = await graphFile('api/login.ts');
  const core = await graphFile('core/auth.ts');
  const model = await graphFile('data/models.py');
  const spec = await graphFile('tests/test_models.py');
  const jsTest = await graphFile('tests/auth.test.ts');
  for (const item of [web, api, core, model, spec, jsTest]) assert.equal(item.status, 200);
  assert.ok(spec.body.edges.some(edge => edge.type === 'IMPORTS' && edge.toId ===
    model.body.nodes.find(node => node.type === 'file').id));
  const common = await graphFile('common/consumer.js');
  const provider = await graphFile('common/provider.js');
  assert.ok(common.body.edges.some(edge => edge.type === 'CALLS' && edge.toId ===
    provider.body.nodes.find(node => node.name === 'ping').id));
  const named = (result, name) => result.body.nodes.find(node => node.name === name);
  const component = named(web, 'LoginPage');
  const endpoint = named(api, 'POST /api/login');
  const handler = named(api, 'login');
  const service = named(core, 'authenticate');
  const entity = named(model, 'User');
  const testSymbol = named(spec, 'test_user_query');
  assert.ok([component, endpoint, handler, service, entity, testSymbol].every(Boolean));
  const dependency = await request('POST', '/graph/path', { fromId: component.id, toId: testSymbol.id });
  assert.equal(dependency.status, 200);
  assert.deepEqual(dependency.body.nodes.map(node => node.name),
    ['LoginPage', 'POST /api/login', 'login', 'authenticate', 'findUser', 'User', 'test_user_query']);
  assert.deepEqual(dependency.body.edges.map(edge => edge.type),
    ['CONSUMES', 'EXPOSES', 'CALLS', 'CALLS', 'READS_FROM', 'READS_FROM']);
  const detail = await request('GET', `/graph/symbol/${service.id}`);
  assert.equal(detail.status, 200);
  assert.ok(detail.body.incoming.some(edge => edge.type === 'CALLS' && edge.fromId === handler.id));
  assert.ok(detail.body.incoming.some(edge => edge.type === 'TESTS' && edge.fromId === named(jsTest, 'auth works').id));
  assert.deepEqual(detail.body.queries.callers.map(node => node.name), ['login', 'auth works']);
  assert.deepEqual(detail.body.queries.tests.map(node => node.name), ['auth works']);
  const unrelated = await graphFile('core/unrelated.ts');
  assert.ok(!detail.body.incoming.some(edge => edge.fromId === named(unrelated, 'authenticate').id));
  const impact = await request('GET', `/graph/impact/${entity.id}`);
  assert.equal(impact.status, 200);
  assert.ok(impact.body.nodes.some(node => node.name === 'LoginPage'));
  assert.ok(impact.body.nodes.some(node => node.name === 'test_user_query'));
  assert.ok(impact.body.nodes.length < 30);
  assert.equal((await runJob(id)).graph.parsedCount, 0);

  fs.writeFileSync(path.join(repo, 'core', 'cycle.ts'),
    'export function a() { return b(); }\nexport function b() { return a(); }\n');
  assert.equal((await runJob(id)).graph.parsedCount, 1);
  const cycle = await graphFile('core/cycle.ts');
  const a = named(cycle, 'a');
  const b = named(cycle, 'b');
  const cycleImpact = await request('GET', `/graph/impact/${a.id}?depth=12`);
  assert.deepEqual(cycleImpact.body.nodes.map(node => node.name), ['a', 'b']);
  assert.equal((await request('POST', '/graph/path', { fromId: a.id, toId: b.id, maxDepth: 12 })).body.depth, 1);

  execFileSync('git', ['-C', repo, 'mv', 'data/users.ts', 'data/accountUsers.ts']);
  fs.writeFileSync(path.join(repo, 'core', 'auth.ts'),
    fs.readFileSync(path.join(repo, 'core', 'auth.ts'), 'utf8').replace('../data/users', '../data/accountUsers'));
  execFileSync('git', ['-C', repo, 'add', 'core/auth.ts']);
  execFileSync('git', ['-C', repo, 'commit', '-qm', 'rename data module']);
  const renamed = await runJob(id);
  assert.equal(renamed.graph.parsedCount, 2);
  assert.equal((await graphFile('data/users.ts')).status, 404);
  const renamedPath = await request('POST', '/graph/path', { fromId: component.id, toId: testSymbol.id });
  assert.equal(renamedPath.body.depth, 6);

  fs.writeFileSync(path.join(repo, 'data', 'accountUsers.ts'), 'export const disabled = true;\n');
  const deleted = await runJob(id);
  assert.equal(deleted.graph.parsedCount, 1);
  const lostPath = await request('POST', '/graph/path', { fromId: component.id, toId: entity.id });
  assert.equal(lostPath.body.depth, null);
  assert.equal((await request('GET', `/graph/symbol/${renamedPath.body.nodes[4].id}`)).status, 404);
  assert.equal((await request('GET', `/graph/impact/${entity.id}?depth=99`)).status, 400);
  assert.equal((await request('GET', `/graph/file/..%2Foutside?repoId=${id}`)).status, 400);
  assert.equal((await request('POST', '/graph/path', { fromId: component.id, toId: entity.id, maxDepth: 99 })).status, 400);
});
