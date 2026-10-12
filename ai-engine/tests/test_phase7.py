"""Phase 7: immutable publication, incremental work and saved-file watching."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import api
import config
from indexer import index_directory
from knowledge_base.graph import build_graph
from knowledge_base.metadata import audit
from knowledge_base.synchronization import SnapshotStore, SyncManager, observe_sources
from knowledge_base.vector_store import FaissClient


class Phase7Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'repo'; self.root.mkdir()
        self.data = Path(self.temp.name)/'data'
        self.auth = self.root/'auth.ts'
        self.original = 'export function validateToken(token: string) { return token.length > 0; }\nexport function stableHelper() { return "stable"; }\n'
        self.auth.write_text(self.original)
        (self.root/'login.ts').write_text('import {validateToken} from "./auth";\nexport function login() { return validateToken("ok"); }\n')
        (self.root/'entry.ts').write_text('import {login} from "./login";\nexport function entry() { return login(); }\n')
        self.repo = config.make_repo_id(str(self.root))
        self.store = SnapshotStore(self.root,self.repo,self.data)
        self.client = FaissClient(self.data/'vectors'); self.addCleanup(self.client.close)
        self.addCleanup(patch.stopall)
        api._REGISTERED_REPOS[self.repo] = {'rootPath':str(self.root)}
        self.addCleanup(api._REGISTERED_REPOS.pop,self.repo)
        for owner in ('api','indexer'):
            patch(owner+'.get_qdrant_client',return_value=self.client).start()
            patch(owner+'.embed_texts',side_effect=lambda texts:[[1.]+[0.]*383 for _ in texts]).start()
        patch('api.DEFAULT_DATA_DIR',str(self.data)).start()
        patch('indexer.get_embedding_model').start()
        patch.dict(os.environ,{'CODEMIND_AUTO_SYNC':'0','CODEMIND_LOCAL_MODEL':''}).start()
        self.publish()

    def publish(self,**options):
        return self.store.publish(client=self.client,collection=config.COLLECTION_NAME,
            index_directory=index_directory,build_graph=build_graph,**options)

    def search(self,query,**options):
        return api.search_code(api.SearchRequest(repoId=self.repo,query=query,**options))

    def database(self):
        return self.store.data_for(self.store.active())/self.repo/'metadata.db'

    def test_noop_does_not_parse_or_embed_or_publish_another_generation(self):
        before = self.store.active()['id']
        with patch('indexer.embed_texts',side_effect=AssertionError('Unchanged chunks must not embed')):
            result = self.publish()
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['chunks_embedded'],0)
        self.assertEqual(before,self.store.active()['id'])

    def test_only_changed_chunks_embed_and_unchanged_files_keep_syntax(self):
        self.auth.write_text(self.original.replace('token.length > 0','token.length > 2'))
        result = self.publish()
        self.assertEqual(result['files_indexed'],1)
        self.assertEqual(result['chunks_embedded'],1)
        self.assertEqual(result['chunks_reused'],1)
        self.assertEqual(result['graph']['parsing']['skipped'],2)
        self.assertEqual(set(result['affected_files']),{'auth.ts','login.ts','entry.ts'})
        self.assertEqual(self.search('who calls validateToken?')['results'][0]['name'],'login')

    def test_symbol_free_unchanged_file_is_not_reparsed(self):
        (self.root/'imports.ts').write_text('import {validateToken} from "./auth";\nvalidateToken("ok");\n')
        self.publish()
        self.auth.write_text(self.original.replace('> 0','> 1'))
        result = self.publish()
        self.assertEqual(result['graph']['parsing']['skipped'],3)

    def test_same_size_edit_with_same_mtime_is_detected(self):
        before = observe_sources(self.root)['digest']; stamp = self.auth.stat().st_mtime_ns
        self.auth.write_text(self.original.replace('> 0','> 1'))
        os.utime(self.auth,ns=(stamp,stamp))
        self.assertNotEqual(before,observe_sources(self.root)['digest'])
        self.assertFalse(self.store.view()['fresh'])
        self.publish(); self.assertTrue(self.store.view()['fresh'])

    def test_hash_observation_excludes_assets_and_nonregular_files(self):
        large = self.root/'dataset.csv'; large.write_bytes(b'x'*1_000_001)
        binary = self.root/'image.png'; binary.write_bytes(b'\0image')
        pipe = self.root/'feed'; os.mkfifo(pipe)
        opening = os.open
        def guarded(path,*args,**kwargs):
            self.assertNotIn(Path(path),(large,pipe))
            return opening(path,*args,**kwargs)
        with patch('knowledge_base.synchronization.os.open',side_effect=guarded):
            observed = observe_sources(self.root)
        self.assertNotIn('dataset.csv',observed['files'])
        self.assertNotIn('image.png',observed['files'])
        self.assertNotIn('feed',observed['files'])
        large.write_text('searchable text\n')
        self.assertIn('dataset.csv',observe_sources(self.root)['files'])

    def test_retention_preserves_pinned_reader_and_reclaims_older_generations(self):
        old = self.store.active(); old_dir = self.store.data_for(old)
        with self.store.pin_view():
            for number in (1,2):
                self.auth.write_text(self.original.replace('> 0',f'> {number}'))
                self.publish()
            self.assertTrue(old_dir.exists())
            self.assertTrue(self.client.collection_exists(old['collection']))
        self.auth.write_text(self.original.replace('> 0','> 3')); self.publish()
        self.assertFalse(old_dir.exists())
        self.assertFalse(self.client.collection_exists(old['collection']))
        self.assertEqual(len(list((self.store.path/'generations').iterdir())),2)

    def test_missing_vector_collection_forces_a_complete_recovery(self):
        before = self.store.active()
        self.client.delete_collection(before['collection'])
        result = self.publish()
        self.assertFalse(result['unchanged'])
        self.assertGreater(result['chunks_embedded'],0)
        self.assertTrue(self.search('Where are tokens validated?',retrieval='semantic')['results'])

    def test_deletion_does_not_require_the_embedding_model(self):
        self.auth.unlink()
        with patch('indexer.get_embedding_model',side_effect=AssertionError('Deletion must not load a model')):
            result = self.publish()
        self.assertEqual(result['chunks_embedded'],0)
        self.assertEqual(self.search('who calls validateToken?')['results'],[])

    def test_invalid_manifest_is_reported_without_using_legacy_indexes(self):
        (self.store.path/'active_snapshot.json').write_text('{broken')
        self.assertFalse(self.store.view()['fresh'])
        result = self.search('Where are tokens validated?',retrieval='semantic')
        self.assertEqual(result['results'],[])
        self.assertIn('invalid',result['warning'])

    def test_first_snapshot_reports_waiting_in_graph_and_live_state(self):
        (self.store.path/'active_snapshot.json').unlink()
        (self.store.path/'sync_state.json').unlink()
        with patch.object(api,'_SYNC_MANAGER') as manager:
            manager.status.return_value = {'watching':True}
            state = api.live_state(self.repo)['synchronization']
            self.assertFalse(state['fresh'])
            self.assertIn('first consistent',state['error'])
            graph = api.graph_file('auth.ts',repoId=self.repo)
            self.assertEqual(graph['nodes'],[])
            self.assertIn('first consistent',graph['warning'])

    def test_retained_cli_search_uses_active_collection_and_rejects_stale_data(self):
        import search
        with patch.object(search,'DEFAULT_DATA_DIR',str(self.data)),patch.object(search,'get_qdrant_client',return_value=self.client),patch.object(search,'embed_texts',return_value=[[1.]+[0.]*383]),redirect_stdout(io.StringIO()):
            results = search.search_codebase('token validation',repo_id=self.repo)
            self.assertTrue(results)
            self.auth.write_text(self.original.replace('> 0','> 2'))
            self.assertEqual(search.search_codebase('token validation',repo_id=self.repo),[])

    def test_latest_repo_lookup_finds_versioned_indexes(self):
        with patch.object(config,'DEFAULT_DATA_DIR',str(self.data)):
            self.assertEqual(config.latest_repo_id(),self.repo)
            self.assertIsNone(config.find_graph_file(self.repo))

    def test_unchanged_importer_is_reresolved_after_export_rename(self):
        old = self.store.active()['id']
        self.auth.write_text(self.original.replace('validateToken','checkToken'))
        result = self.publish()
        self.assertNotEqual(old,result['activeSnapshotId'])
        self.assertEqual(result['files_indexed'],1)
        self.assertEqual(self.search('who calls checkToken?')['results'],[])
        unknown = self.search('what does login call?')
        self.assertTrue(unknown['uncertainty'])
        self.assertEqual(unknown['results'],[])
        self.assertFalse(audit(self.database())['dangling_references'])

    def test_signature_change_refreshes_unchanged_caller_provenance(self):
        self.auth.write_text(self.original.replace('token: string','token: string, retries = 2'))
        result = self.publish()
        self.assertEqual(result['graph']['parsing']['skipped'],2)
        callees = self.search('what does login call?')['results']
        self.assertEqual(callees[0]['name'],'validateToken')
        self.assertTrue(callees[0]['source_verified'])
        self.assertFalse(audit(self.database())['dangling_references'])

    def test_reexport_alias_change_reresolves_unchanged_importer(self):
        barrel = self.root/'barrel.ts'
        barrel.write_text('export {validateToken as check} from "./auth";\n')
        (self.root/'login.ts').write_text('import {check} from "./barrel";\nexport function login() { return check("ok"); }\n')
        self.publish()
        self.assertEqual(self.search('what does login call?')['results'][0]['name'],'validateToken')
        barrel.write_text('export {stableHelper as check} from "./auth";\n')
        result = self.publish()
        self.assertEqual(result['files_indexed'],1)
        self.assertEqual(result['graph']['parsing']['skipped'],3)
        self.assertEqual(self.search('what does login call?')['results'][0]['name'],'stableHelper')
        self.assertEqual(self.search('who calls validateToken?')['results'],[])

    def test_delete_rename_and_move_prune_active_vectors_and_edges(self):
        self.auth.rename(self.root/'renamed.ts'); self.publish()
        self.assertEqual(self.search('who calls validateToken?')['results'],[])
        self.assertEqual(self.search('validateToken')['results'][0]['path'],'renamed.ts')
        (self.root/'nested').mkdir()
        (self.root/'renamed.ts').rename(self.root/'nested/moved.ts'); self.publish()
        self.assertEqual(self.search('validateToken')['results'][0]['path'],'nested/moved.ts')
        (self.root/'nested/moved.ts').unlink(); self.publish()
        self.assertEqual(self.search('Where is validateToken defined?')['results'],[])
        collection = self.store.active()['collection']
        points,_ = self.client.scroll(collection_name=collection,limit=1000)
        self.assertTrue(all(p.payload['file_path'] not in ('auth.ts','renamed.ts','nested/moved.ts') for p in points))
        self.assertFalse(audit(self.database())['dangling_references'])

    def test_failed_embedding_preserves_last_generation_and_recovers(self):
        before = self.store.active(); data = self.database().read_bytes()
        self.auth.write_text(self.original.replace('> 0','> 1'))
        with patch('indexer.embed_texts',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): self.publish()
        self.assertEqual(self.store.active()['id'],before['id'])
        self.assertEqual(self.database().read_bytes(),data)
        self.assertEqual(self.store.view()['state'],'failed')
        result = self.search('who calls validateToken?')
        self.assertEqual(result['results'],[])
        self.assertIn('stale',result['warning'])
        self.publish(); self.assertTrue(self.store.view()['fresh'])

    def test_source_change_during_build_cannot_publish_partial_generation(self):
        old = self.store.active()['id']
        self.auth.write_text(self.original.replace('> 0','> 1'))
        def mutate(texts):
            (self.root/'added.py').write_text('def added():\n    return True\n')
            return [[1.]+[0.]*383 for _ in texts]
        with patch('indexer.embed_texts',side_effect=mutate):
            with self.assertRaises(RuntimeError): self.publish()
        self.assertEqual(old,self.store.active()['id'])
        self.publish()
        self.assertTrue(self.search('added')['results'])

    def test_stale_semantics_blocked_but_current_file_lookup_is_labelled(self):
        self.auth.write_text(self.original.replace('> 0','> 3'))
        with patch.object(api,'embed_texts',side_effect=AssertionError('Stale index must not query embeddings')):
            result = self.search('Where are tokens validated?',retrieval='semantic')
            self.assertEqual(result['results'],[])
            exact = self.search('explain auth.ts')
            self.assertTrue(exact['results'])
            self.assertEqual(exact['results'][0]['sourceOrigin'],'current_disk')
            self.assertFalse(exact['synchronization']['fresh'])

    def test_generation_pointer_change_during_retrieval_does_not_mix_sources(self):
        original = api._semantic_search
        def change(request,**options):
            result = original(request,**options)
            (self.root/'new.py').write_text('def unrelated():\n    return True\n')
            self.publish()
            return result
        old = self.store.active()['id']
        with patch.object(api,'_semantic_search',side_effect=change):
            result = self.search('Where are tokens validated?',retrieval='semantic')
        self.assertEqual(result['knowledgeSnapshotId'],old)
        self.assertEqual(result['results'],[])
        self.assertFalse(result['synchronization']['fresh'])

    def test_unselected_source_change_during_answer_discards_citations(self):
        def reply(*args,**options):
            (self.root/'new.py').write_text('def unrelated():\n    return True\n')
            class Reply:
                def __enter__(self): return self
                def __exit__(self,*args): pass
                def read(self,*args): return json.dumps({'message':{'content':'Token validation checks the length.'}}).encode()
            return Reply()
        with patch.dict(os.environ,{'CODEMIND_LOCAL_MODEL':'test'}),patch('answers.urlopen',side_effect=reply):
            result = api.ask(api.SearchRequest(repoId=self.repo,query='explain validateToken'))
        self.assertEqual(result['citations'],[])
        self.assertIn('stale',result['warning'])

    def test_ignore_changes_remove_previously_indexed_sources(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.publish()
        (self.root/'.gitignore').write_text('auth.ts\n')
        self.publish()
        self.assertEqual(self.search('Where is validateToken defined?')['results'],[])
        (self.root/'.gitignore').write_text('')
        self.publish()
        self.assertTrue(self.search('validateToken')['results'])

    def test_native_watcher_publishes_saved_changes_without_editor(self):
        completed = threading.Event(); errors=[]
        def sync(repo_id):
            try: self.publish(); completed.set()
            except Exception as exc: errors.append(exc)
        manager = SyncManager(self.data,sync,debounce=.05,audit_interval=10)
        self.addCleanup(manager.close)
        manager.add(self.repo,str(self.root))
        self.auth.write_text(self.original.replace('> 0','> 4'))
        self.assertTrue(completed.wait(8),'Native saved-file watcher did not synchronize')
        self.assertFalse(errors)
        self.assertTrue(self.store.view()['fresh'])

    def test_periodic_hash_audit_survives_native_watcher_failure(self):
        completed = threading.Event()
        def sync(repo_id): self.publish(); completed.set()
        with patch('knowledge_base.synchronization.Observer',side_effect=OSError('unsupported filesystem')):
            manager = SyncManager(self.data,sync,debounce=.05,audit_interval=.2)
            self.addCleanup(manager.close); manager.add(self.repo,str(self.root))
        self.auth.write_text(self.original.replace('> 0','> 5'))
        self.assertTrue(completed.wait(5))
        self.assertTrue(manager.status(self.repo)['watchError'])
        self.assertTrue(self.store.view()['fresh'])


if __name__=='__main__': unittest.main()
