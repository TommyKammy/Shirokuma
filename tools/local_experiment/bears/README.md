# Local SRE diagnostics

WP-L2-AGENT-002 has a deterministic baseline and a local Qwen workflow. Both
read the actual MCP Gateway's `shirokuma_doctor`, `k8s_read_pods`, and
`k8s_read_events`. The baseline below classifies observations using fixed rules
without model inference. The Qwen workflow additionally chooses and executes
bounded follow-up reads. Neither performs remediation or creates PRs.

Run from the repository root with the existing Gateway environment:

```sh
rtk proxy uv sync --project tools/local_experiment/gateway --frozen
rtk proxy tools/local_experiment/gateway/.venv/bin/python tools/local_experiment/bears/run_sre.py
rtk proxy python3 -m unittest discover -s tests -p test_local_bears.py -v
```

The fixed `colima-mac-studio-solo` context, `shirokuma-dev` namespace, ClickHouse
AgentHouse, and Loki must be ready. There are no shell, SQL, namespace, file-path,
or provider arguments. The Gateway retains its own strict allowlist and audit
behavior.

The report distinguishes a failed, pending, or unready pod from historical
restarts on a ready pod. Successful completed jobs are not outages. Only the
doctor's exact informational finding for the deliberately suspended root Flux
Kustomization is classified as intentional. Other degraded Flux observations
still require attention. Warning events retain bounded identity/reason fields;
free-text messages are omitted. Empty or truncated pod observations cannot
establish health. These bounded snapshots do not identify root causes or prove
continuous availability.

`result: passed` means observation, classification, and audit correlation
succeeded; `diagnosis.health: attention-required` can coexist with that result.
Failed collection or audit returns a nonzero exit status without a successful
diagnosis. The diagnostic run and all three tool calls share a trace and task
ID. Four retained Pawprints in ClickHouse and the matching four span IDs in Loki
are verified before a successful result. Provider, token, and cost fields do
not claim model usage: provider is `none`, token counts and cost are null.

Evidence is written privately under ignored `tmp/local-experiment/bears/<run>/`:
the structured result, input snapshot hash, root OpenTelemetry span, and Gateway
stderr. Gateway child spans remain in its own ignored evidence directory. Raw
snapshots, event free text, credentials, prompts, and raw tool arguments are not
written by this diagnostic runner. No additional resident service or data
volume is created. There is no runtime rollback: stop the command, retain the
audit/evidence files, and the observed workloads remain unchanged.

## Local Qwen triage and re-observation

```sh
rtk make local-sre-qwen-smoke
rtk make local-sre-qwen-eval
rtk make test-local-sre-llm
```

The smoke uses the existing `qwen3.8-flash-next` MTPLX endpoint and the locked
LLM environment. Its LangGraph executes `collect → triage → followup` and checks
the completed in-memory checkpoint. It makes three initial MCP reads, one model
request, and at most two follow-up reads. The model chooses which observations
to repeat and the findings that justify those reads. Tool arguments, namespace
and context are fixed by the host. No model-provided commands are executed.

The model receives only bounded finding IDs, codes, severities, subjects,
permitted tools and observation completeness/times. Raw doctor summaries,
event messages, logs, credentials and arbitrary payload fields are excluded.
Input is capped at 8 KiB, with at most 24 findings; all critical findings must
fit or the task fails. Omitted warning counts stay visible. The profile bounds
output to 512 tokens with a 60-second network timeout and no automatic retry.

The response must preserve the observed health, reference known findings and
select at most two distinct allowed reads. Every critical Pod finding must be
covered by a Pod re-observation. Invented evidence, omitted critical findings,
irrelevant tools, extra arguments and root-cause claims are rejected before any
follow-up executes. Intentional root Flux suspension is informational. A
historical restart or Warning event is not proof of a current outage. The
contract deliberately leaves `root_cause: undetermined`; this is an evidence
collection assistant, not a general causal diagnosis or repair agent.

Comparisons use Pod/Event UIDs and acquisition times. The same Pod name with a
new UID is a replacement, not restart-counter recovery. Event count growth is
compared only for the same Event UID. Missing UIDs/times, backwards acquisition
time and truncated lists are explicit comparison limitations. Sources the
model did not select are `not-reobserved`, never unchanged. Even
`no-change-observed` covers only the short interval between these two snapshots.

Gateway events include `last_observed_at`, using `series.lastObservedTime` or
`lastTimestamp`. The Event API defines `eventTime` as first observation, so it
is used as the last time only for a singleton event; creation time is never
substituted for occurrence time. Events with known recent occurrence times are
ordered first, and truncation is measured after excluding Secret events.
See the [Kubernetes Event definition](https://kubernetes.io/docs/reference/kubernetes-api/core/event-v1/).

All MCP calls, the model inference and the overall assessment share task/trace
identity. The workflow verifies every expected Pawprint and matching Loki
span/action, including exact model identity, token counts and measured latency.
Only the inference event claims model usage. Provider failures and rejected
proposals retain safe usage metrics; audit failure stops further reads and keeps
a local failed-delivery receipt. External tracing stays disabled.

Private evidence is in `tmp/local-experiment/sre-llm/<uuid>/`: the validated
projection/triage, selected snapshot differences, source hashes, bounded audit
receipts, completed-checkpoint status, and local spans. `observations.json`
retains a fixed safe projection of each initial/follow-up read, including UID,
readiness/restart/Event counters and times, so a later reader can recompute the
findings and comparisons. Hashes cover these retained projections. Arbitrary
snapshot fields and raw model responses are not written. A successful workflow may still report
`attention-required`; it has verified its procedure, not repaired the lab.

The evaluation command sends six synthetic cases twice each: intentional Flux
only, historical restarts, two critical Pods, a Warning event, degraded doctor,
and truncated events. It verifies evidence references, critical coverage,
uncertainty, and allowed read selection using the same validator. It performs
no MCP calls or fault injection. Twelve inference events are individually
written to AgentHouse and read back; an audit failure stops the suite. Evaluation
trace IDs are correlation identifiers, not exported spans. Evidence stays in
`tmp/local-experiment/sre-evaluation/<uuid>/` without raw prompts/responses.

No server settings, weights, resident services, volumes or workload state are
changed. Run the original `local-sre-smoke` to use the deterministic baseline;
retain audit records and experiment evidence. Continuous monitoring, logs and
metrics-based causal diagnosis, remediation and production acceptance remain
outside the demonstrated scope.
