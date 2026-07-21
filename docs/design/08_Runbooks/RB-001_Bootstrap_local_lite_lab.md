---
project: Shirokuma
doc_id: "RB-001"
title: "Bootstrap local-lite lab"
status: draft
created: 2026-07-05
updated: 2026-07-21
version: "1.2.1"
area: "runbook"
tags: [shirokuma, runbook]
---

# Bootstrap local-lite lab

## Purpose

Start and recover the accepted `solo-lite` Colima built-in k3s baseline on the
single `mac-studio-solo` host.

## Preconditions

- Colima, the Docker CLI, kubectl, Helm, OpenTofu, and the repository-pinned Flux CLI are installed.
- The host can reserve 16 CPU, 96GB memory, and 400GB disk for the profile while
  retaining the required 192GB host memory reserve.
- Host SSD free space has been checked. Any non-reproducible VM data has been
  exported outside Colima before reset or recovery.

## Procedure

1. Start the accepted `solo-lite` profile. This pins the
   `mac-studio-solo` VZ/aarch64 VM and Colima built-in k3s configuration.

```bash
make colima-start
```

2. Re-run the non-mutating baseline check at any time. The wrapper uses the
   explicit `colima-mac-studio-solo` Kubernetes context and does not rely on the
   current kubectl context.

```bash
make colima-status
```

3. Capture the operator-facing evidence required by the Work Package.

```bash
kubectl --context colima-mac-studio-solo cluster-info
kubectl --context colima-mac-studio-solo get nodes -o wide
helm version
helm list --kube-context colima-mac-studio-solo --all-namespaces
```

The accepted result is a running `aarch64` VM, a Ready `arm64` node from the
expected context, and successful Helm access to the API. Missing or malformed
architecture/readiness output is a failed baseline.

## GitOps bootstrap

OpenTofu 1.12.3, Helm 4.2.3, kubectl, and the repository-pinned Flux CLI are
required. The repository pins the OpenTofu providers, `fluxcd/flux2`
distribution, controller image digests, and workload image digests. Before any
cluster mutation, the bootstrap target checks that every controller image is
ARM64-capable and admitted by the fail-closed resident-image gate. The
`mac-studio-solo/local-lab` path may use only the exact, unexpired High findings
approved by ADR-0019; Critical, new High, stale exceptions, evidence mismatch,
and production use remain blocked. Candidate manifests remain under
`bootstrap/` rather than `deploy/` until admitted.

The Make targets are non-interactive for supervised execution. OpenTofu manages
cluster prerequisites; `flux bootstrap github` installs the four standard
controllers into `flux-system` and creates repository sync resources without
changing the operator's current Kubernetes context.

Before `make gitops-bootstrap`, generate all six required S3 and Polaris values in a
dedicated trusted owner-only shell. Do not enable `set -x`, run `env` or
`export -p`, echo the variables, write them to a dotenv file, or paste values
into a terminal, log, Issue, or PR. The command substitutions below deliver
fresh values directly into the shell variables without displaying or recording
the generated values; the shell history contains only the generation commands.

```bash
set +x
umask 077
export TF_VAR_seaweedfs_s3_operator_access_key="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export TF_VAR_seaweedfs_s3_operator_secret_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export TF_VAR_seaweedfs_s3_application_access_key="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export TF_VAR_seaweedfs_s3_application_secret_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export TF_VAR_polaris_postgresql_password="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export TF_VAR_polaris_root_client_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

`make gitops-bootstrap` has a fail-closed preflight: if any variable is unset or
empty, it stops before OpenTofu mutates the cluster. It also rejects equal
operator/application access keys. Do not weaken or bypass this preflight.
OpenTofu writes the Polaris values only to the owner-controlled state and the
`shirokuma-dev` Secrets `polaris-postgresql-credentials` and
`polaris-root-credentials`; do not inspect or print either Secret value.
The reviewed `flux-system/ConfigMap/polaris-runtime-generation` is the only
credential-generation source. OpenTofu reads the same repository file used by
Flux, and all three catalog Pod templates consume its substituted token. Do not
try to rotate either credential by changing only a `TF_VAR`: OpenTofu ignores
in-place Secret data changes to prevent a half-rotated database. Credential
replacement requires a separate reviewed catalog rebuild that increments the
generation token, recreates PostgreSQL and the completed Admin Job, and proves
backup/restore and API acceptance.

The same target performs a read-only lookup for the legacy
`shirokuma-dev/PersistentVolumeClaim/seaweedfs-data-seaweedfs-0` before
OpenTofu apply. A lookup error fails closed. If that PVC exists, bootstrap
stops: a PVC cannot be moved in place between Kubernetes namespaces, and
retaining or relabeling it does not migrate its data to `shirokuma-storage`.
Complete the verified export in RB-013, reset the whole `mac-studio-solo`
profile, bootstrap the accepted namespace layout, and restore into the empty
managed bucket. Do not delete the legacy PVC or bypass this guard as a shortcut.

```bash
make tofu-fmt
make tofu-validate
flux check --pre
make gitops-bootstrap
```

### Bootstrap branch staging and `main` handoff

`FLUX_BOOTSTRAP_BRANCH=flux/bootstrap-local-lite` is a staging branch used only
to let the pinned Flux CLI publish and install its generated manifests. It is
not the steady-state reconciliation branch. Complete this handoff after initial
bootstrap or any recovery bootstrap:

1. Confirm the staging branch contains the CLI-generated
   `flux-system/gotk-components.yaml` and `flux-system/gotk-sync.yaml` for Flux
   `v2.9.2`. Do not edit the generated controller resources on the staging
   branch.
2. From a focused branch based on current `origin/main`, bring in exactly those
   two generated files. Change only the `GitRepository.spec.ref.branch` in
   `gotk-sync.yaml` to `main`; retain the repository-owned Kustomize patches
   that replace all four controller tags with their admitted exact digests.
3. Run `make verify-gitops-bootstrap`, `make verify-security`, and
   `make verify-gitops-image-admission`. The gate must reject a missing, extra,
   duplicate, sidecar, init-container, repository, tag, version, patch, digest,
   ledger, or sync-field mismatch. Merge the normal reviewed PR to `main` only
   when all gates pass.
4. Create a bridge branch from `flux/bootstrap-local-lite`, merge the accepted
   `main`, and require the following comparison to be empty before merging a PR
   whose base is `flux/bootstrap-local-lite`:

```bash
git diff --exit-code origin/main -- deploy/gitops/clusters/local-lite
```

5. Keep the staging branch until Flux reports the merged `main` revision.
   Confirm that `flux-system/GitRepository/flux-system` uses branch `main`, and
   that the root, dev, and object-storage Kustomizations are `Ready=True` at
   that revision. Do not delete the staging branch or call the handoff complete
   while the live Source still reports the staging branch or an older revision.

`make gitops-reconcile` always reconciles the root and dev resources. It first
performs a read-only lookup for the optional
`flux-system/Kustomization/shirokuma-object-storage`: absence is an expected
successful skip (including after its GitOps teardown), while an API lookup
failure stops the target. It never turns an authorization, transport, or other
lookup error into an absence.

After bootstrap completes, remove the credentials from the shell environment
without printing them:

```bash
unset TF_VAR_seaweedfs_s3_operator_access_key
unset TF_VAR_seaweedfs_s3_operator_secret_key
unset TF_VAR_seaweedfs_s3_application_access_key
unset TF_VAR_seaweedfs_s3_application_secret_key
unset TF_VAR_polaris_postgresql_password
unset TF_VAR_polaris_root_client_secret
```

Confirm controller readiness, Source/Kustomization state, and the
Git-reconciled smoke object with:

```bash
flux check
make gitops-status
flux get sources git -A
flux get kustomizations -A
kubectl --context colima-mac-studio-solo \
  -n shirokuma-dev get configmap repository-reconciliation-smoke
```

The accepted result is four Available controller Deployments, a `GitRepository`
and root/dev `Kustomization` at `Ready=True`, and the smoke ConfigMap present.
Merge a bounded smoke change through the normal PR path and observe Flux
reconcile the approved revision. Direct `kubectl apply` is not valid evidence.

This is the GitOps control-plane gate, not an application data-plane gate. Once
the Issue #26 object-store revision is present, follow
[[08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo]] to verify
`shirokuma-storage/NetworkPolicy/seaweedfs-s3-ingress`, the
`shirokuma-storage` server Secret, the `shirokuma-dev` application Secret, HTTP
`/healthz` and `/readyz`, and authenticated CRUD. A running Pod at `Ready=True`
can outlive a deleted required Secret and does not by itself prove either
Secret existence or S3 usability. Application clients must use the
bucket-scoped `shirokuma-dev/Secret/seaweedfs-s3-application-credentials`, call
`http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333`, and opt in with
`shirokuma.dev/object-storage-client: "true"`; they cannot mount the
cross-namespace `shirokuma-storage/Secret/seaweedfs-s3-credentials` and never
receive the operator `Admin` identity.

### PostgreSQL catalog metadata capacity and recovery

The Polaris StatefulSet reserves an additional retained `5Gi` PVC named
`shirokuma-dev/PersistentVolumeClaim/data-polaris-postgresql-0`. Together with
the SeaweedFS `20Gi` claim, the reviewed data services reserve `25Gi` inside the
400GB Colima disk, excluding filesystem, image, database WAL, and external
backup overhead. Before bootstrap, teardown, or reset, record `df -h` for the
host and Colima VM and retain enough owner-controlled storage outside Colima for
the database export plus its checksum.

Before any destructive operation, export the live database without displaying
credentials. `pg_dump` creates a consistent archive while the API remains
online; the restore procedure below then quiesces catalog writers before it
changes the database. Store the archive outside Colima with owner-only
permissions and validate it with the exact admitted PostgreSQL image:

```bash
set +x
umask 077
backup_dir="$HOME/Shirokuma-backups/polaris/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  exec statefulset/polaris-postgresql -c postgresql -- \
  sh -ceu 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump \
    --host=127.0.0.1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
    --format=custom --no-owner --no-acl' \
  > "$backup_dir/polaris.dump"
test -s "$backup_dir/polaris.dump"
shasum -a 256 "$backup_dir/polaris.dump" \
  > "$backup_dir/polaris.dump.sha256"
docker run --rm --entrypoint pg_restore \
  -v "$backup_dir:/backup:ro" \
  cgr.dev/chainguard/postgres@sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8 \
  --list /backup/polaris.dump > "$backup_dir/polaris.dump.list"
test -s "$backup_dir/polaris.dump.list"
```

An in-place restore is allowed only after the archive checksum and list pass,
the Polaris server is suspended, and the existing PostgreSQL Pod is Ready. Keep
the Flux server Kustomization suspended until `pg_restore` succeeds:

```bash
shasum -a 256 -c "$backup_dir/polaris.dump.sha256"
flux suspend kustomization shirokuma-catalog -n flux-system \
  --context=colima-mac-studio-solo
kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  scale deployment/polaris --replicas=0
pod="$(kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  get pod -l app.kubernetes.io/name=polaris-postgresql \
  -o jsonpath='{.items[0].metadata.name}')"
kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  exec -i "$pod" -c postgresql -- sh -ceu '
    export PGPASSWORD="$POSTGRES_PASSWORD"
    dropdb --force --host=127.0.0.1 --username="$POSTGRES_USER" "$POSTGRES_DB"
    createdb --host=127.0.0.1 --username="$POSTGRES_USER" "$POSTGRES_DB"
    pg_restore --exit-on-error --single-transaction --no-owner --no-acl \
      --host=127.0.0.1 --username="$POSTGRES_USER" \
      --dbname="$POSTGRES_DB"
  ' < "$backup_dir/polaris.dump"
flux resume kustomization shirokuma-catalog -n flux-system \
  --context=colima-mac-studio-solo
flux reconcile kustomization shirokuma-catalog -n flux-system \
  --context=colima-mac-studio-solo
```

Capture Ready and catalog create/list/read evidence after restore. A lost PVC or
whole-profile reset additionally requires the original root credential and a
reviewed empty-database bootstrap/restore sequence; that path remains part of
the pending live acceptance gate. Do not delete the PostgreSQL PVC, change the
credential generation, or reset Colima until that destructive recovery path has
been exercised and recorded.

Teardown uses the same OpenTofu state and removes the Flux installation and
both `shirokuma-storage` and `shirokuma-dev` namespaces:

OpenTofu evaluates all six required S3 and Polaris variables during destroy
even though it will delete, rather than update, the Secrets. In the same trusted owner-only
shell, re-export six fresh valid values with the non-logging generation block
above. They are destroy evaluation inputs and do not need to recover the
deployed credentials. `make gitops-teardown` fails before any cluster mutation
when a variable is missing or invalid, and completes a non-mutating destroy plan
before it uninstalls Flux.

```bash
make gitops-teardown
unset TF_VAR_seaweedfs_s3_operator_access_key
unset TF_VAR_seaweedfs_s3_operator_secret_key
unset TF_VAR_seaweedfs_s3_application_access_key
unset TF_VAR_seaweedfs_s3_application_secret_key
unset TF_VAR_polaris_postgresql_password
unset TF_VAR_polaris_root_client_secret
```

This command is destructive once the Issue #26 object-store revision is active.
It uninstalls Flux and then destroys both OpenTofu-managed namespaces;
`shirokuma-storage` deletion also removes
`PersistentVolumeClaim/seaweedfs-data-seaweedfs-0` and can delete its backing
volume and all object data. Namespace deletion also removes the retained `5Gi`
PostgreSQL PVC and all catalog metadata. Before running it, quiesce writers and
complete the verified export and paired inventory procedure in
[[08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo]]. The SeaweedFS profile
requests a retained `20Gi` PVC and Polaris requests `5Gi`; actual object and
metadata growth consumes host SSD inside the 400GB Colima disk and is not
negligible operationally.

## Reset and recovery

Reset deletes the entire profile disk. First export non-reproducible object
data, catalog metadata, and required evidence outside the VM; confirm host free
space can hold both the export and replacement 400GB profile. Then run:

For SeaweedFS inventory, checksum, restore, persistence, and Issue #26 closure
evidence, follow [[08_Runbooks/RB-013_Nuke_and_Rebuild_mac_studio_solo]] before
executing the reset command. For Polaris, complete the export above and retain
the original root credential; whole-profile metadata restore remains blocked
until the pending live acceptance procedure has been exercised.

```bash
scripts/colima_baseline.sh reset --confirm-data-loss
```

The command stops and deletes only `mac-studio-solo`, recreates it with the
same pinned baseline, and runs the full status gate. If it fails, retain the
command output and exports, stop the VM, and do not treat the lab as ready.

## Verification

- `make verify`
- `kubectl --context colima-mac-studio-solo cluster-info`
- `kubectl --context colima-mac-studio-solo get nodes -o wide`
- `helm version`
- `helm list --kube-context colima-mac-studio-solo --all-namespaces`
- `make tofu-fmt`
- `make tofu-validate`
- `kubectl --context colima-mac-studio-solo -n flux-system get deployments`
- `flux check`
- `flux get sources git -A`
- `flux get kustomizations -A`
- `make gitops-status`
- RB-013 object-storage NetworkPolicy, Secret-existence, HTTP probe, and
  authenticated CRUD gate when Issue #26 resources are present

## Rollback

- Stop the profile with `colima stop --profile mac-studio-solo`.
- Remove GitOps resources with `make gitops-teardown` before stopping the
  profile when the OpenTofu state is available.
- Revert the focused PR if repository checks regress.
- Restore exported data only after the rebuilt baseline passes its status gate.
- A Colima disk cannot be safely shrunk in place; export, delete, and rebuild
  when returning to a smaller profile.

## Notes

このRunbookはGxP/本番SLAを対象にしません。Lab実験用です。
