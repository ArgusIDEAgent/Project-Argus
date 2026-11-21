"""Repository-owned persistence. Machine runtime files are deliberately separate."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import os

from knowledge_base.identity import repository_id

_scope = ContextVar('codemind_repository_storage', default=None)


def checked_path(path):
    """Reject links at every existing component, including dangling links."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for component in (*reversed(path.parents), path):
        if component.is_symlink() and str(component) not in ('/var', '/tmp', '/etc'):
            raise ValueError('CodeMind storage must not contain symbolic links')
    return path.resolve()


def storage_base(root, data_dir=None):
    root = Path(root).expanduser().resolve(strict=True)
    base = checked_path(data_dir if data_dir is not None else root / '.codemind')
    if base == root or (root in base.parents and root / '.codemind' not in (base, *base.parents)):
        raise ValueError('Repository storage must be inside its reserved .codemind directory')
    return base


class RepositoryStorage:
    def __init__(self, root, data_dir=None):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.repo_id = repository_id(self.root)
        self.base = storage_base(self.root, data_dir)
        self.local = self.base == self.root / '.codemind'
        # Explicit external directories remain supported by isolated offline tools/tests.
        self.path = self.base if self.local else self.base / self.repo_id

    def prepare(self):
        checked_path(self.path).mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.local:
            os.chmod(self.path, 0o700)
            ignore = checked_path(self.path / '.gitignore')
            # Self-contained exclusion: no modification of the project's .gitignore.
            if not ignore.exists():
                try:
                    with open(ignore, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
                        handle.write('*\n')
                except FileExistsError:
                    pass  # Another editor session prepared this same directory.
        return self.path

    @contextmanager
    def scope(self):
        token = _scope.set(self)
        try:
            yield self
        finally:
            _scope.reset(token)


def current_storage():
    return _scope.get()
