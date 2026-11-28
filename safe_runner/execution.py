"""Session-only exact-review execution. No command or host path comes from clients.

Preparation ingests exact public-registry integrity-pinned archives after separate
authorization; extraction runs only in the offline sandbox. No package scripts,
native build hooks, credentials or shared writable dependency cache are supported.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from hashlib import sha256
import json
import re
import secrets
import threading
import time
import uuid

from fastapi import HTTPException

from .broker import Broker
from .contracts import CapturedFile, NodeCheck, RunnerError
from .reports import classify
from .dependencies import VERSION, locked_packages, download
from .comparison import environment, attribution
from .templates import preview

PREPARE_SOURCE = b"require('node:test')('pinned Node runtime',()=>{require('node:assert/strict').equal(process.versions.node.split('.')[0],'24')});"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Executions:
    def __init__(self, changes, broker_factory=Broker):
        self.changes = changes
        self.broker_factory = broker_factory
        self.lock = threading.RLock()
        self.tickets = {}
        self.runs = {}
        self.receipts = {}
        self.closed = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='isolated-tests')

    def temporary_preview(self, job_id, repo_id, module_path, export_name, arguments, expected):
        with self.changes.lock:
            job = self.changes.owned(job_id, repo_id)
            if job['state'] != 'completed':
                raise HTTPException(409, 'Analyze captured source first')
            if job.get('plan') and any(p.framework=='vitest' for p in job['plan'].profiles):
                raise HTTPException(422, 'Literal temporary templates are native Node only; existing Vitest checks can run/compare')
        self.changes._freshness(job)
        if job['stale_for_workspace']:
            raise HTTPException(409, 'Capture is stale')
        try:
            result = preview(job['snapshot'], job['analysis'], module_path, export_name, arguments, expected)
        except (ValueError, TypeError, RecursionError) as error:
            raise HTTPException(422, str(error)) from error
        with self.changes.lock:
            self.changes.owned(job_id, repo_id)
            self.invalidate(job_id)
            job['temporary_check'] = result
        return result

    def close(self):
        with self.lock:
            self.closed = True
            self.tickets.clear()
            self.receipts.clear()
            for run in self.runs.values():
                run['cancel'].set()
            self.runs.clear()
            self.pool.shutdown(wait=False, cancel_futures=True)

    def invalidate(self, job_id):
        with self.lock:
            self.tickets = {k: t for k, t in self.tickets.items() if t['job'] != job_id}
            self.receipts.pop(job_id, None)
            for run in self.runs.values():
                if run['job'] == job_id:
                    run['stale_for_workspace'] = True
                    # Running captures retain historical provenance; queued or
                    # unlaunched files must not start after an invalidation.
                    run['cancel'].set()

    def prune(self):
        with self.lock:
            now = time.monotonic()
            self.tickets = {k: t for k, t in self.tickets.items() if now - t['created'] < 60}
            self.receipts = {k: r for k, r in self.receipts.items() if now - r['created'] < 600}
            for key, run in list(self.runs.items()):
                if now - run['created'] > 900:
                    run['cancel'].set()
                    if run['state'] not in ('queued', 'running'):
                        del self.runs[key]

    def _context(self, job_id, repo_id, kind, during_owned_launch=False):
        with self.changes.lock:
            job = self.changes.owned(job_id, repo_id)
            if job['state'] != 'completed' or 'plan' not in job:
                raise HTTPException(409, 'Review a completed test plan first')
        self.changes._freshness(job)
        with self.changes.lock:
            self.changes.owned(job_id, repo_id)
            if job['stale_for_workspace']:
                raise HTTPException(409, 'Capture changed; analyze and review again')
            plan = job['plan'].model_copy(deep=True)
            snapshot = job['snapshot']
            temporary = job.get('temporary_check')
        selected = sorted(plan.selected_check_ids)
        if not selected or len(selected) > 8:
            raise HTTPException(422, 'Select between one and eight supported checks')
        checks = [c for c in plan.proposed_checks if c.check_id in selected]
        if len(checks) != len(selected) or any(c.eligibility != 'reviewable_existing_test' for c in checks):
            raise HTTPException(422, 'Unknown or unsupported check selection')
        profiles = {p.profile_id: p for p in plan.profiles}
        tests = {t.test_id: t for t in plan.tests}
        frameworks={profiles[c.profile_id].framework for c in checks}
        if len(frameworks)!=1 or not frameworks<= {'node:test','vitest'} or any(profiles[c.profile_id].package_root!='.' or tests[c.test_id].framework!=profiles[c.profile_id].framework for c in checks):
            raise HTTPException(422, 'Select a single root-package supported Node or Vitest profile')
        framework=next(iter(frameworks))
        if framework=='vitest' and temporary:
            raise HTTPException(422, 'Vitest temporary templates are unsupported')
        for check in checks:
            profile = profiles[check.profile_id]
            if profile.dependency_state == 'unknown':
                raise HTTPException(422, 'Dependency metadata is incomplete; capture and review again')
            requirement = profile.runtime_requirement
            if requirement is not None and requirement not in ('24', 'v24', '24.22.3', 'v24.22.3'):
                if not re.fullmatch(r'>=\s*\d+', requirement) or int(re.search(r'\d+', requirement)[0]) > 24:
                    raise HTTPException(422, 'Captured Node requirement is unsupported by the pinned Node 24 adapter')
        files = [CapturedFile(v.path, v.data, v.digest) for v in (*snapshot.after, *snapshot.after_metadata) if v.data is not None]
        if temporary:
            files.append(CapturedFile(temporary['path'], temporary['source'].encode(), temporary['source_hash']))
        if len(files) > 128 or sum(len(f.data) for f in files) > 4194304:
            raise HTTPException(422, 'Capture exceeds isolated input limits: 128 files / 4 MiB')
        manifest = next((f for f in files if f.path == 'package.json'), None)
        if manifest is None:
            raise HTTPException(422, 'A captured root package.json is required')
        try:
            package = json.loads(manifest.data)
            if not isinstance(package, dict):
                raise ValueError('Invalid package manifest')
            lock = next((f for f in files if f.path == 'package-lock.json'), None)
            if lock is None:
                raise ValueError('Captured package-lock.json is required')
            if framework=='vitest':
                from .vitest import project
                toolchain=project(package,json.loads(lock.data),snapshot)
                packages=[]
            else:
                packages = locked_packages(package, json.loads(lock.data))
                toolchain=None
            engine = package.get('engines', {}).get('node')
            # Initial runtime matcher is deliberately narrow and explicit.
            if engine is not None and engine not in ('24','v24','24.22.3','v24.22.3') and (not isinstance(engine, str) or not re.fullmatch(r'>=\s*(\d+)', engine) or int(re.search(r'\d+', engine)[0]) > 24):
                raise ValueError('Supported Node requirement is >=N with N <=24; pinned runtime is Node 24')
        except OSError as error:
            raise HTTPException(503, 'Pinned runner toolchain metadata is unavailable; prepare and qualify the installation') from error
        except (ValueError, TypeError, AttributeError) as error:
            raise HTTPException(422, str(error)) from error
        paths = sorted({tests[c.test_id].source.path for c in checks})
        if temporary:
            paths.append(temporary['path'])
        comparison = environment(snapshot, paths)
        if temporary:
            comparison['differences'].append('Temporary expectation is user-supplied, not baseline regression evidence')
            comparison['comparable'] = False
        if kind == 'compare' and (len(paths) != 1 or temporary):
            raise HTTPException(422, 'Paired retries support one unchanged existing test file; remove temporary checks and select one file')
        if len(paths) > 4:
            raise HTTPException(422, 'At most four captured test files may run per authorization')
        for check in checks:
            if check.argv != [*(['node','--test'] if framework=='node:test' else ['vitest','run']), '--', tests[check.test_id].source.path]:
                raise HTTPException(422, 'Command differs from the supported adapter')
            if framework=='vitest' and (not re.fullmatch(r'[a-zA-Z0-9_./-]+',tests[check.test_id].source.path) or not tests[check.test_id].source.path.endswith(('.js','.mjs'))):
                raise HTTPException(422, 'Vitest supports only literal paths without glob/control characters')
        try:
            broker = self.broker_factory(adapter='vitest') if framework=='vitest' else self.broker_factory()
        except (OSError,ValueError,RunnerError) as error:
            raise HTTPException(503,'Runner installation unavailable: '+str(error)[:200]) from error
        capability = broker.capability(during_owned_launch=during_owned_launch)
        if not capability['isolation_qualified']:
            raise HTTPException(503, capability['reason'])
        identity = capability['identity']
        descriptor = {'schema_version': 'test-run-review-v1', 'kind': kind, 'repository_id': repo_id,
            'adapter':framework,'toolchain':toolchain,
            'capture_id': snapshot.id, 'capture_digest': snapshot.digest, 'plan_id': plan.plan_id,
            'plan_digest': digest(plan.model_dump()), 'selected_check_ids': selected,
            'execution_plan_id': digest({'plan':plan.model_dump(),'temporary':temporary}),
            'profile_ids': sorted({c.profile_id for c in checks}),
            'dependency_identity': digest({'packages': packages, 'toolchain':toolchain,'installer': VERSION, 'manifests': [(f.path, f.digest) for f in files if f.path.endswith('.json')]}),
            'commands': [(['node', '--test', '--test-reporter=<owned structured reporter>', '--', p] if framework=='node:test' else ['node','/opt/vitest/node_modules/vitest/vitest.mjs','run','--config','/out/codemind-vitest.config.mjs','--configLoader','native']) for p in paths],
            'preparation': {'argv': ['node', '--test', '--test-reporter=<owned structured reporter>', '--', 'prepare.cjs'],
                'adapter_source_hash': sha256(PREPARE_SOURCE).hexdigest(), 'packages': packages, 'installer': VERSION,'toolchain':toolchain,
                'vitest_configuration': {'config':'broker-owned','environment':'node','pool':'forks','maxWorkers':1,'fileParallelism':False,'plugins':[],'browser':False,'coverage':False,'envDir':False} if framework=='vitest' else None,
                'lifecycle_scripts': False, 'network': 'Broker GET-only HTTPS public registry.npmjs.org; SHA-512 required; sandbox offline',
                'download_limits': {'packages': 8, 'per_archive_bytes': 262144, 'total_bytes': 1048576, 'seconds': 15}},
            'temporary_test_hashes': [temporary['source_hash']] if temporary else [],
            'temporary_source': temporary, 'comparison': comparison,
            'paired_commands': [{'version': side, 'attempt': attempt, 'path': path} for attempt in (1,2) for side in ('before','after') for path in paths],
            'identity': identity, 'limits': asdict(broker.policy),
            'materialization_digest': digest([(f.path, f.digest) for f in files]),
            'selected': len(selected), 'discovered': len(plan.tests), 'test_files': paths,
            'notice': 'Preparation may download only the listed integrity-pinned public npm tarballs. Archives are unpacked in the offline sandbox; no lifecycle scripts or native builds. Run executes all tests/hooks in selected captured files with fresh dependency copies and no network. Results are historical, not proven regressions.'}
        descriptor['review_digest'] = digest(descriptor)
        return job, plan, files, broker, descriptor

    def review(self, job_id, repo_id, kind):
        if kind not in ('prepare', 'run', 'compare'):
            raise HTTPException(422, 'Unknown execution action')
        _, _, _, _, descriptor = self._context(job_id, repo_id, kind)
        with self.lock:
            if self.closed:
                raise HTTPException(503, 'Execution session is closed')
            now = time.monotonic()
            self.tickets = {k: v for k, v in self.tickets.items() if now - v['created'] < 60}
            if len(self.tickets) >= 8:
                raise HTTPException(429, 'Review capacity reached; wait for expiry')
            if kind in ('run','compare'):
                receipt = self.receipts.get(job_id)
                if receipt is None or now - receipt['created'] > 600 or receipt['binding'] != self._binding(descriptor):
                    raise HTTPException(409, 'Authorize and complete isolated preparation for this exact selection first')
            token = secrets.token_hex(32)
            self.tickets[token] = {'job': job_id, 'repo': repo_id, 'kind': kind, 'created': now, 'descriptor': descriptor}
            return {**descriptor, 'authorization': token, 'expires_in_seconds': 60}

    @staticmethod
    def _binding(descriptor):
        return digest({k: v for k, v in descriptor.items() if k not in ('kind', 'review_digest')})

    def submit(self, job_id, repo_id, token, review_digest, trusted):
        if trusted is not True:
            raise HTTPException(403, 'Workspace Trust is required')
        with self.lock:
            ticket = self.tickets.get(token)
            if not ticket or (ticket['job'], ticket['repo']) != (job_id, repo_id):
                raise HTTPException(409, 'Authorization is missing or belongs to another session')
            if ticket['descriptor']['review_digest'] != review_digest:
                raise HTTPException(409, 'Review digest mismatch')
            if ticket.get('run_id'):
                return self.view(self.runs[ticket['run_id']])
            if self.closed or time.monotonic() - ticket['created'] >= 60:
                raise HTTPException(410, 'Authorization expired; review again')
            if ticket['descriptor']['review_digest'] != review_digest:
                raise HTTPException(409, 'Review digest mismatch')
            if any(r['state'] in ('queued', 'running') for r in self.runs.values()):
                raise HTTPException(429, 'One isolated operation may run at a time; no waiting queue')
            if len(self.runs) >= 8:
                # Never silently replay a historical result as a new run.
                old = next(iter(self.runs))
                del self.runs[old]
                self.tickets = {k: t for k, t in self.tickets.items() if t.get('run_id') != old}
            run = {'id': uuid.uuid4().hex, 'job': job_id, 'repo': repo_id, 'ticket': ticket,
                'cancel': threading.Event(), 'state': 'queued', 'created': time.monotonic(),
                'stale_for_workspace': False, 'results': [], 'error': None}
            self.runs[run['id']] = run
            ticket['run_id'] = run['id']
            self.pool.submit(self._work, run)
            return self.view(run)

    def _work(self, run):
        ticket = run['ticket']
        try:
            def validate(during_owned_launch=False):
                if self.closed or run['cancel'].is_set() or time.monotonic() - ticket['created'] >= 60:
                    raise RunnerError('Authorization cancelled or expired before launch')
                context = self._context(run['job'], run['repo'], ticket['kind'], during_owned_launch)
                if context[-1] != ticket['descriptor']:
                    raise RunnerError('Source, selection, policy or runtime changed; review again')
                if ticket['kind'] in ('run','compare'):
                    with self.lock:
                        receipt = self.receipts.get(run['job'])
                        if not receipt or time.monotonic() - receipt['created'] > 600 or receipt['binding'] != self._binding(ticket['descriptor']):
                            raise RunnerError('Preparation expired or changed')
                return context
            _, plan, files, broker, descriptor = validate()
            framework=descriptor['adapter']
            def observe(result,path):
                if framework=='vitest':
                    from .vitest_reports import classify as vitest_classify
                    return vitest_classify(result,path)
                return classify(result)
            with self.lock:
                run['state'] = 'running'
            if ticket['kind'] == 'prepare':
                archives = download(descriptor['preparation']['packages'], run['cancel'])
                if sum(len(f.data) for f in files) + sum(len(a.data) for a in archives) > broker.policy.input_bytes:
                    raise RunnerError('Combined capture and dependencies exceed the materialization byte limit')
                validate()
                # Only trusted adapter bytes execute in preparation. Captured
                # package scripts and configuration are never evaluated.
                raw = PREPARE_SOURCE
                result = broker._run_check([CapturedFile('prepare.cjs', raw, sha256(raw).hexdigest())],
                    NodeCheck('prepare.cjs', True), run['cancel'], lambda: validate(True),
                    **({'dependencies': archives} if archives else {}))
                observation = classify(result)
                run['results'].append({'path': 'adapter preparation', **observation})
                if observation['outcome'] == 'passed':
                    validate()
                    with self.lock:
                        self.receipts[run['job']] = {'created': time.monotonic(), 'binding': self._binding(descriptor), 'archives': archives}
            elif ticket['kind'] == 'compare':
                # Never replace current checks with changed baseline test bytes.
                # Incompatible environments produce no paired execution/claim.
                if descriptor['comparison']['comparable']:
                    before_files = [CapturedFile(v.path,v.data,v.digest) for v in (*run_snapshot(self.changes,run).before,*run_snapshot(self.changes,run).before_metadata) if v.data is not None]
                    with self.lock:
                        archives = self.receipts[run['job']]['archives']
                    for operation in descriptor['paired_commands']:
                        validate()
                        materialization = before_files if operation['version']=='before' else files
                        result = broker._run_check(materialization,NodeCheck(operation['path'],True,framework),run['cancel'],lambda:validate(True),**({'dependencies':archives} if archives else {}))
                        run['results'].append({**operation, **observe(result,operation['path'])})
                        if not result.cleanup_complete or run['cancel'].is_set():
                            break
                run['comparison_result'] = attribution(run['results'],descriptor['comparison']['comparable'])
            else:
                for path in descriptor['test_files']:
                    validate()
                    with self.lock:
                        archives = self.receipts[run['job']]['archives']
                    result = broker._run_check(files, NodeCheck(path, True,framework), run['cancel'], lambda: validate(True),
                        **({'dependencies': archives} if archives else {}))
                    run['results'].append({'path': path, **observe(result,path)})
                    if not result.cleanup_complete or run['cancel'].is_set():
                        break
            with self.lock:
                run['state'] = 'cancelled' if run['cancel'].is_set() else 'completed'
        except Exception as error:
            with self.lock:
                run['state'] = 'cancelled' if run['cancel'].is_set() else 'failed'
                run['error'] = str(error.detail if isinstance(error, HTTPException) else error)[:300]

    def owned(self, job_id, repo_id, run_id):
        job = self.changes.owned(job_id, repo_id)
        self.changes._freshness(job)
        if job['stale_for_workspace']:
            self.invalidate(job_id)
        with self.lock:
            run = self.runs.get(run_id)
            if not run or (run['job'], run['repo']) != (job_id, repo_id):
                raise HTTPException(404, 'Run not found in this repository session')
            if time.monotonic() - run['created'] > 900:
                run['cancel'].set()
                raise HTTPException(410, 'Run expired')
            return run

    def view(self, run):
        descriptor = run['ticket']['descriptor']
        return {'id': run['id'], 'state': run['state'], 'kind': run['ticket']['kind'],
            'execution_plan_id': descriptor['execution_plan_id'], 'temporary_checks': len(descriptor['temporary_test_hashes']),
            'capture_id': descriptor['capture_id'], 'plan_id': descriptor['plan_id'],
            'review_digest': descriptor['review_digest'], 'historical_capture': True,
            'stale_for_workspace': run['stale_for_workspace'], 'selected': descriptor['selected'],
            'discovered': descriptor['discovered'], 'executed': sum(r['executed'] for r in run['results']),
            'results': list(run['results']), 'error': run['error'],
            'comparison': descriptor['comparison'], 'comparison_result': run.get('comparison_result'),
            'attribution': run.get('comparison_result', {}).get('reason','Not compared with baseline; observed failures are not proven regressions. Temporary-check failures do not establish a defect.')}

    def export(self,job_id,repo_id,run_id):
        """Allowlisted summary only: no console, source, expected literals or errors."""
        from .artifacts import sanitize
        run=self.view(self.owned(job_id,repo_id,run_id))
        return {'schema_version':'test-run-export-v1',
            **{k:run[k] for k in ('id','state','kind','capture_id','plan_id','execution_plan_id','review_digest','historical_capture','stale_for_workspace','selected','discovered','executed')},
            'results':[{**{k:r.get(k) for k in ('version','attempt','outcome','executed','passed','failed','skipped','todo','cancelled','cleanup_complete','termination','exit_code','elapsed_seconds')},
                        'path':sanitize(r.get('path','').encode())} for r in run['results']],
            'comparison_classification':(run.get('comparison_result') or {}).get('classification','inconclusive'),
            'privacy':'Console, failure messages, temporary source, literals, tokens, package URLs and local roots are omitted. Relative source paths and opaque capture/plan identities remain private project metadata.'}


def run_snapshot(changes, run):
    with changes.lock:
        return changes.owned(run['job'],run['repo'])['snapshot']
