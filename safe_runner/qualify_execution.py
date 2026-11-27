"""Explicit P6 qualification on disposable synthetic repositories, never a user plan.

The capture is marked atomic ONLY inside this fixture. Native APFS capture is
qualified separately; this checks the real HTTP -> authorization -> VM path.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ai-engine'))
from change_api import create_change_router, Start
from knowledge_base.identity import repository_id
from knowledge_base.test_change_capture import git
from knowledge_base.test_test_planning import prepare
from safe_runner.broker import Broker
from safe_runner.contracts import CapturedFile, NodeCheck
from safe_runner.reports import classify


def run():
    broker = Broker()
    capability = broker.capability()
    if not capability['isolation_qualified']:
        raise RuntimeError(capability['reason'])
    fixtures = [
        ('passed', "require('node:test')('works',()=>{});", 'passed'),
        ('zero', 'const value=1;', 'zero_tests'),
        ('assertion', "require('node:test')('fails',()=>require('node:assert/strict').equal(1,2));", 'assertion_failed'),
        ('setup', "require('missing-fixture-package');", 'setup_failed'),
        ('skipped', "require('node:test').skip('skip',()=>{});", 'zero_tests'),
        ('timeout', "require('node:test')('wait',()=>new Promise(()=>{setInterval(()=>{},1000)}));", 'timed_out'),
    ]
    observations = []
    for name, source, expected in fixtures:
        raw = source.encode()
        result = broker._run_check([CapturedFile('case.cjs', raw, sha256(raw).hexdigest())], NodeCheck('case.cjs', True))
        parsed = classify(result)
        assert parsed['outcome'] == expected, (name, asdict(result), parsed)
        assert result.cleanup_complete
        observations.append({'fixture': name, 'outcome': parsed['outcome'], 'executed': parsed['executed'], 'cleanup_complete': True})
        print(json.dumps(observations[-1]), flush=True)
    with tempfile.TemporaryDirectory(prefix='cm-p6-fixture-') as temporary:
        root = Path(temporary).resolve()
        git(root, 'init', '-q')
        git(root, 'config', 'user.email', 'fixture@example.invalid')
        git(root, 'config', 'user.name', 'P6 fixture')
        # prepare() needs an existing baseline repository commit.
        (root / 'initial.js').write_text('export const initial=1;')
        git(root, 'add', '.'); git(root, 'commit', '-qm', 'initial')
        capture, analysis = prepare(root)
        original = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
        rid = repository_id(root)
        def auth(x_fixture_session: str = Header(default='')):
            if x_fixture_session != 'synthetic-fixture-only':
                raise HTTPException(401)
        app = FastAPI(dependencies=[Depends(auth)])
        router, manager = create_change_router(SimpleNamespace(_REGISTERED_REPOS={rid: {'rootPath': str(root)}}))
        app.include_router(router)
        try:
            job = manager.jobs[manager.start(Start(repo_id=rid, idempotency_key='p6-http'))['id']]
            job.update(state='completed', snapshot=capture, analysis=analysis)
            with TestClient(app) as client:
                prefix = '/v1/change-analyses/' + job['id']
                assert client.post(prefix + '/execution-review', json={'repo_id': rid, 'kind': 'prepare'}).status_code == 401
                client.headers['x-fixture-session'] = 'synthetic-fixture-only'
                def post(route, body):
                    response = client.post(prefix + route, json={'repo_id': rid, **body})
                    assert response.status_code == 200, response.text
                    return response.json()
                plan = post('/test-plans', {})
                selected = [c['check_id'] for c in plan['proposed_checks'] if c['eligibility'] == 'reviewable_existing_test']
                post('/test-plans', {'selected_check_ids': selected})
                for kind in ('prepare', 'run'):
                    review = post('/execution-review', {'kind': kind})
                    body = {'authorization': review['authorization'], 'review_digest': review['review_digest'], 'workspace_trusted': True}
                    value = post('/runs', body)
                    assert post('/runs', body)['id'] == value['id']
                    for _ in range(200):
                        status = client.get(prefix + '/runs/' + value['id'], params={'repo_id': rid})
                        assert status.status_code == 200, status.text
                        value = status.json()
                        if value['state'] not in ('queued', 'running'):
                            break
                        time.sleep(.1)
                    expected = 'passed' if kind == 'prepare' else 'assertion_failed'
                    assert value['state'] == 'completed', value
                    assert value['results'][0]['outcome'] == expected, value
                    observations.append({'fixture': 'http_' + kind, 'outcome': expected, 'executed': value['executed'], 'historical_capture': value['historical_capture']})
                    print(json.dumps(observations[-1]), flush=True)
                (root / 'a.js').write_text('export const later=3;')
                stale = client.post(prefix + '/execution-review', json={'repo_id': rid, 'kind': 'run'})
                assert stale.status_code == 409
                # The only developer-like file write above is intentional stale testing.
                for path, expected in original.items():
                    if path != 'a.js':
                        assert sha256((root / path).read_bytes()).hexdigest() == expected
                observations.append({'fixture': 'save_blocks_new_authorization', 'status': stale.status_code})
        finally:
            manager.close()
    report = {'schema_version': 'p6-qualification-v1', 'created': time.time(), 'identity': broker.identity(),
              'passed': True, 'fixtures': observations,
              'capture_qualification': 'synthetic atomic marker; native APFS separately qualified',
              'dependency_scope': 'empty third-party dependency set only'}
    print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == '__main__':
    run()
