"""Failure and concurrency contracts. Unit vectors are deterministic; the
separate Phase 8 benchmark uses real BGE embeddings without mocking them.
"""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api
import config
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.synchronization import SnapshotStore


class Phase8Tests(unittest.TestCase):
    def setUp(self):
        # Reuse the isolated fixture, not its inherited test methods.
        from test_phase7 import Phase7Tests
        Phase7Tests.setUp(self)

    def publish(self, **options):
        return self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
                                  index_directory=index_directory, build_graph=build_graph, **options)

    def search(self, query, **options):
        return api.search_code(api.SearchRequest(repoId=self.repo, query=query, **options))

    def test_valid_json_with_missing_or_wrong_manifest_fields_fails_closed(self):
        original = self.store.active()
        for key, value in (('sources',None), ('sources',{}), ('collection','unrelated_collection'), ('result',{})):
            with self.subTest(key=key,value=value):
                malformed = {**original, key:value}
                (self.store.path/'active_snapshot.json').write_text(json.dumps(malformed))
                self.assertFalse(self.store.view()['fresh'])
                response = self.search('Where are credentials validated?', retrieval='semantic')
                self.assertEqual(response['results'], [])
                self.assertIn('invalid', response['warning'])

    def test_concurrent_readers_do_not_receive_candidate_or_mixed_generation(self):
        old = self.store.active()['id']
        self.auth.write_text(self.original.replace('> 0','> 4'))
        staged, release = threading.Event(), threading.Event()
        errors = []
        def pause_graph(*args, **kwargs):
            result = build_graph(*args, **kwargs)
            staged.set()
            if not release.wait(10): raise TimeoutError('test candidate was not released')
            return result
        def writer():
            try:
                self.store.publish(client=self.client, collection=config.COLLECTION_NAME,
                                   index_directory=index_directory, build_graph=pause_graph)
            except Exception as error: errors.append(error)
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(staged.wait(10))
            results = []
            def reader():
                try: results.append(self.search('who calls validateToken?'))
                except Exception as error: errors.append(error)
            readers = [threading.Thread(target=reader) for _ in range(4)]
            for reader_thread in readers: reader_thread.start()
            for reader_thread in readers: reader_thread.join(10)
            self.assertEqual(len(results), 4)
            for response in results:
                self.assertEqual(response['results'], [])
                self.assertFalse(response['synchronization']['fresh'])
            self.assertEqual(self.store.active()['id'], old)
        finally:
            release.set(); thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        response = self.search('who calls validateToken?')
        self.assertNotEqual(response['knowledgeSnapshotId'], old)
        self.assertTrue(response['results'])
        self.assertTrue(response['synchronization']['fresh'])

    def test_killed_candidate_retains_pointer_and_restart_recovers(self):
        old = self.store.active()['id']
        self.auth.write_text(self.original.replace('> 0','> 5'))
        marker = Path(self.temp.name)/'candidate-ready'
        script = '''
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import config
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
root, data, marker = map(Path, sys.argv[2:])
repo = config.make_repo_id(str(root))
client = FaissClient(data/'vectors')
def paused(*args, **kwargs):
    result = build_graph(*args, **kwargs)
    marker.write_text('candidate prepared, pointer not advanced')
    sys.stdin.readline()
    return result
with patch('indexer.get_qdrant_client',return_value=client), patch('indexer.get_embedding_model'), patch('indexer.embed_texts',side_effect=lambda texts:[[1.]+[0.]*383 for _ in texts]):
    SnapshotStore(root,repo,data).publish(client=client,collection=config.COLLECTION_NAME,index_directory=index_directory,build_graph=paused)
'''
        child = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1]),
                                  str(self.root), str(self.data), str(marker)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+15
            while not marker.exists() and child.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'candidate worker did not reach its checkpoint')
            self.assertEqual(self.store.active()['id'], old)
        finally:
            if child.poll() is None: child.terminate()
            child.communicate(timeout=10)
        restarted = SnapshotStore(self.root,self.repo,self.data)
        self.assertEqual(restarted.active()['id'], old)
        self.assertFalse(restarted.view()['fresh'])
        self.assertEqual(self.search('who calls validateToken?')['results'], [])
        self.publish()
        self.assertTrue(restarted.view()['fresh'])
        self.assertNotEqual(restarted.active()['id'], old)
        self.assertEqual(len(list((restarted.path/'generations').iterdir())), 2)

    def test_impact_answers_do_not_claim_guaranteed_behaviour(self):
        response = self.search('impact of validateToken')
        self.assertTrue(response['results'])
        self.assertTrue(all('Potentially affected' in r['reason'] for r in response['results']))

    def test_pipeline_upgrade_rebuilds_graph_even_with_unchanged_sources(self):
        previous = self.store.active()
        legacy = dict(previous); legacy.pop('pipelineVersion')
        (self.store.path/'active_snapshot.json').write_text(json.dumps(legacy))
        (self.store.data_for(previous)/self.repo/'generation.json').write_text(json.dumps(legacy))
        self.assertFalse(self.store.view()['fresh'])
        self.assertFalse(self.store.matches(previous['id']))
        self.assertEqual(self.search('who calls validateToken?')['results'], [])
        with patch('indexer.embed_texts',side_effect=AssertionError('Graph-only upgrade must reuse vectors')):
            published = self.publish()
        self.assertFalse(published['unchanged'])
        self.assertEqual(published['chunks_embedded'], 0)
        self.assertNotEqual(self.store.active()['id'], previous['id'])
        self.assertTrue(self.store.view()['fresh'])
        self.assertTrue(self.search('who calls validateToken?')['results'])


if __name__ == '__main__': unittest.main()
