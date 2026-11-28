# Qualified Vitest adapter — Phase 7

This adapter is separate from the small dependency-archive installer. It uses
Vitest 5.1.2, Vite 8.4.0, npm 11.19.1 and Node 24.22.3, Linux ARM64, in a dedicated
immutable local image recorded in `vitest-image-lock.json`. The toolchain npm
lockfile records the complete resolved graph (63 entries including its root),
public registry tarball URLs and SHA-512 identities. The image ID pins all image
contents, including the framework's native tooling. No framework tooling executes
on macOS.

## Compatibility matrix

| Area | Supported | Unsupported / fail-closed |
| --- | --- | --- |
| Host/runtime | Previously qualified local macOS ARM64 Colima VZ Docker VM | Other hosts/platforms or missing image/attestation |
| Framework | Exact Vitest 5.1.2 and Vite 8.4.0 graph | Other versions, ranges, different transitive identities |
| Source | Root-package ESM `.js` / `.mjs` | TS/JSX compilation, CommonJS tests, monorepo orchestration |
| Declaration discovery | Explicit imports of test/it/expect, named aliases; bounded literal callbacks | Globals, dynamic test factories, each tables, arbitrary assertion chains |
| Project dependencies | Only the pinned framework toolchain declared at exact versions; matching npm v2/v3 packages graph | Additional application packages, peer resolution changes, nested custom trees |
| Configuration | Broker-owned Node environment, forks, one worker, exact selected file | Repository configs, plugins, setupFiles/globalSetup, browser/UI, coverage |
| Workflow | Separate prep/run authorization; comparable baseline/current retries | Vitest temporary-code generation (native Node literal templates remain available) |
| Isolation | Same P5 CPU/memory/PID/disk/time/output limits, no network/host mounts | Any host process fallback, credential sharing, ports, socket mounts |

Mixed Node/Vitest profiles stay unsupported. Framework syntax discovery does not
establish runtime coverage or assertion relevance. Planning remains available
when an execution profile fails compatibility checks. Static selection is never
authorization to run.

## Toolchain preparation and identity

`python -m safe_runner.prepare_vitest` is an explicit installation/development
operation. It submits only the checked-in fixed manifest, lockfile and Dockerfile
to the dedicated VM. The build has registry network access solely for toolchain
acquisition; it never receives developer repository bytes, host home mounts,
credentials or package configuration. npm ci uses the frozen lock with lifecycle
scripts, auditing and funding disabled. Peer auto-resolution is disabled, since
Vite is already explicitly locked. Installed files become a read-only image layer.
The Docker build uses the ordinary image-build sandbox, not the product-run
policy; the package manager and pinned framework toolchain are trusted setup
infrastructure. Untrusted project programs never run during this setup.

The command prints an immutable image lock. On another supported installation,
review that output and update the local `vitest-image-lock.json`, then qualify
both adapters. Merely having a mutable image tag is insufficient. Changing
toolchain image/lock, runtime or enforcement sources invalidates qualification.

Product **Prepare environment** uses only the already-prepared immutable image
and the trusted runtime-check script. It does not resolve packages, run npm, fetch
the network or evaluate repository code. The native confirmation includes the
toolchain versions/image/lock identity and fixed configuration. Exact authorization
and receipt bindings include all of them.

## Execution and reports

Each operation materializes captured bytes into fresh capped tmpfs. Trusted links
inside the container expose only the image's immutable Vitest/Vite packages; they
are not host mounts or developer node_modules. Every subsequent run starts fresh.
The image root is read-only, capabilities dropped, seccomp/no-new-privileges active,
and networking disabled. P5 limits are unchanged: 256 MiB, 48 processes, 0.5 CPU,
12 seconds, 64 KiB console, bounded writable tmpfs. The framework runs a single
fork and applies 2.5-second test/hook timeouts inside that outer wall deadline.

The broker writes a fixed native-loaded config, with no project configuration,
plugins, env files, browser or coverage. Only literal safe-character JS paths may
be selected. All tests/hooks in that selected file may execute. Package scripts
are never used. Custom configurations are refused, not silently approximated.

Vitest JSON is written to a fixed `/out` file. A bounded no-follow regular-file
read transfers at most 32 KiB into the broker's existing output budget. The parser
is independent of Node's event parser, validates test counts/statuses/source path,
and distinguishes assertion, setup, zero-test and timeout observations. It strips
the encoded transfer frame from displayed console output and sanitizes decoded
failure text before returning it. Malformed, ambiguous, oversized or inconsistent
reports cannot pass. Reports are still untrusted program observations, not proof
against deliberate test falsification.

Baseline/current attribution uses the same P7 comparability and retained-retry
rules. Different failures or setup/configuration/dependency mismatches yield
inconclusive. No selected developer plan runs as part of adapter qualification.

## Qualification and operations

```sh
.venv/bin/python -m safe_runner.qualify
.venv/bin/python -m safe_runner.qualify --vitest
.venv/bin/python -m safe_runner.qualify_vitest
.venv/bin/python -m pytest knowledge_base/test_vitest_adapter.py knowledge_base/test_phase7_testing.py knowledge_base/test_authorized_execution.py knowledge_base/test_dependency_preparation.py knowledge_base/test_safe_runner.py knowledge_base/test_test_planning.py ai-engine/tests/test_change_api.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
npm --prefix extension test
```

The Vitest boundary qualifier writes a preliminary boundary record, not product
authorization. The adapter qualifier first disables the final Vitest attestation,
checks that preliminary record, and runs real Vitest compatibility/host/network/
symlink/resource/output/cancellation fixtures plus fixed product lifecycle checks.
Only completion publishes the final `qualification-vitest.json` (24-hour expiry).
Its fixture-only lifecycle manager temporarily supplies the tested identity while
the public/default capability remains false; no user-facing bypass is installed.
Failure leaves the final attestation unavailable. Owned-container cleanup and
orphan reconciliation remain shared and serialized across adapters.

This is local Phase 7 adapter qualification, not Phase 8 release certification.
The synthetic captures in fixtures do not replace native APFS capture qualification.

Final verification (2026-11-27): 115 Python tests and 57 subtests passed; all 55
extension tests passed. The Vitest image passed 13 boundary fixtures plus 19 real
adapter/lifecycle checks, including actual baseline-pass/current-fail attribution.
The existing Node image passed all 13 isolation fixtures again, plus all 16 P6
dependency/execution checks and four P7 template/pair workflows. Both adapter
attestations match the final code and image identities. No fixture containers
remain. The VSIX was rebuilt and installed; restart the backend and reload VS Code
to activate. Phase 7 is complete in this compatibility scope; Phase 8 is unstarted.

References: [Vitest reporters](https://main.vitest.dev/guide/reporters.html),
[single-file parallelism](https://main.vitest.dev/config/fileparallelism),
[npm frozen installation](https://docs.npmjs.com/cli/commands/npm-ci/).
