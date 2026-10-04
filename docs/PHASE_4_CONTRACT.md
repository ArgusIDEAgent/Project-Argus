# Phase 4: Documentation Graph and Synchronization

CodeMind stores generated documentation in its local metadata database. It does not write Markdown into the indexed repository. Phase 4 requires an indexed repository; use **CodeMind: Select Repository** and wait for indexing, then run **CodeMind: Sync Documentation**. **CodeMind: Open Documentation Overview** opens the overview and section navigation in the chat panel. These commands work from any trusted VS Code window after repository selection.

## Generation

The planner reads indexed graph files, symbols, endpoints, entities, and their edges. It produces system overview, module purpose, API flow, data flow, setup, and symbol summary sections. Setup excerpts come only from indexed README files and are checked against their indexed source hash. The local text model receives at most 6 KB of section facts. Configure `codemind.modelUrl` and `codemind.modelName` in VS Code or `CODEMIND_MODEL_URL` and `CODEMIND_MODEL_NAME` for the standalone service. The default is a loopback Ollama server at `http://127.0.0.1:11434` with `llama3.2`. Remote model URLs are rejected. If the model is missing or fails, CodeMind stores deterministic factual Markdown with `generation_reason: model_unavailable`.

Each generated section has `source_commit`, `source_revision`, `generation_reason`, timestamps, a facts hash, and graph evidence. `doc_section` nodes link to indexed entities through `DOCUMENTS` and `GENERATED_FROM`; entities link back through `DOCUMENTED_BY`. Generated content is indexed as a searchable documentation chunk. If local embeddings are available the chunk also has a vector; lexical search works without them.

## Freshness And Overwrite Policy

An indexed refresh marks sections linked to changed or deleted source paths as stale or affected by uncommitted changes. `/docs/sync` compares planned facts and regenerates only changed sections. If a section's facts are unchanged, sync restores its current links without rewriting its content or `updated_at`. Removed routes and symbols lose their live evidence links; their former generated sections stay stale for review. A user-authored section (`generated = 0`) is never overwritten. CodeMind stores a proposed replacement separately in `proposed_markdown` and keeps the original content. `staleCount` counts sections that need sync or review; `affectedCount` counts sections tied to uncommitted code.

Refreshes and documentation syncs are serialized as repository jobs. Sync results and failures are recorded in the local job and audit tables. The extension shows doc status after refresh and offers a Sync Docs button. Opening evidence in VS Code requires Workspace Trust, a path inside the selected repository, and a source hash matching the indexed file.

## Authenticated API

All routes require the local `x-codemind-session` token.

| Route | Purpose |
| --- | --- |
| `GET /docs/overview?repoId=...` | Overview, freshness, commit, and evidence. |
| `GET /docs/section/{id}?repoId=...` | One section with graph links and proposed text if applicable. |
| `GET /docs/sections?repoId=...` | Section list and documentation status for navigation. |
| `GET /docs/stale?repoId=...` | Stale or affected sections with causes and surviving evidence. |
| `POST /docs/sync` | Body: `{ "repoId": "...", "mode": "baseline" }` or `"incremental"`; returns updated IDs, affected IDs, status, overview, and job ID. |

## Known Limits

The planner documents facts the current parser and adapters recognize; dynamic routes and runtime behavior may be absent. Setup is limited to indexed README content. Local model prose should be reviewed against the attached graph evidence before treating it as an operational guarantee. Documentation is not exported to repository files in this phase.
