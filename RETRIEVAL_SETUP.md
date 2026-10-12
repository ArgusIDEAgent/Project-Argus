# Running the local retrieval and answer pipeline

From the project directory (including its trailing space), run:

```sh
python3 start_codemind.py
```

This uses the project's `.venv` so FastAPI and Tree-sitter are loaded from the
correct environment. Keep this terminal running. Stop an existing engine before
starting another, since only one server can bind port 8000.

No Qdrant server or Docker is needed by default. Phase 5 FAISS vectors and their
durable ID journal persist in `~/.codemind/data/faiss`; inventory, graph, source
context and FTS5 keyword indexes are in the active generation's `metadata.db` (the
metadata CLI below reports its actual path).
The old embedded Qdrant directory (`~/.codemind/data/vectors`) is left untouched.
Install the upgraded dependencies once with
`.venv/bin/python -m pip install -r requirements.txt`, then restart and refresh the
selected repository once to build the new chunks and FAISS index.
An explicit `QDRANT_URL` still selects the existing server-backed option.
Phase 7 coalesces engine refresh jobs and serializes publication per repository;
searches pin a consistent generation and reject unfinished or stale source evidence.
Standalone phase commands are diagnostics, not the engine's publication path.

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
Search Code uses the unified retrieval interface. Exact identifiers and copied
expressions search current saved files. Indexed identifiers, error text and
configuration keys also use SQLite FTS5 BM25. Exploratory questions combine keyword
and vector ranks with Reciprocal Rank Fusion; explanations can include verified
callee definitions. Explicit filenames such as `CLAUDE.md` use file lookup.
Semantic scores are similarity,
not proof that code implements the requested behaviour.

Indexing also builds the Phase 3 JS/TS knowledge graph. Questions such as
`who calls validateToken?` use verified static call relationships. Missing,
ambiguous, dynamic and stale relationships are reported rather than guessed.
The new graph's language support is JS/TS, not Python; other indexed languages
remain available through lexical/vector retrieval. Docs/Reuse endpoints remain
separate pre-existing unfinished features, not part of this retrieval fix.

Phase 6 adds authenticated `POST /retrieve`; `/search/code` and chat `/ask` use the
same evidence pipeline. Retrieval works without an answer LLM and reports `found`,
`not_found` or `ambiguous`. Duplicate symbols/files require qualification rather
than a model guess. Stale source evidence is rejected; unavailable vectors use
keyword evidence with an explicit warning. `retrieval: hybrid` is supported;
`auto` chooses exact/graph routes first, while separate `semantic` and `keyword`
modes remain available. Existing Phase 5 indexes need no rebuild for Phase 6;
restart the engine once to load the new code. Older indexes still need a refresh.

Phase 7 automatically watches registered repositories while the engine runs,
including after a restart or closing the chat panel. Saved edits, additions,
deletions, renames, moves and ignore changes trigger synchronization. Five-second
content-hash audits recover missed events; native-watcher failures and indexing
failures are reported explicitly. It indexes **saved files only**, not unsaved
editor buffers. Graph resolution conservatively rechecks included JS/TS sources;
only changed chunks require new embeddings.

An atomic active pointer publishes graph, symbols, FTS and vectors as one coherent
generation. Failed builds preserve the previous consistent pointer but block stale
indexed answers. Current-disk literal/file evidence stays explicitly labelled.
Changes during answer generation discard the answer and citations. The updated
extension header shows synchronization status (compile/rebuild the extension to
load that UI change). The prepared `codemind-extension-phase7-0.4.1.vsix` includes
this UI change; use the editor's **Install from VSIX** action to install it, then
reload the editor window. No need to run individual phase commands before chatting;
restart the upgraded engine once, wait for the initial synchronization, then save
files normally. Inspect the active SQLite database and freshness read-only with:

```sh
.venv/bin/python -m knowledge_base.metadata --repo "/absolute/path/to/repository"
```

Regression checks:

```sh
.venv/bin/python -m unittest discover -s ai-engine/tests -v
.venv/bin/python -m unittest knowledge_base.test_scanner knowledge_base.test_parser knowledge_base.test_graph -v
.venv/bin/python -m unittest knowledge_base.test_metadata knowledge_base.test_semantic knowledge_base.test_retrieval -v
.venv/bin/python -m knowledge_base.benchmark_semantic
.venv/bin/python -m unittest discover -s ai-engine/tests -p test_phase7.py -v
npm --prefix extension run test:integration
```
