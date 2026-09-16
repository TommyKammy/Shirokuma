# Local AgentHouse

This is the bounded functional implementation of WP-L2-CH-001 on the existing
Colima ARM64 Docker daemon. ClickHouse 26.8.5.13 uses the official native ARM64
manifest `sha256:79f936494ccd00791676e0352d5988f30a215286592c6da83966c4c49fabd2c1`.
The container has 2 CPU / 4GiB / 512 PID limits and publishes only
`127.0.0.1:18123`; the native protocol port is not published.

From the repository root:

```sh
rtk proxy docker --context colima-mac-studio-solo pull --platform linux/arm64 \
  clickhouse/clickhouse-server@sha256:79f936494ccd00791676e0352d5988f30a215286592c6da83966c4c49fabd2c1
rtk proxy python3 tools/local_experiment/agenthouse/manage.py up
rtk proxy python3 tools/local_experiment/agenthouse/manage.py smoke
rtk proxy python3 tools/local_experiment/agenthouse/manage.py status
rtk proxy python3 tools/local_experiment/agenthouse/manage.py export
rtk proxy python3 tools/local_experiment/agenthouse/manage.py stop
```

`up` is repeatable. It checks the dedicated Docker socket and ARM64 runtime,
refuses unowned containers/volumes and conflicting image or port settings, then
initializes `agenthouse.pawprints`. It preserves an existing owned data volume.
`stop` stops only this container and retains the volume and credentials. Start
with `up` again; neither command resets Colima or changes other workloads.

Generated credentials and Docker env files are private files under ignored
`tmp/local-experiment/agenthouse/`. They are never printed, committed, or passed
as command-line password arguments. The application connects through loopback
without HTTP proxies or redirects. Keep the credential file with this local
runtime; missing credentials do not cause automatic rotation of a live service.

The Pawprint schema follows ARCH-02A's event/agent/task/action/policy/model/usage
fields, with trace ID and duration for later gateway telemetry. Unmeasured cost,
token, CPU and memory values are NULL, not fabricated zeros. Free-form prompts,
tool arguments and credentials have no fields in this schema. Code callers use
`event(...)` and `insert(Client(), event)`; inputs are bounded and JSON-encoded.
ReplacingMergeTree plus `SELECT ... FINAL` gives one logical event per UUID when
delivery is retried. Plain physical row counts may include unmerged retries.

The smoke inserts the same newly generated event twice, confirms one logical
row, and exports it. The smoke event remains as an audit record. Exports are
bounded to 8MiB and written as private JSONEachRow files; grow the export method
to paginated backup before using this as a large or production event store.

For local recovery, preserve the named `shirokuma-local-agenthouse-data` volume
and private credentials. A JSONEachRow export can be replayed through the same
validated `insert` function after reading each line with `json.loads`; preserve
event IDs so retries remain idempotent. Do not truncate the table or remove the
volume as part of ordinary restart/recovery. These generated local audit records
and the source schema require little storage; the pinned image is larger and can
be reused. No retention TTL silently deletes evidence.

Reference: [official ClickHouse 26.8.5.13 release](https://github.com/ClickHouse/ClickHouse/releases/tag/v26.8.5.13-lts).
