"""
api.py — FastAPI server for the CodeMind (Argus) AI engine.

Exposes REST endpoints consumed by the VS Code extension for repository
registration, background indexing, semantic code search, and documentation.

Security model:
  - Every route (current and future) requires a session token via the
    app-level dependency below.  The token is generated at startup, stored
    with owner-only permissions at ~/.codemind/data/session_token, and the
    extension reads it from that file.
  - Swagger/OpenAPI are disabled and the Host header is pinned to loopback,
    which (together with the token) blocks DNS-rebinding attacks.
  - The server binds 127.0.0.1 only.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from config import (
    COLLECTION_NAME,
    DEFAULT_DATA_DIR,
    REPO_ID_RE,
    SESSION_TOKEN_FILENAME,
    atomic_write_text,
    embed_texts,
    ensure_private_dir,
    get_qdrant_client,
    make_repo_id,
)
from indexer import IndexingError, _discover_files, index_directory
from lexical_search import search_exact_code
from answers import answer_question

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from knowledge_base.graph import KnowledgeGraph, build_graph
from knowledge_base.metadata import SCHEMA_VERSION, trace, validate_vector
from knowledge_base.identity import relative_path as normalized_relative_path
from knowledge_base.semantic import keyword_search, matches_filters
from knowledge_base.retrieval import HybridRetriever, plan_query
from knowledge_base.synchronization import SnapshotStore, SyncManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Session token validation
# ─────────────────────────────────────────────────────────────

_SESSION_TOKEN = ""   # set at startup by lifespan(); empty => every request is rejected (fail closed)
_SYNC_MANAGER = None


def _issue_session_token() -> str:
    token = os.environ.get("CODEMIND_SESSION_TOKEN", "").strip() or secrets.token_hex(32)
    ensure_private_dir(DEFAULT_DATA_DIR)
    path = os.path.join(DEFAULT_DATA_DIR, SESSION_TOKEN_FILENAME)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    os.chmod(path, 0o600)
    return token


async def _require_session(x_codemind_session: str | None = Header(default=None)) -> None:
    supplied = (x_codemind_session or "").encode("utf-8")
    if not _SESSION_TOKEN or not hmac.compare_digest(supplied, _SESSION_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid or missing session token")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _SESSION_TOKEN, _SYNC_MANAGER
    _SESSION_TOKEN = _issue_session_token()
    _load_registry()
    if os.environ.get('CODEMIND_AUTO_SYNC','1')!='0':
        _SYNC_MANAGER = SyncManager(DEFAULT_DATA_DIR,lambda rid:_enqueue_job(rid,False,None))
        for rid,entry in list(_REGISTERED_REPOS.items()): _SYNC_MANAGER.add(rid,entry['rootPath'])
    try:
        yield
    finally:
        if _SYNC_MANAGER: _SYNC_MANAGER.close()
        _SYNC_MANAGER = None


app = FastAPI(title="Argus Python AI Engine", lifespan=lifespan,
              dependencies=[Depends(_require_session)],        # guards EVERY route, including future ones
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])   # anti DNS-rebinding


# ─────────────────────────────────────────────────────────────
# In-memory state
# ─────────────────────────────────────────────────────────────

# Repos registered during this session (repoId → metadata).
_REGISTERED_REPOS: dict[str, dict] = {}

_REPOS_LOCK = threading.Lock()
_REGISTRY_FILE = os.path.join(DEFAULT_DATA_DIR, "repos.json")
_MAX_REPOS = 64
_FORBIDDEN_COMPONENTS = {".ssh", ".aws", ".gnupg", ".kube", ".docker"}


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Running / completed jobs (jobId → job record).
_JOBS: dict[str, dict[str, Any]] = {}

_JOBS_LOCK = threading.Lock()
_INDEX_RUN_LOCK = threading.Lock()      # one heavy indexing run at a time (local, single user)
_MAX_JOBS = 50

_EMPTY_DOCS_STATUS = {"sectionCount": 0, "currentCount": 0, "staleCount": 0, "affectedCount": 0, "generatedCount": 0}


# ─────────────────────────────────────────────────────────────
# Repository registry
# ─────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_root(raw: str) -> tuple[str, str]:
    if not raw.strip() or "\x00" in raw:
        raise HTTPException(status_code=400, detail="Invalid rootPath.")
    abs_root = os.path.abspath(os.path.expanduser(raw))
    real_root = os.path.realpath(abs_root)
    if not os.path.isdir(real_root):
        raise HTTPException(status_code=400, detail="rootPath is not an existing directory.")
    parts = {p.lower() for p in real_root.replace("\\", "/").split("/")}
    if (real_root == os.path.dirname(real_root) or real_root == os.path.realpath(os.path.expanduser("~"))
            or parts & _FORBIDDEN_COMPONENTS):
        raise HTTPException(status_code=400,
                            detail="Refusing to index a filesystem root, a home directory or a credentials directory.")
    return abs_root, make_repo_id(real_root)


def _save_registry() -> None:                      # caller holds _REPOS_LOCK
    atomic_write_text(_REGISTRY_FILE, json.dumps(_REGISTERED_REPOS, indent=2))


def _load_registry() -> None:
    try:
        with open(_REGISTRY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for repo_id, entry in (data.items() if isinstance(data, dict) else []):
        root = entry.get("rootPath") if isinstance(entry, dict) else None
        if REPO_ID_RE.fullmatch(str(repo_id)) and isinstance(root, str) and os.path.isdir(root):
            _REGISTERED_REPOS[repo_id] = {"rootPath": root, "createdAt": str(entry.get("createdAt") or _utc_now_iso())}


# ─────────────────────────────────────────────────────────────
# Git helpers
# ─────────────────────────────────────────────────────────────

def _git(root: str, *args: str) -> str | None:
    """Read-only git: no shell, 5 s timeout, fsmonitor disabled (a hostile repo's core.fsmonitor can execute code),
    no optional locks (otherwise `status` rewrites .git/index and re-triggers the extension's file watcher)."""
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=root, capture_output=True, text=True, timeout=5, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _git_head(root: str) -> dict:
    return {"commitHash": _git(root, "rev-parse", "HEAD") or "unborn",
            "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"}


def _git_state(root: str) -> dict:
    porcelain = _git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    dirty = hashlib.sha256(porcelain.encode()).hexdigest()[:12] if porcelain else "clean"
    return {**_git_head(root), "dirtyFingerprint": dirty}


# ─────────────────────────────────────────────────────────────
# Job management
# ─────────────────────────────────────────────────────────────

def _enqueue_job(repo_id: str, full: bool, background_tasks: BackgroundTasks | None) -> dict:
    entry = _REGISTERED_REPOS.get(repo_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_id}' not registered")
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job["repoId"] == repo_id and job["state"] in (JobState.QUEUED, JobState.RUNNING):
                return {"jobId": job["id"], "state": job["state"].value}      # coalesce duplicates
        job_id = uuid.uuid4().hex
        _JOBS[job_id] = {"id": job_id, "repoId": repo_id, "state": JobState.QUEUED,
                         "error": None, "result": None, "createdAt": time.time()}
        finished = sorted((j for j in _JOBS.values() if j["state"] in (JobState.COMPLETED, JobState.FAILED)),
                          key=lambda j: j["createdAt"])
        for old in finished[:max(0, len(_JOBS) - _MAX_JOBS)]:
            _JOBS.pop(old["id"], None)
    if background_tasks is not None:
        background_tasks.add_task(_run_index_job, job_id, repo_id, entry["rootPath"], full)
    else:
        threading.Thread(target=_run_index_job,args=(job_id,repo_id,entry['rootPath'],full),daemon=True).start()
    return {"jobId": job_id, "state": "queued"}


def _run_index_job(job_id: str, repo_id: str, root_path: str, full_reindex: bool = False) -> None:
    with _INDEX_RUN_LOCK:
        job = _JOBS[job_id]
        job["state"], job["startedAt"] = JobState.RUNNING, time.time()
        store = None
        try:
            store = SnapshotStore(root_path,repo_id,DEFAULT_DATA_DIR)
            result = store.publish(client=get_qdrant_client(),collection=COLLECTION_NAME,
                index_directory=index_directory,build_graph=build_graph,full=full_reindex,
                on_progress=lambda msg:logger.info('[job %s] %s',job_id[:8],msg))
            chunk_count = result['chunk_count']
            job["result"] = {
                "fileCount": result["files_indexed"] + result["files_skipped"],
                **_git_state(root_path),
                "semantic": {"state": "ready", "error": None, "chunkCount": chunk_count},
                "docs": dict(_EMPTY_DOCS_STATUS),
                "graph": {"state":"ready","statistics":result['graph']['graph'],"snapshotId":result['graph']['graph_snapshot_id']},
                "metadata": {"schemaVersion": SCHEMA_VERSION, "snapshotId": result.get('snapshot_id'),
                             "activeSnapshotId":result['activeSnapshotId'],"savedFilesOnly":True},
                "incremental": {k:result.get(k) for k in ('files_indexed','files_skipped','files_pruned','chunks_embedded','chunks_reused','unchanged')},
            }
            job["state"] = JobState.COMPLETED
        except Exception as exc:
            logger.exception("Indexing job %s failed", job_id[:8])
            job["error"], job["state"] = "Indexing failed unexpectedly; see the engine log.", JobState.FAILED
            if store:
                try: store.failed(exc)
                except Exception: logger.exception('Unable to record synchronization failure')
        finally:
            job["completedAt"] = time.time()


# ─────────────────────────────────────────────────────────────
# Exception handlers
# ─────────────────────────────────────────────────────────────

@app.exception_handler(StarletteHTTPException)          # replaces the FastAPI-HTTPException handler
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        detail = detail["error"]
    return JSONResponse(status_code=exc.status_code, content={"error": str(detail)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "Invalid request parameters."})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal engine error."})


# ─────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────

class HelloRequest(BaseModel):
    message: str = ""

class RegisterRequest(BaseModel):
    rootPath: str = Field(min_length=1, max_length=4096)
    config: dict = Field(default_factory=dict)     # accepted for compatibility, intentionally ignored

class SearchFilters(BaseModel):
    files: list[str] = Field(default_factory=list,max_length=25)
    modules: list[str] = Field(default_factory=list,max_length=25)
    languages: list[str] = Field(default_factory=list,max_length=25)
    symbolTypes: list[str] = Field(default_factory=list,max_length=25)

    @field_validator('files','modules')
    @classmethod
    def safe_paths(cls,values):
        return [normalized_relative_path(value) for value in values]

    @field_validator('languages','symbolTypes')
    @classmethod
    def labels(cls,values):
        if any(not value or len(value)>64 for value in values):
            raise ValueError('Invalid metadata filter')
        return [value.lower() for value in values]


class SearchRequest(BaseModel):
    repoId: str = Field(pattern=r"^[a-f0-9]{24}$")
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=25)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    retrieval: Literal['auto','hybrid','semantic','keyword'] = 'auto'

    @field_validator('query')
    @classmethod
    def searchable_query(cls,value):
        plan_query(value)
        return value


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@app.post("/hello")
def hello(req: HelloRequest | None = None):
    """Handshake endpoint.  Requires the shared session token (app-level dependency)."""
    return {"reply": "Python AI Engine connected. Ready to analyze code."}


# ── Repository registration & indexing lifecycle ─────────────

@app.post("/repos/register")
def register_repo(req: RegisterRequest):
    abs_root, repo_id = _validate_root(req.rootPath)
    with _REPOS_LOCK:
        if repo_id not in _REGISTERED_REPOS and len(_REGISTERED_REPOS) >= _MAX_REPOS:
            raise HTTPException(status_code=429, detail="Too many registered repositories.")
        entry = _REGISTERED_REPOS.get(repo_id) or {"createdAt": _utc_now_iso()}
        entry["rootPath"] = abs_root               # NOT realpath: the extension compares paths by prefix
        _REGISTERED_REPOS[repo_id] = entry
        _save_registry()
    if _SYNC_MANAGER: _SYNC_MANAGER.add(repo_id,abs_root)
    return {"repository": {"id": repo_id, "rootPath": abs_root, "defaultBranch": "main",
                           "createdAt": entry["createdAt"], "config": {}}}


@app.post("/repos/{repo_id}/index")
def index_repo(repo_id: str, background_tasks: BackgroundTasks, full: bool = False):
    return _enqueue_job(repo_id, full, background_tasks)   # incremental unless ?full=true (fingerprint forces full when needed)


@app.post("/repos/{repo_id}/refresh")
def refresh_repo(repo_id: str, background_tasks: BackgroundTasks):
    return _enqueue_job(repo_id, False, background_tasks)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    """Return the current state of an indexing job."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
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
            "docs": dict(_EMPTY_DOCS_STATUS),
        },
    }


@app.get("/repos/{repo_id}/live-state")
def live_state(repo_id: str):
    """Return the latest known state for a repo (git HEAD + most recent job).

    Never runs `git status` here — this endpoint is polled every 3 s.
    """
    entry = _REGISTERED_REPOS.get(repo_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_id}' not registered")
    with _JOBS_LOCK:
        repo_jobs = [j for j in list(_JOBS.values()) if j.get("repoId") == repo_id]
    latest_job = max(repo_jobs, key=lambda j: j.get("createdAt", 0)) if repo_jobs else None

    result: dict[str, Any] = {**_git_head(entry["rootPath"]), "indexState": None, "jobId": None}
    if latest_job:
        result["indexState"] = (
            latest_job["state"].value
            if isinstance(latest_job["state"], JobState)
            else latest_job["state"]
        )
        result["jobId"] = latest_job["id"]
    view = SnapshotStore(entry['rootPath'],repo_id,DEFAULT_DATA_DIR).view()
    if _SYNC_MANAGER and not view['manifest']:
        view.update(fresh=False,error='Waiting for the first consistent knowledge snapshot.')
    result['synchronization'] = {k:view[k] for k in ('snapshotId','fresh','state','error','savedFilesOnly')}
    if _SYNC_MANAGER: result['synchronization'].update(_SYNC_MANAGER.status(repo_id))
    return result


# ── Documentation ────────────────────────────────────────────

@app.get("/docs/sections")
def doc_sections(repoId: str = "0" * 24):
    return {
        "repoId": repoId,
        "sections": [],
        "status": dict(_EMPTY_DOCS_STATUS),
    }


@app.get("/docs/overview")
def doc_overview(repoId: str = "0" * 24):
    # The extension expects a 404 with {"error": ...} when no docs exist.
    # Returning 200 with empty sections causes a misleading "up to date" state.
    raise HTTPException(status_code=404, detail="Documentation not found")


@app.get("/docs/section/{section_id}")
def doc_section(section_id: str):
    """Return a single documentation section by ID.  Stub."""
    raise HTTPException(status_code=404, detail="Section not found")


@app.post("/docs/sync")
def doc_sync():
    return {"updated": [], "affected": [], "status": dict(_EMPTY_DOCS_STATUS), "overview": None}


# ── Analysis / Reuse ─────────────────────────────────────────

@app.post("/analysis/reuse")
def analysis_reuse():
    return {"mode": "not_implemented", "warning": "Reuse analysis is not implemented yet.", "results": []}


@app.post("/analysis/reuse/feedback")
def analysis_reuse_feedback():
    """Submit feedback on a reuse suggestion.  Stub."""
    return {"status": "ok"}


# ── Knowledge graph ──────────────────────────────────────────

@app.get("/graph/file/{file_path:path}")
def graph_file(file_path: str, repoId: str = ""):
    if repoId not in _REGISTERED_REPOS:
        raise HTTPException(404, 'Repository is not registered.')
    with SnapshotStore(_REGISTERED_REPOS[repoId]['rootPath'],repoId,DEFAULT_DATA_DIR).pin_view() as view:
        return _graph_file_pinned(file_path,repoId,view)


def _graph_file_pinned(file_path,repoId,view):
    if repoId not in _REGISTERED_REPOS:
        raise HTTPException(404, "Repository is not registered.")
    store = SnapshotStore(_REGISTERED_REPOS[repoId]['rootPath'],repoId,DEFAULT_DATA_DIR)
    if _SYNC_MANAGER and not view['manifest']:
        view.update(fresh=False,error='Waiting for the first consistent knowledge snapshot.')
    if not view['fresh']:
        return {'nodes':[],'edges':[],'warning':view['error'],'knowledgeSnapshotId':view['snapshotId']}
    database = os.path.join(view['data_dir'], repoId, "metadata.db")
    if not os.path.isfile(database):
        return {"nodes": [], "edges": [], "warning": "Knowledge graph is not indexed yet."}
    try:
        graph = KnowledgeGraph(database, repoId)
        with closing(graph._connection()) as db:
            nodes = [dict(row) for row in db.execute("""SELECT n.id, n.kind AS type,
                n.kind, s.start_line AS startLine, s.end_line AS endLine
                FROM graph_nodes n JOIN symbols s ON n.symbol_id=s.id
                WHERE n.repository_id=? AND n.relative_path=?""", (repoId, file_path))]
            edges = [dict(row) for row in db.execute("""SELECT e.* FROM graph_relationships e
                JOIN graph_nodes n ON n.id=e.source_id WHERE e.repository_id=?
                AND n.relative_path=? AND e.resolution='resolved'""", (repoId, file_path))]
        if view['snapshotId'] and not store.matches(view['snapshotId']):
            return {'nodes':[],'edges':[],'warning':'Saved files changed during graph retrieval.'}
        return {"nodes": nodes, "edges": edges,'knowledgeSnapshotId':view['snapshotId']}
    except Exception as exc:
        logger.warning("Graph unavailable: %s", exc)
        return {"nodes": [], "edges": [], "warning": "Graph is stale or unavailable. Refresh the index."}


# ── Semantic code search ─────────────────────────────────────

def _enrich_graph(req, results, data_dir=None):
    database = os.path.join(data_dir or DEFAULT_DATA_DIR, req.repoId, "metadata.db")
    if not os.path.isfile(database):
        return results
    try:
        with closing(KnowledgeGraph(database, req.repoId)._connection()) as db:
            for item in results:
                nodes = db.execute("""SELECT n.id, n.label FROM graph_nodes n
                    JOIN symbols s ON n.symbol_id=s.id WHERE n.repository_id=?
                    AND n.relative_path=? AND s.start_line<=? AND s.end_line>=?""",
                    (req.repoId, item["path"], item["endLine"], item["startLine"])).fetchall()
                callers = {}
                for node in nodes:
                    rows = db.execute("""SELECT n.id, n.label, e.evidence_file, e.evidence_line
                        FROM graph_relationships e JOIN graph_nodes n ON n.id=e.source_id
                        WHERE e.repository_id=? AND e.target_id=? AND e.relationship='CALLS'
                        AND e.resolution='resolved'""", (req.repoId, node["id"]))
                    for row in rows:
                        callers[row["id"]] = {"name": row["label"], "path": row["evidence_file"], "line": row["evidence_line"]}
                item["callers"] = list(callers.values())
                item["graphVerified"] = bool(nodes)
    except Exception:
        # Retrieval remains usable when the graph is absent/stale; don't invent facts.
        pass
    return results

@app.post("/retrieve")
@app.post("/search/code")
def search_code(req: SearchRequest):
    entry = _REGISTERED_REPOS.get(req.repoId)
    if not entry: raise HTTPException(status_code=404, detail='Repository is not registered.')
    with SnapshotStore(entry['rootPath'],req.repoId,DEFAULT_DATA_DIR).pin_view() as view:
        return _retrieve_pinned(req,view)


def _retrieve_pinned(req,view):
    entry = _REGISTERED_REPOS.get(req.repoId)
    if not entry:
        raise HTTPException(status_code=404, detail="Repository is not registered.")
    if _SYNC_MANAGER and not view['manifest']:
        view.update(fresh=False,error='Waiting for the first consistent knowledge snapshot.')
    data_dir = str(view['data_dir'])
    collection = view['manifest']['collection'] if view['manifest'] else COLLECTION_NAME
    def channel(fn):
        def call(query,filters,limit):
            request = req.model_copy(update={'query':query,'filters':SearchFilters(**filters),'limit':limit})
            return fn(request,data_dir=data_dir,collection=collection) if view['managed'] else fn(request)
        return call
    retriever = HybridRetriever(entry['rootPath'],req.repoId,data_dir,indexed_ready=view['fresh'],
        exact_search=lambda query,filters,limit: search_exact_code(entry['rootPath'],req.repoId,query,limit,filters=filters),
        keyword_search=channel(_keyword_search),semantic_search=channel(_semantic_search))
    result = retriever.retrieve(req.query,filters=req.filters.model_dump(),limit=req.limit,retrieval=req.retrieval)
    if view['fresh']: result['results'] = _enrich_graph(req,result['results'],data_dir)
    if view['manifest'] and not SnapshotStore(entry['rootPath'],req.repoId,DEFAULT_DATA_DIR).matches(view['snapshotId']):
        result['results'] = [r for r in result['results'] if r.get('sourceOrigin')=='current_disk']
        result['uncertainty'] = []
        result['status'] = 'found' if result['results'] else 'not_found'
        view.update(fresh=False,error='Saved files changed during retrieval; indexed evidence was discarded.')
    result['knowledgeSnapshotId'] = view['snapshotId']
    result['knowledgeSourceHash'] = view['manifest']['sources']['digest'] if view['manifest'] else None
    result['synchronization'] = {k:view[k] for k in ('fresh','state','error','savedFilesOnly')}
    if view['error']: result['warning'] = ' '.join(filter(None,(view['error'],result.get('warning'))))
    return result


def _semantic_search(req, *, data_dir=None, collection=None):
    data_dir,collection = data_dir or DEFAULT_DATA_DIR,collection or COLLECTION_NAME
    entry = _REGISTERED_REPOS[req.repoId]
    conversational = bool(re.match(r"^(?:where|what|how|why|which|explain|describe|tell|who)\b", req.query, re.I))
    try:
        client = get_qdrant_client()
        if not client.collection_exists(collection):
            return {"mode": "semantic", "warning": "No exact match. This repository has no semantic index yet.", "results": []}
        conditions = [FieldCondition(key="repo_id",match=MatchValue(value=req.repoId))]
        for key,column in (('files','file_path'),('modules','module'),('languages','language'),('symbolTypes','symbol_kind')):
            values = getattr(req.filters,key)
            if values:
                conditions.append(FieldCondition(key=column,match=MatchAny(any=values)))
        scope = Filter(must=conditions)
        if client.count(collection, exact=True, count_filter=scope).count == 0:
            return {"mode": "semantic", "warning": "No semantic evidence is indexed for this repository. Refresh its index.", "results": []}
        points = client.query_points(
            collection_name=collection,
            query=embed_texts([req.query])[0],
            query_filter=scope,
            score_threshold=0.5,
            limit=req.limit * 3,
        ).points
    except Exception:
        logger.exception("Semantic search unavailable")
        return {"mode": "unavailable", "warning": "No exact match. Semantic search is unavailable; check the local embedding/vector engine log.", "results": []}

    results = []
    stale = False
    if conversational:
        def implementation_rank(point):
            path = str((point.payload or {}).get("file_path", "")).lower()
            penalty = 0.15 if (path.endswith((".md", ".markdown")) or
                any(part in {"tests", "test", "__tests__"} for part in path.split("/")) or
                os.path.basename(path).startswith("test_")) else 0
            return point.score - penalty
        points = sorted(points, key=implementation_rank, reverse=True)
    for pt in points:
        payload = pt.payload or {}
        if not matches_filters(payload,req.filters.model_dump()):
            continue
        rel_path = str(payload.get("file_path") or "")
        if not rel_path or os.path.isabs(rel_path) or ".." in rel_path.replace("\\", "/").split("/"):
            continue                                # malformed or poisoned payload
        absolute = os.path.realpath(os.path.join(entry["rootPath"], rel_path))
        if os.path.commonpath([absolute, os.path.realpath(entry["rootPath"])]) != os.path.realpath(entry["rootPath"]):
            continue
        try:
            with open(absolute, "rb") as source:
                current_hash = hashlib.sha256(source.read()).hexdigest()
            if current_hash != payload.get("file_hash"):
                stale = True
                continue
        except OSError:
            stale = True
            continue
        try:
            chunk_index = int(payload.get("chunk_index", 0))
            start_line = int(payload.get("start_line", 1))
            end_line = int(payload.get("end_line", 1))
            if start_line < 1 or end_line < start_line:
                continue
        except (ValueError, TypeError):
            continue
        try:
            provenance = validate_vector(os.path.join(data_dir,req.repoId,'metadata.db'),req.repoId,pt,collection)
        except (ValueError, OSError, sqlite3.Error):
            stale = True
            continue
        results.append({
            "entityId": provenance['entityId'],
            "fileId": provenance['fileId'], "symbolId": provenance['symbolId'],
            "snapshotId": provenance['snapshotId'], "sourceOrigin": provenance['sourceOrigin'],
            "schemaVersion": provenance['schemaVersion'],
            "path": rel_path,
            "name": payload.get('qualified_name') or os.path.basename(rel_path),
            "language": payload.get('language'), "module": payload.get('module'),
            "symbolType": payload.get('symbol_kind'), "chunkKind": payload.get('chunk_kind'),
            "retrievalKind": 'semantic',
            "startLine": start_line,
            "endLine": end_line,
            "reason": f"Semantic similarity score: {pt.score:.4f}; relevance, not proof of dependency" + ("; implementation-first ranking" if conversational else ""),
            "confidence": float(pt.score),
            "revision": "latest",
            "sourceHash": str(payload.get("file_hash", "")),   # hash AT INDEX TIME
            "callers": [], "tests": [],
        })
        if len(results) >= req.limit:
            break
    warning = "Some indexed files or metadata changed. Refresh the repository index." if stale else None
    if not results and not warning:
        warning = "No sufficiently relevant semantic matches found. Try a more specific question or identifier."
    return {"mode": "semantic", "warning": warning, "results": results}


def _keyword_search(req, *, data_dir=None, collection=None):
    database = os.path.join(data_dir or DEFAULT_DATA_DIR,req.repoId,'metadata.db')
    results,stale = [],False
    try:
        rows = keyword_search(database,req.repoId,req.query,filters=req.filters.model_dump(),limit=req.limit*3)
        for row in rows:
            try:
                info = trace(database,req.repoId,row['id'])
            except (ValueError,OSError,sqlite3.Error):
                stale = True
                continue
            results.append({'entityId':info['entityId'],'fileId':info['fileId'],'symbolId':info['symbolId'],
                'snapshotId':info['snapshotId'],'sourceOrigin':info['sourceOrigin'],'schemaVersion':info['schemaVersion'],
                'path':info['path'],'name':row['qualified_name'] or os.path.basename(info['path']),
                'startLine':info['startLine'],'endLine':info['endLine'],'sourceHash':info['sourceHash'],
                'language':row['language'],'module':row['module'],'symbolType':row['symbol_kind'],'chunkKind':row['chunk_kind'],
                'retrievalKind':'keyword','keywordScore':-row['rank'],'confidence':0.8,'revision':'latest',
                'reason':'Keyword evidence ranked by SQLite FTS5 BM25; not a dependency','callers':[],'tests':[]})
            if len(results)>=req.limit:
                break
    except sqlite3.Error:
        logger.exception('Keyword index unavailable')
        return {'mode':'keyword','results':[],'warning':'Keyword index is unavailable. Refresh the repository index.'}
    warning = 'Some keyword evidence changed. Refresh the repository index.' if stale else None if results else 'No keyword evidence matched the query and filters.'
    return {'mode':'keyword','results':results,'warning':warning}


@app.post("/ask")
def ask(req: SearchRequest):
    retrieval = search_code(req)
    root = _REGISTERED_REPOS[req.repoId]['rootPath']
    result = answer_question(root,req.query,retrieval)
    if retrieval.get('knowledgeSourceHash'):
        from knowledge_base.synchronization import observe_sources
        try: current = observe_sources(root)['digest']==retrieval['knowledgeSourceHash']
        except (OSError,ValueError): current = False
    else: current = True
    if not current:
        result.update(results=[],citations=[],status='not_found',answerMode='evidence',
            answer='Saved repository files changed during this answer. Synchronization is updating the index; ask again after it completes.',
            warning='Answer discarded because its knowledge snapshot is stale.')
        result['synchronization']['fresh'] = False
    return result


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("api:app", host="127.0.0.1", port=int(os.environ.get("CODEMIND_PORT", "8000")))
