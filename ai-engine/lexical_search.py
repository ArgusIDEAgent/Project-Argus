"""Exact, repository-scoped source search used before semantic retrieval."""

import hashlib
import os

from config import MAX_FILE_BYTES
from indexer import _discover_files, _redact_secrets, _rel_posix


def search_exact_code(root: str, repo_id: str, query: str, limit: int) -> list[dict]:
    """Find literal code text in eligible saved files, with verified source hashes."""
    needle = " ".join(query.strip().casefold().split())
    if not needle:
        return []

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
        for line_number, line in enumerate(code.splitlines(), start=1):
            if needle not in " ".join(line.casefold().split()):
                continue
            results.append({
                "entityId": hashlib.sha256(f"{repo_id}:{relative_path}:{line_number}".encode()).hexdigest()[:40],
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
