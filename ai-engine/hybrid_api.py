"""Default-off versioned integration. Legacy routes/security remain owned by api.py."""

import asyncio
from collections import OrderedDict
import copy
import os
from pathlib import Path
import secrets
import threading
from time import monotonic
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException

import answers
import config
from knowledge_base.synchronization import SnapshotStore
from query_system.contracts import QueryContext, SnapshotContext
from query_system.evidence_aggregation import EvidenceAggregator, FAILURES
from query_system.executor import MultiEngineExecutor
from query_system.hybrid_contracts import HybridPresentationRequest, HybridQueryRequest, HybridResponse, HybridSourceRequest
from query_system.orchestration import compose_presentation, run_hybrid
from query_system.presentation_contracts import PresentationSource
from query_system.presentation_preview import _scope_unavailable
from query_system.query_understanding import classify_conversational_intent
from query_system.semantic_engine import existing_api_channels


def hybrid_enabled():
    return os.environ.get("CODEMIND_ENABLE_HYBRID", "0").lower() in ("1", "true")


class AnswerCache:
    """Bounded process-local authority, not a persistent knowledge/result database."""
    def __init__(self, *, max_entries=32, max_bytes=8 * 1024 * 1024, ttl=300):
        self.entries = OrderedDict()
        self.lock = threading.Lock()
        self.max_entries, self.max_bytes, self.ttl = max_entries, max_bytes, ttl

    def _prune(self):
        now = monotonic()
        for token in list(self.entries):
            if self.entries[token][0] <= now: del self.entries[token]
        while len(self.entries) > self.max_entries or sum(item[3] for item in self.entries.values()) > self.max_bytes:
            self.entries.popitem(last=False)

    def put(self, repo, root, run):
        size = len(run.package.model_dump_json().encode()) + sum(len(result.model_dump_json().encode()) for _, result in run.generations) + len(run.response.model_dump_json().encode())
        if size > self.max_bytes: return None
        token = secrets.token_hex(24)
        # No caller can mutate cached evidence after publication.
        with self.lock:
            self.entries[token] = (monotonic() + self.ttl, repo, str(Path(root).resolve()), size, copy.deepcopy(run))
            self._prune()
        return token

    def get(self, token, repo, root):
        with self.lock:
            self._prune()
            entry = self.entries.get(token)
            if entry is None or entry[1:3] != (repo, str(Path(root).resolve())):
                raise HTTPException(404, "Hybrid answer handle is unavailable, expired or not authorized for this repository. Ask the question again.")
            return copy.deepcopy(entry[4])


def create_hybrid_router(api):
    router = APIRouter(prefix="/v1/hybrid", tags=["development hybrid"])
    cache = AnswerCache()
    slots = threading.BoundedSemaphore(2)

    def enabled():
        if not hybrid_enabled():
            raise HTTPException(503, "Development hybrid mode is disabled. Enable CODEMIND_ENABLE_HYBRID=1 or use legacy chat.")

    def store_for(repo):
        entry = api._REGISTERED_REPOS.get(repo)
        if entry is None: raise HTTPException(404, "Repository is not registered.")
        _, canonical = api._validate_root(entry["rootPath"])
        if canonical != repo: raise HTTPException(409, "Registered repository identity changed.")
        try: return SnapshotStore(entry["rootPath"], repo, api.DEFAULT_DATA_DIR)
        except (ValueError, OSError): raise HTTPException(409, "Registered repository identity is unavailable or changed.") from None

    def fresh_cached(req, depth=None, reference=None):
        enabled()
        store = store_for(req.repository_id)
        run = cache.get(req.navigation_token, req.repository_id, store.root)
        if reference is not None and reference not in run.package.citations:
            raise HTTPException(404, "Source reference is not owned by this answer.")
        view = compose_presentation(run, depth or run.response.presentation.depth)
        if reference is not None and reference not in view.sources:
            view.sources[reference] = PresentationSource(citation=run.package.citations[reference])
        source = None
        try:
            with MultiEngineExecutor.pinned(store) as executor:
                if executor.context.snapshot_id != run.package.snapshot_id:
                    raise ValueError("Current generation differs")
                verifier = EvidenceAggregator.from_executor(executor)
                infos = {record.proof.evidence_id: verifier.verify(record.proof) for record in run.package.evidence.values()}
                for finding in run.package.findings:
                    operation = next(item for item in run.package.operations if item.operation_id == finding.operation_id)
                    verifier._finding(finding.original, SimpleNamespace(request=operation.request), infos)
                if (store.active() or {}).get("id") != run.package.snapshot_id or not store.matches(run.package.snapshot_id): raise ValueError("Saved sources or active generation changed")
                for item in view.sources.values():
                    item.state, item.reason = "verified", "Canonical source reverified in the current expected generation."
                source = run.package.citations.get(reference)
        except (RuntimeError, *FAILURES):
            _scope_unavailable(view, "stale", "This answer's repository snapshot/source is no longer current; ask again.")
        return {"presentation": view.model_dump(mode="json"), "source": source.model_dump(mode="json") if source else None}

    @router.get("/readiness")
    def readiness(repoId: str):
        store = store_for(repoId)
        ready, snapshot, stages = False, None, []
        try:
            with store.pin_view() as view:
                context = SnapshotContext.from_pinned_view(view, repoId)
                ready, snapshot, stages = True, context.snapshot_id, sorted(context.ready_stage_ids)
        except (ValueError, OSError, RuntimeError): pass
        model = os.environ.get("CODEMIND_LOCAL_MODEL", "llama3:latest")
        return {"schema_version": "hybrid-v1", "enabled": hybrid_enabled(), "mode": "development-opt-in",
            "repository_id": repoId, "snapshot_ready": ready, "snapshot_id": snapshot, "ready_stage_ids": stages,
            "generation_configured": bool(model) and "cloud" not in model.lower() and "://" not in model,
            "model_availability": "checked by the bounded generation request; deterministic queries do not require Ollama",
            "legacy_available": True}

    @router.post("/query", response_model=HybridResponse)
    def query(req: HybridQueryRequest):
        enabled()
        static = classify_conversational_intent(req.query.question)
        if static is not None:
            return HybridResponse(status="success", snapshot_id=None, presentation=None, static_response=static)
        store = store_for(req.query.repository_id)
        if not slots.acquire(blocking=False): raise HTTPException(429, "Hybrid engine is busy; retry after current bounded requests finish.")
        try:
            conversation = None
            if req.previous_answer_token is not None:
                previous = cache.get(req.previous_answer_token, req.query.repository_id, store.root)
                indices = {index for row in previous.original.requirements for index in row.entity_indices}
                targets = [previous.original.entities[index] for index in sorted(indices)]
                unique = {target.entity_id for target in targets if target.resolution == "resolved" and target.evidence is not None}
                # Only a unique original canonical input may become context.
                # Retrieved semantic findings/candidates are never selected here.
                target = targets[0] if targets and len(unique) == 1 and all(
                    item.resolution == "resolved" and item.evidence is not None for item in targets) else None
                conversation = QueryContext(repository_id=previous.package.repository_id,
                    snapshot_id=previous.package.snapshot_id, previous_target=target,
                    previous_intents=previous.original.intents)
            run = asyncio.run(run_hybrid(store, req, existing_api_channels(api, config.EMBEDDING_MODEL_NAME), answers,
                                         conversation=conversation))
            if run.package is not None and run.response.presentation is not None and run.response.status not in ("stale_index", "error"):
                run.response.navigation_token = cache.put(req.query.repository_id, store.root, run)
                if run.response.navigation_token is None:
                    for item in run.response.presentation.sources.values():
                        item.state, item.reason = "unavailable", "Answer cache budget exceeded; source navigation is unavailable."
            return run.response
        finally:
            slots.release()

    @router.post("/presentation")
    def presentation(req: HybridPresentationRequest):
        if not slots.acquire(blocking=False): raise HTTPException(429, "Hybrid engine is busy; retry later.")
        try: return fresh_cached(req, depth=req.depth)
        finally: slots.release()

    @router.post("/source")
    def source(req: HybridSourceRequest):
        if not slots.acquire(blocking=False): raise HTTPException(429, "Hybrid engine is busy; retry later.")
        try: return fresh_cached(req, depth=req.depth, reference=req.reference)
        finally: slots.release()

    # Tests can inspect cache limits through the router object, not a production API.
    router.answer_cache = cache
    return router
