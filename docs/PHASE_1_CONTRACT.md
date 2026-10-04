# Phase 1 Git ingestion contract

Phase 1 runs in the bundled Node service. The VS Code extension may select a trusted local Git directory inside or outside the current workspace. A selected subdirectory remains the inventory and diff boundary. All generated state lives in extension global storage (or `CODEMIND_DATA_DIR` when the service runs directly); the service runs read-only Git commands and never installs hooks or writes to the source repository.

## Inventory and snapshot

The first index enumerates tracked files with `git ls-files`. Untracked, non-ignored files are also included as dirty working-tree additions. Supported source files are JavaScript, TypeScript, and Python, subject to `RepoConfig` and the 2 MiB limit. File records store path, language, size, SHA-256 content hash, current commit hash, last modifying commit, tracked/dirty flags, and exclusion reason. Excluded paths are recorded as metadata but never read or hashed. The default `/files` response omits excluded records; `includeExcluded=true` includes them.

`index.revision` is `<commitHash>:<dirtyFingerprint>`. The fingerprint is `clean` for a clean tree and a SHA-256 digest of sorted dirty-path metadata and eligible content hashes otherwise. `index.commitHash`, `index.branch`, and `index.dirtyFingerprint` are also returned separately. The fingerprint names the working snapshot; it is not a Git object ID. An index describes saved files on disk, not unsaved editor buffers.

The metadata schema is version 2 and migrates Phase 0 databases in place. `files` stores the scan cursor, hash, and stat data. `git_branches`, `git_commits`, and `git_commit_files` provide Repository/Branch/Commit/File membership and changed-in facts for later graph ingestion. Recent commit metadata and changed paths are retained for the latest 100 commits on the current ancestry. The index update and changed-file records are exported atomically to the SQLite file after a successful scan; a failed scan leaves the previous index available.

Refresh compares the last indexed commit with `HEAD`, inspects Git's dirty paths, and reconciles the tracked/untracked path lists. Unchanged file records are reused. Changed source files are hashed, stale paths are deleted, and new metadata is upserted. Jobs report `scannedCount`, `changedCount`, and an added/modified/deleted/renamed change set. Hunk coordinates are based on the previous indexed commit; if the previous snapshot was already dirty, the hunk baseline is still that commit. Git rename hints are used first, with identical-content hash matching for remaining add/delete pairs.

## API

All endpoints require the Phase 0 `x-codemind-session` token. `POST /repos/{id}/index` is retained for compatibility and uses the same incremental scanner as `refresh`.

| Method | Path | Response |
|---|---|---|
| `GET` | `/repos/{id}/status` | Registered repository, indexed revision/commit/branch/fingerprint/counts, latest job |
| `GET` | `/repos/{id}/live-state` | Current Git `commitHash` and `branch`, even if the index is stale |
| `GET` | `/repos/{id}/files?includeExcluded=true` | File inventory with hashes, commit provenance, and exclusion state |
| `POST` | `/repos/{id}/refresh` | `202` with `jobId`; duplicate in-flight requests reuse the active job |
| `GET` | `/repos/{id}/changes?from=&to=` | Changed files, rename information, exclusion reason, and zero-context hunk coordinates |
| `GET` | `/repos/{id}/history?path=` | Recent commits for a repository-relative file path |
| `GET` | `/jobs/{jobId}` | State and result, including changed paths and scan counts |

`changes` accepts Git commit references for `from` and `to`. Omitted `from` uses the indexed commit (or current `HEAD` before indexing); omitted `to` compares against the current working tree and includes untracked additions. The endpoint returns hunk coordinates, not source text. Invalid revisions and paths return 400. The Git service also provides internal `log`, `diff`, `show`, `blame`, `merge-base`, branch listing, and worktree listing methods.

## Automatic refresh

While a repository is selected in chat, the extension debounces local filesystem events, including Git metadata changes, and refreshes the inventory. It polls the live commit and branch every three seconds to catch commits and branch changes in linked worktrees whose Git metadata is outside the selected directory. Editor save events also schedule a refresh. No Git hook is installed in a repository.

Phase 1 stores file and Git facts. AST symbols, semantic search, and repository-aware chatbot answers remain later phases.
