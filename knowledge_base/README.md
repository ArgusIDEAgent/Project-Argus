# Knowledge base: Phases 1–4

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

The private database is stored at `~/.codemind/data/<repository-id>/metadata.db` (or under `CODEMIND_DATA_DIR` / `--data-dir`), outside the scanned repository. It stores repository metadata, one row per observed file or pruned directory, and scan history. Each file row includes a repository-relative path, kind, language, status and reason, size, content SHA-256 for included files, and parsing status. Source contents are not stored. A successful rescan upserts file rows in one transaction, retaining stable IDs and unchanged evidence while removing deleted paths.

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

The normal launcher remains `python3 start_codemind.py`; restart it once after upgrading, then select/refresh the repository in CodeMind. There is no need to run all phase commands before every chat. The current local Qdrant store is retained; FAISS/new chunking from Phase 5 are not part of this change.

Inspect metadata from the project root using the virtual environment:

```sh
.venv/bin/python -m knowledge_base.metadata --repo "/absolute/path/to/repository"
.venv/bin/python -m knowledge_base.metadata --repo "/absolute/path/to/repository" --trace "ENTITY_ID"
.venv/bin/python -m unittest discover -s ai-engine/tests -v
```

These metadata commands are read-only. The report includes row counts, schema version and dangling-reference checks. `--trace` accepts a graph node, edge, symbol, or semantic chunk ID and checks its snapshot state, canonical file path, current source hash and source bounds. Semantic results and backend-generated citations expose the same `entityId`, `fileId`, optional `symbolId`, `snapshotId`, source origin, and schema version. Legacy CLI vector retrieval uses the same validation; unverifiable evidence is skipped with a refresh warning.
