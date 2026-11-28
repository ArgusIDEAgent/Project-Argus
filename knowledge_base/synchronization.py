"""Saved-file synchronization with immutable generations and one atomic pointer.

A generation contains a SQLite metadata copy and a separate vector collection.
Readers capture one pointer, never a mixture of independently published stages.
Native events accelerate updates; periodic content-hash reconciliation catches
missed events, restarts, renames, ignore changes and edits with unchanged mtimes.
"""
from contextlib import closing,contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
from knowledge_base.database import connect
import stat
import sys
import threading
import time
import uuid

from filelock import FileLock,Timeout
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from knowledge_base.identity import repository_id
from knowledge_base.storage import RepositoryStorage, checked_path
from knowledge_base.scanner import DEFAULT_MAX_BYTES, EXCLUDED_DIRS, _git_context, _has_gitignore, _inventory

logger = logging.getLogger(__name__)
PIPELINE_VERSION = 'repo-local-storage-v3'
_validation_scope = ContextVar('codemind_query_validation', default=None)


def pipeline_identity():
    from knowledge_base.chunking import CHUNKING_VERSION
    from knowledge_base.metadata import SCHEMA_VERSION
    config = sys.modules.get('config')
    return {'schema': SCHEMA_VERSION, 'chunking': CHUNKING_VERSION,
            'embeddingModel': getattr(config, 'EMBEDDING_MODEL_NAME', 'BAAI/bge-small-en-v1.5'),
            'dimensions': getattr(config, 'VECTOR_SIZE', 384)}


def compatible(manifest):
    return bool(manifest and manifest.get('pipelineVersion') == PIPELINE_VERSION
                and manifest.get('configuration') == pipeline_identity())


def _read_json(path):
    try:
        return json.loads(checked_path(path).read_text())
    except FileNotFoundError:
        return None


def _write_json(path, value):
    path = checked_path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temporary,'x',opener=lambda p,f:os.open(p,f,0o600)) as handle:
            json.dump(value,handle,sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def observe_sources(root):
    root = Path(root).resolve(strict=True)
    git,revision = _git_context(root)
    if not git and _has_gitignore(root):
        raise ValueError('Git is required to evaluate ignore rules')
    entries = _inventory(root,git,(),DEFAULT_MAX_BYTES)
    sources,inventory = {},[]
    for row in entries:
        path,kind,status,reason = row[:4]
        if path == '.codemind' or path.startswith('.codemind/'):
            continue
        if status=='error': raise OSError('Cannot observe repository file: '+path)
        digest = row[7]
        if status=='unsupported':
            source = root/path
            if source.resolve(strict=True)!=source: raise ValueError('Source path changed during observation')
            observed = source.lstat()
            if not stat.S_ISREG(observed.st_mode):
                status,reason = 'skipped','not_regular_file'
            elif observed.st_size>DEFAULT_MAX_BYTES:
                status,reason = 'skipped','too_large'
            else:
                with os.fdopen(os.open(source,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)),'rb') as handle:
                    before = os.fstat(handle.fileno())
                    if not stat.S_ISREG(before.st_mode): raise OSError('Source type changed during observation')
                    data = handle.read(DEFAULT_MAX_BYTES+1)
                    after = os.fstat(handle.fileno())
                if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise OSError('Source changed during observation')
                if len(data)>DEFAULT_MAX_BYTES: status,reason = 'skipped','too_large'
                elif b'\0' in data[:8192]: status,reason = 'skipped','binary'
                else: digest = hashlib.sha256(data).hexdigest()
        if kind!='directory' and status in ('included','unsupported'):
            sources[path] = digest
        inventory.append((path,kind,status,reason,digest))
    digest = hashlib.sha256(json.dumps([revision,inventory],sort_keys=True).encode()).hexdigest()
    return {'digest':digest,'files':sources,'revision':revision}


def _invalid_graph(result):
    graph = result.get('graph', {})
    parsing = graph.get('parsing', {}) if isinstance(graph, dict) else None
    if not isinstance(parsing, dict) or parsing.get('error', 0) != 0:
        return True
    partial = parsing.get('partial', 0)
    issues = graph.get('syntax_issues', [])
    if not isinstance(partial, int) or not isinstance(issues, list) or not 0 <= partial <= len(issues):
        return True
    if not issues:
        return False
    excluded = graph.get('excluded_graph_files')
    return (not isinstance(excluded, int) or excluded < len(issues)
            or any(not isinstance(issue, dict) or not isinstance(issue.get('path'), str)
                   or not isinstance(issue.get('message'), str) or 'line' not in issue for issue in issues))


def graph_coverage_warning(graph):
    issues = graph.get('syntax_issues', ())
    if not issues:
        return None
    locations = ", ".join(
        f"{issue['path']}{':' + str(issue['line']) if issue['line'] else ''} ({issue['message']})"
        for issue in issues[:3]
    )
    if len(issues) > 3:
        locations += f", and {len(issues) - 3} more with syntax errors"
    return (f"Graph coverage is partial. Fix syntax in {locations}. "
            f"Structural evidence from {graph['excluded_graph_files']} affected file(s) is omitted.")


class SnapshotStore:
    def __init__(self,root,repo_id,data_dir=None):
        self.root = Path(root).resolve(strict=True)
        if repository_id(self.root)!=repo_id: raise ValueError('Repository identity mismatch')
        self.repo_id = repo_id
        self.storage = RepositoryStorage(self.root, data_dir)
        self.base, self.path = self.storage.base, self.storage.path

    def active(self):
        checked_path(self.path/'active_snapshot.json')
        manifest = _read_json(self.path/'active_snapshot.json')
        if manifest is not None:
            if (not isinstance(manifest,dict) or manifest.get('repoId')!=self.repo_id
                or manifest.get('root')!=str(self.root) or manifest.get('state')!='ready'
                or not re.fullmatch(r'[a-f0-9]{32}',str(manifest.get('id','')))):
                raise ValueError('Invalid active knowledge snapshot')
            sources, result = manifest.get('sources'), manifest.get('result')
            if (not isinstance(sources,dict) or not isinstance(sources.get('files'),dict)
                or not re.fullmatch(r'[a-f0-9]{64}',str(sources.get('digest','')))
                or not isinstance(result,dict)
                or any(not isinstance(result.get(key),int) or result[key]<0
                       for key in ('files_indexed','files_skipped'))
                or not isinstance(manifest.get('collection'),str)
                or not manifest['collection'].endswith('_'+self.repo_id+'_'+manifest['id'])):
                raise ValueError('Invalid active knowledge snapshot components')
        return manifest

    def data_for(self,manifest):
        return checked_path(self.path/'generations'/manifest['id']) if manifest else self.base

    @contextmanager
    def pin_view(self):
        with self.storage.scope():
            with self._pin_view() as view:
                yield view

    @contextmanager
    def _pin_view(self):
        # A collector may retire an older generation between pointer read and
        # lock acquisition. Retry selection, never recreate a removed snapshot.
        for _ in range(3):
            view = self.view()
            if not view['manifest']:
                yield view
                return
            try:
                lock = FileLock(str(self.data_for(view['manifest'])/'readers.lock'),timeout=30)
                lock.acquire()
            except FileNotFoundError: continue
            try:
                yield view
            finally:
                lock.release()
            return
        raise RuntimeError('Knowledge snapshot changed repeatedly; retry retrieval')

    def collect(self,client,collection,keep):
        for generation in (self.path/'generations').iterdir():
            if generation.name in keep or generation.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}',generation.name): continue
            lock = FileLock(str(generation/'readers.lock'),timeout=0)
            try:
                with lock:
                    name = collection+'_'+self.repo_id+'_'+generation.name
                    if client.collection_exists(name): client.delete_collection(collection_name=name)
                    shutil.rmtree(generation)
            except (Timeout,FileNotFoundError): continue
            except Exception: logger.exception('Inactive snapshot cleanup failed')

    def matches(self,identifier):
        if not re.fullmatch(r'[a-f0-9]{32}',str(identifier)): return False
        try:
            manifest = _read_json(self.data_for({'id':identifier})/self.repo_id/'generation.json')
            scoped = _validation_scope.get()
            if scoped is not None and scoped[0] is self and scoped[1].get('id') == identifier:
                # Only the hybrid request opts into deferring redundant whole-
                # repository observations. Canonical trace()/source checks still
                # run in every layer; the enclosing scope must validate on exit.
                return bool(compatible(manifest) and manifest == scoped[1])
            return bool(compatible(manifest)
                        and manifest['sources']['digest']==observe_sources(self.root)['digest'])
        except (OSError,ValueError,KeyError): return False

    def freshness_checkpoint(self, identifier):
        """Always re-observe source bytes, even in an optimized request scope."""
        token = _validation_scope.set(None)
        try:
            return self.matches(identifier)
        finally:
            _validation_scope.reset(token)

    @contextmanager
    def validation_scope(self, view):
        """Defer duplicate full scans until release, never across requests.

        The pin has already observed every source byte. Immutable metadata and
        per-evidence source hashes remain checked during intermediate stages.
        A final unconditional observation catches missed events, new/deleted
        files, ignore changes and edits that preserve size/mtime. Exceptions or
        cancellation cannot release an answer through this scope.
        """
        manifest = view.get('manifest')
        if not view.get('fresh') or not manifest:
            raise ValueError('A fresh pinned snapshot is required')
        token = _validation_scope.set((self, manifest))
        try:
            yield
            if ((self.active() or {}).get('id') != manifest['id']
                    or not self.freshness_checkpoint(manifest['id'])):
                raise ValueError('Saved source or active snapshot changed before answer release')
        finally:
            _validation_scope.reset(token)

    def failed(self,exc):
        self.storage.prepare()
        try: previous = self.active()
        except (ValueError,OSError): previous = None
        _write_json(self.path/'sync_state.json',{'state':'failed','activeSnapshotId':previous['id'] if previous else None,
            'lastError':type(exc).__name__+': indexing failed; see engine log.','savedFilesOnly':True})

    def status_view(self):
        """Advisory status from the last publication. Evidence paths use view()."""
        try:
            state = _read_json(self.path/'sync_state.json') or {}
            if not isinstance(state, dict):
                raise ValueError('Invalid synchronization state')
            identifier = state.get('activeSnapshotId')
            if identifier is not None and not re.fullmatch(r'[a-f0-9]{32}', str(identifier)):
                raise ValueError('Invalid active knowledge snapshot')
            ready = state.get('state') in ('ready', 'partial')
            if ready and (not identifier or not checked_path(
                    self.data_for({'id': identifier})/self.repo_id/'metadata.db').is_file()):
                raise ValueError('Snapshot storage is unavailable')
            warning = state.get('graphWarning')
            if state.get('state') == 'partial' and 'graphWarning' not in state:
                warning = graph_coverage_warning((self.active() or {}).get('result', {}).get('graph', {}))
            return {'snapshotId': identifier, 'fresh': ready, 'state': state.get('state', 'unmanaged'),
                    'error': state.get('lastError'), 'savedFilesOnly': True, 'graphWarning': warning}
        except (OSError, ValueError):
            return {'snapshotId': None, 'fresh': False, 'state': 'failed', 'savedFilesOnly': True,
                    'error': 'Knowledge snapshot metadata is invalid or unavailable; refresh is required.',
                    'graphWarning': None}

    def view(self):
        try:
            manifest = self.active()
            state = _read_json(self.path/'sync_state.json') or {}
            if not isinstance(state,dict): raise ValueError('Invalid synchronization state')
        except (ValueError,OSError):
            manifest,state = None,{'state':'failed','lastError':'Knowledge snapshot metadata is invalid or unavailable; refresh is required.'}
        managed = manifest is not None or bool(state)
        fresh,error = True,None
        if managed:
            try:
                fresh = bool(compatible(manifest)
                             and not _invalid_graph(manifest['result'])
                             and manifest['sources']['digest']==observe_sources(self.root)['digest'])
            except (OSError,ValueError): fresh = False
            if manifest:
                try:
                    if not checked_path(self.data_for(manifest)/self.repo_id/'metadata.db').is_file(): fresh = False
                except ValueError:
                    fresh, error = False, 'Snapshot storage path is invalid; refresh is required.'
            if state.get('state')=='failed': fresh = False
            if not fresh:
                error = state.get('lastError') or ('Knowledge pipeline changed; refresh is required.'
                    if manifest and not compatible(manifest)
                    else 'Saved files changed; knowledge index is stale or updating.')
        graph_warning = graph_coverage_warning(manifest['result'].get('graph', {})) if fresh and manifest else None
        return {'manifest':manifest,'data_dir':self.data_for(manifest),
                'snapshotId':manifest['id'] if manifest else None,'managed':managed,
                'fresh':fresh,'state':state.get('state','unmanaged'),'error':error,'savedFilesOnly':True,
                'graphWarning':graph_warning}

    def publish(self,*,client,collection,index_directory,build_graph,full=False,on_progress=None):
        with self.storage.scope():
            return self._publish(client=client, collection=collection, index_directory=index_directory,
                build_graph=build_graph, full=full, on_progress=on_progress)

    def _record_current(self, identifier, graph):
        state = 'partial' if graph.get('syntax_issues') else 'ready'
        _write_json(self.path/'sync_state.json', {'state': state, 'activeSnapshotId': identifier,
            'lastError': None, 'savedFilesOnly': True, 'graphWarning': graph_coverage_warning(graph)})

    def _publish(self,*,client,collection,index_directory,build_graph,full=False,on_progress=None):
        self.storage.prepare()
        with FileLock(str(self.path/'synchronization.lock'),timeout=60):
            retained = None
            try:
                previous = self.active()
            except ValueError:
                relocated = _read_json(self.path/'active_snapshot.json')
                if not isinstance(relocated,dict) or (relocated.get('root') == str(self.root) and relocated.get('repoId') == self.repo_id):
                    raise
                retained = relocated.get('id')
                if not re.fullmatch(r'[a-f0-9]{32}', str(retained)):
                    raise ValueError('Invalid relocated snapshot identity')
                previous = None  # Never reuse provenance copied from a different root.
            before = observe_sources(self.root)
            store_ready = bool(previous and (self.data_for(previous)/self.repo_id/'metadata.db').is_file()
                               and client.collection_exists(previous['collection']))
            if store_ready:
                with closing(connect((self.data_for(previous)/self.repo_id/'metadata.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    expected_count = db.execute('SELECT count(*) FROM code_chunks WHERE repository_id=?',(self.repo_id,)).fetchone()[0]
                store_ready = client.count(previous['collection'],exact=True).count==expected_count
            if (compatible(previous) and not full
                and store_ready and not _invalid_graph(previous['result'])
                and previous['sources']['digest']==before['digest']):
                self._record_current(previous['id'], previous['result'].get('graph', {}))
                return {**previous['result'],'unchanged':True,'activeSnapshotId':previous['id'],
                        'files_indexed':0,'files_skipped':previous['result']['files_indexed']+previous['result']['files_skipped'],
                        'files_pruned':0,'chunks_embedded':0,'chunks_reused':0}
            identifier = uuid.uuid4().hex
            staged = self.path/'generations'/identifier
            repo_dir = staged/self.repo_id
            repo_dir.mkdir(parents=True,mode=0o700)
            database = repo_dir/'metadata.db'
            new_collection = collection+'_'+self.repo_id+'_'+identifier
            _write_json(self.path/'sync_state.json',{'state':'building','pendingSnapshotId':identifier,
                'activeSnapshotId':previous['id'] if previous else None,'lastError':None,'savedFilesOnly':True})
            try:
                from qdrant_client.models import Filter,FieldCondition,MatchValue,PointStruct,VectorParams,Distance
                from knowledge_base.metadata import audit,validate_vector
                from knowledge_base.vector_store import RepositoryVectors
                old_data = self.data_for(previous)
                old_database = old_data/self.repo_id/'metadata.db'
                previous_files = previous['sources']['files'] if previous else {}
                changed_files = {p for p in set(previous_files)|set(before['files']) if previous_files.get(p)!=before['files'].get(p)}
                affected_files = set(changed_files)
                if old_database.is_file():
                    with closing(connect(old_database.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(connect(database)) as target:
                        source.backup(target)
                    os.chmod(database,0o600)
                    with closing(connect(database)) as db:
                        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        dependencies = list(db.execute('''SELECT a.relative_path,b.relative_path FROM graph_relationships e
                            JOIN graph_nodes a ON a.id=e.source_id JOIN graph_nodes b ON b.id=e.target_id
                            WHERE e.repository_id=? AND e.relationship='DEPENDS_ON' AND e.resolution='resolved' ''',(self.repo_id,))) if 'graph_relationships' in tables else []
                    while True:
                        expanded = affected_files|{a for a,b in dependencies if b in affected_files and a}
                        if expanded==affected_files: break
                        affected_files = expanded
                state_path = old_data/self.repo_id/'index_state.json'
                if state_path.is_file(): _write_json(repo_dir/'index_state.json',_read_json(state_path))
                old_collection = previous['collection'] if previous else collection
                if isinstance(client, RepositoryVectors) and old_database.is_file() and client.collection_exists(old_collection):
                    client.reuse_backed_up_collection(old_collection, new_collection)
                elif client.collection_exists(old_collection):
                    size = client.get_collection(old_collection).config.params.vectors.size
                    client.create_collection(collection_name=new_collection,vectors_config=VectorParams(size=size,distance=Distance.COSINE))
                    offset = None
                    while True:
                        points,offset = client.scroll(collection_name=old_collection,scroll_filter=Filter(must=[
                            FieldCondition(key='repo_id',match=MatchValue(value=self.repo_id))]),
                            limit=1000,offset=offset,with_vectors=True)
                        if points:
                            client.upsert(collection_name=new_collection,points=[PointStruct(id=p.id,vector=p.vector,payload=p.payload) for p in points])
                        if offset is None: break
                    if database.is_file():
                        with closing(connect(database)) as db,db:
                            db.execute('UPDATE code_chunks SET collection_name=? WHERE repository_id=?',(new_collection,self.repo_id))
                graph = build_graph(self.root,data_dir=staged,incremental=True)
                if _invalid_graph({'graph': graph}):
                    raise RuntimeError('Graph coverage could not be verified; the previous snapshot remains published')
                result = index_directory(str(self.root),repo_id=self.repo_id,data_dir=str(staged),collection=new_collection,
                    build_graph=False,full_reindex=full or bool(previous and not store_ready),on_progress=on_progress)
                if result['files_failed']: raise RuntimeError('Some files failed indexing')
                if audit(database)['dangling_references']: raise RuntimeError('Dangling metadata references')
                # Prune orphan vectors even if a legacy state file was missing.
                with closing(connect(database)) as db:
                    ids = {r[0] for r in db.execute('SELECT id FROM code_chunks WHERE repository_id=?',(self.repo_id,))}
                with closing(connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    offset,seen = None,set()
                    while True:
                        points,offset = client.scroll(collection_name=new_collection,limit=1000,offset=offset)
                        orphans = [p.id for p in points if str(p.id) not in ids]
                        if orphans: client.delete(collection_name=new_collection,points_selector=orphans)
                        for point in points:
                            if str(point.id) in ids:
                                validate_vector(database,self.repo_id,point,new_collection,connection=db)
                                seen.add(str(point.id))
                        if offset is None: break
                if seen!=ids: raise RuntimeError('Metadata/vector membership mismatch')
                after = observe_sources(self.root)
                if before!=after: raise RuntimeError('Repository changed while indexing; retrying is required')
                published = {**result,'activeSnapshotId':identifier,'graph':graph,'unchanged':False,'chunk_count':len(ids),
                    'changed_files':sorted(changed_files),'affected_files':sorted(affected_files),
                    'resolutionScope':'all included Python and JS/TS files (conservative dependency invalidation)'}
                manifest = {'id':identifier,'repoId':self.repo_id,'root':str(self.root),'state':'ready',
                    'pipelineVersion':PIPELINE_VERSION,
                    'configuration':pipeline_identity(),
                    'collection':new_collection,'sources':after,'result':published,'savedFilesOnly':True,
                    'previousId':previous['id'] if previous else retained}
                if isinstance(client, RepositoryVectors):
                    client.seal(new_collection)
                _write_json(repo_dir/'generation.json',manifest)
                if self.storage.local:
                    with closing(connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise RuntimeError('Snapshot integrity check failed')
                    for item in staged.rglob('*'):
                        checked_path(item)
                        if item.is_file(): os.chmod(item,0o400)
                _write_json(self.path/'active_snapshot.json',manifest)
                self._record_current(identifier, graph)
                self.collect(client,collection,{identifier,previous['id'] if previous else retained})
                return published
            except Exception as exc:
                _write_json(self.path/'sync_state.json',{'state':'failed','pendingSnapshotId':identifier,
                    'activeSnapshotId':previous['id'] if previous else None,
                    'lastError':type(exc).__name__+': indexing failed; see engine log.','savedFilesOnly':True})
                try:
                    if retained is None:
                        active = self.active()
                        self.collect(client,collection,{active['id'] if active else None,active.get('previousId') if active else None})
                except Exception: logger.exception('Unpublished snapshot cleanup failed')
                raise


class _Events(FileSystemEventHandler):
    def __init__(self, manager, repo_id, root, handle):
        self.manager, self.repo_id, self.root, self.handle = manager, repo_id, root, handle

    def on_any_event(self,event):
        if event.event_type in ('created','deleted','modified','moved'):
            for location in (event.src_path,getattr(event,'dest_path','')):
                if not location: continue
                try: parts = Path(location).relative_to(self.root).parts
                except ValueError: continue
                if '.git' in parts:
                    if not any(p in ('HEAD','index','refs','packed-refs','info','exclude') for p in parts): continue
                elif any(p in EXCLUDED_DIRS or p.endswith('.egg-info') for p in parts): continue
                self.manager.changed(self.repo_id, self.handle)
                break


class SyncManager:
    def __init__(self,data_dir,request_sync,*,debounce=.6,audit_interval=30):
        self.data_dir,self.request_sync = data_dir,request_sync
        self.debounce,self.audit_interval = debounce,audit_interval
        self.entries,self.lock,self.stop_event = {},threading.RLock(),threading.Event()
        self.thread = threading.Thread(target=self._loop,name='codemind-sync',daemon=True)
        self.thread.start()

    @staticmethod
    def _watching(entry):
        observer = entry.get('observer')
        return bool(observer and observer.is_alive() and all(e.is_alive() for e in observer.emitters))

    def _audit_delay(self, entry):
        return self.audit_interval if self._watching(entry) else min(5, self.audit_interval)

    def add(self, repo_id, root, *, owner=None):
        with self.lock:
            if repo_id in self.entries:
                return
            entry = {'root':str(Path(root).resolve()), 'due':None, 'auditDue':time.monotonic() + min(5, self.audit_interval),
                     'observer':None, 'watchError':None, 'event':0, 'checked':0, 'owner':owner, 'handle':object()}
            self.entries[repo_id] = entry
            try:
                observer = Observer()
                observer.schedule(_Events(self, repo_id, Path(entry['root']), entry['handle']), entry['root'], recursive=True)
                observer.start()
                entry['observer'] = observer
            except Exception:
                entry['watchError'] = 'Native watcher unavailable; periodic hash reconciliation remains active.'
                logger.exception('Repository watcher unavailable')

    @staticmethod
    def _stop_observer(entry):
        observer = entry.get('observer') if entry else None
        if observer:
            observer.stop()
            observer.join(timeout=5)

    def remove(self, repo_id):
        with self.lock:
            entry = self.entries.pop(repo_id, None)
        self._stop_observer(entry)

    def is_current(self, repo_id, handle):
        with self.lock:
            return self.entries.get(repo_id, {}).get('handle') is handle

    def changed(self, repo_id, handle=None):
        with self.lock:
            entry = self.entries.get(repo_id)
            if entry and (handle is None or entry['handle'] is handle):
                entry['event'] += 1
                entry['due'] = time.monotonic() + self.debounce

    def revision(self, repo_id):
        with self.lock:
            entry = self.entries.get(repo_id)
            return (entry['handle'], entry['event']) if entry else None

    def completed(self, repo_id, revision):
        with self.lock:
            entry = self.entries.get(repo_id)
            if entry is None or revision is None or entry['handle'] is not revision[0]:
                return
            if entry['event'] == revision[1]:
                entry['checked'] = revision[1]
                entry['due'] = None
            elif entry['due'] is None:
                # A newer event may have coalesced into the job just completed.
                entry['due'] = time.monotonic() + self.debounce

    def status(self,repo_id):
        with self.lock:
            entry = self.entries.get(repo_id,{})
            watching = self._watching(entry)
            if entry.get('observer') and not watching:
                entry['watchError'] = 'Native watcher stopped; periodic hash reconciliation remains active.'
                entry['auditDue'] = min(entry['auditDue'], time.monotonic() + self._audit_delay(entry))
            return {'watching':watching, 'watchError':entry.get('watchError'),
                    'hashAuditSeconds':self._audit_delay(entry), 'savedFilesOnly':True,
                    'invalidated':entry.get('event') != entry.get('checked')}

    def _loop(self):
        while not self.stop_event.wait(.1):
            now = time.monotonic()
            work = []
            with self.lock:
                for repo_id, entry in self.entries.items():
                    changed = entry['due'] is not None and entry['due'] <= now
                    audit = entry['auditDue'] <= now
                    if changed or audit:
                        work.append((repo_id, entry['root'], entry['owner'], (entry['handle'], entry['event']), changed))
                    if changed:
                        entry['due'] = None
                    if audit:
                        entry['auditDue'] = now + self._audit_delay(entry)
            for repo_id, root, owner, revision, changed in work:
                try:
                    if not self.is_current(repo_id, revision[0]):
                        continue
                    if not changed:
                        view = SnapshotStore(root, repo_id, self.data_dir).view()
                        if view['managed'] and view['fresh']:
                            if self.revision(repo_id) == revision:
                                self.completed(repo_id, revision)
                            continue
                    if self.is_current(repo_id, revision[0]):
                        if owner is None:
                            self.request_sync(repo_id)
                        else:
                            self.request_sync(repo_id, owner, revision[0])
                except Exception:
                    logger.exception('Repository reconciliation failed for %s', repo_id)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        with self.lock:
            entries = list(self.entries.values())
            self.entries.clear()
        for entry in entries:
            self._stop_observer(entry)


def repository_view(repo_id,data_dir):
    """Resolve managed snapshots for retained read-only CLI callers."""
    if not re.fullmatch(r'[a-f0-9]{24}',str(repo_id)): raise ValueError('Invalid repository identity')
    path = Path(data_dir)/repo_id if data_dir is not None else RepositoryStorage(Path.cwd()).path
    manifest = _read_json(path/'active_snapshot.json')
    if manifest is None:
        if (path/'sync_state.json').exists(): raise ValueError('No consistent knowledge snapshot is ready')
        return None
    return SnapshotStore(manifest['root'],repo_id,data_dir).view()
