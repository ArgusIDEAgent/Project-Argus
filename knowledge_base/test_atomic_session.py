"""Cleanup qualification without elevating test processes or touching real mounts."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from change_analysis import CaptureError
from change_analysis.atomic_session import atomic_session


@pytest.mark.parametrize('failure', [None, 'body', 'mount', 'unmount', 'replacement'])
def test_owned_mount_lifecycle(tmp_path, failure):
    root = tmp_path / 'repo'
    root.mkdir()
    mount = tmp_path / 'private-mount'
    mount.mkdir()
    state = {'mounted': False, 'source': 'owned@/dev/test'}
    calls = []
    session = Mock()

    def filesystem(path):
        return {'type': 'apfs', 'mountpoint': str(mount) if state['mounted'] and Path(path) == mount else str(tmp_path),
                'source': state['source'] if state['mounted'] and Path(path) == mount else '/dev/test'}

    def run(args, timeout=None):
        calls.append(args)
        if '/sbin/mount_apfs' in args:
            if failure == 'mount':
                raise CaptureError('snapshot_command_failed', 'mount failed')
            state['mounted'] = True
        if '/sbin/umount' in args:
            if failure == 'unmount':
                raise CaptureError('snapshot_command_failed', 'unmount failed')
            state['mounted'] = False

    catalog = [{'Snapshots': []}, {'Snapshots': [{'SnapshotName': 'owned', 'SnapshotUUID': 'uuid'}]}]
    with patch('change_analysis.atomic_session._filesystem', side_effect=filesystem), \
         patch('change_analysis.atomic_session._diskutil', side_effect=catalog), \
         patch('change_analysis.atomic_session._run', side_effect=run), \
         patch('change_analysis.atomic_session.tempfile.mkdtemp', return_value=str(mount)), \
         patch('change_analysis.atomic_session.APFSSnapshotCapture', return_value=session):
        def use():
            with atomic_session(root) as capture:
                assert capture is session
                if failure == 'body':
                    raise ValueError('analysis failed')
                if failure == 'replacement':
                    state['source'] = 'unrelated@/dev/test'
        if failure == 'body':
            with pytest.raises(ValueError, match='analysis failed'):
                use()
        elif failure:
            with pytest.raises(CaptureError) as error:
                use()
            assert error.value.code == ('snapshot_command_failed' if failure == 'mount' else 'snapshot_cleanup_failed')
        else:
            use()
    if failure in ('unmount', 'replacement'):
        assert mount.is_dir()  # report failure and retain mountpoint; never rmtree
    else:
        assert not mount.exists()
    assert not any('delete' in arg for call in calls for arg in call)
    if failure != 'mount':
        session.close.assert_called_once()
    if failure == 'replacement':
        assert not any('/sbin/umount' in call for call in calls)
