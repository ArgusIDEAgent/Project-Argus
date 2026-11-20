'use strict';

const path = require('node:path');
const { stableId } = require('./codeParser');
const { all } = require('./graphStore');
const { hash, readSource } = require('./chunker');

function planDocs(db, repoId) {
  const nodes = all(db, "SELECT * FROM graph_nodes WHERE repo_id = ? AND type IN ('file', 'symbol', 'endpoint', 'entity') ORDER BY path, type, name", [repoId])
    .map(row => ({ ...row, props: JSON.parse(row.props_json) }));
  const edges = all(db, "SELECT from_id, to_id, type FROM graph_edges WHERE repo_id = ? AND provenance != 'documentation' ORDER BY type, from_id, to_id", [repoId]);
  const files = nodes.filter(node => node.type === 'file');
  const symbols = nodes.filter(node => node.type === 'symbol');
  const endpoints = nodes.filter(node => node.type === 'endpoint');
  const entities = nodes.filter(node => node.type === 'entity');
  const byId = new Map(nodes.map(node => [node.id, node]));
  const sections = [];

  function add(kind, key, title, lines, evidence, scope, fingerprints = []) {
    const unique = [...new Set(evidence.map(node => node.id))].sort();
    const facts = lines.filter(Boolean).join('\n');
    sections.push({ sectionId: stableId(repoId, 'doc_section', kind, key), kind, title,
      factsMarkdown: facts, fallbackMarkdown: `# ${title}\n\n${facts}\n`, factsHash: hash(JSON.stringify([facts, fingerprints])),
      entityIds: unique, scope });
  }
  function label(node) { return `${node.name} (${node.path}:${node.props.startLine || node.props.line || 1})`; }
  function related(node, types) {
    return edges.filter(edge => types.includes(edge.type) && (edge.from_id === node.id || edge.to_id === node.id))
      .map(edge => byId.get(edge.from_id === node.id ? edge.to_id : edge.from_id)).filter(Boolean);
  }

  const modules = new Map();
  for (const file of files) {
    const moduleName = file.path.includes('/') ? file.path.split('/')[0] : '(root)';
    if (!modules.has(moduleName)) modules.set(moduleName, []);
    modules.get(moduleName).push(file);
  }
  add('system_overview', 'project', 'Project overview', [
    `- Indexed files: ${files.length}; symbols: ${symbols.length}; API endpoints: ${endpoints.length}; data entities: ${entities.length}; tests: ${symbols.filter(node => node.kind === 'test').length}.`,
    `- Modules: ${[...modules.keys()].sort().join(', ') || 'none indexed'}.`,
    ...endpoints.map(node => `- API: ${label(node)}.`),
  ], [...files, ...endpoints], { type: 'project' });

  for (const [moduleName, moduleFiles] of [...modules].sort(([a], [b]) => a.localeCompare(b))) {
    const paths = new Set(moduleFiles.map(file => file.path));
    const members = symbols.filter(node => paths.has(node.path));
    const memberIds = new Set([...moduleFiles, ...members].map(node => node.id));
    const connections = edges.filter(edge => ['IMPORTS', 'CALLS', 'TESTS'].includes(edge.type) &&
      memberIds.has(edge.from_id) && byId.has(edge.to_id));
    const targets = connections.map(edge => byId.get(edge.to_id));
    add('module_purpose', moduleName, `Module: ${moduleName}`, [
      ...moduleFiles.map(file => `- File: ${file.path}.`),
      ...members.map(node => `- ${node.kind}: ${label(node)}.`),
      ...connections.map(edge => `- ${edge.type}: ${label(byId.get(edge.from_id))} -> ${label(byId.get(edge.to_id))}.`),
    ], [...moduleFiles, ...members, ...targets], { type: 'module', key: moduleName },
    members.map(node => node.props.bodyHash || ''));
  }

  for (const endpoint of endpoints) {
    const neighbors = related(endpoint, ['EXPOSES', 'CONSUMES']);
    const handlers = edges.filter(edge => edge.from_id === endpoint.id && edge.type === 'EXPOSES')
      .map(edge => byId.get(edge.to_id)).filter(Boolean);
    const handlerEdges = edges.filter(edge => handlers.some(handler => handler.id === edge.from_id) &&
      edge.type === 'CALLS' || handlers.some(handler => handler.id === edge.to_id) && edge.type === 'TESTS');
    const downstream = handlerEdges.flatMap(edge => [byId.get(edge.from_id), byId.get(edge.to_id)]).filter(Boolean);
    add('api_flow', `${endpoint.path}:${endpoint.name}`, `API: ${endpoint.name}`, [
      `- Route: ${label(endpoint)}.`,
      ...neighbors.map(node => `- Linked ${node.kind}: ${label(node)}.`),
      ...handlerEdges.map(edge => `- ${edge.type}: ${label(byId.get(edge.from_id))} -> ${label(byId.get(edge.to_id))}.`),
    ], [endpoint, ...neighbors, ...downstream], { type: 'endpoint', key: `${endpoint.path}:${endpoint.name}` },
    [...neighbors, ...downstream].map(node => node.props.bodyHash || ''));
  }

  for (const entity of entities) {
    const flowEdges = edges.filter(edge => ['READS_FROM', 'WRITES_TO', 'USES_MODEL', 'DEFINES'].includes(edge.type) &&
      (edge.from_id === entity.id || edge.to_id === entity.id) && byId.has(edge.from_id) && byId.has(edge.to_id));
    const neighbors = flowEdges.flatMap(edge => [byId.get(edge.from_id), byId.get(edge.to_id)])
      .filter(node => node.id !== entity.id);
    add('data_flow', `${entity.path}:${entity.name}`, `Data: ${entity.name}`, [
      `- Entity: ${label(entity)}.`,
      ...flowEdges.map(edge => `- ${edge.type}: ${label(byId.get(edge.from_id))} -> ${label(byId.get(edge.to_id))}.`),
    ], [entity, ...neighbors], { type: 'entity', key: `${entity.path}:${entity.name}` },
    neighbors.map(node => node.props.bodyHash || ''));
  }

  const setupFiles = files.filter(node => /(^|\/)(readme[^/]*\.md|package\.json|requirements[^/]*\.txt|pyproject\.toml|Makefile)$/i.test(node.path));
  const root = all(db, 'SELECT root_path FROM repositories WHERE id = ?', [repoId])[0]?.root_path;
  const setupLines = setupFiles.flatMap(node => {
    if (!root || !node.path.toLowerCase().endsWith('.md')) return [`- Setup source: ${node.path}.`];
    const source = readSource(root, node.path, node.props.contentHash);
    const lines = source.split('\n');
    const selected = [];
    let recording = false;
    for (const line of lines) {
      if (/^#{1,4}\s/.test(line)) recording = /setup|install|getting started|development|run|test/i.test(line);
      if (recording && selected.join('\n').length < 1800) selected.push(line);
    }
    const excerpt = selected.join('\n').trim() || source.slice(0, 800).trim();
    return [`- Setup source: ${node.path}.`, ...(excerpt ? [`\n### ${node.path}\n${excerpt}`] : [])];
  });
  add('setup', 'project', 'Setup', setupFiles.length
    ? setupLines : ['- No indexed setup instructions were found.'], setupFiles, { type: 'setup' },
  setupFiles.map(node => node.props.contentHash || ''));

  const byFile = new Map();
  for (const symbol of symbols.filter(node => node.kind !== 'document')) {
    if (!byFile.has(symbol.path)) byFile.set(symbol.path, []);
    byFile.get(symbol.path).push(symbol);
  }
  for (const [filePath, members] of [...byFile].sort(([a], [b]) => a.localeCompare(b))) {
    const file = files.find(node => node.path === filePath);
    add('symbol_summary', filePath, `Symbols: ${path.basename(filePath)}`, members.map(node =>
      `- ${node.kind}: ${label(node)}${node.props.signature ? `; ${node.props.signature}` : ''}.`),
    [...(file ? [file] : []), ...members], { type: 'file', key: filePath },
    members.map(node => node.props.bodyHash || ''));
  }
  return sections;
}

module.exports = { planDocs };
