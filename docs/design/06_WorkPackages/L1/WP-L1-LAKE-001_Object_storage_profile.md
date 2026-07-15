---
project: Shirokuma
doc_id: "WP-L1-LAKE-001"
title: "WP-L1-LAKE-001 SeaweedFS-first object storage profile"
status: completed
created: 2026-07-05
updated: 2026-07-16
version: "1.4"
area: "workpackage"
tags: [shirokuma, workpackage, l1, lakehouse]
---

# WP-L1-LAKE-001 SeaweedFS-first object storage profile

## Summary

`mac-studio-solo`へSeaweedFS-firstのS3-compatible object storageを導入し、
PolarisとTrinoが利用できるL1 storage boundaryを構成する。

## Context

L1 core workloadの導入はL0 level gate通過後に開始する。SeaweedFSを本線とし、
MinIOは互換性実験用のpinned fallbackに限定する。

| Field | Value |
|---|---|
| WP ID | WP-L1-LAKE-001 |
| Level | L1 |
| Workstream | Lakehouse |
| Depends-on | WP-L0-PLAT-001, WP-L1-ARM-001 |
| Entry gate | L0 Epic #1 closed |

## Dependencies

- Depends on: `WP-L0-PLAT-001`, `WP-L1-ARM-001`.
- Entry gate: L0 Epic [#1](https://github.com/TommyKammy/Shirokuma/issues/1)
  must be closed before any L1 core runtime is introduced.
- `WP-L1-ARM-001` must first record an approved linux/arm64 image decision.

## Scope

Implement an S3-compatible object-store profile for `mac-studio-solo`. The
mainline path is **SeaweedFS first** reconciled by Flux into Colima built-in
k3s. MinIO or a Colima-managed standalone container is allowed only as a
documented compatibility experiment, never as a co-equal primary path.

## Non-scope

- Ceph or a multi-node storage design.
- Production durability, SLA, or disaster-recovery certification.
- Promoting MinIO or RustFS to the mainline object-store default.

## Deliverables

- Flux-managed SeaweedFS manifests and pinned resident-image evidence.
- IaC-managed `shirokuma-storage`/`shirokuma-dev` namespace boundary, bucket,
  credentials boundary, and lifecycle placeholder.
- Repository-owned `scripts/object_storage_backup.py` export/restore/inventory
  helper plus disk-impact, rollback, teardown, and nuke/rebuild procedure in
  [[08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo]].
- Storage readiness checks consumable by later Polaris and Trino Work Packages.

## Acceptance Criteria

- SeaweedFS runs through Flux reconciliation in Colima built-in k3s on linux/arm64.
- The admitted image is pinned by immutable digest in the resident-image ledger;
  `Critical=0`, `High=0` (or an exact unexpired ADR-0019 local-lab exception),
  signature, provenance, SBOM, and scan gates pass.
- Bucket, credentials, lifecycle placeholder, and backup/export path are IaC-managed.
- Requests and limits satisfy the repository policy baseline.
- `weed mini` or equivalent Kubernetes deployment is documented for local-lite.
- Polaris/Trino can access the object store through S3-compatible endpoint.
- Flux reports the object-storage Kustomization and SeaweedFS workload
  `Ready=True`; no direct-apply path is introduced.
- `NetworkPolicy/seaweedfs-s3-ingress` admits only TCP `8333` from
  `shirokuma-dev` pods labeled
  `shirokuma.dev/object-storage-client: "true"`; workload clients cannot reach
  a SeaweedFS non-S3 listener port.
- SeaweedFS, its Service/PVC, and the Admin-bearing server config Secret are
  isolated in the OpenTofu-managed `shirokuma-storage` namespace. Application
  workloads and their bucket-scoped Secret remain in `shirokuma-dev`, so an
  application pod cannot mount the server Secret by name.
- HTTP startup/liveness `/healthz` and readiness `/readyz` probes pass on the S3
  port, both credential Secrets exist, and authenticated CRUD succeeds. An
  existing Pod or StatefulSet at `Ready=True` is not sufficient data-plane or
  Secret-existence evidence.
- Bucket create/read/write/delete, pod-restart persistence, backup/export, and
  restore smoke are observed on `colima-mac-studio-solo` without retaining
  credential values in logs or Git.
- Restore validates the complete export before mutation and refuses a target
  bucket containing any object before the first upload. A partial restore retry
  starts only after the target bucket has been made completely empty again.
- SeaweedFS built-in Iceberg REST Catalog is documented as local-lite optional, not mainline.
- If MinIO is used, exact digest/tag, CVE risk, and replacement plan are recorded.
- Ceph references are removed from primary path.

## Related docs / ADR

- `docs/design/07_ADR/ADR-0016_Use_Colima_on_Mac_Studio_Solo_as_primary_lab_runtime.md`
- `docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md`
- `docs/design/07_ADR/ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab.md`
- `docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md`
- `docs/design/02_Architecture/024_GitOps_Reconciliation_Model.md`
- `docs/design/02_Architecture/02C_Deployment_Topologies.md`
- `docs/design/03_Requirements/034_Platform_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/049_Supply_Chain_Security.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`
- `docs/design/08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo.md`

## Suggested Labels

`level:L1`, `workstream:lakehouse`, `type:work-package`

## Notes

SeaweedFS is Apache 2.0 and has built-in Iceberg REST Catalog support. Polaris remains the mainline Catalog for neutrality.

L0 Epic #1 closed on 2026-07-14, so the level gate is satisfied. The unsigned
upstream SeaweedFS `4.39` image remains rejected. ADR-0020 instead authorizes
the exact Shirokuma repository source build under the closed-world contract.

PR #42 merged the trusted builder and verifier. Main run `29418029340`, attempt
`1`, then published and verified the native `linux/arm64` image:

`ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421`

PR #43 merged the retained main-run evidence and moved artifact admission to
`approved`. The retained source of truth is
`bootstrap/seaweedfs/v4.39/{admission.json,release-evidence.json,evidence/}`.
This approval permits the exact digest to enter the resident-image ledger; it
does not prove that a Kubernetes runtime is ready or that data is durable.

Issue #26 repository and runtime acceptance is retained below. The record covers
Flux readiness, HTTP probes, CRUD and IAM denial, restart persistence,
backup/export and restore, host SSD impact, and GitOps teardown/rollback without
retaining credentials. NetworkPolicy rendering was verified, while live
pod-to-pod enforcement remains explicitly limited to spec-only evidence because
no eligible application Pod existed. This closure record makes Issue #27 the
next executable child after Issue #26 closes.

## Runtime contract

| Concern | Repository contract |
|---|---|
| Storage namespace | `shirokuma-storage`; OpenTofu-managed; contains SeaweedFS, Service, PVC, NetworkPolicy, ConfigMap, and server config Secret |
| Application namespace | `shirokuma-dev`; OpenTofu-managed; contains the bucket-scoped application Secret and opted-in clients |
| Flux object | `flux-system/Kustomization/shirokuma-object-storage` |
| Flux path | `./deploy/gitops/object-storage` |
| Workload | `StatefulSet/seaweedfs` |
| Persistent data | `volumeClaimTemplate/seaweedfs-data` -> `PersistentVolumeClaim/seaweedfs-data-seaweedfs-0`, `20Gi`, retained/prune-protected |
| Managed bucket | `shirokuma-lakehouse` |
| S3 Service | `Service/seaweedfs-s3`, `ClusterIP`, port `8333` |
| In-cluster endpoint | `http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333` |
| S3 client contract | region `us-east-1`, path-style access, lifecycle `none-local-lite-placeholder`, delete-nonempty disabled |
| Network boundary | `NetworkPolicy/seaweedfs-s3-ingress`; SeaweedFS ingress is TCP `8333` only from `shirokuma-dev` pods labeled `shirokuma.dev/object-storage-client: "true"` |
| HTTP probes | startup/liveness `GET /healthz`; readiness `GET /readyz`; named port `s3` (`8333`) |
| Operator identity | `shirokuma-local-lite-operator`, `Admin`; operator smoke, backup, restore, and bucket administration only |
| Application identity | `shirokuma-lakehouse-application`; `Read`, `List`, `Tagging`, and `Write` scoped to `shirokuma-lakehouse` only |
| Server credentials | `shirokuma-storage/Secret/seaweedfs-s3-credentials`, key `s3.json`, contains both identity definitions from OpenTofu sensitive inputs; application pods cannot mount this cross-namespace Secret |
| Consumer credentials | `shirokuma-dev/Secret/seaweedfs-s3-application-credentials`, bucket-scoped application keys and cross-namespace S3 connection contract only |
| Credential generation | `shirokuma.dev/s3-credential-generation`; the two Secret annotations and StatefulSet pod-template annotation use the same positive integer |
| Reconciliation | `dependsOn: shirokuma-dev`, `prune: true`, `wait: true`, `timeout: 10m`, StatefulSet health check |

The repository contract above is not live evidence. The observed Flux revision
must contain the merged Issue #26 commit before runtime acceptance is recorded.

`NetworkPolicy/seaweedfs-s3-ingress` selects only the SeaweedFS object-storage
pod and exposes no separate non-S3 listener to workload clients; `/healthz` and
`/readyz` share the permitted S3 port. A later Polaris or Trino pod must opt in
with `shirokuma.dev/object-storage-client: "true"` and consume
`Secret/seaweedfs-s3-application-credentials`; it must not receive the
`Admin` operator identity. Operator backup and restore with a SeaweedFS
`s3.json` file select exactly
`S3_IDENTITY_NAME=shirokuma-local-lite-operator`.

The operator and application access keys must be distinct. OpenTofu rejects an
equal pair before writing either Secret because SeaweedFS indexes identities by
access key and a duplicate would collapse the intended privilege boundary.

The credential configuration is static process input. Applying either Secret
does not reload a running SeaweedFS process, and a running Pod can remain Ready
after its required Secret has been deleted even though its next restart will
fail. Rotation is therefore planned maintenance: quiesce writers, prepare a
reviewed PR that advances the Secret and pod-template generation to the same
`N+1`, apply the new sensitive values and Secret annotations through OpenTofu,
then merge the pod-template bump so Flux performs the rollout. Confirm both
Secrets exist, both HTTP probes pass, and authenticated CRUD succeeds with the
new operator credential before retiring the old credential or resuming clients.

The repository backup helper bounds payload memory to `O(largest object)`.
Export downloads one object at a time into a sibling
`.<destination>.staging-*` directory, fsyncs new files and the staging directory
tree, atomically publishes with `os.replace`, then fsyncs the parent directory.
Restore is two-pass: pass 1 validates the whole manifest, totals, unique keys,
safe paths, sizes, and SHA-256 digests without uploading; pass 2 reopens and
revalidates one object at a time, uploads it, and verifies the readback before
continuing. Metadata remains `O(object count)`.

## Repository evidence contract

| Concern | Path / mode |
|---|---|
| Resident ledger | `security/resident-images.json` |
| Evidence mode | `repository_source_build` |
| SBOM mirror | `security/evidence/seaweedfs-v4.39/seaweedfs-4.39-arm64.cdx.json` |
| Scan mirror | `security/evidence/seaweedfs-v4.39/trivy.json` |
| Supply-chain record | `security/evidence/seaweedfs-v4.39/supply-chain.json` |
| Admission authority | `bootstrap/seaweedfs/v4.39/admission.json` and `release-evidence.json` |

The security mirrors are byte- and SHA-256-bound to the admitted bootstrap
evidence. Parent traversal, symlinks, hash drift, digest drift, mutable image
references, and any missing repository-source-build control fail closed.

```bash
python3 scripts/verify_supply_chain.py check-images \
  --manifest security/resident-images.json \
  --profile local-lab \
  --exceptions security/resident-image-exceptions.json
make verify-security
make verify
```

Passing these repository gates is necessary but does not satisfy the live
closure observations in the Acceptance Criteria and RB-013.

## Live acceptance evidence

The observations below were collected on `colima-mac-studio-solo` on
2026-07-16. Credential values, workstation-absolute backup paths, and OpenTofu
state are intentionally excluded.

| Concern | Secret-free observation |
|---|---|
| Flux and workload readiness | PR #46 promoted the Flux v2.9.2 canonical manifests and `main` source contract; PR #47 completed the staging-to-main bridge. Acceptance baseline `main@sha1:2609910cdf3654b94909ac0498bdc7135f2be12b` reconciled successfully. `flux-system`, `shirokuma-dev`, and `shirokuma-object-storage` reported `Ready=True`; all four Flux controllers, `StatefulSet/seaweedfs`, and its Pod were Ready on approved immutable digests. SeaweedFS used `sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421`; `/healthz` and `/readyz` probes passed, and the `20Gi` PVC was `Bound`. Post-rollback readiness was reconfirmed at `main@sha1:3f71957763c3def1487ef7fe01eb56ca02853842`. |
| CRUD and IAM boundary | Operator temporary-bucket create/read/write/list/delete succeeded. The application identity successfully performed Put/Get/List/Delete in `shirokuma-lakehouse`; CreateBucket, DeleteBucket, and Put to another bucket each returned HTTP `403`. No credential value was retained. |
| Persistence and restart | `acceptance/persistence.bin` was `102400` bytes with SHA-256 `d8e82f2a1e10ce259f3955a3d946a3f1d643927b371374bb7d38fdff54f1e96f`. The bytes and digest matched before and after the Colima restart, after restore, and after the GitOps teardown/rollback recreation. Post-rollback operator smoke also passed. |
| Export, restore, and host SSD | Export inventory recorded `object_count=1` and `total_bytes=102400`; source, post-export, and post-restore inventories matched, and manifest/payload digests verified. Restore refused the non-empty target before its first upload; after the target was emptied, restore and readback succeeded. Host/export permissions were `0700`/`0600`. The export directory occupied `104 KiB`, the guarded backup root occupied `124 KiB` total, and the target filesystem reported `2900673716 KiB` (about `2.70 TiB`) free after capture. The Colima profile has a `400 GiB` maximum VM disk and the retained PVC is `20Gi`. |
| Flux teardown and rollback | Teardown PR #48 merged as `1e37494435db2465d5d6bf3fb68b6054836d71ae` and removed the object-storage child from the Flux root. After reconciliation, the child Kustomization and workload resources were absent while PVC `57d415f5-d1e8-4e09-a4a8-41704891e4a9`, storage Secret `13904276-650c-409b-8c13-bf7eaa94ed83`, and application Secret `37b0b8fb-f3dc-4a3b-afc7-14cf713fd5f9` were retained. Rollback PR #49 merged as `3f71957763c3def1487ef7fe01eb56ca02853842`; readiness, the same retained-resource UIDs, and the persistence digest were reconfirmed. |
| NetworkPolicy limitation | The rendered `NetworkPolicy/seaweedfs-s3-ingress` admits TCP `8333` only from opted-in `shirokuma-dev` clients. No GitOps-managed application Pod existed during acceptance, so pod-to-pod enforcement and non-S3-port denial were not exercised live; this remains spec-only evidence. The IAM denial boundary above was exercised live. |

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#26](https://github.com/TommyKammy/Shirokuma/issues/26)
- Trusted artifact child: [#41](https://github.com/TommyKammy/Shirokuma/issues/41)
- Trusted artifact PR: [#42](https://github.com/TommyKammy/Shirokuma/pull/42)
- Main evidence PR: [#43](https://github.com/TommyKammy/Shirokuma/pull/43)
- Flux main handoff PR: [#46](https://github.com/TommyKammy/Shirokuma/pull/46)
- Bootstrap bridge PR: [#47](https://github.com/TommyKammy/Shirokuma/pull/47)
- Teardown drill PR: [#48](https://github.com/TommyKammy/Shirokuma/pull/48)
- Teardown rollback PR: [#49](https://github.com/TommyKammy/Shirokuma/pull/49)
- Closure evidence PR: [#50](https://github.com/TommyKammy/Shirokuma/pull/50)
- Main publication: [run 29418029340 attempt 1](https://github.com/TommyKammy/Shirokuma/actions/runs/29418029340/attempts/1)
- GitHub depends on: `#1`, `#8`, `#25`
- Execution order: `2 of 10`
- Queue: Issue #26 repository and live acceptance are complete; this document is
  the closure record. Issue #27 is the next executable child after GitHub records
  Issue #26 as closed.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L1-LAKE-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].
