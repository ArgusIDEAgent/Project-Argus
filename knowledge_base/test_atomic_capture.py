"""Adapter-negative tests; real APFS qualification is separately opt-in."""
import errno
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from change_analysis import CaptureError
from change_analysis.atomic import APFSSnapshotCapture


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True).stdout


@pytest.fixture
def adapter(tmp_path):
    volume = tmp_path / 'volume'
    root = volume / 'repo'
    root.mkdir(parents=True)
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'a.ts').write_text('export const x = 1;\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'baseline')
    (root / 'a.ts').write_text('export const x = 2;\n')
    mount = tmp_path / 'snapshot'
    shutil.copytree(volume, mount)
    state = {'type': 'apfs', 'mountpoint': str(mount), 'source': 'test-snapshot@/dev/test',
             'flags': 5, 'fsid': (1, 2)}

    def filesystem(path):
        path = Path(path)
        if path == mount or mount in path.parents:
            return dict(state)
        return {'type': 'apfs', 'mountpoint': str(volume), 'source': '/dev/test', 'flags': 0, 'fsid': (3, 4)}

    metadata = {'Snapshots': [{'SnapshotName': 'test-snapshot', 'SnapshotUUID': 'TEST-UUID', 'SnapshotXID': 42}]}
    with patch('change_analysis.atomic._filesystem', side_effect=filesystem), patch('change_analysis.atomic._diskutil', return_value=metadata):
        yield root, mount, state, metadata


def test_snapshot_uses_frozen_git_and_source_with_live_ownership(adapter):
    root, mount, _, _ = adapter
    (root / 'a.ts').write_text('later\n')
    git(root, 'commit', '-qam', 'later commit')
    with APFSSnapshotCapture(root, mount, 'TEST-UUID') as store:
        result = store.capture()
        assert result.consistency == 'atomic_apfs'
        assert result.atomic_snapshot_id == 'TEST-UUID'
        assert result.root == str(root)
        assert result.after[0].data == b'export const x = 2;\n'
        assert result.before[0].data == b'export const x = 1;\n'
        assert result.head != git(root, 'rev-parse', 'HEAD').decode().strip()
        assert store.get(result.id) == result
    assert mount.exists()  # externally owned mount is never removed by the adapter


@pytest.mark.parametrize('flags', [0, 1, 4])
def test_requires_readonly_and_noexec(adapter, flags):
    root, mount, state, _ = adapter
    state['flags'] = flags
    with pytest.raises(CaptureError, match='read-only/noexec'):
        APFSSnapshotCapture(root, mount, 'TEST-UUID')


def test_wrong_uuid_rejected(adapter):
    root, mount, _, _ = adapter
    with pytest.raises(CaptureError, match='UUID'):
        APFSSnapshotCapture(root, mount, 'WRONG-UUID')


@pytest.mark.parametrize('layout', ['alternates', 'symlink', 'include', 'worktree_include', 'nonregular'])
def test_git_cannot_escape_snapshot(adapter, layout):
    root, mount, _, _ = adapter
    metadata = mount / 'repo/.git'
    if layout == 'alternates':
        (metadata / 'objects/info/alternates').write_text(str(root / '.git/objects'))
    elif layout == 'nonregular':
        (metadata / 'config').unlink()
        os.mkfifo(metadata / 'config')
    elif layout == 'symlink':
        (metadata / 'objects/escape').symlink_to(root / '.git/objects')
    else:
        with (metadata / ('config.worktree' if layout == 'worktree_include' else 'config')).open('a') as handle:
            handle.write('\n[include]\npath = /outside/config\n')
    with pytest.raises(CaptureError) as error:
        APFSSnapshotCapture(root, mount, 'TEST-UUID')
    assert error.value.code == 'unsupported_git_layout'


def test_missing_snapshot_after_read_does_not_publish(adapter):
    root, mount, _, metadata = adapter
    with APFSSnapshotCapture(root, mount, 'TEST-UUID') as store:
        original = store.store.capture

        def removing():
            result = original()
            metadata['Snapshots'] = []
            return result

        with patch.object(store.store, 'capture', side_effect=removing), pytest.raises(CaptureError):
            store.capture()
        assert not store.store._captures


def test_writable_live_directory_is_not_atomic():
    if os.uname().sysname != 'Darwin':
        pytest.skip('macOS kernel mount check')
    with pytest.raises(CaptureError):
        APFSSnapshotCapture(Path.cwd(), Path.cwd(), 'WRONG')


def test_real_apfs_snapshot_survives_live_edits():
    """Run only with the disposable fixture prepared before a real APFS snapshot."""
    root_value = os.environ.get('CODEMIND_ATOMIC_FIXTURE')
    mount_value = os.environ.get('CODEMIND_ATOMIC_MOUNT')
    uuid = os.environ.get('CODEMIND_ATOMIC_UUID')
    if not all((root_value, mount_value, uuid)):
        pytest.skip('requires mounted, disposable APFS qualification fixture')
    root = Path(root_value)
    assert root.name.startswith('codemind-atomic-fixture-') and root.parent == Path('/private/tmp')
    original_head = git(root, 'rev-parse', 'HEAD').decode().strip()
    (root / 'a.ts').write_text('export const x = 99;\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'live changes after snapshot')
    index = (root / '.git/index').read_bytes()
    with APFSSnapshotCapture(root, mount_value, uuid) as store:
        snapshot = store.capture()
        assert snapshot.head == original_head
        assert {v.path: v.data for v in snapshot.before}['a.ts'] == b'export const x = 1;\n'
        assert {v.path: v.data for v in snapshot.after}['a.ts'] == b'export const x = 3;\n'
        # A write is denied by the filesystem, not merely by Unix file permissions.
        saved_path = store.snapshot_root / 'a.ts'
        with pytest.raises(OSError) as error:
            saved_path.write_text('must fail')
        assert error.value.errno == errno.EROFS
        assert (root / '.git/index').read_bytes() == index
        assert (root / 'a.ts').read_text() == 'export const x = 99;\n'
