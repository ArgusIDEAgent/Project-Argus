# progress.md — Living State Tracker

**Last updated:** 2026-10-09 · **Session:** 1 (baseline) · **Evaluation date:** _TBD — owner to fill in_
**Contract:** [`README.md`](README.md) (locked). IDs F1–F7 refer to its §2.
**Basis of this file:** static review of the repository snapshot. **Nothing was executed.** "Built" means the code exists and reads as complete; "Verified" means a run or test confirmed it. The only automated checks that exist are in `extension/test/suite.js`.

### Session protocol — for any assistant starting from this file

1. Read `README.md`, then this file, before touching code.
2. README is locked. A request that adds, removes or reshapes anything in its §1–§4 is flagged **"⚠️ Scope change"** and waits for an explicit "Confirmed: scope change" — before any code is written.
3. Keep diffs surgical (`CLAUDE.md`): every changed line should trace to the request.
4. After each major chunk and at session end, output the full updated `progress.md` for the owner to commit.

**Legend:** ✅ Built · ⚠️ Built, known defect · 🚧 Partial · ⛔ Stub / missing · ➖ Out of scope

---

## 1. Current Phase

**Integration & hardening.** The building blocks exist: an incremental indexer, vector search, a Python AST graph and a RAG pipeline on the CLI, plus a working extension shell. What is missing is the wiring between them. Structural queries (F4) and grounded Q&A (F5) are reachable **only from the CLI** — no API route, no extension UI. The graph has a correctness defect, the engine has no tests, and half of the API surface is stubs for features that are not in the README.

*Old roadmap (`project_plan.md`): Phase 1 MVP ✅ (minus tests) · Phase 2 AST graph ⚠️ · Phase 3 Git/Jira ➖ dropped · Phase 4 API + extension 🚧.*

## 2. Completed Milestones

| ID | Capability | Status | What exists |
|----|-----------|--------|-------------|
| F1 | Repository indexing | ✅ | Recursive discovery with ignore list, 1 MB cap and symlink-escape guard; SHA-256 incremental indexing; deleted-file pruning; settings fingerprint forces a full reindex; secret redaction before embedding; deterministic point IDs; state kept outside the repo. Reachable via `indexer.py` and the API job flow. |
| F2 | Semantic code search | ✅ | `search.py` CLI and `POST /search/code` (path, line range, score, indexed file hash). |
| F3 | AST knowledge graph | ⚠️ | `graph_builder.py`: module/class/function/method nodes; `calls` / `contains` / `imports` edges; JSON persistence; per-file update and removal. **See DEF-1.** |
| F4 | Structural queries | 🚧 | Logic ✅ (callers, callees, impact chain, members) and `graph_query.py` CLI ✅. API ⛔. Extension ⛔. |
| F5 | Grounded Q&A | 🚧 | `rag_pipeline.py` CLI ✅: intent detection → graph + vector context → LLM, with prompt-injection hardening. API ⛔. Extension chat is search-only. |
| F6 | VS Code extension | 🚧 | Chat panel, repo picker, index-job polling, live refresh (file watcher + git state poll), search results with hash-verified open-at-line, Workspace Trust handling, VSIX script. Missing: Q&A and structural views. |
| F7 | Evidence | ⛔ | No pytest. `ai-engine/test_ai.py` is an LLM connectivity script, not a test. |
| — | Security baseline | ✅ | Session token (constant-time compare), loopback bind, TrustedHost, OpenAPI off, Qdrant API key, path-traversal checks, CSP nonce. |

**API surface:** 7 real routes (`/hello`, `/repos/register`, `/repos/{id}/index`, `/repos/{id}/refresh`, `/jobs/{id}`, `/repos/{id}/live-state`, `/search/code`) · **7 stubs** (`/docs/sections`, `/docs/overview`, `/docs/section/{id}`, `/docs/sync`, `/analysis/reuse`, `/analysis/reuse/feedback`, `/graph/file/{path}`) · **0 routes for F4 or F5.**

**Verified (automated) so far:** only `extension/test/suite.js` — panel opens, commands are registered, the webview script compiles, `/hello` round-trip (skipped when the engine is down), and `verifiedSourcePath` rejects traversal, symlink escape and hash mismatch. Everything else above is *Built, not Verified*.

**To promote "Built" → "Verified"** (Qdrant up, `.env` set; run once and record results here):

```bash
cd ai-engine
python indexer.py --dir ./sample_code --full-reindex      # expect "Indexing complete" + graph summary
python search.py --query "How does authentication work?"  # expect auth_service.py chunks on top
python graph_query.py --stats                             # DEF-1 predicts a node type "unknown"
python graph_query.py --symbol generate_token --mode callers   # expect login_user
python graph_query.py --symbol generate_token --mode callees   # expect _secret, jwt.encode…; DEF-1 predicts "No callees found"
python rag_pipeline.py --query "What happens if I modify verify_token?"   # expect an answer citing auth_service.py
python api.py                                             # 2nd terminal; then in extension/: npm run test:integration
```

Then repeat the indexer step on `./` (ai-engine itself) — the suggested demo corpus. **Results: _not yet recorded_.**

## 3. Current WIP

### Defects (found by code review — reproduce before fixing)

- **DEF-1 · F3/F4/F5 · high — call resolution is a no-op.** In `graph_builder.update_graph_for_file`, `add_edge` auto-creates every callee name as an attribute-less node, so `_resolve_call_edges` always hits `if v in graph.nodes: continue` and never resolves anything. Effects: `calls` edges point at name stubs instead of real definitions; same-named functions in different files merge into one stub; `find_node("generate_token")` returns the stub, so **callees and members come back empty for any symbol that is itself called somewhere**; stubs (`print`, `jwt.encode`, imports…) pollute `--stats` and `--list-all`; the target symbol's own file chunk is not pulled into structural RAG. *Constraint for the fix:* removing a file's nodes also deletes inbound edges from other files, so raw callee names must be kept on the caller node and edges re-resolved in one global pass after each incremental update.
- *Minor (log, don't fix yet):* `graph_builder.py` docstring says `.codemind_graph.json` (actual: `graph.json`); `ast.walk` also attributes a nested function's calls to its parent; intent detection in `rag_pipeline.py` is regex + substring matching, brittle for short words.

### Partial / not wired

- **F4 and F5 have no API route**, so the extension cannot use them. `ask_codemind` prints to stdout and returns a bare string — no structured citations.
- **Extension "chat" is vector search only.** The typed-message path always sends `type: 'search'`; the `send`/`hello` fallback in `extension.ts` is dead code. `extension/README.md` already lists generative answers as planned.
- `/search/code` hard-codes `callers: []` and `tests: []` — the graph is never consulted.
- Default LLM is the free-tier slug `openrouter/cohere/north-mini-code:free`; availability and rate limits are unverified (override with `CODEMIND_LLM_MODEL`).
- The engine must be started by hand (`python api.py`) — accepted limitation (README §3).

### Scope drift — needs owner decision

Built outside the original vision and absent from README §2, so **proposed OUT** (README §3):

- **Docs feature** — commands, buttons, webview renderer and status line in the extension + 4 stub routes. Visible symptom: **Sync Docs does nothing.**
- **Reuse feature** — command, button, reuse-on-save, feedback + 3 stub routes. Visible symptom: `codemind.checkReuseOnSave` defaults to `true` and sends pointless `/graph/file` calls on every save.
- **Recommendation:** commit the current state to a `parked/docs-reuse` branch, then delete both features from `main` in one "drift removal" commit so the extension matches the README. Hiding them behind a flag would keep the maintenance cost; deletion doesn't.
- **Jira, Git history/blame, graph canvas** — promised by the old README, zero code. Proposed OUT.
- **Doc debt:** `project_plan.md` says the AST graph and the extension "don't exist yet" — both exist. Proposed: retire it (scope → README, status → this file, quick start → README).

**Pending owner decisions:** ☐ approve README v1.0 (esp. Jira / Git-history dropped) · ☐ Docs/Reuse: delete vs park · ☐ retire `project_plan.md` · ☐ set the evaluation date

## 4. Next Immediate Steps

*Before starting:* run the verification block above and record the results; apply the drift-removal commit once approved.

1. **NEXT-1 · F3 → F4 — Repair graph resolution, with tests.** Touches `graph_builder.py`, a new `ai-engine/tests/`, and pytest as a dev dependency. Keep raw callee names on the caller node, rebuild resolved `calls` edges in one global pass after every incremental update, and create no attribute-less stub nodes. **Done when:** `--symbol generate_token --mode callees` lists `_secret`; `--stats` shows no `unknown` nodes; re-indexing one file keeps inbound edges from others; pytest covers all three on a two-file fixture that includes a name collision.
2. **NEXT-2 · F5 (+F4) — Make Q&A API-ready.** Refactor `ask_codemind` into a pure function returning `{answer, citations[{path, startLine, endLine}], structural}`, with the CLI printing from it. Add `POST /ask` (repo-scoped, session-token protected, validated like `/search/code`). **Done when:** an authenticated `curl` returns an answer with ≥ 1 citation for *"Where is JWT authentication implemented?"* and an impact chain for *"What happens if I modify verify_token?"*.
3. **NEXT-3 · F6 — Wire the extension chat to `/ask`.** Send → `/ask`; render the answer with clickable citations through the existing hash-verified `openIndexedSource`; keep Search Code as its own button. **Done when:** README demo steps 2–3 pass in the Extension Development Host.

*Order matters:* structural answers are only as good as the graph, so NEXT-1 precedes NEXT-2.
*Queued, not immediate:* F7 — remaining pytest coverage and the golden-question evaluation; a check of demo step 4 (edit → refresh → re-ask).
