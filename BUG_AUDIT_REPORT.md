# BUG_AUDIT_REPORT.md

**Repository:** Project-Argus / CodeMind — AI developer knowledge assistant
**Audit type:** Autonomous full-repo audit & remediation
**Branch:** `om`
**Baseline (verified this session):**

| Signal | Command | Result |
|---|---|---|
| Python suite | `.venv/bin/python -m pytest -q` | **529 passed, 714 subtests passed, 2 warnings** (353.98s) |
| Ruff | `.venv/bin/ruff check ai-engine knowledge_base query_system` | 317 findings (313 unique after `--exclude`) |
| Mypy | `.venv/bin/mypy ai-engine knowledge_base query_system` | **54 errors in 16 files** |
| TypeScript | `npx tsc -p ./ --noEmit` | **clean** (strict mode on) |
| Extension tests | `npm run compile && node --test test/` | **39 passed** |

> **Note on a prior report.** `REPO_AUDIT_REPORT.md` (dated 2026-10-14) documents a
> failing test `test_rag_detection.py::TestImpactDetection::test_what_is_affected`
> and a structural-query regex defect in `ai-engine/rag_pipeline.py:88`. **Both are
> already fixed on this branch** — `_STRUCTURAL_PATTERNS` is now ordered
> specific-before-generic and `_STRUCTURAL_NON_TARGETS` blacklists stop-words such
> as `if`. The suite is fully green. That report is retained as history.

---

## Severity legend

- **CRITICAL** — corrupts output, loses data, or bypasses a security control.
- **HIGH** — wrong result on a reachable path, or crashes on realistic input.
- **MEDIUM** — latent defect, maintenance hazard, or contract drift.
- **LOW** — hygiene: dead imports, style, annotation gaps.

---

## Ledger

### Verified clean (no action)

| Area | Evidence |
|---|---|
| Structural-query detection (`_detect_structural_query`) | Reordered patterns + stop-word blacklist; regression test green |
| Extension TypeScript build | `tsc -p ./` exit 0 under `strict: true` |
| Extension test suite | 39/39 pass after `npm run compile` |
| Blanket `except Exception` in library code | 21 hits reviewed — all are deliberate degradation boundaries around optional subsystems (Qdrant/LLM/watchdog), each logging or setting an explicit failure state |
| `S105`/`S106` "hardcoded password" | False positives on names like `SESSION_TOKEN_FILENAME`, `token_accounting` |
| mypy `str | None` cluster in `knowledge_base/graph.py:278-299` | `symbols_by_file` is `dict[str, list[sqlite3.Row]]`; mypy loses `Row`'s dynamic `__getitem__`. Guarded by `if target` / `if target_node`. Runtime-safe |
| mypy `"int" has no attribute "items"` (`graph_query.py:122`, `indexer.py:487`) | `get_graph_summary()` returns `dict[str, Any]`; mypy infers `int` from the sibling key. Runtime-safe |

*These are recorded so a future session does not "re-fix" them.*

### Open findings

_(populated during Phase 3 — each entry gets `[Severity]`, `[File:Line]`, `[Root Cause]`, `[Planned Fix]`, and the regression test that proves it)_
