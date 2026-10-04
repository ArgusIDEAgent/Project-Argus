import os
import hashlib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import search

app = FastAPI(title="Argus Python AI Engine")

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
            file_path = res.get("file_path", "unknown")
            formatted_results.append({
                "entityId": hashlib.sha256(file_path.encode()).hexdigest()[:40],
                "path": file_path,
                "name": os.path.basename(file_path),
                "startLine": 1,
                "endLine": 10,
                "reason": f"Semantic similarity score: {res.get('score', 0):.4f}",
                "confidence": float(res.get("score", 0.0)),
                "revision": "latest",
                "sourceHash": "",
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