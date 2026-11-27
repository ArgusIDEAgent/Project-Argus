from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from pathlib import PurePosixPath


class RunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapturedFile:
    path: str
    data: bytes
    digest: str

    def __post_init__(self):
        path = PurePosixPath(self.path)
        if (not self.path or self.path == '.' or path.is_absolute() or path.as_posix() != self.path or
                any(p in ('.', '..', '.git', 'node_modules') for p in path.parts) or
                '\\' in self.path or '\x00' in self.path or len(self.path) > 1000):
            raise ValueError('Invalid captured workspace path')
        if type(self.data) is not bytes or sha256(self.data).hexdigest() != self.digest:
            raise ValueError('Captured file digest mismatch')


@dataclass(frozen=True)
class NodeCheck:
    path: str

    def __post_init__(self):
        CapturedFile(self.path, b'', sha256(b'').hexdigest())
        if PurePosixPath(self.path).suffix not in ('.js', '.mjs', '.cjs'):
            raise ValueError('Only native Node test files are supported')

    def argv(self):
        return ['/usr/local/bin/node', '--test', '--test-reporter=tap', '--', '/work/' + self.path]


@dataclass(frozen=True)
class Policy:
    version: str = 'isolation-v1'
    memory: int = 268435456
    cpus_nano: int = 500000000
    pids: int = 48
    workspace_bytes: int = 67108864
    output_disk_bytes: int = 8388608
    tmp_bytes: int = 16777216
    file_bytes: int = 8388608
    output_bytes: int = 65536
    seconds: int = 12
    cancellation_grace_seconds: int = 5
    input_files: int = 128
    input_bytes: int = 4194304

    def digest(self):
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class RunResult:
    state: str
    exit_code: int | None
    output: str
    output_truncated: bool
    cleanup_complete: bool
    cleanup_error: str | None
    elapsed_seconds: float
    container_id: str
