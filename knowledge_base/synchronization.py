"""Saved-file synchronization with immutable generations and one atomic pointer.

A generation contains a SQLite metadata copy and a separate vector collection.
Readers capture one pointer, never a mixture of independently published stages.
Native events accelerate updates; periodic content-hash reconciliation catches
missed events, restarts, renames, ignore changes and edits with unchanged mtimes.
"""
from contextlib import closing,contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid

from filelock import FileLock,Timeout
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from knowledge_base.identity import repository_id
from knowledge_base.scanner import DEFAULT_MAX_BYTES, EXCLUDED_DIRS, _git_context, _has_gitignore, _inventory

logger = logging.getLogger(__name__)
PIPELINE_VERSION = 'phase8-hardening-1'


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None


def _write_json(path, value):
    path = Path(path)
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
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise OSError('Source changed during observation')
                if len(data)>DEFAULT_MAX_BYTES: status,reason = 'skipped','too_large'
                elif b'\0' in data[:8192]: status,reason = 'skipped','binary'
                else: digest = hashlib.sha256(data).hexdigest()
        if kind!='directory' and status in ('included','unsupported'):
            sources[path] = digest
        inventory.append((path,kind,status,reason,digest))
    digest = hashlib.sha256(json.dumps([revision,inventory],sort_keys=True).encode()).hexdigest()
    return {'digest':digest,'files':sources,'revision':revision}


def _incomplete_parsing(result):
    graph = result.get('graph', {})
    parsing = graph.get('parsing', {}) if isinstance(graph, dict) else None
    return not isinstance(parsing, dict) or any(parsing.get(key, 0) != 0 for key in ('error', 'partial'))


class SnapshotStore:
    def __init__(self,root,repo_id,data_dir):
        self.root = Path(root).resolve(strict=True)
        if repository_id(self.root)!=repo_id: raise ValueError('Repository identity mismatch')
        self.repo_id = repo_id
        self.base = Path(data_dir).expanduser().resolve()
        if self.base==self.root or self.root in self.base.parents:
            raise ValueError('Snapshot storage must be outside the repository')
        self.path = self.base/repo_id

    def active(self):
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
        return self.path/'generations'/manifest['id'] if manifest else self.base

    @contextmanager
    def pin_view(self):
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
            return bool(manifest and manifest.get('pipelineVersion')==PIPELINE_VERSION
                        and manifest['sources']['digest']==observe_sources(self.root)['digest'])
        except (OSError,ValueError,KeyError): return False

    def failed(self,exc):
        self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        try: previous = self.active()
        except (ValueError,OSError): previous = None
        _write_json(self.path/'sync_state.json',{'state':'failed','activeSnapshotId':previous['id'] if previous else None,
            'lastError':type(exc).__name__+': indexing failed; see engine log.','savedFilesOnly':True})

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
                fresh = bool(manifest and manifest.get('pipelineVersion')==PIPELINE_VERSION
                             and not _incomplete_parsing(manifest['result'])
                             and manifest['sources']['digest']==observe_sources(self.root)['digest'])
            except (OSError,ValueError): fresh = False
            if manifest and not (self.data_for(manifest)/self.repo_id/'metadata.db').is_file(): fresh = False
            if state.get('state')=='failed': fresh = False
            if not fresh:
                error = state.get('lastError') or ('Knowledge pipeline changed; refresh is required.'
                    if manifest and manifest.get('pipelineVersion')!=PIPELINE_VERSION
                    else 'Saved files changed; knowledge index is stale or updating.')
        return {'manifest':manifest,'data_dir':self.data_for(manifest),
                'snapshotId':manifest['id'] if manifest else None,'managed':managed,
                'fresh':fresh,'state':state.get('state','unmanaged'),'error':error,'savedFilesOnly':True}

    def publish(self,*,client,collection,index_directory,build_graph,full=False,on_progress=None):
        self.path.mkdir(parents=True,exist_ok=True,mode=0o700)
        with FileLock(str(self.path/'synchronization.lock'),timeout=60):
            previous = self.active()
            before = observe_sources(self.root)
            store_ready = bool(previous and (self.data_for(previous)/self.repo_id/'metadata.db').is_file()
                               and client.collection_exists(previous['collection']))
            if store_ready:
                with closing(sqlite3.connect((self.data_for(previous)/self.repo_id/'metadata.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    expected_count = db.execute('SELECT count(*) FROM code_chunks WHERE repository_id=?',(self.repo_id,)).fetchone()[0]
                store_ready = client.count(previous['collection'],exact=True).count==expected_count
            if (previous and previous.get('pipelineVersion')==PIPELINE_VERSION and not full
                and store_ready and not _incomplete_parsing(previous['result'])
                and previous['sources']['digest']==before['digest']):
                _write_json(self.path/'sync_state.json',{'state':'ready','activeSnapshotId':previous['id'],
                    'lastError':None,'savedFilesOnly':True})
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
                old_data = self.data_for(previous)
                old_database = old_data/self.repo_id/'metadata.db'
                previous_files = previous['sources']['files'] if previous else {}
                changed_files = {p for p in set(previous_files)|set(before['files']) if previous_files.get(p)!=before['files'].get(p)}
                affected_files = set(changed_files)
                if old_database.is_file():
                    with closing(sqlite3.connect(old_database.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(database)) as target:
                        source.backup(target)
                    os.chmod(database,0o600)
                    with closing(sqlite3.connect(database)) as db:
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
                if client.collection_exists(old_collection):
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
                        with closing(sqlite3.connect(database)) as db,db:
                            db.execute('UPDATE code_chunks SET collection_name=? WHERE repository_id=?',(new_collection,self.repo_id))
                graph = build_graph(self.root,data_dir=staged,incremental=True)
                if _incomplete_parsing({'graph': graph}):
                    raise RuntimeError('Source parsing is incomplete; the previous snapshot remains published')
                result = index_directory(str(self.root),repo_id=self.repo_id,data_dir=str(staged),collection=new_collection,
                    build_graph=False,full_reindex=full or bool(previous and not store_ready),on_progress=on_progress)
                if result['files_failed']: raise RuntimeError('Some files failed indexing')
                if audit(database)['dangling_references']: raise RuntimeError('Dangling metadata references')
                # Prune orphan vectors even if a legacy state file was missing.
                with closing(sqlite3.connect(database)) as db:
                    ids = {r[0] for r in db.execute('SELECT id FROM code_chunks WHERE repository_id=?',(self.repo_id,))}
                offset,seen = None,set()
                while True:
                    points,offset = client.scroll(collection_name=new_collection,limit=1000,offset=offset)
                    orphans = [p.id for p in points if str(p.id) not in ids]
                    if orphans: client.delete(collection_name=new_collection,points_selector=orphans)
                    for point in points:
                        if str(point.id) in ids:
                            validate_vector(database,self.repo_id,point,new_collection)
                            seen.add(str(point.id))
                    if offset is None: break
                if seen!=ids: raise RuntimeError('Metadata/vector membership mismatch')
                after = observe_sources(self.root)
                if before!=after: raise RuntimeError('Repository changed while indexing; retrying is required')
                published = {**result,'activeSnapshotId':identifier,'graph':graph,'unchanged':False,'chunk_count':len(ids),
                    'changed_files':sorted(changed_files),'affected_files':sorted(affected_files),
                    'resolutionScope':'all included JS/TS files (conservative dependency invalidation)'}
                manifest = {'id':identifier,'repoId':self.repo_id,'root':str(self.root),'state':'ready',
                    'pipelineVersion':PIPELINE_VERSION,
                    'collection':new_collection,'sources':after,'result':published,'savedFilesOnly':True,
                    'previousId':previous['id'] if previous else None}
                _write_json(repo_dir/'generation.json',manifest)
                _write_json(self.path/'active_snapshot.json',manifest)
                _write_json(self.path/'sync_state.json',{'state':'ready','activeSnapshotId':identifier,
                    'lastError':None,'savedFilesOnly':True})
                self.collect(client,collection,{identifier,previous['id'] if previous else None})
                return published
            except Exception as exc:
                _write_json(self.path/'sync_state.json',{'state':'failed','pendingSnapshotId':identifier,
                    'activeSnapshotId':previous['id'] if previous else None,
                    'lastError':type(exc).__name__+': indexing failed; see engine log.','savedFilesOnly':True})
                try:
                    active = self.active()
                    self.collect(client,collection,{active['id'] if active else None,active.get('previousId') if active else None})
                except Exception: logger.exception('Unpublished snapshot cleanup failed')
                raise


class _Events(FileSystemEventHandler):
    def __init__(self,manager,repo_id):
        self.manager,self.repo_id = manager,repo_id

    def on_any_event(self,event):
        if event.event_type in ('created','deleted','modified','moved'):
            root = Path(self.manager.entries[self.repo_id]['root']).resolve()
            for location in (event.src_path,getattr(event,'dest_path','')):
                if not location: continue
                try: parts = Path(location).relative_to(root).parts
                except ValueError: continue
                if '.git' in parts:
                    if not any(p in ('HEAD','index','refs','packed-refs','info','exclude') for p in parts): continue
                elif any(p in EXCLUDED_DIRS or p.endswith('.egg-info') for p in parts): continue
                self.manager.changed(self.repo_id)


class SyncManager:
    def __init__(self,data_dir,request_sync,*,debounce=.6,audit_interval=5):
        self.data_dir,self.request_sync = data_dir,request_sync
        self.debounce,self.audit_interval = debounce,audit_interval
        self.entries,self.lock,self.stop_event = {},threading.RLock(),threading.Event()
        self.thread = threading.Thread(target=self._loop,name='codemind-sync',daemon=True)
        self.thread.start()

    def add(self,repo_id,root):
        with self.lock:
            if repo_id in self.entries: return
            entry = {'root':str(Path(root).resolve()),'due':time.monotonic()+self.audit_interval,
                     'auditDue':time.monotonic()+self.audit_interval,'observer':None,'watchError':None,'failures':0}
            self.entries[repo_id] = entry
            try:
                observer = Observer()
                observer.schedule(_Events(self,repo_id),entry['root'],recursive=True)
                observer.start()
                entry['observer'] = observer
            except Exception:
                entry['watchError'] = 'Native watcher unavailable; periodic hash reconciliation remains active.'
                logger.exception('Repository watcher unavailable')

    def changed(self,repo_id):
        with self.lock:
            if repo_id in self.entries: self.entries[repo_id]['due'] = time.monotonic()+self.debounce

    def status(self,repo_id):
        with self.lock:
            entry = self.entries.get(repo_id,{})
            observer = entry.get('observer')
            watching = bool(observer and observer.is_alive() and all(e.is_alive() for e in observer.emitters))
            if observer and not watching:
                entry['watchError'] = 'Native watcher stopped; periodic hash reconciliation remains active.'
            return {'watching':watching,'watchError':entry.get('watchError'),
                    'hashAuditSeconds':self.audit_interval,'savedFilesOnly':True}

    def _loop(self):
        while not self.stop_event.wait(.1):
            with self.lock:
                due = [(rid,entry) for rid,entry in self.entries.items() if min(entry['due'],entry['auditDue'])<=time.monotonic()]
                for _,entry in due: entry['due'] = entry['auditDue'] = time.monotonic()+self.audit_interval
            for repo_id,entry in due:
                try:
                    view = SnapshotStore(entry['root'],repo_id,self.data_dir).view()
                    if not view['managed'] or not view['fresh']:
                        self.request_sync(repo_id)
                        entry['failures'] = entry['failures']+1 if view['state']=='failed' else 0
                        entry['due'] = entry['auditDue'] = time.monotonic()+min(30,self.audit_interval*2**min(entry['failures'],3))
                    else:
                        entry['failures'] = 0
                except Exception:
                    entry['failures'] += 1
                    entry['due'] = entry['auditDue'] = time.monotonic()+min(30,self.audit_interval*2**min(entry['failures'],3))
                    logger.exception('Hash reconciliation failed for %s',repo_id)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        for entry in self.entries.values():
            if entry['observer']:
                entry['observer'].stop()
                entry['observer'].join(timeout=5)


def repository_view(repo_id,data_dir):
    """Resolve managed snapshots for retained read-only CLI callers."""
    if not re.fullmatch(r'[a-f0-9]{24}',str(repo_id)): raise ValueError('Invalid repository identity')
    path = Path(data_dir)/repo_id
    manifest = _read_json(path/'active_snapshot.json')
    if manifest is None:
        if (path/'sync_state.json').exists(): raise ValueError('No consistent knowledge snapshot is ready')
        return None
    return SnapshotStore(manifest['root'],repo_id,data_dir).view()
