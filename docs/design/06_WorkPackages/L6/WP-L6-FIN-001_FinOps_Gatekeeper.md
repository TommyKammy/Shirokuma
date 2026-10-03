---
project: Shirokuma
doc_id: "WP-L6-FIN-001"
title: "FinOps Gatekeeper with inference and disk watermark cost"
status: local-partial
created: 2026-07-05
updated: 2026-10-04
version: "0.27"
area: "workpackage"
tags: [shirokuma, wp, l6, finops]
---

# FinOps Gatekeeper with inference and disk watermark cost

## 2026-10-04 Direct history malformed report fields (offline)

Direct saved-history rendering now converts missing public report fields and
malformed nested rows to bounded `HistoryError` rather than exposing Python
index/key errors. Disposable malformed reports are rejected; legitimate
selected reports remain displayable. Focused FinOps and local verification
results are in Milestones; private logs are under
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-043/`.
This is offline presentation robustness, not report provenance, current usage,
AgentHouse retained-volume measurement or operator acceptance. No VM start,
collection, retained report/volume/backup changes or GitHub write. Roll back
only this exception boundary and test; preserve saved records. Original Vault
sync pending outside the approved checkout.

## 2026-10-04 Direct history selection shape (offline)

The direct saved-history renderer now refuses malformed selection envelopes
with its bounded `HistoryError` before unpacking/indexing them; it still accepts
the parsed pair format. This is offline presentation robustness, not independent
report provenance, current usage, retained-volume measurement or operator
acceptance. Focused and local verification results are in Milestones. No VM
start, collection or retained report/volume/backup change. Roll back this
preflight and test alone while preserving saved evidence. Original Vault sync
pending outside checkout.

## 2026-10-04 Duplicate saved observation refusal (offline)

The history parser and direct renderer now reject repeated canonical report
content, rather than displaying one saved report as two independent observations.
The disposable negative failed before repair. Focused FinOps tests passed (178
tests; 7 Linux-only skips), followed by full local verification. An existing
workspace-usage fixture that selected the same report twice was changed to use
two distinct valid reports; the intermediate failure is retained in the private
log. This is a content-identity check, not proof that different reports came
from independent producers or that current usage was measured. No VM start,
collection, retained report/volume/backup mutation or GitHub write. Roll back
only the history guard and fixture changes; preserve saved evidence. Logs:
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-033/`.
Original Vault sync pending outside this checkout.

## 2026-10-04 Saved FinOps HTML browser policy (offline)

Standalone and history HTML now declare a restrictive CSP: no default network
resources or script, with inline CSS retained for self-contained display.
Both disposable negatives found the missing policy before repair; focused
FinOps 177 tests passed (7 Linux-only skips). This is defense in depth for
rendered offline pages, not proof of browser enforcement, producer authenticity,
live usage or operator acceptance. No new collection, VM start or retained
data/backup mutation. Roll back only the two template lines and tests, retaining
saved reports. Full local verification, design-context, secret scan and diff
check passed; private logs are under
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-032/`.
Original Vault sync remains pending outside checkout.

## 2026-10-04 Direct saved-history selection order (offline)

Direct callers of `history.render` can no longer duplicate or omit a selected
report index, or display reports out of observation-time order while the page
claims chronological order. The renderer checks the exact index set and the
same timestamp/index ordering used by the parser before producing HTML. A
disposable negative reproduced both misleading cases before the fix; 175
focused FinOps tests passed (7 Linux-only skips). No collection or VM start;
stored reports, AgentHouse volume and backups were not changed. This is display
integrity, not saved-report provenance or current usage. Roll back renderer and
test only, retaining evidence; original Vault sync remains pending.

## 2026-10-04 Direct saved-history renderer digest binding (offline)

The history parser validates selected reports and calculates SHA-256, but a
direct call to `history.render` formerly accepted an arbitrary displayed
digest, including HTML markup. It now validates each report and binds the
displayed digest to its canonical content before producing HTML. Disposable
forged-digest negatives failed before repair; the focused FinOps suite passes.
No collector/schema change or saved-report rewrite, VM start, AgentHouse
volume scan or backup change. This guards an offline presentation boundary,
not independent report provenance or current resource usage. Retain AgentHouse
`null / NOT_MEASURED` and WP local-partial. Roll back only the renderer and
fixture, preserving saved reports and backups. Direct original Vault sync is
pending outside this noninteractive checkout.

## 2026-10-04 Standalone saved HTML underflow boundary (offline)

The standalone saved-report HTML parser still accepted a nonzero `1e-999`
measurement as zero after the history parser had been fixed. A disposable
valid report reproduced the error before repair. Both offline renderers now
use one finite-float decoder; literal `0e-999` and normal reports still pass.
No public schema, collector, original saved report, retained volume or backup
changed. Focused FinOps tests (173; 7 Linux-only skips) and full local
verification passed on rerun; the first full run failed in an unrelated
Warehouse saved-journal fixture, whose focused suite and unchanged full rerun
passed. Logs: `tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-015/`.
This protects only offline JSON interpretation, not current usage, report
authenticity or backup recovery. Roll back the decoder/renderer/test changes
only; preserve saved data. Original Vault sync pending outside checkout.

## 2026-10-04 保存FinOps履歴の非ゼロ浮動小数underflow拒否（offline）

保存した2–8報を読む`history.parse`はJSONの極小非ゼロ数`1e-999`をPythonの
floatで0に丸め、妥当な0の測定値としてHTMLへ渡し得た。隔離された2件の
valid reportの既存0.5測定値を置換した負例で修正前の誤受入を再現。非ゼロ
mantissaが0へunderflowした場合だけ拒否し、真の`0e-999`と通常の保存履歴を
維持する。public schema/collector・保存report原本は変更しない。これは
選択したJSON数値のdecode境界であり、現在の実使用量やレポートの独立
producer真正性・継続量・backup復旧を証明しない。実volume、VM、保持data、
backupを走査/変更せず、AgentHouse volumeは`null / NOT_MEASURED`、WP
local-partial。focusedと全回帰の結果はMilestones、private logは
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-014/`。
戻す場合はhistory decoder/test差分のみ戻し、保存report/backupを保持する。
原Vault同期は非対話checkout外で保留。

## 保存レポートの保存領域観測範囲表（2026-09-27・offline履歴）

保存v1–v9の履歴HTMLにAgentHouse Docker volume、MLflow store、Notebook Lab workspace、Serving HOME/TMPの固定5行×選択観測の範囲表を追加。各reportのvalidated JSONだけで、schema対象外・参照未取得・参照確認/未計測・論理bytes未取得・観測した通常ファイル`st_size`を分ける。0は**当該directoryの実際に保存された論理0**のセルだけに表示。AgentHouseのvolumeは参照が成功していても使用量`NOT_MEASURED`を維持。合計/増減、観測間の連続性、TMP削除可能量、物理占有、backupは推定しない。公開schema・collector・保存JSONは変更せず、選択2–8報をoffline表示するだけ。

実v8/v9保存レポートをread-onlyでレンダリングし、v8ではServing TMPがschema対象外、v9では論理0 bytes、AgentHouseは両方`READ_FAILED / NOT_MEASURED`の別セルとなることをbrowser accessibility tableでも確認。保存原本SHA256 v8 `5ac068bafbc2f86d49165ba3dee2838f41c48057b12daa32fa1e831e0b2eb71b`、v9 `d6f188631b4fd446bd3a8f4472fded5475e430cb489ccd81174a0a68d1a176a6`の一致を前後で確認。見た目のpixel受入・実VM認証・保持volume数値化ではない。Colima/Serving起動・runtime変更なし、AgentHouse `null / NOT_MEASURED`、WP local-partial。private証拠は`tmp/local-experiment/finops-history-coverage-checks/20260927/`。

保存データ・backup/exportへの変更なし。scope表を使わない場合はrendererの呼出しを止めればよく、回復のために保存reportやbackupを削除・再作成しない。旧schemaで参照失敗した対象を「測定失敗」へ誤昇格させない負例も固定した。

focused171件＝164PASS＋Linux専用7skip。全回帰121suites/3786実行＝3778PASS＋8skip（既存1＋Linux専用7）、87.537秒、最終log SHA256 `c1cf06b26ad3ff467147353280a8af2ca79f7a03e8debac307d812f71a19cdbc`。design-context/secret scan PASS。private HTMLは32,268 bytes、SHA256 `22d4b9d80fc145fba270cc169ad7f8df76a1c6b6d428a40a3e97f6956a354d84`。

## Model Serving TMPの独立論理bytes（2026-09-27・v9 local増分）

AgentHouseについて停止中の専用profileの既存記録・設定key名/ファイル種類をread-onlyで再棚卸ししたが、独立して信頼済みのSSH server key bindingや、認証済みguest receiptを返す別経路は確認できなかった。profile設定テキストはkey名を抽出し値を出力・保存・trust採用せず、`vz-identifier`や`guestSocket`設定名をserver認証とはみなさない。別の秘密鍵/credentialファイル、VM disk/iso/guest logは未読。live receipt/保持volume走査を引き続き保留し、独立して進められるServing `TMPDIR/TMP/TEMP`固定rootを選択。

既存Serving ownerと固定environment参照、private0700/root device-inode、process停止を確認してから、no-follow・上限付きの二走査metadata測定をhost schema v5/report v9の**第四行**に追加。停止中の実01はTMP通常ファイル0件/子directory0件/論理0 bytes。別の後続metadata走査も同じで、HOMEは別行0 bytes、MLflow store 1,767,968 bytes・Lab workspace 244,438 bytesは独立したまま。保存v5–v8は元の3行、v1–v4はhost行なしという旧範囲を維持し、旧v8へTMP行を注入しても拒否。全v9レポートは他source未取得で`partial`。TMPは**削除可能量を意味しない**。Serving sibling logs/runtime records/共有model artifacts、物理占有・backup復旧・全component footprintは対象外。Colima/Serving起動・保持データ変更なし、AgentHouse `null / NOT_MEASURED`、WP local-partial。証拠は`tmp/local-experiment/finops-serving-tmp-checks/20260927/`。

focused167件＝160PASS＋Linux専用7skip。全回帰121suites/3782実行＝3774PASS＋8skip（既存1＋Linux専用7）、85.712秒、log SHA256 `57808a4f30c36b962995d901d8ccd4d2da71bbd8fe994adf58aa4f701366ee6b`。design-context/secret scan PASS。保存v8 report原本SHA `5ac068bafbc2f86d49165ba3dee2838f41c48057b12daa32fa1e831e0b2eb71b`不変。v8/v9のoffline HTML/historyとbrowser accessibility tableの4独立行を確認（pixel visual・本番受入ではない）。

## AgentHouse VM procfs visibility（2026-09-27・offline fixture）

`linux_volume_procfs_contract.py`で**callerが渡した模擬値のみ**を対象に、`/proc`のmountinfo/fdinfoのmount ID、procfs root/`hidepid`・PID path overrideなし、自己PIDとPID 1のstat・単一`NSpid`、PID/mount namespace linkとstart tickを有界検査。target不在は明示された`ENOENT`だけを候補とし、`EACCES`・欠損自己可視性・別namespace/不正mount・改ざんstatを拒否。既存receipt wireへのshape接続もfixtureで確認したが、**実VMの`/proc`は未読**。模擬`ENOENT`や`query_complete=True`はkernel由来の証明でもremote child cleanupでもない。認証済みVMでdescriptorをpinし、filesystem/namespace・PID可視性を実確認するまでlive receiptと保持volume走査は保留。Colima停止、AgentHouse `used_bytes=null / NOT_MEASURED`、WP local-partial。

focused163件＝156PASS＋Linux専用7skip、全回帰121suites/3778実行＝3770PASS＋8skip、log SHA256 `12b19f4eba69c0c1420875a5128197d86981d09f03c24fb9edda5ede2b571468`。design-context/secret scan PASS、保存v7原本不変。限定private証拠は`tmp/local-experiment/finops-procfs-contract-checks/20260927/`。

## AgentHouse host sleep/boot時刻境界（2026-09-27・offline fixture）

`host_clock.py`でDarwin boot session UUID、sleepを除く`mach_absolute_time`と含む`mach_continuous_time`を同一timebaseで読取り。boot読取を両時計で挟むlocal sampleと、operation開始・probe・終了の3 sampleを照合するprivate契約を追加。boot変化、逆行、30秒operation/10秒probe age超、両時計の経過差50ms超、probe側のhost時刻不一致を拒否してから、従来のreceipt gateを適用する。synthetic sleep/reboot/逆行/古いprobeをnegative試験し、macOS時計をread-only smoke。**実際のhost sleep/rebootは誘発していない。** 50ms許容差内の短いsleep・不真正な注入sample・VM側実行/cleanupを排除できず、live採用にはrunner自身が操作全期間のclockとbootを取得する必要がある。専用Colima起動/SSH/保持volume走査なし。AgentHouse `used_bytes=null / NOT_MEASURED`、WP local-partial。

focused159件＝152PASS＋Linux専用7skip、全回帰121suites/3774実行＝3766PASS＋8skip、log SHA256 `e16315604b12d387cca9b0e936bb391b46344c16be10135a9672be16a6c862b7`。design-context/secret scan PASS。限定private証拠は`tmp/local-experiment/finops-host-clock-checks/20260927/`。ColimaはStopped、保存v7原本SHA不変。

## AgentHouse worker source bundle相関（2026-09-27・offline）

`linux_volume_source_bundle.py`でVM側予定の固定3 Python source（`linux_openat2.py`・`linux_volume_worker.py`・`linux_volume_supervisor.py`）をno-followなroot descriptorから2回限定readし、owner・mode・link・size・前後identity/内容hash一致を条件にprivate manifestとdomain-separated SHA256を作成。欠損・symlink/hardlink・特殊file・別内容/途中変更・過大fileをfixtureで拒否。実checkoutのlocal sourceは3件・bundle digest `113e05272aef1fd0d5e4f02aa9cc5b86105b1d3c6c6a0d099ec43ebab4c7ab40`。これは**local sourceの相関値のみ**で、今のsupervisor/wireは呼出元が自由に`worker_digest`を渡せる。VMへの転送・そこでのimport/実行、Python interpreter/依存source、receiptと実行bytesの同定を証明せず、公開レポートには含めない。専用Colima起動/SSH/保持volume走査なし。AgentHouseは`null / NOT_MEASURED`、WPはlocal-partial。設計境界は`tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md`。

focused154件＝147PASS＋Linux専用7skip、全回帰121suites/3769実行＝3761PASS＋8skip、log SHA256 `252e7430b006432e53c58d816f6b5dacdf48bfe9d20a40ada3d9f67fa5adae26`。design-context/secret scan PASS。独立SHA計算も一致し、保存v7原本不変。限定private証拠は`tmp/local-experiment/finops-source-bundle-checks/20260927/`。

## AgentHouse known-hosts descriptor snapshot（2026-09-27・offline fixture）

既存のpathname検査→SSH再openの競合を小さくするため、**独立してenroll済みのkeyをcallerが既に持つことを前提**にした`pinned_ssh_prefix`を追加。検証済みknown-hosts/identity fileを必要とし、一致するfixture key entryをunlinked read-only descriptorに固定して、子processへ`pass_fds`と`UserKnownHostsFile=/dev/fd/N`の候補を渡す。元pathnameの差替え後にもfixture子processが固定entryを読めること、`ssh -G`によるoffline option解釈、失敗時にsnapshotを作らないことを検証。保持volume、既存VMの鍵やcredential内容、SSH接続には触れていない。OpenSSHが実接続でdescriptorを保持/複数回readする挙動は未検証で、same-user攻撃者への分離やserver keyの真正なenrollmentも未証明。認証済みreceipt取得の受入・走査許可ではなく、AgentHouseのbytesは`null / NOT_MEASURED`、WPはlocal-partial。境界と残余は`tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md`。

focused151件＝144PASS＋Linux専用7skip、全回帰121suites/3766実行＝3758PASS＋8skip、log SHA256 `3dc8cce81b9bc0e228efc7c69d9db536aa2eba319fde0aa13a00125af1062bed`。design-context/secret scan PASS。private証拠は`tmp/local-experiment/finops-known-hosts-snapshot-checks/20260927/`。

## Model Serving HOMEの限定論理bytes（2026-09-27・v8 local増分）

AgentHouse volumeのauthenticated SSH server key bindingは保存済み専用profile/設計記録に見つからず、live依存作業はunknownのままとした。独立して進められる固定Model Serving HOMEを、既存owner/configの`HOME`参照とroot device/inodeを確認後、サービス停止中にbounded/no-follow二走査で測定した。通常ファイル**0件・子directory 0件・論理0 bytes**。別の後続metadata走査でも同値、前後のowner/root identityとServing停止状態も一致。Notebook LabとMLflow storeは各行の限定値を維持し合算しない。全v8レポートは他の取得項目が未取得で`partial`。旧v1–v7のServingは未計測のままで、旧レポートの数値注入を拒否する。詳細は`tools/local_experiment/finops/README.md`。

HOMEの0はServing全体の0ではない。sibling tmp/log/runtime/venvや共有モデルartifactは対象外で、filesystemの物理占有・APFS共有extent・backup/restore・削除可能量、内容不変を示さない。directory metadata読取によるatime影響も排除していない。専用ColimaとServingは起動せず、保持HOMEを削除/修復していない。保存v7 report原本SHAは不変。限定private証拠は`tmp/local-experiment/finops-serving-usage-checks/20260927/`。WPはlocal-partial。

focused149件＝142PASS＋Linux専用7skip。全回帰121suites/3764実行＝3756PASS＋8skip（既存1＋Linux専用7）、log SHA256 `cf70fbb1635a5dff3e08b89acbbd11b852b76da2ab20570fdc3dc47c475b30cd`。design-context/secret scan PASS。既存v7と新規v8のoffline HTML/history契約を確認し、ブラウザaccessibility tableでも3行の分離とServing HOME 0 bytesを確認（pixel visualの受入ではない）。

## AgentHouse receipt wire / SSH設定境界（2026-09-27・offline）

bounded 16KiBのprivate responseでversion・nonce・VM boot/namespace/procfs前後identity、operation receiptと独立process probeを照合し、重複JSON key・oversize・不正数値・稼働中の同一childを拒否。既存の**信頼済み**server keyを前提にした固定loopback OpenSSH argv候補も作り、user/global config・proxy・agent・forwarding・対話認証を無効化。既知host fileをbounded no-followで検査し、`ssh -G`のoffline設定解釈を確認。ただしhost keyの真正なenrollmentは未確立、SSH自体の起動なし。検証したknown-hosts pathnameをSSHが再openする競合とworker実行source照合、host sleep/boot、VM `/proc`可視性は未達。wireの成功だけでは接続先認証・終了確認・保持volume走査許可と呼ばず、数値は引き続き`null / NOT_MEASURED`。

専用profileの既存`ssh.config`はread-onlyで設定項目のみ確認し、`StrictHostKeyChecking no`/`UserKnownHostsFile /dev/null`を確認。鍵の内容や秘密credentialは読んでいない。この既存接続は信頼済みserver keyのenrollment根拠にならず、first-contact鍵採用や無検証でのlive transportを行わない。別の真正なkey provenanceが必要。

focused145件＝138PASS＋Linux専用7skip、最終全回帰121suites/3760実行＝3752PASS＋8skip（既存1＋Linux専用7、log SHA256 `b7aaaabc7b0b4bc5cc04ce022255da5d00c974dbabb5e5e9f1f2552715bca540`）。design-context/secret scan PASS。限定private証拠は`tmp/local-experiment/finops-receipt-wire-checks/20260927/`。live SSH/remote process・保持volumeは観測していない。

## AgentHouse host receipt gate（2026-09-27・offline）

VM supervisorのterminal receiptと、別に取得した同じbootのprocess probeを照合するprivate受理ゲートを追加。操作UUID、worker相関digest、VM/host boot、子PID/start tick、cleanup、結果上限と、同一host monotonic clockで操作開始から30秒以内の窓を条件とする。@architectレビューでstart tickの先頭ゼロ別名、古い測定＋新規probe、巨大数値の変換を拒否した。これは**入力が真正なVMから取得されたことや実際にremote workerが停止したことの証明ではない**。receipt/probeの安全な取得・再接続とworker sourceの検証は未実装で、保持volumeを走査せず`null / NOT_MEASURED`を維持。専用Colimaは停止したまま。

focused139件＝132PASS＋Linux専用7skip、全回帰121suites/3754実行＝3746PASS＋8skip（既存1＋Linux専用7）、log SHA256 `79f9e64fcdfed52e08d380b676085dbe722e8dcc7ad2e618d62ee5f7e6319d7b`。設計文脈/secret scan PASS、既存v7 report原本不変。限定証拠は`tmp/local-experiment/finops-receipt-gate-checks/20260927/`。次は既存認証済みVM接続でのbounded receipt/probe transport、host sleep/bootとworker source同定の検証。

## AgentHouse openat2 / supervisorのfixture-only実機検証（2026-09-27）

Linux workerのroot/子directory/file metadataを`openat2`の`NO_XDEV|NO_SYMLINKS|BENEATH`で固定rootから解決し、anchorのdevice/inode/mount IDも検査する。VM-side fixture supervisorは子の登録後起動、期限超過TERM/KILL、reap、terminal receipt照合まで数値を受理しない。専用Colimaを一時起動し、disposable fixtureのLinux17件＝15PASS＋macOS専用2skipを確認した。bind mountの隔離namespaceは`uid_map: Operation not permitted`で拒否され、mountは作らずカーネルのbind越境拒否は**未検証**。再起動後は通常停止でStoppedへ復帰し、設定SHA/retained VM disk device/inode一致を確認したが、k3s通常停止のpod mountpoint cleanupと`/var/lib/cni` busyを伴い、guest保持内容・backup不変は証明しない。supervisorのdisk I/O無期限block、host切断後のreceipt再取得、VM-host writerの継続停止も未達。保持volumeは読取・走査せずpublic Docker bytesは`null / NOT_MEASURED`、WPはlocal-partial。限定証拠は`tmp/local-experiment/finops-openat2-supervisor-checks/20260927/`。

最終macOS focused136件＝129PASS＋Linux専用7skip。全回帰121suites/3751実行＝3743PASS＋8skip（既存1＋Linux専用7）、log SHA256 `3f604c264e5a1b83ab48e810bc526ead0b6390a16e081ef89a92915c67f38335`。設計文脈とsecret scanもPASS。Linux fixture15PASSはその時点の実装に対するもので、後続のmacOS側receipt symlink拒否の追加、未実行bind fixtureは別扱い。保存v7 report原本SHAは不変。

## AgentHouse private Docker metadata transport（2026-09-27・offline）

前増分のoffline preflightへ渡すprivate transportを追加。固定contextのUnix socketを**daemon読取より前**に照合し、以後は`--host`で固定する。daemon/root/ARM64・volumeのdriver/Options/固定pathを確認してから、label有無にかかわらず全container IDを上限付きでinspectし、環境変数・余分なlabel・state error等を投影時に捨てる。前後listとdaemon identityを比較し、全処理20秒上限と開始/終了時刻を保持する。誤context・Options内credential・欠損volume・一覧/daemon driftをfake backendで拒否。専用Colimaは停止のまま、live preflight/保持volume走査は行わない。workerのremote cleanupとper-open mount境界、VM-host writer確認は引き続き未達。Docker公開schemaはv1のまま、`null / NOT_MEASURED`を維持し、WPはlocal-partial。

focused124tests＝121PASS＋Linux専用3skip、全回帰121suites/3739実行＝3735PASS＋4skip（既存1＋Linux専用3、log SHA256 `dda8e0a964e7fef1b76a91399e30657883f8bb8eb202ad35e83f2dcaa4235fb6`）。design-context/secret scan PASS、専用Colima停止・保存v7原本SHA不変。限定private証拠は`tmp/local-experiment/finops-volume-transport-checks/20260927/`。transportのlive検証・数値測定とは区別する。

## AgentHouse volume fixture-only worker / offline preflight（2026-09-27）

前回の設計ゲートから継続し、Linux専用の非接続metadata workerと、Docker参照のoffline二時点preflightを実装。macOSではworkerが列挙前に拒否する。VM-local使い捨てfixtureのLinux試験でno-atime二走査、root/子directory atime不変、sparse/hiddenの論理値、symlink/hardlink/特殊file/変更/上限拒否を確認した。初回Linux試験は`PosixPath`型判定で失敗し、修正後に再実行して成功。VMは専用Colimaを明示起動/通常停止し、AgentHouseはexited/restart=no、volumeのlocal driver/Options nullをmetadataだけで確認した。設定SHA/VM disk identityは停止後も一致。k3sの通常停止にpod mountpoint cleanupと`/var/lib/cni` busy warningがあり、保持データやbackup不変の証明ではない。追加起動はremote終了確認とmount越境防止の実装まで見送る。

offline preflightは全Docker container ID・mount参照、bind overlap・固定daemon/volume/container identityと時刻鮮度を検査するが、結果は`volume_traversal_authorized=False`のまま。VM-host writerや同一device bind mountの走査中の出現・消滅、remote cleanupの未確立を理由に**保持volumeの走査、公開schema変更、使用bytesの数値化は未実施**。現行v4–v7の`null / NOT_MEASURED`を維持。コードと未達条件は[測定境界計画](../../../../tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md)を参照。WPはlocal-partial。

focused117tests＝114PASS＋Linux専用3skip（macOS）、Linux VM-local fixture5tests＝4PASS＋macOS用1skip。全回帰121suites/3732実行＝3728PASS＋4skip（既存1＋Linux専用3）、`make verify-design-context`とsecret scanもPASS。VM起動/停止以外のruntime設定・GitHub変更なし。証拠は`tmp/local-experiment/finops-volume-worker-checks/20260927/`。

preflightの他container mount・稼働履歴の比較漏れを@architect再レビューで修正し、最終全回帰も同じ121suites/3732実行＝3728PASS＋4skip（log SHA256 `361101fe47433e510df0c1895f50f446b5afab4ab7ee37a6cdc3c742ed9f0764`）。二時点の一致は中間のwriter不在を証明しない。修正後のLinux実機再試験は追加起動を避け未実施で、先行fixture結果をその範囲で保持する。

## AgentHouse named-volume実使用量の測定境界（2026-09-26・設計のみ）

@architectと相談し、この増分は**inspect-onlyの設計ゲート**とした。専用Colimaが停止しており、Docker inspectは参照関係だけを示し、Linux volumeの論理ファイルbytesや排他的所有を示さない。Colima起動は他workloadの保持領域書込みを伴い得る。Docker `system df`、ClickHouse起動後のexec、read-onlyと称するhelper container、macOSからのVM disk mountは、安全で限定された無変更走査を現時点で証明できない。今回は起動・volume走査・bytes発表をせず、既存Docker schema v1/report v4–v7の`used_bytes=null / NOT_MEASURED`を維持する。欠測を0と呼ばない。

後続数値化の条件は、固定daemon/volume/停止containerのidentityと他のrunning/stopped container/bind参照を前後照合、driver options/実mount topologyを確認し、Linux側のdescriptor相対・no-follow・`O_NOATIME`走査とremote worker終了確認を備えること。symlink/hardlink・別device・走査中の変化や上限/cleanup不明ではunknownを返す。成立時も論理通常ファイル`st_size`だけで、共有物理extent、VM/APFS占有・ClickHouse table帰属・backupや回収可能量ではない。詳しいgo/no-go・negative test・回復条件は[測定境界計画](../../../../tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md)。この設計記録は数値計測の機能受入ではなく、WPはlocal-partial。

offlineのDocker size hint/既存v7への数値注入拒否を追加してfocused108tests PASS。全回帰121suites/3723実行＝3722PASS＋既存1skip。回帰後も専用Colima停止と保存v7原本SHA不変を確認した。私有証拠は`tmp/local-experiment/finops-agenthouse-usage-boundary/20260926/`。volume内部の保持状態やbackupは観測しておらず、回復操作も不要だった。

## MLflow storeの限定論理bytes（2026-09-26・v7 local増分）

v6のNotebook workspace行とは別に、所有markerのSQLite backend URI・artifact rootを確認した固定`tmp/local-experiment/mlflow-tracking/store`を対象とする。`tracking.sqlite`通常ファイルと`artifacts`ディレクトリの存在を2回のdescriptor相対metadata走査で要求し、DB側car（存在する`-wal`/`-shm`/`-journal`）およびartifacts配下も含む全通常ファイルの`st_size`を合計する。内容・SQL・DB lockを読まず、MLflowの起動/backup export/復元も行わない。symlink/hardlink/特殊file・別owner/device・root差し替え・sidecarやartifactsの走査中変化・件数/名前/深さ/時間の上限超過ではMLflow bytes/countをnull/理由付きunknownとする。参照identityとNotebookの独立数値は維持し得る。host schema v3/report v7で行ごとの観測windowと基準を持たせ、保存v1–v6の意味は変えない。

停止中の実02はMLflow storeの通常ファイル25件、子ディレクトリ55件、**論理1,767,968 bytes**。別の後続metadata走査でも同値、Lab workspaceも独立して論理244,438 bytes。Serving HOMEは未計測。Kubernetes等が未取得で全体partial。focused106tests PASS、全回帰121suites/3721実行＝3720PASS＋既存1skip。回帰後の両root identity・metadata manifest一致とColima停止を確認したが、内容/backupの不変や復旧の証明ではない。両行は合算せず、SQLiteの整合性、MLflow run/artifact論理帰属、冷バックアップの復旧可能性、APFSの一意物理占有・snapshot/圧縮・quota・削除可能量を主張しない。原観測/検証は`tmp/local-experiment/finops-mlflow-usage-checks/20260926/`に保存し、WPはlocal-partialのまま。

## Notebook workspaceの限定論理bytes（2026-09-26・v6 local増分）

@architectと相談し、実使用量の最初の数値対象を**Notebook Labの固定workspaceだけ**に限定した。既存owner/config・root device/inodeを確認し、非追従のdirectory descriptorを保持して2回のmetadata走査で通常ファイル`st_size`の合計とfile/子directory件数を比較する。hidden/checkpoint/旧・archiveファイルも含むが内容は開かない。symlink、hardlink、特殊file、別device/owner、identity・metadataの変化、走査上限（10,000 entries、depth32、name data4MiB、10秒）超過ではbytesをnullにする。18秒のworker alarmと20秒の親transport timeoutを持つ。MLflow store・Serving HOMEは意図的に未計測のまま。保存v1–v5の公開契約を保持し、新規host schema v2/report v6で基準・観測window・件数と失敗理由を区別する。

Colima/サービス停止中の実02でNotebook workspaceは**26通常ファイル・子directory1件・論理244,438 bytes**。別のread-only metadata走査でも同じ件数・論理合計だが、独立観測は原子的同時刻比較ではない。host3件の参照はobserved、他のDocker/Kubernetes等の未取得により全reportはpartial。focused102tests、全回帰121suites/3717実行＝3716PASS＋既存1skip。回帰後のworkspace root identity・metadata manifestも独立列挙時と一致し、Colimaは停止したまま。内容bytesやbackup復旧は確認していない。数値はAPFSの物理占有・clone共有・snapshot/圧縮・quota・回収可能量・MLflow/Servingや全Labの容量・backupの証明ではなく、他disk値とも合算しない。限定証跡は`tmp/local-experiment/finops-workspace-usage-checks/20260926/`。WPはlocal-partial。

## 固定host directory参照（2026-09-26・限定local増分）

v5は、MLflowのstore marker内の固定SQLite/artifact-root→`tmp/local-experiment/mlflow-tracking/store`、Notebook Labの所有record/configのroot_dir→`tmp/local-experiment/notebook-lab/workspace`、Model Servingのowner/固定HOME設定→`tmp/local-experiment/ridge-serving/home`の3参照だけを検証する。専用Lab仮想環境の固定read-only workerがprivate owner/config validatorを再利用し、所有情報のdigestと非symlink/0700ディレクトリのdevice/inodeを前後で照合する。owner token・設定内容・絶対pathは公開しない。配下ファイル・shared store・Lab cache/実験・Servingのモデル実体、実使用bytes・物理占有・backupやquotaは未確認。`used_bytes=null / NOT_MEASURED`を保持する。

Colimaと3つのサービスを起動せずに、停止中の実v5レポートは3対象すべて`observed`、Docker/Kubernetes等はunknownで全体partialとなった。実取得と保存v4→v5 offline HTML/historyの原本不変・旧schema未収録区別を確認。symlink・permission・owner/directory差し替えのnegative testを含む。focused 92tests、全回帰121suites/3707実行＝3706PASS＋既存1skip。回帰後も所有digest・ディレクトリdevice/inode・直下entry数の前後一致、Colima停止を確認。直下entry数は配下内容・bytesの証明ではない。この増分は全ホストdirectory列挙ではなく、容量評価や実使用量の測定判断は後続に分ける。証拠は`tmp/local-experiment/finops-host-storage-checks/20260926/`。

## AgentHouse Docker named-volume参照（2026-09-26・限定local増分）

v4は専用Docker contextの固定コンテナ`shirokuma-local-agenthouse`とnamed volume `shirokuma-local-agenthouse-data`を、二度のread-only inspectで検証する。container ID/所有label/pinned imageとvolume名/label/local driver/作成時刻/mountpoint、単一mountのsource・宛先`/var/lib/clickhouse`を照合する。Docker作成時刻のoffsetをUTCへ正規化する。コンテナ停止中でも保存されたmount参照は観測できるが、実際のmount健全性・排他的所有・他コンテナの参照やvolume内の使用量は分からない。`used_bytes=null / NOT_MEASURED`を維持する。設定環境変数・host側mountpoint・credentialは公開レポートに出さない。

停止中v4の対象はunknown/null、専用profile一時起動後の実01はDocker作成時刻の`+09:00`でSAMPLE_INVALIDとして保持し、UTC正規化修正後の実02では停止中の既存AgentHouseコンテナとvolumeの参照を確認した。profile/Fluxに変更を加えず、AgentHouseコンテナやvolumeを起動・作成・削除しない。実レポート全体はAgentHouse推論記録等の未取得でpartial、参照限定の観測成功と区別する。元v1/v2/v3の契約は保持し、v4 HTML/historyはvolume参照/観測identityと旧schemaの未収録を区別する。focused 87testsと全回帰121suites/3702実行（3701PASS＋既存1skip）を確認。終了時にDocker identityとFluxを再読取し、通常stopでColimaを起動前の停止状態に復帰、設定SHA・保持host directoryを確認した。停止後のDocker API再確認はできない。証拠は`tmp/local-experiment/finops-docker-storage-checks/20260926/`。次の固定host directory・実使用量・他のDocker参照の網羅は別増分とする。

## Fixed Kubernetes storage references（2026-09-26・限定local受入）

@architectの判断により、まず固定4workloadのAPI参照関係をread-onlyで検証するv3を追加した。SeaweedFSとPolaris PostgreSQLはworkload→Pod owner→container mount→名前空間付きPVC→PV claimRef UIDを照合し、Trino/Polaris本体は直接PVC参照なしを確認する。要求容量とPVC/PV宣言容量は別々で、実使用量は常に`null / NOT_MEASURED`。20Gi＋5Giは実占有・物理予約・quota・削除可能容量ではない。S3利用者にSeaweedFSの容量を重複帰属しない。保存v1/v2の契約と表示は維持し、履歴ではv3のUID/観測windowを確認できる。

起動前の専用profile停止・構成SHA・保持領域・空き容量を保存し、既存`mac-studio-solo`を`--activate=false --save-config=false --ssh-config=false`で起動した。起動直後のroot Flux suspend=true、storage child suspend=false/patch1、PVC UID・既存volume名を記録。Fluxには変更を加えなかった。実05は2026-09-26T10:38:40.979Zに2PVCがBound、SeaweedFS 20Gi/Polaris PostgreSQL 5Gi（各request/PVC status/PV spec）、Polaris/Trinoは`no_direct_pvc`。PV reclaimはいずれもDeleteで、StatefulSetのwhenDeleted/whenScaled Retainやtemplate pruneとは別の制御。MLflow/Lab/Servingは停止中、AgentHouse宣言eventはSCOPE_INVALIDのため全reportはpartialだがstorage4行の限定観測は成功。実使用bytes、mount健全性や保存データの内訳は未確認。最終sourceの実06でも同じstorage4状態・PVC identity/宣言容量を確認した（`live06.json`、report SHA256 `a1513f0d659dece8c121af4f53d9a6b697dbc5db72f2e9779a83321e4279e70d`）。

停止時v3は全storageがunknown/null、実初回のPodList kind省略とPostgreSQL container名の投影失敗は原reportを保持して狭く修正した。live PVC/PV UIDとclaimRefの独立readback、Flux patch・構成不変、保持directoryの存在を確認。保存v1/v2/v3のHTML/history互換とnegative fixtureで誤owner・別namespace・stale UID・量/retention drift・cleanup境界を検証した。実際のPVC/PV削除やresizeは行わない。手順・証拠と残る制約は[runbook](../../../../tools/local_experiment/finops/README.md)と[Milestones](../061_Local_Experiment_Milestones.md)。focused FinOps 81tests、最終全回帰121suites/3696実行＝3695PASS＋既存1skip。保存public JSON＋2HTMLは69,299 logical bytes／73,728 allocated bytes（0600、物理一意容量ではない）。起動前の状態に戻すため専用Colimaを通常stopし、保持VM disk/backupを削除せず、profile config SHAと保持host directoryを再確認した。停止後のPVC API照合は行えない。WPはlocal-partial、Bound PVCのPV読取失敗時は要求値も未取得に倒す保守的な境界を維持する。

## Saved-report history（2026-09-26・限定offline受入）

明示選択した保存public report 2–8件（v1/v2）だけを静的HTMLへ時刻順に表示する。旧v1のlocal process欠落を停止/0と誤認せず、v2の停止・unknown・observedを区別する。identityとsample時刻、host/Colimaの別diskと当時の助言、各24時間宣言event windowを保持し、異なる区間の使用量・費用・event数を合算/差分化しない。選択原本と表示に秘密のpathを埋め込まず、入力不正ならHTMLを出さず固定エラーで終了。自動収集・継続dashboard・idle/chill判断は未達。

Colima停止時のread-only CLIはpartialで、所有3群はSTOPPED/null、Kubernetes・Colima disk・宣言eventは未取得、host diskのみobserved。保存v1/稼働時v2/停止時v2の3レポートから、原本不変・決定的なHTMLをoffline生成した。focused FinOps 72tests、全回帰121suites/3687実行＝3686PASS＋既存1skip。ブラウザーでの見た目は未確認。操作・容量・証拠は[runbook](../../../../tools/local_experiment/finops/README.md)と[Milestones](../061_Local_Experiment_Milestones.md)を参照。WPはlocal-partialのまま。

## Local owned-process observation（2026-09-20・限定受入完了）

新規収集をv2へ拡張し、固定3群の `local_processes` を追加する。対象はMLflow、Notebook Labのserverと所有kernel、固定model-serving。既存のKubernetes 3components・disk・宣言model eventと、保存済みv1 JSON/HTMLの契約は維持する。収集は既存Lab専用環境の固定read-only workerに委譲し、前後のPID/start identityが一致する所有processだけを集計する。

1秒のCPU累積時間差とRSS合計を観測する。短いCPU sampleは量子化の影響があり、RSSは共有ページを重複計上し得るため、Kubernetes working-setや物理一意メモリとは同一視しない。停止状態を確定できた群はidentity/sampleをnullとして「停止中」と表示し、0へ置き換えない。他の取得が成功していれば全体completeにできる。所有関係や計測が不明なら理由を保持してpartialとする。

2026-09-20T14:38:57.093Zの実01は4.931秒でcompleteとなり、Kubernetes3件とローカル3群がすべてobservedだった。MLflowは2process/487.40625MiB RSS、Labはserver＋13kernelの14process/1354.859375MiB、Servingは1process/32.375MiB。約1.0314595秒のCPU増分は3群とも0msで、量子化を含む観測値として保持し、idleとは判定しない。

1134保護files、ML24runs/1393points/24artifacts、Registry1/1、AgentHouse585行、各runtime identityは当時の前後で不変。保存v1 reportの再検証・HTML生成も互換を保持した。新HTMLの構造・数値・外部resourceなしを検証したが、Browserのfile URL policyで画面表示は未確認であり迂回していない。全回帰は121suites・3682実行＝3681PASS＋既存1skip（93.749秒）。保存public JSON＋HTMLは2files/33,232 logical bytes／36,864 allocated bytes（0600）。検証証拠は別、物理一意容量ではない。2026-09-25のread-only確認では当時のserver PIDは見つからず、現在の稼働を主張しない。

新依存・サービス・start/stop・model実行・MLflow/AgentHouse書込は追加しない。今回の限定3群を超える全component/PVC帰属、idle判断・費用/電力換算、budget強制、継続dashboardは未達のままとする。操作は[FinOps runbook](../../../../tools/local_experiment/finops/README.md)、実測は[Milestones](../061_Local_Experiment_Milestones.md)へ集約する。以下は先行v1の観測時点と受入結果を保持する。

## Local read-only increment（2026-09-20・限定受入完了）

`rtk proxy make local-finops-report`で、固定Trino/Polaris/SeaweedFSのCPU平均・memory working setと観測window/identity、host workspace filesystemと専用Colima filesystemの空き容量、AgentHouseの固定24時間モデル宣言eventをJSONで読む。修正後の実02は0.931秒で`complete`/exit0となった。
[実装runbook](../../../../tools/local_experiment/finops/README.md)の範囲で、新service/model呼出し/AgentHouse書込・stop/delete・budget overrideは行わない。読取失敗は理由とnullを保持する`partial`/exit1、全取得成功は`complete`/exit0であり、FinOps全体の完成を意味しない。

### Observation contract

- CPUはsample windowの平均、memoryはworking set。componentはprimary containerだけを対象とし、identity/start timeとwindowを照合する。欠測・不整合は0に置換しない。
- hostとColimaの空き容量は分け、25%/20%/15%以下をwarning/low/criticalとして固定助言を返す。共有physical storageなので合算せず、APFS reclaimable量や削除可能量を推定しない。
- model/provider別の宣言eventにはsynthetic実験記録も含む。event数を実modelcall数と同一視せず、tokens/costの既知小計と全件coverageを区別する。未知total/currencyはnullのまま、costはproducer-defined estimate、event durationは推論latencyそのものではないproxyとする。

### Local observation

2026-09-20T08:57:46.723Zの実02で3componentsがobserved、host空き61.670289%・Colima81.9312%はいずれもnormal。24時間の宣言model eventは10件/1groupで、prompt4000・completion178 tokens、event duration14288msを記録した。これらは実験記録を含むevent集計であり、実呼出し数や純推論latencyの保証ではない。cost/CPU/memory proxyの全件値とcurrencyはnullを維持した。
初回はbootstrap podがPolaris対象へ混在し、`TARGET_AMBIGUOUS`としてpartialとなった。元report/logを保持し、固定対象の選択を修正して別実02を行った。詳細・最終検証は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

### Cleanup correction and saved HTML（2026-09-20）

補助processで親rtkが先に終了し子がstdoutを保持する場合のtimeout cleanupを修正した。修正後transport26testsがPASS（0.080秒）、実03も0.995秒でcomplete。2026-09-20T09:06:21.343Zに3components observed、host空き61.669668%/Colima81.929112%がnormal、宣言10eventsとcost/proxyのunknownを維持した。実02とその時点の回帰結果は別記録として残す。
`rtk proxy make local-finops-html < report.json > report.html`は、保存JSONの公開契約を検証して静的HTMLへ表示する入口。追加収集を行わず、観測時刻・partial・欠測を保つ。保存先は`umask 077`のprivate directoryとする。保存した実02の通常HTMLと初回partialのHTMLをdesktop/390px幅で表示し、3components/2disks/10events・duration/cost unknown・Polaris TARGET_AMBIGUOUSを確認した。tableのkeyboard横scrollも動作し、追加した一時preview serverは終了した。修正後の最終回帰は3340PASS＋既存1skip、既存44files/MLflow/AgentHouse/runtimeも元baselineと一致した。

### Remaining acceptance

固定3componentsを超える全component/PVC別帰属、idle判断とchill実行、価格/電力換算、budget強制・override、履歴/自動更新/task別などの継続dashboardとPRあたりの費用は未実装。静的reportの表示とは区別する。観測レポートの成功から本番FinOpsや費用削減効果を主張しない。
[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)下の承認済みlocal開発では、旧Issue/PR gateは待機条件にしない。以下の元計画は歴史として保持する。

## 2026-07-05 historical plan

| Field | Value |
|---|---|
| WP ID | WP-L6-FIN-001 |
| Level | L6 |
| Workstream | FinOps |
| Depends-on | WP-L2-OBS-001, WP-L3-CH-001 |

## Scope

Monitor and enforce resource budgets for CPU, memory, Kubernetes PVCs, host SSD free-space watermark, query cost, idle services, cloud token spend, and local inference cost proxies.

## Acceptance Criteria

- Captures CPU/memory/storage usage by component.
- Captures host free-space watermark from macOS/Colima context and emits alerts at 25%/20%/15% free-space thresholds.
- Captures prompt/completion tokens and estimated cost by model/provider.
- Captures local inference latency and resource proxy where available.
- Provides `chill` recommendations.
- Can block or require approval for PRs that raise resource, disk, model-cache, benchmark-data, or token budgets.
- Dashboard shows task inference cost and token cost per accepted PR.

## Notes

FinOps is rule-based; do not let an LLM decide budget override alone.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L6-FIN-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].
