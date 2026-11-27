"""P3 jobs use a trusted synthetic capture fixture; native APFS is separately qualified."""
from dataclasses import replace
import json
import time
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import api
from change_api import ChangeJobs, Start, Owned, Presentation, Source
from change_analysis import CaptureStore
from change_analysis.transport import encode
from knowledge_base.identity import repository_id
from knowledge_base.test_change_capture import repo, git


@pytest.fixture
def manager(repo):
    rid = repository_id(repo)
    manager = ChangeJobs({rid: {'rootPath': str(repo)}})
    yield manager, rid, repo
    manager.close()


def completed(manager):
    jobs, rid, root = manager
    (root / 'a.ts').write_text('export function changed() { return 2; }\n')
    with CaptureStore(root) as store:
        capture = replace(store.capture(), consistency='atomic_apfs', atomic_snapshot_id='test-only')
    value = jobs.start(Start(repo_id=rid, idempotency_key='one'))
    job = jobs.jobs[value['id']]
    (job['directory'] / 'ready.json').write_text(json.dumps({'nonce': job['nonce'], 'capture': encode(capture)}))
    jobs.poll(job['id'], rid)
    for _ in range(200):
        if job['state'] not in ('preparing', 'running'):
            break
        time.sleep(.02)
    assert job['state'] == 'completed', job['error']
    assert job['directory'] is None
    return job, capture


def test_modes_reuse_analysis_and_navigation_is_captured(manager):
    jobs, rid, root = manager
    job, capture = completed(manager)
    with patch('change_api.analyze_change', side_effect=AssertionError('Presentation must not analyze again')):
        for mode in ('simple', 'detailed', 'flow'):
            value = jobs.presentation(job['id'], Presentation(repo_id=rid, mode=mode))
            assert value['mode'] == mode
    reference = next(iter(value['sources']))
    source = jobs.source(job['id'], Source(repo_id=rid, reference=reference))
    original = source['content']
    (root / source['path']).unlink()
    job['stale_for_workspace'] = True
    assert jobs.source(job['id'], Source(repo_id=rid, reference=reference))['content'] == original
    assert jobs.presentation(job['id'], Presentation(repo_id=rid))['stale_for_workspace']
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        jobs.source(job['id'], Source(repo_id=rid, reference='../../etc/passwd'))
    with pytest.raises(HTTPException):
        jobs.owned(job['id'], 'wrong-repository')


def test_idempotency_capacity_cancel_and_shutdown(manager):
    jobs, rid, _ = manager
    request = Start(repo_id=rid, idempotency_key='same')
    job = jobs.start(request)
    assert jobs.start(request)['id'] == job['id']
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        jobs.start(Start(repo_id=rid, idempotency_key='other'))
    assert error.value.status_code == 409
    directory = jobs.jobs[job['id']]['directory']
    jobs._cancel(jobs.jobs[job['id']])
    jobs.poll(job['id'], rid)
    jobs.close()
    assert not directory.exists()


def test_wrong_handoff_nonce_and_expiry(manager):
    jobs, rid, _ = manager
    value = jobs.start(Start(repo_id=rid, idempotency_key='one'))
    job = jobs.jobs[value['id']]
    (job['directory'] / 'ready.json').write_text(json.dumps({'nonce': 'wrong'}))
    assert jobs.poll(job['id'], rid)['state'] == 'failed'
    job['created'] -= 901
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        jobs.owned(job['id'], rid)
    assert error.value.status_code == 410


def test_cancellation_discards_running_analysis(manager):
    jobs, rid, root = manager
    import threading
    entered = threading.Event()
    finish = threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        finish.wait(3)
        return object()
    with CaptureStore(root) as store:
        capture = replace(store.capture(), consistency='atomic_apfs', atomic_snapshot_id='test')
    value = jobs.start(Start(repo_id=rid, idempotency_key='race'))
    job = jobs.jobs[value['id']]
    (job['directory'] / 'ready.json').write_text(json.dumps({'nonce': job['nonce'], 'capture': encode(capture)}))
    with patch('change_api.analyze_change', side_effect=blocked):
        jobs.poll(job['id'], rid)
        assert entered.wait(1)
        jobs._cancel(job)
        finish.set()
    jobs.pool.shutdown(wait=True)
    assert job['state'] == 'cancelled' and 'analysis' not in job


def test_application_routes_require_auth():
    with patch.object(api, '_SESSION_TOKEN', 'p3-token'):
        client = TestClient(api.app, base_url='http://127.0.0.1')
        assert client.post('/v1/change-analyses', json={'repo_id': 'x', 'idempotency_key': 'a'}).status_code == 401
        assert client.get('/v1/change-analyses/x?repo_id=x').status_code == 401
        reply = client.post('/v1/change-analyses', json={'repo_id': 'x', 'idempotency_key': 'a'}, headers={'x-codemind-session': 'p3-token'})
        assert reply.status_code == 404


def test_backend_detects_saved_changes_without_extension_notification(manager):
    jobs, rid, root = manager
    job, capture = completed(manager)
    assert not jobs.presentation(job['id'], Presentation(repo_id=rid))['stale_for_workspace']
    (root / 'a.ts').write_text('later edit\n')
    assert jobs.presentation(job['id'], Presentation(repo_id=rid))['stale_for_workspace']


def test_transport_rejects_wrong_repository_and_changed_bytes(manager):
    jobs, rid, root = manager
    with CaptureStore(root) as store:
        capture = replace(store.capture(), consistency='atomic_apfs', atomic_snapshot_id='fixture')
    from change_analysis.transport import decode
    value = encode(capture)
    value['after'][0]['data'] = 'dGFtcGVyZWQ='
    with pytest.raises(ValueError):
        decode(value, str(root))
    with pytest.raises(ValueError):
        decode(encode(capture), str(root / 'outside'))


def test_capacity_evicts_old_finished_history_and_preserves_active(manager):
    jobs, rid, _ = manager
    for index in range(4):
        view = jobs.start(Start(repo_id=rid, idempotency_key=str(index)))
        old = jobs.jobs[view['id']]
        jobs._cancel(old)
        jobs._cleanup(old)
    next_job = jobs.start(Start(repo_id=rid, idempotency_key='next'))
    assert len(jobs.jobs) == 4 and next_job['state'] == 'preparing'


def test_expired_capture_releases_raw_source_and_analysis(manager):
    jobs, rid, _ = manager
    job, _ = completed(manager)
    job['created'] -= 901
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        jobs.owned(job['id'], rid)
    assert 'snapshot' not in job and 'analysis' not in job


def test_test_plan_cache_selection_and_metadata_navigation(manager):
    jobs, rid, root = manager
    (root / 'a.test.js').write_text("import test from 'node:test'; import assert from 'node:assert/strict'; import {changed} from './a.ts'; test('check',()=>{assert.equal(changed(),2);});\n")
    (root / 'package.json').write_text('{"type":"module","engines":{"node":">=22"}}')
    job, _ = completed(manager)
    from change_api import PlanRequest
    plan = jobs.test_plan(job['id'], PlanRequest(repo_id=rid))
    assert plan['execution_available'] is False
    parses = jobs.planner.catalog.parse_count
    builds = jobs.planner.build_count
    checks = [c['check_id'] for c in plan['proposed_checks'] if c['eligibility'] == 'reviewable_existing_test']
    assert checks
    selected = jobs.test_plan(job['id'], PlanRequest(repo_id=rid, selected_check_ids=checks))
    assert selected['selected_check_ids'] == checks
    assert jobs.planner.build_count == builds and jobs.planner.catalog.parse_count == parses
    metadata_ref = next(key for key, source in plan['sources'].items() if source['path'] == 'package.json')
    source = jobs.source(job['id'], Source(repo_id=rid, reference=metadata_ref))
    assert source['content'].startswith('{"type"')
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        jobs.test_plan(job['id'], PlanRequest(repo_id=rid, selected_check_ids=['forged']))
    (root / 'package.json').write_text('{"type":"commonjs"}')
    with pytest.raises(HTTPException) as error:
        jobs.test_plan(job['id'], PlanRequest(repo_id=rid, selected_check_ids=checks))
    assert error.value.status_code == 409
    assert jobs.test_plan(job['id'], PlanRequest(repo_id=rid))['stale_for_workspace']


def test_test_plan_route_requires_session():
    with patch.object(api, '_SESSION_TOKEN', 'p4-token'):
        client = TestClient(api.app, base_url='http://127.0.0.1')
        assert client.post('/v1/change-analyses/unknown/test-plans', json={'repo_id': 'x'}).status_code == 401


def test_isolation_capabilities_requires_auth_and_never_starts_runs():
    with patch.object(api, '_SESSION_TOKEN', 'p5-token'), patch('safe_runner.broker.isolation_capabilities', return_value={
        'isolation_qualified': False, 'execution_available': False, 'reason': 'Missing runtime'
    }) as probe:
        client = TestClient(api.app, base_url='http://127.0.0.1')
        assert client.get('/v1/isolation/capabilities').status_code == 401
        probe.assert_not_called()
        reply = client.get('/v1/isolation/capabilities', headers={'x-codemind-session':'p5-token'})
        assert reply.status_code == 200 and reply.json()['execution_available'] is False
        probe.assert_called_once_with()
