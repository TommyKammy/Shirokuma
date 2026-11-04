# Databaseモードの機能一覧と利用条件

2026-11-04 JST、[DB-P6-4C2M](database-p6-4c2m-sre-foundation-logs-2026-11-04.md)でSRE PlatformのLoki重大度検索に登録済み基盤のOPAを加えた。固定の発行元を照合し、時刻・発行元・重大度だけを扱う。旧名前空間全体とSQL-09／ADM-09〜11の範囲を照合し、DB-P6-7の独立環境とM1本人受入を継続する。

2026-11-04 JST、[DB-P6-4C2L](database-p6-4c2l-sre-loki-severity-2026-11-04.md)で管理対象AgentHouse／Lokiのerror／fatalを、時刻・発行元・重大度に絞って手動でLokiへ取り込み、SRE Platformで検索できるようにした。対象は固定2コンテナ。旧名前空間別ログの投入範囲と、M1の統合・本人受入は継続する。

2026-11-04 JST、[DB-P6-4C2K](database-p6-4c2k-sre-raw-logs-2026-11-04.md)でSRE Platformに、現在のP管理者がAgentHouse／Lokiの管理対象コンテナの生ログを明示取得する導線を追加した。旧Loki error／fatal検索の対象ストリームと閲覧・保持範囲の確定、DB-P6-7の独立環境とM1本人受入を継続する。

2026-11-04 JST、[DB-P6-7](database-p6-7-integrated-acceptance-2026-11-04.md)で89行と限定技術受入を照合し、既存環境の通常Workbenchで予備縦断を進めた。作成画面を離れた後の再読み込みが古い作成リンクへ戻る不具合を修正した。必須範囲の閉鎖、独立clean・故障環境、作成性能と本人受入は継続する。

2026-11-04 JST、[DB-P6-6F-R1](database-p6-6f-ingest-cleanup-2026-11-04.md)で6Fのコミット後の清掃失敗を照合し、新規Manual Ingestの所有SANDBOXステージを非purge登録解除へ変更した。通常Workbench APIで3行のコミットと登録解除を確認した。コミットと清掃を別々に記録し、元の6Fステージは失敗時の証拠として保持する。M1本人受入を継続する。

2026-11-04 JST、[DB-P6-4C2J](database-p6-4c2j-manifest-advisory-2026-11-04.md)でPolicy PlatformのP管理者がYAML／JSONを1文書貼り付け、Podと5種のコントローラーの4宣言を確認できるようにし、限定技術受入を完了した。SREの生ログ本文／Loki検索、他namespaceの範囲判断、6F清掃、M1本人受入を継続する。

2026-11-04 JST、[DB-P6-4C2I](database-p6-4c2i-controller-baseline-2026-11-04.md)で登録済みnamespaceの5種のコントローラーについてPod templateの4宣言をPolicy Platform／Computeから手動確認できるようにし、限定技術受入を完了した。任意manifest、他namespaceの範囲判断、SREの生ログ本文／Loki検索、6F清掃、M1本人受入を継続する。

2026-11-04 JST、[DB-P6-4C2H](database-p6-4c2h-sre-logs-2026-11-04.md)で専用ColimaのAgentHouse／Lokiを対象に、直近コンテナログの診断概要をSRE Platform／Computeに追加し、限定技術受入を完了した。取得範囲の行数・重大度別件数・警告以上の時刻をP管理者が手動確認する。生ログ本文の閲覧とLokiのerror／fatal検索は別の残件として扱う。Policyのcontroller／manifest検査、6F清掃、M1本人受入を継続する。

2026-11-04 JST、[DB-P6-4C2G](database-p6-4c2g-sre-readiness-2026-11-04.md)でSRE Platform／ComputeにAgentHouseとLokiのHTTP応答を個別に確認する手動取得を限定技術受入済みとした。コンテナの実行状態、生ログ、Lokiのerror／fatal検索は別の観測対象として扱う。Policyのcontroller／manifest検査、6F清掃、M1本人受入を継続する。

2026-11-03 JST、[DB-P6-4C2F](database-p6-4c2f-live-pod-baseline-2026-11-03.md)で、旧Policyの実Pod分岐に対応する登録済みnamespaceの宣言レビューを限定技術受入済みとした。Policy Platform／Computeから、全Podの通常・init・ephemeral containerへ既存4項目を手動確認する。空一覧と取得不能を区別し、runtime強制・admission・aggregate allowは示さない。SRE readiness／raw logs、全controller template・任意manifestの残件と他namespaceの範囲判断、6F清掃、全89行とM1本人受入を継続する。

2026-11-03 JST、[DB-P6-4C2E](database-p6-4c2e-recent-failures-2026-11-03.md)で旧SREの最新20件の失敗Pawprints出力との差を限定して補った。SRE Platform／Computeの独立した手動snapshotが、現在の真Pとadminを取得前後に検査し、source側でfailedを選んだ8項目・最大20 recordsを表示する。現在health・停止原因・DBへの対応付け・全体Policyを推定せず、既存Auditの最新100件previewを維持する。次は残るSRE readiness／raw logsと、Policyの実Pod／ephemeral／namespace／manifestの必須範囲を限定して照合する。6F清掃残件・全89行・M1本人受入は継続し、実Compute伸縮はM1後とする。

2026-11-03 JST、[DB-P6-1A](database-p6-1a-history-wayfinding-2026-11-03.md)でAUD-02のquery／load／actionと現在の保存履歴を通常監査画面へ対応付けた。20種の表示名と8種のsource選択を日英で整え、条件の下書きだけを選んで明示検索する導線とした。適用済み履歴・詳細・ページと独立contextを保持し、保存metadataと全状態遷移、AI成功とcommit、commitと清掃失敗を区別する。次はAI-04／AI-05／CMP-05の既存必須操作と現行観測・通常導線を限定して照合し、6F清掃残件・P6全必須行・M1本人受入を継続する。実Compute伸縮はM1後とする。

2026-11-03 JST、[DB-P6-4B3](database-p6-4b3-wap-guidance-2026-11-03.md)でAI-02／SQL-12／SQL-13の通常導線を整備した。Bears共通領域とCopilotから既存のManual WAP・Managed Martsへ移動でき、独立context・未保存draft・元の追跡情報を保持する。B2の明示取込と保存以降の認可・操作を維持し、旧Bear／Copilot直接実行は閉じたままとする。次はAUD-02の履歴と通常導線の対応付けを進め、6FのIngest清掃失敗、旧SRE／Policy必須操作、P6全必須行とM1本人受入は継続する。実Compute伸縮はM1後とする。

2026-11-03 JST、[DB-P6-6F](database-p6-6f-dataops-recovery-2026-11-03.md)でING-03／TRF-06の通常再接続導線を整備し、限定技術受入を完了した。ブラウザーには本人ごとの最小追跡情報だけを保存し、応答喪失・reload後は元キーlookupと元IDのGETで復旧する。現行権限と完全context、明示取消、入力保持を確認し、Ingestのcommitと清掃失敗も分離した。次はAI-02／SQL-12／13の通常WAPへの案内を整え、清掃失敗の照合・限定修正はM1残件として保持する。P6全体・M1本人受入は継続する。

2026-11-03 JST、[DB-P6-0R1](database-p6-0r1-capability-closure-audit-2026-11-03.md)で全89行の実装・既存証拠・通常導線を照合した。M1義務を維持する82行、既存計画の別track3行、範囲判断OPEN4行を整理し、OPENを必須免除へ変換しない。SQL-01の新規Database Worksheetを空SQLへ修正し、CMP-04のAI計画取得に関する古い説明を既存受入へ合わせた。123群4,880成功・34 skip、通常画面の追加・reloadと台帳・設定保全を確認した。次はING-03／TRF-06のreload後の元operation再接続を限定して実装する。P6全体・統合受入・M1本人受入は継続する。

2026-11-03 JST、[DB-P6-4C2D](database-p6-4c2d-foundation-controllers-2026-11-03.md)でstorage／catalogの管理対象3 Deploymentのcontroller記録を、SRE PlatformとComputeの独立手動snapshotへ接続し限定技術受入を完了した。未報告・実0・世代遅れ・surgeを区別し、取得時刻や件数からservice／SQL healthを保証しない。123群4,878成功・34 skip、両HTTP面と独立3 row照合、日英／390px／実200%、台帳・設定保全を確認した。次は89行の必須操作・実装・受入証拠・通常導線の閉鎖監査で残件を確定する。旧SRE全体・global Policy・P6全体／M1本人受入は継続し、実Compute伸縮はM1後とする。

2026-11-03 JST、[DB-P6-4C2C2](database-p6-4c2c2-foundation-baseline-2026-11-03.md)で既存Bear action・必須行・通常導線を照合し、管理対象の論理基盤6 Deployment＋bootstrap Jobの4宣言レビューを限定技術受入済みとした。現在Pの前後検査とfoundation照合、独立した手動3面snapshotを提供し、C2C1を維持する。121群4,819成功・34 skip、両HTTP面・独立sourceの40判定、日英／390px／実200%、台帳・設定保全を確認した。次はstorage／catalogの3 Deploymentに対する受動的controller観測を限定する。旧SRE全体・namespace-wide Policy・native8強制・P6全体／M1本人受入は継続し、実Compute伸縮はM1後とする。

2026-11-03 JST、[DB-P6-5G](database-p6-5g-compute-explanations-2026-11-03.md)でComputeの観測済み操作条件と停止状態の説明を既存panelへ追加し、限定技術受入を完了した。取得済み候補と現在認可・受付・成功を区別し、Chillだけの6区分条件、元要求の保持、停止原因不明と保存engine証跡の意味を説明する。API・取得契機を増やさず、無効な設定入力だけを従来の送信前検証に合わせてSaveへ反映した。119群4,752成功・34 skip、Workbench実statusの認可境界、日英／390px／実200%と保全を確認した。次はBears／Policyの既存action・必須行・通常導線を照合して残件を限定する。P6全体／M1本人受入は継続し、実伸縮はM1後とする。

2026-11-03 JST、[DB-P6-5F](database-p6-5f-compute-history-2026-11-03.md)で共有LOCALの保存済み操作履歴をComputeのP専用手動一覧へ接続し、限定技術受入を完了した。policy変更と自動休止、保存状態・時刻と実効果、照合と元要求の成功を区別する。118群4,722成功・34 skip、両実APIで144 ID／項目の照合、日英・390px／実200%と保全を確認した。現在権限・停止理由の説明などの残件、P6全体／M1本人受入は継続する。

2026-11-03 JST、[DB-P6-5E](database-p6-5e-platform-capacity-2026-11-03.md)で登録Nodeの容量・利用可能量をComputeのP専用手動観測へ接続し、限定技術受入を完了した。capacity／allocatableとメモリavailable推定、Node rootFS／ImageFS／ContainerFSを区別し、合算やPVC／S3 quotaの推定を行わない。116群（4,663成功・34 skip）、両実API、日英・390px／実200%と保全を確認した。履歴／停止理由等の残件、P6全体・M1本人受入は継続する。

2026-11-03 JST、[DB-P6-5D](database-p6-5d-platform-usage-2026-11-03.md)で固定LOCALのCPU・メモリ使用量をComputeの手動観測へ接続し、限定技術受入を完了した。現在P認可、コンテナ所有関係・再起動とsample期間、設定requests／limitsとの区別、両実APIと日英・390px／実200%の通常画面、114群と修正後Workbench 1,092件・保全を検証した。容量・履歴などの残件とP6全体／M1本人受入は継続する。

2026-11-03 JST、[DB-P6-6E](database-p6-6e-table-drop-2026-11-02.md)でEXP-11を「カタログ解除・データ保持」として通常Explorerへ接続し、限定技術受入を完了した。正規の現在A権限、full9、Ref / headの確認と元keyのGET復旧を維持し、4表のACK・同名再作成保全・旧22ファイル保持を確認した。全体112群と最終backend / retirement / Workbenchの影響群、実API・通常画面・元台帳 / 設定保全を検証した。次はComputeの観測・管理を限定して進め、P6全体とM1本人受入は継続する。

[DB-P6-6D](database-p6-6d-table-lineage-2026-11-02.md)でEXP-13／14の保存WAP／dbt lineageをTableタブとExplorerの可視ヘッダーへ接続し、限定技術受入を完了した。確定証跡と版を固定しない定義、現在／過去、計画中モデルと物理Refを分離する。canonical 110群と最終Workbench 1,058件、両HTTP 40ケース、通常画面と台帳／設定保全を確認した。次はEXP-11のDrop導線を別単位で進め、P6全体とM1本人受入は継続する。

[DB-P6-6C](database-p6-6c-table-profiling-2026-11-02.md)でEXP-12の全行・全列NULL統計を通常Explorerへ接続し、限定技術受入を完了した。現在query権限、固定Snapshot、正確な件数文字列・NULL率、元key回復・取消を実装した。全体回帰4,460成功・34 skip、実2DB×3roleの8件と通常画面3件、63行／過去3行／空0行、再送・拒否・再起動後GET・保全を確認した。次はTable／Explorer lineageを限定単位で進め、P6全体とM1本人受入は継続する。

[DB-P6-6B](database-p6-6b-table-clone-2026-11-02.md)でEXP-10を通常Explorerへ接続し、限定技術受入を完了した。新しいSANDBOXへの単一CTAS・現在／過去Snapshot・元key回復・認可分離を実装し、全体回帰4,385成功・34 skip、実5コピーと値照合、拒否・reload回復・保全を確認した。EXP-10の後続にEXP-12を選定した。P6全体とM1本人受入は継続する。

[DB-P6-6A](database-p6-6a-result-save-contract-2026-11-02.md)でSQL-08のWorksheet返却済み結果のCSV／JSON／TSVファイル保存を実装し、限定技術受入を完了した（全体回帰4,328成功・34 skip、両HTTP面72要求・22成果物、ブラウザー実ファイルとcache喪失拒否、元行と設定の保全）。本人・保存role・現在contextと受領page列を照合し、JSON整数文字列とNULL-aware CSV／TSVで精度・NULLを保持する。表示filter／sortを保存範囲へ反映せず、Table全件Exportとは区別する。SQL-08の後続にEXP-10を選定した。P6全体とM1本人受入は継続する。

[DB-P6-4E](database-p6-4e-ai-settings-2026-11-02.md)でAI-06の共有設定・明示接続test・保存を通常Workbenchへ接続し、限定技術受入を完了した（全体回帰4,264成功・34 skip、両HTTP面84要求、実LOCAL疎通・日英ブラウザー、原設定と既存データの保全）。現在の真P・上限admin・選択global adminを検査し、DB閲覧・管理権限と分離する。共有設定の接続成功はDatabase推論の準備完了を意味せず、既存LOCAL推論の対応条件を維持する。AI-06の後続にSQL-08と残る探索操作を選定した。89項目全体とM1本人受入は継続する。

[DB-P6-4C2C1](database-p6-4c2c1-workload-baseline-2026-11-02.md)はAI-04／AI-05／CMP-05へP専用の固定LOCAL宣言基準レビューを加えた限定技術受入単位である。旧4項目をmet／not_metで示す範囲に限定し、8 admission policyやglobal policy全体、実行許可にはしない。C2C1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,232成功・34 skip、両HTTP面の実APIとWorkbenchの3面表示、最終保全PASS）。

[DB-P6-5C](database-p6-5c-contract-2026-11-02.md)はCMP-02へDB保存activityの手動一覧を追加したUIのみの限定技術受入済み単位である。既存AUD-03のself／database認可とeffective DB admin例外を保ち、P専用のCMP-05とは別に扱う。P5Cの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,188成功・34 skip、既存AUD-03の実APIとWorkbenchの実画面、最終保全PASS）。

[DB-P6-5B](database-p6-5b-platform-resources-2026-11-02.md)はconfigured container resourcesだけの限定技術受入済みの単位であり、能力の一括完了を意味しない。P5Bの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,166成功・34 skip、両HTTP面の実API・Workbenchの実画面、最終保全PASS）。

[DB-P6-4D1](database-p6-4d1-ai-audit-metadata-2026-11-01.md)は既存AUD-03へ保存済みAI要約metadataを追加した限定技術受入済みの単位である。`source=ai`と閉じた5種のaction、既存event形状・`links=[]`だけを使い、内容fieldを追加しない。最新保存要約であり、遷移監査ではない。現在のself／database認可を維持し、保存full9は内部整合を要するが現在revisionとの一致を要しない。platform scopeはAIを除外する。D1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,135成功・34 skip、Workbenchの実API・実画面、最終保全PASS）。 C2C／P5B・AI設定とP6-4全体／P6-5全体／P6-7／M1本人受入は継続する。

2026年11月1日。[DB-P6-0](database-full-capability-and-compute-plan-2026-10-28.md)で固定した89項目に、監査・共有LOCAL管理、SANDBOX VIEW・native TABLE・[LOCAL SQL説明](database-p6-4a1-local-sql-explanation-2026-10-31.md)・[TABLE／条件付きVIEW計画説明](database-p6-4a3-view-plan-explanation-2026-10-31.md)・[Query Doctorの最新SELECT読込](database-p6-4a4-latest-query-source-2026-10-31.md)・[保存済み計画の明示再説明](database-p6-4a5-saved-plan-explanation-2026-10-31.md)を反映した。[B1 SQL案生成](database-p6-4b1-sql-proposals-2026-11-01.md)と[B2 Manual WAP下書き引渡し](database-p6-4b2-wap-handoff-2026-11-01.md)は限定範囲を技術受入済みであり、[C1 Catalog Curatorのmetadata説明・説明文案](database-p6-4c1-catalog-proposals-2026-11-01.md)は限定範囲を技術受入済みである。[C2AのDB内SRE／Policy観測](database-p6-4c2a-context-observations-2026-11-01.md)は限定範囲を技術受入済みである。機能数は増やさず、M1の本人受入は未完了である。

VIEW計画は`logical_v1`の対応するnative／外部登録INVOKER VIEWに限り、再帰依存先も同一DB・現在の全schema query権限を必要とする。保存済みVIEW定義内で整数snapshotを指定するTABLEは、そのsnapshotのschemaが現行列構成と一致する場合に扱う。利用者の直接入力に対するVERSION対応は広げない。DEFINER・他DB参照・履歴schemaの列構成差分・timestamp／tag／branch等の非整数time travelは未対応である。固定の非ANALYZE EXPLAINを実行し、元SQLは実行しない。

最新SELECT読込はQuery Doctor入力だけを対象とし、本人と保存9項目contextが一致する対象の通常read-only queryを選ぶ。全参照schema権限と現在のidentityを再検査し、SQL・AI・Wake・catalogを呼ばない。元queryのID・時刻・状態は出典情報であり、UNKNOWN等の照合・取消・再実行は行わない。説明や計画取得は別の明示操作とする。

保存済み計画の再説明は、元A2／A3のCOLLECTED証拠と、元queryの保存値・現在台帳がともにFINISHEDであることが条件。元の説明がSUCCEEDED／FAILED／CANCELED／UNKNOWNの場合に、取得時点の安全な計画とmetadataを使う。本人・保存9項目contextと、凍結した全依存schemaの現在権限を再検査する。catalogのhead再取得・Engine・Wake・admissionは行わず、元UNKNOWNの解消やA2の新規query受付の解除とは分ける。新しい説明には独立した履歴・復旧・取消を持ち、そのUNKNOWNを自動再推論しない。

B1は明示選択した同一DBの1〜8件のTABLE／VIEWについて、現在の本人・保存9項目context・全source schema権限とResourceRefを検査し、依頼文の原文と許可された名前・列名・型だけをLOCALへ送る。VIEWは列metadataだけを扱い、A3の計画・実行条件を検証したとは表示しない。生成物は単一read-only SELECTの構文・参照範囲・policyを確認する下書きであり、列・型・業務上の正しさや実行可能性は保証しない。実行・editor書換え・WAP引渡しは含めない。B1を技術受入済みとした（全体回帰3,892成功・34 skip、2DB・3role実機・最終保全PASS）。

B2は成功済みB1案から、元contextと同一DB・同一E／A roleのSANDBOX実行先contextを明示指定して、通常WAPの新規未保存下書きを準備する。全選択schema権限を検査し、SQLで使用する現在のTABLE識別情報・列を確認する。使用VIEW・不確かなnamespace変換は拒否し、未使用VIEWの定義や依存を取得しない。明示GETと空の新規フォームへの取込はAI・SQL・Wake・モデル保存を行わず、以後の編集・保存・build・audit・A公開は既存WAPへ委ねる。B2の限定範囲を技術受入済みとした（全体回帰3,944成功・34 skip、2DBの実機・画面・最終保全PASS）。

C1はSQLワークシートから独立したcontextで、同じprimary schemaの安定したTABLE／VIEW 1件を明示選択する。現在の本人・保存9項目context・schema query権限とRef／UUID／列構造を確認し、名前・列名・型だけでLOCALの要約・表説明と最大8列の部分的な説明文案を作る。VIEWの定義・依存や既存コメントを入力へ含めず、A3の計画対応や業務上の意味を保証しない。元要求の復旧・履歴・取消を分離し、SQL・Wake・説明保存・catalog metadata変更は行わない。C1の限定範囲を技術受入済みとした（全体回帰4,020成功・34 skip、2DB・3roleの実API、adminの実画面、最終保全PASS）。P6-4全体／P6-5／P6-7／M1本人受入は継続する。

C2Aは各Bearで明示する独立contextについて、現在の本人・保存9項目context・primary schemaのquery権限が一致する本人のquery／AI記録だけを各最大20候補の窓から取得する。項目はID・kind・保存状態・保存時刻の5項目だけとし、省略数と古い候補の有無を示す。queryのFINISHEDとAIのSUCCEEDEDは変換せず、UNKNOWNも照合しない。Policyは現在権限・SQL modeの上限とplatform能力flagだけを示し、個別SQLの承認にしない。全roleでplatform source・SQL・LLM・書込みを呼ばず、旧SRE／Policy検査は開放しない。重点backend335件・UI889件、実API・admin実画面と復元を確認し、最終8ファイルで全体回帰を完了した。C2Aの限定範囲を技術受入済みとした（全体回帰4,073成功・34 skip、2DB・3実行ロールの実API、未付与権限の拒否、adminの実画面、最終保全PASS）。P6-4全体／P6-5／P6-7／M1本人受入は継続する。

[DB-P6-4C2B](database-p6-4c2b-platform-observations-2026-11-01.md)では、SRE／PolicyのP専用固定LOCAL観測を実装した。現在のaccountとprincipal双方のP権限、上限admin、選択adminをsource取得前後に確認し、DBやgrantを利用条件にしない。固定2 Deploymentのdesired／ready要約、過去のengine検証日時、保存AutoChill設定、LOCALの保存admission件数だけを扱う。desired／readyの一致や両方0をSQL health・完全停止・資源解放の証明にせず、保存日時を現在の稼働保証へ変換しない。明示GETだけで書込み・SQL・LLM・操作を行わず、C2AのDB観測はplatform sourceを読まないままとする。C2Bの限定範囲を技術受入済みとした（全体回帰4,109成功・34 skip、両HTTP面の実APIと権限拒否、Pの実画面、復元と最終保全PASS）。P6-4全体／P6-5／P6-7／M1本人受入は継続する。

[DB-P6-5A](database-p6-5a-compute-observations-2026-11-01.md)では、Compute画面へのP専用固定LOCAL観測の読取り統合を実装した。受入済みC2Bの明示GET・strict DTO・global P cacheを再利用し、新しいAPIやDB context／grantの前提を追加しない。現在の真P・上限admin・選択adminを条件とし、完了snapshotと観測時刻をSRE／Policy／Computeで共有する。画面を開くだけでplatform観測を取得せず、既存Warehouse操作へも接続しない。desired／ready要約・過去のengine証拠・保存AutoChill設定・限定admission件数を現在healthや完全なPolicy監査へ読み替えない。P6-5Aの限定範囲を技術受入済みとした（全体回帰4,122成功・34 skip、両HTTP面の実APIと権限拒否、Computeの実画面、最終保全PASS）。CMP-05の接続はこの限定観測だけを対象とし、旧検査・global auditとP6-4全体／P6-5全体／P6-7／M1本人受入は未完了とする。

## 利用条件と表示の意味

- **V／E／A**は`bi_viewer`／`data_engineer`／選択中のDB `admin`。利用role上限、現在のaccount、DB grantの上限をすべて満たす必要がある。
- **P**は実accountのplatform管理権限、最大role `admin`、選択role `admin`のすべてを満たす状態。Aの選択だけではPにならない。実platform管理者がAを選ぶ場合の既存DB grant例外は維持する。
- **有効context**は本人、DB／Schema、保存role、LOCAL、binding／generation／grant revisionが現在の認可と一致すること。DBはREADYで、desired／applied bindingが一致する。未知・不許可・非READYのDBは同様に隠し、一覧にない理由を推測して開示しない。
- **対応**は実装された入口があるという意味であり、対象未選択や権限失効でも操作できるという意味ではない。**未接続**はDatabase用の入口がなく、roleを管理者にしても解除されない。**別計画**は追加の製品契約が必要な機能である。

| 表示の分類 | 判定と案内 | 利用者の次の操作 |
| --- | --- | --- |
| 機能未接続 | その機能名とDatabase対応状況を表示。古いroute／buttonの拒否を保持 | 対応済みのSQL・WAP・実行履歴などへ移動 |
| 既知のrole条件不一致 | 選択role上限、A／E条件、P条件が不一致 | 許可された実行roleを明示選択。存在を隠したDBの権限不足とは断定しない |
| 対象未選択 | 操作自身のDB／Schema／Table等が未選択 | そのパネルの対象を選ぶ |
| 確認中・context利用不可 | 認可読取り中、古い版、失効、非公開の対象不在等 | context更新・明示再選択。古い結果を再表示しない |
| 利用可能 | 実装対応、現在の認可、操作自身のcapability、入力、状態の全条件が成立 | 既存の確認・送信操作へ進む |

この分類は案内の意味を固定するもので、新しい認可方式ではない。UIは既存の`dbReason`／`dbExplorerReason`と各DataOpsの判定を使う。APIは要求ごとに再認可する。`manage=False`はplatform管理機能の包括許可ではなく、DB内admin操作の全拒否を意味するものでもない。

## 所有と実行記録の共通契約

| 記号 | 入力と出力 | 所有・監査・回復 |
| --- | --- | --- |
| U | ローカルの表示・下書き・明示選択→表示更新 | ブラウザーの下書き。role上限変更で保存済み実行roleを黙示変更しない |
| Q | SQL、mode、行上限、query／worksheet ID、context→query・page・結果 | 本人と受理時contextへ固定。結果・履歴を再認可。取消は元のidentityを使い、UNKNOWNを再送しない |
| X | Schema／Table Ref、detail、Snapshot、format→metadata／preview／artifact | Explorer独自context。実Table identityとincarnation、Snapshotを検査。exportは本人・元operationに固定しdownload時も再認可 |
| I | 入力bytes、型、format、mode、locator／Ref、key、context→preview／取込operation | 入力変更で再preview。受理済みpayloadを固定。本人と元contextで照会・取消し、commitと後続記帳失敗を分ける |
| W | DB内model ID／version、source Ref、build／audit／publication ID→WAP metadata／公開操作 | **modelとbuild／audit／publication metadataはDB内で共有**し、actorを記録。EからAへの明示引渡しが可能。実行operationは呼出者へ固定 |
| J | 版管理したtyped steps、引数、file、context→Job定義／run／step履歴 | owner＋保存role＋DB／Schema／context。CASと受理版固定。元runの取消・証拠に基づく照合 |
| S | 宣言済みlocal root、設定、file、target、context→Stage／preview／scan／export | ownerとDB／context。内容dedupと設定版を保存。UNKNOWNを再取込しない。receiptは完成した出力のみ |
| M | WAPの明示model版、監視source、MARTS target、context→Mart／check／refresh履歴 | ownerとDB／context、定義revision。監査済みSnapshotを公開。基準保存と公開commitを区別 |
| T | Job／Stage／Martの保存版、予定、timezone→予定／tick／alert | ownerと保存role／対象版。既存schedulerのみ。既定無効、競合・遅延は既存skip方針。失効・UNKNOWNで停止 |
| A | Pと管理context、期待revision、key、対象→管理operation／状態 | 作成root・actor・環境、CASと現在権限。登録／受付と実READYを区別。UNKNOWNは同じ要求で照合 |

## 全操作の対応表

入口欄のAPI群はHTTP-only CLIからも利用する。Qは送信時のWorksheet contextを保持し、I／W／J／S／M／Tは各パネル自身のcontextを持つ。受理済み操作を画面の現在選択へ付け替えない。未接続の行はDB-P6以降の残件として保持し、本一覧で対象から落とさない。

| ID | 操作と入力から出力 | 状態・利用条件 | 所有・記録・回復 | 入口または後続 |
| --- | --- | --- | --- | --- |
| GLOBAL-01 | 言語切替 | 対応、認証済み利用者 | U、設定を保存 | ヘッダー |
| GLOBAL-02 | 利用role上限の選択 | 対応、account上限以内 | U、各保存roleは保持 | ヘッダー |
| SQL-01 | Worksheet追加・選択・閉じる | 対応。新規Database Worksheetは空SQLで開始し、選択中の実行コンテキストを継承。既存下書きと明示SQL引数を保持。最後の1枚は保持、表示名は既存の自動採番 | U、独自下書き | SQLワークシート |
| SQL-02 | DB／Schema／role／mode／LOCAL／行上限、更新・再選択 | 対応、有効contextと許可mode | Q、明示再選択 | SQL、`/api/v1/databases`／schemas |
| SQL-03 | SQL本文／選択部分を実行 | VはMARTS読取、Eは4Schema読取とRAW／SANDBOX書込、Aは許可admin-write | Q、SQL guardを維持 | `/api/v1/queries` |
| SQL-04 | page取得・poll・実行状態 | 対応、元queryの現在認可 | Q、部分・打切り・UNKNOWNを区別 | query pages |
| SQL-05 | query取消 | 対応、元本人のcleanup契約 | Q、失効後の取消と結果閲覧を区別 | query cancel |
| SQL-06 | 最近の履歴・詳細・復元 | 対応、本人と許可context | Q、復元だけでは実行しない | query history／inspect |
| SQL-07 | table／chart、filter、sort | 対応、許可された返却済み行 | U＋Q、失効後は消去 | 結果表示 |
| SQL-08 | Worksheet結果CSV／JSON／TSV保存 | 対応、受領済み連続pageの全行。Table全件Export／clipboardとは別 | 本人・元role・現在full9とpage列を保存前後に検査。最大4096 page／16 MiB。JSON整数文字列・NULL-aware CSV／TSV。cache消失は拒否し再実行しない | [DB-P6-6A限定技術受入](database-p6-6a-result-save-contract-2026-11-02.md)、`/api/v1/queries/{id}/results/export` |
| SQL-09 | SQLサンプルの導線 | Worksheetに接続先確認の読取り下書きを追加。EXP-07は選択した表の読取りSQLを新規Worksheetに生成 | 現在のDB／スキーマ／実行ロールを照合。旧3種のサンプルの代替可否は所有者の範囲判断 | Worksheetの接続先確認SQL、Data Explorerの新しいSQLを開く |
| SQL-10 | Copilot SQL生成 | B1の未実行案に条件付き対応。B2のWAP下書き引渡しは限定技術受入済み | V／E／Aで許可metadataによるLOCAL案を生成。B2は成功済み本人案・元contextと同一DB／同一E／A roleのSANDBOX実行先・使用TABLEが条件。worksheet／Doctor本文や結果を変更せず、取込後は通常WAPで明示編集・保存する | DB-P6-4B1技術受入済み、B2限定技術受入済み |
| SQL-11 | Copilot Explain | SQL説明・TABLE／条件付きVIEW実行計画の説明・保存済み計画の明示再説明に対応 | V／E／A、有効なSQLワークシートcontext・再帰依存を含む全schemaの現在のquery権限・LOCAL AI。新規VIEW計画は`logical_v1`の対応INVOKER・同一DBに限定し、固定の非ANALYZE EXPLAINは通常受付・Wakeを利用。再説明は元A2／A3のCOLLECTED＋FINISHED証拠と凍結contextが条件で、Engine等を呼ばない。これらの説明操作で元SQLを実行しない | DB-P6-4A1／A2／A3／A5対応 |
| SQL-12 | Copilot Stage and Audit | 旧Stage／Auditボタンは未接続。B2の条件付き下書き引渡しと、B3の通常Manual WAPへの案内に対応 | 移動は独立context・編集中draftを保持し、提案取込・保存・build・auditを行わない。通常WAPのW条件と明示操作を維持 | 手動WAP、DB-P6-4B2、[B3の限定受入](database-p6-4b3-wap-guidance-2026-11-03.md) |
| SQL-13 | Copilot Publish | 旧Publishボタンは未接続。通常WAP公開とB3の案内に対応 | Aによる明示WAP公開を維持。案内は公開を行わず、rollbackは既存publicationに結びつく | 手動WAP、[DB-P6-4B3](database-p6-4b3-wap-guidance-2026-11-03.md) |
| EXP-01 | DB／Schema／Table／Viewツリー、検索・page・更新 | 対応、V／E／Aの許可Schema | X、Explorer独自context | inventory／objects |
| EXP-02 | DB／Schema概要、identity、columns | 対応、許可browseと安定Ref | X、再作成した同名Tableは別identity | object detail |
| EXP-03 | 最新データpreview | 対応、許可queryとpreview capability | X＋Q、query pages | object preview |
| EXP-04 | Snapshot一覧 | 対応、読取可能な安定TABLE。Viewは対象外 | X、metadata取得 | object snapshots |
| EXP-05 | Snapshot／Time Travel preview | 対応、選んだSnapshotとpreview capability | X＋Q、latestへの代替なし | Snapshot preview |
| EXP-06 | DDL表示 | 対応、Schemaの**write capability**が必要 | X＋Q、browseのみでは不可 | object ddl |
| EXP-07 | 新しいSQL、SQL名挿入、Explorer Schemaの明示適用 | 対応、許可context | U＋X、新しい／元Worksheetを区別 | 探索の明示ボタン |
| EXP-08 | Snapshot固定Table全件Export CSV／JSONL | 対応、読取可能な安定TABLE。許可Vも可 | X、元Snapshotと件数・内容 | `/api/v1/table-exports` |
| EXP-09 | Export照会・取消・新規・receipt／download | 対応、本人と元context | X、同一operation／key、完全出力のみ | table export／dataops |
| EXP-10 | 現在／過去Tableのsandbox clone | 対応。同DB・固定Snapshot→新規SANDBOX TABLEの物理コピー | E／A。source queryとtarget writeを分離。元key回復・単一CTAS。上書き／自動DROPなし、UNKNOWNは観測のみ | [DB-P6-6B限定技術受入](database-p6-6b-table-clone-2026-11-02.md)、Explorer→サンドボックスに複製 |
| EXP-11 | 探索のDrop Table | 明示確認付きのTABLE全体のカタログ解除・データ保持。単一非purge要求、元Ref retirement、ACK／UNKNOWNとID未受領時のGET復旧を分離 | 現在Aの実効DB管理権限（既存P例外を含む）、full9・登録済みraw／sandbox／staging／marts（sql_name == purpose）のquery／write／admin・Ref／headを検査。owned stage／backup／VIEW、物理purge・cascade・Undropは対象外 | [DB-P6-6E](database-p6-6e-table-drop-2026-11-02.md) |
| EXP-12 | profiling／NULL統計 | 対応。固定Snapshotの全行・全トップレベル列の実COUNT | V／E／Aの現在query権限。既存ViewerのMARTS制約維持。件数文字列・NULL率、0行は率—。VIEW未対応。元key回復と失効後消去 | [DB-P6-6C限定技術受入](database-p6-6c-table-profiling-2026-11-02.md)、Explorer→プロファイル & NULL統計 |
| EXP-13 | Table lineage | Tableタブ／Explorerヘッダーから、保存WAP／dbtの直接upstream・downstreamを読取り表示。確認済み証跡と定義、Snapshot／VIEW版の現在・過去を分離 | E／Aと全endpointの現在query権限。保存記録の限定範囲であり、clone・任意SQL推定・全catalog網羅は対象外 | [DB-P6-6D](database-p6-6d-table-lineage-2026-11-02.md) |
| EXP-14 | dbtモデルlineage | 変換画面の保存planに加え、Explorer選択対象から関連する保存定義と確認済み証跡へ接続。計画中モデルと確定物理Refを区別 | Eは本人・保存role、Aは同DBの認可済みmetadata。定義は版を固定しない依存で、実行時固定版は確認済み証跡へ表示 | [DB-P6-3B](database-p6-3b-implementation-2026-10-29.md)、[C2A](database-p6-3c2-sandbox-views-2026-10-30.md)、[DB-P6-6D](database-p6-6d-table-lineage-2026-11-02.md) |
| ING-01 | browser file／drop、型・delimiter等、preview | 対応、E／AかつRAW／SANDBOXのwrite | I、入力と設定のfingerprint | ファイル取り込み |
| ING-02 | create／replace／append／upsertの明示確認 | 対応、previewと現在入力が一致 | I、固定payload／key | `/api/v1/ingests` |
| ING-03 | 取込照会・取消・回復情報 | 通常Manual Ingestから元本人／contextの保存済み元処理へ再接続・照会・取消。実commitと清掃失敗を分離表示。6F-R1で新規コミット済みManual Ingestの所有ステージを非purge登録解除 | I、reload後の再接続はGETのみ（明示取消は既存API）。404／UNKNOWNから元要求を再POSTしない。元の6F清掃失敗は証拠を保持。6F-R1の登録解除は物理ファイルを保持し、確定失敗・未確定をコミットと分離 | Manual Ingest／`/api/v1/dataops/by-key`／元ID／[6F-R1 ステージ清掃](database-p6-6f-ingest-cleanup-2026-11-04.md) |
| ING-04 | 旧server path／固定catalogフォーム | 非表示・未接続 | 新しいbrowser file入口を使用 | 旧ingest routeは拒否 |
| ING-05 | Stage一覧・新規・Load・再Load・保存 | 対応、A、本人の同DB Stage | S、宣言済local rootとCAS | `/api/v1/stages` |
| ING-06 | Stage file一覧・preview・単件取込確認 | 対応、上記条件と入力確認 | S＋I、設定／bytesを固定 | Stage files／ingest |
| ING-07 | Stage scan・照会・scan履歴 | 対応、A、本人のStage、atomic append／keyed upsert | S、内容dedup、未知効果を保護 | Stage scans |
| ING-08 | Stage Snapshot export・receipt・download | 対応、A、元owner／context | S＋X、安定sourceと完成出力 | Stage exports |
| ING-09 | Stage operation照会・取消・履歴 | 対応、元owner／context | S、元の受理identity | Stage operation |
| ING-10 | Stage予定の作成・Load・保存・有効化・停止・tick | 対応、A、atomic append／keyed upsert | S＋T、既定無効、保存target版 | schedules |
| ING-11 | 外部Stage／connector同期 | 別計画。local Stageと区別 | 外部接続の契約が必要 | 後続scope |
| TRF-01 | WAP model一覧・版Load・版保存・source alias／target | 対応、E／A、有効contextと同DB Ref。B2の新規下書き取込に条件付き対応 | W、DB共有metadata。B2は空の新規フォームだけへ取込み、model ID・target・品質を確認後に既存CAS版保存 | `/api/v1/wap/models`、DB-P6-4B2 |
| TRF-02 | 保存版のWAP build | 対応、E／A | W、呼出者とimmutable版 | WAP build |
| TRF-03 | buildのaudit、build／audit／publication詳細 | 対応、E／A、同DB | W、E→Aのmetadata引渡し | WAP audit／details |
| TRF-04 | 監査済みbuildのpublish | 対応、A | W、監査Snapshotから明示公開 | WAP publish |
| TRF-05 | publication rollback | 対応、A | W、publication IDを固定 | WAP rollback |
| TRF-06 | WAP operation照会・取消・metadata ID取得 | 通常Manual WAPからstage／audit／publish／rollbackの保存済み元処理へ再接続・照会・取消・metadata ID取得 | W、元本人・許可role・完全context。reload後の再接続はGETのみ（明示取消は既存API）。実行はcaller-bound、metadataはDB共有 | Manual WAP／`/api/v1/dataops/by-key`／元ID |
| TRF-07 | Managed Mart新規・一覧・Load・保存・model版選択 | 対応、A、MARTS purpose、本人のMart | M、定義revisionと固定model版 | `/api/v1/marts` |
| TRF-08 | 変更確認、無条件／条件付き全件更新 | 対応、上記条件。変更確認はwatch sourceが必要 | M、source Ref／Snapshot集合 | Mart check／refresh |
| TRF-09 | Mart照会・履歴 | 対応、元owner／context | M、未知操作再送なし | Mart runs |
| TRF-10 | Mart予定のLoad・新規・保存・有効化・停止・tick | 対応、A、監視sourceあり | M＋T、保存版で実行 | schedules |
| TRF-11 | dbt一覧・model SQL・build／test | SQL／WAPのcompile・候補build／test・公開をAPI／CLI／Workbenchで提供。単一native TABLEと付属品質test・明示MARTS公開に加え、保存済みTABLE／EPHEMERAL全graphのRunTask／TestTaskを実装（C2C技術受入済み）。C2Dでgraph内TABLE単位の明示MARTS公開を技術受入済み。C2E1のnative VIEW graph構築・品質testを技術受入済み。C2E2のVIEW証拠付きTABLE公開・独立公開履歴を技術受入済み | SQL／WAPが既定で、TABLE／EPHEMERALとrun専用SANDBOX INVOKER VIEWに対応。native共通条件は元本人・READY v2 plan・同DB RAW source・TABLEは固定PARQUET・新規SANDBOX候補。`native_table`はRAW／展開済EPHEMERAL依存の1モデルと付属suiteをopt-inし、全品質成功後にAの固定preview・最低1行監査で同DB MARTS公開。`native_graph`はVIEW→TABLE・TABLE間依存を含む対応する全DAGと保存全generic test一式をopt-inし、source testも含む。品質未指定はNOT_RUN。非対応nodeを含むgraph・品質指定時の空suiteを拒否。VIEWを含むnative graphのTABLE公開は全VIEWの定義・UUID／version・作成query・推移依存を固定する。graph公開は全RunTask／TestTask成功・全非空suiteの厳密な0件成功・全候補証拠を条件に、Aの固定previewと最低1行監査を経て選択TABLEだけを同DB MARTSへ反映。他TABLEは別操作。正式VIEW公開・VIEW連鎖、incremental・並列化・候補直接置換・manual-WAP rollback統合は後続 | [DB-P6-3B](database-p6-3b-implementation-2026-10-29.md)、[C2A](database-p6-3c2-sandbox-views-2026-10-30.md)、[C2B4](database-p6-3c2b4-native-quality-tests-2026-10-30.md)、[C2B5](database-p6-3c2b5-native-publication-2026-10-31.md)、[C2C](database-p6-3c2c-native-graph-2026-10-31.md)、[C2D](database-p6-3c2d-graph-publication-2026-10-31.md)、[C2E1](database-p6-3c2e1-native-views-2026-10-31.md)、[C2E2](database-p6-3c2e2-view-graph-publication-2026-10-31.md) |
| TRF-12 | 旧dynamic WAP一覧・refresh widget | 未接続の旧widget | 手動WAP／Managed Martを使用 | 変換の新パネル |
| JOB-01 | Job新規・一覧・Load・再Load・定義／引数保存 | 対応、E／A | J、owner＋保存role＋context、CAS | `/api/v1/jobs` |
| JOB-02 | SQL／alert step | 対応、E／A、既存SQL policy | J、alertはread-only | typed Job step |
| JOB-03 | Ingest／WAP refresh／Table export step | 対応、A | J、型付きadapterのみ | typed Job step |
| JOB-04 | 手動run、名前付きfile入力 | 対応、保存roleで実行可能なowner | J、受理版／引数／fileを固定 | Job run |
| JOB-05 | 自動poll・照会・実行／step履歴 | 対応、元owner／context | J、元runの状態 | Job runs |
| JOB-06 | run取消 | 対応、元ownerとcleanup契約 | J、失効後も結果閲覧とは分離 | Job cancel |
| JOB-07 | UNKNOWN runの照合 | 対応、E／A、元owner／context | J、backend証拠。未解決子を無視しない | Job reconcile |
| JOB-08 | Job予定一覧・新規・保存・有効化・停止・tick | 対応、E／A、Job owner。runtime upload Jobは不可 | J＋T、保存定義版 | schedules |
| JOB-09 | interval／daily／once／weekly／monthly／timezone | 対応、予定の入力契約 | T、競合・遅延の既存方針 | schedules |
| JOB-10 | alert一覧・更新・確認 | 対応、E／A、元owner／context | T、確認と解消を区別 | alerts |
| JOB-11 | native Stored Procedure／CALL／DAG／任意command | 別計画。Jobと同義にしない | Routineの意味を先に確定 | DB-P9-2 |
| AI-01 | Query Doctor Bear | 明示SQL・TABLE／条件付きVIEW実行計画の説明、最新SELECTの入力読込、保存済み計画の明示再説明に対応 | V／E／A、現在のSQLワークシートcontext・全参照schemaのquery権限。説明にはLOCAL AIを使用。VIEW新規計画は`logical_v1`の対応INVOKERと再帰依存の同一DB権限が条件。最新読込は本人・保存9項目contextが一致する対象の通常read-only SELECTのみ。再説明は元A2／A3のCOLLECTED＋FINISHED証拠と全凍結依存schemaの現在権限が条件。読込から説明を自動送信せず、再説明で元SQL・planを再実行しない | DB-P6-4A1／A2／A3／A4／A5対応 |
| AI-02 | Data Engineer Bear | 旧Bear画面は未接続。B1 SQL案生成、B2の条件付き下書き引渡し、B3のCopilot／通常WAP／Managed Marts案内に対応 | 原案と実行先のcontextを分離する。B2は本人の成功済み案、同一DB／E・A role、許可されたTABLE・SANDBOX、未解決処理のない空formが条件。移動はdraftを保持し、取込・実行権限をAIへ渡さない。publish／rollback／refreshは既存WAP・Managed Martsの明示操作を維持し、旧任意Snapshot rollbackとの完全互換・旧操作全体の閉鎖は示さない | DB-P6-4B1、B2、[B3の限定受入](database-p6-4b3-wap-guidance-2026-11-03.md)／[C2C2の既存action対応表](database-p6-4c2c2-foundation-baseline-2026-11-03.md) |
| AI-03 | Catalog Curator Bear | C1のmetadata説明・説明文案に条件付き対応 | C1はV／E／A、独立contextと同じprimary schemaの安定TABLE／VIEW 1件、現在のquery権限とLOCAL AI。名前・列名・型による推定で、列文案は最大8列。C1は未適用の提案。検索・対象情報は既存Explorer、保存lineageはE／A・保存WAP／dbtのEXP-13／14の限定範囲へ対応付ける。旧横断raw dump・任意SQL推定・説明保存は未開放 | DB-P6-4C1限定技術受入済み／[C2C2の既存action対応表](database-p6-4c2c2-foundation-baseline-2026-11-03.md)（この単位の再受入ではない） |
| AI-04 | SRE Bear | C2AのDB内保存metadata・C2BのP専用固定LOCAL観測は限定技術受入済み。C2C1の宣言基準レビューも限定技術受入済み。C2C2の管理対象7 template・4宣言レビューも限定技術受入済み。C2Dのstorage／catalog 3 Deployment controller記録も限定技術受入済み。C2Eの最新20件の失敗Pawprints metadata参照を追加。C2Gで固定AgentHouse／LokiのHTTP応答を独立手動取得。C2Hで管理対象2コンテナの直近ログ診断概要を独立手動取得 | DB内はV／E／A、本人・保存full9・primary schema権限。P専用は現在のP account・principal・上限admin・選択adminを独立検査しDB／grant不要。C2C1は固定2 templateの全ordinary／initに旧4宣言条件だけを確認し、SQL health・runtime security・旧SRE検査を保証しない。C2C2は6 Deployment＋bootstrap Jobの全ordinary／initを独立手動snapshotで確認。現在真P・最大／選択adminとfoundationを前後検査し、実Pod・現在health・admission強制・global policy全体は示さない。C2Dは現在Pとfoundationを前後検査し、SRE Platform／Computeの2面で世代・desired・報告件数を手動共有。省略はnull、実0と区別し、surgeを許す。controller heartbeat・service／SQL health・操作許可は示さない。C2EはSRE Platform／ComputeのP専用手動sourceで、source側failed選択・8項目・最大20 records。DB対応付け、raw logs、health／全体Policy判断は示さない。C2Gは現在P／adminを確認し、各サービスの時刻とHTTP応答結果を表示。C2Hは固定socketとコンテナ所有情報を照合し、期間・行数・バイトを制限。件数と重大度・時刻を表示。C2KはSRE Platformで固定2コンテナの本文をP管理者が手動取得。C2Lは同じ2コンテナの重大度付きイベントを専用Lokiストリームへ手動投入し、直近10分の最大20件をSRE Platformで表示。C2Mは登録済み基盤のOPAを別の保持・検索範囲に追加。旧Kubernetes名前空間全体の投入範囲は継続 | DB-P6-4C2A／C2B限定技術受入済み、[C2C1契約・限定技術受入](database-p6-4c2c1-workload-baseline-2026-11-02.md)／[C2C2管理基盤の宣言レビュー](database-p6-4c2c2-foundation-baseline-2026-11-03.md)／[C2D controller記録](database-p6-4c2d-foundation-controllers-2026-11-03.md)／[C2E 失敗metadata](database-p6-4c2e-recent-failures-2026-11-03.md)／[C2G HTTP応答確認](database-p6-4c2g-sre-readiness-2026-11-04.md)／[C2H ログ診断概要](database-p6-4c2h-sre-logs-2026-11-04.md)／[C2K 生ログ取得](database-p6-4c2k-sre-raw-logs-2026-11-04.md)／[C2L Loki重大度検索](database-p6-4c2l-sre-loki-severity-2026-11-04.md)／[C2M 基盤OPA重大度検索](database-p6-4c2m-sre-foundation-logs-2026-11-04.md) |
| AI-05 | Policy Gatekeeper Bear | C2Aの現在context権限・C2BのP専用保存設定観測は限定技術受入済み。C2C1の4宣言レビューも限定技術受入済み。C2C2の管理対象7 template・4宣言レビューも限定技術受入済み。C2Fの登録済みnamespace内の全Pod宣言・4項目の独立手動参照も限定技術受入済み。C2Iで登録済みnamespaceの5種のコントローラーテンプレートを独立手動取得。C2JでPolicy Platformの貼り付けmanifest検査を追加 | DB内の現在schema権限・SQL mode上限とP専用scopeを分離。C2C1は現在真P・選択adminで手動取得し、nonRoot=trueかつUID未指定は旧ruleどおり許す。met／not_metとsource確認不可だけで、8 admission policy・global policy全体・個別SQL承認やaggregate allowを示さない。C2C2は6 Deployment＋bootstrap Jobの全ordinary／initを独立手動snapshotで確認。現在真P・最大／選択adminとfoundationを前後検査し、実Pod・現在health・admission強制・global policy全体は示さない。C2Fは通常・init・ephemeralを状態で除外せず、現在P／adminとFoundationを前後検査する。対象namespaceの全PodでありFoundation所有の証明ではない。空は評価対象なし、取得不能は全体unavailable。runtime強制・aggregate allow・全controller template／他namespace／任意manifestの完了を示さない。C2Iは通常／initの4宣言を表示。任意manifest・他namespaceの範囲判断は継続。C2JはP管理者、YAML／JSON単一のPod／5 controller、コンテナ種別・順序・4宣言の結果。複数文書とListは入力エラー | DB-P6-4C2A／C2B限定技術受入済み、[C2C1契約・限定技術受入](database-p6-4c2c1-workload-baseline-2026-11-02.md)／[C2C2管理基盤の宣言レビュー](database-p6-4c2c2-foundation-baseline-2026-11-03.md)／[C2F 実Pod宣言](database-p6-4c2f-live-pod-baseline-2026-11-03.md)／[C2I コントローラーテンプレート](database-p6-4c2i-controller-baseline-2026-11-04.md)／[C2J 貼り付けmanifest](database-p6-4c2j-manifest-advisory-2026-11-04.md) |
| AI-06 | AI provider／model／URL／key／timeout、接続test／保存 | 対応。共有設定、現在の真P・上限admin・選択global admin | DB／grant不要。key保護・接続先変更時のcredential解除。Database推論は既存LOCAL契約を維持し、疎通成功と区別 | [DB-P6-4E限定技術受入](database-p6-4e-ai-settings-2026-11-02.md)、`/api/v1/bears/ai-config`・`ai-test` |
| AUD-01 | Pawprints一覧・filter・status・更新 | 対応。P専用のglobal補助preview、最新100件以内 | Pが明示取得。DB／本人scopeへ帰属させず、耐久監査とは別表示 | DB-P6-1、旧API拒否は維持 |
| AUD-02 | 旧runtime query／load履歴画面 | 旧screenは未接続。query／load／actionの保存履歴対応表と8種のsource選択に対応 | source選択は未適用の下書きだけを変更し、検索は明示操作。現在のscope／DB／role認可と最新保存metadataの範囲を維持 | SQL／Job／WAP／Database Audit、[DB-P6-1A](database-p6-1a-history-wayfinding-2026-11-03.md) |
| AUD-03 | 統合Database監査の検索・詳細・download | 対応。保存metadataの検索・前後page・関連記録・JSON。D1のAI要約追加は限定技術受入済み | V／Eは本人・保存role・許可Schema、Aは対象DB。現在権限を再確認し、保存full9は内部整合のみ。Pのplatform scopeは基盤管理記録だけでAIを除外。本文・全遷移は対象外 | DB-P6-1実装契約、[D1保存AI要約](database-p6-4d1-ai-audit-metadata-2026-11-01.md)（既存event形状、source=ai、閉じた5種のaction、links=[]、内容field追加なし） |
| CMP-01 | ヘッダー／SQLの共有Warehouse状態・更新 | logical_v1は共有runtime観測を実装。技術受入済み | 物理状態・観測時刻・engine検証を分離し、replica数だけでSQL準備完了にしない | `/api/v1/warehouses/LOCAL/runtime`。旧Database profileは従来の観測のみ |
| CMP-02 | Compute panelの共有状態・停止blocker・保存policy・DB保存activity・共有操作履歴 | 共有runtimeは認証済みV／E／Aで技術受入済み。P5Cのquery／job_run保存metadata一覧、P5FのP専用保存済み共有操作履歴も限定技術受入済み。P5Gの観測済み操作条件・状態説明も限定技術受入済み | 共有状態と物理状態／engine検証を区別。P5Cは独立した手動self／database一覧で、本人・保存role・現在DB／Schema権限と既存effective DB admin例外を維持。P5Cはlive activity・SQL／結果・詳細／復旧・exportを含めず、CMP-05のP専用観測を開放しない。P5Fは現在P・最大／選択adminで4actionの保存要約を手動pageし、設定保存・no-op・照合を区別。現在の物理原因・liveness・全遷移を推定しない。P5Gは既存cacheの選択条件を説明し、現在認可・受付・成功や現在停止原因を確定しない。表示からの通信は増やさない | 共有LOCAL panel／runtime GET、[P5C契約・限定技術受入](database-p6-5c-contract-2026-11-02.md)／[P5F保存操作履歴](database-p6-5f-compute-history-2026-11-03.md)／[P5G操作条件・状態説明](database-p6-5g-compute-explanations-2026-11-03.md) |
| CMP-03 | quick／SQL／Computeの明示Wake／Chillと状態再検証 | logical_v1のP専用API／UI／HTTP CLIを実装。技術受入済み | 全DBへの影響を表示。元key／receipt保持、現在権限・admission／drain・全blockerを再確認。SQLは再送しない。P5GはChillだけの6区分条件と元要求の保持等の選択理由を説明し、元受付確認が同じkey・bodyでPOSTする場合があることを明示。無効Save入力の画面判定だけを既存送信前検証に合わせる | runtime操作／operation照会。保存一覧は[P5F](database-p6-5f-compute-history-2026-11-03.md)で限定技術受入済み（操作・復旧は追加しない）。旧Database profileは未対応／[P5G条件説明](database-p6-5g-compute-explanations-2026-11-03.md) |
| CMP-04 | Auto-Chill設定と許可済みSQLのAuto-resume | logical_v1で設定CAS、server側休止、SQL共通経路の自動復帰を実装。技術受入済み、cold 30回はp50 26.44秒・p95 26.92秒（30回すべて成功） | 設定はP、0は無効。自動起動は元のDB／role／intentを認可し、同一初回POSTを再送しない。queued取消・UNKNOWN保護を維持 | Workbench／native／CLI、既存DataOps・Job・Stage・Mart・対応済みdbt・予定の共通SQL経路。[A2](database-p6-4a2-plan-explanation-2026-10-31.md)／[A3](database-p6-4a3-view-plan-explanation-2026-10-31.md)のAI計画取得は通常admission／必要時のLOCAL自動起動に接続。全AI操作がengineを起動する意味ではない |
| CMP-05 | ComputeのSRE check／Policy audit | P5Aの固定観測・P5Bの設定資源・P5Dの実CPU／メモリ使用量・P5Eの登録Node容量／利用可能量は限定技術受入済み。C2C1の宣言レビューも限定技術受入済み。旧SRE check／任意Policy baselineは未接続。C2C2の管理対象7 template・4宣言レビューも限定技術受入済み。C2Dのstorage／catalog 3 Deployment controller記録も限定技術受入済み。C2Eの最新20件の失敗Pawprints metadata参照を追加。C2Fの登録済みnamespace内の全Pod宣言・4項目の独立手動参照も限定技術受入済み。C2Gで固定AgentHouse／LokiのHTTP応答を独立手動取得。C2Hで管理対象2コンテナの直近ログ診断概要を独立手動取得。C2Iで登録済みnamespaceの5種のコントローラーテンプレートを独立手動取得 | P専用の現在account・principal・上限admin・選択admin、DB／schema／grant不要。C2C1はSRE／Policy Platform・Computeの3面で独立cacheを明示共有し、固定2 templateの4宣言結果だけを表示。不正sourceは全体確認不可。既存C2B／P5B／P5Cと旧route拒否を維持し、C2C1はsecurity PASS・admission許可・usage／capacityや操作許可を示さない。C2C2は6 Deployment＋bootstrap Jobの全ordinary／initを独立手動snapshotで確認。現在真P・最大／選択adminとfoundationを前後検査し、実Pod・現在health・admission強制・global policy全体は示さない。C2Dは現在Pとfoundationを前後検査し、SRE Platform／Computeの2面で世代・desired・報告件数を手動共有。省略はnull、実0と区別し、surgeを許す。controller heartbeat・service／SQL health・操作許可は示さない。C2EはSRE Platform／ComputeのP専用手動sourceで、source側failed選択・8項目・最大20 records。DB対応付け、raw logs、health／全体Policy判断は示さない。C2Fは通常・init・ephemeralを状態で除外せず、現在P／adminとFoundationを前後検査する。対象namespaceの全PodでありFoundation所有の証明ではない。空は評価対象なし、取得不能は全体unavailable。runtime強制・aggregate allow・全controller template／他namespace／任意manifestの完了を示さない。C2Gは現在P／adminを確認し、各サービスの時刻とHTTP応答結果を表示。C2Hは固定socketとコンテナ所有情報を照合し、期間・行数・バイトを制限。件数と重大度・時刻を表示。C2KはSRE Platformで固定2コンテナの本文をP管理者が手動取得。C2Lは同じ2コンテナの重大度付きイベントを専用Lokiストリームへ手動投入し、直近10分の最大20件をSRE Platformで表示。C2Mは登録済み基盤のOPAを別の保持・検索範囲に追加。旧Kubernetes名前空間全体の投入範囲は継続。C2Iは通常／initの4宣言を表示。任意manifest・他namespaceの範囲判断は継続 | P5A／[P5B](database-p6-5b-platform-resources-2026-11-02.md)／[P5D](database-p6-5d-platform-usage-2026-11-03.md)／[P5E](database-p6-5e-platform-capacity-2026-11-03.md)限定技術受入済み、[C2C1契約・限定技術受入](database-p6-4c2c1-workload-baseline-2026-11-02.md)／DB-P6-4・5全体は未完了／[C2C2管理基盤の宣言レビュー](database-p6-4c2c2-foundation-baseline-2026-11-03.md)／[C2D controller記録](database-p6-4c2d-foundation-controllers-2026-11-03.md)／[C2E 失敗metadata](database-p6-4c2e-recent-failures-2026-11-03.md)／[C2F 実Pod宣言](database-p6-4c2f-live-pod-baseline-2026-11-03.md)／[C2G HTTP応答確認](database-p6-4c2g-sre-readiness-2026-11-04.md)／[C2H ログ診断概要](database-p6-4c2h-sre-logs-2026-11-04.md)／[C2I コントローラーテンプレート](database-p6-4c2i-controller-baseline-2026-11-04.md)／[C2K 生ログ取得](database-p6-4c2k-sre-raw-logs-2026-11-04.md)／[C2L Loki重大度検索](database-p6-4c2l-sre-loki-severity-2026-11-04.md)／[C2M 基盤OPA重大度検索](database-p6-4c2m-sre-foundation-logs-2026-11-04.md) |
| CMP-06 | XS／Sの実サイズ、Worker増減、用途別Warehouse | 未実装。休止・復帰の対応から伸縮完了を推定しない | 同一computeのaliasを維持し、容量・実資源・性能の計測後に採用 | DB-P7／8 |
| ADM-01 | DB管理一覧・選択・revision／binding情報 | 対応、P | A、Worksheetと独立 | `/api/v1/admin/databases` |
| ADM-02 | 論理DB作成、作成capability、初期access | 対応、P、logical_v1の準備条件 | A、root／actor／環境／key | admin database-creations |
| ADM-03 | 同じ要求の再発見・状態照会・固定payloadの再確認 | 対応、P | A、同じ要求を再発見 | 作成一覧／詳細 |
| ADM-04 | 作成の照合・resume・dispatch停止 | 対応、P＋元owner＋状態別action | A、logical／stagedの意味を分離 | creation recovery |
| ADM-05 | READYから新しいSQL | 対応、現在の許可role／schema | U＋Q、新しいWorksheet | 作成詳細 |
| ADM-06 | DB／Schema表示metadataのCAS更新 | 対応、P | A、SQL名／binding不変 | display API |
| ADM-07 | desired grant一覧・変更・取消 | 対応、P | A、desired／effectiveを分離。付与はbackend検証が条件 | grants API |
| ADM-08 | retire依存確認・要求・照会・照合 | 対応、P、非purge | A、既存UNKNOWNと履歴を保全 | retirement API |
| ADM-09 | 高度なregistry登録、構成plan／operator apply依頼、READY照合 | P＋準備済み材料／profileで条件付き対応。対象DBの登録、apply引継ぎ、適用済み世代の照合をDB管理から確認 | full APPLIEDの検証記録と現在のDB READY・適用bindingを照合。operator受入範囲は所有者判断 | [DB管理と検証記録](database-p6-7-integrated-acceptance-2026-11-04.md#adm-09の登録引継ぎready照合) |
| ADM-10 | staged plan確認・承認・再計画・取下げ・回復照合 | 条件付き、P＋元root owner＋staged modeの状態契約 | 直近の作成状態と操作対象を確認。限定回復と通常fullは個別承認。stagedの受入範囲は所有者判断 | [段階作成の操作と検証](database-p6-7-integrated-acceptance-2026-11-04.md#adm-10の段階作成) |
| ADM-11 | 物理purge、credential／PVCの削除 | 既存retireはカタログ、ストレージ、資格情報を保持 | M1での要否は所有者の破棄範囲判断 | 別計画 |

## 画面と実装の対応

安定したIDの案内をパネルへ置き、手動WAP等の追加順序によって説明対象が変わらないようにする。取り込みと変換、保存metadata監査は対応済み操作へ案内する。logical_v1の共有LOCAL管理は、旧Database profileの非対応と一時的な観測失敗を区別する。dbt、AI Bears、SRE／Policy検査は未接続のまま、理由と利用できる確認先を示す。

Worksheet結果保存とTable全件Exportの対象範囲・権限・cache条件を分けて案内する。profilingはTABLE・現在query権限・Snapshot条件と全行集計の範囲を示し、lineageの未接続案内とは分ける。対象identity未確認・対象／roleのcapability不足も区別し、未接続機能は管理者への変更を解決策にしない。

実装上の根拠は[認可](../../tools/local_experiment/request_auth.py)、[inventoryのcapability](../../tools/local_experiment/database_inventory.py)、[HTTP routeの拒否](../../tools/local_experiment/workbench_server.py)、[Workbench](../../tools/local_experiment/workbench/index.html)、[WAPのDB共有metadata](../../tools/local_experiment/manual_wap.py)、[Jobs](../../tools/local_experiment/db_jobs.py)、[Stages](../../tools/local_experiment/db_stages.py)、[Marts](../../tools/local_experiment/db_marts.py)、[予定とalert](../../tools/local_experiment/db_schedules.py)である。

## DB-P6-0の検証対象

全ナビゲーションと上表の操作について、対応済み／未接続の説明が正しいことを確認する。実行条件は各パネル自身で判定し、role切替、context欠落・失効、遅延応答、言語切替、refresh／reload後に誤って有効化しない。WAPとManaged Martへ到達でき、旧APIは引き続き拒否される。

本unitは監査API・dbt実行・Bears・Warehouse管理を新設しない。コード回帰、実ブラウザー、認可API、性能baselineの結果は[DB-P6-0実装と検証記録](database-p6-0-implementation-2026-10-29.md)へ記録する。

R1ではnative_graph公開の不確定COUNT監査だけをoperator CLIで復旧できる。元本人のplatform admin、取得可能なowner lease、凍結intent／全親子台帳、管理対象computeの停止証明を条件とする。公開・queryのUNKNOWNは保持し、画面には検証済みの実行閉鎖を別表示する。native_table／SQL-WAPや任意の不明書込みは対象外。[R1の契約と証拠](database-p6-3r1-dbt-audit-recovery-2026-10-31.md)。
