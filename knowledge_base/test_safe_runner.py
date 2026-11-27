"""Policy/input/lifecycle tests; actual VM adversarial suite is explicit qualification."""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
from unittest.mock import Mock, patch

import pytest

from safe_runner.artifacts import artifact_name, sanitize
from safe_runner.broker import Broker, isolation_capabilities
from safe_runner.contracts import CapturedFile, NodeCheck, Policy, RunnerError
from safe_runner.policy import create_args, verify_container
from safe_runner.runtime import Runtime

IMAGE = 'docker.io/library/node@sha256:' + 'a' * 64
NAME = 'cm-runner-' + 'b' * 32
OWNER = 'c' * 32


@pytest.mark.parametrize('path', ['.', '../secret.js','/etc/evil.js','a/../b.js','a//b.js','a\\b.js','.git/a.js','node_modules/a.js','a\x00.js'])
def test_materializer_path_validation(path):
    with pytest.raises(ValueError):
        CapturedFile(path,b'',sha256(b'').hexdigest())


def test_broker_rejects_arbitrary_commands_digests_and_unpinned_images():
    with pytest.raises(ValueError):
        NodeCheck('package.json')
    with pytest.raises(ValueError):
        CapturedFile('safe.js',b'changed','0'*64)
    with pytest.raises(RunnerError):
        create_args(Policy(),'node:latest',NAME,OWNER)
    args = create_args(Policy(),IMAGE,NAME,OWNER)
    assert '--network' in args and args[args.index('--network')+1] == 'none'
    assert not any(arg in args for arg in ('--privileged','--volume','--mount','--publish','--env-file'))
    assert args[args.index('--pull=never')] == '--pull=never'


def test_unknown_artifacts_and_malicious_console_stay_text():
    for path in ('../x','/out/x','link/x','artifact.tar'):
        with pytest.raises(ValueError): artifact_name(path)
    assert artifact_name('console.txt') == 'console.txt'
    text = sanitize(b'\x1b[2J<script>text</script>\x00\npassword=synthetic\n')
    assert '\x1b' not in text and '\x00' not in text and 'synthetic' not in text
    assert '<script>text</script>' in text  # plain text, never evaluated as HTML


def test_ambient_docker_credentials_and_proxy_are_not_inherited(tmp_path):
    runtime = Runtime(tmp_path/'state')
    with patch.dict(os.environ, {'DOCKER_HOST':'tcp://evil:2375','HTTP_PROXY':'http://secret','SSH_AUTH_SOCK':'/secret'}):
        assert not any(k in runtime.env for k in ('DOCKER_HOST','HTTP_PROXY','SSH_AUTH_SOCK'))
        assert json.loads((runtime.config/'config.json').read_text()) == {}
    runtime.executable = None
    with pytest.raises(RunnerError, match='unavailable'):
        runtime.command('run','node')


def test_failed_or_stale_qualification_disables_capability(tmp_path):
    runtime = Mock()
    runtime.state_dir = tmp_path
    runtime.fingerprint.side_effect = RunnerError('Missing cgroup limit')
    broker = Broker(runtime)
    report = broker.capability()
    assert not report['isolation_qualified'] and not report['execution_available']
    runtime.fingerprint.side_effect = None
    runtime.fingerprint.return_value = {'runtime':'fixture'}
    report = broker.capability()
    assert not report['isolation_qualified']
    identity = broker.identity()
    (tmp_path/'qualification.json').write_text(json.dumps({'identity':identity,'passed':True,'created':0}))
    assert not broker.capability()['isolation_qualified']


def test_cleanup_failure_is_reported_and_journal_retained(tmp_path):
    runtime = Mock()
    runtime.state_dir = tmp_path
    broker = Broker(runtime)
    runtime.fingerprint.side_effect = RunnerError('Denied before creation')
    raw=b"require('node:test')('x',()=>{});"
    with pytest.raises(RunnerError):
        broker._run_check([CapturedFile('case.cjs',raw,sha256(raw).hexdigest())],NodeCheck('case.cjs'))
    assert not (tmp_path/'active.json').exists()


def test_orphan_cleanup_ownership_and_repeatability(tmp_path):
    runtime = Mock()
    runtime.state_dir = tmp_path
    record={'name':NAME,'owner':OWNER,'pid':2147483647}
    journal=tmp_path/'active.json';journal.write_text(json.dumps(record))
    runtime.call.return_value = subprocess.CompletedProcess([],0,json.dumps([{'Config':{'Labels':{'org.codemind.isolation':'v1','org.codemind.owner':OWNER}}}]).encode(),b'')
    broker=Broker(runtime)
    with patch('safe_runner.broker.os.kill',side_effect=ProcessLookupError):
        assert broker.reconcile() == {'reconciled':1}
    assert broker.reconcile() == {'reconciled':0}
    journal.write_text(json.dumps(record))
    runtime.call.return_value = subprocess.CompletedProcess([],0,json.dumps([{'Config':{'Labels':{'org.codemind.isolation':'unrelated'}}}]).encode(),b'')
    runtime.call.reset_mock()
    with patch('safe_runner.broker.os.kill',side_effect=ProcessLookupError), pytest.raises(RunnerError, match='ownership'):
        broker.reconcile()
    assert journal.exists()
    assert not any(c.args[:2] == ('container','rm') for c in runtime.call.call_args_list)


def test_policy_tampering_is_rejected_before_start():
    with pytest.raises((RunnerError,KeyError)):
        verify_container({'HostConfig':{'NetworkMode':'host'},'Config':{}},Policy(),'id',NAME,OWNER)


def test_pre_cancelled_operation_never_creates_container(tmp_path):
    import threading
    runtime = Mock(); runtime.state_dir = tmp_path
    broker = Broker(runtime)
    cancel = threading.Event(); cancel.set()
    result = broker._run_check([CapturedFile('case.cjs', b'', sha256(b'').hexdigest())], NodeCheck('case.cjs'), cancel)
    assert result.state == 'cancelled' and result.cleanup_complete
    runtime.call.assert_not_called()
    runtime.fingerprint.assert_not_called()
