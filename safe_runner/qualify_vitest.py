"""Explicit fixed Vitest compatibility/isolation qualification, no user plans."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ai-engine'))

from .broker import Broker
from .contracts import CapturedFile,NodeCheck
from .fixtures import fixtures,COMMON
from .vitest_reports import classify
from .vitest import image_lock


def run():
    broker=Broker(adapter='vitest')
    # This mode qualifies an unqualified new adapter using fixed fixtures only.
    # Product review requires the resulting attestation plus exact authorization.
    identity=broker.identity(); observations=[]
    broker.attestation.unlink(missing_ok=True)
    boundary=json.loads((broker.runtime.state_dir/'qualification-vitest-boundary.json').read_text())
    if boundary['identity']!=identity or boundary['passed'] is not True or not 0<=time.time()-boundary['created']<=86400:
        raise RuntimeError('Run python -m safe_runner.qualify --vitest for this policy before adapter qualification')
    def execute(name,source,expected):
        raw=source.encode(); result=broker._run_check([CapturedFile('case.test.js',raw,sha256(raw).hexdigest())],NodeCheck('case.test.js',True,'vitest'))
        parsed=classify(result,'case.test.js')
        assert parsed['outcome']==expected and result.cleanup_complete,(name,result,parsed)
        if name=='memory':
            # The child deliberately hits the cgroup limit; the fixture's own
            # test must still finish successfully after observing SIGKILL/OOM.
            assert result.exit_code==0,result
        observations.append({'fixture':name,'outcome':parsed['outcome'],'cleanup_complete':True})
        print(json.dumps(observations[-1]),flush=True)
    execute('passed',"import {test,expect} from 'vitest';test('pass',()=>expect(1).toBe(1));",'passed')
    execute('assertion',"import {test,expect} from 'vitest';test('fail',()=>expect(1).toBe(2));",'assertion_failed')
    execute('zero','const x=1;','zero_tests')
    execute('skipped',"import {test} from 'vitest';test.skip('skip',()=>{});",'zero_tests')
    execute('setup',"import 'missing-package';",'setup_failed')
    execute('test_timeout',"import {test} from 'vitest';test('wait',()=>new Promise(()=>{}));",'timed_out')
    sentinel=broker.runtime.state_dir/'vitest-host-sentinel';sentinel.write_bytes(b'synthetic fixture only')
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
    programs=fixtures(str(sentinel),listener.getsockname()[1])
    prefix="import {test} from 'vitest';import {createRequire} from 'node:module';const require=createRequire(import.meta.url);const assert=require('node:assert/strict'),fs=require('node:fs'),cp=require('node:child_process');\n"
    try:
        for name in ('controls','host_and_symlink','network','disk','processes','memory','cpu','output','malicious_output'):
            source=programs[name].replace(COMMON,prefix).replace('/work/case.cjs','/work/case.test.js')
            if name=='controls':
                source=source.replace("const keys=Object.keys(process.env).sort();assert.deepEqual(keys,['CODEMIND_TEST','HOME','LANG','NODE_TEST_CONTEXT','NODE_TEST_WORKER_ID','PATH','TZ']);", "for(const key of Object.keys(process.env))assert.ok(!/TOKEN|PASSWORD|SECRET|PROXY|SSH_AUTH|DOCKER/i.test(key));assert.throws(()=>fs.writeFileSync('/opt/vitest/package.json','corrupt toolchain')); ")
            execute(name,source,'output_limited' if name=='output' else 'memory_limited' if name=='memory' else 'passed')
            assert sentinel.read_bytes()==b'synthetic fixture only'
        cancel=threading.Event();timer=threading.Timer(2,cancel.set);timer.start()
        raw=(prefix+"test('cancel descendants',()=>{cp.spawn('/bin/sh',['-c','trap \"\" TERM; sleep 30 & wait'],{detached:true,stdio:'ignore'}).unref();process.stdout.write('VT_CHILD_STARTED\\n');return new Promise(()=>{setInterval(()=>{},1000)});});").encode()
        result=broker._run_check([CapturedFile('case.test.js',raw,sha256(raw).hexdigest())],NodeCheck('case.test.js',True,'vitest'),cancel)
        timer.join()
        assert result.state=='cancelled' and result.cleanup_complete and 'VT_CHILD_STARTED' in result.output and result.elapsed_seconds<2+broker.policy.cancellation_grace_seconds,result
        observations.append({'fixture':'cancellation_descendants','outcome':'cancelled','cleanup_complete':True})
    finally:listener.close();sentinel.unlink(missing_ok=True)
    # Real product lifecycle: capture/planner -> separate prep -> run -> paired retries.
    from change_api import ChangeJobs,Start,PlanRequest
    from knowledge_base.identity import repository_id
    from knowledge_base.test_change_capture import git
    from knowledge_base.test_vitest_adapter import setup
    with tempfile.TemporaryDirectory(prefix='cm-vitest-fixture-') as tmp:
        root=Path(tmp).resolve();git(root,'init','-q');git(root,'config','user.email','fixture@example.invalid');git(root,'config','user.name','Vitest fixture')
        (root/'initial.js').write_text('export const initial=1;');git(root,'add','.');git(root,'commit','-qm','initial')
        capture,analysis=setup(root);rid=repository_id(root)
        manager=ChangeJobs({rid:{'rootPath':str(root)}})
        # Temporary attestation needed for product gate after all actual runner
        # fixtures passed. Invalidate it if any product lifecycle check fails.
        assert not broker.capability()['isolation_qualified']
        class QualificationBroker(Broker):
            def capability(self,**kwargs):
                return {'isolation_qualified':True,'identity':self.identity()}
        # Only this fixed local fixture manager bypasses an unpublished final
        # attestation; public/default Broker capability remains false throughout.
        manager.execution.broker_factory=lambda **kwargs:QualificationBroker(adapter='vitest')
        passed=False
        try:
            job=manager.jobs[manager.start(Start(repo_id=rid,idempotency_key='vitest-qualification'))['id']]
            job.update(state='completed',snapshot=capture,analysis=analysis)
            plan=manager.test_plan(job['id'],PlanRequest(repo_id=rid));selected=[c['check_id'] for c in plan['proposed_checks'] if c['eligibility']=='reviewable_existing_test']
            manager.test_plan(job['id'],PlanRequest(repo_id=rid,selected_check_ids=selected))
            original={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
            for kind in ('prepare','run','compare'):
                review=manager.execution.review(job['id'],rid,kind)
                assert review['adapter']=='vitest' and review['toolchain']==image_lock()
                value=manager.execution.submit(job['id'],rid,review['authorization'],review['review_digest'],True)
                for _ in range(1200):
                    value=manager.execution.view(manager.execution.runs[value['id']])
                    if value['state'] not in ('queued','running'):break
                    time.sleep(.05)
                assert value['state']=='completed',value
                if kind=='prepare':assert value['results'][0]['outcome']=='passed',value
                elif kind=='run':assert value['results'][0]['outcome']=='assertion_failed',value
                else:
                    # Identical captured check passes at HEAD (1) and assertion
                    # fails after the saved edit (3), consistently on both tries.
                    assert value['comparison_result']['classification']=='introduced',value
                    assert len(value['results'])==4,value
                observations.append({'fixture':'product_'+kind,'state':value['state'],'outcomes':[r['outcome'] for r in value['results']], 'comparison':value['comparison_result']})
                print(json.dumps(observations[-1]),flush=True)
            assert all((root/name).read_bytes()==raw for name,raw in original.items())
            passed=True
        finally:
            manager.close()
            if not passed:broker.attestation.unlink(missing_ok=True)
    report={'schema_version':'vitest-v1','created':time.time(),'identity':identity,'passed':True,'fixtures':observations,
            'boundary_fixtures':boundary['fixtures'],
            'toolchain':image_lock(),'capture_qualification':'Synthetic atomic fixture; native APFS separately qualified'}
    broker.attestation.write_text(json.dumps(report,indent=2));broker.attestation.chmod(0o600)
    print(json.dumps(report,indent=2),flush=True)
    return report


if __name__=='__main__':run()
