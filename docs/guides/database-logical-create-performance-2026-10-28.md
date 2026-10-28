# 論理DB作成の性能改善（2026-10-28）

[順序4の本人受入](database-logical-create-owner-acceptance-2026-10-28.md)は条件付き受入。
所有者は作成導線の改善を確認し、待ち時間の改善を継続条件とした。
稼働済み共有基盤で、作成開始からREADY・同じDB/rawの初SQL成功までp50≤2秒／p95≤5秒を暫定目標として維持する。

## 改善前の計測

- 直前の通常CLI4回は初SQL中央値6.820秒、範囲6.811〜6.978秒。目標は未達。
- 今回の通常CLI6回は初SQL中央値6.339秒、最大6.751秒。READY観測中央値5.797秒、受付中央値1.162秒。既存READY数19〜24。
- 通常CREATEごとに変わっていないpasswordのhashも再計算していた。解析用Pythonの純CPU実験は26件で1.528秒だったが、APIのPython環境とは異なるため短縮予測へ直接使わない。
  APIと同じ環境の工程計測では、再起動直後41回のPBKDF2が0.855秒、次のCREATEは新規1回のみ0.021秒となった。
- 全compute nodeのmaterial検証で、先頭nodeを基準用と比較用に2回読む重複がある。

計測は起動済みの共有基盤C、`p26-admin/admin`、初期grant追加なし、新しいDB名で実施する。
初SQLは対象DBの`current_catalog/current_schema`の実問い合わせで、内部管理identityによる代用はしない。
READYはCLIの250ms待ちとSHOW処理を含む観測時刻であり、内部完了の純時間とは分けて扱う。
少数標本のp95は母集団の達成証明とせず、標本数・中央値・最大値を併記する。
秘密を含む応答と計測材料は `var/shirokuma/adr-0038-performance-2026-10-28/private` と
`var/shirokuma/adr-0038-performance-round2-2026-10-28/private` に保管する。

## 改善の境界

現在の認可、account/grantの失効、immutable credential参照、全nodeへの反映、実engine検証、
durable intentとUNKNOWN時の再送拒否を維持する。
性能改善後も、条件付き受入を無条件受入へ変更しない。

## 実装と実機結果

- APIのlifetime内で派生password verifierを最大256件再利用する。keyはcredential参照・利用者・基盤revision・hash方針・process内HMACを含み、raw passwordや認可判定はcacheしない。終了時に破棄する。
- 全nodeで4ファイルと全credential参照を検査することを保ち、先頭nodeの重複readを削除。node単位の読取りは最大2並列、基盤healthの独立した読取りは最大3並列にした。書込みとjournalの順序は維持する。
- READYからSQLを開く際の重複context refreshを1回にした。fresh session・inventory・schemaの再確認を維持し、待機中のrole／worksheet変更後にfocusを奪わない。
- 単一context・inline資格情報のkubeconfigを毎dispatchで直接読む。曖昧な形式は従来のkubectlへ戻し、CA・client資格情報・Node／namespace UIDの照合を維持する。解析結果やGET結果はcacheしない。
- logical modeのBasic認証では、成功したPBKDF派生値だけをprocess内で最大256件再利用する。process固有HMACに現在hash・salt・方針・account・参照元を結び、現在hashとの比較とaccount／role／grantの読直しを毎回行う。600k反復とlegacy認証は維持する。
- 基盤のユーザー・Trino版・coordinator／worker数を1本の実SQLで確認。対象identityとschemaのpositive照会はdefault transport時だけ最大2並列とし、全join・両成功後にnegative→storage→runtimeの検証を行う。
- 初期grantが空の場合だけ未使用のprincipal一覧取得を省略。CLIの状態照会は現在の環境・modeの名前から詳細を1GETで取得し、全rootの描画を外した。新CLIは対応serverと同版で使い、旧一覧・IDルートは保持する。

通常CLI、起動済み基盤C・warehouse、初期grantなしで、各回に新しいDBを作成した。
計測中に回帰検査を並行実行していない。工程計測用の一時起動と、受付へ到達しなかった起動不備の試行は比較から除外した。

| 条件 | 回数 | 開始時の既存READY数 | 初SQL中央値 | 初SQL最大値 |
| --- | ---: | ---: | ---: | ---: |
| 改善前 | 6 | 19〜24 | 6.339秒 | 6.751秒 |
| verifier再利用・重複read削減後 | 8 | 26〜33 | 5.796秒 | 6.075秒 |
| 読取並列化を含む改善後 | 8 | 37〜44 | 5.619秒 | 5.890秒 |
| 上記の継続改善後 | 20 | 118〜137 | 2.700秒 | 2.770秒 |

最新20回は全成功、初SQLのnearest-rank p95は2.759秒、READY観測中央値2.460秒、受付中央値0.328秒。
前回の初SQL中央値5.619秒に対して約52%短い。標本内のp95は5秒以内だが、**p50 2秒は未達**である。
API再起動直後は、既存READY117件で初SQLまで5.220秒だった。初回の計算・読込み費用も残る。
再起動直後と連続作成を分け、少数標本のp95を母集団の達成証明にはしない。
DB数とidentity数も増えた条件なので、上表を同じDB数での厳密なA/B比較とは扱わない。

通常CLIで再起動し、profile指定の環境変数なしで新経路を選択、registry・認証参照の不変を照合した。
実APIで、確認用ユーザーへの権限付与後の照会成功、別DB照会拒否、取消後の照会拒否、現在のaccount認証の有効性を確認。
他account・他grantは変更せず、受入用2行、既存1万行、別DB同名tableの7/42も保持した。
実ブラウザでREADYから正しいDB/rawの新しいSQLを開けること、元worksheetの本文・実行先を保持することを再確認した。
継続改善後は、warm認証からroleの降格・account無効化／削除・password変更を行い、旧要求の拒否と復元後の成功を実APIで確認した。
対象grantの取消、別DB拒否、既存データ保持を含む25項目が成功。ブラウザでも閲覧者の実行無効・本文保持と、
管理者へ戻して接続を更新した後の新しい2行の結果を確認した。いずれもagentの技術検証で、本人の条件付き受入は変更しない。
最終コードでは名前照会とID照会の詳細一致、閲覧者・不正な名前／queryの拒否、新しいDBへの512行の実テーブル作成・集計を確認。
追加20回のCREATE後も元のgrantは取消済みのままで、既存2行・1万行・7/42を再確認した。

対象の実engine検証だけのread-only比較では、positive照会の並列化により中央値0.438秒から0.347秒となり、全proofが成立した。
全体には基盤health、native SPIのread／resolve・公開、実engine検証とCLI観測が残る。
次は4ファイルと全秘密参照の実SPI検証を各compute nodeにつき1回の観測へまとめる方式を検討する。JARは基盤のpin対象なので、
稼働中JARの差替えで済ませず、新しい基盤revisionの管理経路または別基盤で実証する。READYの早出しは採用しない。

## 継続改善前の回帰検査

標準のローカル回帰と設計検査は合計2,814件中、成功2,781件・skip33件。設計整合・改行・末尾空白・秘密ファイル名の検査も通過した。
初回は既存の反映確認テストの差し替え関数が新しいkeyword引数を転送せず停止したため、その2行だけを修正した。
READY・durable leaf・全artifact lock・readbackのassertは維持し、該当logical群316件を再検査。
通過済み769件は繰り返さず、残りの標準43コマンドを完了した。製品コードの追加変更はない。
agentによる最終ブラウザ確認でも、通常URLの元worksheet12で `owner_accept_20261028/raw` の読取専用SQLから `1/alpha`、`2/beta` を取得した。

## 継続改善後の回帰検査

標準回帰・設計検査は合計2,853件中、成功2,820件・skip33件。設計整合・改行・末尾空白・秘密ファイル名の検査も通過した。
システムPythonにPyYAMLが無かったため、Makefileの`PYTHON`へ実APIと同じDataOps venvを指定した。
全体回帰で見つけた旧SQL定数への参照は、cold wakeの1テストだけを1SQL・4列へ追随させた。
2台の登録待ち・read-only・cleanup等のassertを保ち、該当76件とProvider152件を確認。通過済み1,792件は保持し、残りの標準19コマンドを完了した。
最終APIへ反映後も、元worksheet12の同DB/raw・管理者・読取専用で新たに2行を取得した。本人受入は条件付きのままとする。
