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
operation is automatically retried.

## Use

From `worktree/local-autonomous`, with any interactive agent/editing session
finished and with OpenCode already installed/configured:

```sh
rtk make local-autonomous-plan
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --max-runs 2 --max-minutes 60
```

Both commands are **plan only** and make zero model requests. To spend model
capacity for up to two sequential `opencode run` invocations and up to 60
minutes of local runner time, explicitly opt in:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/autonomous_runner.py --execute --acknowledge-model-charges --max-runs 2 --max-minutes 60
```

Allowed limits are 1–8 invocations and 1–360 minutes per manual launch. This
does **not** bound USD/token charges: one invocation may make many model/tool
calls. Check provider pricing and account-level spending limits before opting
in. No cron/launchd task, recurring subscription or background daemon is
installed. Do not use `--dangerously-skip-permissions`. The project instructions
and tool permissions remain in force, but neither is an OS sandbox that makes
model actions inherently safe.

As of 2026-10-02 this host's `opencode` reports **1.17.11**, and its local help
shows `run --format json`. OpenCode V2 documents `opencode run` for automation
and `AGENTS.md` for project guidance, but this runner does not upgrade or
reconfigure the installed CLI. Its first *actual* model invocation and the
installed version's instruction-loading behavior are not verified by offline
fixtures. The default plan never calls a model.

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
