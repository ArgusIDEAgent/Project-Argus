'use strict';

const { all, one } = require('./graphStore');
const { terms, hash } = require('./chunker');
const { stableId } = require('./codeParser');
const { planDocs } = require('./docPlanner');

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS doc_sections (
    section_id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    title TEXT NOT NULL, kind TEXT NOT NULL, content_markdown TEXT NOT NULL,
    facts_hash TEXT NOT NULL, source_commit TEXT, source_revision TEXT,
    generation_reason TEXT NOT NULL, freshness TEXT NOT NULL,
    generated INTEGER NOT NULL, proposed_markdown TEXT, affected_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS doc_sections_repo ON doc_sections(repo_id, kind);
  CREATE TABLE IF NOT EXISTS doc_links (
    section_id TEXT NOT NULL REFERENCES doc_sections(section_id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    relation TEXT NOT NULL, PRIMARY KEY(section_id, entity_id, relation)
  );
  CREATE INDEX IF NOT EXISTS doc_links_entity ON doc_links(entity_id);`;

function sectionObject(db, row) {
  if (!row) return undefined;
  const evidence = all(db, `SELECT n.id, n.type, n.path, n.name, n.kind, n.props_json, l.relation,
    f.content_hash AS source_hash FROM doc_links l JOIN graph_nodes n ON n.id = l.entity_id
    LEFT JOIN graph_files f ON f.repo_id = n.repo_id AND f.path = n.path
    WHERE l.section_id = ? AND n.repo_id = ? ORDER BY n.path, n.name, l.relation`, [row.section_id, row.repo_id])
    .map(item => { const props = JSON.parse(item.props_json); return { entityId: item.id, type: item.type,
      path: item.path, name: item.name, kind: item.kind, relation: item.relation,
      startLine: props.startLine || props.line || 1, endLine: props.endLine || props.line || props.startLine || 1,
      sourceHash: item.source_hash }; });
  return { repoId: row.repo_id, sectionId: row.section_id, title: row.title, kind: row.kind,
    contentMarkdown: row.content_markdown, sourceCommit: row.source_commit,
    sourceRevision: row.source_revision, generationReason: row.generation_reason,
    freshness: row.freshness, generated: Boolean(row.generated), proposedMarkdown: row.proposed_markdown,
    affectedBy: JSON.parse(row.affected_json), createdAt: row.created_at, updatedAt: row.updated_at, evidence };
}

function status(db, repoId) {
  const rows = all(db, 'SELECT freshness, generated, affected_json FROM doc_sections WHERE repo_id = ?', [repoId]);
  return { sectionCount: rows.length, currentCount: rows.filter(row => row.freshness === 'current').length,
    staleCount: rows.filter(row => row.freshness === 'stale' || JSON.parse(row.affected_json).length > 0).length,
    affectedCount: rows.filter(row => row.freshness === 'affected-by-uncommitted-change').length,
    generatedCount: rows.filter(row => row.generated).length };
}

function overview(db, repoId) {
  const row = one(db, "SELECT * FROM doc_sections WHERE repo_id = ? AND kind = 'system_overview' ORDER BY generated DESC LIMIT 1", [repoId]);
  return sectionObject(db, row);
}

function section(db, repoId, sectionId) {
  return sectionObject(db, one(db, 'SELECT * FROM doc_sections WHERE repo_id = ? AND section_id = ?', [repoId, sectionId]));
}

function stale(db, repoId) {
  return all(db, "SELECT * FROM doc_sections WHERE repo_id = ? AND freshness != 'current' ORDER BY updated_at DESC", [repoId])
    .map(row => sectionObject(db, row));
}

function list(db, repoId) {
  return all(db, 'SELECT section_id, title, kind, freshness, generated, updated_at FROM doc_sections WHERE repo_id = ? ORDER BY kind, title', [repoId])
    .map(row => ({ sectionId: row.section_id, title: row.title, kind: row.kind,
      freshness: row.freshness, generated: Boolean(row.generated), updatedAt: row.updated_at }));
}

function semanticChunk(row) {
  const text = row.content_markdown.slice(0, 6000);
  return { entityId: row.section_id, repoId: row.repo_id, path: '', name: row.title,
    kind: 'document', language: 'markdown', startLine: 1, endLine: Math.max(1, text.split('\n').length),
    contentHash: hash(text), sourceHash: row.facts_hash, signature: '', bodyHash: null, calls: [],
    parseErrors: false, summarized: row.content_markdown.length > 6000, terms: terms(text),
    text, chunkVersion: 1, docSectionId: row.section_id, docKind: row.kind };
}

function indexSection(db, row, vector, model) {
  const chunk = semanticChunk(row);
  const { text, ...metadata } = chunk;
  db.run('INSERT OR REPLACE INTO semantic_chunks VALUES (?, ?, ?, ?, ?)',
    [row.section_id, row.repo_id, chunk.contentHash, JSON.stringify(metadata), row.source_revision || 'unindexed']);
  if (vector && model) db.run('INSERT OR REPLACE INTO semantic_embeddings VALUES (?, ?, ?, ?)',
    [row.section_id, model, vector.length, JSON.stringify(vector)]);
}

function markAffected(db, repoId, changes, revision, dirtyFingerprint) {
  if (!changes.length) return [];
  const changed = new Set(changes.flatMap(change => [change.path, change.oldPath].filter(Boolean)));
  const rows = all(db, `SELECT DISTINCT s.* FROM doc_sections s JOIN doc_links l ON l.section_id = s.section_id
    JOIN graph_nodes n ON n.id = l.entity_id WHERE s.repo_id = ?`, [repoId]);
  const impacted = [];
  for (const row of rows) {
    const paths = all(db, `SELECT DISTINCT n.path FROM doc_links l JOIN graph_nodes n ON n.id = l.entity_id
      WHERE l.section_id = ? AND n.path != ''`, [row.section_id]).map(item => item.path).filter(item => changed.has(item));
    if (!paths.length) continue;
    const freshness = dirtyFingerprint === 'clean' ? 'stale' : 'affected-by-uncommitted-change';
    db.run('UPDATE doc_sections SET freshness = ?, affected_json = ? WHERE section_id = ?',
      [freshness, JSON.stringify(paths), row.section_id]);
    db.run('DELETE FROM semantic_chunks WHERE entity_id = ?', [row.section_id]);
    impacted.push(row.section_id);
  }
  return impacted;
}

function linkSection(db, row, entityIds) {
  db.run('DELETE FROM doc_links WHERE section_id = ?', [row.section_id]);
  db.run("DELETE FROM graph_edges WHERE repo_id = ? AND provenance = 'documentation' AND (from_id = ? OR to_id = ?)",
    [row.repo_id, row.section_id, row.section_id]);
  for (const entityId of entityIds) {
    const node = one(db, 'SELECT id, path FROM graph_nodes WHERE id = ? AND repo_id = ?', [entityId, row.repo_id]);
    if (!node) continue;
    for (const type of ['DOCUMENTS', 'GENERATED_FROM']) {
      db.run('INSERT OR IGNORE INTO doc_links VALUES (?, ?, ?)', [row.section_id, entityId, type]);
      db.run(`INSERT OR REPLACE INTO graph_edges
        (id, repo_id, source_path, from_id, to_id, type, confidence, provenance, line, revision)
        VALUES (?, ?, ?, ?, ?, ?, 1, 'documentation', NULL, ?)`,
      [stableId(row.repo_id, 'doc_edge', row.section_id, entityId, type), row.repo_id,
        node.path, row.section_id, entityId, type, row.source_revision || 'unindexed']);
    }
    db.run(`INSERT OR REPLACE INTO graph_edges
      (id, repo_id, source_path, from_id, to_id, type, confidence, provenance, line, revision)
      VALUES (?, ?, ?, ?, ?, 'DOCUMENTED_BY', 1, 'documentation', NULL, ?)`,
    [stableId(row.repo_id, 'doc_edge', entityId, row.section_id, 'DOCUMENTED_BY'), row.repo_id,
      node.path, entityId, row.section_id, row.source_revision || 'unindexed']);
  }
}

async function syncDocs(registry, repoId, mode, modelProvider, embeddingProvider) {
  if (!['baseline', 'incremental'].includes(mode)) throw new Error('Invalid documentation sync mode.');
  const snapshot = await registry.withDb(db => {
    const version = one(db, 'SELECT * FROM index_versions WHERE repo_id = ?', [repoId]);
    if (!version) throw new Error('Repository needs indexing before documentation sync.');
    return { version, plans: planDocs(db, repoId),
      existing: all(db, 'SELECT * FROM doc_sections WHERE repo_id = ?', [repoId]) };
  });
  const existing = new Map(snapshot.existing.map(row => [row.section_id, row]));
  const plannedIds = new Set(snapshot.plans.map(plan => plan.sectionId));
  const pending = snapshot.plans.filter(plan => !existing.has(plan.sectionId) ||
    existing.get(plan.sectionId).facts_hash !== plan.factsHash || mode === 'baseline' &&
    existing.get(plan.sectionId).freshness === 'stale');
  let modelAvailable = false;
  if (pending.length) {
    try { await modelProvider.available(); modelAvailable = true; } catch { /* Factual template fallback. */ }
  }
  const generated = [];
  for (const plan of pending) {
    let content = plan.fallbackMarkdown;
    let reason = modelAvailable ? mode : 'model_unavailable';
    if (modelAvailable) {
      try {
        const prose = await modelProvider.generate(plan.title, plan.factsMarkdown.slice(0, 6000));
        content = `${prose}\n\n## Indexed evidence\n${plan.factsMarkdown}\n`;
      } catch { reason = 'model_unavailable'; }
    }
    generated.push({ plan, content, reason });
  }
  let embeddingModel = null;
  const vectors = new Map();
  if (generated.length) {
    try {
      embeddingModel = await embeddingProvider.identity();
      for (const item of generated) vectors.set(item.plan.sectionId,
        (await embeddingProvider.embed([item.content.slice(0, 6000)]))[0]);
    } catch { embeddingModel = null; vectors.clear(); }
  }
  return registry.withDb(db => {
    const current = one(db, 'SELECT * FROM index_versions WHERE repo_id = ?', [repoId]);
    if (!current || current.revision !== snapshot.version.revision) throw new Error('Index changed during documentation sync. Retry.');
    const now = new Date().toISOString();
    const updated = [];
    const affected = [];
    for (const item of generated) {
      const old = existing.get(item.plan.sectionId);
      if (old && !old.generated) {
        db.run('UPDATE doc_sections SET freshness = ?, proposed_markdown = ?, affected_json = ? WHERE section_id = ?',
          [current.dirty_fingerprint === 'clean' ? 'stale' : 'affected-by-uncommitted-change', item.content,
            JSON.stringify([...new Set([...JSON.parse(old.affected_json), ...item.plan.entityIds])]), old.section_id]);
        linkSection(db, { ...old, source_revision: current.revision }, item.plan.entityIds);
        affected.push(old.section_id);
        continue;
      }
      const row = { section_id: item.plan.sectionId, repo_id: repoId, title: item.plan.title,
        kind: item.plan.kind, content_markdown: item.content, facts_hash: item.plan.factsHash,
        source_commit: current.commit_hash, source_revision: current.revision, generation_reason: item.reason,
        freshness: current.dirty_fingerprint === 'clean' ? 'current' : 'affected-by-uncommitted-change',
        generated: 1, proposed_markdown: null, affected_json: '[]',
        created_at: old?.created_at || now, updated_at: now };
      db.run(`INSERT OR REPLACE INTO doc_sections
        (section_id, repo_id, title, kind, content_markdown, facts_hash, source_commit, source_revision,
        generation_reason, freshness, generated, proposed_markdown, affected_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, Object.values(row));
      db.run(`INSERT OR REPLACE INTO graph_nodes
        (id, repo_id, type, path, name, kind, qualified_name, props_json, revision)
        VALUES (?, ?, 'doc_section', '', ?, ?, ?, ?, ?)`,
      [row.section_id, repoId, row.title, row.kind, row.title,
        JSON.stringify({ sectionId: row.section_id, freshness: row.freshness }), current.revision]);
      linkSection(db, row, item.plan.entityIds);
      indexSection(db, row, vectors.get(row.section_id), embeddingModel);
      updated.push(row.section_id);
    }
    for (const plan of snapshot.plans) {
      const old = existing.get(plan.sectionId);
      if (!old || pending.some(item => item.sectionId === plan.sectionId) || old.freshness === 'current') continue;
      db.run('UPDATE doc_sections SET freshness = ?, affected_json = ?, source_commit = ?, source_revision = ? WHERE section_id = ?',
        [current.dirty_fingerprint === 'clean' ? 'current' : 'affected-by-uncommitted-change', '[]',
          current.commit_hash, current.revision, old.section_id]);
      const refreshed = { ...old, source_commit: current.commit_hash, source_revision: current.revision };
      linkSection(db, refreshed, plan.entityIds);
      if (old.generated) indexSection(db, refreshed);
    }
    for (const row of snapshot.existing.filter(item => !plannedIds.has(item.section_id))) {
      db.run('UPDATE doc_sections SET freshness = ?, affected_json = ? WHERE section_id = ?',
        ['stale', JSON.stringify([...new Set([...JSON.parse(row.affected_json), 'source_removed'])]), row.section_id]);
      db.run('DELETE FROM semantic_chunks WHERE entity_id = ?', [row.section_id]);
      linkSection(db, row, []);
      affected.push(row.section_id);
    }
    db.run(`UPDATE semantic_versions SET chunk_count = (SELECT count(*) FROM semantic_chunks WHERE repo_id = ?),
      embedded_count = (SELECT count(*) FROM semantic_embeddings e JOIN semantic_chunks c ON c.entity_id = e.entity_id WHERE c.repo_id = ?)
      WHERE repo_id = ?`, [repoId, repoId, repoId]);
    db.run(`UPDATE graph_versions SET node_count = (SELECT count(*) FROM graph_nodes WHERE repo_id = ?),
      edge_count = (SELECT count(*) FROM graph_edges WHERE repo_id = ?) WHERE repo_id = ?`, [repoId, repoId, repoId]);
    return { repoId, mode, updated, affected, status: status(db, repoId), overview: overview(db, repoId) };
  }, true);
}

module.exports = { SCHEMA, status, overview, section, stale, list, markAffected, semanticChunk, syncDocs };
