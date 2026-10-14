# CodeMind improvement Phase 1 — verification report

Date: 2026-10-14. Baseline commit: `a62f743`. Scope: critical fixes and foundation
stability only. No Phase 2/3 improvement work is included.

## 1. Initial baseline and inspection

The project working tree was clean, including untracked-file status. The actual
checkout directory ends with a space. Changes and checks are scoped to this
project, whose current Git root is the project itself.

This checkout differs from the supplied audit: it contained no
`ai-engine/tests/test_rag_detection.py`, and `.venv` had no pytest. The original
documented unittest suites passed before production edits:

| Suite | Result | Duration |
| --- | --- | --- |
| AI engine, existing Phase 0–10 and stabilization tests | 318 passed | 162.579 s |
| Knowledge base | 62 passed | 5.218 s |
| Query system | 63 passed | 0.087 s |
| Total original tests | 443 passed, zero failed | |

The audit's 806/805/1 baseline was therefore not assumed to describe this
checkout. The named failing regression was created with the required exact
assertion. Running the initial two regression methods against the original
implementation produced five assertion failures (one named failure and four
subtest failures):

| Query | Original graphless result | Required target |
| --- | --- | --- |
| What is affected if we modify login? | impact / `if` | `login` |
| What breaks if I remove parse? | impact / `if` | `parse` |
| Change impact of login | impact / `impact` | `login` |
| What functions does processPayment call? | unresolved | `processPayment` |
| Show dependencies of AuthService | unresolved | `AuthService` |

The other five requested examples already passed graphless detection. An
additional original defect, `Impact on \`login\`` capturing `on`, was reproduced
from the committed implementation and added to the regression matrix.

Graph validation masked the first two bad captures when `if` was absent from the
graph: the detector continued to the specific pattern and found the real target.
The change-impact, functions-does-call and dependencies examples remained
unresolved even with their target present. This distinguishes the detector bug
from a claim that every legacy RAG request was affected.

## 2. Root cause and call paths

The generic `impact|affect|break|change|modif` prefix matcher ran before specific
conditional-impact forms. Its permissive `\w+` capture could consume grammatical
words and then ignore the rest of the question. The backtick fallback likewise
selected the first mention without proving that it was the only target.

`rag_pipeline.ask()` calls this detector before legacy graph retrieval. Current
`api.py` `/ask` uses `answers.answer_question`; opt-in `/v1/hybrid/*` uses
`hybrid_api`, orchestration and the existing query-understanding/executor path.
Neither current answering path calls this legacy detector. No Phase 5/6
understanding or routing implementation was changed.

## 3. Exact detector correction

Only structural recognition inside `ai-engine/rag_pipeline.py` changed:

- Specific conditional and change-impact expressions precede generic forms.
- Complete single-target matching replaces partial-query capture. Balanced
  quotes and identifier syntax prevent malformed or truncated identifiers from
  being accepted. The two missing required grammatical forms are recognized.
- Original case, underscores, digits after the first character, Unicode names,
  dotted qualification and `::` qualification are retained in returned targets.
- A small non-target word set supplements grammatical validation; it is not the
  primary fix. Quoted legitimate names such as `change` remain usable.
- Multiple operands, unsupported expressions, missing targets and malformed
  questions return the existing unresolved tuple `(False, "", "")`.
- With a supplied graph, an exact node or a unique name/substring resolution is
  required. Ambiguous short names are no longer delegated to `find_node`'s
  first-match choice. Graph lookup/storage code itself is unchanged.
- Generic backtick handling is expressed through explicit single-target forms;
  it no longer turns an arbitrary keyword-containing question into a graph
  query for its first quoted identifier.

The public tuple shape and callers/callees/impact/members query types are
unchanged. Existing retrieval, generation, provenance and redaction code is
unchanged. Twenty previously passing representative forms were checked against
the committed detector and remain passing.

## 4. Regression coverage

`ai-engine/tests/test_rag_detection.py` adds eight unittest methods using
data-driven subtests. It preserves the named `TestImpactDetection.test_what_is_affected`
assertion. Coverage includes all ten required queries, both graphless and
graph-backed target checks, existing caller/callee/impact/member forms, paired
quotes, original casing, underscore/digit/qualified/Unicode identifiers, absent
targets, stop words, malformed expressions, ordinary questions, multiple
identifiers and graph ambiguity.

The focused hybrid compatibility test patches the legacy detector to raise,
then successfully runs pinned `QueryUnderstandingEngine.normalize()` for caller,
callee, impact, qualified-method and multiple-target requests. Existing complete
Phase 5/6 and authenticated Phase 10 tests also remain in the full suites.

`ai-engine/tests/test_manual_smoke.py` adds five methods: safe import, mandatory
opt-in even with a key, missing-key refusal before provider import, a fixed
legacy request through a fake provider, and sanitized provider-error output.
The authorized branch is tested only with a fake provider.

Final focused verification: **13 passed, 313 subtests passed**, 2.66 s. Existing
assertions were not deleted, weakened or skipped. New test methods increase the
suite from 443 to 456; subtests are reported separately, not added to that count.

## 5. Legacy external-LLM smoke isolation

Only historical plans/progress and saved stabilization evidence referenced
`test_ai.py`; no production importer or runner depended on it. The utility was
moved, not discarded, to `ai-engine/manual/legacy_openrouter_smoke.py` and labeled
in `ai-engine/manual/README.md`. Historical reports retain their original paths.

Importing the module loads neither dotenv nor LiteLLM. Execution refuses with
exit code 2 unless `--allow-external-llm` is explicitly supplied. This gate runs
before credential loading and provider import. Missing credentials return 1
without importing LiteLLM. Provider errors are not printed because they may
contain request details. The existing fixed AST prompt/model remain available
for intentional manual use. No external model inference was executed.

The renamed file does not match test discovery; the manual directory is also
explicitly excluded. Even an explicit collection attempt cannot execute its
guarded `main()` merely by importing it. The separate historical RAG CLI retains
its provider behavior; changing production provider selection is outside scope.

## 6. Pytest configuration and reproducibility

Pytest was installed into the existing `.venv` only after the user's explicit
approval. `requirements-dev.txt` pins pytest 9.1.1 separately from unchanged
runtime requirements. Installed development-tool dependencies were iniconfig
2.3.1, pluggy 1.6.0 and pygments 2.21.0; packaging 26.3 was already installed.

Before configuration, both whole-project collection styles happened to collect
445 tests (443 original plus the first two new regressions). Focused console
collection failed with `ModuleNotFoundError: rag_pipeline`: whole-project import
order had incidentally supplied source paths. No blanket invocation-style
failure is claimed.

`pytest.ini` explicitly selects `ai-engine/tests`, `knowledge_base` and
`query_system`; its only import roots are `.` and `ai-engine`. Existing prepend
collection handles sibling fixture modules. Manual utilities, environments,
fixtures, benchmark/generated/temporary directories are excluded. Shared
`conftest.py` adds no `sys.path` manipulation.

The canonical root command is `.venv/bin/python -m pytest`; `.venv/bin/pytest`
uses the same configuration. `TESTING.md` documents both, focused selection and
the existing unittest/Node/extension commands.

LiteLLM was observed attempting a model-price metadata fetch during the initial
guarded reproduction; it was blocked, and it fell back to bundled metadata.
Pytest now forces the library's `LITELLM_LOCAL_MODEL_COST_MAP=True` before test
collection. Its per-test guard blocks external DNS/connect/connect_ex and fails
teardown even when an attempted connection is caught. A direct negative check
verified all three guards without making an external connection. Loopback and
Unix sockets remain available. This is an in-process guard, not an OS sandbox;
existing child fixtures use isolated data and fake model/vector clients.

## 7. Environment inventory

`ai-engine/.env.example` now documents each setting's reader, purpose, status,
actual default, safe example and relevant security concerns. Sections follow the
requested backend/storage, local-model, hybrid, synchronization, session,
vector-storage and optional/legacy organization, plus test-only settings.

| Variable | Status and actual unset behavior | Verified reader |
| --- | --- | --- |
| CODEMIND_DATA_DIR | Optional; `~/.codemind/data` | config, scanner, metadata, extension |
| CODEMIND_PORT | Optional; 8000 | API, extension ServerManager |
| CODEMIND_LOCAL_MODEL | Optional; `llama3:latest`; empty disables generation | answers, hybrid API, LocalOllama |
| CODEMIND_ENABLE_HYBRID | Optional; `0`, disabled | hybrid API |
| CODEMIND_AUTO_SYNC | Optional; `1`, enabled; only exact `0` disables | API lifespan |
| CODEMIND_SESSION_TOKEN | Optional; trimmed empty/unset generates token_hex(32); extension reads private token file | API, extension |
| QDRANT_URL | Optional; no URL default; unset/empty selects local FAISS | config vector factory |
| QDRANT_API_KEY | Optional; None for unset/empty; server authentication may require it | config vector factory |
| OPENROUTER_API_KEY | Optional for application; no default; required for intended legacy OpenRouter requests | legacy RAG, manual utility |
| CODEMIND_LLM_MODEL | Optional; legacy-only `openrouter/cohere/north-mini-code:free` | legacy RAG |
| ARGUS_DEMO_JWT_SECRET | Demo only; empty; >=32 characters required for demo JWT operations | sample auth_service |
| ARGUS_DEMO_ADMIN_HASH | Demo only; empty rejects admin login | sample auth_service |
| CODEMIND_TEST_VSCODE | Test only; no env default; runner uses installed VS Code or may download if absent | host test runner |
| CODEMIND_QUAL_REPO_ROOT | Qualification only; no default; required to activate separate real-source fixture | real-source fixture/host suite |
| CODEMIND_QUAL_MODEL_CACHE | Qualification only; defaults to data-dir/models | real-source fixture |
| LITELLM_LOCAL_MODEL_COST_MAP | Library/test option; pytest forces True, documented unittest exports True | LiteLLM, test conftest |

The first ten are application/legacy settings, the next two belong only to the
demo, and the remainder are testing tools. Git subprocess controls and cleared
`NODE_OPTIONS` are internal tool environment handling, not new app settings.

An AST-based inventory of current Python configuration reads found no missing
application setting. Extension/test and demo readers were also inspected.
`dotenv_values()` verified the only active example assignments: hybrid `0`,
empty session token, empty Qdrant URL/key, empty OpenRouter key. No secret or
remote-enabled configuration was introduced.

Documented existing inconsistencies were preserved: exported environment wins
over dotenv; VS Code does not read the Python `.env`; relative/empty data paths
and invalid/zero/empty port values differ between Python and the extension;
auto-sync accepts any value except exact `0` as enabled. Local and legacy model
settings are separate. No configuration reader or startup logic changed.

## 8. Files changed

| File | Change |
| --- | --- |
| ai-engine/rag_pipeline.py | Targeted legacy structural recognition fix |
| ai-engine/tests/test_rag_detection.py | New detector and hybrid compatibility regressions |
| ai-engine/test_ai.py → ai-engine/manual/legacy_openrouter_smoke.py | Relocated, guarded manual legacy utility |
| ai-engine/manual/README.md | Manual-only behavior and explicit authorization documentation |
| ai-engine/tests/test_manual_smoke.py | Import, opt-in and fake-provider tests |
| pytest.ini | Explicit test discovery and source import roots |
| conftest.py | Test-only local catalog and network isolation |
| requirements-dev.txt | Approved, pinned pytest development tool |
| TESTING.md | Reproducible commands and environment limitations |
| ai-engine/.env.example | Source-verified configuration reference |
| query_system/IMPROVEMENT_PHASE1_REPORT.md | This report |

Generated build output, the approved virtual-environment tooling installation,
and logs/package under `/tmp` are outside tracked source changes. Existing VSIX
files, snapshots, indexes and user repositories were not replaced or deleted.

## 9. Python verification

| Verification command from project root | Actual result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 456 passed, 2 warnings, 575 subtests passed; 178.44 s |
| `.venv/bin/pytest -q` | 456 passed, 2 warnings, 585 subtests passed; 199.82 s |
| Focused detector and smoke modules with `python -m pytest` | 13 passed, 313 subtests passed; 2.66 s |
| Exact named detector regression with `python -m pytest` | 1 passed |
| Focused detector module with console pytest, collection | 8 tests collected; imports succeed |
| Complete final collection | 456 tests; no manual utility or generated/benchmark tests |
| Final documented engine unittest discovery | 331 passed; 191.604 s |
| Final documented knowledge-base unittest discovery | 62 passed; 5.883 s |
| Final documented query-system unittest discovery | 63 passed; 0.100 s |

Both whole-suite pytest commands pass all 456 test methods. Their subtest counts
differ because the reported-query graph/no-graph matrix was expanded after the
first run had collected its test code; the expanded cases then passed the final
focused module run, console whole-suite run and unittest discovery. Production
detector logic was unchanged between these whole-suite runs. The final unittest
suite also totals **456 passed**, zero failed. Nothing is counted twice by adding
subtests to the test-method total.

These complete suites cover AI-engine tests, knowledge-base tests, query-system
contracts and Phases 0–10, S1/S2 and existing stabilization checks. Expected
injected offline/watcher/vector/model exceptions are negative-test evidence,
not test failures. Pytest retained its two warnings without suppression.

Raw verification logs for this run are retained at:

- `/tmp/codemind-phase1-baseline-engine.log`, `-baseline-query.log`,
  `-baseline-kb.log` (same prefix).
- `/tmp/codemind-phase1-pytest-module.log` and `-pytest-console.log`.
- `/tmp/codemind-phase1-final-unittest-engine.log`, `-final-unittest-query.log`,
  `-final-unittest-kb.log`.
- `/tmp/codemind-phase1-node.log`, `-vscode.log` and `-final-collection.log`.

The focused command and exact test address are documented in `TESTING.md`.

## 10. Node and VS Code verification

- Existing Node resolver/presentation/session suites: **29 passed**, zero failed
  or skipped, 1.667 s.
- TypeScript compile and `vscode:prepublish`: **passed**.
- Installed `/Applications/Visual Studio Code.app/Contents/MacOS/Code` used for
  `test:integration`; **exit 0**. Actual Phase 9 preview/navigation/dirty/stale
  checks and Phase 10 authenticated hybrid/legacy transport, settings, routing,
  depth, editor navigation and rollback checks printed PASS.
- The host suite's optional ordinary-engine `checkConnection` smoke printed
  **SKIP: engine not running**. Its isolated authenticated fixtures did execute.
- Separate existing real-source stabilization host suite was **not executed**:
  `CODEMIND_QUAL_REPO_ROOT` was not supplied. It requires prequalified snapshots
  and is outside routine isolated fixture testing; no published/user snapshot
  was used or rebuilt to activate it.
- No VS Code runtime or new runtime dependency was downloaded.

Plain VSIX packaging hit an existing metadata error: no `repository` field in
the unchanged extension manifest, so vsce could not rewrite the relative README
link `../query_system/PHASE10.md`. Supplying `--baseContentUrl` and
`--baseImagesUrl` derived from the configured GitHub origin and verified `main`
branch passed packaging without source edits. The output is
`/tmp/codemind-phase1-vsix.qj0QD1/codemind-extension-0.4.1.vsix`, 11 files,
30.89 KB. Missing repository/license warnings remain. The exact local-only
workaround is in `TESTING.md`; nothing was published or installed.

## 11. Security and compatibility checks

- Locked root README, API, hybrid API, answers, config and Phase 5 understanding
  were compared byte-for-byte with HEAD and are unchanged. All other production
  query-system, knowledge-base and extension sources have no diff.
- No API contract, storage schema/format, embedding model/vector dimension,
  source provenance, citation validation or redaction change.
- Production hybrid gates remain default-off. Local answering/model selection
  and watcher/session behavior are unchanged.
- No external model inference or provider smoke request was performed. The only
  new downloaded tooling was the explicitly approved pytest development tool
  and its dependencies. Authorized-provider smoke coverage uses fakes only.
- Fixtures use private temporary repositories/storage; no user source, indexed
  data or published snapshots were modified or deleted.
- Final tracked/untracked inventory is restricted to the files above. No
  historical benchmark output or locked README was rewritten.
- `git diff --check -- .` passes; final report included in the last check.

## 12. Unresolved issues and limitations

- Audit baseline/test-file mismatch: current original suite is 443 tests with no
  detector regression file; this report records actual evidence, not 806/805/1.
- Existing Starlette/httpx deprecation and local Qdrant payload-index warning
  remain visible in pytest. They were not suppressed or addressed in this phase.
- Historical graph lookup outside this detector retains its existing behavior;
  no global ambiguity/routing redesign was attempted.
- Unflagged VSIX packaging still needs repository/link metadata; verified CLI
  base-URL overrides allow local build. Existing license metadata is absent.
- Optional ordinary-engine and real-source host checks have the execution
  limitations described above. This is regression verification, not renewed
  production qualification or a Phase 2/3 completion claim.

## 13. Phase 1 status

**Phase 1 complete**, with the pre-existing packaging metadata caveat and the
explicit optional host-check limitations above. All required structural queries,
new regressions, complete Python suites, available Node checks, TypeScript build
and isolated VS Code integration checks pass. Both pytest entry points and the
existing unittest commands are reproducible. The manual external-provider
utility is isolated and gated; configuration readers and production defaults
are unchanged. No Phase 2/3 improvement work was started.
