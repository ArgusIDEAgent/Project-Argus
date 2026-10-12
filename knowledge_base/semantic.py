"""SQLite FTS5 keyword index and common Phase 5 metadata filters."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3


def ensure_semantic_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS chunk_context (
            id TEXT PRIMARY KEY REFERENCES code_chunks(id) ON DELETE CASCADE,
            repo_id TEXT NOT NULL, path TEXT NOT NULL, language TEXT NOT NULL, module TEXT NOT NULL,
            symbol_kind TEXT NOT NULL, symbol_name TEXT NOT NULL, qualified_name TEXT NOT NULL,
            signature TEXT NOT NULL, comments TEXT NOT NULL, chunk_kind TEXT NOT NULL,
            parent_symbol_key TEXT NOT NULL, embedding_model TEXT NOT NULL,
            embedding_text TEXT NOT NULL, source TEXT NOT NULL, terms TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
            path,symbol_name,comments,source,terms,content='chunk_context',content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2');
        CREATE TRIGGER IF NOT EXISTS context_insert AFTER INSERT ON chunk_context BEGIN
            INSERT INTO chunk_fts(rowid,path,symbol_name,comments,source,terms)
            VALUES(new.rowid,new.path,new.symbol_name,new.comments,new.source,new.terms); END;
        CREATE TRIGGER IF NOT EXISTS context_delete AFTER DELETE ON chunk_context BEGIN
            INSERT INTO chunk_fts(chunk_fts,rowid,path,symbol_name,comments,source,terms)
            VALUES('delete',old.rowid,old.path,old.symbol_name,old.comments,old.source,old.terms); END;
        CREATE TRIGGER IF NOT EXISTS context_update AFTER UPDATE ON chunk_context BEGIN
            INSERT INTO chunk_fts(chunk_fts,rowid,path,symbol_name,comments,source,terms)
            VALUES('delete',old.rowid,old.path,old.symbol_name,old.comments,old.source,old.terms);
            INSERT INTO chunk_fts(rowid,path,symbol_name,comments,source,terms)
            VALUES(new.rowid,new.path,new.symbol_name,new.comments,new.source,new.terms); END;
    ''')


def context_row(chunk, repo, path, model):
    terms = re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',chunk.qualified_name).replace('_',' ')
    return (chunk.id,repo,path,chunk.language,chunk.module,chunk.symbol_kind,chunk.name,
            chunk.qualified_name,chunk.signature,chunk.comments,chunk.kind,chunk.symbol_key,
            model,chunk.embedding_text(path),chunk.text,terms)


def matches_filters(payload, filters):
    mapping = {'files':'file_path','modules':'module','languages':'language','symbolTypes':'symbol_kind'}
    return all(not filters.get(key) or payload.get(field) in filters[key] for key,field in mapping.items())


def keyword_search(database, repo_id, query, *, filters=None, limit=10):
    """Safely quoted FTS tokens, BM25 ranking, and ready-snapshot filtering."""
    if not Path(database).is_file():
        return []
    tokens = list(dict.fromkeys(re.findall(r'[\w]+',query,flags=re.UNICODE)))[:32]
    if not tokens:
        return []
    expression = ' OR '.join('"'+token.replace('"','""')+'"' for token in tokens)
    conditions,parameters = ['x.repo_id=?',"s.state='ready'"],[repo_id]
    for key,column in (('files','path'),('modules','module'),('languages','language'),('symbolTypes','symbol_kind')):
        values = (filters or {}).get(key)
        if values:
            conditions.append(f'x.{column} IN ({",".join("?" for _ in values)})')
            parameters.extend(values)
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='chunk_fts'").fetchone():
            return []
        rows = db.execute(f'''SELECT x.*,c.file_id,c.symbol_id,c.snapshot_id,c.source_hash,c.start_line,c.end_line,
                bm25(chunk_fts,1.0,4.0,2.0,1.0,2.0) AS rank
            FROM chunk_fts JOIN chunk_context x ON x.rowid=chunk_fts.rowid
            JOIN code_chunks c ON c.id=x.id JOIN index_snapshots s ON s.id=c.snapshot_id
            WHERE chunk_fts MATCH ? AND {' AND '.join(conditions)} ORDER BY rank,x.path,c.start_line LIMIT ?''',
            [expression,*parameters,limit]).fetchall()
        return [dict(row) for row in rows]
