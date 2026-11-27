"""Authenticated, bounded session jobs for deterministic Analyze Change."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from change_analysis.explanation import explain
from change_analysis.capture import CaptureStore
from change_analysis.service import analyze_change
from change_analysis.transport import decode
from test_analysis.planner import TestPlanner
from safe_runner.execution import Executions

ACTIVE = {'preparing', 'running'}


class RequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Start(RequestModel):
    repo_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=80)


class Owned(RequestModel):
    repo_id: str = Field(min_length=1, max_length=64)


class Presentation(Owned):
    mode: str = 'simple'


class PlanRequest(Owned):
    selected_check_ids: list[str] | None = Field(default=None, max_length=200)


class Source(Owned):
    reference: str = Field(min_length=1, max_length=500)


class RunReview(Owned):
    kind: str


class RunSubmit(Owned):
    authorization: str = Field(min_length=64, max_length=64)
    review_digest: str = Field(min_length=64, max_length=64)
    workspace_trusted: bool = Field(strict=True)


@dataclass
class MemoryStore:
    snapshot: object

    def get(self, capture_id):
        if self.snapshot.id != capture_id:
            raise KeyError(capture_id)
        return self.snapshot


class ChangeJobs:
    def __init__(self, registry):
        self.registry = registry
        self.jobs = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='change-analysis')
        self.closed = False
        self.planner = TestPlanner()
        self.execution = Executions(self)
        self._reconcile()
        self._start_reaper()

    def _reconcile(self):
        # Interrupted sessions never regain authorization. Only this module's
        # owner-private artifact directories are considered; mounts are untouched.
        for directory in Path(tempfile.gettempdir()).glob('codemind-change-*'):
            try:
                if directory.is_symlink() or directory.stat().st_uid != os.getuid():
                    continue
                request = directory / 'request.json'
                if request.is_symlink() or request.stat().st_size > 16000:
                    continue
                payload = json.loads(request.read_text())
                if payload.get('format') != 'codemind-change-request-v1':
                    continue
                pid = payload['engine_pid']
                if type(pid) is not int or pid <= 0:
                    continue
                try:
                    os.kill(pid, 0)
                    continue
                except ProcessLookupError:
                    pass
                (directory / 'cancel').touch(mode=0o600, exist_ok=True)
                if not (directory / 'running').exists() or (directory / 'ready.json').exists():
                    shutil.rmtree(directory)
                else:
                    # If a capture worker is still cleaning its mount, it owns
                    # final artifact deletion. Do not interfere with cleanup.
                    worker_pid_file = directory / 'running'
                    try:
                        worker_pid = int(worker_pid_file.read_text())
                        os.kill(worker_pid, 0)
                    except ProcessLookupError:
                        shutil.rmtree(directory)
                    except (ValueError, OSError):
                        pass
            except (OSError, ValueError, KeyError):
                continue

    def _start_reaper(self):
        self.stop_reaper = threading.Event()
        stop = self.stop_reaper
        def reap():
            while not stop.wait(5):
                self.execution.prune()
                with self.lock:
                    for job in list(self.jobs.values()):
                        if time.monotonic() - job['created'] > 900:
                            self._cancel(job)
                            if 'snapshot' in job:
                                self.planner.clear()
                            job.pop('plan', None)
                            job.pop('snapshot', None)
                            job.pop('analysis', None)
                        if job['state'] not in ACTIVE:
                            self._cleanup(job)
        threading.Thread(target=reap, daemon=True, name='change-retention').start()

    def open(self):
        if self.closed:
            self.execution = Executions(self)
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='change-analysis')
            self.closed = False
            self._start_reaper()

    def _cleanup(self, job):
        directory = job.get('directory')
        if directory and (not directory.exists() or (directory / 'ready.json').exists()
                          or (job['state'] not in ACTIVE and not (directory / 'running').exists())):
            if directory.exists():
                shutil.rmtree(directory)
            job['directory'] = None

    def close(self):
        with self.lock:
            self.execution.close()
            self.closed = True
            self.stop_reaper.set()
            for job in self.jobs.values():
                self._cancel(job)
                self._cleanup(job)
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.jobs.clear()
            self.planner.clear()

    def _cancel(self, job):
        self.execution.invalidate(job['id'])
        if job['state'] in ACTIVE:
            job['state'] = 'cancelled'
            if job['directory']:
                (job['directory'] / 'cancel').touch(mode=0o600, exist_ok=True)

    def start(self, request):
        with self.lock:
            if self.closed:
                raise HTTPException(503, 'Change analysis is shutting down')
            entry = self.registry.get(request.repo_id)
            if entry is None:
                raise HTTPException(404, 'Repository not registered')
            now = time.monotonic()
            for key, job in list(self.jobs.items()):
                if now - job['created'] > 900:
                    self._cancel(job)
                    self._cleanup(job)
                    # Keep cancelled request directories if a Terminal still owns
                    # capture/cleanup. Raw source is removed as soon as polled ready.
                    job.pop('snapshot', None)
                    job.pop('analysis', None)
                    if job['directory'] is None:
                        del self.jobs[key]
                if job['repo_id'] == request.repo_id and job['key'] == request.idempotency_key:
                    return self.view(job)
            if len(self.jobs) >= 4:
                retired = sorted((j for j in self.jobs.values() if j['state'] not in ACTIVE and j['directory'] is None), key=lambda j: j['created'])
                if retired:
                    del self.jobs[retired[0]['id']]
                else:
                    raise HTTPException(429, 'Analysis capacity is full; let capture cleanup finish before retrying')
            if any(j['repo_id'] == request.repo_id and j['state'] in ACTIVE for j in self.jobs.values()):
                raise HTTPException(409, 'An analysis is already active for this repository')
            if sum(j['state'] in ACTIVE for j in self.jobs.values()) >= 2:
                raise HTTPException(429, 'Change analysis queue is full')
            directory = Path(tempfile.mkdtemp(prefix='codemind-change-')).resolve()
            nonce = secrets.token_hex(32)
            root = str(Path(entry['rootPath']).resolve(strict=True))
            payload = {'root': root, 'nonce': nonce, 'format': 'codemind-change-request-v1', 'engine_pid': os.getpid()}
            request_file = directory / 'request.json'
            with request_file.open('x') as handle:
                os.chmod(request_file, 0o600)
                json.dump(payload, handle)
            job = {'id': uuid.uuid4().hex, 'repo_id': request.repo_id, 'root': root,
                   'key': request.idempotency_key, 'nonce': nonce, 'directory': directory,
                   'state': 'preparing', 'created': now, 'stale_for_workspace': False, 'error': None,
                   'terminal': {'executable': sys.executable, 'cwd': str(Path(__file__).resolve().parents[1]),
                                'args': ['-m', 'change_analysis.capture_worker', str(request_file)]}}
            self.jobs[job['id']] = job
            return self.view(job)

    def owned(self, job_id, repo_id):
        job = self.jobs.get(job_id)
        if job is None or job['repo_id'] != repo_id or repo_id not in self.registry:
            raise HTTPException(404, 'Analysis not found in this repository session')
        if str(Path(self.registry[repo_id]['rootPath']).resolve()) != job['root']:
            raise HTTPException(409, 'Repository registration changed')
        if time.monotonic() - job['created'] > 900:
            self._cancel(job)
            self._cleanup(job)
            job.pop('snapshot', None)
            job.pop('analysis', None)
            job.pop('plan', None)
            self.planner.clear()
            raise HTTPException(410, 'Analysis expired; capture again')
        return job

    def poll(self, job_id, repo_id):
        with self.lock:
            job = self.owned(job_id, repo_id)
            directory = job['directory']
            ready = directory / 'ready.json' if directory else None
            if ready and ready.exists():
                if job['state'] != 'preparing':
                    self._cleanup(job)
                else:
                    try:
                        if ready.is_symlink() or ready.stat().st_uid != os.getuid() or ready.stat().st_size > 48000000:
                            raise ValueError('Invalid private capture artifact')
                        payload = json.loads(ready.read_text())
                        if not secrets.compare_digest(payload['nonce'], job['nonce']):
                            raise ValueError('Capture request identity mismatch')
                        if 'error' in payload:
                            raise ValueError('Capture failed: ' + str(payload['error'])[:100])
                        snapshot = decode(payload['capture'], job['root'])
                        job['state'] = 'running'
                        self.pool.submit(self._analyze, job, snapshot)
                    except Exception as error:
                        job['state'] = 'failed'
                        job['error'] = str(error)[:300]
                    finally:
                        self._cleanup(job)
            return self.view(job)

    def _analyze(self, job, snapshot):
        try:
            with self.lock:
                if self.closed or job['state'] != 'running':
                    return
            result = analyze_change(MemoryStore(snapshot), snapshot.id)
            with self.lock:
                if not self.closed and job['state'] == 'running':
                    job.update(snapshot=snapshot, analysis=result, state='completed')
        except Exception as error:
            with self.lock:
                if job['state'] == 'running':
                    job.update(state='failed', error='Analysis failed: ' + str(error)[:200])

    def view(self, job):
        return {key: job[key] for key in ('id', 'repo_id', 'state', 'stale_for_workspace', 'error', 'terminal')}

    def _freshness(self, job):
        if job['stale_for_workspace']:
            return
        try:
            with CaptureStore(job['root']) as store:
                current = store.capture()
            captured = job['snapshot']
            def manifest(value):
                return [(v.path, v.mode, v.digest, v.exclusion, v.object_id) for v in (*value.after, *value.after_metadata)]
            job['stale_for_workspace'] = job['stale_for_workspace'] or (captured.head != current.head or captured.staging != current.staging
                                          or manifest(captured) != manifest(current))
        except Exception:
            job['stale_for_workspace'] = True

    def presentation(self, job_id, request):
        with self.lock:
            job = self.owned(job_id, request.repo_id)
            if job['state'] != 'completed':
                raise HTTPException(409, 'Analysis is not complete')
        self._freshness(job)
        with self.lock:
            job = self.owned(job_id, request.repo_id)
            try:
                result = explain(job['snapshot'], job['analysis'], request.mode)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            return {**result, 'stale_for_workspace': job['stale_for_workspace']}

    def test_plan(self, job_id, request):
        with self.lock:
            job = self.owned(job_id, request.repo_id)
            if job['state'] != 'completed':
                raise HTTPException(409, 'Analysis is not complete')
            snapshot, analysis = job['snapshot'], job['analysis']
        if request.selected_check_ids is not None:
            self._freshness(job)
        try:
            plan = self.planner.build(snapshot, analysis)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)[:300]) from error
        with self.lock:
            job = self.owned(job_id, request.repo_id)
            if request.selected_check_ids is not None:
                if job['stale_for_workspace']:
                    raise HTTPException(409, 'Plan is historical/stale; capture again before selecting checks')
                allowed = {c.check_id for c in plan.proposed_checks if c.eligibility == 'reviewable_existing_test'}
                if len(set(request.selected_check_ids)) != len(request.selected_check_ids) or set(request.selected_check_ids) - allowed:
                    raise HTTPException(422, 'Selection contains unknown or unsupported checks')
                plan.selected_check_ids = list(request.selected_check_ids)
                if job.get('plan') is not None and job['plan'].selected_check_ids != plan.selected_check_ids:
                    self.execution.invalidate(job_id)
            elif job.get('plan') is not None:
                plan.selected_check_ids = list(job['plan'].selected_check_ids)
            job['plan'] = plan
            return {**plan.model_dump(), 'stale_for_workspace': job['stale_for_workspace'],
                    'freshness': 'historical_capture', 'sources': self._plan_sources(plan)}

    def _plan_sources(self, plan):
        references = {}
        def add(source):
            import hashlib
            key = 'tp:' + plan.plan_id + ':' + hashlib.sha256(source.model_dump_json().encode()).hexdigest()
            references[key] = {**source.model_dump(), 'reference': key}
        for profile in plan.profiles:
            for source in profile.manifests + profile.configuration:
                add(source)
        for test in plan.tests:
            add(test.source)
            for assertion in test.assertions:
                add(assertion.source)
        for scenario in plan.scenarios:
            for source in scenario.evidence:
                add(source)
        return references

    def source(self, job_id, request):
        with self.lock:
            job = self.owned(job_id, request.repo_id)
            if job['state'] != 'completed':
                raise HTTPException(409, 'Analysis is not complete')
        self._freshness(job)
        with self.lock:
            job = self.owned(job_id, request.repo_id)
            presentation = explain(job['snapshot'], job['analysis'])
            source = presentation['sources'].get(request.reference)
            if source is None and job.get('plan') is not None:
                source = self._plan_sources(job['plan']).get(request.reference)
            if source is None:
                raise HTTPException(404, 'Captured citation not found')
            version = (*getattr(job['snapshot'], source['version']), *getattr(job['snapshot'], source['version'] + '_metadata'))
            saved = next(v for v in version if v.path == source['path'] and v.data is not None)
            return {**source, 'content': saved.data.decode('utf-8'), 'capture_id': job['snapshot'].id,
                    'stale_for_workspace': job['stale_for_workspace']}


def create_change_router(api):
    manager = ChangeJobs(api._REGISTERED_REPOS)
    router = APIRouter(prefix='/v1/change-analyses')

    @router.post('')
    def start(request: Start):
        if os.environ.get('CODEMIND_CHANGE_ANALYSIS', '1') == '0':
            raise HTTPException(503, 'Change analysis disabled')
        return manager.start(request)

    @router.get('/{job_id}')
    def poll(job_id: str, repo_id: str):
        return manager.poll(job_id, repo_id)

    @router.post('/{job_id}/cancel')
    def cancel(job_id: str, request: Owned):
        with manager.lock:
            job = manager.owned(job_id, request.repo_id)
            manager._cancel(job)
            return manager.view(job)

    @router.post('/{job_id}/stale')
    def stale(job_id: str, request: Owned):
        with manager.lock:
            job = manager.owned(job_id, request.repo_id)
            job['stale_for_workspace'] = True
            manager._cancel(job)
            return manager.view(job)

    @router.post('/{job_id}/explanations')
    def presentation(job_id: str, request: Presentation):
        return manager.presentation(job_id, request)

    @router.post('/{job_id}/test-plans')
    def test_plan(job_id: str, request: PlanRequest):
        return manager.test_plan(job_id, request)

    @router.post('/{job_id}/source')
    def source(job_id: str, request: Source):
        return manager.source(job_id, request)

    @router.post('/{job_id}/execution-review')
    def execution_review(job_id: str, request: RunReview):
        if os.environ.get('CODEMIND_CHANGE_EXECUTION', '1') == '0':
            raise HTTPException(503, 'Change execution disabled')
        return manager.execution.review(job_id, request.repo_id, request.kind)

    @router.post('/{job_id}/runs')
    def launch(job_id: str, request: RunSubmit):
        if os.environ.get('CODEMIND_CHANGE_EXECUTION', '1') == '0':
            raise HTTPException(503, 'Change execution disabled')
        return manager.execution.submit(job_id, request.repo_id, request.authorization,
                                        request.review_digest, request.workspace_trusted)

    @router.get('/{job_id}/runs/{run_id}')
    def run_status(job_id: str, run_id: str, repo_id: str):
        run = manager.execution.owned(job_id, repo_id, run_id)
        return manager.execution.view(run)

    @router.post('/{job_id}/runs/{run_id}/cancel')
    def cancel_run(job_id: str, run_id: str, request: Owned):
        run = manager.execution.owned(job_id, request.repo_id, run_id)
        run['cancel'].set()
        return manager.execution.view(run)

    return router, manager
