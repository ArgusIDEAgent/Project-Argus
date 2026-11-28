"""Explicit disposable Node comparison/template qualification. Never a user plan."""
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ai-engine'))
from change_api import ChangeJobs, Start, PlanRequest
from knowledge_base.identity import repository_id
from knowledge_base.test_change_capture import git
from knowledge_base.test_test_planning import prepare


def run():
    results=[]
    for mode in ('introduced','pre_existing','template','incompatible'):
        with tempfile.TemporaryDirectory(prefix='cm-p7-fixture-') as tmp:
            root=Path(tmp).resolve()
            git(root,'init','-q');git(root,'config','user.email','fixture@example.invalid');git(root,'config','user.name','P7 fixture')
            (root/'initial.js').write_text('export const initial=1;')
            git(root,'add','.');git(root,'commit','-qm','fixture')
            source=None
            if mode=='pre_existing':
                source="import test from 'node:test';import assert from 'node:assert/strict';import {value} from './a.js';test('constant assertion',()=>{value();assert.ok(false,'pre-existing failure')});"
            capture,analysis=prepare(root,source)
            if mode=='incompatible':
                from dataclasses import replace
                capture=replace(capture,before_metadata=()) # explicit synthetic incompatible fixture
            rid=repository_id(root);manager=ChangeJobs({rid:{'rootPath':str(root)}})
            try:
                job=manager.jobs[manager.start(Start(repo_id=rid,idempotency_key='qualification'))['id']]
                job.update(state='completed',snapshot=capture,analysis=analysis)
                plan=manager.test_plan(job['id'],PlanRequest(repo_id=rid))
                selected=[c['check_id'] for c in plan['proposed_checks'] if c['eligibility']=='reviewable_existing_test']
                manager.test_plan(job['id'],PlanRequest(repo_id=rid,selected_check_ids=selected))
                original={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
                if mode=='template':
                    preview=manager.execution.temporary_preview(job['id'],rid,'a.js','value',[],2)
                    assert not (root/preview['path']).exists()
                def execute(kind):
                    review=manager.execution.review(job['id'],rid,kind)
                    result=manager.execution.submit(job['id'],rid,review['authorization'],review['review_digest'],True)
                    for _ in range(1200):
                        result=manager.execution.view(manager.execution.runs[result['id']])
                        if result['state'] not in ('queued','running'): break
                        time.sleep(.05)
                    assert result['state']=='completed',result
                    return result
                prep=execute('prepare');assert prep['results'][0]['outcome']=='passed',prep
                result=execute('run' if mode=='template' else 'compare')
                if mode=='template':
                    assert len(result['results'])==2 and result['results'][-1]['outcome']=='passed',result
                    assert result['comparison_result'] is None and not (root/preview['path']).exists()
                else:
                    expected='inconclusive' if mode=='incompatible' else mode
                    assert result['comparison_result']['classification']==expected,result
                    assert len(result['results'])==(0 if mode=='incompatible' else 4),result
                assert all((root/name).read_bytes()==data for name,data in original.items())
                assert all(r['cleanup_complete'] for r in result['results'])
                results.append({'fixture':mode,'passed':True,'observations':len(result['results']),
                    'classification':result.get('comparison_result'),'source_unchanged':True})
                print(json.dumps(results[-1]),flush=True)
            finally:manager.close()
    from safe_runner.broker import Broker
    report={'schema_version':'p7-node-qualification-v1','passed':True,'identity':Broker().identity(),
        'fixtures':results,'capture_qualification':'Synthetic atomic fixture; native APFS qualified separately',
        'vitest_qualified':Broker(adapter='vitest').capability()['isolation_qualified'],'limitation':'No automated native editor/admin-prompt E2E; controller gates tested separately'}
    print(json.dumps(report,indent=2),flush=True)
    return report


if __name__=='__main__':run()
