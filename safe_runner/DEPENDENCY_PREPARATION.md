# Phase 6 dependency preparation

The supported Phase 6 registry-archive-v1 adapter prepares small pure-JavaScript
dependency trees from captured npm lockfiles. It does not invoke npm, so there is
no ambient package-manager version/configuration or lifecycle execution. The
installer version and source, runtime image, platform, source capture, metadata,
selected checks, package URLs/versions/integrities and resource policy are bound
into exact preparation and run authorization. Subsequent Phase 7 and release
qualification are documented in PHASE7.md and PHASE8.md.

## Separate network approval

The native **Review environment preparation** dialog lists every package, exact
tarball URL and SHA-512 integrity. Only after explicit approval does the broker
perform GET requests over HTTPS to public registry.npmjs.org addresses on port
443. DNS addresses are checked and the same validated public address is connected
with certificate/hostname verification. Private/loopback/link-local addresses,
custom hosts, query strings, credentials, redirects and proxy configuration are
not accepted. No authorization/cookie headers, developer package configuration,
source files or application secrets are sent to the registry. The registry sees
the listed package requests and ordinary connection metadata.

The broker treats response bodies only as bounded bytes; it neither extracts nor
imports them on macOS. SHA-512 must match the captured lockfile before an archive
is admitted. Limits: eight packages, 256 KiB per compressed archive, 1 MiB total,
15-second cooperative download deadline, two-second connection/read timeouts.
OS DNS resolution may exceed the ordinary socket timeout; cancellation is checked
between network operations and no later container launch bypasses authorization
expiry. Downloads use no persistent disk cache.

## Offline sandbox installation

The trusted bounded USTAR installer runs inside the same qualified offline
container as preparation. It independently verifies archive integrity, package
names, tar checksums, entry sizes and manifest name/version/dependency identity.
Only regular files and directories below package/ are accepted. Traversal,
symlinks, hardlinks, devices, unsupported tar extensions, duplicate paths,
embedded node_modules/.git and native .node/.gyp files are rejected. Unsupported
archives fail preparation; they do not reach a host extractor or fallback runner.

Expanded archive bounds are 16 MiB per decompression, 8 MiB aggregate written
content, 1 MiB per file and 1,024 entries. Existing container memory/tmpfs/time
limits also apply. Extracted files receive fresh ordinary copies with no execute
permission; archive ownership/modes are ignored. Manifest install hooks, binaries,
native/platform builds, bundled packages, peer/optional dependencies and workspaces
are explicitly unsupported. No package code runs during preparation; ordinary
package code may execute only in a separately authorized test run.

## Reuse and supported dependency semantics

Captured npm package-lock v2/v3 supplies a flat node_modules packages map.
Root dependencies and devDependencies must match its root entry. Every dependency
edge must be an exact version matching the locked flat map; closure and absence
of unrelated entries are checked. Semver ranges, nested layouts, aliases, git/file
dependencies, private registries and registry metadata resolution are unsupported.
These cases stay planning-only rather than being silently installed differently.

The initial runtime remains digest-pinned Node 24.22.3 Linux ARM64. The custom
installer version is registry-archive-v1, pinned by the enforcement digest; npm is
not run. Captured engine constraints retain the previously documented matcher.

A successful preparation receipt retains immutable compressed archive bytes in
bounded session memory, tied to exact reviewed identities. Runs decompress them
anew into disposable node_modules for each file. Tests can modify their disposable
copies without changing reusable archives or developer files. There are no shared
writable dependency directories, host mounts, package credentials, network routes
in the sandbox, symlinks or developer node_modules. Receipt expiry, source saves,
selection changes, shutdown and session restart revoke reuse.

## Qualification

```sh
.venv/bin/python -m pytest knowledge_base/test_dependency_preparation.py knowledge_base/test_authorized_execution.py knowledge_base/test_safe_runner.py knowledge_base/test_test_planning.py ai-engine/tests/test_change_api.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
npm --prefix extension test
.venv/bin/python -m safe_runner.qualify
.venv/bin/python -m safe_runner.qualify_execution --dependencies
```

The dependency qualifier uses fixed disposable repositories and one exact pinned
public package (is-number 7.0.0), not a selected developer plan. It exercises the
real authenticated fixture API -> review -> authorized download -> offline
installation -> receipt -> separately authorized run path. It also rejects
traversal, symlink, hardlink, lifecycle-hook and native-file archives in real
containers and verifies fresh copies across consecutive runs after a test corrupts
its own dependency file. The P5 adversarial isolation suite is rerun because
installer and preparation code are part of the enforcement identity.

The npm lockfile semantics are documented in the [official npm lockfile reference](https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json/).
Qualification establishes the recorded bounded adapter, not general npm ecosystem
compatibility or absence of every possible runtime/kernel vulnerability.

Final local verification (2026-11-27): **102 Python tests and 57 subtests passed**,
**55 extension tests passed**, all **13 P5 isolation fixtures passed**, and all
**16 P6 live checks passed**. The final enforcement digest is recorded in both
qualification JSON files. No owned fixture containers remained. The updated VSIX
was built and installed; reload VS Code and restart the engine to activate the
new client/backend code. No tests from the developer-selected repository ran.
