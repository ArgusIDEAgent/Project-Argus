# Running the local retrieval and answer pipeline

From the project directory (including its trailing space), run:

```sh
python3 start_codemind.py
```

This uses the project's `.venv` so FastAPI and Tree-sitter are loaded from the
correct environment. Keep this terminal running. Stop an existing engine before
starting another: embedded Qdrant allows only one process to own its storage.

No Qdrant server or Docker is needed by default. Vectors persist in
`~/.codemind/data/vectors`; inventory and graph SQLite databases are per repository.
An explicit `QDRANT_URL` still selects the existing server-backed option.
Do not run the standalone vector indexing CLI while the embedded engine is running.

For generated answers, start Ollama and use an installed model. The default is
`llama3:latest`; set `CODEMIND_LOCAL_MODEL` before starting the engine to change it.
Set it to an empty string for evidence-only answers. Answer requests go only to
`127.0.0.1:11434`; no cloud LLM is used by `/ask`. If Ollama is unavailable, the
chat shows evidence with an explicit warning instead of failing.

The model writes only the explanation. The backend creates the numbered Sources
list and API `citations` from verified retrieved files, line ranges and hashes.
Repeated evidence is deduplicated, and source numbers match the editor's result
cards. These sources describe the context supplied for the answer; they do not
independently verify every model claim. Timeouts, malformed replies and output
limits have distinct messages. Source edits during generation discard the answer.

Reload the editor window after installing extension 0.4.1. Select the repository
and wait for indexing. Chat Send retrieves evidence and generates an answer;
Search Code remains a literal/semantic search action. Exact identifiers and copied
expressions search current saved files. Conversational questions use vectors,
except explicit filenames such as `CLAUDE.md`. Semantic scores are similarity,
not proof that code implements the requested behaviour.

Indexing also builds the Phase 3 JS/TS knowledge graph. Questions such as
`who calls validateToken?` use verified static call relationships. Missing,
ambiguous, dynamic and stale relationships are reported rather than guessed.
The new graph's language support is JS/TS, not Python; other indexed languages
remain available through lexical/vector retrieval. Docs/Reuse endpoints remain
separate pre-existing unfinished features, not part of this retrieval fix.

Regression checks:

```sh
.venv/bin/python -m unittest discover -s ai-engine/tests -v
.venv/bin/python -m unittest knowledge_base.test_scanner knowledge_base.test_parser knowledge_base.test_graph -v
npm --prefix extension run test:integration
```
