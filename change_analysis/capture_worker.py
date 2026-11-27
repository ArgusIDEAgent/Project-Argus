"""Invoked in the user's Terminal by the Analyze Change controller."""
import json
import os
from pathlib import Path
import sys
import shutil

from .atomic_session import atomic_session
from .transport import encode


def main():
    request = Path(sys.argv[1])
    if request.is_symlink() or request.parent.is_symlink() or request.stat().st_uid != os.getuid():
        raise ValueError('Invalid capture request ownership')
    with (request.parent / 'running').open('x') as marker:
        os.chmod(request.parent / 'running', 0o600)
        marker.write(str(os.getpid()))
    info = json.loads(request.read_text())
    directory = request.parent
    print('CodeMind: capture saved JS/TS changes using a read-only APFS snapshot.')
    print('Enter your administrator password only at the Apple mount/unmount prompts below.')
    output = {'nonce': info['nonce']}
    try:
        if (directory / 'cancel').exists():
            raise RuntimeError('Capture cancelled')
        with atomic_session(info['root']) as store:
            snapshot = store.capture()
        # Publish only after unmount cleanup; no backend reader owns the mount.
        if (directory / 'cancel').exists():
            raise RuntimeError('Capture cancelled')
        output['capture'] = encode(snapshot)
    except Exception as error:
        output['error'] = getattr(error, 'code', 'capture_failed')
        print(str(error))
    if (directory / 'cancel').exists():
        shutil.rmtree(directory)
        print('Capture cancelled; mount cleanup completed.')
        return
    temporary = directory / 'output.tmp'
    with temporary.open('x') as handle:
        os.chmod(temporary, 0o600)
        json.dump(output, handle)
    temporary.replace(directory / 'ready.json')
    print('Capture finished. Return to the CodeMind Analyze Change panel.')


if __name__ == '__main__':
    main()
