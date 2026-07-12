# Observability

L0 uses bounded, on-demand diagnostics rather than a resident telemetry stack.
Run `shirokuma doctor --output json` for PR evidence or `--output markdown` for
operator review. The command summarizes Kubernetes readiness, Flux controller
and reconciliation health, and the repository supply-chain policy without retaining
command stdout, credentials, kubeconfig content, or prompts.
It discovers the repository root from the current directory; use
`--repo-root /path/to/Shirokuma` when invoking an installed binary elsewhere.

## Baseline signals

| Signal | L0 source | Collection |
|---|---|---|
| health | Kubernetes `/readyz`, Flux controller availability and Source/Kustomization/HelmRelease conditions, policy gate | `shirokuma doctor --output json` |
| events | warning events in `flux-system` and the affected namespace | bounded JSON collected by RB-002 |
| logs | affected controller/pod logs | tail only, collected by RB-002 |
| metrics | `kubectl top` when Metrics API is available | snapshot only; absence is not a health failure |

No Prometheus, Loki, or ClickHouse service is added at L0. Evidence is retained
with its PR for 30 days, then deleted unless an open incident or design decision
requires it. Each file is capped at 1 MiB and collections use at most 100 events
or 200 log lines per workload. `scripts/bound_evidence.py` enforces the byte cap
while draining command output so large single-line log records cannot exceed it.

## Pawprints

[`pawprint.schema.json`](pawprint.schema.json) defines the initial portable
record. It links issue, branch or pull request, verification, policy, outcome,
and bounded evidence references. Pawprints contain summaries and references,
not credentials, environment values, unrestricted command output, or raw
prompts. The failed-reconciliation fixture demonstrates the minimum triage
shape; L2 may later export the same logical record to ClickHouse or OTel.
