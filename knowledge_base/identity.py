"""Shared identities. Keep existing graph, parser and vector IDs compatible."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import uuid


def relative_path(path: str) -> str:
    value = PurePosixPath(path)
    if not path or "\\" in path or value.is_absolute() or ".." in value.parts:
        raise ValueError("Expected a normalized repository-relative path")
    if value.as_posix() != path or path == ".":
        raise ValueError("Expected a normalized repository-relative path")
    return path


def repository_id(root: str | Path) -> str:
    return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()[:24]


def entity_id(kind: str, *parts: str) -> str:
    return f"{kind}:" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def file_id(repo_id: str, path: str) -> str:
    return entity_id("File", repo_id, relative_path(path))


def symbol_identity(repo_id, path, parent, kind, name, signature, ordinal):
    key = json.dumps([repo_id, relative_path(path), parent, kind, name, signature, ordinal],
                     ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest(), key


def chunk_id(repo_id: str, path: str, ordinal: int) -> str:
    if ordinal < 0:
        raise ValueError("Chunk ordinal must be nonnegative")
    return str(uuid.uuid5(uuid.UUID("a3f1b2c4-d5e6-7890-abcd-ef1234567890"),
                          f"{repo_id}::{relative_path(path)}::{ordinal}"))
