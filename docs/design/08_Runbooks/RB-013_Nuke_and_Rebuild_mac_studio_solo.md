---
project: Shirokuma
doc_id: "RB-013"
title: "Operate, back up, and rebuild SeaweedFS on mac-studio-solo"
status: draft
created: 2026-07-05
updated: 2026-07-16
version: "1.1.2"
area: "runbook"
tags: [shirokuma, runbook, seaweedfs, backup, rebuild, colima]
---

# RB-013 Operate, back up, and rebuild SeaweedFS on `mac-studio-solo`

## Purpose

Define the development-only backup/export, host SSD impact, rollback,
teardown, restore, and destructive rebuild path for the SeaweedFS object store
on the single `mac-studio-solo` lab. This is a reproducibility procedure, not a
production backup, durability, disaster-recovery, or SLA claim.

## Repository contract

| Concern | Value |
|---|---|
| Kubernetes context | `colima-mac-studio-solo` |
| Namespace | `shirokuma-dev` |
| Flux Kustomization | `flux-system/shirokuma-object-storage` |
| Workload | `StatefulSet/seaweedfs` |
| PersistentVolumeClaim | template `seaweedfs-data` -> runtime `seaweedfs-data-seaweedfs-0`, `20Gi`, retained/prune-protected |
| Managed bucket | `shirokuma-lakehouse` |
| S3 Service | `seaweedfs-s3`, `ClusterIP`, port `8333` |
| In-cluster endpoint | `http://seaweedfs-s3.shirokuma-dev.svc.cluster.local:8333` |
| S3 client contract | region `us-east-1`, path-style access, lifecycle `none-local-lite-placeholder`, delete-nonempty disabled |
| NetworkPolicy | `seaweedfs-s3-ingress`; TCP `8333` only from same-namespace pods labeled `shirokuma.dev/object-storage-client: "true"` |
| HTTP probes | startup/liveness `GET /healthz`; readiness `GET /readyz`; named port `s3` (`8333`) |
| Operator identity | `shirokuma-local-lite-operator`, `Admin` |
| Application identity | `shirokuma-lakehouse-application`, bucket-scoped `Read`, `List`, `Tagging`, and `Write` |
| Server config Secret | `seaweedfs-s3-credentials`, key `s3.json`, generated from OpenTofu sensitive inputs |
| Application Secret | `seaweedfs-s3-application-credentials`, bucket-scoped keys and S3 connection settings |
| Credential generation | `shirokuma.dev/s3-credential-generation`, shared by both Secret annotations and the StatefulSet pod template |
| Colima profile | `mac-studio-solo`, `solo-lite`, 400GB virtual disk |

The contract above is desired state. Before operating on data, confirm that the
observed Flux revision contains the merged Issue #26 commit and that the
Kustomization and StatefulSet are ready. That control-plane result is not the
data-plane gate: both Secrets must exist, `/healthz` and `/readyz` must respond,
and authenticated CRUD must pass.

## Safety boundaries

- Run this procedure only for the non-production `mac-studio-solo` lab.
- Never print, commit, attach, or place S3 credential values in shell history.
  Load `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from the operator-approved
  secure channel, or use an owner-only regular `S3_CREDENTIALS_FILE` in the
  SeaweedFS `s3.json` format. Never export a Kubernetes Secret manifest.
- Use `shirokuma-local-lite-operator` only for operator smoke, backup, restore,
  and bucket administration. Application workloads consume
  `Secret/seaweedfs-s3-application-credentials` as
  `shirokuma-lakehouse-application`; never distribute the `Admin` identity to
  Polaris, Trino, or another application pod.
- OpenTofu `sensitive` redacts normal CLI output but does not encrypt local
  state. Treat `opentofu/dev/terraform.tfstate*` and every state backup as
  credential material: never commit them, include them in the object-store
  export, or attach them as Issue, PR, backup, or rebuild evidence. Keep every
  host-local copy only on the trusted host with owner-only permissions. After a
  rotation, an old state file or backup still contains the prior credentials
  and remains secret until its approved retention ends and it is securely
  removed.
- Do not use `kubectl apply`, `kubectl edit`, direct scaling, or a cluster-only
  patch. Desired-state rollback and teardown go through a reviewed Git PR and
  Flux reconciliation.
- `PersistentVolumeClaim/seaweedfs-data-seaweedfs-0` is retained and
  prune-protected. Removing the Flux
  workload must not be interpreted as deleting its data.
- Do not reset Colima until a content export is outside the VM and the export
  inventory and smoke result have been checked.
- `scripts/colima_baseline.sh reset --confirm-data-loss` is the only documented
  whole-profile destructive action. It deletes the entire Colima data disk,
  including the prune-protected PVC.

## Preconditions and readiness gate

Required host tools are kubectl, the repository-pinned Flux CLI, Python 3,
OpenTofu, Colima, and the repository checkout at the approved revision. The
repository-owned backup and S3 helpers use only the Python standard library;
AWS CLI, `mc`, and ambient cloud credentials are not required.

```bash
make colima-status
make gitops-status
flux get kustomization shirokuma-object-storage \
  --namespace flux-system \
  --context colima-mac-studio-solo
kubectl --context colima-mac-studio-solo \
  --namespace shirokuma-dev \
  rollout status statefulset/seaweedfs --timeout=10m
kubectl --context colima-mac-studio-solo \
  --namespace shirokuma-dev \
  get service/seaweedfs-s3 persistentvolumeclaim/seaweedfs-data-seaweedfs-0 \
  networkpolicy/seaweedfs-s3-ingress secret/seaweedfs-s3-credentials \
  secret/seaweedfs-s3-application-credentials
```

Stop if the Flux Kustomization is not `Ready=True`, the observed revision does
not contain the approved commit, the StatefulSet is unavailable, or the PVC is
not Bound at `20Gi`, either Secret is absent, or the NetworkPolicy is absent. A
repository test result is not a substitute for this live gate. An already
running Pod can remain Ready after a required Secret is deleted; its next
restart then fails, so Pod readiness never substitutes for the explicit Secret
check or authenticated CRUD.

## Host SSD and export-space impact

- The Colima `solo-lite` profile exposes a 400GB virtual data disk backed by the
  host SSD. The disk is sparse, so host usage grows with image layers, k3s
  state, SeaweedFS data, replicas, metadata, and temporary files.
- `seaweedfs-data` requests up to 20Gi inside that VM. The request is a logical
  ceiling for this profile, not a production capacity or durability guarantee.
- A file-content export temporarily duplicates the live object bytes outside
  Colima. Keep the export outside `~/.colima`, the repository, and the VM.
- Before a nuke/rebuild, preserve room for the export plus a replacement 400GB
  profile and normal macOS headroom. If that boundary cannot be met, stop and
  move the export to a separately managed volume.

Record the following secret-free measurements with the Issue evidence:

```bash
export SHIROKUMA_HOST_EXPORT_ROOT="/Volumes/<managed-backup>/shirokuma"
mkdir -p "$SHIROKUMA_HOST_EXPORT_ROOT"
chmod 0700 "$SHIROKUMA_HOST_EXPORT_ROOT"
export EXPORT_DIR="$SHIROKUMA_HOST_EXPORT_ROOT/seaweedfs-$(date -u +%Y%m%dT%H%M%SZ)"
test ! -e "$EXPORT_DIR"
df -Pk "$SHIROKUMA_HOST_EXPORT_ROOT"
colima status --profile mac-studio-solo --json
kubectl --context colima-mac-studio-solo \
  --namespace shirokuma-dev \
  get persistentvolumeclaim/seaweedfs-data-seaweedfs-0
```

`/Volumes/<managed-backup>` is an operator-selected example, not a committed
workstation path. Record the chosen volume class and free bytes, but do not add
the absolute path to repository documentation.

## Backup/export

1. Quiesce writers through their owning GitOps Work Packages. During Issue #26,
   no Polaris or Trino writer should be admitted yet. If a writer cannot be
   quiesced, the export is not a consistent checkpoint and the rebuild must
   stop.

2. In a dedicated terminal, expose only the local S3 Service without changing
   desired state:

```bash
kubectl --context colima-mac-studio-solo \
  --namespace shirokuma-dev \
  port-forward service/seaweedfs-s3 18333:8333
```

From the second terminal, confirm the HTTP endpoints before using credentials:

```bash
curl --fail --silent --show-error http://127.0.0.1:18333/healthz >/dev/null
curl --fail --silent --show-error http://127.0.0.1:18333/readyz >/dev/null
```

These endpoints and Kubernetes readiness show process health only. The
authenticated `scripts/object_storage_smoke.sh` run below is the data-plane
create/read/write/delete gate.

3. In a second terminal, load exactly one credential source from the approved
   secure channel. Environment credentials require both variables. A
   `S3_CREDENTIALS_FILE` must be a regular non-symlink file with mode `0600`;
   select `S3_IDENTITY_NAME=shirokuma-local-lite-operator`. Do not set both
   credential forms, retrieve a live Secret manifest, use the application
   identity for operator recovery, or paste values into this runbook or an Issue
   comment.

```bash
umask 077
export S3_ENDPOINT=http://127.0.0.1:18333
export S3_REGION=us-east-1
export S3_BUCKET=shirokuma-lakehouse
: "${AWS_ACCESS_KEY_ID:?load from secure channel}"
: "${AWS_SECRET_ACCESS_KEY:?load from secure channel}"
: "${S3_BUCKET:?set the managed bucket name}"
: "${SHIROKUMA_HOST_EXPORT_ROOT:?set a durable macOS host root}"
: "${EXPORT_DIR:?set a new child export directory}"
```

As an alternative to the two `AWS_*` variables, unset both and set:

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export S3_CREDENTIALS_FILE='<owner-only secure path>/s3.json'
export S3_IDENTITY_NAME=shirokuma-local-lite-operator
test "$(stat -f '%Lp' "$S3_CREDENTIALS_FILE")" = 600
```

4. Capture deterministic inventories before and after the export. The helper
   requires macOS, requires `SHIROKUMA_HOST_EXPORT_ROOT`, rejects the root and
   output when they traverse symlinks, rejects Colima and temporary roots, and
   requires a new output directory inside that root. Each object is stored by
   SHA-256 under `objects/`; `manifest.json` binds key, size, ETag, digest, and
   relative file. It never prints credential values. Export writes a sibling
   `.<destination>.staging-*` tree, fsyncs its files and directories, publishes
   the complete tree with atomic `os.replace`, and then fsyncs the parent.

```bash
python3 scripts/object_storage_backup.py inventory \
  --bucket "$S3_BUCKET" \
  > "${EXPORT_DIR}.source-inventory.json"
python3 scripts/object_storage_backup.py export \
  --bucket "$S3_BUCKET" \
  --output "$EXPORT_DIR"
python3 scripts/object_storage_backup.py inventory \
  --bucket "$S3_BUCKET" \
  > "${EXPORT_DIR}.post-export-inventory.json"
cmp "${EXPORT_DIR}.source-inventory.json" \
  "${EXPORT_DIR}.post-export-inventory.json"
shasum -a 256 "$EXPORT_DIR/manifest.json" \
  > "${EXPORT_DIR}.manifest.json.sha256"
scripts/object_storage_smoke.sh
```

The accepted export reports the same object count and total bytes as both
inventories, `cmp` reports no drift while writers are quiesced, and the smoke
script completes create/read/write/delete without exposing credentials. Retain
the export directory, adjacent inventories, and manifest hash outside Git. This
procedure preserves object file contents for the bounded lab; it does not
guarantee version history, ACLs, all custom metadata, or an
application-consistent snapshot under concurrent writes.

The helper handles one object payload at a time, so export and restore payload
memory are `O(largest object)`; manifest and validation metadata are
`O(object count)`. Export failure does not publish a partial destination.

## Static credential rotation

Treat every static credential rotation as planned maintenance. A Secret update
alone does not reload SeaweedFS `s3.json`, and an existing Pod can remain Ready
with its old in-process configuration even when the required Secret is absent.

1. Quiesce writers and keep the old operator and application credentials in the
   approved secure channel for rollback. Choose the next positive decimal
   generation `N+1`.
2. Open a focused PR that advances both the OpenTofu
   `seaweedfs_s3_credential_generation` default and StatefulSet pod-template
   `shirokuma.dev/s3-credential-generation` annotation to the same `N+1`. Do not
   put credential values in the PR. Pass repository and OpenTofu checks, but do
   not merge yet.
3. From the reviewed candidate checkout, inject all four new sensitive
   `TF_VAR_seaweedfs_s3_{operator,application}_{access,secret}_key` values and
   `TF_VAR_seaweedfs_s3_credential_generation=N+1`, then apply OpenTofu. The
   new operator and application access keys must be distinct; OpenTofu rejects
   an equal pair before changing either Secret. Confirm
   the server config and application Secrets both exist and carry generation
   `N+1` without reading or retaining their data. The running process still uses
   the old static configuration at this point.
4. Merge the generation PR and wait for Flux to perform the planned StatefulSet
   rollout. Do not delete the Pod, patch the StatefulSet, or treat the Secret
   apply as a reload shortcut.
5. Re-establish the port-forward, confirm `/healthz` and `/readyz`, and run
   authenticated CRUD with the new
   `S3_IDENTITY_NAME=shirokuma-local-lite-operator`. Confirm application access
   separately through `Secret/seaweedfs-s3-application-credentials`; application
   pods also require `shirokuma.dev/object-storage-client: "true"` for ingress.
6. Retire the old credentials and resume writers only after the rollout, both
   HTTP endpoints, Secret-existence check, and authenticated data-plane smoke
   all succeed. On failure, keep maintenance in effect and use a reviewed
   rollback plus another monotonic generation bump; never reuse `N`. Keep any
   pre-rotation or apply-time `terraform.tfstate*` backup owner-only and outside
   Git and the object-store export; it remains credential material even after
   the live credentials have changed.

## GitOps rollback and non-destructive teardown

For a bad image, configuration, or resource change:

1. Open and merge a focused rollback PR restoring the last accepted immutable
   digest and manifest configuration.
2. Wait for Flux to reconcile that Git revision. Do not patch the live
   StatefulSet.
3. Re-run the readiness gate and `scripts/object_storage_smoke.sh`.
4. Confirm `persistentvolumeclaim/seaweedfs-data-seaweedfs-0` remains Bound and the known
   persistence-smoke object remains readable.

For a non-destructive object-store teardown, export first, then remove the
object-storage Kustomization and workload resources through a reviewed Git PR.
Flux may prune the StatefulSet, Service, ConfigMap, and NetworkPolicy, but the
prune-protected PVC remains. The two credential Secrets are owned by OpenTofu,
not Flux, and remain until an OpenTofu destroy or explicit replacement. The
retained PVC continues to consume host SSD space and is not proof of a valid
backup. Re-adding the accepted Git resources is the rollback path.

There is no GitOps path in this Work Package for deleting only the PVC. If data
must be destroyed, use the whole-profile nuke/rebuild path below after explicit
export and confirmation.

## Nuke and rebuild

1. Complete the backup/export procedure and verify the paired inventories,
   export manifest, manifest hash, and smoke output. Confirm `EXPORT_DIR` is a
   guarded child of `SHIROKUMA_HOST_EXPORT_ROOT` outside Colima.
2. Capture secret-free pre-reset evidence:

```bash
mkdir -p artifacts/seaweedfs-rebuild
make colima-status > artifacts/seaweedfs-rebuild/colima-before.txt
flux get kustomizations -A --context colima-mac-studio-solo \
  > artifacts/seaweedfs-rebuild/flux-before.txt
kubectl --context colima-mac-studio-solo \
  --namespace shirokuma-dev \
  get statefulset/seaweedfs persistentvolumeclaim/seaweedfs-data-seaweedfs-0 \
  > artifacts/seaweedfs-rebuild/storage-before.txt
```

Review the files before attaching them; redact object keys and never retain a
Secret manifest or credential-bearing log.

3. Delete and recreate only the accepted profile:

```bash
scripts/colima_baseline.sh reset --confirm-data-loss
```

The command targets `mac-studio-solo`, recreates the pinned VZ/aarch64,
16 CPU, 96GB memory, 400GB disk, Colima built-in k3s baseline, restores the
operator's prior kubectl/Docker contexts, and fails unless the baseline status
gate passes.

4. Bootstrap Flux through the repository path and wait for the merged Issue
   #26 revision. Never use direct `kubectl apply` as a shortcut.

```bash
make tofu-validate
make gitops-bootstrap
make gitops-status
flux get kustomization shirokuma-object-storage \
  --namespace flux-system \
  --context colima-mac-studio-solo
kubectl --context colima-mac-studio-solo \
  --namespace shirokuma-dev \
  rollout status statefulset/seaweedfs --timeout=10m
```

Stop if the root GitRepository tracks a branch or revision that does not contain
the merged object-storage manifests.

## Restore and persistence verification

1. Re-establish the local port-forward, guarded host root, and exactly one
   secure credential source from the backup section.
2. Restore the exported contents only after Flux and the empty managed bucket
   are ready:

```bash
shasum -a 256 -c "${EXPORT_DIR}.manifest.json.sha256"
python3 scripts/object_storage_backup.py restore \
  --bucket "$S3_BUCKET" \
  --input "$EXPORT_DIR"
python3 scripts/object_storage_backup.py inventory \
  --bucket "$S3_BUCKET" \
  > "${EXPORT_DIR}.restored-inventory.json"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); i=json.load(open(sys.argv[2])); assert [(o["key"],o["size"]) for o in m["objects"]] == [(o["key"],o["size"]) for o in i["objects"]]' \
  "$EXPORT_DIR/manifest.json" "${EXPORT_DIR}.restored-inventory.json"
scripts/object_storage_smoke.sh
```

3. Verify persistence through an ordinary reviewed GitOps rollout (for example,
   change a harmless pod-template evidence annotation in a focused PR), not by
   deleting or restarting the pod directly. After Flux reports `Ready=True`,
   read a retained persistence-smoke object and run CRUD smoke again.

Restore rejects an unsupported or malformed manifest, a symlink or path escape,
size/digest drift, duplicate key, inconsistent total, or a bucket mismatch. It
uses two passes: pass 1 validates the complete manifest and every referenced
blob without any upload; pass 2 reopens and revalidates one blob at a time,
uploads it, and verifies the readback SHA-256. Payload memory is therefore
`O(largest object)` rather than `O(total export bytes)`. The pre-export and
restored inventories, export manifest and hash, Flux observed revision,
StatefulSet readiness, PVC binding, CRUD output, and persistence readback form
the bounded recovery evidence. Inventory differences must be explained before
the lab is called ready.

## Rollback and failure handling

- Before destructive reset, abort and keep the current VM when export capacity,
  inventory, checksums, writer quiescence, or CRUD smoke fails.
- After reset but before restore, stop at the first failed Colima, Flux,
  resident-image, StatefulSet, PVC, or bucket gate. Keep the external export;
  do not repeatedly rebuild or overwrite it.
- If the new Git revision fails, use a reviewed rollback PR and let Flux return
  to the last accepted revision. The external export remains authoritative for
  recovery of non-reproducible object contents.
- There is no rollback after Colima profile deletion without a verified export.

## Issue #26 closure gate

Issue #26 remains open until all of the following have observed, secret-free
evidence from `colima-mac-studio-solo`:

- Flux object-storage Kustomization and SeaweedFS StatefulSet `Ready=True` at
  the merged revision.
- `NetworkPolicy/seaweedfs-s3-ingress` admits only labeled S3 clients on TCP
  `8333`; no workload client path to a separate non-S3 listener is introduced.
- Both credential Secrets exist, `/healthz` and `/readyz` pass, and authenticated
  CRUD succeeds with the separated operator identity. Ready state alone is not
  accepted.
- Resident-image local-lab gate passes for the exact admitted digest while
  strict mode still rejects any exception-dependent image.
- Bucket create/read/write/delete succeeds.
- A retained object survives a GitOps-driven pod rollout.
- PVC capacity and host/export free-space impact are recorded.
- Backup/export checksums and inventory verify, restore succeeds, and CRUD
  passes after restore.
- GitOps rollback/non-destructive teardown is documented and the destructive
  nuke/rebuild boundary is acknowledged.

Repository-only tests or manifest merge do not satisfy this closure gate.
