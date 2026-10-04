'use strict';

const { all, graphSymbol } = require('./graphStore');
const { terms, readSource } = require('./chunker');
const { status } = require('./semanticStore');

function validateRequest(body) {
  if (!body || typeof body !== 'object' || !/^[a-f0-9]{24}$/.test(body.repoId) ||
      (body.entityId !== undefined && !/^[a-f0-9]{40}$/.test(body.entityId)) ||
      (body.contextEntityId !== undefined && !/^[a-f0-9]{40}$/.test(body.contextEntityId)) ||
      (body.query !== undefined && (typeof body.query !== 'string' || !body.query.trim() || body.query.length > 2000)) ||
      (!body.entityId && !body.query) ||
      (body.limit !== undefined && (!Number.isInteger(body.limit) || body.limit < 1 || body.limit > 20))) {
    throw new Error('Invalid search request. Provide repoId and query or entityId; limit is 1 to 20.');
  }
}

function cosine(left, right) {
  if (!left || !right || left.length !== right.length) return 0;
  return Math.max(-1, Math.min(1, left.reduce((sum, value, i) => sum + value * right[i], 0)));
}

async function searchCode(registry, provider, body, options = {}) {
  validateRequest(body);
  const repository = await registry.get(body.repoId);
  if (!repository) throw new Error('Repository not found.');
  const snapshot = await registry.withDb(db => ({
    version: status(db, body.repoId),
    rows: all(db, `SELECT c.*, e.model, e.vector_json FROM semantic_chunks c
      JOIN graph_nodes n ON n.id = c.entity_id AND n.repo_id = c.repo_id
      LEFT JOIN semantic_embeddings e ON e.entity_id = c.entity_id WHERE c.repo_id = ?`, [body.repoId]),
    edges: all(db, "SELECT from_id, to_id, type FROM graph_edges WHERE repo_id = ? AND type != 'SIMILAR_TO'", [body.repoId]),
  }));
  if (!snapshot.version) throw new Error('Repository needs indexing before search.');
  const rows = snapshot.rows.map(row => ({ ...JSON.parse(row.metadata_json), revision: row.revision,
    model: row.model, vector: row.vector_json ? JSON.parse(row.vector_json) : null }));
  const source = body.entityId ? rows.find(row => row.entityId === body.entityId) : null;
  if (body.entityId && !source) throw new Error('Source entity not found in this repository.');
  const contextId = body.contextEntityId || body.entityId;
  if (contextId && !rows.some(row => row.entityId === contextId)) throw new Error('Context entity not found in this repository.');
  if (source?.path) readSource(repository.rootPath, source.path, source.sourceHash);
  const query = body.query || `${source.name} ${source.signature} ${source.terms.join(' ')}`;
  const queryTerms = terms(query);
  let queryVector = null;
  let warning = snapshot.version.error;
  try {
    if (snapshot.version.state !== 'ready') throw new Error(snapshot.version.error || 'Embedding index needs refresh.');
    const model = await provider.identity();
    if (model !== snapshot.version.model) throw new Error('Embedding model changed. Refresh the repository index.');
    queryVector = source?.vector || (await provider.embed([query], 'query'))[0];
    if (rows.some(row => row.vector && row.vector.length !== queryVector.length)) throw new Error('Embedding dimension mismatch. Refresh the index.');
  } catch (error) { warning = error.message; queryVector = null; }
  const neighbors = new Set(snapshot.edges.filter(edge => edge.from_id === contextId || edge.to_id === contextId)
    .flatMap(edge => [edge.from_id, edge.to_id]));
  const documentFrequency = new Map();
  for (const row of rows) for (const term of row.terms) documentFrequency.set(term, (documentFrequency.get(term) || 0) + 1);
  const weight = term => Math.log(1 + rows.length / (1 + (documentFrequency.get(term) || 0)));
  const totalWeight = queryTerms.reduce((sum, term) => sum + weight(term), 0) || 1;
  const ranked = rows.filter(row => row.entityId !== body.entityId && (!options.kinds || options.kinds.has(row.kind))).map(row => {
    const exact = row.name.toLowerCase() === query.toLowerCase().trim() ||
      (/[a-z][A-Z]|_|\./.test(row.name) && query.split(/\s+/).includes(row.name));
    const lexical = queryTerms.reduce((sum, term) => sum + (row.terms.includes(term) ? weight(term) : 0), 0) / totalWeight;
    const semantic = Math.max(0, cosine(queryVector, row.vector));
    const identicalBody = Boolean(source?.bodyHash && source.bodyHash === row.bodyHash);
    const graphBoost = neighbors.has(row.entityId) ? 0.05 : 0;
    const codeIntent = /\b(function|method|implementation)\b/i.test(query);
    const kindPenalty = codeIntent && ['document', 'test', 'commit'].includes(row.kind) ? 0.8 : 1;
    const score = Math.min(1, Math.max(exact ? 0.95 : 0, identicalBody ? 0.98 : 0,
      (0.75 * semantic + 0.2 * lexical) * kindPenalty) + graphBoost);
    return { ...row, score, scores: { exact: Number(exact), lexical, semantic, graphBoost, identicalBody } };
  }).filter(row => row.scores.exact || row.scores.identicalBody || row.scores.lexical >= 0.15 || row.scores.semantic >= 0.55)
    .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path) || a.entityId.localeCompare(b.entityId));
  const seeds = new Set(ranked.slice(0, 5).map(row => row.entityId));
  // Promote an owning symbol when its nested helper matches; ubiquitous callees are not evidence of relevance.
  const connected = new Set(snapshot.edges.filter(edge => edge.type === 'CONTAINS' && seeds.has(edge.to_id))
    .map(edge => edge.from_id));
  for (const row of ranked) {
    if (!seeds.has(row.entityId) && connected.has(row.entityId) && row.scores.semantic >= 0.55) {
      row.scores.graphBoost = Math.min(0.1, row.scores.graphBoost + 0.08);
      row.score = Math.min(1, row.score + 0.08);
    }
  }
  ranked.sort((a, b) => b.score - a.score || a.path.localeCompare(b.path) || a.entityId.localeCompare(b.entityId));
  const results = [];
  const checked = new Map();
  let staleCount = 0;
  for (const row of ranked) {
    if (row.path) {
      const key = `${row.path}:${row.sourceHash}`;
      if (!checked.has(key)) {
        try { readSource(repository.rootPath, row.path, row.sourceHash); checked.set(key, true); }
        catch { checked.set(key, false); }
      }
      if (!checked.get(key)) { staleCount++; continue; }
    }
    const { vector, terms: _terms, model, ...result } = row;
    results.push(result);
    if (results.length >= (body.limit || 10)) break;
  }
  // Recheck after the model await; an indexing job may have replaced the snapshot meanwhile.
  return registry.withDb(db => {
    const current = status(db, body.repoId);
    if (!current || current.refreshedAt !== snapshot.version.refreshedAt) throw new Error('Index changed during search. Retry the query.');
    return { repoId: body.repoId, revision: current.revision, indexedAt: current.refreshedAt, mode: queryVector ? 'hybrid' : 'lexical', warning,
      staleCount, results: results.map(result => {
        const graph = graphSymbol(db, result.entityId);
        return { ...result, callers: graph?.queries.callers || [], tests: graph?.queries.tests || [],
          reason: result.scores.identicalBody ? 'Matching AST body; review signature and dependencies.' :
            result.scores.exact ? 'Exact symbol name match.' : 'Ranked by code terms, semantic similarity, and graph context.' };
      }) };
  });
}

module.exports = { searchCode, validateRequest, cosine };
