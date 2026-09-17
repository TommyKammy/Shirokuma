# Inspect saved Query Doctor runs

The CLI inspects saved diagnoses without running another diagnosis:

```sh
shirokuma query diagnose report
shirokuma query diagnose report --token <diagnosis-evidence-token>
```

The token is a diagnosis `evidence_token`, not its source `read_token`. Omitting
it inspects the fixed local diagnosis directory; supplying it inspects one run.
Both modes return one bounded JSON object with the same schema. The existing
`query diagnose --read-token TOKEN` command continues to run a new diagnosis.
Only `local-experiment` is accepted. There are no positional arguments, arbitrary
paths, SQL inputs or new report evidence directories.

The report reads diagnosis summaries and saved spans. It does not read the
original Reader directory, access its table, query AgentHouse/Loki, load a model
or emit new audits. Original tables and Reader files need not remain present.
It neither re-executes a SELECT nor reruns a diagnosis. Existing inputs remain
unchanged; the bounded JSON is written only to stdout.

## Results and exit status

`command_result` describes this inspection. Each run has a separate
`recorded_result`, verification level and state. A completed failed diagnosis
is useful evidence and is not converted into a success or omitted.

| Condition | Exit | Meaning |
| --- | --- | --- |
| Saved evidence is internally consistent | 0 | Includes recorded failed or interrupted runs |
| An existing run lock is busy | 0 | Active was observed; its unfinished payload is not trusted or aggregated |
| All-run root is absent or empty | 0 | Explicit absent/empty coverage, zero counts; no directory is created |
| Requested token is missing | 1 | Single-run target was not found |
| Integrity or complete coverage cannot be established | 1 | Fixed invalid/incomplete state; includes read failure, mutation during reading or a bound exceeded |

All-run inspection includes unsuccessful and unusable entries. It does not select
the newest successes. Root access failure is `not-observed`, never an empty
directory. Non-UUID entries are counted without exposing their names. Active,
incomplete, invalid and missing entries do not contribute to terminal groups.

## Evidence and metric boundaries

Private file checks reject symlinks, unsafe modes, duplicate JSON keys, nonfinite
numbers and changes during reading. Existing run locks are opened without
creation and checked without waiting. Limits are 128 run tokens, 32 MiB total
input and 256 KiB output, with per-file and per-record bounds. Exceeding a bound
is incomplete coverage, not permission to silently drop old runs.

The validator recomputes the embedded source, observation, allowed findings,
query ownership, cleanup and saved audit/span consistency. Reader v1 and snapshot
Reader v2 remain separate profiles. Historical 23-file and 27-file source
manifests are validated as stored provenance; their values need not equal
today's code. This report does not prove that old source files, a table or remote
audit records still exist.

Saved audit records and spans can be matched locally. `producer_verified`
retains the producer's earlier remote readback claim; it is not fresh
AgentHouse/Loki verification. Raw model responses are not stored, so the report
does not claim to rescore an unsaved response independently.

Groups contain only internally verified terminal runs and separate source
profile and recorded provider/model. Missing provider/model values remain null;
current configuration is not used to fill them. Model call intent and recorded
inference status are distinct. Known token subtotals and missing-usage counts
are shown separately; missing usage and cost are never replaced with zero. Known
costs use token-ordered float64 additions consistently in Python and Go. Recorded inference
duration belongs to a run and is not a new performance measurement.

The output contains fixed findings, safe identities, counts and recorded
metrics. It omits SQL, row cells, table/column names, raw plans, logs, error text,
prompts and model responses. It does not combine operational runs with the six
synthetic evaluation suites or present their counts as model quality, repair
rate, SLO attainment or production readiness. File modification time is not used
to infer attempt order.

## Verified local acceptance

Nine real CLI invocations passed acceptance: all, each of six individual runs,
all again, and one missing token. Eight returned exit 0; the missing token returned
exit 1 with its fixed missing row. The six saved diagnoses remain four passed
(three v1, one v2) and two closed, pre-model observation failures, in three groups.
Every single-run row equals its all-run row, and both all-run JSON objects match.
Recorded cost remains null. No diagnosis or SELECT was rerun.

The six input directories and eighteen files retained their bytes/SHA, modes,
mtimes, ctimes, inodes and inventory. All 77 sealed source files matched, and the
nine acceptance output logs are private mode 0600. API, model, new diagnosis,
audit and runtime calls were zero. AgentHouse was not freshly queried: the prior
486-row runtime baseline is inherited, not presented as a new observation.

Before the final known-cost compatibility adjustment, full local validation
passed 55 suites / 2,099 tests (2,098 passed, one existing optional skip), and Go
passed 157 top-level tests / 116 subtests plus vet and race checks. The final
adjustment was checked by 158 diagnosis-family Python tests (including all 52
report tests) and all nine real-Python Go integration paths, including the new
0.1/0.2/0.3 cost case. The full suite was not repeated after that narrow change.

Evidence is in `tmp/local-experiment/query-diagnose-report-checks/5f502301717046f39c3718f85d73fdb3/`:
`acceptance-input-seal.json`, `acceptance-input-after.json`,
`acceptance-verification.json`, `acceptance-source-verification.json`, and
`independent-final-review.json`. These are local functional and preservation
checks, not model-quality or production-acceptance claims.

The preceding historical-diagnosis preservation audit is the runtime baseline:
`query-diagnose-snapshot-preflight/92bebffcde6b4bed8c3bd65127bfb163/independent-verification.json`
(60 checks). This read-only increment requires input preservation and code-path
verification; it does not repeat that runtime audit.
