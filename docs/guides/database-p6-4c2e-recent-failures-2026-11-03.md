---
project: Shirokuma
doc_id: DB-P6-4C2E
title: Recent failed Pawprints metadata for platform SRE
status: accepted
created: 2026-11-03
updated: 2026-11-03
version: "1.0"
area: guides
tags: [database, sre, policy, compute, telemetry, acceptance]
---

# DB-P6-4C2E: 最新20件の失敗Pawprintsを参照する

[閉鎖監査](database-p6-0r1-capability-closure-audit-2026-11-03.md)に従い、AI-04／AI-05／CMP-05の旧操作と現在の観測を照合した。今回の実装は旧SREの最新20件の失敗telemetryという一出力を扱う。現在のAudit Platform previewは最新100件を取得後にローカルで絞るため、100件以上の新しい成功記録が続くと、それ以前の失敗を表示できない。取得前にfailedへ絞る新しい独立sourceを追加する。

## 旧出力と現在の対応

| 旧操作・出力 | 現在の対応と残件 |
| --- | --- |
| SREのTrino／storage／catalog ready数 | C2B／C2Dの固定LOCAL・登録基盤controller記録。replica数は現在のSQL／storage／catalog healthの保証ではない |
| SREのcontainer使用量 | P5Dの固定LOCAL CPU／memory使用量。旧namespace全体のtopとは対象が異なる |
| 最新20件の失敗Pawprints | 今回、source側のfailed選択を追加。既存Auditの最新100件取得後の絞り込みとは区別 |
| AgentHouse／Loki readiness・raw logs・Loki query range | 現在の限定metadata観測では完了にしない。必要対象と安全に公開する範囲の照合が残る |
| Policyの4宣言条件 | C2C1／C2C2の固定LOCAL・登録templateで限定対応 |
| namespace全体・実Pod／ephemeral・任意manifest check | 未閉鎖。native8設定など別機能への置換や必須範囲の免除は行わない |

根拠は旧 `bears_resident.sre`／`policy`、現行 `audit_service.telemetry`、C2A／C2B／C2C1／C2C2／C2DとP5D／P5Gの既存guideである。旧Bear actionを再開放せず、必要な一出力を現在の認可で取り戻す。

## 実装した契約

通常のSRE PlatformとComputeへ、独立した手動snapshotを置く。現在の真P・global current／maximum admin・selected adminを条件にし、DB閲覧grantで代用しない。権限を明示更新してから取得でき、自動取得・poll・retryは追加しない。各panelのreceiptと非同期処理を分け、他のDB context・未保存draft・元処理・Audit previewを保持する。

`GET /api/v1/bears/platform-recent-failures` は入力なし。workbench／native両HTTP面で既存P認可を取得前後に確認する。AgentHouse Clientの既存3秒readを使い、固定SQLで `WHERE status='failed'` を `ORDER BY timestamp DESC, event_id DESC` と `LIMIT 21` より先に適用する。最大20件と続きの有無だけを返し、client側のstatus filterで代用しない。

返すのは既存のevent_id／timestamp／agent_id／action_type／target_system／status／trace_id／duration_msの8項目。sourceのUTC timestamp形式を受理し、UInt64のdurationは桁を保った文字列で扱う。空のtrace_id等も許容し、ReplacingMergeTreeの未統合行を独自に重複排除しない。最大20件は20個の一意な障害ではなくsourceの記録件数である。

statusがfailedでない行やmalformed sourceは未取得として扱い、余分なSQL・prompt・payload・raw logを保持・表示しない。共有HTTP responderと同じJSON encodingで64KiB上限を検査し、部分的な成功や追加読取りで補完しない。取得できた0件とsource unavailableを区別し、空の成功をhealth／Policy allowと表示しない。event時刻は過去になり得て、取得時刻や停止原因とは別である。

sourceの内部transportは固定ClickHouse SELECTをHTTP POSTで送る。通常HTTP入口はGET-onlyだが、全通信POST 0や全SQL 0とは記録しない。ClickHouse書込み、Trino実行、LLM、admission、Wake、Kubernetes操作、runtime.status、復旧・清掃は追加しない。既存Auditの最新100件API・DTO・UI・local filterは変更しない。

## 検証と受入

2026-11-03 JST、この一出力の限定技術受入を完了した。証拠はgit対象外の `var/shirokuma/db-p6-4c2e-recent-failures-2026-11-03/private/` に保存し、最初の失敗も後続結果と区別した。

| 検証層 | 結果と限界 |
| --- | --- |
| 契約・重点試験 | 386件成功。新backend 15件・新UI 18件を含む。100件超の新しい成功より前の失敗を選ぶsource、空・故障・malformed、認可失効、UTC、UInt64、実HTTP encodingの上限、非空の既存Auditを検証 |
| 全体回帰 | 正規125 group、合計5,021件中4,987成功・34 skip。初回の失敗4 groupはHTML増加に伴うNode引数長とstdin移行時の既存fixture変換であり、共有補助を修正して同じ4 groupを再実行。成功済み121 groupは再利用した。最後のCSS selector 1か所の修正では全scriptと他10ファイルの同一性を確認し、Workbench全1,241件を再実行した。別々の実行記録を保持し、最終版で125 groupを一斉再実行したとは記録しない |
| 実HTTP・認可 | 両portの真P＋selected adminで、当初はunavailable各1回。selected bi_viewer／data_engineerは両面とも403、計4件。DB adminのみ・非P・上限不足・取得中失効等はcontrolled試験で確認し、実HTTPとの証拠を区別 |
| 実source | 既存の専用AgentHouse containerが停止していた。所有・固定image・loopback port・既存volume・明示Docker contextを照合し、同じcontainerのstartを1回だけ行った。新container・schema・fixture・credentialの作成や自動再試行は行わず、復帰後に両面を新規観測し、各20件・truncated true・6,201 bytesを取得した。最初のunavailableを空成功へ変更しない |
| 通常ブラウザー | 最終HTMLを通常port 8888から取得した内容と照合。最終CSSでSRE PlatformとComputeから各1回、非空20件を手動取得し、別のAPI記録作成時刻を保持。CSS修正前の各1回を含め、browserの手動source取得は計4回。権限更新・画面移動・日英切替はsource取得を増やさない。日英切替後も20件のUTC時刻・metadata・正確なdurationとnative detailsの展開状態を保持した。390pxの日英表示はdocument幅390、panel幅366、ボタンが収まる。200% zoomは未実施 |
| 操作状態 | 元の19 Worksheet・選択・SQL・DB／schema／role／mode、Bear 28入力、Audit 9入力、言語・viewportを保持。元のSRE scopeへ戻し、検証用tabだけを閉じた。Worksheet／TrinoのSQL実行・LLM・WAP操作は行わない |
| 保全 | 通常2processは従来と同じargv・cwd・全29環境変数・安定TMPDIR・loopbackで1回ずつ交代。最終PIDは96049／96050。保護14ファイルの内容・modeと既存backend 19ファイル、全control行・schemaを照合。active 12,271行／default 156行、活動中query／DataOps／admission／AIは0、既存UNKNOWNのquery 3／admission 3／AI 6／dbt 1を保持 |
| 文書・差分 | `make verify-design-context`成功。機能表89 IDと順序を維持し、AI-04／CMP-05以外の87行（AI-05を含む）は変更しない。既存Audit実装も不変で、ownerの2 YAML差分は保持してcommit対象から除外 |

ブラウザーの古いbackground区間にはCDP保持上限による欠落があるため、全通信の証明には使わず、8888 serverのbaseline offsetからの有限access logと照合する。8080はaccess logを出力しないため、保存済みの直接HTTP receiptを根拠とし、8080全通信のmethodは証明しない。通常入口のGETと内部ClickHouseのread SELECT／HTTP POST、通常server初期化時の既存SQLを区別する。AgentHouse内部startupやvolumeデータの全byte同一性は検証していない。観測機能に自動復旧を追加したわけではない。

## 残るgate

今回の一出力でAI-04全体、AI-05／CMP-05の全必須操作、global Policy、全89行、P6-7／M1本人受入を閉じない。残るraw log等とPolicyの実Pod／ephemeral／namespace範囲を照合し、必須範囲の変更はowner合意で扱う。[6Fの清掃残件](database-p6-6f-dataops-recovery-2026-11-03.md)を保持し、実Compute伸縮はM1後とする。
