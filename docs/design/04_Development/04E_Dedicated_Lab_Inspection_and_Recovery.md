---
project: Shirokuma
doc_id: "DEV-04E"
title: "Dedicated lab inspection and cold recovery boundary"
status: accepted
created: 2026-10-07
updated: 2026-10-08
version: "1.2"
area: development
tags: [shirokuma, local, retention, recovery]
---

# Dedicated lab inspection and recovery

The owner explicitly requested resolving the dedicated inspection/retention
blocker on 2026-10-07. This permits scoped inspection of `mac-studio-solo`,
not unrelated profiles/credentials, global permissions, deleting retained data,
whole-Colima reset or replaying saved requests. The earlier noninteractive
`permission.rejected` remains historical evidence; it was not bypassed inside
that session. An authorized parent inspected the dedicated configuration and
provided checkout-scoped private evidence for subsequent sessions.

## Verified inspection route

1. Confirm all profiles Stopped and the source checkout/runner idle. Read only
   the dedicated Colima profile, its matching Lima instance and additional-disk
   identity. Configuration is VZ/arm64, 16 CPUs, 96 GiB, 20 GiB root plus
   400 GiB ext4 data, `autoActivate:false`. Do not use a default Docker context.
2. Preserve the stopped root/data disks, base image, EFI/VM identity and both
   generated and source profile configurations before starting services. The
   private evidence lives in
   `tmp/local-experiment/lab-inspection-20261007/` (0700; artifacts 0600).
   Eight APFS clonefile copies have independent inodes; logical sizes total
   approximately 450 GB. All reported data extents (53,962,405,731 bytes) were
   compared byte-for-byte and hole maps matched. Both cold ext4 filesystems
   were clean: the read-only parser did not mount, repair or replay journals.
3. Inspect the copies, not live retained filesystems. A project-local ignored
   venv pins `dissect.extfs=3.14`, `dissect.util=3.22`, `dissect.cstruct=4.6`;
   no global package/host installation. Root systemd enablement, Docker
   configs and kube-state records were inspected without printing environment,
   tokens, Secret values or application data. `state.db`, **WAL and SHM** are
   included; SQLite opens private extracted copies read-only and integrity is
   `ok`. Reading the DB without its WAL is not current-state evidence.
4. Inspection boot is separate from Kubernetes/workload boot:

   ```sh
   rtk proxy colima start mac-studio-solo --kubernetes=false --activate=false --ssh-config=false --save-config=false
   rtk proxy colima ssh --profile mac-studio-solo -- systemctl is-active k3s
   rtk proxy colima ssh --profile mac-studio-solo -- docker ps --format '{{.Names}}'
   rtk proxy colima stop mac-studio-solo
   ```

   Recheck prerequisites on each attempt. On the tested inspection boot k3s
   was **inactive/disabled**, no containers ran, and all 58 cold Docker
   container configs had restart policy `no`. This includes Kubernetes's
   Docker containers but is **not** evidence that kubelet would not restart
   them if normal k3s were started. Nineteen named Docker volumes and two
   PVC directories were observed, on the dedicated `/dev/vdb1` ext4 mount.
   The VM was then normally stopped; the source profile config is byte-identical
   to the saved original. Boot writes OS/daemon/filesystem metadata; it is not
   a claim that disk bytes stayed unchanged after boot.

## Retained-state map and recovery

| Preserved copy | Scope |
| --- | --- |
| `cold-backup/00-diffdisk` | Guest root, systemd and runtime configuration |
| `cold-backup/01-datadisk` | Docker named volumes, Kubernetes local-path PVCs, k3s DB/identities and runtime state |
| `cold-backup/02-basedisk` | Matching immutable guest base |
| `cold-backup/03-vz-efi`, `04-vz-identifier` | Matching EFI and VZ identity |
| `cold-backup/05-lima.yaml`, `06-colima.yaml`, `07-colima.yaml` | Generated Lima/internal Colima and source profile configuration; manifest disambiguates the two Colima files |

Live PVC directory mapping:

- `pvc-4da2f1ea-d122-4133-91d7-4cf579f4108f`: `shirokuma-dev/data-polaris-postgresql-0`.
- `pvc-57d415f5-d1e8-4e09-a4a8-41704891e4a9`: `shirokuma-storage/seaweedfs-data-seaweedfs-0`.

Guest data includes metadata PostgreSQL/search, AgentHouse, Loki, Grafana and
Superset named volumes. Host bind-mounted files and the retained metadata dumps
remain outside the guest-disk backup: preserve their original owned directories;
do not claim this is a complete host backup. No retained request journal is
adopted, re-POSTed or sent to a backend. Inspection does not resolve historical
uncertain requests; it avoids dispatching them entirely.

File-level recovery was rehearsed into a **new** private directory using
independent clone inodes. Root/data UUIDs matched, 58 Docker metadata records
were readable, and the extracted DB plus WAL passed SQLite integrity. These
are same-host copy/reconstruction results, **not** booted VM recovery,
application restore, independent-media backup or disaster-recovery acceptance.

For an actual recovery: stop the dedicated VM and confirm no file handles/writers;
retain the changed disks/configs in another versioned private location; validate
the matching manifest/extent fingerprints; reconstruct from the complete copy
set into a fresh staging location. Review mapping and current retention needs
before any replacement. Never overwrite newer retained state or reset Colima
automatically. Unknown/mismatched identity requires investigation. First boot
the recovered state through the same **k3s-disabled inspection route**, and
verify data mounts/SQLite/volume references before considering workload startup.

## Normal Kubernetes startup is a separate gate

### 2026-10-08 API-only secondary-owner repair observation

Repair worker `gate-8ancmu2h` preserved complete owner/resource originals and
inhibited six known nonzero app/Flux ReplicaSets using UID/resourceVersion and
original-replica JSON Patch tests. Fresh final owner/spec inventory shows only
the six expected changes, all scoped parents/secondary owners zero, no scoped
Pods, Flux8/Jobs3 suspended, original PV/PVC UIDs/specs, zero containers and
nineteen volumes/two retained PVC directories present. No normal node start.
API-only `shirokuma-l0-owner-repair-8ancmu2h.service` remains available for
independent parent verification; parent owns subsequent finite-end normal stop.
This is a worker repair observation, not parent clearance or L0 acceptance.

Private snapshots/intents/results and exact rollback are in
`tmp/local-experiment/autonomous-runner/gate-8ancmu2h/attempt-001/` and the WP.
Never automatically restore replicas/unsuspend sources or Jobs. Any deliberate
field rollback needs a separate writer-safety review, same UID/owner chain,
fresh resourceVersion and current replicas=0 tests, replacing only the selected
field with its captured original value. Preserve changed disks before any
cold recovery; original cold copies and failed stops remain untouched.

k3s rewrites live argv: inspect the active transient unit's ExecStart disabled
flags and match its MainPID to the single live k3s server, rather than assuming
`ps args` retains launch flags. The parent probe now tests this binding. No
agent/scheduler/controller-manager/cloud-controller or writer may start in
this repair-only stage.

### 2026-10-08 secondary-owner finding: parent inhibition is insufficient

Fresh API-only observations in private
`tmp/local-experiment/autonomous-runner/gate-uklhzopt/attempt-001/` confirm the
nineteen inhibited parent fields, zero app/Flux Pods and unchanged PV/PVC UIDs.
However, `get replicasets,replicationcontrollers,hpa -A -o json` shows eleven
ReplicaSets, including six nonzero app/Flux owners: Polaris, Trino and the four
Flux controllers each still request one replica. Their ownerReferences match
the zero-replica Deployments. No HPA/ReplicationController was observed.

The disabled controller-manager has not propagated parent scale-down. Normal
startup could race ReplicaSet Pod creation against Deployment reconciliation;
retiring old Pods alone does not remove that risk. **Do not proceed directly
from the earlier parent-field validation to node startup.** A subsequent reviewed
mission must preserve secondary-owner originals and inhibit those six matching
ReplicaSets with atomic UID/resourceVersion conditions while agent/scheduler/
controller-manager remain disabled. Inventory every owner surface, ensure no
app/Flux Pod or unexpected workload, and recheck retained identities before
startup. Unknown identities/mutations stop the dependent operation. Do not
restore original writer replicas, replay Jobs or unsuspend Flux automatically.

This batch made no resource mutations or node/controller starts. Both API-only
services and the dedicated VM were stopped with zero running containers. No
disk restoration/deletion was performed; original cold backup, recovery rehearsal
and safety-stop evidence remain intact. Source/Kustomization status is saved
but Ready conditions are stale; normal Node/Flux readiness remains unverified.

### Subsequent API-only validation and inhibition (2026-10-07)

The proposed stage below was subsequently **tested** on the installed k3s.
After the same k3s-disabled Colima inspection boot, a transient
`shirokuma-l0-api-inspection.service` ran `k3s server` with
`--disable-agent --disable-scheduler --disable-controller-manager
--disable-cloud-controller --disable=traefik --https-listen-port=55737
--bind-address=127.0.0.1`, the existing service EnvironmentFile, `Restart=no`
and `KillMode=control-group`. The ordinary k3s unit stayed inactive. `/readyz`
returned `ok`; no containers ran. This API-only stage writes Kubernetes/OS
metadata, not an offline-only observation.

Forty-one Node/workload/Pod/PV/PVC/Flux resource objects were captured privately
in `api-prior-resources.json` before change. The dedicated node was the only
node; no DaemonSet/CronJob or additional persisted Flux resource group was
observed. The server's auto-manifest directory held system components only,
not Shirokuma application manifests. Existing Flux images were digest-pinned.

With atomic UID/resourceVersion preconditions and no mutation retries, the
parent performed 19 scoped desired-state changes:

- Root plus six child Kustomizations and the one GitRepository: suspend=true.
- Polaris, Trino, Polaris PostgreSQL and SeaweedFS: replicas=0.
- Four Flux controllers: replicas=0.
- Three already-completed bootstrap/smoke Jobs: suspend=true; no re-run.

Eleven captured app/Flux **Pod objects** were retired with UID-preconditioned
DELETEs while no kubelet/containers ran, preventing old assigned Pods from
briefly restarting before a scale-to-zero reconciliation. Their original
metadata is preserved. No PVC, PV, Docker volume, retained disk or backup was
deleted. `api-inhibited-resources.json` independently confirms desired states,
zero app/Flux Pods, and identical UIDs for both PVs and both PVCs. Cold images
remain the pre-change versions. The transient API service was stopped and
the dedicated VM normally stopped; source profile configuration remains
byte-identical. All original stopped-runner evidence remains unchanged.

**Next L0 gate:** inspect that the persisted inhibition still holds; boot only
the dedicated node with app/Flux replicas=0, all Flux sources/Kustomizations
and Jobs suspended. Verify no unexpected application containers appear.
Then selectively bring up the four pinned Flux controllers for readiness
measurement while leaving every source and child reconciliation suspended.
Any drift/unrecognized workload stops the dependent step; never re-POST a
saved request. This clears the specific access/unknown-writer prerequisite,
but it does **not** claim normal-node/Flux live readiness has already passed.

For recovery of the 19 metadata changes, `api-prior-resources.json` preserves
each UID and original field. Restore only a deliberately selected field for
the same UID after a new safety review; do not wholesale `kubectl apply` the
snapshot, blindly unsuspend children or scale retained writers back to one.
Retired Pods must be recreated by their reviewed Deployment/StatefulSet/Job
owners, not by replaying old bootstrap jobs. Preserve originals and the
changed disks if cold-disk recovery is required.

### Earlier cold-stage finding and proposal (retained historical context)

The cold kube DB identifies one dedicated node and no CronJobs. Root Flux
`flux-system` Kustomization is suspended, but its GitRepository (`main`) and
six child Kustomizations are **unsuspended**, all children with `prune:true`.
Thus root suspension alone is not sufficient to protect retained workloads.
Do not run normal `colima start --kubernetes` or reconcile root to discover this.

Next bounded mission: validate an **API-only, no-agent** control-plane stage on
the installed pinned k3s, then capture live desired-state identities and inhibit
the child Flux/job/writer surfaces **before** enabling kubelet or workloads.
K3s documents experimental `--disable-agent` (no kubelet, runtime or CNI).
At this earlier cold stage it had **not yet been tested**; the subsequent
validation above supersedes that status. Controller/add-on effects and the
single-node topology must be considered as well; a flag name alone is not
proof of zero reconciliation. Record prior suspension/replicas/jobs and exact
recovery; enable only named L0 measurement components once scope is established.
An unknown RPC/database operation remains non-replayable throughout.

Do not alter or delete the previous stopped runner's safety checkpoint to
schedule this mission. A new batch needs a separate reviewed mission and the
same original week, 100-new-session allocation and routing checks in DEV-04C.
