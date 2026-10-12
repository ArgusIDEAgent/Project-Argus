"""Phase 4 identity, provenance, migration and publication regression tests."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from knowledge_base.graph import build_graph, KnowledgeGraph
from knowledge_base.identity import chunk_id, file_id, repository_id, relative_path
from knowledge_base.metadata import SCHEMA_VERSION, SemanticIndex, audit, ensure_schema, trace
from knowledge_base.parser import parse_repository
from knowledge_base.scanner import scan_repository


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'repo '
        self.root.mkdir()
        self.data = Path(self.temp.name)/'private'
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.path = self.root/'src/auth.ts'
        self.path.parent.mkdir()
        self.path.write_text('export function validateToken(token: string) {\n  return token.length > 0;\n}\nexport function login() { return validateToken("ok"); }\n')
        self.repo = repository_id(self.root)
        self.database = self.data/self.repo/'metadata.db'

    def connection(self):
        db = sqlite3.connect(self.database)
        db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(db.close)
        return db

    def test_normalized_identity_and_compatibility(self):
        self.assertEqual(file_id(self.repo,'src/auth.ts'),
                         'File:'+hashlib.sha256(json.dumps((self.repo,'src/auth.ts')).encode()).hexdigest())
        for bad in ('../auth.ts','/src/auth.ts','src/../auth.ts','src//auth.ts','./auth.ts','src\\auth.ts','.'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                relative_path(bad)
        self.assertNotEqual(chunk_id(self.repo,'src/auth.ts',0),chunk_id('b'*24,'src/auth.ts',0))

    def test_symbols_occurrences_and_graph_are_traceable(self):
        result = build_graph(self.root,data_dir=self.data)
        db = self.connection()
        self.assertEqual(audit(self.database)['dangling_references'],[])
        self.assertEqual(audit(self.database)['schema_version'],SCHEMA_VERSION)
        for identifier, in db.execute('SELECT id FROM graph_nodes UNION SELECT id FROM relationships'):
            info = trace(self.database,self.repo,identifier)
            self.assertEqual(info['sourceOrigin'],'disk')
            self.assertTrue(info.get('structural') or info.get('fileId'))
        sid = db.execute("SELECT id FROM symbols WHERE name='validateToken'").fetchone()[0]
        definition = trace(self.database,self.repo,sid)
        self.assertEqual(definition['path'],'src/auth.ts')
        self.assertEqual((definition['startLine'],definition['endLine']),(1,3))
        references = db.execute("SELECT count(*) FROM symbol_occurrences WHERE symbol_id=? AND kind!='definition'",(sid,)).fetchone()[0]
        self.assertGreater(references,0)
        self.assertEqual(result['graph_snapshot_id'], db.execute("SELECT snapshot_id FROM relationships LIMIT 1").fetchone()[0])

    def test_unchanged_rescan_retains_chunk_and_symbol_binding(self):
        parse_repository(self.root,data_dir=self.data)
        source_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with SemanticIndex(self.root,self.repo,self.data) as metadata:
            records = metadata.records('src/auth.ts',source_hash,[('return token.length > 0;',2,2)],'code')
            metadata.publish('src/auth.ts',records)
        self.assertIsNotNone(records[0][3])
        before = trace(self.database,self.repo,records[0][0])
        parse_repository(self.root,data_dir=self.data)
        after = trace(self.database,self.repo,records[0][0])
        self.assertEqual(before['fileId'],after['fileId'])
        self.assertEqual(before['symbolId'],after['symbolId'])
        self.assertEqual(before['snapshotId'],after['snapshotId'])

    def test_edit_preserves_identity_but_invalidates_old_source(self):
        parse_repository(self.root,data_dir=self.data)
        db = self.connection()
        sid = db.execute("SELECT id FROM symbols WHERE name='validateToken'").fetchone()[0]
        self.path.write_text('// inserted header\n'+self.path.read_text().replace('> 0','> 1'))
        with self.assertRaisesRegex(ValueError,'changed'):
            trace(self.database,self.repo,sid)
        parse_repository(self.root,data_dir=self.data)
        self.assertEqual(sid,db.execute("SELECT id FROM symbols WHERE name='validateToken'").fetchone()[0])
        self.assertEqual(trace(self.database,self.repo,sid)['startLine'],2)

    def test_delete_cascades_without_dangling_entities(self):
        build_graph(self.root,data_dir=self.data)
        self.path.unlink()
        scan_repository(self.root,data_dir=self.data)
        db = self.connection()
        self.assertEqual(db.execute('SELECT count(*) FROM symbols').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT count(*) FROM relationships WHERE file_id IS NOT NULL').fetchone()[0],0)
        self.assertEqual(audit(self.database)['dangling_references'],[])

    def test_foreign_keys_cross_repository_and_transaction_rollback(self):
        build_graph(self.root,data_dir=self.data)
        db = self.connection()
        edge = db.execute('SELECT * FROM relationships LIMIT 1').fetchone()
        with self.assertRaises(sqlite3.IntegrityError), db:
            db.execute('UPDATE relationships SET target_id=? WHERE id=?',('missing-node',edge[0]))
        self.assertEqual(db.execute('SELECT target_id FROM relationships WHERE id=?',(edge[0],)).fetchone()[0],edge[3])
        with self.assertRaises(sqlite3.IntegrityError), db:
            db.execute('UPDATE relationships SET repository_id=? WHERE id=?',('other',edge[0]))
        with self.assertRaises(sqlite3.IntegrityError), db:
            db.execute("UPDATE index_snapshots SET source_origin='buffer'")

    def test_failed_vector_snapshot_is_not_retrievable(self):
        parse_repository(self.root,data_dir=self.data)
        source_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'vector failure'):
            with SemanticIndex(self.root,self.repo,self.data) as metadata:
                records = metadata.records('src/auth.ts',source_hash,[('return token.length > 0;',2,2)],'code')
                metadata.publish('src/auth.ts',records)
                raise RuntimeError('vector failure')
        with self.assertRaisesRegex(ValueError,'not ready'):
            trace(self.database,self.repo,records[0][0])
        db = self.connection()
        self.assertEqual(db.execute("SELECT count(*) FROM index_errors WHERE stage='semantic'").fetchone()[0],1)

    def test_migrate_old_inventory_and_reject_future_version(self):
        self.database.parent.mkdir(parents=True)
        db = self.connection()
        db.executescript('''
            CREATE TABLE repositories(id TEXT PRIMARY KEY,root_path TEXT NOT NULL,git_revision TEXT,last_scanned_at TEXT NOT NULL,schema_version INTEGER NOT NULL);
            CREATE TABLE files(repository_id TEXT NOT NULL REFERENCES repositories(id),relative_path TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,extension TEXT,language TEXT,size_bytes INTEGER,content_sha256 TEXT,parse_status TEXT NOT NULL,PRIMARY KEY(repository_id,relative_path));
        ''')
        db.execute('INSERT INTO repositories VALUES (?,?,NULL,?,2)',(self.repo,str(self.root),'old'))
        db.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?)',(self.repo,'src/auth.ts','source','included','eligible','.ts','typescript',self.path.stat().st_size,hashlib.sha256(self.path.read_bytes()).hexdigest(),'pending'))
        db.commit()
        ensure_schema(db)
        ensure_schema(db)
        self.assertEqual(db.execute('SELECT id FROM files').fetchone()[0],file_id(self.repo,'src/auth.ts'))
        self.assertEqual(db.execute('SELECT count(*) FROM index_snapshots').fetchone()[0],1)
        self.assertEqual(audit(self.database)['dangling_references'],[])
        db.execute('PRAGMA user_version=6')
        with self.assertRaisesRegex(ValueError,'newer'):
            ensure_schema(db)

    def test_semantic_discovery_respects_ignore_and_sensitive_paths(self):
        (self.root/'.gitignore').write_text('ignored.py\n')
        (self.root/'ignored.py').write_text('print("ignored")')
        (self.root/'.env').write_text('password=fixture')
        (self.root/'notes.md').write_text('Useful project description')
        with SemanticIndex(self.root,self.repo,self.data) as metadata:
            self.assertNotIn('ignored.py',metadata.entries)
            self.assertNotIn('.env',metadata.entries)
            self.assertIn('notes.md',metadata.entries)

    def test_trace_rejects_replaced_symlink(self):
        parse_repository(self.root,data_dir=self.data)
        sid = self.connection().execute('SELECT id FROM symbols LIMIT 1').fetchone()[0]
        copy = self.root/'copy.ts'
        copy.write_bytes(self.path.read_bytes())
        self.path.unlink()
        self.path.symlink_to(copy)
        with self.assertRaisesRegex(ValueError,'saved source'):
            trace(self.database,self.repo,sid)

    def test_vector_only_edit_does_not_make_an_old_graph_appear_fresh(self):
        build_graph(self.root,data_dir=self.data)
        self.path.write_text(self.path.read_text().replace('> 0','> 1'))
        source_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with SemanticIndex(self.root,self.repo,self.data) as metadata:
            records = metadata.records('src/auth.ts',source_hash,[('return token.length > 1;',2,2)],'code')
            metadata.publish('src/auth.ts',records)
        with self.assertRaisesRegex(RuntimeError,'stale'):
            KnowledgeGraph(self.database,self.repo).symbol('validateToken')
        self.assertIsNone(trace(self.database,self.repo,records[0][0])['symbolId'])

    def test_v4_graph_migration_backfills_trace_without_changing_ids(self):
        build_graph(self.root,data_dir=self.data)
        db = self.connection()
        node_ids = [r[0] for r in db.execute('SELECT id FROM graph_nodes')]
        db.execute('PRAGMA foreign_keys=OFF')
        for trigger in ('chunk_symbol_repo_insert','chunk_symbol_repo_update','file_graph_cleanup','symbol_graph_cleanup'):
            db.execute(f'DROP TRIGGER {trigger}')
        for table in ('symbol_sources','symbol_occurrences','graph_node_sources','relationships','code_chunks','index_errors','index_snapshots'):
            db.execute(f'DROP TABLE {table}')
        db.execute('UPDATE repositories SET schema_version=4')
        db.execute('PRAGMA user_version=0')
        db.commit()
        db.execute('PRAGMA foreign_keys=ON')
        ensure_schema(db)
        self.assertEqual(node_ids,[r[0] for r in db.execute('SELECT id FROM graph_nodes')])
        for identifier in node_ids:
            self.assertEqual(trace(self.database,self.repo,identifier)['schemaVersion'],SCHEMA_VERSION)
        self.assertEqual(audit(self.database)['dangling_references'],[])


if __name__ == '__main__':
    unittest.main()
