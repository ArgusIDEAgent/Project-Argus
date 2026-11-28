"""Explicitly qualify fixed sandbox fixtures; never execute a selected repo plan."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
import os
import uuid
import threading
import time

from .broker import Broker
from .contracts import CapturedFile, NodeCheck, RunnerError
from .fixtures import fixtures


def child_fixture(cleanup_failure=False, adapter='node'):
    from .policy import create_args, verify_container
    broker = Broker(adapter=adapter)
    if cleanup_failure:
        original = broker.runtime.call
        def failing(*args, **kwargs):
            if args[:2] == ('container', 'rm'):
                raise RunnerError('Injected cleanup-control failure')
            return original(*args, **kwargs)
        broker.runtime.call = failing
        raw = b"require('node:test')('cleanup fixture',()=>{});"
        result = broker._run_check([CapturedFile('case.cjs',raw,sha256(raw).hexdigest())],NodeCheck('case.cjs'))
        print(json.dumps({'state':result.state,'cleanup_complete':result.cleanup_complete,'cleanup_error':result.cleanup_error}),flush=True)
        return
    name='cm-runner-'+uuid.uuid4().hex; owner=uuid.uuid4().hex
    identity=broker.identity()
    broker._record(name,owner)
    broker.runtime.call(*create_args(broker.policy,broker.image,name,owner))
    verify_container(broker.runtime.inspect('container',name),broker.policy,identity['runtime']['image_id'],name,owner)
    broker.runtime.call('container','start',name)
    # Simulate an abrupt engine exit, leaving only our labeled container/journal.
    os._exit(0)


def run(adapter='node'):
    broker = Broker(adapter=adapter)
    identity = broker.identity()
    sentinel = broker.runtime.state_dir / 'qualification-host-sentinel'
    sentinel.write_text('synthetic host data, never exposed to container mounts')
    before = sentinel.read_bytes()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen()
    programs = fixtures(str(sentinel), listener.getsockname()[1])
    results = []
    try:
        for name, source in programs.items():
            raw = source.encode()
            result = broker._run_check([CapturedFile('case.cjs',raw,sha256(raw).hexdigest())],NodeCheck('case.cjs'))
            if name == 'timeout':
                passed = result.state == 'timed_out' and result.cleanup_complete
            elif name == 'output':
                passed = result.state == 'output_limited' and result.output_truncated and result.cleanup_complete
            else:
                passed = result.exit_code == 0 and result.cleanup_complete
            if name == 'malicious_output':
                passed &= '\x1b' not in result.output and '\x00' not in result.output and 'pretend-secret' not in result.output
            passed &= sentinel.read_bytes() == before
            results.append({'fixture':name,'passed':bool(passed),'result':asdict(result)})
            print(json.dumps({'fixture':name,'passed':bool(passed),'state':result.state,'exit_code':result.exit_code}),flush=True)
            if not passed:
                print(result.output[-3000:],flush=True)
        cancel = threading.Event()
        timer = threading.Timer(.8, cancel.set); timer.start()
        source=programs['timeout'].encode()
        cancelled = broker._run_check([CapturedFile('case.cjs',source,sha256(source).hexdigest())],NodeCheck('case.cjs'),cancel)
        timer.join()
        passed = cancelled.state == 'cancelled' and cancelled.cleanup_complete and cancelled.elapsed_seconds < .8 + broker.policy.cancellation_grace_seconds
        results.append({'fixture':'cancellation_descendants','passed':passed,'result':asdict(cancelled)})
        print(json.dumps({'fixture':'cancellation_descendants','passed':passed,'elapsed_seconds':cancelled.elapsed_seconds}),flush=True)
        for fixture, flag in [('orphan_recovery','--orphan-child'), ('cleanup_failure_recovery','--cleanup-failure-child')]:
            child = subprocess.run([sys.executable,'-m','safe_runner.qualify',flag,*(['--vitest'] if adapter=='vitest' else [])],capture_output=True,text=True,timeout=20,check=True)
            recovered = broker.reconcile()
            repeated = broker.reconcile()
            passed = recovered['reconciled'] == 1 and repeated['reconciled'] == 0
            if flag == '--cleanup-failure-child':
                observed = json.loads(child.stdout)
                passed &= observed['state'] == 'cleanup_failed' and not observed['cleanup_complete'] and bool(observed['cleanup_error'])
            results.append({'fixture':fixture,'passed':bool(passed),'reconciled':recovered['reconciled']})
            print(json.dumps({'fixture':fixture,'passed':bool(passed)}),flush=True)
        report = {'schema_version':1,'created':time.time(),'identity':identity,'passed':all(r['passed'] for r in results),'fixtures':results}
        target = broker.runtime.state_dir/'qualification-vitest-boundary.json' if adapter=='vitest' else broker.attestation
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report,indent=2));temporary.chmod(0o600);temporary.replace(target)
        print(json.dumps({'passed':report['passed'],'fixtures':len(results),'report':str(target)}),flush=True)
        return report['passed']
    finally:
        listener.close();sentinel.unlink(missing_ok=True)


if __name__ == '__main__':
    if '--orphan-child' in sys.argv:
        child_fixture(adapter='vitest' if '--vitest' in sys.argv else 'node')
    elif '--cleanup-failure-child' in sys.argv:
        child_fixture(True,adapter='vitest' if '--vitest' in sys.argv else 'node')
    else:
        raise SystemExit(0 if run('vitest' if '--vitest' in sys.argv else 'node') else 1)
