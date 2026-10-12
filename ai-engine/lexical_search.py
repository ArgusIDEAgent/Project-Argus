"""Exact, repository-scoped source search used before semantic retrieval."""

import hashlib
import os
import re

from config import MAX_FILE_BYTES
from indexer import _discover_files, _redact_secrets, _rel_posix
from knowledge_base.identity import file_id, entity_id
from knowledge_base.metadata import SCHEMA_VERSION


def search_exact_code(root: str, repo_id: str, query: str, limit: int) -> list[dict]:
    """Find literal code text in eligible saved files, with verified source hashes."""
    needle = " ".join(query.strip().casefold().split())
    if not needle:
        return []

    # A question such as "what is in CLAUDE.md?" names a file but does not
    # contain a line that can match literally. Treat an explicit filename as a
    # file lookup and return a verified file citation; semantic/Q&A layers can
    # summarize the contents later.
    requested_names = {
        token.casefold() for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.[A-Za-z0-9]+", query)
    }

    results: list[dict] = []
    for absolute_path in sorted(_discover_files(root)):
        try:
            with open(absolute_path, "rb") as source:
                raw = source.read(MAX_FILE_BYTES + 1)
        except OSError:
            continue
        if len(raw) > MAX_FILE_BYTES or b"\x00" in raw[:8192]:
            continue

        source_hash = hashlib.sha256(raw).hexdigest()
        relative_path = _rel_posix(absolute_path, root)
        code = _redact_secrets(raw.decode("utf-8", errors="replace"))
        basename = os.path.basename(relative_path).casefold()
        if basename in requested_names:
            results.append({
                "entityId": file_id(repo_id,relative_path),
                "fileId": file_id(repo_id,relative_path), "snapshotId": None,
                "sourceOrigin": "current_disk", "schemaVersion": SCHEMA_VERSION,
                "path": relative_path,
                "name": os.path.basename(relative_path),
                "startLine": 1,
                "endLine": max(1, len(code.splitlines())),
                "reason": f"Requested file: {os.path.basename(relative_path)}",
                "confidence": 1.0,
                "revision": "latest",
                "sourceHash": source_hash,
                "callers": [], "tests": [],
            })
            if len(results) >= limit:
                return results
        for line_number, line in enumerate(code.splitlines(), start=1):
            if needle not in " ".join(line.casefold().split()):
                continue
            results.append({
                "entityId": entity_id('source_match',file_id(repo_id,relative_path),source_hash,str(line_number)),
                "fileId": file_id(repo_id,relative_path), "snapshotId": None,
                "sourceOrigin": "current_disk", "schemaVersion": SCHEMA_VERSION,
                "path": relative_path,
                "name": os.path.basename(relative_path),
                "startLine": line_number,
                "endLine": line_number,
                "reason": f"Exact source match on line {line_number}",
                "confidence": 1.0,
                "revision": "latest",
                "sourceHash": source_hash,
                "callers": [], "tests": [],
            })
            if len(results) >= limit:
                return results
    return results
