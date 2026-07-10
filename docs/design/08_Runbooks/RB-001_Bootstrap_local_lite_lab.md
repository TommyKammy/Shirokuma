---
project: Shirokuma
doc_id: "RB-001"
title: "Bootstrap local-lite lab"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "runbook"
tags: [shirokuma, runbook]
---

# Bootstrap local-lite lab

## Purpose

`shirokuma init --profile local-lite`で最小Labを起動する。

## Preconditions

- GitHub repository access.
- Local kubeconfig or lab access.
- `shirokuma` CLI installed.
- No production data.

## Procedure

1. Confirm current status.

```bash
shirokuma status
shirokuma doctor --output markdown
```

2. Collect context.

```bash
kubectl get pods -A
kubectl get events -A --sort-by=.lastTimestamp | tail -50
```

3. Ask Agent for diagnosis if applicable.

```bash
shirokuma ask "Diagnose this issue and propose a PR-safe remediation."
```

4. Apply only through PR unless this is a local disposable lab.

```bash
shirokuma pr "Apply the recommended remediation for Bootstrap local-lite lab."
```

## Verification

- CI passes.
- Argo CD sync is healthy.
- `shirokuma doctor` reports healthy status.
- Pawprint is recorded.

## Rollback

- Revert PR.
- Argo CD sync previous commit.
- If data/catalog affected, restore from latest backup.

## Notes

このRunbookはGxP/本番SLAを対象にしません。Lab実験用です。
