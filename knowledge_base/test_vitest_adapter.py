import base64
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import pytest

from knowledge_base.test_change_capture import repo, git
from knowledge_base.test_test_planning import prepare
from knowledge_base.test_change_evidence import synthetic_atomic
from change_analysis.evidence import build_change_evidence
from change_analysis.impact import analyze_impact
from change_analysis.service import ChangeAnalysis
from test_analysis.planner import TestPlanner as Planner
from safe_runner.contracts import NodeCheck,RunResult
from safe_runner.vitest import image_lock, project,ROOT
from safe_runner.vitest_reports import classify


def setup(repo):
    source="import {test as check, expect as verify} from 'vitest';import {value} from './a.js';check('value contract',()=>{verify(value()).toBe(1)});"
    manifest=json.loads((ROOT/'vitest-toolchain/package.json').read_text())
    manifest['type']='module'
    capture,_=prepare(repo,source,manifest)
    (repo/'package-lock.json').write_text((ROOT/'vitest-toolchain/package-lock.json').read_text())
    (repo/'a.js').write_text('export function value(){return 1;}')
    git(repo,'add','.');git(repo,'commit','-qm','vitest fixture baseline')
    (repo/'a.js').write_text('export function value(){return 3;}')
    capture=synthetic_atomic(repo)
    e=build_change_evidence(capture)
    return capture,ChangeAnalysis(capture.id,e,analyze_impact(e,capture))


def test_vitest_static_discovery_and_owned_assertions(repo):
    capture,analysis=setup(repo);plan=Planner().build(capture,analysis)
    assert plan.profiles[0].framework=='vitest'
    assert plan.proposed_checks[0].argv==['vitest','run','--','value.test.js']
    assert plan.tests[0].assertions[0].expression=='verify(value()).toBe(1)'
    metadata={v.path:v.data for v in capture.after_metadata if v.data is not None}
    assert project(json.loads(metadata['package.json']),json.loads(metadata['package-lock.json']),capture)['image']==image_lock()['image']


def test_vitest_configuration_version_and_tree_changes_fail_closed(repo):
    capture,_=setup(repo)
    metadata={v.path:v.data for v in capture.after_metadata if v.data is not None}
    package=json.loads(metadata['package.json']);lock=json.loads(metadata['package-lock.json'])
    for update in ('version','graph','config'):
        p=json.loads(json.dumps(package));l=json.loads(json.dumps(lock));s=capture
        if update=='version':p['dependencies']['vitest']='^5.1.2'
        elif update=='graph':l['packages']['node_modules/vitest']['integrity']='sha512-wrong'
        else:s=replace(capture,after=(*capture.after,replace(capture.after[0],path='vite.config.js')))
        with pytest.raises(ValueError):project(p,l,s)
    with pytest.raises(ValueError):NodeCheck('file.test.ts',True,'vitest')
    with pytest.raises(ValueError):NodeCheck('file.test.cjs',True,'vitest')


def report(status='passed',message='',success=True,code=0):
    a={'fullName':'one','status':status,'failureMessages':[message] if message else []}
    d={'numTotalTests':1,'numPassedTests':int(status=='passed'),'numFailedTests':int(status=='failed'),
       'numPendingTests':int(status=='pending'),'numTodoTests':0,'numFailedTestSuites':int(status=='failed'),
       'success':success,'testResults':[{'name':'/work/one.test.js','assertionResults':[a]}]}
    data='CM_VITEST_REPORT:'+base64.b64encode(json.dumps(d).encode()).decode()
    return RunResult('completed',code,data,False,True,None,.1,'fixture')


def test_vitest_reports_cannot_pass_empty_malformed_or_failed_runs():
    assert classify(report(),'one.test.js')['outcome']=='passed'
    assert classify(report('failed','AssertionError: expected 1 to be 2',False,1))['outcome']=='assertion_failed'
    assert classify(report('failed','Error: callback failed',False,1))['outcome']=='test_error'
    assert classify(report('pending','',False,0))['outcome']=='zero_tests'
    assert classify(report(),'other.test.js')['outcome']=='setup_failed'
    assert classify(replace(report(),output=report().output+'\n'+report().output))['outcome']=='setup_failed'
    assert classify(replace(report(),state='timed_out'))['outcome']=='timed_out'
    value=classify(report('failed','AssertionError: password=pretend-secret',False,1))
    assert 'pretend-secret' not in json.dumps(value)
    assert 'CM_VITEST_REPORT:' not in value['console']
    from safe_runner.comparison import attribution
    failure=classify(report('failed','AssertionError: constant failure',False,1))
    paired=[{**failure,'version':side,'attempt':attempt} for side in ('before','after') for attempt in (1,2)]
    assert attribution(paired,True)['classification']=='pre_existing'
