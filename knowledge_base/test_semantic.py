"""Phase 5 code-unit boundaries, FAISS persistence and FTS5 contracts."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from knowledge_base.chunking import chunk_source
from knowledge_base.identity import repository_id
from knowledge_base.metadata import SemanticIndex, audit, trace
from knowledge_base.parser import parse_repository
from knowledge_base.semantic import keyword_search
from knowledge_base.vector_store import FaissClient


class ChunkTests(unittest.TestCase):
    def chunks(self,source,path='auth.ts',size=1200):
        return chunk_source(source,path=path,repo_id='a'*24,language='typescript',max_chars=size,overlap=20)

    def test_functions_are_distinct_not_fixed_size_blobs(self):
        source='export function authorizeRequest(role: string) { return role === "admin"; }\nexport function drawChart() { return "chart"; }\n'
        chunks=self.chunks(source)
        funcs=[c for c in chunks if c.kind=='symbol']
        self.assertEqual([c.name for c in funcs],['authorizeRequest','drawChart'])
        self.assertNotIn('drawChart',funcs[0].text)
        self.assertNotIn('authorizeRequest',funcs[1].text)
        self.assertIn('authorize Request',funcs[0].embedding_text('auth.ts'))
        shifted=self.chunks('// new header\n'+source.replace('admin','editor'))
        self.assertEqual({c.name:c.id for c in funcs},{c.name:c.id for c in shifted if c.kind=='symbol'})

    def test_large_function_children_keep_parent_and_source_ranges(self):
        source='function verifyToken() {\n'+''.join(f'  const item{i} = {i};\n' for i in range(70))+'}\n'
        chunks=[c for c in self.chunks(source,size=180) if c.name=='verifyToken']
        self.assertGreater(len(chunks),5)
        self.assertEqual(len({c.symbol_key for c in chunks}),1)
        self.assertTrue(all(c.kind=='symbol_child' and len(c.text)<=180 for c in chunks))
        lines=source.splitlines()
        for chunk in chunks:
            self.assertIn(chunk.text,'\n'.join(lines[chunk.start_line-1:chunk.end_line]))

    def test_attached_comments_do_not_compete_with_their_function(self):
        chunks=self.chunks('import {check} from "./checks";\n/** Check resource permissions. */\nexport function authorize() { return check(); }\n')
        self.assertEqual(len(chunks),2)
        symbol=next(c for c in chunks if c.name=='authorize')
        self.assertIn('resource permissions',symbol.comments)
        self.assertEqual(symbol.start_line,3)
        self.assertEqual(next(c for c in chunks if c.kind=='module_context').text,'import {check} from "./checks";')

    def test_nested_python_functions_are_not_class_methods(self):
        chunks=self.chunks('def outer():\n    def inner():\n        return True\n    return inner()\n',path='nested.py')
        self.assertEqual(next(c for c in chunks if c.name=='inner').symbol_kind,'function')

    def test_json_sections_keep_nested_configuration_together(self):
        chunks=self.chunks('{\n  "server": {\n    "port": 8000,\n    "host": "localhost"\n  },\n  "auth": {"required": true}\n}\n',path='settings.json')
        server=next(c for c in chunks if c.name.startswith('"server"'))
        self.assertIn('"port"',server.text)
        self.assertIn('"host"',server.text)
        self.assertFalse(any(c.name.startswith('"port"') for c in chunks))

    def test_python_methods_docstrings_and_unicode(self):
        source='"""Service entry point."""\nclass Session:\n    def authorize(self, role):\n        """Reject requests without resource permissions."""\n        return role == "管理员"\n\ndef render():\n    return "chart"\n'
        chunks=self.chunks(source,path='auth.py')
        method=next(c for c in chunks if c.qualified_name=='Session.authorize')
        self.assertEqual(method.symbol_kind,'method')
        self.assertIn('resource permissions',method.comments)
        self.assertEqual((method.start_line,method.end_line),(3,5))
        self.assertTrue(any(c.kind=='module_context' and 'Service entry' in c.text for c in chunks))

    def test_markdown_configuration_and_invalid_syntax_fallback(self):
        docs=self.chunks('# Authentication\nCheck permissions.\n# Charts\nPlot values.\n',path='README.md')
        self.assertEqual(len(docs),2)
        self.assertTrue(all(c.kind=='documentation' for c in docs))
        config=self.chunks('[server]\nport=8000\n[auth]\nrequired=true\n',path='settings.toml')
        self.assertEqual(len(config),2)
        invalid=self.chunks('def broken(:\n  pass',path='bad.py')
        self.assertEqual(invalid[0].kind,'syntax_fallback')

    def test_same_named_classes_methods_and_duplicates_do_not_collide(self):
        source='class One { check() { return true; } }\nclass Two { check() { return false; } }\n'
        methods=[c for c in self.chunks(source) if c.symbol_kind=='method']
        self.assertEqual({c.qualified_name for c in methods},{'One.check','Two.check'})
        self.assertEqual(len({c.id for c in methods}),2)


class FaissTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'vectors'
        self.client=FaissClient(self.path)
        self.addCleanup(lambda:self.client.close())
        self.client.create_collection('code',{'size':3,'distance':'Cosine'})
        self.client.upsert('code',[PointStruct(id='a',vector=[1.,0.,0.],payload={'repo_id':'repo-a','file_path':'auth.ts'}),
            PointStruct(id='b',vector=[0.,1.,0.],payload={'repo_id':'repo-b','file_path':'auth.ts'})])

    def test_cosine_repository_filter_and_restart(self):
        scope=Filter(must=[FieldCondition(key='repo_id',match=MatchValue(value='repo-a'))])
        result=self.client.query_points('code',[1.,0.,0.],query_filter=scope)
        self.assertEqual([r.id for r in result.points],['a'])
        self.assertAlmostEqual(result.points[0].score,1.)
        self.client.close()
        self.client=FaissClient(self.path)
        self.assertEqual(self.client.query_points('code',[0.,1.,0.]).points[0].id,'b')
        self.assertTrue(list(self.path.glob('*.faiss')))

    def test_delete_and_corrupted_cache_recovery(self):
        self.client.delete('code',['a'])
        self.assertEqual(self.client.count('code').count,1)
        self.client.close()
        for path in self.path.glob('*.faiss'):
            path.write_bytes(b'invalid cache')
        self.client=FaissClient(self.path)
        result=self.client.query_points('code',[0.,1.,0.])
        self.assertEqual([r.id for r in result.points],['b'])

    def test_bad_dimensions_nan_zero_and_filter_are_rejected(self):
        for vector in ([1.,0.],[float('nan'),0.,1.],[0.,0.,0.]):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                self.client.upsert('code',[PointStruct(id='bad',vector=vector,payload={})])
        self.assertEqual(self.client.count('code').count,2)

    def test_multiple_clients_observe_writes_and_stable_mapping(self):
        second=FaissClient(self.path)
        self.addCleanup(second.close)
        ids=self.client.db.execute('SELECT chunk_id,vector_id FROM vectors ORDER BY chunk_id').fetchall()
        second.upsert('code',[PointStruct(id='a',vector=[1.,1.,0.],payload={'repo_id':'repo-a'})])
        self.assertEqual(ids,self.client.db.execute('SELECT chunk_id,vector_id FROM vectors ORDER BY chunk_id').fetchall())
        self.assertEqual(self.client.retrieve('code',['a'])[0].payload,{'repo_id':'repo-a'})


class KeywordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'repo'
        self.root.mkdir()
        self.data=Path(self.temp.name)/'data'
        self.file=self.root/'auth.ts'
        self.file.write_text('export function authorizeRequest(role: string) {\n  if (role !== "admin") throw new Error("ACCESS_DENIED");\n  return true;\n}\nexport function drawChart() { return "chart"; }\n')
        self.repo=repository_id(self.root)
        parse_repository(self.root,data_dir=self.data)
        self.database=self.data/self.repo/'metadata.db'
        self.publish()

    def publish(self):
        source=self.file.read_text()
        contexts=chunk_source(source,path='auth.ts',repo_id=self.repo,language='typescript')
        with SemanticIndex(self.root,self.repo,self.data) as metadata:
            records=metadata.records('auth.ts',hashlib.sha256(self.file.read_bytes()).hexdigest(),
                [(c.text,c.start_line,c.end_line) for c in contexts],'code',contexts=contexts)
            metadata.publish('auth.ts',records,contexts=contexts,embedding_model='test')

    def test_identifier_error_and_filters_with_valid_sources(self):
        for query in ('authorizeRequest','ACCESS_DENIED','authorize request'):
            rows=keyword_search(self.database,self.repo,query,filters={'languages':['typescript'],'symbolTypes':['function']})
            self.assertTrue(rows)
            self.assertEqual(rows[0]['symbol_name'],'authorizeRequest')
            self.assertEqual(trace(self.database,self.repo,rows[0]['id'])['path'],'auth.ts')
            self.assertIsNotNone(rows[0]['symbol_id'])
        self.assertEqual(keyword_search(self.database,'b'*24,'ACCESS_DENIED'),[])
        self.assertEqual(keyword_search(self.database,self.repo,'ACCESS_DENIED',filters={'files':['other.ts']}),[])
        self.assertEqual(keyword_search(self.database,self.repo,'ACCESS_DENIED',filters={'modules':['other']}),[])

    def test_ready_snapshot_and_updates_remove_old_fts_terms(self):
        self.file.write_text('export function authorizeRequest() { return "NEW_ERROR"; }\n')
        parse_repository(self.root,data_dir=self.data)
        self.publish()
        self.assertEqual(keyword_search(self.database,self.repo,'ACCESS_DENIED'),[])
        rows=keyword_search(self.database,self.repo,'NEW_ERROR')
        self.assertTrue(rows)
        with closing(sqlite3.connect(self.database)) as db:
            with db:
                db.execute("UPDATE index_snapshots SET state='failed' WHERE stage='semantic'")
        self.assertEqual(keyword_search(self.database,self.repo,'NEW_ERROR'),[])
        self.assertEqual(audit(self.database)['dangling_references'],[])

    def test_user_fts_syntax_is_treated_as_data(self):
        for query in ('"','NEAR(foo bar)', 'ACCESS_DENIED OR " ) --'):
            self.assertIsInstance(keyword_search(self.database,self.repo,query),list)


if __name__=='__main__':
    unittest.main()
