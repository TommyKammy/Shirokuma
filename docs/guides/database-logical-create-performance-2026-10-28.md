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
秘密を含む応答と計測材料は `var/shirokuma/adr-0038-performance-2026-10-28/private` に保管する。

## 改善の境界

現在の認可、account/grantの失効、immutable credential参照、全nodeへの反映、実engine検証、
durable intentとUNKNOWN時の再送拒否を維持する。
性能改善後も、条件付き受入を無条件受入へ変更しない。

## 実装と実機結果

- APIのlifetime内で派生password verifierを最大256件再利用する。keyはcredential参照・利用者・基盤revision・hash方針・process内HMACを含み、raw passwordや認可判定はcacheしない。終了時に破棄する。
- 全nodeで4ファイルと全credential参照を検査することを保ち、先頭nodeの重複readを削除。node単位の読取りは最大2並列、基盤healthの独立した読取りは最大3並列にした。書込みとjournalの順序は維持する。
- READYからSQLを開く際の重複context refreshを1回にした。fresh session・inventory・schemaの再確認を維持し、待機中のrole／worksheet変更後にfocusを奪わない。

通常CLI、起動済み基盤C・warehouse、初期grantなしで、各回に新しいDBを作成した。
計測中に回帰検査を並行実行していない。工程計測用の一時起動と、受付へ到達しなかった起動不備の試行は比較から除外した。

| 条件 | 回数 | 開始時の既存READY数 | 初SQL中央値 | 初SQL最大値 |
| --- | ---: | ---: | ---: | ---: |
| 改善前 | 6 | 19〜24 | 6.339秒 | 6.751秒 |
| verifier再利用・重複read削減後 | 8 | 26〜33 | 5.796秒 | 6.075秒 |
| 読取並列化を含む改善後 | 8 | 37〜44 | 5.619秒 | 5.890秒 |

改善後の範囲は5.326〜5.890秒。基盤は稼働中でもAPI再起動直後の最初の1回は6.537秒だった。
process cacheの初期計算は残る。少数標本のnearest-rank p95は5.890秒で、中央値2秒／p95 5秒の暫定目標は引き続き未達。
DB数とidentity数も増えた条件なので、上表を同じDB数での厳密なA/B比較とは扱わない。

通常CLIで再起動し、profile指定の環境変数なしで新経路を選択、registry・認証参照の不変を照合した。
実APIで、確認用ユーザーへの権限付与後の照会成功、別DB照会拒否、取消後の照会拒否、現在のaccount認証の有効性を確認。
他account・他grantは変更せず、受入用2行、既存1万行、別DB同名tableの7/42も保持した。
実ブラウザでREADYから正しいDB/rawの新しいSQLを開けること、元worksheetの本文・実行先を保持することを再確認した。

残る主な時間は、受付前の現行基盤health、native SPIのread／resolve、実engineでの権限・schema・node検証とCLI観測である。
次の改善も、確認の省略やREADYの早出しでなく、これらのI/Oをまとめられるかを検証する。

## 回帰検査

標準のローカル回帰と設計検査は合計2,814件中、成功2,781件・skip33件。設計整合・改行・末尾空白・秘密ファイル名の検査も通過した。
初回は既存の反映確認テストの差し替え関数が新しいkeyword引数を転送せず停止したため、その2行だけを修正した。
READY・durable leaf・全artifact lock・readbackのassertは維持し、該当logical群316件を再検査。
通過済み769件は繰り返さず、残りの標準43コマンドを完了した。製品コードの追加変更はない。
agentによる最終ブラウザ確認でも、通常URLの元worksheet12で `owner_accept_20261028/raw` の読取専用SQLから `1/alpha`、`2/beta` を取得した。
