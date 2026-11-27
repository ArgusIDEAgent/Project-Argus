# Phase 5 — qualified isolation boundary

Implemented and locally qualified 2026-11-27. The P5 broker runs fixed adversarial fixtures in disposable Linux containers inside a dedicated VM. It does not expose a test-run API or turn Phase 4 review selections into execution authorization. Phase 6 remains necessary before product execution is available.

## Phase 4 prerequisite correction

Static Node discovery accepted arbitrary members of a default test binding, such as `test.notARealAPI(...)`, and some unsupported chained assertion expressions. The adapter now recognizes only supported Node member names and restricts assertion member depth/direct-import use. Its version was bumped to invalidate stale extraction caches. A fixture verifies that a fake runner method is not qualified and an unsupported assertion chain supplies no expectation source.

## Supported local configuration

Qualification on this Mac used macOS 27.0, Apple Silicon, Colima 0.10.3 / Lima 2.3.0 using Apple's Virtualization.Framework, Docker CLI 29.10.0, Linux Docker Engine 29.5.2, cgroup v2, Linux kernel 6.8.0-117-generic and runc 1.3.5. Only the dedicated `codemind-runner` VM profile is accepted. No other OS/runtime configuration is claimed qualified by this record.

The VM has two CPUs, 2 GiB memory, a 12 GiB data disk and a 12 GiB root-disk ceiling. It was created with host-directory sharing disabled, no SSH-agent forwarding and automatic context activation disabled. Runtime checks inspect both its configuration and live VM mount table. The container does not receive the VM's Docker/containerd sockets, host source, home, SSH keys, credentials or services.

Runner image: official `docker.io/library/node`, Linux ARM64, immutable digest pinned in `image-lock.json`. The one-time preparation resolved `node:24-bookworm-slim` to digest `sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530`, containing Node 24.22.3. Runs use `--pull=never`; a tag is not accepted by policy. Image preparation downloaded only the trusted runtime image; no repository source or dependencies were uploaded or installed. The image's architecture, digest identity and absence of declared volumes are verified before use.

## Enforcement and contracts

- `CapturedFile` accepts immutable byte payloads with validated SHA-256 and normalized relative paths. Absolute paths, traversal, dot paths, `.git`, `node_modules`, duplicates and mismatched hashes are rejected. There is no symlink/hardlink input representation or host bind mount.
- `NodeCheck` describes a native JS test file in the captured input. The broker owns the Node argument array; callers cannot supply shell commands, Docker flags, ports, mounts, environment files or package scripts.
- The trusted bootstrap reads bounded JSON from stdin and writes copies into a fresh capped `/work` tmpfs. It independently validates paths/counts/hashes before invoking Node. The developer's bytes are never writable through this materialization.
- Containers run as UID/GID 65532, with all Linux capabilities dropped, no-new-privileges, the daemon's builtin seccomp profile, private PID/cgroup namespaces, no shared IPC and a read-only image root. Created-container configuration is inspected before start; the kernel controls were independently checked inside the fixture process.
- `--network none` leaves only the container's loopback interface, with no external or host route, published port or injected host mapping. Same-container loopback services work; host loopback, host gateway, IPv4 external and IPv6 external access are denied by the qualified boundary.
- The adapter uses the dedicated local Unix Docker socket and an owner-private, empty Docker CLI configuration. Ambient Docker context/host variables, proxy settings, credentials, SSH-agent settings and Node options are not inherited by the sandbox. The test child receives only synthetic values and Node's own test-worker metadata.

Default per-container limits:

| Resource | Limit / enforcement |
| --- | --- |
| CPU | 0.5 core via cgroup quota; throttling observed |
| Memory | 256 MiB cgroup cap; swap disabled (`memory.swap.max=0`) |
| Processes/threads | 48 via pids cgroup; EAGAIN observed |
| Workspace | 64 MiB `/work` tmpfs |
| Temporary space | 16 MiB `/tmp` tmpfs |
| Output file space | 8 MiB `/out` tmpfs |
| Single regular file | 8 MiB RLIMIT_FSIZE |
| File descriptors | 128; core files disabled |
| Wall time | 12 seconds per active container |
| Console output | 64 KiB raw combined stdout/stderr; excess triggers termination |
| Captured input | 128 files / 4 MiB; independent bootstrap bounds |
| Cancellation cleanup | Five-second qualification grace; overrun becomes cleanup failure |

Writable application locations are disposable tmpfs mounts with noexec/nosuid/nodev. Docker's synthetic kernel/device interfaces remain present; they are not developer-file mounts. Tmpfs/memory/PID limits were observed, not inferred only from flags. Docker logging is disabled so a console flood cannot grow daemon log files. The VM disk limits bound its background storage; source test programs cannot write the read-only image layer.

## Lifecycle, failure behavior and artifacts

A process lock and filesystem flock allow one internal broker operation at a time. A private journal records only an owned container's random ID, ownership nonce and broker PID. Cancellation/timeout/output overflow use container KILL and force-removal; the container cgroup includes descendants, including detached children. Cleanup verifies container absence. An error does not switch to a host process runner.

Cleanup failure is explicit, retains recovery information and invalidates the qualification attestation. Orphan reconciliation checks the dead owner PID and exact broker ownership labels before removing a container. It never uses global container pruning or deletes unrelated resources. Recovery is repeatable. A live/ambiguous owner or mismatched labels prevents removal. If the VM/daemon cannot be reached, the journal remains and the capability is unqualified until recovery succeeds. No guarantee is claimed that a hung/unreachable hypervisor can be terminated by the container client within the ordinary grace period.

P5 exports only a bounded in-memory `console.txt` artifact. Arbitrary file/artifact extraction is refused; no `docker cp` or untrusted tar extraction is used. Console sanitization removes terminal control sequences/control bytes and masks common secret assignments. Source-embedded secrets cannot be identified perfectly, so this is defense in depth. Console content remains plain text; a future UI must still escape it. Passing a broker fixture does not classify arbitrary repository test results as passed.

## Capability gate and product boundary

`python -m safe_runner` and authenticated `GET /v1/isolation/capabilities` inspect the boundary and its latest qualification. They never create a test container. Missing Docker/VM/image/cgroup/seccomp controls, invalid journal state, code/image/runtime changes or a failed/stale attestation return `isolation_qualified=false` and `execution_available=false` with a reason. No installation or image pull happens during capability reads.

Qualification binds the image, bootstrap, enforcement/fixture source hashes, policy and runtime identity (including CLI hash, daemon/kernel and runc/containerd versions). The local attestation expires after 24 hours. Updating any enforcement component requires requalification. Even with a current passing attestation, **`execution_available=false`** remains explicit because P6 exact-plan authorization, dependency preparation and structured result classification are not implemented.

The private `_run_check` operation is internal qualification infrastructure, not an authorization boundary against code already running as the developer's OS account. Public API and webview inputs have no path to it in P5. The Docker daemon, VM host and broker installation are trusted infrastructure; these tests do not establish immunity to all kernel/hypervisor vulnerabilities or malicious administrator changes.

## Setup and operations

The implementation session installed Colima, Lima and Docker CLI with Homebrew and started the dedicated VM. No login service, passwordless sudo rule or permanent privileged CodeMind helper was installed. No existing Docker context was activated or unrelated VM deleted. The dedicated VM remains running after qualification; stop it to release its reserved memory.

Reproduce setup on a separately supported Mac, then qualify before use:

```sh
brew install colima docker
colima start codemind-runner --activate=false --vm-type vz --runtime docker --cpus 2 --memory 2 --disk 12 --root-disk 12 --mount none --ssh-agent=false --ssh-config=false --port-forwarder none
docker --context colima-codemind-runner pull docker.io/library/node@sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530
.venv/bin/python -m safe_runner.qualify
.venv/bin/python -m safe_runner
```

To stop only this VM:

```sh
colima stop codemind-runner
```

To reconcile a dead broker's journal after the VM is reachable:

```sh
.venv/bin/python -c 'from safe_runner.broker import Broker; print(Broker().reconcile())'
```

Runtime state and the owner-private full fixture report live under `~/.codemind/data/safe-runner/`. The committed `qualification-report.json` records a concise, sanitized summary of this local run, not transferable authorization for another installation.

## Qualification

Thirteen real VM/container cases cover kernel controls; host read/write and symlink escape; absent host/socket/credential mounts; external/host networking and permitted same-container loopback; capped workspace/tmp/output storage; PID exhaustion; memory exhaustion; CPU throttling; timeout with persistent descendants; output flood; malicious console sequences; cancellation descendants; orphan cleanup and cleanup-control failure/recovery. The cleanup-control failure is deliberately injected; recovery subsequently uses real Docker removal and verifies repeatability. Every fixture container was removed.

The first live run found Node's two synthetic test-worker environment keys missing from the fixture expectation. The fixture was corrected to account for those keys; host environment inheritance was not enabled. An initially invalid Docker `--pid private` argument was also removed: Docker's private PID namespace uses the empty/default mode, which is inspected and qualified. Neither failed attempt enabled product execution.

The final runtime report and tests are recorded below. Relevant Phase 4 planning, API, chat and storage regressions were run. Broader release/OS-matrix qualification remains P8.

References: [Docker resource controls](https://docs.docker.com/engine/containers/resource_constraints/), [network none](https://docs.docker.com/engine/network/drivers/none/), [tmpfs](https://docs.docker.com/engine/storage/tmpfs/), [seccomp](https://docs.docker.com/engine/security/seccomp/), [Colima commands](https://www.colima.run/docs/commands/).

Final results: **13 mandatory live fixtures passed** with the final code/policy digest. Cancellation/descendant cleanup returned in **0.902 seconds**, inside the five-second target. Both orphan and injected cleanup-failure recovery passed repeatability checks. A targeted additional native check confirmed `/usr/bin/unshare` actually exists and its mount-namespace attempt fails with Operation not permitted (not an absent-executable false positive). No owned fixture containers remained.

Relevant regressions passed **66 Python tests and 57 subtests**, with one existing Starlette/httpx deprecation warning. The final broker input-policy recheck passed **17 tests** after adding explicit dot-path rejection. `git diff --check` passed within the selected project. The capability reports `isolation_qualified=true` and `execution_available=false`; the default Docker context remains `default`.

Verification commands:

```sh
.venv/bin/python -m pytest knowledge_base/test_safe_runner.py knowledge_base/test_test_planning.py ai-engine/tests/test_change_api.py ai-engine/tests/test_runtime_api.py ai-engine/tests/test_repository_storage.py -q
.venv/bin/python -m safe_runner.qualify
.venv/bin/python -m safe_runner
```

The dedicated VM remains running. No proposed repository test plan was launched. Installation, policy, qualification summaries and source changes are local and uncommitted; this is not P8 release certification.
