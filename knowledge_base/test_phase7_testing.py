from dataclasses import replace
import time
from hashlib import sha256
from pathlib import Path
import pytest
from fastapi import HTTPException

from knowledge_base.test_authorized_execution import execution, repo, git, authorize, FakeBroker
from knowledge_base.test_test_planning import prepare
from safe_runner.comparison import attribution, environment
from safe_runner.templates import preview


def wait(manager,value):
    run=manager.execution.runs[value['id']]
    for _ in range(1500):
        if run['state'] not in ('queued','running'): return manager.execution.view(run)
        time.sleep(.01)
    raise AssertionError('Paired worker did not finish')


def observed(side,outcome='passed',attempt=1,message='constant assertion'):
    return {'version':side,'attempt':attempt,'outcome':outcome,'executed':1,'passed':int(outcome=='passed'),
        'failed':int(outcome=='assertion_failed'),'skipped':0,'todo':0,'cancelled':0,'cleanup_complete':True,
        'failures':[{'name':'contract','code':'ERR_ASSERTION','message':message,'failure_digest':sha256(message.encode()).hexdigest()}] if outcome=='assertion_failed' else []}


@pytest.mark.parametrize('before,after,expected', [('passed','assertion_failed','introduced'),('assertion_failed','assertion_failed','pre_existing'),('passed','passed','no_failure_observed'),('setup_failed','assertion_failed','inconclusive'),('zero_tests','assertion_failed','inconclusive')])
def test_paired_attribution(before,after,expected):
    results=[observed(side,outcome,attempt) for attempt in (1,2) for side,outcome in [('before',before),('after',after)]]
    assert attribution(results,True)['classification']==expected
    assert attribution(results,False)['classification']=='inconclusive'


def test_flaky_and_different_failures_do_not_get_attribution():
    results=[observed(s,'assertion_failed',a) for a in (1,2) for s in ('before','after')]
    results[-1]=observed('after','passed',2)
    assert attribution(results,True)['unstable']
    assert len(results)==4
    results[-1]=observed('after','assertion_failed',2,'different assertion')
    assert attribution(results,True)['classification']=='inconclusive'
    results=[observed(s,'assertion_failed',a) for a in (1,2) for s in ('before','after')]
    results[-1]['omitted_failure_observations']=1
    assert attribution(results,True)['classification']=='inconclusive'
    results[-1].pop('omitted_failure_observations')
    results[-1]['failures'][0]['message']='x'*1000
    assert attribution(results,True)['classification']=='inconclusive'


def test_baseline_metadata_and_check_differences_block_comparability(repo):
    capture,_=prepare(repo)
    assert environment(capture,['value.test.js'])['comparable']
    assert not environment(capture,['new.test.js'])['comparable']
    assert not environment(replace(capture,before_metadata=()),['value.test.js'])['comparable']
    assert not environment(replace(capture,head=None),['value.test.js'])['comparable']


def test_template_is_literal_owned_and_deterministic(repo):
    capture,analysis=prepare(repo)
    value=preview(capture,analysis,'a.js','value',[],2)
    assert value==preview(capture,analysis,'a.js','value',[],2)
    assert value['target']['source_hash']==next(v.digest for v in capture.after if v.path=='a.js')
    assert 'assert.deepStrictEqual' in value['source']
    assert not (repo/value['path']).exists()
    for module,name,args,expected in [('a.js','value);evil()',[],2),('../outside.js','value',[],2),('a.js','missing',[],2),('a.js','value',[1]*9,2),('a.js','value',[],float('nan'))]:
        with pytest.raises(ValueError):preview(capture,analysis,module,name,args,expected)


def test_paired_execution_and_template_revoke_receipts(execution):
    manager,job,rid=execution
    _,value=authorize(manager,job,rid,'prepare'); assert wait(manager,value)['state']=='completed'
    review,value=authorize(manager,job,rid,'compare')
    result=wait(manager,value)
    assert result['comparison_result']['classification']=='no_failure_observed'
    assert len(result['results'])==4
    assert [(r['version'],r['attempt']) for r in result['results']]==[('before',1),('after',1),('before',2),('after',2)]
    temporary=manager.execution.temporary_preview(job['id'],rid,'a.js','value',[],2)
    assert not (Path(job['root']) / temporary['path']).exists()
    with pytest.raises(HTTPException): manager.execution.review(job['id'],rid,'run')
    with pytest.raises(HTTPException): manager.execution.review(job['id'],rid,'compare')
    review,_=authorize(manager,job,rid,'prepare')
    assert review['temporary_test_hashes']==[temporary['source_hash']]
    assert review['temporary_source']['source']==temporary['source']


def test_incompatible_comparison_does_not_launch_pairs(execution):
    manager,job,rid=execution
    job['snapshot']=replace(job['snapshot'],before_metadata=())
    _,value=authorize(manager,job,rid,'prepare');wait(manager,value)
    _,value=authorize(manager,job,rid,'compare');result=wait(manager,value)
    assert result['comparison_result']['classification']=='inconclusive'
    assert result['results']==[] and FakeBroker.launched==['prepare.cjs']
