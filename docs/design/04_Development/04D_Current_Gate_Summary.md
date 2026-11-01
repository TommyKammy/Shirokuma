---
project: Shirokuma
doc_id: "DEV-04D"
title: "Current local gate summary"
status: active
created: 2026-10-06
updated: 2026-11-01
version: "2.25.0"
area: development
tags: [shirokuma, local, handoff, gates]
---

# Current Gate Summary (Pragmatic Core Reset — 2026-10-16)

2026-11-01、[DB-P6-4B2](../../guides/database-p6-4b2-wap-handoff-2026-11-01.md)のManual WAP下書き引渡しを実装し、限定範囲の技術受入を完了した。本人の成功済みB1案の保存SQLを元contextで解釈し、使用するTABLEの現在識別情報・列を検査して通常WAPのsource aliasへ変換する。実行先は利用者が選ぶ同一DB・同一E／A roleのSANDBOX contextとし、元contextと実行先contextを別々に検査・表示する。metadataだけの明示GETと未保存の新規WAPフォームへの取込に限り、AI推論・SQL・Wakeや保存／build／audit／publishを自動実行しない。編集中の案や未解決WAP操作を上書きせず、確認・編集後は既存の版保存・実行・品質・A公開の認可を使う。B2の限定範囲を技術受入済みとした（全体回帰3,944成功・34 skip、2DBの実機・画面・最終保全PASS）。後続は他Bearsの必須導線の統合とし、P6-4全体／P6-7／M1本人受入は未完了である。

2026-11-01、[DB-P6-4B1](../../guides/database-p6-4b1-sql-proposals-2026-11-01.md)のSQL下書き生成を実装した。本人・保存9項目contextと、明示選択した同一DBの1〜8件のTABLE／VIEW参照を検査し、依頼文と許可された名前・列名・型だけをLOCALモデルへ渡す。依頼文は原文を送信することを画面で示し、VIEWは列metadataだけを扱う。生成物は単一read-only SELECTの構文・参照範囲・既存policyを検査するが、列・型・業務上の正しさや実行可能性を保証しない。SQL実行・Engine・Wake・WAP、editor／結果の書換えは行わない。B1を技術受入済みとした（全体回帰3,892成功・34 skip、2DB・3role実機・最終保全PASS）。B1受入時点の後続B2は上記で受入済みであり、P6-4全体／P6-7／M1本人受入は未完了である。

2026-10-31、[DB-P6-4A5](../../guides/database-p6-4a5-saved-plan-explanation-2026-10-31.md)で、Query Doctor／Copilotへ保存済み計画だけを使う明示的な再説明を実装した。元A2／A3がCOLLECTEDで、元queryの保存値・現在台帳がともにFINISHEDの場合に、取得時点の安全な演算子・metadataを新しい説明要求へ固定する。元の説明がFAILED・CANCELED・UNKNOWNでも対象になり得るが、元状態を変更せず、A2の新規query受付も解除しない。本人・保存9項目context・凍結した全依存schemaの現在権限を検査し、catalog再取得・Engine・Wake・admissionを使わない。取消は新しい説明だけへ作用し、そのUNKNOWNを自動再送しない。重点backend198件・UI190件、全体回帰3,824成功・34 skip、2DB・3roleの休止中実機確認と最終保全照合を完了した。次はSQL案生成、既存WAPへの明示引渡し・他Bearsを扱う。P6-4全体／DB-P6-3全体／P6-7／M1本人受入は未完了である。

2026-10-31、[DB-P6-4A4](../../guides/database-p6-4a4-latest-query-source-2026-10-31.md)で、Query Doctorへ最新の対象SELECTを読み込む明示操作を実装した。本人と保存済み9項目contextが一致する通常のread-only queryに限り、参照schema権限と現在のlogical identityを再検査する。読込だけではSQL・AI・Wake・catalogを呼ばず、元queryのID・時刻・状態を表示する。遅着応答と未解決の説明要求を保護し、別入力へ移ったA1の元要求も既存の明示復旧操作で照合できる。重点backend116件・UI168件、全体回帰92単位（3,779成功・34 skip）、2DB・3roleの実機・画面と既存状態の保全を確認し、この限定範囲を技術受入済みとした。A4時点の後続だった保存済み計画の明示再説明はA5で扱う。P6-4全体／DB-P6-3全体／P6-7／M1本人受入は未完了である。

2026-10-31、[DB-P6-4A3](../../guides/database-p6-4a3-view-plan-explanation-2026-10-31.md)で[A2](../../guides/database-p6-4a2-plan-explanation-2026-10-31.md)のTABLE計画説明を、`logical_v1`の対応するnative／外部登録INVOKER VIEWへ拡張した。再帰依存を同一DBへ限定し、全schemaのquery権限とVIEWのhead／格納先証拠を検査する。元SQLは固定の非ANALYZE EXPLAINで計画取得し、実行しない。保存済みVIEW内の整数snapshot参照は、参照先TABLE・指定snapshotの存在と現行列構成に一致するschemaを検証して扱う。実機・画面で重点検証した。全体回帰91単位（3,719成功・34 skip）に加え、回帰開始後の修正を最終重点backend204件・UI80件で確認し、この限定範囲を技術受入済みとした。DEFINER・他DB参照・列構成が異なる履歴schema・非整数のtime travel、他Bears／SQL案・WAP連携は後続であり、P6-4全体／DB-P6-3全体／P6-7／M1本人受入は未完了である。

> [!IMPORTANT]
> **Operating Model**: [ADR-0036](../07_ADR/ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md) and [DEV-04F](04F_Commander_Worker_and_Pragmatic_Roadmap.md) govern current development.
> - **Commander**: `Antigravity` (Architecture, Task Scoping, Anti-Over-Engineering Guardrails, Review)
> - **Worker**: `codex-cli` (`gpt-6.1-sol` / `medium`)
> - Historical gate logs from the retired 500-session runner are preserved in [`../06_WorkPackages/archive/04D_Current_Gate_Summary_Archive_2026-10.md`](../06_WorkPackages/archive/04D_Current_Gate_Summary_Archive_2026-10.md).

## Current Phase & Active Gate Status

2026-10-31、[DB-P6-4A1 LOCAL SQL説明](../../guides/database-p6-4a1-local-sql-explanation-2026-10-31.md)を実装し、Query Doctor／Copilotを本人・9項目context・全参照schema権限へ接続した。SQLを実行せず、値・コメントを伏せたSQLと列metadataだけでローカル説明を返す。2DBの実推論、復旧・履歴・遅着応答の画面ガードを検証し、canonical 89単位（3,595成功・34 skip）と文書検証を完了し、この限定範囲を技術受入済みとした。実行計画はA2へ分け、DB-P6-3全体／P6-7／M1本人受入は継続する。

後続計画は[Databaseコア機能統合とコンピュート拡張](../../guides/database-full-capability-and-compute-plan-2026-10-28.md)を参照する。DB作成導線は[本人の条件付き受入](../../guides/database-logical-create-owner-acceptance-2026-10-28.md)に進み、待ち時間改善を継続する。
2026-10-29、DB-P6-0の[表示契約](../../guides/database-p6-0-implementation-2026-10-29.md)、DB-P6-1の[保存metadata監査](../../guides/database-p6-1-implementation-2026-10-29.md)は技術受入済みである。
[DB-P6-2の共有LOCAL管理](../../guides/database-p6-2-implementation-2026-10-29.md)は実装済み、技術受入は完了（cold 30回: p50 26.44秒・p95 26.92秒（30回すべて成功））。read-only UNKNOWNは結果を変えず実行閉鎖・OPENまで照合し、元の138 DB／552 Schemaとbindingを保持した。
[DB-P6-3B](../../guides/database-p6-3b-implementation-2026-10-29.md)・[C1](../../guides/database-p6-3c-dbt-compatibility-2026-10-29.md)・[C2A](../../guides/database-p6-3c2-sandbox-views-2026-10-30.md)・[C2B1](../../guides/database-p6-3c2b-native-materialization-2026-10-30.md)・[C2B2](../../guides/database-p6-3c2b2-native-runs-2026-10-30.md)・[C2B3](../../guides/database-p6-3c2b3-native-ui-2026-10-30.md)は各限定範囲の技術受入済み。[C2B4](../../guides/database-p6-3c2b4-native-quality-tests-2026-10-30.md)で新規native TABLE 1件に付属する品質test一式を明示選択でき、TABLE作成の検証と品質結果を分離した。API／実CLI／画面、E／A・2DB・休止復帰・品質失敗・SDK停止／取消／owner喪失と保全を実機確認した。この限定範囲の技術受入を完了した。C2B4時点の通常RunTask／全graphは後述のC2Cで扱う。native VIEWの構築・testは後述C2E1で拡張する。native SDKの候補直接置換、native VIEWの正式公開、DB-P6-3全体とM1本人受入は後続である。
DB-P7〜9は後続計画であり、下表の旧PhaseのCompletedから新Database対応・実サイズ変更・用途別Warehouseの完了を推定しない。

2026-10-31、[C2B5 native TABLEの明示公開](../../guides/database-p6-3c2b5-native-publication-2026-10-31.md)を実装した。付属品質test全件成功の候補について、Aが固定previewを確認し、行数監査>=1と同じDatabaseのMARTS公開を行う。E作成／A公開、API／実CLI／画面、元key回収と空監査の拒否を実機確認し、この限定範囲の技術受入を完了した。native SDKの再buildやmanual-WAP履歴の偽装を行わない。graph候補のTABLE公開は後述C2Dで対応した。native VIEWの構築・testは後述C2E1で拡張する。manual-WAP rollback統合、DB-P6-3全体とM1本人受入は後続である。

2026-10-31、[C2C native RunTask／全graph](../../guides/database-p6-3c2c-native-graph-2026-10-31.md)を実装した。対応する保存済みTABLE／EPHEMERAL全DAGを実際のRunTaskで構築し、明示選択時はsource test・TABLE間relationshipsを含む保存全testをTestTaskで実行する。画面／API／CLIに`native_graph`と利用条件を接続した。E／A・2DB・休止復帰・SQL失敗／SKIPPED・取消／owner停止と保全を実機確認し、この限定範囲の技術受入を完了した。graph候補のTABLE公開は後述C2Dで対応した。native VIEWの構築・testは後述C2E1で拡張し、incremental・並列実行は後続とし、C2B5の単一TABLE公開と既存SQL/WAPを維持する。

2026-10-31、優先unit [C2D graph候補の明示公開](../../guides/database-p6-3c2d-graph-publication-2026-10-31.md)を実装し、この範囲の技術受入を完了した。全graphの構築と保存全品質testの成功証拠を条件に、現在のAが選択TABLEと固定targetを確認し、最低1行の監査後に同じDBのMARTSへ公開する。他TABLEの公開は別操作であり、source Eとpublisher Aのcontext・資格情報、元要求の回収と履歴証拠を分けて保持する。

2026-10-31、優先unit [C2E1 native graphのINVOKER VIEW](../../guides/database-p6-3c2e1-native-views-2026-10-31.md)を実装し、この範囲の技術受入を完了した。同じDBのrun専用SANDBOXへVIEWを構築し、VIEWからTABLEへの依存と保存全品質testを実SDKで実行する。C2E1受入時点ではVIEWを含むgraphの正式公開を保留した。C2E2でVIEW定義・識別情報と推移依存を公開証拠へ接続する。既存のTABLE／EPHEMERAL graph公開とSQL/WAPを維持する。

2026-10-31、優先unit [C2E2 VIEWを含むnative graphのTABLE公開](../../guides/database-p6-3c2e2-view-graph-publication-2026-10-31.md)を実装し、この範囲の技術受入を完了した。全VIEWの定義・UUID／version・閉鎖済み作成queryと推移依存を公開intentへ固定し、全graph構築・品質成功後に選択TABLEを明示公開する。確認済みVIEW要約と受付結果を照合し、元VIEWが変更・削除された後も公開履歴を独立して照会できる。正式VIEW公開やVIEW連鎖は含めない。 実機の監査UNKNOWNは[R1の限定運用復旧](../../guides/database-p6-3r1-dbt-audit-recovery-2026-10-31.md)で、UNKNOWNを保持して実行閉鎖・受付再開を証明した。新しい未閉鎖実行は0件である。次はTRF-11の必須操作との対応を確認し、DB-P6-4AのQuery Doctor／Copilot Explain読取り専用context統合へ進む。

| Phase / WP | Gate Objective | Current Status |
|---|---|---|
| **WP-P0-001** | Adopt ADR-0036 & DEV-04F; archive bloated logs and slim WBS/Milestones | **Completed (2026-10-16)** |
| **WP-P0-002** | Fix the single remaining `UNSAFE_PATH` test error in `warehouse_lifecycle` (`after_handler` fixture on macOS `/var` symlink) so `make verify-local-experiment` is 100% green | **Completed (2026-10-16)** |
| **WP-P1-001** | Start Colima (`mac-studio-solo`) and unsuspend Flux v2, SeaweedFS, PostgreSQL, Polaris, Trino, and ClickHouse AgentHouse using digest-pinned official ARM64 images | **Completed (2026-10-16)** |
| **WP-P1-002** | Verify `shirokuma doctor` reports all components Healthy on the live cluster; provide clean `make lab-up / lab-down / lab-status` entrypoints | **Completed (2026-10-16)** |
| **WP-P2-001** | Graduate Virtual Warehouse (`shirokuma warehouse chill / wake / query`) from fixed 25-row fixture to general SQL execution | **Completed (2026-10-16)** |
| **WP-P2-002** | Establish persistent Iceberg sample Lakehouse with Dagster/dbt, OpenMetadata lineage, and Superset dashboards | **Completed (2026-10-17)** |
| **WP-P2-003** | Integrate all 5 Core Bears with resident services and Pawprints telemetry through general CLI / MCP workflows | **Completed (2026-10-17)** |
| **WP-P3-001** | Formalize Peripheral Engine Optional Plugin Tracks (`02D_Optional_Plugin_Tracks.md`) to guard core lakehouse simplicity | **Completed (2026-10-17)** |
| **WP-P3-002** | Build unified **Shirokuma Workbench** local Web UI (`shirokuma workbench`, `make workbench`) for warehouse, catalog, bears, and Pawprints | **Completed (2026-10-17)** |
| **WP-P4-001** | Add SQL safety guardrails, Write-Audit-Publish sandbox execution, and Iceberg snapshot rollback | **Completed (2026-10-17)** |
| **WP-P4-002** | Ingest local CSV / TSV / JSONL / Parquet files into Iceberg with automatic schema inference (`shirokuma lake ingest`) | **Completed (2026-10-17)** |
| **WP-P4-003** | Enrich Pawprints audit trails, reuse the resident Trino tunnel, and integrate Workbench WAP / Ingest UI | **Completed (2026-10-17)** |
| **WP-P4-004** | Add fast resident core verification (`make verify-core`) and synchronize Phase 4 roadmap, gates, and milestones | **Completed (2026-10-17)** |
| **WP-P5-001** | Add single-scan WAP audit, QUALIFY rewriting, regression guards, and AI SQL self-correction | **Completed (2026-10-17)** |
| **WP-P5-002** | Register dynamic WAP models and lineage; rerun registered models with one-command `--refresh` | **Completed (2026-10-17)** |
| **WP-P5-003** | Add idempotent keyed upsert ingestion, Workbench upsert controls, and Phase 5 documentation sync | **Completed (2026-10-17)** |
| **WP-P6-001** | Extract the resident WAP module and deduplicate shared SQL helpers | **Completed (2026-10-17)** |
| **WP-P6-002** | Remove legacy fixture CLI subcommands and consolidate the resident Warehouse CLI | **Completed (2026-10-17)** |
| **WP-P6-003** | Remove retired fixture scripts, tests, virtual environments, and Make targets; verify the retained core and synchronize Phase 6 documentation | **Completed (2026-10-17)** |
| **WP-P6-004** | Remove retired CVE source-build scripts, evidence, and tests; align `make verify` with the retained core | **Completed (2026-10-17)** |
| **WP-P6-005** | Simplify Doctor implementation and tests while preserving resident health checks | **Completed (2026-10-17)** |
| **WP-P6-006** | Slim AgentHouse management, preserve credentials and resident APIs, verify standard HTTP responses, and synchronize Phase 6 documentation | **Completed (2026-10-17)** |
| **WP-P7-001** | Add rich Table Inspector, Iceberg Time Travel preview, browser file upload & pre-ingest schema preview, and unified dbt/WAP transformation models APIs | **Completed (2026-10-17)** |
| **WP-P7-002** | Upgrade Shirokuma Workbench with Snowsight-style 3-category navigation, multi-tab SQL worksheets, inline Bear Copilot, result export, D&D upload preview, and 6-tab Table Inspector | **Completed (2026-10-17)** |
| **WP-P8-001** | Add session schema context, 1-click sandbox table & snapshot clone, table data export/unload (`shirokuma lake export`), Virtual Warehouse Auto-Chill, and query/load history APIs | **Completed (2026-10-17)** |
| **WP-P8-002** | Add top-right `EN / 日本語` bilingual UI switcher, inline SVG result charts, selected-SQL execution, and Workbench controls for sandbox clone, table export, Auto-Chill, and query/load history | **Completed (2026-10-17)** |
| **WP-P9-001** | Preserve identifiers with leading zeroes, DECIMAL precision, empty string vs NULL, CP932 auto-detection, and explicit `--schema-hints` / `--encoding` | **Completed (2026-10-17)** |
| **WP-P9-002** | Guarantee atomic batch ingestion via intermediate staging table swap (preventing table corruption on partial batch failure) | **Completed (2026-10-17)** |
| **WP-P9-003** | Fix upsert column loss bug: preserve existing table columns absent from input file via set-based merge | **Completed (2026-10-17)** |
| **WP-P9-004** | Guarantee full table export without silent 10k capping and verify row counts in CLI and Workbench UI | **Completed (2026-10-17)** |
| **WP-P9-005** | Enforce strict WAP regression checks (block publish on baseline query errors) and pin published table to audited snapshot | **Completed (2026-10-17)** |
| **WP-P9-006** | Run background server-side Auto-Chill daemon and protect active/queued Trino queries from unexpected chill | **Completed (2026-10-17)** |


