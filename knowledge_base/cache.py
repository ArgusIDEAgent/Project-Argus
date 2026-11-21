"""Bounded, disposable JSON cache. Never stores session authorization handles."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import time

from knowledge_base.storage import checked_path


class RepositoryCache:
    def __init__(self, storage, *, max_entries=256, max_bytes=16 * 1024 * 1024, ttl=3600):
        self.storage = storage
        self.max_entries, self.max_bytes, self.ttl = max_entries, max_bytes, ttl

    def _connect(self):
        self.storage.prepare()
        path = checked_path(self.storage.path / 'cache.db')
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        db = sqlite3.connect(path, timeout=10)
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL, used REAL NOT NULL)')
        return db

    def key(self, namespace, parts):
        return hashlib.sha256(json.dumps([self.storage.repo_id, namespace, parts], sort_keys=True).encode()).hexdigest()

    def get(self, namespace, parts):
        with closing(self._connect()) as db, db:
            key = self.key(namespace, parts)
            row = db.execute('SELECT value FROM cache WHERE key=? AND expires>?', (key, time.time())).fetchone()
            if row:
                db.execute('UPDATE cache SET used=? WHERE key=?', (time.time(), key))
                return json.loads(row[0])
        return None

    def put(self, namespace, parts, value):
        payload = json.dumps(value)
        if len(payload.encode()) > self.max_bytes:
            return
        now = time.time()
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM cache WHERE expires<=?', (now,))
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)', (self.key(namespace, parts), payload, now + self.ttl, now))
            rows = db.execute('SELECT key,length(CAST(value AS BLOB)) FROM cache ORDER BY used DESC').fetchall()
            size = 0
            for index, (key, count) in enumerate(rows):
                size += count
                if index >= self.max_entries or size > self.max_bytes:
                    db.execute('DELETE FROM cache WHERE key=?', (key,))

    def clear(self):
        with closing(self._connect()) as db, db:
            db.execute('DELETE FROM cache')


def query_cache():
    from knowledge_base.storage import current_storage
    storage = current_storage()
    return RepositoryCache(storage) if storage is not None and storage.local else None


def cache_get(cache, namespace, key):
    try:
        return cache.get(namespace, key) if cache else None
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        return None  # A disposable cache must not make healthy retrieval unavailable.


def cache_put(cache, namespace, key, value):
    try:
        if cache: cache.put(namespace, key, value)
    except (sqlite3.Error, OSError):
        pass
