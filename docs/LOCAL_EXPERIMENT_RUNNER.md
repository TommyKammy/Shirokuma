# Local experiment runner

`tools/local_experiment/local_experiment.py` provides the first bounded deployment path for the
accepted local experimental mode. It uses the existing `colima-mac-studio-solo`
ARM64 k3s cluster and local checkout manifests; it does not bootstrap Flux or
publish Git commits, images, Issues or PRs.

## Commands

Run from the checkout root, with Python 3.10+ and kubectl available:

For the running lakehouse, these commands manage their own loopback-only Trino
port-forward and remove it on exit:

```sh
rtk make local-lakehouse-smoke
rtk make local-dataops-smoke
rtk make local-dataops-recovery-smoke
rtk make local-doctor
```

The DataOps targets use uv to synchronize the checked-in dependency lock into
`tools/local_experiment/dataops/.venv`, without changing the global Python or
dbt configuration. They read the retained fixture and create only disposable
experiment tables. See the [DataOps runbook](../tools/local_experiment/dataops/README.md)
for expected results, failure evidence and cleanup.

`local-doctor` explicitly selects `local-experiment`; it reports workload
readiness and the scoped reconciliation state. SQL correctness comes from the
smoke commands. The default `shirokuma doctor` retains the original diagnostics.

For bounded manifest application:

```sh
rtk make local-experiment-status
rtk make test-local-experiment
rtk make local-experiment-prepare COMPONENT=catalog
rtk make local-experiment-apply RUN=/absolute/path/printed/by/prepare
rtk make local-experiment-status
```

The supported components are deliberately small:

| Component | Existing resources applied | Suspended Flux child |
| --- | --- | --- |
| `dev` | Reconciliation smoke ConfigMap | `shirokuma-dev` |
| `catalog` | Polaris Deployment, Service and NetworkPolicy | `shirokuma-catalog` |

`prepare` reads live state, checks server-side admission with a dry run, and
saves a private evidence directory under ignored `tmp/local-experiment/`. It
performs no persistent cluster writes. Polaris credential generation comes from
the existing child's substitution, so no Secret is read or rotated. The evidence
contains the exact rendered input, its SHA256, Git HEAD, the pre-change resource
snapshots and a state file. Uncommitted edits are included in the rendered hash.
The CLI prints only the directory after successful preparation.

`apply` uses that exact payload and rejects changes to it or to the live resource
specification since preparation. It first records and suspends the root
`flux-system` Kustomization and the selected child. Suspending the root prevents
it from overwriting the child's suspension. Other children keep their existing
state and continue reconciling. A preexisting root suspension remains in effect.
The GitRepository source is untouched. Each object is merged using server dry-run,
then written using an atomic JSON Patch that tests its UID and resource version.
Concurrent edits, recreation or deletion fail instead of being overwritten or
recreated. No prune, deletion or resource creation is supported, and admission
controls remain active. A successful apply means the
API accepted the manifests; use the relevant runtime acceptance checks to prove
application behavior.

Commands are serialized across the repository's worktrees. New runs for the same
component inherit the original suspension flags, allowing repeated local fixes
without handing control back to Flux between edits. Resume using the newest run;
older runs are marked superseded. An active run blocks applying a different
component until its suspension state is restored. The active run path is
stored in the common Git directory as `shirokuma-local-experiment-active.json`.
While reserving a replacement run, that record also retains `previous_run` until
the replacement has durable recovery state. If the process dies in that window,
the next mutation command restores the previous recovery pointer.

## Return to the previous reconciliation state

```sh
rtk make local-experiment-resume RUN=/absolute/path/printed/by/prepare
```

`resume` restores only suspension flags changed by that run, child before root.
It verifies resource UIDs so a recreated reconciler is not changed accidentally.
This may let Flux replace the local changes with its current source revision.
It does not resume a root that was already suspended before the run.

If apply or admission fails, suspension intent and the failure state remain in
`plan.json`; retry `resume` with the same run directory after inspecting the
failure. Do not delete that evidence directory or the active pointer while the
run is active. A partially successful apply is possible. `before.json` records
the exact previous objects for investigation and a reviewed recovery; `resume`
is a reconciliation handoff, not a byte-for-byte resource rollback. If the
previous Flux source differs from the captured objects, inspect those snapshots
and restore the required fields before handing reconciliation back.

If a suspension request is interrupted before confirmation, its `pending` flag
remains set and automatic resume is refused. Inspect the live UID, suspension
state and request outcome before recovering that ambiguous operation; the runner
does not assume it owns an unconfirmed suspension.

The runner does not manage storage, retained data, bootstrap Jobs, local image
imports, Kyverno exceptions or Colima lifecycle. Extend the small component
allowlist with focused tests when a further safe deployment path is needed.

## Verification boundaries

On 2026-09-16, the runner's 18 regression tests passed. A live catalog
prepare/apply/resume cycle restored the recorded suspension flags: the already
suspended root remained suspended and the selected child's previously unset
flag was restored. No suspension remained pending or owned by that run.

`make verify-local-experiment` checks the local runner and local SQL smoke
behavior. These checks and live local acceptance are the feedback loop for this
mode. Local-only helpers live under `tools/local_experiment/`, outside the frozen
publication input inventory in `scripts/`; that inventory and its verifier are
unchanged.

The 2026-09-16 baseline recorded four failures in the strict Trino publication
suite: two historical source/dependency authorizations have
expired, the closed publication build inventory sees the new local build files,
and the publication prerequisite check sees the separate local Trino Deployment.
The resident-image security gate also reports an expired `owner_authorization`,
and the Trivy exception verifier reports an expired exception.
These strict gates remain active and are not local-mode completion criteria.
No historical authorization date or acceptance record is renewed to make local
experiments pass. Rendering checks require the pinned Helm v4.2.2; a newer host
Helm is not an equivalent validation environment.
