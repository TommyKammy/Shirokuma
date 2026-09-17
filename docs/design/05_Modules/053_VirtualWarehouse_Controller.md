---
project: Shirokuma
doc_id: "MOD-053"
title: "VirtualWarehouse Controller"
status: draft
created: 2026-07-05
updated: 2026-09-17
version: "0.3"
area: "module"
tags: [shirokuma, virtualwarehouse, trino]
---

# VirtualWarehouse Controller

## Purpose

Emulate Snowflake-like virtual warehouses using Trino Gateway and multiple Trino clusters.

## Important constraints

- Trino Gateway routes to backend Trino clusters. It is not the compute engine.
- Trino Gateway requires a backend database such as PostgreSQL.
- Plain Trino does not natively scale to zero as a warehouse. Idle suspend/wake is implemented by Shirokuma controller logic that starts/stops the coordinator and worker deployments.

## CRD sketch

```yaml
apiVersion: shirokuma.dev/v1alpha1
kind: VirtualWarehouse
metadata:
  name: analyst-medium
spec:
  engine: trino
  size: M
  gatewayRoute: analytics
  minReplicas: 0
  maxReplicas: 8
  idleSuspendMinutes: 15
  backendDatabase: postgres-trino-gateway
  requireCostExplain: true
```

## Controller responsibilities

1. Render Trino cluster Helm values from warehouse spec.
2. Register backend in Trino Gateway routing config.
3. Manage idle detection and suspend/wake.
4. Emit Pawprints to ClickHouse.
5. Block unsafe changes through OPA/Kyverno policies.

## Local routing experiment boundary (2026-09-17)

WP-L3-VW-001のlocal増分では、このCRD sketchをclusterへ適用しない。
Git管理の固定対応表で`engine=trino`、`size=XS|S`、`gatewayRoute=local-xs|local-s`、
新しい専用`backendDatabase`と各backend/node.id/resourcesの関係を保存してルーティングを試す。
Gatewayがcompute engineではないこと、query-ID単位の継続経路、未知routeのdefault fallbackを
実データ/metadataと照合する。min/max replicas、idleSuspendMinutes、requireCostExplain、
controllerによるreconcile/Helm変更は未実装・未観測のまま。実用CRD/controller完成とは扱わない。
