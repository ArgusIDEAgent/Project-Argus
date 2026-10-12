"""Hybrid API contracts using real SQLite/FAISS and independently bound TS edges."""
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
from answers import answer_question
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.metadata import trace
from knowledge_base.vector_store import FaissClient


class Phase6Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'repo'; self.root.mkdir()
        self.data=Path(self.temp.name)/'data'
        (self.root/'auth.ts').write_text('export function validateToken(token: string) {\n // Reject invalid authentication tokens.\n if (!token) throw new Error("TOKEN_INVALID");\n return token.length > 0;\n}\n')
        (self.root/'login.ts').write_text('import {validateToken} from "./auth";\nexport function login(token: string) { return validateToken(token); }\n')
        (self.root/'entry.ts').write_text('import {login} from "./login";\nexport function entry() { return login("ok"); }\n')
        (self.root/'cache.py').write_text('def memoize_result(key, value):\n    """Cache expensive computed results."""\n    return {key:value}\n')
        self.repo=config.make_repo_id(str(self.root)); self.database=self.data/self.repo/'metadata.db'
        self.client=FaissClient(self.data/'vectors'); self.addCleanup(self.client.close)
        api._REGISTERED_REPOS[self.repo]={'rootPath':str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop,self.repo); self.addCleanup(patch.stopall)
        for owner in ('api','indexer'):
            patch(owner+'.get_qdrant_client',return_value=self.client).start()
            patch(owner+'.embed_texts',side_effect=lambda texts:[[1.]+[0.]*383 for _ in texts]).start()
        patch('api.DEFAULT_DATA_DIR',str(self.data)).start(); patch('indexer.get_embedding_model').start()
        self.refresh()

    def refresh(self):
        build_graph(self.root,data_dir=self.data)
        index_directory(str(self.root),repo_id=self.repo,data_dir=str(self.data),build_graph=False)

    def search(self,query,**kwargs):
        return api.search_code(api.SearchRequest(repoId=self.repo,query=query,**kwargs))

    def test_exact_symbol_before_embeddings_and_standard_contract(self):
        with patch.object(api,'embed_texts',side_effect=AssertionError('Exact lookup must not embed')):
            result=self.search('Where is validateToken defined?')
        self.assertEqual((result['mode'],result['status']),('exact','found'))
        item=result['results'][0]
        self.assertEqual(item['symbol'],'validateToken')
        self.assertEqual(item['symbol_id'],item['symbolId'])
        self.assertEqual(item['resolution'],'syntax_only')
        self.assertTrue(item['source_verified'])
        self.assertEqual(item['file'],item['path'])
        self.assertEqual(item['index_snapshot'],trace(self.database,self.repo,item['entityId'])['snapshotId'])

    def test_hybrid_combines_channels_and_deduplicates_source_units(self):
        result=self.search('Where are invalid authentication tokens rejected?',retrieval='hybrid')
        self.assertEqual(result['mode'],'hybrid')
        units=[(r['path'],r['startLine'],r['endLine']) for r in result['results']]
        self.assertEqual(len(units),len(set(units)))
        self.assertTrue(any(set(r['retrievalMethods'])=={'keyword','semantic'} for r in result['results']))
        self.assertTrue(all(r['source_verified'] for r in result['results']))

    def test_same_named_definitions_are_ambiguous_not_guessed(self):
        (self.root/'other.ts').write_text('export function validateToken(token: string) { return false; }\n')
        self.refresh()
        result=self.search('validateToken')
        self.assertEqual(result['status'],'ambiguous')
        self.assertEqual({r['path'] for r in result['results']},{'auth.ts','other.ts'})
        result=self.search('validateToken',filters={'files':['auth.ts']})
        self.assertEqual(result['status'],'found')
        self.assertEqual(len(result['results']),1)

    def test_ambiguous_answer_never_invokes_a_model(self):
        (self.root/'other.ts').write_text('export function validateToken(token: string) { return false; }\n')
        self.refresh()
        with patch('answers.urlopen',side_effect=AssertionError('Ambiguity cannot be resolved by an LLM')):
            result=answer_question(str(self.root),'explain validateToken',self.search('explain validateToken'))
        self.assertEqual(result['answerMode'],'evidence')
        self.assertIn('Several source definitions',result['answer'])

    def test_callers_callees_and_transitive_impact_are_verified(self):
        cases=(('who calls validateToken?',{'login'}),('what does login call?',{'validateToken'}),
               ('what breaks if I change validateToken?',{'login','entry'}))
        for query,names in cases:
            with self.subTest(query=query):
                result=self.search(query)
                self.assertEqual(result['mode'],'graph')
                self.assertEqual({r['name'] for r in result['results']},names)
                self.assertTrue(all(r['resolution']=='resolved' for r in result['results']))
                for row in result['results']:
                    for edge in row['graphEdges']:
                        self.assertEqual(trace(self.database,self.repo,edge)['resolution'],'resolved')
        filtered=self.search('who calls validateToken?',filters={'files':['login.ts']})
        self.assertEqual(filtered['results'][0]['name'],'login')

    def test_imports_reverse_dependencies_and_file_change_impact(self):
        imports=self.search('imports of login.ts')
        self.assertTrue(imports['results'])
        self.assertTrue(all(r['relationship']=='IMPORTS' for r in imports['results']))
        dependencies=self.search('what happens if I modify auth.ts?')
        self.assertEqual({r['path'] for r in dependencies['results']},{'login.ts','entry.ts'})
        self.assertTrue(all(r['relationship']=='DEPENDS_ON' for r in dependencies['results']))

    def test_explanation_expands_verified_callee_definitions(self):
        result=self.search('explain login')
        self.assertEqual(result['results'][0]['name'],'login')
        callee=next(r for r in result['results'] if r['name']=='validateToken')
        self.assertEqual(callee['path'],'auth.ts')
        self.assertIn('graph',callee['retrievalMethods'])
        self.assertEqual(callee['resolution'],'resolved')

    def test_structural_search_never_uses_semantic_similarity_as_an_edge(self):
        with patch.object(api,'embed_texts',side_effect=AssertionError('Structural questions must not embed')):
            result=self.search('who calls nonexistent?')
        self.assertEqual(result['status'],'not_found')
        self.assertEqual(result['results'],[])
        py=self.search('who calls memoize_result?')
        self.assertEqual(py['status'],'not_found')
        self.assertIn('unavailable',py['warning'])

    def test_deleted_stale_and_failed_snapshots_are_rejected(self):
        (self.root/'auth.ts').write_text('export const removed = true;\n')
        for query in ('validateToken','who calls validateToken?','Where are invalid tokens rejected?'):
            result=self.search(query,filters={'files':['auth.ts']})
            self.assertEqual(result['results'],[])
            self.assertEqual(result['status'],'not_found')
        self.refresh()
        with closing(sqlite3.connect(self.database)) as db:
            with db: db.execute("UPDATE index_snapshots SET state='failed' WHERE stage='semantic'")
        self.assertEqual(self.search('Where are expensive computed results cached?',retrieval='hybrid',filters={'languages':['python']})['results'],[])

    def test_python_exact_chunks_keep_null_canonical_symbol_id(self):
        result=self.search('memoize_result')
        self.assertEqual(result['results'][0]['path'],'cache.py')
        self.assertIsNone(result['results'][0]['symbol_id'])
        self.assertEqual(result['results'][0]['resolution'],'syntax_only')

    def test_unknown_explicit_file_never_returns_unrelated_vectors(self):
        with patch.object(api,'embed_texts',side_effect=AssertionError('Missing file must not embed')):
            result=self.search('explain missing.ts')
        self.assertEqual(result['status'],'not_found')
        self.assertEqual(result['results'],[])

    def test_http_retrieve_is_authenticated_and_does_not_call_answer_model(self):
        from fastapi.testclient import TestClient
        with TestClient(api.app,base_url='http://127.0.0.1') as client, patch('answers.urlopen',side_effect=AssertionError('Retrieval must not call an LLM')):
            body={'repoId':self.repo,'query':'validateToken'}
            self.assertEqual(client.post('/retrieve',json=body).status_code,401)
            result=client.post('/retrieve',json=body,headers={'X-CodeMind-Session':api._SESSION_TOKEN})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['status'],'found')
            invalid=client.post('/retrieve',json={**body,'query':'""'},headers={'X-CodeMind-Session':api._SESSION_TOKEN})
            self.assertEqual(invalid.status_code,422)

    def test_ambiguous_basenames_and_modules_require_a_relative_path(self):
        (self.root/'nested').mkdir()
        (self.root/'nested/auth.ts').write_text('export const nestedAuth = true;\n')
        self.refresh()
        self.assertEqual(self.search('explain auth.ts')['status'],'ambiguous')
        self.assertEqual(self.search('imports of auth')['status'],'ambiguous')
        self.assertEqual(self.search('explain ./nested/auth.ts')['results'][0]['path'],'nested/auth.ts')

    def test_graph_depth_bound_and_cycles_do_not_invent_or_loop(self):
        for i in range(4):
            name='hop'+str(i)
            dependency='validateToken' if i==0 else 'hop'+str(i-1)
            module='auth' if i==0 else 'hop'+str(i-1)
            argument='"ok"' if i==0 else ''
            (self.root/(name+'.ts')).write_text(f'import {{{dependency}}} from "./{module}";\nexport function {name}() {{ return {dependency}({argument}); }}\n')
        self.refresh()
        result=self.search('impact of validateToken',limit=25)
        self.assertNotIn('hop3',{r['name'] for r in result['results']})
        self.assertTrue(all(r['graphDistance']<=3 for r in result['results']))
        self.assertIn('bound',result['warning'])
        (self.root/'auth.ts').write_text('import {entry} from "./entry";\nexport function validateToken(token: string) { return entry(); }\n')
        self.refresh()
        result=self.search('impact of validateToken',limit=25)
        self.assertNotIn('validateToken',{r['name'] for r in result['results']})
        self.assertEqual(len({r['name'] for r in result['results']}),len(result['results']))

    def test_keyword_only_retrieval_is_useful_without_any_vector_store(self):
        with patch.object(api,'get_qdrant_client',side_effect=OSError('vector store offline')):
            result=self.search('Where is TOKEN_INVALID raised?',retrieval='hybrid')
        self.assertEqual(result['mode'],'keyword')
        self.assertEqual(result['status'],'found')
        self.assertTrue(all(r['source_verified'] for r in result['results']))
        self.assertIn('unavailable',result['warning'])

    def test_final_verification_discards_a_source_edit_between_channels(self):
        original=api._semantic_search
        def mutate(request):
            result=original(request)
            (self.root/'auth.ts').write_text('export const changed = true;\n')
            return result
        with patch.object(api,'_semantic_search',side_effect=mutate):
            result=self.search('Where are authentication tokens rejected?',retrieval='hybrid',filters={'files':['auth.ts']})
        self.assertEqual(result['results'],[])
        self.assertEqual(result['status'],'not_found')
        self.assertIn('Refresh',result['warning'])

    def test_filters_are_applied_before_graph_result_limit(self):
        (self.root/'z.ts').write_text('import {validateToken} from "./auth";\nexport function finalCaller() { return validateToken("ok"); }\n')
        self.refresh()
        result=self.search('callers of validateToken',limit=1,filters={'files':['z.ts']})
        self.assertEqual(result['results'][0]['name'],'finalCaller')


if __name__=='__main__': unittest.main()
