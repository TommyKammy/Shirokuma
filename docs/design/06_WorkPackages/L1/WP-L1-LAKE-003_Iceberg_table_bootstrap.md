---
project: Shirokuma
doc_id: "WP-L1-LAKE-003"
title: "WP-L1-LAKE-003 Iceberg table bootstrap"
status: evidence-in-review
created: 2026-07-05
updated: 2026-07-22
version: "1.0"
area: "workpackage"
tags: [shirokuma, workpackage, l1, lakehouse]
---

# WP-L1-LAKE-003 Iceberg table bootstrap

## Summary

TPC-H/CSVをIcebergにロードする

## Context

このWork PackageはLevel L1「最小Lakehouse」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Dependencies

- Depends on: `WP-L1-LAKE-002` and the L0 level gate.
- Polaris catalog readiness and the underlying SeaweedFS profile are required.

## Scope

- Repository-owned deterministic fixtureからsmall TPC-H/CSV datasetを生成する。
- Polaris catalogへIceberg namespace/tableを作成し、SeaweedFSへdata/metadataを保存する。
- create、append、read、metadata persistenceを再現可能なsmokeで検証する。
- Cleanup、再実行、rollbackを自動化する。

## Non-scope

- Trino query engine、dbt model、production data、GxP対応。
- SparkなどL4 componentの先行導入。
- Direct applyまたはmutable imageの利用。

## Deliverables

- Deterministic fixture and Iceberg bootstrap/cleanup commands.
- Catalog/object-store persistence checks and retained evidence.
- Runbook covering retry, rollback, and disk-space recovery.
- `docs/design/08_Runbooks/RB-014_Verify_and_recover_Iceberg_table_bootstrap.md`

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
- `docs/design/02_Architecture/02C_Deployment_Topologies.md`
- `docs/design/03_Requirements/030_Functional_Requirements.md`
- `docs/design/03_Requirements/034_Platform_Requirements.md`
- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/04_Development/046_CI_CD_Test_Strategy.md`
- `docs/design/08_Runbooks/RB-014_Verify_and_recover_Iceberg_table_bootstrap.md`
- `docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md`

## Suggested Labels

`level:L1`, `workstream:lakehouse`, `type:work-package`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L1-LAKE-003: Iceberg table bootstrap.
Read AGENTS.md, related ADRs, and this Work Package.
Create a focused PR that implements only this scope.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## GitHub Tracking

- Epic: [#24](https://github.com/TommyKammy/Shirokuma/issues/24)
- Issue: [#28](https://github.com/TommyKammy/Shirokuma/issues/28)
- PR: [#53](https://github.com/TommyKammy/Shirokuma/pull/53)
- Runtime follow-up Epic: [#60](https://github.com/TommyKammy/Shirokuma/issues/60)
- Runtime follow-up Issue: [#62](https://github.com/TommyKammy/Shirokuma/issues/62)
- Runtime follow-up PR: [#99](https://github.com/TommyKammy/Shirokuma/pull/99)
- Runtime acceptance evidence PR: [#100](https://github.com/TommyKammy/Shirokuma/pull/100)
- GitHub depends on: `#27`
- Runtime follow-up depends on: `#61`
- Runtime repair depends on: `#96`
- Execution order: `2 of 8`
- Queue: PR #99 merged as `5e4b1a43d95d7a6b495487cb25166be3f7f71ee3`.
  Flux reconciled the catalog and Iceberg bootstrap to that revision. Fresh
  Polaris backup/restore and catalog API acceptance passed, and RB-014 restart
  persistence/idempotence evidence is retained for focused review. Issue #62
  remains Open until the evidence PR is reviewed and merged; #63 must not begin.

## Implementation status

- 2026-07-16: #27 is closed, but its merged PR #52 explicitly contains no
  Polaris/PostgreSQL runtime, credentials, Secrets, resident-ledger admission,
  or GitOps manifests. Repository inspection confirms that the Polaris runtime
  and catalog readiness prerequisite is absent.
- Issue #28 remains fail-closed. A focused regression test records the missing
  prerequisite; fixture/bootstrap implementation must not begin until
  WP-L1-LAKE-002 runtime admission, Flux readiness, catalog API smoke, and
  recovery evidence are complete.
- PR #53 retains this reproduction checkpoint without claiming Issue #28
  implementation readiness. Review repair makes the blocker regression
  non-failing, requires a Deployment/StatefulSet backed by a resident-ledger
  image before recognizing Polaris, accepts the exact `polaris` name, and wires
  the focused test into `make verify`; focused verification and `make verify`
  pass.
- 2026-07-18: #61 was reopened after PR #69's conditional close text triggered
  a premature auto-close. The current phase is a runtime-disabled static
  source/candidate contract. Gradle dependency closure, main-only Polaris
  publication, evidence-only atomic Polaris/PostgreSQL admission, runtime/Flux
  readiness, catalog API smoke, and backup/restore remain pending. #62 therefore
  stays fail-closed under ADR-0021 and does not begin while #61 is Open.
- 2026-07-22: PR #95 was merged and #61 was explicitly closed after its required
  review. The first #62 implementation probe created only the deterministic
  Polaris catalog/namespace, but both direct and staged Iceberg table create
  failed because the existing Polaris server had no ambient S3 application
  credential. No direct apply or credential disclosure occurred. Issue #96 now
  owns the prerequisite repair; the #62 worktree is retained but must not be
  pushed until #96 is reviewed, merged, reconciled, and live-verified.
- 2026-07-22: PR #97 merged the credential-reference repair as
  `e6bc687ef936943a1d73d82dd1eb4ea8fec07bbc`. Flux reconciled all six local-lite
  Kustomizations to that revision and created a new Ready Polaris Pod. A fresh
  credential-redacted runtime acceptance passed catalog create/list/read/delete
  and isolated PostgreSQL restore, and an additional temporary Iceberg probe
  passed namespace/table create, list, and load before cleanup. The default AWS
  credential-provider failure is no longer reproducible, so #62 may proceed.
- 2026-07-22: Runtime evidence PR #98 merged as
  `585f6a7e319ef8aace468eba983f753ebd781049` and retained the fresh
  credential-redacted acceptance receipt. The focused Issue #62 branch now adds
  a Flux-ordered, exact-digest Polaris Job that creates the deterministic
  `shirokuma_l1.smoke.fixture_v1` Iceberg v2 table, writes and reads the two-row
  Avro fixture through SeaweedFS, verifies catalog listing and idempotence, and
  provides a bounded cleanup path. The Job is non-root, read-only, has no
  service-account token, uses only external Secret references, and has egress
  limited to Polaris, SeaweedFS, and DNS.
- 2026-07-22: Local pre-publish review compiled and ran the source in the admitted
  image with network disabled and a read-only root filesystem, verified generic
  error redaction with deliberately equal dummy credential values, and passed
  all 121 focused Iceberg tests. A bounded credential-redacted live client probe
  then passed create, two-row write/read, list, a snapshot-stable idempotent
  re-run with `created=false`, and cleanup. RB-014 records Flux-only
  retry/rollback, disk-space recovery, and the post-merge server-restart evidence
  procedure. Issue #62 remains Open until that live receipt is reviewed.
- 2026-07-22: Adding the downstream Flux Kustomization changes the accepted
  local-lite root manifest. The implementation PR therefore deliberately moves
  `security/polaris-runtime-activation.json` back to
  `runtime_acceptance_pending`, removes the now-stale PR #98 receipt, and updates
  the reviewed root hash. After merge and Flux reconciliation, one focused
  evidence PR must recapture both the Polaris runtime acceptance and RB-014
  Iceberg restart/idempotence receipt before #62 closes or #63 begins.
- 2026-07-22: Draft PR #99 now carries the reviewed implementation commit
  `b0fb3d7`. Local focused and full repository verification pass. Issue #62
  remains Open while PR review/CI and the required post-merge evidence review
  are pending.
- 2026-07-22: PR #99 review repair quantified the deterministic storage impact.
  A credential-redacted bounded probe measured six `l1/` objects and 16,547
  logical bytes, unchanged by the idempotent re-run; cleanup restored zero
  objects. RB-014 now sets an eight-object/1 MiB acceptance guard and 128 MiB
  host/Colima minimum operational headroom before reconciliation.
- 2026-07-22: PR #99 was squash-merged as
  `5e4b1a43d95d7a6b495487cb25166be3f7f71ee3`, and Flux reconciled both
  `shirokuma-catalog` and `shirokuma-iceberg-bootstrap` Ready at that exact
  revision. Fresh Polaris runtime acceptance passed catalog API smoke and an
  isolated PostgreSQL backup/restore with no retained credential material.
  After deleting only the controller-owned Polaris Pod, its UID changed with
  zero restarts; Flux then recreated the bootstrap Job with a new UID. The
  second Job reported `created=false`, retained snapshot
  `7141845066324476177`, and read the same two rows from one data file. The
  post-rerun `l1/` inventory was six objects / 16,549 logical bytes, within the
  eight-object / 1 MiB guard. Host and Colima capacity guards also passed. The
  credential-free Polaris and Iceberg receipts are now awaiting focused PR
  review, so Issue #62 remains Open and #63 remains blocked.

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。
