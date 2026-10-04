# CodeMind Service

Local Node.js HTTP service. It binds to an ephemeral `127.0.0.1` port and requires the `x-codemind-session` header to match `CODEMIND_SESSION_TOKEN`. The extension starts and stops it; direct `npm start` requires that environment variable.

Run `npm install` and `npm test` to verify authentication, migrations, inventory, graph traversal, semantic refresh, reuse detection, documentation sync, and stale-record cleanup against temporary repositories. The database lives outside the source tree. See the [Phase 4 contract](../docs/PHASE_4_CONTRACT.md) for local text model setup, documentation APIs, and freshness rules. The [Phase 3 contract](../docs/PHASE_3_CONTRACT.md) covers search/reuse APIs and calibration; the [Phase 0](../docs/PHASE_0_CONTRACT.md), [Phase 1](../docs/PHASE_1_CONTRACT.md), and [Phase 2](../docs/PHASE_2_CONTRACT.md) contracts describe earlier layers. Generative chat answers remain planned.
