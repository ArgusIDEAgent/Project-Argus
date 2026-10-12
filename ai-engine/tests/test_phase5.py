"""Real FAISS/FTS storage with deterministic embeddings for API contracts."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api
import config
from indexer import index_directory
from lexical_search import search_exact_code
from knowledge_base.graph import build_graph
from knowledge_base.metadata import audit
from knowledge_base.vector_store import FaissClient


class Phase5Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'repo'
        self.root.mkdir()
        self.data=Path(self.temp.name)/'data'
        (self.root/'auth.ts').write_text('export function authorizeRequest(role: string) {\n if (role !== "admin") throw new Error("ACCESS_DENIED");\n return true;\n}\nexport function drawChart() { return "chart"; }\n')
        (self.root/'cache.py').write_text('def memoize_result(key, value):\n    """Cache a computed value by key."""\n    return {key: value}\n')
        (self.root/'settings.json').write_text('{\n "port": 8000,\n "requireAuth": true\n}\n')
        self.repo=config.make_repo_id(str(self.root))
        self.database=self.data/self.repo/'metadata.db'
        build_graph(self.root,data_dir=self.data)
        self.client=FaissClient(self.data/'vectors')
        self.addCleanup(self.client.close)
        api._REGISTERED_REPOS[self.repo]={'rootPath':str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop,self.repo)
        self.addCleanup(patch.stopall)
        for owner in (api,):
            patch.object(owner,'get_qdrant_client',return_value=self.client).start()
            patch.object(owner,'DEFAULT_DATA_DIR',str(self.data)).start()
            patch.object(owner,'embed_texts',side_effect=lambda texts:[[1.]+[0.]*383 for _ in texts]).start()
        patch('indexer.get_qdrant_client',return_value=self.client).start()
        patch('indexer.get_embedding_model').start()
        patch('indexer.embed_texts',side_effect=lambda texts:[[1.]+[0.]*383 for _ in texts]).start()
        self.refresh()

    def refresh(self):
        return index_directory(str(self.root),repo_id=self.repo,data_dir=str(self.data),build_graph=False)

    def search(self,query='Where are permissions checked?',**kwargs):
        return api.search_code(api.SearchRequest(repoId=self.repo,query=query,**kwargs))

    def test_filters_files_modules_languages_symbol_types(self):
        cases=({'files':['auth.ts']},{'modules':['auth']},{'languages':['typescript']},{'symbolTypes':['function']})
        for filters in cases:
            with self.subTest(filters=filters):
                result=self.search(retrieval='semantic',filters=filters)
                self.assertTrue(result['results'])
                if filters.get('symbolTypes'):
                    self.assertTrue(all(r['symbolType']=='function' for r in result['results']))
                else:
                    self.assertTrue(all(r['path']=='auth.ts' for r in result['results']))
                    self.assertTrue(all(r['symbolId'] for r in result['results']))
        self.assertEqual(self.search(retrieval='semantic',filters={'files':['absent.ts']})['results'],[])
        self.assertEqual(self.search(retrieval='semantic',filters={'languages':['python'],'symbolTypes':['class']})['results'],[])

    def test_keyword_error_routes_and_config_are_source_grounded(self):
        result=self.search('ACCESS_DENIED',retrieval='keyword',filters={'files':['auth.ts']})
        self.assertEqual(result['mode'],'keyword')
        self.assertEqual(result['results'][0]['name'],'authorizeRequest')
        self.assertEqual(result['results'][0]['startLine'],1)
        config_result=self.search('requireAuth',retrieval='keyword',filters={'languages':['json']})
        self.assertEqual(config_result['results'][0]['path'],'settings.json')

    def test_keyword_fallback_survives_embedding_failure(self):
        with patch.object(api,'embed_texts',side_effect=RuntimeError('model offline')):
            result=self.search('Where is ACCESS_DENIED raised?')
        self.assertEqual(result['mode'],'keyword')
        self.assertTrue(result['results'])
        self.assertIn('keyword',result['warning'])

    def test_stale_keyword_and_vector_sources_are_not_returned(self):
        (self.root/'auth.ts').write_text('export const removed = true;\n')
        for retrieval in ('keyword','semantic'):
            self.assertEqual(self.search('ACCESS_DENIED',retrieval=retrieval,filters={'files':['auth.ts']})['results'],[])

    def test_incremental_ids_retain_functions_and_prune_removed_units(self):
        before=self.search(retrieval='semantic',filters={'files':['auth.ts']})['results']
        identities={r['name']:r['entityId'] for r in before}
        source=self.root/'auth.ts'
        source.write_text('// inserted header\n'+source.read_text().replace('admin','editor').split('export function drawChart')[0])
        self.refresh()
        after=self.search(retrieval='semantic',filters={'files':['auth.ts']})['results']
        self.assertEqual(next(r['entityId'] for r in after if r['name']=='authorizeRequest'),identities['authorizeRequest'])
        self.assertFalse(self.client.retrieve(config.COLLECTION_NAME,[identities['drawChart']]))
        self.assertEqual(audit(self.database)['dangling_references'],[])

    def test_similarity_does_not_create_call_or_dependency_edges(self):
        with closing(sqlite3.connect(self.database)) as db:
            before=db.execute("SELECT id FROM graph_relationships WHERE relationship IN ('CALLS','DEPENDS_ON')").fetchall()
        self.refresh()
        self.search(retrieval='semantic')
        self.search('authorize request',retrieval='keyword')
        with closing(sqlite3.connect(self.database)) as db:
            after=db.execute("SELECT id FROM graph_relationships WHERE relationship IN ('CALLS','DEPENDS_ON')").fetchall()
        self.assertEqual(before,after)

    def test_filter_path_escape_is_rejected(self):
        with self.assertRaises(ValueError):
            api.SearchRequest(repoId=self.repo,query='x',filters={'files':['../outside.py']})

    def test_sensitive_configuration_is_excluded_from_all_retrieval_paths(self):
        (self.root/'.env.json').write_text('{"privateValue": "PRIVATE_CONFIG_MARKER"}')
        self.refresh()
        self.assertEqual(search_exact_code(str(self.root),self.repo,'PRIVATE_CONFIG_MARKER',5),[])
        self.assertEqual(self.search('PRIVATE_CONFIG_MARKER',retrieval='keyword')['results'],[])
        self.assertEqual(self.search(retrieval='semantic',filters={'files':['.env.json']})['results'],[])


if __name__=='__main__':
    unittest.main()
