"""Manual qualification: real APFS capture through authenticated Phase 3 routes.

Run from the project root: .venv/bin/python -m change_analysis.qualify_phase3
A separate Terminal prompts for Apple mount authorization. Outputs counts only.
"""
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile
import time


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-plan', action='store_true')
    parser.add_argument('--fixture', action='store_true',help='Use a disposable small Git repository for native release qualification')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixture=None
    if args.fixture:
        fixture=Path(tempfile.mkdtemp(prefix='cm-native-release-')).resolve()
        for values in (['init','-q'],['config','user.name','CodeMind qualification'],['config','user.email','fixture@example.invalid']):
            subprocess.run(['git',*values],cwd=fixture,check=True)
        (fixture/'a.js').write_text('export function value(){return 1;}\n')
        (fixture/'value.test.js').write_text("import test from 'node:test';import assert from 'node:assert/strict';import {value} from './a.js';test('value',()=>assert.equal(value(),1));\n")
        (fixture/'package.json').write_text('{"type":"module","engines":{"node":">=22"}}')
        (fixture/'package-lock.json').write_text('{"lockfileVersion":3,"packages":{"":{}}}')
        subprocess.run(['git','add','.'],cwd=fixture,check=True)
        subprocess.run(['git','commit','-qm','qualification baseline'],cwd=fixture,check=True)
        (fixture/'a.js').write_text('export function value(){return 2;}\n')
    sys.path.insert(0, str(root / 'ai-engine'))
    import api
    from fastapi.testclient import TestClient
    from knowledge_base.identity import repository_id
    selected=fixture or root
    rid = repository_id(selected)
    api._REGISTERED_REPOS[rid] = {'rootPath': str(selected)}
    api._SESSION_TOKEN = secrets.token_hex(32)
    headers = {'x-codemind-session': api._SESSION_TOKEN}
    client = TestClient(api.app, base_url='http://127.0.0.1')
    launch = None
    job=None
    try:
        reply = client.post('/v1/change-analyses', json={'repo_id': rid, 'idempotency_key': secrets.token_hex(16)}, headers=headers)
        reply.raise_for_status()
        job = reply.json()
        descriptor = job['terminal']
        handle = tempfile.NamedTemporaryFile(prefix='codemind-p3-', suffix='.command', delete=False)
        launch = Path(handle.name)
        handle.close()
        command = ' '.join(shlex.quote(value) for value in [descriptor['executable'], *descriptor['args']])
        launch.write_text('#!/bin/zsh\ncd ' + shlex.quote(descriptor['cwd']) + '\n' + command + '\n')
        launch.chmod(0o700)
        subprocess.run(['open', '-a', 'Terminal', str(launch)], check=True)
        print('Awaiting real atomic capture in Terminal.', flush=True)
        deadline = time.monotonic() + 300
        while job['state'] in ('preparing', 'running') and time.monotonic() < deadline:
            time.sleep(1)
            status = client.get(f"/v1/change-analyses/{job['id']}?repo_id={rid}", headers=headers)
            status.raise_for_status()
            job = status.json()
        assert job['state'] == 'completed', job
        summaries = []
        for mode in ('simple', 'detailed', 'flow'):
            response = client.post(f"/v1/change-analyses/{job['id']}/explanations", json={'repo_id': rid, 'mode': mode}, headers=headers)
            response.raise_for_status()
            value = response.json()
            summaries.append({'mode': mode, 'sections': len(value['sections']), 'sources': len(value['sources'])})
        plan_summary = None
        if args.test_plan:
            endpoint = f"/v1/change-analyses/{job['id']}/test-plans"
            first = client.post(endpoint, json={'repo_id': rid}, headers=headers)
            first.raise_for_status()
            plan = first.json()
            second = client.post(endpoint, json={'repo_id': rid}, headers=headers)
            second.raise_for_status()
            assert second.json()['plan_id'] == plan['plan_id']
            plan_summary = {'plan_id': plan['plan_id'], 'profiles': len(plan['profiles']),
                            'tests': len(plan['tests']), 'scenarios': len(plan['scenarios']),
                            'checks': len(plan['proposed_checks']), 'execution_available': plan['execution_available']}
            if plan['sources']:
                plan_source = client.post(f"/v1/change-analyses/{job['id']}/source",
                    json={'repo_id': rid, 'reference': next(iter(plan['sources']))}, headers=headers)
                plan_source.raise_for_status()
        reference = next(iter(value['sources']))
        source = client.post(f"/v1/change-analyses/{job['id']}/source", json={'repo_id': rid, 'reference': reference}, headers=headers)
        source.raise_for_status()
        client.post(f"/v1/change-analyses/{job['id']}/stale", json={'repo_id': rid}, headers=headers).raise_for_status()
        after = client.post(f"/v1/change-analyses/{job['id']}/source", json={'repo_id': rid, 'reference': reference}, headers=headers)
        assert after.json()['content'] == source.json()['content'] and after.json()['stale_for_workspace']
        print(json.dumps({'state': job['state'], 'capture_id': value['capture_id'], 'modes': summaries,
                          'source_navigation': 'captured bytes preserved after stale marking', 'model': value['model_status'], 'test_plan': plan_summary}), flush=True)
    finally:
        api._CHANGE_JOBS.close()
        if launch:
            launch.unlink(missing_ok=True)
        if fixture and job and job.get('state')=='completed':
            import shutil
            shutil.rmtree(fixture)
        client.close()


if __name__ == '__main__':
    main()
