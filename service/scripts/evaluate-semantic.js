'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { Registry } = require('../src/registry');
const { prepareGraph } = require('../src/graphBuilder');
const { prepareSemantic } = require('../src/semanticStore');
const { EmbeddingProvider } = require('../src/embeddingProvider');
const { searchCode } = require('../src/retrievalService');
const { analyzeReuse } = require('../src/reuseAnalyzer');
const { calibrate } = require('../src/semanticCalibration');
const { all } = require('../src/graphStore');

async function main() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'cm-live-eval-'));
  try {
    const provider = new EmbeddingProvider();
    await provider.identity();
    const repo = process.argv[2] ? fs.realpathSync(process.argv[2]) : path.join(temporary, 'repo');
    if (!process.argv[2]) {
      fs.cpSync(path.join(__dirname, '..', 'test', 'fixtures', 'phase3'), repo, { recursive: true });
      execFileSync('git', ['init', repo], { stdio: 'pipe' });
    }
    const registry = await Registry.open(path.join(temporary, 'state'));
    const repository = await registry.register(repo);
    const started = Date.now();
    const inventory = await registry.scan(repository.id);
    inventory.graph = await prepareGraph(registry, repository.id, inventory);
    inventory.semantic = await prepareSemantic(registry, repository.id, inventory, provider);
    const job = await registry.createJob(repository.id);
    await registry.updateJob(job, 'completed', inventory);
    const status = (await registry.status(repository.id)).semantic;
    assert.equal(status.state, 'ready', status.error);
    process.stdout.write(JSON.stringify({ stage: 'indexed', chunks: status.chunkCount, milliseconds: Date.now() - started, model: status.model }) + '\n');
    const rows = await registry.withDb(db => all(db, 'SELECT metadata_json FROM semantic_chunks WHERE repo_id = ?', [repository.id])
      .map(row => JSON.parse(row.metadata_json)));
    const cases = process.argv[2] ? [
      ['Find the function that extracts symbols from source code using tree sitter', 'parseCode', 'service/src/codeParser.js'],
      ['Find the method that registers a repository root and configuration', 'register', 'service/src/registry.js'],
      ['Find the function that finds a dependency path between graph nodes', 'graphPath', 'service/src/graphStore.js'],
      ['Find the method that retrieves files modified between git revisions', 'changedFiles', 'service/src/gitService.js'],
    ] : [
      ['trim whitespace and lowercase an email address', 'normalizeEmail', 'utilities.js'],
      ['limit a number between minimum and maximum bounds', 'clamp', 'utilities.js'],
      ['read malformed JSON without throwing and return a default value', 'parseJson', 'utilities.js'],
      ['calculate the sum of two numbers', 'add', 'utilities.js'],
    ];
    let hits = 0;
    const examples = [];
    for (const [index, [query, name, filePath]] of cases.entries()) {
      const target = rows.find(row => row.name === name && row.path === filePath);
      assert.ok(target, `${name} fixture missing`);
      const result = await searchCode(registry, provider, { repoId: repository.id, query, limit: 20 });
      assert.equal(result.mode, 'hybrid', result.warning);
      const rank = result.results.findIndex(row => row.entityId === target.entityId) + 1;
      if (rank > 0 && rank <= 5) hits++;
      process.stdout.write(JSON.stringify({ query, expected: name, rank, top: result.results.slice(0, 5).map(row => ({ name: row.name, similarity: row.scores.semantic })) }) + '\n');
      examples.push({ query, entityId: target.entityId, label: 'EXTEND' });
      const [, otherName, otherPath] = cases[(index + 2) % cases.length];
      const other = rows.find(row => row.name === otherName && row.path === otherPath);
      if (other) examples.push({ query, entityId: other.entityId, label: 'DISTINCT' });
    }
    const calibration = await calibrate(registry, provider, { repoId: repository.id, examples });
    process.stdout.write(JSON.stringify({ stage: 'calibration', ...calibration }) + '\n');
    if (!process.argv[2]) {
      const source = rows.find(row => row.name === 'sum');
      const reuse = await analyzeReuse(registry, provider, { repoId: repository.id, entityId: source.entityId });
      assert.ok(reuse.results.some(row => row.path === 'utilities.js' && row.name === 'add' && row.classification === 'REUSE'));
      assert.ok(!reuse.results.some(row => row.path === 'duplicates.js' && row.name === 'add' && row.classification === 'REUSE'));
    }
    process.stdout.write(JSON.stringify({ recallAt5: hits / cases.length, cases: cases.length, passed: hits === cases.length }) + '\n');
    assert.equal(hits, cases.length, 'Expected implementation missing from top five.');
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
}

main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
