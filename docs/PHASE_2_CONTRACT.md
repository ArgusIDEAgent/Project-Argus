# Phase 2 code graph contract

Phase 2 extends each Phase 1 refresh with Tree-sitter parsing and framework extraction. It indexes saved, eligible JavaScript, JSX, TypeScript, TSX, and Python files from the selected repository. The service never edits the source repository. Parser output and graph records live in the extension's local metadata database. This implementation deliberately uses the existing SQLite database rather than the blueprint's proposed Neo4j service, so the installed extension remains self-contained. A later graph backend can preserve these IDs and API shapes.

## Parsing and identity

Every symbol has a deterministic ID scoped to repository and file, plus `kind`, `name`, `qualifiedName`, `signature`, `startLine`, `endLine`, `visibility`, `parentId`, `docstring`, `language`, `path`, and source `revision`. Kinds include function, method, class, interface, type, enum, component, test, and ML entry point. File, endpoint, and entity nodes have their own deterministic IDs. IDs remain stable across line-only moves that do not alter a symbol's signature; changing a signature may change its ID.

The parser records imports and lexical calls. Framework adapters extract HTTP routes and frontend API calls, ORM/schema entities and access, test calls, and ML entry points. The graph uses `CONTAINS`, `IMPORTS`, `CALLS`, `EXPOSES`, `CONSUMES`, `DEFINES`, `TESTS`, `READS_FROM`, `WRITES_TO`, and `USES_MODEL` edges. Each edge includes a confidence score, provenance, source path, line, and revision. A lexical call is not a runtime call graph proof: unresolved or ambiguous calls are omitted rather than guessed. Cross-file import resolution covers repository-relative JS/TS imports and local Python modules; package-manager dependencies are not indexed.

On refresh, the Phase 1 content hash decides which files need parsing. The parser version also invalidates cached facts. The graph writer deletes facts and nodes for removed, renamed, or newly excluded files, upserts changed files, then reconciles cross-file edges. An unchanged refresh has `parsedCount: 0`. Graph records and the inventory are committed together only after the full scan succeeds. A parse with syntax errors is retained with `parseErrors` reported in graph status; inspect such files cautiously.

## API

All endpoints require the Phase 0 `x-codemind-session` header. Start with `GET /repos/{id}/status`; its `graph` object reports `revision`, `indexedAt`, `parsedCount`, `nodeCount`, `edgeCount`, and `parseErrors`. The graph revision identifies the saved-file snapshot, not unsaved editor buffers.

| Method | Path | Result |
|---|---|---|
| `GET` | `/graph/file/{encodedRepoRelativePath}?repoId={id}` | File nodes and edges, plus graph version |
| `GET` | `/graph/symbol/{nodeId}` | Node, incoming/outgoing edges, related nodes, and direct callers/callees, endpoint handlers, component endpoints, tests, and data access |
| `GET` | `/graph/impact/{nodeId}?depth=5` | Bounded incoming dependency traversal and `truncated` flag |
| `POST` | `/graph/path` with `{ "fromId": "...", "toId": "...", "maxDepth": 10 }` | Shortest semantic path between nodes in one repository; empty `nodes` and `edges` mean no path within the bound |

Node IDs are 40 lowercase hexadecimal characters; repository IDs are 24 lowercase hexadecimal characters. `depth` and `maxDepth` must be integers from 1 to 12. Paths are URL-encoded, repository-relative POSIX paths. Invalid requests return 400 and missing graph records return 404. Traversals are cycle-safe and bounded to 500 visited nodes for impact and 1000 for path. Path traversal treats semantic edges as traversable in either direction, so inspect each returned edge's `fromId` and `toId` before interpreting direction. `CONTAINS` and `IMPORTS` are discoverable through file/symbol results but excluded from shortest semantic paths.

## Validation and limits

`service/test/fixtures/phase2` is the golden cross-layer repository: component to API endpoint to backend handler to data function to entity and test. Parser tests cover all three languages, framework facts, exact source lines, and duplicate-route prevention. Integration tests cover graph path, import precision, incremental refresh, rename/delete cleanup, cycles, migration, authentication, and invalid graph requests. Run `npm test` in `service/` and `npm run test:integration` in `extension/`.

This is a source-derived structural graph, not semantic search or a model-backed answer engine. Dynamic dispatch, re-exports, dependency injection, arbitrary aliases, and external packages may not resolve; absent edges are not proof of absent runtime behavior. Phase 3 can consume these graph APIs alongside retrieval and explicitly cite indexed source revisions.
