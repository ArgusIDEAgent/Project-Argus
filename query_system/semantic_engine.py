"""Typed retrieval over existing semantic/keyword channels and RRF verification."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from time import monotonic
from typing import Callable, Literal

from pydantic import Field, model_validator

from knowledge_base.identity import relative_path
from knowledge_base.metadata import trace
from knowledge_base.retrieval import HybridRetriever, evidence_key, plan_query
from knowledge_base.semantic import matches_filters
from query_system.contracts import (
    ContractError, ContractModel, EngineError, EngineResult, Evidence, OperationRequest,
    OperationSpec, SemanticFinding, SnapshotContext, StaleIndexError, validate_engine_result,
)


class SearchParameters(ContractModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=25)
    filters: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_search(self):
        plan_query(self.query)
        if set(self.filters) - {"files", "modules", "languages", "symbolTypes"}:
            raise ValueError("Unsupported retrieval filter")
        for key, values in self.filters.items():
            if len(values) > 25 or any(not value or len(value) > 4096 for value in values):
                raise ValueError("Invalid retrieval filter values")
            if key in ("files", "modules"):
                for value in values:
                    relative_path(value)
            else:
                if any(len(value) > 64 for value in values):
                    raise ValueError("Invalid language or symbol type")
                self.filters[key] = [value.lower() for value in values]
        return self


SEMANTIC_OPERATIONS = {
    name: OperationSpec("semantic", name, SearchParameters, frozenset({"code_chunk"}), 15000, "partial")
    for name in ("semantic_search", "keyword_search", "hybrid_search")
}


@dataclass
class ChannelResult:
    status: Literal["available", "unavailable", "stale", "missing_repository"]
    results: list[dict]
    warning: str | None = None


@dataclass(frozen=True)
class RetrievalChannels:
    semantic_search: Callable[[str, dict, int], ChannelResult]
    keyword_search: Callable[[str, dict, int], ChannelResult]
    embedding_model: str


def existing_api_channels(api, embedding_model: str):
    """Bind the current API's existing channel functions to a supplied pinned view.

    Passing the loaded API explicitly avoids importing it or starting a server.
    No repository registration, indexing, embedding rebuild, or API changes occur.
    """
    def factory(view):
        manifest = view["manifest"]
        repo, collection = manifest["repoId"], manifest["collection"]
        def call(provider, query, filters, limit):
            entry = api._REGISTERED_REPOS.get(repo)
            if not entry or Path(entry["rootPath"]).resolve() != Path(manifest["root"]):
                return ChannelResult("missing_repository", [], "Repository is missing or not registered for retrieval")
            request = api.SearchRequest(repoId=repo, query=query, limit=min(limit, 25), filters=filters)
            # Match the baseline's internal candidate overfetch, which is not an
            # external SearchRequest limit and uses model_copy in the current API.
            request = request.model_copy(update={"limit": limit})
            if provider == "semantic":
                try:
                    client = api.get_qdrant_client()
                    if not client.collection_exists(collection):
                        return ChannelResult("unavailable", [], "Pinned semantic index is unavailable")
                    database = Path(view["data_dir"]) / repo / "metadata.db"
                    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                        expected = db.execute("SELECT count(*) FROM code_chunks WHERE repository_id=?", (repo,)).fetchone()[0]
                    scope = api.Filter(must=[api.FieldCondition(key="repo_id", match=api.MatchValue(value=repo))])
                    actual = client.count(collection, exact=True, count_filter=scope).count
                    if actual < expected:
                        return ChannelResult("unavailable", [], "Pinned semantic index has missing vectors")
                    if actual > expected:
                        return ChannelResult("stale", [], "Vector membership differs from pinned metadata")
                    raw = api._semantic_search(request, data_dir=str(view["data_dir"]), collection=collection)
                except Exception as error:
                    return ChannelResult("unavailable", [], f"Semantic channel unavailable ({type(error).__name__})")
                if raw["mode"] == "unavailable":
                    return ChannelResult("unavailable", [], raw.get("warning"))
                warning = raw.get("warning")
                if warning and "Some indexed files or metadata changed" in warning:
                    return ChannelResult("stale", [], warning)
                # A healthy collection with no matching filtered rows is empty,
                # not an unavailable index. The engine checks canonical metadata.
                if not raw["results"] and warning and "No semantic evidence" in warning:
                    warning = "No semantic evidence matched the query filters"
            else:
                raw = api._keyword_search(request, data_dir=str(view["data_dir"]), collection=collection)
                warning = raw.get("warning")
                if warning and "Some keyword evidence changed" in warning:
                    return ChannelResult("stale", [], warning)
                if warning and "Keyword index is unavailable" in warning:
                    return ChannelResult("unavailable", [], warning)
            return ChannelResult("available", raw["results"], warning)
        return RetrievalChannels(
            semantic_search=lambda query, filters, limit: call("semantic", query, filters, limit),
            keyword_search=lambda query, filters, limit: call("keyword", query, filters, limit),
            embedding_model=embedding_model,
        )
    return factory


class SemanticSearchEngine:
    """Use inside pinned(store, channels_factory); each result uses that generation."""

    def __init__(self, store, view, context, channels):
        self.store, self.context, self.channels = store, context, channels
        self.view = view
        self.collection = view["manifest"]["collection"]
        self.database = Path(view["data_dir"]) / context.repository_id / "metadata.db"
        self.retriever = HybridRetriever(
            store.root, context.repository_id, view["data_dir"],
            exact_search=lambda *args: [], keyword_search=None, semantic_search=None,
        )
        self.evidence: dict[str, Evidence] = {}
        self._active = False

    @classmethod
    @contextmanager
    def pinned(cls, store, channels_factory):
        with store.pin_view() as view:
            context = SnapshotContext.from_pinned_view(view, store.repo_id)
            engine = cls(store, view, context, channels_factory(view))
            engine._active = True
            try:
                yield engine
            finally:
                engine._active = False

    def _index_health(self):
        if not self.database.is_file():
            raise StaleIndexError("Pinned metadata is missing")
        with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("""SELECT DISTINCT c.collection_name,x.embedding_model,s.state
                FROM code_chunks c LEFT JOIN chunk_context x ON x.id=c.id
                LEFT JOIN index_snapshots s ON s.id=c.snapshot_id AND s.repository_id=c.repository_id
                WHERE c.repository_id=?""", (self.context.repository_id,)).fetchall()
            if any(collection != self.collection or model is None or state != "ready"
                   for collection, model, state in rows):
                raise StaleIndexError("Chunk metadata is incomplete, unready, or from another collection")
            keyword_ready = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='chunk_fts'").fetchone())
        models = {row[1] for row in rows}
        return keyword_ready, not models or models == {self.channels.embedding_model}

    async def execute(self, operation: OperationRequest, context: SnapshotContext) -> EngineResult:
        started = monotonic()
        def failure(status, code, message):
            return EngineResult(engine="semantic", status=status, repository_id=context.repository_id,
                                snapshot_id=context.snapshot_id, execution_ms=0,
                                error=EngineError(code=code, message=message))
        if not self._active or context != self.context or operation.snapshot_id != context.snapshot_id:
            return failure("stale_index", "stale_index", "Search is outside its pinned generation")
        if operation.repository_id != context.repository_id:
            return failure("error", "invalid_operation", "Requested repository is missing or outside the pinned scope")
        spec = SEMANTIC_OPERATIONS.get(operation.name)
        if spec is None or operation.engine != "semantic":
            return failure("unsupported", "invalid_operation", "This retrieval operation is unsupported")
        try:
            parameters = spec.validate(operation, context)
        except ValueError as error:
            return failure("error", "invalid_operation", str(error))
        mode = operation.name.removesuffix("_search")
        deadline = started + spec.max_execution_ms / 1000
        try:
            if not self.store.matches(context.snapshot_id):
                raise StaleIndexError("Pinned saved-source evidence changed")
            keyword_ready, model_matches = self._index_health()
            plan = plan_query(parameters.query)
            budget = min(100, parameters.limit * 3)
            channels = {}
            def retrieve(method):
                if monotonic() >= deadline:
                    raise TimeoutError()
                if method == "semantic" and not model_matches:
                    return ChannelResult("unavailable", [], "Query embedding model differs from stored embeddings; semantic search disabled")
                if method == "keyword" and not keyword_ready:
                    return ChannelResult("unavailable", [], "Keyword index is unavailable")
                query = plan.query if method == "semantic" else plan.keywords
                if not query:
                    return ChannelResult("available", [])
                callback = getattr(self.channels, method + "_search")
                try:
                    return callback(query, parameters.filters, budget)
                except (OSError, RuntimeError, sqlite3.Error) as error:
                    return ChannelResult("unavailable", [], f"{method} channel unavailable ({type(error).__name__})")
            if mode != "semantic":
                channels["keyword"] = retrieve("keyword")
            if mode != "keyword":
                channels["semantic"] = retrieve("semantic")
            if any(channel.status == "missing_repository" for channel in channels.values()):
                return failure("error", "unavailable", "Repository is missing or not registered for retrieval")
            if mode == "semantic" and channels["semantic"].status == "unavailable":
                channels["keyword"] = retrieve("keyword")
            if any(channel.status == "stale" for channel in channels.values()):
                raise StaleIndexError("Retrieval channel rejected stale evidence")
            unavailable = [method for method, channel in channels.items() if channel.status == "unavailable"]
            rankings = {method: channel.results for method, channel in channels.items() if channel.status == "available"}
            for items in rankings.values():
                for item in items:
                    if not self.retriever._verify(item):
                        raise StaleIndexError("Retrieval channel returned invalid or mismatched evidence")
                    info = trace(self.database, context.repository_id, item["entityId"])
                    if not matches_filters({"file_path": info["path"], "language": info.get("language"),
                                            "module": info.get("module"), "symbol_kind": info.get("symbol_kind")},
                                           parameters.filters):
                        raise StaleIndexError("Retrieval channel ignored a canonical metadata filter")
            self.retriever.warnings = []
            # Existing finalization owns RRF, duplicate elimination, contextual
            # ordering, and independent provenance checks. No routing/expansion.
            retrieval = self.retriever._finish(plan, rankings, parameters.limit)
            if self.retriever.warnings:
                raise StaleIndexError("Evidence changed during rank fusion")
            findings, evidence = [], {}
            raw_scores = {method: {evidence_key(item): item for item in items}
                          for method, items in rankings.items()}
            for rank, item in enumerate(retrieval["results"], 1):
                info = trace(self.database, context.repository_id, item["entityId"])
                if any(info.get(field) != item.get(field) for field in (
                    "path", "sourceHash", "snapshotId", "fileId", "symbolId", "startLine", "endLine")):
                    raise StaleIndexError("Canonical evidence changed after ranking")
                source = Evidence.from_trace(info, context, evidence_type="code_chunk", resolution="not_applicable")
                evidence[source.evidence_id] = source
                key = evidence_key(item)
                semantic = raw_scores.get("semantic", {}).get(key, {})
                keyword = raw_scores.get("keyword", {}).get(key, {})
                findings.append(SemanticFinding(
                    chunk_id=source.evidence_id, rank=rank,
                    name=info.get("qualified_name") or info["path"],
                    retrieval_score=semantic.get("confidence"), keyword_score=keyword.get("keywordScore"),
                    fusion_score=item["fusionScore"], retrieval_methods=item["retrievalMethods"],
                    ranking_signals=item["rankingSignals"], evidence_ids=[source.evidence_id],
                ))
            if not self.store.matches(context.snapshot_id):
                raise StaleIndexError("Saved sources changed during retrieval")
            for identifier in evidence:
                info = trace(self.database, context.repository_id, identifier)
                if Evidence.from_trace(info, context, evidence_type="code_chunk", resolution="not_applicable") != evidence[identifier]:
                    raise StaleIndexError("Canonical evidence changed during final verification")
            if monotonic() >= deadline:
                raise TimeoutError()
            if not findings and unavailable:
                return failure("error", "unavailable", "; ".join(
                    channels[method].warning or f"{method} unavailable" for method in unavailable))
            warnings = [channel.warning for channel in channels.values() if channel.warning]
            if unavailable and findings:
                warnings.append("Partial retrieval: " + ", ".join(unavailable) + " unavailable; returning verified " +
                                ", ".join(method for method, channel in channels.items() if channel.results) + " evidence")
            result = EngineResult(engine="semantic", status="partial" if unavailable else "success" if findings else "not_found",
                                  repository_id=context.repository_id, snapshot_id=context.snapshot_id,
                                  findings=findings, evidence_ids=list(evidence), warnings=warnings,
                                  execution_ms=int((monotonic() - started) * 1000))
            validate_engine_result(result, context, evidence, spec)
            self.evidence.update(evidence)
            return result
        except TimeoutError:
            return failure("partial", "budget_exceeded", "Retrieval exceeded its execution budget")
        except (StaleIndexError, ContractError, ValueError, KeyError):
            return failure("stale_index", "stale_index", "Retrieval evidence is stale, invalid, or mismatched")
        except (sqlite3.Error, OSError):
            return failure("error", "unavailable", "Pinned retrieval metadata is unavailable")
