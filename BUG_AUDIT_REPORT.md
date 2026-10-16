# BUG_AUDIT_REPORT.md

**Repository:** Project-Argus / CodeMind — AI developer knowledge assistant
**Audit type:** Autonomous full-repo audit & remediation
**Branch:** `om`
**Baseline (re-verified this session, before any edit):**

| Signal | Command | Result |
|---|---|---|
| Python suite | `.venv/bin/python -m pytest -q` | **529 passed, 714 subtests passed, 2 warnings** (407.85s, exit 0) |
| Ruff (bug rules) | `.venv/bin/ruff check --select F,E9,B,E722,S --ignore E501 --statistics ai-engine knowledge_base query_system` | 125 findings |
| Mypy | `.venv/bin/mypy --ignore-missing-imports --follow-imports=silent ai-engine knowledge_base query_system` | **49 errors in 12 files** |
| TypeScript | `npm run compile` (`tsc -p ./`, strict) | **clean** (exit 0) |
| Extension tests | `npm test` (`node --test`, 5 files) | **48 passed, 0 failed** |
| Dependencies | `.venv/bin/python -m pip_audit -r requirements.txt` | **no known vulnerabilities** |

> The Python baseline matches the previous revision exactly (529 / 714), so the suite
> is green and not degraded before this pass.
>
> **Note on a prior report.** `REPO_AUDIT_REPORT.md` (2026-10-14) documents a failing
> test `test_rag_detection.py::TestImpactDetection::test_what_is_affected` and a
> structural-query regex defect in `ai-engine/rag_pipeline.py:88`. **Both are fixed
> on this branch.** That report is retained as history. The defect *class* was not
> fixed everywhere — see F1/F2 below.

---

## Severity legend

- **CRITICAL** — corrupts output, loses data, or bypasses a security control.
- **HIGH** — wrong result on a reachable path, or crashes on realistic input.
- **MEDIUM** — latent defect, maintenance hazard, or contract drift.
- **LOW** — hygiene: dead imports, style, annotation gaps.

---

## Ledger

### Confirmed findings

#### F1 — [HIGH] `knowledge_base/retrieval.py:52` — impact query resolves the query keyword as the target symbol

**Root cause.** The `impact` alternation mixes the specific phrase (`impact of`) with the
bare verbs (`change|modify|changing|modifying`). `plan_query` uses `re.search`, i.e. the
**leftmost** match wins, so in `"change impact of login"` the verb at offset 0 consumes the
following word — the query keyword `impact` — as the operand. The guard on line 64 only
checks that *some* impact-ish word occurs in the query, so the mis-capture is committed
instead of rejected. This is the same defect class that was already fixed in
`ai-engine/rag_pipeline.py` (`_STRUCTURAL_PATTERNS` ordering + `_STRUCTURAL_NON_TARGETS`);
the fix never reached this sibling path.

**Observed (executed, `.venv/bin/python`):**

| query | `plan.intent` / `plan.reference` | correct? |
|---|---|---|
| `change impact of login` | `impact` / `'impact'` | ✗ expected `'login'` |
| `Change impact of login` | `impact` / `'impact'` | ✗ expected `'login'` |
| `impact of changing validate` | `impact` / `'changing'` | ✗ expected `'validate'` |
| `what is affected if we modify login?` | `impact` / `'login'` | ✓ |
| `what happens if I change generate_token?` | `impact` / `'generate_token'` | ✓ |
| `impact of function validateToken` | `impact` / `'validateToken'` | ✓ (asserted in `test_retrieval.py:15`) |

`knowledge_base/test_retrieval.py:15-17` already asserts the neighbouring forms, and
`ai-engine/tests/test_rag_detection.py:20` asserts `"Change impact of login" -> login` for the
legacy detector — so this phrasing is a supported input, not an edge case.

**Impact.** `plan_query` drives the default retrieval path (`/search/code`, `/retrieve`,
`/ask`, and extension chat with hybrid mode off). Because `plan.reference` is non-empty, the
fallback identifier recovery on lines 80-89 is skipped, `literal` search is suppressed
(`retrieval.py:402`), and the retriever looks up a definition for a symbol named `impact` and
walks reverse `CALLS` edges from it — so a change-impact question about `login` returns
`not_found` or unrelated evidence.

**Proposed fix (minimal, impact-intent only).** Iterate the candidates with `re.finditer`
and prefer the first capture that is **not** a bare query keyword
(`impact(s)`, `effect(s)`, `change(s)`, `changing`, `modify`, `modifying`, `affect(s)`,
`affected`, `break(s)`, `breaking`) when it is unquoted and a later candidate exists;
keep the leftmost capture if *every* candidate is a keyword (so `"impact of change"` still
resolves a real `change` symbol). Callers/callees/imports/dependents patterns are untouched.

**Regression test.** `knowledge_base/test_retrieval.py` — parametrized family (verb-first,
noun-first, "what is the impact of changing X", quoted `change`, trailing punctuation).

---

#### F2 — [HIGH] `query_system/query_understanding.py:226-240` — same class: the real operand is dropped, a keyword entity is created

**Root cause.** The `target_prefix` alternation contains the bare verbs
(`change|modify|changing`), so for `"change impact of login"` the lookahead anchored at
`change` captures `impact` as an entity. The genuine operand `login` is preceded only by the
preposition `of`, which no rule in `parse_question` covers, so it is never extracted. The
existing rejection list `_IGNORE` (line 27) already carries the other intent words
(`callers`, `callees`, `dependencies`, `contract`, `signature`, `tests`, `overview`, …) but
not the impact verbs.

**Observed (executed):**

| query | `[e.name for e in parse_question(q).entities]` | correct? |
|---|---|---|
| `change impact of login` | `['impact']` | ✗ expected `['login']` |
| `change impact of the login function` | `['impact', 'login']` | ✗ spurious `impact` |
| `impact of login` | `[]` | ✗ operand never captured |
| `what is affected if we modify login?` | `['login']` | ✓ |
| `what breaks if I remove parse` | `[]` | ✗ coverage gap (degrade, not wrong) |

**Impact.** The opt-in hybrid pipeline (`CODEMIND_ENABLE_HYBRID=1`, `/v1/hybrid/*`) and the
Phase-9 preview resolve `impact` as the target symbol and produce a "no verified match" /
clarification answer for a question that names a real symbol. Same user-visible failure as F1
in the newer pipeline.

**Proposed fix.** (a) Add the noun-phrase forms (`impact(?:s)? of`, `effect(?:s)? of`) to
`target_prefix` so the operand after the preposition is captured; (b) in that loop the
candidate-selection rule from F1 (skip an unquoted impact keyword capture when a later
candidate exists), mirroring `_STRUCTURAL_NON_TARGETS` in `ai-engine/rag_pipeline.py`.
`_IGNORE` is *not* broadened, so `"who calls modify"` keeps resolving `modify`.

**Regression test.** `query_system/test_understanding.py` — parametrized family + a
boundary block (empty/whitespace/control chars/punctuation-only/already-covered forms).

---

#### F3 — [MEDIUM] `ai-engine/indexer.py:184-192` — assigned-secret redaction misses unquoted values and JSON keys

**Root cause.** `_ASSIGNED_SECRET` requires a quote character **immediately** after `[:=]`
and a word boundary that ends the name, so (a) unquoted assignments (YAML/INI/`.env` style)
and (b) JSON/YAML keys quoted on both sides are never matched; the name alternation also
rejects env-style prefixes (`DATABASE_PASSWORD`, `AWS_SECRET_ACCESS_KEY`).

**Observed (executed, `_redact_secrets` on the exact text that is chunked and stored):**

| input line | result | expected |
|---|---|---|
| `  password: hunter2-total-secret` | unchanged | `[REDACTED]` |
| `password = hunter2-total-secret` | unchanged | `[REDACTED]` |
| `DATABASE_PASSWORD=hunter2-in-the-plain` | unchanged | `[REDACTED]` |
| `{"apiKey": "hunter2-total-secret"}` | unchanged | `[REDACTED]` |
| `API_KEY = "hunter2-total-secret"` | `[REDACTED]` ✓ | — |
| `db:\n  api_key: AKIA...` | `[REDACTED]` ✓ (value pattern) | — |

**Impact.** `_redact_secrets` is the redaction applied to everything **persisted** — the
FAISS/Qdrant payload `code_snippet` and the SQLite metadata — and it is what
`answers.evidence_for`, `lexical_search` and `documentation._safe` display. The presentation
layer (`query_system/generation_safety.py`, hybrid path only) catches some of these, so the
gap is invisible in hybrid mode but present in the default chat path and in the stored index.
Local-only data and no privilege boundary is crossed, hence MEDIUM, but the module's own
"Item 5" assurance does not hold for the most common config syntaxes.

**Proposed fix.** Allow an optional env-style prefix on the name; accept a quoted value
(single or double) *or* an unquoted literal `[A-Za-z0-9_./+=-]{8,}` that terminates the value
(line end, `,`, `;`, `}`, `]`, `#`) so `password = get_password()` is left readable; keep the
existing line-count-preserving replacement for the multiline private-key pattern.

**Regression test.** New `ai-engine/tests/test_redaction.py` — parametrized over the syntax
family above, plus negative assertions (`get_password()` assignment, `MAX_TOKEN_SIZE = 1024`,
`db_url:` non-secret) that must **not** be redacted.

---

## Resolutions (Phase 3)

### F1 — RESOLVED, commit `3eb0e2d`

- **Change.** `knowledge_base/retrieval.py`: added `IMPACT_KEYWORDS` and `_impact_match()`,
  which searches the impact pattern **overlapping** (lookahead per offset, then re-match
  there) and returns the first candidate whose operand is not an unquoted impact keyword.
  If every candidate is a keyword the leftmost is kept, so a symbol genuinely named
  `change` still resolves for `impact of change`. The callers/callees/imports/dependents
  branches still use plain `re.search`; only `intent=='impact'` routes through the helper.
- **Verified after.** `change impact of login` → `('impact','login')`; `Change impact of
  login` → `('impact','login')`; `impact of changing validate` → `('impact','validate')`;
  `impact of change` → `('impact','change')`. The five previously-correct forms are
  unchanged.
- **Tests.** `knowledge_base/test_retrieval.py`: 8 passed / 33 subtests (RED first: 5
  sub-failures, then a deliberate split — `change login`/`modify login` keep `intent=explore`,
  which the impact guard requires and which is now asserted explicitly rather than assumed).

### F2 — RESOLVED, commit `abb4d63`

- **Change.** `query_system/query_understanding.py`: added the noun forms
  (`impact(?:s)? of|effect(?:s)? of`) to `target_prefix`; collected the prefix-anchored
  candidates; reject a captured **unquoted impact keyword when it sits directly after impact
  vocabulary** (`IMPACT_ALTERNATION`, longest-first) — the verb's object in "change impact
  of X", the gerund in "impacts of modifying X".
- **Correction made during implementation.** The first version keyed the rejection on "does
  this clause already carry a real operand". That was wrong: the real operand
  (`validateToken`) is only discovered by a *later* pass, so the gerund `modifying` survived
  and became the target, breaking
  `test_phase10_integration.py::test_plural_impact_and_related_tests_use_existing_rules_without_generation`.
  The anchor-keyed rule is order-independent and fixes it. That case is now guarded by
  `test_impact_gerund_never_becomes_the_target`.
- **Verified after.** `change impact of login` → `['login']`; `Impact of login` → `['login']`;
  `change impact of the login function` → `['login']` (spurious `impact` gone);
  `impacts of modifying validateToken()` → `['validateToken']`, no gerund.
  Preserved: `Who calls modify` → `['modify']`, `modify login`/`change login` → `['login']`,
  `Impact of changing validateToken` → `['validateToken']`, the compound-clause
  `entity_indices == [0]` contract, and the pasted-comment/fenced-code cases.
- **Deliberate behaviour change, stated plainly.** `Impact of change` yields `[]` (unchanged
  from before the fix — the keyword-only operand stays unresolved rather than being
  invented). The documented escape hatch is the quoted form `Impact of \`change\`` → `['change']`.
  F1 and F2 therefore differ here: `plan_query` keeps the leftmost keyword, `parse_question`
  drops it. Both are safe degrades; the difference is recorded rather than papered over.
- **Tests.** `query_system/test_understanding.py`: 10 passed / 31 subtests (RED first: 6
  sub-failures across the four wrong inputs).

### F3 — RESOLVED, commit `99e956f`

- **Change.** `ai-engine/indexer.py`: the name now permits an environment prefix and a
  multi-word suffix (`DATABASE_PASSWORD`, `STRIPE_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`) and
  tolerates a closing quote on container keys (`{"apiKey": …}`); the value may be quoted or
  unquoted. An unquoted value is accepted only when it cannot be read as code — a negative
  lookahead rejects a bare identifier or attribute path. Replacement moved into
  `_redact_assigned` so the name, separator and quote are preserved verbatim.
- **Verified after.** all four previously-leaking syntaxes now mask the value; the
  multiline private-key replacement still preserves the line count.
- **Known limitation, asserted in the test.** A pure-alphanumeric unquoted value
  (`password: swordfish123`) is deliberately left alone, because the only way to catch it is
  also to mask ordinary source assignments such as `password = get_password()` and
  `token = os.environ`, which would destroy the evidence CodeMind exists to return. The
  value-shaped patterns (`AKIA…`, `gh[pousr]_…`, `sk-…`, `xox…`) still cover real
  credential formats. Flagged for the owner as a policy choice, not hidden.
- **Tests.** `ai-engine/tests/test_redaction.py` (new file): 8 passed / 33 subtests (RED
  first: 10 sub-failures — 4 unquoted, 4 env-style, 2 quoted-container-key).

### Deferred (not implemented, by owner-controlled scope)

The LOW hygiene bundle below was **not** actioned: the approved scope was F1-F3. It remains
open, unchanged, and is listed with its reasoning so the next session can pick it up or
dismiss it. Note `L6`/`L7` sit in files F1-F3 did edit, so their line numbers in this table
refer to the pre-fix revision.

---

#### LOW — hygiene bundle (still open; no behavioural change)

| # | File:line | Rule | Note |
|---|---|---|---|
| L1 | `ai-engine/api.py:54` | F401 | `IndexingError`, `_discover_files` imported, unused |
| L2 | `ai-engine/api.py:698` | F841 | `chunk_index` parsed for validation then never used |
| L3 | `ai-engine/graph_query.py:121,124,132` | F541 | f-strings without placeholders |
| L4 | `knowledge_base/semantic.py:3` | F401 | `json` imported, unused |
| L5 | `ai-engine/documentation.py:40` | S324 | `sha1` used as a 40-hex document ID, not a security primitive — **no action recommended** (changing it invalidates every stored document ID); would only get a comment |
| L6 | 10 non-test sites | B905 | `zip()` without `strict=`; each pairs same-length sequences, so hygiene only |
| L7 | `ai-engine/indexer.py:580` | B904 | `raise SystemExit(1)` inside `except IndexingError` loses the cause chain (`from exc`) |
| L8 | 12 files | mypy | 49 errors, dominated by narrowing noise (see dismissals); genuine gaps are missing annotations on `graph.py:458 queue`, `router.py:74 operations/depths`, `query_understanding.py:168/253/315 mentions/events/requirements` |

---

### Investigated and dismissed

| Signal | Sites | Why dismissed |
|---|---|---|
| `S608` string-built SQL | `graph.py:394,468`, `metadata.py:200,329`, `parser.py:358`, `retrieval.py:169,184,285`, `semantic.py:66`, `graph_engine.py:255`, `rule_engine.py:374` | Every interpolation is a literal column/table name from a module-level constant (`'source_id' if reverse else 'target_id'`, `for table in ('graph_relationships','graph_unresolved')`) or a `','.join('?' …)` placeholder run. All values are bound parameters. No user text reaches the SQL string. |
| `S603`/`S607` subprocess | `api.py:197`, `graph.py:79`, `scanner.py:58`, `validation.py:39,283,333` | argv lists, never `shell=True`; read-only `git` with `core.fsmonitor=false`, `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`, 5-180 s timeouts; `node`/`python` resolved locally. |
| `S105`/`S106` hardcoded password | `config.py:51`, `generation_contracts.py:73` | False positives on the *names* `SESSION_TOKEN_FILENAME` and `token_accounting`. |
| `S110` try/except pass | `api.py:597`, `indexer.py:300` | Deliberate, commented degradation boundaries (graph enrichment optional; payload index already exists). |
| `S310` urlopen | `answers.py:91` | Hardcoded `http://127.0.0.1:11434/api/chat`; no user-controlled URL or scheme. |
| `S324` sha1 | `documentation.py:40` | Content-addressing ID; the format (`[a-f0-9]{40}`) is validated on load. Kept (see L5). |
| `B023` loop-variable lambda | `validation.py:411` | Lambda is called inline in the same iteration. |
| `B905` zip | 20 sites | Same-length sequences; adding `strict=True` would turn silent truncation into a crash on inputs that currently work. |
| `RUF021` `a or b and c` | 18 sites (`graph.py:98-100`, `scanner.py:103,113`, `evidence_aggregation.py:37-193`, `graph_rag.py:312`, `local_ollama.py:40`, …) | Precedence is the intended reading in each guard (`x is None or kind == k and res != r`); readability only. |
| `RUF059` unused unpack | `query_understanding.py:498,540` | `status` from `GraphReader.module/symbol` is redundant with `assign()`'s own empty-candidate handling. |
| mypy `union-attr` cluster | `response_presentation.py:156-182`, `presentation_preview.py:80-93`, `parser.py:248-249` | Verified field-by-field: `RequirementCoverage` has `limitations/truncated/state/original`, `AnswerCoverage` has `order/claim_ids/supporting_requirement_ids`; each ternary branch only touches fields the selected type owns. Loop-variable reuse confuses mypy, not runtime. |
| mypy `attr-defined` cluster | `graph_query.py:122,125`, `indexer.py:487`, `graph_presentation.py:44-48` | `get_graph_summary()` returns `dict[str, Any]`; mypy unions a sibling key's `int`. `graph_presentation` builds hashable dict keys (tuple or str), which is valid. |
| Prompt injection into the LLM prompt | `rag_pipeline._build_context_block`, `answers.answer_question` | Already mitigated: explicit "untrusted repository data" system prompt, fence escalation via `_fence_for` (fence longer than any backtick run), control-char stripping, evidence re-hash after generation. |
| XSS in the chat webview | `extension.ts:860` (`innerHTML`), `914/919` (`insertAdjacentHTML`) | `responsePresentation.renderResponseBody` routes every interpolated value through `escapeDisplay`; `debugFooter` is produced only from fixed label literals in `debugAnswerEngine.ts`; the webview CSP is `default-src 'none'; script-src 'nonce-…'`. No untrusted sink today. |
| Path traversal | `identity.relative_path`, `api._validate_root`, `_semantic_search`, `sourceNavigation.verifiedSourcePath`, `verifiedPreviewSource` | Executed probes: `..`, `a/../../b`, `/abs`, `a\\b`, `.`, `a//b`, root path, `~/.ssh`, `/tmp/../../..`, NUL all rejected; payload paths are `realpath`-contained. Legit paths pass. |
| Dead code / TODO markers | whole tree | No `TODO`/`FIXME`/`NotImplementedError`/`HACK` in project sources. |
| Dangerous constructs | whole tree | No `os.system`, `eval`, `exec`, `pickle`, `yaml.load`, `shell=True`, `verify=False`, `md5`, insecure RNG in project code (venvs excluded). |
| Dependencies | `requirements.txt` | `pip-audit` (installed into `.venv` only, deliberately **not** added to the frozen lockfile): *No known vulnerabilities found*. Extension has no runtime `dependencies`; its devDependencies are build-time only. |

*These are recorded so a future session does not "re-fix" them.*

---

## Verification (Phase 4)

| Gate | Command | Baseline | Final |
|---|---|---|---|
| Python suite | `.venv/bin/python -m pytest -q` | 529 passed / 714 subtests, exit 0 | **545 passed / 782 subtests, exit 0** |
| TypeScript | `npm run compile` (`tsc -p ./`, strict) | clean | **clean** (no TS file touched) |
| Extension tests | `npm test` (`node --test`, 5 files) | 48 passed / 0 failed | **48 passed / 0 failed** |
| Ruff (bug rules, changed files) | `ruff check --select F,E9,B,E722,S` | — | **no new findings** (7 hits, all pre-existing and line-shifted) |
| Mypy (changed sources) | `mypy --ignore-missing-imports --follow-imports=silent` | 11 errors in the 3 files | **same 11, none new** (line-shifted) |

The +16 tests / +68 subtests are exactly the added regression tests; no pre-existing test was
weakened, skipped, or deleted. The only new static-analysis signal is `S105` on the test
fixture constant `SECRET = "hunter2-total-secret"` in `test_redaction.py` — the same
false-positive family already recorded above.

Full suite time 331.75 s (0:05:31).

### Modified files

| File | Change |
|---|---|
| `knowledge_base/retrieval.py` | F1: `IMPACT_KEYWORDS` + `_impact_match()`; impact branch routes through it |
| `knowledge_base/test_retrieval.py` | F1 tests (4 added; 2 existing cases kept) |
| `query_system/query_understanding.py` | F2: noun forms in `target_prefix`; anchor-keyed keyword rejection |
| `query_system/test_understanding.py` | F2 tests (3 added) |
| `ai-engine/indexer.py` | F3: `_ASSIGNED_SECRET` rewrite + `_redact_assigned` |
| `ai-engine/tests/test_redaction.py` | F3 tests (new file, 8 tests) |
| `BUG_AUDIT_REPORT.md` | this ledger |

No dependency was added to `requirements.txt`; `pip-audit` was installed into `.venv` only.

### Commit history

```
99e956f fix(redaction): mask unquoted and env-style assigned secrets (F3)
abb4d63 fix(understanding): stop the impact keyword becoming the resolved target (F2)
3eb0e2d fix(retrieval): capture the real operand in change-impact queries (F1)
19b7835 docs: record verified audit findings F1-F3 with executed evidence
```

`REPO_AUDIT_REPORT.md` is superseded as the live ledger (it still describes the
`test_what_is_affected` failure that was fixed two commits before this pass). It is retained
as history.

**STATUS: Phase 1-4 complete. F1, F2, F3 fixed, tested and pushed. LOW hygiene bundle open
by owner-controlled scope.**

