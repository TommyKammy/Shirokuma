# Local lineage coverage

This WP-L5-LINEAGE-001 report compares verified historical Data Plan evidence
with the existing OpenMetadata catalog. It uses the current local services and
the Python standard library.

```sh
rtk make test-local-lineage-coverage
rtk make local-lineage-coverage
```

The entrypoint accepts no arbitrary endpoint, source directory or output path.
It scans the checkout's private Data Plan evidence, authenticates once to the
fixed loopback OpenMetadata server, and uses catalog GET requests. It does not
call a model, change the catalog, rotate a password or initialize a service.
Private JSON and self-contained HTML reports are written under
`tmp/local-experiment/lineage-coverage/<run-token>/`.
The writer creates any missing checkout-local parents through opened directory
descriptors and writes the new private run directory and both files relative to
those descriptors. It refuses symlink parents, non-private output roots, and a
path displaced while writing. On such a refusal an already-created directory
may remain at its original inode for manual inspection; it is not automatically
removed or retried through a substituted path. No source/catalog/backup is
deleted. Same-UID actors that can replace entries inside the already-open
directory are not independently excluded by this pathname-to-fd check.

## Reading the result

Each eligible historical run contributes two expected table-level edges:
fixture → staging → mart. Eligibility requires the existing artifact validator
to confirm the Data Plan, dbt/Dagster execution, independent result checks and
cleanup. The generated SQL tables were already removed; these catalog records
describe historical results and do not indicate currently running tables.

- **Present:** the expected identities, ownership, schema and lineage match.
- **Missing:** a completed catalog observation lacks the expected owned record
  or edge.
- **Conflict:** catalog semantics differ from the host-generated expectation.
- **Unknown:** availability, response validity or changing evidence prevents a
  reliable conclusion.

Failed, invalid and incomplete source runs remain listed with reasons and
counts. They do not become successful histories or disappear from the report.
The denominator is the number of eligible runs at the start multiplied by two.
If a source changes during observation, its expected edges remain in that
denominator and become unknown.

A report with missing edges can still have completed its observations. Report
completion and lineage coverage are separate fields. Unknown edges suppress
the coverage rate; the number of present edges and the observation counts remain
visible. A zero denominator has no percentage. A conflicted run is conservatively
classified as a whole, rather than treating its unverified edge as present.

## Evidence boundary

The report compares two reads of saved catalog state and records input hashes.
It does not claim a transactional catalog snapshot. Changes during observation
prevent a complete result. Tokens, credentials, arbitrary API bodies and source
request text are omitted from the report. HTML uses no external assets or scripts.

The known denominator covers this checkout's retained, verified Data Plan
histories. It does not cover every catalog table, arbitrary SQL, column-level
lineage, other platforms, or production readiness. Missing records are findings;
this command does not repair them. Existing source evidence and retained catalog
entities remain in place.
