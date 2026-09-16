# Local DataOps experiment

This implements the local functional slice of WP-L1-DATAOPS-001. A Dagster asset
runs `dbt build` and `dbt test` against the already running local Trino, Polaris,
and SeaweedFS path. Python and dbt/Dagster dependencies are pinned by the adjacent
`pyproject.toml` and `uv.lock`; execution uses this project's isolated environment.

From the repository root:

```sh
rtk make local-dataops-smoke
rtk make local-dataops-recovery-smoke
```

These targets synchronize the lockfile, open a temporary loopback-only Trino
tunnel on a free port, and close it after the experiment and cleanup. They use
the dedicated `colima-mac-studio-solo` context. No manual port-forward is needed.
If a loopback Trino port-forward is already available, run the worker directly:

```sh
rtk proxy uv run --frozen --project tools/local_experiment/dataops python tools/local_experiment/dataops/run.py --exercise-failure
```

The default endpoint is `http://127.0.0.1:18080`; `--url` accepts loopback HTTP only.
No credentials are read. Generated dbt profiles fix the catalog to `iceberg` and
schema to the existing `local_experiment` namespace. This command creates no
schemas and grants no privileges. The namespace needs the scoped data-write and
purge capability established by the preceding local SQL experiment.

The retained `iceberg.smoke.fixture_v1` is read before and after execution. Each
invocation generates UUID-prefixed staging and mart tables. Both are Iceberg
Parquet tables; the mart maps the fixture names to uppercase. Two successful
Dagster materializations run against the same relations and verify identical
rows. `--exercise-failure` additionally runs a dbt row-count assertion expecting
three rows, verifies that this exact test fails in Dagster, then retries with the
correct two-row expectation. Unrelated failures cannot satisfy this check.

The adapter uses `on_table_exists: drop` to rebuild the two owned relations;
temporary relation renames and catalog view support are unnecessary. Each
`CREATE TABLE` embeds an invocation ownership comment through `persist_docs`.
The runner refuses an initial name collision and verifies ownership before
rebuild and cleanup. Cleanup checks only the two exact names and drops only
tables with the matching comment. Existing fixtures and unrelated relations
are never cleanup candidates. A missing ownership marker or purge error is
reported and the relation is preserved for inspection.

Evidence is stored under ignored `tmp/local-experiment/dataops/<run-token>/`:

- `summary.json`: results, both fixture reads, deterministic readback, cleanup.
- Each attempt: Dagster run storage and `result.json` with the run ID and dbt node
  statuses; separate build/test logs and native dbt artifacts.
- `profiles/profiles.yml`: generated local endpoint configuration, no secrets.

The normal command runs two materializations; the failure exercise runs four.
Each dbt command has a 300-second process timeout, configurable up to 900 seconds;
queries also have a 120-second Trino session limit. Timed-out subprocess groups
are terminated. The runner returns nonzero for unexpected failures, fixture
changes, or incomplete cleanup, while preserving the original failure details.
After a timeout, inspect the exact named relations and the recorded marker before
retrying cleanup; do not reset Colima or delete retained storage.

dbt telemetry is disabled. Profiles, logs, targets, and Dagster instance storage
remain under the run evidence directory; no global dbt profile is modified.
Dagster telemetry is disabled in the local instance. Proxy and dbt environment
overrides are removed from the dbt subprocess environment.

This is host-local orchestration on an ARM64 host, with the existing ARM64 query
runtime. It does not install a resident Dagster webserver/scheduler, publish an
image, satisfy historical publication admission, or claim production readiness.
The experiment tables contain four rows total while present and are removed at
the end. They need no backup; reproducibility comes from the retained fixture,
versioned models and lockfile. Evidence directories persist until explicitly
removed and contain the data needed to explain failure/retry behavior.

Reference interfaces: [dbt Trino profile](https://docs.getdbt.com/docs/local/connect-data-platform/trino-setup),
[dbt-trino adapter](https://github.com/starburstdata/dbt-trino), and
[Trino system queries and cancellation](https://trino.io/docs/current/connector/system.html).
