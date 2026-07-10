---
project: Shirokuma
doc_id: "RB-002"
title: "Diagnose failed Argo CD sync"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "runbook"
tags: [shirokuma, runbook]
---

# Diagnose failed Argo CD sync

## Purpose

Argo CD同期失敗時の調査と修正PR手順。

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
shirokuma pr "Apply the recommended remediation for Diagnose failed Argo CD sync."
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
