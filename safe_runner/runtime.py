"""Local Colima VZ Docker transport; ignores ambient Docker config/credentials."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from knowledge_base.storage import checked_path
from .contracts import RunnerError


class Runtime:
    def __init__(self, state_dir=None):
        self.state_dir = checked_path(state_dir or Path.home() / '.codemind/data/safe-runner')
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.config = checked_path(self.state_dir / 'docker-config')
        self.config.mkdir(mode=0o700, exist_ok=True)
        # An empty config prevents Docker CLI proxy/credential/plugin inheritance.
        config_file = checked_path(self.config / 'config.json')
        if not config_file.exists():
            try:
                with config_file.open('x') as handle:
                    handle.write('{}\n')
                config_file.chmod(0o600)
            except FileExistsError:
                pass
        if config_file.read_text().strip() != '{}':
            raise RunnerError('Runner Docker configuration must be empty; inherited proxies/credentials are forbidden')
        self.executable = '/opt/homebrew/bin/docker' if Path('/opt/homebrew/bin/docker').is_file() else None
        self.socket = Path.home() / '.colima/codemind-runner/docker.sock'

    @property
    def env(self):
        return {'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                'HOME': str(self.config), 'DOCKER_CONFIG': str(self.config), 'LANG': 'C.UTF-8'}

    def command(self, *args):
        if not self.executable or not self.socket.exists() or not stat.S_ISSOCK(self.socket.stat().st_mode):
            raise RunnerError('Dedicated local Docker VM is unavailable; planning-only mode')
        if self.socket.stat().st_uid != os.getuid():
            raise RunnerError('Docker socket is not owned by this user')
        return [self.executable, '--host', 'unix://' + str(self.socket), *args]

    def call(self, *args, timeout=10, allow_error=False):
        try:
            value = subprocess.run(self.command(*args), capture_output=True, env=self.env,
                                   timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RunnerError('Docker control operation failed or timed out') from error
        if len(value.stdout) > 2000000 or len(value.stderr) > 65536:
            raise RunnerError('Docker control output exceeded its budget')
        if value.returncode and not allow_error:
            raise RunnerError('Docker control operation failed: ' + value.stderr.decode(errors='replace')[:300])
        return value

    def inspect(self, kind, identity):
        return json.loads(self.call(kind, 'inspect', identity).stdout)[0]

    def fingerprint(self, image):
        if sys.platform != 'darwin':
            raise RunnerError('Only the qualified macOS Colima VZ configuration is supported')
        info = json.loads(self.call('info', '--format', '{{json .}}').stdout)
        required = ('MemoryLimit', 'SwapLimit', 'CpuCfsQuota', 'PidsLimit')
        if (info.get('OSType') != 'linux' or info.get('Name') != 'colima-codemind-runner'
                or info.get('CgroupVersion') != '2' or info.get('DefaultRuntime') != 'runc' or not all(info.get(k) for k in required)
                or 'name=seccomp,profile=builtin' not in info.get('SecurityOptions', [])):
            raise RunnerError('Required Linux cgroup/seccomp controls unavailable')
        try:
            import yaml
            config = yaml.safe_load((Path.home() / '.colima/codemind-runner/colima.yaml').read_text())
            if config.get('mounts') not in (None, []) or config.get('forwardAgent') or config.get('vmType') != 'vz':
                raise RunnerError('Dedicated VM must use VZ with no host directory mounts or agent forwarding')
        except (OSError, ValueError, ImportError) as error:
            raise RunnerError('VM boundary configuration could not be checked') from error
        try:
            mounts = subprocess.run(['colima', 'ssh', '-p', 'codemind-runner', '--', 'cat', '/proc/mounts'],
                env={'PATH': self.env['PATH'], 'HOME': str(Path.home())}, capture_output=True, timeout=10, check=True).stdout.decode()
            if any(kind in mounts for kind in ('virtiofs', '9p', 'fuse.sshfs')):
                raise RunnerError('Running VM exposes host directory shares')
        except (OSError, subprocess.SubprocessError) as error:
            raise RunnerError('Running VM mount boundary could not be checked') from error
        node = self.inspect('image', image)
        if image.removeprefix('docker.io/library/') not in [r.removeprefix('docker.io/library/') for r in node.get('RepoDigests', [])] or node.get('Os') != 'linux' or node.get('Architecture') != 'arm64':
            raise RunnerError('Pinned Linux ARM64 runner image missing or mismatched')
        if node['Config'].get('Volumes'):
            raise RunnerError('Runner image declares unexpected persistent volumes')
        return {k: info[k] for k in ('ID', 'ServerVersion', 'KernelVersion', 'Architecture', 'CgroupVersion', 'DefaultRuntime', 'RuncCommit', 'ContainerdCommit')} | {
            'image_id': node['Id'], 'image': image,
            'docker_cli_sha256': __import__('hashlib').sha256(Path(self.executable).read_bytes()).hexdigest()}
