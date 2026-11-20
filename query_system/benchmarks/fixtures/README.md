# Frozen legacy Node source corpus

`vendor/legacy_node_server/` preserves all 16 files formerly stored in the ignored
`extension/server/` directory. They are inert qualification inputs, not a runnable
CodeMind server. Do not install their dependencies or import them into the app.

`legacy_node_manifest.json` records the original SHA-256 of every file before
relocation from baseline `37e5cf6`. The `node` qualification profile points directly
to this directory and copies the same filenames into a disposable corpus root, so
existing Node labels such as `chunker.js` and `registry.js` remain valid.

The `vendor` parent keeps archived source out of ordinary repository indexing;
the benchmark profile explicitly selects its child as the source root. The files
remain available to Git and are outside the extension package. Existing benchmark
labels and historical result JSON are unchanged.

Verify preservation with:

```sh
.venv/bin/python -m pytest query_system/test_benchmark_corpus.py -q
```
