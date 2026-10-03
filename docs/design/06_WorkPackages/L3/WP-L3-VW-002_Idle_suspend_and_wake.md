---
project: Shirokuma
doc_id: "WP-L3-VW-002"
title: "Idle suspend and wake for VirtualWarehouse"
status: local-partial
created: 2026-07-05
updated: 2026-10-03
version: "0.75"
area: "workpackage"
tags: [shirokuma, wp, l3, warehouse]
---

# Idle suspend and wake for VirtualWarehouse

## 2026-10-03: reject ambiguous RPC header whitespace (offline)

The bounded single-request Unix parser no longer strips whitespace from all
header values before validating framing. It requires one space after the colon
and no extra surrounding whitespace or tabs, so a padded `Content-Length`
cannot be normalized into an accepted transition. Disposable socket negatives
return 400 without dispatch; the real Unix/Go fixture and focused suite pass.
The dedicated VM remained stopped; no archived UUID replay, backend/SQL or
retained session/backup mutation. This cannot prove caller receipt, backend
authenticity or restart recovery. Roll back only the parser and test additions,
preserving archived sessions. Original Vault sync pending outside checkout;
broader verification is recorded in Milestones.

## 2026-10-03: buffered trailing request bytes refused (offline)

The single-request Unix RPC reader now refuses bytes already received beyond
the declared HTTP body rather than dispatching the valid prefix. A disposable
connection delivering a complete request plus a NUL or another request line in
one receive is rejected before any journal entry or handler call. The bounded
reader does not wait for future bytes after an exact body; later arrivals remain
outside this assertion. Focused Warehouse and combined checks passed (Milestones).
No archived UUID replay, backend/SQL, VM start or retained session/backup change.
This is not caller-receipt, authenticated backend or restart-recovery evidence.
Roll back only this parser guard/test, preserving saved sessions. Direct original
Vault synchronization remains pending outside checkout.

## 2026-10-03: request receive must finish before parsing deadline (offline)

The owner now checks its absolute HTTP read deadline immediately after each
socket receive; a complete disposable request returned after expiry is refused
before parsing or dispatch. Focused Warehouse tests passed after correcting a
fixture that initially used an uninitialized owner. No archived UUID replay,
backend/SQL, VM start, retained session or backup mutation. This does not prove
archived caller receipt, backend authenticity or restart recovery. Roll back
only the receive guard and test, keeping archived sessions. Direct original
Vault synchronization remains pending outside checkout.

## 2026-10-03: peer check precedes disposable request read (offline)

The existing Unix-socket peer-UID rejection now has a stronger disposable
regression: a mismatched UID receives a refusal without invoking the request
parser, handler or saving a request/reply. No owner implementation changed.
The focused Warehouse suite passed; combined verification is in Milestones.
No VM start, live backend/SQL, archived UUID replay or retained session/backup
mutation. This does not prove remote backend authentication, historical caller
receipt, restart recovery or operator acceptance. Roll back only the test
assertions, retaining archived evidence. Original Vault sync pending outside
checkout.

## 2026-10-03: serve-loop clock must follow stop callback (offline)

A disposable monotonic clock advanced through TTL during a false-returning stop callback; the old pre-callback sample admitted another `accept`. The serve loop now samples time after the callback and refuses that connection with reason `ttl`. A focused negative failed before repair; the Warehouse suite passed afterward (Milestones). No VM start, live backend/SQL, archived UUID replay or retained session/backup mutation. This does not authenticate backend state, prove caller receipt, owner restart recovery or operator acceptance. Roll back only the serve-loop clock change and disposable fixture, preserving saved sessions. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: serve-loop TTL expiry inside stop callback (offline)

A test-owned callback advances the owner past TTL while returning false. The existing serve-loop TTL check refuses another socket accept and reports `ttl` with zero requests; no owner behavior changed. Focused Warehouse and combined verification are in Milestones. No VM start, backend/SQL, archived request replay or retained session/backup mutation. This does not authenticate backend state, prove archived caller receipt, owner restart recovery or operator acceptance. Roll back only the disposable test; preserve existing sessions and stopped VM. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: serve-loop stop cannot accept another connection (offline)

A test-owned stop callback setting the event while returning false previously allowed `serve` to enter another `accept` before the loop noticed the stop. It now exits before accepting again. The isolated negative failed before repair; focused and broader evidence is in Milestones. Existing admitted workers still drain. No archived request replay, backend/SQL, VM start or retained session/backup change. This does not prove caller receipt, backend identity, restart recovery or operator acceptance. Roll back only the serve guard and fixture, keeping archived sessions; original Vault sync remains pending outside checkout.

## 2026-10-03: stop callback raises after setting stop (offline)

A disposable callback sets the stop event then raises: its fresh UUID and a subsequent fresh UUID are durably rejected, the completed UUID replays exactly, the callback runs only once and no second handler runs. This pins existing fail-closed dispatch behavior; no owner implementation, archived replay or live backend/SQL change. Focused checks and limits are in Milestones. It does not prove archived caller receipt, backend authenticity or restart recovery. Roll back the fixture only; retain sessions and backups. Original Vault synchronization remains pending outside checkout.

## 2026-10-03: stop during a false dispatch callback (offline)

A disposable stop callback sets the owner's stop event but returns false. The existing post-callback guard durably rejects the new UUID without running its handler or recording a false callback-failure code. No owner implementation change, archived replay, VM start, live backend/SQL or retained session/backup mutation. This cannot prove original caller receipt, authenticated backend state or restart recovery. Roll back only this disposable fixture; preserve archived sessions. Focused and broader results are in Milestones; original Vault synchronization remains pending outside checkout.

## 2026-10-03: owner expiry during dispatch stop check (offline)

An independent disposable fixture moves the owner past TTL inside its stop callback, which returns false. The existing post-callback TTL guard durably rejects the fresh UUID without handler execution or a false callback-failure code. No owner behavior change, VM start, live backend/SQL, archived replay or retained session/backup mutation. This does not prove caller receipt, authenticated backend state or restart recovery. Roll back only this fixture, retaining historical sessions; original Vault synchronization remains pending outside checkout. Focused and broader checks are in Milestones.

## 2026-10-03: expired owner skips stop callback at dispatch (offline)

After the owner TTL, a waiting disposable worker now skips the stop callback and durably rejects its fresh UUID without handler execution or a misleading callback-failure code. The isolated negative failed before repair; focused and broader checks are recorded in Milestones. This is a dispatch-boundary guard, not proof that a request accepted before TTL reached the backend or that an archived caller received a reply. No live backend/SQL, VM start, archived replay, retained session/backup change or unknown-request recovery. Roll back the TTL callback guard/test only; preserve historical sessions. Direct original Vault sync pending outside checkout.

## 2026-10-03: stopped owner skips callback for waiting dispatch (offline)

An already stopped disposable owner no longer invokes an attached stop callback when a previously accepted worker reaches dispatch. The fresh UUID is durably rejected without a handler call or spurious `STOP_CALLBACK_FAILED`, and a completed UUID replays unchanged. This check is distinct from the earlier callback-removal regression; focused and broader verification are in Milestones. No VM start, live backend/SQL, archived replay, retained session/backup change or caller-receipt proof. It cannot establish independent backend authenticity or restart recovery. Roll back this dispatch guard/test only, preserving historical sessions. Direct original Vault sync pending outside checkout.

## 2026-10-03: stopped owner refuses a waiting fresh worker (offline)

Dispatch now checks the owner's stop event before admitting a fresh UUID. A disposable failed stop callback sets the event; after the callback is removed, a worker reaching dispatch with a new UUID is durably rejected without invoking the handler, while an already completed UUID still replays its saved result. The previous replay-grace and TTL checks remain. This does not prove actual backend state, archived caller delivery or restart recovery. No VM start, live backend/SQL, retained journal/backup change or archived replay. Roll back only the stop-event guard and disposable test, keeping saved sessions; original Vault direct sync pending outside checkout. Verification is in Milestones.

## 2026-10-03: completed UUID survives a failed stop check (offline)

A disposable owner fixture verifies the existing fail-closed dispatch behavior: after a completed UUID, a stop callback that raises rejects and journals a fresh UUID without handler execution; the completed UUID still returns its exact saved response without a second operation. No production owner behavior was changed. Focused and full verification are in Milestones. No VM start, live SQL/backend, archived replay, retained session or backup change. This does not prove caller receipt, owner restart recovery or independent backend/cleanup state. Roll back only this disposable test; keep archived sessions and unknown requests untouched. Original Vault direct sync pending outside checkout.

## 2026-10-03: one-shot stop observed at dispatch (offline)

A disposable stop callback returned true only to the dispatch worker, then false. Previously the first UUID was rejected but a subsequent new UUID could reach the handler before the serve loop observed a stop. Dispatch now enters bounded replay-only grace on the first true result; subsequent new UUIDs are durably rejected, while already saved UUIDs retain same-owner replay behavior. Focused Warehouse and full local/design verification passed. This does not establish backend state, caller receipt, archived restart recovery or operator acceptance. No dedicated VM start, archived replay, retained session/backup modification or live SQL/API. Roll back the guard and disposable test only; preserve archived evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: serve-loop stop callback failure returns failed (offline)

A disposable stop callback throwing on the first serve-loop check previously escaped as an exception rather than returning a failed owner result. The owner now records `STOP_CALLBACK_FAILED`, stops accepting, drains existing workers, persists its journal and returns `failed`; no handler is dispatched for this fixture. The isolated negative failed before repair, then the focused Warehouse suite and local verification passed (see Milestones). This does not authenticate backend state, establish archived UUID recovery or caller receipt. No dedicated VM start, archived request replay, retained session/backup change or live API/SQL. Roll back only the serve-loop guard and disposable test; retain saved evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: stop callback failure at dispatch (offline)

A disposable owner with an injected stop callback exception previously let the worker exit before journaling the new request, with no handler call or durable rejection. Dispatch now records an uncertain rejected response and stops the owner on callback failure; previously admitted operations still follow their existing drain path. The isolated negative failed before repair and the focused Warehouse suite passed afterward. Full verification initially hit the unchanged intermittent saved-completion test (`SAVED_COMPLETION_UNVERIFIED`); its failed log is retained, and focused and full reruns passed. No archived UUID replay, real backend/API/SQL, VM start or retained session/backup mutation. An uncertain saved response is not proof of caller delivery or independent backend state, and this does not establish restart recovery or operator acceptance. Roll back owner guard/test only, keeping saved evidence and stopped VM. Original Vault direct sync pending outside checkout; results in Milestones.

## 2026-10-03: TTL checked again at dispatch (offline)

A connection accepted just before expiry may reach its worker after expiry. New UUIDs now fail closed at dispatch, persist an uncertain rejected response and never invoke the handler; a disposable pre-accepted/post-deadline regression exercises this gap. Previously admitted handlers still drain. Focused checks and broader verification are recorded in Milestones. No live backend, archived request replay, retained session/backup mutation or VM start. The rejected record cannot prove caller receipt or backend state. Roll back only the owner guard/test, retaining historical evidence and the stopped VM. Original Vault direct sync pending outside checkout.

## 2026-10-03: TTL fixture keeps admitted handler pending (offline)

The disposable RPC TTL/drain test now holds its admitted handler until an explicit release instead of relying on a three-second wall-clock expiry, and asserts no response was produced before that release. The existing `finally` releases and joins the test-owned thread on assertion failure. Warehouse focused and broader verification are in Milestones. This is a test fixture improvement only; it does not establish live backend behavior, archived caller receipt, unknown-request recovery or operator acceptance. No archived UUID replay, VM start, API/SQL, retained session or backup change. Roll back only the test assertion/wait change, preserving evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: disposable concurrent reply order (offline)

The serial-handler test now checks the exact durable reply sequence for a pending retry, competing busy UUID, original verified response and both completed replays; its two saved request records remain unchanged. `make test-local-warehouse-lifecycle` passed; combined verification is recorded in Milestones. This tests only one disposable owner and does not prove archived caller receipt, backend authenticity, owner restart recovery, historical cleanup or a repair for intermittent failures. No archived UUID replay, VM start, API/SQL or retained session/backup mutation. Roll back only the test assertions, preserving evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: completed disposable original UUID replay (offline)

The serial-handler fixture now retries its test-owned original UUID after both concurrent replies and handler completion. It requires the exact verified original caller response, unchanged two request records and one handler call; the competing UUID still returns its saved busy response. `make test-local-warehouse-lifecycle` passed. This is within one disposable owner, not archived recovery, backend authentication or proof of historical caller receipt; no real API/SQL, archived UUID replay, VM start or retained journal/backup mutation. Roll back this test assertion only and retain saved evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: disposable concurrent responses match durable entries (offline)

The serial-handler fixture now requires the original caller's complete verified response to carry its own UUID/action, and checks that both that response and the competing busy reply match their separate durable journal entries. The first focused run exposed an incorrect test assumption that the competing busy request was not saved; the corrected assertion and full focused Warehouse suite passed. This strengthens only the disposable regression and does not establish a fix for intermittent failures, backend authenticity, caller delivery in archived sessions or safe unknown-request replay. No VM start, API/SQL, retained journal/backup mutation or archived UUID replay; dedicated Colima was observed stopped. Full verification is recorded in Milestones. Roll back this test assertion only, preserving evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: concurrent busy reply correlation (offline)

The disposable serial-handler fixture now checks that the competing busy 409 carries the competing UUID, and that the original caller thread finishes with exactly one response after handler release. The focused Warehouse suite and full local verification passed; this is test-only and does not demonstrate a fix for the previously intermittent Warehouse failure. No archived UUID was replayed and no backend, VM, retained session or backup was changed. Roll back this assertion only, retaining evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: query retry fixture holds its disposable handler (offline)

The disconnected-query test now holds its injected handler through both pending and competing busy replies rather than allowing a three-second fixture timeout to release it under load. It releases the handler in `finally` and checks each complete 409 envelope's result, query field and own request UUID before inspecting successful replay. Focused Warehouse suite passed; broader verification is in Local Experiment Milestones. This is test-only and does **not** prove the prior intermittent `KeyError` was caused or fixed by the fixture. No archived UUID replay, actual backend/API/SQL, owner implementation, retained journal/backup or VM start. Roll back only this disposable test, preserving saved evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: disposable busy-handler test ordering (offline)

The serial-handler socket fixture now holds its test-owned first handler until both concurrent replies are observed, rather than releasing it after an arbitrary three-second wait under suite load. The assertion checks the complete 409/unknown envelope and original request identity before inspecting a result; a malformed 400 response can no longer surface only as an opaque `KeyError`. Focused test and Warehouse suite passed after the test-only change. The previous full-regression 400-like failure was not independently reproduced or established to have this cause; preserve its log and do not claim the intermittent failure is repaired. No production owner, saved journal, archived UUID, VM, API or SQL was changed. Roll back this fixture only; do not replay retained requests. Original Vault direct sync pending outside checkout.

## 2026-10-03: partial reply before disposable owner exit (offline)

A tenth test-owned child writes only a partial HTTP header after a durable verified reply, injects a failed send and exits. The disposable caller receives exactly that incomplete prefix, while an independent saved reader still reports `unknown-no-replay` and unknown runtime state for original and fresh UUIDs; a new owner process refuses the occupied session without dispatch or journal mutation. Initial focused run failed because the fixture did not enter the socket branch; after correction `make test-local-warehouse-lifecycle` passed. No archived UUID replay, backend/API/SQL, VM start or retained session/backup mutation; dedicated Colima was read-only observed stopped. Partial bytes do not establish caller receipt, backend authenticity, historical cleanup or operator acceptance. Roll back the disposable test only, retaining VM/evidence; original Vault direct sync pending outside checkout.

## 2026-10-03: failed send after durable reply (offline)

An independent ninth disposable child-process boundary injects `BrokenPipeError` on send after the durable verified reply, observes no bytes at the test caller, then exits. An independent saved reader still classifies the original and fresh UUID `unknown-no-replay` with unknown runtime state; a new owner process refuses the occupied identity without dispatch or journal mutation. `make test-local-warehouse-lifecycle`, `make verify-local-experiment` and `make verify-design-context` passed. This is injected socket failure, not archived recovery or authenticated backend state. Dedicated Colima remained stopped; no API/SQL, VM start, unknown UUID replay or retained session/backup mutation. Roll back only the disposable test, retaining VM/evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-03: disposable handler failure across owner exit (offline)

An eighth test-owned child process exits immediately after journaling an injected handler exception as an uncertain response, before any reply send. A separate saved reader still classifies the original and unrelated fresh UUIDs `unknown-no-replay` with unknown runtime state; independent parent and new-process owner attempts refuse the occupied session without dispatch or journal mutation. `make test-local-warehouse-lifecycle` passed. This exercises a disposable process-exit boundary, not backend authentication, caller receipt or archived UUID recovery. Read-only dedicated Colima was stopped; no VM start, actual API/SQL, retained session/backup mutation or unknown-request replay. Roll back this test boundary only while preserving archived evidence and stopped VM; original Vault direct sync remains pending outside checkout.

## 2026-10-03: disposable caller receives reply before owner exit (offline)

A seventh test-owned child-process boundary reads a complete 200/verified socket response after the injected handler and durable reply, then exits. An independent reader still classifies the saved UUID and a fresh UUID `unknown-no-replay` with unknown runtime state; a separate owner process refuses journal collision without dispatch or journal mutation. Initial focused run failed because the new boundary inherited the `sent` fault injector and exited before receipt; the corrected branch passed `make test-local-warehouse-lifecycle`. Caller receipt in this disposable process is not authenticated backend state or an archived caller's receipt. No archived UUID replay, actual backend/API/SQL, VM start or retained data/backup mutation; dedicated Colima observed stopped. Roll back the test case only, preserving VM/evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: clean owner shutdown cannot transfer session identity (offline)

A sixth disposable child-process boundary closes the owner normally after a durable verified response, without a caller reply. A separate reader still classifies the original and a fresh UUID `unknown-no-replay` with unknown runtime state; a second process refuses the session identity without dispatch or journal changes. `make test-local-warehouse-lifecycle` passed. This uses only test-owned temporary paths and an injected handler: no archived UUID replay, backend/API/SQL, VM start, retained session or backup mutation. Clean shutdown is not caller delivery or authenticated backend recovery; historical cleanup and operator acceptance remain open. Roll back the test addition only, retaining the stopped VM and saved evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: fresh process also refuses exited owner identity (offline)

For each of five disposable owner-exit boundaries, a second, independently launched owner process now tries the same test-owned session and must refuse `JOURNAL_COLLISION` without dispatch or changing the journal; the existing parent-process collision and new-UUID saved-classification assertions remain. This exercises process-independent collision refusal, not safe replay or authenticated backend recovery. Only test-owned temporary paths were created and removed; archived UUIDs, runs and backups were untouched, with dedicated Colima stopped. Roll back the added child assertion only and preserve archived evidence/VM. Original Vault sync pending outside checkout.

## 2026-10-03: new UUID cannot adopt an exited disposable owner (offline)

Final regression also passed `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check`; private logs are under `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-039/`.

At each of five test-owned process-exit boundaries (durable intent, handler return, durable handler response, reply journal, successful send), a separately generated request UUID absent from the saved journal remains `not-recorded-in-snapshot` and `unknown-no-replay`, with `runtime_state=unknown` and the original journal hash unchanged. The existing fresh-owner collision test still refuses dispatch. `make test-local-warehouse-lifecycle` passed; milestone verification is in Local Experiment Milestones. This strengthens the no-adoption regression, not the backend recovery implementation: there was no archived UUID replay, authenticated backend read, real caller receipt or historical cleanup. Dedicated Colima remains stopped; no API/SQL, VM start, retained run/backup mutation. Roll back this disposable test assertion only, keeping saved evidence and the VM. Original Vault sync remains pending outside checkout.

## 2026-10-03: two more disposable process-exit boundaries (offline)

Two independent additions to the test-owned child-process matrix: exit after the injected handler returns but before its response is persisted, and exit after a successful socket send. A separate reader finds the former pending with no reply and the latter verified with one recorded reply; both remain `unknown-no-replay` with `runtime_state=unknown`. A fresh owner refuses the same identity without invoking a handler or altering the journal. A successful send is **not** an acknowledgment by the original caller, and a handler return is not a saved backend result. The injected handler has no backend; no archived UUID was replayed. Focused and milestone verification are recorded in Local Experiment Milestones. Dedicated Colima was observed stopped; no API/SQL, retained run/backup modification or VM start. Roll back only the two disposable test cases, preserving saved evidence and the VM. Authenticated backend state, historical cleanup and operator/production acceptance remain open; direct original Vault sync pending outside checkout.

## 2026-10-03: disposable process exit after reply journal (offline)

A third test-owned child now exits with `os._exit` immediately after fsync of the reply record but before the socket send. A separate process reads the verified saved response and one journaled reply, nevertheless classifies the request `unknown-no-replay` with `runtime_state=unknown`; a fresh owner refuses collision without dispatch or journal mutation. This adds a real process-exit boundary to the previous two, not backend recovery, original-caller delivery evidence or a restart of an archived session. Only disposable test directories are cleaned. Focused and milestone regressions are recorded in Local Experiment Milestones. Dedicated Colima remained stopped; no API/SQL, unknown UUID replay or retained session/backup mutation. Roll back test only, preserving saved evidence and VM. Independent backend identity, historical cleanup and operator/production acceptance remain open. Original Vault direct sync pending outside checkout.

## 2026-10-03: real disposable process exit at journal boundaries (offline)

A new test-owned private session, socket root and journal were created for each of two child processes. Each child exited with `os._exit` immediately after fsync of durable intent or durable handler response, respectively. A separate parent read classified both original UUIDs `unknown-no-replay`, with `runtime_state=unknown` and zero recorded replies; attempting a fresh owner for either session refused `JOURNAL_COLLISION` without dispatch or changing the saved journal. These are actual process exits with an injected handler and no backend, **not** archived-session restart recovery or caller-delivery proof. Only test-owned temporary paths were cleaned; the dedicated Colima profile was observed stopped, with no API/SQL, retained run/backup mutation or unknown UUID POST. Focused Warehouse tests passed; full regression is recorded in Local Experiment Milestones. Revert the disposable test only, retaining archived runs, backups and VM. Independent backend identity, historical cleanup and operator/production acceptance remain open. Original Vault direct sync pending outside checkout.

## 2026-10-03: disposable reply-journal termination boundary (offline)

An isolated Unix socketpair with an injected handler now interrupts `_connection` after the exact final reply has been durably appended to the test-owned journal but before `_send`. The saved projection has one verified response and one reply, but still reports `runtime_state=unknown`: neither record attests delivery. Attempting a fresh owner with the same session ID and output refuses `JOURNAL_COLLISION` without another handler call or journal change; the interrupted UUID is never replayed. This complements the previous durable-intent and durable-handler-response fault fixtures; it is not a real process restart, backend observation or archived-session recovery. `make test-local-warehouse-lifecycle`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed. Dedicated Colima was observed stopped; no live API/SQL, retained run/backup modification or cleanup. Revert this test only to roll back; preserve the saved sessions, stopped VM and unknown UUIDs. Original Vault direct sync pending outside checkout.

## 2026-10-03: isolated restart-boundary fixtures and read-only classification

An opt-in `status.py --classify-recovery --request-id <canonical-uuid> <session-id>` reads only a fixed private saved journal and, for a recorded verified/noop response, tries the existing complete-session receipt/operation correlation. It emits `correlated-saved-completion` only when that correlation succeeds and the journal remains unchanged; missing/pending/uncorrelated or contradictory saved evidence remains `unknown-no-replay`. A changed journal refuses output. `runtime_state` remains `unknown` in both classes; neither class authorizes a POST, adopts an archived UUID, attests caller receipt or identifies the backend. The prior CLI modes are unchanged. A new disposable test-owned RPC owner with injected handler exercised termination immediately after durable intent and after durable handler response, before reply recording. The saved results differ (`pending` versus `verified` without a reply), but neither proves delivery or safe replay. This is *simulated* termination, not a real owner restart or backend recovery.

`make test-local-warehouse-lifecycle`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed. Private failed/final focused and regression logs are under `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-034/`. Dedicated Colima read-only status reported stopped; no VM start, Kubernetes/SQL, archived UUID POST, retained run/backup mutation or cleanup. Roll back only the opt-in classifier and isolated tests, retaining saved sessions, evidence and empty socket directories. Real process restart, independent authenticated backend operation identity, historical cleanup and operator/production acceptance remain untested. Direct original Vault sync remains pending outside checkout.

## 2026-10-03: unknown-request recovery boundary for the next local experiment

Current code has a **within-one-owner** UUID journal/replay guarantee (`session_rpc.py`); saved-request/completion readers correlate archived claims after owner exit, but cannot establish whether a caller received a reply or whether a previously pending action reached the backend. A new owner must **not** adopt an old UUID as a fresh operation or re-POST an unknown request just because the saved journal is readable. The stopped dedicated Colima profile was confirmed by read-only status; no owner, SQL or retained session was opened for this planning increment.

Dependency-ready next step: exercise a *new disposable session only* with an injected handler and isolated journal, simulate reply loss and process termination at the durable-intent, handler-return and durable-response boundaries, then implement a read-only recovery classification that distinguishes verified saved completion from pending/unknown without scheduling a backend action. Preserve the old journal, operation files, socket directory and backup on any uncertain result. Before any actual owner-restart recovery is claimed, independently identify the current backend and correlate the target's operation identity with authenticated read-only runtime observations; a matching saved receipt alone is not that proof. An unrecorded/contradictory result remains unknown and blocks replay, cleanup and acceptance. Document the exact disposable scope and rollback before a lab run. This is a design/test plan, **not** completed recovery, historical cleanup or production/operator acceptance. Vault original direct sync remains pending outside this checkout.

## 2026-10-03: invalid decoded kubectl Unicode (offline)

The opt-in read-only metadata stdout reader now converts an unencodable Unicode response (such as a lone surrogate returned by a custom command boundary) into the fixed `RUNTIME_OBSERVATION_UNVERIFIED` refusal instead of raising an uncaught encoding exception. The 65536-byte UTF-8 bound and valid boundary behavior remain. The focused Warehouse suite initially had an unrelated RPC assertion failure (`KeyError: 'result'` in `test_query_same_id_returns_saved_result_and_cross_action_is_not_reused`), then passed on rerun; the first failure is not erased or claimed repaired. `make verify-local-experiment` passed. Private rerun and regression logs: `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-032/warehouse.log` and `verify.log`. Dedicated Colima remained stopped; no live API/SQL, owner operation, unknown-request replay or retained session/backup change. Offline error framing does not authenticate an API, prove atomicity/historical cleanup or operator/production acceptance. Roll back reader/test only, preserving VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: bounded forward-clock interval and metadata bytes (offline)

Two separate read-only safeguards: a forward wall-clock jump beyond the 90-second observation budget now rejects the reported interval, complementing the previous backwards-clock check; and the decoded metadata-only kubectl response is checked against the 65536-byte limit in UTF-8, not just Python characters. Isolated negatives reproduced both acceptances before repair; boundary positives, `make test-local-warehouse-lifecycle`, `make verify-local-experiment`, `make verify-design-context` (89 documents), and `git diff --check` pass afterward. Private logs: `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-031/warehouse-final.log`, `verify-final.log`, `design-final.log`. Dedicated Colima was read-only observed stopped; no live API/SQL, owner action, unknown-request replay or retained session/backup mutation. The command runner already bounds subprocess bytes; this second check covers its decoded response contract. These are offline bounds, not authenticated/atomic API inventory, historical cleanup or operator/production acceptance. Roll back reader/tests only, preserve VM and saved evidence. Direct original Vault handoff sync pending outside checkout.

## 2026-10-03: wall-clock rollback cannot publish an inverted observation (offline)

The opt-in two-round read-only scoped inventory now refuses a wall-clock interval whose end precedes its start, instead of publishing a misleading observation window when the workstation clock steps backwards. A fake clock negative failed before repair, then passed; `make test-local-warehouse-lifecycle` and `make verify-local-experiment` passed afterward. Monotonic deadline and unchanged two-round consistency remain the freshness boundaries. Dedicated Colima remained stopped; no live API/SQL, owner action, unknown-request replay or retained session/backup mutation. This cannot authenticate the API, prove an atomic inventory or historical cleanup, or establish operator/production acceptance. Roll back only the offline reader/test; preserve VM and saved evidence. Direct original Vault handoff sync pending outside checkout.

## 2026-10-03: reverse scoped projection membership (offline)

The opt-in read-only two-round inventory now refuses an owner-selected fixed-name resource absent from its named GET and a service-selected EndpointSlice advertising this session owner absent from the owner-selected list. These complement the existing checks in the other direction; unrelated or differently owned slices still count as scoped presence. Two isolated negatives failed before repair; `make test-local-warehouse-lifecycle` passed after repair. Dedicated Colima was observed stopped; no live API/SQL, owner operation, unknown-request replay or retained data/backup change. Separate API reads can race and do not authenticate the backend, prove atomicity/historical cleanup or establish production/operator acceptance. Roll back only the reader/tests and preserve saved sessions, backups and VM. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: cross-selector membership in archived-session inventory (offline)

The opt-in read-only scoped reader now rejects a fixed-name object advertising the session owner if it is absent from that round's owner-selected list. Independently, an owner-selected EndpointSlice advertising either fixed Service must occur in that round's corresponding service-selected list. Unrelated owner-selected slices still count as scoped presence without requiring membership in those two Service lists. Fake contradictory and matching projections pass focused Warehouse tests. Dedicated Colima was observed stopped; no VM/API/SQL read, owner operation, unknown-request replay, retained data or backup change. These checks reject contradictory projections, **not** transient races, atomic inventory, authenticated API identity, historical cleanup, or production/operator acceptance. Roll back only runtime-status reader/tests, preserving saved sessions and stopped VM. Original Vault direct handoff sync remains pending outside checkout.

## 2026-10-03: bounded projected list count (offline)

The opt-in metadata-only inventory now bounds the decimal list-count text by the fixed per-list limit before converting it to an integer. A 5000-digit fake count reproduced unbounded conversion before repair; an empty complete list still passes. `make test-local-warehouse-lifecycle` passed. The dedicated Colima profile remained stopped; no live template/API/SQL, owner call, unknown-request replay, retained session or backup mutation. This does not authenticate inventory, establish atomic absence or historical cleanup, or prove operator/production acceptance. Revert only this reader/test change and preserve the VM and saved evidence. Original Vault handoff direct sync remains pending outside checkout.

## 2026-10-03: bounded report assembly (offline)

Final milestone regression also passed `make verify-local-experiment`, `make verify-design-context` and `git diff --check`; private logs are under `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-022/`.

The opt-in two-round scoped inventory now checks its deadline again after assembling the report, before handing it to the saved-claim reread. A fake monotonic clock that crossed the deadline between the existing post-snapshot check and report return previously published the stale report; the isolated negative failed before repair and passed afterward, as did the 19 runtime-status tests and `make test-local-warehouse-lifecycle`. This is an offline freshness boundary only; the dedicated Colima profile was observed stopped, with no VM start, API/SQL, owner operation, unknown-request replay, retained session or backup mutation. It does not make separate reads atomic, authenticate a backend or prove historical cleanup, unknown-request recovery or operator/production acceptance. Revert only the scoped reader/test, retaining saved evidence, backups and the stopped VM. Direct original Vault handoff synchronization remains pending outside checkout.

## 2026-10-03: bounded visible list version (offline)

The opt-in metadata-only scoped-list reader now rejects Unicode format/control characters in the projected resource version (including zero-width characters) and bounds its UTF-8 byte length rather than its character count. It still accepts valid opaque versions. A zero-width negative failed before repair; fake malformed/valid cases and `make test-local-warehouse-lifecycle` passed afterward. The dedicated Colima profile was observed stopped; no live API/template rendering, SQL, owner call, unknown-request replay, retained session or backup mutation. Reported metadata does not authenticate the API, make reads atomic, prove historical cleanup or establish production acceptance. Roll back the reader/test only, retaining the stopped VM and saved evidence. Original Vault handoff direct sync pending outside checkout.

## 2026-10-03: scoped-list resource version framing (offline)

The metadata-only list template now projects the reported resource version into the bounded header, and the opt-in runtime-status reader rejects whitespace-only, whitespace-containing, control-character or oversized versions before treating an empty scoped list as absence. Valid opaque versions still pass; malformed/valid fake list cases and `make test-local-warehouse-lifecycle` passed. The dedicated Colima lab remained stopped; the template has not been rendered against a live API, and no owner request, SQL, unknown-request replay or retained backup/session mutation occurred. This only tests local parsing, not authenticated API identity, atomicity, historical cleanup or production acceptance. Roll back the projection and reader tests only; preserve saved evidence and the stopped VM. Direct original Vault sync pending outside checkout.

## 2026-10-03: canonical metadata list row framing (offline)

The opt-in scoped inventory reader now requires LF-only projected list rows rather than accepting CRLF through Python `splitlines()` normalization. Fake CRLF malformed headers, valid LF examples and `make test-local-warehouse-lifecycle` passed. This does not validate a live API; the dedicated Colima profile remained stopped, with no owner call, SQL, unknown-request replay or retained session/backup mutation. Separate reads are not atomic or authenticated, and historical cleanup and operator/production acceptance remain open. Roll back only the local reader/test; retain saved evidence and the stopped VM. Original Vault handoff direct sync remains pending outside checkout.

## 2026-10-03: list metadata requires a resource version (offline)

The opt-in scoped-list metadata-only template projects `INVALID` when `metadata.resourceVersion` is empty, even if the envelope includes metadata and `items=[]`. The bounded reader already rejects `INVALID`; fake owner/endpoint-list rejection and Warehouse focused tests passed. The requirement assumes a Kubernetes API list supplies a nonempty opaque resource version; no numeric format or cross-read equality is inferred. Dedicated Colima was stopped; client-side template rendering against a live API was not possible and the fake/template checks do not validate live behavior. No VM start, API/SQL call, owner operation, unknown-request replay, retained session or backup change. This does not authenticate a backend, make separate reads atomic, prove historical cleanup or establish production acceptance. Revert only the template/test and retain saved sessions and stopped VM. Original Vault handoff sync remains pending outside checkout.

## 2026-10-03: absent list metadata cannot imply scoped absence (offline)

The opt-in metadata-only list projection now marks a nil `.metadata` invalid, even if `.items` is a complete empty array, rather than treating that response as scoped absence. The bounded reader rejects the fixed invalid marker; fake owner/endpoints rejection, `make test-local-warehouse-lifecycle`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed. Final verification log: `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-015/verify-after-warehouse.log` (private/ignored). Client-side template rendering against a live Kubernetes API was not possible with dedicated Colima stopped; the fake/template assertion is not a live observation. No VM start, API/SQL, owner call, unknown-request replay, retained session or backup mutation. This check cannot authenticate a backend, make two reads atomic, prove historical cleanup or establish production acceptance. Roll back only the projection/test, preserving stopped VM and saved sessions. Original Vault handoff direct sync remains pending outside checkout.

## 2026-10-03: absent list items cannot project scoped absence (offline)

The opt-in metadata-only kubectl list projection emits a fixed invalid marker if `.items` has nil type, instead of projecting zero items as a complete empty inventory. The bounded reader already rejects that marker, before reporting scoped absence. Fake invalid/valid list headers and `make test-local-warehouse-lifecycle` passed. Client-side kubectl rendering of a synthetic List could not run while Colima was stopped: kubectl attempted API discovery against the unavailable local endpoint; the failed attempt is not live validation. No VM start, SQL, owner operation, unknown-request replay, retained session/volume or backup mutation. The check cannot authenticate the API or prove atomicity, historical cleanup or production acceptance. Roll back projection/test only; preserve saved sessions and stopped VM. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: malformed named GET is not absence (offline)

The metadata-only named GET template distinguishes a missing resource (empty stdout under `--ignore-not-found`) from a returned object with missing metadata (fixed `INVALID` marker). The latter is rejected before an absence report. Fake missing/malformed/valid cases, client-side kubectl template rendering and the focused Warehouse suite passed. The dedicated Colima profile was observed stopped; no live API/SQL, owner request, unknown-request replay, retained session or backup changes. Client-side rendering does not establish live API behavior or provenance; scoped absence remains two separate reads, not historical cleanup or production acceptance. Roll back reader/test only; retain saved sessions and stopped VM. Original Vault synchronization remains pending outside checkout.

## 2026-10-03: scoped list type/version pairing (offline)

The optional metadata-only reader now accepts only `v1/List` or `discovery.k8s.io/v1/EndpointSliceList` for endpoint selections; the former is emitted by the local kubectl generic printer. It rejects the previously accepted crossed pairs even for empty lists. Fake positive/negative responses and `make test-local-warehouse-lifecycle` passed. Dedicated Colima was observed stopped; no live API/SQL, owner operation, saved-request replay, retained data or backup changes. The reported list envelope cannot authenticate the API, make separate reads atomic, or prove historical cleanup or operator acceptance. Roll back reader/tests only, leaving archived sessions and stopped VM intact. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: scoped list API version projection (offline)

The opt-in read-only scoped inventory now projects list `apiVersion` before interpreting an empty owner-labelled or EndpointSlice list. It accepts Kubernetes `v1/List` and either `v1/List` or `discovery.k8s.io/v1/EndpointSliceList` for endpoints; an absent/wrong version fails closed instead of publishing scoped absence. Fake positive/negative projections and `make test-local-warehouse-lifecycle`, followed by `make verify-local-experiment`, passed. Dedicated Colima remained stopped; no live API/SQL, owner request, unknown-request replay, retained session, backup or volume changes. API-version syntax does not authenticate the server or prove historical cleanup, atomicity or production acceptance. Roll back reader/tests only; preserve saved evidence and VM. Original Vault handoff direct sync pending outside checkout.

## 2026-10-03: projected metadata names have bounded label syntax (offline)

The optional read-only runtime-status reader now rejects projected object names and service-name labels beginning or ending in `-` instead of treating these malformed values as stable present resources. Existing length and character bounds remain; valid generated ReplicaSet and EndpointSlice names continue to pass. Isolated fake-name/service-label negatives and healthy inventory plus `make test-local-warehouse-lifecycle` pass. Dedicated Colima remained stopped; no live API/SQL, owner request, unknown-request replay, archived session, backup or volume change. A syntactically plausible projection does not authenticate the cluster or make its two rounds atomic; historical cleanup and production acceptance remain unverified. Revert only this reader/test change and preserve saved evidence and stopped VM. Original Vault direct handoff sync pending outside checkout.

## 2026-10-03: remaining-item metadata cannot imply scoped absence (offline)

The metadata-only kubectl list projection now marks `TRUNCATED` when `remainingItemCount` is nonzero, even with an empty continuation token. The existing reader already refuses `TRUNCATED` without publishing partial JSON. This conservative consistency guard applies to the owner-label and EndpointSlice lists, not fixed-name GETs. The focused fake reader test checks that the projection includes the field and that truncated output fails; no live API or SQL read was made. Dedicated Colima remained stopped, with no owner call, saved-request replay, retained run, backup or volume mutation. The new guard does not authenticate a backend, make the two rounds atomic, or prove historical cleanup or production acceptance. Roll back only the projection/test and retain saved runs and VM; direct original Vault handoff sync remains pending outside this checkout.

## 2026-10-03: scoped projections agree on labels (offline)

The optional read-only runtime-status reader now requires the same kind/name/UID to carry the same observed owner and service-name labels across fixed-name, owner-selected and EndpointSlice projections in each round. It also includes these labels for *all* selected objects, not only fixed-name objects, in the two-round hash. Fake same-UID/different-owner and stable-UID/changing-child-service-label regressions fail closed; stable matching metadata still passes. `make test-local-warehouse-lifecycle` passed. The dedicated Colima profile was read-only observed stopped; no live API/SQL, owner request, saved-request replay, retained run, volume or backup mutation. This is not an atomic observation, independently authenticated backend, historical cleanup, unknown-request recovery or production acceptance. Roll back only the reader/tests, retaining saved sessions and backups. Original Vault handoff direct sync remains pending outside this checkout.

## 2026-10-03: fixed-name labels participate in both inventory rounds (offline)

The read-only scoped runtime-status reader now includes the observed owner and service-name labels for each fixed-name resource in its two-round snapshot hash. A fixed-name object whose UID stays constant but whose owner label changes between rounds previously looked stable if it was not selected by the owner-label listing; a fake stable-positive and relabel-negative now pass. `make test-local-warehouse-lifecycle`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed. Private regression log: `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-005/verify-final.log` (SHA256 `789e1067848ad3f868fa8fdf07c9b8c65eb033d0eb29897e8af5becb8c1dff64`). Dedicated Colima remained stopped: no live API/SQL, owner request, unknown-request replay, archived session or backup change. This is bounded metadata consistency, **not** an atomic snapshot, historical cleanup proof, authenticated backend observation or production acceptance. Revert only the reader/test changes, preserving retained runs/VM and unknown requests. Original Vault handoff direct synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: one UID cannot name two scoped resources (offline)

The opt-in read-only runtime-status reader now rejects a UID assigned to different kind/name pairs within one inventory round, across fixed-name, owner-labelled and EndpointSlice projections. Repeated views of the *same* kind/name/UID remain valid. Stable but contradictory fake inventory previously produced `scoped-resources-present`; isolated negative tests now fail closed. First focused suite exposed a misplaced old test assertion (`NameError`), corrected without changing the reader; final `make test-local-warehouse-lifecycle`, `make verify-local-experiment` and `git diff --check` passed. Only fake API metadata were read; dedicated Colima remained stopped, with no live API/SQL read, saved-request replay, archived session or backup change. This is not atomic snapshot evidence, independent backend authenticity, historical cleanup proof, unknown-request recovery or operator/production acceptance. Roll back reader/test changes only; preserve stopped VM, archived sessions and backups. Original Vault handoff direct sync pending outside checkout.

## 2026-10-03: saved-claim reread shares the observation deadline

The opt-in metadata-only runtime-status command previously enforced its 90-second deadline after two inventory rounds but not after the saved journal/completion reread before stdout. The command now passes one explicit deadline into `observe` and checks it again after saved-claim correlation; a delayed reread cannot publish an aged scoped-absence/presence report. The isolated late-reread negative test failed before repair, then late and timely tests and `make test-local-warehouse-lifecycle` passed. No VM start, live API/SQL read, owner request or unknown-request replay, retained run or backup mutation. This bounded freshness check is not an atomic snapshot, independent VM authenticity, historical cleanup proof or production acceptance. Roll back only the reader/test change, retaining archived sessions and backups; original Vault handoff direct sync remains pending outside this noninteractive checkout.

## 2026-10-03: no late scoped inventory result

The opt-in read-only runtime-status reader now checks its overall deadline after the second inventory snapshot, before reporting scoped presence or absence. A fake two-round result that finished after the deadline was previously accepted; the new negative test failed before the fix and passed afterward. `make test-local-warehouse-lifecycle` passed after repair. This is an offline deadline boundary, not an atomic observation or independent evidence of historical cleanup, current SQL/VM authenticity or unknown-request recovery. Dedicated Colima was observed stopped, with no start, API/SQL request, replay or retained run/backup mutation. Original Vault handoff synchronization remains pending outside this noninteractive checkout. Roll back only the reader/test change; preserve stored sessions, empty socket directories and backups, and do not replay unknown requests.

## 2026-10-03: scoped read-only inventory cross-projection identity

全回帰`make verify-local-experiment`・design-context・diff checkも成功。private log `tmp/local-experiment/autonomous-runner/run-3au9fp_4/attempt-001/verify-warehouse.log` SHA256 `8b518e0c5f100ff4cd6a9252fcc641af6dbf0a9057aad211689af18905ecfc6d`。

保存完了session用のopt-in runtime-statusで、同じkind/nameが固定名・owner label・EndpointSliceの複数GET投影に出る際、単一round内でUIDが食い違えば保守的に拒否。各list内の同名別UIDも拒否し、後続の2周同一性検査とbounded metadata-only出力を維持する。隔離fakeの一致正例/不一致負例とWarehouse focusedが成功。VMは停止したまま、実API/owner/SQL/保持run/backupに接続・変更なし。現在のcluster/cleanupや同時変更への原子的保証、owner再起動後の未知要求回復、独立認証・本番受入は依然未達。Vault原本への非対話直接同期はcheckout外権限がないため保留。戻す場合はreaderとfake testの局所差分のみ戻し、保存資料と空socket directoryを残して未知要求は再POSTしない。

## 2026-10-02: saved-requestのopt-in完了相関 — owner終了後のread-only照会

元UUIDの応答を失った場合、既存Go CLI `warehouse saved-request`のjournal-only照会と別に、明示`--with-saved-completion`で保存sessionの最終receipt・全operation fileまで相関させる。子processは固定checkoutの既存`status.py`、空environment、固定session/UUIDを一回だけ起動。Go側は出力の追加3 fieldの型/固定値・hash/operation数、元UUIDの存在/成功、`runtime_state=unknown`を検査し、CLI child失敗・欠損UUID・pending/unknown・改変・不正shape・中断では部分JSONなし。flagなし旧journal-onlyのJSON/欠損照会は維持。実際のowner再起動・RPC POST/SQL/cleanupは行わず、**同一owner由来の保存物の整合性**だけを提示する。

停止中の専用Colima下で、既存閉鎖済み2 sessionの保存6要求をGo CLIからUUID毎にread-only相関し、別の既存Python completion readerによるsession全体のhash/件数・元journalのUUID/action/operation hashと一致。存在しないUUIDは非0・stdout空。保持2 session計10 filesの集合/SHA/inode/mode/mtime/sizeは前後同一。private記録 `tmp/local-experiment/warehouse-completed-request-checks/20261002/`。これはownerから元callerへのreply到達、保存producer真正性、歴史的cleanupやSQL実行、現backend/全namespaceを独立確認せず、owner終了後の未知要求を安全に再POSTできる条件にもならない。WP `local-partial`。止めるにはopt-in flagを使用せず、保持session/socket directory/backupを削除・再作成しない。

Go全package test PASS（log SHA256 `c234dd9c882001898e0bee7f85636322912742f6b278a59d59665bc753e21ca9`）、`go vet ./...` PASS。Warehouse focused5 suites/191件PASS（log SHA256 `9967a32a3810f7daf1456fc8e8ceebabc1c7f975ecf5bda0ad8ad7530ef8b1ce`）。最終全回帰121 suites/3839実行＝3831 PASS＋8 skip、91.25秒、log SHA256 `3b6671cb2d78b050e196021c3ead4a3a94b5047dd237743de182dbcc82338b9d`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-10-02: partial HTTP timeout test accepts an already closed peer

Lineage live再観測後の全回帰がWarehouse `test_slow_partial_request_has_absolute_deadline`で1件ERROR。test threadは`READ_TIMEOUT_SECONDS=0.15`に対して最初の`POST `断片後0.09秒sleepし、2断片目`sendall`より先にownerの絶対deadlineが到来して`BrokenPipeError`。本番owner側`_read_request`は元からmonotonic deadlineを使い、不完全要求はhandlerへ渡さず400を試みて接続終了する。testだけを修正し、2断片目が既に閉じたpeerへ送れない場合と、400応答を読める場合の両方で不完全要求がhandlerへ届かず有界終了することを要求。client test threadの過剰な起床遅延をserver応答遅延と誤認しない時間上限を維持する。session owner/RPC・保存runの機能契約や実保持backendは変更しない。旧全回帰失敗logは`tmp/local-experiment/lineage-live-recheck/20261002/verify-local-experiment-initial-failed.log`に残し、通過扱いへ書換えない。実Unix focusedで当該負例を10回、Warehouse focused 5 suites/191件PASS（SHA256 `8ae3c43d089108f58bf81d54dd7529bd49be74ff243d5f757c0c77f133ab97d7`）。テストを戻す場合は差分だけ以前のrevisionへ戻し、保持session/backupやsocket directoryを削除しない。

最終全回帰121 suites/3839実行＝3831 PASS＋8 skip、90.39秒、log SHA256 `158330c6b663b60b9c48bfa43878ef8421efda2f17f6636993f2c1c042e18599`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-10-02: opt-in scoped live Kubernetes inventory — bounded live check

保存`correlated-saved-claim`を現在backendの証明へ昇格させないため、完了済みsession向けread-only `make local-warehouse-session-runtime-status WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>`を追加。固定Colima context/loopback/single ARM64 k3s事前確認後、token由来の11個の明示資源名、同token owner labelの8 kind、XS/S Service由来EndpointSliceをそれぞれ2回読む。stdoutはkind/name/namespace/UID/必要なlabelのみのmetadata投影で、Secret dataやPod spec/Endpoint addressは出力しない。応答欠損、pagination未完了、異常なidentity/重複、2回の差、保存claimの前後差を固定code/部分JSONなしで拒否する。固定名を異なるownerが占めても`scoped-resources-present`、2回とも不在のときだけ`scoped-absence-observed`。backend query stateは常に`unknown`。

隔離fakeの不在/存在/EndpointSlice/owner競合/UID置換/空・欠損list/不正contextと、実kubectlの`--dry-run=client`によるmetadata-only ConfigMap・Secret投影及びGo list template描画を確認。初回templateはlabelがないと失敗したため修正し、無label固定名も存在と判定。VM停止中のarchived1件への実診断は固定失敗＋stdout空。専用Colima `mac-studio-solo`を一時起動し、固定context preflight・保存completion相関は通過。しかし実`kubectl`がEndpointSlice一覧を`EndpointSliceList`ではなく`List`で表示したため、最初の2件は固定失敗/部分JSONなしで記録。item kindを`EndpointSlice`へ束縛したまま外側の両形式を受け入れて再読したところ、完了済み2件とも11固定名0・owner label一覧0・両ServiceのEndpointSlice0を**各2周**確認し、`scoped-absence-observed`/`backend_query_state=unknown`。別の直接kubectl GETで各tokenにlabelのあるDeployment/Pod/ReplicaSetを1回ずつ照会して0件。これは同じAPIに対する別呼出しでありVM独立認証ではない。

検証後VMを通常停止しStoppedへ復帰。profile config SHAと保存済み2 session計10ファイルの集合・SHA/inode/mode/mtime/sizeが起動前後で一致。起動ログにはloopback TCP53 forwarding、停止ログには`/var/lib/cni/` busyと保持PodのPVC/secret volume unmount出力があり、保持PVC/backupの内容不変は独立照合していない。owner/SQL/RPC POST・保持volume scan・VM全体reset/保持backup削除は行わず、unknown要求を再POSTしていない。private記録は`tmp/local-experiment/warehouse-runtime-status-checks/20261002/`。新しい診断を使わない復旧はVMを追加起動せずopt-in readerのみ停止し、保持run・空socket dir・backupを維持する。

2回の読みは原子的snapshotでもVM真正性の独立認証でもなく、保存claimの真正性、historical cleanupの実施時点、全namespace残留、現backend readiness/SQLの独立証明は未達。同UID元private dir改変・owner再起動後未知要求回復も未達。停止後の現在clusterをさらに継続観測した結果でもない。`local-partial`継続。戻すにはopt-in readerを使わずコードを以前のローカルrevisionへ戻す。archived run・残置socket directory・backupは削除せず、unknown要求を再POSTしない。

最終Warehouse focused5 suites/191件PASS、log SHA256 `e51bfaa629f3b6a1243a6f812f2bc1b4b504861eaf1432999d35db78d6709306`。全回帰121 suites/3835実行＝3827 PASS＋8 skip、90.28秒、log SHA256 `5b90dff9662ac672c8ad04f44b4505e9e0cc1b6f3cc69e5ba963d47e3a629c43`。design-context/secret scan・diff check PASS。

## 2026-10-02: archived status root opened component by component — offline

既存のread-only `status.py`と保存completion照会は、固定runs rootの祖先pathを`lstat`で検査してからrootを絶対pathで再openした。同UID actorがその間に親をsymlinkへ差し替える隔離temp負例で、旧statusは模擬別treeの`pending` journalを正当なsessionの保存状態として投影し、旧completion `_saved_files`も別treeのreceiptを読み出した。両入口を共通の`_open_root`へ変更。`/`から全componentを`O_DIRECTORY|O_NOFOLLOW`のfd相対で開き、private rootのuid/mode・現pathとのinode照合を行う。読取を終える直前にもroot pathの全祖先/identityを再照合し、open直後に親が退避された場合も拒否する。隔離負例は修正後いずれも別treeを読まず固定失敗。正当な保存照会のJSON契約は変更しない。

既存完了済みsession 2件をread-onlyで新status/completionから照会し、保存上のoperation数2件/4件、`correlated-saved-claim`と`runtime_state=unknown`を確認。照会対象のjournal/receipt/operation filesは前後でSHA256/inode/mode/mtime/size・file集合が一致。session owner、SQL、cluster/Colima、保持backup/credentialには接続・書込みせず、未知要求を再POSTしていない。private記録は`tmp/local-experiment/warehouse-status-root-checks/20261002/`。保存物の内部相関はproducer真正性、現在backend/最終cleanupの独立確認ではない。元dir内部を改変可能な同UID actor、owner再起動後の未知要求回復、保持データ受入はなお未達。WP `local-partial`。取り消す場合はoffline readerのroot open差分のみ以前のローカルrevisionへ戻し、保存run/空socket dir/backupを削除しない。

focused5 suites/182件PASS（log SHA256 `a3ea69ccee12146b6b2b550133827a341007306208838d4952bdf28dd96ad056`）、最終全回帰121 suites/3826実行＝3818 PASS＋8 skip、91.63秒、log SHA256 `62f1861bfbc2bca938e6d74e41b9fee806b6503301370fdc7b0ba48ae337d290`。design-context/secret scan・diff check PASS、専用Colima Stopped。試験に使ったシステムPython3.9では既存`Path.hardlink_to`試験とtests間importが失敗したため、Make指定の専用venv Pythonへ切り替え、全focused/全回帰で確認。機能失敗として隠さない。

| Field | Value |
|---|---|
| WP ID | WP-L3-VW-002 |
| Level | L3 |
| Workstream | Warehouse |
| Depends-on | WP-L3-VW-001, WP-L0-OBS-001 |

## 2026-09-29: create 0600 socket without post-bind chmod — offline

前増分のfd固定子processが作った`control.sock`を、ownerが一度statしてからfd相対`chmod`していた。同UID actorが元directory内のentryをこの間に別のsocketへ替えると、別socketのmodeを変更し得る。Darwin隔離tempで子processだけの`umask(0177)`によるUnix socket作成modeが`0600`であることを確認し、子のumaskを`0177`へ変更。ownerは`chmod`せず、元fd相対で`0600`/type/uid/inodeを2回照合してpath identityを確認する。隔離test-owned別socket（`0644`）へ2回のstat間でentryを置換した負例では起動を拒否し、別socketは`0644`のまま。symlink entryと親path差し替えの負例も拒否、実Unix owner/Go CLI query integrationを維持。private記録は`tmp/local-experiment/warehouse-bind-create-mode-checks/20260929/`。Colima/SQL・保持session/backup変更なし、WP `local-partial`。

作成時modeの限定確認はDarwinの隔離tempと実Unix testの結果であり、任意OS/filesystemでの許可を推測しない。初期modeが`0600`でなければ修復chmodを行わず起動拒否。元directory内entryの2回の照合**後**の同UID置換は排除できず、listener到達・cleanupが失われ得る。拒否されたsocket/dirを自動削除しない。owner再起動後未知要求の回復、現在backend/最終cleanupの独立確認、保存物独立認証は未達。戻す場合は新owner起動を止め、保存runを維持してコードのみ戻し、unknown要求を再POSTしない。

Warehouse focused5 suites/179件PASS（log SHA256 `85302a6c6373a10db8b2845fbdbccdcec4ba80fdda2e448c4095fc6d0ed2f160`）。socket swap負例では起動失敗時の保守的closeが余分なentryを検知し`SOCKET_DIRECTORY_NOT_EMPTY`で拒否する。別socket modeは不変で、自動削除なし。

全回帰121 suites/3820実行＝3812PASS＋8skip、94.22秒、log SHA256 `467c91a8e4ebacd726a7d7548adf28e44bf00a88611bed389f5586a502c729aa`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-09-29: socket bind anchored by an isolated child — offline

Darwin Unix `bind`はpathname APIで親fdを直接渡せないため、ownerのcwdは変えず、短命の`-I` Python子processに開いたprivate directory fdとprivate socketpairだけを渡す。子だけが`fchdir(fd)`して相対名`control.sock`をbind、listener fdを`SCM_RIGHTS`でownerへ返す。子終了・fd受領後に元directory fd相対でsocket type/owner/mode/inodeを照合してchmodし、元path identityを再確認する。子失敗・timeout・fd受領失敗は固定codeで拒否し、別pathの自動削除や再bindはしない。bind直前に元directory pathを別treeへのsymlinkへ差し替えた隔離試験では、子は開いた元directoryにのみsocketを作り、別treeは空のまま、ownerはpath不一致で拒否。元directory/socketは拒否時に自動削除せず保持。実Unix owner/Go CLI query統合も継続。private記録は`tmp/local-experiment/warehouse-fd-child-bind-checks/20260929/`。Colima/SQL・実保持session/backupには接続せず、WP `local-partial`。

この境界は親path差し替えによる**別treeへのbind作成**を防ぐが、同UID actorが開いた元directory内の`control.sock` entryをbind後に改変する場合、保存物独立認証、owner再起動後の未知要求回復、現backend/最終cleanupの独立確認は未達。空private directoryの残置は継続し、socketや保持runを一括削除しない。復旧時は新ownerの起動を止め、保存runを維持し、unknown要求は再POSTしない。

Warehouse focused5 suites/178件PASS、最終全回帰121 suites/3819実行＝3811PASS＋8skip、92.23秒、log SHA256 `fab2be92e1909bb913f549ea3a89bd681a56c3240528e9688b442c3624de0b9b`。初回のsocketpair context manager誤用でfocused 30件失敗、修正後通過。親差し替え負例の期待codeを実際の`UNSAFE_PATH`へ修正し再実行。最終fd受領例外処理の補強後にfocused・全回帰を再実行。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-09-28 descriptor-bound socket permission update — offline

開始時の`bind`後に絶対pathを辿って`os.chmod(control.sock, 0600)`していた。隔離tempでsocketを模擬別fileへのsymlinkに替えると、旧処理は別fileを`0644→0600`に変更（その後拒否）。親socket directoryのpathを模擬別socket treeへのsymlinkへ替える負例も対象にした。directoryをno-followで開きowner/mode/inodeを確認し、`bind`後のsocket type/ownerを同fd相対で照合、`follow_symlinks=False`のfd相対chmod後に同一inode/0600と現path identityを再確認する。socket entryまたは親をchmod直前に替える隔離試験では模擬先modeは不変、開始は拒否。実Unix owner/Go query integrationと通常socket closeは維持。実保持session/backup・Colima/SQLには触れず、WP local-partial。

`socket.bind`自体はpathname APIでfd相対bindに対応せず、Darwinで隔離tempの`/dev/fd/<dirfd>/control.sock` bindは`ENOENT`。bind直前に親pathを差し替える同UID actorにより別treeへsocketが**作られない**ことまでは保証しない。process-global `chdir`による回避はownerの並行処理に影響するため採用しない。検査とchmodの間に同じ元dir内entryを改変できる同UID actorも排除しない。失敗時の拒否path/別actor所有物は自動削除しない。復旧は新ownerを止め、保存runを残しコードだけ以前のrevisionへ戻す。unknown要求は再POSTしない。private検証は`tmp/local-experiment/warehouse-socket-startup-permissions-checks/20260928/`。

Warehouse focused145件PASS、全回帰121 suites/3817実行＝3809PASS＋8skip、90.081秒、log SHA256 `58e34c36b0fd6cb1172581a762d8f6097c5fccdbf8f1832ff94fdfe1ec3b60ec`。design-context/secret scan・diff check PASS、専用Colima Stopped。chmod前symlink負例は修正前に模擬file modeを変更して1件失敗、修正後の2負例/実Unix socket integrationと全回帰は通過。

## 2026-09-28 safe empty socket directory retention — offline

前増分はsocket `unlink`を元directory fdへ固定したが、その後の空directory `rmdir`はpathで実行していた。隔離tempで元の空dirを退避して同pathに別の空dirを置くと、旧`close()`は模擬別dirを削除。POSIXのpath-based `rmdir`には確認済みinodeとの原子的な一致条件がないため、ownerはsocketを閉じて相対unlinkした後、private `shirokuma-wl-<session-id>` directoryを**自動削除せず残す**。close中に開いた元dirで余分なentryの不在を確認し、存在すれば削除せず`SOCKET_DIRECTORY_NOT_EMPTY`で拒否。通常の新UUID sessionは従来どおり動作し、同IDの再利用は既存dir衝突で拒否。実Unix owner/Go query integrationを2経路で確認し、テストが作成した固定IDの空dirのみowner/inode/mode/空内容を照合してテスト側で除去。実保持run/backup、Colima/SQLには接続せずWP local-partial。

残置は1 sessionあたり空directoryのinode/entryを消費し、OSの一時領域管理に依存する。host backup/exportへの包含は未確認で、保持backupには触れない。保持するrootや別actorのdirを自動cleanupせず、operatorが個別のidentityと使用状況を確認する。既存保存`cleanup.verified`はKubernetes等のowner cleanup claimであり、host上の全path不在や残置dirの独立確認を意味しない。同UID actorによる元dir内socketのcheck/unlink間改変、owner再起動後の未知request回復と現在backend/最終cleanup独立証明は未達。復旧時は新ownerを止め、保存runを残してコードだけ以前のrevisionへ戻し、unknown要求は再POSTしない。private検証は`tmp/local-experiment/warehouse-socket-directory-retention-checks/20260928/`。

Warehouse focused143件PASS、最終全回帰121 suites/3815実行＝3807PASS＋8skip、89.155秒、log SHA256 `b0522bf0eef046f6f9fc87b8f88cf636099fc8b4f6a51e8945f2cdbe5af51e88`。design-context/secret scan・diff check PASS、専用Colima Stopped。初回integrationは旧「dir不存在」期待で失敗し、固定IDの新規空dirだけ検証して除去した後に新契約へ合わせて通過。余分なentry拒否・有界fd走査追加後に全回帰を再実行した。

## 2026-09-28 descriptor-bound socket unlink on session close — offline

終了処理の`close()`はsocket inode確認後にpathを再解決して`control.sock`をunlinkしていた。隔離tempで確認直後に所有socket directoryを移動し、現pathを模擬別socket treeへのsymlinkへ差し替えると、修正前は**別socketを削除**した（元socketは残存、後続rmdirは失敗）。新実装はsocket directoryをno-followで開き、fdのowner/mode/inodeとfd相対socketのtype/owner/mode/inodeを照合し、同fd相対でunlinkする。open前差し替えはunlink前に拒否、open後差し替えは元socketだけを除去して現pathの不一致を拒否し、模擬別socketは残る。通常の終了は従来どおりsocketと空directoryを除去。差し替えで拒否された元dirや別actorのsocketは自動削除しない。終了時の空directory `rmdir`には同UID actorによる最後のpath差し替え競合がなお残り、元dir内socketのcheck/unlink間の同UID改変も完全排除しない。保持run/backup・Colima/SQLは変更せず、owner再起動後の要求回復・現在backend/cleanupの独立証明ではない。WP local-partial。private検証は`tmp/local-experiment/warehouse-socket-close-fd-checks/20260928/`。

復旧時は新session起動を止め、保存runを保持しコードのみ以前のローカルrevisionへ戻す。残った所有socket directoryは自動で消さず、個別にidentityと使用状況を確認する。未知requestは再POSTしない。

Warehouse focused141件PASS、全回帰121 suites/3813実行＝3805PASS＋8skip、89.461秒、log SHA256 `8c0bb875073836ca6bc2658bbd5644231337dc79da1b43e15507c8d0f37f6720`。design-context/secret scanとdiff check PASS。修正前の負例は模擬別socketの削除と元socket残存、隔離testのteardown失敗を記録。修正中のopen後試験では元socket削除の期待値を旧値として1件失敗し、期待値修正後にfocused・全回帰通過。専用Colima Stopped。

## 2026-09-28 descriptor-bound session journal writes — offline

短命ownerの`SessionRPC._persist()`はjournal directoryのpath identity確認後、path指定の`mkstemp`/`replace`/fsyncを再実行していた。同UID actorが確認と書込みの間にjournal親pathを別treeへ差し替える境界を、隔離tempのopen前/直後の差し替え試験で固定した。新実装はdirectoryを`O_DIRECTORY|O_NOFOLLOW`で開き、owner/mode/inodeを照合し、`journal.json`既存fileのno-follow metadata、exclusive temp作成、atomic replace、directory fsync、temp cleanupを同fd相対に限定。成功replace後はtempを再削除せず、失敗時も作成した同一inodeに限ってcleanup（別actorが置換したtempは残す）。open前差し替えは保存前に拒否、open後差し替えは元directoryへだけ書き現path identity不一致で拒否、模擬保持先は空。元directoryや差し替えたpathは自動削除しない。これは同UID actorによる元dir内の改変、保存物の独立認証、owner再起動後の未知request回復・現在backend/最終cleanupの確認を証明しない。実archived session/backup・Colima/SQLには触れずWP local-partial。private記録は`tmp/local-experiment/warehouse-journal-fd-checks/20260928/`。

復旧は新しいsession ownerの起動を止めたうえでコードだけを以前のローカルrevisionへ戻し、既存のunknown requestを再POSTせず保存runをそのまま残す。fd失敗時に残る元dir/別actorのtempも自動削除せずoperatorが個別確認する。

Warehouse focused139件PASS、全回帰121 suites/3811実行＝3803PASS＋8skip、89.685秒、log SHA256 `5c73e804e69dca32c85856ba2d51e9b924d58e6924c41374158466c6ff62c472`。design-context/secret scanとdiff check PASS。最初の負例はopen後の正当な`UNSAFE_PATH`拒否を旧期待codeにして1件失敗し、期待を修正して再実行。最後の置換temp負例追加後にも全回帰を再実行した。専用Colima Stopped。

## 2026-09-27 closed session recovery guard — retained-run protection

従来の汎用`local-warehouse-lifecycle-recover`は、保存済みXS sessionが`closed/passed`でもlockを開いて`recovery.json`を書き得た。既存完了sessionのread-only receiptをVM/cluster check・lock・回収より前に確認し、`RECOVERY_CLOSED_SESSION`で拒否。lock取得後にも再読して状態変化を拒否する。汎用runの従来回収・失敗runのretry経路は保持。Make recipeのtokenをシェル補間から環境変数渡しへ変更し、PythonでID検証する。実archived2 sessionはMakeから非0/固定codeで拒否され、保持ファイル集合・SHA/inode/modeは不変。不正なtokenを介したシェルcommandも実行しない。初回smokeはMakeが失敗時に出す通常stderrを誤って空と期待し検証側が失敗、期待値を修正して再実行成功。専用Colimaは停止のまま、実cluster回収/バックアップ復元/現backend確認は未実施。保存receiptの`passed/closed`も独立した現cleanup証明ではなく、owner再起動後のrequest再POST/回復は未実装。WP local-partial。private証拠は`tmp/local-experiment/warehouse-recover-closed-guard-checks/20260927/`。

warehouse focused5 suites/167件PASS、全回帰121 suites/3798実行＝3790PASS＋8skip、87.669秒、log SHA256 `da220f2e37f331de36e43be5d23acac6054273beaade1e8dd6d231730a3949e8`。design-context/secret scan PASS、専用Colima Stopped。保持archived2 sessionはlock/recovery file追加なし。

## 2026-09-27 saved completion requires exact final replies — offline

`correlated-saved-claim`が同UUID・別actionの拒否replyや最終reply欠損を含むjournalでも、receipt件数を合わせれば通ることを修正前に再現。opt-in completion照合に限り、全replyが元requestの完全一致を要し、全operationに保存responseそのものの**最終replyが1件以上**あることを確認する。正当な同requestの処理中`unknown`は許可。競合・最終reply欠損は`SAVED_COMPLETION_UNVERIFIED`、CLI stdoutなし。journal-only statusは拒否された競合をなお表示可能で、旧入口は変更しない。実archived2 sessionのcompletionは通過し、元journal SHA不変。これは同じownerの保存物の整合性のみで、callerへのreply到達、実backend/cleanup/監査の独立証明ではない。owner/SQL/Colima/保持run/backup変更なし、WP local-partial。private証拠は`tmp/local-experiment/warehouse-completion-reply-checks/20260927/`。

warehouse focused5 suites/165件PASS、全回帰121 suites/3796実行＝3788PASS＋8skip、87.429秒、log SHA256 `5cccc7b5774c123ec1b97cbc2f9b49ebc14f35db208fe81c100883f1a6a1ae3d`。design-context/secret scan PASS、archived2 sessionのcompletion JSONは旧出力とbyte一致、元journal SHA不変、専用Colima Stopped。

## 2026-09-27 archived RPC negative-reply correlation — offline

保存statusが成功replyは元request/保存応答と相関していた一方、`unknown/busy/failed` replyは同UUIDだけで受け入れる隙間を修正。元requestと完全一致した**保存responseそのもの**、または実ownerが処理中/同ID別action競合時に出す**operation/receiptなしの`unknown`**だけを許す。元actionと異なるreplyがoperation ID/hashを主張する場合や、保存結果にない`busy`を捏造した場合は`JOURNAL_UNVERIFIED`で部分JSONなし。修正前に不正3種が誤受理されることを再現し、修正後拒否。実Unix ownerの同ID競合・query replay、正当な処理中replyを確認。archived2 sessionの旧status JSONはbyte不変、保存完了claimも通過、元journal SHA不変。保存物は同一owner由来であり、独立実行・監査配送・現backend/cleanupは証明しない。owner/SQL/Colima/保持backupは変更せず、WP local-partial。private証拠は`tmp/local-experiment/warehouse-journal-reply-contract-checks/20260927/`。

warehouse focused5 suites/164件PASS、全回帰121 suites/3795実行＝3787PASS＋8skip、87.473秒、log SHA256 `f463db4631dada04df4ac6b9292743a6a3b04b8f6147a6ded70c48947abd6421`。design-context/secret scan PASS、専用Colima Stopped、archived2 sessionの元journal SHA不変。

## 2026-09-27 lost-reply guidance across CLI paths — offline

`chill/wake`の通信失敗時に旧「同じIDでもう一度確認」としか示さない誤解を修正。`chill/wake`と`warehouse query`のいずれも元session/UUIDを保持し、固定`warehouse saved-request --session ... --request-id ...`の**read-only保存照会**を先に案内する。同じUUIDのRPC再送は**同じownerが生存中（有界replay graceを含む）に限る**と明記し、owner退出後/別sessionへの再送を禁じる。保存上のpending/不在を「未実行」や再POST許可に変換しない。自動再送、live owner判定、実backend/cleanup確認、保持run変更なし。既存transport・owner・保存statusの実行契約は不変。local-partial。private検証は`tmp/local-experiment/warehouse-lost-reply-guidance-checks/20260927/`。

明示UUIDと自動生成UUIDの両方でRPC failure時に元session/UUIDが診断に残り、追加呼出し0・部分stdoutなしを確認。`gofmt`、`go test ./...`、`go vet ./...` PASS。全回帰121 suites/3794実行＝3786PASS＋8skip、90.383秒、log SHA256 `c82d2efd24e8becbe18cab532e84774fdfbd755c72b8db977d693dbb3a9d1053`。design-context/secret scan PASS、Colima Stopped。

## 2026-09-27 archived request lookup in Go CLI — offline

`shirokuma warehouse saved-request --session <session-id> --request-id <original-uuid> --profile local-experiment`を追加。既存live query/transitionとは別コマンドで、固定checkoutの既存保存status scriptを専用Pythonで1回だけ起動。session/UUID/profileの事前検証、固定子プロセス引数・空環境、上限付き1行JSONのsession/UUID・`runtime_state=unknown`検査、失敗時の部分JSON抑止を行う。任意socket/SQL/owner回復/scaleの入口は追加せず、旧Make/Python・live queryの契約を維持。archived2 sessionの元UUIDをGo CLIからread-only確認し、保存記録上のquery/wakeとruntime unknownを確認。欠損IDは`not-recorded-in-snapshot`で未実行証明ではない。元保持journal/backup変更なし、Colima起動なし、owner再起動後の要求回復・現在のbackend/cleanup独立確認は未達。取り消す場合は新CLIを使用せず、保持物の復元・移行不要。private証拠は`tmp/local-experiment/warehouse-saved-request-cli-checks/20260927/`。WP local-partial。

`gofmt`、`go test ./...`、`go vet ./...` PASS。Go CLIで不在UUIDはnullの保存状態、無効UUIDはstdoutなしで失敗。最終全回帰121 suites/3794実行＝3786PASS＋8skip、88.396秒、log SHA256 `7e6250d2889d5bd20b99900fb63123b50a34dfa0569d428863695f4e98f4a350`。design-context/secret scan PASS。変更前後の保持journal SHAは不変、専用ColimaはStopped。

## 2026-09-27 archived request-ID lookup without replay — offline

紛失したRPC応答の**元request UUID**を1件だけ照会する`make local-warehouse-session-request-status WAREHOUSE_LIFECYCLE_RUN_TOKEN=... WAREHOUSE_LIFECYCLE_REQUEST_ID=...`を追加。既存固定private journal readerの保存値にexact UUIDで束縛し、記録上のaction/result、operation IDとreceipt相関hash（ある場合）を投影する。`pending/unknown`を成功にしない。見つからないIDは`not-recorded-in-snapshot`＋null値であり、実際に要求が**未実行だったとは証明しない**。UUID不正は読取前に拒否し、Make引数をrecipeへ展開せず環境変数経由。既存all-entries status JSON bytesを維持し、ownerへのRPC/SQL再送や新たなGo CLI child実行は追加しない。

実保存2sessionの元UUIDでquery/wakeの保存結果を個別照合し、存在しないcanonical UUIDはnot-recordedと確認。元journalの内容SHA不変、現warehouseは常にunknown。前回のopt-in完了claimはsession全体の保存相関であり、欠損requestの未実行証明にはならない。Colima停止、保持run/backup変更なし、owner再起動後のlive照合・要求回復・汎用warehouse/本番受入は未達。private証拠は`tmp/local-experiment/warehouse-session-request-checks/20260927/`。WP local-partial。

warehouse focused5 suites/163件すべてPASS、全回帰121 suites/3794実行＝3786PASS＋8skip、88.177秒、log SHA256 `ff01430e0896ba09903310dc65a9aaf0713eb94d8693de2a066983fb7cd97dc6`。design-context/secret scan PASS。保存journal原本SHAと旧全件status JSONのbyte不変を確認。

## 2026-09-27 saved owner completion claim correlation — offline

既存journal-only statusを維持したまま、opt-in `make local-warehouse-session-completion WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>`を追加。固定sessionのprivate最終`receipt.json`、全`operations/<operation-id>.json`とRPC journalを有界no-followで読取・再読し、closed/passed/cleanup・telemetryの保存claim、request/response順、operation結果と**raw file bytes** SHA、reply数と余分なoperation file不在を照合する。欠損・矛盾・途中差替えは部分JSONを出さず`SAVED_COMPLETION_UNVERIFIED`。旧status入口のJSON bytesは変えず、実保存2sessionsは記録2/4 operationsで`correlated-saved-claim`。両セッションの元journal/receipt/operationのSHA、inode/mode/mtimeは前後一致。

初回の新CLIはoperationに存在しない`receipt_sha256`をfile fieldとして比較し拒否。修正後もcanonical dict hashを試して拒否されたが、ownerが実際にはoperation **file生bytes**をhashしていると確認して修正した。失敗を隠さず、既存保存ファイルを再生成・修復せずに成功。`correlated-saved-claim`は**同じownerが残した複数保存物の内部整合のみ**で、実backend query、独立audit delivery、VM/owner現在状態、最終cleanup実施の独立証明ではない。表示`runtime_state=unknown`を維持し、owner再起動・再POST/scale・recoveryなし。Colima停止、保持run/backup変更なし、WP local-partial。使わなければofflineコマンドを呼ばず、移行/削除は不要。private証拠は`tmp/local-experiment/warehouse-session-completion-checks/20260927/`。

warehouse focused5 suites/161件すべてPASS、最終全回帰121 suites/3792実行＝3784PASS＋8skip、87.615秒、log SHA256 `c44f9e33dfc479526591b41bda8f69f89e869f556edd9d73ab3c64ea14c5efd1`。design-context/secret scan PASS。元2sessionのjournal/receipt/operation file集合・SHAとmode/inode/mtimeは前後一致。

## 2026-09-27 archived warehouse RPC status — read-only local increment

`make local-warehouse-session-status WAREHOUSE_LIFECYCLE_RUN_TOKEN=<session-id>`を追加。固定warehouse runs rootの一つのprivate `rpc/journal.json`のみを有界no-followで読み、owner/mode/link/root deviceと読取前後metadata、session/request/response/replyの保存契約を照合する。`recorded_result`は当時のjournalの記録であり、出力の`runtime_state`は**常にunknown**。保留中・失敗・不正/途中変更を成功に昇格させず、欠損/不正は`JOURNAL_UNVERIFIED`で拒否。取引時のreceipt本文・操作の実在・owner最終cleanup・現warehouse readinessを独立証明しない。journal SHAは相関用で署名ではない。

既存短命owner・Go CLI/ query送信の契約を変更せず、owner再起動後の実行再開・再POST・永続queue・自動復旧は未実装。停止中Colimaへ接続/起動せず、既存2件のarchived journalをread-onlyで投影した。記録上の結果は一方が2 actions、他方が4 actions、両方の現状態はunknown。内容SHAとmode/inode/size/mtimeは前後で一致し、credentials・旧run・backupの書換え/削除なし。作業を止めるにはoffline statusコマンドを使わず、旧記録の復元操作は不要。証拠は`tmp/local-experiment/warehouse-session-status-checks/20260927/`。WP local-partial。

warehouse focusedは5 suites/158件すべてPASS。最終全回帰121 suites/3789実行＝3781PASS＋8skip、85.694秒、log SHA256 `1cc7f6a2e0d7f59fa37f234086e8b23716ea442fd39be4be6c5e098909146f0d`。design-context/secret scan PASS。journalの`sha256`は同一保存bytesの相関だけで、同一ユーザー等による偽造や保存後の変化を排除しない。読取に伴うatime影響は除外せず、backup復旧の検証はしていない。

## 2026-09-18 query-triggered wake plan

停止中の生成XSへ固定SELECTを要求し、同じownerがwake完了後に一度だけ実行する。
`session run --query-mode`と`shirokuma warehouse query XS --session TOKEN`を追加する。
旧chill/wake、既存wakeのXS/S probeを維持し、callerは別case/query ID/sourceで束縛する。
任意SQL・保持catalog・新resident service・LLM呼出しは追加しない。

- [x] chill済みXSのquery要求をdurable intentで記録し、同じUIDのDeploymentをwakeする。
- [x] Pod/process/Endpoint/registryの確認と既存probe完了後に、固定TPCH SELECTを一度だけPOSTする。
- [x] 25nation結果・XS自身の終端履歴・Sへfallbackしないこと・全handle消費を照合する。
- [x] 要求UUID・SQL/結果hash・query ID・監査を束縛し、完了応答の保存後だけ成功を返す。
- [x] 生存ownerと終了後15秒grace内の同ID再送は同一応答で、追加scale/POSTを行わない。
- [x] 失敗・取消・切断・不明結果で再POSTせず、所有資源の回収結果を保持する。
- [x] 既存実験を保持して実CLI/監査/回帰を確認し、Vaultへ結果と制限を反映する。

待機時間には既存wake probe・guard・監査が含まれ、cold-query性能の証明にはしない。
短命・単一ownerの固定XSだけが対象であり、owner再起動後の要求復旧、任意warehouse/query、
一般tenant認可、HA、常駐queue、実課金削減、本番受入は未実装として残す。

## Scope

Implement idle suspend/wake semantics for Trino VirtualWarehouse classes. This is a Shirokuma controller behavior, not native Trino scale-to-zero.

## Acceptance Criteria

- Controller detects idle warehouse.
- Coordinator/worker deployments can be stopped and restarted.
- Gateway routing is updated or health checks prevent routing to stopped backend.
- `shirokuma chill` and `shirokuma wake` commands operate on warehouse definitions.
- Pawprints record suspend/wake decisions.

## Notes

Plain Trino does not provide Snowflake-like warehouse suspend by itself.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L3-VW-002`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-17 local experiment plan

ADR-0034/DEV-04Bの承認済みlocal loopで、新規runだけを対象に停止/再開を実装する。
旧Issue/PR/都度承認gateは外部delivery向けであり、このlocal loopの実行条件にはしない。
Gateway/PGを専用Pod、Trino XS/Sを別々の新規Deployment＋Serviceに分離し、
11新規resourceとcontroller生成RS/Podのowner UID chainだけを管理する。
既存Trino/catalog/Lakehouse/DB/PVCへの接続、ホスト/Colima全体の変更は行わない。
固定ARM64 imagesはVW-001のbuild証跡を再利用する。合成TPCHの同じ25nationを検証する。
Serviceの実clusterIPは作成応答と所有権を照合して登録し、DNS/外部向け通信許可は追加しない。

停止判断にはfreshな全query一覧とhost側POST intent/未完了handleの両方を使う。
Trinoは受付直後のqueryが最初のGETまで一覧へ現れないため、サーバーの0件だけでidleとしない。
既知handleの未完了・POST結果不明・別client queryがあれば停止せず理由を保存する。
既知queryの終端を確認してからGateway deactivate→再drain確認→UID/RV付きreplicas0。
wakeは同じDeploymentをreplicas1へ戻し、新しいPod UID/readiness確認後にactivateする。
停止中もSへのSELECTが成功し、XSがSへfallbackしないことと、再開後のXS結果不変を確認する。

同一hostの単一coordinator/worker構成であり、本番の分散drain、独立fault domain、
HA、汎用CRD reconciliation、無期限の自動運転、原価削減率を証明しない。
CLIとPawprints連携を含め観測できた範囲だけをlocal-partialとして記録する。
未観測項目や実装段階は実機結果と分け、実行前の計画を完了扱いにはしない。

## 2026-09-17 local functional evidence

同期run `97b8a7289b2e42e496a1d87bddd2b4ad` とCLI session
`c41cf9f753c14275837ee9f453ed2ad0` が成功した。どちらも7固定requestを検証し、
初期XS/S・未完了handleの消費・停止中S・再開後XS/Sの6SELECTが25nation行と実node IDで一致。
停止中XSはHTTP500・空body・query IDなしでSの全query一覧へ現れず、Sのprocess IDは不変。
host未完了handleが1件ある時、サーバーの全query一覧が0件でも停止を延期した。
完了後のfreshなidle/drain観測を経て、同じXS Deploymentだけをreplicas1→0→1へ変更し、
新しいPod UIDとTrino coordinator ID、空の新XS履歴、Gatewayの再配送を確認した。

実compiled Go CLIから6commandを実行：prepared状態のwakeはnoop、chillはverified、
同request IDのchill再送は同一応答、別IDのchillはnoop、wakeはverified、その再送も同一応答。
4個のowner operation receiptと6応答をprivate保存し、再送によるscale追加は0。
socketは同UIDの0700 directory/0600 Unix socket、型付きrequest/responseとdurable journalを使用。
受付は準備後10分・16接続、成功wake後15秒は既存応答の再取得だけを許す。
CLI切断は実行開始済み操作を取り消さず、ownerが検証・回収まで担当する。
これは生成済み固定planのXSだけを対象にした短命sessionで、任意warehouse定義の操作ではない。

各runは17個の既知UIDを明示回収し、停止時にcontrollerが消した旧XS Podも残留0。
3decision＋1runのPawprint/Loki/実OTel spanをそれぞれ4件照合した。
busy-deferはblockedだがpolicy拒否ではなく、usage/costはNULL。
同期trace `2823135a5def108632ed71493ce72530`、CLI trace `ced9a0cbc583bf91dbeed4541eddf247`。
OTel child spanは監査deliveryを計測し、先に実行したcontroller操作時間を遡ってtrace化しない。
Pawprintには別途測定したcontroller durationを保存する。

初期4runはfailedとして保持した。Kubernetes生成Pod名の切り詰め、空EndpointSliceのnull値、
停止中PodのSucceeded状態、Gateway inactiveとhealth表示の非同期更新、TCP TIME_WAITを修正し、
UID/spec/配送対象の確認を維持して回帰テストを追加した。最初のrecover失敗もarchiveを残し、
次のrecoverで既知UIDのみ回収した。CLI初回はscript相対importでruntime生成前に失敗し、
入口のimport修正と実script回帰を追加した。失敗receiptを成功へ書き換えていない。

証跡は `tmp/local-experiment/warehouse-lifecycle/runs/<run-token>`。
[Runbook](../../../../tools/local_experiment/warehouse_lifecycle/README.md)とMake targetsで再実行できる。
汎用CRD reconciliation、複数workerのgraceful drain、tenant認可/HA/常駐運転/本番受入は未達。
次は既存Data Bearの3固定operationを自然文入力から利用できる入口を実装する。

独立最終監査 `warehouse-lifecycle-audit/80d8bfbffe19418b82b249aed451dd92`（08:17:20 UTC）で、
保持6Pods/27resources/6CM/3Secret UIDs、3Dockerの全設定/起動時刻/restarts、履歴4表の全行hash/5DDL、
従来22証跡filesが不変。Pawprints330＝従来311件不変＋今回19件、同じLoki19件と保存span IDsを照合。
失敗4runの11件も残し、成功2run各4件と区別した。全6runtime runsと初CLI startup failureの残留0。
CLI6応答/4operationのraw SHA256、実binary hash、同IDの完全同一応答、scale2回だけを独立再確認。
全回帰836件（835成功・標準環境のoptional OTel1skip）後、更新済みfocused112件がすべて成功。
同OTel結合は専用venvで成功。Go test/vet/race、設計79documents/関連14tests、diff checkも成功。

## 2026-09-18 query-triggered wake evidence

`warehouse query XS`をquery-modeの短命ownerへ接続した。旧chill/wakeと7固定caseは維持し、
既存wake probes後に別source/request UUIDの`xs_caller`を追加した。callerのintent、POST intent、
query ID、固定SQL/結果hash、終端履歴、UID/scale chain、監査を束縛してから成功応答を保存する。
query CLIは240秒待機し、切断でownerを止めず、same-ID再照会以外の自動再実行をしない。

実session `5edf12a5864e4a1391b8c4d1a7d309da`でGo CLIのchill→query→same-ID query再送が成功。
caller request `7ce1b703-b16c-4375-8db6-ead09ae38485` はSQL POST1回、
query `20260917_152707_00001_gps5i`の25nation行とXS nodeが一致した。
結果hashは `eba954dccf4cef71463d4c4ffe8f7dfbd3777be9660c71265791467025f4acc1`。
再送応答はbyte単位で同一、追加SQL/scaleは0。RPCは3接続・2実operationを記録した。
全8caseの結果/handleを消費し、XS終端履歴はwake probeとcallerの2件、Sは既存3終端caseだけだった。

同じXS Deploymentを1→0→1に変更し、新Pod/processを確認、SのPod/Deployment identityは不変。
17既知UIDを明示回収、停止時にcontrollerが削除した旧XS Podも残存0、conflict/deferredは0。
caller計測はwake待機26059ms、query client3283ms、監査前までの全体31581ms。
前者には起動/probes/監査/guard、後者には結果取得/host処理が含まれ、cold-query性能とはしない。
busy/chill/wake/query/rootの5件をAgentHouse/Loki/実保存OTelで照合し、
traceは `dc95af2097fd91da1fe61ecaac05276e`。LLM/cloud呼出し0、usage/costはNULL。
query成功応答後の回収と最終root監査まで含め、owner exit0・session passedを確認した。

新query22・query telemetry15・RPC追加6のPython43件と新Go6件が成功。
実Go→Unix RPC→実owner/controller/client（外部だけfake）結合で同ID再送とprechill拒否を確認した。
厳密な型、scope/hash、UID/scale履歴、取消の原object、保存障害/監査失敗/改変したspanを確認。
旧session/chill/wakeを含む全local回帰1701件（1700成功・既存optional OTel1skip）、
Go97 top-level/59 subtests、vet/race、設計83documents/関連14tests、secret検査が成功した。

実行・検証証拠は `tmp/local-experiment/warehouse-query-checks/f735cfb20dcc4bcd9c532b7d64f9f086/`、
owner証拠は `warehouse-lifecycle/runs/5edf12a5864e4a1391b8c4d1a7d309da/`、
独立監査は `warehouse-query-preflight/bded3460dc4d4afba5459680c4c3604c/`。
fresh前後18checkで旧AH465全行hash、保持3478files、14Pods/27resources/3Docker/MLflow、
16native policiesとnamespaceが不変、現AH470、生成resource残留0、ports/locks/socket閉鎖を確認した。
fixture/SQL履歴/Metadata/MLflow全論理データは直前監査のsealを継承し、全再走査していない。
任意SQL/warehouse、実tenant認可、owner再起動後復旧、常駐queue、HA、一般並行writer、本番受入は未達。
