"""Interactive Apple-tool adapter. Run as the developer, never as root.

    python -m change_analysis.atomic_session /path/to/repository

sudo asks for the administrator password on the user's Terminal, solely for
mount/unmount. Time Machine manages created snapshots; this module never deletes
backups. Its private mount directory is removed only after confirmed unmount.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .atomic import APFSSnapshotCapture, _diskutil, _filesystem
from .capture import CaptureError


def _run(args, timeout=None):
    try:
        subprocess.run(args, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureError('snapshot_command_failed', 'Apple snapshot/mount command failed') from error


@contextmanager
def atomic_session(root, limits=None):
    if os.geteuid() == 0:
        raise CaptureError('root_engine_refused', 'Run CodeMind as your normal user; only Apple mount tools need sudo')
    root = Path(root).resolve(strict=True)
    volume = _filesystem(root)
    if volume['type'] != 'apfs' or '@' in volume['source']:
        raise CaptureError('unsupported_volume', 'Expected a live APFS source volume')
    device = volume['source']
    previous = {s['SnapshotUUID'] for s in _diskutil('apfs', 'listSnapshots', '-plist', device).get('Snapshots', [])}
    _run(['/usr/bin/tmutil', 'localsnapshot'], timeout=60)
    new = [s for s in _diskutil('apfs', 'listSnapshots', '-plist', device).get('Snapshots', [])
           if s['SnapshotUUID'] not in previous]
    if len(new) != 1:
        raise CaptureError('snapshot_identity_ambiguous', 'Expected exactly one new snapshot on the source volume')
    snapshot = new[0]
    mount = Path(tempfile.mkdtemp(prefix='codemind-apfs-', dir='/private/tmp'))
    expected_source = snapshot['SnapshotName'] + '@' + device
    session = None
    try:
        _run(['/usr/bin/sudo', '/sbin/mount_apfs', '-o', 'rdonly,nobrowse,noexec',
              '-s', snapshot['SnapshotName'], volume['mountpoint'], str(mount)])
        session = APFSSnapshotCapture(root, mount, snapshot['SnapshotUUID'], limits)
        yield session
    finally:
        if session is not None:
            session.close()
        state = _filesystem(mount)
        if state['mountpoint'] == str(mount):
            if state['source'] != expected_source:
                raise CaptureError('snapshot_cleanup_failed', f'Unexpected mount at {mount}; left untouched')
            try:
                _run(['/usr/bin/sudo', '/sbin/umount', str(mount)])
            except CaptureError as error:
                raise CaptureError('snapshot_cleanup_failed', f'Unmount failed; retained mount at {mount}') from error
            if _filesystem(mount)['mountpoint'] == str(mount):
                raise CaptureError('snapshot_cleanup_failed', f'Mount still present at {mount}')
        mount.rmdir()  # no recursive deletion; removes only our empty mount directory


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    args = parser.parse_args()
    with atomic_session(args.root) as store:
        snapshot = store.capture()
        print(json.dumps({'capture_id': snapshot.id, 'consistency': snapshot.consistency,
                          'snapshot_uuid': snapshot.atomic_snapshot_id, 'head': snapshot.head,
                          'bytes': snapshot.size_bytes, 'conflicts': snapshot.conflicts}))


if __name__ == '__main__':
    main()
