# Phase 9 — Adaptive response formatting and VS Code preview

## Outcome and compatibility assessment

Phase 9 adds a deterministic projection of accepted Phase 7 evidence and grounded
Phase 8 answers into an intent-specific, source-navigable display. It performs no
model inference, engine dispatch, query understanding, additional retrieval or
knowledge-base rebuild. Existing Phase 1–8 implementations and production
`/ask`/`/retrieve` are unchanged; Phase 10 is not implemented.

The current chatbot accepts the legacy `SearchResult`/`CodeResult` messages,
including `answer`, `results`, opaque result-action keys and warning fields.
`chatHtml()` uses `textContent`, a nonce CSP and separately registered host actions.
It does not understand Phase 7 canonical citations or Phase 8 claim ownership.
Replacing it would require an API/schema migration, outside this phase. A separate
command/panel therefore consumes backend packet exports without changing chat.

| Existing interface | Reuse |
| --- | --- |
| Phase 1 `ContractModel`, `Depth`, `IntentRequirement`, identities | Strict minimal display wrappers; original requirements/dependencies retained |
| Phase 6 `ExecutionReport` → Phase 7 `OperationCoverage`/requirements | Execution and coverage outcomes are copied through existing Phase 7 packaging, including failed/deferred branches |
| Phase 6 `ROUTING_POLICY`, `operation_spec()` | Only supported existing follow-up capabilities; single-target dependencies use existing `rules.dependency_summary` |
| `format_graph_result()` | Existing incoming/outgoing graph label, canonical entry and path orientation logic |
| Phase 7 `EvidencePackage`, `SupportedFinding`, `Citation`, `Clarification` | Accepted findings and source proofs reused, not replaced with another knowledge base or citation model |
| Phase 8 `GenerationResult`, claims/coverage/context catalog | Original explanation text and classification copied exactly, no rewriting or regeneration |
| `SnapshotStore.pin_view()`, `matches()`, `EvidenceAggregator.verify()`/`_finding()` | Fresh canonical source/claim verification before navigation; no graph/semantic/rule engine execution |
| `verifiedSourcePath()` | Existing canonical root containment, symlink containment, regular-file/size and SHA-256 checks |
| VS Code `openTextDocument`, `showTextDocument`, `Range` | Actual validated file opening and exact selected range; dirty documents rejected |
| Existing webview safety pattern | Nonce CSP, escaped/text-only untrusted data, host-generated opaque action keys, no model-written command/file links |

The compatibility audit was provided before implementation. Historical memory
about the old Node service was not treated as current architecture: this checkout's
`ServerManager` talks to the existing Python AI engine. No service migration or
backend route is introduced.

## Files created and modified

New implementation:

- `query_system/presentation_contracts.py`
- `query_system/response_presentation.py`
- `query_system/presentation_preview.py`
- `extension/src/responsePresentation.ts`
- `extension/src/responsePreview.ts`

New verification/artifacts:

- `query_system/test_presentation.py`
- `ai-engine/tests/test_phase9_presentation.py`
- `ai-engine/tests/phase9_preview_fixture.py`
- `extension/test/presentation.test.js`
- `extension/test/presentationSuite.js`
- `query_system/benchmark_presentation.py`
- `query_system/benchmarks/phase9_results.json`
- This report.

Modified only:

- `extension/src/extension.ts`: import/register the independent preview command;
  existing chat renderer/handlers are unchanged.
- `extension/package.json`: contribute/activate the preview command; no dependency changes.
- `extension/test/suite.js`: run the additional isolated preview/navigation suite.
- `extension/README.md`, `query_system/README.md`: preview documentation/navigation.

## Contracts and deterministic formatting

`PreviewInput` references existing `EvidencePackage` and optional `GenerationResult`
directly. `PresentationResult` is a display projection: query/support query/package
identity, repository/generation, depth/status, shared item/source catalogs, ordered
requirement sections, original requirement/dependency metadata, operation outcomes,
limitations, existing clarifications and bounded follow-ups. It does not duplicate
the original finding/proof/answer schemas. Source entries wrap existing `Citation`.

`format_response(package, generation=None, depth="standard")` is pure: strict shape,
ownership and citation/catalog checks only; no disk, database, model or retrieval
calls. Inputs must be backend-owned accepted results. Deserialization alone is
not authentication or current source verification. Generation must match the
package, supporting query, repository and generation; claim records must belong
to its approved context and exact existing citation map. Backend catalog facts
must retain their catalog text, classification and proof ownership. Redacted
Phase 8 text is preserved rather than compared to/replaced with raw source prose.

All requested 12 template families are covered by 13 intent headings (direct
callers and direct callees have separate headings):

| Family | Available content; no new inference |
| --- | --- |
| Symbol location | Existing canonical definition labels and source locations |
| Direct callers/callees | Known resolved static relationships; incoming/outgoing labels |
| Dependencies/paths | Existing dependency summaries or ordered static paths, not runtime execution order |
| Feature discovery | Ranked producer candidates and retrieval methods/scores; similarity is explicitly not dependency |
| Function contracts | Existing declaration/signature fields and producer statements; unavailable runtime behavior stays unknown |
| Change impact | Accepted static review locations; possible consequences visibly hypothetical |
| Module overview | Indexed declarations/interfaces/relationships only |
| Test discovery | Existing test/reference findings and discovery limits, not a promise of test completeness |
| Function explanation | Existing Phase 8 source-linked prose plus accepted support findings |
| Workflow explanation | Existing Phase 8 explanation and supplied static multi-file relationships |
| Behavior investigation | Qualified possible causes, explicit incomplete/runtime-proof limits |
| Code comparison | Existing two-target explanation/support; no new equivalence/performance assertion |

## Quick, Standard and Deep

Quick shows one primary existing item per requirement and essential references.
Standard shows up to eight primary items plus, for explanations, up to eight
accepted support findings; declaration/retrieval/relationship details are included.
Deep shows up to 25 primary and 25 explanation-support items per requirement,
available producer detail fields, indexed path direction, operation context and
support ownership. Individual detail JSON is capped at 6,000 characters with a
visible truncation note. The complete display envelope is capped at 1 MiB; an
oversized mandatory envelope is rejected rather than losing required coverage.

All depths preserve every original requirement, status, critical limitations,
model/fallback state, clarification and uncertainty. Hidden display items are
counted explicitly; this is display selection, not new analysis or a changed
reliability classification. Upstream evidence truncation remains incomplete.
Changing depth re-formats/revalidates the existing packet; it does not run engines
or collect new facts. A preview-verification round trip is not retrieval.

## Coverage, compound questions and clarification

Requirement order and IDs are preserved. Phase 8 coverage uses original orders
because its contract has no separate requirement-ID field; the projection assigns
`generation-req-<order>` display IDs and retains supporting Phase 7 requirement IDs.
Original `IntentRequirement` preserves repeated intents, entity indices and target
dependencies. Supplied target/support scope is visibly input context, never source
navigation authority. Successful independent findings remain when another branch
fails; missing/error/skipped/deferred/ambiguous/unsupported states stay explicit.

Shared source references render once with per-item citation anchors. Claims are
not merged across distinct requirements. Ambiguity candidates remain a manual
clarification list, without choosing a candidate or introducing a selection action.
Producer warnings, unresolved references, missing proofs, omissions and relevant
limitations survive even at Quick depth. Fallback retains verified findings and
shows actual model/failure/fallback states; partial results never say complete.

## Navigation authority and safe extension integration

The opt-in command is **CodeMind: Preview Validated Response (Phase 9)**
(`codemind.previewResponse`). Choose a backend packet JSON file and explicitly
authorize its repository folder. No scan/index/generation is started.

Packet construction in a trusted backend context after existing Phase 6–8 work:

```python
from query_system.presentation_contracts import PreviewInput
from query_system.response_presentation import format_response

packet = PreviewInput(package=accepted_package, generation=grounded_answer,
                      depth="standard")  # generation may be None
export_json = packet.model_dump_json()  # caller can save through its existing workflow
view = format_response(packet.package, packet.generation, depth=packet.depth)
```

Source controls are non-clickable until fresh verification. The fixed local
verification subprocess uses the checkout's `.venv` and
`python -m query_system.presentation_preview`, bounded stdin/stdout and no shell.
It uses the configured existing snapshot store (`CODEMIND_DATA_DIR`, default
`~/.codemind/data`) and one reader pin. It checks authorized repository identity,
current published generation, whole saved-source digest, ready internal stage,
canonical metadata trace, file/symbol/hash/range/provenance and existing finding
support. Invalid supplied findings are excluded without losing valid independent
branches. Historical stale evidence is visibly non-authoritative/non-navigable.

Every source click re-runs this verification. Only a registry-owned opaque key is
accepted from the webview; paths and citation IDs supplied in arbitrary model text
are never actions. Before opening, the host independently checks repository ID,
`cm7:` ownership, generation, canonical `File:` identity, normalized relative path,
range, hash, containment and symlink resolution. It then checks actual editor text,
dirty state and disk bytes again before selecting the exact range. Invalid, stale,
unavailable or unauthorized sources become non-clickable. Workspace Trust is
required; existing Restricted Mode chat remains unchanged.

All source/model/producer/metadata strings are escaped in HTML; model prose is
not interpreted as HTML, Markdown links, code to execute or command URIs. The CSP
permits only nonce-bound local style/script. Hypotheses, unresolved claims, model
interpretations and similarity have explicit distinct labels.

Follow-ups are bounded, derived from resolved JS/TS function/method context and
registered capabilities. Direct callers, single-target dependencies and indexed
tests produce file-scoped, canonical-context **copy-query** suggestions. Source
inspection points to the existing citation registry. No follow-up runs a query,
starts a model or adds a backend action automatically.

## Verification, benchmark and limitations

Commands using the existing project environment:

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_rules query_system.test_semantic query_system.test_understanding query_system.test_execution query_system.test_evidence query_system.test_generation query_system.test_presentation -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run compile
node --test extension/test/presentation.test.js
npm --prefix extension run test:integration
.venv/bin/python -m query_system.benchmark_presentation --fixture
git diff --check
```

There are 11 new Python pure-format tests, 14 new Python integration tests, 14
renderer tests and an actual VS Code preview/navigation integration sequence.
Coverage includes all templates/depths, original claim/citation ownership, repeated
and compound requirements, independent failures, fallback, ambiguity, unsupported/
deferred/missing targets, truncation, hypotheses/similarity, escaping, deterministic
formatting, no model/retrieval/DB calls, real source opening/range selection,
dirty/stale/invalid/unauthorized navigation and unchanged legacy renderer assertions.

Final regression totals: **384 Python tests passed, zero failed** (50 shared
query-system tests, 59 knowledge-base tests, 275 AI-engine tests covering
Phases 0B–9). This includes 25 new Phase 9 Python tests, counted once.
**14/14 renderer tests passed** with no skips; the combined existing VS Code
integration plus Phase 9 panel/navigation sequence passed, exit code 0. TypeScript
compilation, Python/JavaScript syntax checks, `git diff --check` and whitespace
checks on all new files passed. Existing SQLite resource/deprecation/local-Qdrant
warnings and injected-offline failure logs did not fail the final suites and were
not changed in this phase. No regression remains failing.

Saved [formatting benchmark](benchmarks/phase9_results.json): 14 workloads (all
families, plus both dependency forms and separate callers/callees), three depths,
five repetitions: **210 deterministic valid projections; 835/835 unchanged citation
map checks; original coverage preserved**. Median of per-case formatting medians:
**0.265 ms**; serialization separately: **0.016 ms**. Maximum serialized response:
**47,839 bytes**. Per-case median/p95 timings are in the artifact. Fixture retrieval
and mocked explanation preparation are outside the timed region; formatting has
zero actual model, engine or database calls. Fresh source-navigation verification
and VS Code rendering/opening are also outside these Python timings.

Known limitations:

- This is a separately accessible development preview, not production chat
  migration. It requires the CodeMind checkout/project venv alongside the extension;
  an installed VSIX alone does not bundle the Python preview runtime.
- The operator must align `CODEMIND_DATA_DIR` with the existing engine store.
  No packet-export API, implicit indexing or automatic missing-evidence retrieval
  is added; callers supply existing accepted packages/grounded answers.
- Exported JSON is not authenticated. The bridge verifies canonical provenance
  and source navigation, not arbitrary supplied rule/model prose or authorship.
  Model interpretations remain non-authoritative and not independently fact-checked.
- Static evidence, template coverage and valid citations do not prove runtime
  causes, behavior, feature/test completeness, performance or semantic answer quality.
- Preview navigation requires the packet generation to still be current and fresh;
  it refuses historical/retired generations instead of selecting another location.
- Editor text must be byte-compatible UTF-8; BOM/encoding-normalization differences
  can safely reject a legitimate file rather than navigate with an uncertain range.
- Path/canonical-ID behavior is integration-tested on macOS. Cross-platform parity
  is not claimed. Verification uses synchronous existing metadata/hash checks in
  a bounded subprocess; file edits after the final check remain a general filesystem
  race and are not claimed impossible.
- Preview buttons/protocol are unit-tested, and actual panel creation, all depths,
  source opening/range selection and rejection paths run in VS Code. Pixel-perfect
  visual/accessibility testing or exhaustive webview event-race coverage is not claimed.

No root README, production API, model configuration, dependencies, stored user
embeddings, retrieval architecture, Phase 5 understanding, Phase 6 routing, Phase 7
validation or Phase 8 implementation/explanations were modified. Phase 10 was not
started. Only isolated temporary fixture indexes were created for tests.
