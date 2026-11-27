# Phase 4 — static test discovery and reviewable plans

Implemented 2026-11-27. The Analyze Change panel now offers **Review test plan**, also available as **CodeMind: Review Test Plan** after an analysis completes. Phase 4 discovers existing tests and produces bounded review proposals. Execution is unavailable in this phase.

## Phase 3 issues resolved first

- A late captured-source response is now checked against the originating job, repository and controller epoch before opening a document. Starting a new analysis cannot open a late source from the previous one.
- Capture byte accounting previously counted inspected binary bytes that were then excluded from retained data. The private JSON transport compared that number with retained bytes and could reject a valid capture. Published `size_bytes` now describes retained bytes; inspection still enforces the original byte budget. A binary source/metadata round-trip fixture covers the correction.

## Capture and discovery

Test planning consumes the existing immutable capture and Phase 2 relationship evidence. The source inventory is not rescanned by the planner. A small metadata allowlist is captured inside the same atomic source view: package manifests, supported lockfile names, runtime hints and tsconfig JSON files. Metadata has separate before/after tuples so generic source parsing does not parse lockfiles as JS/TS. Metadata policy, hashes and exclusions participate in capture identity and private transport validation. The source and metadata share existing file/byte limits, no-follow opens and consistency checks. Arbitrary JSON and credential files are not added to the allowlist.

The Node metadata adapter parses captured syntax only. It recognizes supported ESM aliases/namespace bindings and simple CommonJS Node test/assert bindings, literal named test callbacks and supported assertion calls. Shadowed bindings, malformed syntax and unsupported declarations remain unknown. Simple captured relative imports are associated with exact available source paths; generic Phase 2 source relationships are also reused. File-wide association does not establish that an individual test reaches a changed branch or asserts its intended behavior.

Package JSON is parsed as data. Scripts are never commands to execute. JS/TS configuration is inventoried and hashed but not evaluated. Runtime requirements, lockfile identity, configuration references, environment policy and unknown execution limits are recorded in validated `TestProfile` objects. Missing/excluded metadata is explicitly unknown. Native JS Node files can produce adapter-owned argument-array proposals; TypeScript execution, nested-package orchestration and other frameworks remain unsupported for execution proposals. Vitest/Jest/Mocha/Playwright dependency metadata produces limitations rather than invented runner commands.

## Plans, expectations and selection

`TestPlan` owns profiles, discovered tests, scenarios, proposed checks and selected review IDs. It carries repository/capture/catalog identities, search scope, omitted counts and explicit limitations. Contract validators reject unknown check/test/profile/scenario references and expectations that do not belong to a related captured assertion.

Related existing tests retain captured assertion expressions as expectation sources. Those expressions are not evaluated, and their relevance to the changed behavior remains unverified. Assertionless tests retain unknown expected outcomes. Input, error, permission, state and contract scenarios are review questions anchored to changed source; they do not invent status codes, business rules or authorization policy. They require a validated contract or user-confirmed expectation before becoming defect checks.

The panel shows requirements, related tests, proposed argument arrays, evidence strength, expected-outcome sources/unknowns and eligibility. It supports selecting/deselecting eligible checks **for review**. No Run button, authorization endpoint, dependency installation or test execution is added. A proposal may run an entire test file, including its hooks and other tests; this is visible in its reason. Proposed checks always report `not_run` and the plan always reports `execution_available=false`.

No-test-match text says exactly that no match was found within the inspected captured candidate files. It never claims missing runtime coverage. Unsupported and misleading filename-only matches remain labeled as such. Plans are historical. A stale workspace blocks new review selections, and changing metadata is detected by the backend freshness check before selection.

## API and navigation

`POST /v1/change-analyses/{id}/test-plans` accepts the owning `repo_id` and, optionally, `selected_check_ids`. Global engine session authentication, repository ownership and job expiry apply. Clients cannot submit source, commands, complete plans or expectations. Unknown/unsupported selections fail. Future execution phases must perform their own exact-plan authorization; review selection is not authorization.

Captured planning references reuse the Phase 3 source endpoint, including manifest/lock/configuration and assertion locations. They are resolved through the job's server-owned plan and original capture. Webview messages contain opaque action keys; source text and plan strings are escaped and the existing CSP remains in force.

## Incremental cache and limits

The session catalog caches extraction by source path, source hash and adapter/parser versions (up to 64 entries). Changes to a test cause that file to be re-extracted; unchanged tests are reused when source/manifests change. Catalog identity includes the capture inventory, metadata policy, manifest/config hashes and adapter versions. Plan identity additionally includes capture, evidence and planning-rule identities. At most four serialized plans are cached; callers receive validated copies so review selections cannot mutate cached base plans. Session shutdown/expiry clears planning caches with retained analysis data.

Initial budgets: 100 candidate test files, 200 discovered tests, 200 declarations per inspected file, 20 retained assertions per test (500 bytes each), 100,000 AST nodes per inspected file, 20 package profiles and 100 scenarios. Import metadata is capped at 64 specifiers of 200 characters, Node binding metadata at 64 names, and serialized plans at 4 MB. Oversized serialized plans fail explicitly. Every applied truncation records a reason and affected scope; catalog-level omitted-test counts are separate from per-file/AST limitations. More than 20 profiles fails planning explicitly. The profile limit and source-capture byte/file limits bound the workload; no whole-job latency SLA is claimed.

## Verification

Fixtures cover misleading filenames, imports without assertions, unsupported/malicious configuration, unknown fixtures, ESM/CommonJS bindings, shadowing, exact relative import association, manifest/runtime changes, test-source changes, metadata symlinks, ignored secret/unrelated JSON, staged-versus-saved metadata, binary exclusions, source ownership and selected-ID rejection. A planner fixture forbids subprocess creation and filesystem reads after capture/analysis preparation. Planner code does not import a runner.

Live qualification through authenticated routes used atomic capture `972f266ac4e44bc780decd5a6c8b7b21` and plan `1eaa057cb24ce662d91693283f4673734efe29b25922af9818735fd3a70e7312`: **2 profiles, 64 tests, 51 scenarios, 6 proposals**, execution unavailable. Repeated planning returned the same plan identity and a plan-owned source opened from captured bytes. Snapshot mount cleanup completed; no CodeMind mount remained. This native run preceded the final CommonJS association and binary-accounting fixes, which are independently regression-tested.

The local VS Code host smoke exercised Analyze Change followed by Review Test Plan against a fixture HTTP API and exited 0. It did not request administrator access; native capture was qualified separately. Controller tests cover plan display/selection, forged action keys, escaping, stale navigation and the late-response fix. The extension/Node suite passed **52 tests**, including compilation. Relevant final Python results are recorded below.

Commands:

```sh
.venv/bin/python -m pytest knowledge_base/test_test_planning.py ai-engine/tests/test_change_api.py knowledge_base/test_change_capture.py knowledge_base/test_atomic_capture.py knowledge_base/test_atomic_session.py knowledge_base/test_change_evidence.py knowledge_base/test_change_impact.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
cd extension
npm test
npm run test:change-integration
```

Manual native qualification, from the project root:

```sh
.venv/bin/python -m change_analysis.qualify_phase3 --test-plan
```

## Remaining boundary

P4 is complete for static Node test planning within the stated scope. Runtime reach, meaningful coverage, expected behavior beyond existing assertions, dependency preparation, enforced isolation, authorization and test execution remain later-phase work. Neither a discovered test nor a selected proposal demonstrates that a change is safe.

Final combined Python verification passed **134 tests and 57 subtests**, with one opt-in native fixture skipped after separate native qualification and one existing Starlette/httpx deprecation warning. A final focused run covers the subsequent one-letter nested-package and catalog/output budget hardening; its result is recorded below. Scoped whitespace checks passed. Snapshot mounts were cleaned up. The existing user edits and prior phase changes were preserved; no Git commit or VSIX release was created.

Final focused verification: **24 tests passed** in 17.49 seconds after nested-package isolation and metadata/output budget hardening. This includes all static-planning and authenticated plan API fixtures. The only warning is the existing Starlette/httpx deprecation warning.

## Phase 5 prerequisite correction — 2026-11-27

Unknown Node test members and unsupported assertion chains are now excluded from qualified metadata. Discovery adapter version v2 invalidates old cached extraction. The dedicated regression passed with the planner/API suite; Phase 5 does not change review selection into execution.
