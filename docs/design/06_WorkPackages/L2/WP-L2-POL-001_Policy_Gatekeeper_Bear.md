---
project: Shirokuma
doc_id: "WP-L2-POL-001"
title: "WP-L2-POL-001 Policy Gatekeeper Bear"
status: local-partial
created: 2026-07-05
updated: 2026-09-27
version: "0.9"
area: "workpackage"
tags: [shirokuma, workpackage, l2, policy]
---

# WP-L2-POL-001 Policy Gatekeeper Bear

## 2026-09-27: pin workflow evidence writes to owned output fd

policy focused101件PASS、全回帰121 suites/3806実行＝3798PASS＋8skip、90.172秒、log SHA256 `891e4be8b0312aa866a9ddefd4e029fdabc62ec4801f4518967f26cecbafe271`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-anchored-writes-checks/20260927/`。

前増分は出力directoryの**作成**までfdに固定したが、その後の`candidate.json`/他receipt/OTel span append/再読込が再びpathを辿った。隔離tempで作成直後に親を模擬保持treeへのsymlinkへ差し替えると、旧workflowは`candidate.json`を模擬保持先へ書く負例を再現。今回、workflowの作成後から完了まで開いた出力fdを保持し、JSONのatomic replace、span append/chmod、保存span再読込を同fd相対へ変更。append対象のsymlink・複数linkを拒否。差し替え後の完全workflowでは模擬保持treeは空、元dirにだけcandidate/result/spansが残り、終了時のpath identity不一致を`OUTPUT_PARENT_INVALID`で拒否。元dirを自動削除しない。保存statusは従来どおり現pathのread-only検証であり、この修正は外部同UID actorによる元dir内の内容改変や、保存物の独立認証・live admissionを証明しない。Kubernetes/モデル/実保持run/backup変更なし、Colima起動なし。WP local-partial。

## 2026-09-27: descriptor-bound output creation

policy focused98件PASS、全回帰121 suites/3803実行＝3795PASS＋8skip、87.595秒、log SHA256 `f84bca0f904bc6e8f1ee5302a962b20394cf707c4adc4a42b65fdb7fc9e80ab7`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-fd-output-checks/20260927/`。

既存の作成前symlink検査と`Path.mkdir`の間に隔離tempの親をsymlinkへ入れ替えると、旧実装は拒否しつつリンク先に新ディレクトリを作った。親の各componentを`O_DIRECTORY|O_NOFOLLOW`で開き、欠けた階層と最終token directoryを開いた親fd相対で作成。最後にpathが同じ親/子inodeを指すことを再確認し、差し替え時は`OUTPUT_PARENT_INVALID`で拒否。隔離tempの模擬保持treeでは差し替え先への新規directoryなし、通常のprivate nested creationと既存token拒否も確認。作成後にpathベースで続く保存処理までfdに固定したわけではなく、作成終了後の別actorによる継続的な差し替えは未防御。拒否されたpathや保持物は自動削除しない。Kubernetes/モデル/保持run/backup変更なし、Colima起動なし。WP local-partial。

## 2026-09-27: reject linked output ancestor before mkdir

policy focused96件PASS、全回帰121 suites/3801実行＝3793PASS＋8skip、89.067秒、log SHA256 `ad77fdba20917904274cb424d5d4737ea9a95b7daa13b574e25b2ca161ae157a`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-output-ancestor-checks/20260927/`。隔離tempの模擬保持treeのみを負例に使用し、実保持dataに変更なし。

Standalone候補workflowの`create_output()`は親pathのsymlink検査より先に`mkdir(parents=True)`を呼んでいた。保存先の祖先を別の保持treeへのsymlinkにすると、拒否エラーを返しながらリンク先へ新しいディレクトリを作ることを隔離tempで再現。既存祖先のsymlinkを作成**前**に拒否し、作成後の再検査も維持した。これにより既存リンク経由の意図しない書込みを防ぐ。競合する外部actorが検査と作成の間にpathを入れ替える状況までの原子的な防御は未証明。Kubernetes/モデル/保持run/backupには接続せず、root以外へ新しいartifactは残していない。復旧は拒否されたpathを自動で掃除せず、operatorが保存先を確認して新tokenで新規試行する。WP local-partial。

## 2026-09-27: candidate allowed response content guard (offline)

`--pod-stdin`のserver dry-runでexit 0のJSONを名前・namespaceだけで`allowed`にしていた。fake APIで同名別image、sidecar/init container、CPU request欠損、hostNetwork、hostPathを返すと誤通過することを修正前に再現。修正後は応答Podの限定policy featuresと送信したcontainer名/image、CPU・memory request/limitの意味値、ports、固定安全defaultを照合し、不一致は`unknown`とpendingを維持する。安全側のAPI defaultや等価quantity表現は受理する。生のPod/responseは保存しない。この照合は応答に含まれないcluster状態、すべてのmutation、任意のPod field、admission webhook不在の独立証明ではない。今回はColimaを起動せず、live admission受入の更新なし、WP local-partial。回復は新しいdry-run/保存証拠で行い、未知結果をallowに再分類・自動再送しない。

policy focused95件PASS、全回帰121 suites/3800実行＝3792PASS＋8skip、88.253秒、log SHA256 `b617aac864ae427ee0af592fe8de8f519578078d4a3616a8e61b4a2d7959f73d`。design-context/secret scan PASS。private検証記録は`tmp/local-experiment/policy-candidate-response-checks/20260927/`。

## Summary

Local実験では固定candidateのrule違反・適用範囲を実native admissionと照合する。
モデルは助言だけを返し、権限付与・本番Tier/PRリスク判断は未実装とする。

## 2026-09-18: bounded caller Pod candidate plan

固定15caseだけの実証から、利用者がstdinで渡す限定Pod1件を事前検査できる入口へ進める。
`policy check --pod-stdin`はhostの厳密なinput validation、safe featureだけを使うQwen分類、
同じ候補のnative server dry-runを順に照合する。権限付与・workload実作成・policy変更は行わない。

- [x] 16KiB以内のJSON1文書・Pod1件/container1個と限定fieldだけを受理する。
- [x] 不明field・Secret/環境変数/任意command等を推論前に拒否し、原候補を保存しない。
- [x] P01–P06のhost期待値を独立計算し、特にregistry portとresource値をnative CELと照合する。
- [x] モデルの全違反集合と実APIが報告する違反subsetを区別する。
- [x] protected namespaceと16policy/bindingの同一性、pending intent、dry-run後の不存在を検証する。
- [x] 適合・privileged違反・推論前拒否の3経路、保存status、取消・不明結果を実証する。

送信名はhostが生成し、固定の安全側defaultを補う場合は変換を記録する。
入力/送信bodyのhashと特徴・型付き結果だけを残し、raw manifest/image名/原API応答を保存しない。
原文を保存しないため、後日の再現には元JSONの再提出とhash一致が必要である。
未知結果をallowへ変換せず、自動再送しない。既存15case corpusと認可境界の評価は維持する。

## Context

このWork PackageはLevel L2「Agent第一陣」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l2`, `area:policy`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-POL-001: Policy Gatekeeper Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local development and fixed server dry-run verification.
The model identifies observations; it cannot grant authorization or weaken policies.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

既存native admission baselineの15 server dry-run corpusを再利用し、
モデルが固定candidateのどのrule違反を観測するかを実APIの結果と照合する。
候補はneutral IDsとsafe featureだけにし、allow/denyを含むcase名やhost expected answerを
モデル入力に渡さない。Model出力はviolation/scope-limit/verification IDsのみ。
モデルにallow/approve/Tier付与やpolicy緩和を決定させない。

- [x] 15固定candidateのモデル入力がexpected答えを含まないことを検証する。
- [x] Native policyのsource/manifest identityとready状態を照合し、固定server dry-runだけを行う。
- [x] モデルのrule/limit/verification選択をhost truthと実Admissionへ照合する。
- [x] default namespace controlのallowを安全性や実作成権限へ変換しない。
- [x] ADR-0034の範囲内/範囲外をhostが固定分類し、modelやK8s成功で権限を昇格しない。
- [x] 実Qwen評価、実API比較、Pawprintと保存証跡、README/Vault更新を確認する。

実Pod等の作成、policy apply/delete、GitHub書込、外部公開、追加費用、保持データ削除、
global host変更、全Colima resetはこのentrypointでは実行しない。
Local commitや新規使い捨て実験データ再作成には既存の包括承認があり、
この実装は都度owner承認やGitHub review待ちを再導入しない。
K8s baselineのnamespace適用範囲、image digest syntaxと署名/CVE承認の違い、
server dry-runと実workload動作の違いを明示する。
PRリスクや本番Tier判定・一般的な権限判断の品質は未達として残す。

## 2026-09-17 local functional evidence

実Qwenはnative 15候補（5×3batch）と操作境界10候補（1batch）をhost truthと完全一致で分類。
入力3907/出力1171 tokens、推論合計33.125秒。全分類完了後だけ固定15server dry-runを実行し、
allow2/deny12/対象外namespace control1が期待と一致した。権限付与・境界操作・保存workloadは0。
7件のAgentHouse/Loki/保存OTelとcompleted checkpointを確認した。

実workflowは`tmp/local-experiment/policy-bear/f37433c5b92d462a8ea69f4465c334b5/`。
04:31 UTCの独立再観測で保持6PodのUID/phase/ready/restartsと16policy/binding identitiesは不変、
両namespaceにdry-run残存objectは0。事前/事後証跡は
`tmp/local-experiment/policy-bear-preflight/fa25204603de4af38a659279a90a3dd0/`。
Pending candidateをAPI前に保存し、確認後に結果を保存する。途中失敗・signal・保存障害では
未知の結果と既知/未知token使用を保持し、次の推論/操作を止めることをfakeで確認した。

8合成case×2の実Qwen評価は16/16成功、16監査readback。
入力12454/出力2768 tokens、推論81.187秒、retry0。
`tmp/local-experiment/policy-evaluation/11754e69d7ce4ef89e8e231b5ad5a629/`に全attemptを保存。
Core12＋Bear18の新規tests、既存native17 testsを含む全回帰486 tests、設計73documentsが成功。

再実行は`make local-policy-bear-eval`と`make local-policy-bear-smoke`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_GATEKEEPER.md)に境界・中断証跡・終了方法を記録。
今回の成功は固定25分類と15dry-runに限る。任意policy、一般的なリスク判断、
実workload作成、署名/CVE保証、本番受入、historical GitHub gatesの完了を主張しない。

## 2026-09-18 caller Pod policy CLI evidence

`policy check --pod-stdin`と`policy status TOKEN`を実装した。16KiB以内の限定Pod1件から
safe boolean featuresを作り、独立host期待値・Qwen分類・同じmemory bodyへのdry-runを照合する。
入力のraw bytesはargvや保存filesへ出さず、入力/body hash・変換ID・features・型付き結果を残す。
namespace/name、Never設定、service account token無効だけを補い、policy違反は修復しない。
limitだけでrequestがない入力と、hostNetworkの暗黙hostPortはnative defaultingで判定が変わるため
事前拒否する。resource数量は限定した正の文字列と大小関係を検証し、SI kiloは小文字`k`とする。

実compiled CLIで以下を確認した。各workflowはモデル1回、dry-run1回、retry/cloud呼出し0。

| 保存token | 結果 | Qwen入力/出力 tokens | 推論ms |
|---|---|---:|---:|
| `255b21eed54252825d90870c0811f86f` | passed / allowed、exit0 | 675 / 74 | 4080 |
| `d906b723859c1be5095da0c786ddabb6` | failed / unknown、exit1 | 675 / 84 | 3148 |
| `1e0a67ec0d8785febb8539f1580f5b9f` | passed / denied P01、exit2 | 675 / 84 | 3146 |

最初のprivileged試行はモデルP01一致後、APIのInvalid/422形式をForbidden/403専用parserが
認識できずunknownで停止した。元証拠を保持し、別の新候補によるmodel無しの診断dry-run1回も
unknownとして保持した。Kubernetes v1.33.4の一次sourceに沿ってexact Invalid単一cause形式を追加し、
新tokenで拒否を実証した。policy名・binding名・固定messageの完全一致を維持し、foreign/schema/
混在/余分な末尾は認識しない。未知の元試行を再送・成功への書換えはしていない。

未対応env入力 `02c0bd3c1089d36dd579130a0c2a7f21` はINPUT_INVALIDで推論/API/intent保存前に拒否。
namespace UID/label、空LimitRange、16policy/binding UID/generation/spec/owner/source hashを固定し、
API前のpending intentをfsync、後の不存在とbaseline不変を確認した。権限付与・保存workloadは0。
成功2回と失敗1回に各4件のAgentHouse/Loki/保存OTelを残した。モデル原文・raw API応答は保存しない。
3tokenのstatus各3回（計9回）で32のfiles/directoriesのhash/mode/mtimeが不変だった。
statusは保存features/host期待値/plan/型付き結果/spans/hash sealを検証し、原manifestの再構成や
最新admissionの再観測を主張しない。旧15case corpusは別入口のまま維持した。

Python新規78件（入力27/観測17/workflow13/CLI21）、Go91 top-level testsと59 subtests、
vet/raceが成功。実Python bridge＋実workflow/外部fakeの7経路でexit/取消/再送防止/非保存を検証。
全local回帰1658件（1657成功・既存optional OTel1skip）、設計83documents/関連14tests、secret検査も成功。
証拠は `tmp/local-experiment/policy-cli-checks/38ccaf07c7e64c93935b49fa18234d4d/`、
各workflowは `tmp/local-experiment/policy-candidate-bear/<token>/`、
独立監査は `policy-candidate-preflight/6abcff1fbcd841e9be85f43791c848b2/`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_CLI.md)に入出力と停止時の扱いを記録した。
実証は限定PodのP01適合/違反と入力拒否であり、全P01–P06のcaller入力live網羅、任意manifest、
画像署名/CVE、scheduling、アプリ動作、権限判断、本番受入の完了とは区別する。

独立の軽量再観測でAgentHouseは453→465（上記3workflow各4件）、旧453件の全行hashは不変。
対応Loki各4件、保持3410files、14Pods/3Docker/MLflow identity、16policy/bindingとnamespaceは不変、
LimitRange空、candidate Pod残存0、既存tunnel閉鎖を確認した。深いDB/metadata/fixture観測は
直前のQuery/SRE監査の保存証拠を継承し、今回は各ストアの全内容を再走査していない。
