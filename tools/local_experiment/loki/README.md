# Local Loki logs

Loki 3.7.0 runs as one native ARM64 container, pinned to the manifest digest in
`manage.py`, in the dedicated Colima Docker context. This implements the log
correlation part of WP-L2-OBS-001. The configuration follows the
[official single-binary local deployment](https://grafana.com/docs/loki/latest/setup/install/docker/)
with filesystem storage, analytics disabled and no external object store.

```sh
rtk make local-loki-start
rtk proxy python3 tools/local_experiment/loki/manage.py status
rtk proxy python3 tools/local_experiment/loki/manage.py stop
```

Only `127.0.0.1:13100` is published; gRPC is internal to the container. This
single-user experiment has no authentication, so do not broaden that binding.
The container is limited to 1 CPU, 1 GiB and 256 processes, has a read-only root
filesystem, dropped capabilities and no privilege escalation. An owned named
volume stores its WAL, chunks and indexes. Stop/up retains it; no deletion,
volume pruning, public exposure or changes to existing Kubernetes resources
are part of this helper. The exact image, mounts, loopback binding and resource
limits are checked before changing an existing container.

`push_record` accepts only `task_id`, `trace_id`, `span_id`, `action` and `status`.
Prompts, arguments, credentials and arbitrary logs cannot be supplied. Task and
trace identifiers are JSON fields rather than high-cardinality stream labels.
`query_task` validates its identifier and retrieves at most 1,000 entries from
the last hour. The Gateway uses the same task/trace identifiers in OpenTelemetry,
Loki and ClickHouse. Unknown token/cost values remain null in ClickHouse.

Workbench SRE Platform can manually capture error and fatal events from the
managed AgentHouse and Loki containers. It stores event time, source, and
severity in the `sre-telemetry-severity` stream and searches the latest 20 events
in a 10-minute window. Loki's Compactor applies a 24-hour retention policy to
that stream, and the manual deletion API is disabled. [SRE capture and search](../../../docs/guides/database-p6-4c2l-sre-loki-severity-2026-11-04.md)

The Foundation source option captures the registered Kubernetes `trino-opa`
Deployment's current `opa` container. It uses the separate
`sre-foundation-severity` stream, scoped to the registered environment, namespace,
Foundation ID, and revision. That stream has its own 24-hour policy and the same 10-minute
search window. Capture requires the two-rule Loki configuration. [Foundation source scope and acceptance](../../../docs/guides/database-p6-4c2m-sre-foundation-logs-2026-11-04.md)

The 2026-09-16 runtime test pushed and queried a normalized record, stopped and
restarted Loki, then queried the identical record again. Private receipts are
under ignored `tmp/local-experiment/loki/`. Pawprint records have no configured age expiration. Durable audit export is the matching AgentHouse JSONEachRow export plus
Gateway JSONL span/log records. A full Loki volume restore is not yet claimed.
Removing retained log volumes requires a separate decision. Stop the named
container for a non-destructive rollback. Grafana, Tempo and an OTel collector
are not required for this bounded correlation check and are not installed here.
