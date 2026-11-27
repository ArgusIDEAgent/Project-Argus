"""Non-executable metadata captured for test planning; never arbitrary JSON."""
from pathlib import PurePosixPath

POLICY = 'test-metadata-v1'
NAMES = {'package.json', 'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml',
         'yarn.lock', 'bun.lock', '.nvmrc', '.node-version'}


def is_metadata(path):
    name = PurePosixPath(path).name
    return name in NAMES or name == 'tsconfig.json' or (name.startswith('tsconfig.') and name.endswith('.json'))
