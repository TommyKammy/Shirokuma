# DB-P6-3C2E1 native graphのINVOKER VIEW

2026年10月31日。C2D後の優先項目として、`native_graph`のSANDBOX VIEW構築と品質テストを実装し、この範囲の技術受入を完了した。

## 利用条件と範囲

承認済みprojectの保存planから、VIEW／TABLE／EPHEMERALの対応graphを実際のRunTaskで構築する。VIEWからTABLEへの依存と保存全generic testのTestTask実行を対象とする。VIEWは同じDatabaseのrun専用SANDBOXへ新規作成し、必ずSECURITY INVOKERとする。既存のVIEWからVIEWへの依存拒否を維持する。

SDKに渡す有効設定をINVOKERへ固定し、実際に生成されたcallback SQLでも、単一VIEW・固定target・INVOKER・承認済みSELECTを検査する。任意のDDLを実行せず、不在を確認した候補へ型付き処理で作成する。TABLEの固定PARQUET、RAW Snapshot、同runの検証済み候補、所有権・admission・取消の境界を維持する。

VIEW候補にはUUID・version・INVOKER設定を表示する。内部ではCatalogのVIEW metadata、定義、識別情報と固定依存を保存・再照合し、TABLEのSnapshotを代用しない。下流TABLEと品質testがVIEWを参照する場合も同じ証拠を検査する。構築成功と品質成功を分け、GET／reload／履歴／元要求の回収によって構築やtestを再実行しない。

## 正式公開の境界と次の優先項目

**VIEWを含むnative graphの正式公開は、この段階では利用できない。** TABLEを選んだ場合もpreview／POSTを`DBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTED`で拒否し、公開可能と表示しない。TABLEの公開が必要な場合は既存SQL/WAPを利用する。

次の優先unit C2E2で、VIEWの定義・UUID／versionと推移依存を公開intent／履歴証拠へ固定する。C2DのTABLE／EPHEMERALのみのnative graph公開、C2B5の単一native TABLE公開、既存SQL/WAPの動作を維持する。native VIEWの正式VIEW公開・既存候補の直接置換、incremental・並列実行、manual-WAP rollback統合、DB-P6-3全体とM1本人受入は別の後続項目である。

## 検証記録

実環境ではE／BをGo CLIの8080から、A／AをWorkbenchから各1回実行した。各graphはVIEW 1件・TABLE 3件・EPHEMERAL 1件、保存全7 testに成功し、計22 child／queryを照合した。CLIは実際の0/0休止状態から復帰し、元keyの再送と最終コードでの再照会による追加SQL・要求・admissionは0件だった。未変更の標準lake_martsも、staging既定VIEWのplanでnative graph適格と確認した。

VIEWと下流dim／summaryを各DBで新たにSELECTし、AのALPHAとBのBETA、summaryの文字数を照合した。6 SELECTのactor・全9項目context・read-only mode・query IDを確認した。VIEWまたはTABLEを選ぶ公開preview／POSTの8ケースはDBT_GRAPH_VIEW_PUBLICATION_UNSUPPORTEDで拒否され、追加SQL・要求・admissionはなかった。既存C2DのPUBLISHED履歴4件も維持された。実viewerのSANDBOX非表示・参照拒否、別DB／別actorの参照拒否も照合した。

別の1 graphをVIEW commit後・下流TABLE開始前で一時停止し、元Eが取消した。作成済みVIEW 1件と完了child／queryを保持し、graphはCANCELED、品質はCANCELED、SDK全体の完了証拠は未成立となった。下流TABLE／testのSQLは0件で、ownerのadmissionは閉鎖された。新規のDBT child／queryは合計23件、新しい候補はVIEW 3件・TABLE 6件である。

正常2 graphのVIEW metadata・UUID／version・INVOKER設定・定義digestと固定依存、実際のDDL target／SELECT参照pins、SDK Task結果、SQLite操作・query・資格情報・admissionを製品の証拠判定関数から独立して照合した。取消で保持したVIEWも独立照合した。正常証拠を保存後、新規B VIEWだけを同じSELECTで置換する故障注入を行い、現在のheadが観測した更新後metadataと一致することを確認した。再照会はDBT_VIEW_CHANGEDで拒否され、元CLI keyによる受付記録の回収でも追加構築はなかった。更新前の正常証拠と更新後の利用可否を分けて保存している。

画面は構築検証済み・全7品質test成功・VIEW UUID／version／INVOKER・TABLE Snapshot・公開保留理由を表示し、公開操作は無効だった。日英、390px幅の横はみ出しなし、reload／履歴、viewer切替時の詳細消去を確認した。参照操作によるSQL・要求・admissionの増加はなく、既存13 worksheetとSQL下書きを保持した。

焦点テストはbackend 227件、CLI／UI 108件が成功した。新しいVIEW群16件をMakefileの正規経路に追加した。正規回帰テストは先行44群と残り43群を段階実行し、全87群を通過した。合計3,464件のうち3,430件成功、既存skip34件。Go全体、design-context、改行・空白・secret filename・diff検査も通過した。

既存25 RAW／MARTS heads、旧DBT／DataOps／query履歴、manual-WAP全記録、141 Database／564 Schemaと各141 binding、権限・資格情報・runtime profile・既存ローカルYAMLを保持した。新しい未閉鎖admissionは0件、既存FENCED_READ_ONLY UNKNOWN 2件を維持した。8888／8080は通常構成のlake_martsのみへ戻し、LOCALは2/2、policy 0分／revision 5、前後doctorは46項目healthyだった。通常構成でAの正常VIEW graphと保全を再照合した。

実機証拠・独立照合・画面は`var/shirokuma/db-p6-3c2e1-2026-10-31/private`へ保存した。この受入範囲はnative graphのSANDBOX VIEW構築・品質testであり、C2E2の公開証拠拡張、DB-P6-3全体とM1本人受入は後続である。
