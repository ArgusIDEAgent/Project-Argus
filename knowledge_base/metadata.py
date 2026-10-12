"""Phase 4: versioned identities and source provenance, alongside legacy tables.

SQLite publication is transactional. Vector publication is not a distributed
transaction: only a ready snapshot with matching payload IDs is retrievable.
Source text is not copied into this database. All locations describe saved disk
content, never an unsaved editor buffer.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from knowledge_base.identity import entity_id, file_id, relative_path, repository_id

SCHEMA_VERSION = 6


def ensure_schema(db: sqlite3.Connection) -> None:
    """Idempotent v2-v4 migration; caller must enable foreign keys first."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION or db.execute("SELECT 1 FROM repositories WHERE schema_version>?", (SCHEMA_VERSION,)).fetchone():
        raise ValueError("Metadata schema is newer than this engine")
    # The scanner owns the repository/file base schema. These definitions stay
    # column-compatible with the parser and graph's existing INSERT statements.
    db.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, relative_path TEXT NOT NULL,
            identity_key TEXT NOT NULL, parent_symbol_id TEXT, kind TEXT NOT NULL,
            name TEXT NOT NULL, qualified_name TEXT NOT NULL, parameters_json TEXT NOT NULL,
            return_annotation TEXT, modifiers_json TEXT NOT NULL, exported INTEGER NOT NULL,
            default_export INTEGER NOT NULL, doc_comment TEXT, start_line INTEGER NOT NULL,
            start_column INTEGER NOT NULL, end_line INTEGER NOT NULL, end_column INTEGER NOT NULL,
            start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL,
            FOREIGN KEY(repository_id, relative_path) REFERENCES files(repository_id, relative_path) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, kind TEXT NOT NULL,
            label TEXT NOT NULL, relative_path TEXT, symbol_id TEXT, metadata_json TEXT NOT NULL
        );
    """)
    if "id" not in {r[1] for r in db.execute("PRAGMA table_info(files)")}:
        db.execute("ALTER TABLE files ADD COLUMN id TEXT")
    for repo, path in db.execute("SELECT repository_id, relative_path FROM files WHERE id IS NULL").fetchall():
        db.execute("UPDATE files SET id=? WHERE repository_id=? AND relative_path=?",
                   (file_id(repo, path), repo, path))
    db.commit()
    db.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS files_identity ON files(id);
        CREATE UNIQUE INDEX IF NOT EXISTS files_repo_identity ON files(repository_id,id);
        CREATE UNIQUE INDEX IF NOT EXISTS symbols_repo_identity ON symbols(repository_id,id);
        CREATE UNIQUE INDEX IF NOT EXISTS nodes_repo_identity ON graph_nodes(repository_id,id);
        CREATE TABLE IF NOT EXISTS index_snapshots (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
            stage TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('building','ready','failed')),
            schema_version INTEGER NOT NULL, source_origin TEXT NOT NULL CHECK(source_origin='disk'),
            observed_at TEXT NOT NULL, completed_at TEXT, git_revision TEXT,
            UNIQUE(repository_id,id)
        );
        CREATE TABLE IF NOT EXISTS symbol_sources (
            symbol_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, file_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL, source_hash TEXT NOT NULL,
            FOREIGN KEY(repository_id,symbol_id) REFERENCES symbols(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,file_id) REFERENCES files(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,snapshot_id) REFERENCES index_snapshots(repository_id,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS symbol_occurrences (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, file_id TEXT NOT NULL,
            symbol_id TEXT, scope_symbol_id TEXT, snapshot_id TEXT NOT NULL,
            kind TEXT NOT NULL, expression TEXT, source_hash TEXT NOT NULL,
            start_line INTEGER NOT NULL CHECK(start_line>0), start_column INTEGER NOT NULL CHECK(start_column>0),
            end_line INTEGER NOT NULL CHECK(end_line>=start_line), end_column INTEGER NOT NULL CHECK(end_column>0),
            start_byte INTEGER NOT NULL CHECK(start_byte>=0), end_byte INTEGER NOT NULL CHECK(end_byte>=start_byte),
            FOREIGN KEY(repository_id,file_id) REFERENCES files(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,symbol_id) REFERENCES symbols(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,scope_symbol_id) REFERENCES symbols(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,snapshot_id) REFERENCES index_snapshots(repository_id,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS graph_node_sources (
            node_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, file_id TEXT,
            symbol_id TEXT, snapshot_id TEXT NOT NULL, source_hash TEXT,
            FOREIGN KEY(repository_id,node_id) REFERENCES graph_nodes(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,file_id) REFERENCES files(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,symbol_id) REFERENCES symbols(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,snapshot_id) REFERENCES index_snapshots(repository_id,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS relationships (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, source_id TEXT NOT NULL,
            target_id TEXT, relationship TEXT NOT NULL, resolution TEXT NOT NULL
                CHECK(resolution IN ('resolved','inferred','unresolved')),
            evidence_type TEXT NOT NULL, file_id TEXT, snapshot_id TEXT NOT NULL,
            source_hash TEXT, start_line INTEGER, start_byte INTEGER, end_byte INTEGER,
            metadata_json TEXT NOT NULL,
            CHECK(resolution='unresolved' OR target_id IS NOT NULL),
            CHECK(file_id IS NULL OR source_hash IS NOT NULL),
            FOREIGN KEY(repository_id,source_id) REFERENCES graph_nodes(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,target_id) REFERENCES graph_nodes(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,file_id) REFERENCES files(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,snapshot_id) REFERENCES index_snapshots(repository_id,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS code_chunks (
            id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, file_id TEXT NOT NULL,
            symbol_id TEXT REFERENCES symbols(id) ON DELETE SET NULL, snapshot_id TEXT NOT NULL,
            embedding_id TEXT NOT NULL UNIQUE, collection_name TEXT NOT NULL, chunk_index INTEGER NOT NULL CHECK(chunk_index>=0),
            source_hash TEXT NOT NULL, content_hash TEXT NOT NULL,
            start_line INTEGER NOT NULL CHECK(start_line>0), end_line INTEGER NOT NULL CHECK(end_line>=start_line),
            FOREIGN KEY(repository_id,file_id) REFERENCES files(repository_id,id) ON DELETE CASCADE,
            FOREIGN KEY(repository_id,snapshot_id) REFERENCES index_snapshots(repository_id,id) ON DELETE CASCADE,
            UNIQUE(repository_id,file_id,chunk_index)
        );
        CREATE INDEX IF NOT EXISTS chunks_file ON code_chunks(repository_id,file_id);
        CREATE INDEX IF NOT EXISTS occurrences_symbol ON symbol_occurrences(repository_id,symbol_id);
        CREATE TABLE IF NOT EXISTS index_errors (
            id INTEGER PRIMARY KEY, repository_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            relative_path TEXT, stage TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT,
            FOREIGN KEY(repository_id,snapshot_id) REFERENCES index_snapshots(repository_id,id) ON DELETE CASCADE
        );
        CREATE TRIGGER IF NOT EXISTS chunk_symbol_repo_insert BEFORE INSERT ON code_chunks
        WHEN NEW.symbol_id IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM symbols WHERE id=NEW.symbol_id AND repository_id=NEW.repository_id)
        BEGIN SELECT RAISE(ABORT,'Chunk symbol belongs to another repository'); END;
        CREATE TRIGGER IF NOT EXISTS chunk_symbol_repo_update BEFORE UPDATE ON code_chunks
        WHEN NEW.symbol_id IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM symbols WHERE id=NEW.symbol_id AND repository_id=NEW.repository_id)
        BEGIN SELECT RAISE(ABORT,'Chunk symbol belongs to another repository'); END;
        CREATE TRIGGER IF NOT EXISTS file_graph_cleanup BEFORE DELETE ON files
        BEGIN DELETE FROM graph_nodes WHERE repository_id=OLD.repository_id AND relative_path=OLD.relative_path; END;
        CREATE TRIGGER IF NOT EXISTS symbol_graph_cleanup BEFORE DELETE ON symbols
        BEGIN DELETE FROM graph_nodes WHERE symbol_id=OLD.id; END;
    """)
    from knowledge_base.semantic import ensure_semantic_schema
    ensure_semantic_schema(db)
    with db:
        if version < SCHEMA_VERSION:
            # Backfill existing v2-v4 evidence without changing any entity IDs.
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for repo, observed, revision in db.execute("SELECT id,last_scanned_at,git_revision FROM repositories").fetchall():
                snapshot = begin_snapshot(db,repo,'migration',observed_at=observed,revision=revision)
                if 'syntax_references' in tables:
                    for path, source_hash in db.execute("SELECT DISTINCT f.relative_path,f.content_sha256 FROM files f JOIN symbols s ON s.repository_id=f.repository_id AND s.relative_path=f.relative_path WHERE f.repository_id=? AND f.content_sha256 IS NOT NULL", (repo,)).fetchall():
                        publish_syntax(db,repo,path,snapshot,source_hash)
                if {'graph_relationships','graph_unresolved'} <= tables:
                    db.execute('DELETE FROM graph_node_sources WHERE repository_id=?',(repo,))
                    publish_graph(db,repo,snapshot)
                finish_snapshot(db,snapshot)
        db.execute("UPDATE repositories SET schema_version=? WHERE schema_version<?", (SCHEMA_VERSION, SCHEMA_VERSION))
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def begin_snapshot(db, repo_id, stage, *, observed_at=None, revision=None):
    snapshot = str(uuid.uuid4())
    db.execute("INSERT INTO index_snapshots VALUES (?,?,?,?,?,'disk',?,NULL,?)",
               (snapshot, repo_id, stage, 'building', SCHEMA_VERSION,
                observed_at or datetime.now(timezone.utc).isoformat(), revision))
    return snapshot


def finish_snapshot(db, snapshot, *, failed=False):
    db.execute("UPDATE index_snapshots SET state=?, completed_at=? WHERE id=?",
               ('failed' if failed else 'ready', datetime.now(timezone.utc).isoformat(), snapshot))


def publish_syntax(db, repo_id, path, snapshot, source_hash):
    fid = file_id(repo_id, path)
    db.execute("DELETE FROM symbol_occurrences WHERE repository_id=? AND file_id=?", (repo_id, fid))
    columns = ('start_line', 'start_column', 'end_line', 'end_column', 'start_byte', 'end_byte')
    db.row_factory = sqlite3.Row
    for row in db.execute("SELECT * FROM symbols WHERE repository_id=? AND relative_path=?", (repo_id, path)).fetchall():
        db.execute("INSERT OR REPLACE INTO symbol_sources VALUES (?,?,?,?,?)",
                   (row['id'], repo_id, fid, snapshot, source_hash))
        db.execute("INSERT INTO symbol_occurrences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (entity_id('occurrence', row['id'], 'definition'), repo_id, fid, row['id'],
                    row['parent_symbol_id'], snapshot, 'definition', row['name'], source_hash,
                    *(row[c] for c in columns)))
    for row in db.execute("SELECT * FROM syntax_references WHERE repository_id=? AND relative_path=?", (repo_id, path)).fetchall():
        db.execute("INSERT INTO symbol_occurrences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (entity_id('occurrence', repo_id, path, row['kind'], str(row['start_byte']), str(row['id'])),
                    repo_id, fid, None, row['scope_symbol_id'], snapshot, row['kind'], row['expression'],
                    source_hash, *(row[c] for c in columns)))
    for row in db.execute("SELECT kind,detail FROM parse_diagnostics WHERE repository_id=? AND relative_path=?", (repo_id, path)).fetchall():
        db.execute("INSERT INTO index_errors(repository_id,snapshot_id,relative_path,stage,kind,detail) VALUES (?,?,?,'syntax',?,?)",
                   (repo_id, snapshot, path, row['kind'], row['detail']))


def publish_graph(db, repo_id, snapshot):
    """Called in the same transaction that publishes the legacy graph."""
    db.row_factory = sqlite3.Row
    files = {r['relative_path']: r for r in db.execute("SELECT * FROM files WHERE repository_id=?", (repo_id,))}
    for node in db.execute("SELECT * FROM graph_nodes WHERE repository_id=?", (repo_id,)).fetchall():
        file = files.get(node['relative_path'])
        if file and file['kind'] == 'directory':
            file = None
        db.execute("INSERT INTO graph_node_sources VALUES (?,?,?,?,?,?)",
                   (node['id'], repo_id, file['id'] if file else None, node['symbol_id'], snapshot,
                    file['content_sha256'] if file else None))
    db.execute("DELETE FROM relationships WHERE repository_id=?", (repo_id,))
    for table in ('graph_relationships', 'graph_unresolved'):
        for edge in db.execute(f"SELECT * FROM {table} WHERE repository_id=?", (repo_id,)).fetchall():
            file = files.get(edge['evidence_file'])
            if file and file['kind'] == 'directory':
                file = None
            unresolved = table == 'graph_unresolved'
            db.execute("INSERT INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (edge['id'], repo_id, edge['source_id'], None if unresolved else edge['target_id'],
                        edge['relationship'], edge['resolution'], 'semantic_unresolved' if unresolved else edge['evidence_type'],
                        file['id'] if file else None, snapshot, file['content_sha256'] if file else None,
                        edge['evidence_line'], edge['evidence_start_byte'], edge['evidence_end_byte'],
                        json.dumps({'expression': edge['expression'], 'reason': edge['reason']}) if unresolved else edge['metadata_json']))
    # Bind only reference spans whose checker target is a uniquely indexed symbol.
    db.execute("""UPDATE symbol_occurrences SET symbol_id=(
        SELECT min(n.symbol_id) FROM relationships r JOIN graph_nodes n ON n.id=r.target_id
        WHERE r.repository_id=symbol_occurrences.repository_id AND r.file_id=symbol_occurrences.file_id
          AND r.start_byte=symbol_occurrences.start_byte AND r.end_byte=symbol_occurrences.end_byte
          AND r.resolution='resolved' AND n.symbol_id IS NOT NULL
        HAVING count(DISTINCT n.symbol_id)=1)
        WHERE repository_id=? AND kind!='definition'""", (repo_id,))


def chunk_record(db, repo_id, path, snapshot, point_id, ordinal, source_hash, content, start, end, collection):
    """Associate only wholly contained, uniquely most-specific parsed symbols."""
    fid = file_id(repo_id, path)
    candidates = db.execute("""SELECT s.id,s.end_byte-s.start_byte AS width,s.parent_symbol_id,s.start_byte,s.end_byte FROM symbols s
        JOIN symbol_sources p ON p.symbol_id=s.id
        WHERE s.repository_id=? AND p.file_id=? AND p.source_hash=?
          AND s.start_line<=? AND s.end_line>=? ORDER BY width""", (repo_id, fid, source_hash, start, end)).fetchall()
    symbol = None
    if candidates:
        root = Path(db.execute('SELECT root_path FROM repositories WHERE id=?',(repo_id,)).fetchone()[0])
        source = (root/path).read_bytes()
        # Line containment alone is not enough: two declarations may share a
        # line, or a chunk can include code outside a one-line declaration.
        matching = [r for r in candidates if content in source[r[3]:r[4]].decode('utf-8',errors='replace').replace('\r\n','\n').replace('\r','\n')]
        if matching:
            ancestors = {matching[0][0]}
            parent = matching[0][2]
            while parent and parent not in ancestors:
                ancestors.add(parent)
                row = db.execute('SELECT parent_symbol_id FROM symbols WHERE id=?',(parent,)).fetchone()
                parent = row[0] if row else None
            if all(r[0] in ancestors for r in matching):
                symbol = matching[0][0]
    return (point_id, repo_id, fid, symbol, snapshot, point_id, collection, ordinal,
            source_hash, hashlib.sha256(content.encode()).hexdigest(), start, end)


def trace(database, repo_id, identifier):
    """Read-only canonical lookup, checking source hash and saved-source bounds."""
    if not Path(database).is_file():
        raise ValueError("Metadata index is missing; refresh the repository")
    with closing(sqlite3.connect(f"{Path(database).resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM code_chunks WHERE repository_id=? AND id=?", (repo_id, identifier)).fetchone()
        kind = 'chunk'
        if not row:
            row = db.execute("SELECT p.*,s.start_line,s.end_line,s.start_byte,s.end_byte FROM symbol_sources p JOIN symbols s ON s.id=p.symbol_id WHERE p.repository_id=? AND p.symbol_id=?", (repo_id, identifier)).fetchone()
            kind = 'symbol'
        if not row:
            row = db.execute("SELECT * FROM relationships WHERE repository_id=? AND id=?", (repo_id, identifier)).fetchone()
            kind = 'relationship'
        if not row:
            row = db.execute("SELECT p.*,s.start_line,s.end_line,s.start_byte,s.end_byte FROM graph_node_sources p LEFT JOIN symbols s ON s.id=p.symbol_id WHERE p.repository_id=? AND p.node_id=?", (repo_id, identifier)).fetchone()
            kind = 'node'
        if not row:
            raise ValueError("Entity has no unified metadata; refresh the repository")
        info = dict(row)
        if kind == 'chunk' and db.execute("SELECT 1 FROM sqlite_master WHERE name='chunk_context'").fetchone():
            context = db.execute('SELECT * FROM chunk_context WHERE id=?',(identifier,)).fetchone()
            if context:
                if hashlib.sha256(context['source'].encode()).hexdigest() != info['content_hash']:
                    raise ValueError('Chunk context differs from its indexed source')
                info.update({key:context[key] for key in ('language','module','symbol_kind','symbol_name',
                    'qualified_name','signature','comments','chunk_kind','parent_symbol_key','embedding_model')})
        snapshot = db.execute("SELECT * FROM index_snapshots WHERE id=? AND repository_id=?", (info['snapshot_id'], repo_id)).fetchone()
        if not snapshot or snapshot['state'] != 'ready':
            raise ValueError("Index snapshot is not ready")
        info.update(entityId=identifier, kind=kind, sourceOrigin=snapshot['source_origin'],
                    schemaVersion=snapshot['schema_version'], snapshotId=info['snapshot_id'],
                    fileId=info.get('file_id'),symbolId=info.get('symbol_id'))
        if not info.get('file_id'):
            info['structural'] = True
            return info
        file = db.execute("SELECT * FROM files WHERE id=? AND repository_id=?", (info['file_id'], repo_id)).fetchone()
        root = Path(db.execute("SELECT root_path FROM repositories WHERE id=?", (repo_id,)).fetchone()[0]).resolve()
        path = root / relative_path(file['relative_path'])
        try:
            if path.resolve(strict=True) != path or not path.is_file():
                raise ValueError("Indexed path no longer identifies a saved source file")
            source = path.read_bytes()
        except OSError as error:
            raise ValueError("Indexed file is unavailable") from error
        if not info.get('source_hash') or hashlib.sha256(source).hexdigest() != info['source_hash']:
            raise ValueError("Indexed source changed; refresh the repository")
        start, end = info.get('start_line'), info.get('end_line')
        if start is None:
            start, end = 1, max(1, len(source.splitlines()))
        if end is None:
            end = start
        if not 1 <= start <= end <= max(1, len(source.splitlines())):
            raise ValueError("Indexed source location is invalid")
        if info.get('start_byte') is not None and not 0 <= info['start_byte'] <= info['end_byte'] <= len(source):
            raise ValueError("Indexed byte location is invalid")
        info.update(path=file['relative_path'], fileId=file['id'], symbolId=info.get('symbol_id'),
                    snapshotId=info['snapshot_id'], sourceHash=info['source_hash'], startLine=start, endLine=end)
        return info


def audit(database):
    with closing(sqlite3.connect(f"{Path(database).resolve().as_uri()}?mode=ro", uri=True)) as db:
        dangling = list(db.execute("PRAGMA foreign_key_check"))
        dangling += list(db.execute("SELECT 'symbols',id FROM symbols WHERE parent_symbol_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM symbols p WHERE p.id=symbols.parent_symbol_id AND p.repository_id=symbols.repository_id)"))
        for repo,path,identifier in db.execute('SELECT repository_id,relative_path,id FROM files'):
            try:
                if identifier != file_id(repo,path):
                    dangling.append(('file_identity',identifier))
            except ValueError:
                dangling.append(('file_path',path))
        dangling += list(db.execute("SELECT 'graph_symbol',n.id FROM graph_nodes n WHERE n.symbol_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM symbols s WHERE s.id=n.symbol_id AND s.repository_id=n.repository_id)"))
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in
                  ('files','symbols','symbol_occurrences','relationships','code_chunks','index_snapshots','index_errors')}
        return {'schema_version': db.execute('PRAGMA user_version').fetchone()[0], 'counts': counts, 'dangling_references': dangling}


class SemanticIndex:
    """One vector-index run. Failed runs are never exposed as ready snapshots."""
    def __init__(self, root, repo_id, data_dir):
        from knowledge_base.scanner import _prepare_database, _inventory, _git_context, _has_gitignore
        self.root = Path(root).resolve(strict=True)
        self.repo_id = repo_id
        if repo_id != repository_id(self.root):
            raise ValueError('Repository ID does not match its canonical root')
        base = Path(data_dir).expanduser().resolve()
        if base == self.root or self.root in base.parents:
            raise ValueError('Metadata storage must be outside the repository')
        git, revision = _git_context(self.root)
        if not git and _has_gitignore(self.root):
            raise ValueError('A Git repository is required to evaluate .gitignore rules')
        self.entries = {r[0]: r for r in _inventory(self.root, git, (), 1_000_000)
                        if r[1] != 'directory' and r[2] in ('included', 'unsupported')}
        self.database = base / repo_id / 'metadata.db'
        self.db = _prepare_database(self.database)
        with self.db:
            self.db.execute("INSERT INTO repositories VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                            (repo_id,str(self.root),revision,datetime.now(timezone.utc).isoformat(),SCHEMA_VERSION))
            self.snapshot = begin_snapshot(self.db,repo_id,'semantic',revision=revision)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, tb):
        try:
            with self.db:
                if error:
                    self.error(None, type(error).__name__)
                finish_snapshot(self.db,self.snapshot,failed=error is not None)
        finally:
            self.db.close()

    def error(self, path, kind):
        self.db.execute("INSERT INTO index_errors(repository_id,snapshot_id,relative_path,stage,kind) VALUES (?,?,?,'semantic',?)",
                        (self.repo_id,self.snapshot,path,kind))

    def prune(self, paths):
        keep = set(paths)
        with self.db:
            for fid, path in self.db.execute("SELECT DISTINCT c.file_id,f.relative_path FROM code_chunks c JOIN files f ON f.id=c.file_id WHERE c.repository_id=?", (self.repo_id,)).fetchall():
                if path not in keep:
                    self.db.execute("DELETE FROM code_chunks WHERE repository_id=? AND file_id=?", (self.repo_id,fid))

    def has_chunks(self, path, source_hash):
        row = self.db.execute("""SELECT count(*),count(x.id) FROM code_chunks c
            JOIN index_snapshots s ON s.id=c.snapshot_id LEFT JOIN chunk_context x ON x.id=c.id
            WHERE c.repository_id=? AND c.file_id=? AND c.source_hash=? AND s.state='ready'""",
            (self.repo_id,file_id(self.repo_id,path),source_hash)).fetchone()
        return row[0]>0 and row[0]==row[1]

    def records(self, path, source_hash, chunks, collection, *, contexts=None):
        from knowledge_base.identity import chunk_id
        entry = self.entries[relative_path(path)]
        fid = file_id(self.repo_id,path)
        # Register semantic-only files (e.g. Python/Markdown) without pretending
        # that a JS/TS parser extracted symbols for them.
        with self.db:
            previous = self.db.execute('SELECT content_sha256,status FROM files WHERE repository_id=? AND relative_path=?',(self.repo_id,path)).fetchone()
            if previous and previous[1] == 'included' and previous[0] != source_hash:
                self.db.execute('UPDATE repositories SET last_scanned_at=? WHERE id=?',
                                (datetime.now(timezone.utc).isoformat(),self.repo_id))
            self.db.execute("""INSERT INTO files(repository_id,relative_path,kind,status,reason,extension,language,size_bytes,content_sha256,parse_status,id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(repository_id,relative_path) DO UPDATE SET
                kind=excluded.kind,status=excluded.status,reason=excluded.reason,extension=excluded.extension,
                language=excluded.language,content_sha256=excluded.content_sha256,size_bytes=excluded.size_bytes,
                parse_status=CASE WHEN files.content_sha256=excluded.content_sha256 THEN files.parse_status ELSE excluded.parse_status END""",
                (self.repo_id,*entry[:7],source_hash,entry[8],fid))
        records = []
        for i,(text,start,end) in enumerate(chunks):
            identifier = contexts[i].id if contexts else chunk_id(self.repo_id,path,i)
            record = chunk_record(self.db,self.repo_id,path,self.snapshot,identifier,i,source_hash,text,start,end,collection)
            if contexts and contexts[i].symbol_key:
                verified = self.db.execute('''SELECT s.id FROM symbols s JOIN symbol_sources p ON p.symbol_id=s.id
                    WHERE s.id=? AND s.repository_id=? AND p.source_hash=? AND s.relative_path=?
                      AND s.start_line<=? AND s.end_line>=?''',
                    (contexts[i].symbol_key,self.repo_id,source_hash,path,start,end)).fetchone()
                if verified:
                    record = (*record[:3],verified[0],*record[4:])
            records.append(record)
        return records

    def publish(self, path, records, *, contexts=None, embedding_model=''):
        source = self.root / relative_path(path)
        if source.resolve(strict=True) != source or (records and hashlib.sha256(source.read_bytes()).hexdigest() != records[0][8]):
            raise ValueError('Source changed during vector publication')
        with self.db:
            self.db.execute('DELETE FROM chunk_context WHERE repo_id=? AND path=?',(self.repo_id,path))
            self.db.execute("DELETE FROM code_chunks WHERE repository_id=? AND file_id=?", (self.repo_id,file_id(self.repo_id,path)))
            self.db.executemany('INSERT INTO code_chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',records)
            if contexts:
                from knowledge_base.semantic import context_row
                self.db.executemany('INSERT INTO chunk_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    [context_row(chunk,self.repo_id,path,embedding_model) for chunk in contexts])


def validate_vector(database, repo_id, point, collection):
    """All semantic entry points share the same payload/SQLite/source checks."""
    if len(repo_id) != 24 or any(c not in '0123456789abcdef' for c in repo_id):
        raise ValueError('Invalid canonical repository identity')
    payload = point.payload or {}
    info = trace(database,repo_id,str(point.id))
    if (info['kind'] != 'chunk' or payload.get('repo_id') != repo_id
        or info['path'] != payload.get('file_path') or info['sourceHash'] != payload.get('file_hash')
        or info['snapshotId'] != payload.get('snapshot_id') or info['fileId'] != payload.get('file_id')
        or info['symbolId'] != payload.get('symbol_id') or info['entityId'] != payload.get('chunk_id')
        or info['chunk_index'] != payload.get('chunk_index') or info['collection_name'] != collection
        or info['schemaVersion'] != payload.get('schema_version') or payload.get('source_origin') != 'disk'
        or info['content_hash'] != payload.get('content_hash')
        or info['content_hash'] != hashlib.sha256(str(payload.get('code_snippet','')).encode()).hexdigest()
        or (info['startLine'],info['endLine']) != (payload.get('start_line'),payload.get('end_line'))):
        raise ValueError('Vector payload disagrees with canonical metadata')
    for key in ('language','module','symbol_kind','symbol_name','qualified_name','signature','comments','chunk_kind','parent_symbol_key','embedding_model'):
        if key in info and info[key] != payload.get(key):
            raise ValueError('Vector structural context disagrees with metadata')
    return info


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Inspect the unified metadata index (read only)')
    parser.add_argument('--repo',required=True)
    parser.add_argument('--data-dir',default=None)
    parser.add_argument('--trace',help='Graph node, relationship, symbol or chunk ID')
    args = parser.parse_args()
    import os
    base = Path(args.data_dir or os.environ.get('CODEMIND_DATA_DIR','~/.codemind/data')).expanduser()
    repo = repository_id(args.repo)
    from knowledge_base.synchronization import SnapshotStore
    try:
        with SnapshotStore(args.repo,repo,base).pin_view() as view:
            database = view['data_dir']/repo/'metadata.db'
            result = trace(database,repo,args.trace) if args.trace else audit(database)
            result['active_snapshot_id'] = view['snapshotId']
            result['database'] = str(database)
            result['synchronization'] = {k:view[k] for k in ('fresh','state','error','savedFilesOnly')}
    except (ValueError,sqlite3.Error) as error:
        parser.exit(1,f'{error}. Refresh the repository index.\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
