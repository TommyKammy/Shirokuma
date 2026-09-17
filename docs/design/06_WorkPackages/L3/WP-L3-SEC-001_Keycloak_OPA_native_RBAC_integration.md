---
project: Shirokuma
doc_id: "WP-L3-SEC-001"
title: "Keycloak OPA and native RBAC integration"
status: local-partial
created: 2026-07-05
updated: 2026-09-17
version: "0.7"
area: "workpackage"
tags: [shirokuma, wp, l3, security]
---

# Keycloak OPA and native RBAC integration

| Field | Value |
|---|---|
| WP ID | WP-L3-SEC-001 |
| Level | L3 |
| Workstream | Security |
| Depends-on | WP-L0-POL-001, WP-L1-QUERY-001 |

## Scope

Implement the default security path without Apache Ranger: Keycloak/OIDC optional identity, OPA policy, Trino OPA authorizer, StarRocks native RBAC, ClickHouse native RBAC.

## Acceptance Criteria

- Trino OPA authorizer is configured in dev.
- A row or column policy example is tested in Trino.
- StarRocks and ClickHouse RBAC examples are documented.
- Policy decisions are visible to agents through documented metadata/Pawprints.
- Ranger is not installed in default L3.

## Notes

Ranger remains a future side option only.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-SEC-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local isolated implementation plan

ADR-0034に沿って、保持Trino/Lakehouseを変更しない一時Podの実験から進める。
既存の検証済みTrino483 archiveからOPA/TPCH pluginだけのARM64 imageを新規local buildし、
OPA sidecarで合成userごとの許可・拒否・行filter・列maskを固定TPCH tiny.nationで確認する。
Polaris/S3 credentials、既存Iceberg table、PVC、Secret、外部公開には接続しない。

- [x] exact input hashes / native ARM64 / bounded resource設定を検証する。
- [x] 固定SELECTで全25行、行filter、列mask、denyを独立期待値へ照合する。
- [x] OPAに接続できない別の使い捨てPodで、具体的なOPA通信失敗と結果非返却を確認する。
- [x] UUID/owner/UIDを記録したPod/ConfigMap/NetworkPolicyだけをcleanupし、保持workloads不変を確認する。
- [x] local tests・実機receipt・再実行/復旧手順を保存する。

Trinoの未認証user headerは呼出側が偽装できる。この実験はsynthetic identityに応じた
認可判断の機能検証であり、認証・Keycloak/OIDC統合・production securityを保証しない。
StarRocks/RBAC全体、Pawprintへのpolicy決定相関は後続未達。Grafana専用ClickHouse readerの
SELECT範囲/readonly実測は先行する限定RBACの参考証拠として、L3全体完了と分ける。
Issue/PR/過去の都度承認gateは外部deliveryの境界であり、このlocal実験の停止条件にしない。

## 2026-09-17: isolated authorization verified

Trino483/OPA1.20.2をnative ARM64でlocal buildし、使い捨てTPCH-only Podで8caseが成功した。
全25行、nationkey=0の1行制限、name列をMASKEDにした25行、両方を適用した1行、
未許可user/columnの拒否0行を独立期待値と照合した。同じclientがadmin headerを選んで
全行を読めるcaseも明示的に確認し、未認証headerが認証の代わりにならないことを示した。
正常Pod撤去後、別PodでOPA endpointだけを未待受portへ変えた同SELECTは、
Trino OPA QueryFailed＋ConnectException・返却0行になった。一般errorを合格にしない。
Trino readiness/versionはquery前後に厳密確認するコード経路を通過しているが、
個別readiness応答の保存はなく、削除後の実機再観測を意味しない。

成功run: `trino-opa/runs/02986c45d05247d0918cd7cae440f380`。
OPA decision59件のうち52件はquery ID＋user、7件のExecuteQueryはquery IDがないため
user/operationだけの限定照合として保存。policy拒否・rowfilter・columnmaskを実結果へ対応付けた。
これはprivate decision receiptであり、まだPawprints/MCPへのpolicy telemetry統合ではない。

初回`86d71344797f44eea55658b9e5c36d06`はHTTP200とstarting=falseの起動差で停止し、
最大120秒の厳密起動待ちを追加。次の`719f5e739aab48bba48de7f5ca13bcfc`は正常7caseに成功したが、
Java nested classの実JSON表記差で不達判定を拒否した。固定483の実表記と接続拒否を照合する修正を
加えて再試験した。失敗receipt/diagnostic/build4件を消去・成功へ書換えせず保持する。

Pod/ConfigMap/NetworkPolicyだけをUUIDで作成し、既知UID/resourceVersion付き削除で回収。
UID未確定のcreateは採用せずdeferredに残す。復旧は保存planのscope/hash/imageを直接検証し、
現行Rego/PINS/manifest生成コードへの依存を外した。初回runのrecover再実行は0deletesで成功。
全回帰632 tests、focused23 tests、native OPA policy8 tests、設計76documentsが成功。

### Inputs and runtime boundary

build receipt: `trino-opa/builds/55cc059f9d9648b0a28191b13905b6c7`。
Trino image: `sha256:420d88cf037c400e23ed42a319f0683d6753a525dc94d8f46bfdd57d86aacaa3`。
OPA image: `sha256:9bf48d2d88d954b562ee2090a9887814966707ec55f676f7c5127afb4f29918b`。
OPA公式ARM64 static binaryは`sha256:431bed5a365578241ab06c7cc1c7d0cdff8c11dcbc6f12c3488590deb8b8d66d`。
実験は最大同時1Pod、2.5CPU/4.25GiB、read-only rootfs、non-root、token非mount、
256Mi/512Mi emptyDir。ingress/egress拒否、OPAは同Pod loopback、hostはloopback port-forwardだけ。
既存Lakehouse/PVC/Secretへ接続せず、runtimeを残さない。image/cacheはローカルに保持する。

再実行/復旧: repository `tools/local_experiment/trino_opa/README.md`。
`rtk make local-trino-opa-run TRINO_OPA_BUILD_TOKEN=<build token>`。
このWPはlocal-partial。Keycloakの認証とTrinoのidentity bindingは次節の別環境で検証した。
StarRocks RBAC・policy telemetry・本番security acceptanceは未達を維持する。

### Independent retained-state check

06:16 UTCの独立照合で、既存6PodsのUID/spec/phase/ready/restart、27resourcesのUID/generation/spec、
6ConfigMapsのUID/data/binaryData/immutableがbaselineと完全一致。3実機runの合計15生成UIDは残留0。
成功receiptの8case・固定SQL hash・59decisions・全既知UID cleanupを再照合した。
原文rowsや決定log全bytesは成功receiptにはなく、保存射影の再計算という証拠範囲を明記した。
private証跡`trino-opa-preflight/d14316b781e74ec0ab18804df9ac8739/after.json`。

## 2026-09-17: Keycloak token identity

新規の隔離3container Pod（Keycloak/Trino/OPA）で、Keycloak発行の期限付きJWTを
Trino483のJWT authenticatorが検証し、認証済み固定5usernameへ既存のrow/column policyを適用する。
既存TrinoやOPA実験は変更せず、synthetic fixture/専用realm/clientだけを作成する。
JWT signing RSAとTLS CAは分離し、CAの信頼設定は実験client/プロセス内だけとする。
macOS hosts/keychain/global truststore、外部DNS/公開endpoint、保持dataに変更は加えない。

- [x] 公式Keycloak ARM64 imageをdigestで固定し、必要なlocal buildとnative動作を確認する。
- [x] private CA/証明書/realm key/user/clientをprivate stateへ保存し、Secretもrun所有だけに限定する。
- [x] valid tokenでadmin/row/mask/both/denied userの固定SQL結果を確認する。
- [x] tokenなし/改変署名、署名が正しいwrong issuer/wrong audience/expired tokenの認証拒否を確認する。
- [x] X-Trino-UserおよびOriginal-User経由の偽装を拒否し、認証401とSQL permission拒否を区別する。
- [x] positive control、保存readiness、全known UID cleanup、保持環境不変、再実行/復旧/testsを確認する。

Realm RSAのpublic PEMをTrinoへ固定し、JWKS起動競合を避ける。この実験はOIDC IdP発行JWTの
検証であり、browser login/redirect、JWKS rotation、realm roles/groups同期を含まない。
Trino483はJWTのexpが存在すれば期限を検証するが、その存在必須を設定できないため、
Keycloak発行tokenにexpがあることと、正しい署名の期限切れfixtureの拒否を検査する。

### Live evidence and limits

成功run: `trino-identity/runs/8121b529b1844086bfa489a1336a7b60`。
Keycloak26.7.4が実発行した5tokenはRS256署名・issuer/audience/principal/期限を検査し、
寿命はすべて300秒。独立鍵で署名した負例と、同じrealm鍵の正常対照を分けた。
正常6case（admin前後、row、mask、both、host署名対照）、認証拒否6case（なし/別鍵署名/
issuer/audience/期限/未登録principal）、認可拒否3case（denied/両header偽装）が全件一致。
401時は新規OPA決定0。両偽装は認証済み`lab_mask`から`lab_admin`へのImpersonateUser拒否を確認。
61decisionsの52件はquery ID＋user、9件は直列case window＋user/operationの限定相関。
queryless決定を複数caseへ再利用しない。Keycloak/Trinoの前後4readinessを時刻/TLS検証付きで保存。

初回`3da115d1df454b10a139086a6460bacc`はtoken必須claim検査で停止しSQL未実行。
default scopesを空にしたKeycloak26.7.4に明示的な`oidc-sub-mapper`を追加した。
検査を緩めず、固定reason codeを細分化し、failed証跡を保持して新runで再検証した。
2run計8resources（Pod/Secret/ConfigMap/NetworkPolicy）残留0。初回のrecover再実行も削除0で成功。
既存6Pods/27resources/6CM/3Secret UIDsが前後一致。private0700/0600、fixture hashes、
通常receiptの秘密値非混入、case/decision相関を独立再照合した。実tokenや結果行の原文は保存せず、
署名・完全行一致はlive runner検証、後段の再検証は保存射影の検査という範囲を維持する。
証跡: `trino-identity-preflight/fc0e1b3343bb411b992cb55fc18e76c5`。

build: `trino-identity/builds/6a5ddcfa93c141de861113b2160a5227`。
Keycloak image: `sha256:0e4df83be2a3ad04b6fce7618004e7791917c1035fdc833c6c9d2bc2258395d2`。
公式ARM64固定digestから専用imageをlocal buildし、既存Trino/OPA imageを再利用した。
最大1Pod/4.5CPU/6.25GiB、4 emptyDir計3GiB、PVC/Serviceなし、保持catalogへの接続なし。
再実行: `rtk make local-trino-identity-run TRINO_IDENTITY_BUILD_TOKEN=<build token>`。
手順と一次source: repository `tools/local_experiment/trino_identity/README.md`。

全回帰664 tests（identity focused32を含む）・設計76documentsが成功。
次節で新しいlive runのcase観測をPawprints/Loki/OTelへ相関した。過去のOPA receiptは変換しない。
StarRocks RBAC、browser SSO、JWKS rotation、realm role同期、本番security acceptanceは未達。

## 2026-09-17: live policy observation telemetry

新run`trino-identity/runs/b94184c624174f0c8f01fc00cae8d7d7`が全15caseに成功し、
専用actor `local-trino-identity`の16Pawprints・16Loki records・16OTel spansを相互照合した。
実5token/61decisions、生成4UID cleanupも完了。Keycloak実験3run計12resources残留0、
既存6Pods/27resources/6CM/3Secret UIDs不変を追加確認した。

- 6正常case＋全体結果1はsuccess、拒否9caseはblocked。期待どおりの拒否でもblockedを残す。
- 検証済みOPA拒否3caseだけ`blocked_by_policy=true`。認証401の6caseはfalse。
- case時間はquery送信/読取/結果検査/監査照合を含む実測時間。元OPA判断の時間や純SQL latencyではない。
- trace `8a6354a575534d342d3849134fb12295`に実query IDs・61decision IDs/限定相関を結び付けた。
- token/credentials/SQL本文/結果行/生OPA logは送信しない。model usage/costはNULL。
- runtime所有/同IDを送信前に確認し、pending journal→AH/Loki読戻し→保存spanの意味照合を実施する。
- cleanup結果は実験失敗時にも独立保存。全case/cleanup/最終delivery/span確認前はpassedにしない。

既存MCP `agenthouse_recent(limit=20,status=any)`がこのTaskの全16eventを返し、
status/policy flag/trace/duration/NULLがsource journalと一致した。MCP読出し自身の監査1件も保存。
決定の詳細/元query相関はprivate OTel/telemetry射影、MCPに見えるのは固定Pawprint metadataという境界を保つ。
GrafanaのTask/Trace絞込みで拒否9件の表示を確認した。これは実policy/requestの観測であり、
モデル推論や汎用security運用、本番受入を示すものではない。

独立after照合でPawprintsは311件＝保持294件完全不変＋新identity16＋MCP1。残4表の全行hash/DDL、
Pawprint DDL、AH/Loki/GrafanaのID/Config/HostConfig/起動時刻/restarts/mount/ports/networksも一致。
保護31filesのhash/mtime/modeを保持。実結果行は非保存のため再照合はcount/matched射影までとする。

再実行: `rtk make local-trino-identity-telemetry TRINO_IDENTITY_BUILD_TOKEN=<build token>`。
通常runは標準ライブラリのまま、opt-in時だけ既存pinned LLM venvのOTelを使う。新規LLM呼出なし。
59 focused testsはpinned環境で全成功。全回帰は692実行中691成功/標準環境のOTel結合1skipで、
同じ結合テストは専用venv targetで必ず実行・成功。設計76documentsも成功。
証跡: `trino-identity-telemetry-preflight/c96c6414e88c4531a7f21b7f2c760a3c`。
次はWP-L3-VW-001の隔離Gateway/2backendルーティング実験を進める。
