'use strict';

const { searchCode } = require('./retrievalService');
const { all, one } = require('./graphStore');
const { hash } = require('./chunker');
const { stableId } = require('./codeParser');

const DEFAULT_THRESHOLDS = { reuse: 0.88, extend: 0.78 };
const REUSABLE = new Set(['function', 'method', 'class', 'component', 'ml_entry']);

function classify(source, candidate, thresholds = DEFAULT_THRESHOLDS) {
  if (!REUSABLE.has(candidate.kind) || (source && !REUSABLE.has(source.kind)) || candidate.parseErrors || source?.parseErrors) {
    return { classification: 'DISTINCT', confidence: 0, reason: 'Not a complete reusable code symbol.' };
  }
  if (source) {
    const parameters = signature => signature.slice(signature.indexOf('(')).replace(/\s+/g, ' ').trim();
    const compatible = source.language === candidate.language && source.kind === candidate.kind &&
      parameters(source.signature) === parameters(candidate.signature);
    const sameCalls = JSON.stringify([...source.calls].sort()) === JSON.stringify([...candidate.calls].sort());
    const selfContained = !(source.externalNames?.length || candidate.externalNames?.length);
    if (compatible && sameCalls && selfContained && source.bodyHash && source.bodyHash === candidate.bodyHash) {
      return { classification: 'REUSE', confidence: 0.99,
        reason: 'Matching AST body, parameter contract, language, and called names. Review external bindings before reuse.' };
    }
    if (source.name === candidate.name && source.bodyHash !== candidate.bodyHash) {
      return { classification: 'DISTINCT', confidence: candidate.scores.semantic, reason: 'Same name with different implementation; insufficient behavioral evidence.' };
    }
    if (source.language !== candidate.language || !compatible || !sameCalls) {
      return { classification: candidate.scores.semantic >= thresholds.extend ? 'EXTEND' : 'DISTINCT',
        confidence: candidate.scores.semantic, reason: 'Related behavior may require adapting the signature or dependencies.' };
    }
  }
  const confidence = candidate.scores.semantic;
  return { classification: confidence >= thresholds.extend ? 'EXTEND' : 'DISTINCT', confidence,
    reason: confidence >= thresholds.extend ? 'Semantically related implementation; inspect it against the requested behavior.' :
      'Similarity does not establish reusable behavior.' };
}

async function analyzeReuse(registry, provider, body) {
  const search = await searchCode(registry, provider, { ...body, limit: 20 }, { kinds: REUSABLE });
  const queryHash = hash(body.entityId || body.query.trim());
  return registry.withDb(db => {
    const version = one(db, 'SELECT refreshed_at FROM semantic_versions WHERE repo_id = ?', [body.repoId]);
    if (version?.refreshed_at !== search.indexedAt) throw new Error('Index changed during reuse analysis. Retry.');
    const calibration = one(db, `SELECT c.thresholds_json FROM semantic_calibration c JOIN semantic_versions v
      ON v.repo_id = c.repo_id AND v.model = c.model WHERE c.repo_id = ?`, [body.repoId]);
    const thresholds = calibration ? JSON.parse(calibration.thresholds_json) : DEFAULT_THRESHOLDS;
    const row = body.entityId && one(db, 'SELECT metadata_json FROM semantic_chunks WHERE repo_id = ? AND entity_id = ?', [body.repoId, body.entityId]);
    const source = row ? JSON.parse(row.metadata_json) : null;
    const ignored = new Set(all(db, "SELECT entity_id FROM reuse_feedback WHERE repo_id = ? AND query_hash = ? AND decision = 'ignore'",
      [body.repoId, queryHash]).map(item => item.entity_id));
    const candidates = search.results.filter(candidate => REUSABLE.has(candidate.kind) && !ignored.has(candidate.entityId))
      .map(candidate => ({ ...candidate, ...classify(source, candidate, thresholds) }));
    if (source) {
      db.run("DELETE FROM graph_edges WHERE repo_id = ? AND from_id = ? AND type = 'SIMILAR_TO'", [body.repoId, source.entityId]);
      for (const candidate of candidates.filter(item => item.classification === 'REUSE')) {
        db.run('INSERT OR REPLACE INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
          [stableId(body.repoId, source.entityId, candidate.entityId, 'SIMILAR_TO'), body.repoId, source.path,
            source.entityId, candidate.entityId, 'SIMILAR_TO', candidate.confidence, 'ast_body_and_signature', source.startLine, search.revision]);
      }
    }
    const suggestions = candidates.filter(candidate => candidate.classification !== 'DISTINCT')
      .sort((a, b) => b.confidence - a.confidence || b.score - a.score).slice(0, Math.min(body.limit || 3, 3));
    return { ...search, results: suggestions, classification: suggestions[0]?.classification || 'DISTINCT',
      queryHash, thresholds, calibration: calibration ? 'repository-labels' : 'provisional',
      distinctCount: candidates.filter(item => item.classification === 'DISTINCT').length };
  }, true);
}

async function recordFeedback(registry, body) {
  if (!body || !/^[a-f0-9]{24}$/.test(body.repoId) || !/^[a-f0-9]{40}$/.test(body.entityId) ||
      !/^[a-f0-9]{64}$/.test(body.queryHash) || !['use', 'ignore'].includes(body.decision)) throw new Error('Invalid reuse feedback.');
  return registry.withDb(db => {
    if (!one(db, 'SELECT id FROM graph_nodes WHERE repo_id = ? AND id = ?', [body.repoId, body.entityId])) throw new Error('Entity not found.');
    db.run('INSERT OR REPLACE INTO reuse_feedback VALUES (?, ?, ?, ?, ?)',
      [body.repoId, body.queryHash, body.entityId, body.decision, new Date().toISOString()]);
    return { recorded: true };
  }, true);
}

module.exports = { analyzeReuse, recordFeedback, classify, DEFAULT_THRESHOLDS };
