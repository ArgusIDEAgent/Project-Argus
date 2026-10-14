# Legacy RAG compatibility

The supported application launch is `python3 start_codemind.py` from the project
root, which uses root `.venv`. Normal VS Code chat uses authenticated `/ask`
and `answers.answer_question` with local Ollama. Development hybrid chat, still
default-off, uses `/v1/hybrid/*`, existing query-system orchestration, one pinned
snapshot, evidence validation and the local Ollama bridge. Neither answering
route imports or dispatches `rag_pipeline.ask_codemind`.

| Entry point | State and dependencies |
| --- | --- |
| `rag_pipeline.py` CLI → `main()` → `ask_codemind()` | Retained legacy answering interface. Reads optional per-repo `graph.json` using NetworkX and graph_builder helpers, retrieves verified chunks, calls historical LiteLLM provider configuration. Deprecated for current snapshot-managed repositories: returns a redirect/refusal before retrieval or model invocation. |
| Direct `rag_pipeline.ask_codemind()` import | Same legacy contract, not a supported way to answer from managed snapshots. Phase 1 detector tuple and identifier behavior remain covered by tests. |
| `graph_query.py` CLI → graph_builder | Retained read-only Python/NetworkX graph inspection; no answer model. It does not read the current JS/TS SQLite graph or establish pinned-generation authority. |
| `indexer.py --dir ...` (legacy default `build_graph=True`) | Retained standalone semantic indexing and optional Python NetworkX graph update. It does not atomically publish a modern multi-stage generation. Use normal registered-repository API indexing for that workflow. |
| `/repos/{id}/index` and `/refresh` | SnapshotStore stages modern metadata/graph/vector data, checks failures and atomically selects a ready generation. Its indexer call explicitly uses `build_graph=False`; NetworkX state is not merged. |
| `search.py` CLI | Retained search path that resolves the current managed collection and rejects stale snapshots. No answer model. |
| `manual/legacy_openrouter_smoke.py` | Manual-only fixed AST question; explicit `--allow-external-llm` gate before credentials/provider import. Not automated regression. |

The legacy model variable `CODEMIND_LLM_MODEL` still defaults historically to
`openrouter/cohere/north-mini-code:free`; this is distinct from
`CODEMIND_LOCAL_MODEL` used by the application. No remote legacy request is
authorized by the local-first test workflow. Managed-snapshot refusal, invalid
snapshot refusal and independent modern API operation are regression-tested
without calling a provider. No legacy entry point, command or graph format was
removed, merged or migrated. Retiring these CLIs needs a separate scope decision.
