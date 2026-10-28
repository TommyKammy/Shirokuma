# Local Flux source without GitHub pushes

`tools/local_experiment/local_gitops.py` delivers local manifests to the existing
SeaweedFS bucket `shirokuma-gitops`. Flux reads it through the Bucket
`flux-system/shirokuma-local-source`. No Git commit, push, registry publication,
controller change or GitRepository change is involved.

The existing runtime delivery workflow below requires the dedicated
`colima-mac-studio-solo` context, existing bucket,
running source-controller, and the OpenTofu-provided **read-only** Secret
`flux-system/shirokuma-local-source` with `accesskey` and `secretkey`. Keep the
root Flux Kustomization `flux-system` suspended throughout this workflow. The
helper checks this prerequisite but never changes the root. Python needs PyYAML;
only `snapshot` additionally needs boto3. Use an existing temporary environment
with these dependencies. Runtime commands below are for the parent/operator's
separate controlled cutover, not part of implementation verification.

P2-4 additionally supports offline candidate generation validation:

```sh
rtk proxy python -m tools.local_experiment.local_gitops prepare --database-generation /absolute/path/to/bundle
# Optionally validate explicitly supplied candidate backend authentication:
rtk proxy python -m tools.local_experiment.local_gitops prepare --database-generation /absolute/path/to/bundle --prospective-backend-auth /private/path/backend.json
```

Use a virtual environment with `tools/local_experiment/requirements-generation.txt`.
This mode checks the complete public bundle, profile fingerprint, generated
children, Secret references and delivery digest. It reads neither the current
runtime profile nor its authentication implicitly, and requires no running
cluster. The receipt separates the configuration generation ID from the delivery
digest. The optional authentication must have the same generation, DB set and
identities. Generation mode is limited to `prepare`; it cannot upload, snapshot
or activate a candidate. Applying it belongs to P2-5 and later. See the
[generation report](database-p2-4-config-generation-report-2026-10-20.md).

For a new environment, bootstrap Flux and suspend the root Kustomization first.
Prepare the SeaweedFS operator and dedicated GitOps reader inputs in OpenTofu,
apply the Secrets, and start SeaweedFS from the infrastructure manifests before
using this helper. Create `shirokuma-gitops` if missing with the operator identity; the helper
does not create buckets. Then upload a snapshot, activate the children suspended,
and resume them in dependency order. See the [Polaris adoption procedure](polaris-180-adoption.md)
for the two-stage consumer credential setup, Trino authentication Secret and
mandatory node preload of the pinned Trino/OPA images (`imagePullPolicy: Never`).
The [clean rebuild report](polaris-180-rebuild-and-s3-rotation-2026-10-19.md)
records a successful empty-cluster bootstrap and unchanged snapshot retry.
Docker archives may lose digest-qualified image names when imported into a new
containerd store. Verify exact names in the target runtime; the tested rebuild
pulled all ten images directly by their pinned registry digest.

```sh
rtk proxy python -m tools.local_experiment.local_gitops prepare
rtk proxy python -m tools.local_experiment.local_gitops snapshot
# Wait for Bucket Ready for its current metadata.generation, then:
rtk proxy python -m tools.local_experiment.local_gitops activate --suspend
rtk proxy python -m tools.local_experiment.local_gitops status
```

`prepare` performs local validation and prints only relative file count and
content digest. `snapshot` privately reads only the uploader's existing
`shirokuma-storage/seaweedfs-s3-credentials` Secret (`s3.json`), selects the unique
identity with `Admin` action, and uploads over a temporary **127.0.0.1-only** S3
port-forward. Credentials never enter arguments, files, manifests or console
output. Flux uses its separate read-only credentials. All subprocess calls use
`rtk proxy` and the fixed Kubernetes context.

The fixed upload roots are `deploy/gitops/`, `deploy/catalog/bootstrap/`, and
`deploy/local-experiment/trino/`. Each regular file, including exact relative
Kustomize references, is included. Missing or external references fail validation;
symlinks, hidden/environment/secret paths, Kubernetes Secret documents or
generators, and private key content fail closed. No `var/`, credentials or `.git/`
content is collected. New confidential material must never be added to these
manifest roots; the content checks detect Kubernetes Secrets and private keys,
not every possible application credential format.

Uploads use immutable `generations/<sha256>/` keys. Conditional writes allow
identical retries and reject conflicting existing content. All uploads finish
before the Bucket prefix and the additive storage NetworkPolicy are applied.
A failed upload leaves the prior Bucket configuration intact. The NetworkPolicy
allows source-controller pods in `flux-system` to reach SeaweedFS TCP 8333;
Kubernetes combines this rule with existing policies.

Flux's [Bucket prefix documentation](https://fluxcd.io/flux/components/source/buckets/#prefix)
defines prefix as server-side filtering. Its
[fetchIndexFiles implementation](https://github.com/fluxcd/source-controller/blob/main/internal/controller/bucket_controller.go)
downloads objects using their full keys, retaining the prefix in the artifact.
Accordingly each child path includes `./generations/<sha256>/deploy/...`.

`activate` validates that the current local snapshot digest matches the Bucket
prefix and that the Bucket is Ready for its current generation. It renders the
six existing child definitions directly from the captured local file map, with
the generation value from `polaris-runtime-generation.yaml`, plus the new
`shirokuma-trino` child. Updated dependencies and health checks are retained;
database, catalog bootstrap, catalog and Iceberg bootstrap all receive the
Polaris generation substitution. The existing local SeaweedFS memory limit is
retained as an 8Gi child patch. Trino uses the secure overlay, depends on Iceberg
bootstrap and health-checks `Deployment/trino-local` in `shirokuma-dev`.
Server-side apply takes field ownership for these selected objects using
`--force-conflicts`; the root and GitRepository are never in the applied set.

Use `activate --suspend` to apply all seven children suspended. The parent/operator
then reviews and resumes them in dependency order while keeping the root
suspended. Without `--suspend`, activation explicitly resumes the seven children.
Before changing an already active Bucket generation, suspend its children first:
snapshot switches the source before activate switches their paths. Source upload
and child activation are intentionally separate operations. If repository files
change between the two, activation refuses the mismatch; snapshot the new content
again. Prior immutable objects are retained; cleanup and rollback are operator
work. This helper does not persist snapshots locally, poll reconciliation,
resume the root, or supervise controllers.

Offline verification:

```sh
rtk proxy python -m unittest tests.test_local_gitops -v
```
