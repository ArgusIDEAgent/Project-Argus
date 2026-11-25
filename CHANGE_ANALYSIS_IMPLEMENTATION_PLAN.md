# CodeMind: Change Analysis, Explanation and Safe Testing

**Created:** 2026-11-25  
**Status:** P1 qualified for administrator-assisted macOS/APFS capture; P2 source-evidence foundation implemented, impact work ongoing  
**Purpose:** Execution checklist for the owner-provided proposal and the repository-based architecture review.

A task is complete only when its implementation and required verification are complete. The original planning review was source-only. Phase 1 implementation and verification are recorded below and in `change_analysis/PHASE1.md`. Preserve existing local changes.

## 1. Product objective and responsibilities

A developer saves changes, clicks **Analyze Change**, understands the relevant before/after flow, reviews proposed checks, and optionally executes selected supported tests in an isolated environment.

Build one coordinated workflow with shared evidence and a separately isolated runner. The three agents describe bounded software responsibilities; they do not require three autonomous LLM systems.

| Component | Responsibility | Must not do |
| --- | --- | --- |
| Understanding | Capture saved changes and establish facts, relationships, impact and uncertainty | Invent runtime behavior or mix source versions |
| Explanation | Teach using validated evidence and deterministic templates | Create graph facts or hide uncertainty |
| Testing planner | Discover related tests and propose evidence-backed scenarios | Treat static association as coverage or invent expected behavior |
| Runner | Execute exact authorized checks under enforced isolation | Execute arbitrary model commands or write into developer source |
| Result classifier | Report observations and supported change attribution | Treat every failed command as a newly introduced bug |

The product must distinguish what source shows, what relationships establish, what is inferred, what a test observed, whether the change caused it, and what remains unverified.

## 2. Current project integration points

These are findings from the 2026-11-25 source review. Revalidate before implementation.

| Existing file/component | Reuse | Required extension |
| --- | --- | --- |
| `extension/src/extension.ts` | Selection, commands, watchers and refresh | Dedicated controller and stale-result lifecycle |
| `extension/src/serverManager.ts` | Authenticated engine calls | Typed analysis and test-run client methods |
| `ai-engine/api.py` | Local API and session infrastructure | Versioned change routes without replacing `/ask` |
| `knowledge_base/synchronization.py` | Immutable knowledge generations, freshness, reader protection | Distinct captured source state and matching logic |
| `knowledge_base/storage.py` | Repository-local storage and containment | Bounded session artifacts and lifecycle integration |
| `knowledge_base/scanner.py` | Inventory, exclusions and hashes | Capture-safe inventory and test metadata support |
| `knowledge_base/semantic_resolver.cjs` | JS/TS symbols and relationships | Both-version analysis and supported framework adapters |
| `knowledge_base/identity.py` | Canonical identities | Separate before/after correspondence |
| `query_system/evidence_contracts.py` | EvidencePackage and integrity checks | Wrapper around separate version-specific packages |
| `query_system/execution_contracts.py` | Bounded analysis operations | Reuse concepts; separate repository-program execution |
| `query_system/rule_registry.py` | Test-reference limitations | Preserve distinction between references and coverage |

Important constraints:

- `SnapshotStore.changed_files` compares index generations, not the entire saved diff against HEAD.
- Existing `EvidencePackage` validates one snapshot identity. Before/after evidence needs a wrapper, not mixed records.
- Symbol identity includes attributes that change during renames/signature edits. Identity equality is insufficient for correspondence.
- The API's Git porcelain-based dirty fingerprint is not a content hash.
- The locked README conflicts with this proposal and parts of the current implementation. Reconcile scope and architecture in Phase 0 before implementing feature code under the updated contract.
- Saving this plan does not implement the proposal or establish that existing release gates pass.

## 3. Scope of the first release

### Included

- One selected local Git repository at a time.
- Saved `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`, `.mts`, `.cts` files.
- Tracked changes and eligible, non-ignored untracked files.
- Direct calls, imports, exports and bounded caller relationships.
- Focused textual before/after flow.
- Deterministic explanations and scenario planning without an LLM.
- Optional local-model wording improvements.
- Node test runner first; Vitest through a separately qualified adapter.
- Planning remains available when execution is unsupported.
- Existing supported tests before narrow temporary-test templates.

### Deferred

- Arbitrary package scripts and broad Jest compatibility.
- Playwright/browser execution and multi-service integration environments.
- General monorepo orchestration.
- Arbitrary LLM-generated test programs.
- Python execution adapters.
- Automatic fixes or adding tests to developer source.
- Cloud/team deployment and distributed agent infrastructure.

JS/TS syntax support does not imply understanding every framework. Express routes and similar behaviors require explicit adapters. String matching does not prove a UI action reaches a backend.

## 4. Corrections to the original proposal

| Original concept | Required correction |
| --- | --- |
| Saved workspace against HEAD | Define staging, untracked, unsaved and exclusion semantics |
| One evidence package | Wrap separate before/after packages |
| Verified flow graph | Separate structural facts, inferred flows and runtime observations |
| Tests protect the change | Distinguish association, runtime reach, assertions and regression evidence |
| Missing coverage | Say “No matching test found within analyzed scope” unless stronger proof exists |
| Expected behavior | Require an expectation source |
| Reproduced failure | Separate setup failure, assertion failure, pre-existing failure and regression |
| Temporary directory is safe | Require an enforced execution boundary |
| Allowlisted command is safe | Treat scripts, configuration, plugins and dependencies as untrusted code |
| Remove all secrets | Minimize exposure; filtering cannot detect arbitrary embedded secrets |
| One cache key | Include tools, policies, dependencies and configuration identities |
| New save invalidates everything | Mark old results stale for current workspace while preserving historical provenance |
| No planner rescan | Shared inventory, incremental test catalog and bounded extra inspection |
| Immutable execution workspace | Immutable source capture plus disposable writable materialization |

## 5. Capture policy

| Situation | Required behavior |
| --- | --- |
| Baseline | HEAD captured when analysis starts |
| Tracked files | Compare final saved working-tree bytes with HEAD |
| Staged and unstaged edits | Analyze final saved contents; show staging metadata separately |
| Untracked files | Include eligible files and report exclusions |
| Unsaved buffers | Exclude with visible notice |
| Deleted files | Preserve baseline evidence and removed relationships |
| Renames | Track correspondence; label ambiguity |
| Binary/oversized files | List as unanalyzed changes |
| Merge conflicts | Identify conflicts and block definitive analysis |
| No initial commit | Explicit all-files-added mode |
| Non-Git folder | Unsupported for initial HEAD comparison |
| Submodules | Report reference change without recursive analysis |
| Symlinks | Initially exclude symlinked source; reject escaping paths |
| Nested project/larger Git root | Explicit repository boundary and path scope |

Capture sequence:

1. Resolve and validate repository boundary.
2. Capture HEAD and eligible inventory.
3. Read saved bytes into a private capture.
4. Record hashes, exclusions and diff metadata.
5. Recheck HEAD and source consistency.
6. Publish only a consistent capture.
7. Retry a bounded number of times, then report changing files rather than mixed evidence.

Do not checkout HEAD in the developer workspace. Use controlled Git object access and disable external diff helpers, text conversion and optional execution hooks where applicable. Git status text is not content identity.

## 6. Three different snapshots

| Snapshot | Purpose |
| --- | --- |
| Source capture | Exact immutable saved bytes being analyzed |
| Knowledge snapshot | Parsed symbols, graph, search records and evidence |
| Execution workspace | Disposable writable copy/overlay for selected tests |

Reuse a knowledge generation only when its manifest matches the capture; otherwise derive structural data from captured files. Structural analysis must not wait for embeddings. Preserve reader/freshness protections but do not hold index locks during LLM calls or tests. Never hard-link execution files to developer files.

## 7. Versioned contracts and certainty

| Contract | Required information |
| --- | --- |
| `ChangeSnapshot` | Repository, HEAD, saved manifest, exclusions, digest, capture time |
| `ChangeEvidencePackage` | Before/after packages, symbol mapping, changed relationships, limitations, adapter versions |
| `TestProfile` | Framework/runtime, dependency identity, command descriptors, environment policy, limits |
| `TestPlan` | Scenarios, evidence, expectation sources, related tests, unknowns, proposed checks |
| `RunAuthorization` | Capture, plan, profile, selected checks, temporary-test hashes, policy digest |
| `TestRun` | State, environment, command, test counts, outcomes, artifacts, termination reason |

Validation invariants:

- Every source reference belongs to a specific version.
- Every graph edge has validated nodes and relationship evidence.
- Symbol correspondence may have zero, one or multiple candidates; ambiguity remains visible.
- Validate repository/capture ownership at every boundary.
- Changed commands, temporary tests, profiles or policies invalidate authorization.
- Every truncation records reason and affected scope.
- Preserve existing EvidencePackage integrity checks initially.

Use independent dimensions, not one confidence percentage:

| Dimension | Values |
| --- | --- |
| Evidence basis | Source-verified, relationship-verified, inferred, unresolved |
| Execution | Passed, assertion-failed, setup-failed, timed-out, cancelled, not-run |
| Attribution | Introduced, pre-existing, inconclusive |
| Completeness | Complete within supported scope, partial, unsupported |

## 8. Impact analysis and explanation

For each changed symbol:

1. Map diff ranges to declarations.
2. Compare definitions and map correspondence.
3. Identify added, removed and changed relationships.
4. Traverse bounded callers/callees.
5. Connect supported routes and test metadata.
6. Record unresolved boundaries and omissions.

Correspondence uses exact identity where possible, enclosing scope/declaration location, rename metadata and structural similarity. Multiple plausible matches remain ambiguous.

Proposed initial budgets: depth 2, 100 displayed symbols, 200 displayed relationships, stable ordering, cycle detection and explicit omitted counts. Validate through benchmarks.

“loginRoute calls validateToken” may be structurally verified. “Invalid tokens reach this branch” requires control-flow evidence or an inference label.

LLM output uses validated evidence IDs and cannot add graph facts or commands. Validate schema and ownership; citations alone do not establish semantic truth. Reject unsupported claims or use deterministic fallback. Treat repository text as untrusted prompt content. Presentation variants reuse evidence.

## 9. Test planning and coverage evidence

| Level | Permitted statement |
| --- | --- |
| Name/filename association | This test may be relevant |
| Resolved import/call | This test references changed code |
| Exact-run runtime coverage | This execution reached these lines/branches |
| Relevant assertion | This assertion checks this expected outcome |
| Comparable baseline/current runs | This check passes before and fails after |

Line coverage alone does not prove a meaningful assertion. Reuse shared inventory, maintain an incremental test catalog, and permit bounded inspection of captured test source. Do not create another full repository-understanding pipeline.

Each scenario records:

- Scenario and changed behavior/relationship.
- Evidence references.
- Preconditions and fixtures.
- Expected outcome and its source.
- Related tests and relationship strength.
- Required adapter/environment.
- Eligibility and remaining uncertainty.

Expected behavior comes from existing assertions, explicit contracts, validated specifications or user-confirmed requirements. If unknown, ask for intended behavior before treating the scenario as a defect check. Do not invent HTTP status codes or business rules.

## 10. Safe execution and dependency preparation

### Enforced execution boundary

- Narrow runner broker separate from analysis.
- Pinned runner image in a supported Linux VM/container environment.
- No direct host-process fallback.
- No privileged execution or runtime socket inside the sandbox.
- No developer repository, home directory, SSH agent or credential mounts.
- Minimal environment containing synthetic test values only.
- No external network route during runs.
- Writable locations limited to disposable workspace/output.
- Explicit CPU, memory, process, disk, time and output limits.
- Process-tree termination and cleanup.
- Workspace Trust as an additional user-intent gate, not isolation.
- Same-sandbox loopback services only initially; no host port publishing.
- Unsupported sandbox means planning-only mode.

Scoped guarantee: supported runs receive no host credentials or host services, have no external network route, and cannot write to developer source through exposed mounts. Secret filtering is defense in depth; arbitrary secrets embedded in source cannot be perfectly detected.

Node permissions alone are not a security boundary against malicious code. Container resource limits require explicit configuration and qualification.

### Dependencies

Separate **Prepare test environment** from **Run selected checks**. Network-enabled preparation needs its own reviewed policy and authorization; ordinary runs remain offline.

- Require a supported lockfile.
- Pin runtime and package-manager versions.
- Prepare dependencies in isolation.
- Disable lifecycle scripts initially.
- Report unsupported native builds/install hooks explicitly.
- Never mount developer node_modules or use developer package credentials.
- Key artifacts by lockfile, platform, runtime and preparation policy.
- Prevent runs from modifying shared dependency artifacts.
- Use adapter-owned validated argument arrays, not model/webview shell strings.

Discovery of `npm test` does not authorize execution. Framework configuration, setup hooks and plugins can execute arbitrary code.

## 11. Results and attribution

| Result | Meaning |
| --- | --- |
| Selected tests passed | Structured adapter report confirms actual tests ran and passed |
| Assertion failure observed | Assertion failed in captured environment |
| Regression reproduced | Comparable check passed on baseline and failed after |
| Pre-existing failure | Comparable baseline also failed |
| Environment/setup failure | Dependency/configuration/fixture/service startup failed |
| No matching test found | No match within declared discovery scope |
| Potential risk | Source-backed concern not demonstrated |
| Could not verify | Required capability/environment unavailable |
| Inconclusive | Inconsistent or insufficient observations |

Rules:

- Zero tests is never a pass.
- Exit code alone is insufficient; parse supported runner reports.
- Generated tests may contain incorrect expectations; failure is not automatically a product defect.
- Retry success does not erase a preceding failure.
- Baseline comparisons record dependency/environment differences.
- Passing selected checks never means the change is universally safe.
- Show exact temporary tests before authorization; start with narrow templates.

Reports include versioned evidence, command/scenario, expectation source, expected/actual behavior, sanitized bounded logs, limitations and next step.

## 12. Lifecycle, APIs and authorization

Workflow: capture → analyze → explain/plan → review → authorize exact checks → prepare/run → report.

Job states: `queued → preparing → running → completed`, with `failed`, `cancelled` and `timed_out` alternatives. Keep `stale_for_workspace` separate from execution state.

Every job needs an ID, repository/capture identity, stage, cancellation token, budgets, timestamps, structured errors, idempotency key and artifact references.

On save:

- Mark prior analysis stale for current workspace.
- Cancel queued old-capture execution.
- Request cancellation of active old-capture runs by default.
- Preserve produced results with original provenance.
- Require a new plan and authorization for the new capture.

Revalidate authorization immediately before launch. Do not persist authorization across engine restart initially.

| Endpoint | Responsibility |
| --- | --- |
| `POST /v1/change-analyses` | Capture and start analysis |
| `GET /v1/change-analyses/{id}` | Status/results |
| `POST /v1/change-analyses/{id}/explanations` | Presentation variant |
| `POST /v1/change-analyses/{id}/test-plans` | Bounded plan |
| `POST /v1/test-plans/{id}/authorizations` | Bind exact reviewed checks |
| `POST /v1/test-runs` | Authorized execution |
| `GET /v1/test-runs/{id}` | Status/sanitized results |
| `POST /v1/test-runs/{id}/cancel` | Cancellation |

Reuse session authentication and independently validate ownership/containment. The server resolves IDs into internal files; the webview cannot supply arbitrary filesystem paths. Finalize dependency-preparation authorization/status endpoints in P0.

## 13. Proposed module boundaries

```text
change_analysis/
  contracts.py
  capture.py
  diff.py
  symbol_mapping.py
  impact.py
  service.py
  explanation.py
test_analysis/
  contracts.py
  discovery.py
  catalog.py
  planner.py
  outcomes.py
  adapters/
    node_test.py
    vitest.py
safe_runner/
  contracts.py
  policy.py
  broker.py
  lifecycle.py
  artifacts.py
ai-engine/
  change_api.py
extension/src/
  changeAnalysisController.ts
  changeAnalysisPresentation.ts
```

Reuse knowledge_base and query_system through explicit interfaces. Avoid growing extension.ts into the workflow implementation, duplicating graph validation, replacing chat or building distributed agent infrastructure.

## 14. Caches, retention and cleanup

| Cache | Identity includes |
| --- | --- |
| Structural analysis | Capture, baseline, parser/resolver versions, adapter configuration |
| Test catalog | Tests, manifests, framework configuration, adapter version |
| Plan | Evidence package, test catalog, planning rules |
| Explanation | Package, mode, template/model version |
| Dependencies | Lockfile, runtime, platform, preparation policy |
| Run result | Capture, selected checks, environment, execution policy |

Historical passes must be labeled historical. Keep session evidence bounded and ephemeral initially. Execution files live outside developer source. Optional history stores bounded sanitized summaries. Delete raw workspaces, reconcile orphans at startup, expose cleanup failures and protect active artifacts from eviction.

## 15. Beginner-facing experience

Answer: what changed, how flow changed, what might be affected, supporting evidence, available checks and unknowns.

Each check shows purpose, relevance, existing/temporary origin, expectation certainty, requirements, limits and eligibility. Show analyzed commit and saved-state identity unobtrusively. Stale source links open captured content or disclose changed live content.

Example result: “12 selected tests passed. Two suggested scenarios remain unverified. This result applies to the saved version analyzed at 14:32.”

## 16. Phase tracker and dependencies

| Phase | Status | Depends on | Main output |
| --- | --- | --- | --- |
| P0 Contract and baseline | Capture prerequisites addressed; broader phase open | None | Scope, decisions, support matrix, baseline |
| P1 Reliable capture | Complete within qualified macOS/APFS scope | P0 | Immutable source capture/diff |
| P2 Change evidence | Started: versioned source evidence and correspondence | P1 | Before/after evidence |
| P3 Explanation experience | Not started | P2 | Useful Analyze Change without LLM |
| P4 Test planner | Not started | P2 | Reviewable test plan |
| P5 Runner qualification | Not started | P0; integration with P4 | Qualified isolation boundary |
| P6 Existing-test execution | Not started | P4, P5 | Authorized Node execution |
| P7 Controlled expansion | Not started | P6 | Vitest, templates, baseline comparison |
| P8 Release qualification | Not started | P3, P6; P7 if shipped | Qualified packaged release |

```mermaid
flowchart LR
  P0 --> P1 --> P2
  P2 --> P3
  P2 --> P4
  P0 --> P5
  P4 --> P6
  P5 --> P6
  P6 --> P7
  P3 --> P8
  P6 --> P8
  P7 -. if included .-> P8
```

Start runner prototyping after P0. Begin fixtures in P0 and extend them each phase. Do not defer security and correctness qualification to the end.

### P0 — Product contract, decisions and baseline

**Dependencies:** None. **Suggested owner:** Technical lead with product owner/security reviewer.

Tasks:

- [x] P0.1 Revalidate integration points, local edits and repository instructions.
- [ ] P0.2 Reconcile locked README with approved scope and actual architecture before feature implementation. Capture-specific scope is reconciled; broader architecture reconciliation remains open.
- [ ] P0.3 Freeze initial support matrix: language, framework, Git cases, OS/runtime and exclusions.
- [ ] P0.4 Record decisions for snapshots, wrapper, isolation and dependency preparation.
- [ ] P0.5 Define threat model, denied capabilities and scoped guarantees.
- [ ] P0.6 Specify schemas, errors, authorization identities and preparation API.
- [ ] P0.7 Select concrete resource/retention defaults and benchmark hardware/workload.
- [x] P0.8 Run relevant existing regression baseline; separate pre-existing failures.
- [ ] P0.9 Create correctness/adversarial/held-out fixture manifests.
- [ ] P0.10 Specify independent analysis/planning and execution feature flags and rollback behavior.

Acceptance criteria:

- [ ] Scope conflicts and unsupported cases are explicit; no unresolved decision blocks capture implementation.
- [ ] Every safety promise has an enforcement mechanism and test.
- [ ] Contract ownership and version boundaries are reviewable.
- [ ] Baseline results and existing local edits are recorded without overwriting user work.

**Evidence:** Decision records, support matrix, threat model, baseline report and fixture manifest.

### P1 — Reliable saved-state capture

**Dependencies:** P0. **Suggested owner:** Backend engineer.

Tasks:

- [x] P1.1 Implement repository-boundary validation and controlled Git reads.
- [x] P1.2 Capture HEAD, saved files, exclusions and digests.
- [x] P1.3 Implement final saved working-tree versus HEAD diff.
- [x] P1.4 Handle staging, untracked files, deletion, rename and unborn HEAD.
- [x] P1.5 Report binary, oversized, submodule and symlink limitations.
- [x] P1.6 Detect conflicts, concurrent saves and HEAD movement; bounded retry.
- [x] P1.7 Publish immutable capture with size limits and cleanup ownership.
- [x] P1.8 Verify no developer-file or Git-index mutation.

Acceptance criteria:

- [x] Staged/unstaged combinations match final saved bytes exactly.
- [x] Unsaved edits are excluded with visible notice. Every library result carries the notice; UI rendering is P3.
- [x] Races cannot publish internally inconsistent evidence **in the qualified atomic path**: `APFSSnapshotCapture` reads source, HEAD and index from one UUID-verified read-only APFS snapshot. Real post-snapshot source/index/HEAD changes and EROFS enforcement were tested. Ordinary `CaptureStore` remains explicitly `observed_stable`, and is rejected by the Phase 2 builder.
- [x] Excluded changes remain visible as limitations (status `unanalyzed`, not a claim that excluded bytes changed).
- [x] Nested Git roots cannot widen selected scope.

**Evidence:** `knowledge_base/test_change_capture.py`, frozen `ChangeSnapshot` manifests and workspace/index-integrity assertions. Implementation and verification record: `change_analysis/PHASE1.md`. In-memory session ownership replaces persistent artifacts for P1; no disk cleanup or migration is needed. Capture-specific scope reconciliation and the relevant baseline are complete, but broader P0 architecture/runner/release decisions remain open.

### P2 — Before/after evidence and impact

**Dependencies:** P1. **Suggested owner:** Analysis/backend engineer.

Tasks:

- [x] P2.1 Add wrapper around existing version-specific EvidencePackages. Existing package integrity validators remain unchanged.
- [ ] P2.2 Reuse matching knowledge snapshots or derive capture-specific structure. Implemented syntax definitions directly from captured bytes; resolved structure remains pending.
- [ ] P2.3 Map diff ranges and implement symbol correspondence. Implemented changed-line association, declaration-key candidates and exact-content file renames; broader rename/signature correspondence remains pending.
- [ ] P2.4 Compute relationship additions/removals/changes.
- [ ] P2.5 Add bounded traversal, cycle handling and omissions.
- [ ] P2.6 Add only explicitly supported framework metadata adapters.
- [ ] P2.7 Validate relationships, citations, repository and version identities. Source/citation/capture ownership and canonical syntax proofs are validated; relationship validation awaits the graph layer.
- [x] P2.8 Remove embedding/model availability from structural critical path. Initial syntax-evidence path uses no embeddings, models or subprocesses.
- [ ] P2.9 Verify reader-lock lifetime and freshness behavior.

Acceptance criteria:

- [ ] Every displayed edge has canonical relationship proof.
- [x] Deleted-code citations reference baseline content.
- [ ] Ambiguous correspondence/dynamic behavior stays unresolved or inferred.
- [ ] Budgets truncate transparently with stable ordering.
- [x] Single-version packages never mix source versions. Wrapper-negative tests reject mixed ownership.

**Evidence:** Before/after fixtures, provenance-negative tests, traversal-budget results.

### P3 — Deterministic explanation and VS Code experience

**Dependencies:** P2. **Suggested owner:** Extension engineer with backend support.

Tasks:

- [ ] P3.1 Add Analyze Change command and dedicated controller.
- [ ] P3.2 Add authenticated job API and typed client methods.
- [ ] P3.3 Render changes, flow, impact, evidence and uncertainty.
- [ ] P3.4 Add captured-source navigation and stale-state UI.
- [ ] P3.5 Add simple/detailed/flow variants using the same evidence.
- [ ] P3.6 Implement cancellation, bounded queues and idempotency.
- [ ] P3.7 Add optional local-model wording with validation/fallback.
- [ ] P3.8 Escape repository/log content and preserve webview security.

Acceptance criteria:

- [ ] End-to-end explanation works with LLM disabled.
- [ ] Presentation changes do not rerun structural analysis.
- [ ] Generated claims cannot become verified facts without supporting evidence.
- [ ] Saves/branch changes mark old results stale; navigation remains truthful.
- [ ] Relevant chat/indexing regressions pass.

**Evidence:** Extension workflow checks, deterministic examples, stale-navigation and model-failure cases.

### P4 — Test discovery and scenario planning

**Dependencies:** P2; UI integration with P3. **Suggested owner:** Test-analysis engineer.

Tasks:

- [ ] P4.1 Define and validate repository testing profile schema.
- [ ] P4.2 Discover manifests/configuration statically without execution.
- [ ] P4.3 Build incremental catalog and Node test metadata adapter.
- [ ] P4.4 Map tests using explicit evidence levels.
- [ ] P4.5 Add bounded scenarios for input, errors, permissions, state and contracts.
- [ ] P4.6 Require expectation sources; preserve unknown intended behavior.
- [ ] P4.7 Display selected checks, requirements and eligibility.
- [ ] P4.8 Implement catalog/plan cache identity and invalidation.
- [ ] P4.9 Verify planning does not run tests or evaluate executable configuration.

Acceptance criteria:

- [ ] Association is never described as runtime coverage.
- [ ] No-test-match results declare search scope/limitations.
- [ ] Every scenario has evidence, expectation source or explicit unknown, and eligibility.
- [ ] Unsupported tools produce limitations rather than guessed commands.
- [ ] Repeated planning reuses catalog/inventory without a second understanding pipeline.

**Evidence:** Planner golden cases, misleading filenames, unknown expectations and cache checks.

### P5 — Runner isolation prototype and qualification

**Dependencies:** P0; integration with P4 contracts. **Suggested owner:** Runtime/security engineer.

Tasks:

- [ ] P5.1 Select supported runtime and pinned image policy.
- [ ] P5.2 Implement capability probe and fail-closed behavior.
- [ ] P5.3 Implement broker with validated commands and mount policy.
- [ ] P5.4 Enforce no external network, minimal environment and no host services/credentials.
- [ ] P5.5 Enforce CPU/memory/disk/process/time/output limits.
- [ ] P5.6 Materialize disposable workspace without developer-file hard links.
- [ ] P5.7 Implement process-tree cancellation, cleanup and orphan reconciliation.
- [ ] P5.8 Qualify host access, network, symlink, child-process and exhaustion fixtures.
- [ ] P5.9 Validate artifact paths and malicious output handling.

Acceptance criteria:

- [ ] Mandatory isolation fixtures pass on every supported configuration.
- [ ] Missing controls disable execution with a clear reason.
- [ ] No host fallback or runtime socket is exposed to tests.
- [ ] Descendants terminate within documented cancellation grace period.
- [ ] Cleanup failures are visible and orphan handling is repeatable.

**Evidence:** Policy, capability reports, adversarial results and crash/cleanup tests.

### P6 — Authorized existing-test execution

**Dependencies:** P4 and P5. **Suggested owner:** Runtime engineer with extension support.

Tasks:

- [ ] P6.1 Implement exact-plan authorization and pre-launch revalidation.
- [ ] P6.2 Gate execution on Workspace Trust and explicit user action.
- [ ] P6.3 Implement separately authorized isolated dependency preparation.
- [ ] P6.4 Pin dependency identities and disable lifecycle scripts initially.
- [ ] P6.5 Execute Node tests and parse structured reports.
- [ ] P6.6 Track selected/discovered/executed counts and zero-test results.
- [ ] P6.7 Classify setup/assertion/timeout/cancellation outcomes.
- [ ] P6.8 Sanitize bounded artifacts and link captured evidence.
- [ ] P6.9 Handle saves, duplicate requests, restarts and authorization expiry.
- [ ] P6.10 Enforce one active run per repository and bounded queues initially.

Acceptance criteria:

- [ ] No run launches without authorization matching all relevant identities.
- [ ] Source/profile/command changes require renewed review.
- [ ] Zero tests never passes; setup failure is distinct from assertion failure.
- [ ] Runs cannot mutate reusable dependency artifacts or developer source.
- [ ] Historical results are not presented as new execution.

**Evidence:** End-to-end traces, authorization races, Node fixtures and dependency-policy checks.

### P7 — Controlled expansion and attribution

**Dependencies:** P6. **Suggested owner:** Adapter/test-analysis engineer.

Tasks:

- [ ] P7.1 Add separately qualified Vitest adapter and compatibility matrix.
- [ ] P7.2 Add narrow temporary-test templates with expectation provenance.
- [ ] P7.3 Show exact temporary source before authorization and hash into plan.
- [ ] P7.4 Run comparable baseline/current checks in separate isolation.
- [ ] P7.5 Record environment/dependency differences and block unjustified attribution.
- [ ] P7.6 Distinguish introduced, pre-existing and inconclusive failures.
- [ ] P7.7 Preserve retry history and flag unstable outcomes.
- [ ] P7.8 Keep general generation and browser execution deferred.

Acceptance criteria:

- [ ] Regression claims require comparable baseline-pass/current-fail evidence.
- [ ] Generated-test failure does not automatically become a defect claim.
- [ ] Baseline incompatibility yields inconclusive/could-not-verify.
- [ ] Temporary tests never enter developer source automatically.
- [ ] Every new adapter passes isolation and classification gates.

**Evidence:** Paired reports, template examples, Vitest matrix and flaky-test fixtures.

### P8 — Release qualification and packaging

**Dependencies:** P3 and P6; P7 when shipped. **Suggested owner:** Release lead/QA.

Tasks:

- [ ] P8.1 Run correctness, security, lifecycle and held-out suites.
- [ ] P8.2 Run existing chat/indexing/navigation regressions.
- [ ] P8.3 Benchmark cold/warm latency, memory/disk and cancellation.
- [ ] P8.4 Verify OS/runtime matrix and disable unsupported execution.
- [ ] P8.5 Test crash, disk-full, stale authorization and orphan recovery.
- [ ] P8.6 Verify retention, privacy documentation and sanitized export.
- [ ] P8.7 Update scope, setup, limitations, troubleshooting and release notes.
- [ ] P8.8 Package VSIX and exercise workflow from installation.
- [ ] P8.9 Verify independent execution-disable flag preserves analysis/chat.
- [ ] P8.10 Record gate results, limitations and release decision.

Acceptance criteria:

- [ ] Mandatory release gates pass with retained evidence.
- [ ] No unresolved safety failure is waived into execution availability.
- [ ] Documentation matches supported and unsupported behavior.
- [ ] Fresh installation and model-disabled workflow succeed.
- [ ] Release summary separates implemented, qualified and deferred capabilities.

**Evidence:** Release report, benchmarks, packaged-extension checks and support matrix.

## 17. Qualification fixture matrix

| Area | Required cases |
| --- | --- |
| Capture | Unsaved/concurrent edits, staging, untracked, rename, deletion, HEAD movement |
| Graph | Re-export, alias, overload, dynamic dispatch, cycles, unresolved dependency |
| Planner | Misleading filename, import without assertion, unknown expectation, missing fixture |
| Execution | Zero tests, setup failure, assertion failure, timeout, crash, flaky result |
| Attribution | Baseline failure; baseline pass/current fail; incompatible environments |
| Isolation | Host read/write, network, symlink escape, persistent children, exhaustion |
| Presentation | Invalid citation, stale link, malicious HTML/logs, unsupported model claims |
| Recovery | Engine crash, cancellation race, orphan workspace, disk-full cleanup |

Each fixture records expected findings, required evidence, prohibited claims, uncertainty and execution outcome. Maintain separate calibration and held-out fixtures.

Include the original realistic defect cases: broken API argument after refactoring, missing authorization, null/empty crash, incorrect error response, dependency/retry failure, UI action disconnected from backend, and passing existing tests despite an uncovered user flow. Unsupported UI/runtime paths must remain hypotheses until supported execution establishes them.

## 18. Release gates and performance budgets

### Mandatory gates

- [ ] No dangling, cross-repository or wrong-version citations in qualification fixtures.
- [ ] No inferred behavior presented as verified.
- [ ] No static association presented as runtime coverage.
- [ ] No execution without matching snapshot/plan/command/profile/policy authorization.
- [ ] All mandatory denied-access fixtures pass.
- [ ] Zero-test runs are identified correctly.
- [ ] Cancellation terminates process trees within documented grace period.
- [ ] Capture, explanation, planning and reporting work without an LLM.
- [ ] Interrupted jobs and cleanup failures are explicit; no silent orphan execution.
- [ ] Existing chat, indexing and navigation pass relevant regressions.

### Proposed targets, not claims about current performance

| Metric | Initial target |
| --- | --- |
| UI acknowledgement | Under 300 ms |
| Warm structural analysis | p95 under 5 seconds on defined small-change benchmark |
| Deterministic explanation | Under 500 ms after evidence is ready |
| Planning | p95 under 3 seconds after catalog preparation |
| Test concurrency | One active run per repository initially |
| Graph limits | Depth 2, 100 symbols, 200 relationships by default |
| Logs/artifacts | Explicit bounded sizes selected in P0 |
| Cold indexing/preparation | Separate timing, progress and cancellation |

Record hardware, workload, cache state and model settings. Do not hide cold preparation or model generation inside one average. Freeze concrete runner/retention limits in P0 and verify enforcement in P5.

## 19. Delivery management and progress records

Planning estimate: **12–18 focused engineer-weeks** for a narrow qualified release. This is not a committed schedule. Sandbox portability and dependency compatibility are major uncertainties; re-estimate after P0 and the P5 prototype. Broad browser testing, arbitrary monorepos and cloud deployment are excluded.

Critical path: reliable capture → versioned evidence → deterministic explanation → planning → qualified isolation → approved execution.

First usable milestone: Analyze Change, explanation and reviewable test plan. Enable execution only after its separate safety gate passes.

Suggested reviewable change sequence:

1. Contracts and fixtures.
2. Capture and diff.
3. Before/after evidence.
4. API lifecycle.
5. Deterministic extension presentation.
6. Discovery and planning.
7. Broker and policy.
8. Node adapter.
9. Reports and baseline comparison.
10. Vitest and controlled templates.

For every phase record owner, dates, changed files, verification commands/results, limitations and next phase. Do not mark runtime acceptance criteria complete from source inspection alone.

### Completion record template

```text
Phase:
Owner:
Started:
Completed:
Implemented tasks:
Changed files / review reference:
Verification commands and results:
Acceptance criteria evidence:
Known limitations:
Decision / next phase:
```

### Progress log

| Date | Phase | Update | Verification |
| --- | --- | --- | --- |
| 2026-11-25 | Planning | Saved phased implementation plan | Document checklist/structure review; feature implementation not started |
| 2026-11-25 | P1 | Implemented isolated saved-source capture, bounded diff, exclusions, conflict/race handling and session ownership; narrow README scope authorization recorded | 71 tests and 84 subtests passed across capture/scanner/validation/storage/API suites; local-project capture succeeded. Strict atomic snapshot criterion remains open; see Phase 1 report. |
| 2026-11-25 | P1 safety review | Reproduced and fixed same-byte replacement, post-diff edit and repository-replacement defects; added stat receipts, final validation, directory identity binding and explicit atomic rejection. P2 deferred pending the strict consistency gate. | 81 tests and 84 subtests passed; real-project capture preserved the Git-index checksum; atomic requests fail closed. Full record in `change_analysis/PHASE1.md`. |
| 2026-11-26 | P1 atomic qualification | Verified APFS snapshot capability directly; strict gate blocked by unavailable snapshot-creation privileges. P2 remains unstarted. | Native fs_snapshot_create returned EPERM; no snapshot created. No existing Data-volume snapshot and no passwordless administrator authority. See Phase 1 report for the required helper qualification. |
| 2026-11-26 | P1 completed / P2 started | Apple tmutil route created snapshots; user authorized native read-only mounts. Qualified atomic adapter and interactive mount lifecycle. Began source-evidence wrapper, canonical validation and conservative correspondence. | Real APFS race/EROFS test passed; end-to-end command exited 0 and all test mounts were removed. 119 tests and 95 subtests passed; one opt-in native test skipped after its separate mounted run passed. Full records in Phase reports. |

## 20. Technical references

- [Node permission model limitations](https://nodejs.org/api/permissions.html)
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints)
- [VS Code Workspace Trust integration](https://code.visualstudio.com/api/extension-guides/workspace-trust)

Recheck current documentation when implementing supported versions. These references inform architecture; they do not certify CodeMind's implementation.
