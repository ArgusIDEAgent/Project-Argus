"""Explicit trusted toolchain-image preparation. Never receives repository source.

Only this development/setup operation has network access. Product runs are offline.
Print the resulting immutable image identity for installation into the image lock.
"""
import io
import json
from pathlib import Path
import subprocess
import tarfile

from .runtime import Runtime


def prepare():
    root=Path(__file__).parent
    base=json.loads((root/'image-lock.json').read_text())['image']
    manifest=(root/'vitest-toolchain/package.json').read_bytes()
    lock=(root/'vitest-toolchain/package-lock.json').read_bytes()
    package_lock=json.loads(lock)
    for entry in package_lock['packages'].values():
        if entry.get('resolved') and (not entry['resolved'].startswith('https://registry.npmjs.org/') or not entry.get('integrity','').startswith('sha512-')):
            raise ValueError('Toolchain lock must use public integrity-pinned registry archives')
    dockerfile=f'''FROM {base}
WORKDIR /opt/vitest
COPY package.json package-lock.json ./
RUN test "$(npm --version)" = "11.19.1" && npm ci --ignore-scripts --legacy-peer-deps --no-audit --no-fund --registry=https://registry.npmjs.org --loglevel=error && npm cache clean --force && chmod -R a-w /opt/vitest
WORKDIR /work
LABEL org.codemind.vitest="5.1.2"
'''.encode()
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for name,data in [('Dockerfile',dockerfile),('package.json',manifest),('package-lock.json',lock)]:
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
            archive.addfile(info,io.BytesIO(data))
    runtime=Runtime()
    result=subprocess.run(runtime.command('build','--network=default','--pull=false','--tag','codemind/vitest-toolchain:5.1.2','-'),
        input=buffer.getvalue(),capture_output=True,env=runtime.env,timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-4000:])
    image=runtime.inspect('image','codemind/vitest-toolchain:5.1.2')
    return {'schema_version':1,'image':image['Id'],'base':base,'vitest':'5.1.2','vite':'8.4.0','npm':'11.19.1',
            'platform':'linux/arm64','package_lock_sha256':__import__('hashlib').sha256(lock).hexdigest()}


if __name__=='__main__':print(json.dumps(prepare()))
