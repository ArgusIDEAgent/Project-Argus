This directory contains manual-only legacy utilities, excluded from automated
test discovery. `legacy_openrouter_smoke.py` was formerly `ai-engine/test_ai.py`;
the old path in historical plans and benchmark reports is retained as history.
No production code imports this utility. Normal chat uses local Ollama.

Running the utility without authorization (including when a key is already set)
refuses before importing provider libraries or loading `.env`. Importing the
module itself has no provider or credential side effects.

Only if you intentionally authorize an external request, from the project root:

```sh
.venv/bin/python ai-engine/manual/legacy_openrouter_smoke.py --allow-external-llm
```

This sends a fixed AST question to the legacy OpenRouter model; no repository
source is included. An `OPENROUTER_API_KEY` is required. Provider availability and
pricing are not guaranteed by the historical `:free` model name. Never commit a
key or paste it into logs. This command is not part of any regression workflow.
