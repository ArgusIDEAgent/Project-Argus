"""No caller-supplied flags, mounts, environment, ports or shell commands."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re

from .contracts import Policy, RunnerError

BOOTSTRAP = "const installDependencies=(()=>{const module={exports:{}};\n" + Path(__file__).with_name('unpack.cjs').read_text() + "\nreturn module.exports;})();\n" + Path(__file__).with_name('bootstrap.cjs').read_text()
LABEL = 'org.codemind.isolation'


def policy_digest(policy, image):
    modules = {name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
               for name in ('policy.py','runtime.py','broker.py','contracts.py','artifacts.py','fixtures.py','qualify.py','execution.py','reports.py','dependencies.py','comparison.py','templates.py','vitest.py','vitest_reports.py','prepare_vitest.py','qualify_vitest.py')}
    modules['vitest_toolchain']=sha256(Path(__file__).with_name('vitest-image-lock.json').read_bytes()).hexdigest()
    return sha256(json.dumps([asdict(policy), image, BOOTSTRAP, modules], sort_keys=True).encode()).hexdigest()


def create_args(policy, image, name, owner):
    from .vitest import image_lock
    if not re.fullmatch(r'docker.io/library/node@sha256:[a-f0-9]{64}', image) and image!=image_lock()['image']:
        raise RunnerError('An explicitly pinned official Node image digest is required')
    if not re.fullmatch(r'cm-runner-[a-f0-9]{32}', name) or not re.fullmatch(r'[a-f0-9]{32}', owner):
        raise RunnerError('Invalid broker identity')
    return ['container', 'create', '--name', name, '--label', LABEL + '=v1',
            '--label', 'org.codemind.owner=' + owner, '--label', 'org.codemind.policy=' + policy_digest(policy, image),
            '--interactive', '--pull=never', '--entrypoint', '/usr/local/bin/node',
            '--workdir', '/work', '--user', '65532:65532', '--hostname', 'codemind-sandbox',
            '--network', 'none', '--ipc', 'none', '--cgroupns', 'private',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true',
            '--memory', str(policy.memory), '--memory-swap', str(policy.memory),
            '--cpus', str(policy.cpus_nano / 1000000000), '--pids-limit', str(policy.pids),
            '--ulimit', 'nofile=128:128', '--ulimit', 'core=0:0',
            '--ulimit', f'fsize={policy.file_bytes}:{policy.file_bytes}',
            '--tmpfs', f'/work:rw,noexec,nosuid,nodev,size={policy.workspace_bytes},uid=65532,gid=65532,mode=0700',
            '--tmpfs', f'/tmp:rw,noexec,nosuid,nodev,size={policy.tmp_bytes},uid=65532,gid=65532,mode=0700',
            '--tmpfs', f'/out:rw,noexec,nosuid,nodev,size={policy.output_disk_bytes},uid=65532,gid=65532,mode=0700',
            '--env', 'HOME=/work/home', '--env', 'LANG=C.UTF-8', '--env', 'TZ=UTC',
            '--log-driver', 'none', '--restart', 'no', '--stop-timeout', '1',
            '--no-healthcheck', image, '-e', BOOTSTRAP]


def verify_container(value, policy, image_id, name, owner):
    host, config = value['HostConfig'], value['Config']
    checks = [value['Name'] == '/' + name, value['Image'] == image_id,
              config['User'] == '65532:65532', config['WorkingDir'] == '/work',
              config['Entrypoint'] == ['/usr/local/bin/node'], config['Cmd'] == ['-e', BOOTSTRAP],
              config['Labels'].get('org.codemind.owner') == owner,
              host['NetworkMode'] == 'none', host['ReadonlyRootfs'], not host['Privileged'],
              host['CapDrop'] == ['ALL'], not host['CapAdd'],
              'no-new-privileges=true' in host['SecurityOpt'],
              len(host['SecurityOpt']) == 1,
              host['Memory'] == policy.memory, host['MemorySwap'] == policy.memory,
              host['NanoCpus'] == policy.cpus_nano, host['PidsLimit'] == policy.pids,
              host['PidMode'] == '', host['IpcMode'] == 'none', host['CgroupnsMode'] == 'private',
              host['LogConfig']['Type'] == 'none', host['RestartPolicy']['Name'] == 'no',
              not host.get('Binds'), not host.get('Mounts'), not host.get('Devices'),
              not host.get('DeviceRequests'), not host.get('VolumesFrom'),
              not host.get('PortBindings'), not host.get('PublishAllPorts'), not config.get('Volumes'),
              not host.get('ExtraHosts'), not host.get('Links')]
    expected = {'/work': policy.workspace_bytes, '/tmp': policy.tmp_bytes, '/out': policy.output_disk_bytes}
    if set(host['Tmpfs']) != set(expected):
        checks.append(False)
    for target, size in expected.items():
        options = host['Tmpfs'].get(target, '')
        checks.append(all(item in options.split(',') for item in ('rw', 'noexec', 'nosuid', 'nodev', f'size={size}', 'uid=65532', 'gid=65532', 'mode=0700')))
    limits = {item['Name']: (item['Soft'], item['Hard']) for item in host['Ulimits']}
    checks.append(limits == {'nofile': (128,128), 'core': (0,0), 'fsize': (policy.file_bytes,policy.file_bytes)})
    env = {key.split('=',1)[0]: key.split('=',1)[1] for key in config.get('Env', [])}
    checks.append(set(env) <= {'PATH', 'NODE_VERSION', 'YARN_VERSION', 'HOME', 'LANG', 'TZ'})
    if not all(checks):
        raise RunnerError('Created container does not match enforced isolation policy')
