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
