"""Narrow Vitest 5.1.2 toolchain profile; no repository configs or package scripts."""
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).parent


def image_lock():
    lock=json.loads((ROOT/'vitest-image-lock.json').read_text())
    if lock['package_lock_sha256']!=sha256((ROOT/'vitest-toolchain/package-lock.json').read_bytes()).hexdigest():
        raise ValueError('Vitest toolchain lock identity changed; rebuild and qualify')
    return lock


def project(package, lock, snapshot):
    pinned=image_lock()
    if any('.config.' in v.path for v in snapshot.after if v.data is not None) or any(Path(v.path).name.startswith('tsconfig') for v in snapshot.after_metadata):
        raise ValueError('Vitest profile supports no repository configuration; custom config/TS projects remain unsupported')
    if any(package.get(k) for k in ('workspaces','imports','overrides','optionalDependencies','peerDependencies','vitest','vite')):
        raise ValueError('Unsupported Vitest project configuration')
    expected={'vitest':pinned['vitest'],'vite':pinned['vite']}
    declared={**package.get('dependencies',{}),**package.get('devDependencies',{})}
    if declared!=expected:
        raise ValueError('Vitest profile requires only exact vitest 5.1.2 and vite 8.4.0 dependencies')
    if lock.get('lockfileVersion') not in (2,3) or not isinstance(lock.get('packages'),dict):
        raise ValueError('Captured npm v2/v3 lockfile required for Vitest')
    root=lock['packages'].get('',{})
    if any(root.get(k,{})!=package.get(k,{}) for k in ('dependencies','devDependencies')):
        raise ValueError('Vitest manifest and root lock declarations differ')
    canonical=json.loads((ROOT/'vitest-toolchain/package-lock.json').read_text())['packages']
    def identities(entries):
        return {path:{k:value.get(k) for k in ('version','resolved','integrity','dependencies','optionalDependencies','peerDependencies','os','cpu','link','hasInstallScript','inBundle')} for path,value in entries.items() if path}
    if identities(lock['packages'])!=identities(canonical):
        raise ValueError('Project lock differs from pinned qualified Vitest toolchain graph')
    return pinned
