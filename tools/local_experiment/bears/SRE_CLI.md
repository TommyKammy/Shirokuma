# Local SRE Bear CLI

The local SRE workflow collects doctor, Pod, Event and platform observations,
asks the existing Qwen endpoint to choose up to two fixed follow-up reads, and
compares the resulting observations. It does not change workloads or execute
remediation. The CLI uses the existing dedicated Python environments and does
not install dependencies or start resident services.

```sh
rtk go build -o tmp/shirokuma ./cmd/shirokuma
rtk proxy tmp/shirokuma sre run
rtk proxy tmp/shirokuma sre status <evidence-token>
```

Both commands use the `local-experiment` profile. Use `--repo-root` to select
this checkout when launching elsewhere. There are no arbitrary prompt, shell,
namespace, tool-argument or endpoint options.

`run` allocates a fresh evidence token and invokes the existing workflow once.
`status` reads saved local evidence without model calls, API calls or file
changes. Allocation identifies a directory; it does not establish that durable
intent or model execution occurred. The evidence token names the directory
under `tmp/local-experiment/sre-llm`; the internal workflow token identifies its
audits and traces. Invocation intent is kept separately under `sre-cli`.

The final JSONL summary distinguishes workflow completion from observed health.
An `attention-required` result can be a successfully completed diagnosis.
Historical restarts, current abnormal container states and unknown observations
remain distinct. Same-name Pods with different UIDs are replacements, and a
short comparison does not establish recovery or continuous availability.
The root cause remains `undetermined` and `remediation_executed` remains false.

Raw logs, manifests, prompts, model responses and exception messages are not
copied into CLI output. Retained observations support the saved assessment and
comparison; inspecting them does not assert a fresh runtime state.

Older receipts are checked against their original observation scope. Some have
only doctor, Pod and Event snapshots; others include platform observations but
predate container details. Missing sources or details are not invented. A saved
success without reconstructable observations remains `unknown` with
`journal-only` verification. Status does not add a new hash seal to legacy files.

Cancellation signals only the CLI's own child and allows up to 240 seconds for
cleanup. An unconfirmed completion retains an unknown outcome, token and child
PID. The CLI does not send SIGKILL, replay an attempt or provide `resume`.
Inspect status before deciding how to handle an interrupted attempt.

For `run`, exit 0 requires a verified passed workflow receipt and matching child
exit. For `status`, exit 0 means the saved state could be inspected, including a
failed, busy or unknown state; it does not mean the diagnosis passed. Exit 1
reports a failed run or unavailable/unverified evidence. Established cancellation
uses exit 130 or 143.
