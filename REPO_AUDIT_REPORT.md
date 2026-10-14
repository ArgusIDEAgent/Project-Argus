# REPO_AUDIT_REPORT.md

**Repository:** Project-Argus / CodeMind — AI developer knowledge assistant
**Audit type:** Autonomous QA & codebase audit (tests written + executed, report generated)
**Date:** 2026-10-14
**Auditor:** Autonomous QA Engineer (read/write/exec permissions)
**Constraint honoured:** no production/application code was modified. Only new test files and this report were created.

---

## 1. Executive Summary

Project-Argus is a four-part monorepo:

| Part | Language | Role | Tests |
|------|----------|------|-------|
| `ai-engine/` | Python (FastAPI) | Embedding, indexing, RAG, HTTP API, CLIs | pytest (`ai-engine/tests/`) |
| `knowledge_base/` | Python | Scanner, parser, chunking, graph, metadata, retrieval, vector store | pytest (module-level + `knowledge_base/tests/`) |
| `query_system/` | Python | 10-phase query pipeline (understanding, rules, evidence, generation, presentation) | pytest (module-level) |
| `extension/` | TypeScript | VS Code chat panel + engine client | `node:test` (2) + Electron suites (3) |

**Stability:** good. The Python suite is large and mostly green; the single failure is a real (small) logic bug in query-intent detection, documented in §3 and intentionally left failing.

| Metric | Before audit | After audit |
|--------|--------------|-------------|
| Python tests collected | 645 | **806** |
| Python tests passed | 644 | **805** |
| Python tests failed | 1 | **1 (same known bug)** |
| Python subtests passed | 272 | 272 |
| Source-module coverage (excl. tests/benchmarks) | 84.3 % | **87.2 %** |
| Whole-repo coverage (incl. test files) | 84 % | **87 %** |
| Extension `node:test` tests | 20 (runnable, unverified) | **20 passing (verified)** |
| Extension TypeScript build | unverified | **compiles clean (`tsc -p ./`)** |

**Verdict:** the codebase is in a workable state for the F1–F7 scope in `README.md`. The blocking defect is narrow (one regex family). The largest remaining risks are untested request-surface paths (`ai-engine/api.py`, `ai-engine/indexer.py`) and the inability to run the Electron extension suites offline.

**Test-environment note:** all Python tests must be run as `python -m pytest` from the repository root (there is no `pytest.ini`/`conftest.py`, and `ai-engine/tests/*` and `knowledge_base/*` rely on the repo root and `ai-engine/` being importable via `sys.path`). The canonical interpreter is the repo-root `.venv` (Python 3.14.8, pytest 9.1.1, pytest-cov 7.1.0).

---

## 2. What Is Working (Verified)

All items below are backed by passing, executed tests.

**Baseline suite (pre-existing, still green):**
- `ai-engine/tests/` phase suites: `test_phase0b`, `test_phase2_graph`, `test_phase3_semantic`, `test_phase4_rules`, `test_phase5(_understanding)`, `test_phase6(_execution)`, `test_phase7(_evidence)`, `test_phase8(_generation)`, `test_phase9_presentation`, `test_phase10_integration`, `test_stabilization`, `test_retrieval`, `test_semantic_answers`, `test_s2_understanding`.
- `knowledge_base/`: `test_graph`, `test_metadata`, `test_parser`, `test_retrieval`, `test_scanner`, `test_semantic`, `test_validation`, `test_resolver_s1`, `tests/test_identity`.
- `query_system/`: `test_contracts`, `test_evidence`, `test_execution`, `test_generation`, `test_presentation`, `test_rules`, `test_s2_understanding`, `test_semantic`, `test_stabilization`, `test_understanding`.

**Newly verified by this audit (161 new tests):**
- **Knowledge-graph CLI (`ai-engine/graph_query.py`)** — was 0 % covered; now 98 %. Stats, list-all, callers/callees/impact/members modes, missing-graph and unknown-symbol exits all verified.
- **Local FAISS vector store (`knowledge_base/vector_store.py`)** — CRUD, filtered/`score_threshold` queries, offset scroll, payload merge, delete by id and by filter, and the full metadata-filter matcher (`match`, `match.any`, `range`, `should`, `must_not`, unsupported-filter error). Now 99 %.
- **Chunking (`knowledge_base/chunking.py`)** — Python AST units, decorator spans, method/class splitting, large-class `class_context` split, JS tree-sitter units, Markdown/YAML/JSON document/config units, syntax-error fallback, size/overlap/path validation, deterministic ids. Now 99 %.
- **Lexical search (`ai-engine/lexical_search.py`)** — literal matches, case/whitespace normalisation, explicit-filename citations, language filters, git-without-.gitignore safety, assigned-secret redaction, limits. Now 94 %.
- **Semantic search orchestration (`ai-engine/search.py`)** — CLI top-k bounds, repo resolution, snapshot-unavailable fallback, stale-view discard, stale-vector skipping, verified-result assembly. Now 96 %.
- **RAG helpers (`ai-engine/rag_pipeline.py`)** — `_fence_for` (fence-escalation against backtick injection), `_build_context_block` (budget + control-char sanitisation), structural-pattern families, and the pure `ask_codemind` early returns (over-length question, versioned-snapshot redirect, missing snapshot, missing API key, no-context). Now 51 %.
- **Config factory (`ai-engine/config.py`)** — `_LocalVectorClient` delegation, client-singleton caching, remote-URL branch, atomic-write temp cleanup on failure. Now 95 %.
- **Snapshot lifecycle (`knowledge_base/synchronization.py`)** — `observe_sources` digests, `SnapshotStore` identity/placement validation, `active()`/`view()` state machine, `repository_view` resolution. Now 95 %.
- **Quality-gate helpers (`knowledge_base/validation.py`)** — `compare_sets`, `percentile`, `storage_sizes`, `memory_usage`, `write_markdown`, `fixture_expectations`. Now 38 % (pure surface covered).
- **Extension (TypeScript):** `npm run compile` succeeds; `test/presentation.test.js` (17) and `test/stabilization.test.js` (3) pass under `node --test`.

---

## 3. What Is Broken or Failing

### 3.1 Failing test: structural-query target-symbol capture (real application bug)

- **Failing test:** `ai-engine/tests/test_rag_detection.py::TestImpactDetection::test_what_is_affected`
- **Source:** `ai-engine/rag_pipeline.py`, `_STRUCTURAL_PATTERNS`, **line 88** (pattern index 6); interaction with line 89.
- **Expected:** for `"what is affected if we modify login?"` → `(True, "login", "impact")`.
- **Actual:** `(True, "if", "impact")` — the captured symbol is the stop-word `if`.

**Failure output:**
```
E       AssertionError: assert 'if' == 'login'
E         - login
E         + if
ai-engine/tests/test_rag_detection.py:93: AssertionError
```

**Root cause:** patterns are evaluated in list order and the first match wins:

```python
# line 88 (generic impact — matches first)
(r"\b(?:impact|affect|break|change|modif)\w*\s+(?:of\s+)?(?:changing\s+|modifying\s+)?[`'\"]?(\w+)", "impact"),
# line 89 (specific phrasing — never reached for this input)
(r"\b(?:what\s+(?:happens|breaks|is\s+affected)|change.impact)\s+(?:if\s+(?:I|we)\s+)?(?:change|modify|remove|delete)\s+[`'\"]?(\w+)", "impact"),
```

On `"what is affected if we modify login?"`, line 88 first matches `affected` via `affect\w*`, consumes the following space, and captures the **next word `if`** as the symbol. Because line 88 returns on first match, the more specific line-89 pattern (which would correctly capture `login`) is never tried.

**Scope of the defect (verified by direct execution):**

| Input | Result | Correct? |
|-------|--------|----------|
| `what is affected if we modify login?` | `(True, 'if', 'impact')` | ✗ (should be `login`) |
| `what breaks if I remove parse` | `(True, 'if', 'impact')` | ✗ (should be `parse`) |
| `change impact of login` | `(True, 'impact', 'impact')` | ✗ (should be `login`) |
| `what happens if I change generate_token?` | `(True, 'generate_token', 'impact')` | ✓ (by luck of word order) |
| `impact of changing validate` | `(True, 'validate', 'impact')` | ✓ |

**Impact:** F4 structural queries / F5 grounded Q&A resolve the wrong symbol whenever the impact phrasing puts a stop-word directly after the impact verb (`is affected if …`, `breaks if …`). Because `find_node` then fails to locate `"if"`, these queries degrade to non-structural search instead of returning an impact chain.

**Suggested fix (application code — not applied, per guardrails):** either (a) move the specific `what ... affected|breaks ... change|modify|remove|delete ...` pattern (line 89) *before* the generic line-88 pattern, or (b) constrain the line-88 capture so it cannot take a stop-word (`if|we|I|the|a|when|what|it|that`). A short stop-word blacklist on the captured group fixes the whole family at once.

*Per the audit guardrails, the failing test was left in place; no assertion was weakened.*

### 3.2 Test suite in the environment vs. CI (not a code bug)

- The three Electron suites (`extension/test/hybridSuite.js`, `presentationSuite.js`, `stabilizationRealSuite.js`) and `extension/test/runTest.js` require `@vscode/test-electron`, which downloads/locates a VS Code binary. They **could not be executed** in this offline audit environment and are therefore **unverified**, not failing. `extension/package.json` exposes only `test:integration` (Electron) — there is no headless `npm test`.

---

## 4. Incomplete or Dead Code

No `TODO`/`FIXME`/`NotImplementedError`/`HACK` markers exist anywhere in the Python sources (verified). Findings are structural/housekeeping:

1. **`ai-engine/test_ai.py` — misnamed manual smoke script, collected by pytest.**
   It matches pytest's `test_*.py` glob (contributes 0 tests) yet is a hand-run script that, if executed, calls the **paid** OpenRouter model (`completion(model="openrouter/cohere/north-mini-code:free", …)`). Recommend renaming (e.g. `scripts/smoke_llm.py`) or excluding it from collection to prevent accidental billed API calls.

2. **`.env.example` under-documents configuration.**
   The example lists only `OPENROUTER_API_KEY`, `QDRANT_API_KEY`, and commented `CODEMIND_LLM_MODEL`/`CODEMIND_PORT`. The code also reads (undocumented): `CODEMIND_DATA_DIR`, `CODEMIND_ENABLE_HYBRID`, `CODEMIND_LOCAL_MODEL`, `CODEMIND_AUTO_SYNC`, `CODEMIND_SESSION_TOKEN`, `QDRANT_URL`.

3. **README ↔ extension scope drift.**
   `README.md` §3 (marked LOCKED) lists *Documentation generation and sync* and *Reuse detection* as **Non-Goals (OUT)**, but `extension/src/extension.ts` **registers and ships** `codemind.openDocsOverview`, `codemind.syncDocs`, `codemind.checkReuse`, `codemind.searchCode`, and `codemind.previewResponse` (and `extension/package.json` declares them). Either the README contract or the extension is out of date; the project's own §5 scope-change protocol requires them to agree.

4. **Three Python virtualenvs on disk (~1.06 GB).**
   `.venv` (569 MB, canonical — the one the code and extension suites spawn), `ai-engine/venv` (481 MB), and `extension/venv` (14 MB, a stray Python env inside the TypeScript package). The latter two appear stale/duplicated. All are git-ignored.

5. **Dev-tooling scripts are untested by design (not dead, but unguarded).**
   `knowledge_base/validation.py` (38 %), `query_system/qualify_stabilization*.py`, `query_system/benchmark_*.py`, `knowledge_base/verify_resolver_s1.py`, `knowledge_base/benchmark_semantic.py` execute real indexing/embedding pipelines and are largely uncovered. They are operational tooling, not library code, but a regression in them ships silently.

6. **Legacy-path code in `ai-engine/rag_pipeline.py`.**
   `ask_codemind` returns an early redirect message for repositories using versioned snapshots; the legacy NetworkX-and-LLM path below it (lines ~294, 321–356, 404–426) is reachable only via the legacy CLI and remains partly uncovered (module at 51 %). Not dead, but lightly used and lightly tested.

---

## 5. Test Suite Summary

### 5.1 Before vs. after

| | Tests collected | Passed | Failed | Subtests |
|---|---|---|---|---|
| Before audit | 645 | 644 | 1 | 272 |
| After audit | **806** | **805** | **1** | 272 |

Net change: **+161 Python tests**, all passing. The single failure is the pre-existing application bug in §3.1 (kept intentionally).

Extension: `node --test` → **20 passing** (17 presentation + 3 stabilization); TypeScript compiles clean; 3 Electron suites not executed (offline).

### 5.2 New test files created by this audit

| File | Tests | Target module (coverage before → after) |
|------|-------|------------------------------------------|
| `ai-engine/tests/test_graph_query.py` | 12 | `ai-engine/graph_query.py` (0 % → 98 %) |
| `ai-engine/tests/test_rag_pipeline_helpers.py` | 23 | `ai-engine/rag_pipeline.py` (24 % → 51 %) |
| `ai-engine/tests/test_lexical_search.py` | 13 | `ai-engine/lexical_search.py` (85 % → 94 %) |
| `ai-engine/tests/test_config_extra.py` | 7 | `ai-engine/config.py` (81 % → 95 %) |
| `ai-engine/tests/test_search_cli.py` | 10 | `ai-engine/search.py` (60 % → 96 %) |
| `knowledge_base/tests/test_validation_units.py` | 27 | `knowledge_base/validation.py` (31 % → 38 %) |
| `knowledge_base/tests/test_chunking.py` | 21 | `knowledge_base/chunking.py` (93 % → 99 %) |
| `knowledge_base/tests/test_vector_store.py` | 31 | `knowledge_base/vector_store.py` (91 % → 99 %) |
| `knowledge_base/tests/test_synchronization.py` | 17 | `knowledge_base/synchronization.py` (93 % → 95 %) |

(Pre-existing untracked tests `ai-engine/tests/test_{answers,config,graph_builder,indexer_helpers,rag_detection}.py` and `knowledge_base/tests/test_identity.py` were part of the 645 baseline and were left in place.)

### 5.3 Coverage breakdown (source modules, excluding test/benchmark/tooling files)

Overall source coverage: **84.3 % → 87.2 %**.

| Module | Before | After |
|--------|--------|-------|
| `ai-engine/graph_query.py` | 0 % | **98 %** |
| `ai-engine/rag_pipeline.py` | 24 % | **51 %** |
| `ai-engine/search.py` | 60 % | **96 %** |
| `ai-engine/config.py` | 81 % | **95 %** |
| `ai-engine/lexical_search.py` | 85 % | **94 %** |
| `ai-engine/graph_builder.py` | 83 % | 84 % |
| `ai-engine/indexer.py` | 73 % | 73 % |
| `ai-engine/api.py` | 80 % | 80 % |
| `ai-engine/answers.py` | 95 % | 95 % |
| `ai-engine/hybrid_api.py` | 96 % | 96 % |
| `knowledge_base/chunking.py` | 93 % | **99 %** |
| `knowledge_base/vector_store.py` | 91 % | **99 %** |
| `knowledge_base/synchronization.py` | 93 % | **95 %** |
| `knowledge_base/identity.py` | 100 % | 100 % |
| `knowledge_base/semantic.py` | 97 % | 97 % |
| `knowledge_base/parser.py` | 91 % | 91 % |
| `knowledge_base/graph.py` | 92 % | 92 % |
| `knowledge_base/retrieval.py` | 95 % | 95 % |
| `knowledge_base/metadata.py` | 85 % | 85 % |
| `knowledge_base/scanner.py` | 85 % | 85 % |
| `knowledge_base/validation.py` | 31 % | 38 % |
| `query_system/hybrid_contracts.py` | 100 % | 100 % |
| `query_system/rule_registry.py` | 100 % | 100 % |
| `query_system/rule_engine.py` | 94 % | 94 % |
| `query_system/query_understanding.py` | 93 % | 93 % |
| `query_system/response_presentation.py` | 94 % | 94 % |
| `query_system/graph_engine.py` | 92 % | 92 % |
| `query_system/contracts.py` | 92 % | 92 % |
| `query_system/evidence_aggregation.py` | 89 % | 89 % |
| `query_system/semantic_engine.py` | 91 % | 91 % |
| `query_system/graph_rag.py` | 90 % | 90 % |
| `query_system/generation_safety.py` | 95 % | 95 % |
| `query_system/local_ollama.py` | 89 % | 89 % |
| `query_system/orchestration.py` | 94 % | 94 % |
| `query_system/router.py` | 85 % | 85 % |
| `query_system/presentation_preview.py` | 71 % | 71 % |

**Lowest remaining source coverage:** `knowledge_base/validation.py` (38 %), `ai-engine/rag_pipeline.py` (51 %), `query_system/presentation_preview.py` (71 %), `ai-engine/indexer.py` (73 %), `ai-engine/api.py` (80 %).

---

## 6. Prioritized Action Plan

**CRITICAL**
1. **Fix the structural-query detector** in `ai-engine/rag_pipeline.py:88-89`. Mis-captured target symbols break the F4 impact/caller/callee features for common phrasings (`is affected if`, `breaks if`, `change impact of`). Reorder the specific pattern ahead of the generic one and/or blacklist stop-words in the captured group. Keep `test_rag_detection.py::test_what_is_affected` as the regression guard (it must go green after the fix).

**HIGH**
2. **Raise coverage on the request surface**: `ai-engine/indexer.py` (73 %) and `ai-engine/api.py` (80 %) — indexing error/prune paths, auth-token rejection, and background-job state transitions are the least-tested high-traffic code.
3. **Add a pytest configuration** (`pytest.ini`/`pyproject.toml` + `conftest.py`) so the suite is discoverable from any working directory and rootdir/`sys.path` behaviour is explicit rather than incidental. Today `pytest` only passes when invoked as `python -m pytest` from the repo root.
4. **Complete `.env.example`** with every environment variable the code reads (`CODEMIND_DATA_DIR`, `CODEMIND_ENABLE_HYBRID`, `CODEMIND_LOCAL_MODEL`, `CODEMIND_AUTO_SYNC`, `CODEMIND_SESSION_TOKEN`, `QDRANT_URL`).

**MEDIUM**
5. **Reconcile the README ↔ extension scope drift** (Docs-sync and Reuse-detection are declared Non-Goals but are implemented and registered as VS Code commands). Either remove them from the extension or log a scope change per `README.md` §5.
6. **Neutralise `ai-engine/test_ai.py`** — rename out of the `test_*` glob or exclude it, so pytest does not collect it and a stray run cannot trigger billed OpenRouter calls.
7. **Extend `knowledge_base/validation.py` coverage** beyond the pure helpers; at minimum smoke-test `run_case`/`main` argument handling and `regression_tests` failure reporting (currently 38 %).
8. **Add offline-runnable extension tests and a headless `npm test` script.** The `test/presentation.test.js` / `test/stabilization.test.js` pattern (module-mocking `vscode`) works without Electron; port more of the `*Suite.js` assertions to it so CI does not depend on downloading VS Code.

**LOW**
9. **Reclaim disk:** remove the stale `ai-engine/venv` (481 MB) and `extension/venv` (14 MB); document `.venv` as the single canonical environment.
10. **Broaden coverage of `query_system/presentation_preview.py` (71 %)** and `query_system/router.py` (85 %) — the lowest-covered query-pipeline modules.

---

*End of report. Generated by the autonomous QA audit; application code was not modified.*
