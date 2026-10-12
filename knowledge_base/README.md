# Knowledge base: Phases 1–7

Run from the project root:

```sh
python3 -m knowledge_base.scanner --repo /absolute/path/to/local/git/repository
python3 -m knowledge_base.scanner --repo /absolute/path/to/local/git/repository --explain src/example.ts
python3 -m knowledge_base.parser --repo /absolute/path/to/local/git/repository
npm --prefix extension install
python3 -m knowledge_base.graph --repo /absolute/path/to/local/git/repository
.venv/bin/python -m unittest knowledge_base.test_scanner knowledge_base.test_parser knowledge_base.test_graph knowledge_base.test_metadata -v
```

The Python entry point is `knowledge_base.scanner.scan_repository(...)`. It returns the repository ID, Git revision, SQLite location, scan time, and coverage counts. Optional `--exclude` globs and `--max-file-bytes` override the default one-megabyte limit.

Standalone phase commands store their private database at `~/.codemind/data/<repository-id>/metadata.db` (or under `CODEMIND_DATA_DIR` / `--data-dir`), outside the scanned repository. Phase 7's engine uses immutable generation databases described below; use the metadata inspection command to locate its active database. The inventory stores repository metadata, one row per observed file or pruned directory, and scan history. Each file row includes a repository-relative path, kind, language, status and reason, size, content SHA-256 for included files, and parsing status. The scanner itself stores no source contents; Phase 5 stores redacted eligible chunks locally for keyword retrieval. A successful rescan upserts file rows in one transaction, retaining stable IDs and unchanged evidence while removing deleted paths.

This phase recognizes JavaScript/TypeScript source files and common JS/TS project manifests. Other file types are reported as unsupported. Git's own ignore matcher handles nested `.gitignore` rules and negations for Git repositories. Sensitive filenames, symlinks, generated directories, binary files, oversized files, and high-confidence embedded secret markers are excluded separately. Ignored directories are counted once; their descendants are intentionally not enumerated. A non-Git directory can be scanned only if it contains no `.gitignore` rules to evaluate.

The Phase 2 command refreshes the inventory, then parses all included JS/JSX/TS/TSX source and parseable JS/TS config files with Tree-sitter. It writes `symbols` (definitions), `syntax_references` (imports, exports, calls, construction, inheritance, decorators), `syntax_comments`, and `parse_diagnostics` tables. Parameters, return annotations, modifiers, export flags, parent symbol IDs, and qualified names are stored with exact byte offsets and 1-based Unicode line/column spans (end-exclusive). A file is marked `parsed`, `partial` for syntax errors, or `error` for read/hash failures. Files removed from the inventory lose their syntax rows on the next scan.

IDs are derived from repository, relative path, enclosing symbol, kind, name, overload signature, and duplicate ordinal, so line shifts and body edits do not change ordinary symbol IDs. Anonymous or indistinguishable duplicate declarations may change IDs when earlier siblings are inserted; stronger cross-edit matching belongs to incremental indexing. The parser's references remain unresolved syntax until Phase 3. Phase 4 links the existing vector indexer to these identities; VS Code's chat interface remains compatible.

## Phase 3 semantic graph

`python3 -m knowledge_base.graph --repo ...` refreshes the inventory and parser, then runs the TypeScript 5.9 compiler/type checker against **only** included source files. It uses the root `tsconfig.json` (including in-repository `extends` files in the inventory) for path aliases and module resolution. The Node bridge uses the TypeScript installation under `extension/node_modules`; install the extension's npm dependencies first. It does not read excluded source files or installed package implementations. Local ES imports, re-exports, dynamic imports with literal paths, and literal CommonJS `require` calls can produce module edges.

The graph is persisted in `graph_nodes`, `graph_relationships`, `graph_unresolved`, and `graph_snapshots` in the same private SQLite database. Nodes include repositories, directories, files, modules, symbols, and external package specifiers. Edges include `CONTAINS`, `DEFINES`, `IMPORTS`, `EXPORTS`, `CALLS`, `REFERENCES`, `EXTENDS`, `IMPLEMENTS`, and `DEPENDS_ON`, with evidence file, line, byte range, evidence type, and explicit `resolved` or `inferred` status. Missing or ambiguous targets are recorded with `resolution='unresolved'` and a reason; they are never promoted to resolved edges. External package edges are inferred from exact import specifiers, not proof that the package is installed. `CALLS` denotes a TypeScript-checker-bound declaration, not proof of which override executes at runtime.

`KnowledgeGraph(database, repository_id)` provides `symbol`, `direct_callers`, `direct_callees`, `imports`, `reverse_dependencies`, `dependency_paths`, and `unresolved_references`. Query operations reject snapshots that predate a scan or whose included on-disk file hashes have changed. Dependency paths use resolved edges only and bound depth, path count, and search work. Python, excluded dependencies, nonliteral dynamic imports, and targets without a unique indexed declaration remain unresolved. Nested independent TypeScript project configurations are not yet supported. The API already uses local vector retrieval and graph enrichment; new code-aware chunking and continuous synchronization remain later phases.

## Phase 4 unified metadata

SQLite schema version **5** connects `repositories`, `files`, `symbols`, `symbol_occurrences`, `relationships`, `code_chunks`, `index_snapshots`, and `index_errors`. `symbol_sources` and `graph_node_sources` provide provenance bindings while keeping existing syntax and graph tables compatible. Existing v2–v4 metadata migrates idempotently; file, symbol, graph, and Qdrant point IDs are preserved. Newer schemas are rejected rather than downgraded. Deleted/excluded source evidence is removed; foreign keys and the audit detect dangling or cross-repository links.

The shared identity module normalizes repository roots and rejects non-normalized or escaping paths. A file ID is also its File graph-node ID. A semantic chunk ID is its actual Qdrant point/embedding UUID, not an unrelated API-generated hash. Each chunk stores its file ID, optional verified symbol ID, source hash, snippet hash, line range, embedding collection, and observation snapshot. Symbol association requires matching source hashes, containment, and a uniquely most-specific declaration; multi-symbol, ambiguous, or redacted unmatched chunks retain a null symbol ID. Ordinary symbols retain their IDs after line shifts/body edits; renames, moves, and ambiguous duplicate matching are not inferred.

Snapshots record stage, schema version, Git revision, observation/completion times, and `building`, `ready`, or `failed` state. Indexed positions refer to **saved disk content** (`sourceOrigin=disk`), never unsaved editor buffers. Live lexical matches explicitly use `current_disk` and no snapshot. Syntax and graph byte offsets are end-exclusive; vector chunks use enclosing line ranges because redaction/newline normalization can change byte offsets. Structural Repository/Directory/External nodes have snapshot provenance but no invented file location.

Graph rows and canonical relationships publish in the same SQLite transaction. SQLite and Qdrant cannot share one atomic transaction: a vector run publishes a ready snapshot only after its writes complete. Failed or interrupted snapshots and mismatched payloads are rejected by retrieval. Missing chunk metadata forces reindexing even when the saved hash state matches. The first refresh after this upgrade re-embeds old vectors once to populate the new payload fields. Subsequent runs remain hash-incremental. JS/TS ignore/security rules also constrain semantic discovery; eligible Python/Markdown and other existing vector languages still work without claiming JS/TS symbol extraction.

The normal launcher remains `python3 start_codemind.py`; restart it once after upgrading, then select/refresh the repository in CodeMind. There is no need to run all phase commands before every chat. The Phase 4 release retained local Qdrant; Phase 5 below changes the default backend without deleting that store.

Inspect metadata from the project root using the virtual environment:

```sh
.venv/bin/python -m knowledge_base.metadata --repo "/absolute/path/to/repository"
.venv/bin/python -m knowledge_base.metadata --repo "/absolute/path/to/repository" --trace "ENTITY_ID"
.venv/bin/python -m unittest discover -s ai-engine/tests -v
```

These metadata commands are read-only. The report includes row counts, schema version and dangling-reference checks. `--trace` accepts a graph node, edge, symbol, or semantic chunk ID and checks its snapshot state, canonical file path, current source hash and source bounds. Semantic results and backend-generated citations expose the same `entityId`, `fileId`, optional `symbolId`, `snapshotId`, source origin, and schema version. Legacy CLI vector retrieval uses the same validation; unverifiable evidence is skipped with a refresh warning.

## Phase 5 semantic knowledge representation

Schema **6** adds `chunk_context` and an external-content SQLite FTS5 index, with
transactional update/delete triggers. The upgrade keeps canonical file/symbol/
graph IDs; the first refresh replaces old ordinal-based chunk IDs with semantic
unit UUIDs. Ordinary function chunks keep their IDs across line shifts and body
edits. Child ordinal changes, renames and moves are not cross-edit identity claims.

JS/TS chunks use Tree-sitter declarations; Python uses AST function/class/method
boundaries without inventing resolved Python graph symbols. Small classes and
methods are searchable units, while large functions have bounded overlapping
children retaining their parent symbol key. Imports and executable module-level
code remain searchable. Markdown headings and configuration sections are separate
units; unsupported or invalid syntax uses a labelled text fallback. Function-attached
comments enrich the function rather than appearing as competing standalone hits.
Each chunk carries its path, language, module, qualified name, kind, signature,
comments, redacted source and exact saved-source line range.

The default backend is local FAISS normalized inner-product (cosine) search,
persisted under `~/.codemind/data/faiss`. Its SQLite journal durably maps a stable
int64 FAISS vector ID to the chunk UUID, which connects to canonical file/symbol
metadata and a source range. Cache writes are atomic; missing/corrupted caches
rebuild from the journal. Cached FAISS files are hash-checked before loading.
Explicit `QDRANT_URL` remains an opt-in compatibility path; the old embedded
Qdrant directory is not deleted. Neither vectors nor FTS rows create graph edges.
See the primary [FAISS cosine documentation](https://github.com/facebookresearch/faiss/wiki/MetricType-and-distances),
[FAISS index-loading precautions](https://github.com/facebookresearch/faiss/wiki/Index-IO,-cloning-and-hyper-parameter-tuning)
and [SQLite FTS5 documentation](https://www.sqlite.org/fts5.html).

FTS5 BM25 supports indexed identifiers, package names, error messages, routes and
configuration terms. Search rejects stale source hashes and failed snapshots on
both keyword and vector paths. If embedding queries fail, available keyword
evidence is labelled explicitly instead of being presented as semantic retrieval.
`POST /search/code` and `/ask` accept optional `retrieval` (`auto`, `semantic`,
`keyword`) and exact metadata filters:

```json
{
  "repoId": "REGISTERED_REPO_ID",
  "query": "Where are resource permissions checked?",
  "retrieval": "semantic",
  "filters": {
    "files": ["src/auth.ts"],
    "modules": ["src/auth"],
    "languages": ["typescript"],
    "symbolTypes": ["function"]
  }
}
```

Filters combine with AND; values within one field combine with OR. Modules are
repository-relative paths without their final extension, not guessed namespaces.
Paths cannot escape the repository. Existing editor requests require no changes;
normal Send still uses the backend's validated citations and local answer model.
Phase 5 provides the separate retrieval representations; Phase 6 below combines
them. Similarity means relevance, not verified program behavior.

The retained local CPU model is `BAAI/bge-small-en-v1.5` (384 dimensions).
`.venv/bin/python -m knowledge_base.benchmark_semantic` benchmarks real embeddings
against 12 curated natural-language questions over JavaScript, TypeScript and
Python. Both raw syntax units and contextual units retrieved the expected function
first in 12/12 cases after eliminating duplicate attached-comment hits. This small
smoke benchmark is not a general accuracy guarantee or a comparison against all
code embedding models. `--model NAME` can evaluate another supported FastEmbed
candidate without changing production indexes. It never calls an answer LLM.

Upgrade/run/check from the project root:

```sh
.venv/bin/python -m pip install -r requirements.txt
python3 start_codemind.py
# Select/refresh the repository once in CodeMind; subsequent indexing is incremental.
.venv/bin/python -m unittest knowledge_base.test_semantic -v
.venv/bin/python -m unittest discover -s ai-engine/tests -v
.venv/bin/python -m knowledge_base.benchmark_semantic
```

## Phase 6 hybrid information retrieval

`knowledge_base.retrieval.HybridRetriever` is the unified local retrieval interface.
Authenticated `POST /retrieve` and the compatible `POST /search/code` share it;
`/ask` uses the same evidence before generating an optional local-model answer.
Retrieval itself never calls an answer LLM. Existing camel-case fields and source
citations remain compatible with the editor. Legacy CLI vector/RAG commands remain
separate; they are not claimed to use this new hybrid interface.

The deterministic planner normalizes queries and detects explicit symbols,
filenames, paths and module names. Exact definitions and copied source text take
precedence over broader retrieval. Explicit files/modules are resolved by source
path; missing files do not fall through to unrelated vectors. Duplicate definitions
or basenames return `status: ambiguous` and candidates rather than silently choosing
one. Use a qualified name or repository-relative path; ordinary exact lookup also
supports file/language filters. `/ask` returns clarification evidence for ambiguous
results without asking a model to choose a definition.

Exploratory questions combine FTS5 BM25 keyword ranks and FAISS cosine ranks using
Reciprocal Rank Fusion: each channel contributes `1 / (60 + rank)`. Raw keyword and
cosine scores are never added together. Identical source units contribute once per
channel and are deduplicated across channels. Exact identity takes precedence;
implementation/test/documentation context and requested symbol type influence final
ordering. Graph candidates are ordered by bounded traversal distance. Source
reliability is a verification gate, not a model-generated confidence score. See the
primary [RRF ranking documentation](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking).

Supported graph queries include `who calls validateToken?`, `what does login call?`,
`what breaks if I change validateToken?`, `imports of src/login.ts` and
`dependents of src/auth.ts`. Structural queries never substitute semantic similarity
for a relationship. Impact/dependency traversal uses resolved edges, at most three
hops and 100 examined edges; cycles terminate. Result budgets are bounded, and a
warning indicates traversal truncation. Structural filters constrain returned
evidence, not the target definition; qualify the target in the query if ambiguous.
For explanations/dependency context, at most three selected canonical symbols
expand to up to ten verified callee definitions each. Python syntax-aware units
remain searchable but have no invented resolved Python call graph.

An exact external import may be shown with `resolution: inferred` and its real
specifier range. This is not proof that the package is installed or executed.
Unresolved references appear separately in `uncertainty`, with source provenance;
they are never counted as resolved graph edges. `source_verified` verifies the
evidence location, not the existence of an unresolved target or runtime behavior.

Each channel and the final fused result set are checked against canonical IDs,
ready snapshots, current saved-file hashes and source ranges. Deleted, stale,
unfinished or mismatched evidence is discarded with a refresh warning. A vector
failure falls back to explicitly labelled keyword evidence when available. Healthy
empty search is `not_found`, not an unavailable service. Phase 6 alone does not
provide continuous synchronization; Phase 7 below adds it.

Example request (the same filters and limit contract as `/search/code`):

```json
{"repoId":"REGISTERED_REPO_ID","query":"Where are resource permissions checked?","retrieval":"hybrid","limit":5}
```

Each result adds the plan's standard `symbol_id`, `symbol`, `file`, `start_line`,
`end_line`, `retrieval_method`, `resolution` and `source_verified` fields alongside
the existing editor fields. Syntax-only definitions are labelled `syntax_only`,
non-relationship search evidence `not_applicable`, and verified graph edges
`resolved`. Python canonical `symbol_id` remains null; its chunk `entityId` is real.
`retrievalMethods`, `fusionScore` and `rankingSignals` explain the contributing
channels. `index_version` is the schema version; `index_snapshot` identifies the
actual observation snapshot. Response status is `found`, `not_found` or `ambiguous`.
`mode` identifies the chosen route; per-result channels distinguish contributions.
Structural results identify an actual relationship `entityId` and its source call
or import range; `relatedSymbolId`/`graphContext` identify the related definition
when indexed. They do not relabel a call-site range as the callee's definition.

Phase 6 introduces no schema migration or embedding model change. Restart the
engine once to load this code. Already-ready Phase 5 indexes can be reused without
re-embedding; older indexes still need the Phase 5 refresh described above.

```sh
.venv/bin/python -m unittest knowledge_base.test_retrieval -v
.venv/bin/python -m unittest discover -s ai-engine/tests -v
```

## Phase 7 incremental indexing and continuous synchronization

The normal engine watches every registered repository, including after a restart
and when the chat panel is closed. Native filesystem events are debounced for
0.6 seconds. Independent five-second SHA-256 reconciliation catches missed events,
ignore-rule changes and same-size edits with unchanged modification timestamps.
If native watching fails, hash reconciliation continues with an explicit warning;
failed indexing retries with bounded backoff. Observation retains the inventory's
ignore/path protections and bounds unsupported text reads to one megabyte; large,
binary and non-regular assets are not read as source text. Only **saved disk files**
are supported, not unsaved editor buffers.

`SnapshotStore.publish` builds a private candidate from the previous SQLite
database and repository-scoped vectors. Unchanged parsed files reuse their syntax
rows. Changed files are reparsed, and resolved reverse dependencies identify
affected importers transitively. The TypeScript checker conservatively re-resolves
**all included JS/TS files**: an unchanged caller is rechecked when exports,
signatures, import aliases or configuration change. This is deliberately not a
fine-grained incremental compiler. Rename/move is delete plus add; identities are
not guessed across paths. Deleted/excluded entities, relationships, keyword chunks
and vectors are removed from the new generation.

Only changed embedding inputs are sent to the model. Unchanged units within an
edited file reuse their vectors while refreshing source hashes, ranges and
payloads. No-op refreshes create no generation and compute no embeddings; deletion
does not require loading the embedding model. Snapshot isolation currently copies
unchanged vector rows into a new collection, so it avoids re-embedding, not all
storage/copy work. Graph, metadata and vector membership/provenance are validated,
then the complete repository fingerprint is checked again before publication.

One atomic `active_snapshot.json` pointer selects the complete generation only
after the related components are ready. Its database is at:

```text
<data-dir>/<repository-id>/generations/<generation-id>/<repository-id>/metadata.db
```

The vector collection is private to that repository and generation. Ready
per-stage snapshot IDs remain valid inside this coherent generation;
`knowledgeSnapshotId` is the outer generation ID, not a replacement for each
result's provenance `snapshotId`. Current and previous generations are retained;
older private generations/collections are reclaimed unless pinned by a reader.
Legacy databases/shared collections and repository source files are not deleted.

Search, graph and metadata readers pin one generation. Whole-repository hash
checks before/after retrieval block stale graph/keyword/vector evidence, including
changes to files not selected by the query. `/ask` checks the captured repository
fingerprint after answer generation and discards answers/citations if it changed.
Failures retain the previous consistent pointer and report stale/failed status;
they are not reported as completed indexing. Literal/file lookup may still return
explicitly labelled `sourceOrigin: current_disk` evidence while indexes are stale.
There is no silent old-snapshot fallback for semantic/structural answers.

`GET /repos/<id>/live-state` reports `synchronization.snapshotId`, `fresh`, `state`,
`error`, `watching`, `watchError`, `hashAuditSeconds` and `savedFilesOnly`. Retrieval
also returns synchronization status and `knowledgeSnapshotId`. The extension's
repository header shows stale/updating/failed state and the saved-files boundary.
The metadata CLI resolves/pins the active database; standalone scanner/parser/
graph commands remain diagnostics and do not advance the engine's active pointer.
Retained scoped CLI vector search follows the active collection. The old
NetworkX/cloud-RAG CLI refuses managed snapshots; use chat or `/ask` instead.

Install dependencies once, stop the old engine, and start the normal launcher:

```sh
.venv/bin/python -m pip install -r requirements.txt
python3 start_codemind.py
```

The first synchronization upgrades an existing index to a consistent generation.
Afterward, save files normally; no manual phase commands or refresh are needed.
Keep the engine running. `CODEMIND_AUTO_SYNC=0` disables automatic watching for
controlled tests; explicit refresh and source-staleness safeguards still work.
Compile/use the updated extension (or rebuild its VSIX) to see the new header.
No metadata-schema or embedding-model migration is introduced by Phase 7.

Verification includes saved edits, unchanged timestamps, unchanged importers,
rename/move/delete cleanup, ignore changes, native-watcher failure, failed builds,
concurrent source edits during retrieval/answers, snapshot retention and recovery.

```sh
.venv/bin/python -m unittest discover -s ai-engine/tests -p test_phase7.py -v
.venv/bin/python -m knowledge_base.metadata --repo "/absolute/path/to/repository"
```

Primary implementation references: [watchdog observer/events](https://watchdog.readthedocs.io/en/stable/quickstart.html)
and [SQLite online backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
