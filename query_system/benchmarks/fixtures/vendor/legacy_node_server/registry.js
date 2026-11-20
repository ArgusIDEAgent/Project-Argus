'use strict';

const { createHash, randomBytes } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const initSqlJs = require('sql.js');
const { GitService } = require('./gitService');
const graphStore = require('./graphStore');
const semanticStore = require('./semanticStore');
const docStore = require('./docStore');

const DEFAULT_EXCLUSIONS = ['.git', 'node_modules', 'build', 'dist', '.venv', 'venv', '__pycache__', 'secrets', '.secrets'];
const SECRET_NAMES = ['.env', '.env.*', '*.pem', '*.key', 'id_rsa', 'id_ed25519'];
const LANGUAGES = { '.js': 'javascript', '.jsx': 'javascript', '.ts': 'typescript', '.tsx': 'typescript', '.py': 'python', '.md': 'markdown' };
const SCHEMA_VERSION = 5;
const MAX_FILE_BYTES = 2 * 1024 * 1024;

function queryOne(db, sql, params = []) {
  const statement = db.prepare(sql);
  try {
    statement.bind(params);
    return statement.step() ? statement.getAsObject() : undefined;
  } finally {
    statement.free();
  }
}

function queryAll(db, sql, params = []) {
  const statement = db.prepare(sql);
  try {
    statement.bind(params);
    const rows = [];
    while (statement.step()) rows.push(statement.getAsObject());
    return rows;
  } finally {
    statement.free();
  }
}

function run(db, sql, params = []) {
  db.run(sql, params);
}

function publicRepository(row) {
  if (!row) return undefined;
  return {
    id: row.id,
    rootPath: row.root_path,
    defaultBranch: row.default_branch,
    createdAt: row.created_at,
    config: JSON.parse(row.config_json),
  };
}

function matchesPattern(name, pattern) {
  if (pattern.startsWith('*.')) return name.endsWith(pattern.slice(1));
  if (pattern.endsWith('.*')) return name === pattern.slice(0, -2) || name.startsWith(pattern.slice(0, -1));
  return name === pattern;
}

function shouldExclude(relativePath, config) {
  const segments = relativePath.split('/');
  const exclusions = [...DEFAULT_EXCLUSIONS, ...config.excludedPaths];
  if (exclusions.some(exclusion => exclusion.includes('/')
    ? relativePath === exclusion || relativePath.startsWith(`${exclusion}/`)
    : segments.includes(exclusion))) return true;
  if (config.secretPatterns.some(pattern => matchesPattern(segments.at(-1), pattern))) return true;
  return false;
}

function exclusionReason(relativePath, config, language, stat) {
  if (shouldExclude(relativePath, config)) return 'excluded_path';
  if (!language || (language !== 'markdown' && !config.supportedLanguages.includes(language))) return 'unsupported_language';
  if (!stat) return 'missing';
  if (!stat.isFile()) return 'not_regular_file';
  if (stat.size > MAX_FILE_BYTES) return 'too_large';
  return null;
}

function readSource(absolutePath) {
  const descriptor = fs.openSync(absolutePath, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.size > MAX_FILE_BYTES) throw new Error('File changed during scan.');
    return fs.readFileSync(descriptor);
  } finally { fs.closeSync(descriptor); }
}

function inventoryRow(row) {
  return { path: row.path, language: row.language, sizeBytes: row.size_bytes,
    contentHash: row.content_hash, commitHash: row.commit_hash,
    lastModifiedCommit: row.last_modified_commit, excludedReason: row.excluded_reason,
    tracked: Boolean(row.tracked), dirty: Boolean(row.dirty),
    mtimeMs: row.mtime_ms, ctimeMs: row.ctime_ms };
}

function repoConfig(input = {}) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Invalid repository config.');
  const fields = ['allowedBuildCommands', 'allowedTestCommands', 'excludedPaths', 'secretPatterns', 'supportedLanguages', 'frameworkHints'];
  const result = {
    allowedBuildCommands: [],
    allowedTestCommands: [],
    excludedPaths: [],
    secretPatterns: SECRET_NAMES,
    supportedLanguages: ['javascript', 'typescript', 'python'],
    frameworkHints: [],
  };
  for (const field of fields) {
    if (!(field in input)) continue;
    if (!Array.isArray(input[field]) || input[field].length > 100 ||
        input[field].some(value => typeof value !== 'string' || !value || value.length > 256)) {
      throw new Error(`Invalid ${field}.`);
    }
    result[field] = input[field];
  }
  result.secretPatterns = [...new Set([...SECRET_NAMES, ...result.secretPatterns])];
  return result;
}

class Registry {
  constructor(dataDir, SQL) {
    this.dataDir = dataDir;
    this.dbPath = path.join(dataDir, 'metadata.sqlite');
    this.lockPath = `${this.dbPath}.lock`;
    this.SQL = SQL;
    fs.mkdirSync(dataDir, { recursive: true, mode: 0o700 });
    fs.mkdirSync(path.join(dataDir, 'indexes'), { recursive: true, mode: 0o700 });
    fs.mkdirSync(path.join(dataDir, 'worktrees'), { recursive: true, mode: 0o700 });
  }

  static async open(dataDir = process.env.CODEMIND_DATA_DIR || path.join(os.homedir(), '.codemind')) {
    const SQL = await initSqlJs({ locateFile: file => require.resolve(`sql.js/dist/${file}`) });
    const registry = new Registry(dataDir, SQL);
    await registry.withDb(db => {
      run(db, `UPDATE jobs SET state = 'failed', error = 'Interrupted by service restart.', updated_at = ?
        WHERE state IN ('queued', 'running')`, [new Date().toISOString()]);
    }, true);
    return registry;
  }

  async withDb(action, save = false) {
    const lock = await this.acquireLock();
    let db;
    try {
      db = fs.existsSync(this.dbPath)
        ? new this.SQL.Database(fs.readFileSync(this.dbPath))
        : new this.SQL.Database();
      const version = queryOne(db, 'PRAGMA user_version').user_version;
      if (version > SCHEMA_VERSION) throw new Error(`Unsupported metadata schema version ${version}.`);
      db.run(`PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS repositories (
          id TEXT PRIMARY KEY, root_path TEXT UNIQUE NOT NULL, default_branch TEXT NOT NULL,
          created_at TEXT NOT NULL, config_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repo_settings (repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE, config_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
          state TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          result_json TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS index_versions (
          repo_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
          revision TEXT NOT NULL, indexed_at TEXT NOT NULL, file_count INTEGER NOT NULL, total_bytes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id TEXT NOT NULL, job_id TEXT,
          event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, detail_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
          repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
          path TEXT NOT NULL, language TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          PRIMARY KEY(repo_id, path)
        );
        CREATE TABLE IF NOT EXISTS git_branches (
          repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
          name TEXT NOT NULL, commit_hash TEXT NOT NULL, PRIMARY KEY(repo_id, name)
        );
        CREATE TABLE IF NOT EXISTS git_commits (
          repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
          hash TEXT NOT NULL, parents_json TEXT NOT NULL, authored_at TEXT NOT NULL,
          subject TEXT NOT NULL, PRIMARY KEY(repo_id, hash)
        );
        CREATE TABLE IF NOT EXISTS git_commit_files (
          repo_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
          commit_hash TEXT NOT NULL, path TEXT NOT NULL,
          PRIMARY KEY(repo_id, commit_hash, path)
        );`);
      if (version < 2) {
        db.run(`ALTER TABLE files ADD COLUMN content_hash TEXT;
          ALTER TABLE files ADD COLUMN commit_hash TEXT;
          ALTER TABLE files ADD COLUMN last_modified_commit TEXT;
          ALTER TABLE files ADD COLUMN excluded_reason TEXT;
          ALTER TABLE files ADD COLUMN tracked INTEGER NOT NULL DEFAULT 1;
          ALTER TABLE files ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0;
          ALTER TABLE files ADD COLUMN mtime_ms REAL;
          ALTER TABLE files ADD COLUMN ctime_ms REAL;
          ALTER TABLE index_versions ADD COLUMN commit_hash TEXT;
          ALTER TABLE index_versions ADD COLUMN dirty_fingerprint TEXT;
          ALTER TABLE index_versions ADD COLUMN branch TEXT;
          PRAGMA user_version = 2;`);
      }
      db.run(graphStore.SCHEMA);
      db.run(semanticStore.SCHEMA);
      db.run(docStore.SCHEMA);
      if (version < 5) db.run('PRAGMA user_version = 5');
      const result = action(db);
      if (save) {
        const temporary = `${this.dbPath}.${process.pid}.${randomBytes(6).toString('hex')}.tmp`;
        try {
          fs.writeFileSync(temporary, Buffer.from(db.export()), { mode: 0o600 });
          fs.renameSync(temporary, this.dbPath);
        } finally {
          if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
        }
      }
      return result;
    } finally {
      db?.close();
      await lock.close();
      fs.unlinkSync(this.lockPath);
    }
  }

  async acquireLock() {
    const deadline = Date.now() + 5000;
    while (true) {
      try {
        const handle = await fs.promises.open(this.lockPath, 'wx', 0o600);
        await handle.writeFile(String(process.pid));
        return handle;
      } catch (error) {
        if (error.code !== 'EEXIST') throw error;
        try {
          const owner = Number(fs.readFileSync(this.lockPath, 'utf8'));
          if (Number.isInteger(owner) && owner > 0) {
            try { process.kill(owner, 0); }
            catch (ownerError) { if (ownerError.code === 'ESRCH') fs.unlinkSync(this.lockPath); }
          }
        } catch (statError) {
          if (statError.code !== 'ENOENT') throw statError;
        }
        if (Date.now() > deadline) throw new Error('Repository metadata is busy.');
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    }
  }

  async register(rootPath, configInput) {
    if (typeof rootPath !== 'string' || !path.isAbsolute(rootPath)) throw new Error('An absolute repository path is required.');
    const source = fs.realpathSync(rootPath);
    if (!fs.statSync(source).isDirectory()) throw new Error('Repository path must be a directory.');
    await new GitService(source).run(['rev-parse', '--show-toplevel'], { timeout: 5000 });
    const root = source;
    const config = repoConfig(configInput);
    let branch = 'HEAD';
    branch = await new GitService(root).branch();
    const id = createHash('sha256').update(root).digest('hex').slice(0, 24);
    return this.withDb(db => {
      const existing = queryOne(db, 'SELECT * FROM repositories WHERE id = ?', [id]);
      if (existing) return publicRepository(existing);
      const now = new Date().toISOString();
      run(db, 'INSERT INTO repositories VALUES (?, ?, ?, ?, ?)', [id, root, branch, now, JSON.stringify(config)]);
      run(db, 'INSERT INTO repo_settings VALUES (?, ?)', [id, JSON.stringify(config)]);
      run(db, 'INSERT INTO audit_events (repo_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?)',
        [id, 'REPO_REGISTERED', now, '{}']);
      return publicRepository(queryOne(db, 'SELECT * FROM repositories WHERE id = ?', [id]));
    }, true);
  }

  async list() {
    return this.withDb(db => queryAll(db, 'SELECT * FROM repositories ORDER BY created_at DESC').map(publicRepository));
  }

  async get(id) {
    return this.withDb(db => publicRepository(queryOne(db, 'SELECT * FROM repositories WHERE id = ?', [id])));
  }

  async status(id) {
    return this.withDb(db => {
      const repository = publicRepository(queryOne(db, 'SELECT * FROM repositories WHERE id = ?', [id]));
      if (!repository) return undefined;
      const version = queryOne(db, 'SELECT * FROM index_versions WHERE repo_id = ?', [id]);
      const latestJob = queryOne(db, 'SELECT id, state, kind, updated_at FROM jobs WHERE repo_id = ? ORDER BY created_at DESC LIMIT 1', [id]);
      return {
        repository,
        index: version ? { revision: version.revision, indexedAt: version.indexed_at,
          commitHash: version.commit_hash, dirtyFingerprint: version.dirty_fingerprint,
          branch: version.branch, fileCount: version.file_count, totalBytes: version.total_bytes } : null,
        graph: graphStore.graphVersion(db, id),
        semantic: semanticStore.status(db, id),
        docs: docStore.status(db, id),
        latestJob: latestJob || null,
      };
    });
  }

  async liveState(id) {
    const repository = await this.get(id);
    if (!repository) return undefined;
    const git = new GitService(repository.rootPath);
    const [commitHash, branch] = await Promise.all([git.commit(), git.branch()]);
    return { commitHash, branch };
  }

  async files(id, includeExcluded = false) {
    return this.withDb(db => {
      if (!queryOne(db, 'SELECT id FROM repositories WHERE id = ?', [id])) return undefined;
      return queryAll(db, `SELECT * FROM files WHERE repo_id = ? ${includeExcluded ? '' : 'AND excluded_reason IS NULL'} ORDER BY path`, [id])
        .map(inventoryRow);
    });
  }

  async snapshot(id) {
    return this.withDb(db => {
      const version = queryOne(db, 'SELECT * FROM index_versions WHERE repo_id = ?', [id]);
      const files = queryAll(db, 'SELECT * FROM files WHERE repo_id = ?', [id]).map(inventoryRow);
      return { version, files: new Map(files.map(file => [file.path, file])) };
    });
  }

  async remove(id) {
    return this.withDb(db => {
      if (!queryOne(db, 'SELECT id FROM repositories WHERE id = ?', [id])) return false;
      run(db, 'DELETE FROM audit_events WHERE repo_id = ?', [id]);
      run(db, 'DELETE FROM repositories WHERE id = ?', [id]);
      return true;
    }, true);
  }

  async createJob(repoId, kind = 'refresh') {
    return this.withDb(db => {
      if (!queryOne(db, 'SELECT id FROM repositories WHERE id = ?', [repoId])) return undefined;
      const active = queryOne(db, "SELECT id, kind FROM jobs WHERE repo_id = ? AND state IN ('queued', 'running')", [repoId]);
      if (active) {
        if (active.kind === 'docs_sync') throw new Error('Repository job is already running.');
        return active.id;
      }
      const id = randomBytes(16).toString('hex');
      const now = new Date().toISOString();
      run(db, 'INSERT INTO jobs (id, repo_id, state, kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        [id, repoId, 'queued', kind, now, now]);
      return id;
    }, true);
  }

  async updateJob(id, state, result, error) {
    await this.withDb(db => {
      const job = queryOne(db, 'SELECT repo_id FROM jobs WHERE id = ?', [id]);
      if (!job) return;
      const now = new Date().toISOString();
      const affectedDocs = state === 'completed'
        ? docStore.markAffected(db, job.repo_id, result.changes, result.revision, result.dirtyFingerprint) : [];
      const summary = result && { revision: result.revision, commitHash: result.commitHash,
        dirtyFingerprint: result.dirtyFingerprint, branch: result.branch,
        fileCount: result.fileCount, totalBytes: result.totalBytes,
        scannedCount: result.scannedCount, changedCount: result.changes.length, changes: result.changes,
        graph: state === 'completed' ? graphStore.applyGraph(db, job.repo_id, result.revision, result.graph) : null };
      if (state === 'completed' && result?.semantic) {
        summary.semantic = semanticStore.applySemantic(db, job.repo_id, result.revision, result.semantic);
        for (const sectionId of affectedDocs) db.run('DELETE FROM semantic_chunks WHERE entity_id = ?', [sectionId]);
        if (affectedDocs.length) run(db, `UPDATE semantic_versions SET
          chunk_count = (SELECT count(*) FROM semantic_chunks WHERE repo_id = ?),
          embedded_count = (SELECT count(*) FROM semantic_embeddings e JOIN semantic_chunks c ON c.entity_id = e.entity_id WHERE c.repo_id = ?)
          WHERE repo_id = ?`, [job.repo_id, job.repo_id, job.repo_id]);
        summary.graph.nodeCount = queryOne(db, 'SELECT count(*) AS count FROM graph_nodes WHERE repo_id = ?', [job.repo_id]).count;
        run(db, 'UPDATE graph_versions SET node_count = ? WHERE repo_id = ?', [summary.graph.nodeCount, job.repo_id]);
      } else if (state === 'completed') {
        run(db, 'DELETE FROM semantic_versions WHERE repo_id = ?', [job.repo_id]);
      }
      if (state === 'completed') {
        run(db, `INSERT OR REPLACE INTO index_versions
          (repo_id, revision, indexed_at, file_count, total_bytes, commit_hash, dirty_fingerprint, branch)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
        [job.repo_id, result.revision, now, result.fileCount, result.totalBytes,
          result.commitHash, result.dirtyFingerprint, result.branch]);
        const statement = db.prepare(`INSERT OR REPLACE INTO files
          (repo_id, path, language, size_bytes, content_hash, commit_hash, last_modified_commit, excluded_reason, tracked, dirty, mtime_ms, ctime_ms)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
        try {
          for (const file of result.upserts) statement.run([job.repo_id, file.path, file.language,
            file.sizeBytes, file.contentHash, file.commitHash, file.lastModifiedCommit,
            file.excludedReason, Number(file.tracked), Number(file.dirty), file.mtimeMs, file.ctimeMs]);
        } finally { statement.free(); }
        for (const filePath of result.deletes) run(db, 'DELETE FROM files WHERE repo_id = ? AND path = ?', [job.repo_id, filePath]);
        run(db, 'DELETE FROM git_branches WHERE repo_id = ?', [job.repo_id]);
        for (const branch of result.branches) run(db, 'INSERT INTO git_branches VALUES (?, ?, ?)',
          [job.repo_id, branch.name, branch.commitHash]);
        for (const commit of result.commits) run(db, 'INSERT OR REPLACE INTO git_commits VALUES (?, ?, ?, ?, ?)',
          [job.repo_id, commit.hash, JSON.stringify(commit.parents), commit.authoredAt, commit.subject]);
        for (const [hash, paths] of result.commitFiles) {
          for (const filePath of paths) run(db, 'INSERT OR IGNORE INTO git_commit_files VALUES (?, ?, ?)',
            [job.repo_id, hash, filePath]);
        }
        summary.docs = docStore.status(db, job.repo_id);
      }
      run(db, 'UPDATE jobs SET state = ?, updated_at = ?, result_json = ?, error = ? WHERE id = ?',
        [state, now, summary ? JSON.stringify(summary) : null, error || null, id]);
      const event = state === 'running' ? 'INDEX_STARTED' : state === 'completed' ? 'INDEX_DONE' : 'INDEX_FAILED';
      run(db, 'INSERT INTO audit_events (repo_id, job_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?, ?)',
        [job.repo_id, id, event, now, JSON.stringify(summary || (error ? { error } : {}))]);
    }, true);
  }

  async job(id) {
    return this.withDb(db => {
      const row = queryOne(db, 'SELECT * FROM jobs WHERE id = ?', [id]);
      if (!row) return undefined;
      return { id: row.id, repoId: row.repo_id, kind: row.kind, state: row.state,
        createdAt: row.created_at, updatedAt: row.updated_at,
        result: row.result_json ? JSON.parse(row.result_json) : null, error: row.error || null };
    });
  }

  async graphSymbol(id) { return this.withDb(db => graphStore.graphSymbol(db, id)); }
  async graphImpact(id, depth) { return this.withDb(db => graphStore.graphImpact(db, id, depth)); }
  async graphPath(fromId, toId, depth) { return this.withDb(db => graphStore.graphPath(db, fromId, toId, depth)); }
  async graphFile(repoId, filePath) {
    return this.withDb(db => graphStore.graphFile(db, repoId, filePath));
  }

  async docsOverview(repoId) { return this.withDb(db => docStore.overview(db, repoId)); }
  async docSection(repoId, sectionId) { return this.withDb(db => docStore.section(db, repoId, sectionId)); }
  async docSections(repoId) { return this.withDb(db => {
    if (!queryOne(db, 'SELECT id FROM repositories WHERE id = ?', [repoId])) return undefined;
    return { repoId, sections: docStore.list(db, repoId), status: docStore.status(db, repoId) };
  }); }
  async staleDocs(repoId) { return this.withDb(db => {
    if (!queryOne(db, 'SELECT id FROM repositories WHERE id = ?', [repoId])) return undefined;
    return { repoId, sections: docStore.stale(db, repoId), status: docStore.status(db, repoId) };
  }); }

  async syncDocs(repoId, mode, modelProvider, embeddingProvider) {
    const jobId = await this.withDb(db => {
      if (!queryOne(db, 'SELECT id FROM repositories WHERE id = ?', [repoId])) throw new Error('Repository not found.');
      if (queryOne(db, "SELECT id FROM jobs WHERE repo_id = ? AND state IN ('queued', 'running')", [repoId])) {
        throw new Error('Repository job is already running.');
      }
      const id = randomBytes(16).toString('hex');
      const now = new Date().toISOString();
      run(db, 'INSERT INTO jobs (id, repo_id, state, kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
        [id, repoId, 'running', 'docs_sync', now, now]);
      run(db, 'INSERT INTO audit_events (repo_id, job_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?, ?)',
        [repoId, id, 'DOCS_SYNC_STARTED', now, JSON.stringify({ mode })]);
      return id;
    }, true);
    try {
      const result = await docStore.syncDocs(this, repoId, mode, modelProvider, embeddingProvider);
      await this.withDb(db => {
        const now = new Date().toISOString();
        run(db, "UPDATE jobs SET state = 'completed', updated_at = ?, result_json = ? WHERE id = ?",
          [now, JSON.stringify({ mode, updated: result.updated, affected: result.affected, status: result.status }), jobId]);
        run(db, 'INSERT INTO audit_events (repo_id, job_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?, ?)',
          [repoId, jobId, 'DOCS_SYNC_DONE', now, JSON.stringify({ updated: result.updated, affected: result.affected })]);
      }, true);
      return { ...result, jobId };
    } catch (error) {
      await this.withDb(db => {
        const now = new Date().toISOString();
        run(db, "UPDATE jobs SET state = 'failed', updated_at = ?, error = ? WHERE id = ?", [now, error.message, jobId]);
        run(db, 'INSERT INTO audit_events (repo_id, job_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?, ?)',
          [repoId, jobId, 'DOCS_SYNC_FAILED', now, JSON.stringify({ error: error.message })]);
      }, true);
      throw error;
    }
  }

  async changeSet(git, previous, current, commitHash, commitChanges) {
    const oldFiles = new Map([...previous.files].filter(([, file]) => !file.excludedReason));
    const newFiles = new Map([...current].filter(([, file]) => !file.excludedReason && file.contentHash));
    const removed = new Set([...oldFiles.keys()].filter(filePath => !newFiles.has(filePath)));
    const added = new Set([...newFiles.keys()].filter(filePath => !oldFiles.has(filePath)));
    const renames = [];
    const baseline = previous.version?.commit_hash;
    const hints = baseline && baseline !== 'unborn' ? await git.changedFiles(baseline, 'WORKTREE') : commitChanges;
    for (const hint of hints) {
      if (hint.status === 'R' && removed.has(hint.oldPath) && added.has(hint.path)) {
        renames.push({ status: 'renamed', path: hint.path, oldPath: hint.oldPath, similarity: hint.similarity });
        removed.delete(hint.oldPath);
        added.delete(hint.path);
      }
    }
    for (const oldPath of [...removed]) {
      const old = oldFiles.get(oldPath);
      const match = [...added].find(filePath => old.contentHash && old.contentHash === newFiles.get(filePath).contentHash);
      if (match) {
        renames.push({ status: 'renamed', path: match, oldPath, similarity: 100 });
        removed.delete(oldPath);
        added.delete(match);
      }
    }
    const changes = [
      ...renames,
      ...[...added].map(filePath => ({ status: 'added', path: filePath })),
      ...[...removed].map(filePath => ({ status: 'deleted', path: filePath })),
      ...[...newFiles].filter(([filePath, file]) => oldFiles.has(filePath) &&
        oldFiles.get(filePath).contentHash !== file.contentHash)
        .map(([filePath]) => ({ status: 'modified', path: filePath })),
    ];
    for (const change of changes) {
      const file = newFiles.get(change.path);
      change.hunks = baseline && baseline !== 'unborn'
        ? await git.diffHunks(baseline, 'WORKTREE', change.path, change.oldPath) : [];
      if (!change.hunks.length && change.status === 'added' && file) {
        const absolute = path.join(git.rootPath, change.path);
        const source = readSource(absolute).toString('utf8');
        const lineCount = source.split('\n').length - (source.endsWith('\n') ? 1 : 0);
        change.hunks = [{ oldStart: 0, oldLines: 0, newStart: 1, newLines: lineCount }];
      }
    }
    return changes.sort((left, right) => left.path.localeCompare(right.path));
  }

  async changes(repoId, from, to) {
    const repository = await this.get(repoId);
    if (!repository) return undefined;
    const git = new GitService(repository.rootPath);
    const snapshot = await this.snapshot(repoId);
    const fromHash = from ? await git.resolveRevision(from) : snapshot.version?.commit_hash || await git.commit();
    const toHash = to ? await git.resolveRevision(to) : 'WORKTREE';
    const changes = fromHash === 'unborn'
      ? toHash === 'WORKTREE' ? [...new Set([...await git.tracked(), ...await git.untracked()])]
        .map(filePath => ({ status: 'A', path: filePath, untracked: true })) : []
      : await git.changedFiles(fromHash, toHash);
    if (toHash === 'WORKTREE' && fromHash !== 'unborn') {
      for (const filePath of await git.untracked()) changes.push({ status: 'A', path: filePath, untracked: true });
    }
    const result = [];
    for (const change of changes) {
      const language = LANGUAGES[path.extname(change.path).toLowerCase()] || 'unknown';
      let stat;
      try { stat = fs.lstatSync(path.join(repository.rootPath, change.path)); } catch { /* Deleted file. */ }
      const excludedReason = exclusionReason(change.path, repository.config, language, stat);
      let hunks = excludedReason && excludedReason !== 'missing' ? [] :
        await git.diffHunks(fromHash, toHash, change.path, change.oldPath);
      if (change.untracked && !excludedReason) {
        const source = readSource(path.join(repository.rootPath, change.path)).toString('utf8');
        const lineCount = source.split('\n').length - (source.endsWith('\n') ? 1 : 0);
        hunks = [{ oldStart: 0, oldLines: 0, newStart: 1, newLines: lineCount }];
      }
      result.push({ status: { A: 'added', M: 'modified', D: 'deleted', R: 'renamed', C: 'copied' }[change.status] || change.status,
        path: change.path, ...(change.oldPath ? { oldPath: change.oldPath, similarity: change.similarity } : {}),
        excludedReason: excludedReason === 'missing' ? null : excludedReason,
        hunks });
    }
    return { from: fromHash, to: toHash, changes: result };
  }

  async history(repoId, filePath) {
    const repository = await this.get(repoId);
    if (!repository) return undefined;
    if (typeof filePath !== 'string' || !filePath || filePath.includes('\0') || path.isAbsolute(filePath) ||
        path.normalize(filePath).startsWith('..') || filePath.length > 4096) throw new Error('Invalid repository-relative path.');
    return { path: filePath, commits: await new GitService(repository.rootPath).history(filePath) };
  }

  async scan(repoId) {
    const repository = await this.get(repoId);
    if (!repository) throw new Error('Repository not found.');
    const git = new GitService(repository.rootPath);
    const previous = await this.snapshot(repoId);
    const [commitHash, branch, trackedPaths, untrackedPaths, dirtyPaths, branches, commits, commitFiles] = await Promise.all([
      git.commit(), git.branch(), git.tracked(), git.untracked(), git.dirtyPaths(), git.branches(), git.log(), git.commitFiles(),
    ]);
    const tracked = new Set(trackedPaths);
    const paths = new Set([...trackedPaths, ...untrackedPaths]);
    const changedByCommit = previous.version?.commit_hash && previous.version.commit_hash !== commitHash &&
      previous.version.commit_hash !== 'unborn'
      ? await git.changedFiles(previous.version.commit_hash, commitHash) : [];
    const candidates = new Set([...dirtyPaths, ...changedByCommit.flatMap(change => [change.path, change.oldPath].filter(Boolean))]);
    const committedChanged = new Set(changedByCommit.flatMap(change => [change.path, change.oldPath].filter(Boolean)));
    const lastModified = await git.lastModified([...paths].filter(filePath => tracked.has(filePath) &&
      (!previous.files.has(filePath) || committedChanged.has(filePath))));
    const current = new Map();
    const upserts = [];
    let scannedCount = 0;
    for (const relativePath of paths) {
      const absolute = path.resolve(repository.rootPath, relativePath);
      if (!absolute.startsWith(repository.rootPath + path.sep)) continue;
      let stat;
      try { stat = fs.lstatSync(absolute); } catch { /* Deleted tracked file. */ }
      const language = LANGUAGES[path.extname(relativePath).toLowerCase()] || 'unknown';
      let excludedReason = exclusionReason(relativePath, repository.config, language, stat);
      const old = previous.files.get(relativePath);
      const dirty = dirtyPaths.has(relativePath);
      const needsRead = !excludedReason && (!old || !old.contentHash || committedChanged.has(relativePath) ||
        (candidates.has(relativePath) && (old.mtimeMs !== stat.mtimeMs || old.ctimeMs !== stat.ctimeMs ||
          old.sizeBytes !== stat.size)));
      let contentHash = excludedReason ? null : old?.contentHash || null;
      if (needsRead) {
        try {
          contentHash = createHash('sha256').update(readSource(absolute)).digest('hex');
          scannedCount++;
        } catch { contentHash = null; excludedReason = 'unreadable'; }
      }
      const file = { path: relativePath, language, sizeBytes: stat?.size || 0, contentHash,
        commitHash: tracked.has(relativePath) && commitHash !== 'unborn' ? commitHash : null,
        lastModifiedCommit: tracked.has(relativePath) ? (lastModified.get(relativePath) || old?.lastModifiedCommit || null) : null,
        excludedReason, tracked: tracked.has(relativePath), dirty,
        mtimeMs: stat?.mtimeMs ?? null, ctimeMs: stat?.ctimeMs ?? null };
      current.set(relativePath, file);
      if (!old || Object.keys(file).some(key => file[key] !== old[key])) upserts.push(file);
    }
    const deletes = [...previous.files.keys()].filter(filePath => !current.has(filePath));
    const changes = await this.changeSet(git, previous, current, commitHash, changedByCommit);
    const dirtyEntries = [...dirtyPaths].sort().map(filePath => {
      const file = current.get(filePath);
      if (file?.contentHash) return [filePath, file.contentHash];
      if (file) return [filePath, file.excludedReason, file.sizeBytes];
      return [filePath, 'deleted'];
    });
    const dirtyFingerprint = dirtyEntries.length
      ? createHash('sha256').update(JSON.stringify(dirtyEntries)).digest('hex') : 'clean';
    const included = [...current.values()].filter(file => !file.excludedReason && file.contentHash);
    const totalBytes = included.reduce((sum, file) => sum + file.sizeBytes, 0);
    return { revision: `${commitHash}:${dirtyFingerprint}`, commitHash, dirtyFingerprint, branch,
      fileCount: included.length, totalBytes, scannedCount, changes, upserts, deletes,
      branches, commits, commitFiles, files: [...current.values()] };
  }
}

module.exports = { Registry, SCHEMA_VERSION };
