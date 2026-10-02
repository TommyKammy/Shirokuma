# Bounded local OpenCode continuation (Shirokuma)

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

## Use

From `worktree/local-autonomous`, with any interactive agent/editing session
finished, inspect the **local CLI's** connection and model list. The Pro login
in this chat is not automatically shared with `opencode`:

```sh
rtk proxy opencode auth list
rtk proxy opencode models openai
```

Only after connecting the **OpenAI ChatGPT Pro/Plus OAuth (headless)** account
and selecting an eligible `openai/...` model should you plan a long run. OpenCode
V2 documents `opencode auth login openai` and `/connect` for interactive sign-in.
The installed V1 help instead lists `opencode auth login --provider openai`;
the user must complete the appropriate interactive OAuth login themselves.
Do not add an API key as a shortcut.
Neither `auth list` nor a model catalog alone proves billing or entitlement.
This host still has `opencode` **1.17.11** (not V2), currently reports one
credential but **no OpenAI credential label**; V2 documentation does not
establish that the installed V1 CLI will load the same instructions or login
method. No login, CLI upgrade, or paid model run was performed by this change.

Plan-only (zero model requests) with a model **actually available to this
account**; substitute the model ID found above:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --model openai/SELECTED_MODEL --max-runs 96 --max-minutes 10080
```

`subscription_preflight` must report `openai_oauth_label_present` before
executing; `SUBSCRIPTION_AUTH_UNVERIFIED` means **do not run**, not "Pro is
free through this CLI". After personally confirming the selected account,
model and subscription route, start the bounded series with the same model ID:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --execute --use-pro-weekly-limit --model openai/SELECTED_MODEL --max-runs 96 --max-minutes 10080
```

Allowed limits are **1–96 sequential runs and 1–10080 minutes (seven days)**
per manual launch. Set smaller values if unattended host operation is
undesirable. The runner rechecks the OAuth label before **each** attempt and
pins `--model`; it rejects ambiguous OpenAI account listings and inherited
`OPENAI_API_KEY`/`OPENAI_BASE_URL`. These checks cannot independently verify
the active server account, that its subscription covers the selected model,
subagents' routing, config/plugin overrides, or provider billing. If account
routing cannot be verified, stop rather than fall back to API-key/other
providers. A weekly quota error is a stop, never an automatic paid retry or a
request to buy more capacity. No cron/launchd task, recurring subscription or
background daemon is installed. Do not use `--dangerously-skip-permissions`.
The project instructions and tool permissions are not an OS sandbox.

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
- Maximum invocation/time limits are local scheduling guards, not a hard
  billing cap or a guarantee that a model/provider request cannot outlive the
  runner. `AGENTS.md` and the checkpoint are not enforcement against an
  adversarial same-user actor. Repository/Vault changes and runtime evidence
  still require ordinary scoped review and verification.

To stop using it, do not run `--execute` again. Leave existing private logs,
commits, preserved evidence and backups untouched. No data migration, service
installation or VM reset is required. The runner does not start Colima by
itself and does not create GitHub Issues/PRs or push.
