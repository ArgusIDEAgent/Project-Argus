"""Real repository-local SQLite/FAISS, deterministic embeddings, no live model."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import api
import config
import indexer
from knowledge_base.cache import RepositoryCache
from knowledge_base.graph import build_graph
from knowledge_base.storage import RepositoryStorage
from knowledge_base.synchronization import SnapshotStore, SyncManager, observe_sources
from knowledge_base.vector_store import RepositoryVectors


class RepositoryStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cm-repository-storage-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()/'repo'
        self.root.mkdir()
        self.file = self.root/'auth.ts'
        self.file.write_text('export function validateToken(token: string) { return token.length > 0; }\n')
        self.repo = config.make_repo_id(str(self.root))
        self.store = SnapshotStore(self.root, self.repo)
        self.client = RepositoryVectors(self.store.storage)
        self.addCleanup(self.close_vectors)
        for guard in (
            patch.object(api, 'DEFAULT_DATA_DIR', None),
            patch.object(api, '_SYNC_MANAGER', None),
            patch.dict(api._REGISTERED_REPOS, {self.repo: {'rootPath': str(self.root)}}, clear=True),
            patch.dict(os.environ, {'QDRANT_URL':'', 'CODEMIND_AUTO_SYNC':'0', 'CODEMIND_LOCAL_MODEL':''}),
            patch.object(indexer, 'embed_texts', side_effect=lambda texts: [[1.]+[0.]*383 for _ in texts]),
            patch.object(indexer, 'get_embedding_model'),
            patch.object(api, 'embed_texts', side_effect=lambda texts: [[1.]+[0.]*383 for _ in texts]),
        ):
            guard.start(); self.addCleanup(guard.stop)

    def close_vectors(self):
        for generation in (self.store.path/'generations').glob('*'):
            self.client.release('codemind_codebase_'+self.repo+'_'+generation.name)

    def publish(self, **options):
        return self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
            index_directory=indexer.index_directory, build_graph=build_graph, **options)

    def search(self):
        return api.search_code(api.SearchRequest(repoId=self.repo, query='Where is token validation?', retrieval='semantic'))

    def database(self):
        return self.store.data_for(self.store.active())/self.repo/'metadata.db'

    def test_reopen_reuses_readonly_snapshot_and_persistent_query_cache(self):
        self.publish()
        database = self.database()
        self.assertEqual(database.stat().st_mode & 0o777, 0o400)
        digest = hashlib.sha256(database.read_bytes()).hexdigest()
        self.assertTrue(self.search()['results'])
        self.close_vectors()
        reopened = SnapshotStore(self.root, self.repo)
        self.assertEqual(reopened.active()['id'], self.store.active()['id'])
        with patch.object(indexer, 'embed_texts', side_effect=AssertionError('No rebuild on reopen')):
            self.assertTrue(self.publish()['unchanged'])
        with patch.object(api, 'embed_texts', side_effect=AssertionError('Cached query must survive reopen')):
            self.assertTrue(self.search()['results'])
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), digest)
        self.assertTrue((self.store.path/'cache.db').is_file())
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM files')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertGreater(db.execute('SELECT count(*) FROM vectors').fetchone()[0], 0)

    def test_new_process_reuses_cached_candidates_without_model(self):
        self.publish(); self.assertTrue(self.search()['results'])
        script = '''
import sys
sys.path.insert(0, sys.argv[1])
from unittest.mock import patch
import api
repo = api.register_repo(api.RegisterRequest(rootPath=sys.argv[2]))['repository']['id']
with patch('api.embed_texts', side_effect=AssertionError('No embedding on persistent cache hit')):
    result = api.search_code(api.SearchRequest(repoId=repo, query='Where is token validation?', retrieval='semantic'))
assert result['results'], result
print('persistent-cache-hit')
'''
        child = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1]), str(self.root)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn('persistent-cache-hit', child.stdout)

    def test_refresh_invalidates_candidates_but_reuses_query_embedding(self):
        self.publish(); self.search()
        old = self.store.active()['id']
        self.file.write_text(self.file.read_text().replace('> 0', '> 2'))
        self.assertEqual(self.search()['results'], [])
        self.publish()
        self.assertNotEqual(self.store.active()['id'], old)
        with patch.object(api, 'embed_texts', side_effect=AssertionError('Query embedding reusable across snapshots')):
            self.assertTrue(self.search()['results'])

    def test_failed_refresh_keeps_snapshot_and_legacy_data(self):
        legacy = self.root.parent/'old-data'; legacy.mkdir()
        marker = legacy/'vectors.sqlite3'; marker.write_bytes(b'old data stays untouched')
        self.publish(); old = self.store.active()['id']; saved = self.database().read_bytes()
        self.file.write_text('export function validateToken() { return false; }\n')
        with (patch.object(indexer, 'embed_texts', side_effect=RuntimeError('embedding unavailable')),
              self.assertRaisesRegex(RuntimeError, 'embedding unavailable')):
            self.publish()
        self.assertEqual(self.store.active()['id'], old)
        self.assertEqual(self.database().read_bytes(), saved)
        self.assertEqual(marker.read_bytes(), b'old data stays untouched')
        self.publish()
        self.assertTrue(self.store.view()['fresh'])

    def test_advisory_poll_never_hashes_sources_and_pending_events_survive_older_jobs(self):
        self.publish()
        stable = self.store.active()['id']
        manager = SyncManager(None, lambda _: None, debounce=3600, audit_interval=3600)
        self.addCleanup(manager.close)
        manager.add(self.repo, self.root)
        with patch.object(api, '_SYNC_MANAGER', manager):
            with patch('knowledge_base.synchronization.observe_sources',
                       side_effect=AssertionError('Status polling must not scan saved files')):
                status = api.live_state(self.repo, verify=False)['synchronization']
                self.assertTrue(status['fresh'])
                self.assertFalse(status['verified'])
                self.assertEqual(status['snapshotId'], stable)
                manager.changed(self.repo)
                consumed = manager.revision(self.repo)
                manager.changed(self.repo)
                manager.completed(self.repo, consumed)
                pending = api.live_state(self.repo, verify=False)['synchronization']
                self.assertEqual(pending['state'], 'pending')
                self.assertFalse(pending['fresh'])
            self.file.write_text(self.file.read_text().replace('> 0', '> 3'))
            verified = api.live_state(self.repo)['synchronization']
            self.assertTrue(verified['verified'])
            self.assertFalse(verified['fresh'])
            self.assertEqual(self.search()['results'], [])

    def test_each_local_publication_builds_one_accelerator_and_prunes_vectors(self):
        import faiss
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        removed = self.root/'removed.py'
        removed.write_text('def retired(): return True\n')
        with patch('knowledge_base.vector_store.faiss.write_index', wraps=faiss.write_index) as build:
            before = self.publish()
            self.assertEqual(build.call_count, 1)
            self.file.write_text(self.file.read_text().replace('> 0', '> 2'))
            removed.unlink()
            after = self.publish()
            self.assertEqual(build.call_count, 2)
        self.assertEqual(after['chunks_embedded'], 1)
        self.assertEqual(after['files_pruned'], 1)
        self.assertEqual(after['chunk_count'], before['chunk_count'] - 1)
        active = self.store.active()['collection']
        removed_filter = Filter(must=[FieldCondition(key='file_path', match=MatchValue(value=removed.name))])
        self.assertEqual(self.client.count(active, count_filter=removed_filter).count, 0)

    def test_storage_events_do_not_change_source_digest(self):
        before = observe_sources(self.root)
        self.publish(); self.search()
        self.assertEqual(observe_sources(self.root), before)
        self.assertEqual((self.store.path/'.gitignore').read_text(), '*\n')

    def test_symlink_storage_is_rejected_before_writing(self):
        outside = self.root.parent/'outside'; outside.mkdir()
        (self.root/'.codemind').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): RepositoryStorage(self.root).prepare()
        self.assertEqual(list(outside.iterdir()), [])

    def test_other_repository_has_independent_storage(self):
        self.publish(); self.search()
        other = self.root.parent/'other'; other.mkdir()
        storage = RepositoryStorage(other)
        cache = RepositoryCache(storage)
        self.assertIsNone(cache.get('semantic-candidates-v1', ['anything']))
        with self.assertRaises(ValueError): RepositoryVectors(storage).collection_exists(self.store.active()['collection'])

    def test_local_mode_rejects_remote_vector_backend(self):
        with self.store.storage.scope(), patch.dict(os.environ, {'QDRANT_URL':'https://invalid.example'}):
            with self.assertRaisesRegex(ValueError, 'Remote Qdrant'): config.get_qdrant_client()

    def test_query_before_first_snapshot_returns_empty_evidence_without_global_fallback(self):
        result = api.search_code(api.SearchRequest(repoId=self.repo, query='Find code that verifies token expiration'))
        self.assertEqual(result['results'], [])

    def test_two_sessions_share_a_single_publication(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.publish(), range(2)))
        self.assertEqual(sum(not r['unchanged'] for r in results), 1)
        self.assertEqual(len({r['activeSnapshotId'] for r in results}), 1)

    def test_cache_limits_expiry_and_clear_do_not_modify_published_database(self):
        self.publish(); saved = self.database().read_bytes()
        cache = RepositoryCache(self.store.storage, max_entries=2, ttl=1)
        for index in range(4): cache.put('test', [index], {'number':index})
        self.assertIsNone(cache.get('test', [0]))
        self.assertEqual(cache.get('test', [3]), {'number':3})
        with patch('knowledge_base.cache.time.time', return_value=10**12):
            self.assertIsNone(cache.get('test', [3]))
        self.assertTrue(api.clear_repository_cache(self.repo)['cleared'])
        self.assertIsNone(cache.get('test', [3]))
        self.assertEqual(self.database().read_bytes(), saved)

    def test_model_version_change_requires_new_snapshot(self):
        self.publish(); previous = self.store.active()['id']
        with patch.object(config, 'EMBEDDING_MODEL_NAME', 'changed-local-model'):
            self.assertFalse(self.store.view()['fresh'])
            self.assertFalse(self.publish()['unchanged'])
            self.assertNotEqual(self.store.active()['id'], previous)

    def test_corrupt_faiss_is_rebuilt_in_memory_without_writing_snapshot(self):
        self.publish(); self.close_vectors()
        accelerator = next(self.database().parent.glob('*.faiss'))
        os.chmod(accelerator, 0o600); accelerator.write_bytes(b'invalid'); os.chmod(accelerator, 0o400)
        self.assertTrue(self.search()['results'])
        self.assertEqual(accelerator.read_bytes(), b'invalid')
