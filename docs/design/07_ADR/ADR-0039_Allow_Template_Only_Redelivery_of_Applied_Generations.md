---
project: Shirokuma
doc_id: "ADR-0039"
title: "Allow Template-Only Redelivery of Applied Generations"
status: accepted
created: 2026-11-22
updated: 2026-11-22
version: "0.1"
area: adr
tags: [shirokuma, adr, database, generation, delivery, trino]
---

# ADR-0039: Allow Template-Only Redelivery of Applied Generations

## Context

Trino 484 is a platform template update: the official arm64 image changes and
the `run-trino` wrapper is replaced by `launcher`. The live generation
`p52-7359ee22b1bc653c91e93436` is a **database generation bundle** with 13 tenant
databases, generated Polaris instances, bootstrap Jobs and rotated Secret
references. It is already `APPLIED`.

`warehouse_operations._create_plan` stores exactly one manifest per generation
and raised `GENERATION_CONFLICT` for any different manifest or delivery digest.
Minting a new generation through `database_creation_preparation` is not possible
for an `APPLIED` creation (the lifecycle rejects a new phase plan), and a new
database creation would generate new credentials. The repository-only
`local_gitops.py` snapshot was rejected in preflight because it would prune the
generated tenant resources and restore stale Secret references.

The actual 484 delta is two delivery files: the Trino Deployment (image and
command) and the generated OPA guard policy. All tenant databases, bindings,
schemas, grants, Secret references and public identity files are unchanged.

## Decision

Permit a **template-only redelivery** of an `APPLIED` generation under the same
`generation_id`, with a new `delivery_digest`, only when all conditions hold:

1. **Identity is unchanged.** `spec.databases` (catalog, binding, schemas,
   secret refs), `spec.shared_secret_refs`, `spec.grant_revisions`,
   `spec.warehouse_id`, `spec.version`, `child_files`, `tofu` and every public
   identity file digest are identical. Only `spec.profile.template_fingerprint`
   may differ.
2. **Delivery scope is unchanged.** The only differing `file_digests` are:
   - `deploy/local-experiment/trino/deployment.yaml`
   - `deploy/local-experiment/trino/opa/guard.rego`

   This allowlist is fixed in
   `warehouse_operations.TEMPLATE_ONLY_DELIVERY_PATHS`. Extending it requires a
   new ADR review and test update.
3. **Generation state is settled.** `warehouse_config_generations.state` is
   `APPLIED` and no operation for the generation is in
   `AWAITING_APPLY`, `PREPARING`, `DRAINING`, `APPLYING` or `UNKNOWN`.
4. **No new credentials.** The redelivery reuses the sealed private input,
   `runtime_profile`, `managed_handoff` and `candidate_credentials`. It never
   renders or applies a new private input.
5. **Existing safety gates are unchanged.** A new ToFu plan is still created
   and inspected; `check_state_before_apply` still requires the exact state
   serial, and the stepwise `plan → apply → reconcile → verify` flow is kept.

`validate_prepared_handoff` may ignore `profile.template_fingerprint` only when
the caller has already validated the redelivery conditions. All other physical
fields (warehouse, database bindings, schemas) remain strict.

## Storage and audit

- `generation_plans` remains the latest **admitted** delivery and the head for
  existing readers. `operation_requests.state` records whether that delivery is
  still `AWAITING_APPLY` or has reached `APPLIED`; a second redelivery is
  rejected while any operation for the generation is active.
- `generation_plan_history` is append-only and records every delivery
  (`generation_id`, `delivery_digest`, `manifest`, `secret_observations`,
  `recorded_at`). The previous head is copied there on the first redelivery and
  every new delivery is appended; rows are never updated or deleted.
- `operation_requests.redelivery` records whether an operation was admitted as a
  redelivery.
- Operation views (`_view`, receipt reconciliation, phase-plan association) read
  the manifest from `operation_requests.payload`, so an older operation keeps
  its own delivery after a later redelivery.

## Consequences

- A Trino image or platform delivery update no longer requires a new database
  generation, a new database, or credential rotation.
- Only the Trino workload rolls out; tenant Polaris, PostgreSQL, SeaweedFS and
  bootstrap Jobs keep their desired content and are not recreated.
- The generation is no longer immutable in its **delivery** dimension, although
  its database and Secret identity remains immutable. Recovery of a frozen
  operation that resolves the generation head observes the latest admitted
  delivery, including one still `AWAITING_APPLY`; this limitation is accepted
  because redelivery requires no active operation at admission and the tenant
  configuration is identical across deliveries.
- Rollback is another redelivery: revert the template files in the repository,
  render the previous delivery, and admit it through the same path. The old
  manifest remains available in `generation_plan_history` for review, but it is
  not applied from history because `_bundle` requires the current approved
  template render.

## Rejected alternatives

- `local_gitops.py snapshot/activate`: prunes generated tenant resources.
- A new generation through the creation workflow: rejected after `APPLIED` and
  would mint new credentials.
- Direct control-store or bucket edits: bypasses the operator gates and audit.

## References

- [Trino 484 release](https://github.com/trinodb/trino/releases/tag/484)
- [Redelivery guide](../guides/database-template-redelivery-2026-11-22.md)
- [ADR-0036](ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md)
