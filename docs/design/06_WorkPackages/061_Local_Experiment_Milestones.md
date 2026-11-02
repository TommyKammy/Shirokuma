---
project: Shirokuma
doc_id: "WP-LOCAL-MILESTONES"
title: "Local Experiment Milestones"
status: active
created: 2026-09-16
updated: 2026-11-02
version: "7.33.0"
area: work-packages
tags: [shirokuma, local, milestones]
---

# Local Experiment Milestones

> [!NOTE]
> **2026-10-16 Strategic Reset ([ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) / [DEV-04F](../04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md))**
> The 12,463-line granular session ledger from the September–October 2026 autonomous runner series (523 sessions) has been preserved intact in [`archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md`](archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md). Going forward, only concise, human-readable milestone summaries are recorded here.

---

## Active Roadmap Milestones (Phase 0 – Phase 6)

### 2026-11-02 — DB-P6-4C2C1 固定LOCAL宣言基準（限定技術受入）

- [C2C1契約](../../guides/database-p6-4c2c1-workload-baseline-2026-11-02.md)：真P専用の固定2 Pod-template・全ordinary／initについて、旧4項目の宣言意図だけを手動確認する。native graphのDB-P6-3C2Cとは別単位である。
- nonRoot=trueかつUID未指定を旧ruleどおり許し、met／not_metと原子的なsource確認不可を区別する。8 admission policy・global policy全体やaggregate PASS、操作許可を表さない。
- C2C1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,232成功・34 skip、両HTTP面の実APIとWorkbenchの3面表示、最終保全PASS）。3面の明示共有、現在認可、既存状態と全旧行の保全・新規行0を確認した。
- usage／AI設定より先に進めたのは今回のCommander判断であり、C2C全体・P6-4全体／P6-5全体／P6-7／M1本人受入は未完了を維持する。

### 2026-11-02 — DB-P6-5C Compute DB saved activity（限定技術受入）

- [P5C契約](../../guides/database-p6-5c-contract-2026-11-02.md)：既存AUD-03のquery／job_runだけをCompute専用の手動一覧・前後pageで読む。live activity・詳細・実行／復旧は含めない。
- 独立したrole／READY DB／self・database scope／filter・cacheを使い、既存の現在認可とeffective DB admin例外を維持する。backend・API・policyは変更しない。
- P5Cの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,188成功・34 skip、既存AUD-03の実APIとWorkbenchの実画面、最終保全PASS）。既存行・UNKNOWN・設定を保持し、新規行0を確認した。
- Auditのdraft／pageとP5A／P5Bを分離し、P6-4全体／P6-5全体／P6-7／M1本人受入、C2C・usage／capacity・AI設定は未完了のままとする。

### 2026-11-02 — DB-P6-5B configured LOCAL container resources

- [実装・検証記録](../../guides/database-p6-5b-platform-resources-2026-11-02.md)：固定2 Deploymentの全ordinary／initコンテナ設定を、種類別ordinalと4つのliteral quantity／nullで扱う。
- 独立した入力なしの明示GETとCompute専用panel・P cacheを対象とし、真P・上限admin・選択adminをsource読取り前後に検査する。既存C2Bを変更しない。
- P5Bの限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,166成功・34 skip、両HTTP面の実API・Workbenchの実画面、最終保全PASS）。 重点backend111件・UI113件・全Workbench事前回帰936件もそれぞれ成功した。
- P6-4全体／P6-5全体／P6-7／M1本人受入はOPEN。C2C／global policy、usage／capacity／activity、resizingは別タスクとする。

### 2026-11-02 — DB-P6-4D1 saved AI audit metadata

- [実装・検証記録](../../guides/database-p6-4d1-ai-audit-metadata-2026-11-01.md)：既存AUD-03へ`source=ai`と閉じた5種のactionを追加した。既存event形状・`links=[]`を使い、内容fieldを増やさない。
- 最新保存要約だけを投影し、現在のself／database認可と保存full9の内部整合を検査する。platform scopeはAIを除外し、本文の表示・検索、AI取得・復旧、書込みを含めない。
- D1の限定範囲を2026-11-02 JSTに技術受入済みとした（全体回帰4,135成功・34 skip、Workbenchの実API・実画面、最終保全PASS）。 重点backend32件・重点UI32件・全Workbench923件はそれぞれ成功した。
- C2C／P5B・AI設定とP6-4全体／P6-5全体／P6-7／M1本人受入は継続する。

### 2026-11-01 — DB-P6-5A read-only Compute platform observations

- [実装・検証記録](../../guides/database-p6-5a-compute-observations-2026-11-01.md)：Compute画面へ受入済みC2BのP専用固定LOCAL観測を統合した。既存API・strict DTO・global P cacheを再利用し、DB contextやgrantを前提にしない。
- 明示GETだけでsourceを読み、SRE／Policy／Compute間のsnapshot・観測時刻を共有する。既存Warehouse操作や旧SRE check／Policy baselineを開放しない。
- 重点回帰・両HTTP面・実画面、同じ認可でのdraft保全と権限喪失時の保護表示消去、最終保全を確認した。P6-5Aの限定範囲を技術受入済みとした（全体回帰4,122成功・34 skip、両HTTP面の実APIと権限拒否、Computeの実画面、最終保全PASS）。
- C2Cのbaselineは独立後続とし、P6-4全体／P6-5全体／P6-7／M1本人受入は継続する。

### 2026-11-01 — DB-P6-4C2B fixed P-only platform observations

- [実装・検証記録](../../guides/database-p6-4c2b-platform-observations-2026-11-01.md)：現在のP account・principal・選択adminを前後で確認する独立GETを実装した。DB contextやgrantを前提にしない。
- 固定2 Deploymentのdesired／ready要約、過去のengine検証日時、保存AutoChill設定、LOCALの保存admission件数だけを扱い、現在のSQL health・完全停止・全blocker検査を主張しない。
- C2AのDB観測と各draftを維持し、重点回帰・実API・JA／ENの実画面・復元と最終保全を確認した。C2Bの限定範囲を技術受入済みとした（全体回帰4,109成功・34 skip、両HTTP面の実APIと権限拒否、Pの実画面、復元と最終保全PASS）。
- global workload／policy検査と旧run／manifest・Compute検査は別単位とし、P6-4全体／P6-5／P6-7／M1本人受入は継続する。

### 2026-11-01 — DB-P6-4C2A own-context SRE and Policy observations

- [実装・検証記録](../../guides/database-p6-4c2a-context-observations-2026-11-01.md)：独立した各Bearのcontextで、現在の本人・保存9項目context・primary schema権限を検査し、本人のquery／AI保存metadataを各最大20候補の窓から取得する。
- ID・kind・保存状態・保存時刻だけを表示し、省略数と古い候補の有無を示す。Policyは現在権限・SQL mode上限だけで、全roleにplatform未観測理由を示す。SQL・LLM・照合・書込みや他のdraft／未解決要求を変更しない。
- 重点backend335件・UI889件、実API・admin実画面と復元を確認し、最終8ファイルで全体回帰を完了した。
- C2Aの限定範囲を技術受入済みとした（全体回帰4,073成功・34 skip、2DB・3実行ロールの実API、未付与権限の拒否、adminの実画面、最終保全PASS）。P専用platform観測・global policy検査は後続とし、P6-4全体／P6-5／P6-7／M1本人受入は継続する。

### 2026-11-01 — DB-P6-4C1 authorized catalog explanation and description drafts

- [実装・検証計画](../../guides/database-p6-4c1-catalog-proposals-2026-11-01.md)：独立したCatalog Curator contextから、本人が選ぶprimary schemaのTABLE／VIEW 1件の現在権限・安定Ref・UUID・列構造を確認する。
- 名前・列名・型だけでLOCALへ要約と未適用の説明文案を要求し、列文案は最大8列の部分的な提案と表示する。SQL・Engine・Wake・catalog metadata変更や他の画面の入力書換えは行わない。
- 元要求の明示復旧・履歴・取消とUNKNOWNを保護する。C1の限定範囲を技術受入済みとした（全体回帰4,020成功・34 skip、2DB・3roleの実API、adminの実画面、最終保全PASS）。SRE／Policy・lineage・説明保存は別単位で、P6-4全体／P6-5／P6-7／M1本人受入は継続する。

### 2026-11-01 — DB-P6-4B2 explicit Manual WAP draft handoff

- [実装・検証計画](../../guides/database-p6-4b2-wap-handoff-2026-11-01.md)：成功済みB1案の元9項目contextと、明示選択した同一DB・同一E／A roleのSANDBOX実行先contextを検査し、使用TABLEの識別情報・列とsource aliasを確認する。
- metadataだけのGETから、編集中・未解決操作のない新規WAPフォームへ未保存下書きを明示取込する。AI・SQL・WakeやWAP保存／実行は自動化せず、以後は既存の版保存・build・audit・A公開を使う。
- B2の限定範囲を技術受入済みとした（全体回帰3,944成功・34 skip、2DBの実機・画面・最終保全PASS）。次は他Bearsの必須導線の統合。P6-4全体／P6-7／M1本人受入は未完了。

### 2026-11-01 — DB-P6-4B1 authorized metadata SQL draft

- [実装・検証計画](../../guides/database-p6-4b1-sql-proposals-2026-11-01.md)：現在の本人・保存9項目context・明示選択した同一DBの1〜8件のTABLE／VIEWから、原文の依頼文と許可された名前・列名・型だけでLOCAL SQL案を生成する。VIEW定義・行データは対象外。
- 単一read-only SELECTの構文・参照範囲・policy検査と独立した復旧・履歴・取消を扱い、SQL・Engine・Wake・WAPの実行やeditor／結果の書換えは行わない。
- B1を技術受入済みとした（全体回帰3,892成功・34 skip、2DB・3role実機・最終保全PASS）。B1受入時点の後続B2は上記で受入済み。P6-4全体／P6-7／M1本人受入は未完了。

### 2026-10-31 — DB-P6-4A5 frozen collected plan re-explanation

- [実装・検証記録](../../guides/database-p6-4a5-saved-plan-explanation-2026-10-31.md)：元A2／A3のCOLLECTED計画と保存値・実台帳がFINISHEDの元queryを検証し、取得時点の計画・metadataだけで新しいLOCAL説明を要求する。現在の本人・保存9項目context・全凍結依存schema権限が条件。
- 元親のFAILED／CANCELED／UNKNOWNを変更せず、catalog・Engine・Wake・admissionを呼ばない。独立した元key復旧・履歴・取消とUNKNOWN保持を実装。重点backend198件・UI190件、全体回帰3,824成功・34 skip、2DB・3roleの休止中実機確認・最終保全照合を完了。
- 次はSQL案生成、既存WAPへの明示引渡し・他Bears。P6-4全体／P6-7／M1本人受入は未完了。

### 2026-10-31 — DB-P6-4A4 latest SELECT source for Query Doctor

- [実装・検証記録](../../guides/database-p6-4a4-latest-query-source-2026-10-31.md)：本人・保存9項目context・全参照schema権限が一致する通常read-only SELECTを明示GETでDoctor入力へ読み込む。SQL・AI・Wake・catalogは呼ばない。
- 元queryのID・時刻・状態と入力の遅着ガードを追加。未解決A1／A2を保持し、A1の元ID／元key・bodyを既存の明示復旧操作で確認する。重点backend116件・UI168件、全体回帰92単位（3,779成功・34 skip）と2DB・3roleの実機・画面、既存状態の保全を確認。
- A4時点の後続だった保存済み計画の明示再説明はA5で拡張。SQL案／WAP・他Bears、P6-4全体／P6-7／M1本人受入は残件。

### 2026-10-31 — DB-P6-4A3 compatible INVOKER VIEW plan explanation

- [実装・検証記録](../../guides/database-p6-4a3-view-plan-explanation-2026-10-31.md)：`logical_v1`の対応するnative／外部登録INVOKER VIEWへ計画説明を拡張。再帰依存の同一DB・全schema権限、VIEW head、整数snapshotのTABLE／schema証拠を検査し、元SQLは実行しない。
- 実機・画面で重点検証した。全体回帰91単位（3,719成功・34 skip）に加え、回帰開始後の修正を最終重点backend204件・UI80件で確認し、この限定範囲を技術受入済みとした。DEFINER・他DB・履歴schema差分・非整数time travel、他Bears／SQL案・WAP連携とP6-4全体／P6-7／M1本人受入は残件。

### 2026-10-31 — DB-P6-4A2 authorized TABLE plan explanation

- [実装・検証記録](../../guides/database-p6-4a2-plan-explanation-2026-10-31.md)：Query Doctor／Copilotへ固定の非ANALYZE EXPLAINと安全な演算子構造のローカル説明を接続。元SQLは未実行。
- 2DB・V/E/A、取消・cold Wake・保存後restart・入力変更／再読込の元要求復旧を実機確認。既存全行・28 data head・設定を保全した。canonical 90単位（3,672成功・34 skip）と最終重点・文書検証を完了し、この限定範囲を技術受入済みとした。
- VIEW計画・他Bears／SQL案とWAP連携は後続。P6-4全体／P6-7／M1本人受入は未完了。

### 2026-10-31 — DB-P6-4A1 LOCAL SQL explanation

- Added Query Doctor/Copilot SQL interpretation with full9 actor/schema authority, literal/comment redaction, local inference and durable idempotent history; no SQL, plan or admission execution.
- Real A/admin/8888 and B/engineer/8080 explanations pass. Equal-context different actors, cross-DB input, delayed editor changes, timeout and process-owner loss preserve isolation and never auto-infer on recovery.
- Both endpoints resolve active-profile AI settings; no credentials copied. Existing rows in 78 control-store tables, 28 data heads, original profile/global config/YAML remain intact; doctor is healthy. Legacy default-store lazy schema23 migration is recorded separately; its test is now isolated. Canonical:89 groups,3,595 pass/34 skip.
- [Evidence](../../guides/database-p6-4a1-local-sql-explanation-2026-10-31.md). Plan explanation is tracked separately in A2; full DB-P6-3/P6-7 and owner M1 acceptance remain open.

### 2026-10-31 — DB-P6-3R1 indeterminate native graph audit recovery

- Added a platform-operator path for an ownerless native_graph publication COUNT audit, requiring the frozen native proof, exact publisher/context/children, all owner locks and existing logical managed-compute stop proof. Publication/query outcomes remain UNKNOWN; verified execution abandonment is a separate API/UI fact. A deterministic GET/recovery race was reproduced and fixed by rechecking the marker under the DBT owner lock. See the [contract and evidence](../../guides/database-p6-3r1-dbt-audit-recovery-2026-10-31.md).
- Actual same-operation recovery fenced the two planned Trino containers, resumed partial-stop/proof stages and restored OPEN. Independent comparison of all 78 ledger tables found only planned recovery changes, retained every original query/SQL row, two old fences and all 28 RAW/MARTS heads. GET/list/original-key recovery, historical source changes and truthful JA/EN/390px/viewer behavior passed; normal services, policy and 46 doctor checks were restored.
- Final staged canonical evidence covers 88 groups: 3,535 tests, 3,501 passed, 34 existing skips, with five affected groups rerun after the final change. The wildcard includes all 29 recovery tests; UI has 97 tests. Existing Go and documentation checks passed. This is limited operator recovery, not arbitrary write abandonment or automatic recovery; full DB-P6-3 and M1 owner acceptance remain open.

### 2026-10-31 — DB-P6-3C2E2 VIEW graph TABLE publication

- Connected complete VIEW/TABLE/EPHEMERAL native graph proofs to explicit per-TABLE MARTS publication, matching frozen VIEW preview/ACK summaries and independent publication collection/direct links. Actual browser testing found and fixed publication-only URL startup. See the [contract and evidence](../../guides/database-p6-3c2e2-view-graph-publication-2026-10-31.md).
- Actual A/UI and E-built B/CLI publication, cold resume/dropped ACK/same-key recovery, separate TABLE publication, a fresh A zero-row refusal, and post-audit B VIEW drift refusal all passed. Source cancellation and a new target head did not invalidate published history. Five ordinary publications (three PUBLISHED, two FAILED) and one independently recovered UNKNOWN passed wire/private proof; original 25 heads and two fences remain, with no new unclosed execution.
- Normal endpoints expose only lake_marts again, preserve 13 worksheets/SQL drafts and owner-local YAML/profile bytes. One preexisting committed-recording-failed observation timestamp was refreshed by normal GET, explicitly recorded as the sole timestamp exception. Next priority is DB-P6-4A after a short TRF-11 mandatory-operation mapping check; advanced native extensions remain separate.

### 2026-10-31 — DB-P6-3C2E1 native graph INVOKER VIEW

- Added actual RunTask/TestTask execution of supported VIEW/TABLE/EPHEMERAL graphs, including VIEW-to-TABLE dependencies and quality tests. Run-specific SANDBOX VIEWs require INVOKER configuration, checked callback SQL, fixed inputs and distinct VIEW identity/definition proof. See the [contract and evidence](../../guides/database-p6-3c2e1-native-views-2026-10-31.md).
- Real CLI and Workbench acceptance passed all seven saved tests in each of two databases. Cancellation retained one completed VIEW and sent no downstream SQL; an intentionally changed new VIEW was rejected without re-execution. Independent proof covered 23 native child/query records, six fresh SELECTs, role/database/actor isolation, preserved 25 original RAW/MARTS heads and restored normal services with 46 healthy doctor checks.
- All 87 canonical groups passed in stages: 3,464 tests, 3,430 passed, 34 existing skips. Go and documentation checks passed. Publication from any VIEW-containing native graph remains blocked; C2E2 will freeze VIEW definitions and transitive dependencies into publication intent/history. Existing TABLE/EPHEMERAL graph publication and SQL/WAP remain; full DB-P6-3 and M1 owner acceptance are open.

### 2026-10-31 — DB-P6-3C2D native graph TABLE publication

- Connected complete native graph build and all saved quality-test proofs to explicit per-TABLE MARTS publication through Workbench/API/CLI. Current admin reviews the frozen target and performs a fresh row audit >= 1; other TABLEs remain separate operations. Source and publisher identities, original-request recovery, and historical publication proof remain distinct. See the [contract and evidence](../../guides/database-p6-3c2d-graph-publication-2026-10-31.md).
- Real acceptance covered 3 graphs, 6 publications, cold CLI execution with a dropped acknowledgement and same-key recovery, browser publication, empty audit, source cancel, target/candidate drift refusal, and fresh two-database MARTS results. Independent ledger/query/SQL/catalog proof validated 34 children, preserved the original 22 RAW/MARTS heads and existing history/configuration, and found no new unclosed admissions. Normal services and 46 healthy doctor checks were restored.
- All 86 canonical groups passed in stages: 3,442 tests, 3,408 passed, 34 existing skips. Go and documentation checks passed. Native VIEW/incremental/parallel execution, direct SDK candidate replacement, manual-WAP rollback integration, full DB-P6-3 and M1 owner acceptance remain separate follow-ups.

### 2026-10-31 — DB-P6-3C2C native RunTask graph

- Added explicit native_graph execution of complete supported TABLE/EPHEMERAL DAGs through installed RunTask, with optional TestTask for the complete saved suite, including source tests and TABLE relationships. SQL/WAP and single-native/B5 publication remain. See the [contract and evidence](../../guides/database-p6-3c2c-native-graph-2026-10-31.md).
- E/CLI/B passed 6 TABLEs and 10 tests from cold; A/UI passed 3 TABLEs/3 EPHEMERAL and completed all 10 tests, detecting one existing RAW value violation. SQL failure/SKIPPED, cancellation, owner loss, replay, two-Database results and preservation passed runtime acceptance. Original 22 RAW/MARTS heads and configurations remain; normal endpoints restored, doctor healthy.
- Technical acceptance: all 85 canonical groups passed in stages, 3,379 passes and 34 existing skips, including final 33 graph tests; Go/design checks passed. Graph publication, native VIEW/incremental, parallel execution, full DB-P6-3 and M1 owner acceptance remain separate.

### 2026-10-31 — DB-P6-3C2B5 explicit native TABLE publication

- Added metadata-only publication preview and exact reviewed target through Workbench/API/CLI. A publishes an E-built native quality-PASSED candidate to same-Database MARTS after a real min-one-row audit. Native origin remains distinct in dbt/DataOps receipts, without forged manual-WAP artifacts. See the [contract and evidence](../../guides/database-p6-3c2b5-native-publication-2026-10-31.md).
- E/A, two databases, new and existing targets, empty audit/cold resume, exact-request replay, JA/EN/viewer and preservation passed runtime acceptance. All 83 canonical test groups passed in stages after updating one obsolete B4 assertion; the final workflow module passed 90 tests. Current scope: 3,272 passed and 34 existing skips; Go/design checks passed. Full graph/RunTask, native VIEW, direct SDK candidate replacement, manual-WAP rollback integration, full DB-P6-3 and M1 owner acceptance remain separate.

### 2026-10-30 — DB-P6-3C2B4 native quality tests

- Added explicit native_tests opt-in through API, real CLI and Workbench; installed TestRunner checks the complete attached suite against the original candidate snapshot. Build proof and quality status remain separate; legacy NOT_RUN requests and publication prohibition remain. See the [contract and evidence](../../guides/database-p6-3c2b4-native-quality-tests-2026-10-30.md).
- E/A, two databases, cold resume, quality failure, SDK exit after SELECT, silent-SDK cancellation and owner loss passed actual runtime checks without replay. Preserved original metadata, credentials, WAP rows and 20 RAW/MARTS heads.
- Technical acceptance passed: 3,307 Python tests, 34 existing skips, Go and design checks. Standard RunTask/full graph, replacement, native VIEW/publication, full DB-P6-3 and M1 owner acceptance remain separate.

### 2026-10-30 — DB-P6-3C2B3 Workbench native TABLE selection

- Added explicit execution-method and single-model selection, per-model eligibility reasons, and original-intent recovery. SQL/WAP remains the default; native results remain tests NOT_RUN and publication unavailable. See the [UI contract and evidence](../../guides/database-p6-3c2b3-native-ui-2026-10-30.md).
- Two admin-owned databases passed actual UI native execution with one child/CREATE each, including cold resume and ALPHA/BETA isolation. Existing SQL/WAP, viewer restrictions, other-owner refusal, read-only reload/history, Japanese/English and 390px layout passed; preserved original metadata, credentials, configuration and RAW/MARTS heads. Technical acceptance passed: 3,281 Python tests, 34 existing skips, Go and design checks.
- Standard RunTask/graph, native tests, replacement and publication remain separate. Full DB-P6-3 and M1 owner acceptance stay open.

### 2026-10-30 — DB-P6-3C2B2 saved native TABLE runs

- Connected explicit single-node native TABLE selection to durable runs, API and the real CLI; isolated the SDK process while keeping original credentials and typed SQL in parent DataOps. See the [contract and evidence](../../guides/database-p6-3c2b2-native-runs-2026-10-30.md).
- E/A, two databases, cold resume, source pins, SDK failure/silence, cancellation and abrupt owner recovery passed without replay. History distinguishes NATIVE_VERIFIED, tests not run and publication unavailable; Technical acceptance passed: 3,268 Python tests, 34 existing skips, Go and design checks.
- Native launch UI, standard RunTask/graph, native tests and replacement remain separate. Full DB-P6-3 and M1 owner acceptance stay open.

### 2026-10-30 — DB-P6-3C2B1 native TABLE materialization bridge

- Added a private installed ModelRunner/TABLE macro callback into existing DataOps children for one fresh SANDBOX candidate. See the [scope and proof](../../guides/database-p6-3c2b-native-materialization-2026-10-30.md).
- Real E/A and two-database candidates, pinned source after append, target rejection, post-CREATE failure/cancel and abrupt owner recovery passed without replay; credentials, policy and original data stayed unchanged. Technical proof acceptance passed: 3,244 Python tests with 34 existing skips, Go and design checks.
- This proves native materialization only. Standard RunTask/schema/cache/graph, native tests, replacement and normal API/CLI/UI exposure remain separate; full DB-P6-3 and M1 owner acceptance stay open.

### 2026-10-30 — DB-P6-3C2A sandbox INVOKER VIEWs

- Added actual run-specific VIEWs with TABLE snapshot pins, separate VIEW identity/head receipts, before/after dependency checks and TABLE-only admin publication. See the [contract and evidence](../../guides/database-p6-3c2-sandbox-views-2026-10-30.md).
- Live API/CLI/UI checks passed, including two-database test failure, metadata failure, cancellation, head mutation and abrupt owner death without SQL replay. Only same-catalog SANDBOX CreateView was added for scoped admin; credentials, grants and original data were preserved. Technical acceptance passed: 3,219 Python tests with 34 optional skips, Go and design checks.
- Standard dbt executor, VIEW chains/formal VIEW publication, full DB-P6-3 and M1 owner acceptance remain open.

### 2026-10-29 — DB-P6-3C1 SQL/WAP configuration compatibility

- Added saved, versioned compatibility diagnostics and fixed typed PARQUET for candidate CTAS and publication CTAS/CRTA. See the [contract and verification record](../../guides/database-p6-3c-dbt-compatibility-2026-10-29.md).
- Unsupported materialization/model/test settings are rejected with reasons; legacy plans require recompilation for new runs while admitted histories and receipts remain usable. Technical acceptance passed: 3,194 Python tests with 33 existing skips, Go tests/vet, design checks and live API/CLI/browser/format verification.
- Standard dbt executor and true VIEW identity/authority/lifecycle remain C2 work. Full DB-P6-3 and M1 owner acceptance remain open.

### 2026-10-29 — DB-P6-3B durable dbt SQL / WAP integration

- Connected approved SQL projects to durable plans/runs, server-enforced test gates and normal API/CLI/Workbench. See the [implementation record](../../guides/database-p6-3b-implementation-2026-10-29.md).
- Live checks covered pinned source versions, two-database isolation, explicit admin publication, same-key receipt recovery, cancellation/revocation, owner loss and committed recording failure. Technical acceptance passed: 3,174 Python tests plus 33 existing skips, Go tests/vet, design checks and normal-process browser verification.
- Native dbt executor/view compatibility, the full DB-P6-3 scope and M1 owner acceptance remain open. Table/ephemeral SQL support does not imply those milestones are complete.

### 2026-10-29 — DB-P6-3A dbt compile and WAP feasibility

- Added a private offline SQL compiler and exact same-DB relation bindings, with table/ephemeral support and unchanged legacy defaults. See the [feasibility record](../../guides/database-p6-3a-dbt-feasibility-2026-10-29.md).
- Two dedicated databases each built two WAP candidates and passed all seven compiled tests; explicit audit/publication preserved separate values. A failing accepted-values test left published snapshot lists and values unchanged.
- Continue with DB-P6-3B durable runs, server-enforced test gates and normal API/CLI/UI. Native dbt build, view materialization and M1 acceptance remain open.

### 2026-10-29 — DB-P6-2 shared LOCAL lifecycle — technically accepted

- Implemented P-only Wake/Chill/Revalidate, Auto-Chill policy CAS, server-side idle handling and authorized SQL auto-resume through API/CLI/Workbench. See the [implementation and verification record](../../guides/database-p6-2-implementation-2026-10-29.md); Technical acceptance passed: 30/30 cold queries, p50 26.44s and p95 26.92s against the 30s target.
- Warm 30-run sample p95 was 203.5ms versus 168.2ms before the change (35.3ms difference). Bounded read-only recovery preserved UNKNOWN, fenced execution and restored OPEN; The original 138 databases, 552 schemas and bindings were preserved; one bounded acceptance database was added.
- Proceed to DB-P6-3 dbt feasibility. Bears/Copilot, SRE/Policy, actual sizing, multi-Warehouse and M1 owner acceptance remain open; legacy Database profiles are not implicitly upgraded.

### 2026-10-29 — DB-P6-1 Database audit technical acceptance completed

- Added read-only persisted-metadata audit APIs, HTTP CLI, scoped search/detail/related metadata/JSON and an independent P-only Pawprints preview with bounded filters. See the [contract and verification record](../../guides/database-p6-1-implementation-2026-10-29.md).
- Full regression: 2,862 passed, 33 existing skips, zero failures; Go tests/vet and live API/CLI/browser checks passed. Audit GETs preserved the ledger digest. Live Pawprints reports `TELEMETRY_UNAVAILABLE`; dedicated fault acceptance remains separate.
- Next unit is DB-P6-2 shared-Warehouse suspend/resume. M1 owner acceptance and remaining mandatory feature rows stay open.

### 2026-10-29 — DB-P6-0 capability inventory and accurate UI completed

- Fixed supported-feature notices, localized disabled reasons and per-panel role/context guidance; preserved action gates and receipt focus during unchanged refresh. The [89-row capability matrix](../../guides/database-capability-matrix-2026-10-29.md) distinguishes DB/platform authority and shared WAP metadata from caller-bound execution.
- Verified 470 Workbench tests plus 39 authentication/inventory tests, role-specific live API reads, Japanese/English browser flows and 390px layout. Read-only warm SQL succeeded 30/30 with median154ms and sample p95 292ms ([implementation and evidence](../../guides/database-p6-0-implementation-2026-10-29.md)).
- Next is DB-P6-1 scoped audit. Database creation latency improvement and M1 owner acceptance remain separate; this unit does not enable the remaining unsupported features.

### 2026-10-28 — Database core integration and compute expansion planned

- Created the [DB-P6–9 implementation plan](../../guides/database-full-capability-and-compute-plan-2026-10-28.md): integrate Database transformations, Bears, scoped audit, governance and shared-compute lifecycle before resource sizing and Worker scaling.
- Defined M1 technical and owner acceptance, then conditional multi-Warehouse and independent external-S3/Routine tracks. Current LOCAL/XS/S aliases and disconnected Database auto-chill/resume paths are recorded as implementation gaps.
- Next unit is DB-P6-0 capability contracts and accurate UI, followed by scoped audit. The [current creation flow](../../guides/database-logical-create-owner-acceptance-2026-10-28.md) has conditional owner acceptance with latency improvement continuing; no feature implementation or deployment is claimed by this planning entry.

### 2026-10-27 — ADR-0038: Bounded feasibility complete; successor contract drafted

- Isolated real-stack tests covered shared Polaris/STS storage boundaries, hot catalog and secret references, restart persistence, worker physical I/O, and native nonroot PVC publication ([evidence](../../guides/database-adr0038-feasibility-2026-10-27.md)).
- Select shared catalog/credential vending for the next implementation. The [successor contract](../../guides/database-logical-create-contract-2026-10-27.md) separates foundation revision, DB binding/auth revisions, final CAS, UNKNOWN reconciliation and explicit resume.
- The owner waived mandatory existing-DB preservation; clean reconstruction is the default design. Product hot authentication/authorization, CLI/WebUI, latency percentiles and P5-7 owner acceptance remain incomplete.

### 2026-10-27 — P5-7: Owner acceptance held for database-creation redesign

- The owner completed two new databases through READY/SQL, Job auto-observation and role restoration, then identified creation steps, approvals and multi-minute processing as blocking despite clearer UI guidance.
- [ADR-0038 (proposed)](../07_ADR/ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md) separates ordinary logical database creation from platform provisioning. Hot catalog, dynamic credentials/storage isolation and DB-level runtime revisions require an isolated real-stack proof before choosing the implementation; second-scale performance is a target, not evidence.
- P5-6 remains dated technical evidence. P5-7 and overall owner acceptance remain on hold; existing databases, grants and unresolved-operation history are retained ([owner record](../../guides/database-p5-7-owner-acceptance-2026-10-27.md)).

### 2026-10-27 — P5-6: Technical acceptance complete; P5-7 owner acceptance pending

- OPA4 demonstrated the real query's OPA timeout/FAILED result. TLS4 demonstrated original-CA x509 rejection with a healthy expected ALT peer; the sealed operator SQL was dispatched once but incomplete with no backend ID, so it is not reported as SQL zero or engine FAILED. Both remained UNKNOWN without false READY, effective grants or OPEN, then restored the original resources/socket and completed normal STOP/MAX_ONE0. Independent causal/preservation reviews passed 29 and 26 grouped checks.
- Current source201/material147/test101/binaries and the saved whole result (2354 passed, 33 existing skips) match. PRIMARY's known Go/HTTP processes and rev6 canonical remain present; no new SQL or runtime mutation was added. The existing `rtk make verify-design-context` passed once with exit0: 11 plus 3 tests and design-context validation of 93 documents, 10 issues and 42 references. P5-A01–19 technical acceptance is complete. P5-7 owner acceptance remains separate and unperformed; historical quarantined CLAIMED/UNKNOWN and setup HOLDs remain preserved. No new commit was created ([current matrix and final evidence](../../guides/database-p5-6-acceptance-2026-10-25.md#実証json索引)).

The following dated entries retain historical progress; use the latest entry and acceptance matrix for current status.

### 2026-10-26 — P5-6: Database lifecycle technical acceptance (In Progress)
- Confirmed real ACK-loss recovery, public observation with zero new sends, bounded recovery, and separately approved normal FULL to all four databases READY／OPEN.
- The separate FULL approval reuses the same candidate at current op/root revisions; no second candidate prepare or completed verification SQL replay is required ([P5-0 contract](../../guides/database-p5-0-contract-2026-10-23.md)).
- Preserved the failed source147-r4 run and fixed the Python 3.9 import in two lines. The separate standard source147-r5 run passed all 76 groups (2323 executed, 33 existing skips; all source/test/binary pins unchanged). Remaining negative acceptance is pending; P5-6 technical acceptance is not complete ([acceptance record](../../guides/database-p5-6-acceptance-2026-10-25.md)).
- Completed dedicated-clean A16/A17, verified the normal grant cleanup FULL, and retired the three owned databases without purge; A14 stale-apply/new-PLAN refusal and RETIRED persistence after fresh R5 startup passed.
- Corrected the retired creation card's false READY display (48 focused tests and real reload passed). Source147-r6 whole verification was stopped after a separate retired-baseline preparation bug; its partial result and the original CLAIMED request remain preserved.
- The four-file normal-retirement proof fix passed 95 focused tests; its new callback code is private diagnostics only. The separate standard source147-r7 whole run passed all 76 groups (2364 declared, 2331 executed, 33 existing skips; frozen inputs preserved).
- In an independent clean scope, separate bootstrap/FULL approvals reached READY with real WebUI SQL. Normal baseline retirement (DB revision 1→3) then allowed the second PREPARE. The actual owner crash during DRAIN, native-context cancellation, FD fence, same-record restart refusal, exact normal STOP, and history/material preservation passed. Snapshot publication wrote 61 S3 objects once while preserving the old 151; later ToFu/consumer/SQL steps did not run. Earlier HOLDs and the quarantined OWNER CLAIMED request remain distinct evidence.
- A07's STALE/PENDING/REGISTERED scopes passed safety-stop and preservation checks. The pending grant stopped the first WebUI PREPARE without new plan/material/receipt; its historical CLAIMED request remains preserved and receives no retroactive proof. The first OPA ARM held at the socket-length guard before approval or fault injection.
- Added private terminal proof for a positively known prejournal failure, explicit same-root REPLAN, and never-applied desired-grant cancellation. Multiple normal FULLs of the same generation are validated without replacing an existing cancellation's original evidence. Final focused suites passed 207 plus 77 tests; the OPA-r2/TLS and recovery helper suites separately passed 142 each. Code checks and real acceptance are recorded separately.
- A fresh recovery scope passed WebUI known-failure COMPLETE → normal CLI revoke CANCELED → normal restart with the same logical enrollment revision and fresh owners → same-root REPLAN sequence2 → separate WebUI bootstrap/FULL approvals → APPLIED17 and READY. Raw rows, audit, revocation epoch, and canceled projection persisted. Real runtime ACK2, verification SQL6, one five-type WebUI query, and actual BI denial passed. The next normal CREATE was accepted and retained as an unprepared REGISTERED intent with reservation1; the recovered READY root retained reservation0. Normal STOP/MAX_ONE0 preserved all 70 tables, 556 private files, volumes, and network ([13-proof index](../../../var/shirokuma/p5-6-prepare-recovery-acceptance/private/actual-prepare-recovery-same-root-WebUI-CLI-restart-final-acceptance-index-PASS-v1.json)). No receipt was added to any historical CLAIMED request.
- PRIMARY's current-cohort normal rev6 refresh passed [24 checks](../../../var/shirokuma/p5-6-acceptance/private/existing/final-current-cohort-primary-normal-rev6-v1/PRIMARY-final-current-source-normal-rev6-SQL1-preservation-PASS-v1.json): old70 tables/private65/auth4/physical84 remained unchanged, with only three new query/context/admission rows from one normal CLI SQL. A [real WebUI GET reload](../../../var/shirokuma/p5-6-acceptance/private/existing/browser/final-preservation-rev6-v1/browser-readonly-preservation-PASS-v1.json) preserved both original drafts, role ceiling, and Japanese locale without another SQL submission.
- OPA2's first ARM held after acquiring its proxy handle because the coordinator incorrectly required the rtk wrapper PID to equal its actual child PID. No FULL approval, release delivery, OPA fault, or FULL SQL occurred. Its original intent was retained without re-ARM; normal STOP preserved the 70 tables, original private contents, four volumes and network, with MAX_ONE=0. A fresh OPA3 identity-only clone of the same nine helpers passed native142 once while preserving source201 and the old nine files ([evidence](../../guides/database-p5-6-acceptance-2026-10-25.md#実証json索引)). Setup HOLD and helper tests do not count as actual OPA/TLS fault acceptance.
- 2026-10-27 update: OPA3 held before command selection and injected no fault. After helper HOLD/restoration, one normal FULL approval was canonically recorded and reached APPLIED17, READY, effective DE and six completed SQL checks; that positive result is distinct from the helper's HOLD/UNKNOWN. No release, OPA signal, re-ARM or repeated approval occurred. Normal STOP/MAX_ONE0 preserved 70 tables, 599 private files plus original SSH log prefixes, volumes and network. The missing first frame, unobserved client exit and unused receipt leaf slots are not treated as success evidence. OPA/TLS fault acceptance and final design verification remain incomplete.
- New OPA4/TLS2 private cohorts preserve the old18 helpers and source201. A bounded four-file fix relays strict authenticated readonly IPC without consuming the selected ACTIVATE, and keeps selected-clock/claim/ACK/release/restore guards. Seven added real Unix-socket cases per cohort cover preselection and held reads, bad/duplicate rejection and post-release restoration. Final native149 each and independent RO passed, preserving all old284 test-method ASTs. The sandbox bind failure and first synthetic fixture-handle collision remain separate HOLDs. Normal FULL approval uses the documented Workbench base-url; no nonreadonly DISPATCH passthrough was added. These helper checks are separate from the unchanged whole result and pending actual OPA/TLS acceptance ([evidence](../../guides/database-p5-6-acceptance-2026-10-25.md#実証json索引)).
- OPA4 then demonstrated the actual Trino query's OPA timeout (5000ms) and FAILED result. FULL remained UNKNOWN, the target remained REGISTERED with its grant revoked, and the warehouse remained non-OPEN. The same OPA process and original socket were restored; direct/reload WebUI retained UNKNOWN without a new-SQL action. Normal STOP/MAX_ONE0 preserved 70 tables, 604 private files (original SSH prefixes plus shutdown append), owner records, volumes and network. TLS fault acceptance, final independent checks and design verification remain pending; the earlier setup HOLDs and OPA3's normal APPLIED result remain separate.
- TLS2 reached ACK hold but stopped at coordinator PATCH-response parsing before a real x509 refusal. The finally PATCH succeeded before JSON parsing failed; the initial exception was not preserved. After confirming the original field, Secret and CA, normal STOP retained 70 tables, 600 private files, owners, volumes and network. Its unrestored socket and UNKNOWN remain quarantined. An identity-only TLS3 clone preserves all 149 test-method ASTs and passed native149 once; only new immutable coordinator DATA adds JSON output to PATCH. No product/helper feature changed, and this setup result is not TLS fault acceptance.
- TLS3 passed both PATCH/rollout calls but both ALT/original-CA peer connections reset before strict x509 refusal or release. Two SSH upstream refusals and a later healthy original-CA peer are preserved; a specific NodePort/EndpointSlice convergence cause remains unproven. Normal STOP retained UNKNOWN, 70 tables, 600 private files, owners, volumes and network with MAX_ONE0. A bounded readonly peer-readiness wait within the original deadline is a DATA-only next-fixture correction; no TLS fault PASS or product/helper change is claimed.
- The current cohort's single standard make passed all 76 groups: 2387 declared = 2354 executed plus 33 existing skips, failure/error zero. Fresh inventory and source201/material147/test101/helper71+34/binary pre/post equality were independently checked; separate helper results and old source147-r7 results were not added to this total ([evidence index](../../guides/database-p5-6-acceptance-2026-10-25.md#実証json索引)). OPA/TLS faults, final design verification, and closeout remain pending. Historical CLAIMED/UNKNOWN quarantine is unchanged; P5-6 technical completion is not claimed and P5-7 owner acceptance is still separate and unperformed.

### 2026-10-16 — WP-P0-001: Governance Reset & Documentation Slimming (Completed)
- Adopted [ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) and [DEV-04F](../04_Development/04F_Commander_Worker_and_Pragmatic_Roadmap.md), establishing the **Commander (`Antigravity`) + Implementation Worker (`codex-cli` GPT-6.1 Sol Medium)** operating model.
- Retired the unattended 500-session runner and blocking upstream CVE source-build rules in favor of digest-pinned official ARM64 images and audit-only vulnerability scanning.
- Archived the 1MB+ historical milestone log into `archive/061_Local_Experiment_Milestones_Archive_2026-09_to_10.md` and slimmed the active WBS and Milestone documents.

### 2026-10-16 — WP-P0-002: Warehouse Lifecycle Test Normalization (Completed)
- Fixed the macOS `/var` → `/private/var` temp symlink path resolution in `tests/test_local_warehouse_session_rpc.py` (`test_isolated_termination_boundaries_leave_unknown_saved_intent`, `boundary='after_handler'`) via `codex-cli` (`gpt-6.1-sol` Medium).
- Confirmed all 268 tests in `make test-local-warehouse-lifecycle` pass (`OK`).

### 2026-10-16 — WP-P1-001 & WP-P1-002: Resident L0/L1 Core Revival & 1-Command Lab Lifecycle (Completed)
- Booted `mac-studio-solo` Colima VM (without affecting other profiles) and repaired the stale DHCP Node IP in k3s `state.db` (`192.168.5.1` → `192.168.5.3`).
- Restored and unsuspended all 4 Flux v2 controllers, `GitRepository/flux-system`, 6 child Kustomizations, `seaweedfs`, `polaris-postgresql`, `polaris`, `trino-local`, `agenthouse` (ClickHouse), and `loki`.
- Fixed `tools/local_experiment/agenthouse/manage.py` to accept standard ClickHouse `Transfer-Encoding: chunked` and `application/x-ndjson` responses as well as already-running containers.
- Added `tools/local_experiment/lab_lifecycle.py` (`make lab-up`, `make lab-down`, `make lab-status`) and verified live `make local-doctor` (`Status: healthy`), `make local-lakehouse-smoke` (`result: passed`), `make local-agenthouse-smoke` (`result: passed`), and `make lab-status` (`ready: true`).

### 2026-10-16 — WP-P2-001: Resident Virtual Warehouse & General SQL Execution (Completed)
- Implemented `tools/local_experiment/warehouse_resident.py` and `internal/cli/warehouse_resident.go` via `codex-cli` (`gpt-6.1-sol` Medium), adding `shirokuma warehouse status / chill / wake / query` against the resident `Deployment/trino-local` in `shirokuma-dev` while keeping 100% of legacy `--session` unit tests green.
- Removed the fixed 25-row `tpch.tiny.nation` fixture restriction for resident queries, supporting arbitrary SQL (`--sql` or positional argument), `--catalog` / `--schema`, pagination over `/v1/statement`, automatic cold wake (`auto_woken: true`), and AgentHouse Pawprint recording (`agenthouse.pawprints`).
- Verified live on `mac-studio-solo`: `status`, Iceberg query (`iceberg.smoke.fixture_v1`), TPC-H join/aggregation (`tpch.tiny.nation JOIN tpch.tiny.region`), `chill` (`replicas=0`), and auto-wake SQL query (`tpch.tiny.customer`, `auto_woken: true`, `telemetry: recorded`).

### 2026-10-17 — WP-P2-001a: Commander Review Fixes & Corrections (Completed)
- Fixed bare `make` running `lab-up` / `warehouse-status` (regression since WP-P1-002) via `.DEFAULT_GOAL := verify`.
- Correction: XS/S/LOCAL share the single `trino-local` backend (now default `LOCAL`, reports include `backend: trino-local`); Gateway-based XS/S separation is deferred to Optional Track per Owner decision.
- Added `--max-rows` (default 1000, server-side cancel, `truncated` flag) and `status=failed` Pawprints for failed queries (no SQL/error text). Verified live.
- Correction to WP-P1-001: the root `Kustomization/flux-system` remains intentionally suspended because its source tracks GitHub `main` while this branch is local-only; child Kustomizations are active. `trino-local` is not Flux-managed.

### 2026-10-17 — WP-P2-002a: Persistent Iceberg TPC-H Dataset Seeding (Completed)
- Implemented `tools/local_experiment/lake_seed.py` and `tests/test_lake_seed.py` (13 unit tests passed), providing `make lake-seed` and `make lake-status`.
- Seeds 8 persistent TPC-H tables (`region`, `nation`, `part`, `supplier`, `partsupp`, `customer`, `orders`, `lineitem`) into `iceberg.tpch_tiny` via a single Trino session without any DROP/destructive operations.
- Verified live on `mac-studio-solo`: initial `lake-status` exits 1 (missing), initial `lake-seed` creates all 8 tables and matches row counts (total 86,805 rows), second `lake-seed` verifies idempotency (`present` with 0 recreation), and multi-table 4-way JOIN revenue query against Iceberg executes cleanly.

### 2026-10-17 — WP-P2-002b: Persistent Iceberg dbt Analytics Pipeline (Completed)
- Created persistent dbt project at `tools/local_experiment/dataops/dbt_persistent/` with staging views (`stg_tpch_orders`, `stg_tpch_customers`, `stg_tpch_lineitem`) and dimensional/fact marts (`dim_customers`, `fct_orders`) backed by Iceberg Parquet storage.
- Implemented `tools/local_experiment/lake_pipeline.py` and `tests/test_lake_pipeline.py` (6 unit tests passed), adding `make lake-build` and `make lake-test`.
- Verified live on `mac-studio-solo`: `make lake-build` builds all 5 models and passes 7 schema assertions in 9.9s; `make lake-test` passes all 7 assertions in 3.5s; marts aggregate query joins `fct_orders` and `dim_customers` to produce correct nation-level revenue stats; Pawprints recorded to ClickHouse (`lake.pipeline.build`, `lake.pipeline.test`).

### 2026-10-17 — WP-P2-003: 5 Core Bears Resident Integration (Completed)
- Removed mock-only restrictions across the 5 Core Bears, implementing `tools/local_experiment/bears_resident.py`, `tests/test_bears_resident.py` (10 unit tests), and Go CLI bindings `internal/cli/bear.go` (`shirokuma bear sre`, `query`, `data`, `catalog`, `policy`). Added `make bears-smoke` and `make bear-*`.
- **SRE Bear**: Gathers live readiness across 6 core components, pod CPU/memory container metrics, and recent failed ClickHouse Pawprints.
- **Query Doctor Bear**: Fetches runtime query history and distributed EXPLAIN execution plans from resident Trino, providing automated cost, fragment, and join/scan skew recommendations.
- **Data Engineer Bear**: Queries live Iceberg catalog column metadata, prompts local LLM (`qwen3.8-flash-next` via `127.0.0.1:8001/v1`) with `enable_thinking: false`, strips markdown fences, and verifies generated SQL plan validity via Trino `EXPLAIN`.
- **Catalog Curator Bear**: Discovers live schemas/tables across `iceberg` (`tpch_tiny`, `staging`, `marts`), resolves dbt model lineage (`fct_orders` -> `orders`, `lineitem`), and inspects Kustomize-labeled ConfigMaps (`app.kubernetes.io/name=trino-local`).
- **Policy Gatekeeper Bear**: Audits live pod security standards, drop capabilities, root filesystem, and resource limits against resident manifests.
- Verified live on `mac-studio-solo`: all 5 bears execute end-to-end and pass in `make bears-smoke`, recording Pawprint telemetry (`bear.sre_bear`, `bear.query_doctor`, `bear.data_engineer`, `bear.catalog_curator`, `bear.policy_gatekeeper`) to ClickHouse.

### 2026-10-17 — WP-P3-001: Peripheral Engine Optional Plugin Track Formalization (Completed)
- Authored [`docs/design/02_Architecture/02D_Optional_Plugin_Tracks.md`](../02_Architecture/02D_Optional_Plugin_Tracks.md), formalizing the architectural boundaries between the **Resident Core Mainline** (SeaweedFS, PostgreSQL, Polaris, Iceberg, Trino, ClickHouse AgentHouse, dbt, OpenMetadata, Superset, 5 Core Bears) and the **Optional Plugin Tracks**.
- Decoupled secondary engines (Trino Gateway multi-cluster routing, StarRocks, Apache Doris, Apache Gravitino, Apache XTable, Apache Amoro, RustFS, Cube.js, Ray) from the mainline lifecycle to prevent resource bloat and keep the local macOS/Colima lab fast, clean, and reliable.
- Defined four strict plugin guardrails: zero core degradation, Kustomize overlay isolation under `deploy/gitops/plugins/`, strict memory/CPU limits within `solo-core`, and official digest-pinned arm64 images.

### 2026-10-17 — WP-P3-002: Shirokuma Workbench Unified Local Web UI (Completed)
- Implemented `tools/local_experiment/workbench_server.py`, `tools/local_experiment/workbench/index.html`, and Go CLI bindings `internal/cli/workbench.go` (`shirokuma workbench [--port <port>] [--open]`) with `make workbench`.
- Zero-dependency local web architecture using Python standard library `http.server`, serving a high-performance single-page web UI designed with a clean Snowflake/Databricks B2B SaaS aesthetic (dark/light mode, responsive, accessible).
- Features 4 fully integrated operational surfaces:
  1. **Warehouse & SQL Workspace**: Live warehouse state (Active/Chilled/Replicas), 1-click Chill/Wake/Refresh, SQL editor with sample query shortcuts, tabular query results with execution latency.
  2. **Lakehouse Catalog & Lineage Explorer**: Interactive Iceberg schema/table tree (`tpch_tiny`, `staging`, `marts`), column details, and visual dbt model lineage (`dim_customers`, `fct_orders`).
  3. **Autonomous Bears Cockpit**: Interactive controls for all 5 Core Bears (`SRE`, `Query Doctor`, `Data Engineer`, `Catalog Curator`, `Policy Gatekeeper`) with instant formatted finding/recommendation display.
  4. **AgentHouse Pawprints Telemetry**: Real-time ClickHouse telemetry viewer with automatic background polling.
- Unit testing: 14 tests in `tests/test_workbench.py` and 7 tests in `internal/cli/workbench_test.go` (100% green). Live cluster verification passed across all HTTP REST API endpoints and SQL execution.

---

### 2026-10-17 — WP-P4-001: SQL Safety Guardrails, Write-Audit-Publish (WAP), & Iceberg Snapshot Rollback (Completed)

- Added SQL safety checks and WAP sandbox execution to review changes before publishing them to Iceberg.
- Provided Iceberg snapshot rollback to restore an earlier table state.
- Exposed the workflow through the resident warehouse CLI for controlled data changes.

### 2026-10-17 — WP-P4-002: Local File (CSV / JSONL / Parquet) Ingestion into Iceberg (Completed)

- Added `shirokuma lake ingest` for local CSV, TSV, JSONL, and Parquet files.
- Infers source schemas and loads data into a selected Iceberg table.
- Supports configurable ingestion mode and batch size for different input datasets.

### 2026-10-17 — WP-P4-003: Rich Pawprints Audit Trail, Persistent Trino Tunnel, & Workbench WAP/Ingest UI (Completed)

- Enriched AgentHouse Pawprints with audit details for resident operations.
- Reuses the resident Trino tunnel across requests to reduce connection setup overhead.
- Integrated WAP and local file ingestion controls into Shirokuma Workbench.

### 2026-10-17 — WP-P4-004: Fast Resident Core Verification (`make verify-core`) & Phase 4 Roadmap Sync (Completed)

- Added `make test-resident-core` for the 7 resident core test modules and included it in `make verify-local-experiment`.
- Added `make verify-core` to combine resident tests, Go checks, design validation, and repository hygiene checks.
- Synchronized Phase 4 completion records across DEV-04F, DEV-04D, and this milestone ledger.

---

### 2026-10-17 — WP-P5-001: WAP Audit & SQL Self-Correction (Completed)

- Added single-scan WAP quality auditing and regression guards.
- Rewrites QUALIFY queries for resident Trino execution.
- Added AI SQL self-correction for generated queries.

### 2026-10-17 — WP-P5-002: Dynamic WAP Models, Lineage & Refresh (Completed)

- Registers WAP model definitions and their lineage dynamically.
- Provides one-command `--refresh` to rerun registered models.
- Exposes model refresh through the resident Data Bear and Workbench.

### 2026-10-17 — WP-P5-003: Idempotent Upsert Ingestion & Phase 5 Sync (Completed)

- Added `shirokuma lake ingest --mode upsert --key order_id` with comma-separated composite keys.
- Keeps the last input row per key before batching; atomically merges each batch with existing Iceberg rows.
- Added Workbench upsert mode and key input, and synchronized Phase 5 roadmap, gates, and milestones.

---

### 2026-10-17 — WP-P6-001: Resident WAP Module & Shared SQL Helpers (Completed)

- Extracted resident WAP execution into `tools/local_experiment/wap_resident.py`.
- Reused shared SQL helpers across resident Bears, Warehouse, and Ingest workflows.
- Removed duplicated WAP orchestration from the resident Bears module.

### 2026-10-17 — WP-P6-002: Legacy Fixture CLI Removal (Completed)

- Removed retired fixture CLI subcommands and their Go tests.
- Consolidated Warehouse commands around the resident execution path.
- Updated CLI registration and resident command tests for the reduced surface.

### 2026-10-17 — WP-P6-003: Legacy Scripts, Tests, Virtual Environments & Makefile Cleanup (Completed)

- Retained resident core scripts, AgentHouse, Loki, Workbench, and persistent dbt while removing retired fixture trees and over 1.2 GB of unused virtual environments.
- Removed 261 legacy fixture test files and ~910 lines of retired `local-*` Make targets while preserving all 42 CI and resident core test suites.
- Verified `make verify-core` (all 114 resident Python tests and Go CLI tests passing) and synchronized Phase 6 roadmap, gates, and milestones.

### 2026-10-17 — WP-P6-004: CVE Source-Build Cleanup & Verification Alignment (Completed)

- Removed retired CVE source-build scripts, patches, and evidence.
- Removed associated legacy tests and verification dependencies.
- Aligned `make verify` with the retained resident core and upstream images.

### 2026-10-17 — WP-P6-005: Doctor Implementation & Test Simplification (Completed)

- Simplified Doctor JSON handling and component health checks.
- Removed byte-mutation defensive tests from the local Doctor suite.
- Preserved resident health reporting and CLI verification.

### 2026-10-17 — WP-P6-006: AgentHouse Simplification & Phase 6 Sync (Completed)

- Replaced fd-level state handling and byte-level inspection with standard path, JSON, and HTTP operations.
- Preserved saved credentials, resident telemetry APIs, and all five CLI commands; covered chunked and NDJSON responses.
- Synchronized Phase 6 roadmap, gate summary, and milestones with core, full, and live runtime verification.

---

### 2026-10-17 — WP-P7-001: Rich Table Inspector, Time Travel Preview, Upload Preview, & Models Registry APIs (Completed)

- Extended `GET /api/catalog` with `detail=full` (50-row data preview, single-scan row/NULL profile, DDL, upstream/downstream lineage) and `snapshot_id=<id>` for non-destructive Iceberg Time Travel preview (`FOR VERSION AS OF`).
- Added `POST /api/lake/preview` and browser content upload support (`filename` + `content`/`content_base64`) to `lake_ingest.py`, plus `GET /api/models`, `POST /api/models/build`, and restricted `POST /api/catalog/drop`.
- Verified HTTP/1.1 Keep-Alive connection reuse and single-query catalog discovery (`~58ms` catalog load, `~256ms` full table inspector).

### 2026-10-17 — WP-P7-002: Snowsight-Style Navigation & Multi-Feature Workbench Web UI (Completed)

- Redesigned `tools/local_experiment/workbench/index.html` around Snowsight-style grouped navigation (`Work with Data`, `Catalog`, `Manage & Monitor`) across 7 sections with a global header warehouse status bar.
- Upgraded SQL Worksheets with an inline Database Object Explorer, persistent multi-tab worksheets and query history, inline AI Bear Copilot (`Generate SQL`, `Explain Plan`, `Stage & Audit`, `Publish`), client-side sorting/filtering, and CSV/JSON/TSV export.
- Added browser drag-and-drop file upload with schema/row preview, a unified Transformation (`dbt` + `WAP`) view, and a 6-tab Table Inspector with Time Travel snapshot preview and rollback.

---

### 2026-10-17 — WP-P8-001: Session Schema Context, Sandbox Clone, Table Export CLI/API, Auto-Chill, & Activity History (Completed)

- Added session `schema` selection to `POST /api/warehouse/query`, safe sandbox/raw table and historical snapshot cloning (`POST /api/catalog/clone`), and table data export (`shirokuma lake export` and `POST /api/lake/export` in CSV, JSONL, and Parquet).
- Added configurable idle Auto-Chill (`POST /api/warehouse/autochill`) to `warehouse_resident.py` and dedicated Trino runtime query + copy/load history (`GET /api/history`) to `bears_resident.py`.
- Verified across 311 Python unit tests and Go CLI tests.

### 2026-10-17 — WP-P8-002: Bilingual (`EN` / `日本語`) UI Switcher, Inline Result Charts, & Snowflake Core Experience UI (Completed)

- Added a top-right `EN / 日本語` language switcher with `localStorage` persistence and automatic browser locale detection covering all 7 Workbench sections and dynamic views.
- Added zero-dependency inline SVG bar/line chart visualization (`Table View` / `Chart View`), selected-SQL execution, and session schema selector in SQL Worksheets.
- Integrated 1-click Sandbox Clone (current table or historical snapshot) and Table Export in Database Explorer, Auto-Chill timer controls in Compute & Governance, and dedicated Query History & Copy/Load History tables in Pawprints.

---

### 2026-10-17 — WP-P9-001 to WP-P9-006: Data Correctness & Lakehouse Integrity Hardening (Completed)

- **Type & Character Preservation (WP-P9-001)**: Prevented string identifiers with leading zeros (e.g. `'000123'`) from being coerced into BIGINT; preserved DECIMAL precision, distinguished empty strings from NULL for VARCHAR, auto-detected CP932 CSVs, preserved Japanese column names, and added `--schema-hints` & `--encoding`.
- **Atomic Staging & Swap Ingest (WP-P9-002)**: Replaced direct in-place multi-batch table updates with an isolated staging table (`__stage_<id>`) and atomic swap upon completion, preventing corrupted table states on partial batch failures.
- **Upsert Column Preservation (WP-P9-003)**: Resolved column loss bug in upsert by introspecting existing table schemas and preserving unmentioned existing columns across set-based updates (`LEFT JOIN` / `IS NOT DISTINCT FROM`).
- **Full Export Guarantee (WP-P9-004)**: Removed the silent 10,000-row limit on table exports, defaulted to full export (`--full`), verified exported row counts against total row counts, and displayed exact match details in UI.
- **WAP Strict Regression Audit & Snapshot Pinning (WP-P9-005)**: Blocked publication if baseline table introspection encounters an error, and pinned published tables to the exact snapshot ID verified during audit.
- **Server-Side Auto-Chill Daemon & Query Protection (WP-P9-006)**: Added a background idle check daemon thread operating independently of browser window state, and protected active/queued Trino queries from unexpected warehouse suspension.


## Summary of Pre-Reset Verified Capabilities (2026-09-16 to 2026-10-10)

The following capabilities were functionally demonstrated on `mac-studio-solo` during the initial local experiment wave and serve as the foundation for Phase 1–3 graduation:

| Layer | Capability | Status & Next Step in Phase 1–3 |
|---|---|---|
| **L0 Platform & Policy** | Colima (`vz`, Linux/arm64) + k3s + 4 Flux v2 controllers + 16 native Kubernetes admission policies (`169` policy tests green) | Unsuspend in **WP-P1-001**; make `shirokuma doctor` green on live cluster (**WP-P1-002**) |
| **L1 Core Lakehouse** | SeaweedFS S3 + Apache Polaris + PostgreSQL + Apache Iceberg (snapshots, time-travel, branches/tags) + Trino 483 + Dagster/dbt + OpenMetadata (`105` lineage tests green) + Superset + TPC-H SF10 smoke | Transition from disposable post-smoke `DROP` fixtures to persistent local Lakehouse in **WP-P2-002** |
| **L2 Agent Control Plane** | ClickHouse AgentHouse + OTel Pawprints + Loki + MCP stdio Gateway + local Qwen (`127.0.0.1:8001`) + 5 Bears (`SRE`, `Data Engineer`, `Catalog Curator`, `Query Doctor`, `Policy Gatekeeper`) | Remove fixed-fixture restrictions in **WP-P2-003** so Bears operate on arbitrary queries/requests and live cluster state |
| **L3 Virtual Warehouse & Security** | Trino Gateway XS/S routing + `shirokuma chill / wake / query` lifecycle + Keycloak JWT + OPA row filters/column masks | Fix remaining `UNSAFE_PATH` test fixture in **WP-P0-002**; open `warehouse query` to general SQL in **WP-P2-001** |
| **L4 Spark, ML & Notebooks** | Spark 4.1.3 + Iceberg 1.11.0 + Apache Comet ARM64 native acceleration + MLflow Tracking/Registry + local JupyterLab workbench | Keep Spark/MLflow/Notebooks integrated with persistent Iceberg catalog |
| **L5 & L6 Observability & FinOps** | Grafana Alerting → Dagster Sensor → SRE diagnosis flow + Lineage completeness report + FinOps resource/storage report | Consolidate operator views into the unified **Shirokuma Workbench** (**WP-P3-002**) |
