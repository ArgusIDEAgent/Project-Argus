"""P8 independent held-out fixtures and bounded local measurements. No user tests."""
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import tempfile
import time

from .broker import Broker
from .contracts import CapturedFile,NodeCheck
from .reports import classify
from .comparison import attribution


def files(module,test):
    values={'a.mjs':module,'case.test.mjs':test}
    return [CapturedFile(p,s.encode(),sha256(s.encode()).hexdigest()) for p,s in values.items()]


def held_out():
    broker=Broker()
    if not broker.capability()['isolation_qualified']:raise RuntimeError('Qualify current runner first')
    prefix="import test from 'node:test';import assert from 'node:assert/strict';import {value} from './a.mjs';\n"
    fixtures=[
      ('broken_api_argument',"export function value(p){return p.token;}","export function value(p){return p.auth.token;}","test('API argument contract',()=>assert.doesNotThrow(()=>value({token:'ok'})));",'introduced'),
      ('missing_authorization',"export function value(role){return role==='admin';}","export function value(role){return true;}","test('permission contract',()=>assert.equal(value('guest'),false));",'introduced'),
      ('null_crash',"export function value(s){return (s??'').trim();}","export function value(s){return s.trim();}","test('null callback',()=>{value(null)});",'inconclusive'),
      ('wrong_error_response',"export function value(){return 401;}","export function value(){return 200;}","test('error contract',()=>assert.equal(value(),401));",'introduced'),
      ('retry_removed',"export function value(fn){try{return fn()}catch{return fn()}}", "export function value(fn){return fn()}","test('retry contract',()=>{let calls=0;assert.doesNotThrow(()=>value(()=>{if(++calls===1)throw Error('retry');return 'ok'}))});",'introduced'),
      ('passing_tests_uncovered_flow',"export function value(mode){return mode==='checked'?1:false;}","export function value(mode){return mode==='checked'?1:true;}","test('only checked path',()=>assert.equal(value('checked'),1));",'no_failure_observed'),
    ]
    results=[]
    for name,before,after,test,expected in fixtures:
        observations=[]
        for attempt in (1,2):
            for version,module in [('before',before),('after',after)]:
                result=broker._run_check(files(module,prefix+test),NodeCheck('case.test.mjs',True))
                assert result.cleanup_complete,result
                observation=classify(result)
                observations.append({**observation,'version':version,'attempt':attempt})
        paired=attribution(observations,True)
        assert paired['classification']==expected,(name,paired,observations)
        if name=='null_crash':assert observations[1]['outcome']=='test_error',observations
        results.append({'fixture':name,'passed':True,'classification':paired['classification'],
                        'outcomes':[v['outcome'] for v in observations], 'coverage_claim':False})
        print(json.dumps(results[-1]),flush=True)
    results.append({'fixture':'UI_action_disconnected','status':'unsupported_not_executed','classification':'could_not_verify',
                    'reason':'Browser/UI runtime adapter absent; event-string adjacency is not a proven workflow'})
    return {'schema_version':'held-out-v1','passed':True,'fixtures':results,'scope':'Fresh independent Node programs, no developer source; UI/browser case explicitly unsupported'}


def measure():
    from knowledge_base.test_change_capture import git
    from knowledge_base.test_test_planning import prepare
    from change_analysis.service import analyze_change
    from change_analysis.explanation import explain
    from test_analysis.planner import TestPlanner
    class Store:
        def __init__(self,s):self.s=s
        def get(self,ident):
            assert ident==self.s.id
            return self.s
    def timing(fn):
        start=time.perf_counter();value=fn();return value,(time.perf_counter()-start)*1000
    def stats(values):
        ordered=sorted(values)
        return {'samples':len(values),'median_ms':statistics.median(ordered),'p95_ms':ordered[math.ceil(.95*len(values))-1],'max_ms':max(values)}
    with tempfile.TemporaryDirectory(prefix='cm-release-bench-') as tmp:
        root=Path(tmp).resolve();git(root,'init','-q');git(root,'config','user.name','Benchmark');git(root,'config','user.email','benchmark@example.invalid')
        (root/'initial.js').write_text('export const initial=1;');git(root,'add','.');git(root,'commit','-qm','initial')
        capture,_=prepare(root)
        # Imports are warm, but the first measured analysis has no result cache.
        analysis,cold=timing(lambda:analyze_change(Store(capture),capture.id))
        warm=[];explanations=[];planning=[];planner=TestPlanner()
        _,cold_plan=timing(lambda:planner.build(capture,analysis))
        for _ in range(20):
            _,elapsed=timing(lambda:analyze_change(Store(capture),capture.id));warm.append(elapsed)
            _,elapsed=timing(lambda:explain(capture,analysis,'detailed'));explanations.append(elapsed)
            _,elapsed=timing(lambda:planner.build(capture,analysis));planning.append(elapsed)
        usage=resource.getrusage(resource.RUSAGE_SELF);children=resource.getrusage(resource.RUSAGE_CHILDREN)
        scale=1 if sys.platform=='darwin' else 1024
        report={'schema_version':'release-benchmark-v1','hardware':{'os':platform.platform(),'machine':platform.machine(),'logical_cpus':os.cpu_count()},
            'workload':{'source_files':len(capture.after),'metadata_files':len(capture.after_metadata),'captured_bytes':capture.size_bytes,'python_imports':'warm','node_compiler':'new child per analysis','model':'disabled; no embedding/LLM calls'},
            'structural_cold_ms':cold,'structural_warm':stats(warm),'explanation':stats(explanations),'plan_cold_ms':cold_plan,'plan_cached':stats(planning),
            'process_peak_rss_bytes':usage.ru_maxrss*scale,'child_compiler_peak_rss_bytes':children.ru_maxrss*scale,
            'memory_scope':'Process high-water marks include imports; not incremental operation peaks or guest VM memory',
            'disk_scope':'Disposable capture has ordinary copies; no reusable source/dependency writes; image preparation excluded',
            'targets':{'structural_warm_p95_under_5s':stats(warm)['p95_ms']<5000,'explanation_p95_under_500ms':stats(explanations)['p95_ms']<500,'cached_plan_p95_under_3s':stats(planning)['p95_ms']<3000}}
    # Fresh containers/materializations; image/VM cache already warm. Report this
    # separately from structural analysis and network-enabled cold preparation.
    runtime=[]
    for adapter in ('node','vitest'):
        broker=Broker(adapter=adapter)
        if not broker.capability()['isolation_qualified']:raise RuntimeError('Qualify both runner adapters before benchmark')
        source="import {test,expect} from 'vitest';test('bench',()=>expect(1).toBe(1));" if adapter=='vitest' else "require('node:test')('bench',()=>{});"
        path='bench.test.js' if adapter=='vitest' else 'bench.cjs'
        checks=[]
        for _ in range(3):
            raw=source.encode();result=broker._run_check([CapturedFile(path,raw,sha256(raw).hexdigest())],NodeCheck(path,True,'vitest' if adapter=='vitest' else 'node:test'))
            assert result.cleanup_complete and result.exit_code==0,result
            checks.append(result.elapsed_seconds*1000)
        runtime.append({'adapter':adapter,'first_materialization_ms':checks[0],'subsequent':stats(checks[1:]),'limits':asdict(broker.policy)})
    report['runtime']=runtime
    from .dependencies import download
    from knowledge_base.test_dependency_preparation import PIN
    import threading
    _,elapsed=timing(lambda:download([PIN],threading.Event()))
    report['cold_registry_download_ms']=elapsed
    report['cold_scope']='Empty in-memory archive cache; fixed is-number 7.0.0; DNS/HTTPS included. VM boot, image pulls/build and interactive APFS password time not benchmarked.'
    return report


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--bench',action='store_true');args=parser.parse_args()
    print(json.dumps(measure() if args.bench else held_out(),indent=2),flush=True)


if __name__=='__main__':main()
