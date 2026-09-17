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
  separate from local Qwen; six-case, eight-case and sixteen-case SRE results
  remain separate groups. The last adds eight container-state cases. Data
  `baseline` (six cases × two) and `requests`
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

### Explicit SRE observation-signals receipts

`observation-signals-v1` is eight fixed cases × two independent samples. Its
explicit `suite` and registered `dataset_sha256` are checked before interpreting
the old requested-count branches. Requesting 16 calls alone never identifies
this profile: the old eight-case SRE run and new signal cases have distinct IDs,
signatures and groups. Field-free legacy six/eight/sixteen-case receipts keep
their exact previous interpretation. Partial identity fields, unknown suites,
wrong hashes, changed case order/counts and duplicate executions are rejected
or excluded with explicit coverage issues.

For the new profile only, each passed attempt must contain safe exact `triage`
and a matching `selected_tools` list. The fixed pure case module is checked
against registered SHA256
`b2757e1e198c1212c0e21459a6f882967168db34e6371a3c647337922175c732`,
then the existing triage validator and literal case oracle recheck assessment,
allowed reads, critical finding coverage and an undetermined cause. Check order
is not prescribed; the saved tool list must match that attempt's own check
order. Failed attempts receive no synthesized valid proposal, and partial runs
keep the full 16-call denominator. Triages are not copied into report output.

This verifies a saved proposal against registered synthetic evidence. A matched
producer-declared dataset hash does not prove the actual HTTP model input or
rescore an unsaved raw response. Fixture-content comparability remains unknown.
The evaluation is one bounded synthetic run, not a general diagnostic-quality
or real-log-quality guarantee. See the
[SRE evaluation command and eight-case oracle](../bears/README.md#explicit-observation-signals-evaluation).

The 2026-09-18 saved report added one valid signal run and one group: 12 valid
runs / 11 groups, while preserving all prior 11 run projections and 10 groups.
The new run passed 16 / 16 samples and 16 / 16 audit checks; this report read
saved evidence without new model or audit calls.

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
