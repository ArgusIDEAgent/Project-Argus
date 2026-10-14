"""Ephemeral authenticated read-only server for actual VS Code qualification.

No lifespan, registry persistence, indexing, cache repair or source writes.
The only writable authority is the ordinary process-local answer cache.
"""
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api
import config
import faiss
from fastembed import TextEmbedding
from fastapi.responses import JSONResponse
import uvicorn
from knowledge_base.identity import repository_id
from knowledge_base.synchronization import SnapshotStore
from knowledge_base.vector_store import FaissClient
from query_system.local_ollama import LocalModelFailure, LocalOllama


def main():
    root = Path(os.environ["CODEMIND_QUAL_REPO_ROOT"]).resolve(strict=True)
    data = Path(config.DEFAULT_DATA_DIR); repo = repository_id(root)
    store = SnapshotStore(root, repo, data)
    if not store.view()["fresh"]: raise ValueError("Real UI qualification requires an existing fresh snapshot; no rebuild is authorized")
    if not (data / "faiss/vectors.sqlite3").is_file(): raise ValueError("Existing vector journal required")
    counts = {"query": 0, "source": 0, "presentation": 0, "model": 0, "blocked_mutations": 0}
    allowed = {"/hello", "/health", "/v1/hybrid/readiness", "/v1/hybrid/query", "/v1/hybrid/source", "/v1/hybrid/presentation", "/test/stabilization/metrics"}
    @api.app.middleware("http")
    async def read_only(request, call_next):
        if request.url.path not in allowed:
            counts["blocked_mutations"] += 1
            return JSONResponse(status_code=405, content={"error": "Read-only qualification server; mutation is forbidden."})
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind in counts: counts[kind] += 1
        return await call_next(request)
    @api.app.get("/test/stabilization/metrics")
    def metrics(): return counts
    token = secrets.token_hex(32); native = LocalOllama.generate
    # Explicit failure injection belongs only to this disposable test server.
    # It is never a production setting or real-model quality measurement.
    fault = os.environ.get("CODEMIND_QUAL_FAULT", "")
    if fault not in ("", "model_unavailable", "model_timeout", "graph_failure"):
        raise ValueError("Unknown qualification fault")
    def measured(self, *args):
        counts["model"] += 1
        if fault == "model_unavailable": raise LocalModelFailure("connection_unavailable", "unavailable")
        if fault == "model_timeout": raise LocalModelFailure("socket_timeout", "timeout")
        return native(self, *args)  # Actual installed local model, not a producer mock.
    model = TextEmbedding(model_name=config.EMBEDDING_MODEL_NAME, cache_dir=os.environ.get("CODEMIND_QUAL_MODEL_CACHE", str(data / "models")), local_files_only=True, threads=2)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(16)
    try:
        with contextlib.ExitStack() as stack:
            client = FaissClient(data / "faiss"); client.db.execute("PRAGMA query_only=ON"); stack.callback(client.close)
            stack.enter_context(patch.object(faiss, "write_index", side_effect=AssertionError("Read-only qualification cannot rebuild FAISS")))
            stack.enter_context(patch.object(api, "get_qdrant_client", return_value=client))
            stack.enter_context(patch.object(config, "get_embedding_model", return_value=model))
            stack.enter_context(patch.dict(api._REGISTERED_REPOS, {repo: {"rootPath": str(root)}}))
            stack.enter_context(patch.object(api, "_SESSION_TOKEN", token))
            stack.enter_context(patch.dict(os.environ, {"CODEMIND_ENABLE_HYBRID": "1"}))
            stack.enter_context(patch.object(LocalOllama, "generate", measured))
            if fault == "graph_failure":
                from query_system.graph_engine import GraphQueryEngine
                native_graph = GraphQueryEngine.execute
                async def broken_definition(self, request, context):
                    if request.name == "find_definition": raise RuntimeError("Injected qualification graph failure")
                    return await native_graph(self, request, context)
                stack.enter_context(patch.object(GraphQueryEngine, "execute", broken_definition))
            server = uvicorn.Server(uvicorn.Config(api.app, log_level="error", lifespan="off"))
            worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True); worker.start()
            print(json.dumps({"port": sock.getsockname()[1], "token": token, "repo": repo, "rootPath": str(root)}), flush=True)
            sys.stdin.read()
            server.should_exit = True; worker.join(timeout=10)
            if worker.is_alive(): raise RuntimeError("Read-only qualification server did not drain")
    finally: sock.close()


if __name__ == "__main__": main()
