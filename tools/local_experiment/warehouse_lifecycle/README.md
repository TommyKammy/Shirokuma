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
