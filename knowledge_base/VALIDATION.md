# Phase 8 validation results

Measured on 2026-10-13 with Python 3.14.6, macOS arm64, TypeScript 5.9.3,
Tree-sitter 0.25.2, FastEmbed 0.8.0 and FAISS 1.15.1. The benchmark used real
`BAAI/bge-small-en-v1.5` embeddings and private local FAISS storage. No answer
LLM or user-repository indexing was used. These are synthetic fixture results,
not a claim of general accuracy or production readiness for every repository.

## Reproduce

From the project root with Python dependencies and extension npm dependencies
already installed:

```sh
.venv/bin/python -m knowledge_base.validation --output /tmp/codemind-quality-run --sizes 0,32,128
```

Choose a new/empty output directory outside the repository. The command runs six
isolated workers, then all three complete Python regression suites (knowledge
base, AI engine and query system). It writes JSON,
Markdown and diagnostic logs and exits nonzero on failures. Structural-only or
regression-skipping runs cannot claim a complete passing gate. Model download or
missing dependencies are failures, not passing empty results.

The verified local run is retained at:

```text
/Users/paraspingale/.codemind/validation/phase8-2026-10-13-final/quality-report.md
/Users/paraspingale/.codemind/validation/phase8-2026-10-13-final/quality-report.json
```

JSON contains implementation/fixture hashes, dependency versions, per-construct
differences, per-question results, parser failures, unsupported coverage, source
provenance, update checks and resource measurements.

## Results

Every profile achieved 100% eligible inventory, 100% supported-symbol precision
and recall, 100% measured static-relationship precision and recall, 100% curated
recall@5 and 100% returned source-context citation accuracy. There were no parser
failures or dangling metadata references in these fixtures.

| Language | Eligible files | Supported definitions | Index seconds | Retrieval p95 ms |
| --- | ---: | ---: | ---: | ---: |
| JavaScript | 4 | 9 | 0.476 | 125.18 |
| JavaScript | 36 | 41 | 0.964 | 266.20 |
| JavaScript | 132 | 137 | 2.927 | 391.02 |
| TypeScript | 12 | 32 | 0.782 | 292.08 |
| TypeScript | 44 | 64 | 1.576 | 311.55 |
| TypeScript | 140 | 160 | 3.667 | 303.07 |

Ten distinct hand-authored natural-language questions were repeated across three
sizes per language: 30 query executions and 150 checked citations. A hit requires
the expected file, qualified symbol and declaration span, not just the same file.
Each latency percentile has five samples; this is not a concurrent-load benchmark.
Index timings exclude separately measured model warmup; warmup uses the already
cached model and is not a first-ever download measurement.

Worker peak RSS was approximately 426–454 MiB; largest finished child RSS was
119–128 MiB, reported separately, not summed as simultaneous RAM. Initial private
storage was 0.40–3.07 MiB, excluding the shared embedding-model cache. Storage after
update checks is separately recorded; it includes retained generations and vectors.
Performance budgets are deliberately not established until representative
repositories and workloads are tested.

Relationship quality counts unique CALLS, IMPORTS, DEPENDS_ON, EXTENDS and
IMPLEMENTS bindings: 8 in the base JS fixture and 32 in the base TS fixture.
Every relationship occurrence, including structural/reference edges outside that
precision denominator, is independently provenance-checked. Expected unresolved
construct coverage was 100%. TS additionally reported an unsupported variable
export as unresolved; this is visible in the JSON differences, not silently
treated as a verified symbol export.

## Reliability and future impact contracts

All six profiles passed saved-edit invalidation, modification, rename, move,
deletion, no-op embedding reuse and final metadata-integrity checks. Graph checks
cover method callers, module dependents, module/function dependency paths,
canonical test references and explicitly unresolved dynamic relationships.
Bounded call traversal is cycle-safe and reports truncation; test references use
verified relationships and conventional test filenames, not name similarity.
Impact results explicitly mean potentially affected, not guaranteed behaviour.

144 Python regression tests passed: 59 knowledge-base and 85 engine/answer tests.
The new tests include ambiguous union dispatch, overload implementation binding,
duplicate-symbol isolation, UTF-8/CRLF multiline evidence, in-bounds line
tampering, wrong citation identity, malformed manifests, concurrent readers,
process interruption/restart and old-pipeline upgrade without re-embedding.
Existing model/vector-store/watcher failure and corruption tests remain enabled.
Injected failures produce expected warnings in their logs, not test failures.
Extension compilation/integration, Python compilation and whitespace checks also
passed. The existing port-8000 engine was not restarted by these checks.

The hardening fixes incorrect union-callee selection, targets unique overload
implementations, checks line/byte agreement, rejects malformed manifest components
and rebuilds pre-hardening graph generations after the usual engine restart.
Unchanged embeddings are reused. No new dependencies or schema migration are
introduced. Until upgrade synchronization completes, old indexed evidence is
explicitly stale.

## Remaining validation boundaries

Synthetic corpora do not establish production performance budgets or general
semantic accuracy. Remote Qdrant, resolved graphs for languages other than JS/TS,
unsaved editor buffers, runtime dispatch/execution and model-generated claim
correctness are not verified by this benchmark. Static test references are not
coverage guarantees. Full future change-impact UI/execution analysis is not part
of Phase 8; its graph traversal foundations are implemented and tested.
