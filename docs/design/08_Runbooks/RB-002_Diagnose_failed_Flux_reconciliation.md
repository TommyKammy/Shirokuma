---
project: Shirokuma
doc_id: "RB-002"
title: "Diagnose failed Flux reconciliation"
status: accepted
created: 2026-07-05
updated: 2026-07-12
version: "0.4"
area: "runbook"
tags: [shirokuma, runbook, gitops, flux]
---

# Diagnose failed Flux reconciliation

## Purpose

Flux GitRepository、Kustomization、HelmReleaseまたはcontrollerのreconciliation失敗をbounded evidenceで調査し、GitHub PR経由で修復する。

## Preconditions

- `kubectl`とrepository-pinned `flux` CLIを利用できる。
- `colima-mac-studio-solo` contextを明示している。
- repositoryとGitHub Issueを特定している。
- Secret、token、Deploy Keyを出力しない。
- live clusterを変更する前にGit上のdesired stateを確認する。

## Procedure

All collection shells must fail closed:

```bash
set -o errexit -o nounset -o pipefail
```

1. 調査用directoryを作成し、`shirokuma doctor`のbounded reportを取得する。

```bash
evidence_dir="artifacts/flux-reconciliation-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${evidence_dir}"
shirokuma doctor --output json > "${evidence_dir}/doctor.json"
```

2. controller、Source、Kustomization、HelmReleaseの状態をSecretなしで取得する。

```bash
kubectl --context colima-mac-studio-solo -n flux-system get deployments \
  -o wide > "${evidence_dir}/controllers.txt"
flux get sources all -A --status-selector ready=false \
  | python3 scripts/bound_evidence.py --max-bytes 1048576 \
  > "${evidence_dir}/sources-not-ready.txt"
flux get kustomizations -A --status-selector ready=false \
  | python3 scripts/bound_evidence.py --max-bytes 1048576 \
  > "${evidence_dir}/kustomizations-not-ready.txt"
flux get helmreleases -A --status-selector ready=false \
  | python3 scripts/bound_evidence.py --max-bytes 1048576 \
  > "${evidence_dir}/helmreleases-not-ready.txt"
```

空のnot-ready一覧は正常である。CLIの終了状態とファイルの有無を確認し、失敗を正常扱いしない。

3. 対象resourceのconditions、observed revision、eventsを取得する。YAML全体やSecret参照先の値は保存しない。

```bash
kubectl --context colima-mac-studio-solo -n flux-system \
  describe gitrepository flux-system \
  > "${evidence_dir}/gitrepository-describe.txt"
kubectl --context colima-mac-studio-solo -n flux-system \
  describe kustomization flux-system \
  > "${evidence_dir}/kustomization-describe.txt"
kubectl --context colima-mac-studio-solo get events -A \
  --field-selector type=Warning \
  > "${evidence_dir}/warning-events.txt"
```

4. 問題の種類に応じてbounded controller logを取得する。

- Git/OCI/Helm source取得: `source-controller`
- manifest build/apply/health/prune: `kustomize-controller`
- HelmRelease lifecycle: `helm-controller`
- webhook/alert: `notification-controller`

```bash
logs_tmp="${evidence_dir}/.controller-tail.log.tmp"
flux logs --all-namespaces --level=error --since=30m \
  | python3 scripts/bound_evidence.py --max-bytes 1048576 \
  > "${logs_tmp}"
test -s "${logs_tmp}"
mv "${logs_tmp}" "${evidence_dir}/controller-tail.log"
```

logにcredentialまたはSecret値が含まれていないことを確認する。必要な場合はredactし、raw logをcommitしない。各artifactは最大1 MiBとし、既定保持期間は30 daysとする。

5. Git revisionとrepository manifestを比較し、最小の修正PRを作成する。

- source authentication、URL、ref、path
- Kustomize build error、CRD ordering、`dependsOn`
- health check、timeout、prune、RBAC
- Helm chart/version/values/image digest
- policy admission denial

direct `kubectl apply`、`kubectl edit`、cluster-only patchは修復として扱わない。

```bash
gh pr create --draft --title "fix: remediate failed Flux reconciliation"
```

6. PRがmergeされた後、通常intervalを待つか、bounded verificationとして対象resourceを明示して再reconcileする。

```bash
flux reconcile source git flux-system -n flux-system
flux reconcile kustomization flux-system -n flux-system --with-source
```

## Verification

- Flux standard controller DeploymentsがAvailableである。
- 対象Sourceが承認済みrevisionで`Ready=True`を報告する。
- 対象Kustomization/HelmReleaseが同じrevisionで`Ready=True`を報告する。
- `flux get all -A`に予期しないNot Ready resourceがない。
- `shirokuma doctor`が`healthy`を報告する。
- expected workloadとGit-reconciled smoke objectが存在する。
- Issue/PR/PawprintのevidenceにSecret値と無制限logがない。

## Rollback

- 問題を導入したPRをGit revertする新しいPRを作成する。
- merge後、対象Source/Kustomizationを再reconcileし、以前の承認済みrevisionへ収束したことを確認する。
- 反復失敗または破壊的影響がある場合だけ対象Kustomization/HelmReleaseを`flux suspend`し、Issueへ理由とresume条件を記録する。
- cluster上のresourceを手動で以前の状態へ書き戻さない。

## Notes

このRunbookはGxP/本番SLAを対象にしません。Lab実験用です。Argo CD同期失敗向けの旧手順は[[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler|ADR-0018]]によりsupersededされた。
