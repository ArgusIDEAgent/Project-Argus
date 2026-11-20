'use strict';

const path = require('node:path');
const { stableId, PARSER_VERSION } = require('./codeParser');

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS graph_files (
    repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path TEXT NOT NULL, content_hash TEXT NOT NULL, parser_version INTEGER NOT NULL, facts_json TEXT NOT NULL,
    PRIMARY KEY(repo_id, path)
  );
  CREATE TABLE IF NOT EXISTS graph_nodes (
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    type TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    qualified_name TEXT, props_json TEXT NOT NULL, revision TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS graph_nodes_repo_path ON graph_nodes(repo_id, path);
  CREATE TABLE IF NOT EXISTS graph_edges (
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL, from_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    to_id TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    type TEXT NOT NULL, confidence REAL NOT NULL, provenance TEXT NOT NULL,
    line INTEGER, revision TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS graph_edges_from ON graph_edges(repo_id, from_id);
  CREATE INDEX IF NOT EXISTS graph_edges_to ON graph_edges(repo_id, to_id);
  CREATE TABLE IF NOT EXISTS graph_versions (
    repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
    revision TEXT NOT NULL, indexed_at TEXT NOT NULL, parsed_count INTEGER NOT NULL,
    node_count INTEGER NOT NULL, edge_count INTEGER NOT NULL, parse_errors INTEGER NOT NULL
  );`;

function all(db, sql, params = []) {
  const statement = db.prepare(sql);
  try {
    statement.bind(params);
    const rows = [];
    while (statement.step()) rows.push(statement.getAsObject());
    return rows;
  } finally { statement.free(); }
}
function one(db, sql, params = []) { return all(db, sql, params)[0]; }
function nodeObject(row) {
  return row && { id: row.id, repoId: row.repo_id, type: row.type, path: row.path,
    name: row.name, kind: row.kind, qualifiedName: row.qualified_name,
    revision: row.revision, ...JSON.parse(row.props_json) };
}
function edgeObject(row) {
  return { id: row.id, fromId: row.from_id, toId: row.to_id, type: row.type,
    confidence: row.confidence, provenance: row.provenance, line: row.line,
    sourcePath: row.source_path, revision: row.revision };
}

function resolveImport(filePath, module, language, available) {
  const bases = module.startsWith('.')
    ? [path.posix.normalize(path.posix.join(path.posix.dirname(filePath), module))]
    : language === 'python' ? [path.posix.normalize(path.posix.join(path.posix.dirname(filePath), module.replace(/\./g, '/'))),
      module.replace(/\./g, '/')] : [];
  for (const base of bases) {
    if (base.startsWith('../') || base === '..') continue;
    const candidates = [base, ...['.js', '.jsx', '.ts', '.tsx', '.py'].map(ext => `${base}${ext}`),
      ...['index.js', 'index.ts', 'index.tsx', '__init__.py'].map(name => `${base}/${name}`)];
    const match = candidates.find(candidate => available.has(candidate));
    if (match) return match;
  }
  return null;
}

function graphVersion(db, repoId) {
  const row = one(db, 'SELECT * FROM graph_versions WHERE repo_id = ?', [repoId]);
  return row ? { revision: row.revision, indexedAt: row.indexed_at,
    parsedCount: row.parsed_count, nodeCount: row.node_count,
    edgeCount: row.edge_count, parseErrors: row.parse_errors } : null;
}

function applyGraph(db, repoId, revision, prepared) {
  const changedPaths = new Set([...prepared.parsed.map(facts => facts.path), ...prepared.deletes]);
  for (const filePath of changedPaths) {
    db.run('DELETE FROM graph_files WHERE repo_id = ? AND path = ?', [repoId, filePath]);
    db.run('DELETE FROM graph_edges WHERE repo_id = ? AND source_path = ?', [repoId, filePath]);
    db.run('DELETE FROM graph_nodes WHERE repo_id = ? AND path = ?', [repoId, filePath]);
  }
  for (const facts of prepared.parsed) {
    db.run('INSERT INTO graph_files VALUES (?, ?, ?, ?, ?)',
      [repoId, facts.path, facts.contentHash, PARSER_VERSION, JSON.stringify(facts)]);
    const nodes = [
      { id: stableId(repoId, 'file', facts.path), type: 'file', path: facts.path,
        name: facts.path, kind: 'file', qualifiedName: facts.path,
        language: facts.language, contentHash: facts.contentHash },
      ...facts.symbols,
      ...facts.routes.map(route => ({ id: stableId(repoId, 'endpoint', facts.path, route.method, route.route),
        type: 'endpoint', path: facts.path, name: `${route.method} ${route.route}`, kind: 'endpoint',
        qualifiedName: `${route.method} ${route.route}`, method: route.method, route: route.route, line: route.line })),
      ...facts.entities.map(entity => ({ id: stableId(repoId, 'entity', facts.path, entity.name),
        type: 'entity', path: facts.path, name: entity.name, kind: 'entity',
        qualifiedName: entity.name, line: entity.line })),
    ];
    for (const node of nodes) db.run(`INSERT OR REPLACE INTO graph_nodes
      (id, repo_id, type, path, name, kind, qualified_name, props_json, revision)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`, [node.id, repoId, node.type, facts.path,
      node.name, node.kind, node.qualifiedName || null, JSON.stringify(node), revision]);
  }

  const allFacts = all(db, 'SELECT facts_json FROM graph_files WHERE repo_id = ?', [repoId])
    .map(row => JSON.parse(row.facts_json));
  const nodeRows = all(db, 'SELECT * FROM graph_nodes WHERE repo_id = ?', [repoId]);
  const nodes = new Map(nodeRows.map(row => [row.id, nodeObject(row)]));
  const paths = new Set(allFacts.map(facts => facts.path));
  const byPath = new Map();
  const endpoints = new Map();
  const entities = new Map();
  for (const node of nodes.values()) {
    if (node.type === 'symbol') {
      if (!byPath.has(node.path)) byPath.set(node.path, []);
      byPath.get(node.path).push(node);
    }
    if (node.type === 'endpoint') {
      const key = `${node.method} ${node.route}`;
      if (!endpoints.has(key)) endpoints.set(key, []);
      endpoints.get(key).push(node);
    }
    if (node.type === 'entity') {
      if (!entities.has(node.name)) entities.set(node.name, []);
      entities.get(node.name).push(node);
    }
  }

  const desired = new Map();
  function edge(facts, fromId, toId, type, confidence, provenance, line) {
    if (!nodes.has(fromId) || !nodes.has(toId)) return;
    const id = stableId(repoId, fromId, toId, type, facts.path, line || 0);
    desired.set(id, { id, repoId, sourcePath: facts.path, fromId, toId, type,
      confidence, provenance, line: line || null, revision });
  }
  for (const facts of allFacts) {
    const fileId = stableId(repoId, 'file', facts.path);
    const imported = new Map();
    for (const item of facts.imports) {
      const targetPath = resolveImport(facts.path, item.module, facts.language, paths);
      if (!targetPath) continue;
      edge(facts, fileId, stableId(repoId, 'file', targetPath), 'IMPORTS', 1, 'syntax', item.line);
      for (const binding of item.bindings) imported.set(binding.local, { path: targetPath, imported: binding.imported });
    }
    function resolveSymbol(name, ownerId) {
      if (!name) return null;
      const parts = name.split('.');
      const simple = parts.at(-1);
      const local = byPath.get(facts.path) || [];
      const owner = nodes.get(ownerId);
      if (parts[0] === 'this' && owner) {
        const parent = owner.parentId && nodes.get(owner.parentId);
        const match = local.find(symbol => symbol.name === simple && symbol.parentId === parent?.id);
        if (match) return { symbol: match, confidence: 0.95 };
      }
      if (parts.length === 1) {
        const matches = local.filter(symbol => symbol.name === simple && symbol.id !== ownerId);
        if (matches.length === 1) return { symbol: matches[0], confidence: 1 };
      }
      const binding = imported.get(parts[0]);
      if (binding) {
        const expected = parts.length === 1 ? binding.imported : simple;
        const matches = (byPath.get(binding.path) || []).filter(symbol =>
          symbol.name === expected || (expected === 'default' && symbol.visibility === 'public'));
        if (matches.length === 1) return { symbol: matches[0], confidence: 0.85 };
      }
      return null;
    }
    for (const symbol of facts.symbols) {
      edge(facts, fileId, symbol.id, 'CONTAINS', 1, 'syntax', symbol.startLine);
      if (symbol.parentId) edge(facts, symbol.parentId, symbol.id, 'CONTAINS', 1, 'syntax', symbol.startLine);
    }
    for (const route of facts.routes) {
      const endpointId = stableId(repoId, 'endpoint', facts.path, route.method, route.route);
      edge(facts, fileId, endpointId, 'CONTAINS', 1, 'framework', route.line);
      const handler = resolveSymbol(route.handlerName, null);
      if (handler) edge(facts, endpointId, handler.symbol.id, 'EXPOSES', handler.confidence, 'framework', route.line);
    }
    for (const entity of facts.entities) {
      const entityId = stableId(repoId, 'entity', facts.path, entity.name);
      edge(facts, fileId, entityId, 'CONTAINS', 1, 'framework', entity.line);
      if (entity.sourceId) edge(facts, entity.sourceId, entityId, 'DEFINES', 0.95, 'framework', entity.line);
    }
    for (const call of facts.calls) {
      const target = resolveSymbol(call.name, call.callerId);
      if (!target) continue;
      edge(facts, call.callerId, target.symbol.id, 'CALLS', target.confidence, 'lexical', call.line);
      if (nodes.get(call.callerId)?.kind === 'test') {
        edge(facts, call.callerId, target.symbol.id, 'TESTS', target.confidence, 'test_adapter', call.line);
      }
    }
    for (const client of facts.clients) {
      const targets = endpoints.get(`${client.method} ${client.route}`) || [];
      if (targets.length === 1) edge(facts, client.sourceId, targets[0].id, 'CONSUMES', 0.95, 'framework', client.line);
    }
    for (const access of facts.accesses) {
      const targets = entities.get(access.entityName) || [];
      if (targets.length === 1) edge(facts, access.sourceId, targets[0].id, access.type, 0.8, 'framework', access.line);
    }
  }
  const existing = new Set(all(db, "SELECT id FROM graph_edges WHERE repo_id = ? AND provenance != 'documentation'", [repoId]).map(row => row.id));
  for (const id of existing) if (!desired.has(id)) db.run('DELETE FROM graph_edges WHERE id = ?', [id]);
  for (const item of desired.values()) {
    if (existing.has(item.id)) continue;
    db.run(`INSERT INTO graph_edges
      (id, repo_id, source_path, from_id, to_id, type, confidence, provenance, line, revision)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, [item.id, repoId, item.sourcePath, item.fromId,
      item.toId, item.type, item.confidence, item.provenance, item.line, item.revision]);
  }
  const nodeCount = one(db, 'SELECT count(*) AS count FROM graph_nodes WHERE repo_id = ?', [repoId]).count;
  const edgeCount = one(db, 'SELECT count(*) AS count FROM graph_edges WHERE repo_id = ?', [repoId]).count;
  const parseErrors = allFacts.filter(facts => facts.parseErrors).length;
  db.run(`INSERT OR REPLACE INTO graph_versions VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [repoId, revision, new Date().toISOString(), prepared.parsed.length, nodeCount, edgeCount, parseErrors]);
  return { parsedCount: prepared.parsed.length, nodeCount, edgeCount, parseErrors };
}

function graphSymbol(db, id) {
  const row = one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [id]);
  if (!row) return undefined;
  const outgoing = all(db, 'SELECT * FROM graph_edges WHERE from_id = ? ORDER BY type, line', [id]);
  const incoming = all(db, 'SELECT * FROM graph_edges WHERE to_id = ? ORDER BY type, line', [id]);
  const relatedIds = new Set([...outgoing.map(edge => edge.to_id), ...incoming.map(edge => edge.from_id)]);
  const related = [...relatedIds].map(nodeId => nodeObject(one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [nodeId])));
  return { node: nodeObject(row), outgoing: outgoing.map(edgeObject), incoming: incoming.map(edgeObject), related,
    queries: {
      callers: graphNeighbors(db, id, 'CALLS', 'incoming'),
      callees: graphNeighbors(db, id, 'CALLS', 'outgoing'),
      endpointHandlers: graphNeighbors(db, id, 'EXPOSES', 'outgoing'),
      componentEndpoints: graphNeighbors(db, id, 'CONSUMES', 'outgoing'),
      tests: graphNeighbors(db, id, 'TESTS', 'incoming'),
      dataAccess: ['READS_FROM', 'WRITES_TO', 'USES_MODEL'].flatMap(type => graphNeighbors(db, id, type, 'outgoing')),
    } };
}

function graphNeighbors(db, id, type, direction) {
  const column = direction === 'incoming' ? 'to_id' : 'from_id';
  const target = direction === 'incoming' ? 'from_id' : 'to_id';
  return all(db, `SELECT ${target} AS id FROM graph_edges WHERE ${column} = ? AND type = ?`, [id, type])
    .map(row => nodeObject(one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [row.id])));
}

function graphFile(db, repoId, filePath) {
  const file = one(db, 'SELECT path FROM graph_files WHERE repo_id = ? AND path = ?', [repoId, filePath]);
  if (!file) return undefined;
  const nodes = all(db, 'SELECT * FROM graph_nodes WHERE repo_id = ? AND path = ? ORDER BY type, name', [repoId, filePath]);
  const edges = all(db, 'SELECT * FROM graph_edges WHERE repo_id = ? AND source_path = ? ORDER BY type, line', [repoId, filePath]);
  return { path: filePath, version: graphVersion(db, repoId), nodes: nodes.map(nodeObject), edges: edges.map(edgeObject) };
}

function graphImpact(db, id, maxDepth = 5) {
  const root = one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [id]);
  if (!root) return undefined;
  const nodes = [nodeObject(root)];
  const edges = [];
  const visited = new Set([id]);
  let frontier = [{ id, depth: 0 }];
  while (frontier.length && visited.size < 500) {
    const next = [];
    for (const item of frontier) {
      if (item.depth >= Math.min(maxDepth, 12)) continue;
      for (const edge of all(db, `SELECT * FROM graph_edges WHERE to_id = ? AND type IN
        ('CALLS', 'CONSUMES', 'EXPOSES', 'IMPORTS', 'TESTS', 'READS_FROM', 'WRITES_TO', 'USES_MODEL')`, [item.id])) {
        if (visited.has(edge.from_id)) continue;
        visited.add(edge.from_id);
        edges.push(edgeObject(edge));
        nodes.push(nodeObject(one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [edge.from_id])));
        next.push({ id: edge.from_id, depth: item.depth + 1 });
        if (visited.size >= 500) break;
      }
    }
    frontier = next;
  }
  return { rootId: id, nodes, edges, truncated: visited.size >= 500 };
}

function graphPath(db, fromId, toId, maxDepth = 10) {
  const from = one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [fromId]);
  const to = one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [toId]);
  if (!from || !to || from.repo_id !== to.repo_id) return undefined;
  const queue = [{ id: fromId, nodes: [fromId], edges: [] }];
  const visited = new Set([fromId]);
  for (let index = 0; index < queue.length && visited.size < 1000; index++) {
    const item = queue[index];
    if (item.id === toId) return { nodes: item.nodes.map(id => nodeObject(one(db, 'SELECT * FROM graph_nodes WHERE id = ?', [id]))),
      edges: item.edges, depth: item.edges.length };
    if (item.edges.length >= Math.min(maxDepth, 12)) continue;
    const neighbors = all(db, `SELECT * FROM graph_edges WHERE repo_id = ?
      AND type IN ('CALLS', 'CONSUMES', 'EXPOSES', 'READS_FROM', 'WRITES_TO', 'TESTS', 'USES_MODEL')
      AND (from_id = ? OR to_id = ?)`,
      [from.repo_id, item.id, item.id]);
    for (const row of neighbors) {
      const nextId = row.from_id === item.id ? row.to_id : row.from_id;
      if (visited.has(nextId)) continue;
      visited.add(nextId);
      queue.push({ id: nextId, nodes: [...item.nodes, nextId], edges: [...item.edges, edgeObject(row)] });
    }
  }
  return { nodes: [], edges: [], depth: null };
}

module.exports = { SCHEMA, all, one, graphVersion, applyGraph, graphSymbol, graphFile,
  graphImpact, graphPath, graphNeighbors };
