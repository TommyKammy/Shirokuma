---
project: Shirokuma
doc_id: "REQ-030"
title: "Functional Requirements"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2.2"
area: "requirements"
tags: [shirokuma, requirements]
---

# Functional Requirements

| ID | Area | Requirement | Priority | Acceptance |
|---|---|---|---|---|
| FR-CLI-001 | CLI | `shirokuma init`でLabを初期化できる | Must | local-liteが完走 |
| FR-CLI-002 | CLI | `doctor`で主要コンポーネントを診断できる | Must | JSON/Markdown出力 |
| FR-CLI-003 | CLI | `pr`でAgent PRを起票できる | Must | Issue番号と紐づく |
| FR-LAKE-001 | Lakehouse | Iceberg tableを作成・登録できる | Must | Polarisで参照可能 |
| FR-LAKE-002 | Lakehouse | Snapshot rollbackを実演できる | Must | データ差分が戻る |
| FR-WH-001 | Warehouse | Trino Gatewayで複数Warehouseへルーティングできる | Should | XS/S/M/L定義 |
| FR-WH-002 | Serving | StarRocksからIcebergを読む | Should | Direct Lake track demo |
| FR-AGENT-001 | Agent | IssueからPRを生成できる | Must | CI通過 |
| FR-AGENT-002 | Agent | Agent行動をPawprintとして記録できる | Must | ClickHouse/OTelに保存 |
| FR-META-001 | Metadata | LineageをOpenMetadataに反映できる | Must | dbt/Dagster実行後に表示 |
| FR-POL-001 | Policy | 危険操作をCIで拒否できる | Must | test PRで拒否 |
| FR-UI-001 | UI | Den画面でAsk入力、Proposal Queue、Pawprintsを確認できる | Must | UI mockup and later Storybook |
| FR-UI-002 | UI | VirtualWarehouse変更は直接適用ではなくPR作成になる | Must | CRD diff and PR CTA |
| FR-UI-003 | UI | Pawprintでpolicy/CI/token/traceを確認できる | Must | detail panel |
| FR-UI-004 | UI | Catalog/Lineage画面でowner/schema/quality/impactを確認できる | Must | table detail and lineage mockup |
