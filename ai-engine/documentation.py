"""Deterministic documentation for one published CodeMind knowledge snapshot.

Documents live outside the repository. The indexed source remains authoritative:
this module never indexes files, generates embeddings, or trusts model-written paths.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from filelock import FileLock, Timeout

from config import sanitize_text
from indexer import _redact_secrets
from knowledge_base.identity import file_id, relative_path
from knowledge_base.synchronization import SnapshotStore

VERSION = 1
MAX_FILES = 500
MAX_DOCUMENTS = 120
MAX_SOURCE_BYTES = 12_000_000
MAX_REFERENCES = 30


class DocumentationError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _safe(value):
    return sanitize_text(_redact_secrets(str(value or ""))).replace("\n", " ").strip()[:300]


def _id(repo_id, kind, key):
    return hashlib.sha1(json.dumps([repo_id, kind, key], sort_keys=True).encode()).hexdigest()


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


class DocumentationService:
    def __init__(self, root, repo_id, data_dir):
        self.snapshot = SnapshotStore(root, repo_id, data_dir)
        self.root = self.snapshot.root
        self.repo_id = repo_id
        self.directory = self.snapshot.path / "documentation"
        self.path = self.directory / "documents-v1.json"

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": VERSION, "repoId": self.repo_id, "root": str(self.root),
                    "documents": {}, "lastSuccessfulSync": None, "lastOutcome": None,
                    "lastWarning": None}
        except (OSError, ValueError) as exc:
            raise DocumentationError("failed", "Documentation storage is unreadable.") from exc
        if (not isinstance(data, dict) or data.get("version") != VERSION or
                data.get("repoId") != self.repo_id or data.get("root") != str(self.root) or
                not isinstance(data.get("documents"), dict)):
            raise DocumentationError("failed", "Documentation storage identity or version is invalid.")
        try:
            if len(data["documents"]) > 2000:
                raise ValueError("Too many documentation records")
            for identifier, document in data["documents"].items():
                if (not re.fullmatch(r"[a-f0-9]{40}", identifier) or document["sectionId"] != identifier or
                        not isinstance(document["contentMarkdown"], str) or
                        len(document["contentMarkdown"]) > 100_000):
                    raise ValueError("Invalid document")
                for item in document["sources"]:
                    path = relative_path(item["path"])
                    if (item["fileId"] != file_id(self.repo_id, path) or
                            not re.fullmatch(r"[a-f0-9]{64}", item["sourceHash"]) or
                            not isinstance(item["endLine"], int) or item["endLine"] < 1):
                        raise ValueError("Invalid source reference")
                for item in document.get("dependencies", document["sources"]):
                    relative_path(item["path"])
                    if not re.fullmatch(r"[a-f0-9]{64}", item["sourceHash"]):
                        raise ValueError("Invalid dependency")
        except (KeyError, TypeError, ValueError) as exc:
            raise DocumentationError("failed", "Documentation references are invalid.") from exc
        return data

    def _write(self, data):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.directory / (self.path.name + "." + os.urandom(8).hex() + ".tmp")
        try:
            with open(temporary, "x", opener=lambda path, flags: os.open(path, flags, 0o600), encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fresh(document, view):
        manifest = view["manifest"]
        if not manifest or not view["fresh"]:
            return False
        files = manifest["sources"]["files"]
        return all(files.get(item.get("path")) == item.get("sourceHash")
                   for item in document.get("dependencies", document.get("sources", [])))

    def _public(self, document, view):
        current = self._fresh(document, view)
        return {"repoId": self.repo_id, "sectionId": document["sectionId"], "title": document["title"],
                "kind": document["kind"], "contentMarkdown": document["contentMarkdown"],
                "sourceCommit": document.get("sourceCommit"), "sourceSnapshotId": document["sourceSnapshotId"],
                "generationReason": document["generationReason"], "freshness": "current" if current else "stale",
                "generated": True, "proposedMarkdown": None, "affectedBy": [] if current else [s["path"] for s in document["sources"]],
                "createdAt": document["createdAt"], "updatedAt": document["updatedAt"],
                "evidence": [{"entityId": s["fileId"], "type": "file", "path": s["path"],
                              "name": Path(s["path"]).name, "kind": "source", "relation": "GENERATED_FROM",
                              "startLine": 1, "endLine": s["endLine"], "sourceHash": s["sourceHash"]}
                             for s in document["sources"][:MAX_REFERENCES]]}

    def _listing(self, data, view):
        rows = [self._public(row, view) for row in data["documents"].values()]
        rows.sort(key=lambda row: (row["kind"], row["title"]))
        current = sum(row["freshness"] == "current" for row in rows)
        status = {"sectionCount": len(rows), "currentCount": current, "staleCount": len(rows) - current,
                  "affectedCount": 0, "generatedCount": len(rows)}
        return {"repoId": self.repo_id, "snapshotId": view["snapshotId"], "snapshotFresh": view["fresh"],
                "synchronization": {"state": view["state"], "error": view["error"]},
                "lastSuccessfulSync": data.get("lastSuccessfulSync"), "lastOutcome": data.get("lastOutcome"),
                "lastWarning": data.get("lastWarning"), "status": status,
                "sections": [{key: row[key] for key in ("sectionId", "title", "kind", "freshness", "updatedAt")}
                             for row in rows]}

    def list(self):
        with self.snapshot.pin_view() as view:
            return self._listing(self._load(), view)

    def overview(self):
        with self.snapshot.pin_view() as view:
            data = self._load()
            row = next((item for item in data["documents"].values() if item["kind"] == "system_overview"), None)
            return self._public(row, view) if row else None

    def section(self, section_id):
        with self.snapshot.pin_view() as view:
            row = self._load()["documents"].get(section_id)
            return self._public(row, view) if row else None

    def _plans(self, view):
        manifest = view["manifest"]
        database = self.snapshot.data_for(manifest) / self.repo_id / "metadata.db"
        if not database.is_file():
            raise DocumentationError("missing_snapshot", "Published metadata is unavailable. Refresh the repository.")
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            files = db.execute("""SELECT id,relative_path,kind,language,content_sha256,size_bytes FROM files
                WHERE repository_id=? AND status='included' ORDER BY relative_path LIMIT ?""",
                (self.repo_id, MAX_FILES + 1)).fetchall()
            truncated = len(files) > MAX_FILES
            files = files[:MAX_FILES]
            observed = manifest["sources"]["files"]
            sources = []
            total_bytes = 0
            for row in files:
                try:
                    path = relative_path(row["relative_path"])
                except ValueError as exc:
                    raise DocumentationError("stale_source", "Indexed source path is not canonical.") from exc
                if row["id"] != file_id(self.repo_id, path):
                    raise DocumentationError("stale_source", "Indexed file identity is invalid.")
                expected = row["content_sha256"]
                if not expected or observed.get(path) != expected:
                    raise DocumentationError("stale_source", "Indexed source hashes disagree with the published snapshot.")
                source = self.root / path
                try:
                    if source.resolve(strict=True) != source or not source.is_file():
                        raise DocumentationError("stale_source", "An indexed source path changed. Refresh the repository.")
                    raw = source.read_bytes()
                except OSError as exc:
                    raise DocumentationError("stale_source", "An indexed source is unavailable. Refresh the repository.") from exc
                total_bytes += len(raw)
                if total_bytes > MAX_SOURCE_BYTES:
                    raise DocumentationError("resource_limit", "Documentation source verification exceeded its byte limit.")
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise DocumentationError("stale_source", "A saved source changed. Refresh the repository.")
                sources.append({"fileId": row["id"], "path": path, "sourceHash": expected,
                                "endLine": max(1, len(raw.splitlines())), "kind": row["kind"],
                                "language": row["language"]})
            symbols = db.execute("""SELECT relative_path,kind,name,qualified_name,parameters_json,return_annotation,
                start_line,end_line FROM symbols WHERE repository_id=? ORDER BY relative_path,start_line LIMIT 10001""",
                (self.repo_id,)).fetchall()
            if len(symbols) > 10000:
                truncated = True
            by_path = {}
            included = {source["path"] for source in sources}
            for symbol in symbols[:10000]:
                if symbol["relative_path"] in included:
                    by_path.setdefault(symbol["relative_path"], []).append(dict(symbol))
            relationships = db.execute("""SELECT a.relative_path AS source_path,b.relative_path AS target_path,
                r.relationship FROM graph_relationships r
                JOIN graph_nodes a ON a.id=r.source_id AND a.repository_id=r.repository_id
                JOIN graph_nodes b ON b.id=r.target_id AND b.repository_id=r.repository_id
                WHERE r.repository_id=? AND r.resolution='resolved' AND
                r.relationship IN ('IMPORTS','DEPENDS_ON','CALLS') ORDER BY a.relative_path,b.relative_path LIMIT 301""",
                (self.repo_id,)).fetchall()
            if len(relationships) > 300:
                truncated = True
            relationships = [dict(row) for row in relationships[:300]]

        def plan(kind, key, title, lines, dependencies):
            clean = ["# " + _safe(title), "", *["- " + _safe(line) for line in lines]]
            source_refs = [{field: source[field] for field in ("fileId", "path", "sourceHash", "endLine")}
                           for source in dependencies[:MAX_REFERENCES]]
            dependency_hashes = [{"path": source["path"], "sourceHash": source["sourceHash"]} for source in dependencies]
            # All dependencies contribute to the fingerprint, even when the UI shows a bounded subset.
            return {"sectionId": _id(self.repo_id, kind, key), "kind": kind, "title": _safe(title),
                    "contentMarkdown": "\n".join(clean).strip() + "\n", "sources": source_refs,
                    "dependencies": dependency_hashes,
                    "factsHash": _fingerprint([lines, [(s["path"], s["sourceHash"]) for s in dependencies]])}

        modules = {}
        for source in sources:
            module = source["path"].split("/")[0] if "/" in source["path"] else "(root)"
            modules.setdefault(module, []).append(source)
        overview_lines = [f"Indexed files verified in this documentation pass: {len(sources)}; modules represented: {len(modules)}.",
                          "This overview lists verified indexed metadata. It does not infer runtime behavior."]
        overview_lines += [f"Module {module}: {len(group)} indexed files" for module, group in sorted(modules.items())[:50]]
        plans = [plan("system_overview", "project", "Project overview", overview_lines, sources)]
        for module, group in sorted(modules.items()):
            lines = [f"File: {s['path']} ({s['language'] or s['kind']})" for s in group[:30]]
            lines += [f"Symbol: {sym['qualified_name']} ({sym['kind']}) in {path}:{sym['start_line']}"
                      for s in group[:30] for path, sym in [(s["path"], item) for item in by_path.get(s["path"], [])[:4]]][:60]
            if len(group) > 30:
                lines.append(f"{len(group) - 30} additional files omitted from this bounded view")
            paths = {source["path"] for source in group}
            lines += [f"Verified static {edge['relationship']}: {edge['source_path']} to {edge['target_path']}"
                      for edge in relationships if edge["source_path"] in paths and
                      edge["target_path"] in included][:30]
            plans.append(plan("module_purpose", module, f"Module: {module}", lines, group))
        for source in sources:
            members = by_path.get(source["path"], [])
            if not members:
                continue
            lines = [f"{item['kind']} {item['qualified_name']} at lines {item['start_line']}-{item['end_line']}"
                     + (f"; returns {item['return_annotation']}" if item['return_annotation'] else "")
                     for item in members[:40]]
            if len(members) > 40:
                lines.append(f"{len(members) - 40} additional symbols omitted")
            plans.append(plan("symbol_summary", source["path"], f"Symbols: {source['path']}", lines, [source]))
        return plans, truncated

    def _validate_plan(self, plan, view):
        try:
            if (not re.fullmatch(r"[a-f0-9]{40}", plan["sectionId"]) or
                    plan["kind"] not in ("system_overview", "module_purpose", "symbol_summary") or
                    not isinstance(plan["contentMarkdown"], str) or
                    not plan["contentMarkdown"].strip() or len(plan["contentMarkdown"]) > 100_000 or
                    not isinstance(plan["sources"], list) or
                    not isinstance(plan["dependencies"], list)):
                raise ValueError("Invalid generated document")
            observed = view["manifest"]["sources"]["files"]
            dependency_paths = set()
            for item in plan["dependencies"]:
                path = relative_path(item["path"])
                if observed.get(path) != item["sourceHash"]:
                    raise ValueError("Unverified dependency")
                dependency_paths.add(path)
            for item in plan["sources"]:
                path = relative_path(item["path"])
                if (path not in dependency_paths or observed.get(path) != item["sourceHash"] or
                        item["fileId"] != file_id(self.repo_id, path) or
                        not isinstance(item["endLine"], int) or item["endLine"] < 1):
                    raise ValueError("Unverified source reference")
        except (KeyError, TypeError, ValueError) as exc:
            raise DocumentationError("failed", "Generated documentation references are invalid.") from exc

    def sync(self, mode):
        if mode not in ("baseline", "incremental"):
            raise DocumentationError("unsupported", "Unsupported documentation sync mode.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with FileLock(str(self.directory / "sync.lock"), timeout=0):
                with self.snapshot.pin_view() as view:
                    if not view["manifest"]:
                        raise DocumentationError("missing_snapshot", "Index the repository before syncing documentation.")
                    if not view["fresh"]:
                        raise DocumentationError("stale_source", view["error"] or "Refresh the knowledge snapshot before syncing documentation.")
                    plans, truncated = self._plans(view)
                    for item in plans:
                        self._validate_plan(item, view)
                    data = self._load()
                    previous = data["documents"]
                    plans_by_id = {plan["sectionId"]: plan for plan in plans}
                    pending = [plan for plan in plans if previous.get(plan["sectionId"], {}).get("factsHash") != plan["factsHash"]]
                    selected = pending[:MAX_DOCUMENTS]
                    if self.snapshot.view()["snapshotId"] != view["snapshotId"] or not self.snapshot.view()["fresh"]:
                        raise DocumentationError("stale_source", "The knowledge snapshot changed during documentation sync.")
                    updated = []
                    for item in selected:
                        old = previous.get(item["sectionId"])
                        stamp = _now()
                        previous[item["sectionId"]] = {**item, "sourceSnapshotId": view["snapshotId"],
                            "sourceCommit": view["manifest"]["sources"].get("revision"),
                            "generationReason": "deterministic_verified_metadata",
                            "createdAt": old["createdAt"] if old else stamp, "updatedAt": stamp}
                        updated.append(item["sectionId"])
                    removed = []
                    if not truncated and len(pending) <= MAX_DOCUMENTS:
                        for identifier in list(previous):
                            if identifier not in plans_by_id:
                                removed.append(identifier)
                                del previous[identifier]
                    outcome = "partial" if truncated or len(pending) > MAX_DOCUMENTS else ("success" if updated or removed else "no_changes")
                    warning = ("Only the first 500 indexed files were inspected; the repository exceeds the documentation source limit."
                               if truncated else "Documentation limit reached; run Sync Docs again." if len(pending) > MAX_DOCUMENTS else None)
                    if outcome != "no_changes":
                        data["lastSuccessfulSync"] = _now() if outcome == "success" else data.get("lastSuccessfulSync")
                        data["lastOutcome"] = outcome
                        data["lastWarning"] = warning
                        self._write(data)
                    result = self._listing(data, view)
                    overview = next((row for row in previous.values() if row["kind"] == "system_overview"), None)
                    return {**result, "mode": mode, "outcome": outcome, "updated": updated, "removed": removed,
                            "affected": removed, "reusedCount": len(plans) - len(pending),
                            "pendingCount": max(0, len(pending) - len(selected)),
                            "warning": warning,
                            "overview": self._public(overview, view) if overview else None}
        except Timeout as exc:
            raise DocumentationError("resource_limit", "A documentation sync is already running.") from exc
