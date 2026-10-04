'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { stableId, PARSER_VERSION } = require('./codeParser');
const CHUNK_VERSION = 2;
const hash = text => createHash('sha256').update(text).digest('hex');

function boundedText(text, bytes) {
  let size = 0;
  let result = '';
  for (const character of text) {
    size += Buffer.byteLength(character);
    if (size > bytes) break;
    result += character;
  }
  return result;
}

function terms(text) {
  return [...new Set(String(text).replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().match(/[a-z0-9_]+/g) || [])]
    .filter(term => term.length > 1).slice(0, 1024);
}

function readSource(root, relativePath, expectedHash) {
  const absolute = path.resolve(root, relativePath);
  if (!absolute.startsWith(root + path.sep) || !fs.realpathSync(absolute).startsWith(root + path.sep)) {
    throw new Error('Semantic source is outside repository.');
  }
  const descriptor = fs.openSync(absolute, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.size > 2 * 1024 * 1024) throw new Error('Semantic source is not an eligible file.');
    const bytes = fs.readFileSync(descriptor);
    if (hash(bytes) !== expectedHash) throw new Error(`Source changed since indexing: ${relativePath}`);
    return bytes.toString('utf8');
  } finally { fs.closeSync(descriptor); }
}

function chunksForFile(repoId, facts, source) {
  const lines = source.split('\n');
  const entities = [...facts.symbols, ...facts.routes.map(route => ({
    id: stableId(repoId, 'endpoint', facts.path, route.method, route.route), kind: 'endpoint',
    name: `${route.method} ${route.route}`, startLine: route.line, endLine: route.line,
    signature: `${route.method} ${route.route} ${route.handlerName || ''}`,
  }))];
  return entities.map(entity => {
    const body = entity.startIndex === undefined
      ? lines.slice(entity.startLine - 1, entity.endLine).join('\n') : source.slice(entity.startIndex, entity.endIndex);
    const calls = facts.calls.filter(call => call.callerId === entity.id).map(call => call.name);
    const humanName = entity.name.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/_/g, ' ');
    const modules = [...new Set(facts.imports.map(item => item.module))].join(' ');
    const children = facts.symbols.filter(item => item.parentId === entity.id).map(item => item.name).join(' ');
    const summary = `${entity.kind}: ${humanName}\nFile: ${facts.path}\nModules: ${boundedText(modules, 200)}\n${entity.signature || ''}\n${entity.docstring || ''}\nContains: ${children}\nCalls: ${calls.join(' ')}`;
    // Large symbols stay one entity. An explicitly marked summary avoids arbitrary overlapping windows.
    const summarized = Buffer.byteLength(`${summary}\n${body}`) > 1800;
    const text = summarized ? `${boundedText(summary, 800)}\n${boundedText(body, 700)}\n[body summarized]\n${boundedText(body.slice(-200), 200)}` : `${summary}\n${body}`;
    return { entityId: entity.id, repoId, path: facts.path, name: entity.name, kind: entity.kind,
      language: facts.language, startLine: entity.startLine, endLine: entity.endLine,
      contentHash: hash(`${PARSER_VERSION}:${CHUNK_VERSION}:${text}:${hash(body)}`), sourceHash: facts.contentHash,
      signature: entity.signature || '', bodyHash: entity.bodyHash || null, calls, externalNames: entity.externalNames || [],
      parseErrors: facts.parseErrors, summarized, terms: terms(text), text, chunkVersion: CHUNK_VERSION };
  });
}

function commitChunks(repoId, inventory) {
  const eligible = new Set(inventory.files.filter(file => !file.excludedReason).map(file => file.path));
  const paths = new Map(inventory.commitFiles);
  return inventory.commits.filter(commit => paths.get(commit.hash)?.some(file => eligible.has(file))).slice(0, 50)
    .map(commit => ({ entityId: stableId(repoId, 'commit', commit.hash), repoId,
      path: '', name: commit.subject, kind: 'commit', language: '', startLine: 0, endLine: 0,
      contentHash: hash(commit.subject), sourceHash: commit.hash, signature: '', bodyHash: null, calls: [],
      parseErrors: false, summarized: Buffer.byteLength(commit.subject) > 1800, terms: terms(commit.subject), text: boundedText(commit.subject, 1800),
      chunkVersion: CHUNK_VERSION, commitHash: commit.hash }));
}

module.exports = { CHUNK_VERSION, chunksForFile, commitChunks, readSource, terms, hash };
