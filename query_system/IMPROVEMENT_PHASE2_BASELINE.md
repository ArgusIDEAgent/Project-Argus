# Phase 2 baseline — 2026-10-14

Before Phase 2 implementation: `.venv/bin/python -m pytest -q` passed **456 tests,
585 subtests**, with two existing warnings, in 176.40 s. Existing Node tests
passed **29/29**, zero skipped, in 1.571 s. The Phase 1 named structural regression
and its expanded detector matrix are part of that passing suite.

Phase 1's uncommitted files were present at the start and are preserved. The user
then supplied `REPO_AUDIT_REPORT.md`; it is read-only reference material. Its
806-test/Python 3.14.8/three-venv snapshot differs from the current checkout:
Python 3.14.6, 456 tests, and only root `.venv` present (466 MiB allocated).
Phase 1, Phase 10 and S2 reports were also inspected. No audit coverage percentage
is treated as a current measurement.

Two defects reproduced in disposable repositories before production changes:

1. An indexing hash read raising PermissionError returns `files_failed=0` and
   marks its semantic stage ready despite indexing no file.
2. Malformed TypeScript produces `parsing.partial=1` but publishes a `ready`,
   `fresh=True` generation with no parsed symbols.

Planned changes: narrowly fix confirmed failure propagation in indexing/snapshot,
API state and validation tooling; expand isolated runtime/router/preview tests;
add an offline extension test command and relevant Node regressions; document
scope, legacy entry points and environment findings. Further production fixes
require a failing reproduction first. No dependency, schema, embedding, default
hybrid, locked README, command-removal or user-data change is planned.

Baseline logs: `/tmp/codemind-phase2-baseline-python.log` and
`/tmp/codemind-phase2-baseline-node.log`.
