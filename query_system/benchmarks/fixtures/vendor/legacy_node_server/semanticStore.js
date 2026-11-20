'use strict';

const { all, one } = require('./graphStore');
const { chunksForFile, commitChunks, readSource } = require('./chunker');
const docStore = require('./docStore');
const SCHEMA = `
  CREATE TABLE IF NOT EXISTS semantic_chunks (
    entity_id TEXT PRIMARY KEY REFERENCES graph_nodes(id) ON DELETE CASCADE,
    repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL, metadata_json TEXT NOT NULL, revision TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS semantic_chunks_repo ON semantic_chunks(repo_id);
  CREATE TABLE IF NOT EXISTS semantic_embeddings (
    entity_id TEXT PRIMARY KEY REFERENCES semantic_chunks(entity_id) ON DELETE CASCADE,
    model TEXT NOT NULL, dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS semantic_versions (
    repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    revision TEXT NOT NULL, model TEXT, state TEXT NOT NULL, error TEXT,
    chunk_count INTEGER NOT NULL, embedded_count INTEGER NOT NULL, refreshed_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS reuse_feedback (
    repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    query_hash TEXT NOT NULL, entity_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    decision TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(repo_id, query_hash, entity_id)
  );
  CREATE TABLE IF NOT EXISTS semantic_calibration (
    repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    model TEXT NOT NULL, thresholds_json TEXT NOT NULL, metrics_json TEXT NOT NULL, calibrated_at TEXT NOT NULL
  );`;

function status(db, repoId) {
  const row = one(db, 'SELECT * FROM semantic_versions WHERE repo_id = ?', [repoId]);
  return row ? { revision: row.revision, model: row.model, state: row.state, error: row.error,
    chunkCount: row.chunk_count, embeddedCount: row.embedded_count, refreshedAt: row.refreshed_at } : null;
}

async function prepareSemantic(registry, repoId, inventory, provider) {
  const repository = await registry.get(repoId);
  if (!repository) throw new Error('Repository not found.');
  const previous = await registry.withDb(db => ({
    facts: all(db, 'SELECT facts_json FROM graph_files WHERE repo_id = ?', [repoId]).map(row => JSON.parse(row.facts_json)),
    chunks: all(db, `SELECT c.*, e.model, e.vector_json FROM semantic_chunks c LEFT JOIN semantic_embeddings e
      ON e.entity_id = c.entity_id WHERE c.repo_id = ?`, [repoId]),
    docs: all(db, "SELECT * FROM doc_sections WHERE repo_id = ? AND generated = 1 AND freshness != 'stale'", [repoId]),
  }));
  const facts = new Map(previous.facts.map(item => [item.path, item]));
  for (const file of inventory.graph.deletes) facts.delete(file);
  for (const file of inventory.graph.parsed) facts.set(file.path, file);
  const chunks = [];
  for (const file of facts.values()) chunks.push(...chunksForFile(repoId, file,
    readSource(repository.rootPath, file.path, file.contentHash)));
  chunks.push(...commitChunks(repoId, inventory));
  chunks.push(...previous.docs.map(docStore.semanticChunk));
  const cached = new Map(previous.chunks.map(row => [row.entity_id, row]));
  let model = null;
  let error = null;
  let embeddedCount = 0;
  try { model = await provider.identity(); }
  catch (cause) { error = cause.message; }
  const pending = [];
  for (const chunk of chunks) {
    const old = cached.get(chunk.entityId);
    if (model && old?.model === model && old.content_hash === chunk.contentHash && old.vector_json) {
      chunk.vector = JSON.parse(old.vector_json);
    } else if (model) pending.push(chunk);
  }
  if (model) {
    try {
      for (let offset = 0; offset < pending.length; offset += 8) {
        const batch = pending.slice(offset, offset + 8);
        const vectors = await provider.embed(batch.map(chunk => chunk.text));
        batch.forEach((chunk, index) => { chunk.vector = vectors[index]; embeddedCount++; });
      }
      if (new Set(chunks.filter(chunk => chunk.vector).map(chunk => chunk.vector.length)).size > 1) {
        throw new Error('Embedding dimensions changed. Refresh with a consistent model.');
      }
      if (await provider.identity() !== model) throw new Error('Embedding model changed during indexing. Refresh again.');
    } catch (cause) {
      error = cause.message;
      for (const chunk of chunks) delete chunk.vector;
    }
  }
  for (const file of facts.values()) readSource(repository.rootPath, file.path, file.contentHash);
  return { chunks, model, error, embeddedCount };
}

function applySemantic(db, repoId, revision, prepared) {
  // Graph and vectors are committed together, so a search never observes mixed source revisions.
  db.run('DELETE FROM semantic_chunks WHERE repo_id = ?', [repoId]);
  db.run("DELETE FROM graph_nodes WHERE repo_id = ? AND type = 'commit'", [repoId]);
  for (const chunk of prepared.chunks) {
    const { text, vector, ...metadata } = chunk;
    if (chunk.kind === 'commit') db.run(`INSERT INTO graph_nodes VALUES (?, ?, 'commit', '', ?, 'commit', ?, ?, ?)`,
      [chunk.entityId, repoId, chunk.name, chunk.name, JSON.stringify(metadata), revision]);
    db.run('INSERT INTO semantic_chunks VALUES (?, ?, ?, ?, ?)',
      [chunk.entityId, repoId, chunk.contentHash, JSON.stringify(metadata), revision]);
    if (vector) db.run('INSERT INTO semantic_embeddings VALUES (?, ?, ?, ?)',
      [chunk.entityId, prepared.model, vector.length, JSON.stringify(vector)]);
  }
  const embedded = prepared.chunks.filter(chunk => chunk.vector).length;
  db.run('INSERT OR REPLACE INTO semantic_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [repoId, revision, prepared.model, prepared.error ? 'degraded' : 'ready', prepared.error,
      prepared.chunks.length, embedded, new Date().toISOString()]);
  return { ...status(db, repoId), reembeddedCount: prepared.embeddedCount };
}

module.exports = { SCHEMA, status, prepareSemantic, applySemantic };
