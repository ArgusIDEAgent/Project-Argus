import os
import hashlib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import search

app = FastAPI(title="Argus Python AI Engine")

# Repos registered during this session (repoId -> metadata). Used to resolve the
# on-disk location of indexed files so their hashes can be verified.
_REGISTERED_REPOS: dict[str, dict] = {}


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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # The extension reads a top-level "error" field, unlike FastAPI's "detail".
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        detail = detail["error"]
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


class HelloRequest(BaseModel):
    message: str

class RegisterRequest(BaseModel):
    rootPath: str
    config: dict = {}

class SearchRequest(BaseModel):
    repoId: str
    query: str
    limit: int = 5

@app.post("/hello")
def hello(req: HelloRequest):
    return {"reply": "Python AI Engine connected. Ready to analyze code."}

# Repository registration & indexing lifecycle
@app.post("/repos/register")
def register_repo(req: RegisterRequest):
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
@app.post("/repos/{repo_id}/refresh")
def index_repo(repo_id: str):
    return {"jobId": "0123456789abcdef0123456789abcdef", "state": "queued"}

@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    return {
        "id": job_id,
        "state": "completed",
        "error": None,
        "result": {
            "fileCount": 1,
            "commitHash": "latest",
            "branch": "main",
            "dirtyFingerprint": "clean",
            "semantic": {
                "state": "ready",
                "error": None,
                "chunkCount": 1
            },
            "docs": {
                "sectionCount": 0,
                "currentCount": 0,
                "staleCount": 0,
                "affectedCount": 0,
                "generatedCount": 0
            }
        }
    }

@app.get("/repos/{repo_id}/live-state")
def live_state(repo_id: str):
    return {"commitHash": "latest", "branch": "main"}

@app.get("/docs/sections")
@app.get("/docs/overview")
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

@app.get("/graph/file/{file_path:path}")
def graph_file(file_path: str, repoId: str = ""):
    return {"nodes": [], "edges": []}

# Semantic code search via Qdrant
@app.post("/search/code")
def search_code(req: SearchRequest):
    try:
        results = search.search_codebase(query=req.query, top_k=req.limit)
        formatted_results = []
        for res in results:
            # Indexed paths are repo-relative; the extension resolves them against
            # the selected rootPath and rejects absolute paths.
            rel_path = res.get("file_path", "unknown")
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
                "startLine": res.get("start_line", 1),
                "endLine": res.get("end_line", 1),
                "reason": f"Semantic similarity score: {res.get('score', 0):.4f}",
                "confidence": float(res.get("score", 0.0)),
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