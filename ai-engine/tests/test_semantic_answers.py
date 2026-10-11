"""Semantic retrieval regression tests with isolated local vector storage."""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import config
from answers import answer_question, evidence_for
from indexer import index_directory
from knowledge_base.graph import build_graph
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.data = Path(self.temp.name) / "data"
        self.file = self.root / "auth.ts"
        self.file.write_text("export function validateToken(token: string) { return token.length > 0; }\nexport function login() { return validateToken('ok'); }\n")
        self.repo_id = config.make_repo_id(str(self.root))
        api._REGISTERED_REPOS[self.repo_id] = {"rootPath": str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop, self.repo_id)
        self.client = QdrantClient(':memory:')
        self.addCleanup(self.client.close)
        self.addCleanup(patch.stopall)
        patch.object(api, 'get_qdrant_client', return_value=self.client).start()
        patch.object(api, 'DEFAULT_DATA_DIR', str(self.data)).start()
        # Deterministic vectors test storage/filtering independently of model quality.
        patch.object(api, 'embed_texts', side_effect=lambda texts: [[1.0]+[0.0]*383 for _ in texts]).start()
        with patch('indexer.get_qdrant_client', return_value=self.client), patch('indexer.embed_texts', side_effect=lambda texts: [[1.0]+[0.0]*383 for _ in texts]), patch('indexer.get_embedding_model'):
            index_directory(str(self.root), repo_id=self.repo_id, data_dir=str(self.data), build_graph=False)

    def search(self, query):
        return api.search_code(api.SearchRequest(repoId=self.repo_id, query=query))

    def test_natural_language_uses_vectors(self):
        (self.root / 'question_test.py').write_text('question = "Where is user authentication checked?"\n')
        result = self.search('Where is user authentication checked?')
        self.assertEqual(result['mode'], 'semantic')
        self.assertEqual(result['results'][0]['path'], 'auth.ts')
        self.assertIsNone(result['warning'])

    def test_repository_isolation(self):
        other_id = 'b'*24
        api._REGISTERED_REPOS[other_id] = {'rootPath': str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop, other_id)
        result = api.search_code(api.SearchRequest(repoId=other_id, query='Where is authentication checked?'))
        self.assertEqual(result['results'], [])

    def test_low_similarity_is_not_presented_as_relevant_code(self):
        with patch.object(api, 'embed_texts', return_value=[[0.0, 1.0]+[0.0]*382]):
            result = self.search('Where is a completely unrelated feature?')
        self.assertEqual(result['results'], [])
        self.assertIn('No sufficiently relevant', result['warning'])

    def test_missing_repo_vectors_rebuild_even_with_matching_index_state(self):
        self.client.delete(config.COLLECTION_NAME, points_selector=Filter(must=[
            FieldCondition(key='repo_id', match=MatchValue(value=self.repo_id))]))
        with patch('indexer.get_qdrant_client', return_value=self.client), patch('indexer.embed_texts', side_effect=lambda texts: [[1.0]+[0.0]*383 for _ in texts]), patch('indexer.get_embedding_model'):
            result = index_directory(str(self.root), repo_id=self.repo_id, data_dir=str(self.data), build_graph=False)
        self.assertEqual(result['files_indexed'], 1)
        self.assertTrue(self.search('Where is authentication checked?')['results'])

    def test_embedded_storage_survives_restart_and_worker_threads(self):
        with patch.object(config, 'DEFAULT_DATA_DIR', str(self.data)), patch.dict(os.environ, {'QDRANT_URL': ''}):
            client = config._create_vector_client()
            client.create_collection('persist', vectors_config={'size': 384, 'distance': 'Cosine'})
            client.close()
            reopened = config._create_vector_client()
            try:
                with ThreadPoolExecutor(max_workers=4) as workers:
                    self.assertEqual(list(workers.map(lambda _: reopened.collection_exists('persist'), range(8))), [True]*8)
            finally:
                reopened.close()

    def test_changed_and_deleted_files_never_return_stale_citations(self):
        self.file.write_text('export const changed = 1;\n')
        self.assertEqual(self.search('Where is authentication checked?')['results'], [])
        self.file.unlink()
        self.assertEqual(self.search('Where is authentication checked?')['results'], [])

    def test_grounded_answer_and_local_model_failure_fallback(self):
        retrieval = self.search('Where is authentication checked?')
        with patch('answers.urlopen', side_effect=OSError('offline')):
            result = answer_question(str(self.root), 'Where is authentication checked?', retrieval)
        self.assertIn('auth.ts:', result['answer'])
        self.assertEqual(result['answerMode'], 'evidence')
        self.assertIn('Local answer model', result['warning'])
        self.file.write_text('changed')
        self.assertEqual(evidence_for(str(self.root), retrieval['results']), ([], []))

    def test_local_model_receives_redacted_evidence_and_returns_answer(self):
        self.file.write_text('const api_key = "abcdefghijk-secret";\n')
        item = {'path': 'auth.ts', 'startLine': 1, 'endLine': 1,
                'sourceHash': hashlib.sha256(self.file.read_bytes()).hexdigest(), 'reason': 'Exact match'}
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return json.dumps({'message': {'content': json.dumps({'answer': 'Local explanation.', 'citations': [1]})}}).encode()
        with patch('answers.urlopen', return_value=Reply()) as model:
            result = answer_question(str(self.root), 'explain', {'results': [item], 'warning': None, 'mode': 'lexical'})
        self.assertEqual(result['answerMode'], 'local-llm')
        sent = model.call_args.args[0]
        self.assertEqual(sent.full_url, 'http://127.0.0.1:11434/api/chat')
        self.assertNotIn('abcdefghijk-secret', sent.data.decode())
        self.assertIn('[REDACTED]', sent.data.decode())

    def test_verified_graph_callers_and_graph_file(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        build_graph(self.root, data_dir=self.data)
        result = self.search('who calls validateToken?')
        self.assertEqual(result['mode'], 'graph')
        self.assertEqual(result['results'][0]['name'], 'login')
        self.assertEqual(result['results'][0]['startLine'], 2)
        self.assertTrue(api.graph_file('auth.ts', self.repo_id)['nodes'])
        self.assertEqual(self.search('validateToken')['results'][0]['callers'][0]['name'], 'login')
        self.file.write_text('changed')
        self.assertEqual(self.search('who calls validateToken?')['results'], [])

    def test_http_auth_and_unknown_repo(self):
        from fastapi.testclient import TestClient
        with patch.object(api, '_SESSION_TOKEN', 'test-token'):
            client = TestClient(api.app, base_url='http://127.0.0.1')
            self.assertEqual(client.post('/ask', json={'repoId': self.repo_id, 'query': 'auth'}).status_code, 401)
            self.assertEqual(client.post('/ask', headers={'X-Codemind-Session': 'test-token'}, json={'repoId': 'c'*24, 'query': 'auth'}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
