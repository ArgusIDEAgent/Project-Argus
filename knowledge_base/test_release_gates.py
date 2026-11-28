"""Held-out P8 regressions: redaction, retention, privacy, independent disable."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from change_api import create_change_router, Start
from knowledge_base.test_authorized_execution import execution,repo,git,authorize,wait
from knowledge_base.test_phase7_testing import observed
from safe_runner.comparison import attribution
from safe_runner.vitest_reports import classify
from knowledge_base.test_vitest_adapter import report


def test_redaction_does_not_erase_failure_identity():
    left=classify(report('failed','AssertionError: password=alpha-private',False,1))
    right=classify(report('failed','AssertionError: password=beta-private',False,1))
    assert left['failures'][0]['message']==right['failures'][0]['message']
    assert left['failures'][0]['failure_digest']!=right['failures'][0]['failure_digest']
    results=[{**value,'version':side,'attempt':attempt} for attempt in (1,2) for side,value in [('before',left),('after',right)]]
    assert attribution(results,True)['classification']=='inconclusive'
    results[-1]={**left,'version':'after','attempt':2}
    assert attribution(results,True)['unstable']
    assert 'alpha-private' not in json.dumps(left)


def test_expiry_prunes_private_state_and_export_omits_raw_content(execution):
    manager,job,rid=execution
    _,value=authorize(manager,job,rid,'prepare');wait(manager,value)
    _,value=authorize(manager,job,rid,'run');wait(manager,value)
    run=manager.execution.runs[value['id']]
    run['results'][0]['console']='password=private user source program'
    run['results'][0]['failures']=[{'message':'private assertion'}]
    exported=manager.execution.export(job['id'],rid,value['id'])
    assert exported['schema_version']=='test-run-export-v1'
    assert 'private user source' not in json.dumps(exported)
    assert 'private assertion' not in json.dumps(exported)
    assert all('console' not in r and 'failures' not in r for r in exported['results'])
    with pytest.raises(Exception):manager.execution.export(job['id'],'foreign',value['id'])
    for ticket in manager.execution.tickets.values():ticket['created']-=61
    for receipt in manager.execution.receipts.values():receipt['created']-=601
    for current in manager.execution.runs.values():current['created']-=901
    manager.execution.prune()
    assert not manager.execution.runs and not manager.execution.receipts and not manager.execution.tickets
    job['temporary_check']={'source':'private literal'}
    job['created']-=901
    replacement=manager.start(Start(repo_id=rid,idempotency_key=job['key']))
    assert replacement['id']!=job['id'] and replacement['id'] in manager.jobs
    assert all(key not in job for key in ('snapshot','analysis','plan','temporary_check'))


def test_independent_disable_preserves_analysis_routes_and_never_calls_broker():
    router,manager=create_change_router(SimpleNamespace(_REGISTERED_REPOS={}))
    app=FastAPI();app.include_router(router)
    try:
        with TestClient(app) as client, patch.object(manager.execution,'review',side_effect=AssertionError('must not probe runtime')):
            with patch.dict(os.environ,{'CODEMIND_CHANGE_EXECUTION':'0'}):
                assert client.post('/v1/change-analyses/job/execution-review',json={'repo_id':'repo','kind':'run'}).status_code==503
                assert client.post('/v1/change-analyses/job/runs',json={'repo_id':'repo','authorization':'a'*64,'review_digest':'b'*64,'workspace_trusted':True}).status_code==503
                assert client.post('/v1/change-analyses',json={'repo_id':'repo','idempotency_key':'one'}).status_code==404
                assert client.post('/v1/change-analyses/job/explanations',json={'repo_id':'repo','mode':'simple'}).status_code==404
                assert client.post('/v1/change-analyses/job/test-plans',json={'repo_id':'repo'}).status_code==404
            with patch.dict(os.environ,{},clear=True):
                assert client.post('/v1/change-analyses/job/execution-review',json={'repo_id':'repo','kind':'run'}).status_code==503
    finally:manager.close()


def test_compare_without_unredacted_fingerprints_fails_closed():
    values=[observed(side,'assertion_failed',attempt) for side in ('before','after') for attempt in (1,2)]
    for item in values:item['failures'][0].pop('failure_digest')
    assert attribution(values,True)['classification']=='inconclusive'


def test_disable_preserves_successful_model_free_explanation_and_plan(execution):
    manager,job,rid=execution
    with patch('change_api.ChangeJobs',return_value=manager):router,_=create_change_router(SimpleNamespace(_REGISTERED_REPOS={}))
    app=FastAPI();app.include_router(router)
    with TestClient(app) as client,patch.dict(os.environ,{'CODEMIND_CHANGE_EXECUTION':'0','CODEMIND_LOCAL_MODEL':''}):
        prefix='/v1/change-analyses/'+job['id']
        explained=client.post(prefix+'/explanations',json={'repo_id':rid,'mode':'detailed'})
        assert explained.status_code==200 and explained.json()['model_status']=='disabled_deterministic'
        assert client.post(prefix+'/test-plans',json={'repo_id':rid}).status_code==200
        assert client.get(prefix,params={'repo_id':rid}).status_code==200
        assert client.post(prefix+'/execution-review',json={'repo_id':rid,'kind':'prepare'}).status_code==503


def test_unsupported_host_platforms_fail_before_docker(tmp_path):
    from safe_runner.runtime import Runtime
    from safe_runner.contracts import RunnerError
    runtime=Runtime(tmp_path/'runtime')
    for operating_system,architecture in [('linux','aarch64'),('win32','AMD64'),('darwin','x86_64')]:
        with patch('safe_runner.runtime.sys.platform',operating_system),patch('safe_runner.runtime.platform.machine',return_value=architecture),patch.object(runtime,'call',side_effect=AssertionError('No host fallback or VM call')):
            with pytest.raises(RunnerError):runtime.fingerprint('unused')
