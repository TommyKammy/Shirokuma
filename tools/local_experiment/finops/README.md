# Local resource and inference report

The FinOps collector reads the existing Shirokuma lab and returns one JSON
report. Fresh reports use schema version 4 and cover the fixed Trino, Polaris
and SeaweedFS containers; the owned local MLflow, JupyterLab and Model Serving
processes; the workspace and dedicated Colima filesystems; and the last 24 hours
of declared inference records in AgentHouse. A bounded read-only Kubernetes
storage reference inventory covers SeaweedFS, Polaris PostgreSQL, Polaris and
Trino, plus one fixed AgentHouse Docker named-volume reference. Saved version 1,
2 and 3 reports retain their original coverage.

```sh
rtk make local-finops-report
rtk make test-local-finops
```

To inspect a saved JSON observation as a standalone Japanese HTML report:

```sh
rtk proxy make local-finops-html < report.json > report.html
```

To display a bounded history, explicitly select 2–8 saved public reports in
one JSON array and render it offline:

```sh
rtk proxy make local-finops-history < selected.json > history.html
```

The selection must contain full v1/v2/v3/v4 report objects, not file paths. No
directory search, file opening, service start, collection or automatic refresh
occurs. Input is capped at 8 × 256 KiB plus array framing and output at 4 MiB;
invalid or duplicate-key inputs fail without partial HTML. The view sorts by
validated observation time, keeps equal-time selection order, and displays
content SHA-256 only as an identifier, not an authenticity proof. Each entry
retains its own status, scope, component/process identity, sample window,
disk severity and inference coverage. Version 1 does not contain local process
measurements; that absence is not a stopped service. No usage/cost deltas or
event sums across overlapping windows are calculated. Old disk advice is not
presented as a current alert. Keep the selected array and generated HTML in a
private directory (0700/0600); do not commit generated evidence. Stop using the
view by deleting no data and simply not invoking the renderer.

On 2026-09-25, with Colima stopped, the read-only v2 CLI produced `partial`:
three owned local groups were `STOPPED` with null usage; Kubernetes/Colima and
inference were unavailable, while host disk was observed. `make` returned 2
because the underlying report command returned 1 for partial, not because the
JSON was malformed. The saved contract and HTML validated. Evidence is under
`tmp/local-experiment/finops-offline-checks/20260925-stopped/`.

On 2026-09-26, the offline history renderer accepted an explicitly selected
old v1, observed v2 and stopped v2 report, retained all source bytes, and
repeated byte-identical output. Evidence is under
`tmp/local-experiment/finops-history-checks/20260926-offline/`. Browser visual
inspection is not verified. This is a static saved-observation view, not the
continuous/task-cost dashboard or chill decision in the full WP.

## Fixed Kubernetes storage reference inventory (v3)

The collector validates the fixed owner chain from workload and Pod (Deployment
via ReplicaSet), through container mount, namespaced PVC and PV `claimRef` UID.
SeaweedFS references the storage-namespace claim at `/data`; Polaris PostgreSQL
references its own dev-namespace claim at `/var/lib/postgresql/data`. Polaris
and Trino have no direct PVC reference in this inspected scope; this does not
mean they use no disk. Service access to a catalog or S3 does not give its
client a second copy of the provider's claim.

PVC request, PVC status capacity and PV spec capacity are separate declarations.
The repository requests 20Gi and 5Gi, but fresh reports read live objects,
not those constants. `used_bytes` remains null with `NOT_MEASURED`. A local-path
claim's declared size is not proof of bytes consumed, exclusive physical
reservation, filesystem quota, writable headroom or safely reclaimable space.
`df` describes a shared filesystem; `du` alone cannot prove unique occupancy,
application-consistent usage or S3 object ownership. The page displays no
grand storage total. StatefulSet retention, template prune and PV reclaim
policy are distinct controls, not backup guarantees.

Fixed scoped metadata GETs follow the dedicated k3s guard. PodList items may
omit `kind`; explicit contradictory kinds, wrong owners, ambiguous listings,
mismatched mounts/claimRef UID, changing request/capacity and terminating
objects fail closed. Pending/Lost and zero-replica retained claims keep their
known request without fabricating usage. If Kubernetes is stopped, storage is
unknown, not empty. A missing/failed PV read on a Bound claim currently makes
that row unknown rather than publishing an unverified binding. No collector
call starts services, traverses a filesystem or changes a PVC/PV/workload.

## Fixed Docker volume reference (v4)

Two bounded, read-only Docker inspect passes validate the dedicated context,
the labeled AgentHouse container's immutable ID and pinned image, and the named
local volume's label, driver, creation time and exact mount source/destination.
The container can be stopped: the mount is a stored Docker configuration, not
proof of a currently mounted or writable filesystem. Unknown identity, a
foreign label, changed inspect data or a failed read stays unknown rather than
claiming volume absence or zero usage. CreatedAt offsets are converted exactly
to UTC; mountpoint paths, environment and credential values are not published.

This verifies **one container's reference**, not exclusive ownership: other
containers may also reference the volume, and a label is not proof of who wrote
its data. `used_bytes` remains null/`NOT_MEASURED`; no volume size, `du`,
physical reservation, ClickHouse table or backup coverage is inferred. The
collector never creates/starts/stops AgentHouse or a Docker volume. Profile
startup is a separate recorded runtime action; saved v1/v2/v3 reports have no
Docker volume observation and cannot be read as volume absence.

Create reports in a private directory with `umask 077`. The HTML renderer reads
only the supplied JSON, validates its report contract, and performs no new
collection. It uses no external fonts, scripts or network resources. Observation
times, missing values and partial collection remain visible in the saved page.

The report includes observation times and coverage limits. An unavailable
source produces a partial report with exit code 1; the JSON remains available
for inspection. A complete collection does not imply complete FinOps coverage.

For Kubernetes containers, CPU usage is a sample average over the reported
metrics window; memory is the observed working set. A component's identity and start time must agree
with the sample window. Missing or inconsistent observations have a reason
and null values. They do not become zero usage.

Local process measurements use the existing Lab Python environment through a
fixed, bounded read-only worker. It verifies each runtime's saved identity and
owned process set before and after two PID-limited `ps` samples separated by
one second. CPU cores are the cumulative CPU-time difference divided by the
measured elapsed time; this short observation is quantized by `ps` and does
not support idle decisions. RSS is the sum at the second sample and can count
shared pages repeatedly. It is not a host-wide unique-memory total and must
not be added to Kubernetes working-set measurements.

JupyterLab includes its server and verified kernels in one group. Kernel child
processes and unrelated host processes are not covered; an unverified process
set yields an ownership-unknown row. PID or identity changes, missing samples
and collection errors retain null values. A verified stopped service is shown
as stopped with no numeric usage; it is a known state and does not alone make
the report partial. Missing environments, uninitialized services and unknown
ownership do make it partial. Public output contains only an identity digest
and process/kernel counts, without PID lists, command lines or credentials.

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

The 2026-09-20 version 2 acceptance report observed 2 MLflow processes,
14 JupyterLab processes including 13 kernels, and 1 Serving process. Their RSS
sums were 487.40625, 1354.859375 and 32.375 MiB. The 1.0314595-second sample
recorded no CPU-time increment at `ps` resolution; this does not establish that
the services were idle. The three Kubernetes observations, both filesystems and
17 declared model events were also collected. The full CLI, HTML generation and
retention verification completed in 4.931 seconds.

Evidence and the saved report are under
`tmp/local-experiment/finops-process-checks/861e64255e6e437d93f0030115975ebc/`.
The public JSON and HTML add 33,232 logical bytes (36,864 allocated bytes);
verification files are separate. All 1,134 retained files, 13 Lab Notebooks,
MLflow/Registry records and 585 AgentHouse rows were unchanged. No runtime was
restarted. Static HTML structure, numeric units and version 1 compatibility
passed; browser visual inspection remains unverified because the browser URL
policy rejected the local file URL. No workaround was used.

To stop using this observation, stop invoking the report command. It installs
nothing and has no persistent collector or rollback step; retained reports and
other experiment data can remain in place.
