'use strict';

const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { parseCode, PARSER_VERSION } = require('./codeParser');
const { all } = require('./graphStore');

async function prepareGraph(registry, repoId, inventory) {
  const repository = await registry.get(repoId);
  if (!repository) throw new Error('Repository not found.');
  const indexed = await registry.withDb(db => new Map(all(db,
    'SELECT path, content_hash, parser_version FROM graph_files WHERE repo_id = ?', [repoId])
    .map(row => [row.path, row])));
  const eligible = new Map(inventory.files.filter(file => !file.excludedReason && file.contentHash)
    .map(file => [file.path, file]));
  const deletes = [...indexed.keys()].filter(filePath => !eligible.has(filePath));
  const parsed = [];
  for (const file of eligible.values()) {
    if (indexed.get(file.path)?.content_hash === file.contentHash &&
        indexed.get(file.path)?.parser_version === PARSER_VERSION) continue;
    const absolute = path.resolve(repository.rootPath, file.path);
    if (!absolute.startsWith(repository.rootPath + path.sep)) throw new Error('Graph file is outside repository.');
    const descriptor = fs.openSync(absolute, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    let source;
    try {
      const stat = fs.fstatSync(descriptor);
      if (!stat.isFile() || stat.size > 2 * 1024 * 1024) throw new Error(`Graph source changed during scan: ${file.path}`);
      source = fs.readFileSync(descriptor);
    } finally { fs.closeSync(descriptor); }
    const actualHash = createHash('sha256').update(source).digest('hex');
    if (actualHash !== file.contentHash) throw new Error(`Graph source changed during scan: ${file.path}`);
    parsed.push(await parseCode({ repoId, filePath: file.path, language: file.language,
      source: source.toString('utf8'), contentHash: file.contentHash, revision: inventory.revision }));
  }
  return { parsed, deletes };
}

module.exports = { prepareGraph };
