# Phase 0 local contract

The active runtime is the VS Code extension plus the bundled Node service. The Python `backend/` is an experimental later-phase pipeline and is not started by the extension. The extension can open chat in an empty window, another local Git repository, or Restricted Mode. Repository scanning starts only in a trusted workspace.

## Repository scope

Phase 0 supports local Git working directories and JavaScript, TypeScript, and Python source files. Registration keeps the selected directory as the scan boundary, even when its Git root is a parent directory. The default `RepoConfig` contains `allowedBuildCommands`, `allowedTestCommands`, `excludedPaths`, `secretPatterns`, `supportedLanguages`, and `frameworkHints`. Both command lists default to empty and no registered command is executed in this phase.

The Phase 0 scanner uses `git ls-files` and records path, language, and size for regular files up to 2 MiB. It does not read or store file contents. Phase 1 adds content hashes and Git metadata as described in [the Phase 1 contract](PHASE_1_CONTRACT.md). It excludes `.git`, `node_modules`, `build`, `dist`, `.venv`, `venv`, `__pycache__`, `secrets`, `.secrets`, `.env`, `.env.*`, private-key names, and any configured exclusions. Symlinks are not scanned. A registration or scan never writes to the source repository.

Metadata is stored in `metadata.sqlite` under VS Code's extension global storage directory, alongside dedicated `indexes/` and `worktrees/` directories. When the service runs directly, the default is `~/.codemind`. The `CODEMIND_DATA_DIR` environment variable overrides that location for tests. The Phase 0 schema version was 1; Phase 1 migrates it to version 2. Repository IDs are the first 24 hex characters of SHA-256 over the canonical selected directory. Source content stays in Git. Interrupted jobs are marked failed when the service restarts.

## API

The service listens on an ephemeral `127.0.0.1` port and requires `x-codemind-session` on every request. The extension creates a random token per session and passes it to the child process. Responses are JSON and never cached.

| Method | Path | Request | Response |
|---|---|---|---|
| `GET` | `/health` | None | `status`, `service`, `version`, `schemaVersion` |
| `POST` | `/hello` | `{ "message": "..." }` | `{ "reply": "..." }` |
| `POST` | `/repos/register` | `{ "rootPath": "/absolute/path", "config": {} }` | `{ "repository": { "id", "rootPath", "defaultBranch", "createdAt", "config" } }` |
| `GET` | `/repos` | None | `{ "repositories": [...] }` |
| `GET` | `/repos/{id}/status` | None | `{ "repository", "index", "latestJob" }` |
| `GET` | `/repos/{id}/files` | None | `{ "files": [{ "path", "language", "sizeBytes" }] }` |
| `POST` | `/repos/{id}/index` | None | `202 { "jobId", "state": "queued" }` |
| `GET` | `/jobs/{jobId}` | None | Job state, times, summary result, or error |
| `DELETE` | `/repos/{id}` | None | `{ "removed": true }` |

Job states are `queued`, `running`, `completed`, and `failed`. Audit events are `REPO_REGISTERED`, `INDEX_STARTED`, `INDEX_DONE`, and `INDEX_FAILED`. A completed scan records the current commit or `unborn`, file count, total bytes, and timestamp. The current scan is a file inventory; it does not build symbols or answer code questions.

## Next schema boundary

Phase 2 graph records should use stable IDs derived from `repo_id`, revision, relative path, symbol kind, qualified name, signature, and source range. Relationships should carry type, provenance, confidence, and revision. The active Phase 0 database contains no graph records yet. The service makes no outbound requests, runs no repository commands other than read-only Git queries, and does not invoke an LLM.
