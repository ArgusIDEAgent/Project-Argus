# PRESENTATION_READINESS.md

**Purpose:** what to do before the external examiner sees this, what is provably working, and
what will be asked if you cannot answer it confidently.
**Method:** every "WORKS" / "BROKEN" line below was executed against the running engine on
2026-10-16, from the committed revision. Nothing here is inferred from reading code.

---

## 0. The three things that will decide the demo

| # | Blocker | Why it matters | Fix | Effort |
|---|---|---|---|---|
| **B1** | **Answer prose is missing.** `answerMode` is `evidence`, not a written answer. | README's demo says *"grounded answer … explained in plain English"*. Right now you get a ranked evidence list with a warning banner. This is the single most visible gap. | Install Ollama + a model, or present it honestly as evidence-ranked retrieval | ~20–30 min + model download |
| **B2** | **You cannot index the bundled `sample_code/`.** | It is one file, and it is refused outright (see §3.1). The README demo tells you to use it. | Use `~/codemind-demo` (created for you, see §4) | done |
| **B3** | **README §2/§3 describe a different system than the code.** | §3 forbids two features that are implemented; §2 promises a Python graph that does not exist in the indexed path. An examiner with the README in hand will catch this. | Decide your story *before* the demo (see §5) | your call |

---

## 1. Verified working (executed, with evidence)

**Engine starts and is secure**
```
.venv/bin/python ai-engine/api.py
→ POST /hello with token       → {"reply": "Python AI Engine connected..."}
→ POST /hello WITHOUT token    → HTTP 401
→ session token file           → -rw------- (0600)
→ binds 127.0.0.1 only
```

**Indexing — incremental, fast, correct**
```
register ~/codemind-demo → repoId 9371267f8a55b3c8f9dcb5e3
index (full)             → completed, 12 files, 47 chunks
graph                    → 64 nodes / 138 resolved relationships / 8 unresolved
edit ONE file            → 1 to index, 11 unchanged, 4 points upserted, detected in 3 seconds
```

**Structural queries — real call chains, real hop distances**
```
"What happens if I modify signToken?"
  → src/jwt.ts:25        authenticateRequest          (distance 1)
  → src/jwt.ts:29        guardProtectedResource       (distance 2)   ← 2-hop chain
"What happens if I modify validateCredential?"
  → src/service.ts:13    requestLogin, methodTest     (distance 1)
  → src/controller.ts:6  applicationEntry             (distance 2)
"Who calls validateCredential?"
  → src/dynamic.ts:12    nestedCheck
  → src/service.ts:13    requestLogin, methodTest
```
Every result carries `graphVerified: True` and the correct caveat
*"not proof of runtime impact"*.

**Refuses to guess when ambiguous** (this is a strength — say it out loud)
```
"Who calls authorizeRequest?"
  → status = ambiguous
  → "Symbol is ambiguous. Use its qualified name and/or a file filter."
  → returns BOTH candidate definitions rather than picking one
```

**Uncertainty is explicit, not hidden**
```
"What happens if I modify signToken?"
  → warning: "Graph traversal reached its depth/work/result bound; results may be incomplete."
```

**Auto-refresh on file save**
```
append a new function to src/auth.ts → re-indexed within 3 seconds → function immediately
findable ("Where is demoRefreshProbe defined?" → src/auth.ts:14) and its CALLS edge present
in the graph (demoRefreshProbe -> validateCredential)
```

**Static analysis + tests**
```
.venv/bin/python -m pytest -q     → 545 passed, 782 subtests, exit 0
extension: npm run compile        → clean (strict TS)
extension: npm test               → 48 passed, 0 failed
pip-audit -r requirements.txt     → no known vulnerabilities
```

**Evaluation artifacts exist** (README F7 asks for a committed evaluation table)
```
knowledge_base/VALIDATION.md
knowledge_base/benchmarks/s1_resolver_results.json
query_system/benchmarks/phase3_results.json, phase10_real_results.json, phase10_mocked_results.json
knowledge_base/benchmark_semantic.py, query_system/benchmark_*.py
```

---

## 2. What is NOT working / not present

| Feature | State | Detail |
|---|---|---|
| **Written answers (F5 prose)** | ⛔ absent | Ollama is **not installed** (`which ollama` → not found). Everything falls back to an evidence list. |
| **Python AST graph (F3)** | ⚠️ built, but not wired into the current path | `knowledge_base/parser.py LANGUAGES` supports **only** `.js/.jsx/.mjs/.cjs/.ts/.mts/.cts/.tsx` — no `.py`. A *working* Python NetworkX graph does exist for `sample_code` (`graph.json`, 24 nodes / 26 edges) and is reachable via `graph_query.py --graph-file <path>`, but `config.py:240` deliberately refuses it whenever a versioned snapshot exists, so the API and extension never see it. So: implemented, superseded, not deleted. |
| **JS/TS graph** | ✅ present | The exact thing README §3 declares a Non-Goal. |
| **Docs generation** | ⛔ blocked by design | `/docs/*` returns *"awaiting the product scope decision"* unless `CODEMIND_ENABLE_DOCS=1`. README §3 says OUT; the extension still ships **Open Documentation Overview** and **Sync Documentation** buttons. |
| **Reuse detection** | ⛔ explicit stub | `/analysis/reuse` → `{"mode":"not_implemented","warning":"Reuse analysis is not implemented yet."}`. README §3 says OUT; the extension still ships a **Check for Reusable Code** button. |
| **`ai-engine/sample_code/`** | ⛔ unusable | One file (`auth_service.py`), and indexing it is refused (§3.1). |
| **Installed VSIX** | ⚠️ stale | `~/.vscode-oss/extensions/codemind-local.codemind-extension-0.4.1` is dated **2026-10-13**. Running from source with **F5** uses the current build. |

---

## 3. Traps — verified, and how they will present

### 3.1 You cannot index a directory that is not its own git repository

**Symptom the examiner sees:** they point CodeMind at any folder and the job **fails**.

Observed, indexing `knowledge_base/fixtures/phase8/typescript`:
```
ValueError: Git is required to evaluate ignore rules
→ job state = failed
```
Cause chain (all verified):
- `knowledge_base/scanner.py:65 _git_context` requires `git rev-parse --show-toplevel` to equal
  the target directory **exactly** → any subdirectory inside a repo reports `git = False`
- `scanner.py:76 _has_gitignore` walks the tree and finds *any* `.gitignore` → the fixture
  has one
- `synchronization.py:56` → `if not git and _has_gitignore(root): raise ValueError(...)`

So the bundled TypeScript fixture **cannot be indexed in place**. A standalone git repo works
(proved: `~/codemind-demo` indexed fine).

**What to say if asked:** "Ignore rules are evaluated through git, and the engine refuses
rather than silently indexing files git would ignore. Sub-directories of a repository are
rejected because git's ignore semantics are defined at the repository root."

### 3.2 The bundled TypeScript fixture has no git metadata

Same root cause as 3.1 — it lives *inside* the Argus repo, so `--show-toplevel` returns the
Argus root, not the fixture root.

### 3.3 `ai-engine/sample_code/` is a one-file repo

The README demo step 1 tells you to select a repository; `auth_service.py` alone cannot show a
multi-file impact chain. And indexing it is refused (no `.git` of its own).

### 3.4 The demo's flagship question returns nothing on `sample_code`

```
"What happens if I modify verify_token?"  → status = not_found, 0 results
```
Not a bug — `verify_token` has **zero callers** in a single-file repo, so there is no impact
chain to show. It looks like a failure. Use the multi-file demo repo instead, where the same
class of question gives a 2-hop chain.

### 3.5 Two visible buttons lead to stubs

`codemind.checkReuse` → *"Reuse analysis is not implemented yet."*
`codemind.syncDocs` → *"awaiting the product scope decision"*.

Do not click these. If you do, the honest framing is: *"declared out of scope in the README,
retained in the UI from an earlier iteration."*

### 3.6 The installed extension is from 2026-10-13

If you launch the **installed** CodeMind rather than the development host, you are running
three-day-old code. Demo via **F5** (Extension Development Host) from the repo root.

### 3.7 `graph_query.py` refuses the sample repo's graph

```
python graph_query.py --repo-id <sample> --stats
→ "Knowledge graph not found"
```
because `config.find_graph_file` deliberately returns `None` when `active_snapshot.json` exists
(`config.py:240` — *"The old NetworkX file cannot represent a versioned JS/TS graph"*). The data
is there (`graph.json`, 24 nodes/26 edges) but the CLI path is intentionally closed. Use
`--graph-file <path>` to prove the legacy Python graph exists.

---

## 4. The demo repo (created and verified for you)

**Path:** `/home/babatunde/codemind-demo` — a standalone git repo, 12 files.
It is the `phase8/typescript` fixture **plus** a new `src/jwt.ts` I added so the README's own
narrative question works, wired into `src/controller.ts` to create a real multi-hop chain:

```
src/jwt.ts:  signToken → verifyToken → authenticateRequest
src/controller.ts:  guardProtectedResource → authenticateRequest, requestLogin
```
`git log` shows it as a normal two-commit repo, so you can demo "edit → save → re-index".

**Do not demo `knowledge_base/fixtures/phase8/typescript` in place** — it cannot be indexed
(§3.1).

---

## 5. The 40 % implementation conversation — decide before the viva

README §2 defines seven features, F1–F7. My assessment of what is genuinely built:

| ID | Feature | Genuinely built? | Evidence |
|---|---|---|---|
| F1 | Repository indexing | **Yes** | 12 files indexed; 1-file incremental re-index in 3 s |
| F2 | Semantic code search | **Yes** | hybrid mode returns chunks with path + line range |
| F3 | AST knowledge graph (**Python**) | **Only for JS/TS** — Python version exists but superseded | `parser.LANGUAGES` has no `.py`; a working Python graph exists in `graph.json` via the legacy CLI only |
| F4 | Structural queries | **Yes, for JS/TS** | 2-hop impact chains, verified above |
| F5 | Grounded Q&A | **Retrieval yes, prose no** | graph-augmented retrieval works; prose needs Ollama |
| F6 | VS Code extension | **Yes** | 8 commands, chat panel, 48 tests, strict-TS clean |
| F7 | Evidence (pytest + evaluation) | **Yes** | 545 tests; validation/benchmark artifacts committed |

**6 of 7 are substantially built**, with three honest caveats: F3 is implemented for the wrong
language (JS/TS instead of Python), F5's prose layer needs a model that is not installed, and
F3/F4 are unreachable on `.py` files.

**The thing to resolve before the viva:** README §3 says *"Do not build, stub or scaffold"*
Docs-generation and Reuse-detection — yet both are implemented and both are visible buttons in
the extension. §3 also declares JS/TS call graphs a Non-Goal, yet JS/TS is the only language
the graph supports, and it is the graph that powers your best demo. There is a
**Scope-Change Protocol** in §5: the correct move is a scope-change entry in the log (README
§5.3 says the README and code change in the same commit) rather than leaving the contradiction
for the examiner to find.

**If asked "is this 40 % of the project?"** — the defensible answer is: *"Six of the seven
features defined in §2 are implemented and demonstrated; the knowledge graph is implemented
for JavaScript/TypeScript rather than the Python-only scope §3 originally declared, which is
the one scope change I would record."* Do not claim F5's prose generation works; show the
evidence-ranked output and say the explanation layer is local-model dependent.

---

## 6. Pre-flight checklist (do these in order)

1. `cd /home/babatunde/work/Project-Argus && .venv/bin/python -m pytest -q` → expect
   **545 passed, 782 subtests**.
2. Start the engine: `.venv/bin/python ai-engine/api.py` (keep the terminal visible; the log is
   your evidence if anything misbehaves).
3. Open the **repo root** in VS Code, press **F5**, in the Extension Development Host run
   **CodeMind: Open Chat**.
4. **CodeMind: Select Repository** → `/home/babatunde/codemind-demo` → wait for "indexed".
5. Run §7's script.
6. Keep `BUG_AUDIT_REPORT.md` open in a tab — it documents three real defects that were found
   and fixed, which is exactly the evidence-of-engineering an examiner wants.

---

## 7. The 5-minute script (every step verified)

| # | Do this | Expected result |
|---|---|---|
| 1 | Select `/home/babatunde/codemind-demo`; show it indexed with 12 files | status shows indexed |
| 2 | Ask **"Where is JWT authentication implemented?"** | returns `src/jwt.ts` with line numbers |
| 3 | Ask **"What happens if I modify signToken?"** | 2-hop chain: `authenticateRequest` → `guardProtectedResource` |
| 4 | Ask **"Who calls requestLogin?"** | `src/entry.ts:3`, `src/controller.ts:12` |
| 5 | Ask **"Who calls authorizeRequest?"** | *explicitly reports ambiguity instead of guessing* — point this out |
| 6 | Open `src/jwt.ts`, change `TOKEN_TTL_SECONDS` to `7200`, **save** | re-indexes in ~3 s |
| 7 | Ask **"What happens if I modify verifyToken?"** again | answer reflects the edit |
| 8 | Show the pytest run (545 passed) and `knowledge_base/VALIDATION.md` | F7 evidence |

Step 5 is the strongest single moment: a tool that *refuses* to answer when the target is
ambiguous is more credible than one that always produces something.

---

## 8. Questions you should be ready for

**"How do you stop it hallucinating?"**
It cannot invent evidence. Answers are assembled from verified source ranges whose SHA-256 is
re-checked at read time; stale evidence is discarded rather than shown. Structural claims come
from the static graph and are labelled *"not proof of runtime behaviour"*. `graphVerified` is
true only when a real edge was found.

**"What happens to my secrets?"**
Repository content is redacted before it is embedded or stored. During this audit I found the
redactor missed unquoted config syntaxes (`password: hunter2`) and fixed it, with tests —
that is F3 in `BUG_AUDIT_REPORT.md`.

**"Is this secure?"**
Loopback-only bind, per-session token on every route (verified 401 without it), token file
`0600`, read-only git with `fsmonitor` disabled and a timeout, path-traversal guards, and a
strict CSP in the webview. `pip-audit` reports no known vulnerabilities.

**"What did you not finish?"**
Answer honestly with §2's table: the Python AST graph, the prose generation layer, and Docs /
Reuse (declared out of scope). Do not improvise.

**"Why is there a bug audit report?"**
Because the three defects it describes were real, reachable, and returning wrong answers
silently. Each has a regression test now.

---

## 9. If you only do three things

1. **Install Ollama** (or decide to present evidence-ranked output deliberately) — B1.
2. **Demo `~/codemind-demo`, never `sample_code/`** — B2.
3. **Resolve the README §2/§3 contradiction with a scope-change entry** before the examiner
   reads it — B3.
