---
project: Shirokuma
doc_id: DB-P6-4D1
title: Saved AI summary metadata in unified audit
status: accepted
created: 2026-11-01
updated: 2026-11-02
version: "1.0"
area: guides
tags: [database, ai, audit, metadata, authorization]
---

# DB-P6-4D1: 統合監査の保存AI要約metadata

既存[AUD-03](database-capability-matrix-2026-10-29.md)へ、保存済みAI記録の最新要約metadataを読取り専用で投影する実装単位である。[DB-P6-1](database-p6-1-implementation-2026-10-29.md)の統合監査契約を拡張する限定範囲とし、新しい監査pipelineやtelemetryを作らない。D1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,135成功・34 skip、Workbenchの実API・実画面、最終保全PASS）。

## 対象とeventの意味

`source=ai`とし、actionは保存kindに対応する次の閉じた集合だけを扱う。

- `sql_explanation`
- `sql_plan_explanation`
- `saved_plan_explanation`
- `sql_proposal`
- `catalog_proposal`

既存event DTOの形状と各fieldの意味をそのまま使い、AIの`links=[]`を固定する。内容fieldや元内容への関連リンクを追加しない。表示・検索・詳細・JSON downloadも同じmetadata投影の範囲に限る。

これは最新の永続化済み要約であり、受付から終了までの全遷移を記録した監査ではない。保存状態・時刻は保存時点の情報であり、現在の稼働や実効果の保証へ変換しない。UNKNOWNや進行中の記録を照合・更新しない。保存されていない過去の実効roleを推定して補わない。

## 認可と保存context

既存AUD-03のself／databaseの認可を維持し、一覧・詳細・downloadで現在のaccount・選択role・DB／Schema権限を検査する。selfでは本人・保存role・許可Schemaの境界を保つ。DBAのdatabase scopeは許可された対象DBの要約metadataに限り、他人のAI原文を取得する権限にはしない。元のAI内容は既存のowner-only経路の独立した認可に従い、D1から取得しない。

保存full9は`ai_explanations.context`と`record.context`で一致し、SQL行の`id`・`actor`は`record.id`・`record.actor`と一致する必要がある。kind・state・timeはSQL行の列ではなく、厳密に検証するrecord metadataである。過去の保存contextが現在のbinding／generation／grant revisionと等しいことは要求せず、履歴の整合確認と現在の閲覧認可を分ける。内部整合だけで現在権限を代用せず、現在の失効やDB／Schemaの利用不可は既存の認可に従う。

platform scopeのMANAGEMENT allowlistはAIを含めない。P権限だけでplatform監査へAI要約を混在させず、DBに関する閲覧は既存self／databaseの条件で判断する。

## 有界な厳密投影とprivacy

保存JSONの大きさと構造を有界に検査し、context・ID・actor・kind・state・timeの型と相互整合を確認する。不正行と不許可行は、共通keyset／limitによるページングより前に除外する。既存の署名cursor、rowid上限、fresh authorization fenceを保ち、ページ取得時に現在認可を再確認する。contextは8,192 bytes、recordは262,144 bytes、JSONの深さは32という一行ごとの上限を適用する。Python側へ全raw記録の配列を取得せず、SQLで許可行を走査・投影する。

既存cursorは非暗号化のbase64 JSONとHMAC署名を使い、各台帳のrowid上限などのページング情報を含む。署名・現在認可・filterとの束縛を保証し、全DBの挿入進行量の秘匿を保証する契約ではない。認可範囲外のAI記録の識別子・本文をcursorへ追加しない。

SQL・request・prompt・AI output・plan・model・provider・error・pinsを応答や検索対象へ含めない。保存内容の一部を検索用文字列へ展開することも避け、既存metadataの意味だけで検索する。原文・計画・生成物のコピーや新fieldを監査へ追加しない。

AI各familyの取得・履歴回復・復旧処理を呼ばず、provider／catalog／Engine／SQL／leaseの呼出し、推論、書込み、migrationを追加しない。取消・再実行・回復操作や元記録へのmutationを提供しない。

## 検証と限定技術受入

2026-11-01に着手し、2026-11-02 JSTに限定技術受入とした。実API・実画面の観測時刻と、全体回帰の終了時刻はprivate evidenceへ個別に記録している。重複するテスト実行の件数は合算しない。

| 層 | 結果と適用範囲 |
|---|---|
| 重点backend／UI | backend32件、UI32件（監査28・Marts3・日英1）がそれぞれ成功。全Workbenchの事前回帰923件も成功。不正JSON・重複key・非有限値・深い構造・UTF-8・型／保存context不整合を排除する。 |
| 認可と隔離 | fixtureでaccount／grant／binding変更時の取得後fenceとcursor失効を検証。実APIではP本人A46件、DB管理scopeのA62件／B13件、E本人A8件／B12件、V本人A6件／B0件、同じEのviewer保存role2件を分離。platform scopeのAI一覧は空。 |
| ページング／詳細／JSON | 実APIで3件ずつ21ページを取得し、欠落・重複なしとページ再表示を確認。詳細とdownloadは同一投影。不正行を前方に20件超置くfixtureでも有効行を取得する。 |
| 実API／CLI | 通常Workbench8888の51 GETケースが成功。既存SQL endpoint8080のAudit非公開404は維持。既存HTTP専用CLIのlist／show／exportも成功し、同一metadataと出力mode0600を確認。 |
| 実画面 | 5種類・本人46件、DB Bの13件、前後ページ、日英切替、幅390pxのページ外へのはみ出しなしを確認。監査表は専用枠内で横scrollを使う。実JSONを保存して内容を検証。ロール降格で一覧・詳細・保存リンクを消去し、復帰時の自動再表示なし。 |
| 無変更と保全 | 同権限の監査・DB切替で4か所のdraftを保持。検証区間は857 GET・mutation0、うち監査137 GET（capabilities127・一覧6・詳細3・download1）。旧共通runtime109 GETは別経路で、AI family呼出しは0。最終保全はactive11,955行＋default156行、schema23、既存UNKNOWN、7ファイルの内容／mode、31 TABLE／33 VIEWを維持し、新規行0。 |
| 全体回帰／環境 | 最終6 source／testと12無変更guardを固定し、98群・4,135成功・34 skip・失敗0・終了コード0。34 skipの名称・理由は前回と同一。runtime doctorは46確認中45 healthy・SQL probe未実施を示すinformational1、終了コード0。新たなEngine SQLの実行証拠とはしない。 |
| 文書整合 | 最終文書のdesign-context確認は2群14件成功・終了コード0。能力ID89件の順序と他能力行、過去の受入結果、P5Aの元文書prefixを維持する。 |

コード検査・認可隔離・実画面・実運用・所有者受入は別の証拠として扱う。D1は保存AI要約の読取りに限る技術受入であり、所有者本人の全体受入を代行しない。

## 後続境界

[C2B](database-p6-4c2b-platform-observations-2026-11-01.md)と[P6-5A](database-p6-5a-compute-observations-2026-11-01.md)の受入済み固定platform観測とは対象が異なり、その過去の証拠をD1へ流用しない。C2Cのworkload baseline／全面的global policy検査、P5B、AI設定は未完了の別単位である。AI原文の監査保存・検索、全遷移監査、取得・復旧やmutationは本単位の対象外とする。

[全機能計画](database-full-capability-and-compute-plan-2026-10-28.md)に従い、P6-4全体／P6-5全体／P6-7縦断検証／M1本人受入は継続する。D1の文書整備や限定実装を親gateの閉鎖へ読み替えない。

次の優先候補はP5Bの固定LOCAL構成資源（設定済みrequests／limits）の読取り表示とする。C2C・AI設定は独立した後続単位として残す。


## 後続の履歴参照（2026-11-02）

[DB-P6-5B](database-p6-5b-platform-resources-2026-11-02.md)はComputeの固定LOCALコンテナ設定資源を扱う別の限定技術受入単位である。P5Bの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,166成功・34 skip、両HTTP面の実API・Workbenchの実画面、最終保全PASS）。 D1の受入範囲・結果は従前どおりとし、P6-4全体／P6-5全体／P6-7／M1本人受入はOPENのままである。
