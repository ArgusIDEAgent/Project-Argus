"""Read-only Git capture with bounded retries and session-owned immutable bytes.

Complete observations, file identities and timestamps must agree, including a
post-diff check. This is observed stability, not an atomic filesystem snapshot.
No Git checkout, filters, diff drivers, repository programs or index writes run.
"""
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import stat
import subprocess
import time
import threading
import uuid

from knowledge_base.identity import repository_id
from knowledge_base.scanner import EXCLUDED_DIRS, SOURCE_EXTENSIONS, _sensitive
from .contracts import CaptureLimits, ChangeSnapshot, SourceVersion
from .diff import compare
from .metadata_policy import POLICY, is_metadata


class CaptureError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _git(root, limits, *args, allow_missing=False):
    try:
        return _git_read(root, limits, *args, allow_missing=allow_missing)
    except subprocess.TimeoutExpired as error:
        raise CaptureError('git_timeout', 'Git read exceeded its deadline') from error
    except OSError as error:
        raise CaptureError('git_unavailable', 'Controlled Git read could not start or complete') from error


def _git_read(root, limits, *args, allow_missing=False):
    # Discard injected Git routing/config variables; preserve OS executable lookup.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
    command = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
               '-c', 'core.hooksPath=/dev/null', '-c', 'core.untrackedCache=false',
               '-c', 'protocol.allow=never', '-C', str(root), *args]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as proc:
        output = bytearray()
        deadline = time.monotonic() + limits.git_timeout_seconds
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise CaptureError('git_timeout', 'Git read exceeded its deadline')
                    block = os.read(proc.stdout.fileno(), 65536)
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > limits.max_git_output_bytes:
                        raise CaptureError('git_output_limit', 'Git inventory exceeds capture budget')
            proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.returncode and not (allow_missing and proc.returncode == 1):
            raise CaptureError('git_read_failed', 'Controlled Git read failed: ' + args[0])
        return bytes(output) if proc.returncode == 0 else None


def _path(raw):
    value = os.fsdecode(raw)
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value or value == '.':
        raise CaptureError('invalid_path', 'Git returned a non-relative path')
    return value


def _head(root, limits):
    value = _git(root, limits, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}', allow_missing=True)
    if value is None:
        # Only an actual unborn branch is all-added; corrupt/missing objects fail closed.
        ref = _git(root, limits, 'symbolic-ref', '-q', 'HEAD', allow_missing=True)
        if ref is None or _git(root, limits, 'show-ref', '--verify', '--quiet',
                               os.fsdecode(ref.strip()), allow_missing=True) is not None:
            raise CaptureError('invalid_head', 'HEAD is not a valid commit or unborn branch')
        return None
    return value.strip().decode('ascii')


def _policy(path):
    parts = PurePosixPath(path).parts
    if any(p in EXCLUDED_DIRS for p in parts):
        return 'excluded_directory'
    if _sensitive(parts[-1]):
        return 'sensitive_filename'
    if PurePosixPath(path).suffix.lower() not in SOURCE_EXTENSIONS:
        return 'unsupported_file_type'
    return None


def _version(path, mode, data=None, exclusion=None, object_id=None):
    if data is not None:
        if b'\0' in data:
            exclusion = 'binary'
        else:
            try:
                data.decode('utf-8')
            except UnicodeDecodeError:
                exclusion = 'non_utf8'
    return SourceVersion(path, mode, _hash(data) if data is not None else None,
                         data if not exclusion else None, exclusion, object_id)


def _signature(observed):
    return (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_size,
            observed.st_mtime_ns, observed.st_ctime_ns)


def _directory_identity(path):
    """Validate every directory component, including parents of the selected root."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        observed = os.fstat(fd)
        return observed.st_dev, observed.st_ino
    finally:
        os.close(fd)


def _read_saved(root_fd, path, limit):
    """Use directory-relative, no-follow opens at every component, not resolve/open."""
    parent = os.dup(root_fd)
    try:
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        with os.fdopen(os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                               dir_fd=parent), 'rb') as source:
            before = os.fstat(source.fileno())
            mode = '100755' if before.st_mode & 0o111 else '100644'
            if not stat.S_ISREG(before.st_mode):
                return mode, None, 'not_regular_file', _signature(before)
            if before.st_size > limit:
                return mode, None, 'oversized', _signature(before)
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
            if _signature(before) != _signature(after):
                raise CaptureError('source_changed', 'A source changed during capture')
            if len(data) > limit:
                raise CaptureError('source_changed', 'A source grew during capture')
            return mode, data, None, _signature(after)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            return '120000', None, 'symlink_or_non_directory', None
        if error.errno == errno.ENOENT:
            return None
        raise CaptureError('source_unreadable', 'A saved source could not be read') from error
    finally:
        os.close(parent)


class CaptureStore:
    """One selected folder per session; close/release drops owned bytes, no disk artifacts.

    Callers retaining a snapshot retain its immutable bytes. Store capacity is
    bounded; callers must release old captures before publishing further ones.
    """
    def __init__(self, root, limits=None):
        if os.name != 'posix':
            raise CaptureError('unsupported_platform', 'Capture requires POSIX no-follow directory opens')
        self.limits = limits or CaptureLimits()
        requested = Path(os.path.abspath(Path(root).expanduser()))
        if any(p.is_symlink() for p in (requested, *requested.parents)
               if str(p) not in ('/tmp', '/var', '/etc')):
            raise CaptureError('symlink_root', 'Selected folder must not be symlinked')
        self.root = requested.resolve(strict=True)
        if not self.root.is_dir():
            raise CaptureError('invalid_root', 'Selected folder must be a directory')
        git_root = _git(self.root, self.limits, 'rev-parse', '--show-toplevel')
        self.git_root = Path(os.fsdecode(git_root.removesuffix(b'\n'))).resolve(strict=True)
        if self.root != self.git_root and self.git_root not in self.root.parents:
            raise CaptureError('invalid_boundary', 'Selected folder is outside the Git root')
        git_dir = _git(self.root, self.limits, 'rev-parse', '--absolute-git-dir')
        self.git_dir = Path(os.fsdecode(git_dir.removesuffix(b'\n'))).resolve(strict=True)
        self._anchors = tuple((path, _directory_identity(path))
                              for path in dict.fromkeys((self.root, self.git_root, self.git_dir)))
        self.repo_id = repository_id(self.root)
        self._captures = {}
        self._closed = False
        self._lock = threading.RLock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._lock:
            self._captures.clear()
            self._closed = True

    def get(self, capture_id):
        with self._lock:
            return self._captures[capture_id]

    def release(self, capture_id):
        with self._lock:
            del self._captures[capture_id]

    def _check_boundary(self):
        try:
            if any(_directory_identity(path) != identity for path, identity in self._anchors):
                raise CaptureError('boundary_changed', 'Repository directory identity changed; reopen the session')
        except OSError as error:
            raise CaptureError('boundary_changed', 'Repository directory became unavailable or symlinked') from error
        boundary = _git(self.root, self.limits, 'rev-parse', '--show-toplevel')
        git_dir = _git(self.root, self.limits, 'rev-parse', '--absolute-git-dir')
        if (Path(os.fsdecode(boundary.removesuffix(b'\n'))).resolve() != self.git_root
                or Path(os.fsdecode(git_dir.removesuffix(b'\n'))).resolve() != self.git_dir):
            raise CaptureError('boundary_changed', 'Git repository boundary changed during capture')

    def _observe(self):
        limits, root = self.limits, self.root
        self._check_boundary()
        head = _head(root, limits)
        tree = _git(root, limits, 'ls-tree', '-r', '-l', '-z', head, '--', '.') if head else b''
        staging_raw = _git(root, limits, 'ls-files', '--stage', '-z', '--', '.')
        untracked = _git(root, limits, 'ls-files', '--others', '--exclude-standard', '-z', '--', '.')
        ignored = _git(root, limits, 'ls-files', '--others', '--ignored', '--exclude-standard',
                       '--directory', '-z', '--', '.')
        staging, baseline, current, paths = [], [], [], set()
        for record in staging_raw.split(b'\0'):
            if record:
                meta, raw = record.split(b'\t', 1)
                mode, oid, stage = meta.decode('ascii').split()
                path = _path(raw)
                staging.append((path, mode, oid, stage))
                paths.add(path)
        tree_entries = []
        for record in tree.split(b'\0'):
            if record:
                meta, raw = record.split(b'\t', 1)
                mode, kind, oid, size = meta.decode('ascii').split()
                path = _path(raw)
                tree_entries.append((path, mode, kind, oid, size))
                paths.add(path)
        paths.update(_path(p) for p in untracked.split(b'\0') if p)
        ignored_paths = {_path(p.rstrip(b'/')) for p in ignored.split(b'\0') if p} - paths
        if len(paths | ignored_paths) > limits.max_files:
            raise CaptureError('file_limit', 'Inventory exceeds capture file budget')
        total = 0

        def account(data):
            nonlocal total
            total += len(data) if data else 0
            if total > limits.max_total_bytes:
                raise CaptureError('size_limit', 'Capture exceeds total byte budget')

        before_metadata, after_metadata = [], []
        for path, mode, kind, oid, size in tree_entries:
            reason = ('submodule' if mode == '160000' else 'symlink' if mode == '120000'
                      else _policy(path))
            if reason is None and int(size) > limits.max_file_bytes:
                reason = 'oversized'
            metadata = is_metadata(path) and reason in (None, 'unsupported_file_type')
            if metadata and int(size) > limits.max_file_bytes:
                reason = 'oversized'
                metadata = False
            data = _git(root, limits, 'cat-file', 'blob', oid) if not reason or metadata else None
            account(data)
            baseline.append(_version(path, mode, None if metadata else data, reason, oid))
            if is_metadata(path):
                before_metadata.append(_version(path, mode, data, None if metadata else reason, oid))
        modules = {p: oid for p, mode, oid, stage in staging if mode == '160000' and stage == '0'}
        receipts = []
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for path in sorted(paths | ignored_paths):
                reason = 'ignored' if path in ignored_paths else 'submodule' if path in modules else _policy(path)
                metadata = is_metadata(path) and reason in (None, 'unsupported_file_type')
                if reason and not metadata:
                    # lstat checks presence without reading excluded source bytes.
                    # No traversal reads are performed for excluded entries.
                    if path in ignored_paths or os.path.lexists(root / path):
                        current.append(_version(path, '160000' if reason == 'submodule' else 'excluded',
                                                exclusion=reason, object_id=modules.get(path)))
                        if is_metadata(path):
                            after_metadata.append(_version(path, 'excluded', exclusion=reason))
                    continue
                saved = _read_saved(root_fd, path, limits.max_file_bytes)
                if saved is not None:
                    mode, data, reason, signature = saved
                    receipts.append((path, signature))
                    account(data)
                    if metadata:
                        after_metadata.append(_version(path, mode, data, reason))
                        current.append(_version(path, mode, exclusion='unsupported_file_type'))
                    else:
                        current.append(_version(path, mode, data, reason))
        finally:
            os.close(root_fd)
        if _head(root, limits) != head:
            raise CaptureError('source_changed', 'HEAD moved during capture')
        self._check_boundary()
        return (head, tuple(sorted(baseline, key=lambda v: v.path)), tuple(current),
                tuple(sorted(staging)), total, tuple(receipts), tuple(before_metadata), tuple(after_metadata))

    def capture(self, *, require_atomic=False):
        with self._lock:
            if require_atomic:
                raise CaptureError('atomic_capture_unavailable',
                                   'This backend provides observed stability, not atomic filesystem capture')
            return self._capture()

    def _capture(self):
        if self._closed:
            raise CaptureError('closed', 'Capture session is closed')
        if len(self._captures) >= self.limits.max_captures:
            raise CaptureError('retention_limit', 'Release a capture before publishing another')
        for _ in range(self.limits.attempts):
            try:
                first = self._observe()
                second = self._observe()
                if first != second:
                    continue
                head, before, after, staging, total, _receipts, before_meta, after_meta = second
                changes = compare(before, after, self.limits.max_diff_lines)
                # Diff computation may be slow; validate again immediately before publication.
                if self._observe() != second:
                    continue
            except CaptureError as error:
                if error.code == 'source_changed':
                    continue
                raise
            conflicts = tuple(sorted({p for p, _, _, stage in staging if stage != '0'}))
            identity = [1, vars(self.limits), self.repo_id, str(self.git_root), head, staging,
                        [[v.path, v.mode, v.digest, v.exclusion, v.object_id] for v in before],
                        [[v.path, v.mode, v.digest, v.exclusion, v.object_id] for v in after], POLICY,
                        [[v.path, v.digest, v.exclusion] for v in before_meta],
                        [[v.path, v.digest, v.exclusion] for v in after_meta]]
            notices = ('Only saved filesystem bytes are captured; unsaved editor buffers are excluded.',
                       'Ignored and unsupported entries are unanalyzed; excluded entries may be unchanged.',
                       'Rename candidates use exact content; edited renames remain added/deleted.',
                       'Submodules are not traversed; their saved staging references are recorded.',
                       'Consistency uses repeated observations, not an atomic filesystem snapshot.')
            retained_bytes = sum(len(v.data or b'') for v in (*before, *after, *before_meta, *after_meta))
            snapshot = ChangeSnapshot(1, uuid.uuid4().hex, self.repo_id, str(self.root),
                                      str(self.git_root), head, 'head' if head else 'all_files_added',
                                      datetime.now(timezone.utc).isoformat(),
                                      _hash(json.dumps(identity, sort_keys=True).encode()),
                                      before, after, staging, changes, conflicts, notices,
                                      not conflicts, retained_bytes, metadata_policy=POLICY,
                                      before_metadata=before_meta, after_metadata=after_meta)
            self._captures[snapshot.id] = snapshot
            return snapshot
        raise CaptureError('source_changed', 'Saved source, staging or HEAD kept changing; capture not published')
