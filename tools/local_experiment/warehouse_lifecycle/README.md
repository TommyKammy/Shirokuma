# Local warehouse lifecycle experiment

A fixed-scope host controller exercises idle suspension and wake of a
new XS Trino Deployment while a separate S Deployment continues to serve queries.
A new Gateway/PostgreSQL Pod and two Services connect them. Eleven primary resources
and their verified controller-owned children are disposable and run only in
`shirokuma-dev` on `colima-mac-studio-solo`. All SQL uses synthetic TPCH data.

```sh
rtk make test-local-warehouse-lifecycle
rtk make local-warehouse-lifecycle-plan GATEWAY_WAREHOUSE_BUILD_TOKEN=<token>
rtk make local-warehouse-lifecycle-run GATEWAY_WAREHOUSE_BUILD_TOKEN=<token>
rtk make local-warehouse-lifecycle-recover WAREHOUSE_LIFECYCLE_RUN_TOKEN=<token>
```

The Gateway warehouse build/pins are reused. The run uses the existing pinned LLM
Python environment for local OTel export; it makes no LLM or cloud calls. Credentials
and plans stay under private, ignored `tmp/local-experiment/warehouse-lifecycle`.
No retained Trino/catalog/database/PVC is connected or changed.

## Local CLI session

Run the owner in one terminal. It provisions the same disposable topology and
prints a `ready` record with its session ID after the initial queries and busy
deferral have been checked:

```sh
rtk make local-warehouse-lifecycle-session GATEWAY_WAREHOUSE_BUILD_TOKEN=<token>
```

In another terminal, use the printed session ID:

```sh
rtk proxy go run ./cmd/shirokuma chill XS --profile local-experiment --session <session-id>
rtk proxy go run ./cmd/shirokuma wake XS --profile local-experiment --session <session-id>
```

Only `XS` and the `local-experiment` profile are accepted. The session owns a
private, same-user Unix socket under `/private/tmp/shirokuma-wl-<session-id>`;
commands cannot select arbitrary endpoints or execute host commands. The owner
keeps the UID inventory, query ledger, local forwards and telemetry context alive.
New connections are accepted for up to ten minutes and sixteen connections,
including retries, after preparation. It removes
its known resources after a completed wake or when the session stops.

Every request has a UUID. If a response is lost, retry with the same
`--request-id <uuid>` printed by the command; the owner replays its saved response
without applying the transition again while the session remains available.
After wake it keeps a short replay-only grace period before closing. A new chill
request when already chilled, or wake before chilling, returns a verified no-op
after fresh ownership and routing checks. A CLI timeout or disconnect does not
cancel an operation already running in the owner. Inspect the private session
receipt for the final outcome if the owner has closed. Recovery uses the same
`local-warehouse-lifecycle-recover` target and the session ID as the run token.

### Archived RPC status (read-only)

```sh
rtk make local-warehouse-session-status WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>
```

This reads **only** that session's private `rpc/journal.json` under the fixed
warehouse-lifecycle runs directory. It checks bounded size, ownership, mode,
single link, exact schema/request IDs, stored replies, and the RPC response
contract. A saved `verified` result is labeled `recorded_result`, never a fresh
warehouse observation: `runtime_state` is always `unknown`. A pending journal
is shown as `pending`, not resumed. A missing, changed or invalid journal fails
with `JOURNAL_UNVERIFIED` rather than guessing success. The output includes a
snapshot SHA only for correlation, not authentication or proof that the final
owner, query or cleanup ran. It does not open credentials, connect to the owner,
call Trino/Kubernetes, replay SQL, or invoke recovery. Existing session and
query-mode commands remain unchanged. This does **not** implement owner restart
recovery or a durable queue. To stop using the view, stop invoking this command;
no saved run or backup needs to be removed or migrated.

Opt in to cross-checking a **saved completion claim** against the final owner
receipt and all referenced operation files:

```sh
rtk make local-warehouse-session-completion WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>
```

This rereads the bounded private journal and owner receipt, checks the recorded
closed/passed/cleanup/telemetry fields, matches each durable RPC response to a
session request and the exact operation file's raw SHA-256, and rejects extra
operation files or conflicting replays. Only a fully correlated saved claim
produces `recorded_completion: correlated-saved-claim`. Absent, incomplete,
changed, or contradictory evidence fails with `SAVED_COMPLETION_UNVERIFIED`
and no partial JSON. The files share one original owner; this is **not** an
independent check of actual cleanup, backend state, audit delivery or execution
provenance. `runtime_state` stays `unknown`, and nothing is restarted or replayed.
The original journal-only status command and its output remain unchanged.

To locate **one original request UUID** after its reply is lost, without
retransmitting it:

```sh
rtk make local-warehouse-session-request-status \
  WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id> \
  WAREHOUSE_LIFECYCLE_REQUEST_ID=<original-request-uuid>
```

This reads the same fixed saved journal and adds `request_lookup` for that exact
canonical UUID. `recorded-in-snapshot` identifies the journal's stored action,
result and optional operation/receipt correlation IDs. `pending` and `unknown`
are never changed into success. `not-recorded-in-snapshot` has null result and
**does not prove no operation ran** (a lost or incomplete journal is possible).
It never connects to the owner, sends an RPC or replays SQL. Both Make values
are passed through environment variables, not interpolated into a shell recipe.
The old all-entries status output remains byte-identical; the optional saved
completion claim refers to its entire session, never an absent request ID.

The same archived request can be inspected from the Go CLI while in this checkout:

```sh
rtk proxy go run ./cmd/shirokuma warehouse saved-request \
  --session <session-id> --request-id <original-request-uuid> \
  --profile local-experiment
```

This is a separate command from live `warehouse query`. It starts only the fixed
offline status script with validated IDs using the dedicated local Python, with
no inherited environment or socket client. The CLI checks the bounded report's
identity and `runtime_state=unknown` and emits no partial JSON on child failure.
Use `--repo-root` only if running outside the checkout. Missing UUIDs still
mean `not-recorded-in-snapshot`, **not** proof of non-execution. No owner recovery,
SQL retry, backend observation or cleanup verification is performed.

If `chill`, `wake` or `warehouse query` loses a reply, its error identifies the
original session and UUID and points to this read-only lookup. Do not interpret
the archived result as proof of current state or authorization to replay.
Only the **same still-live owner** (including its bounded replay grace) can
replay an already recorded response for the same UUID; after owner exit, do not
send the request to a new session, even if the saved lookup is missing or pending.

## Query-triggered wake

The optional query session accepts a fixed caller SELECT after XS is chilled:

```sh
rtk make local-warehouse-query-session GATEWAY_WAREHOUSE_BUILD_TOKEN=<token>
rtk proxy go run ./cmd/shirokuma chill XS --session <session-id>
rtk proxy go run ./cmd/shirokuma warehouse query XS --session <session-id>
```

This mode retains the same disposable topology, local profile and existing
ten-minute/sixteen-connection owner limit. It requires `chill` before `query`;
standalone `wake` is unavailable in query mode. The original chill/wake session
and Query Doctor's `query run/status` commands keep their existing contracts.

One owner request records durable intent, wakes XS with the existing readiness
probes, then sends the separate fixed `xs_caller` SELECT once through Gateway.
It checks 25 TPCH nation rows, the XS node/result hash, terminal query history,
unchanged S identity, no S fallback and complete consumption of all known results.
The caller request UUID determines its distinct source header. These synthetic
headers establish experiment provenance; they are not tenant authentication.

The verified response includes a query ID, row count/hash and a single POST
count. A wake or readiness probe alone cannot produce a successful caller
response. SQL, tables and endpoints cannot be supplied by the caller. The
measured wait includes probes and local audit/guard work and is not cold-query
performance evidence.

The query CLI waits up to 240 seconds. Disconnecting it does not cancel the
owner's work. Retry only with the same session and `--request-id` while that
owner is alive or in its fifteen-second replay grace. An in-flight or lost
result remains unknown; no new POST is issued. A saved busy response is not
converted into later execution. There is no restart recovery of request IDs,
new-session resubmission or permanent queue.

Query sessions add one verified caller-query audit to the existing three
controller decisions and final run audit. Five local OTel spans correlate with
Pawprints/Loki; the query audit span measures delivery, while separately stored
durations describe the controller work. The owner returns query success only
after caller verification and audit delivery, then replays the saved response
briefly and cleans up its known resources. The final session receipt separately
confirms cleanup and the complete five-event audit.

## What is verified

The host journals every SQL POST intent before submission and holds an initial XS
POST response without polling its next URI. Trino has not submitted that query yet,
so it is absent from the full query list. The controller must still defer suspension
because its own result handle is unfinished. This is a controlled single-producer
experiment, not protection against arbitrary untracked producers.

After consuming the result, two fresh complete query observations separated by a
measured idle window must be empty of nonterminal queries. The controller deactivates
XS in Gateway, reads that state back, repeats the drain observation, and changes only
the recorded XS Deployment to zero replicas using UID/resource-version/current-value
preconditions. S must keep returning the expected 25 nation rows. An XS request while
suspended must not reach S through fallback.

Wake changes the same Deployment to one replica. A new Pod UID and new Trino
coordinator process ID must become ready before Gateway activation. The new XS
history starts empty; final XS/S reads must match the original fixtures and their
own terminal query histories. Unknown query states, foreign query identities,
incomplete results, lost POST responses, stale observations and failed reads never
justify a scale-down.

Network policies allow only the generated Gateway to the matching XS/S backend
port. Registered cluster IPs come from verified generated Services. EndpointSlices
are checked against the same owned ready Pod. No DNS or external egress is needed.

## Ownership and audit

Deployment-to-ReplicaSet-to-Pod controller UID chains, template hashes and allowed
specifications are checked before recording child UIDs. Only recorded UIDs are
removed: parents are orphan-deleted, followed by known children and dependencies.
Unknown create outcomes or conflicts preserve the remaining resources for recovery.
The saved plan is the recovery reference; original receipts remain unchanged.

Three decision events and one run event are written to existing Pawprints/Loki and
read back. Busy deferral is operational and has `blocked_by_policy=false`.
Model usage and cost stay NULL. OTel traces cover the actual run and callback audit
delivery; delivery spans do not pretend to trace an earlier controller decision.
The controller's measured phase durations are explicitly separate. Successful query
behavior, cleanup and audit delivery are all required for a passing run.

This prototype is not a generic CRD controller, a permanent background service,
a multi-worker graceful drain implementation, HA, tenant isolation, or a production
acceptance result. Interactive `shirokuma chill`/`wake` integration remains a separate
acceptance check from the synchronous runner; its live evidence is recorded in
WP-L3-VW-002. The fixed generated plan is the only supported warehouse definition.
