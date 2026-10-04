'use strict';

const { execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);
const MAX_OUTPUT = 64 * 1024 * 1024;

function zeroList(buffer) {
  return buffer.toString('utf8').split('\0').filter(Boolean);
}

function parseNameStatus(buffer) {
  const parts = zeroList(buffer);
  const changes = [];
  for (let i = 0; i < parts.length;) {
    const status = parts[i++];
    const oldPath = /^[RC]/.test(status) ? parts[i++] : undefined;
    const filePath = parts[i++];
    changes.push({ status: status[0], path: filePath, ...(oldPath ? { oldPath, similarity: Number(status.slice(1)) } : {}) });
  }
  return changes;
}

function parseHunks(text) {
  return [...text.matchAll(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/gm)].map(match => ({
    oldStart: Number(match[1]), oldLines: match[2] === undefined ? 1 : Number(match[2]),
    newStart: Number(match[3]), newLines: match[4] === undefined ? 1 : Number(match[4]),
  }));
}

class GitService {
  constructor(rootPath) {
    this.rootPath = fs.realpathSync(rootPath);
  }

  async run(args, options = {}) {
    const env = { ...process.env };
    for (const name of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
      'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG_COUNT']) delete env[name];
    const { stdout } = await execFileAsync('git', ['--no-optional-locks', '--literal-pathspecs', '-C', this.rootPath, ...args], {
      encoding: options.binary ? 'buffer' : 'utf8', maxBuffer: MAX_OUTPUT, timeout: options.timeout || 30000,
      env: { ...env, GIT_OPTIONAL_LOCKS: '0', GIT_PAGER: 'cat', GIT_CONFIG_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0' },
    });
    return stdout;
  }

  async commit() {
    try { return (await this.run(['rev-parse', '--verify', 'HEAD^{commit}'])).trim(); }
    catch { return 'unborn'; }
  }

  async branch() {
    try { return (await this.run(['symbolic-ref', '--short', 'HEAD'])).trim(); }
    catch { return 'HEAD'; }
  }

  async resolveRevision(revision) {
    if (typeof revision !== 'string' || !revision || revision.length > 200 || revision.startsWith('-') || revision.includes('\0')) {
      throw new Error('Invalid Git revision.');
    }
    try { return (await this.run(['rev-parse', '--verify', `${revision}^{commit}`])).trim(); }
    catch { throw new Error(`Unknown Git revision: ${revision}`); }
  }

  async tracked() {
    return [...new Set(zeroList(await this.run(['ls-files', '-z', '--cached', '--', '.'], { binary: true })))];
  }

  async untracked() {
    return zeroList(await this.run(['ls-files', '-z', '--others', '--exclude-standard', '--', '.'], { binary: true }));
  }

  async dirtyPaths() {
    const parts = zeroList(await this.run(['status', '--porcelain=v1', '-z', '--untracked-files=all', '--', '.'], { binary: true }));
    const paths = new Set();
    for (let i = 0; i < parts.length; i++) {
      const entry = parts[i];
      paths.add(entry.slice(3));
      if (entry[0] === 'R' || entry[1] === 'R' || entry[0] === 'C' || entry[1] === 'C') paths.add(parts[++i]);
    }
    return paths;
  }

  async branches() {
    const output = await this.run(['for-each-ref', '--format=%(refname:short)%00%(objectname)', 'refs/heads', 'refs/remotes']);
    return output.trimEnd().split('\n').filter(Boolean).map(line => {
      const [name, commitHash] = line.split('\0');
      return { name, commitHash };
    });
  }

  async log(limit = 100) {
    if (await this.commit() === 'unborn') return [];
    const output = await this.run(['log', `-n${Math.min(Math.max(limit, 1), 500)}`, '--format=%H%x00%P%x00%aI%x00%s']);
    return output.trimEnd().split('\n').filter(Boolean).map(line => {
      const [hash, parents, authoredAt, subject] = line.split('\0');
      return { hash, parents: parents ? parents.split(' ') : [], authoredAt, subject };
    });
  }

  async history(filePath, limit = 50) {
    if (await this.commit() === 'unborn') return [];
    const output = await this.run(['log', '--follow', `-n${Math.min(Math.max(limit, 1), 200)}`,
      '--format=%H%x00%aI%x00%s', '--', filePath]);
    return output.trimEnd().split('\n').filter(Boolean).map(line => {
      const [hash, authoredAt, subject] = line.split('\0');
      return { hash, authoredAt, subject };
    });
  }

  async lastModified(paths) {
    if (!paths.length || await this.commit() === 'unborn') return new Map();
    const output = await this.run(['log', '--format=%x00%H%x00', '--name-only', '-z', '--relative', '--', '.'],
      { binary: true, timeout: 120000 });
    const wanted = new Set(paths);
    const result = new Map();
    let commit;
    for (const part of output.toString('utf8').split('\0')) {
      if (/^[a-f0-9]{40,64}$/.test(part)) { commit = part; continue; }
      const filePath = part.replace(/^\n/, '');
      if (commit && wanted.has(filePath) && !result.has(filePath)) result.set(filePath, commit);
      if (result.size === wanted.size) break;
    }
    return result;
  }

  async commitFiles(limit = 100) {
    if (await this.commit() === 'unborn') return [];
    const output = await this.run(['log', `-n${limit}`, '--format=%x00%H%x00', '--name-only', '-z', '--relative', '--', '.'],
      { binary: true, timeout: 120000 });
    const result = new Map();
    let commit;
    for (const part of output.toString('utf8').split('\0')) {
      if (/^[a-f0-9]{40,64}$/.test(part)) { commit = part; result.set(commit, []); continue; }
      const filePath = part.replace(/^\n/, '');
      if (commit && filePath) result.get(commit).push(filePath);
    }
    return [...result];
  }

  async changedFiles(from, to) {
    if (from === 'unborn') return [];
    const args = ['diff', '--name-status', '-z', '-M', '--relative', from];
    if (to && to !== 'WORKTREE') args.push(to);
    args.push('--', '.');
    return parseNameStatus(await this.run(args, { binary: true }));
  }

  async diffHunks(from, to, filePath, oldPath) {
    if (from === 'unborn') return [];
    const args = ['diff', '--unified=0', '--no-ext-diff', '-M', '--relative', from];
    if (to && to !== 'WORKTREE') args.push(to);
    args.push('--', oldPath || filePath);
    if (oldPath && oldPath !== filePath) args.push(filePath);
    return parseHunks(await this.run(args));
  }

  async show(revision, filePath) {
    if (typeof filePath !== 'string' || !filePath || filePath.includes('\0') ||
        path.isAbsolute(filePath) || path.normalize(filePath).startsWith('..')) throw new Error('Invalid repository-relative path.');
    const hash = await this.resolveRevision(revision);
    const root = (await this.run(['rev-parse', '--show-toplevel'])).trim();
    const gitPath = path.relative(root, path.join(this.rootPath, filePath)).split(path.sep).join('/');
    return this.run(['show', `${hash}:${gitPath}`]);
  }

  async blame(revision, filePath) {
    if (typeof filePath !== 'string' || !filePath || filePath.includes('\0') ||
        path.isAbsolute(filePath) || path.normalize(filePath).startsWith('..')) throw new Error('Invalid repository-relative path.');
    const hash = await this.resolveRevision(revision);
    return this.run(['blame', '--line-porcelain', hash, '--', filePath]);
  }

  async mergeBase(left, right) {
    return (await this.run(['merge-base', await this.resolveRevision(left), await this.resolveRevision(right)])).trim();
  }

  async worktrees() {
    const output = await this.run(['worktree', 'list', '--porcelain']);
    return output.trim().split(/\n\n/).filter(Boolean).map(block => Object.fromEntries(
      block.split('\n').map(line => { const index = line.indexOf(' '); return index < 0 ? [line, true] : [line.slice(0, index), line.slice(index + 1)]; }),
    ));
  }
}

module.exports = { GitService, parseNameStatus, parseHunks };
