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
rtk proxy tmp/shirokuma sre status <evidence-token> --usage
rtk proxy tmp/shirokuma sre status <evidence-token> --logs
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

`status TOKEN --usage` exposes the safe usage already present in a verified
passed receipt. Its final frame is schema 2: the original 13 keys plus
`resource_usage`, whose exact fields are `source`, `availability`, `initial`,
`followup` and `comparison`. The allocated frame remains schema 1 with four
keys. Default status and run retain their schema-1 final frames; run rejects
`--usage`. Every final frame is bounded to 4096 bytes including its newline.

`availability: available` carries the saved initial/follow-up projections and
their checked comparison. A saved `state: unknown` still has a null sample;
observed zero stays the string `"0"`. `legacy-absent` means no usage field was
recorded. Failed, busy, missing, invalid or journal-only evidence yields
`unverified`. `legacy-absent` and `unverified` carry three null payloads.

Projection happens within the existing lock and verified raw snapshot. The
final snapshot and intent checks complete, and the lock is released, before
the output is emitted. There is no separate scan, new evidence token, API,
model or audit call, or change to the saved files. Sample timestamps and
windows describe the original observation. `same-sample-window` can be a
cached sample; `different-sample-window` is not a trend or cause assessment.
Missing follow-up stays unobserved rather than borrowing the initial sample.

`status TOKEN --logs` shows the saved typed log observations. Its final frame
is schema 3: the original 13 keys plus `log_signals`, with the same five-field
saved-evidence envelope and availability rules as usage. The allocated frame
stays schema 1. `--usage` and `--logs` cannot both be true; each display keeps
the 4096-byte limit. Both are status-only flags.

Initial and follow-up values contain the verified fixed 12-field log projection,
including its eight-field fragment and four limitations. Unknown acquisition
has a null fragment; a valid empty fragment has observed zero counts. Neither
establishes healthy logs or absence of errors. Header counts describe matching
text lines, not authenticated events. The five-field comparison preserves
identity and requested-window overlap, without deriving count deltas, rates,
trends or recovery. Old receipts without log observations stay `legacy-absent`.
This display repeats no collection, model, API or audit operation.

Cancellation signals only the CLI's own child and allows up to 240 seconds for
cleanup. An unconfirmed completion retains an unknown outcome, token and child
PID. The CLI does not send SIGKILL, replay an attempt or provide `resume`.
Inspect status before deciding how to handle an interrupted attempt.

For `run`, exit 0 requires a verified passed workflow receipt and matching child
exit. For `status`, exit 0 means the saved state could be inspected, including a
failed, busy or unknown state; it does not mean the diagnosis passed. Exit 1
reports a failed run or unavailable/unverified evidence. Established cancellation
uses exit 130 or 143.

The saved-usage display passed 17 product-CLI checks: five tokens read before,
with and after the flag, one repeated new-usage view, and one expected missing
token. The default results and recorded sample values were preserved, with no
input or source changes. API/model/audit calls were not added; the earlier
AgentHouse count of 493 was inherited rather than queried. An independent
saved-only audit passed 68 checks without rerunning the CLI or APIs. Acceptance evidence
is under `tmp/local-experiment/sre-saved-usage-checks/e4c49ef987d34d078b0884e6102f4b6c/`.

The saved-log display passed 30 product-CLI invocations across six retained
tokens: 12 baseline views and 18 views with the new build. All 12 prior default
and usage outputs were byte-identical, and all 40 saved directory/file entries
were unchanged. Four log views were legacy-absent, one was unverified, and the
existing log-aware run retained both observed empty fragments and its saved
identity/window comparison. The largest actual final frame was 2639 bytes;
the conservative maximum allowed fixture was 3266 bytes, both including newline.
Evidence is under `tmp/local-experiment/sre-saved-logs-checks/fb4d3d0240354c7985a9a2bdb94cf00a/`.
