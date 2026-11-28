"""Local FAISS cosine index with durable, stable vector-to-chunk identities.

The SQLite vector journal is authoritative; a missing, interrupted or corrupt
FAISS cache is rebuilt from it. Never deserialize an unchecked FAISS file.
The narrow client interface preserves existing engine/CLI callers.
"""
from contextlib import contextmanager
from itertools import batched, islice
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace as Result
import uuid

import faiss
from filelock import FileLock
import numpy as np

from knowledge_base.source_ranking import source_role

faiss.omp_set_num_threads(1)


def matches(payload, condition):
    if condition is None:
        return True
    if hasattr(condition, 'must'):
        return (all(matches(payload,c) for c in condition.must or [])
                and (not condition.should or any(matches(payload,c) for c in condition.should))
                and not any(matches(payload,c) for c in condition.must_not or []))
    value = payload.get(condition.key)
    match = condition.match
    if match is not None:
        if hasattr(match,'any'):
            return value in match.any
        return value == match.value
    if condition.range is not None:
        if not isinstance(value,(int,float)):
            return False
        return all(bound is None or test(value,bound) for bound,test in
                   ((condition.range.gte,lambda a,b:a>=b),(condition.range.gt,lambda a,b:a>b),
                    (condition.range.lte,lambda a,b:a<=b),(condition.range.lt,lambda a,b:a<b)))
    raise ValueError('Unsupported vector filter')


_INDEXED_FILTERS = frozenset({"repo_id", "file_path"})


def _index_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _index_constraints(condition):
    """Push down mandatory string filters and leave other predicates to matches()."""
    if condition is None:
        return
    if hasattr(condition, "must"):
        for child in condition.must or ():
            yield from _index_constraints(child)
        return
    if condition.key not in _INDEXED_FILTERS or condition.match is None:
        return
    values = condition.match.any if hasattr(condition.match, "any") else [getattr(condition.match, "value", None)]
    if len(values) <= 500 and all(isinstance(value, str) for value in values):
        expression = f"json_extract(payload,'$.{condition.key}')"
        placeholders = ",".join("?" for _ in values)
        yield f"{expression} IN ({placeholders})", values


class FaissClient:
    is_embedded = True
    backend = 'faiss'

    def __init__(self, path, *, readonly=False, database_name='vectors.sqlite3', defer_index=False):
        from knowledge_base.storage import checked_path
        self.path = checked_path(path)
        self.readonly = readonly
        self.defer_index = defer_index
        if not readonly:
            self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock = threading.RLock()
        self.file_lock = FileLock(str(self.path/'writer.lock'),timeout=30)
        database = checked_path(self.path/database_name)
        if not readonly and not database.exists():
            fd = os.open(database,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(fd)
        self.db = sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,check_same_thread=False,timeout=30) if readonly else sqlite3.connect(database,check_same_thread=False,timeout=30)
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute('PRAGMA foreign_keys=ON')
        if not readonly:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY,dimensions INTEGER NOT NULL,generation INTEGER NOT NULL,index_hash TEXT);
            CREATE TABLE IF NOT EXISTS vectors(collection_name TEXT NOT NULL REFERENCES collections(name) ON DELETE CASCADE,
                chunk_id TEXT NOT NULL,vector_id INTEGER NOT NULL,embedding BLOB NOT NULL,payload TEXT NOT NULL,
                PRIMARY KEY(collection_name,chunk_id),UNIQUE(collection_name,vector_id));
            CREATE INDEX IF NOT EXISTS vector_file ON vectors(collection_name,json_extract(payload,'$.file_path'),chunk_id);
        ''')
        self.cache = {}

    @contextmanager
    def _guard(self):
        with self.lock:
            if self.readonly:
                yield
            else:
                with self.file_lock:
                    yield

    def close(self):
        with self.lock:
            self.db.close()
            self.cache.clear()

    def get_collections(self):
        with self._guard():
            return Result(collections=[Result(name=r[0]) for r in self.db.execute('SELECT name FROM collections')])

    def collection_exists(self, collection_name):
        with self._guard():
            return bool(self.db.execute('SELECT 1 FROM collections WHERE name=?',(collection_name,)).fetchone())

    def create_collection(self, collection_name, vectors_config):
        dimension = vectors_config['size'] if isinstance(vectors_config,dict) else vectors_config.size
        distance = vectors_config.get('distance','Cosine') if isinstance(vectors_config,dict) else vectors_config.distance
        if str(distance).lower() not in ('cosine','distance.cosine') or dimension <= 0:
            raise ValueError('FAISS store requires positive dimensions and cosine distance')
        with self._guard(), self.db:
            self.db.execute('INSERT INTO collections VALUES (?,?,0,NULL)',(collection_name,dimension))

    def _collection(self, name):
        row = self.db.execute('SELECT dimensions,generation,index_hash FROM collections WHERE name=?',(name,)).fetchone()
        if not row:
            raise ValueError('Vector collection does not exist')
        return row

    def get_collection(self, collection_name):
        with self._guard():
            size = self._collection(collection_name)[0]
            return Result(config=Result(params=Result(vectors=Result(size=size))))

    def delete_collection(self,collection_name):
        with self._guard(),self.db:
            self.db.execute('DELETE FROM collections WHERE name=?',(collection_name,))
            self.cache.pop(collection_name,None)
            self._index_path(collection_name).unlink(missing_ok=True)

    def _matching_rows(self, name, condition, *, with_vectors=False, offset=None, role=None):
        columns = 'chunk_id,vector_id,payload' + (',embedding' if with_vectors else '')
        clauses, args = ['collection_name=?'], [name]
        if offset is not None:
            clauses.append('chunk_id>=?')
            args.append(str(offset))
        for sql, values in _index_constraints(condition):
            clauses.append(sql)
            args.extend(values)
        order = ' ORDER BY chunk_id' if offset is not None or with_vectors else ''
        for row in self.db.execute(f'SELECT {columns} FROM vectors WHERE ' + ' AND '.join(clauses) + order, args):
            cid, vid, data, *embedding = row
            payload = json.loads(data)
            if matches(payload, condition) and (role is None or source_role(payload.get('file_path', '')) == role):
                yield cid, vid, payload, np.frombuffer(embedding[0], dtype='<f4').tolist() if embedding else None

    def _selected_rows(self, name, column, ids, *, with_vectors=False):
        columns = 'chunk_id,vector_id,payload' + (',embedding' if with_vectors else '')
        for batch in batched(ids, 500):
            placeholders = ','.join('?' for _ in batch)
            for row in self.db.execute(f'SELECT {columns} FROM vectors WHERE collection_name=? AND {column} IN ({placeholders})',
                                       (name, *batch)):
                cid, vid, data, *embedding = row
                yield cid, vid, json.loads(data), np.frombuffer(embedding[0], dtype='<f4').tolist() if embedding else None

    def _index_path(self, name):
        from knowledge_base.storage import checked_path
        return checked_path(self.path/(hashlib.sha256(name.encode()).hexdigest()+'.faiss'))

    def _load(self, name):
        dimension,generation,digest = self._collection(name)
        if name in self.cache and self.cache[name][0] == generation:
            return self.cache[name][1]
        path = self._index_path(name)
        index = None
        if digest and path.is_file() and not path.is_symlink() and _index_digest(path) == digest:
            try:
                index = faiss.read_index(str(path))
            except RuntimeError:
                index = None
        if index is None:
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
            rows = self.db.execute('SELECT vector_id,embedding FROM vectors WHERE collection_name=? ORDER BY vector_id', (name,))
            for batch in batched(rows, 2048):
                vectors = np.asarray([np.frombuffer(data, dtype='<f4') for _, data in batch], dtype='float32')
                ids = np.asarray([identifier for identifier, _ in batch], dtype='int64')
                index.add_with_ids(vectors, ids)
            if self.readonly:
                self.cache[name] = (generation,index)
                return index
            temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                faiss.write_index(index,str(temporary))
                os.chmod(temporary,0o600)
                os.replace(temporary,path)
            finally:
                temporary.unlink(missing_ok=True)
            with self.db:
                self.db.execute('UPDATE collections SET index_hash=? WHERE name=?',(_index_digest(path),name))
        self.cache[name] = (generation,index)
        return index

    def upsert(self, collection_name, points):
        with self._guard():
            dimension = self._collection(collection_name)[0]
            rows = []
            for point in points:
                vector = np.asarray(point.vector,dtype='float32').reshape(1,-1)
                if vector.shape[1] != dimension or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                    raise ValueError('Invalid embedding dimension or values')
                faiss.normalize_L2(vector)
                cid = str(point.id)
                vid = int.from_bytes(hashlib.sha256(cid.encode()).digest()[:8],'big') & ((1<<63)-1)
                rows.append((collection_name,cid,vid,vector.astype('<f4').tobytes(),json.dumps(point.payload or {})))
            with self.db:
                self.db.executemany('INSERT INTO vectors VALUES (?,?,?,?,?) ON CONFLICT(collection_name,chunk_id) DO UPDATE SET embedding=excluded.embedding,payload=excluded.payload',rows)
                self.db.execute('UPDATE collections SET generation=generation+1,index_hash=NULL WHERE name=?',(collection_name,))
            if not self.defer_index:
                self._load(collection_name)

    def build_index(self, collection_name):
        with self._guard():
            self._load(collection_name)

    def query_points(self, collection_name, query, query_filter=None, limit=10, score_threshold=None, *, role=None):
        with self._guard():
            index = self._load(collection_name)
            vector = np.asarray(query,dtype='float32').reshape(1,-1)
            if vector.shape[1] != index.d or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                raise ValueError('Invalid query embedding')
            if limit <= 0 or not index.ntotal:
                return Result(points=[])
            faiss.normalize_L2(vector)
            filtered = query_filter is not None or role is not None
            candidates = min(index.ntotal, max(32, 2 * limit) if filtered else limit)
            scores, ids = index.search(vector, candidates)
            points = self._ranked_rows(collection_name, scores[0], ids[0], query_filter, role, score_threshold, limit)
            if (filtered and len(points) < limit and candidates < index.ntotal
                    and (score_threshold is None or scores[0][-1] >= score_threshold)):
                # Sparse filters can hide every early hit. Restrict an exact
                # top-k query to eligible IDs instead of ranking the corpus.
                eligible = np.fromiter((row[1] for row in self._matching_rows(collection_name, query_filter, role=role)),
                                       dtype='int64')
                if not len(eligible):
                    return Result(points=[])
                selector = faiss.IDSelectorBatch(eligible)
                params = faiss.SearchParameters(sel=selector)
                scores, ids = index.search(vector, min(limit, len(eligible)), params=params)
                points = self._ranked_rows(collection_name, scores[0], ids[0], None, None, score_threshold, limit)
            return Result(points=points)

    def _ranked_rows(self, collection, scores, ids, condition, role, threshold, limit):
        ranked = [(int(identifier), float(score)) for identifier, score in zip(ids, scores)
                  if identifier >= 0 and (threshold is None or score >= threshold)]
        rows = {row[1]: row for row in self._selected_rows(collection, 'vector_id', (vid for vid, _ in ranked))}
        points = []
        for vid, score in ranked:
            if vid not in rows:
                continue
            cid, _, payload, _ = rows[vid]
            if matches(payload, condition) and (role is None or source_role(payload.get('file_path', '')) == role):
                points.append(Result(id=cid, payload=payload, score=score, vector=None))
                if len(points) == limit:
                    break
        return points

    def count(self, collection_name, exact=True, count_filter=None):
        with self._guard():
            if count_filter is None:
                return Result(count=self.db.execute('SELECT count(*) FROM vectors WHERE collection_name=?', (collection_name,)).fetchone()[0])
            return Result(count=sum(1 for _ in self._matching_rows(collection_name, count_filter)))

    def delete(self, collection_name, points_selector):
        condition = getattr(points_selector,'filter',points_selector)
        with self._guard():
            ids = sorted({str(i) for i in condition}) if isinstance(condition,list) else [
                row[0] for row in self._matching_rows(collection_name, condition)]
            if not ids:
                return
            with self.db:
                self.db.executemany('DELETE FROM vectors WHERE collection_name=? AND chunk_id=?', ((collection_name, cid) for cid in ids))
                self.db.execute('UPDATE collections SET generation=generation+1,index_hash=NULL WHERE name=?',(collection_name,))
            if not self.defer_index:
                self._load(collection_name)

    def retrieve(self, collection_name, ids, **kwargs):
        with self._guard():
            rows = self._selected_rows(collection_name, 'chunk_id', dict.fromkeys(map(str, ids)),
                                       with_vectors=kwargs.get('with_vectors', False))
            return [Result(id=cid, payload=payload, vector=vector)
                    for cid, _, payload, vector in sorted(rows, key=lambda row: row[1])]

    def scroll(self, collection_name, scroll_filter=None, limit=200, offset=None, **kwargs):
        with self._guard():
            rows = list(islice(self._matching_rows(collection_name, scroll_filter, offset='' if offset is None else offset,
                                                   with_vectors=kwargs.get('with_vectors', False)), limit + 1))
            return ([Result(id=cid, payload=payload, vector=vector) for cid, _, payload, vector in rows[:limit]],
                    rows[limit][0] if len(rows) > limit else None)

    def set_payload(self, collection_name, payload, points):
        with self._guard(), self.db:
            rows = list(self._selected_rows(collection_name, 'chunk_id', dict.fromkeys(map(str, points))))
            self.db.executemany('UPDATE vectors SET payload=? WHERE collection_name=? AND chunk_id=?',
                                ((json.dumps({**current, **payload}), collection_name, cid) for cid, _, current, _ in rows))


class RepositoryVectors:
    """Route collection names into that repository's immutable generation DBs.

    Vector records share metadata.db; FAISS files are derived accelerators beside it.
    No repository content is written into the machine-wide vector journal.
    """
    backend = 'faiss'
    is_embedded = True
    _clients = {}
    _lock = threading.RLock()

    def __init__(self, storage):
        self.storage = storage

    def _path(self, name):
        import re
        from knowledge_base.storage import checked_path
        match = re.fullmatch(r'codemind_codebase_' + self.storage.repo_id + r'_([a-f0-9]{32})', name)
        if match:
            return checked_path(self.storage.path/'generations'/match[1]/self.storage.repo_id)
        if name != 'codemind_codebase':
            raise ValueError('Collection does not belong to the selected repository')
        return checked_path(self.storage.base/self.storage.repo_id)

    def _client(self, name):
        path = self._path(name)
        readonly = (path/'generation.json').is_file()
        key = (str(path), readonly)
        with self._lock:
            if key not in self._clients:
                self._clients[key] = FaissClient(path, readonly=readonly, database_name='metadata.db', defer_index=True)
            return self._clients[key]

    def collection_exists(self, collection_name):
        path = self._path(collection_name)
        if not (path/'metadata.db').is_file():
            return False
        client = self._client(collection_name)
        return client.collection_exists(collection_name)

    def reuse_backed_up_collection(self, previous, current):
        client = self._client(current)
        with client._guard(), client.db:
            # The metadata backup already includes the normalized vector journal.
            # Retarget it atomically and build its accelerator once at seal time.
            client._collection(previous)
            client.db.execute('PRAGMA defer_foreign_keys=ON')
            client.db.execute('UPDATE collections SET name=?,index_hash=NULL WHERE name=?', (current, previous))
            client.db.execute('UPDATE vectors SET collection_name=? WHERE collection_name=?', (current, previous))
            client.db.execute('UPDATE code_chunks SET collection_name=? WHERE repository_id=? AND collection_name=?',
                              (current, self.storage.repo_id, previous))

    def seal(self, collection_name):
        self._client(collection_name).build_index(collection_name)
        self.release(collection_name)

    def release(self, collection_name):
        path = str(self._path(collection_name))
        with self._lock:
            for key in list(self._clients):
                if key[0] == path:
                    self._clients.pop(key).close()

    def delete_collection(self, collection_name):
        # SnapshotStore removes retired generation directories under its reader lock.
        self.release(collection_name)

    def create_collection(self, collection_name, vectors_config):
        client = self._client(collection_name)
        # A staged SQLite backup includes the previous vector journal. Keep only
        # this generation's collection; prior vectors are read from the old DB.
        with client.db:
            client.db.execute('DELETE FROM collections WHERE name<>?', (collection_name,))
        return client.create_collection(collection_name, vectors_config)

    def get_collections(self):
        return Result(collections=[])

    def __getattr__(self, method):
        def call(*args, **kwargs):
            name = kwargs.get('collection_name') or args[0]
            return getattr(self._client(name), method)(*args, **kwargs)
        return call
