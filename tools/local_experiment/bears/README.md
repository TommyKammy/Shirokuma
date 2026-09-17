# Local SRE diagnostics

WP-L2-AGENT-002 has a deterministic baseline and a local Qwen workflow. Both
read the actual MCP Gateway's `shirokuma_doctor`, `k8s_read_pods`, and
`k8s_read_events`. The baseline below classifies observations using fixed rules
without model inference. The Qwen workflow also reads `local_platform_status`
for the five fixed runtime groups and chooses bounded follow-up reads. Neither
performs remediation or creates PRs.

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
the completed in-memory checkpoint. It makes four initial MCP reads, one model
request, and at most two follow-up reads. The model chooses which observations
to repeat and the findings that justify those reads. Tool arguments, namespace
and context are fixed by the host. No model-provided commands are executed.

The model receives only bounded finding IDs, codes, severities, subjects,
permitted tools, typed container-state facts and observation completeness/times. Raw doctor summaries,
event messages, logs, credentials and arbitrary payload fields are excluded.
Input is capped at 8 KiB, with at most 24 findings; all critical findings must
fit or the task fails. Omitted warning counts stay visible. The profile bounds
output to 512 tokens with a 60-second network timeout and no automatic retry.

The response must preserve the observed health, reference known findings and
select at most two distinct allowed reads. Every critical Pod finding must be
covered by a Pod re-observation. Every `RUNTIME_NOT_READY` finding must be covered
by `local_platform_status`; simultaneous Pod/runtime problems require both
reads. Runtime `unknown` stays distinct from observed unavailability and health.
Invented evidence, omitted critical findings,
irrelevant tools, extra arguments and root-cause claims are rejected before any
follow-up executes. Intentional root Flux suspension is informational. A
historical restart or Warning event is not proof of a current outage. The
contract deliberately leaves `root_cause: undetermined`; this is an evidence
collection assistant, not a general causal diagnosis or repair agent.

### Fixed Trino resource usage

The Pod read can additionally observe average CPU nanocores and memory
working-set bytes for the fixed Trino Pod's single `trino` container. Values are
exact integer strings. Missing or unsupported metrics remain `unknown` with a
null sample; an observed zero remains zero. Existing receipts without this field
retain their original shape. The model input remains bounded to 8 KiB.

This adds at most two application-level kubectl acquisition intents per Pod
read: Metrics API and the same Pod again, without application retries.
`get_attempts` counts these intents. Each has an eight-second deadline and a
64 KiB stdout cap, plus up to two seconds to reap its owned child. Internal
[client-go HTTP retries](https://raw.githubusercontent.com/kubernetes/client-go/v0.33.4/rest/request.go)
may occur within that deadline; wire-level GET counts and existing
context/readiness checks are outside this two-operation bound. Before/after container
identity and request/sample times are checked; the sample is correlated by name
and time, not authenticated by Pod UID. Repeated cached windows are not new
measurements. No load threshold, trend, peak/RSS, OOM-cause or repair inference
is added. A Pod comparison of `no-change-observed` concerns Pod/container state;
usage has a separate sample-window comparison and may contain different values.

Pure evidence/provider regressions and legacy compatibility checks have passed.
The first CLI run with usage (`52aa250a1b92b29d90205836a6ea0a3a`) passed with
both initial and follow-up samples observed for the same Pod/container identity.
The windows and values differed; Pod/container state had no observed changes.
The result remained attention-required with an undetermined cause and no repair.
The final independent audit passed 73 checks. Of 4,003 retained files, 3,999
matched their saved SHA, mode, mtime, size, uid and link count. Two Gateway files retained
their old prefixes and appended five records each; two runtime files changed
only their mtime, retaining content and mode. The 486 old AgentHouse rows were
unchanged, with exactly seven new events matching Loki and the saved spans.
All 219 source files were unchanged. New and legacy status reads preserved
their inputs and returned matching results. This completes local acceptance
for the observation feature; causal diagnosis and repair remain unproven.
See [WP-L2-AGENT-002](../../../docs/design/06_WorkPackages/L2/WP-L2-AGENT-002_SRE_Bear.md)
for the recorded values, retention audit, helper corrections, and limitations.

### Inspect saved resource usage

```sh
rtk proxy tmp/shirokuma sre status <evidence-token> --usage
```

The opt-in final frame uses schema 2 with the existing 13 fields plus
`resource_usage`. Default status and run retain schema 1; the allocated frame
also stays schema 1. `run --usage` is rejected. The final JSON frame, including
its newline, is limited to 4096 bytes.

The display uses the same locked, verified saved snapshot. It adds no model,
API or audit calls, new token or changes to the original evidence. `available`
contains saved initial/follow-up usage and the verified comparison, including
an unknown state with a null sample. `legacy-absent` means the old receipt did
not observe usage. Failed, busy, missing or unverified receipts return
`unverified`; `legacy-absent` and `unverified` have null payloads.

Values remain exact integer strings at their saved observation times. Same or
different sample windows do not establish a trend, current load or cause.
Saved-CLI acceptance passed 17 calls, preserving the five default results and
the new two-sample projection, legacy absence and unverified old evidence. The
32 input directory/file entries, two parent inventories, two inactive locks,
two Gateway audit files and 223 source files were unchanged. All 2,150 local
tests completed (2,149 passed and one existing optional skip), with independent
review and 68 independent saved-acceptance checks. The latter used saved outputs
and 31 evidence files without rerunning the CLI or APIs. The preceding AgentHouse count of 493 is inherited and was not queried
again for this display increment. See [the SRE CLI runbook](SRE_CLI.md).

Application-container observations distinguish the current state from the last
reported termination. The Gateway binds status names to the Pod's declared
application containers and retains at most eight rows, with explicit incomplete
or unknown coverage. It excludes init and ephemeral containers. Only known
reason codes, strict exit/restart counts, readiness and normalized timestamps
survive projection; messages, image/container IDs, commands and environment
variables do not. Missing fields remain unknown. Older snapshots without this
field retain their original readiness meaning and label detailed coverage as
not observed.

The host classifies a reported nonzero exit, explicit `OOMKilled`, image-pull
wait or restart backoff into evidence findings. Qwen selects the next permitted
reads from those findings. Exit code 137 alone is not classified as OOM, and a
previous termination is historical evidence rather than a current failure.
These distinctions follow the [Kubernetes 1.33 container-state contract](https://v1-33.docs.kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-states).
Container comparisons bind the same Pod UID and container name; a changed
state is not a claim of recovery or root cause.

Comparisons use Pod/Event UIDs and acquisition times. The same Pod name with a
new UID is a replacement, not restart-counter recovery. Event count growth is
compared only for the same Event UID. Missing UIDs/times, backwards acquisition
time and truncated lists are explicit comparison limitations. Sources the
model did not select are `not-reobserved`, never unchanged. Even
`no-change-observed` covers only the short interval between these two snapshots.
The platform observation checks Lakehouse, Superset, OpenMetadata, AgentHouse
and Loki. Superset readiness requires its owned container to be running and a
bounded, proxy-free, non-redirecting HTTP `/health` request to return `200 OK`
with body `OK`. Only fixed status/ready fields and time reach the model.
Platform comparisons cover component status changes; instance identity is not
observed, so a changed status is not labeled recovery.

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

The evaluation command sends sixteen synthetic cases twice each. Its original
eight cases cover intentional Flux
only, historical restarts, two critical Pods, a Warning event, degraded doctor,
truncated events, simultaneous Pod/runtime unavailability, and unknown runtime
status. Eight additional cases cover current nonzero exit, explicit OOM,
image-pull wait, restart backoff, previous nonzero exit/OOM, unknown details and
truncated details. It verifies evidence references, critical coverage,
uncertainty, and allowed read selection using the same validator. It performs
no MCP calls or fault injection. Thirty-two inference events are individually
written to AgentHouse and read back; an audit failure stops the suite. Evaluation
trace IDs are correlation identifiers, not exported spans. Evidence stays in
`tmp/local-experiment/sre-evaluation/<uuid>/` without raw prompts/responses.
The report preserves earlier six/eight-case receipts as separate case groups.

No server settings, weights, resident services, volumes or workload state are
changed. Run the original `local-sre-smoke` to use the deterministic baseline;
retain audit records and experiment evidence. Continuous monitoring, logs and
metrics-based causal diagnosis, remediation and production acceptance remain
outside the demonstrated scope.

## Disposable Failed Pod drill

```sh
rtk make local-sre-qwen-drill
```

This is a real local fault-detection experiment. It creates exactly one new
`sre-drill-<uuid>` Pod, runs `/bin/sh -c 'exit 1'`, waits for `Failed` with exit
code 1, then invokes the same Qwen SRE workflow. Success requires a critical
`WORKLOAD_FAILED` and `CONTAINER_EXIT_NONZERO` finding, and a selected Pod
follow-up that observes the same UID still Failed. Both snapshots must report
the `fail-once` container's current `Error` termination, exit code 1, unchanged
start/finish times and zero restarts. The drill reconstructs the model-facing
evidence from saved observations and verifies both hashes and audit success.
This does not test repair of a retained workload or induce an actual OOM.

The drill reads the existing `trino-local` Deployment's fixed local image digest
and numeric non-root user. It uses `imagePullPolicy: Never`, 50m CPU / 32 MiB,
`restartPolicy: Never`, a 60-second Pod deadline, read-only root filesystem,
dropped capabilities and RuntimeDefault seccomp. It mounts no secret/PVC/host
path, disables service-account tokens, and exposes no ports. Its labels do not
match existing Service or workload-controller selectors. The adapter verifies
the dedicated loopback Kubernetes context before every operation.

`creation-intent.json` records the manifest; cleanup authority starts only after
the successful creation UID is retained in private `created.json`. Cleanup checks
the exact name, namespace, owner/run labels and UID, then sends Kubernetes
DeleteOptions with **UID and resourceVersion preconditions**. It confirms the
original UID is absent and preserves any replacement. Uncertain creation,
missing receipts, identity mismatch or an unconfirmed delete becomes `deferred`
and makes the run fail. There is no name-only deletion or broad cleanup retry.

Evidence is retained under `tmp/local-experiment/sre-drill/<uuid>/`, including
the nested workflow's safe observation receipts and audit records. No new model
weights, images or data volumes are created. A deferred cleanup needs inspection
of its exact receipt and current UID before any further action; do not reset
Colima or delete matching names broadly.

At the 2026-09-17 preflight, the live cluster had no Kyverno CRDs or additional
admission policies/webhooks. This drill's manifest checks are implemented in
the local adapter; this result does not establish cluster-wide policy enforcement.
