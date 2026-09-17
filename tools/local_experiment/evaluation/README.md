# Retained local evaluation reports

This WP-L2-EVAL-001 local workflow reads existing evaluation evidence. It does
not call a model, query AgentHouse, change a runtime or rerun evaluations.

```sh
rtk make local-evaluation-report
rtk make test-local-evaluation-report
```

The fixed suites are inventory, SRE, Data Engineer, Catalog Curator, Query
Doctor and Policy Gatekeeper. Every discovered UUID directory in their private evaluation roots is
inspected; selection never prefers the latest successful run. Result receipts
and attempt logs must agree, and declared totals are recomputed. Saved audit
identities, statuses and model usage must match each attempt. Duplicate run,
trace or event identities are reported and excluded from metric groups.
Malformed or incomplete receipts stay visible in coverage instead of silently
becoming zero calls or successes. Setup failures and interrupted runs remain
separate from complete failed evaluations.

The command writes `report.json` and `report.md` into a new private directory
under `tmp/local-experiment/evaluation-reports/` and prints a compact result.
It exits 1 if any suite is unobserved, evidence is invalid/incomplete/duplicated,
or a saved evaluation reports failure. The report distinguishes evidence
integrity from the model evaluation result. Exit 0 does not indicate live
service readiness or general model quality.

## Metric meanings

- Groups separate suite, provider/model and case signature. Mock results stay
  separate from local Qwen; older six-case SRE results stay separate from the
  newer eight-case suite. Data `baseline` (six cases × two) and `requests`
  (seven cases × two) have separate signatures and labels. A Data receipt with
  no `suite` field retains legacy baseline compatibility; unknown suite names,
  changed case order/IDs/counts, or altered expected operations are rejected.
- Functional success includes both observed-attempt and requested-call
  denominators. Attempt coverage shows how much of a run actually executed.
- Audit coverage means saved, internally consistent readback evidence. It does
  not verify that the current audit service still contains those records.
- Token totals are unknown if any attempt lacks usage. Known subtotals and
  known/missing call counts are shown separately. Missing values are never 0.
- Latency p50/p95 use nearest-rank over recorded per-inference duration. Elapsed
  workflow time remains a separate measure.
- First-repeat success is the first independent sample in each saved case. It
  is not chronological first-attempt success, correction rate or retry quality.

The old receipts do not retain evaluator code revision, fixture content hashes
or reliable start timestamps. These remain unknown; file modification times do
not fill the gap. Case signatures identify case IDs and repeat counts, not
identical historical prompts or implementations. New Data receipts additionally
require their `dataset_sha256` to match the registered v1 golden cases (canonical
JSON, sorted keys, compact separators, UTF-8 with `ensure_ascii=False`). Focused
tests recompute those registered hashes from the checked-in golden definitions.
Legacy Data receipts without a suite/hash keep `dataset_binding=legacy-unrecorded`;
no hash is invented for them. A matching recorded hash binds the producer's
declared dataset identity and typed semantic verdict, but cannot prove which
prompt was actually sent or independently rescore an unsaved raw model response.
The report never loads or copies raw requests. Saved audit consistency remains
distinct from fresh AgentHouse verification, and source hashes provide local
change detection, not signed provenance.

PR success/revision rates, MTTP, runtime success and production readiness are
N/A for this read-only synthetic evaluation report. Inference cost remains
unknown. Raw prompts, SQL, free-text errors and credentials are not copied into
the report; only fixed identifiers and numeric outcomes are emitted.

Inputs are retained unchanged. Generated reports are additional disposable
local evidence; stopping this command has no effect on services or data.
