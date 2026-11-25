"""Version 1 immutable, in-memory capture contracts (no live source references)."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CaptureLimits:
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 32_000_000  # before + after, including duplicate bytes
    max_files: int = 10_000
    max_git_output_bytes: int = 8_000_000
    git_timeout_seconds: float = 10
    attempts: int = 3
    max_diff_lines: int = 10_000
    max_captures: int = 2

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == 'git_timeout_seconds':
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise ValueError('Git timeout must be finite and positive')
            elif type(value) is not int or value <= 0:
                raise ValueError('Capture budgets must be positive integers')
        if self.attempts > 5:
            raise ValueError('Capture attempts must not exceed five')


@dataclass(frozen=True)
class SourceVersion:
    path: str
    mode: str
    digest: str | None
    data: bytes | None
    exclusion: str | None = None
    object_id: str | None = None


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    # Half-open, one-based before/after line ranges, including empty insertions.
    ranges: tuple[tuple[int, int, int, int], ...] = ()
    limitation: str | None = None
    rename_candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangeSnapshot:
    schema_version: int
    id: str
    repository_id: str
    root: str
    git_root: str
    head: str | None
    mode: str
    captured_at: str
    digest: str
    before: tuple[SourceVersion, ...]
    after: tuple[SourceVersion, ...]
    staging: tuple[tuple[str, str, str, str], ...]
    changes: tuple[FileChange, ...]
    conflicts: tuple[str, ...]
    notices: tuple[str, ...]
    definitive: bool
    size_bytes: int
    # Conflict eligibility is separate from the filesystem consistency guarantee.
    consistency: str = 'observed_stable'
    atomic_snapshot_id: str | None = None
