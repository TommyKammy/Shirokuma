# Local resource and inference report

This first FinOps increment reads the existing Shirokuma lab and returns one
JSON report. It covers the fixed Trino, Polaris and SeaweedFS components,
the workspace filesystem and the dedicated Colima filesystem, and the last
24 hours of declared inference records in AgentHouse.

```sh
rtk make local-finops-report
rtk make test-local-finops
```

To inspect a saved JSON observation as a standalone Japanese HTML report:

```sh
rtk proxy make local-finops-html < report.json > report.html
```

Create reports in a private directory with `umask 077`. The HTML renderer reads
only the supplied JSON, validates its report contract, and performs no new
collection. It uses no external fonts, scripts or network resources. Observation
times, missing values and partial collection remain visible in the saved page.

The report includes observation times and coverage limits. An unavailable
source produces a partial report with exit code 1; the JSON remains available
for inspection. A complete collection does not imply complete FinOps coverage.

CPU usage is a sample average over the reported metrics window; memory is
the observed working set. A component's identity and start time must agree
with the sample window. Missing or inconsistent observations have a reason
and null values. They do not become zero usage.

Host and Colima space are reported separately. They share physical storage
and must not be added together. Warnings apply when available space is at or
below 25%, 20% and 15%. Available filesystem space does not estimate APFS
reclaimable space, unique file ownership or safe-to-delete data.

Inference records carry their own coverage. Known token and cost subtotals
are distinct from complete totals; missing data stays unknown. AgentHouse
does not declare a currency for its cost estimates. A recorded event or its
duration is not proof of a single model request or pure inference latency.
These figures are recorded estimates and resource proxies, not a bill.

The collector starts no services and writes no AgentHouse events. It makes
no model calls, stops no existing services and deletes no data. Its temporary
inspection commands have bounded output and time limits; a failed command's
owned process group is terminated before its leader is reaped. Low-space advice is
fixed text. Reports can be saved as private experiment evidence by the caller.

This is partial coverage of WP-L6-FIN-001. Full component/PVC attribution,
idle-service decisions, budget enforcement, monetary pricing and dashboards
with history or automatic refresh remain separate increments. The historical PR budget gate is not required
for the authorized local development loop.

The 2026-09-20 local run collected all three component samples and both disk
observations in 0.931 seconds. Host and Colima available space were 61.67%
and 81.93%, respectively. The 24-hour AgentHouse window contained 10 declared
model events, with 4,000 prompt tokens and 178 completion tokens. Cost and
CPU/memory duration proxies remained unknown. This collection made no model
requests and is not a count of real inference requests.

The first attempt retained a partial report because Polaris's completed
bootstrap Pod shared the server's name label. The collector now requires
the `catalog-server` component label in its query and identity checks.
Both attempts are retained under
`tmp/local-experiment/finops-checks/013bc5048f8a4131bcb12edf7fc16137/`;
`actual02-report.json` is the successful observation. The bootstrap Pod was
preserved. Focused verification covers this coexistence, multiple servers,
and mismatched component labels alongside the report contracts. A subsequent
cleanup-path repair prevents an exited command leader from leaving descendants
holding stdout open. A fresh collection after that repair also completed in
0.995 seconds and is retained as `actual03-report.json`; the earlier reports
remain unchanged.
