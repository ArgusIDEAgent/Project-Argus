"""Local FAISS cosine index with durable, stable vector-to-chunk identities.

The SQLite vector journal is authoritative; a missing, interrupted or corrupt
FAISS cache is rebuilt from it. Never deserialize an unchecked FAISS file.
The narrow client interface preserves existing engine/CLI callers.
"""
from contextlib import contextmanager
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


class FaissClient:
    is_embedded = True
    backend = 'faiss'

    def __init__(self, path, *, readonly=False, database_name='vectors.sqlite3'):
        from knowledge_base.storage import checked_path
        self.path = checked_path(path)
        self.readonly = readonly
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

    def _rows(self, name, condition=None):
        return [(cid,vid,np.frombuffer(vector,dtype='<f4'),json.loads(payload))
                for cid,vid,vector,payload in self.db.execute('SELECT chunk_id,vector_id,embedding,payload FROM vectors WHERE collection_name=? ORDER BY vector_id',(name,))
                if matches(json.loads(payload),condition)]

    def _index_path(self, name):
        from knowledge_base.storage import checked_path
        return checked_path(self.path/(hashlib.sha256(name.encode()).hexdigest()+'.faiss'))

    def _load(self, name):
        dimension,generation,digest = self._collection(name)
        if name in self.cache and self.cache[name][0] == generation:
            return self.cache[name][1]
        path = self._index_path(name)
        index = None
        if digest and path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            try:
                index = faiss.read_index(str(path))
            except RuntimeError:
                index = None
        if index is None:
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
            rows = self._rows(name)
            if rows:
                index.add_with_ids(np.asarray([r[2] for r in rows],dtype='float32'),np.asarray([r[1] for r in rows],dtype='int64'))
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
                self.db.execute('UPDATE collections SET index_hash=? WHERE name=?',(hashlib.sha256(path.read_bytes()).hexdigest(),name))
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
            self._load(collection_name)

    def query_points(self, collection_name, query, query_filter=None, limit=10, score_threshold=None):
        with self._guard():
            index = self._load(collection_name)
            vector = np.asarray(query,dtype='float32').reshape(1,-1)
            if vector.shape[1] != index.d or not np.isfinite(vector).all() or not np.linalg.norm(vector):
                raise ValueError('Invalid query embedding')
            faiss.normalize_L2(vector)
            rows = {r[1]:r for r in self._rows(collection_name,query_filter)}
            # Exact FAISS search, then metadata filtering. Searching all IDs
            # ensures a filter cannot hide an otherwise eligible top result.
            scores,ids = index.search(vector,max(1,index.ntotal))
            points = []
            for score,vid in zip(scores[0],ids[0]):
                if vid not in rows or (score_threshold is not None and score < score_threshold):
                    continue
                row = rows[vid]
                points.append(Result(id=row[0],payload=row[3],score=float(score),vector=None))
                if len(points) >= limit:
                    break
            return Result(points=points)

    def count(self, collection_name, exact=True, count_filter=None):
        with self._guard():
            if count_filter is None:
                return Result(count=self.db.execute('SELECT count(*) FROM vectors WHERE collection_name=?', (collection_name,)).fetchone()[0])
            return Result(count=len(self._rows(collection_name,count_filter)))

    def delete(self, collection_name, points_selector):
        condition = getattr(points_selector,'filter',points_selector)
        with self._guard():
            rows = self._rows(collection_name,None if isinstance(condition,list) else condition)
            ids = {str(i) for i in condition} if isinstance(condition,list) else {r[0] for r in rows}
            with self.db:
                self.db.executemany('DELETE FROM vectors WHERE collection_name=? AND chunk_id=?',[(collection_name,r[0]) for r in rows if r[0] in ids])
                self.db.execute('UPDATE collections SET generation=generation+1,index_hash=NULL WHERE name=?',(collection_name,))
            self._load(collection_name)

    def retrieve(self, collection_name, ids, **kwargs):
        with self._guard():
            selected = {str(i) for i in ids}
            return [Result(id=r[0],payload=r[3],vector=r[2].tolist() if kwargs.get('with_vectors') else None)
                    for r in self._rows(collection_name) if r[0] in selected]

    def scroll(self, collection_name, scroll_filter=None, limit=200, offset=None, **kwargs):
        with self._guard():
            rows = sorted(self._rows(collection_name,scroll_filter),key=lambda r:r[0])
            rows = [r for r in rows if offset is None or r[0] >= str(offset)]
            return ([Result(id=r[0],payload=r[3],vector=r[2].tolist() if kwargs.get('with_vectors') else None)
                     for r in rows[:limit]],rows[limit][0] if len(rows)>limit else None)

    def set_payload(self, collection_name, payload, points):
        with self._guard():
            selected = {str(i) for i in points}
            with self.db:
                for row in self._rows(collection_name):
                    if row[0] in selected:
                        self.db.execute('UPDATE vectors SET payload=? WHERE collection_name=? AND chunk_id=?',(json.dumps({**row[3],**payload}),collection_name,row[0]))


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
                self._clients[key] = FaissClient(path, readonly=readonly, database_name='metadata.db')
            return self._clients[key]

    def collection_exists(self, collection_name):
        path = self._path(collection_name)
        if not (path/'metadata.db').is_file():
            return False
        client = self._client(collection_name)
        return client.collection_exists(collection_name)

    def seal(self, collection_name):
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
