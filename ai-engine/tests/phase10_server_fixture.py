"""Isolated authenticated loopback server for the real VS Code host integration."""
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
import uvicorn
import test_phase6_execution as phase6
from test_phase8_generation import valid_answer


def main():
    owner = phase6.ExecutionTests(); owner.setUp()
    api._REGISTERED_REPOS[owner.repo]["createdAt"] = api._utc_now_iso()
    token = secrets.token_hex(32)
    counts = {"query": 0, "presentation": 0, "source": 0, "model": 0, "legacy": 0}
    def generated(_self, context, *_args):
        counts["model"] += 1
        return valid_answer(context)
    def legacy(_root, _question, retrieval):
        counts["legacy"] += 1
        return {**retrieval, "answer": "Legacy fixture answer", "answerMode": "evidence"}
    @api.app.middleware("http")
    async def meter(request, call_next):
        kind = request.url.path.rsplit("/", 1)[-1]
        if request.url.path.startswith("/v1/hybrid/") and kind in counts: counts[kind] += 1
        return await call_next(request)
    @api.app.get("/test/phase10/metrics")
    def metrics(): return counts
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen(16)
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(api, "_SESSION_TOKEN", token))
            stack.enter_context(patch.object(api, "_REGISTRY_FILE", str(owner.fixture.data / "repos.json")))
            stack.enter_context(patch.dict(os.environ, {"CODEMIND_ENABLE_HYBRID": "1", "CODEMIND_ENABLE_DOCS": "1"}))
            stack.enter_context(patch("query_system.local_ollama.LocalOllama.generate", generated))
            stack.enter_context(patch("api.answer_question", legacy))
            server = uvicorn.Server(uvicorn.Config(api.app, log_level="error", lifespan="off"))
            worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True); worker.start()
            print(json.dumps({"port": sock.getsockname()[1], "token": token, "repo": owner.repo,
                              "rootPath": str(owner.fixture.root.resolve())}), flush=True)
            sys.stdin.read()
            server.should_exit = True; worker.join(timeout=10)
    finally:
        sock.close(); owner.doCleanups()

if __name__ == "__main__": main()
