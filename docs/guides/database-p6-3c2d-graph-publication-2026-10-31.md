# DB-P6-3C2D native graph候補の明示公開

2026年10月31日。[C2C](database-p6-3c2c-native-graph-2026-10-31.md)の保存graph構築・品質証拠を、TABLE単位の正式公開へ接続した。この範囲の実装・技術受入を完了した。

## 利用条件と操作

対応するTABLE／EPHEMERAL全graphの構築が検証済みで、明示選択した非空の保存全generic testが成功していることを条件とする。実際のRunTaskとTestTaskの全選択・完了・結果、全TABLE候補と全品質receiptを保存記録から照合する。選択TABLEに付属するtestだけを見て、他のtestの失敗を無視しない。

現在のadminが選択TABLEの候補、元の実行者とcontext、同じDatabaseのMARTS公開先を確認する。metadataだけのpreviewを表示し、確認したexpected_targetと元keyを固定して公開を受け付ける。品質成功後も公開時に最低1行の監査を実施する。空候補は公開しない。

公開は選択したTABLEごとの操作である。他TABLEや依存先の公開は別操作とする。あるTABLEの公開予約や監査失敗によって、別TABLEの有効な公開条件を一括で失わせない。EPHEMERALは公開対象外である。

## 証拠と実行

元graphのactor・全9項目context・plan／source digest、選択TABLEの候補とbuild child、全TABLEのbuild receipt／依存Snapshot、RAW pins、RunTask／TestTaskと保存全品質結果を公開intentへ固定する。他モデルのpublication_idのような後から変わる予約状態を、凍結する実行証拠に混ぜない。

公開者は現在のadminであり、Eが作成したgraphをAが確認・公開する場合も、sourceとpublisherの権限・資格情報・contextを分けて検査する。graphの先頭モデルではなく、選択TABLEの作成操作と候補を使う。

既存nativeの型付きDataOps監査・公開を再利用し、固定候補Snapshotから固定PARQUETのCTAS／CREATE OR REPLACE TABLEを実行する。候補全体と確認済みtargetを受付前・実送信前に再検証し、target claim／CASとadmission・cancelを維持する。originはnative_graphとして記録し、SDKやcompilerを公開時に再実行せず、manual-WAPの履歴を作ったことにしない。

受付不明・UNKNOWN・commit後の記帳失敗では、元keyとpublicationを照合する。GET／reload／履歴／reconcileは追加のbuild・監査・公開SQLを送らない。完了後のPUBLISHEDは凍結intentと閉鎖済み操作・永続bindingから照合し、後日の候補削除や公開先更新と履歴証拠を分ける。

[C2B5](database-p6-3c2b5-native-publication-2026-10-31.md)の単一native TABLEと既存SQL/WAPの公開契約を維持する。native VIEW／incremental・並列実行、manual-WAP rollback統合、DB-P6-3全体とM1本人受入は後続である。

## 検証記録

2 Databaseで、EPHEMERAL 1件とTABLE 3件のgraphを計3回実行した。各graphはRunTaskの構築と保存全5品質testに成功し、計24件のchild／queryを照合した。6件のTABLE公開を実行し、4件はPUBLISHED、空候補1件は新しい行数監査でFAILED、元Eによるsource cancelを監査後に受けた1件は正式SQLを送らずCANCELEDとなった。公開処理は計10件のchild／queryであり、source graphを再実行していない。

WorkbenchからAのTABLEを確認・公開し、別TABLEを独立して公開した。Go CLIからはEが作成したBのTABLEをAが公開した。実際のcold状態から復帰し、受付後のHTTP応答を一度切断した後も、同一key・同一bodyの再送で元publicationを回収した。source cancel、別TABLEの予約、公開先の後続更新の後も、元keyの照会は同じPUBLISHEDを返し、追加SQLはなかった。

確認後に新しいMARTS公開先のSnapshotを進めた要求は、受付前にDBT_PUBLICATION_REVIEW_CHANGEDで拒否した。再確認した要求では置換公開に成功した。新しい候補のSnapshotを進めた場合はpreviewとPOSTをSTAGE_CHANGEDで拒否し、SQL・intent・admissionは増加しなかった。全graph内の他testが失敗している過去run、非admin、別DB、EPHEMERAL、予約済みTABLEなどの実機境界も照合した。品質未実行graphの拒否は焦点テストで検証した。

3 graphと6 publicationについて、保存SQLite・DataOps child・engine query・SQL構文・Catalogを製品の証拠判定関数から独立して照合した。全9項目context、選択TABLEと依存Snapshot、元actorと公開actor、資格情報の分離、監査・公開ID、固定MARTS target、commit証拠と閉鎖を検証した。MARTSの新しいSELECT結果では、AのALPHAとBのBETA、下流summaryの値と実行contextを確認した。

画面では公開先FQN、実行元、builder／publisher、全品質成功、監査行数、公開済みSnapshotを表示した。日英切替、390px幅の横はみ出しなし、reload／履歴、viewer切替時の詳細消去を確認した。これらの参照操作によるSQL・intent・admissionの増加はなく、既存13 worksheetとSQL下書きも保持された。確認後に識別情報が変わった場合の再確認案内と、受付拒否が確定した新規公開要求だけを明示終了できる境界も追加した。

`make verify-local-experiment`の現行86コマンド群を段階実行し、全群を通過した。先行52群の後、originを明示していなかった旧mockを修正し、DataOps群39件を再実行、残り33群を通過した。合計3,442件のうち3,408件成功、既存skip34件。新しいgraph公開群16件をMakefileの正規経路に追加した。Go全体、design-context、改行・空白・secret filename・diff検査も通過した。

既存22 RAW／MARTS heads、旧DBT／DataOps履歴、manual-WAP全記録、141 Database／564 Schemaと各141 binding、policy・資格情報・runtime profile・既存ローカルYAMLを保持した。新しい未閉鎖admissionは0件、既存のFENCED_READ_ONLY UNKNOWN 2件は維持した。確認用project登録はprocess限定で、終了時は8888／8080を通常構成のlake_martsへ戻した。LOCALは2/2、policyは0分／revision 5、前後doctorは46項目healthyである。

実機証拠・テスト集計・画面は`var/shirokuma/db-p6-3c2d-2026-10-31/private`に保存した。この記録は上記範囲の技術受入であり、DB-P6-3全体やM1本人受入の完了を意味しない。

## C2E1への追補（2026年10月31日）

[C2E1](database-p6-3c2e1-native-views-2026-10-31.md)で、native graphのSANDBOX INVOKER VIEW構築・品質testを実装し、この範囲の技術受入を完了した。VIEWを含むgraphのTABLE公開は保留し、C2E2でVIEW定義と推移依存の公開証拠を拡張する。本書のTABLE／EPHEMERAL graph公開の受入範囲を維持する。
