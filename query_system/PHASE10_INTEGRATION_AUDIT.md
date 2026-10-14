# Phase 10 integration audit and authorized change map

This audit precedes source implementation. Existing uncommitted Phase 9 work is
preserved. The locked root README and knowledge-base/embedding implementations
will not change. Initial rollout is development-only and opt-in, never default.

Additional compatibility finding during verification: Phase 5's `active_file` is
an explicit scope, not a weak hint. The extension therefore sends editor context
only for deictic questions (`this`, `here`, `current`, `selected`); ordinary named
repository questions stay repository-wide. Explicit API context remains unchanged.
An "and ... and" compound comparison can lose its second target; semicolon/versus
phrasing is supported. No parser redesign is proposed or applied.

Real-corpus publication exposed an existing TypeScript resolver crash at
`knowledge_base/semantic_resolver.cjs:99` (`decl.parent` undefined). This is outside
the integration changes. Target-sized qualification is blocked; fixing the
knowledge-base resolver requires a separately authorized change and regressions.

| Producer → consumer | Concrete compatibility |
| --- | --- |
| `QueryRequest` → `MultiEngineExecutor.understand()` | Direct Phase 1/5 contract; registered root establishes repository, `pinned()` establishes one published generation |
| `NormalizedQuery` → `executor.plan()` → `execute()` | Direct Phase 6 contracts; same executor scope, existing routing/operations/budgets |
| `ExecutionReport` → `EvidenceAggregator.from_executor().aggregate()` | Direct Phase 7 contract; verifies canonical proofs, ready internal stages, ownership, conflicts and coverage |
| `EvidencePackage` → `GraphRagGenerator.generate(GenerationRequest, package)` | Compatible, but requires explicit deterministic support bindings; Phase 6 explanation requirements are deliberately deferred |
| Package/generation → `format_response()` | Direct Phase 9 contracts, but one generation task/type at a time; mixed deterministic/explanation questions need a display-only composition |
| `PresentationResult` → VS Code `renderResponseBody()` | Phase 9 renderer reusable; current chat receives legacy `SearchResult`, so a versioned opt-in transport branch is necessary |
| Backend citations → existing verified source opener | Canonical citation data is compatible; fresh backend navigation authorization must replace the preview's manual JSON/subprocess workflow |

## Minimal necessary changes

1. Add an orchestration entry point and minimal versioned request/response wrappers
   that reuse existing contracts. Keep normalization, execution, aggregation,
   generation and final source checks inside one executor pin.
2. Collect explanation support using a bounded projection of existing deterministic
   requirements (definitions, contracts, callers/callees and paths). Original
   requirements remain first, unchanged and ordered. Append only supported
   requirements, respect the existing 12-requirement/operation bounds, deduplicate
   equivalent support and reuse already requested deterministic work. This is an
   integration projection, not a replacement Phase 5 classifier or Phase 6 router.
3. Group compatible explanation requirements by the existing Phase 8 task type.
   Supply accepted/redacted evidence through explicit bindings; call Phase 8 only
   for those groups. Reindex group-local contract orders, then restore original
   identity/dependencies in the final display. Preserve deterministic independent
   branches and actual generation/fallback outcomes.
4. Compose Phase 9 displays using one unified Phase 7 package/citation namespace.
   Add no new factual statements, retrieval, inference or citation model.
5. Add authenticated `/v1/hybrid` query/readiness/presentation/navigation routes.
   Require explicit `CODEMIND_ENABLE_HYBRID=1`, registered repositories, bounded
   requests/concurrency and a bounded expiring server-owned answer cache. Navigation
   accepts opaque answer handles/reference IDs, never client file paths/proofs.
   Legacy routes, error conventions, session dependency and loopback Host boundary
   remain intact.
6. Add default-false `codemind.hybridEnabled` and scoped depth/optional-generation
   settings. Reuse chat UI, authenticated loopback transport, Phase 9 renderer and
   verified source opener. Disabled mode takes the existing code path. Display-only
   depth changes and copy-only follow-ups must not regenerate or retrieve answers.
7. Add end-to-end, security and rollback tests; evaluate isolated snapshots of real
   source repositories/content as well as known-ground-truth fixtures. Never
   reindex user repositories or overwrite their stored embeddings for evaluation.

## Preserved boundaries and explicit gaps

Phase 6's deferred-intent policy is not modified. Its real dependency/blocking
semantics remain visible, including inherited dependencies on deferred requirements.
If support admission reaches existing bounds, show incomplete coverage rather than
discard original requirements or invent facts. Ambiguous targets are not selected
from semantic scores. Unsupported statistics, languages, dynamic relationships and
unresolved comparison/workflow targets remain capability gaps. No new statistics
operation or unrestricted agent is introduced.

Readiness distinguishes deterministic snapshot availability from optional local
model availability. All inference uses the existing configured loopback Ollama
bridge; no model installation/provider switch is automatic. Model/source work is
cooperative and is not claimed hard-cancellable. A successful synthetic run alone
cannot justify production-default migration; final status and evaluation limitations
will be recorded in `PHASE10.md`.
