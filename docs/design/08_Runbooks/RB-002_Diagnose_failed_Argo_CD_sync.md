---
project: Shirokuma
doc_id: "RB-002"
title: "Diagnose failed Argo CD sync"
status: accepted
created: 2026-07-05
updated: 2026-07-12
version: "0.3"
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

1. Create a private evidence directory. Do not commit credentials, kubeconfig,
   environment dumps, Secret resources, or raw agent prompts.

```bash
evidence_dir="$(mktemp -d)"
shirokuma doctor --output json > "${evidence_dir}/doctor.json"
```

2. Collect bounded Application and warning-event summaries. The selectors avoid
   Secret bodies and the limits prevent unbounded PR artifacts.

```bash
kubectl --context colima-mac-studio-solo -n argocd get applications \
  -o json | jq '{schema_version:"1",items:[.items[:100][] | {
    name:.metadata.name,
    namespace:.metadata.namespace,
    sync:(.status.sync.status // "Unknown"),
    health:(.status.health.status // "Unknown"),
    conditions:[(.status.conditions // [])[:10][] | {type,lastTransitionTime}]
  }]}' > "${evidence_dir}/applications-summary.json"
kubectl --context colima-mac-studio-solo get events -A \
  --field-selector type=Warning --sort-by=.lastTimestamp \
  -o json | jq '{schema_version:"1",items:[.items[-100:][] | {
    type,reason,count,firstTimestamp,lastTimestamp,
    involvedObject:{kind:.involvedObject.kind,namespace:.involvedObject.namespace,name:.involvedObject.name}
  }]}' > "${evidence_dir}/events-warning.json"
```

3. If a workload is implicated, retain no more than 200 lines. Review the file
   before attaching it to a PR.

```bash
kubectl --context colima-mac-studio-solo -n argocd logs deployment/argocd-repo-server \
  --tail=200 > "${evidence_dir}/repo-server-tail.log"
```

4. Record a Pawprint matching `observability/pawprint.schema.json`. Reference
   the evidence filenames; never embed unrestricted logs or prompts in it.

5. Diagnose and remediate through an issue-linked PR. Do not directly apply a
   proposed fix to the cluster.

```bash
gh pr create --draft --title "fix: remediate failed Argo CD sync"
```

## Verification

- CI passes.
- Argo CD sync is healthy.
- `shirokuma doctor` reports healthy status.
- Pawprint is recorded.
- Evidence contains no Secret values, credentials, kubeconfig, or raw prompts.
- Each evidence file is at most 1 MiB; retain with the PR for 30 days.

## Rollback

- Revert PR.
- Argo CD sync previous commit.
- If data/catalog affected, restore from latest backup.

## Notes

このRunbookはGxP/本番SLAを対象にしません。Lab実験用です。
