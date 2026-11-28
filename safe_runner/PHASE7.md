# Phase 7 — controlled Node templates and paired attribution

Implemented and locally qualified, 2026-11-27. **Phase 7 is complete within the
Node/Vitest compatibility matrix.** The separate pinned Vitest toolchain,
configuration/report adapter and qualification are documented in [VITEST.md](VITEST.md).
Phase 8 release qualification and current verification are recorded in
[PHASE8.md](PHASE8.md). The results below are the earlier Phase 7 record.

## Compatibility matrix

| Adapter/profile | Status | Scope |
| --- | --- | --- |
| Existing native Node 24 checks | Qualified locally | Existing P5/P6 isolated runtime and bounded dependency policy |
| Node paired comparison | Qualified Node fixtures | One unchanged existing test file; two attempts per version; identical captured environment metadata |
| Literal temporary Node check | Qualified Node fixture | Captured ESM .js/.mjs declaration; <=8 JSON arguments; JSON expected value; <=4k literal review budget |
| Vitest 5.1.2 + Vite 8.4.0 | Qualified locally | Separate immutable toolchain image; matching captured lock graph; ESM JS, no repository configs/plugins/additional dependencies |
| Other Vitest versions | Unqualified | No version has a qualified runtime/configuration/dependency/report adapter |
| Arbitrary generated JS, TS execution, browser tests | Deferred | Never executed through the new UI/API |

Vitest now uses a separate prepared and pinned toolchain/runtime with qualified
configuration/report handling. Generic dependency limits were not raised and
there is no `npm test` fallback. [Vitest's reporter documentation](https://main.vitest.dev/guide/reporters.html)
also distinguishes its own JSON report contract from Node reporter events.
Current Node report parsing must not be applied to a Vitest process.

## Phase 6 prerequisite fix

Manifest runtime validation now agrees with the profile/version-file matcher for
24/v24 and 24.22.3/v24.22.3. Unknown constraints still fail closed. The installer,
network policy and existing separate preparation/run confirmations are retained.

## Temporary templates

Select existing checks, then use **Preview literal temporary check**. Enter a
captured module path, an evidenced declaration name and a JSON object containing
arguments and expected. The only template imports the module namespace, calls the
named property with literal arguments and compares the awaited result using
deepStrictEqual. It accepts no source program, expressions, shell command or
custom runner options. Static evidence of a declaration does not prove that it is
exported or callable; such problems remain setup failures.

The preview returns exact source, SHA-256, reserved temporary path, template
version and captured target source hash. The final native preparation/run modal
shows the literal source and hash again. The execution-plan identity includes the
entire preview; receipts and authorization are invalidated when it changes or is
removed. Preview itself never executes code. Explicit source authorization is
required, and an expectation remains a user-supplied literal rather than a
validated specification. Template failure never automatically becomes a defect
or regression claim. No temporary check is written into developer source.

The initial UI requires existing selected checks; a template is an additional
check, not a replacement for analysis/planning. Removing a template requires
preparing again. Paired comparison is disabled while a temporary check is present.

## Comparable pairs and retries

Select checks belonging to one existing native Node file, prepare its environment,
then use **Review paired baseline/current retries**. The native modal shows all
four operations: before/after, attempts 1/2. Every attempt uses a separate
container/materialization and the same receipt's immutable dependency archives.
The baseline uses captured HEAD bytes, never a checkout in developer source.

The check file must have identical bytes in before/after. Captured package/lock,
runtime/configuration metadata and inventoried executable configuration must
match. Missing/unborn baselines and environment differences produce an
inconclusive result with differences listed, without launching pairs. P6 checks
repeat before every launch; expiry, cancellation, save, changed selection or
policy prevents remaining operations. Partial results are retained and cannot
produce a definitive paired claim. The 60-second launch authorization and all P5
resource limits remain unchanged; large pairs may expire instead of extending
authorization automatically.

Attribution requires two consistent observations on each side and nonzero equal
runtime test counts with cleanup complete. Baseline pass/current assertion failure
supports **introduced** for that check under the captured comparable environment.
The same stable assertion observations on both sides support **pre_existing**.
Both pass yields **no_failure_observed**, not a coverage guarantee. Different retry
signatures flag **unstable** and yield inconclusive. Truncated failure observations,
setup failure, zero tests, missing attempts or differences block attribution.
Failure messages join names/codes/counts in the observed signature; message length
or omitted-failure budgets prevent pretending incomplete signatures are exact.

The result UI preserves every version, attempt, console and outcome. No retry
erases a preceding failure. Reports are untrusted program observations and cannot
establish full application correctness or intended behavior beyond the check.

## Verification

```sh
.venv/bin/python -m pytest knowledge_base/test_phase7_testing.py knowledge_base/test_dependency_preparation.py knowledge_base/test_authorized_execution.py knowledge_base/test_safe_runner.py knowledge_base/test_test_planning.py ai-engine/tests/test_change_api.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
npm --prefix extension test
.venv/bin/python -m safe_runner.qualify
.venv/bin/python -m safe_runner.qualify_phase7
```

Live qualification uses disposable synthetic atomic captures and the real VM
broker; native APFS capture remains separately qualified. Fixtures demonstrate
introduced/pre-existing failures, successful temporary literal checks with no
source writes, and incompatible baselines. Deterministic unit fixtures cover
unstable retries. This is not an editor/admin-prompt end-to-end qualification or
P8 release approval. No tests from the selected developer repository are run.

Final local verification: **115 Python tests + 57 subtests**, **55 extension
tests**, all **13 P5 isolation fixtures**, all **16 P6 dependency/execution
checks**, and four real P7 Node fixture workflows passed. The final identity and
Node outcomes are in `qualification-phase7-report.json`. No fixture containers
remain. A qualification interrupted by a concurrent enforcement-source edit
correctly failed closed; the final frozen-source requalification passed. The
updated VSIX was built and installed. Reload VS Code and restart the backend to
activate these routes/controllers. Vitest's separate qualification is recorded in `qualification-vitest-report.json`.
The final combined record is `qualification-phase7-report.json`.
