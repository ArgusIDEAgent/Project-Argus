# Phase 3 verification: 2026-10-03

Verified locally with Ollama `nomic-embed-text`, digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`, 768-dimensional normalized embeddings. No cloud embedding provider was used.

| Check | Observed result |
| --- | --- |
| Service tests | 25 passed, including HTTP authentication, migrations, incremental indexing, parser/graph behavior, semantic refresh, reuse, feedback, concurrent source edits, and provider failure |
| VS Code development-host test | Passed: chat opens without a workspace, service greeting, parser, Phase 3 command registration, packaged provider module |
| Small semantic fixture | 10 chunks; all 4 expected implementations in top 5; ranks 1, 2, 2, 2 |
| CodeMind repository search | 299 chunks; all 4 expected implementations in top 5; ranks 1, 2, 2, 2 |
| CodeMind initial semantic preparation | Approximately 11.9 seconds in this run, with model already installed |
| Duplicate fixture | Differently named identical arithmetic bodies identified as REUSE; different operations not labeled REUSE |
| Calibration fixture | 8 fixed positive/negative pairs; precision 1.0, recall 0.75 on the calibration set; extend threshold 0.61 |
| CodeMind calibration | 8 fixed positive/negative pairs; precision 1.0, recall 1.0 on the calibration set; extend threshold 0.50 |

The CodeMind searches targeted `parseCode`, `Registry.register`, `graphPath`, and `GitService.changedFiles`. Their queries are checked into `service/scripts/evaluate-semantic.js`. Evaluation metadata is temporary; these runs do not change thresholds in the user's extension database.

These are small development checks, not independent held-out benchmarks. Retrieval ranking was adjusted using these cases. Calibration precision and recall describe those labeled pairs only; default runtime thresholds remain conservative until a repository is explicitly calibrated. No broad accuracy, production-scale latency, or automatic behavioral-equivalence claim follows from these results.

Reproduce with `npm test`, `npm run eval:semantic`, and `npm run eval:semantic -- ..` from `service/`, then `npm run test:integration` from `extension/`. The model must be installed and Ollama running for the real-model commands.
