"""Pinned bounded registry ingestion; never executes or extracts packages on host."""
import base64
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time

from .contracts import RunnerError

VERSION = 'registry-archive-v1'
NAME = r'(?:@[a-z0-9_-]+/)?[a-z0-9_-][a-z0-9._-]*'
SEMVER = r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?'
MAX_ARCHIVE = 262144
MAX_TOTAL = 1048576


def spec(value):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or len(value[k]) > limit for k, limit in (('name',214),('version',100),('url',600),('integrity',95))):
        raise ValueError('Package identity exceeds supported limits')
    if not isinstance(value, dict) or not re.fullmatch(NAME, value.get('name', '')) or not re.fullmatch(SEMVER, value.get('version', '')):
        raise ValueError('Unsupported package name/version')
    expected = 'https://registry.npmjs.org/' + value['name'] + '/-/' + value['name'].split('/')[-1] + '-' + value['version'] + '.tgz'
    if value.get('url') != expected:
        raise ValueError('Only exact public npm registry tarball URLs are supported')
    integrity = value.get('integrity', '')
    if not isinstance(integrity, str) or not integrity.startswith('sha512-'):
        raise ValueError('A single SHA-512 integrity value is required')
    try:
        raw = base64.b64decode(integrity[7:], validate=True)
    except ValueError as error:
        raise ValueError('Invalid package integrity') from error
    if len(raw) != 64:
        raise ValueError('Invalid SHA-512 digest')
    return value


def locked_packages(package, lock):
    if package.get('workspaces') or package.get('imports') or package.get('overrides') or package.get('optionalDependencies') or package.get('peerDependencies'):
        raise ValueError('Workspaces, import maps, overrides, optional and peer dependencies are unsupported')
    if lock.get('lockfileVersion') not in (2, 3) or not isinstance(lock.get('packages'), dict):
        raise ValueError('Captured npm lockfile v2/v3 with packages map required')
    entries = lock['packages']
    if len(entries) > 9:
        raise ValueError('Preparation supports at most eight packages')
    root = entries.get('', {})
    result = []
    for path, item in entries.items():
        if path == '':
            continue
        name = path.removeprefix('node_modules/')
        if path != 'node_modules/' + name or not re.fullmatch(NAME, name) or not isinstance(item, dict):
            raise ValueError('Only flat registry package layouts are supported')
        if any(item.get(k) for k in ('link', 'inBundle', 'hasInstallScript', 'optional', 'os', 'cpu', 'peerDependencies', 'optionalDependencies')):
            raise ValueError('Native/platform/install-hook/optional/peer packages are unsupported')
        result.append(spec({'name': name, 'version': item.get('version'), 'url': item.get('resolved'),
                            'integrity': item.get('integrity'), 'dependencies': item.get('dependencies', {})}))
    if len(result) > 8:
        raise ValueError('Preparation supports at most eight packages')
    versions = {p['name']: p['version'] for p in result}
    def exact(deps):
        if not isinstance(deps, dict) or any(not isinstance(v, str) or versions.get(k) != v for k, v in deps.items()):
            raise ValueError('Dependency constraints must be exact versions matching the flat lockfile')
    for field in ('dependencies', 'devDependencies'):
        if package.get(field, {}) != root.get(field, {}):
            raise ValueError('Manifest and root lock dependencies differ')
        exact(package.get(field, {}))
    for item in result:
        exact(item['dependencies'])
    # Reject unrelated lock entries as well as missing transitive entries.
    reachable = set(package.get('dependencies', {})) | set(package.get('devDependencies', {}))
    while True:
        expanded = reachable | {d for p in result if p['name'] in reachable for d in p['dependencies']}
        if expanded == reachable:
            break
        reachable = expanded
    if reachable != set(versions):
        raise ValueError('Lockfile contains unrelated packages')
    return sorted(result, key=lambda p: p['name'])


@dataclass(frozen=True)
class Archive:
    metadata_json: str
    data: bytes

    def __post_init__(self):
        metadata = spec(json.loads(self.metadata_json))
        if type(self.data) is not bytes or len(self.data) > MAX_ARCHIVE:
            raise ValueError('Package archive exceeds byte limit')
        actual = 'sha512-' + base64.b64encode(hashlib.sha512(self.data).digest()).decode()
        if actual != metadata['integrity']:
            raise ValueError('Package archive integrity mismatch')

    def payload(self):
        return {**json.loads(self.metadata_json), 'content': base64.b64encode(self.data).decode()}


class RegistryConnection(http.client.HTTPSConnection):
    def connect(self):
        # Resolve once, validate, and connect to that same address. No DNS rebinding
        # gap, proxy, redirect, registry config, client credential or private IP.
        addresses = socket.getaddrinfo('registry.npmjs.org', 443, socket.AF_INET, socket.SOCK_STREAM)
        public = [a[4][0] for a in addresses if ipaddress.ip_address(a[4][0]).is_global]
        if not public:
            raise RunnerError('Registry did not resolve to a public address')
        sock = socket.create_connection((public[0], 443), timeout=2)
        try:
            self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname='registry.npmjs.org')
        except BaseException:
            sock.close()
            raise


def download(packages, cancel):
    deadline = time.monotonic() + 15
    archives, total = [], 0
    for package in packages:
        spec(package)
        if cancel.is_set() or time.monotonic() >= deadline:
            raise RunnerError('Dependency download cancelled or timed out')
        connection = RegistryConnection('registry.npmjs.org', timeout=2)
        try:
            connection.request('GET', package['url'].removeprefix('https://registry.npmjs.org'), headers={'Accept-Encoding': 'identity', 'User-Agent': 'CodeMind-registry-archive-v1'})
            reply = connection.getresponse()
            if reply.status != 200 or reply.getheader('Content-Encoding', 'identity') != 'identity':
                raise RunnerError('Registry response refused; redirects are not followed')
            parts, size = [], 0
            while True:
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise RunnerError('Dependency download cancelled or timed out')
                chunk = reply.read1(16384)
                if not chunk:
                    break
                size += len(chunk); total += len(chunk)
                if size > MAX_ARCHIVE or total > MAX_TOTAL:
                    raise RunnerError('Dependency download byte budget exceeded')
                parts.append(chunk)
            archives.append(Archive(json.dumps(package, sort_keys=True), b''.join(parts)))
        finally:
            connection.close()
    return tuple(archives)
