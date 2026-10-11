# Knowledge base: Phases 1–3

Run from the project root:

```sh
python3 -m knowledge_base.scanner --repo /absolute/path/to/local/git/repository
python3 -m knowledge_base.scanner --repo /absolute/path/to/local/git/repository --explain src/example.ts
python3 -m knowledge_base.parser --repo /absolute/path/to/local/git/repository
npm --prefix extension install
python3 -m knowledge_base.graph --repo /absolute/path/to/local/git/repository
python3 -m unittest knowledge_base.test_scanner knowledge_base.test_parser knowledge_base.test_graph -v
```

The Python entry point is `knowledge_base.scanner.scan_repository(...)`. It returns the repository ID, Git revision, SQLite location, scan time, and coverage counts. Optional `--exclude` globs and `--max-file-bytes` override the default one-megabyte limit.

The private database is stored at `~/.codemind/data/<repository-id>/metadata.db` (or under `CODEMIND_DATA_DIR` / `--data-dir`), outside the scanned repository. It stores repository metadata, one row per observed file or pruned directory, and scan history. Each file row includes a repository-relative path, kind, language, status and reason, size, content SHA-256 for included files, and parsing status. Source contents are not stored. A successful rescan replaces that repository's file rows in one transaction, so deleted paths disappear.

This phase recognizes JavaScript/TypeScript source files and common JS/TS project manifests. Other file types are reported as unsupported. Git's own ignore matcher handles nested `.gitignore` rules and negations for Git repositories. Sensitive filenames, symlinks, generated directories, binary files, oversized files, and high-confidence embedded secret markers are excluded separately. Ignored directories are counted once; their descendants are intentionally not enumerated. A non-Git directory can be scanned only if it contains no `.gitignore` rules to evaluate.

The Phase 2 command refreshes the inventory, then parses all included JS/JSX/TS/TSX source and parseable JS/TS config files with Tree-sitter. It writes `symbols` (definitions), `syntax_references` (imports, exports, calls, construction, inheritance, decorators), `syntax_comments`, and `parse_diagnostics` tables. Parameters, return annotations, modifiers, export flags, parent symbol IDs, and qualified names are stored with exact byte offsets and 1-based Unicode line/column spans (end-exclusive). A file is marked `parsed`, `partial` for syntax errors, or `error` for read/hash failures. Files removed from the inventory lose their syntax rows on the next scan.

IDs are derived from repository, relative path, enclosing symbol, kind, name, overload signature, and duplicate ordinal, so line shifts and body edits do not change ordinary symbol IDs. Anonymous or indistinguishable duplicate declarations may change IDs when earlier siblings are inserted; stronger cross-edit matching belongs to incremental indexing. The parser's references remain unresolved syntax until Phase 3. This knowledge-base pipeline does not change the existing `ai-engine/indexer.py` or VS Code extension indexing behavior.

## Phase 3 semantic graph

`python3 -m knowledge_base.graph --repo ...` refreshes the inventory and parser, then runs the TypeScript 5.9 compiler/type checker against **only** included source files. It uses the root `tsconfig.json` (including in-repository `extends` files in the inventory) for path aliases and module resolution. The Node bridge uses the TypeScript installation under `extension/node_modules`; install the extension's npm dependencies first. It does not read excluded source files or installed package implementations. Local ES imports, re-exports, dynamic imports with literal paths, and literal CommonJS `require` calls can produce module edges.

The graph is persisted in `graph_nodes`, `graph_relationships`, `graph_unresolved`, and `graph_snapshots` in the same private SQLite database. Nodes include repositories, directories, files, modules, symbols, and external package specifiers. Edges include `CONTAINS`, `DEFINES`, `IMPORTS`, `EXPORTS`, `CALLS`, `REFERENCES`, `EXTENDS`, `IMPLEMENTS`, and `DEPENDS_ON`, with evidence file, line, byte range, evidence type, and explicit `resolved` or `inferred` status. Missing or ambiguous targets are recorded with `resolution='unresolved'` and a reason; they are never promoted to resolved edges. External package edges are inferred from exact import specifiers, not proof that the package is installed. `CALLS` denotes a TypeScript-checker-bound declaration, not proof of which override executes at runtime.

`KnowledgeGraph(database, repository_id)` provides `symbol`, `direct_callers`, `direct_callees`, `imports`, `reverse_dependencies`, `dependency_paths`, and `unresolved_references`. Query operations reject snapshots that predate a scan or whose included on-disk file hashes have changed. Dependency paths use resolved edges only and bound depth, path count, and search work. Python, excluded dependencies, nonliteral dynamic imports, and targets without a unique indexed declaration remain unresolved. Nested independent TypeScript project configurations are not yet supported. Embeddings, retrieval integration, and continuous synchronization remain later phases.
