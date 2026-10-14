# S2 audit before behavior changes

`query_understanding.parse_question()` owns lexical intent recognition
(`_PATTERNS`), mention extraction, clause boundaries and ordered target/dependency
assignment. `_Understanding.symbol()` reuses pinned `_Run.symbol()` and
`metadata.trace()`; `assign()` preserves canonical ambiguity candidates.
`capabilities()` annotates support/clarification. Phase 6 `build_plan()` consumes
these contracts directly; `FEATURE_DISCOVERY` selects existing
`semantic.hybrid_search`. Comparison/explanation requirements remain deferred
at that deterministic boundary and are consumed by existing Phase 10 support.

## Reproduced causes

1. Every unquoted `and` becomes a clause boundary before intent/target roles are
   known. Comparison target assignment uses all entities only for a single-event
   question; in compounds the first `and` ends the comparison before target two.
   Bare lowercase operands are not extracted because the target-prefix list has
   no comparison role. Repeated comparisons can inherit a prior unrelated target.
2. Typed noun extraction treats prose like `function responsible` and `file hashes`
   as explicit function/file references. `hashes` then becomes a semantic file
   filter although no path was requested, so Phase 6 blocks discovery as ambiguous.
3. `Show ...` has no location pattern, and `_PRONOUN` treats relative `that` in
   `Show the code that processes uploaded files` as a contextual pronoun. The
   resulting UNRESOLVED requirement goes through exact-target clarification.
4. `both` is extracted as an identifier instead of a plural reference. Inherited
   comparison operands can unnecessarily depend on deferred comparison output;
   their canonical input IDs are already independently resolved by Phase 5.

The baseline reproduces these failures with existing fixture `login`, `entry`,
`validateToken` and `standalone`, without executing answering engines or a model.

## Focused correction and compatibility

- Preserve comparison-operand conjunctions and descriptive conjunctions; recognize
  a new requirement at existing intent/command cues. Quoted/code spans retain
  their existing masking. Do not split every `and` globally.
- Extract both comparison operands by role, including exact quoted, qualified and
  path-scoped operands. Preserve each comparison pair and distinct repeated rows.
- Recognize descriptive discovery clauses before exact mention extraction; keep
  their full text and only genuine explicit paths as optional retrieval scopes.
  Explicit definition/caller/callee/named-symbol queries retain deterministic lookup.
- Treat plural references to a comparison's input pair as shared inputs rather
  than a dependency on a deferred comparison result. Singular references to pairs
  remain ambiguous. Discovery-dependent references still require candidate selection.
- Preserve existing contracts, canonical resolution, engines, Phase 6 operations,
  evidence verification, generation, formatting and production defaults.

New tests and the before/after benchmark will label actual target pairs, required
rows and router decisions. Existing Phase 10 tests that explicitly encoded the
old comparison defect need narrowly justified expectation updates. No knowledge
base or stored embeddings are rebuilt. Existing test fixtures remain private test
data; real integration will use an existing snapshot only if one is available.

Verification also identified a necessary output compatibility detail: Phase 6
interprets a two-target `DEPENDENCY_PATH` as a path. A plural dependency-summary
request after a comparison is therefore represented as existing single-target
summary requirements in original target order. It does not assert a path between
the compared inputs. No router or operation implementation needs changing.
