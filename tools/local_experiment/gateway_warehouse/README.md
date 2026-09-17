# Disposable Trino Gateway warehouse experiment

This authorized local experiment implements the bounded routing/database portion of
WP-L3-VW-001. It creates one new Pod with Gateway 21, PostgreSQL 18.4, and two Trino
483 single-coordinator backends. XS has a 1 CPU / 2 GiB limit; S has 2 CPU / 4 GiB.
They use distinct node IDs, configuration, discovery ports and data directories.
They share a Pod and host; no tenant isolation, HA or comparative performance is claimed.

The only Kubernetes objects created are a uniquely named deny-all NetworkPolicy,
immutable ConfigMap, immutable Secret and Pod in `shirokuma-dev` on
`colima-mac-studio-solo`. No retained database, catalog, table, credential, PVC or
Service is used. Only synthetic TPCH data is queried. Authentication is out of scope;
HTTP user and warehouse headers here are synthetic test inputs.

## Run

```sh
rtk make test-local-gateway-warehouse
rtk make local-gateway-warehouse-build
rtk make local-gateway-warehouse-plan GATEWAY_WAREHOUSE_BUILD_TOKEN=<build-token>
rtk make local-gateway-warehouse-run GATEWAY_WAREHOUSE_BUILD_TOKEN=<build-token>
rtk make local-gateway-warehouse-recover GATEWAY_WAREHOUSE_RUN_TOKEN=<run-token>
```

The build step verifies pinned ARM64 source images and imports them to the dedicated
local runtime. It reuses the already verified local Trino OPA image but configures
native read-only access control only; this experiment does not run OPA. The fixed
images and prior Trino build receipt are in `inputs.lock.json`.

The loopback forwards are Gateway 18580 and backend metadata observers 18581/18582.
A collision fails without killing or reconfiguring its current owner. Workload SQL
always enters Gateway; direct backend requests are limited to readiness and fixed
`system.runtime.queries` observations.

## Success boundary

The fresh registry must initially be empty. Only two fixed backend records are
registered through the Gateway API and read back. Git-managed FILE rules map
`X-Shirokuma-Warehouse: XS|S` to their corresponding group. The default group
`local-unrouted` is deliberately unregistered: Gateway's ordinary default fallback
must not silently route unknown or absent classes. Unknown-class input also supplies
the legacy routing group header to test that it does not bypass the fixed FILE rule.

Eight cases run in order: XS/S SELECT before, unknown and missing class, XS/S fixed
DROP denied with `PERMISSION_DENIED`, and XS/S SELECT after. SELECT verifies all 25
nation rows and the real coordinator node ID/version/state. The initial two queries
must each produce continuation GETs; those GETs carry the opposite class header and
must retain the original query ID and backend. Zero GETs is unobserved, not success.
All next URIs are bounded to the intended loopback origin, query ID and statement path.

Both Trino histories independently verify exactly three workload query IDs per
backend, their user/source, terminal state and error code. Unknown/missing sources
must be absent on both. A timeout or HTTP 500 alone cannot prove correct routing.

Gateway history storage is intentionally disabled. PostgreSQL readback must show
exactly two backend rows, four successful Flyway migrations and zero history rows.
The new database is saved as a private custom-format dump (at most 16 MiB), restored
without `--clean` into a distinct newly created database in the same new PostgreSQL,
and schema/data are compared with source snapshots before and after. Each database
command first verifies the current Pod UID and allowed shape. This is an ephemeral
restore exercise; durable backup scheduling and production recovery are unobserved.

A passing result also requires final readiness and deletion of all four known UIDs.
Unknown create outcomes, changed UIDs or scope drift prevent success and prevent
unsafe deletion. A Pod deletion conflict preserves its dependent resources.
Recovery uses the saved private plan and writes a separate receipt, keeping the
original result intact. A completed query/history check is distinct from successful
Kubernetes cleanup.

## Evidence and limitations

Private plans, generated credentials, journals, bounded diagnostics and database
dumps live under `tmp/local-experiment/gateway-warehouse` (directories 0700, files
0600). Summaries and logs omit passwords; plans and dumps remain private and ignored
by Git. Failure receipts retain pending operations and do not imply successful cleanup.

This increment does not implement a VirtualWarehouse controller, install a CRD,
autoscale, suspend/wake, authenticate users, authorize warehouse selection, calculate
costs, or make production acceptance claims. The draft mapping is recorded in
`docs/design/05_Modules/053_VirtualWarehouse_Controller.md`.

Sources: [Gateway 21](https://github.com/trinodb/trino-gateway/releases/tag/21),
[installation/configuration](https://trinodb.github.io/trino-gateway/installation/),
[default fallback implementation](https://github.com/trinodb/trino-gateway/blob/21/gateway-ha/src/main/java/io/trino/gateway/ha/router/BaseRoutingManager.java).

## Observed local run (2026-09-17)

Build `b3811eea3c3c494d8641dff5ddd5f4cc` and run
`2823f7f9bc0842c1adf54f37b0b9cc0e` passed on the first live attempt. All eight cases
matched; XS/S first queries used five/four opposite-header continuation GETs.
Independent histories contained the expected six terminal queries. Unknown/missing
routes returned HTTP 500 with an empty body and neither Source appeared on either
backend. The 5363-byte database dump restored with exact logical equality. Four
known UIDs were removed; repeated recovery deleted nothing. Four readiness checks
were saved. Focused tests: 49 passed. Full local/design regression: 740 passed,
one optional standard-Python OTel skip; the dedicated pinned-venv integration passed.
