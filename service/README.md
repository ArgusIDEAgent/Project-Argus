# CodeMind Service

Phase 1 local Node.js HTTP service. It binds to an ephemeral `127.0.0.1` port and accepts `GET /health` and `POST /hello` only when the `x-codemind-session` header matches `CODEMIND_SESSION_TOKEN`. The extension starts and stops it; direct `npm start` requires that environment variable.

Run `npm test` to verify the token check, health endpoint, and Hello reply. Indexing and query APIs will be added in later phases. The target contracts and data model are in [the architecture](../docs/ARCHITECTURE.md).
