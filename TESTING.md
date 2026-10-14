Run tests from the project root using its existing virtual environment. This
checkout's directory name ends with a space, so quote absolute paths when using
`cd`. Runtime dependencies are in `requirements.txt`; the separate approved test
tooling is in `requirements-dev.txt` (install with
`.venv/bin/python -m pip install -r requirements-dev.txt` when needed).

The canonical complete Python regression command is:

```sh
.venv/bin/python -m pytest
```

The console entry point and individual modules use the same configuration:

```sh
.venv/bin/pytest
.venv/bin/pytest ai-engine/tests/test_rag_detection.py
.venv/bin/python -m pytest ai-engine/tests/test_rag_detection.py::TestImpactDetection::test_what_is_affected
.venv/bin/python -m pytest ai-engine/tests
.venv/bin/python -m pytest knowledge_base query_system
```

After activating `.venv`, `python -m pytest` and `pytest` are equivalent to the
first two commands. `pytest.ini` explicitly selects the three suite directories
and supplies only the root and the existing, non-package `ai-engine` source
directory as import roots. Pytest's default prepend mode makes sibling engine
fixtures available. No shared `sys.path` rewriting is added. Before this config,
full collection happened to import `config` through other modules, while focused
console collection could not import `rag_pipeline`.

Manual utilities, fixtures, benchmark outputs, environments, dependencies and
generated directories are excluded. The former `ai-engine/test_ai.py` is now
`ai-engine/manual/legacy_openrouter_smoke.py`; it requires an explicit CLI opt-in
and is never part of regression runs.

`conftest.py` forces LiteLLM's bundled model-cost catalog during collection and
blocks external DNS/connections during each test, including swallowed attempts.
Loopback and Unix sockets remain available for isolated fixtures. The socket
guard applies to the pytest process; existing child fixtures use private data
and mocked model/vector clients. No external model request is authorized by a
test command. Tests use temporary source repositories and private index stores.

Existing unittest commands remain supported (the first two also work as documented
without `PYTHONPATH`; engine tests use the existing source import root):

```sh
.venv/bin/python -m unittest discover -s query_system -p 'test_*.py' -q
.venv/bin/python -m unittest discover -s knowledge_base -p 'test_*.py' -q
LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH=ai-engine .venv/bin/python -m unittest discover -s ai-engine/tests -q
```

Use the local-catalog variable on engine unittest runs because unittest does not
load pytest's network guard or configuration. It is a LiteLLM library setting,
not a CodeMind runtime default. Automated generation tests mock model replies.

Additional checks using the installed dependencies:

The validation CLI can also check the two structural fixtures and all three
unittest suites in private temporary storage:

```sh
phase2_check_dir=$(mktemp -d /tmp/codemind-validation.XXXXXX)
.venv/bin/python -m knowledge_base.validation --output "$phase2_check_dir" --sizes 0 --structural-only
```

This reports `partial` because embedding/retrieval/citation/update quality is
unmeasured, even when all regression suites pass. Use `--skip-regressions` only
for a quick structural smoke; that also leaves the regression gate unmeasured.
Invalid workers, missing test counts and subprocess failures make the gate fail.

Offline Node and real editor verification:

```sh
npm --prefix extension test
npm --prefix extension run compile
npm --prefix extension run vscode:prepublish
CODEMIND_TEST_VSCODE='/Applications/Visual Studio Code.app/Contents/MacOS/Code' npm --prefix extension run test:integration
git diff --check -- .
```

The offline command (`npm test` inside `extension`) compiles with existing local
TypeScript tooling and runs Node resolver, rendering, session, transport and
source-navigation tests. It never launches VS Code or downloads dependencies.
Set `CODEMIND_TEST_VSCODE` to an existing local binary before host tests; otherwise
the runner uses the existing installed macOS binary. If neither is available,
it refuses without downloading; report host tests as **not executed**. Its short
temporary profile keeps user settings isolated. The optional real-source host suite requires prequalified
private snapshots and is separate from normal fixture integration tests; do not
point it at a user repository or published snapshots for routine regressions.

The existing VSIX packaging script needs explicit README link bases in this
checkout: `extension/package.json` has no `repository` field. A verified local
packaging command, using the configured origin and main branch, is:

```sh
cd extension
phase1_package_dir=$(mktemp -d /tmp/codemind-phase1-vsix.XXXXXX)
./node_modules/.bin/vsce package --baseContentUrl https://github.com/ArgusIDEAgent/Project-Argus/blob/main/extension --baseImagesUrl https://github.com/ArgusIDEAgent/Project-Argus/raw/main/extension --out "$phase1_package_dir/codemind-extension-0.4.1.vsix"
```

This builds without publishing or replacing existing packages. The missing
repository/license metadata warnings remain release-maintenance issues.
