# Local Data Engineer Bear

This is a bounded local slice of WP-L2-AGENT-003. The existing Qwen server
classifies a small transformation request into a typed plan. The host generates
a dbt project from fixed templates, materializes disposable Iceberg tables
through Dagster, and checks the results against an independent Python oracle.

```sh
rtk make local-data-bear-eval
rtk make local-data-bear-smoke
rtk make test-local-data-bear
```

The smoke defaults to `name_lengths_ja`. To exercise the other two fixed
requests, invoke the same runner with `--case uppercase_names_ja` or
`--case row_count_ja` using `tools/local_experiment/llm/.venv/bin/python`.
Only these three case identifiers are accepted; no free-form executable
request or output path is a smoke argument.

The evaluation checks six fixed requests twice each: five Japanese requests
and one English paraphrase, covering three supported transformations and two
unsupported requests. It compares the chosen operation with each request's
expected meaning. Valid JSON alone is insufficient. Evaluation does not
materialize tables or inject faults, and every inference is audited and read
back from AgentHouse. An audit failure stops the suite.

The plan has exactly three fields: integer `schema_version: 1`,
`source: "fixture_v1"`, and one `operation` from this table:

| Operation | Output | Boundary |
|---|---|---|
| `uppercase_names` | `id`, `name_upper` | ASCII names only |
| `name_lengths` | `id`, `name_length` | Unicode codepoints, not grapheme clusters |
| `row_count` | One `row_count` | All rows in the fixed fixture |
| `unsupported` | No execution | A refusal, never a fallback SQL instruction |

The source is always `iceberg.smoke.fixture_v1(id bigint, name varchar)`.
The model receives its fixed schema and a bounded request; it cannot choose
SQL, shell commands, filenames, catalogs, namespaces, output aliases or other
data sources. The smoke checks its requested operation before any table
materialization. Source rows remain in the local execution boundary.

The generated project retains the existing `stg_fixture` and `mart_fixture`
asset identities. Both relations use host-generated UUID names in
`iceberg.local_experiment`. Source rows must match the retained experiment
fixture before execution. Each supported plan is built and tested twice;
Trino readback must match the oracle's exact column order and values.
Output includes generated SQL/schema/tests, content hashes, dbt manifests,
run results, Dagster materializations, and safe plan/execution receipts.

Cleanup reuses the existing DataOps safeguards: refuse name collisions,
verify ownership comments before replacing or dropping generated relations,
drain the run's queries, then remove only its two UUID relations. Confirm the
retained fixture afterwards. Unknown query state or mismatched ownership
defers cleanup and fails the experiment; no broad schema or volume cleanup is
used. Retain the receipt and inspect its exact run token before recovery.
Generated files remain private under ignored `tmp/local-experiment/`.

The LangGraph is `observe → plan → execute → verify`. It reads the existing
dbt inventory through MCP and verifies the live source schema/rows through a
managed loopback Trino tunnel. MCP, model, materialization and assessment
share a task/trace identity; all four retained audit events are correlated
with Loki. Materialization records include elapsed execution time and failures.
Stdout must match the private saved receipt under `data-plans/<run-token>`.
SIGINT/SIGTERM cleanup keeps the tunnel available while the child stops its
queries; an interrupted run does not claim successful cleanup without evidence.

This experiment does not generate arbitrary application code or SQL, infer
new data models, modify retained data, or establish production readiness.
Uppercase uses an ASCII-only oracle because Python and Trino differ for some
Unicode casing rules. The existing MTPLX server and its startup settings are
unchanged; cloud providers stay disabled.
