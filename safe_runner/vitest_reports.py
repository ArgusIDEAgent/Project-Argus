"""Strict bounded Vitest JSON observations, independent of Node reporter parsing."""
import base64
import json
import re
from hashlib import sha256
from .artifacts import sanitize

PREFIX='CM_VITEST_REPORT:'


def classify(result, path=None):
    from .reports import classify as node_classify
    value=node_classify(result)
    lines=result.output.splitlines()
    value['console']='\n'.join(line for line in lines if not line.startswith(PREFIX))
    if result.state!='completed':return value
    value['outcome']='setup_failed'
    try:
        encoded=[line[len(PREFIX):] for line in lines if line.startswith(PREFIX)]
        if len(encoded)!=1:return value
        raw=base64.b64decode(encoded[0],validate=True)
        if len(raw)>32768:return value
        report=json.loads(raw)
        keys=('numTotalTests','numPassedTests','numFailedTests','numPendingTests','numTodoTests','numFailedTestSuites')
        if any(type(report[k]) is not int or not 0<=report[k]<=100000 for k in keys):return value
        if type(report['success']) is not bool:return value
        files=report['testResults']
        if not isinstance(files,list) or len(files)!=1:return value
        if path and files[0]['name']!='/work/'+path:return value
        assertions=files[0]['assertionResults']
        if not isinstance(assertions,list) or len(assertions)>200:return value
        total=report['numTotalTests'];passed=report['numPassedTests'];failed=report['numFailedTests']
        skipped=report['numPendingTests'];todo=report['numTodoTests']
        if total!=len(assertions) or total!=passed+failed+skipped+todo:return value
        statuses=[a['status'] for a in assertions]
        if any(s not in ('passed','failed','pending','skipped','todo','disabled') for s in statuses):return value
        if statuses.count('passed')!=passed or statuses.count('failed')!=failed:return value
        failures=[]
        for assertion in assertions:
            if assertion['status']!='failed':continue
            messages=assertion['failureMessages']
            if not isinstance(messages,list) or any(not isinstance(m,str) for m in messages):return value
            text=sanitize('\n'.join(messages).encode())
            failures.append({'name':sanitize(str(assertion['fullName']).encode())[:500],
                             'failure_digest':sha256(json.dumps([assertion['fullName'],messages],ensure_ascii=True).encode()).hexdigest(),
                             'message':text[:1000], 'code':'ERR_ASSERTION' if re.match(r'^AssertionError(?: \[ERR_ASSERTION\])?:',text) else 'VITEST_ERROR'})
        value.update(executed=passed+failed,passed=passed,failed=failed,skipped=skipped,todo=todo,cancelled=0,
                     failures=failures[:20],omitted_failure_observations=max(0,len(failures)-20))
        if not total:
            if 'No test suite found' in str(files[0].get('message','')) or not report['numFailedTestSuites']:
                value['outcome']='zero_tests'
        elif passed+failed==0:value['outcome']='zero_tests'
        elif failed:
            if any('Test timed out' in f['message'] or 'Hook timed out' in f['message'] for f in failures):value['outcome']='timed_out'
            elif all(f['code']=='ERR_ASSERTION' for f in failures):value['outcome']='assertion_failed'
            else:value['outcome']='test_error'
        elif report['success'] and not report['numFailedTestSuites'] and result.exit_code==0:
            value['outcome']='passed'
        return value
    except (ValueError,KeyError,TypeError,AttributeError):return value
