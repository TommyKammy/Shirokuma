# Read rows with one typed equality

```sh
shirokuma query select --table smoke.fixture_v1 --columns id,name --limit 10 --where-column id --equals 1
shirokuma query select --table smoke.fixture_v1 --columns name --limit 10 --snapshot-id <listed-id> --where-column id --equals 1
shirokuma query diagnose --read-token <successful-filtered-read-token>
rtk make test-local-query-filter
```

Supply `--where-column` and `--equals` together. This is one non-NULL equality,
with the type taken from the freshly verified schema. There is no SQL expression,
operator, join or caller-selected endpoint. The filter column may be outside
`--columns`; it still belongs to the verified full schema.

| Column type | Accepted value |
| --- | --- |
| integer / bigint | Exact signed 32 / 64-bit integer; `1.0` is rejected |
| decimal(p,s) | Exact decimal within precision and scale; excess trailing zeroes are accepted, rounding is not |
| date | Real calendar date in `YYYY-MM-DD` form |
| boolean | Lowercase `true` or `false` |

Values are nonempty ASCII strings of at most 128 bytes. Whitespace, leading `+`,
exponents, NULL, text and floating-point predicates are unsupported. Negative
zero normalizes to zero. Decimal `1.2300` can match scale 2 as `1.23`, while
`1.234` cannot. Nullable columns are allowed; NULL cells do not match equality.

Without `--snapshot-id`, the read resolves the current head once from the before
metadata window. An explicit ID must be retained and have the same ordered field
IDs, names, types and nullability as the current schema. The selected ID stays
separate from the truthful `current_snapshot_id`. UUID, metadata, full schema,
head and retained-snapshot catalog must match after the read.

Only filtered reads use schema 3 and the `filtered-snapshot-reader-v3` source
profile, with private evidence in `tmp/local-experiment/query-filter/<token>/`.
Commands without these flags retain their existing Reader v1 or Snapshots v2
shapes. Limits remain 32 schema columns, 16 selected columns, 100 returned rows
and 256 KiB public output. Limit+1 detects truncation. When the filter column is
selected, every fetched cell, including the extra row, is checked against the
canonical typed value. For a non-projected filter column, verification is limited
to the compiled SQL/schema/snapshot binding; hidden cells are not fetched again.
Ordering remains unspecified.

The literal is present in CLI argv and may remain in shell history. It is also
saved in the private request for exact SQL reconstruction, and appears in Trino
SQL/history. Public predicate metadata contains only column, type and
`operator: eq`; it adds no literal to model evidence or audits. Explicit selected
rows can contain the value, and a caller can save stdout. Normal receipts save
row counts/hashes rather than rows, SQL, credentials or raw upstream errors.

Invalid syntax is refused before discovery. Missing columns, unsupported column
types or incompatible typed values are refused after discovery/schema reads and
before the data SELECT. Unknown responses, schema drift, failed cleanup or final
save failure suppress successful output. There is no retry, token reuse, fallback
to another snapshot or automatic resume. Owned queries are drained and tunnels
closed before rows are released.

[Query Doctor](../query_diagnose/README.md) validates a successful schema 3 source
and explains that exact filtered SELECT without executing it again. Statistics
use all compatible schema columns, the same snapshot and predicate, and no LIMIT.
They are estimates of that filtered scope, not an exact count or an unfiltered
table observation. [Saved reports](../query_diagnose_report/README.md) keep this
profile separate from v1/v2.

Seven actual filtered reads and one unchanged v1 read passed on a fresh two-snapshot
fixture. Invalid literal syntax was rejected before child startup; an unsupported
varchar predicate was refused before data SELECT. One historical diagnosis passed
with the same predicate and snapshot, one model call and no original SELECT replay.
The saved report includes its separate v3 group. The same fixture UUID was dropped
once; table absence and the retained fixture's unchanged projections were verified.
The 4,231 old files and 250 sources matched, and 167 independent saved-evidence
assertions passed. All 49 unique product query IDs were finished/complete; fixture
SQL and audit reads are counted separately. Evidence: `tmp/local-experiment/query-filter-checks/640deb117c414457bdccf23baca5e0b2/`.
 This remains bounded local access, without general SQL, tenant
authorization, performance-improvement or production-readiness claims.
