# Diagnose a completed local read

```sh
shirokuma query select --table smoke.fixture_v1 --columns id,name --limit 10
shirokuma query diagnose --read-token <successful-select-evidence-token>
rtk make test-local-query-diagnose
```

Use the `evidence_token` from a successful `query select`. `diagnose` creates a
different token under `tmp/local-experiment/query-diagnose/`. It uses the existing
local Qwen profile and lab services; it installs or starts no persistent service.
Use `--repo-root` outside the checkout. Only `local-experiment` is accepted.

The source must be a completed, private Reader receipt with verified query and
tunnel cleanup. The command checks its request, SQL hash, query identity,
table UUID, schema, snapshot, counts and immutable file fingerprint. It holds
the existing source lock and never rewrites that receipt. Historical source
digests record provenance; they need not equal today's code after development.
This is host-local evidence validation, not a signature or tenant authorization.

The original SELECT is reconstructed only for an exact hash check and EXPLAIN.
It is never executed again. The diagnostic profile issues five bounded reads:
schema before, EXPLAIN of that snapshot-pinned SELECT, history for its single
query ID, SHOW STATS for that table, and schema after. Polaris metadata must
match the original UUID/schema/head before and after. Changed or deleted tables,
unsupported plans and conflicting history provenance are refused before inference.
Trino's bounded `varchar(n)` output is normalized only for EXPLAIN, history and
STATS; per-phase byte limits remain in force. The ordinary Reader's type policy
is unchanged.

An empty history result is `not-retained`; the command does not assume why the
entry is missing. Returned history must match the original user, source and SQL
hash. Estimates remain estimates, zero remains known, and NULL/missing timings
remain unknown. Raw plan text, query text, low/high values, names and rows never
enter model evidence. Only bounded numeric observations and fixed findings do.

After owned queries have ended and tunnels have closed, Qwen is called once.
Its response must contain the exact findings and corresponding next-step IDs:

| Observation | Suggested next check |
| --- | --- |
| Reader output was truncated | Review the Reader limit |
| Original history is absent | Capture history promptly in a future measurement |
| History timings are incomplete | Check the available timing evidence |
| Queue time was observed | Review queue timing |
| Plan estimates or table statistics are incomplete | Review table statistics |

Every result retains `MEASURE_BEFORE_OPTIMIZING`, `root_cause: undetermined` and
`performance_gain: unmeasured`. These are bounded next checks, not a claim that
the query is slow, a SQL rewrite, or permission to change data or configuration.
There are no model-selected followups, retries, resume or arbitrary SQL inputs.

Successful output is released only after the audit events, Loki correlation,
saved OTel spans, cleanup, final receipt and Python exit are verified. Allocation
alone does not prove execution. Failed or interrupted runs keep their private
evidence; upstream error text and model response text are not copied into it.
The three successful audit phases retain known token usage and measured inference
duration; cost stays NULL. Cancellation preserves the first interrupt through
secondary audit, span, unlock and save failures.

Metadata checks cover the observation window before inference. They do not
provide an atomic view against concurrent writers or promise that the table
still has the same head when the user later inspects the answer. This remains a
bounded local experiment without general root-cause or production acceptance.
