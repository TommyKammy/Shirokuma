# Local AgentHouse dashboard

The WP-L3-CH-001 dashboard reads existing AgentHouse Pawprints. It helps an
operator find failed or blocked events, narrow them to a task or trace, and
inspect phase latency and model usage. It does not run a model or change data.

```sh
rtk make local-grafana-prepare
rtk make local-grafana-up
rtk make local-grafana-smoke
rtk make local-grafana-verify-dashboard
```

Open [AgentHouse](http://127.0.0.1:18030/d/shirokuma-agenthouse-local).
No login is needed for the local Viewer. Dashboard definition changes belong in
`dashboard.py`; regenerate the provisioned JSON by running that Python script.

The native ARM64 Grafana OSS image and official ClickHouse plugin are pinned in
`inputs.lock.json`. Grafana verifies the plugin signature at startup. This is
bounded local compatibility evidence; it is not a vulnerability clearance or
production acceptance.

The dashboard uses Grafana's native tables, time filter and standard controls.
The light theme follows the existing Pawprints mockup and supports inspecting
dense records on a desktop in normal daylight. Status has text as well as color.
No external fonts, decorative assets or custom stylesheet are required.

## Reading the dashboard

- Start with **失敗・ブロックされた記録** and adjust the selected time period.
- Enter a Task or Trace ID in the top controls to filter all panels. Clear the
  field to include all records. Both controls apply together.
- **処理別の時間** groups measured event durations by action and model. Parent
  and child phases can overlap; their durations are not added into wall time.
- **モデル別の使用量と欠測** separates provider/model combinations, including
  mock. Known token subtotals and missing-call counts are shown together.
- **Task・Traceのイベント** shows the matching records and event IDs, up to the
  stated row limit. Narrow the time period or identifiers for longer histories.

Event success is not a workflow completion verdict or a PR success rate.
Unrecorded failures and work outside the audit path are not visible. Missing
tokens and cost remain unknown, while an observed zero remains zero. Cost
estimates are not converted into currency. The separate retained-evaluation
report remains the place to compare fixed suite receipts and their denominators.

All queries use `FINAL` to read logical ReplacingMergeTree events instead of
counting duplicate physical deliveries. Task and Trace controls use Grafana
13.2.2's strict percent encoder and ClickHouse `decodeURLComponent`; untrusted
text is not interpolated directly as a SQL string. The formatter comes from
the [pinned scenes 8.13.5 source](https://github.com/grafana/scenes/blob/v8.13.5/packages/scenes/src/variables/interpolation/formatRegistry.ts).

## Runtime boundary

Grafana publishes only `127.0.0.1:18030`, uses 1 CPU and 1 GiB memory, and runs
as UID 472 with a read-only root filesystem and no added capabilities. An
internal Docker network connects it to the existing owned AgentHouse container.
Grafana also joins its own ordinary bridge for the loopback port mapping;
Docker did not create a published port with an internal network alone. Only
Grafana joins this frontend bridge. No global Docker networking setting changes.
The existing AgentHouse host port, volume, writer credentials and table schema
remain unchanged.

Anonymous access is Viewer only on the loopback endpoint. Grafana admin and
database credentials are generated in private ignored files. The dedicated
database reader has SELECT on `agenthouse.pawprints` only, with resource limits;
the existing writer credentials are not used by the dashboard.
The database enforces a 30-second maximum. The plugin timeout is 25 seconds,
because its pinned clickhouse-go driver adds five seconds to the context deadline
when setting `max_execution_time`. This leaves the database cap intact. See the
[pinned driver behavior](https://github.com/ClickHouse/clickhouse-go/blob/v2.48.0/context.go#L239).

The reader is added through one owned `users.d` XML file because the existing
AgentHouse writer does not have user-management authority. The configuration
adds a user and profile without replacing existing configuration. Its password
is stored as a SHA-256 hash in that XML. The original private credentials are
required on subsequent starts; missing credentials do not trigger rotation.

No retention policy or table migration is introduced. Benchmark-event import,
persistent Trino query-history ingestion and measured monetary cost remain
outside this increment.

## Stop and recover

```sh
rtk make local-grafana-status
rtk make local-grafana-stop
rtk make local-grafana-up
```

Stopping preserves Grafana's owned data volume, private credentials, reader
configuration and internal network. Starting again verifies existing ownership,
mounts, resource limits, image, datasource and plugin identity before reuse.
It does not rotate credentials or recreate retained AgentHouse data.

For network rollback, run
`rtk proxy python3 tools/local_experiment/grafana/manage.py disconnect`.
This stops only Grafana, then removes only the two journaled attachments from
its owned internal network and Grafana's frontend attachment. It retains the
networks, both data volumes and the
dedicated reader configuration for a subsequent verified `up`. An identity
change or unrelated network endpoint blocks the disconnect.

Preserve `tmp/local-experiment/grafana/credentials.json`, its receipts and the
named `shirokuma-local-grafana-data` volume. The additional reader file lives in
the existing AgentHouse container's writable layer; `up` verifies it and can
recreate that exact file if it is absent. A replacement AgentHouse identity
requires inspecting and reconciling the original network receipt, not silently
adopting a new container. No automatic retention cleanup deletes audit data.

The pinned Grafana image has about 439 MB of compressed layers; the signed
ARM64 plugin archive is about 12 MB. Grafana adds one local data volume for
its SQLite metadata and settings. Dashboard definition and provisioned
datasource files stay in the repository, and generated secrets stay out of Git.

## Panel verification

`local-grafana-verify-dashboard` freezes a seven-day range and compares all five
anonymous Viewer query frames with bounded direct SELECTs using the dedicated
reader. It covers all records, known Task/Trace separately and together, missing
identifiers and adversarial filter text. Reference reads before and after each
Grafana query detect concurrent data drift. Column types, row bounds, numeric
precision and NULL values must match; token/cost coverage is checked separately.
It records pending work and failures under a new private UUID directory in
`tmp/local-experiment/grafana-verification/`. No model, data write or extra
credential is required. UI checks complement this API evidence.
