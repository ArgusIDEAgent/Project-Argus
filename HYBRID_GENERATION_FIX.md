# Hybrid generation rejection fix

Final shared-checkout verification: **559 tests and 784 subtests passed**, with
2 existing warnings, in 285.48 seconds. The offline extension suite passed all
48 tests; real VS Code integration and docs/save-refresh suites also passed.
The final live Ollama check on that shared checkout returned explanatory prose
with model_status=success, fallback_status=not_used, and no failure category.

Date: 2026-11-20. Validation baseline: `a43fc8e` (committed dead-code cleanup).

## Cause and reproduction

The request schema offered a `fact` response branch, including exact canonical
source-location strings. Ollama repeatedly chose that branch and returned only:

```json
{
  "sections": [{
    "title": "source_facts",
    "claims": [{
      "requirement_orders": [0],
      "mode": "fact",
      "fact_id": "source:<approved-record>",
      "record_ids": ["<approved-record>"],
      "text": "Indexed source at login.ts:3-3."
    }]
  }],
  "coverage": [{"order": 0, "state": "partial"}]
}
```

This is structurally valid under the former request schema but contains no
explanation. The existing backend validator correctly rejects it as
`no_usable_explanation` and emits the deterministic evidence summary. Both live
before-fix runs of “Where is login defined and explain login” reproduced this.

The mismatch is between decoding guidance and the application's explanation
contract, rather than unavailable Ollama, invalid JSON, missing indexed evidence,
or an incorrect source-hash check.

## Change

- `query_system/local_ollama.py`: request explanation prose with approved evidence
  IDs, a null fact ID, and interpretation/hypothesis modes; retain unknown for
  honest abstention. Remove the fact-selection branch from model decoding.
- Bound model output to one section and at most one claim per requirement,
  respecting the request's smaller max_claims limit.
- `query_system/graph_rag.py`: align the system instruction with that schema.
  Canonical facts remain supplied as evidence and displayed by the application.
- Keep the general ModelAnswer contract and all backend validation unchanged.
  Fact-only/unknown-only answers still trigger fallback; source freshness,
  citations, requirement ownership, unsupported fact text, and semantic-only
  interpretations are still checked.
- Keep the configured model, endpoint, token/context budgets, timeouts, and
  one-attempt generation policy unchanged. No additional retry or model/provider
  migration was introduced.

Regression tests verify that the outgoing schema rejects fact-only output,
allows evidence-linked interpretation/hypothesis and honest unknown responses,
rejects invented/missing record IDs and non-null fact IDs, respects output caps,
and retains backend fallback for a producer that ignores decoding guidance.

## Verification

Two new request-schema assertions failed before the production fix and passed
afterward. The focused generation/security suites passed: **48 tests and 25 subtests**.

Three live after-fix runs of the original question returned actual explanatory
prose with approved record IDs, `model_status=success`,
`fallback_status=not_used`, and no failure category. The model described login's
token parameter and delegation to validateToken instead of only repeating a
source-location fact.

The top-level response remained partial because coverage/limitations are separate
from model acceptance. The fix does not relabel partial evidence as complete.

| Check on isolated cleanup baseline plus this patch | Result |
|---|---|
| Full Python regression suite | 548 passed, 784 subtests passed, 2 existing warnings; 372.45 seconds |
| Offline extension suite | 48 passed |
| Real VS Code integration | Passed |
| Real VS Code docs and save-refresh suite | Passed |
| Actual launcher + real BGE/FAISS/local Ollama smoke | Index/search/navigation/refresh passed; initial patched generation accepted, later under-load run hit the existing socket timeout and returned evidence fallback |
| git diff --check (generation patch) | Passed |

## Concurrent checkout changes

During verification, separate in-progress repository-storage/cache changes appeared
in the shared checkout. The first full shared-tree run failed **390 tests with
11 errors** (147 passed) because the new checked_path implementation rejected
macOS /var and /tmp aliases. The editor fixtures failed for the same reason.
These are outside the four-file generation fix and were not reverted or edited.

To verify this fix independently, a detached worktree was created at:

`/private/tmp/codemind-hybrid-validation.ivFdS9/checkout`

It contains baseline a43fc8e plus only the generation fix's four changed files.
It reuses installed dependencies and runs tests against disposable repositories.
The generation fix is also present in the main shared checkout; its unrelated
in-progress changes are preserved.

The storage implementation continued changing during the run, so shared-tree
results must be interpreted with that timing in mind. Isolated passes do not
qualify an unfinished combined storage migration.

After those storage edits progressed, the shared checkout's real VS Code
integration and documentation/save-refresh suites were rerun and passed too.
The fresh combined Python run passed all 559 tests and 784 subtests (2 warnings)
in 285.48 seconds; the offline extension suite also passed all 48 tests again.
Earlier shared-tree fixture failures were transient during that separate work,
not the final test result. Unscoped whitespace checking also observed separate
README CRLF/trailing-whitespace changes; those were not edited by the generation fix.

An initial longer instruction also exposed the existing real-workflow context
budget regression test: required source was displaced. The final instruction was
shortened and that test passed under the unchanged 8192-context/350-output budget.
The obsolete in-flight isolated run was stopped and the complete suite restarted
against the final instruction.

The three repetitions with the final shorter instruction also all succeeded
without fallback. A separate actual-engine smoke while two regression processes
were running hit socket_timeout. No invalid model output was released. The
30-second generation deadline was preserved; fixing the schema mismatch does not
remove legitimate fallback for a slow local model.

## Limits

Live checks use small synthetic code and the installed local llama3 model.
Approved evidence IDs verify provenance, not arbitrary prose factuality.
Other legitimate fallback conditions remain: unavailable/slow models, output
truncation, stale sources, missing evidence, invalid citations, and honest abstention.
No blanket claim of universal explanation success is made.

