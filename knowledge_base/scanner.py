"""Phase 1: scan one local repository and persist an explainable file inventory.

Run from the project root: python -m knowledge_base.scanner --repo /path/to/repo
Only metadata and hashes are stored. Source contents never enter the database.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from datetime import datetime, timezone


SCHEMA_VERSION = 2
DEFAULT_MAX_BYTES = 1_000_000
SOURCE_EXTENSIONS = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".mts": "typescript", ".cts": "typescript",
}
CONFIG_NAMES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "pnpm-workspace.yaml", "bun.lock", "bun.lockb",
    "jsconfig.json", "webpack.config.js", "rollup.config.js", "vite.config.js",
    "vite.config.ts", "next.config.js", "next.config.mjs", "next.config.ts",
    "eslint.config.js", "eslint.config.mjs", "eslint.config.ts",
    "babel.config.js", "babel.config.json", ".gitignore",
}
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "dist", "build", "out", "coverage", ".next", ".cache", "vendor",
    "__pycache__", ".pytest_cache", ".vscode-test", ".codemind",
}
SENSITIVE_NAMES = {
    ".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519",
    "credentials.json", "secrets.json", "service-account.json",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
SECRET_MARKERS = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def _git(root: Path, *args: str, input_data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        input=input_data, capture_output=True, timeout=30, check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
    )


def _git_context(root: Path) -> tuple[bool, str | None]:
    try:
        result = _git(root, "rev-parse", "--show-toplevel")
        if result.returncode != 0 or Path(os.fsdecode(result.stdout.rstrip(b"\r\n"))).resolve() != root:
            return False, None
        head = _git(root, "rev-parse", "HEAD")
        return True, os.fsdecode(head.stdout.strip()) if head.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return False, None


def _has_gitignore(root: Path) -> bool:
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames
                       if name not in EXCLUDED_DIRS and not (Path(current) / name).is_symlink()]
        if ".gitignore" in filenames:
            return True
    return False


def _ignored_by_git(root: Path, paths: list[str]) -> dict[str, str]:
    if not paths:
        return {}
    raw = b"\0".join(os.fsencode(path) for path in paths) + b"\0"
    result = _git(root, "check-ignore", "--no-index", "-v", "-z", "--stdin", input_data=raw)
    if result.returncode not in (0, 1):
        raise RuntimeError("Git ignore check failed: " + os.fsdecode(result.stderr).strip())
    fields = result.stdout.split(b"\0")
    ignored = {}
    for i in range(0, len(fields) - 3, 4):
        source, line, pattern, path = (os.fsdecode(part) for part in fields[i:i + 4])
        if not pattern.startswith("!"):
            ignored[path] = f"gitignore:{source}:{line}:{pattern}"
    return ignored


def _kind(path: str) -> tuple[str, str | None]:
    name = Path(path).name
    if name in CONFIG_NAMES or name.startswith("tsconfig.") and name.endswith(".json"):
        suffix = Path(name).suffix.lower()
        return "config", SOURCE_EXTENSIONS.get(suffix) or {".json": "json", ".yaml": "yaml"}.get(suffix)
    language = SOURCE_EXTENSIONS.get(Path(name).suffix.lower())
    return ("source", language) if language else ("unsupported", None)


def _sensitive(name: str) -> bool:
    lowered = name.lower()
    return (lowered in SENSITIVE_NAMES or lowered.startswith(".env.")
            or lowered.startswith("service-account") and lowered.endswith(".json")
            or lowered.endswith(SENSITIVE_SUFFIXES))


def _entry(path: str, kind: str, status: str, reason: str, *, language: str | None = None,
           size: int | None = None, digest: str | None = None) -> tuple:
    parse_status = "pending" if status == "included" and kind == "source" else "not_applicable" if status == "included" else "not_indexed"
    return path, kind, status, reason, Path(path).suffix.lower(), language, size, digest, parse_status


def _inventory(root: Path, git_repo: bool, exclusions: tuple[str, ...],
               max_bytes: int) -> list[tuple]:
    entries: list[tuple] = []
    def walk_error(error: OSError) -> None:
        rel = Path(error.filename).relative_to(root).as_posix() if error.filename else "."
        entries.append(_entry(rel, "directory", "error", type(error).__name__))

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()
        rel_dir = current_path.relative_to(root)
        names = dirnames + filenames
        paths = [(rel_dir / name).as_posix() for name in names]
        git_ignored = _ignored_by_git(root, paths) if git_repo else {}

        kept_dirs = []
        for name in dirnames:
            path = (rel_dir / name).as_posix()
            if name in EXCLUDED_DIRS or name.endswith(".egg-info"):
                reason = "excluded_directory"
            elif (current_path / name).is_symlink():
                reason = "symlink_directory"
            elif path in git_ignored:
                reason = git_ignored[path]
            elif any(fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern)
                     for pattern in exclusions):
                reason = "custom_exclusion"
            else:
                kept_dirs.append(name)
                continue
            entries.append(_entry(path, "directory", "ignored", reason))
        dirnames[:] = kept_dirs

        for name in filenames:
            path = (rel_dir / name).as_posix()
            full_path = current_path / name
            kind, language = _kind(path)
            if full_path.is_symlink():
                entries.append(_entry(path, kind, "skipped", "symlink", language=language))
                continue
            if _sensitive(name):
                entries.append(_entry(path, kind, "ignored", "sensitive_filename", language=language))
                continue
            if path in git_ignored:
                entries.append(_entry(path, kind, "ignored", git_ignored[path], language=language))
                continue
            if any(fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern)
                   for pattern in exclusions):
                entries.append(_entry(path, kind, "ignored", "custom_exclusion", language=language))
                continue
            if kind == "unsupported":
                entries.append(_entry(path, kind, "unsupported", "unsupported_file_type"))
                continue
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(full_path, flags), "rb") as source:
                    file_stat = os.fstat(source.fileno())
                    if not stat.S_ISREG(file_stat.st_mode):
                        entries.append(_entry(path, kind, "skipped", "not_regular_file", language=language))
                        continue
                    size = file_stat.st_size
                    data = source.read(max_bytes + 1) if size <= max_bytes else b""
                    after_stat = os.fstat(source.fileno())
                if (file_stat.st_size, file_stat.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
                    entries.append(_entry(path, kind, "error", "changed_during_scan", language=language))
                    continue
                if size > max_bytes:
                    entries.append(_entry(path, kind, "skipped", "too_large", language=language, size=size))
                    continue
                if len(data) > max_bytes:
                    entries.append(_entry(path, kind, "skipped", "too_large", language=language, size=len(data)))
                elif b"\0" in data[:8192]:
                    entries.append(_entry(path, kind, "skipped", "binary", language=language, size=len(data)))
                elif any(pattern.search(data) for pattern in SECRET_MARKERS):
                    entries.append(_entry(path, kind, "skipped", "possible_embedded_secret", language=language, size=len(data)))
                else:
                    entries.append(_entry(path, kind, "included", "eligible", language=language,
                                          size=len(data), digest=hashlib.sha256(data).hexdigest()))
            except OSError as error:
                entries.append(_entry(path, kind, "error", type(error).__name__, language=language))
    return entries


def _prepare_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS repositories (
            id TEXT PRIMARY KEY, root_path TEXT NOT NULL, git_revision TEXT,
            last_scanned_at TEXT NOT NULL, schema_version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
            repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
            relative_path TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, extension TEXT, language TEXT, size_bytes INTEGER,
            content_sha256 TEXT, parse_status TEXT NOT NULL,
            PRIMARY KEY (repository_id, relative_path)
        );
        CREATE TABLE IF NOT EXISTS inventory_runs (
            id INTEGER PRIMARY KEY, repository_id TEXT NOT NULL REFERENCES repositories(id),
            scanned_at TEXT NOT NULL, git_revision TEXT, statistics_json TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
    if "parse_status" not in columns:
        connection.execute("ALTER TABLE files ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'not_indexed'")
    return connection


def scan_repository(repo_root: str | Path, *, data_dir: str | Path | None = None,
                    exclusions: tuple[str, ...] = (), max_file_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """Scan saved files, replace this repo's inventory atomically, and return coverage."""
    root = Path(repo_root).expanduser().resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Repository root must be an existing project directory")
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")
    base = Path(data_dir or os.environ.get("CODEMIND_DATA_DIR", "~/.codemind/data")).expanduser().resolve()
    if base == root or root in base.parents:
        raise ValueError("Inventory data directory must be outside the repository")
    repo_id = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()[:24]
    db_path = base / repo_id / "metadata.db"
    git_repo, revision = _git_context(root)
    if not git_repo and _has_gitignore(root):
        raise ValueError("A Git repository is required to evaluate .gitignore rules")
    entries = _inventory(root, git_repo, exclusions, max_file_bytes)
    counts = {status: sum(row[2] == status and row[1] != "directory" for row in entries)
              for status in ("included", "unsupported", "ignored", "skipped", "error")}
    counts["ignored_directories"] = sum(row[1] == "directory" and row[2] == "ignored" for row in entries)
    counts["error_directories"] = sum(row[1] == "directory" and row[2] == "error" for row in entries)
    counts["source_files"] = sum(row[1] == "source" and row[2] == "included" for row in entries)
    counts["config_files"] = sum(row[1] == "config" and row[2] == "included" for row in entries)
    scanned_at = datetime.now(timezone.utc).isoformat()
    with closing(_prepare_database(db_path)) as db:
        with db:
            db.execute("""INSERT INTO repositories VALUES (?, ?, ?, ?, ?)
                          ON CONFLICT(id) DO UPDATE SET root_path=excluded.root_path,
                          git_revision=excluded.git_revision,
                          last_scanned_at=excluded.last_scanned_at,
                          schema_version=excluded.schema_version""",
                       (repo_id, str(root), revision, scanned_at, SCHEMA_VERSION))
            db.execute("DELETE FROM files WHERE repository_id=?", (repo_id,))
            db.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [(repo_id, *entry) for entry in entries])
            db.execute("INSERT INTO inventory_runs (repository_id, scanned_at, git_revision, statistics_json) VALUES (?, ?, ?, ?)",
                       (repo_id, scanned_at, revision, json.dumps(counts, sort_keys=True)))
    return {"repository_id": repo_id, "root_path": str(root), "git_revision": revision,
            "database": str(db_path), "scanned_at": scanned_at, "statistics": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local CodeMind file inventory")
    parser.add_argument("--repo", required=True, help="Local repository root")
    parser.add_argument("--data-dir", help="Private data directory outside the repository")
    parser.add_argument("--exclude", action="append", default=[], help="Extra glob to exclude (repeatable)")
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--explain", help="Explain one repository-relative path after scanning")
    args = parser.parse_args()
    result = scan_repository(args.repo, data_dir=args.data_dir,
                             exclusions=tuple(args.exclude), max_file_bytes=args.max_file_bytes)
    if args.explain:
        with closing(sqlite3.connect(result["database"])) as db:
            row = db.execute("SELECT kind, status, reason, language, size_bytes, content_sha256 "
                             "FROM files WHERE repository_id=? AND relative_path=?",
                             (result["repository_id"], Path(args.explain).as_posix())).fetchone()
        result["explanation"] = dict(zip(("kind", "status", "reason", "language", "size_bytes", "content_sha256"), row)) if row else None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
