# Workbench認可重複読取り削減の正式再測定（2026-11-17）

直前study（[認可reads](performance-authz-reads-2026-11-17.md)）のcandidateを、同一の凍結SQL・reference・23列・600秒・4GiB peak・concurrency 1で、baselineとcandidateの両phaseを新規に24 trialずつ測り直した。両phaseとも24/24 SUCCESS、full comparison、guard failed scans 0で、途中停止・再送・UNKNOWNは無い。**288kでは同一rep内のpublic−direct付加overheadがHTTP total **−12.3%**、header **−3.6%**、follow-up header **−5.6%** と小さく整合して低下した。一方1MはHTTP total付加が **−9.5%** でもheader付加は **+0.9%（flat）** で、c02観測のheader −3.1%は再現しなかった。100kは−2.2%/−2.4%でdirectも同方向に動き、信号と区別できない。wallはpublic +2.75/+0.95/−1.01%、direct +2.20/+0.96/−0.01%で、size依存の差は最大1ppに留まる。よってexactな認可SQL callback削減（90→24/page）は前studyのqualified findingとして維持するが、今回の正式E2Eは「288kで小さなHTTP側削減、1M headerはneutral」であり、wall・SLO・engine性能の一般改善は主張しない。c02は**observational only**のまま正式証拠に使っていない。**

## 手順（両phaseともfresh、同一procedure）

- 3 return size（100000 / 288464 / 1000000）× 2 route（public / direct）× (initial 1 + warm 3) = 24 trial/phase。順序はrep 0/2がdirect→public、rep 1/3がpublic→directで両phase同一。
- runnerは`benchmarks/local_perf` HEAD `3fd3d7a2`（attempt会計・gate hardening込み）を両phaseで同一使用。benchmark・meter・guard・正式timing経路は変更していない。
- 凍結入力は既存manifest（SHA一致）、既存DuckDB reference（SHA一致）、23列、full comparison、600秒、4GiB declared peak、concurrency 1、0.2秒capacity sampler、2TB ceiling、512MiB drain。guardはcheck calls 1,665–16,954、global failed scans 0。
- 製品差替えは3ファイルのみをGit blob bytesで書込み、worktree indexは触れていない。BEFORE=`fdb2e24e`（前回deployed baseline）、AFTER=`02c04cd8`（candidate）。phaseごとに別Workbench processへ再起動し、`/api/session` 200（p26-admin/admin）、`/api/status` healthy、`/api/v1/databases` 148、Trino `GET /v1/info` 200（483、starting=false）を**SQL送信なし**で確認した。
- 初回dispatch前にc01（UNKNOWNをNOT_SENTで確定済み、blockers 0）、c02（観測24/24、blockers 0）、新campaign（blockers 0）を`inspect_campaign`で再読し、各trial後にも再確認した。1 trialでも失敗すれば全matrixを停止する手順で、今回は発動していない。

## provenance

- 測定時HEAD `3fd3d7a24e4f0a7b596694586aac62cdab374fa1`（branch `local/autonomous-experiment`）。baseline product `fdb2e24e`、candidate product `02c04cd8`。
- 製品SHA256: baseline `454ef00a…` / `0004a35d…` / `cd2c5b5e…`、candidate `8437cb48…` / `2a2b2cda…` / `d639e90b…`（`request_auth.py` / `query_context.py` / `query_service.py`）。測定後のworktreeはcandidateと一致し、製品diffはHEADまで空。
- manifest SHA256 `1592cba6…`（100k）/ `0d9c4c83…`（288k）/ `6befed8b…`（1M）、public connection `5a6a26e4…`、reference run SHAは`83bce21f…` / `96d46023…` / `2d3a03c5…`。Python 3.12.12。

## 結果（warm 3回のmedian [min–max]、秒）

| rows | route | wall before | wall after | HTTP total before | HTTP total after | header before | header after | dispatch→result before | dispatch→result after |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 100k | direct | 30.071 [30.011–30.436] | 30.734 [30.653–30.792] | 1.207 [1.204–1.213] | 1.164 [1.145–1.226] | 0.660 [0.635–0.669] | 0.628 [0.626–0.631] | 6.860 [6.815–6.968] | 6.874 [6.838–6.906] |
| 100k | public | 30.484 [30.276–30.984] | 31.324 [31.226–31.560] | 1.515 [1.446–1.516] | 1.468 [1.467–1.482] | 1.419 [1.348–1.420] | 1.372 [1.370–1.384] | 7.279 [7.020–7.335] | 7.388 [7.383–7.394] |
| 288k | direct | 84.610 [84.280–85.120] | 85.420 [84.753–85.698] | 1.457 [1.422–1.462] | 1.465 [1.458–1.503] | 0.226 [0.214–0.235] | 0.227 [0.218–0.231] | 17.294 [17.214–17.560] | 17.516 [17.316–17.628] |
| 288k | public | 85.930 [85.743–86.873] | 86.750 [86.646–90.030] | 2.135 [1.995–2.211] | 2.080 [2.048–2.093] | 1.868 [1.737–1.942] | 1.813 [1.781–1.826] | 18.592 [18.138–18.751] | 18.829 [18.633–19.012] |
| 1M | direct | 300.355 [300.089–300.898] | 300.316 [297.063–301.195] | 4.771 [4.524–4.910] | 4.891 [4.785–5.059] | 0.627 [0.591–0.654] | 0.642 [0.641–0.665] | 60.654 [60.410–60.923] | 60.961 [60.530–61.370] |
| 1M | public | 306.598 [303.413–307.243] | 303.492 [302.517–305.592] | 6.991 [6.964–7.327] | 7.043 [6.890–7.069] | 6.071 [6.060–6.399] | 6.131 [5.973–6.158] | 65.297 [63.920–65.470] | 64.651 [64.060–65.148] |

- warm median差（after−before）: public wall **+2.75% / +0.95% / −1.01%**、direct wall **+2.20% / +0.96% / −0.01%**。HTTP totalはpublic **−3.08% / −2.57% / +0.74%**、direct **−3.55% / +0.54% / +2.52%**。headerはpublic **−3.32% / −2.93% / +1.00%**、direct **−4.86% / +0.46% / +2.30%**。
- paired public−direct付加overhead（同一rep、warm median [min–max]）: HTTP totalは100k **0.309[0.233–0.311]→0.302[0.242–0.337]（−2.2%）**、288k **0.674[0.574–0.753]→0.590[0.590–0.615]（−12.3%）**、1M **2.221[2.054–2.802]→2.010[1.999–2.258]（−9.5%）**。
- header付加は100k **−2.4%**、288k **−3.6%**、1M **+0.9%**。follow-up header付加は100k −1.2%、288k **−5.6%**、1M +0.3%。1MのHTTP付加低下はheader以外の成分（connect/send/body/decode）で、headerはflatである。

## 信号とphase-wide drift

- directはWorkbenchページ経路を通らないcontrolである。100kではdirectのHTTP/headerがpublicより大きく低下（−3.55/−4.86% vs −3.08/−3.32%）し、paired差も−2%台なので、このsizeの変化は**host phaseの共通変動**と整合し、認可削減へ帰属しない。
- 288kはdirectが+0.5%でほぼ不変の一方、publicが−2.6/−2.9%、paired付加もwarm 3中2 repで低下し上限側が縮んだ（HTTP 0.674→0.590、header 1.642→1.583）。このsizeだけは**信号**として扱えるが、絶対量はHTTP −0.055秒、header −0.055秒で小さい。
- 1Mはdirectが+2.5/+2.3%で、publicの絶対値は横ばい（+0.7/+1.0%）。paired HTTP付加は−0.211秒だが、before範囲[2.054–2.802]が広く、header付加は+0.9%で**削減を確認できない**。dispatch→resultはpublic −0.99% / direct +0.51%で、最大1%程度の差は残るが、warm 3の範囲とvalidation変動に埋もれる。
- 結論として、c02観測の「1M public header −3.1%」は正式fresh比較では再現しなかった。前studyのexact callback削減は維持しつつ、E2Eの主張は「288kで小さなHTTP側削減、1M headerはneutral、wallはdriftと分離不能」に限定する。

## 安全性・会計・cleanup

- 両matrix 48 attemptのhandleをTrino `GET /v1/query/{id}`（read-only、SQL送信なし）で確認し、**48/48 FINISHED**、error 0。cancellation 0、reconciliation 0、再送 0、NOT_SENT sidecar 0。
- Workbench query recordは+24（public 12×2 phase）で全てFINISHED。既存UNKNOWN 5件は不変、新規UNKNOWN/CANCELED/FAILEDは0。
- doctorはpre/finalともhealthy（45 healthy + 1 informational）。Trino pod UID `250da53f…` は不変。
- 会計はc02の58 rootを継承し、BEFOREはtask/worker-log/BEFORE rootを加えた61 root、AFTERはAFTER rootを加えた62 root。BEFORE used **47,239,888,896** / remaining **1,952,223,240,192**、AFTER used **50,817,536,000** / remaining **1,948,645,593,088**（2TB ceiling、512MiB drain、4GiB peak内）。
- 終了時runtimeはcandidate（`02c04cd8`、PID 15920）へ復元し、`/api/session` 200（admin）、`/api/status` healthy、`/api/v1/databases` 148を再確認。GitOps既存dirty 2ファイル・index・stashは不変で、製品3ファイルはHEAD bytesに一致。c01/c02のattempt・reconciliation・`not-sent.json`はbyte-identical（`fe8a548a…` / `33b42d8d…`）で、測定中の書込みも0。

## 限界

- 単一host、concurrency 1、warm 3、統計検定なし。initialはcold cacheの証明ではない。
- 両phaseは別process・別時刻であり、direct controlで差し引いても残余の外部変動を排除できない。1Mは約5分/trialで、phase内の熱・cache状態を完全には揃えられない。
- 今回のE2EはWorkbenchページ経路の認可readsだけを対象にしたものではなく、engine・client・validationを含む。exactなcallback削減は別途の製品外trace（前study）による。
- c02の24 runはobservationalのまま保持し、本比較の証拠に使っていない。

## 証拠

- 作業領域: `/private/tmp/shirokuma-authz-remeasure-20261117-c01/`（`raw/before-matrix.json`、`raw/after-matrix.json`、`raw/comparison.json`、`report/tables.md`、`evidence/preflight-before.json`、`evidence/preflight-after.json`、`evidence/source-deployed-baseline.json`、`evidence/source-deployed-candidate.json`、`evidence/integrity-audit-final.json`、`evidence/handles.json`、`evidence/final-checks.json`、`evidence/provenance.json`、`evidence/git-state-*.txt`）
- campaign: `/private/tmp/shirokuma-authz-remeasure-20261117-before`、`/private/tmp/shirokuma-authz-remeasure-20261117-after`
- 継承・保持: `/private/tmp/shirokuma-authz-reads-20261117-c01`（NOT_SENT）、`/private/tmp/shirokuma-authz-reads-20261117-c02`（observational）、reference `/private/tmp/shirokuma-return-scale-20261116-c01/campaign`
- worker log: `/var/folders/sq/2c5hd3nx07n3_tzybgfmfvwh0000gn/T/shirokuma-formal-remeasure-cl3ghoig`
