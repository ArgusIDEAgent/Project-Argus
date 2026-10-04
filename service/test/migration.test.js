'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const initSqlJs = require('sql.js');
const { Registry } = require('../src/registry');

test('opens Phase 0 metadata and preserves repository and file records', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codemind-migration-'));
  try {
    const SQL = await initSqlJs({ locateFile: file => require.resolve(`sql.js/dist/${file}`) });
    const db = new SQL.Database();
    db.run(`PRAGMA foreign_keys = ON;
      CREATE TABLE repositories (id TEXT PRIMARY KEY, root_path TEXT UNIQUE NOT NULL,
        default_branch TEXT NOT NULL, created_at TEXT NOT NULL, config_json TEXT NOT NULL);
      CREATE TABLE repo_settings (repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
        config_json TEXT NOT NULL);
      CREATE TABLE jobs (id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
        state TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        result_json TEXT, error TEXT);
      CREATE TABLE index_versions (repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
        revision TEXT NOT NULL, indexed_at TEXT NOT NULL, file_count INTEGER NOT NULL, total_bytes INTEGER NOT NULL);
      CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id TEXT NOT NULL, job_id TEXT,
        event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, detail_json TEXT NOT NULL);
      CREATE TABLE files (repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
        path TEXT NOT NULL, language TEXT NOT NULL, size_bytes INTEGER NOT NULL, PRIMARY KEY(repo_id, path));
      PRAGMA user_version = 1;`);
    const id = 'a'.repeat(24);
    const config = JSON.stringify({ excludedPaths: [], secretPatterns: [], supportedLanguages: ['javascript'] });
    db.run('INSERT INTO repositories VALUES (?, ?, ?, ?, ?)', [id, '/tmp/old-repo', 'main', '2026-01-01', config]);
    db.run('INSERT INTO files VALUES (?, ?, ?, ?)', [id, 'index.js', 'javascript', 10]);
    db.run('INSERT INTO index_versions VALUES (?, ?, ?, ?, ?)', [id, 'old-commit', '2026-01-01', 1, 10]);
    fs.writeFileSync(path.join(dataDir, 'metadata.sqlite'), Buffer.from(db.export()));
    db.close();

    const registry = await Registry.open(dataDir);
    const status = await registry.status(id);
    assert.equal(status.repository.rootPath, '/tmp/old-repo');
    assert.equal(status.index.fileCount, 1);
    assert.equal((await registry.files(id))[0].path, 'index.js');
    const reopened = new SQL.Database(fs.readFileSync(path.join(dataDir, 'metadata.sqlite')));
    assert.equal(reopened.exec('PRAGMA user_version')[0].values[0][0], 5);
    reopened.close();
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('adds graph tables when opening a Phase 1 database', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codemind-phase1-migration-'));
  try {
    await Registry.open(dataDir);
    const SQL = await initSqlJs({ locateFile: file => require.resolve(`sql.js/dist/${file}`) });
    const db = new SQL.Database(fs.readFileSync(path.join(dataDir, 'metadata.sqlite')));
    db.run(`DROP TABLE graph_edges;
      DROP TABLE graph_nodes;
      DROP TABLE graph_files;
      DROP TABLE graph_versions;
      PRAGMA user_version = 2;`);
    fs.writeFileSync(path.join(dataDir, 'metadata.sqlite'), Buffer.from(db.export()));
    db.close();
    const registry = await Registry.open(dataDir);
    const tables = await registry.withDb(connection => connection.exec(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'graph_%' ORDER BY name"));
    assert.deepEqual(tables[0].values.map(row => row[0]),
      ['graph_edges', 'graph_files', 'graph_nodes', 'graph_versions']);
  } finally { fs.rmSync(dataDir, { recursive: true, force: true }); }
});
