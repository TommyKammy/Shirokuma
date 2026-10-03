---
project: Shirokuma
doc_id: "WP-L5-LINEAGE-001"
title: "WP-L5-LINEAGE-001 Lineage completeness dashboard"
status: local-partial
created: 2026-07-05
updated: 2026-10-03
version: "1.04"
area: "workpackage"
tags: [shirokuma, workpackage, l5, metadata]
---

# WP-L5-LINEAGE-001 Lineage completeness dashboard

## 2026-10-03: non-lossy catalog JSON (offline)

The bounded read-only catalog client now rejects raw invalid UTF-8 and escaped lone surrogates anywhere in a response, including ignored fields/keys. Previously those could be decoded with replacement or carried as invalid Unicode into the evidence comparison. Disposable HTTP negatives failed before the guard; ordinary multibyte Unicode and surrogate-pair characters remain accepted. Malformed responses cannot become present/missing evidence; the existing observation path marks unavailable catalog reads unknown. No live login/GET, VM start, saved credential/source/report or backup mutation. This is response shape validation, not authenticated API/producer provenance or atomic catalog observation; whole-catalog/column lineage and operator acceptance remain open. Roll back decoder and tests only, retaining historical reports. Direct original Vault sync pending outside checkout; checks in Milestones.

## 2026-10-03: credential read-to-identity and parent stability (offline)

The private credential reader now returns identity captured by the verified open-file read and checks that the entry still matches it before returning. A disposable touch immediately after that read used to pair old bytes with new metadata. Separately, it checks the opened parent directory's full metadata across the read; a disposable change from 0700 to 0755 previously returned credential bytes. Both new negatives pass, alongside 64 focused tests and local verification. No live login, GET, VM start, retained credential, saved source, report or backup change occurred. Same-UID restoration between observations, credential producer/API provenance and whole-catalog/column lineage remain unproved. Roll back reader and temporary tests only; preserve retained evidence. Direct original Vault sync pending outside checkout; broader results in Milestones.

## 2026-10-03: credential identity across login (offline)

The private `.env` reader now compares file device/inode/mode/owner/size/mtime/ctime/link count across its two bounded reads around the one existing login, as well as the bytes. A disposable fixture touched its mtime after the POST while leaving the bytes identical; before repair the GET proceeded, now login fails closed without a GET or retry. This does not undo the already-sent login, authenticate the credential producer or detect a same-UID actor that restores metadata between observations. No real credential, retained source/report/backup or VM was changed; read-only Colima listing showed the dedicated profile stopped. Roll back only this reader/test, preserving existing evidence. Direct original Vault sync pending outside checkout; broader checks and unresolved criteria are in Milestones.

## 2026-10-03: credential read bound to original directory (offline)

The OpenMetadata read-only client now reads its existing private `.env` through a no-follow directory descriptor, and rechecks that the directory is still at its original path before using the bytes. A disposable parent-directory rename and symlink substitution after file open previously returned credential bytes without error; the negative failed before repair and passes afterward. Existing login fixtures still exercise a single login and no retry. This does not authenticate the credential producer, rule out a restoring same-UID actor, or prove catalog/API authenticity. No real credential, saved source, report, backup or VM was modified; the dedicated VM was read-only observed stopped. Revert the reader/test only if needed, preserving retained evidence. Original Vault direct sync pending outside checkout; verification and remaining boundaries are recorded in Milestones.

## 2026-10-03: metadata stability during a saved-file read (offline)

The snapshot now compares an opened file's full device/inode/mode/owner/size/mtime/ctime/link-count metadata before and after reading, and compares its final entry mode as well. A disposable fixture changed a file during `os.read` and restored its mtime and bytes; previously the snapshot succeeded, while the tightened reader refuses it. `make test-local-lineage-coverage` passed (60 tests). No saved source, report, catalog or backup was modified; read-only Colima listing showed the dedicated VM stopped. This is conservative against concurrent writes and cannot detect a same-UID actor restoring all metadata between checks, authenticate a producer, establish atomicity or prove whole-catalog/column lineage. Roll back reader/test only, preserving retained evidence; original Vault sync remains pending outside checkout. Broader verification is recorded in Milestones.

## 2026-10-03: bind source metadata across separate reads (offline)

The saved-source digest now includes directory owner/mode/size/mtime/ctime/link count and file device/owner/ctime/link count, in addition to existing identities and file bytes. Two disposable negatives showed that changing an empty directory's mtime, or changing a file's metadata and restoring its mtime without changing its bytes, previously produced the same digest across separate source reads; both now yield unknown edges with no coverage rate. Intra-read metadata checks remain in place. This is a digest format change: do not compare new digests to historical report hashes or rewrite retained reports. Focused (59 tests), full local verification and design-context checks passed; see Milestones. Dedicated Colima was read-only observed stopped; no live API/SQL, retained source/report/backup mutation or request replay. Metadata restoration between observations and independent producer/API provenance remain unproved; whole-catalog/column lineage and operator acceptance are still open. Roll back only the reader and disposable tests while preserving retained evidence; original Vault direct sync pending outside checkout.

## 2026-10-03: inventory drift and empty-directory replacement (offline)

The initial eligible run inventory still fixes the denominator, but a changed or unavailable final collection listing now makes every initially observed edge unknown rather than reporting complete coverage or a success rate. A disposable new run during catalog observation and an injected failed final listing both reproduced the old misleading complete coverage before repair. Separately the source digest now binds opened directory device/inode, including an empty nested directory: replacing a disposable empty directory between source reads previously left the digest unchanged and present edges intact. All three negatives pass after repair. This changes snapshot digest values; compare newly computed snapshots within the same reader version, not old report hashes. No saved source/report/catalog/backup or VM was changed; read-only Colima status was stopped. These checks cannot detect restored identities between reads, authenticate producer or API, provide an atomic inventory, or establish whole-catalog/column lineage or operator acceptance. Roll back reader/tests only, preserving saved reports and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: nested saved-run directory metadata (offline)

The snapshot now compares each opened nested directory's full device/inode/mode/owner/size/mtime/ctime/link-count identity before and after its traversal and against its no-follow parent entry. A disposable nested-directory mtime change during traversal is refused, even when names and file hashes remain unchanged. The 54-test focused suite and full local verification passed; see Local Experiment Milestones. This assumes concurrent legitimate changes should fail closed. Reverting this reader/test change leaves retained source and reports untouched. No live catalog, VM, retained source or backup was changed. Restored metadata, independent provenance, atomicity, whole-catalog/column lineage and operator acceptance remain unproved. Original Vault direct sync pending outside checkout.

## 2026-10-03: collection run-entry metadata stability (offline)

The descriptor-relative collection listing now compares each no-follow run entry's size, mtime, ctime and link count as well as identity/owner/mode across its two name reads. A disposable run whose mtime changed after enumeration is refused with `SOURCE_INVENTORY_CHANGED`; normal listings still pass. This conservatively rejects concurrent edits, but cannot detect restored metadata, authenticate a producer, or establish an atomic inventory. Focused lineage tests passed (53); broader verification is in Local Experiment Milestones. The dedicated VM was read-only observed stopped; no saved sources, reports, backups or catalog were mutated. Roll back only the listing and test, leaving retained evidence intact. Original Vault direct sync pending outside checkout.

## 2026-10-03: source and collection root metadata stability (offline)

The saved-run snapshot and collection listing now compare the opened root directory's device, inode, mode, owner, size, mtime, ctime and link count across their respective bounded reads. Two disposable negatives changed only the root mtime after traversal/listing while leaving the entry names and privacy unchanged; both were accepted before repair and refused afterward. `make test-local-lineage-coverage` passed (52 tests). This is a conservative check: concurrent metadata updates may reject an otherwise readable run. A same-UID actor restoring metadata between observations remains undetectable; neither atomicity, producer authentication, whole-catalog/column lineage nor operator acceptance follows. The dedicated VM was read-only observed stopped; no retained source, report, backup or catalog was changed. Roll back reader/tests only, preserving evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: external hard links in saved source (offline)

The run snapshot refuses regular files with more than one hard link, both when opening and after reading; the final entry comparison includes link count. A disposable hard link from a file outside the run was previously hashed as run evidence; the isolated negative failed before repair and the focused suite passed (50 tests). This assumes saved Data Plan artifacts are single-link files; valid single-link fixture verification still passes. It does not authenticate the producer or defeat a same-UID actor restoring state between checks. No retained source, report, backup, catalog or VM was changed. Roll back the snapshot guard/test only and preserve evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: source snapshot same-name replacement (offline)

The descriptor-relative snapshot now compares no-follow entry identities and metadata after its second directory listing. A disposable source file replaced at the same name after hashing previously produced a digest; the isolated negative failed before repair and the 49-test focused suite passed afterward. Only test-owned fixtures changed; the dedicated VM remained stopped. This cannot detect a replacement restored between checks, authenticate source producers, establish an atomic catalog observation or satisfy whole-catalog/column lineage. Roll back reader/test only, retaining reports, sources and backups. Direct original Vault sync pending outside checkout.

## 2026-10-03: same-name collection entry replacement (offline)

An independently enumerated run name now retains a no-follow device/inode/mode/owner identity through the second listing. Replacing a test-owned directory with another directory at the same name previously passed both name lists; the isolated negative failed before repair, and the focused suite passes (48 tests). Unexpected/symlink entries remain visible to per-run validation rather than being silently discarded. A restored entry between reads is still undetectable; no atomic snapshot or producer authentication is claimed. No retained source, report, catalog, API, SQL, backup or VM was touched. Roll back reader/test only and preserve saved evidence; direct original Vault sync pending outside checkout.

## 2026-10-03: collection listing stability (offline)

The open-descriptor collection inventory now re-enumerates run names before returning and refuses additions/removals during the read. Independently it compares the opened collection directory's owner/mode before and after enumeration and against its current path. Two disposable negatives (new run and mode change) failed before repair; the focused suite passes (47 tests). The fixed collection may be 0755; individual source runs remain private. No retained source, report, catalog, API, SQL, backup or VM was changed. These observations are not atomic against a same-UID actor restoring state between checks and do not establish provenance, whole-catalog/column coverage or operator acceptance. Roll back reader/tests only and preserve retained data; original Vault direct sync pending outside checkout.

## 2026-10-03: source directory membership recheck (offline)

After visiting a bounded source directory, re-enumerate its entries through the same open descriptor and reject a changed set rather than hashing an incomplete snapshot. A disposable run gained a new sibling immediately after the first listing and incorrectly returned a digest before repair; the focused suite then passed (45 tests). No saved Data Plan, catalog, API, SQL, VM, report or backup was changed. This does not detect a same-UID actor that restores entries between checks, prove atomicity or source provenance, or satisfy whole-catalog/column lineage and operator acceptance. Roll back reader/test only; preserve retained sources and reports. Original Vault direct sync pending outside checkout.

## 2026-10-03: source directory privacy after traversal (offline)

The source snapshot now checks both the opened descriptor and current entry for 0700 ownership/mode after visiting each nested directory, and checks the root descriptor again after the walk. Two disposable negative fixtures change a nested directory or the root to 0755 after its initial inspection; the nested fixture failed before repair. `make test-local-lineage-coverage` passed (44 tests). The first `make verify-local-experiment` stopped at an unchanged Warehouse saved-completion assertion (expected `SAVED_COMPLETION_UNVERIFIED`, observed `JOURNAL_UNVERIFIED`); focused Warehouse rerun and second full verification passed, without modifying the test or claiming to repair its intermittency. `make verify-design-context` passed (89 documents). The dedicated Colima profile was read-only observed stopped; no saved Data Plan, catalog, SQL, backup or VM change. An actor who restores a mode between observations remains outside this guarantee; this is neither source-producer authentication nor current catalog/whole-catalog acceptance. Roll back the reader/tests only and preserve retained sources, reports and backups. Direct original Vault sync pending outside checkout.

## 2026-10-03: source file privacy recheck after read (offline)

The descriptor-relative source reader now checks current entry owner, 0600 mode and size/mtime/ctime against the opened file after reading. A test-owned source chmod after the file read but before final entry stat previously yielded a snapshot; the isolated negative failed before repair and passes afterward. `make test-local-lineage-coverage` (42 tests) and `make verify-local-experiment` passed. Only temporary fixture files were changed and restored; the dedicated Colima profile was observed stopped. No retained Data Plan, catalog, SQL, API, backup or VM was mutated. A same-UID actor modifying and restoring evidence between checks remains possible, and this does not establish independent provenance, whole-catalog/column lineage or operator acceptance. Roll back reader/test only, preserving existing source, reports and backups. Original Vault sync pending outside checkout.

## 2026-10-02: current catalog reread for seven historical Data Plans — bounded live

source読取のfd固定後、停止中の専用ColimaとOpenMetadata既存Composeの状態を確認。起動前にprofile設定hash、Metadata host状態15files（`.env`/鍵/4件backupを含む）、既存report3件、保存Data Plan7件のhash・inode/mode/mtimeとsnapshotをprivate記録。VM起動後、固定Docker context/socket、3既存容器のproject/service label、固定digest=image ID、mount/loopback port、exited状態を確認。`runtime.py up`・Compose `up`・migration・credential rotationは使わず、元の3 container IDだけPostgreSQL→search→serverの順に起動。healthyになってから既存`make local-lineage-coverage`を実行した。既存credentialによる認証POST1回の後は固定GETのみで、新report `tmp/local-experiment/lineage-coverage/bb0ade41206241248ec0fe7e5c81cd76/`をprivate作成。

実reportはeligible7・期待14 table-level edges、`present=14/missing=conflict=unknown=0`、分母/観測ともcomplete、sourceとmetadataの再読は安定。独立した別の認証済みGET session（32 API calls）で、保存Data Plan7件に対する生成table14件のUUID/owner/schema/column/evidence markerと、期待14edgeのlineage source/descriptionを再照合。これはcollector保存物とは別呼出しだが**同じOpenMetadata API**であり、VM/producerやDB volumeの独立認証ではない。旧2026-09-17の5run/10edge・前増分の模擬API不達14 unknownと混ぜない。現存SQL relationの実在やcolumn lineage、全catalog・常時dashboardの完成も主張しない。

完了後は元の3 container IDをserver→search→PostgreSQL順に停止し専用Colimaを通常停止、全てStoppedへ復帰。profile設定hash・Metadata host状態15files・既存report3件・保存source7件の起動前後不変を確認、新reportの2file/dirはprivate。DB/search named volumeは起動中に通常書込みがあり得、内容と保持PVC/backup全体の独立不変証明はない。停止ログには保持PodのPVC/secret volume unmountと`/var/lib/cni/` busyを記録。保持data/backup削除・全VM reset・GitHub書込みなし。private実行記録は`tmp/local-experiment/lineage-live-recheck/20261002/`、WP `local-partial`。回復は3容器とVMを追加起動せず停止状態を維持し、作成したprivate reportを根拠ごと保持する（旧report・backupを消して戻さない）。

Lineage focused1 suite/41件PASS（SHA256 `f2f724a31b2e70e369cf1e1424bfd7a69ad9228e03ad114f98ad10b51feaac64`）。初回全回帰はWarehouseの短いpartial-request testに`BrokenPipe`で停止し、失敗log（SHA256 `b1da2e49b865cba54a134495a459dce4d6cbd11288cfc59f3e61251c6f7402cd`）を保存。owner実装ではなくtestの期限競合を別WP-L3-VW-002増分で修正、再検証した最終全回帰121 suites/3839実行＝3831 PASS＋8 skip、90.39秒、log SHA256 `158330c6b663b60b9c48bfa43878ef8421efda2f17f6636993f2c1c042e18599`。design-context/secret scan・diff check PASS。

## 2026-10-02: saved source inventory/snapshot pinned to directory descriptors — offline

前増分はreport**出力**をfd固定したが、読取側`source_snapshot`は私有runの親を検査した後に`iterdir`/`read_file`をpathで再解決していた。checkout内隔離tempで親pathを検査直後に別treeへのsymlinkへ差し替えると、旧実装が別treeの保存runをhashして戻す負例を再現。source collection列挙・run snapshot・summary file読取を`/`からcomponent毎のno-follow directory fdとfd相対の有界走査・file読取へ変更。`_source`は元run fdをevidence validatorの呼出しまで保持して現pathとのinode照合を行い、途中で親が退避されたら正常なData Planに昇格させない。入力内のfile entryをstat/open間に別inodeへ替える負例も拒否。出力writerやcatalog APIの振舞いは変えない。元directory内部の同UID actor改変、途中で元に戻す競合、producer認証や全catalog網羅は未解決。

実保存Data PlanをAPI不達の**模擬client**でoffline読取すると、従来2026-09-17の5 run/10 edgeではなく、今は7 run/期待14 edgeが検証済み。14件すべて`unknown`、missing0、rate NULL、source snapshots前後一致。これを「現catalogの14/14存在」と読み替えない。metadata API/VMを起動・認証せず、現entity/lineageは未観測。credential・既存report・保存source・backupは書換え/削除しない（読取atime除外せず）。private記録は`tmp/local-experiment/lineage-source-reader-checks/20261002/`。初回smokeでは過去の分母10を期待して失敗し、実際の7/14を確認して修正した。WP `local-partial`。停止・復旧はこのsource reader差分のみ以前のローカルrevisionへ戻し、既存reportや元runは保持する。

focused1 suite/41件PASS、log SHA256 `b702b74d6db90eb41a8c8a7c9e11355b85ff1dcc54a811726ab26855e9158e7f`。全回帰121 suites/3839実行＝3831 PASS＋8 skip、91.09秒、log SHA256 `c987e1b3898ff11a2ed7666ded2add534b48d1fdae63c07e18b42500770305d6`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-10-02: lineage report output pinned to owned directory descriptors

`write_report`の従来の`directory(OUTPUT_ROOT)`確認とpathname `output.mkdir`間に親pathを差し替えると、隔離tempの模擬別treeへ新report JSON/HTMLを作成してしまうことを負例で再現。checkout内の未作成親は開いたdirectory fdから相対作成し、既存親/出力root/新runの所有・mode・inodeを照合する。新runと2固定fileはfd相対作成・fsyncし、最後に現pathと開いたfdのidentityを再照合してからreport pathを返す。差し替え後の模擬別treeは空、元inodeに作成されたreportは失敗時も消さず、手動確認に残す。既存rootへのsymlink親も書込み前に拒否。既存report・catalog・Data Plan保存物・backupは削除/再生成しない。模擬同UID actorが既に開いたprivate root内の新run entryを作成直後に改変する競合と、保存source/catalogの独立認証は未解決。

実metadata API/VMは停止状態のまま使用せず、tempで生成したreportのみの機能検査。従来の歴史的分母10edge/10presentは再観測していない。既存のL5未達（全catalog・常時更新・column lineage・本番/人間受入）を残し`local-partial`。戻す場合は出力writer差分だけを以前のローカルrevisionへ戻し、差し替え負例で作ったprivate runも同一inodeを確認するまで削除しない。focused・全回帰の実測は[Milestones](../061_Local_Experiment_Milestones.md)へ記録する。

## Summary

Lineage充足率を可視化する

## Context

このWork PackageはLevel L5「OSS Fabric再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l5`, `area:metadata`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L5-LINEAGE-001: Lineage completeness dashboard.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and a bounded read-only report for verified historical Data Plan evidence.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17: bounded local implementation plan

依存WP-L1-META-001 / WP-L2-OBS-001はローカル実験で充足している。既存OpenMetadataと
Data Engineerの成功証跡を使い、履歴上のlineage充足状況を読み取り専用で可視化する。
歴史的PR/CI gatesは外部deliveryの条件であり、ADR-0034のlocal実行条件にはしない。

- [x] private Data Plan証跡を列挙し、既存のdbt/Dagster/cleanup検証を通ったrunを特定する。
- [x] 検証済みrunあたりfixture→stg→martの期待2edgesを既知分母にする。
- [x] OpenMetadataの同一identity/owner/schema/lineage観測をpresent/missing/conflict/unknownへ分類する。
- [x] invalid/failed/incompleteな入力を無言で消さず、件数と理由を明示する。
- [x] API不達、取得途中の証跡・entity revision変化、観測上限到達をmissingと混同しない。
- [x] private JSONと自己完結HTMLを生成し、分母・観測時刻・各runの根拠を辿れるようにする。
- [x] 元証跡、保持metadata、稼働環境の前後不変を検証し、実結果を追記する。

この分母はこのcheckoutにある検証済み実験履歴だけであり、組織全体の全table/column/pipelineを
網羅しない。削除済みSQL relationのhistorical catalog recordをlive tableとして表示しない。
ログイン以外はGETのみで、metadata修復/削除・password変更・再取り込みを行わない。
新service/依存パッケージ/LLM呼出は不要。JSON/HTMLはホスト上のprivate証跡で外部公開しない。
初期増分はtable-level 2edge chainの充足可視化であり、任意queryの完全なcolumn lineage、
常時更新dashboard、cross-platform lineage、本番受入を主張しない。

## 2026-09-17: first read-only coverage result

run `4ef6d7943baf4e8c914f32f63381b9c9`（09:09:24 UTC観測開始）は初回で成功。
保存済み5 Data Plansすべてのdbt/Dagster/cleanup証跡を検証し、期待10edges中present6/missing4、
conflict0/unknown0を確認。観測はcomplete、登録率60.0%、coverage completeはfalseと分けた。
未登録は`396f97befadd41b3af49bf4db9be4b1c`と`eb8902c0a4e54c8591dcbfee02550927`の各2edge。
API不達を模擬した実source結合では、分母10を維持したunknown10/missing0/rate NULLとなった。

private JSON/自己完結HTMLを`tmp/local-experiment/lineage-coverage/<run-token>/`に保存。
ブラウザ1280pxで6/10・各分類・既登録/未登録の詳細展開を確認し、document/tableの横はみ出し0、
console error0。検証用loopback serverは終了した。mobile viewportの実画面検証は未実施。

独立監査 `metadata-coverage-preflight/18d58ceea4b34635be2e267336669a9f/verification.json` は
31checks成功。23API endpointsのUUID/version/full semantic hashと全5source projections、
既存6table/6lineage、保護892files（hash/mode/mtime）、資格情報/.envとadmin markerが前後一致。
Metadata3serviceの状態も再観測済み。他のKube/Docker/Pawprints366は直近Iceberg refs監査を参照し、
今回再読したという主張はしない。catalog mutation0、model/cloud呼出0、新service/dependency0。

focused34 tests、全local949件（948成功＋標準環境optional OTel1skip）、設計80documents/14testsが成功。
次は確認した2runの不足履歴を既存Catalog Curatorで限定登録し、新しいreportで変化を再確認する。
常時更新/全catalog/column lineage/本番受入はこの増分の達成条件ではなく、local-partialを維持する。

## 2026-09-17: observed gaps completed with Catalog Curator

初回reportでmissingだったData Plans `396f97befadd41b3af49bf4db9be4b1c` と
`eb8902c0a4e54c8591dcbfee02550927` に既存Curatorを適用し、historical4table/4edgesだけ追加した。
Catalog runs `48c698b868ad4b18bc69c5c3bebc65de` / `1bfb893944c04f98a463484e7386affb` は成功。
各初回は2table/2edgeの4writes、再適用は0writes。同一taskの実Qwen推論は各314入力/96出力tokens、
4106ms / 2950ms。cloud/retry0、cost NULL、2run合計628入力/192出力tokens。
traceは`a5898cccc00b61dd68f7d5a00fecca87` / `8ec47c336227cc55a056a130a960bd5f`。

補完後のread-only report `ea83feab78db4e2189d8adab339af346`（09:16:39 UTC）は5run・10/10edges、
missing/conflict/unknown0、observation/coverageともcomplete。元の60%reportは変更せず保持した。
独立監査 `catalog-completion-audit/fdcf070415da47f082d8e0e5a39521dc/verification.json` の84checksと
metadata比較45checksが成功。既存6tables/6edges/anchorsは不変、新4tables/4edgesだけを帰属させた。
旧Pawprints366行のhash不変＋新10行（現376）、Loki10件/保存実OTel10spansの相関・usageを照合。
保持Kube/3Docker、履歴4tables/5DDL不変。934保護files中933は完全不変、既存認証入口の
admin-initialized markerだけmtime更新（content/mode/.env不変）を既知差として記録した。
reportの全10edge verdictとmetadata aggregate hashも独立APIから再計算済み。

本増分は既存実装を使う実験結果の更新で、直前の全949tests/設計80documents/14testsを継承する。
次はData→Catalogの一連実行と中断再開を専用の小さな入口へまとめる。Data開始intentと保存先を
先に固定し、同handoffでDataを二度呼ばず、成功証跡を検証できた場合だけ同Data PlanをCatalogへ渡す。
