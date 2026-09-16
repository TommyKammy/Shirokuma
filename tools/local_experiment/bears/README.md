# Local SRE diagnostics

This is a deterministic, read-only diagnostic scenario for WP-L2-AGENT-002.
It uses the actual MCP Gateway for `shirokuma_doctor`, `k8s_read_pods`, and
`k8s_read_events`, then classifies structured observations using fixed rules.
It performs no model inference and does not satisfy an LLM-agent acceptance
criterion. It creates no remediation, PR, workload change, or external request.

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
