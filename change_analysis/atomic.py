"""Consume an OS-verified read-only APFS snapshot; never elevate the engine.

Creation/mount authorization stays with Apple's tools. This adapter does not
create, delete, or unmount Time Machine snapshots. All Git/source reads use the
same mounted snapshot; external Git metadata is deliberately unsupported.
"""
from dataclasses import replace
import ctypes
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import stat
import sys

from knowledge_base.identity import repository_id
from .capture import CaptureError, CaptureStore, _directory_identity


class _StatFS(ctypes.Structure):
    _fields_ = [('bsize', ctypes.c_uint32), ('iosize', ctypes.c_int32),
                *[(name, ctypes.c_uint64) for name in ('blocks', 'bfree', 'bavail', 'files', 'ffree')],
                ('fsid', ctypes.c_int32 * 2), ('owner', ctypes.c_uint32),
                ('type', ctypes.c_uint32), ('flags', ctypes.c_uint32), ('subtype', ctypes.c_uint32),
                ('fstype', ctypes.c_char * 16), ('mountpoint', ctypes.c_char * 1024),
                ('source', ctypes.c_char * 1024), ('reserved', ctypes.c_uint32 * 8)]


def _filesystem(path):
    if sys.platform != 'darwin':
        raise CaptureError('atomic_capture_unavailable', 'APFS capture requires macOS')
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    fn = lib.statfs64
    fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(_StatFS)]
    fn.restype = ctypes.c_int
    result = _StatFS()
    if fn(os.fsencode(path), ctypes.byref(result)):
        raise CaptureError('snapshot_unavailable', 'Cannot inspect snapshot filesystem')
    return {'type': os.fsdecode(result.fstype), 'mountpoint': os.fsdecode(result.mountpoint),
            'source': os.fsdecode(result.source), 'flags': result.flags,
            'fsid': tuple(result.fsid)}


def _diskutil(*args):
    try:
        proc = subprocess.run(['/usr/sbin/diskutil', *args], capture_output=True, timeout=15, check=True)
        result = plistlib.loads(proc.stdout)
        if result.get('Error'):
            raise ValueError('Disk inspection failed')
        return result
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise CaptureError('snapshot_unavailable', 'APFS snapshot identity could not be verified') from error


def _contained_git(root):
    """Reject metadata that can cause Git to read the live filesystem instead."""
    git = root / '.git'
    if git.is_symlink() or not git.is_dir():
        raise CaptureError('unsupported_git_layout', 'Atomic capture initially requires an in-repository .git directory')
    count = 0
    for current, dirs, files in os.walk(git, followlinks=False):
        for name in dirs + files:
            count += 1
            if count > 100000:
                raise CaptureError('git_metadata_limit', 'Git metadata exceeds validation budget')
            mode = (Path(current) / name).lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise CaptureError('unsupported_git_layout', 'Symlinked or non-regular Git metadata is unsupported')
    for name in ('commondir', 'objects/info/alternates', 'objects/info/http-alternates'):
        if (git / name).exists():
            raise CaptureError('unsupported_git_layout', 'External Git metadata is unsupported for atomic capture')
    for config in (git / 'config', git / 'config.worktree'):
        if config.exists():
            if config.stat().st_size > 1000000:
                raise CaptureError('git_metadata_limit', 'Git configuration is too large')
            value = config.read_text(encoding='utf-8-sig', errors='replace')
            if re.search(r'^\s*\[\s*include', value, re.I | re.M) or re.search(r'^\s*worktree\s*=', value, re.I | re.M):
                raise CaptureError('unsupported_git_layout', 'Git config includes/worktree overrides are unsupported')


class APFSSnapshotCapture:
    """Session over one explicitly selected, pre-mounted, UUID-verified snapshot.

    The mount is externally owned. close() releases captures only; it never
    deletes a backup or unmounts a resource another reader might be using.
    """
    def __init__(self, live_root, mountpoint, snapshot_uuid, limits=None):
        self.live_root = Path(live_root).resolve(strict=True)
        self.mountpoint = Path(mountpoint).absolute()
        _directory_identity(self.mountpoint)  # reject all symlinked mount components
        self.mount = _filesystem(self.mountpoint)
        # MNT_RDONLY | MNT_NOEXEC. Snapshot mounts mirror some other live-volume flags.
        if (self.mount['type'] != 'apfs' or self.mount['mountpoint'] != str(self.mountpoint)
                or self.mount['flags'] & 0x5 != 0x5 or '@' not in self.mount['source']):
            raise CaptureError('not_atomic_snapshot', 'Expected an APFS snapshot mount with read-only/noexec')
        self.name, self.device = self.mount['source'].rsplit('@', 1)
        live_fs = _filesystem(self.live_root)
        if live_fs['source'] != self.device:
            raise CaptureError('snapshot_volume_mismatch', 'Snapshot is not from the selected repository volume')
        self.volume = Path(live_fs['mountpoint'])
        try:
            relative = self.live_root.relative_to(self.volume)
        except ValueError:
            # macOS Data-volume firmlink: /Users/x == /System/Volumes/Data/Users/x.
            candidate = self.volume / self.live_root.relative_to('/')
            if _directory_identity(candidate) != _directory_identity(self.live_root):
                raise CaptureError('snapshot_volume_mismatch', 'Cannot map selected folder onto snapshot volume')
            relative = candidate.relative_to(self.volume)
        self.snapshot_root = self.mountpoint / relative
        _directory_identity(self.snapshot_root)
        self.uuid = str(snapshot_uuid).upper()
        self._verify()
        # Resolve Git boundary without following .git pointer files to live metadata.
        root = self.snapshot_root
        while not (root / '.git').exists():
            if root == self.mountpoint:
                raise CaptureError('unsupported_git_layout', 'No contained Git repository in snapshot')
            root = root.parent
        _contained_git(root)
        self.snapshot_git_root = root
        depth = len(self.snapshot_root.relative_to(root).parts)
        self.live_git_root = self.live_root.parents[depth - 1] if depth else self.live_root
        self.store = CaptureStore(self.snapshot_root, limits)
        if self.store.git_root != root or self.store.git_dir != root / '.git':
            self.store.close()
            raise CaptureError('unsupported_git_layout', 'Git metadata escaped the snapshot')

    def _verify(self):
        current = _filesystem(self.mountpoint)
        if current != self.mount or _filesystem(self.snapshot_root) != current:
            raise CaptureError('snapshot_changed', 'Snapshot mount was replaced or source crosses a mount boundary')
        snapshots = _diskutil('apfs', 'listSnapshots', '-plist', self.device).get('Snapshots', [])
        matches = [s for s in snapshots if s.get('SnapshotName') == self.name
                   and s.get('SnapshotUUID', '').upper() == self.uuid]
        if len(matches) != 1:
            raise CaptureError('snapshot_changed', 'Snapshot name/UUID is no longer available')
        self.xid = matches[0]['SnapshotXID']

    def capture(self, *, require_atomic=True):
        with self.store._lock:
            self._verify()
            result = self.store.capture()
            try:
                self._verify()
                rid = repository_id(self.live_root)
                identity = [result.digest, rid, self.uuid, self.xid, str(self.live_root)]
                result = replace(result, repository_id=rid, root=str(self.live_root),
                                 git_root=str(self.live_git_root), consistency='atomic_apfs',
                                 digest=hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                                 atomic_snapshot_id=self.uuid,
                                 notices=tuple(n for n in result.notices if not n.startswith('Consistency uses')) +
                                 ('Source, HEAD and index were read from one verified read-only APFS snapshot.',
                                  'This is historical snapshot state, not a claim about the current workspace.'))
                self.store._captures[result.id] = result
                return result
            except BaseException:
                self.store.release(result.id)
                raise

    def get(self, capture_id):
        return self.store.get(capture_id)

    def release(self, capture_id):
        self.store.release(capture_id)

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
