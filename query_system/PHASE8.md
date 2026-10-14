# Phase 8 — Evidence-grounded local Graph RAG

## Compatibility assessment and scope

This is a standalone generation consumer, not a replacement knowledge base,
retriever, agent, router or production API. The locked root README, dependencies,
stored user embeddings, existing engines and `/ask`/`/retrieve` are unchanged.
Test/benchmark indexes exist only in temporary synthetic fixture repositories.
Phase 9 presentation is not implemented.

The existing `ai-engine/answers.py` combines evidence extraction, free-text prompt
construction, one Ollama call and legacy answer formatting. It has no separately
callable structured-output client. Calling `answer_question()` would lose the
Phase 7 per-claim citation/identity guarantees; it is inspected, not invoked.
Its `evidence_for()` also clips excerpts and deduplicates overlapping locations,
which is unsuitable for retaining exact canonical Phase 7 records.

| Existing facility | Phase 8 reuse |
| --- | --- |
| `answers.os.environ`, `CODEMIND_LOCAL_MODEL` | Same configured model; default `llama3:latest`, empty value disables generation |
| `answers.Request`, stdlib urllib HTTP | Same request facility and loopback chat endpoint; private opener disables proxies and redirects without changing the legacy client |
| `answers._redact_secrets`, `sanitize_text` | Mandatory presentation filtering of every model-bound field, wrapped with narrowly additive credential patterns |
| `answers._explanation_only` | Detect numeric citation markers without treating code arrays as source references |
| Phase 6 `MultiEngineExecutor.pinned()` | Existing `SnapshotStore.pin_view()` lifetime; no second pin or active-pointer lookup |
| Phase 6 `_target()` | Canonical original-target verification, no new resolver |
| Phase 7 `EvidenceAggregator.from_executor()`, `verify()`, `_finding()` | Fresh source/provenance and canonical fact validation under that pin |
| `SnapshotStore.matches()`, existing `metadata.trace()` | Whole saved-source digest, exact hashes/ranges, entity/file mappings and ready internal stage checks |
| Phase 7 findings, requirements, evidence records and `cm7:` citation map | Preserve accepted proof ownership, classification, ordering, coverage and backend citations |

The small HTTP bridge is necessary because the existing request is inline and
returns arbitrary free text. It adds structured output and transport restrictions
without modifying production code. Ollama's documented chat `format` accepts a
JSON schema: [chat API](https://docs.ollama.com/api/chat) and
[structured output](https://docs.ollama.com/capabilities/structured-outputs).
Schema guidance is not factual verification.

## Files and contracts

Created:

- `generation_contracts.py`: `GenerationRequest`, `GenerationBudget`, prepared
  sources/facts/context, `ModelAnswer`, evidence-linked answer claims/sections,
  original-requirement coverage, unsupported assertions and `GenerationResult`.
- `generation_safety.py`: presentation-only redaction and model-citation rejection.
- `local_ollama.py`: narrow bounded local structured-generation bridge.
- `graph_rag.py`: preparation gate, context selection, generation validation,
  citations, coverage and deterministic fallback.
- `test_generation.py` and `ai-engine/tests/test_phase8_generation.py`: unit and
  existing-fixture integration verification.
- `benchmark_generation.py`, `benchmarks/phase8_mocked_results.json` and
  `benchmarks/phase8_real_results.json`: separate mocked and local evaluations.
- This report; `query_system/README.md` gains only a Phase 8 navigation paragraph.

Phase 1–7 contracts are not modified. Requests carry the original query,
normalized original requirements, package reference, explanation type, bounded
settings and explicit original-order → supporting Phase 7 requirement bindings.
Result `query_id` identifies the original explanation; `support_query_id` retains
the distinct Phase 7 supporting retrieval query identity.
The package/context establish repository and published-generation identity.
Results retain all original requirements, including repeated intents, and carry
separate result, model, fallback and coverage statuses. Internal indexing-stage IDs
remain in canonical citation proofs; different ready stages within one generation
are allowed. Identity hashes are content identifiers, not authentication/signatures.

## One-pin use and supporting evidence

Phase 6 still deliberately defers explanation intents. Feeding that deferred
empty report into generation does not magically retrieve supporting code.
The caller must explicitly supply an existing deterministic support package and
bind it to the separately normalized original explanation. No engine is dispatched
inside generation and no router policy is changed.

Minimal function example, using an already configured `store`, existing retrieval
`channels`, and loaded `answers` module:

```python
from query_system.contracts import QueryRequest
from query_system.executor import MultiEngineExecutor
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.generation_contracts import GenerationRequest
from query_system.graph_rag import GraphRagGenerator

with MultiEngineExecutor.pinned(store, channels) as executor:
    support = QueryRequest(repository_id=executor.context.repository_id,
                           question="Where is `login` defined?")
    report = await executor.execute(executor.plan(executor.understand(support)))
    package = EvidenceAggregator.from_executor(executor).aggregate(report)
    original = QueryRequest(repository_id=executor.context.repository_id,
                            question="Explain `login`")
    normalized = executor.understand(original)
    request = GenerationRequest(
        query=original, normalized=normalized, package_id=package.package_id,
        explanation_type="function",
        bindings={item.order: [coverage.requirement_id
                              for coverage in package.requirements]
                  for item in normalized.requirements},
    )
    answer = GraphRagGenerator(executor, answers).generate(request, package)
```

This minimal package supports only the supplied definition, not missing callers
or callees. A richer caller-created support package can include the existing
definition, callers, callees, contract, path, rule and semantic operations.
Comparison requires two explicit resolved targets and source excerpts for both.
Workflow requires supplied resolved relationships/path evidence. Existing Phase 5
resolution limitations are preserved: use backtick-delimited symbols or canonical
editor context; ambiguous/unresolved plain text is never guessed.
Generation is synchronous; callers should not block an interactive event loop.

## Evidence gate, redaction and bounded context

Before model access, validate package shape/citations, live pin, repository and
generation identity; re-run verification for every accepted record and canonical
finding. Quarantined records cannot be restored. Exact original-target proofs are
also checked. Recheck immediately before generation and before releasing output;
changed evidence discards all generated claims, context and citations.

Read only accepted exact line ranges with byte-line semantics consistent with
metadata; hashes remain hashes of original source bytes. Presentation redaction
does not rewrite provenance or persisted indexes. Phase 7 budget-omitted source
context is not restored. Redact excerpts, paths/names, question, requirements,
signatures/parameter defaults, graph/rule details, limitations and coverage metadata.
If the existing privacy filter fails, send nothing to the model.

Additional patterns cover named secret assignments/JSON fields, secret parameter
defaults, environment assignments, Bearer credentials and JWT-like literals.
These are pattern-based defenses, not a guarantee of detecting every possible
credential. Source comments/injection strings stay JSON-escaped user data. The
trusted system instruction is fixed plus enum-selected task guidance. The model
has no search, file, graph, command or tool interface. Prompt defenses cannot prove
that a model never follows malicious text; output checks still apply.

Priority goes to original target source records, then supporting accepted records.
Records deduplicate by Phase 7 canonical record ID, not names or text. Graph/path
direction and producer finding order remain intact; semantic rank/method are not
rewritten. All support mappings remain available; findings retain ownership.
Target source-location observations are deterministic backend facts referring to
existing proofs, not new knowledge-base records.

Defaults: 16 records, 20 facts, 8,000 source bytes, 20,000 message bytes,
8,192 context tokens, 350 output tokens, 30-second cooperative deadline,
65,536 response bytes and 24 claims. Selection conservatively admits message bytes
below `min(max_context_bytes, num_ctx - num_predict - 512)` and reserves coverage
annotation space. This is not exact tokenization. `input_bytes` counts message
content, not HTTP/schema serialization. Model-reported token counts are also
checked when available. Limits are configurable through strict bounded contracts.
Optional records/facts/excerpts are removed atomically, with explicit omissions;
required-target omissions block generation or downgrade coverage. No truncation
is reported as a complete explanation. An oversized mandatory envelope cannot
be sent; safely retained facts can still support an incomplete fallback.

## Tasks, structured output and grounding

Supported task guidance covers function definitions/visible inputs, outputs,
steps, branches and supplied direct relationships; multi-file static workflows;
possible behavioral causes; and comparisons of two explicit targets. Missing
characteristics remain partial/unknown. Static paths do not establish runtime
execution order, root cause, performance or behavioral equivalence.

There is at most one client generation invocation per request. Fixed loopback
transport has no remote endpoint/provider parameter, no proxies, redirects,
tools, model downloads or automatic retry. Explicit cloud-tagged/URL models are
blocked. The operator must still run a trusted local Ollama daemon with local
weights: application loopback transport alone cannot certify arbitrary daemon
configuration/hidden aliases or enforce system-wide network isolation.

The response is strict JSON. Dynamic schema guidance restricts record IDs,
requirement orders and fact IDs; prose uses `fact_id=null`. Backend validation
still independently checks schema, approved context membership, requirement
ownership, complete catalog-fact proof sets and claim limits. A fact's optional
text must exactly equal its approved canonical statement; the backend renders
that statement. Hypotheses/unresolved facts cannot be promoted to authoritative
claims, and semantic candidates cannot be rewritten as verified dependencies.
Similarity-only interpretations cannot establish target behavior.

Original model prose is always a non-authoritative interpretation/hypothesis,
even when its source IDs are valid. Arbitrary prose is not fully fact-checked.
Backend-owned `cm7:<package>:<record>` citations are selected from the existing
approved map; model-written citation IDs/markers and extra citation fields are
rejected. Coverage must include every original requirement; missing target
references, supporting gaps or omissions prevent complete coverage. Behavioral
investigation remains partial without runtime proof. Coverage validation catches
structural omissions, not every omission of meaning in free-form prose.

## Fallback

Unavailable/disabled/late models, malformed JSON, output limits, invalid IDs,
fabricated catalog facts or unusable explanations yield a deterministic summary
of safely retained facts with backend source references and actual failure category.
Possible impacts remain hypotheses, never fabricated behavioral explanations.
Explanation coverage is incomplete. No safe evidence yields no claims/citations.
Socket timeouts/deadline checks are cooperative, not hard cancellation; synchronous
HTTP/filesystem validation can outlast the deadline. The pin stays open until the
call returns. A subsequent source change always fails closed at the final gate.

## Verification and separate evaluations

Reproducible commands using the project virtual environment:

```sh
.venv/bin/python -m unittest query_system.test_contracts query_system.test_rules query_system.test_semantic query_system.test_understanding query_system.test_execution query_system.test_evidence query_system.test_generation -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
npm --prefix extension run test:integration
.venv/bin/python -m query_system.benchmark_generation --fixture
.venv/bin/python -m query_system.benchmark_generation --fixture --real-local
git diff --check
```

The deterministic suite runs without Ollama. Nine new unit tests and 27 new
integration tests cover all requested scenarios, plus closed-pin safety,
redactor failure, ordered paths, repeated requirements, two-target coverage,
explicit resolution and no legacy answer/engine dispatch.

Final regression results: **359 Python tests passed, zero failed** (39 shared
query-system tests, 59 knowledge-base tests, 261 AI-engine tests including
Phases 0B–8). The 36 new tests are included, not counted twice. VS Code extension
compile/integration completed with exit code 0. `git diff --check`, whitespace
checks on all new files, and compilation checks passed. Existing SQLite resource,
FastAPI deprecation and local-Qdrant warnings were emitted, along with expected
injected-offline failure logs; they did not fail the suites and were not changed
in this scoped phase. No production route, legacy answer implementation,
retrieval/routing/evidence implementation, dependency or locked root README was
modified; the only previously tracked file changed is `query_system/README.md`.

Saved [mocked results](benchmarks/phase8_mocked_results.json): four tasks × three
repetitions, 12 accepted structured model fixtures, 84/84 valid citations, all
original requirement coverage entries preserved, no positive-run fallback.
Three negative probes (offline, malformed and fabricated fact with valid source)
all yield incomplete deterministic evidence summaries. Median/p95 preparation:
38.371/51.934 ms; mocked client invocation: 0.040/0.054 ms; total including
validation: 90.785/112.989 ms. Median/max input: 12,939.5/13,198 bytes, using an
explicit 32,768-token test window. Mock latency is not real-model performance.

Saved [real local results](benchmarks/phase8_real_results.json) separately retain
three development smoke calls, not automatic retries. The first correctly fell
back on invalid claim fields; later schema/prompt changes were followed by two
successful calls. Final configured `llama3:latest` call: one generation invocation,
62.606 ms preparation, 14,467.151 ms model call, 14,600.306 ms total, 6,130 input
bytes and 302 excerpt bytes. Model and backend schemas passed; grounding checks
accepted the claim; one backend citation is valid. Result remains partial because
context was truncated. No model installation, provider switch or user indexing
was performed. One smoke example is not a broad answer-quality benchmark.

### Agent-reviewed examples and grounding limitations

Reviewed the existing fixture and saved structured outputs, not an independent
human evaluation. Citation/schema validity is not a factual-accuracy metric.

| Example | Review finding |
| --- | --- |
| Mock function/workflow | Generic static-flow prose matches supplied path evidence, but does not explain all visible inputs/outputs or workflow steps; tests validate protocol, not explanation richness |
| Mock behavior | Appropriately qualified possible static behavior, partial status; no verified runtime cause |
| Mock comparison | Both targets referenced, but generic prose is not a useful detailed comparison; structural coverage cannot certify semantic completeness |
| Fabricated fact probe | “Always succeeds and is O(1)” has valid source IDs but disagrees with the canonical fact; rejected with `unsupported_fact_text`, not released |
| First local development smoke | Invalid claim-field usage rejected with `missing_evidence_or_text`; raw response was not captured, so no prose-quality claim is made |
| Final real function explanation | `login(token: string) { return validateToken(token); }` supports the input, delegated return and `login.ts` location in the returned prose; no fabricated runtime/performance assertion found in this one example, and missing detailed relationships remain partial |

Remaining limits: no automatic explanation retrieval/routing or Phase 9 UI;
explicit trusted support bindings; existing symbol-resolution constraints; static
analysis only; pattern-based redaction; no independent proof of arbitrary model
prose, semantic completeness or daemon isolation; conservative rather than exact
token accounting; synchronous cooperative deadlines; default output budget may
be too small for complex JSON and will safely fall back. Private canonical
verification helpers are compatibility dependencies covered by regression tests.
