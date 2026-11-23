---
project: Shirokuma
doc_id: "GUIDE-TEMPLATE-REDELIVERY"
title: "Template-only redelivery of an applied database generation"
status: accepted
created: 2026-11-22
updated: 2026-11-22
version: "0.1"
area: guide
tags: [shirokuma, database, operator, trino, delivery]
---

# Template-only redelivery (ADR-0039)

This guide re-delivers an **already APPLIED** database generation with the same
`generation_id` when only the audited platform delivery files changed. It never
creates a database, rotates a credential, or runs `local_gitops.py`.

## Preconditions

- The generation is `APPLIED` and has no operation in `AWAITING_APPLY`,
  `PREPARING`, `DRAINING`, `APPLYING` or `UNKNOWN`.
- The bundle diff is limited to
  `deploy/local-experiment/trino/deployment.yaml` and
  `deploy/local-experiment/trino/opa/guard.rego` (see
  `warehouse_operations.TEMPLATE_ONLY_DELIVERY_PATHS`).
- The sealed private inputs already exist: `runtime_profile`,
  `managed_handoff`, `candidate_credentials`, `auth_headers`, `s3_credentials`.
  They are reused unchanged.
- `plan`, `apply` and mutating `reconcile` must run in the **managed owner
  context** (the Workbench/native owner that supplies `SHIROKUMA_MANAGED_FD=3`
  and the matching runtime profile). A standalone shell reaches `leaf_intent`
  and fails `OPERATOR_UNAVAILABLE`, leaving `publish_snapshot` UNKNOWN. Do not
  remove `managed_handoff`/`managed_transport` to bypass this gate; use the
  owner's normal invocation.
- The operator's S3 endpoint is a **127.0.0.1 port-forward**. Read the exact
  port from `s3_operator.json` and start it before planning; the operator uses
  this endpoint directly and does not create a forward:

```sh
# port from the sealed s3_operator.json endpoint_url
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-storage \
  port-forward svc/seaweedfs-s3 60536:8333
```

- `catalog_admin` authenticates its actor from the shared auth file. Export the
  sealed runtime profile's `auth_file`:

```sh
export SHIROKUMA_AUTH_MODE=shared
export SHIROKUMA_AUTH_FILE=/absolute/private/runtime/auth/accounts.json
```

- Use the live control store:

```sh
export SHIROKUMA_STATE_DIR="$PWD/var/shirokuma/p2-6-acceptance/live/state"
```

- Private files must be mode `0600` in a `0700` directory.

## 1. Render the current template with the existing spec

Reuse the applied generation's `spec` (all databases and Secret references are
preserved byte for byte). Only `profile.template_fingerprint` changes.

```sh
PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" \
PYTHONPATH="$PWD/tools/local_experiment" \
python3 tools/local_experiment/catalog_admin.py render \
  --spec /absolute/private/redelivery/spec.json \
  --output /absolute/private/redelivery/bundle \
  --root "$PWD"

PATH="$PWD/tools/local_experiment/dataops/.venv/bin:$PATH" \
PYTHONPATH="$PWD/tools/local_experiment" \
python3 tools/local_experiment/catalog_admin.py check \
  --bundle /absolute/private/redelivery/bundle --root "$PWD"
```

Stop if the diff against the current head bundle is anything other than the two
audited files. The bundle's `deploy/` tree must exactly match the operator
`snapshot`.

## 2. Create and inspect the saved ToFu plan

Generate a new plan with the existing ToFu workdir, state and
`material/terraform.tfvars.json`:

```sh
cd /absolute/private/operator/work/opentofu/dev
rtk proxy env TF_DATA_DIR=/absolute/private/operator/work/opentofu/dev/.terraform \
  tofu plan -var-file=/absolute/private/operator/material/terraform.tfvars.json \
  -out=/absolute/private/operator/redelivery.plan
rtk proxy env TF_DATA_DIR=/absolute/private/operator/work/opentofu/dev/.terraform \
  tofu show -json /absolute/private/operator/redelivery.plan
```

Proceed only if the plan is a no-op for existing resources (no create, delete or
replace). The operator still validates the saved plan itself; state-serial
equality is not relaxed.

## 3. Build the operator config

Copy the sealed `operator.json` to a new `0600` file in a `0700` directory and
change only:

| Field | New value |
|---|---|
| `bundle` | the rendered redelivery bundle |
| `snapshot` | a directory containing exactly `bundle/deploy/**` |
| `tofu_plan` | the new saved plan |
| `state_serial` | the current ToFu state serial (read it, do not assume) |

Keep `candidate_credentials`, `runtime_profile`, `managed_handoff`,
`managed_transport`, `preparation_inputs`, `s3_credentials`, `auth_headers`,
`tofu_state`, `tofu_state_lineage`, `tofu_workdir`, `kube_context` and
`predecessor_id` unchanged. `predecessor_id` may stay: the APPLIED redelivery
path validates the physical handoff but does not transfer a closed reservation.

## 4. Plan, apply and reconcile

```sh
CATALOG="PATH=$PWD/tools/local_experiment/dataops/.venv/bin:$PATH PYTHONPATH=$PWD/tools/local_experiment python3 tools/local_experiment/catalog_admin.py"
export SHIROKUMA_AUTH_MODE=shared
export SHIROKUMA_AUTH_FILE=/absolute/private/runtime/auth/accounts.json

# Run inside the managed owner process, not a bare terminal.
# plan; capture id, revision and delivery_digest from the JSON response
rtk proxy env SHIROKUMA_STATE_DIR="$SHIROKUMA_STATE_DIR" $CATALOG plan \
  --config /absolute/private/operator/redelivery.json \
  --idempotency-key "$(uuidgen | tr A-Z a-z)"

# apply the inspected revision
rtk proxy env SHIROKUMA_STATE_DIR="$SHIROKUMA_STATE_DIR" $CATALOG apply \
  --config /absolute/private/operator/redelivery.json \
  --operation-id <operation-id> --expected-revision <plan-revision>
```

If `publish_snapshot` becomes `UNKNOWN`:

- with no `snapshot_puts` and an unreachable S3 endpoint, start the forward
  above;
- with no `snapshot_puts` and a reachable S3 endpoint, the process lacks the
  managed owner context (`OPERATOR_UNAVAILABLE`); rerun inside the owner.

In either case, restore the pre-plan control-store backup, remove the orphan
operator receipt and any partial `generations/<digest>/` objects, then create a
fresh plan. Do not re-dispatch the step.

`apply` runs the five steps and `verify`. If the operation must be resumed, use
`reconcile` with the same operation id and the next step:

```sh
rtk proxy env SHIROKUMA_STATE_DIR="$SHIROKUMA_STATE_DIR" $CATALOG reconcile \
  --config /absolute/private/operator/redelivery.json \
  --operation-id <operation-id> --step publish_snapshot
# apply_secrets, reconcile_storage, reconcile_polaris, reconcile_trino, verify
```

After a successful `apply`, the receipt is under
`$SHIROKUMA_STATE_DIR/warehouse-operator-receipts/<operation-id>.json`.
`--verify-readonly` only recovers an `UNKNOWN` operation without a predecessor,
so it does not apply after a successful redelivery; use the checks in section 5.

## 5. Verify

```sh
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  rollout status deployment/trino-local --timeout=10m
# operation state and delivery identity (APPLIED)
rtk proxy env SHIROKUMA_STATE_DIR="$SHIROKUMA_STATE_DIR" $CATALOG inspect-operation \
  --operation-id <operation-id> --auth-headers /absolute/private/operator/auth/headers.json
# Trino Deployment image/command
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev \
  get deployment/trino-local \
  -o jsonpath='{.spec.template.spec.containers[0].image}{" "}{.spec.template.spec.containers[0].command}{"\n"}'
# /v1/info: nodeVersion.version=484, starting=false
# authenticated 127.0.0.1 port-forward: SELECT version(), current_timezone();
# SHOW CATALOGS returns the 13 tenant catalogs; one light SELECT; OPA sidecar Ready.
```

Only `trino-local` should roll out. Tenant Polaris Deployments, PostgreSQL and
SeaweedFS must keep their Pod creation timestamps and restart counts.

## Rollback

A rollback is another redelivery: revert the Trino template files in the
repository, render the previous delivery with the same spec, and repeat this
guide. The previous manifest and delivery digest remain in
`generation_plan_history` for review, but ToFu/operator admission always
requires a bundle that re-renders against the current approved template.
A direct Deployment rollback is a temporary operational measure, not a ledger
rollback.
