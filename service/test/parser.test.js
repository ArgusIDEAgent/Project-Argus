'use strict';

const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { parseCode } = require('../src/codeParser');

const fixtureRoot = path.join(__dirname, 'fixtures', 'phase2');
async function parse(filePath, language) {
  const source = fs.readFileSync(path.join(fixtureRoot, filePath), 'utf8');
  return parseCode({ repoId: 'a'.repeat(24), filePath, language, source,
    contentHash: createHash('sha256').update(source).digest('hex'), revision: 'fixture' });
}

test('TSX golden file extracts a component and HTTP client with exact lines', async () => {
  const facts = await parse('web/LoginPage.tsx', 'typescript');
  assert.equal(facts.parseErrors, false);
  assert.deepEqual(facts.symbols.map(({ kind, name, signature, startLine, endLine }) =>
    ({ kind, name, signature, startLine, endLine })),
  [{ kind: 'component', name: 'LoginPage', signature: 'function LoginPage()', startLine: 1, endLine: 4 }]);
  assert.deepEqual(facts.clients.map(({ method, route, line }) => ({ method, route, line })),
    [{ method: 'POST', route: '/api/login', line: 2 }]);
});

test('TypeScript golden file extracts imports, route, and lexical calls', async () => {
  const facts = await parse('api/login.ts', 'typescript');
  assert.equal(facts.parseErrors, false);
  assert.deepEqual(facts.imports[0].bindings, [{ imported: 'authenticate', local: 'authenticate' }]);
  assert.deepEqual(facts.routes.map(({ method, route, handlerName }) => ({ method, route, handlerName })),
    [{ method: 'POST', route: '/api/login', handlerName: 'login' }]);
  assert.equal(facts.calls[0].name, 'authenticate');
});

test('JavaScript golden file identifies an ML entrypoint', async () => {
  const source = fs.readFileSync(path.join(__dirname, 'fixtures', 'javascript-golden.js'), 'utf8');
  const facts = await parseCode({ repoId: 'a'.repeat(24), filePath: 'javascript-golden.js',
    language: 'javascript', source, contentHash: createHash('sha256').update(source).digest('hex'), revision: 'fixture' });
  assert.equal(facts.parseErrors, false);
  assert.deepEqual(facts.symbols.map(({ name, kind, signature }) => ({ name, kind, signature })),
    [{ name: 'predict', kind: 'ml_entry', signature: 'function predict(input)' }]);
  assert.equal(facts.mlEntries.length, 1);
  assert.deepEqual(facts.accesses.map(({ type, entityName }) => ({ type, entityName })),
    [{ type: 'USES_MODEL', entityName: 'model' }]);
});

test('Python golden files extract ORM entity, test, and model access', async () => {
  const model = await parse('data/models.py', 'python');
  const spec = await parse('tests/test_models.py', 'python');
  assert.equal(model.parseErrors, false);
  assert.equal(spec.parseErrors, false);
  assert.equal(model.symbols[0].kind, 'class');
  assert.equal(model.symbols[0].docstring, '"""Stored account."""');
  assert.deepEqual(model.entities.map(entity => entity.name), ['User']);
  assert.equal(spec.symbols[0].kind, 'test');
  assert.deepEqual(spec.accesses.map(access => [access.entityName, access.type]), [['User', 'READS_FROM']]);
});

test('Python route decorator creates one endpoint fact', async () => {
  const source = '@app.post("/items")\ndef create_item():\n    return 1\n';
  const facts = await parseCode({ repoId: 'a'.repeat(24), filePath: 'api.py', language: 'python',
    source, contentHash: createHash('sha256').update(source).digest('hex'), revision: 'fixture' });
  assert.deepEqual(facts.routes.map(({ method, route, handlerName }) => ({ method, route, handlerName })),
    [{ method: 'POST', route: '/items', handlerName: 'create_item' }]);
});
