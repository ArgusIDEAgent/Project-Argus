# Runtime verification and dead-code audit

Cleanup follow-up: confirmed unused code has now been removed and the retired
Node source relocated into a frozen benchmark corpus. See [CLEANUP_REPORT.md](CLEANUP_REPORT.md)
for the changes and post-cleanup checks. Findings below describe the pre-cleanup baseline.

Date: 2026-11-20. Baseline: `37e5cf6` on `main`.

## Scope and result

The actual Python launcher, local embedding/vector indexing, search, Ollama answers,
hybrid queries, source authorization, incremental refresh, offline extension tests,
and real VS Code host integration were exercised. Application source was not changed
and no code was deleted. Existing user repositories, registries, and published
indexes were not used as test targets.

Private runtime artifacts and the reproducible smoke script are under
`/tmp/codemind-runtime-audit.DfaxIv/`. The temporary engine was stopped afterward.
The existing cached BGE model and installed local Ollama model were reused.

“Unused” means unused by this checkout's declared entry points and observed source
references. External consumers of importable helpers cannot be ruled out.
An unexecuted test branch alone does not establish dead code.

## Verification ledger

| Check | Result and limits |
|---|---|
| `.venv/bin/python -m pytest -q` | PASS: 545 tests, 782 subtests, 2 warnings, 241.34 seconds |
| `npm --prefix extension test` | PASS: compilation and 48 Node tests |
| `npm --prefix extension run test:integration` | PASS, exit 0: real installed VS Code; Phase 9 preview and Phase 10 authenticated hybrid suites |
| `npm --prefix extension run test:docs` | PASS, exit 0: VS Code commands → Python snapshot → persisted docs → verified editor source |
| JS/TS structural validation | Measured symbols/relationships: 100% precision/recall on synthetic fixtures; overall gate remains partial |
| Actual launcher, private data directory/port | PASS: startup and authenticated handshake; missing token rejected with 401 |
| Real cached BGE + local FAISS | PASS: two initial chunks; semantic search returned two matches |
| Real `/ask` + installed local Ollama | PASS: `answerMode=local-llm`, one citation, no warning |
| Real hybrid caller query | PASS: `find_callers`, status success, source authorization succeeded |
| Real hybrid model explanation | Model success, fallback not used; overall response partial, not a complete-answer quality pass |
| Saved edit + real incremental refresh | PASS: stale detected; indexed one file, embedded one new chunk, reused two chunks |
| TypeScript extra unused checks | Three diagnostics: unused webview argument, extensionPath and dataDir fields; ordinary configured compile passes |
| `vsce ls --no-dependencies` | Ten packaged entries; old Node server excluded |

Python warnings were a Starlette/httpx test-client deprecation and a local-Qdrant
payload-index warning; neither failed a test. `git diff --check` also passed.

Additional commands:

```sh
.venv/bin/python -m knowledge_base.validation --output /tmp/codemind-runtime-audit.DfaxIv/structural --sizes 0 --structural-only --skip-regressions
# In extension/:
./node_modules/.bin/tsc -p . --noEmit --noUnusedLocals --noUnusedParameters
./node_modules/.bin/vsce ls --no-dependencies
# Actual engine smoke:
.venv/bin/python /tmp/codemind-runtime-audit.DfaxIv/smoke.py
```

The structural command separately checked four JavaScript and twelve TypeScript
fixture files. It reports partial because embedding/retrieval/citation/update gates
are unmeasured by that command; regressions were run separately.

The editor integration suites use real VS Code and HTTP with mocked vectors/model
responses in disposable fixtures. The actual-launcher smoke separately used real
BGE embeddings, FAISS and local Ollama. Its source edit was confined to a temporary
fixture and restored afterward. No remote model call or model download was needed.

## A. Confirmed unused implementation

### A1. Obsolete embedded-Qdrant wrapper

Location: `ai-engine/config.py:156-169`, class `_LocalVectorClient`.

Repository-wide symbol search finds only its definition in executable source.
The current factory at `config.py:181-188` returns either a remote `QdrantClient`
or local `FaissClient`; neither constructs this wrapper.

Remove the class in a cleanup change. Keep `_VECTOR_LOCK`, which still protects
singleton client creation.

### A2. Superseded ordinal point-ID helper

Location: `ai-engine/indexer.py:102-109`, function `_point_id`.

Only its definition references the name. Current indexing uses semantic `doc.id`
for vector point IDs (`indexer.py:454-455`) and canonical metadata records
(`indexer.py:448`). The `chunk_id` import at line 64 exists only for this unused helper.

Remove the helper and that import together. Keep `knowledge_base.identity.chunk_id`:
`metadata.py:411` still uses it when semantic contexts are absent.

### A3. Old Qdrant connection constants

`ai-engine/config.py:33-34`: `QDRANT_HOST` and `QDRANT_PORT` have no source consumers.
Current selection uses `QDRANT_URL` or FAISS. Remove these constants, retaining the
optional Qdrant adapter.

### A4. Unused extension constructor state and argument

The TypeScript compiler confirmed:

```text
src/extension.ts(536,26): TS6133 'webview' is never read.
src/serverManager.ts(49,22): TS6138 'extensionPath' is never read.
src/serverManager.ts(50,22): TS6138 'dataDir' is never read.
```

`ServerManager.start()` now selects a loopback port and logs it; it never launches
the old Node service. Token lookup uses the environment or the CodeMind data
directory, not those constructor fields.

Simplify the constructor with all application/test call sites updated. Remove or
explicitly mark the unused `chatHtml` argument as compatibility-only.

### A5. Unused imports

AST load-reference inspection found no loads for:

- `ai-engine/api.py:54`: `IndexingError`, `_discover_files`.
- `knowledge_base/semantic.py:3`: `json`.

Retain `index_directory` and the indexer module itself.

## B. Reproducibly unreachable application branch

### B1. Automatic reuse suggestions cannot pass the graph filter

Consumer: `extension/src/extension.ts:187-190` requires:

```ts
node.type === 'symbol' &&
['function', 'method', 'class', 'component', 'ml_entry'].includes(node.kind)
```

Producer: `ai-engine/api.py:559-562` selects `n.kind AS type` and `n.kind`.
The graph builder at `knowledge_base/graph.py:210-216` emits capitalized kinds
such as `Function`, `Method`, `Class`, `Interface`, and `Type`.

Both fields therefore contain the same capitalized kind. The conjunction cannot
hold under the current API contract. Live reproduction returned `["Function"]`
and the extension's exact filter selected **zero** nodes.

The `server.reuse(...)` and suggestion-display body cannot execute through this
loop with the current producer. The surrounding save listener and refresh flow
are active and necessary. Remove only reuse-specific tracking if that feature
is retired, or repair its contract if retained.

## C. Superseded subsystem: application-inactive, still benchmark input

### C1. Old `extension/server/` Node implementation

Sixteen JavaScript files total **2,719 lines**:

```text
chunker.js                 codeParser.js
docPlanner.js              docStore.js
embeddingProvider.js       frameworkAdapters.js
gitService.js              graphBuilder.js
graphStore.js              modelProvider.js
registry.js                retrievalService.js
reuseAnalyzer.js           semanticCalibration.js
semanticStore.js           server.js
```

Evidence:

1. Extension activation constructs the TypeScript `ServerManager` at
   `extension/src/extension.ts:25`.
2. That manager connects to Python HTTP; it never starts/imports the Node server
   (`serverManager.ts:67-71,165-217`).
3. `.vscodeignore:6` excludes `server/**`; packager inventory confirms exclusion.
4. Git ignores this directory; `git ls-files extension/server` returns no entries.
5. Old dependencies `sql.js` and `web-tree-sitter` both return MODULE_NOT_FOUND
   when resolved from the server directory.
6. `query_system/qualify_phase3.py:40` still uses the directory as a **source corpus**,
   with corresponding benchmark labels. It does not execute it as a server.

| Old responsibility | Current replacement |
|---|---|
| Server and registry | `ai-engine/api.py` |
| Parsing/graph | `knowledge_base/parser.py`, `graph.py`, `semantic_resolver.cjs` |
| Chunking/embedding | `knowledge_base/chunking.py`, `ai-engine/indexer.py`, `config.py` |
| Storage/retrieval | `knowledge_base/vector_store.py`, `retrieval.py`, `semantic.py` |
| Docs | `ai-engine/documentation.py` |
| Reuse | No working modern replacement; current API is a stub |

Archive the old subsystem after freezing/relocating its benchmark corpus and
updating profile paths and relevance labels. Deleting it immediately changes
benchmark inputs. It is inactive in the application, but not unused for every
repository activity.

## D. Reachable placeholders

### D1. Reuse APIs

- `api.py:524-526`: analysis always returns not_implemented and empty results.
- `api.py:529-532`: feedback always returns status ok without persistence.

Both were called over real authenticated HTTP in this audit. They are reachable
stubs. Hide/remove the unsupported feature or implement it after its scope
decision. HTTP feedback success does not mean a decision was stored.

## E. Older paths that remain reachable

| Code | Current caller or entry point |
|---|---|
| `ai-engine/rag_pipeline.py` | Explicit CLI and direct imports; detector and managed-snapshot refusal behavior is tested |
| `ai-engine/graph_builder.py` | Standalone indexer with build_graph=True, graph CLI, and tests |
| `ai-engine/graph_query.py` | Explicit read-only CLI |
| NetworkX branches in indexer | Standalone CLI defaults to build_graph=True; runtime tests exercise it |
| `ai-engine/search.py` | Supported CLI resolves current managed snapshots |
| `ai-engine/answers.py`, /ask | Default extension chat; actual local model answer succeeded |
| `query_system/` | Hybrid flags make it reachable; host and live smoke exercised it |
| `ai-engine/documentation.py` | Docs flag makes it reachable; real editor docs suite passed |
| `extension/out/*.js` | Compiled files are the packaged runtime, not dead duplicate source |

Legacy compatibility boundaries are documented at `ai-engine/LEGACY_RAG.md:3-26`.
The active default /ask implementation has not been replaced merely because the
hybrid route exists.

## F. Design residue

`query_system/contracts.py:439-440` declares a `QueryEngine` typing Protocol.
No current implementation or caller imports/annotates against it; implementations
follow the shape structurally. This is currently interface documentation without
runtime behavior. Use it in factory/executor annotations or remove it after
deciding whether the public interface is retained. It is a low-priority candidate,
not an application defect.

## Recommended cleanup order

1. Remove unused helpers/class/constants/imports; simplify unused arguments.
2. Resolve reuse scope; remove its broken tracking or repair its contract.
3. Freeze/relocate old Node benchmark data and archive the obsolete server.
4. Decide whether legacy Python CLIs remain supported before retiring their stack.
5. Keep /ask until a hybrid default change is approved and qualified.
6. Repeat Python, Node, and real editor suites after each cleanup group.

## Limits

- Generated explanations were not human fact-checked. Hybrid generation succeeded
  but the overall result remained partial.
- Large-corpus/resource qualification, dependency advisory scans, and remote
  Qdrant service validation were not run.
- The optional CODEMIND_QUAL_REPO_ROOT real-source editor cohort requires a prepared
  private full-source snapshot and was not enabled by the ordinary host suite.
- The manual OpenRouter smoke was not run; it is explicitly outside local regression.
- Coverage, vulture and ruff are not installed. Findings use source references,
  AST inspection, TypeScript diagnostics, packaging and runtime reproductions.
- No finite test run proves code is unused under every imaginable external caller.

