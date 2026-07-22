---
project: Shirokuma
doc_id: "RB-014"
title: "Verify and recover the Iceberg table bootstrap"
status: active
created: 2026-07-22
updated: 2026-07-22
version: "1.0"
area: "runbook"
tags: [shirokuma, runbook, iceberg, polaris, flux, local-lite]
---

# RB-014: Verify and recover the Iceberg table bootstrap

## Purpose

Verify the bounded WP-L1-LAKE-003 Iceberg fixture after Flux reconciliation,
prove server-restart persistence and idempotent re-run behavior, and recover or
remove only the deterministic shirokuma_l1.smoke.fixture_v1 resources.

This runbook applies only to the mac-studio-solo local-lite development
profile. It makes no production, GxP, HA, or physical multi-node claim.

## Safety boundary

- Git and Flux remain the desired-state authority. Never use kubectl apply,
  kubectl patch, Helm CLI installation, or a mutable image.
- The only direct Kubernetes mutations below are bounded deletion of a
  controller-owned Pod or Job for restart/retry acceptance. Deployment and Job
  recreation must come from their existing controllers and reviewed Git state.
- Do not print, copy, retain, or place in shell arguments any Secret value,
  OAuth token, S3 key, PostgreSQL credential, or encoded Authorization header.
- Retained evidence contains only revisions, Kubernetes object identities,
  status, row/file counts, snapshot IDs, hashes, and the bootstrap JSON summary.
- Take the existing Polaris PostgreSQL host backup before destructive restart or
  cleanup work. Keep the host backup outside Colima with private permissions.

## Preconditions

1. Confirm the expected Git revision is merged to main and the working tree is
   clean.
2. Confirm mac-studio-solo is active and the Kubernetes context is
   colima-mac-studio-solo.
3. Run the repository gates:

       make verify-iceberg-table-bootstrap
       make verify-security
       make verify

4. Confirm the prerequisite Flux objects are Ready at one revision:

       flux get kustomizations -A
       kubectl -n shirokuma-dev rollout status deployment/polaris --timeout=5m
       kubectl -n shirokuma-storage rollout status statefulset/seaweedfs --timeout=5m

5. Capture a fresh Polaris backup using the repository-owned acceptance path
   and its private host export root. Do not redirect Secret or Pod environment
   output into evidence.

## Normal bootstrap verification

Flux owns shirokuma-iceberg-bootstrap, which depends on
shirokuma-catalog. It reconciles the digest-pinned, non-root Job and waits for
completion.

    flux reconcile kustomization shirokuma-iceberg-bootstrap --with-source
    kubectl -n shirokuma-dev wait --for=condition=complete       job/iceberg-table-bootstrap --timeout=10m
    kubectl -n shirokuma-dev logs job/iceberg-table-bootstrap

The final log line must be one JSON object with all of these properties:

- result is passed;
- catalog, namespace, and table are shirokuma_l1, smoke, and fixture_v1;
- rows is 2 and data_files is 1;
- snapshot_id is a positive integer;
- credential_material_retained is false.

Do not retain earlier library log lines. Retain only the parsed summary and its
SHA-256.

## Restart persistence and idempotent re-run acceptance

Perform this only after the implementation PR is merged, the normal bootstrap
completed, and the backup prerequisite above passed.

1. Record the initial bootstrap summary, Polaris Pod UID, Flux revision, and
   Job UID. Do not record the Pod specification or environment.
2. Delete exactly the current Polaris Pod selected by both
   app.kubernetes.io/name=polaris and
   app.kubernetes.io/component=catalog-server. The Deployment controller
   recreates it from Git-managed desired state.
3. Wait for deployment/polaris to become Available at its observed generation.
4. Delete exactly job/iceberg-table-bootstrap and reconcile
   shirokuma-iceberg-bootstrap. Flux must recreate the reviewed Job; do not
   create or apply a replacement manifest manually.
5. Wait for completion and retain only the new parsed JSON summary.

Acceptance passes only when:

- the Polaris Pod UID changed and the replacement has zero restarts at capture;
- both Flux Kustomizations remain Ready at the merged revision;
- the second summary reports created=false;
- the second snapshot_id equals the initial positive snapshot ID;
- rows remain 2, data files remain 1, and credential retention remains false;
- the Job UID changed and the re-run completed successfully.

Commit the credential-free receipt in a focused evidence PR. Bind it to the
merged implementation revision and keep Issue #62 open until that review
merges.

## Retry a failed Job

1. Read the bounded Job status, Kubernetes events, and redacted logs. Never
   collect Secret objects or Pod environment.
2. Resolve prerequisite readiness, NetworkPolicy, disk pressure, or catalog
   contract drift in a focused Git PR.
3. After that fix is merged and reconciled, delete only the failed
   iceberg-table-bootstrap Job.
4. Reconcile shirokuma-iceberg-bootstrap and wait for the controller-created
   replacement.

A table with an unexpected schema, location, fixture marker, storage endpoint,
region, or path-style setting fails closed. Do not overwrite or adopt it.

## Cleanup and rollback

Cleanup is destructive and requires a reviewed two-stage Git workflow:

1. In a focused cleanup PR, append --cleanup to the reviewed Job arguments.
   Flux force-replaces the immutable Job and runs the repository-owned cleanup
   path. Verify the summary reports cleanup-passed.
2. After cleanup evidence is retained, merge a second PR that removes the
   shirokuma-iceberg-bootstrap Flux resource and its bootstrap manifests.
   Flux prune removes the completed cleanup Job and generated ConfigMap.

Do not revert the cleanup-mode PR before removing the bootstrap desired state;
doing so would recreate the fixture. A plain implementation revert removes
Kubernetes objects but does not claim that catalog or S3 data was purged.

The cleanup path removes the deterministic table reference before deleting its
known Iceberg data, then removes the empty namespace and catalog. If cleanup
fails after catalog detachment, treat remaining s3://shirokuma-lakehouse/l1
objects as orphans and preserve them until a reviewed recovery PR authorizes
deletion.

## Disk-space recovery

- Stop new benchmark or data-load work and inspect host/Colima capacity without
  exposing credentials.
- Preserve the latest Polaris PostgreSQL backup and SeaweedFS export before
  deleting caches or reproducible data.
- Never delete Iceberg metadata independently of the catalog cleanup order.
- Recover space using the established object-storage backup/rebuild runbook,
  then retry through Flux as described above.
- If consistency cannot be proven, keep downstream Issue #63 blocked and
  restore from backup instead of adopting partial data.

## Related records

- docs/design/06_WorkPackages/L1/WP-L1-LAKE-003_Iceberg_table_bootstrap.md
- docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md
- docs/design/08_Runbooks/RB-002_Diagnose_failed_Flux_reconciliation.md
- docs/design/08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo.md
- docs/design/04_Development/049_Supply_Chain_Security.md
- docs/design/07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler.md

