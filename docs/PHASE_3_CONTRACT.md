# Phase 3: semantic index and reuse detection

Phase 3 connects the existing Git inventory and AST graph to local embeddings, hybrid search, and conservative reuse suggestions. It never modifies indexed source. The default provider is Ollama with `nomic-embed-text`; SQLite remains the graph and vector store. Generative answers, automatic edits, and generated documentation are later phases.

## Setup and IDE workflow

Run Ollama locally and install the embedding model once:

```sh
ollama pull nomic-embed-text
```

If Ollama is not running, start it with `ollama serve`. In VS Code, reload the updated extension, open CodeMind, and select a trusted Git repository. Wait for indexing. Chat messages search the selected repository; greetings still work without a repository. **CodeMind: Search Code** retrieves evidence. **CodeMind: Check for Reusable Code** checks a task description before generating or writing code. New symbols in saved files also trigger reuse checks while the repository panel is open; disable this with `codemind.checkReuseOnSave`.

Results include symbol, source path and line range, explanation, indexed callers and tests. **Open** checks the saved source hash and opens the location. **Use Reference** opens it and inserts a citation into the chat input; it does not copy or rewrite code. **Ignore** records feedback for that query/entity pair. At most three reuse suggestions are shown. Unsaved source is not indexed.

User-level settings `codemind.embeddingUrl` and `codemind.embeddingModel` configure the provider; reload after changing them. Standalone service equivalents are `CODEMIND_EMBEDDING_URL` and `CODEMIND_EMBEDDING_MODEL`. Only loopback HTTP endpoints and locally installed models are supported. The service never downloads a model automatically. Missing/offline models produce an explicit degraded status with exact/lexical search still available.

## Index contract

- Schema version 4 adds `semantic_chunks`, `semantic_embeddings`, `semantic_versions`, `reuse_feedback`, and `semantic_calibration`.
- Functions, classes, methods, components, endpoints, Markdown sections, and up to 50 relevant commit subjects are separate entities. Commit subjects are selected only when their changed paths intersect eligible inventory files. Markdown follows the existing secret/path exclusions.
- Symbol chunks use AST character offsets and exact line ranges. Large symbols remain one entity with an explicitly marked bounded summary, under 1,800 UTF-8 bytes for the default model. Full body hashes still invalidate changes omitted from that summary.
- Vectors join graph entity IDs. Stored metadata contains hashes, names, signatures, token terms, source ranges, parser/chunker identity, and inferred calls; full source bodies are read from the filesystem, not stored in vectors.
- A model identity includes its Ollama digest and embedding-input version. Changed chunks alone are embedded; a model change re-embeds everything. Unchanged vectors are reused. Refresh still validates source files and rewrites SQLite records; it is not an approximate-nearest-neighbor database optimized for huge repositories.
- Index, graph, and semantic records publish in one database save. Renames, deletes, exclusions, and repository removal clean up stale records. Source changes during preparation fail the job without publishing partial records. Model failures publish a coherent lexical index marked `degraded`; subsequent refresh retries embeddings.
- Retrieval normalizes vectors and combines cosine similarity, weighted lexical coverage, exact-name preference, and a bounded graph-neighbor boost. Results are joined against graph metadata, checked against saved source hashes, and tagged with the index revision. Concurrent index replacement causes a retry error.

## Reuse and calibration

`REUSE` requires matching AST body tokens, parameter contract, language, called names, and a self-contained symbol. Conservative free-name detection can miss valid reuse opportunities involving local variables or external dependencies. It avoids claiming equivalence just because vectors are close. `EXTEND` identifies conceptually related code that needs inspection/adaptation. `DISTINCT` means insufficient evidence; these candidates do not become warnings. Similarity scores are ranking signals, not probabilities or a behavioral proof.

Intent-only queries produce `EXTEND` suggestions, since no proposed implementation exists to establish equivalence. Initial similarity thresholds are provisional (`extend: 0.78`, `reuse: 0.88`). Repository-specific labeled queries can calibrate the semantic suggestion gate. Model changes invalidate calibration. The calibration API requires positive and negative labels and selects a threshold reaching at least 90% precision on that set. Its metrics are training-set measurements; use separate held-out examples before claiming broader accuracy.

Only explicit high-confidence symbol comparisons materialize `SIMILAR_TO` edges. A repository refresh conservatively removes these inferred edges; a new comparison recreates them. Lower-confidence matches remain search results. Feedback is retained while the target graph entity remains valid.

## Authenticated APIs

All endpoints require `x-codemind-session`. Source/entity identifiers are scoped to `repoId`.

| Endpoint | JSON body | Response |
| --- | --- | --- |
| `POST /search/code` | `{repoId, query, limit?: 1..20, contextEntityId?}` | Results, revision, mode, source-staleness count, provider warning |
| `POST /analysis/reuse` | `{repoId, query}` or `{repoId, entityId}` | Up to three REUSE/EXTEND results, overall classification, thresholds, queryHash |
| `POST /embeddings/refresh` | `{repoId}` | 202 with jobId; polls through existing `/jobs/{id}`; refresh includes inventory and graph |
| `POST /analysis/reuse/feedback` | `{repoId, entityId, queryHash, decision: "use" or "ignore"}` | Recorded feedback |
| `POST /analysis/reuse/calibrate` | `{repoId, examples: [{query, entityId, label}]}` | Thresholds and calibration-set metrics; 4-30 pairs, labels REUSE/EXTEND/DISTINCT |

`GET /repos/{id}/status` includes semantic state, model digest, revision, counts, timestamp, and any error. Invalid search/calibration requests return 400. Authentication failures return 401. Queries require a completed index. Request bodies retain the service's 8 KiB limit.

## Verification

```sh
cd service
npm test
npm run eval:semantic
npm run eval:semantic -- ..
```

The first command uses deterministic provider fixtures and temporary repositories. The evaluation commands use the real installed model: first against duplicate/search fixtures, then four labeled CodeMind implementation searches. They create temporary metadata, print scores and calibration metrics, and remove the metadata afterward; they do not silently calibrate a user's registered repository. `npm run test:integration` in `extension/` verifies the VS Code host and packaged modules.

Reference: [Ollama embedding API](https://github.com/ollama/ollama/blob/main/docs/api.md) uses batched inputs and explicit truncation control. This implementation disables silent truncation and reports model errors.
