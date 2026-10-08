# Bounded local OpenCode continuation (Shirokuma)

**2026-10-08 policy precedence:** DEV-04C now authorizes finite autonomous
parent supervision: classify a terminal stop, perform authorized bounded
investigation/repair, independently verify outcomes, then select a new mission.
Ordinary in-scope technical defects do not require a human restart instruction.
The manual-only/no-successor statements below are historical scheduling rules;
their safety checks and immutable evidence remain. Six-session/120-minute
batches, two repair execution sessions per unchanged blocker/approach, 600
cumulative starts, original week and OAuth-only/no-new-spending boundaries
still apply. Repair/review session starts count too; remaining at adoption is 98.

**Implementation:** `python -m tools.local_experiment.gate_supervisor` is a
finite L0 parent supervisor. Default is a zero-call plan; execution additionally
requires `--execute --use-pro-weekly-limit --max-sessions 2 --max-minutes 120`.
It starts one API-only secondary-owner repair worker, independently queries the
exact server session and live Kubernetes state, then automatically selects one
Node/four-controller readiness worker if repair is verified. Each worker is a
single-session batch accounted by the existing runner. The persistent repair
counter allows at most two reservations across invocations for this approach;
even a preflight-failed reservation conservatively consumes that repair counter,
though only actual runner starts consume the session allocation.

Unresolved terminal safety stops can admit **only** `L0_OWNER_CHAIN_REPAIR`
through private `repair-review-<run>.json`, bound to current HEAD and exact
original evidence with `allowed_gate:L0_OWNER_CHAIN_REPAIR` and decision
`terminal_safety_stop_repair_only_no_replay`. This attests terminal inspection
eligibility, **not resolution**. The prompt explicitly forbids node/controller/
writer startup. Parent live verification includes Deployment/ReplicaSet/Pod
owners, storage identities, suspended sources/Jobs and zero running containers.
Only after passing can it create a separate resolution review. A terminal
`complete` stage uses `verified_stage_new_mission_no_replay`; it is scoped
evidence, not full acceptance. Original records remain immutable.

Readiness verification requires fresh Node heartbeat/Ready, four available
digest-matching Flux Deployments and live matching Ready Pods, suspended
sources/Jobs, zero app owners and unchanged retained PV/PVC specs/UIDs. Raw
observations and decisions are private under `tmp/local-experiment/gate-supervisor/`.
The parent normally stops only the dedicated VM after confirmed terminal workers.
If worker/server outcome is unknown it does not issue runtime mutations. Cleanup
failure is recorded as unconfirmed and is not retried.

This first finite implementation does **not** infer safe repairs for arbitrary
new safety stops, denied permissions, failed workers or failed parent probes:
those terminate with a classified hold. It does not endlessly schedule other
WPs, unsuspend sources or revive the old 1000-extension. Live operation must be
reported separately from test success; workers cannot recursively launch it.

**2026-10-07 restart allocation:** 100 new starts after the reconciled old
500, hence current `SERIES_LIMIT=600`. The historical 1000 ceiling below is
not the active allocation. Six-session/120-minute batches still need a named
gate and functional review between launches; no automatic 100-session job.

On 2026-10-07 the owner selected **GPT-6.1 Sol** for each new primary
development session. Project `opencode.jsonc` and the runner's `--model`
default are `openai/gpt-6.1-sol`. Existing session history is unchanged;
the 500-total run has stopped at `run_budget_reached` and its successor
remains on `POLICY_REVIEW_HOLD`. This model setting does not restart it.
The optional, tool-less Astra blocker consultation remains a separate
previously approved exception; OAuth/no-new-spending checks still apply.

This is an **opt-in local runner**, not an unattended daemon or the GitHub
supervisor. It works only from the dedicated `local/autonomous-experiment`
checkout with a clean worktree and an existing Vault handoff. Each attempt
starts a **new** `opencode run --format json` session from that checkout. The
session is asked to read `AGENTS.md`, DEV-04B, current Milestones/WPs and the
private Vault handoff. It should continue through safe increments in its own
turn; the runner launches another session only after an explicit private
checkpoint, a clean worktree and a new descendant local commit. A missing
checkpoint, error, dirty tree or unchanged HEAD stops the runner. No failed
operation is automatically retried. On 2026-10-03 the owner allowed consuming
the **whole ChatGPT Pro weekly allowance** for local Shirokuma development;
this does not authorize metered API calls or extra purchases.

**Current policy supersedes the historical 500-run examples below:** the
existing `run-qjiwuw2k` process retains its original 240-run/500-total
boundary, but its prepared 1000-total one-shot successor was unloaded on
2026-10-06 and marked `POLICY_REVIEW_HOLD`. Do not restart that job or launch
the historical 500-run example. After a *separate* successful reconciliation
of the 500 boundary, use [DEV-04C](../../docs/design/04_Development/04C_Gate_Driven_Local_Continuation.md)
and its short [gate index](../../docs/design/04_Development/04D_Current_Gate_Summary.md).
The owner-set cumulative 1000 ceiling is only a maximum. Reaching 500 does
not start another invocation, clear an earlier failure or prove a WP gate.

The **future** runner code prepared in `local/astra-consult` is not deployed
to the active checkout during a request. Once integrated at a clean boundary,
it accepts at most **six new sessions and 120 minutes per manual L0 mission**.
Execution requires an owned 0600 JSON objective in ignored private
`tmp/local-experiment/gate-batches/mission.json`, bound to the current HEAD:

```json
{"schema_version":1,"wp_id":"WP-L0-GITOPS-001","gate_id":"L0_GITOPS_READINESS","head_before":"<full HEAD>","baseline":"<measured current boundary, at least 30 characters>","target_evidence":"<observable L0 result or concrete blocker, at least 30 characters>","allowed_scope":"<named dedicated runtime/resources, at least 30 characters>","stop_conditions":"<unknown state, quota, retained data and safety stops>","recovery":"<specific scoped restoration and evidence preservation>"}
```

No example objective is a substitute for a fresh lab observation. A second
private 0600 `gate-batches/ready.json` may be written **only after** the old
run's exact 240 results/HEAD chain, last checkpoint, and specific server
session are independently reconciled as succeeded, idle and non-active. Its
fields are `schema_version:1`, `source_run:"run-qjiwuw2k"`, `source_head`,
`series_started:500`, `last_session_id`, and
`server_outcome:"succeeded_idle_nonactive"`. This file is a *review
attestation*, not proof of the remote session. The runner checks the original
run summary/final result, original seven-day clock, retained review-hold
marker and subsequent local gate-run summaries. Missing/failed/unknown
evidence refuses launch; a batch counts against the original 1000 ceiling
including failed starts. A prior nonbudget or missing gate-run summary
requires investigation, not a blind next batch.

A terminal `agent_safety_boundary_claim` may permit a **new** mission only
after an independent review resolves its blocker. Preserve the original
summary/result/checkpoint and specific server session. The private 0600
`gate-batches/review-<gate-run>.json` attests schema_version=1, source_run,
exact original_summary and original_result, resolution_head (full descendant
commit), last_session_id, server_outcome=`succeeded_idle_nonactive`, and
decision=`blocker_resolved_new_mission_no_replay`. The runner validates the
original safety checkpoint and ancestry; it does not independently query the
server or prove the review. Failed/unknown/nonterminal sessions and other
nonbudget stop reasons remain blocked. This is never permission to repeat the
old failed action, erase evidence, renew the week or increase the allocation.

Only after all preflights and a current mission selection, plan without a
model request; then deliberately execute at most six sessions:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --model openai/gpt-6-sol --max-runs 6 --max-minutes 120 --mission-file "$(pwd)/tmp/local-experiment/gate-batches/mission.json"
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --execute --use-pro-weekly-limit --model openai/gpt-6-sol --max-runs 6 --max-minutes 120 --mission-file "$(pwd)/tmp/local-experiment/gate-batches/mission.json"
```

Inspect functional evidence and the exact terminal server state **between**
batches. A commit/checkpoint/checkbox cannot authorize a second batch. There
is no recurring scheduler or automatic restart. The original seven-day guard,
OAuth routing and no-new-spending boundary remain; the new 120-minute bound is
not a token/USD cap.

## Use

From `worktree/local-autonomous`, with any interactive agent/editing session
finished, inspect the **local CLI's** connection and model list. The Pro login
in this chat is not automatically shared with `opencode`:

```sh
rtk proxy opencode auth list
rtk proxy opencode models
```

Only after connecting the **OpenAI ChatGPT Pro/Plus OAuth (headless)** account
and selecting an eligible `openai/...` model should you plan a long run. OpenCode
V2 documents `opencode auth login openai` and `/connect` for interactive sign-in;
do not add an API key as a shortcut. On 2026-10-03 the host CLI was migrated
from Homebrew V1.17.11 to the official npm V2.0.22 in the user prefix. The
Homebrew V1 executable is backed up in the ignored private
`tmp/local-experiment/opencode-cli-upgrade/20261003/`; existing user data and
the Desktop service were not reset. V2 `auth list --format json` reports one
OpenAI OAuth connection and `opencode models` lists OpenAI model IDs, but
neither observation proves that this account/model uses the Pro subscription
without additional charges. The first real one-session pilot stopped at an
agent `safety_boundary` before any WP change: it demanded an independent
billing ledger. The owner explicitly requested continuation, so a single
OAuth connection, explicit OpenAI model and the owner's Pro attestation are
now the bounded experiment's routing preconditions, not a verified bill.

Historical 500-run example **for the previous series only, do not use now**.
Plan-only (zero model requests) with a model listed for this project; substitute
an eligible model ID after checking the account's entitlement:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --model openai/SELECTED_MODEL --max-runs 500 --max-minutes 10080
```

`subscription_preflight` must report `openai_oauth_label_present` before
executing; `SUBSCRIPTION_AUTH_UNVERIFIED` means **do not run**, not "Pro is
free through this CLI". The owner's explicit Pro attestation and this
OAuth-only route support the local experiment despite the billing uncertainty.
Start the bounded series with the same model ID:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --execute --use-pro-weekly-limit --model openai/SELECTED_MODEL --max-runs 500 --max-minutes 10080
```

The previous runner allowed **1–500 sequential runs and 1–10080 minutes**
per manual launch. Future gate batches are capped at 6/120; set less if host operation is
undesirable. The runner rechecks the OAuth label before **each** attempt and
pins `--model`; it rejects ambiguous OpenAI account listings and inherited
`OPENAI_API_KEY`/`OPENAI_BASE_URL`. These checks cannot independently verify
the active server account, that its subscription covers the selected model,
subagents' routing, config/plugin overrides, or provider billing. If account
selection changes or metered API usage is observed, stop rather than fall back to API-key/other
providers. A weekly quota error is a stop, never an automatic paid retry or a
request to buy more capacity. No cron/launchd task, recurring subscription or
background daemon is installed. Do not use `--dangerously-skip-permissions`.
The project instructions and tool permissions are not an OS sandbox.

### Conditional Astra advice for difficult blockers

The owner requested autonomous **consultation**, not a change of primary
model. A future runner prompt may call the project-local `astra-advisor`
subagent (`openai/gpt-6-astra`) when focused investigation leaves a genuinely
difficult blocker. The advisor has no tools; the parent supplies only sanitized
code/test context and independently checks recommendations. Limit this to two
calls total per parent session: one initial and at most one follow-up on that
blocker. No standalone
`opencode run`, new provider, API-key connection, paid fallback, retained-data
action or GitHub write is authorized. Recheck single OpenAI OAuth/no API-key
route and that the model is listed before a consultation using
`rtk proxy tools/local_experiment/llm/.venv/bin/python -m tools.local_experiment.astra_preflight`
(no model request); an unclear route,
model error, quota exhaustion or sign of new charges stops that call without
retry. Advisor model requests use the existing weekly allowance, **but do not
consume runner session slots**; the run/time guards do not cap tokens or USD.
`opencode models` lists Astra in this checkout, but the list and OAuth label
do not independently prove Pro billing or model entitlement. The configuration
and prompt must be deployed together at a clean boundary: editing the tracked
checkout during an active runner attempt would invalidate its clean-tree gate.

The owner raised the local series ceiling from 96 to 500 after observing low
ChatGPT Pro weekly allowance usage. This changes the **next** invocation's
scheduling guard, not the arguments of an already running process or the
subscription/billing evidence. An existing 96-session series may naturally
finish, then a one-shot continuation may use only the remaining 404 sessions
after independently checking its checkpoint, clean descendant HEAD and stop
reason `run_budget_reached`. Never continue after an error, unknown remote
state, safety boundary or quota exhaustion. Do not kill an active request just
to apply the new ceiling or replay its last operation.

On 2026-10-06 the owner raised the **cumulative** series ceiling to 1000 while
the 500-total series was active. The runner's per-launch maximum stays 500;
do not alter an in-flight process or replace the original seven-day clock.
A private one-shot handoff may start only the remaining 500 from a **new**
session after the existing run records all 240 of its own results with
`run_budget_reached`, a valid final `continue/more_work` checkpoint, clean
descendant HEAD and terminal succeeded/idle/non-active server session.
The prior series comprises 496 runner results, three separately reconciled
server successes and one failed OAuth request, if it reaches that boundary.
Recheck original time left, clean checkout and OAuth-only route before starting;
preserve every old failure/result, and fail closed on any other stop reason or
unknown state. The session ceiling is not completion, a token/USD cap, or
independent proof of ChatGPT Pro billing. Do not automatically renew the
allowance at a later weekly reset or schedule a recurring runner.
That extension design was subsequently **put on hold** after review. This
paragraph records the earlier decision, not permission to reactivate its job.

The checkout's `opencode.jsonc` denies normal use of every provider except
OpenAI. The CLI's noninteractive permission requests for the parent workspace,
`~/.codex`, and the original Vault path auto-reject. Each attempt therefore
copies the current Vault handoff, with identity checks, to a 0600 private file
under the ignored checkout attempt. The agent reads that snapshot, root
`AGENTS.md`, and current repository WPs; shell commands must use `rtk`.
Do not bypass denied external access. If writing the original Vault is denied,
record that sync as pending and do not claim it happened. Re-read current WP
state on every run; the snapshot is not independent runtime evidence.

One invocation can make many model/tool calls. The run/time limits are not a
token or currency cap, and exhaustion of the weekly allowance may arrive
mid-attempt. The first *actual* model invocation, account routing, and this
installed CLI's instruction-loading behavior are not verified by fake-CLI
tests. Stop and inspect if a real run shows a different provider or billable
route. OpenCode V2 [provider accounts](https://opencode.ai/v2/docs/cli/providers)
and [CLI automation](https://opencode.ai/v2/docs/cli/) document the intended
OAuth and `run` paths; installed CLI behavior must be confirmed separately.

## Stop and evidence contract

- The runner holds a nonblocking private lock; a second copy fails before
  launching a session. It checks the exact Git checkout/branch, a clean tree,
  the handoff, and a valid full HEAD before each attempt. It never resets or
  cleans work left by an agent or a user. Other editors are **not** covered by
  this lock; do not run one concurrently.
- A session writes one `0600` checkpoint containing its nonce, attempt number,
  final HEAD and `continue/more_work` or a `stop` reason. This is an agent
  control **claim**, not independent evidence of completion, backend state or
  human acceptance. `continue` requires a new descendant commit; `stop` never
  converts passing tests into 100% completion.
- Private `stdout.jsonl`, `stderr.log`, checkpoint, per-attempt result and final
  `summary.json` are preserved under ignored
  `tmp/local-experiment/autonomous-runner/run-*/` (0700 directory, 0600 files).
  CLI output can contain sensitive material: do not publish these logs. A
  polled size/deadline limit terminates the **local process group** and stops further
  attempts. It does **not** prove that OpenCode's shared background service
  cancelled its work, nor strictly cap bytes written between size polls. Treat timeout/interruption as
  `PROCESS_STOPPED_REMOTE_UNCONFIRMED`, inspect the service/session and the
  checkout before any manual resumption. Never blindly rerun an unknown RPC,
  SQL operation or retained-data mutation.
- A group-signal race can yield `ProcessLookupError` or (on macOS, including
  a zombie leader) `PermissionError`. The first case reaps the local child
  with a bound; permission denial and an unreaped child stop as
  `PROCESS_STOPPED_REMOTE_UNCONFIRMED`. Neither case authorizes replay or
  establishes that a shared server-side session has stopped.
- Maximum invocation/time limits are local scheduling guards, not a hard
  billing cap or a guarantee that a model/provider request cannot outlive the
  runner. `AGENTS.md` and the checkpoint are not enforcement against an
  adversarial same-user actor. Repository/Vault changes and runtime evidence
  still require ordinary scoped review and verification.

To stop using it, do not run `--execute` again. Leave existing private logs,
commits, preserved evidence and backups untouched. No data migration, service
installation or VM reset is required. The runner does not start Colima by
itself and does not create GitHub Issues/PRs or push.
