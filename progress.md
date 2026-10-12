# progress.md — Living State Tracker

**Last updated:** 2026-10-12 · **Session:** integrated local retrieval repair · **Evaluation date:** _TBD — owner to fill in_
**Contract:** [`README.md`](README.md) (locked). IDs F1–F7 refer to its §2.
**Basis of this file:** legacy roadmap sections were a static review of an earlier snapshot; dated knowledge-base entries above record executed checks. "Built" means the code exists and reads as complete; "Verified" means a run or test confirmed it.

### Session protocol — for any assistant starting from this file

1. Read `README.md`, then this file, before touching code.
2. README is locked. A request that adds, removes or reshapes anything in its §1–§4 is flagged **"⚠️ Scope change"** and waits for an explicit "Confirmed: scope change" — before any code is written.
3. Keep diffs surgical (`CLAUDE.md`): every changed line should trace to the request.
4. After each major chunk and at session end, output the full updated `progress.md` for the owner to commit.

**Legend:** ✅ Built · ⚠️ Built, known defect · 🚧 Partial · ⛔ Stub / missing · ➖ Out of scope

## New knowledge base plan: Phases 1–3

The owner supplied a new local knowledge base implementation plan and requested Phase 1. `knowledge_base/scanner.py` now inventories JavaScript/TypeScript source and supported JS/TS manifests into a private per-repository SQLite database. It applies Git ignore rules, custom exclusions, secret and symlink safeguards, binary and size checks, and records per-path reasons, hashes, Git revision, parsing status, and coverage counts. This component is separate from the existing Qdrant indexer and does not change the VS Code extension.

**Verified 2026-10-11:** `python3 -m unittest knowledge_base.test_scanner -v` passed 5 tests. A live scan of this repository found 4 source files and 3 config files included, 21 unsupported files, 9 ignored files, 9 pruned directories, and no scan errors. The new inventory database is outside the repository. The existing README scope reflects the earlier roadmap and needs reconciliation with the new plan before future integrated work.

**Phase 2, 2026-10-11:** `knowledge_base/parser.py` uses Tree-sitter JavaScript, TypeScript, and TSX grammars to extract scoped symbols and unresolved syntactic references, with comments, source spans, stable IDs for ordinary declarations, and per-file parsing status/diagnostics in SQLite. The fixture suite covers nesting, overloads, aliases, dynamic imports, JSX/TSX, Unicode spans, rescan cleanup, and errors. All 10 Phase 1/2 unit tests passed; a live run parsed 4 source files with 94 symbols, 423 syntactic references, 2 comments, and no syntax or read errors. This is a separate knowledge-base component.

**Phase 3, 2026-10-12:** `knowledge_base/semantic_resolver.cjs` uses the local TypeScript checker for source-limited semantic bindings and tsconfig path aliases. `knowledge_base/graph.py` stores typed, evidence-bearing nodes and relationships plus unresolved occurrences in SQLite; exposes caller/callee, import, reverse-dependency, and bounded path queries; rejects stale snapshots. Independent fixture tests verify alias imports, same-name disambiguation, class inheritance, CommonJS imports, re-exports, missing targets, deletion, and stale-file detection. All 17 Phase 1–3 tests passed; a live graph build parsed 5 source files and recorded 155 nodes, 329 resolved relationships, 21 inferred external imports, and 399 unresolved references. Runtime dispatch remains a static declaration relationship, not proof of the executed override. The pipeline remains separate from the extension and existing Python engine; Phases 4–8 of the new plan remain unimplemented.

**Retrieval fix, 2026-10-11:** The existing chat search now checks saved repository source for exact text before vector search, and repository selection supports a lexical mode when Qdrant or the embedding model is unavailable. The C fixture `strcasecmp` and a copied expression resolve to `crack_detection_functions.c` at the expected lines; four retrieval regression tests pass. A running engine must be restarted to load this change. The extension's pre-existing deleted `tsconfig.json` prevents `npm run compile`; this deletion was preserved.

**Installed-extension correction, 2026-10-11:** The installed CodeMind 0.4.0 still contained an older bundled Node search server, explaining why restarting Python did not change its ranking results. The current TypeScript sources were compiled with equivalent command-line options, packaged without the old server, and installed with `code --install-extension --force`. A live request to the Python API for `tensorflow` returned exact matches at lines 7–11 of the CNN fixture. VS Code must reload its window to activate the replacement extension.

**Integrated retrieval repair, 2026-10-12:** Qdrant now defaults to persistent embedded local storage (no Docker/server); explicit `QDRANT_URL` retains server-backed operation. Threaded access is serialized. Matching index-state files no longer suppress rebuilding a repository whose vectors are missing. Semantic results are repository-filtered, reject stale source hashes, and apply a similarity floor. Conversational questions do not short-circuit on quoted questions in test code; exact expressions and explicit filenames still work.

Repository indexing now builds the Phase 3 SQLite graph alongside vectors, and `/graph/file` and verified static caller queries use that graph. Retrieved JS/TS evidence can include verified caller facts. This supersedes the earlier statement that Phase 3 was entirely disconnected, but does not implement all Phases 4–8 or repair the separate legacy Python graph. The API no longer builds that legacy graph during vector indexing.

`/ask` retrieves current evidence, redacts secrets and generates cited explanations using loopback Ollama (`llama3:latest` by default). Missing local models or invalid citations fall back to evidence; evidence changed during generation causes the answer to be discarded. Chat Send now calls `/ask`, while Search Code stays separate. Extension 0.4.1 was compiled, packaged and installed in VS Code and Antigravity's extension directories; reload the editor to activate it. Restored the missing TypeScript config and integration runner. `python3 start_codemind.py` uses the project's virtual environment; operational details are in `RETRIEVAL_SETUP.md`.

**Verified:** 15 retrieval/answer tests and 17 Phase 1–3 tests pass; extension compile and integration checks pass. A live semantic request for “Where is user authentication checked?” returned `ai-engine/sample_code/auth_service.py` and Ollama explained `login_user` with an evidence citation. Embedded storage restart, multithreaded access, empty-store rebuild, repository isolation, exact search, stale-file rejection, secret redaction, model fallback, graph callers and API authentication have regression coverage. Docs/Reuse remain pre-existing unfinished features. Semantic similarity and static graph relationships are not proofs of runtime behaviour; arbitrary LLM correctness is not guaranteed.

**Backend citation correction, 2026-10-12:** Reproduced `project_plan.md` failing because Ollama repeated citation `1` until its 350-token limit, truncating the generated JSON. Answer generation now requests a plain-text explanation only. The backend deduplicates evidence, creates numbered source references with file paths, line ranges and source hashes, and appends a Sources list matching the existing result cards. The API exposes the same records as `citations`. Model-written citation markers are removed outside code examples. Provider failures, timeouts, malformed responses and output limits have distinct messages; changed evidence discards both the answer and its citations. All 21 retrieval/answer tests pass, including authenticated `/ask`, duplicate/invented model references, redaction, source changes and readable truncated responses. After restarting the backend, authenticated `/ask` explanations of `project_plan.md` and `README.md` both completed without warnings; source numbering, current hashes and result-card mapping were verified. The editor's source-path/hash verification also passed for both files. These references identify provided retrieval context, not independent verification of each model claim.

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

## 2026-10-12 — Knowledge-base Phase 4

Implemented the separately supplied knowledge-base plan's metadata/identity phase (not the historical F1–F7 roadmap above): SQLite schema v5, shared IDs, file/symbol/graph/chunk provenance, definition/reference occurrences, snapshot/error records, cross-repository foreign keys, deletion cleanup, and saved-source validation. Existing graph and vector IDs remain compatible. API/CLI vector results and answer citations now use canonical metadata; failed snapshots or disagreeing vector payloads are not presented as verified evidence. Local Qdrant and the working citation UI are retained. See `knowledge_base/README.md` for inspection commands and boundaries.

Regression verification: scanner/parser/graph/metadata suites and existing retrieval/local-answer/citation suites. First index refresh upgrades old vector payloads once; ordinary indexing remains hash-incremental. Code-aware chunking, editor-buffer indexing, cross-rename matching and continuous synchronization are not claimed as Phase 4 work.

## 2026-10-12 — Knowledge-base Phase 5

Implemented the owner's separate semantic-representation plan: source-aware JS/TS
and Python function/class/method units, bounded children with parent identities,
module/documentation/configuration sections, and explicit syntax/text fallbacks.
Chunks include qualified names, signatures, attached comments, language/module and
saved-source ranges. Attached comments enrich their symbols instead of competing
as standalone hits; nested JSON configuration stays together within size limits.

Default vector storage is now local FAISS cosine search with a durable SQLite
vector-ID/chunk-ID journal and recoverable hash-checked cache files. The old local
Qdrant directory is untouched; explicit QDRANT_URL retains remote compatibility.
Metadata schema v6 adds redacted chunk context and SQLite FTS5 BM25 keyword
retrieval. The API accepts file/module/language/symbol-kind filters, separate
semantic/keyword modes and labelled keyword fallback when embeddings fail.
Both retrieval paths reject stale/unfinished evidence and preserve backend
citations. Similarity creates no CALLS or DEPENDS_ON relationships.

Verified: 76 storage, API, previous phase and citation regression tests; Python
compilation, extension compilation/integration and diff whitespace checks. A real
local-embedding smoke benchmark retrieves the expected function first in all 12
curated JavaScript/TypeScript/Python questions for both raw units and contextual
units. This is not a general accuracy guarantee or an exhaustive model comparison.
An isolated live API test on port 8001 upgraded a copy of the existing schema-v5
database successfully and indexed 46 files into 990 chunks with no indexing
errors or dangling metadata references. Live checks verified natural-language
authentication retrieval, file/language/kind filters, keyword configuration/error
retrieval, rejected unauthenticated requests and a local Ollama answer whose
backend citation matched its source hash, chunk ID and range. Literal lookup now
shares inventory ignore/sensitive-file exclusions with semantic discovery.
The user's port-8000 engine
and original database were not restarted or modified by that test.

Run `python3 start_codemind.py` after stopping the old engine, then refresh the
selected repository once. Dependencies include faiss-cpu in requirements.txt;
the launcher's dependency check now checks FAISS too. See knowledge_base/README.md
and RETRIEVAL_SETUP.md. Phase 6 hybrid fusion/reranking and later continuous
synchronization work are not claimed as implemented here.

## 2026-10-12 — Knowledge-base Phase 6

Implemented the owner's separate hybrid-retrieval plan with the local
`HybridRetriever` interface, authenticated `/retrieve`, compatible `/search/code`
and the same evidence pipeline for `/ask`. Deterministic intent planning detects
symbols/files/modules; exact identity and current-source lookup precede exploratory
FTS5/vector rank fusion. RRF combines channel ranks rather than incompatible raw
scores; deduplication, implementation/symbol context and bounded verified graph
expansion influence final evidence ordering. Caller/callee/import/impact/dependent
queries use static graph provenance, not semantic guesses.

Standard snake-case result fields coexist with the editor's source/citation fields.
Results explicitly report found, not_found or ambiguous; ambiguous explanations
do not call an answer model. Saved-source hashes, ready snapshots, canonical IDs
and source ranges are checked before fusion and again before return. Stale/failed
evidence is discarded. Keyword fallback remains usable when vectors fail. Exact
Python definitions remain syntax-only, including cross-language name collisions;
external import specifiers remain inferred and unresolved references are separate
source-verified uncertainty, not resolved edges. Graph traversal is cycle-safe and
bounded to three hops/100 examined edges with truncation warnings.

Verified: 104 Python regression tests (48 knowledge-base and 56 API/answer tests),
extension compilation/integration, Python compilation and whitespace checks. An
isolated live API on port 8001 indexed a five-file TS/Python fixture with the real
local BGE embedding model: 16 checks covered hybrid natural-language relevance,
exact identity, callers/callees/impact/imports/dependents, ambiguity/file filters,
module lookup, missing files, Python retrieval, keyword errors, evidence-only
answers, canonical source citations and authentication. The expected permission
function ranked first with both keyword/vector contributions. These are curated
smoke checks, not a general retrieval-accuracy guarantee. Test data was temporary;
the user's port-8000 engine and original repository database were left untouched.

Restart `python3 start_codemind.py` once to load Phase 6 after stopping the old
engine. Ready Phase 5 indexes need no re-embedding for this phase; older indexes
still require the Phase 5 refresh. There are no new dependencies or schema changes.
Continuous synchronization/watchers remain Phase 7, not completed work here.
