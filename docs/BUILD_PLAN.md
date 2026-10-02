# CodeMind build plan

## 1. Scope and assumptions

This plan converts the supplied project analysis and flow diagram into a sequence that can be built and demonstrated. The source material is design input, not an implementation specification or a set of operational instructions.

The investor milestone targets one excellent workflow: **trace code with evidence, then investigate a failed command using code and history**. TypeScript/JavaScript and VS Code are the first platform. A local service stores indexes and runs retrieval. A model adapter supports a configured local model and an optional cloud provider; the product must display which mode is active. GitHub and a ticket/document source are connected after local code answers work. Screen capture, autonomous edits, and multi-repository intelligence follow the investor milestone.

Planning estimate: 12 weeks for two full-time engineers plus part-time design/product. This is a sequencing estimate, not a performance or delivery guarantee. The investor milestone is not an enterprise production release. The extension targets VS Code 1.93 or newer because its planned terminal shell integration API first appeared in 1.93.

## 2. Architecture and source of truth

```text
VS Code extension ── workspace changes, questions, terminal events, review UI
        │
        ▼
Local service ── ingestion → normalized records → syntax/symbol graph
        │                                ├────────→ lexical index
        │                                └────────→ vector index (when enabled)
        ▼
Query router → exact search + graph traversal + semantic search + history
        ▼
Evidence ranking → bounded model context → cited answer / proposed patch
        ▼
Developer review → optional write action
```

Every indexed record should include `repository_id`, `revision`, `path` or source URL, `start_line`, `end_line`, `source_type`, `indexed_at`, and a stable content ID. Symbol and edge records add symbol IDs, relationship type, provenance (`scip`, `syntax`, `framework`, `explicit_external_link`, or `model_inferred`), and confidence. Git and error records carry commit ID, occurrence time, and any explicit issue/CI link. Graph, lexical, and vector indexes refer to these same IDs.

Use **SQLite** for the initial canonical metadata store, edge tables, and FTS5 lexical search. Add a local vector index only when semantic search improves the evaluation set. A dedicated graph server and GNN are not prerequisites for multi-hop traversal. A later scale test may justify them. For precise references, import SCIP output from a supported indexer when available; retain syntax and search fallbacks if indexing fails.

Keep two independently versioned views: the developer's working tree and committed history. Label answers from unsaved or uncommitted code accordingly. Never silently combine evidence from incompatible revisions.

## 3. Phases and exit gates

| Phase | Target | Deliverable | Exit gate |
|---|---:|---|---|
| 0. Define and measure | Week 1 | Demo repository, 40–60 real questions, baseline manual answers, data policy, API contracts | Questions have reference answers and source locations; demo scenario is reproducible. |
| 1. Product shell | Weeks 2–3 | VS Code extension, local service lifecycle, settings, health view, basic chat UI | Clean install, connect, ask, disconnect, and recover from service restart on macOS; no hidden setup steps. |
| 2. Code intelligence | Weeks 4–5 | File watcher, incremental Tree-sitter parsing, stable symbols, import/call edges, SQLite/FTS5, optional SCIP import | Changed files update without full rebuild; known definitions and callers in the fixture resolve to correct lines. |
| 3. Evidence retrieval | Weeks 6–7 | Intent routing, lexical + graph retrieval, optional embeddings, ranking, revision-aware context, citations | Fixed evaluation set reaches agreed correctness and citation targets; unknown answers say what evidence is missing. |
| 4. Model and answer UX | Week 8 | Local model adapter, optional cloud adapter, answer streaming, source navigation, feedback capture | One complete cited answer works in a fresh install; provider mode and data transmission are visible. |
| 5. Failure and history | Weeks 9–10 | Terminal command capture, exit code handling, bounded output retention, Git commit/diff indexing, related failure search | Reproduced failed test yields the right code location and a relevant historical candidate; false matches are labelled. |
| 6. Team context | Week 11 | GitHub PR/push ingestion; one read-only ticket/document connector; drafted summary with review | PR or ticket context can be cited; no external ticket or document changes occur without review. |
| 7. Investor release | Week 12 | Installer/VSIX, one-command demo fixture, evaluation report, demo recording, pitch material, reset script | Live demo succeeds twice from a clean machine/account; claims match the measured evaluation and shipping features. |
| 8. Product beta | After demo | More languages, opt-in visual awareness, patch proposals and verification, team deployment, stronger observability | Each capability has its own privacy, accuracy, reliability, and rollback gate. |

### Phase 0: definition and measurement

- Choose a sample full-stack repository with a UI, API, database access, tests, three or more meaningful commits, and a reproducible failing test. Obtain rights to show its code publicly.
- Write 40–60 questions split among exact symbol lookup, caller/dependency tracing, architectural explanation, and failure/history investigation. Record a reference path and allowed alternative answers for each.
- Record baseline answer time by a developer using IDE search alone. Do not use a fabricated speedup in a pitch.
- Decide storage and retention settings, excluded paths, secret scanning behavior, and whether cloud inference can ever receive source snippets.
- Define local service endpoints: `index.start`, `index.status`, `search`, `trace`, `ask`, `history.search`, and `feedback`.

### Phase 1: installable product shell

- Extension: activation, assistant panel or chat participant, command palette actions, settings, diagnostics, and status display.
- Service: start/stop, version handshake, request IDs, cancellation, structured errors, and index schema migration.
- Safety: follow VS Code Workspace Trust; disable command execution and indexing that reads workspace configuration in untrusted workspaces.
- Add a guided first-run flow that selects a repository and model mode, then shows indexing progress.

### Phase 2: indexing and graph

- Ignore generated files, dependencies, binary files, and user-configured exclusions; hash file contents to avoid redundant indexing.
- Parse functions, classes, imports, exports, and syntactic calls; preserve exact source ranges.
- Build typed edges (`DEFINES`, `IMPORTS`, `CALLS`, `REFERENCES`, `ROUTE_TO`, `QUERIES`) with provenance and confidence. Do not turn an inferred edge into a proven edge.
- Import SCIP definitions/references for projects where the indexer succeeds. Detect and surface gaps caused by build configuration or unsupported files.
- Apply atomic per-revision updates and remove stale records on rename/delete.

### Phase 3: hybrid retrieval and evaluation

- Route exact identifiers and stack traces to lexical search first; route relationship questions to symbol resolution and graph traversal; use embeddings for broad conceptual search.
- Cap traversal depth, result count, and context tokens. Rank evidence by match strength, provenance, recency, and revision compatibility.
- Require every factual code claim to cite a source location or commit. Return uncertainty when the link is only inferred.
- Add an evaluation command that reports answer correctness, recall of required evidence, citation validity, abstention, indexing delay, and p50/p95 query latency.
- Initial *targets to validate*, not claims: at least 80% correct answers on the fixed in-scope set, at least 95% valid citations, and no unsupported definitive claim in the investor demo script. Revise only after inspecting failures.

### Phase 4: model layer and user experience

- Keep model prompts and adapters separate from retrieval. A provider receives the smallest relevant evidence set, never a whole repository by default.
- Show indexing freshness, chosen model mode, evidence cards, and one-click file/line navigation.
- Answer format: short conclusion, traced path, evidence, uncertainty, and optional next action.
- Collect explicit useful/incorrect feedback linked to a query and index revision.

### Phase 5: terminal and temporal context

- Subscribe when a shell execution starts and read its stream immediately; use completion and exit code to classify the result. Support unknown exit code, unavailable shell integration, cancellation, and output truncation.
- Parse useful locations from common TypeScript/Node test/build output, while retaining raw excerpts as evidence. Redact secrets before persistence or model use.
- Index local Git commits and diffs with revision IDs. Associate failures with commits only through explicit CI/issue links or clearly marked similarity.
- Suggest a patch only after retrieving relevant code and history; show a diff and require developer review before applying it.

### Phase 6: team sources

- GitHub: repository authorization, initial backfill, local polling for push and PR changes, deduplication, deletion/revocation handling, and a visible sync state. A hosted webhook receiver is a later team-deployment option.
- Ticket/document connector: start with one provider and read-only scopes. Normalize title, URL, update time, access scope, and cited excerpt.
- Draft a PR summary or documentation suggestion with evidence. A reviewer chooses whether and where to publish it.
- Keep tenant and repository boundaries in retrieval so one organization's content cannot appear in another's answer.

### Phase 7: investor packaging

- Produce a signed or otherwise installable VSIX, reproducible demo fixture, short setup guide, known-limits page, evaluation report, and two-minute fallback recording.
- Rehearse a live sequence: install/health → trace API path → click citations → run failing test → retrieve prior related fix → review proposed change → rerun test.
- Include honest comparison with IDE search alone and disclose which sources are local, synced, or simulated in the fixture.
- Run a clean-machine rehearsal and a disconnected-network rehearsal if claiming local operation.

### Phase 8: beta extensions

- Add languages only with language-specific gold questions and build-system coverage.
- Add screen understanding as an explicit, time-bounded opt-in capture. Prefer editor/browser semantic APIs first; define capture exclusions, retention, and visible recording state.
- Add safe agent actions: proposed edits, isolated execution, tests, rollback, and audit trail.
- Consider a dedicated graph or vector service only after profiling SQLite/local indexes at realistic repository sizes and concurrency.

## 4. Integration order and contracts

| Integration | Trigger and data | Product behavior | Main failure handling |
|---|---|---|---|
| VS Code workspace | Open, save, rename, delete; active file | Incremental index and current-file context | Pause or show stale status on parse/index error. |
| SCIP indexer | Explicit or CI-generated index | Higher-confidence definition/reference edges | Keep syntax/search fallback and show coverage. |
| Git | Local commits and diffs | Revision and change-history evidence | Skip malformed history entries; preserve last good index. |
| Terminal shell integration | Start stream, completion, exit code | Failure investigation with captured excerpt | Handle unknown exit and missed/unavailable output explicitly. |
| Model runtime | Configured local provider, optional cloud provider | Bounded, cited synthesis | Timeout/cancel; return retrieved evidence without fabricated answer. |
| GitHub | Authorized local polling for push/PR changes | Team change context | Idempotent sync, retry, revocation cleanup. |
| Ticket/document source | Read-only sync | Design intent and decisions with URLs | Respect source permissions and stale timestamps. |
| Vector index | Normalized chunks and records | Broad conceptual retrieval | Lexical/graph search remains available if absent. |

## 5. Release quality and privacy gates

- **Correctness:** cite source revision and exact location; distinguish compiler, syntax, and inferred relationships. Test renames, deleted files, branches, monorepos, and duplicate symbols.
- **Security:** honor Workspace Trust, use least-privilege connector scopes, store tokens in platform secret storage, exclude secret and generated paths, redact terminal output, and make external model use explicit.
- **Reliability:** version service/index schemas, support cancellation and retry, measure resource use, and recover from interrupted indexing.
- **User control:** capture, uploads, writes, and command execution are visible and reviewable. Documentation/ticket changes remain drafts until approved.
- **Evidence:** publish a dated evaluation report with dataset size, repository characteristics, hardware, provider mode, latency distributions, errors, and limitations.

## 6. Investor milestone versus full vision

The investor release demonstrates a defensible wedge: trustworthy code-path answers and failure investigation in one ecosystem. The full vision adds team memory, more languages, visual context, and reviewed agent actions. Do not describe those later capabilities as shipped. A successful pitch should show the working product, quantified outcomes from the evaluation set, the cost to run a typical query, early user feedback, and the next funded milestone.

## 7. Technical references

- [VS Code Chat Participant API](https://code.visualstudio.com/api/extension-guides/ai/chat)
- [VS Code terminal execution API](https://code.visualstudio.com/api/references/vscode-api)
- [VS Code Workspace Trust](https://code.visualstudio.com/api/extension-guides/workspace-trust)
- [Tree-sitter incremental parsing](https://tree-sitter.github.io/tree-sitter/)
- [SCIP precise code navigation](https://sourcegraph.com/docs/code-navigation/precise-code-navigation)
- [GitHub webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [Qdrant local quickstart](https://qdrant.tech/documentation/quick-start/) (candidate if a separate vector store becomes necessary)
