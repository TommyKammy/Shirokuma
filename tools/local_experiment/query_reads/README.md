# Inspect saved Reader metadata

```sh
shirokuma query reads --profile local-experiment
shirokuma query reads --token <reader-evidence-token> --profile local-experiment
```

Use a Reader evidence token of exactly 32 lowercase hexadecimal characters.
Omit `--token` to inspect all recognized runs within the bounds below. Use
`--repo-root` when the binary runs outside this checkout. The existing dedicated
Python environment must already be present; this command installs nothing.
There are no positional arguments, arbitrary directory paths or SQL inputs.

The command inspects saved summaries under four fixed roots:

| Reader version | Saved root under `tmp/local-experiment/` | Included actions |
| --- | --- | --- |
| 1 | `query-read/` | tables, describe, select |
| 2 | `query-snapshots/` | snapshots, historical select |
| 3 | `query-filter/` | filtered select |
| 4 | `query-count/` | count |

A token is searched across all four roots; if it exists in multiple roots, each
version is reported separately. Recorded table and snapshot metadata
can help select an earlier Reader run, including a run whose table has since
been removed. Snapshot IDs remain decimal strings; their numeric order is not
an ancestry or recency test. No fixed number of saved summaries is required.

This is a filesystem inspection. It performs no API, SQL, kernel, model or
service operation, creates no state directory, lock or new receipt, and does
not reopen the original table. It does not repeat a select or count. Its single
bounded JSON line is released only after the child exits with a matching result.
Existing summaries, locks and stored data remain unchanged.

## Public metadata

The top-level schema has seven fields: `schema_version`, `command`,
`command_result`, `requested_token`, `inventory`, `entries` and `limitations`.
Each inventory record has six fields: `reader_version`, `root`, `complete`,
`inspected_entries`, `unrecognized_entries` and `error_code`.

Each entry has exactly twelve fields: `reader_version`, `token`, `action`,
`table`, `snapshot_id`, `current_snapshot_id`, `recorded_result`,
`cleanup_verified`, `inspection_state`, `error_code`, `started_at` and
`evidence`. The evidence path points to the existing summary, not a new report.
Action-specific or unavailable metadata remains null. Rows, count values, SQL,
predicate literals and column selections are omitted.

`command_result` describes this inspection. `recorded_result` and
`cleanup_verified` describe claims in the saved producer receipt whose local
consistency was checked. They are not proof of current server state, a table's
continued existence, a new query cleanup, or independent replay of the result.
A recorded failure is kept as a failure; missing observations are not filled
with zero or a successful result. Producer error text is not exposed; inspection
errors use fixed `READS_*` codes. Saved source hashes are not compared with the
current checkout, so a later code change does not itself invalidate an old run.

The report always includes these three limitations:

- Saved Reader metadata only; no query, row, SQL or model execution is repeated.
- Recorded results and cleanup are producer claims checked for local consistency, not current server or table state.
- Rows, counts, SQL, predicate literals and columns are omitted.

## Inspection result and exit status

| Condition | Exit | Meaning |
| --- | --- | --- |
| All recognized receipts are consistent and coverage is complete | 0 | `command_result: complete`; saved failures and interruptions remain visible |
| A saved run lock is busy | 0, if other coverage is complete | `inspection_state: active`; payload metadata is null and not read |
| All-run roots are absent or empty | 0 | Complete empty coverage; no directory is created |
| A requested token is missing | 1 | Incomplete inventory with a fixed missing-token code and no invented entry |
| Partial, invalid, changed or bounded-out evidence | 1 | `command_result: incomplete`; available entries do not imply complete coverage |
| Interrupted or unconfirmed child | Nonzero | No successful report is released |

The public entry states are `saved`, `active`, `incomplete`, `invalid` and
`missing`. A structurally invalid root takes precedence over a missing-token
explanation. `root` in each inventory record is `present`, `absent` or
`not-observed`, not an arbitrary filesystem path.

## Bounded inspection

An existing busy `run.lock` is reported as active. Its payload is not read, and
unverified metadata remains null. Missing, partial, invalid or changed evidence
and unsafe symlinks are reported explicitly; an active run is not recast as a
completed failure. The same checks apply to Reader runs created by a Notebook.

The scan is bounded to 128 runs, 2 MiB per summary, 32 MiB of summary bytes in
total (including verification rereads) and 256 KiB of public output, including
the newline. Exceeding a limit leaves coverage incomplete;
a scanned prefix is never reported as the complete inventory. Unknown entry
names are counted without exposing those names. Files and directory identity
are checked around reading, so a changed snapshot cannot be published as stable.

These bounds and local consistency checks do not establish a transaction across
all saved directories. Run a new Reader command to observe present data;
`query reads` only reports the available saved metadata.

See the [Reader](../query_read/README.md),
[snapshot Reader](../query_snapshots/README.md),
[filtered Reader](../query_filter/README.md) and
[Count](../query_count/README.md) runbooks for the commands that produced these
summaries. [Saved Query Doctor reports](../query_diagnose_report/README.md) inspect
diagnosis evidence separately.
