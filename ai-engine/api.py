"""
api.py — FastAPI server for the CodeMind (Argus) AI engine.

Exposes REST endpoints consumed by the VS Code extension for repository
registration, background indexing, semantic code search, and documentation.
"""

import hashlib
import logging
import os
import time
import uuid
from enum import Enum
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from qdrant_client.models import Filter, FieldCondition, MatchValue

import search
from config import COLLECTION_NAME, get_qdrant_client
from indexer import IndexingError, index_directory

logger = logging.getLogger(__name__)

app = FastAPI(title="Argus Python AI Engine")

# ─────────────────────────────────────────────────────────────
# Session token validation
# ─────────────────────────────────────────────────────────────

# Tokens issued during this server lifetime.  The extension sends the
# token it received at /hello in every subsequent request via the
# x-codemind-session header.
_VALID_SESSION_TOKENS: set[str] = set()


async def _require_session(
    x_codemind_session: str | None = Header(default=None),
) -> str:
    """FastAPI dependency that validates the x-codemind-session header.

    Skips validation when no tokens have been issued yet (first-run /
    dev convenience).  Returns the validated token string.
    """
    if not _VALID_SESSION_TOKENS:
        # No tokens issued yet — allow unauthenticated access so the
        # extension can call /hello to obtain a token.
        return x_codemind_session or ""
    if not x_codemind_session or x_codemind_session not in _VALID_SESSION_TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or missing session token")
    return x_codemind_session

# ─────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────

# Repos registered during this session (repoId → metadata).
_REGISTERED_REPOS: dict[str, dict] = {}


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Running / completed jobs (jobId → job record).
_JOBS: dict[str, dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _resolve_source(repo_id: str, rel_path: str) -> str | None:
    """Resolve a repo-relative indexed path to an absolute on-disk path."""
    if not rel_path:
        return None
    candidates: list[str] = []
    entry = _REGISTERED_REPOS.get(repo_id)
    if entry:
        candidates.append(os.path.join(entry["rootPath"], rel_path))
    candidates.append(os.path.abspath(rel_path))
    candidates.append(os.path.abspath(os.path.join("sample_code", os.path.basename(rel_path))))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _run_index_job(job_id: str, repo_id: str, root_path: str, full_reindex: bool = False) -> None:
    """Background task: run index_directory and update the job record."""
    job = _JOBS[job_id]
    job["state"] = JobState.RUNNING
    job["startedAt"] = time.time()

    try:
        result = index_directory(
            target_dir=root_path,
            repo_id=repo_id,
            full_reindex=full_reindex,
            on_progress=lambda msg: logger.info("[job %s] %s", job_id[:8], msg),
        )
        job["state"] = JobState.COMPLETED
        job["result"] = {
            "fileCount": result["files_indexed"] + result["files_skipped"],
            "commitHash": "latest",
            "branch": "main",
            "dirtyFingerprint": "clean",
            "semantic": {
                "state": "ready",
                "error": None,
                "chunkCount": result["points_upserted"],
            },
            "docs": {
                "sectionCount": 0,
                "currentCount": 0,
                "staleCount": 0,
                "affectedCount": 0,
                "generatedCount": 0,
            },
        }
        job["error"] = None
    except (IndexingError, Exception) as exc:
        job["state"] = JobState.FAILED
        job["error"] = str(exc)
        logger.exception("Indexing job %s failed", job_id[:8])
    finally:
        job["completedAt"] = time.time()


# ─────────────────────────────────────────────────────────────
# Exception handler
# ─────────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # The extension reads a top-level "error" field, unlike FastAPI's "detail".
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        detail = detail["error"]
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


# ─────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────

class HelloRequest(BaseModel):
    message: str

class RegisterRequest(BaseModel):
    rootPath: str
    config: dict = {}

class SearchRequest(BaseModel):
    repoId: str
    query: str
    limit: int = 5


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@app.post("/hello")
def hello(req: HelloRequest):
    """Handshake endpoint.  Issues a session token on first contact."""
    token = uuid.uuid4().hex
    _VALID_SESSION_TOKENS.add(token)
    return {
        "reply": "Python AI Engine connected. Ready to analyze code.",
        "sessionToken": token,
    }


# ── Repository registration & indexing lifecycle ─────────────

@app.post("/repos/register")
def register_repo(req: RegisterRequest, _token: str = Depends(_require_session)):
    repo_id = hashlib.sha256(req.rootPath.encode()).hexdigest()[:24]
    _REGISTERED_REPOS[repo_id] = {"rootPath": req.rootPath, "config": req.config}
    return {
        "repository": {
            "id": repo_id,
            "rootPath": req.rootPath,
            "defaultBranch": "main",
            "createdAt": "2026-10-05T00:00:00.000Z",
            "config": {}
        }
    }


@app.post("/repos/{repo_id}/index")
def index_repo(repo_id: str, background_tasks: BackgroundTasks, _token: str = Depends(_require_session)):
    """Queue a full indexing job for the registered repo."""
    entry = _REGISTERED_REPOS.get(repo_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_id}' not registered")

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {
        "id": job_id,
        "repoId": repo_id,
        "state": JobState.QUEUED,
        "error": None,
        "result": None,
        "createdAt": time.time(),
    }
    background_tasks.add_task(_run_index_job, job_id, repo_id, entry["rootPath"], True)
    return {"jobId": job_id, "state": "queued"}


@app.post("/repos/{repo_id}/refresh")
def refresh_repo(repo_id: str, background_tasks: BackgroundTasks, _token: str = Depends(_require_session)):
    """Queue an incremental (refresh) indexing job for the registered repo."""
    entry = _REGISTERED_REPOS.get(repo_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_id}' not registered")

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = {
        "id": job_id,
        "repoId": repo_id,
        "state": JobState.QUEUED,
        "error": None,
        "result": None,
        "createdAt": time.time(),
    }
    background_tasks.add_task(_run_index_job, job_id, repo_id, entry["rootPath"], False)
    return {"jobId": job_id, "state": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    """Return the current state of an indexing job."""
    job = _JOBS.get(job_id)
    if not job:
        # Backwards-compatible: return a synthetic completed record for
        # unknown job IDs so the extension doesn't hard-fail on stale IDs.
        return {
            "id": job_id,
            "state": "completed",
            "error": None,
            "result": {
                "fileCount": 0,
                "commitHash": "latest",
                "branch": "main",
                "dirtyFingerprint": "clean",
                "semantic": {"state": "ready", "error": None, "chunkCount": 0},
                "docs": {
                    "sectionCount": 0, "currentCount": 0,
                    "staleCount": 0, "affectedCount": 0, "generatedCount": 0,
                },
            },
        }
    return {
        "id": job["id"],
        "state": job["state"].value if isinstance(job["state"], JobState) else job["state"],
        "error": job.get("error"),
        "result": job.get("result") or {
            "fileCount": 0,
            "commitHash": "latest",
            "branch": "main",
            "dirtyFingerprint": "clean",
            "semantic": {"state": "pending", "error": None, "chunkCount": 0},
            "docs": {
                "sectionCount": 0, "currentCount": 0,
                "staleCount": 0, "affectedCount": 0, "generatedCount": 0,
            },
        },
    }


@app.get("/repos/{repo_id}/live-state")
def live_state(repo_id: str):
    """Return the latest known state for a repo.

    Checks if any job is running for this repo and surfaces that.
    """
    # Find the most recent job for this repo
    repo_jobs = [
        j for j in _JOBS.values() if j.get("repoId") == repo_id
    ]
    latest_job = max(repo_jobs, key=lambda j: j.get("createdAt", 0)) if repo_jobs else None

    result: dict[str, Any] = {"commitHash": "latest", "branch": "main"}
    if latest_job:
        result["indexState"] = (
            latest_job["state"].value
            if isinstance(latest_job["state"], JobState)
            else latest_job["state"]
        )
        result["jobId"] = latest_job["id"]
    return result


# ── Documentation ────────────────────────────────────────────

@app.get("/docs/sections")
def doc_sections(repoId: str = "0" * 24):
    return {
        "repoId": repoId,
        "sections": [],
        "status": {
            "sectionCount": 0,
            "currentCount": 0,
            "staleCount": 0,
            "affectedCount": 0,
            "generatedCount": 0
        }
    }


@app.get("/docs/overview")
def doc_overview(repoId: str = "0" * 24, _token: str = Depends(_require_session)):
    # The extension expects a 404 with {"error": ...} when no docs exist.
    # Returning 200 with empty sections causes a misleading "up to date" state.
    raise HTTPException(status_code=404, detail="Documentation not found")


@app.get("/docs/section/{section_id}")
def doc_section(section_id: str, _token: str = Depends(_require_session)):
    """Return a single documentation section by ID.  Stub."""
    raise HTTPException(status_code=404, detail="Section not found")


@app.post("/docs/sync")
def doc_sync(_token: str = Depends(_require_session)):
    """Trigger documentation synchronisation.  Stub."""
    return {"status": "ok", "synced": 0}


# ── Analysis / Reuse ─────────────────────────────────────────

@app.post("/analysis/reuse")
def analysis_reuse(_token: str = Depends(_require_session)):
    """Identify reusable code patterns.  Stub."""
    return {"patterns": [], "status": "not_implemented"}


@app.post("/analysis/reuse/feedback")
def analysis_reuse_feedback(_token: str = Depends(_require_session)):
    """Submit feedback on a reuse suggestion.  Stub."""
    return {"status": "ok"}


# ── Knowledge graph ──────────────────────────────────────────

@app.get("/graph/file/{file_path:path}")
def graph_file(file_path: str, repoId: str = ""):
    return {"nodes": [], "edges": []}


# ── Semantic code search ─────────────────────────────────────

@app.post("/search/code")
def search_code(req: SearchRequest, _token: str = Depends(_require_session)):
    try:
        # Perform search scoped to the specific repo_id via Qdrant filter
        client = get_qdrant_client()
        embedding_model = search.get_embedding_model()
        query_vector = list(embedding_model.embed([req.query]))[0].tolist()

        search_results = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="repo_id", match=MatchValue(value=req.repoId))]
            ),
            limit=req.limit,
        ).points

        formatted_results = []
        for res in search_results:
            payload = res.payload or {}
            rel_path = payload.get("file_path", "unknown")
            abs_path = _resolve_source(req.repoId, rel_path)

            # Hash the on-disk file so the extension can verify it hasn't changed.
            file_hash = ""
            if abs_path:
                with open(abs_path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()

            formatted_results.append({
                "entityId": hashlib.sha256((abs_path or rel_path).encode()).hexdigest()[:40],
                "path": rel_path,
                "name": os.path.basename(rel_path),
                "startLine": payload.get("start_line", 1),
                "endLine": payload.get("end_line", 1),
                "reason": f"Semantic similarity score: {res.score:.4f}",
                "confidence": float(res.score),
                "revision": "latest",
                "sourceHash": file_hash,
                "callers": [],
                "tests": []
            })

        return {
            "mode": "semantic",
            "warning": None,
            "results": formatted_results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8000)