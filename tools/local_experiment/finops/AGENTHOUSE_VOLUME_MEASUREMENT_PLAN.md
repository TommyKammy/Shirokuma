# AgentHouse named-volume measurement boundary (WP-L6-FIN-001)

Decision: 2026-09-26, local experiment, after consultation with @architect.
**Design/inspect-only milestone; no volume byte measurement is authorized by
the present collector.** The published Docker-storage schema stays v1 and
`used_bytes=null / NOT_MEASURED` in saved reports v4–v7. The dedicated Colima
profile was stopped at this decision point; it was not started for this review.

## Why a number is not available yet

`docker_storage.py` verifies one labeled container's saved mount reference to
one local named volume. Docker inspect does not traverse that volume or prove
exclusive ownership. The Linux mountpoint returned by Docker is not a macOS
host directory. `docker system df` and Docker's daemon-side volume usage hints
have different coverage, boundedness and cancellation semantics; neither is
the narrowly verified logical-file measurement defined here. Do not infer
zero from a stopped container or an unavailable daemon.

Starting the dedicated Colima VM is a separate lifecycle operation: Docker
restart policies and k3s workloads may write retained state on boot. AgentHouse
`ensure_runtime()` writes credentials/environment, may create resources and
starts ClickHouse; it must **never** be used by this measurement. `docker exec`
requires a running ClickHouse container. A helper container can create a
missing named volume, copy image files into it or leave runtime state even with
a nominally read-only mount. Host mounting the VM disk and Docker Debug add
unnecessary mutable state. None is a safe substitute for a bounded scanner.

## Candidate implementation: opt-in Linux metadata worker

This is a *future* go/no-go checklist, not a claim of an existing worker:

1. Record the exact dedicated profile's stopped/running state, configuration
   hash, retained disk/backup identity, Docker context/socket and recovery
   action. Start the profile only as a separately recorded operation if the
   access prerequisites are ready; never start AgentHouse or other containers
   just to measure it. Keep Flux and unrelated scopes unchanged.
2. Verify daemon identity/ARM64 and Docker root, exact canonical named-volume
   path, local driver/scope/label/creation identity and *empty* driver options.
   Reinspect the fixed container ID, pinned image, stopped/not-restarting
   state and its exact mount. Reject missing/changed/ambiguous objects. A
   volume label alone does not prove that other users cannot write it.
3. Enumerate **all** daemon containers, including stopped/unlabeled ones, and
   inspect their mount records before/after. Bound count/output/time and reject
   truncation, extra running volume references, bind references to the backing
   path or ancestors, and changing/ambiguous reference sets. A stable Docker
   list still cannot exclude VM-host processes, mount aliases or unknown writers;
   do not claim exclusive ownership. Check submounts and filesystem topology.
4. If the stopped topology is accepted, send a fixed, reviewed worker via the
   dedicated Colima SSH channel; use an already present interpreter, no
   install/script/cache in the volume and no credential/output path in the
   public report. Probe effective Linux `O_NOATIME` on *every* directory fd
   used for enumeration; fail closed if unsupported or permission denied.
   `O_NOFOLLOW`/descriptor-relative ancestry, pinned root device+inode and
   explicit Linux owner/mode policy are required. Never silently fall back to
   `du`, a regular directory open, `chmod`, `chown`, remount or a writable
   helper mount. `O_NOATIME` is a prerequisite for avoiding scanner-induced
   atime updates, not proof that boot or other processes made no writes.
5. Two bounded passes may sum `st_size` of accepted regular files. Initially
   reject symlinks, hardlinks, special files, foreign devices/submounts, root
   substitutions and any identity/entry/metadata drift. A ClickHouse layout
   may have such entries; **unknown is a valid outcome**, not a reason to
   follow/skip links. Suggested limits: 10,000 entries, depth 32, 4 MiB total
   names and a 10-second two-pass deadline with checked 64-bit integer bounds.
   Do not read contents. Do not assume a positive byte count exists until this
   topology is independently inspected.
6. Require a VM-side timeout plus host transport timeout and a verified remote
   termination/cleanup acknowledgement. Killing a host SSH client alone does
   not prove the remote worker stopped. On interruption, unverified cleanup or
   uncertain process survival, publish null/`CLEANUP_UNVERIFIED`, suppress
   dependent reads and investigate without changing retained state.
7. Repeat identity, stopped-state and container-reference reads after the scan.
   Record an independently bounded metadata comparison, then return the
   dedicated profile to its recorded prior state by ordinary stop if it was
   started for this experiment. Verify configuration and retained disk/backup
   identity; never reset/delete them. Post-stop Docker API validation is not
   possible and should be labeled as such.

If implemented, the only number eligible for publication is **logical regular-
file bytes (`st_size`) beneath the verified volume root**, with file/child-dir
counts and a time window. Sparse holes count logically; `st_blocks * 512`, if
ever measured, is a *different* metric. Neither is unique disk/VM/APFS
occupancy, ClickHouse table cost, safe reclaimable bytes, quota, backup
completeness or exclusive AgentHouse ownership. Do not add this number to host
or VM filesystem totals. Matching metadata passes are not atomic snapshots.

## Required tests before a numeric schema

- Wrong context/socket/daemon/architecture, noncanonical mountpoint, nonempty
  local-driver options, foreign label, changed creation/container/image/mount.
- Missing volume does not trigger create/mount/repair. Running/restarting
  AgentHouse, other references (including foreign labels and bind aliases),
  unbounded/truncated/changing inventories produce unknown.
- Missing Linux interpreter, denied/unsupported `O_NOATIME`, symlinked ancestor,
  hardlink, special file, submount, root replacement, changing file/sidecar,
  entry/name/depth/time/byte overflow produce null, never a partial sum or 0.
- Remote timeout, orphaned worker and unverifiable cleanup stop dependent
  collection. Secret paths, mountpoints and arbitrary exception text stay
  outside the public report.
- Introduce a **new** Docker-storage/report schema with explicit compatibility
  mapping. Saved v4–v7 reports reject injected Docker bytes and keep their
  original `NOT_MEASURED` meaning. A valid saved mount reference may remain
  observed while new usage is unknown/partial; offline HTML/history must not
  retroactively reinterpret old reports or add cross-report totals.

For this design milestone, only offline contract checks and stopped-profile
status are evidence. No retained volume contents, writer inventory, Linux
no-atime support, backup recovery or byte count were inspected. Functional
acceptance of a numeric measurement and production acceptance are both open.

## 2026-09-27 fixture-only implementation update

`linux_volume_worker.py` now implements the **private, unconnected** Linux
metadata scanner. macOS rejects before traversal. VM-local disposable fixtures
confirmed two fresh no-atime passes, unchanged root/child directory atimes,
logical sparse/hidden file sizes and fail-closed links, special files, root
replacement and limits. The first Linux test failed on `PosixPath` type
checking and was repaired; no retained volume was scanned. The dedicated
Colima profile was started only for this fixture, then ordinarily stopped.
AgentHouse remained exited with restart policy `no`; Docker volume metadata
showed local driver and empty Options. Profile config SHA and VM disk identity
were unchanged after shutdown. k3s shutdown logged normal pod mountpoint
cleanup plus `/var/lib/cni` busy; these facts do **not** establish byte-level
retention or backup restoration. Avoid a further VM start until a justified
remote-lifecycle and mount-boundary implementation is ready.

The worker's mountinfo checks are best-effort. Same-device bind mounts arriving
and disappearing during traversal are not prevented by before/after checks;
per-open mount boundaries and worker/supervisor termination still need proof.
The `mountinfo` parameter is a test seam only, never a retained-volume input.
`docker_volume_preflight.py` adds a pure, offline two-snapshot guard for exact
daemon/container/volume metadata, bounded all-container references, bind-path
overlap, container lifecycle/restart count, projected mount references and
sample freshness. Endpoint equality is not proof of uninterrupted stoppage or
writer quiescence between observations. It explicitly returns
`volume_traversal_authorized=False`: Docker inventory cannot rule out VM-host
writers or mount aliases. There is **no public schema change** and no numeric
Docker storage row at this milestone.

## 2026-09-27 private metadata transport follow-up

`docker_volume_transport.py` now gathers **only** private preflight snapshots
through fixed, bounded Docker/boot-ID reads. It validates the dedicated Unix
socket *before* any daemon inspection, then pins `--host` to that endpoint.
It rejects a foreign daemon and nonempty local-driver Options before inspecting
containers. It strips environment, arbitrary labels, state errors and extra
mount properties immediately; no raw inspect JSON is persisted or returned.
It accepts unrelated unlabeled containers (`Labels: null`) so they cannot be
silently excluded from the all-container inventory. The 20-second transport
deadline, second complete ID listing and daemon reread fail closed on drift.
Start/end timestamps preserve the age of earlier reads for the offline pair
validator. Mocked negative tests cover foreign contexts/options, missing
volume, incomplete/drifting lists and daemon replacement. The Colima profile
remained stopped; these are **transport contract tests**, not a live preflight.
The result still says `volume_traversal_authorized=False` and no Docker bytes
are published. A Colima SSH boot-ID read can outlive a killed host client;
this metadata-only operation does not establish the verified remote-worker
cleanup required before any volume traversal.

## 2026-09-27 offline VM-side source bundle correlation

`linux_volume_source_bundle.py` reads **only** the three fixed local Python
sources `linux_openat2.py`, `linux_volume_worker.py` and
`linux_volume_supervisor.py` through a pinned, no-follow directory. It requires
current-user ownership, single-link regular files, no group/other write bits,
bounded lengths and two equal metadata/content-hash passes; missing, changing,
linked or special sources fail closed. It returns a domain-separated digest and
fixed private manifest, not source contents or a public report row. The local
manifest is reproducible for identical bytes and changes if a source changes.

This is **correlation of local source bytes only**. `worker_digest` in the
existing supervisor, receipt and wire remains caller-controlled: none of those
modules import/execute the pinned files through this descriptor, and they do
not verify the remote VM's interpreter, import graph or actual executed bytes.
Future trusted VM transport must deliver and pin the precise source, establish
VM-side execution provenance, correlate the receipt with that *same* execution,
and prove the independent cleanup and writer/topology gates. A local SHA or
matching caller-supplied field is not remote attestation or permission to scan.
No SSH, VM boot or retained-volume access occurs in this increment; public
AgentHouse bytes stay `NOT_MEASURED`.

## 2026-09-27 openat2 / supervisor fixture boundary

The unconnected Linux worker now uses `openat2` with `RESOLVE_BENEATH |
RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV` for root, directory and ordinary-file
metadata, resolving descendants from its pinned root rather than a potentially
relocated child descriptor. A caller-supplied trusted anchor device/inode and
mount ID are checked, but the caller still must establish trusted anchor
identity and writer/topology quiescence. No unsafe syscall fallback is used.
The private VM-side fixture supervisor gates child execution on a registered
record, bounds runtime, escalates TERM/KILL, reaps the exact child and accepts
bytes only with a terminal, matching cleanup receipt. Disk-backed receipt I/O
can block and host disconnect/remote record retrieval are **not** integrated;
this is not yet a retained-volume execution contract.
The caller-provided `worker_digest` is only an operation correlation field in
the fixture protocol; it does not attest to executable source or remote code.
Before a retained scan, pin and verify the VM-side worker source and establish
an independent reconnect/reap check. Neither the current Linux fixture nor
the Docker metadata transport grants scan permission.

## 2026-09-27 host boot / sleep-sensitive clock fixture gate

`host_clock.py` reads the Darwin `kern.bootsessionuuid` and pairs
`mach_absolute_time` (awake) with `mach_continuous_time` (sleep-inclusive)
using the kernel timebase. Sampling brackets the boot reads and rejects a
long/inconsistent sample. A pure three-sample validator fails closed if boot
IDs change, a clock regresses, the operation/probe ages exceed the existing
30/10-second bounds, or awake and continuous elapsed times differ by over
50 ms. `accept_with_clock` also binds the receipt probe's host timestamp to
its supplied clock sample before invoking the existing private receipt gate.
Synthetic sleep/reboot/reset/stale/mismatched-probe cases are rejected and a
local read-only macOS smoke obtains the native clocks. **No actual host sleep
or reboot was induced.** The 50 ms tolerance and sampling gaps cannot prove
the total absence of short sleeps or that injected samples came from the
trusted host. A future runner must collect the samples itself, spanning the
whole operation and probe without unobserved gaps, and separately authenticate
the VM/worker/cleanup. A receipt plus this offline fixture is not scan
authorization. AgentHouse bytes remain `NOT_MEASURED`.

## 2026-09-27 offline host receipt gate

`linux_volume_receipt_gate.py` adds a pure acceptance boundary for a terminal
VM receipt plus independently obtained same-boot process probe. It checks
operation ID, caller-supplied worker correlation digest, VM and host boot IDs,
canonical process-start ticks (including rejection of leading-zero aliases),
bounded result integers and a 30-second window measured with the **same host
monotonic clock** from the trusted operation start. A missing/stale probe or
still-running exact child rejects bytes. This does not yet obtain, authenticate
or transport a VM receipt or `/proc` probe; the caller must pin the operation
start before launching the worker and securely reconnect to the same VM. A
fabricated probe, host reboot, stale operation ID or unverified worker source
must never be used as positive evidence. No report schema or saved bytes change.

## 2026-09-27 offline known-hosts descriptor snapshot (fixture only)

`pinned_ssh_prefix` adds a scoped, unlinked, read-only descriptor holding the
**caller-supplied, independently enrolled** exact ed25519 entry. It first checks
the preexisting private known-hosts file and identity file with the same strict
contract as `strict_ssh_prefix`. The anonymous descriptor is handed to a child
by `pass_fds`, with `UserKnownHostsFile=/dev/fd/N`, and closed when the scope
ends. In local fixture tests a child can read the pinned entry even after the
original enrollment pathname is replaced; only fixture material is used.
OpenSSH `ssh -G` verifies option parsing, not connection-time host-key lookup.
The descriptor shares an offset with `/dev/fd/N`; whether OpenSSH retains it
and how often it reads it during a **real authenticated connection** is still
unverified. There is deliberately no SSH invocation against the VM, nor any
server-key enrollment. A same-user hostile process is not isolated by this
local file mechanism. A future runner must own the child lifecycle through
exit and prove the pinned file is actually used on a trusted connection before
calling it an authenticated receipt channel. This offline boundary does not
authorize retained-volume traversal or change `NOT_MEASURED`.

## 2026-09-27 offline receipt wire / SSH configuration contract

`linux_volume_receipt_wire.py` validates a bounded (16 KiB), duplicate-key-free
versioned response, with nonce, expected bundle correlation digest, VM boot and
PID/mount namespace plus procfs identity before/after, exact operation receipt,
and a complete independent child-process observation. Invalid UTF-8, unknown
fields, oversized/noncanonical numbers, still-running child and identity drift
fail closed. `linux_volume_ssh_contract.py` constructs a fixed loopback-only
OpenSSH configuration using an **already enrolled** ed25519 server key in a
private exact known-hosts file, no user/global SSH configuration, no proxy,
agent, forwarding or interactive authentication. Literal paths, ancestor
permissions and a bounded no-follow read are checked. Offline OpenSSH `ssh -G`
tests confirm the relevant parsed options without making a connection.

Neither module makes an SSH call or enrolls a VM host key. The original
pathname-based argv builder cannot preserve its known-hosts validation through
SSH reopening that pathname later; the fixture-only descriptor snapshot above
does not yet establish a live OpenSSH trust channel. The bundle digest is still caller-supplied
correlation, not executed-source attestation. Host sleep/reboot clocks, VM
procfs visibility, authenticated source bundle, worker-writer quiescence and
blocked-I/O cleanup remain unproven. These structural checks **do not** grant
retained-volume traversal or change `NOT_MEASURED`.

The existing dedicated Colima `ssh.config` was inspected read-only (no
credential/key bytes copied). It specifies `StrictHostKeyChecking no` and
`UserKnownHostsFile /dev/null`. That configuration provides **no previously
enrolled server-key binding** for this transport. Do not reuse it as proof of
authentication, generate an enrollment from `ssh-keyscan`/first contact, or
enable live receipt collection by relaxing this prerequisite. A separately
trustworthy key provenance is required before a strict SSH runner is attached.

One bounded dedicated Colima boot tested disposable Linux fixtures: 17 tests,
15 pass and two macOS-specific skips, including no-atime worker, supervisor
timeout, TERM-resistant child and failed record registration. Attempting a
private user/mount namespace for same-device directory and file bind fixtures
failed at `uid_map` with `Operation not permitted`; no mount was created.
Do not broaden VM privilege or claim kernel bind-boundary acceptance from
mocked `openat2` flag tests. The profile was ordinarily stopped again; its
config SHA and retained disk device/inode match pre-boot, while guest contents
and backups remain unverified. k3s shutdown again cleaned pod mountpoints and
reported `/var/lib/cni` busy. Retained-volume bytes remain `NOT_MEASURED`.
