"""Internal P5 broker. No HTTP route or plan-selection action launches runs."""
import base64
import fcntl
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time
import uuid

from .artifacts import sanitize
from .contracts import CapturedFile, NodeCheck, Policy, RunResult, RunnerError
from .policy import create_args, verify_container, policy_digest, LABEL
from .runtime import Runtime
from .dependencies import Archive

_LOCK = threading.Lock()


class _Cancelled(Exception):
    pass


class Broker:
    def __init__(self, runtime=None, adapter='node'):
        self.runtime = runtime or Runtime()
        lock = json.loads(Path(__file__).with_name('image-lock.json').read_text())
        self.image = lock['image']
        if adapter not in ('node','vitest'):
            raise ValueError('Unknown runner adapter')
        self.adapter=adapter
        if adapter=='vitest':
            from .vitest import image_lock
            self.image=image_lock()['image']
        self.policy = Policy()

    @property
    def attestation(self):
        return self.runtime.state_dir / ('qualification-vitest.json' if self.adapter=='vitest' else 'qualification.json')

    def identity(self):
        return {'runtime': self.runtime.fingerprint(self.image),
                'policy_digest': policy_digest(self.policy, self.image)}

    def capability(self, during_owned_launch=False):
        result = {'isolation_qualified': False, 'execution_available': False,
                  'reason': 'Execution requires capture-specific review and separate preparation/run authorization; pinned registry archive adapter.'}
        try:
            journal = self.runtime.state_dir / 'active.json'
            if journal.exists():
                if not during_owned_launch or journal.is_symlink() or json.loads(journal.read_text()).get('pid') != os.getpid():
                    raise RunnerError('Runner cleanup is required before qualification can be used')
            identity = self.identity()
            attestation = self.attestation
            if attestation.is_symlink() or not attestation.exists() or attestation.stat().st_size > 200000:
                raise RunnerError('Run isolation qualification for this runtime and policy first')
            report = json.loads(attestation.read_text())
            if report['identity'] != identity or report['passed'] is not True or not 0 <= time.time() - report['created'] <= 86400:
                raise RunnerError('Isolation qualification is stale or differs from the current runtime/policy')
            result.update(isolation_qualified=True, identity=identity)
        except (OSError, ValueError, KeyError, RunnerError) as error:
            result['reason'] = str(error)
        return result

    def _record(self, name, owner):
        journal = self.runtime.state_dir / 'active.json'
        if journal.is_symlink():
            raise RunnerError('Runner journal must not be a symlink')
        journal.write_text(json.dumps({'name': name, 'owner': owner, 'pid': os.getpid()}))
        journal.chmod(0o600)
        return journal

    def reconcile(self):
        """Remove only containers carrying this broker's exact labels, never global prune."""
        journal = self.runtime.state_dir / 'active.json'
        if not journal.exists():
            return {'reconciled': 0}
        if journal.is_symlink():
            raise RunnerError('Unsafe runner journal')
        record = json.loads(journal.read_text())
        if type(record['pid']) is not int or record['pid'] <= 0:
            raise RunnerError('Invalid orphan journal process identity')
        try:
            os.kill(record['pid'], 0)
            raise RunnerError('Another runner session may still be active')
        except ProcessLookupError:
            pass
        value = self.runtime.call('container', 'inspect', record['name'], allow_error=True)
        if value.returncode == 0:
            state = json.loads(value.stdout)[0]
            labels = state['Config'].get('Labels', {})
            if labels.get(LABEL) != 'v1' or labels.get('org.codemind.owner') != record['owner']:
                raise RunnerError('Orphan ownership mismatch; container left untouched')
            self.runtime.call('container', 'rm', '--force', record['name'], timeout=5)
        # A failing daemon must not be mistaken for an absent container.
        self.runtime.call('info', '--format', '{{.ID}}')
        remaining = self.runtime.call('container', 'ls', '--all', '--filter', 'name=^/' + record['name'] + '$', '--format', '{{.ID}}')
        if remaining.stdout.strip():
            raise RunnerError('Orphan container remains; recovery journal retained')
        journal.unlink()
        return {'reconciled': 1}

    def _run_check(self, files, check, cancel=None, before_launch=None, dependencies=()):
        """Internal adapter operation, used only by fixed qualification fixtures in P5."""
        if not _LOCK.acquire(blocking=False):
            raise RunnerError('A runner operation is already active')
        name = 'cm-runner-' + uuid.uuid4().hex
        owner = uuid.uuid4().hex
        journal = None
        lock_handle = None
        proc = None
        started = time.monotonic()
        state, exit_code, output, truncated = 'failed', None, bytearray(), False
        cleanup_error = None
        failure = None
        cancel_observed = None
        try:
            lock_path = self.runtime.state_dir / 'broker.lock'
            if lock_path.is_symlink():
                raise RunnerError('Unsafe runner lock')
            lock_handle = lock_path.open('a')
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RunnerError('Another runner process is active') from error
            if type(check) is not NodeCheck or any(type(f) is not CapturedFile for f in files):
                raise ValueError('Broker accepts captured files and adapter-owned Node checks only')
            if check.framework=='vitest' and self.adapter!='vitest':
                raise RunnerError('Vitest requires its separately qualified toolchain image')
            paths = [file.path for file in files]
            if type(dependencies) is not tuple or len(dependencies) > 8 or any(type(d) is not Archive for d in dependencies):
                raise ValueError('Only immutable validated dependency archives are accepted')
            if sum(len(d.data) for d in dependencies) > 1048576:
                raise ValueError('Dependency archive budget exceeded')
            if len(paths) != len(set(paths)) or check.path not in paths:
                raise ValueError('Check must belong to a duplicate-free captured input')
            if len(files) > self.policy.input_files or sum(len(f.data) for f in files) + sum(len(d.data) for d in dependencies) > self.policy.input_bytes:
                raise ValueError('Captured materialization exceeds input budgets')
            if cancel and cancel.is_set():
                raise _Cancelled()
            # No symlink objects/hardlinks/host paths are accepted: only immutable bytes.
            identity = self.identity()
            if (self.runtime.state_dir / 'active.json').exists():
                self.reconcile()
            journal = self._record(name, owner)
            self.runtime.call(*create_args(self.policy, self.image, name, owner))
            value = self.runtime.inspect('container', name)
            verify_container(value, self.policy, identity['runtime']['image_id'], name, owner)
            if cancel and cancel.is_set():
                raise _Cancelled()
            if before_launch is not None:
                before_launch()
            payload = json.dumps({'files': [{'path': f.path, 'content': base64.b64encode(f.data).decode(), 'digest': f.digest} for f in files],
                                  'check': check.path, 'structured': check.structured,
                                  'framework': check.framework,
                                  'packages': [d.payload() for d in dependencies]}).encode()
            proc = subprocess.Popen(self.runtime.command('container', 'start', '--attach', '--interactive', name),
                                    env=self.runtime.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            def send():
                try:
                    proc.stdin.write(payload)
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            writer = threading.Thread(target=send, daemon=True)
            writer.start()
            state = 'completed'
            deadline = time.monotonic() + self.policy.seconds
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    if cancel and cancel.is_set():
                        cancel_observed = time.monotonic(); state = 'cancelled'; break
                    if time.monotonic() >= deadline:
                        state = 'timed_out'; break
                    if not selector.select(.05):
                        if proc.poll() is not None: break
                        continue
                    chunk = os.read(proc.stdout.fileno(), 8192)
                    if not chunk: break
                    available = self.policy.output_bytes - len(output)
                    output.extend(chunk[:available])
                    if len(chunk) > available:
                        truncated = True; state = 'output_limited'; break
            if state != 'completed':
                self.runtime.call('container', 'kill', '--signal', 'KILL', name, timeout=3, allow_error=True)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=2)
                if state == 'completed': state = 'failed'
            writer.join(timeout=1)
            value = self.runtime.inspect('container', name)
            exit_code = value['State'].get('ExitCode')
            if value['State'].get('OOMKilled') and state == 'completed':
                state = 'memory_limited'
        except _Cancelled:
            state = 'cancelled'
        except Exception as error:
            failure = error
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill(); proc.wait(timeout=2)
            if proc is not None:
                for handle in (proc.stdin, proc.stdout):
                    try:
                        handle.close()
                    except (OSError, ValueError):
                        pass
            if journal is not None:
                try:
                    self.runtime.call('container', 'rm', '--force', name, timeout=self.policy.cancellation_grace_seconds, allow_error=True)
                    remaining = self.runtime.call('container', 'ls', '--all', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}')
                    if remaining.stdout.strip():
                        raise RunnerError('Owned container is still present after cleanup')
                    journal.unlink(missing_ok=True)
                except (OSError, RunnerError) as error:
                    cleanup_error = str(error)
            if lock_handle is not None:
                lock_handle.close()
            _LOCK.release()
        if cancel_observed is not None and time.monotonic() - cancel_observed > self.policy.cancellation_grace_seconds:
            cleanup_error = cleanup_error or 'Cancellation cleanup exceeded the qualified grace period'
        if cleanup_error:
            state = 'cleanup_failed'
            if failure is not None:
                cleanup_error = str(failure)[:200] + '; cleanup: ' + cleanup_error
            attestation = self.attestation
            if attestation.exists() and not attestation.is_symlink():
                attestation.unlink()
        elif failure is not None:
            raise failure
        return RunResult(state, exit_code, sanitize(bytes(output)), truncated, cleanup_error is None,
                         cleanup_error, round(time.monotonic() - started, 3), name)


def isolation_capabilities():
    try:
        return Broker().capability()
    except (OSError, ValueError, KeyError, RunnerError) as error:
        return {'isolation_qualified': False, 'execution_available': False, 'reason': str(error)}
