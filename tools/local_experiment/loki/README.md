# Local Pawprint logs

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

The 2026-09-16 runtime test pushed and queried a normalized record, stopped and
restarted Loki, then queried the identical record again. Private receipts are
under ignored `tmp/local-experiment/loki/`. Generated normalized logs have no
automatic expiration in this small experiment; periodically inspect the volume
size. Durable audit export is the matching AgentHouse JSONEachRow export plus
Gateway JSONL span/log records. A full Loki volume restore is not yet claimed.
Removing retained log volumes requires a separate decision. Stop the named
container for a non-destructive rollback. Grafana, Tempo and an OTel collector
are not required for this bounded correlation check and are not installed here.
