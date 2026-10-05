---
project: Shirokuma
doc_id: "WP-L2-POL-001"
title: "WP-L2-POL-001 Policy Gatekeeper Bear"
status: local-partial
created: 2026-07-05
updated: 2026-10-06
version: "0.193"
area: "workpackage"
tags: [shirokuma, workpackage, l2, policy]
---

# WP-L2-POL-001 Policy Gatekeeper Bear

## 2026-10-06: smoke-image credential reference source (offline)

The same-name Trino Deployment now must use exactly the six Secret key
environment references declared by the local manifest before lending its
image to a smoke candidate. Four redirected/literal fake responses passed
before repair; after repair missing/duplicate/extra entries fail, reordered
valid entries pass. Policy suite 166 and full local verifier passed. Colima
was stopped; no Secret values were read or live admission attempted. This does
not authenticate the cluster or grant operator acceptance. Roll back only the
reader/test while retaining saved evidence. Original Vault sync pending.

## 2026-10-06: complete smoke volume reference (offline)

The fixed Trino smoke-image source requires all three expected config/tmp/data
volumes and mounts. A same-name synthetic Deployment with a digest-shaped image
but missing one reference no longer supplies the smoke image; 166 policy tests
and the full local verifier passed. Dedicated Colima was observed stopped; no
live admission or retained request/volume/backup mutation. This is not API
authenticity, strict admission or operator acceptance. Roll back only the scoped
reader/test while preserving saved evidence. Original Vault sync pending.

## 2026-10-06: restrict smoke-source container execution (offline)

The fixed-image lookup accepted a synthetic same-name Deployment with an
alternate command, lifecycle hook, bulk environment source or device mapping.
The reader now refuses nine unowned execution/IO fields absent from the
repository's Trino manifest; its existing minimal and manifest-shaped controls
still pass. `make test-local-policy` passed 166 tests and
`make verify-local-experiment` passed (the fake Dagster failure is an intentional
diagnostic). Dedicated Colima was
observed stopped; no live API, admission, unknown-request replay or retained
resource/backup change. This does not authenticate the source or establish
strict live admission. Revert only the scoped image guard/test if necessary;
preserve saved requests. Original Vault sync pending outside checkout.

## 2026-10-06: bound smoke-source DNS and placement overrides (offline)

Five synthetic same-name Trino Deployment replies with host aliases, custom DNS,
nondefault DNS policy, runtime class or node pinning supplied the fixed image
before repair. The image lookup now refuses those unowned template overrides;
the default `ClusterFirst` policy remains valid. The five negatives failed
before repair; 166 focused policy tests and the local verifier passed. Dedicated
Colima was observed stopped, with no API, dry-run, request replay or retained
data/backup changes. This filter does not authenticate the Deployment or prove
strict live admission. Restore only the scoped reader/test if needed; retain
saved requests. Original Vault sync pending outside checkout.

## 2026-10-06: restrict smoke-source security-context extensions (offline)

Synthetic same-name Deployment replies could supply the Trino smoke image with
Pod sysctls or container `procMount: Unmasked` despite the required safe fields.
The reader now refuses unexamined security-context keys and constrains optional
runAsGroup/fsGroup to the manifest's UID 10000 (including rejecting boolean
aliases). The manifest's explicit group settings and minimal fixture still pass;
the negative regression failed before repair and 166 policy tests passed after.
No live API, admission or resource mutation; this is not source authentication
or strict live admission. Restore only the reader/test if needed, preserving
saved requests. Original Vault sync pending outside checkout.

## 2026-10-06: bound fixed smoke volume payloads (offline)

The source-image reader accepted synthetic ConfigMap default-mode and key/path
remapping and memory-backed or oversized emptyDir definitions. It now permits
only the fixed Trino config item destinations and bounded tmp/data volume
options; absent optional item/size fields remain compatible with prior fixtures.
Seven negative variants and a valid catalog item passed the 166-test policy
suite; the broader local verifier passed. This is an offline filter, not
authentication of the Kubernetes API or live admission. No request was replayed
or retained resource changed. Restore only this reader/test if necessary and
preserve saved evidence. Original Vault sync pending outside checkout.

## 2026-10-06: smoke source volume mount scope (offline)

Six synthetic mount lists used an unowned volume, config subPath, writable or
redirected config, duplicate mount, or malformed mount list to supply the fixed
smoke image before repair. The reader now restricts present mounts to the
dedicated config/tmp/data paths, requires config read-only and rejects mount
extensions; the repository's three-mount shape passes. Focused policy suite
passed 166 tests and `make verify-local-experiment` passed. The dedicated
Colima profile was observed stopped; no live API, admission or saved request
was touched. This is not API authentication or strict live admission. Restore
only the mount guard/test if necessary, preserving saved requests. Original
Vault sync pending outside checkout.

## 2026-10-06: legacy service account alias in smoke source (offline)

The fixed Trino image reader accepted a fake Deployment with a foreign legacy
`serviceAccount` even when `serviceAccountName` was absent. Four negatives failed
before both aliases were constrained to absent/default; the explicit default
control passes. `make test-local-policy` passed 166 tests and the local verifier
passed. Colima was observed stopped; no API request, admission, saved request or
retained backup mutation. This cannot authenticate the API or prove live strict
admission. Restore only the scoped alias guard/test if necessary; preserve saved
intents. Original Vault sync pending outside checkout.

## 2026-10-06: fixed smoke source service account (offline)

A synthetic otherwise-matching Trino Deployment selecting a foreign service
account supplied the fixed smoke image before repair. Its template now allows
only the repository's absent/default service account, refusing foreign and
malformed explicit values; the explicit `default` control passes. Four
negatives failed before repair. `make test-local-policy` passed 166 tests and
`make verify-local-experiment` passed. Dedicated Colima was freshly observed
stopped; no live API, dry-run, policy change, unknown request replay or
retained data/backup mutation. This does not authenticate the API, certify
the live Deployment or establish strict admission. Revert only the scoped
source guard/test if needed; retain saved intents. Original Vault sync pending.

## 2026-10-06: bound smoke ConfigMap source and unique volumes (offline)

Four synthetic source lists with a foreign/missing ConfigMap name, optional
ConfigMap, or repeated volume name supplied the fixed image before repair.
The source reader now requires the dedicated `config` ConfigMap name, a
non-optional reference and unique volume names. ConfigMap and emptyDir controls
pass; `make test-local-policy` (166) and `make verify-local-experiment` pass.
Dedicated Colima was freshly observed stopped; no live API, dry-run, image
import or retained request/data/backup mutation. This filter is not live
admission or API authenticity. Revert only the scoped reader/tests if needed,
retaining saved intents. Original Vault sync pending outside checkout.

## 2026-10-06: smoke image volume sources limited (offline)

Five fake Deployment volume lists using projected service-account tokens, PVC,
CSI, mixed sources or no source supplied the pinned smoke image before repair.
The reader now accepts only one well-formed ConfigMap or emptyDir source per
volume, consistent with the local Trino manifest; both permitted controls pass.
`make test-local-policy` passed 166 tests after the expected failing negatives.
Dedicated Colima was observed stopped; no live API, dry-run, retained resource,
backup or request change. This does not verify the manifest in the current
cluster, authenticate an API or establish strict live admission. Revert only
the image-source filter/test if needed, preserving saved intents. Original
Vault sync pending outside checkout.

## 2026-10-06: smoke image rejects host-mounted source Pods (offline)

The same-name Trino Deployment image could be borrowed from a Pod template
with a hostPath volume despite its restricted container context. The reader
now rejects malformed volume lists and any hostPath entry; the manifest's
ConfigMap and emptyDir volumes remain allowed. Synthetic hostPath and malformed
negatives and an emptyDir control pass in `make test-local-policy`. No API,
dry-run, request replay, image import or retained data/backup change. This is
not current workload verification, server authentication or strict live
admission. Revert only the scoped reader/test if needed; preserve saved
intents. Original Vault sync pending outside checkout.

## 2026-10-06: smoke source requires restricted Trino security context (offline)

The pinned-image reader accepted a synthetic same-name Deployment with root,
unconfined or privileged Trino Pod/container context. Nine negative cases failed
before the guard. It now requires the manifest's non-root UID and RuntimeDefault
seccomp, disabled escalation, read-only root filesystem and dropped capabilities;
container identity/seccomp overrides, missing fields and Python boolean/number
aliases are refused. The explicit
unprivileged `privileged: false` default remains valid. `make test-local-policy`
passed 166 cases after repair; `make verify-local-experiment` passed. Dedicated
Colima was freshly observed stopped;
no API, dry-run, image import, retained request/data or backup mutation.
This is an offline source filter, not authenticated API provenance, proof of
current deployment security or strict admission. Revert only this reader guard
and regression fixture if necessary; retain prior requests. Original Vault
sync pending outside checkout.

## 2026-10-06: smoke source Pod isolation (offline)

The fixed Deployment image lookup previously accepted a local-only image from
a template that enabled host network/PID/IPC or mounted a service-account token.
The lookup now requires the manifest's explicit `automountServiceAccountToken:
false` and absent or strictly false host-namespace flags. Synthetic negative
and explicit-false controls pass in `make test-local-policy` (166 tests). The
dedicated Colima profile was observed stopped; no API, dry-run, image import,
retained data or backup mutation occurred. This is a conservative smoke-image
source check, not proof that the Pod is running, the server is authentic or
strict live admission works. Restore only the scoped lookup/test if necessary;
preserve saved intents. Original Vault sync pending outside checkout.

## 2026-10-06: smoke image requires local-only pull policy (offline)

The fixed, persisted Trino Deployment could supply a digest-shaped smoke image
even if its only container omitted `imagePullPolicy` or selected `Always` or
`IfNotPresent`. The reader now requires the repository Trino manifest's `Never`
setting; five synthetic malformed/remote-capable pull-policy cases are refused
and the local-only control passes. `make test-local-policy` passed 166 tests and
`make verify-local-experiment` passed. The dedicated Colima profile was freshly
observed stopped. No API call, dry-run, image import, policy mutation or retained
data/backup change. This does not prove image bytes are present on a node, API
authenticity, live strict admission or operator acceptance. Revert only the
lookup guard/test if necessary, retaining saved requests. Original Vault sync
pending outside checkout.

## 2026-10-06: smoke image excludes controller-owned Deployment (offline)

The fixed-name, labelled Deployment could supply a smoke image even with a
foreign controller ownerReference. Four synthetic nonempty/malformed owner
reference negatives failed before the reader required absent or empty references;
both ordinary and explicitly empty owner lists pass. The 166-test focused
policy suite passed after repair. The dedicated lab was observed stopped; no
API, dry-run, policy mutation or retained-data/backup change occurred. This
does not authenticate API responses or establish live strict admission. Revert
only this guard and its tests if necessary; retain saved requests. Original
Vault sync pending outside checkout.

## 2026-10-06: smoke image source ownership labels (offline)

The same-name, matching-selector persisted Deployment could still supply the
smoke image without the dedicated workload's ownership labels. Eight fake
response negatives (missing/foreign scope or part-of labels on either the
Deployment or Pod template) failed before requiring the labels present in the
local Trino manifest. The policy suite passed 166 tests after repair. An
independent FinOps suite passed 212 tests (seven Linux-only skips) without
collecting or modifying its reader. The local verifier and design-context
passed; dedicated Colima was freshly observed stopped. No live API/admission,
retained requests, reports, volumes or backups were changed. Labels are not an
authenticated provenance or proof of live strict admission. Restore only this
image-source guard/test if needed, preserving saved evidence; original Vault
sync pending outside checkout.

## 2026-10-06: smoke Deployment selector bound to Trino template (offline)

The fixed Trino image lookup accepted a persisted same-name Deployment selecting
different Pods (or no matching Pods). Four fake-API selector/template negatives
failed before repair. It now requires the exact dedicated `trino-local` selector
and its label on the template, while allowing other template labels. Missing or
malformed selector/template labels also fail. The checked selector matches the
repository's local Trino Deployment manifest; the 166-test policy suite passed.
Colima was freshly observed stopped: this does not prove live deployment shape,
API authenticity, image execution or strict admission. No API calls, policy
resources, retained data, requests or backups were changed. Revert only the
scoped image guard/test if necessary, preserving historical intents. Original
Vault sync pending outside checkout.

## 2026-10-06: smoke Deployment persisted identity (offline)

The bounded Trino image lookup previously accepted a same-name, digest-shaped
synthetic Deployment reply with missing or malformed UID/resourceVersion. Five
fake negatives failed before the guard; the lookup now requires persisted API
identity before using the image for the fixed dry-run corpus. The existing
single-container, namespace and digest checks remain. Focused policy suite:
166 tests passed. Dedicated Colima was freshly observed stopped; this is not
API authenticity, live admission or proof of current deployment readiness.
No policy objects, saved requests, volumes or backups were changed. Restore
only the lookup guard/test if necessary; keep prior evidence. Original Vault
sync pending outside checkout.

## 2026-10-06: malformed optional smoke container lists (offline)

The smoke image reader treated falsey non-list `initContainers` and
`ephemeralContainers` values as absence. It now requires either an absent field
or an empty list; nonempty lists remain refused. Synthetic null/map negatives
and the empty-list control pass in the 166-test policy suite. The separate
FinOps offline suite passed 210 tests (seven Linux-only skips) without
collection. Dedicated Colima was freshly observed stopped. This is response
shape validation, not proof of live admission or current Deployment safety.
No retained data/backup or saved intent was changed. Restore only the scoped
reader/test if needed; original Vault sync pending outside checkout.

## 2026-10-06: smoke image source must be a single-container deployment (offline)

The Trino deployment image lookup previously returned the pinned main image
even when the observed Pod template also carried a mutable sidecar, init
container or ephemeral container. Three fake API negatives failed before the
repair. The lookup now refuses these extra containers before using that image
for the fixed dry-run corpus. The 166 focused policy tests pass. This changes
only smoke input selection, not cluster admission or deployment state. Colima
was observed stopped; live strict admission and current deployment shape are
not verified. Revert only the image lookup/test if necessary; preserve saved
intents and policy resources. Original Vault sync pending outside checkout.

## 2026-10-06: policy UID continuity in rollback retention (offline)

A fake API replaced a policy during the first binding deletion with the same
owned spec but a different UID. Before repair rollback removed the bindings yet
incorrectly reported `policy_definitions_retained: true`; the negative failed.
Rollback now observes each owned persisted policy UID before binding deletion
and requires the same UID at its final retention read. A status-only
resourceVersion update remains valid. Focused policy tests passed (166) and
`make verify-local-experiment` passed. Dedicated Colima was freshly observed
stopped; no live API, retained data/backup mutation or request replay. Sequential
reads cannot prove atomic retention or live strict admission. Restore only the
UID continuity check/tests if needed, preserving saved intents. Original Vault
sync pending outside checkout.

## 2026-10-06: binding returns during retention inspection (offline)

An owned binding reappearing while rollback checked retained policy definitions
previously yielded `bindings_removed: true`. A fake API negative failed before
repair. A second full-name absence scan after retention inspection now rejects
that result; 164 policy tests pass. The independent FinOps 207-test offline
suite passed (seven Linux-only skips), without collecting or changing saved
reports. Dedicated Colima was freshly observed stopped; no live API, replay,
retained volume/backup mutation or GitHub write. These sequential reads are
not atomic and cannot certify future absence, live strict admission or operator
acceptance. Revert only the added scan/test if necessary, retaining saved
intents. Original Vault sync pending outside checkout.

## 2026-10-06: rollback policy-retention claim (offline)

Rollback previously returned `policy_definitions_retained: true` solely because
it did not issue policy deletes, even when a definition was missing, foreign or
without persistent identity. The final read now marks retention false in those
cases while allowing the scoped binding rollback to finish; it does not
silently restore definitions. Synthetic negative and normal rollback tests pass.
The separate FinOps 207-test suite passed (seven Linux-only skips), without
collection or changes to saved reports. Dedicated Colima was observed stopped;
no live admission, retained data/backup mutation, unknown-request replay or
GitHub write. Final reads are sequential, not an atomic cluster snapshot or
server authentication. Strict live admission and operator acceptance remain
open. Revert only this result check/test if necessary; preserve saved intents.
Original Vault sync pending outside checkout.

## 2026-10-06: final rollback binding inventory (offline)

Rollback could return `bindings_removed: true` after an earlier binding
reappeared while later bindings were being removed. A fake API regression
failed before repair; rollback now scans all dedicated binding names after
the delete phase and refuses success if any remains. `make test-local-policy`
passed 162 tests and `make verify-local-experiment` passed. The dedicated
Colima profile was freshly observed stopped, so no live API or retained
data/backup was changed. This scan is sequential, not atomic or authenticated:
a binding can still appear after its read. Live strict admission and operator
acceptance remain open. Restore only the final scan/test if necessary;
preserve historical intents. Original Vault sync pending outside checkout.

## 2026-10-06: binding continuity after policy recheck (offline)

A binding introduced during the final policy ownership/type-check pass was
previously detected only by the attempted binding create (`name already
exists`), after the binding scan had completed. A fake API negative failed
before repair. Activation now scans all bindings again after the policy pass,
checking ownership, preflight presence and pinned UID before its first binding
create. The 161 policy tests, independent 207 FinOps tests (seven Linux-only
skips), `make verify-local-experiment` and diff check passed. Dedicated Colima
was freshly observed stopped; no live API, unknown-request replay, retained
volume/backup mutation or GitHub write. Sequential reads still race subsequent
changes; live strict admission and operator acceptance remain open. Revert only
this recheck and its regression if necessary, preserving saved intents. Original
Vault sync pending outside checkout.

## 2026-10-06: pre-binding policy recheck (offline)

After successful type-check, a policy could change during the binding scan;
activation would discover the change only in final inventory, after creating
bindings. A fake API negative first failed because a binding was created before
the error. Activation now rechecks every policy's ownership, pinned UID and
current type-check after scanning bindings and before the first binding create.
Owner drift and stale observed generation both leave bindings absent. Focused
policy tests (160), `make verify-local-experiment` and design-context passed; the independent FinOps
suite passed without collection. Dedicated Colima was observed stopped; no live
API, unknown-request replay, retained volume/backup mutation or GitHub write.
The recheck is sequential, not atomic: a later change can still race activation;
strict live admission and operator acceptance remain open. Roll back only this
pre-binding check and its tests if required; preserve saved intents. Original
Vault sync pending outside checkout.

## 2026-10-06: activation per-kind presence continuity (offline)

The per-kind read could silently adopt an owned policy appearing after the
final whole-inventory scan, or recreate one that disappeared; an existing
binding could likewise disappear before the binding phase. Synthetic policy
appearance/disappearance negatives failed before repair. The per-kind read now
requires the same preflight presence before it creates anything in that kind;
binding disappearance is also refused. The two new regressions pass, the
focused policy suite passed 157 tests and `make verify-local-experiment`
passed. An independent, unchanged FinOps suite passed 207 tests (seven
Linux-only skips), without collection. Dedicated Colima was observed stopped;
no live API, unknown-request replay, retained volume/backup change or GitHub
write. Sequential reads remain non-atomic: changes after the read, live strict
admission, AgentHouse volume bytes and operator acceptance are unverified.
Restore only this presence guard/tests if needed; retain saved intents and
reports. Original Vault sync pending outside checkout.

## 2026-10-06: activation preflight presence continuity (offline)

An exactly matching binding appearing during server dry-runs previously passed
the final scan, allowing policy creation despite the changed preflight inventory.
A disappearing pre-existing policy was also not rejected at that scan. Both
synthetic cases now refuse activation before any persistent create, while the
normal activation and the foreign-object refusal still pass. The first negative
failed before repair; `make test-local-policy` passed 156 tests and `make
verify-local-experiment` passed after repair. Colima was freshly observed stopped;
no live API call, retained volume/backup mutation, request replay or GitHub
write. Sequential reads do not establish atomicity, source/API identity or live
strict admission; races after the scan and operator acceptance remain open.
Restore only the presence guard and regressions if necessary, preserving saved
intents. Original Vault sync pending outside checkout.

## 2026-10-06: recheck full inventory after activation dry-runs (offline)

A foreign binding introduced during the final dry-run could previously leave
newly created policy definitions before the binding phase discovered it. A
fake-API regression now requires a whole-inventory ownership and UID recheck
after dry-runs and before the first persistent create; it leaves no new policy
definitions. `make test-local-policy` passed 154 cases and `make
verify-local-experiment` passed. This sequential recheck is not an atomic
cluster snapshot and cannot prevent a later race or prove live strict admission.
Dedicated Colima was observed stopped; no live policy write, request replay,
retained data/backup mutation or GitHub write. Revert only this recheck and
test if needed; retain saved intents. Original Vault sync pending outside the
checkout.

## 2026-10-06: activation resource identity across reads (offline)

An existing policy replaced at the same name between preflight and type-check
could previously activate bindings; a binding replacement at final inventory
could certify ready. Two disposable fake-API negatives now refuse changed UIDs
across preflight, creation, type-check and final inventory. The first negative
failed before repair; the policy suite passed 153 tests afterward and full
`make verify-local-experiment` passed. This is not an atomic API snapshot or
authenticated live admission. Dedicated Colima was observed stopped; no live
policy write, unknown-request replay, retained volume/backup mutation or
GitHub write. Revert only the activation UID pin/tests if needed; preserve
saved intents. Original Vault sync pending outside checkout.

## 2026-10-06: nonzero persisted resource version (offline)

Direct status and activation previously accepted synthetic policy metadata with
`resourceVersion: "0"` as persisted; a negative failed before repair. The common
identity guard now rejects all-zero decimal versions, including `"000"`; normal
version `"12"` remains valid. `make test-local-policy` passed 151 tests before
the expanded zero spelling fixture; the full local verifier passed with the
guard. Dedicated Colima was observed stopped; no API request,
unknown-request replay or retained data/backup change. This is an offline
identity check, not API authenticity, live admission or operator acceptance.
Restore only the guard/test if needed, preserving saved intents. Original Vault
sync pending outside this checkout.

## 2026-10-06: persisted identity required for policy readiness (offline)

An otherwise matching synthetic policy or binding with missing/invalid UID or
resourceVersion previously passed direct status and could make activation ready.
The negative failed before repair. Existing and newly created objects now need
validated persistent identity before readiness or a later write; dry-run replies
remain spec/ownership checked without requiring persistence. The Gatekeeper
observer's persisted-object fixture was updated to include a resource version;
the first full verifier exposed that fixture gap, and the rerun passed. Focused
policy 150 tests passed. Independently, the unchanged FinOps suite passed 207
tests (seven Linux-only skips). The dedicated Colima profile was observed
stopped; no live API, unknown-request replay, retained data or backup changes.
This is not API authentication, atomic activation, live strict admission or
operator acceptance. Roll back only this guard and its tests if needed; keep
saved intents. Original Vault sync pending outside the approved checkout.

## 2026-10-06: terminating resources cannot certify readiness (offline)

An otherwise matching policy or binding with a non-null deletionTimestamp
previously passed ownership and could make inventory status ready, or pass
activation preflight; a synthetic terminating dry-run reply could also precede
persistent creates. Both negatives failed before repair. The shared ownership
guard now refuses these observations; the normal owned inventory and activation
remain valid. Focused policy 149 tests and the full local verifier passed.
Dedicated Colima was observed stopped; no live API, activation, unknown-request
replay or retained data/backup mutation. Offline fake replies do not authenticate
the API, make activation atomic or prove live strict admission/operator acceptance.
Restore only this ownership check and tests if necessary; preserve historical
intents. Original Vault sync pending outside this checkout.

## 2026-10-06: whole-inventory rollback identity recheck (offline)

A late binding changing UID or resourceVersion on its second read previously
allowed seven earlier deletes before the existing per-binding guard stopped.
The negative failed before repair. Rollback now compares every binding against
its preflight identity on a second full scan before the first deletion, retaining
the per-delete read and atomic API preconditions. Focused policy tests passed
147 cases; independent saved FinOps history/storage review passed 207 cases
(seven Linux-only skips) without collection or code changes; `make
verify-local-experiment` passed. Dedicated Colima
was observed stopped. No live API request, retained data/backup mutation or
unknown-request replay. Scans are sequential, not a transaction; drift after
the second scan can still leave a partial rollback. Live strict admission and
operator acceptance remain unverified. Restore only the scoped recheck/test if
needed; preserve historical intents. Original Vault sync pending outside this
checkout.

## 2026-10-06: validate dry-run replies and direct status inventory (offline)

Activation previously ignored successful server dry-run bodies, allowing a
missing, foreign or altered reply to precede persistent creation. It now
checks each reply against the expected owned policy/binding before creating
anything. Independently, direct `inventory_status` could declare an empty or
partial inventory ready (vacuous `all`); it now requires the complete unique
16-item inventory before any read. Both negatives failed before repair; the
normal fake activation/status pass with 146 policy tests and the full local
verifier. The dedicated Colima profile was freshly observed stopped; no live
API, saved-request replay, retained data or backup mutation. This does not
authenticate the server, prove live admission or make activation atomic;
operator acceptance remains open. Roll back only these two guards and tests
if needed, preserving saved intents. Original Vault sync pending outside this
checkout.

## 2026-10-06: complete caller inventory before activation or rollback (offline)

Direct callers could pass 15 of 16 native items (or no bindings) and obtain a
successful but incomplete rollback; duplicate entries could also attempt a
second delete. A regression reproduced this before repair. Both activation and
rollback now require each of the eight fixed policies and eight bindings exactly
once, with typed identities, before any scoped cluster read or write. The
constructor's separate certified snapshot check remains in force; this guard
does not authenticate the caller's source. `make test-local-policy` passed 144
tests, `make verify-local-experiment` passed, and the independent FinOps suite
passed 207 tests (seven Linux-only skips). Colima was freshly observed stopped;
no live API call, intent replay, retained data or backup change. Concurrent
changes can still yield partial rollback despite atomic delete preconditions;
strict live admission and operator acceptance remain open. Restore only the
inventory guard and tests if necessary; preserve saved intents. Original Vault
sync pending outside this checkout.

## 2026-10-06: rollback rechecks preflight binding identity (offline)

An owned binding replaced or updated between the initial rollback preflight and
its delete-phase read could be deleted using the newer object's UID/version;
a binding absent at preflight could also appear and be removed. Disposable
regressions reproduced both cases before repair. Rollback now retains each
preflight absence or UID/resource-version pair and refuses a different
delete-phase observation, while atomic delete preconditions still protect the
subsequent request. `make test-local-policy` passed 141 tests and `make
verify-local-experiment` passed. Dedicated
Colima was observed stopped; no live API request, retained intent/data or backup
mutation. A change after this recheck can still cause a partial rollback; the
operation is not transactional and live admission/operator acceptance remain
unverified. Restore only the scoped identity recheck and tests if needed;
preserve saved intents. Original Vault sync pending outside this checkout.

## 2026-10-06: rollback validates all delete preconditions first (offline)

Ownership preflight alone allowed a later owned binding with a missing UID or
invalid resource version to fail after earlier bindings had been deleted. The
rollback now validates every existing binding's delete preconditions before
any deletion, reusing the per-delete validation for the subsequent fresh read.
Synthetic late-binding negatives leave all eight bindings and the event stream
unchanged; the normal rollback still passes. `make test-local-policy` passed
139 tests and `make verify-local-experiment` passed. Dedicated Colima was
freshly observed stopped; no API request, retained intent/data or backup
mutation. A binding can still change between preflight and deletion: atomic
UID/version delete preconditions refuse that request, but a prior deletion may
already have occurred. This is not transactional rollback, live admission or
operator acceptance. Restore only the preflight/helper/test if needed; keep
saved intents. Original Vault sync pending outside this checkout.

## 2026-10-06: post-delete binding observation must still be owned (offline)

A synthetic malformed post-delete binding read previously raised a raw
`AttributeError` while checking removal. Rollback now checks the surviving
object's dedicated ownership and spec before interpreting its UID; a foreign
same-UID object cannot be waited on as if it were the old binding. The negative
failed before repair, both negatives and the normal removal pass in 138 policy
tests; `make verify-local-experiment` passed. The dedicated Colima profile was freshly observed stopped; no live
delete, request replay, retained intent/data or backup mutation. This does not
verify live API identity, actual deletion or operator acceptance. Restore only
the scoped post-delete check/tests if needed; preserve saved intents. Original
Vault sync pending outside this checkout.

## 2026-10-06: JSON-exact source projection certification (offline)

The bounded native manifest reader previously certified a synthetic projected
boolean replaced by numeric zero (and numeric one replaced by `true`), because
Python dict equality aliases these JSON types. A negative test reproduced both
false acceptances; certification now compares canonical serialized JSON before
returning the checked snapshot and digest. The current generated manifest has
no affected boolean/numeric field; the normal projection and 136 focused
tests pass, as does the full local verifier. Colima was freshly observed
stopped; no live API, admission or retained input/backup mutation. This guards
local projection equality, not producer authenticity, live strict admission or
operator acceptance. Revert only the projection comparator/test if needed;
preserve saved intents. Original Vault sync pending outside this checkout.

## 2026-10-06: exact owned spec and rollback identity (offline)

Existing policy/binding preflight now compares the spec after only the documented
empty selector defaults using JSON-exact semantics; Python boolean/number aliases
can no longer certify ownership. Malformed spec/defaults fail with `PolicyError`.
Rollback additionally requires the caller's binding to match the certified
snapshot JSON-exactly, and scoped policy reads reject malformed identities
before I/O. Synthetic alias/malformed negatives failed before repair; 135 focused
policy tests and `make verify-local-experiment` passed afterward. Separately,
WP-L6-FIN-001's 207 offline tests passed (seven Linux-only skips). Dedicated
Colima was freshly observed stopped; no live API, admission, collection, unknown
request replay, retained data/backup mutation or GitHub write. Source/response
authenticity, live strict admission and operator acceptance remain open. Restore
only the scoped ownership/read/rollback guards and regressions if needed, keeping
saved intents. Original Vault sync pending outside this checkout.

## 2026-10-06: JSON-exact create and malformed rollback preflight (offline)

The scoped Kubernetes create allowlist now compares canonical JSON rather
than Python nested-dict equality: boolean/number aliases in a certified
snapshot cannot pass as the original request. Malformed creation and rollback
binding identities return `PolicyError` before a request, rather than leaking
unhashable-key exceptions. Synthetic certified fields exercise both alias
directions; today's generated inventory contains no numeric/boolean field
subject to this particular alias. The policy suite and full local verifier
passed. Dedicated Colima was observed stopped; no live API call, unknown
request replay, retained intent/data or backup mutation. This does not
authenticate certified inputs or establish live strict admission or operator
acceptance. Revert only these guards and regressions if required, preserving
saved intents. Original Vault sync pending outside checkout.

## 2026-10-06: malformed dry-run identity fails closed (offline)

After the fixed-corpus increment, independently checked the namespace/kind
preflight with unhashable and non-string JSON-like values. Lists and mappings
previously raised a raw `TypeError` during set membership; explicit type checks
now return `PolicyError` before any request. All 130 focused policy tests pass.
No live API call, replay, retained report/volume/backup mutation or admission
claim. Revert only this type guard and regression if required; retain saved
intents. Original Vault sync pending outside checkout.

## 2026-10-06: fixed protected-namespace smoke request corpus (offline)

The native bridge previously checked only namespace, kind and disposable name
for protected-namespace server dry-runs. It now compares the entire submitted
request against the generated 14-case protected corpus, permitting only the
disposable name and a local immutable Trino image digest as variables. The
fixed mutable-image negative is still admitted for its expected policy denial.
Malformed image, altered metadata, execution controls and JSON type aliases
are refused before any request. The first test run exposed the deliberate
mutable-image case; a corrected check and 129 policy tests pass. `make
verify-local-experiment` passed. The dedicated Colima profile was freshly
observed stopped; no API call, intent replay, retained data or backup mutation.
The caller-provided digest is not authenticated by this bridge: the smoke
workflow obtains it from the scoped Deployment read. No live admission or
operator acceptance was established. Revert only this guarded request boundary
and test if required, preserving saved intents. Original Vault sync pending
outside this checkout.

## 2026-10-06: fixed outside-namespace dry-run control (offline)

The native Kubernetes bridge previously allowed any disposable-named Pod in
`default` with one container and the mutable-image tag. It now limits that
outside-namespace dry-run to the exact generated control, apart from its
disposable name. Privilege, host networking, extra containers/metadata, image
drift and JSON boolean/integer aliases fail before a scoped request; the real
control still reaches the mocked dry-run. `make test-local-policy` passed 128
tests; `make verify-local-experiment` passed after the change. The dedicated
Colima profile was observed stopped. No API request, new intent, saved data or
backup mutation. This in-process guard does not authenticate the API, establish
live admission or grant operator acceptance. Roll back only the smoke guard
and regression if needed; preserve saved intents. Original Vault sync pending
outside this checkout.

## 2026-10-06: complete unique native allowlist inventory (offline)

The Kubernetes bridge now refuses a caller-supplied certified inventory unless
all eight policy and eight binding identities occur exactly once. Previously a
duplicate could silently replace an entry when the list became a map. Missing,
duplicate, unexpected and malformed identities fail before any scoped request;
the unchanged 16-item projection remains accepted without another manifest read.
`make test-local-policy` passed 127 tests and `make verify-local-experiment`
passed. The dedicated Colima profile was freshly observed stopped: no API,
admission activation, retained intent or backup change. This guards a local
constructor boundary, not producer authenticity or live strict admission.
Revert only the constructor guard/test if required; preserve saved intents.
Original Vault sync pending outside this checkout.

## 2026-10-05: owned native admission allowlist snapshot (offline)

The Kubernetes bridge now deep-copies certified manifest items at construction;
later edits to a caller-held list or nested item cannot change the scoped create
allowlist. A disposable mutation negative refuses altered create requests while
the original certified request remains allowed by the local guard. The real
projection, 126 policy tests and full local verifier passed. Dedicated Colima was observed stopped;
no API request or policy activation was attempted. This guards in-process
aliasing, not the source producer, Kubernetes response identity, live strict
admission or operator acceptance. Restore only the constructor/test if needed;
preserve saved intents. Original Vault sync pending outside this checkout.

## 2026-10-05: explicit digest at native projection boundary (offline)

The renderer no longer falls back to hashing the source pathname when its
caller omits a digest. It requires a lowercase SHA-256 supplied by the caller;
the manifest comparison continues to pass the digest of the bounded source
bytes that it decoded. A disposable constructor regression refuses missing or
malformed digests without reopening the source and accepts an explicit digest.
The real 16-item projection, 125 native policy tests and full local verifier
passed. Dedicated Colima was freshly observed stopped; no live API, admission,
unknown-request replay, retained intent, volume or backup mutation. An explicit
digest is caller-provided, not producer authentication or atomic filesystem
identity; strict admission and operator acceptance remain open. Roll back only
the renderer signature/test if necessary; preserve saved evidence. Original
Vault sync pending outside this checkout.

## 2026-10-05: final-entry metadata across bounded policy reads (offline)

Both the CEL source and dedicated native manifest readers now compare the
post-read pathname entry's size and modification/change timestamps as well as
device/inode against the descriptor's checked snapshot. The manifest also
explicitly requires a regular final entry after reading. Disposable in-place
edits after the final descriptor check are rejected before source projection;
the real projection and 124 native policy tests pass, as does `make
verify-local-experiment`. A post-read manifest relink to its former inode is
also refused (its ctime already exposed this in the fixture). The dedicated
Colima profile was observed stopped; no API request, retained intent, volume
or backup mutation occurred. Sequential metadata checks cannot exclude ABA,
same-timestamp changes or authenticate the tool/producer; live strict admission
and operator acceptance remain open. Roll back only these reader checks/tests
if necessary, preserving historical intents. Original Vault sync pending
outside the approved checkout.

## 2026-10-05: certified policy CLI allowlist and digest (offline)

The native policy CLI previously validated one manifest/source projection, then
reloaded the manifest for its Kubernetes create allowlist and reopened the source
pathname for the reported digest. The CLI now uses the already certified items
for the allowlist and their checked source annotation for the digest. Isolated
CLI and constructor regressions ensure neither second read is required. The
dedicated Colima profile was freshly observed stopped; no Kubernetes operation,
retained intent, volume or backup was changed. This binds the local CLI's
reported and authorized inputs to one validated snapshot, not to an
authenticated producer, an atomic filesystem view, a live admission result or
operator acceptance. Restore only the CLI snapshot wiring if needed, keeping
saved intents and historical evidence. Original Vault sync pending outside
the approved checkout.

## 2026-10-05: decode the checked source bytes (offline)

The native projection previously checked source bytes before/after `kubectl`
but let the decoder independently reopen the source pathname. It now supplies
the bounded checked UTF-8 snapshot via `kubectl patch --local -f -` stdin and
compares the source again after decoding. A disposable source replacement during
decoding proves the original bytes were supplied while the changed pathname
still fails certification. Real seven-source projection and 119 focused tests,
plus `make verify-local-experiment` and design-context passed; dedicated Colima
remained stopped. Strict `make verify-security` retains five historical failures
from expired owner authorization/missing image ledger entries, not renewed for
this offline change. This does not authenticate kubectl or the source producer,
prevent in-place ABA modification, or prove live admission. No saved intents,
retained data or backup changed. Roll back only the stdin reader/test, preserving
saved evidence. Original Vault sync pending outside the approved checkout.

## 2026-10-05: source ancestor linkage (offline)

The bounded native policy source snapshot now opens each directory component
without following links and rechecks its linkage after the read. A disposable
linked ancestor and a post-read replacement with a link to the original
directory both fail closed; the real source and 118 policy tests pass. Full
`make verify-local-experiment`, design-context, secret scan and diff check
passed. Dedicated Colima was observed stopped; no live admission, retained
data/backup mutation or unknown-request replay. Source decoding still invokes
kubectl by pathname; sequential snapshots cannot defeat ABA races or prove
decoder authenticity, strict admission or operator acceptance. Revert only
the source reader and test if required; preserve saved intents and reports.
Original Vault synchronization pending outside this checkout.

## 2026-10-05: bounded source snapshot and same-byte replacement (offline)

The native exact-projection check formerly read unbounded source path bytes
before and after local decoding. It now requires a <=256 KiB regular source,
opens the final component without following links or waiting on a FIFO, checks
descriptor stability and the current entry, and compares both bytes and file
identity across decoding. Disposable symlink, FIFO, oversize and same-byte
replacement negatives fail before certification; the actual source/manifest
projection, 117 native tests, 22 Gatekeeper tests, 61 candidate-observer tests,
23 policy CLI tests and `make verify-local-experiment` pass. This guards local source reads, not
the kubectl decoder's independent pathname read: parent links, ABA replacement,
tool authenticity and live admission remain unproved. Colima remained stopped;
no saved intent, retained data or backup was modified. Roll back only the source
reader and its tests if the source contract changes; preserve prior evidence.
Original Vault sync pending outside checkout.

## 2026-10-05: source projection digest consistency (offline)

The native manifest comparison previously decoded the seven CEL sources and
then hashed the source pathname independently; a source replacement during the
local decode could bind the projected items to different bytes. The comparison
now checks source bytes before and after decoding and hashes the first snapshot
for the rendered annotations. A disposable changed-source negative and normal
projection pass; 115 focused tests and the full local verifier passed. Dedicated
Colima was freshly observed stopped; no live admission, request replay, retained
data/backup mutation or GitHub write. This is a sequential check, not atomic
source identity or decoder authenticity; ABA swaps, symlinks, strict admission
and operator acceptance remain unproved. Revert only the scoped consistency
guard/test if necessary and preserve historical intents. Original Vault sync
pending outside checkout.

## 2026-10-05: strict numeric manifest decoding (offline)

The bounded manifest reader now refuses Python's non-JSON `NaN`/`Infinity`
tokens and floating-point overflow/underflow before decoding the repository
source. Disposable replacements and the real 16-item manifest passed 114
focused tests; full local verification passed. Independently reviewed FinOps
saved-history bounds and per-report coverage (204 tests, seven Linux-only
skips), without a FinOps behavior change. Dedicated Colima was observed
stopped; no live admission, collection, unknown-request replay or retained
data/backup mutation. Strict JSON does not authenticate the source decoder,
resolve concurrent pathname races or prove admission/operator acceptance.
Rollback only this numeric guard and test if needed; preserve saved intents.
Original Vault sync pending outside checkout.

## 2026-10-05: post-read manifest ancestor linkage (offline)

The manifest reader pinned directory descriptors during traversal but checked
only the final parent after reading. An ancestor replaced with a symlink to its
original directory could retain the same final parent inode. The reader now
checks every retained parent/child directory entry after the bounded read and
before source projection. A disposable swap during `os.read` is refused before
source access; the real manifest and 113 focused policy tests passed, as did
`make verify-local-experiment`. Dedicated Colima was freshly observed stopped;
no live admission, saved-request replay, retained data/backup mutation or
GitHub write. This does not prevent a swap after the checks, authenticate the
checkout or decoder, or establish strict admission/operator acceptance. Roll
back only the directory-chain check/test if needed; preserve saved intents.
Original Vault sync pending outside checkout.

## 2026-10-05: observer guard snapshot correlation (offline)

Gatekeeper's before/after dry-run guards and the one-Pod observer previously
compared validated manifest items and a manifest digest from separate reads.
Each guard now compares items and digest from the same bounded validated
snapshot; before and after remain separate observations. Fake digest-only drift
fails without a passed receipt (the fixed corpus may already have sent its
requests), and caller-Pod drift stops before its request. Focused native 112,
Gatekeeper 22 and candidate-observer 61 tests and full local verification
passed. The dedicated Colima profile was freshly observed stopped; no live
API, unknown-request replay, retained data/backup mutation or GitHub write.
This does not make separate snapshots atomic, authenticate their producers,
establish strict admission or grant operator acceptance. Revert only the
observer guards and tests if needed; preserve saved intents. Original Vault
sync pending outside checkout.

## 2026-10-05: validated manifest digest and independent observer path (offline)

The native management report formerly hashed `MANIFEST.read_bytes()` after a
bounded, non-following validated read, so a replacement pathname could produce
a digest unrelated to the 16 policy items used. `load_manifest_snapshot` now
returns both the validated items and the SHA-256 of those same bytes. Independently,
the fixed-corpus and caller-Pod observers bind their initial digest to their
validated item snapshot; later source checks validate a fresh snapshot instead
of directly hashing an unchecked pathname. A disposable replacement-after-read
test checks the original digest and a linked-path observer negative refuses the
link. Focused policy suites and full local verification passed; a first test
command used two nonexistent unittest module names and was corrected without
changing code. Dedicated Colima observed stopped; no live API, admission,
unknown-request replay, retained data/backup change or GitHub write. This does
not make independent snapshots atomic, authenticate producers or establish
strict admission. Roll back the scoped loader/report/observer changes together
if needed; retain saved intents. Original Vault synchronization pending outside
this checkout.

## 2026-10-05: manifest ancestor traversal (offline)

The bounded manifest reader previously followed a symlink in an ancestor above
its immediate parent. It now opens every parent component from `/` using
directory-relative, non-following descriptors before opening the manifest.
A synthetic ancestor-link negative and the real 16-item manifest pass their
respective rejection/positive checks; 111 focused policy tests passed. This
does not authenticate the checkout, prevent all concurrent path swaps or
same-UID content edits, or prove live admission. The dedicated Colima profile
was observed stopped; no retained intents, data or backups were changed.
Rollback only this traversal and its regression if necessary; keep historical
records. Original Vault sync pending outside checkout.

## 2026-10-05: immediate manifest parent identity (offline)

A symlinked immediate parent of the dedicated manifest previously passed the
bounded file reader and reached source projection. The negative failed first;
the reader now opens that directory without following its final symlink, reads
the file relative to the directory descriptor, and checks the file and parent
entry identities after reading. File metadata also includes ctime in the
before/after check. The real manifest, 110 focused policy tests and full local
verifier passed. This
does not pin higher ancestors, guarantee atomic contents against a same-UID
writer, or authenticate the source/tool. Colima remained stopped; no live
admission, retained data/backup mutation or replay. Roll back only the scoped
reader and regression if needed; keep historical intents. Original Vault sync
pending outside checkout.

## 2026-10-05: bounded dedicated manifest descriptor (offline)

The exact-projection manifest reader now opens a non-following, nonblocking
descriptor and requires a regular file of at most 256 KiB, an exact single
read and stable descriptor metadata before JSON decoding. Synthetic symlink,
oversize and FIFO replacements fail before source projection (the FIFO does
not block waiting for a writer); ordinary and duplicate-key controls remain.
Focused 109 native policy tests and the local verifier passed; Colima was
observed stopped. No live admission, replay or retained data/backup change.
This pins the opened file, not its parent-directory provenance or concurrent
same-size in-place content edits; repository/tool authenticity and strict
admission are still unverified. Roll back only this reader and its fixtures
if the manifest input contract changes; preserve existing intents. Original
Vault sync pending outside checkout.

## 2026-10-05: strict source separators and manifest key identity (offline)

The local source decoder accepted non-JSON Unicode whitespace between otherwise
valid policy objects (or at either edge). Only JSON's four ASCII whitespace
characters now separate documents; a mixed-ASCII separator control passes.
Separately the dedicated native manifest reader could treat a repeated equal
`kind` key (including an escaped alias) as an exact projection. It now refuses
duplicate keys at any depth before comparing against the rendered source.
Both negatives failed before repair. Focused policy 107 tests and full local
verification passed. Dedicated Colima was observed stopped; no live API,
activation, replay, retained data/backup mutation or GitHub write. This does
not authenticate decoder/tool or source bytes, prove strict admission or grant
operator acceptance. Roll back only either scoped decoder/manifest reader and
its test if its actual input contract differs; preserve manifest and saved
intents. Original Vault sync pending outside checkout.

## 2026-10-05: local decoder document framing (offline)

Seven adjacent, individually valid JSON policy objects without whitespace were
previously treated as seven decoded source documents. The local decoder now
requires whitespace between successive objects; newline-separated source and
its manifest projection still pass. The focused negative failed before repair;
106 policy tests and the local verifier passed. Colima was freshly observed
stopped; no live admission, replay, retained data/backup mutation or GitHub
write. This does not authenticate the local decoder or establish strict
admission/operator acceptance. Roll back only the framing guard and regression
if the decoder's actual output contract changes; preserve manifests and saved
intents. Original Vault sync remains pending outside checkout.

## 2026-10-05: nested escaped dry-run key ambiguity (offline)

An independent synthetic successful Pod reply with an unexamined nested key
spelled both literally and with a JSON escape is rejected by the existing
duplicate-key decoder; a distinct-key control passes. The full fake smoke
halts at the first case; 105 native policy tests and the local verifier passed.
Dedicated Colima was observed stopped. No live dry-run, replay, data/backup
mutation or GitHub write. This does not authenticate a response producer,
establish strict admission or grant operator acceptance. Revert only the new
regression if needed; preserve saved intents. Original Vault sync pending
outside checkout.

## 2026-10-05: dry-run success JSON Unicode boundary (offline)

The separate smoke success decoder admitted an escaped lone surrogate in an
otherwise valid JSON object. Reject invalid decoded Unicode anywhere before
comparing a candidate; retain a valid surrogate pair. The parser negative failed
before repair; 104 native policy tests and the full local verifier passed.
Dedicated Colima was freshly observed stopped; no live dry-run, admission,
request replay or retained-data/backup change. This parser boundary does not
authenticate kubectl or establish live admission or operator acceptance.
Rollback only the smoke decoder guard/test; preserve saved intents. Original
Vault sync pending outside checkout.

## 2026-10-05: independent local source decoded Unicode boundary (offline)

The local YAML-to-JSON decoder could return seven otherwise correctly named
policies while an unexamined source spec key contained an escaped lone
surrogate. It now refuses invalid Unicode before rendering native policies;
an escaped valid surrogate pair remains accepted. The focused negative failed
before repair and 103 native tests passed. No lab activation or saved request
replay; dedicated Colima remained stopped. Offline fixtures do not
authenticate local kubectl, prove live admission or grant operator acceptance.
Revert only this local source guard/test if the decoder contract changes;
preserve existing manifests and intents. Original Vault sync pending outside
checkout.

## 2026-10-05: decoded policy response Unicode boundary (offline)

An escaped lone surrogate in a successful scoped JSON response previously
passed the reader despite not representing valid UTF-8 text. The reader now
refuses malformed decoded Unicode anywhere in the object; a valid escaped
surrogate pair remains accepted. The focused negative failed before repair;
102 native policy tests passed. Dedicated Colima was freshly observed stopped;
no live API, dry-run, request replay, retained data/backup mutation or GitHub
write. Synthetic replies cannot authenticate the API or clear strict admission
or operator acceptance. Revert only the scoped Unicode guard/test if actual
API behavior requires a revised contract; preserve saved intents. Original
Vault sync pending outside checkout.

## 2026-10-05: pinned smoke image source ambiguity controls (offline)

Independent fake Deployment replies with duplicate Trino containers, mutable
image tags, uppercase digest or truncated digest are refused by the existing
smoke image reader; the valid dedicated Deployment still passes. The 101-test
native policy suite passed. Colima was observed stopped; no live API, dry-run,
saved-request replay, retained data/backup mutation or GitHub write. These
fixtures cannot authenticate the producer or prove strict admission, current
Trino readiness or operator acceptance. Revert only the independent test if
necessary; preserve previous evidence. Original Vault sync pending outside
checkout.

## 2026-10-05: scoped smoke image source identity (offline)

The smoke image reader previously accepted a pinned Trino image from a
malformed or differently named/namespace Deployment response. It now requires
the `apps/v1` Deployment identity in the dedicated namespace, and refuses a
terminating Deployment before selecting the image. Synthetic mismatches and
the valid fixture pass 101 focused policy tests and the local verifier. Colima
was freshly observed stopped; no live API/dry-run, request replay, retained
data/backup change or GitHub write. This cannot authenticate kubectl or prove
current Trino readiness, live admission, strict gating or operator acceptance.
Revert only the scoped source guard/test if the real read contract differs;
preserve saved intents and reports. Original Vault sync pending outside checkout.

## 2026-10-05: disposable candidate absence and smoke image failures (offline)

The scoped JSON reader rejected kubectl's empty successful missing-Pod reply,
so a fresh disposable candidate could not pass its preflight absence check.
An exact namespace/name/arguments-only exception now accepts that reply as
absence; whitespace, another name/namespace and blank mutations still fail.
Separately, malformed Trino deployment container responses now produce a
controlled PolicyError instead of an uncaught indexing/type exception. Both
negative regressions failed before repair; focused native policy 101 and
candidate observer 59 tests passed, as did the full local verifier. Dedicated
Colima was freshly observed stopped; no live API request, server dry-run,
saved-request replay or retained data/backup mutation. These offline replies
do not authenticate kubectl or prove live admission, strict gate or operator
acceptance. Roll back only the respective narrow reader/image guards if the
actual scoped API contract differs; preserve saved intents and reports.
Original Vault sync pending outside the checkout.

## 2026-10-05: local source decoder diagnostic channel (offline)

A successful seven-policy local decoder reply could contain diagnostic stderr
yet certify an exact source projection. Synthetic warning, whitespace and
non-text stderr now fail closed; clean output remains accepted. The negatives
failed before the guard, then 100 native policy tests and the full local
verifier passed. The dedicated Colima profile was observed stopped; no API
request, activation, saved-request replay or retained data/backup mutation.
Revert only this decoder guard/test if actual local kubectl output needs a
revised contract; preserve the manifest and saved intents. Tool identity,
live admission, strict gate and operator acceptance remain unverified. Original
Vault sync remains pending outside the approved checkout.

## 2026-10-05: type-check shape and scoped stderr controls (offline)

A synthetic policy with malformed `status` or `metadata` previously raised an
uncaught attribute error in the readiness helper; it now remains pending. The
existing warning-list guard already refused empty malformed warning values;
an independent regression pins that behavior. Separately, a successful scoped
JSON policy read or create with diagnostic stderr previously counted as clean;
the dedicated reader now refuses it (including the empty missing-policy read).
The negative cases failed before repair; policy 99 and observer/CLI 128 tests
passed after repair. The first broad regression found 20 observer fixture errors
because older fake subprocess replies omitted stderr; their fake transport
shape was corrected without relaxing the new guard; the full local verifier
passed on rerun. Dedicated Colima was
observed stopped; no API call, policy activation, saved-request replay or
retained data/backup change. The empty-stderr requirement is a conservative
offline assumption; live kubectl warnings and producer authenticity are not
verified, nor are strict admission or operator acceptance. Revert only these
scoped guards/tests if the actual API contract differs; preserve saved intents.
Original Vault sync remains pending outside checkout.

## 2026-10-05: nested native response ambiguity controls (offline)

Independent fake scoped policy JSON responses with duplicate nested keys
(including escaped-key aliases), non-JSON constants, float overflow and nonzero
underflow are refused by the existing bounded reader; an exact zero exponent
control remains accepted. The new negative/positive regression and 96 native
policy tests plus `make verify-local-experiment` passed. Colima was freshly
observed stopped; no live request, policy activation, unknown-request replay,
retained data/backup mutation or GitHub write. No reader change was needed.
This does not authenticate the response producer or establish strict admission
or operator acceptance. Revert only the independent test if necessary; preserve
saved intents. Original Vault sync remains pending outside checkout.

## 2026-10-05: malformed advisory dry-run Unicode (offline)

The candidate observer now bounds both dry-run streams with an explicit UTF-8
encoding check: unpaired Unicode is a typed failed observation with the pending
request preserved rather than a raw encoding exception. Direct denial matching
also refuses malformed Unicode. Synthetic stdout/stderr negatives and the
positive path passed 59 observer tests; `make test-local-policy` passed 95 and
`make verify-local-experiment` passed. The dedicated Colima profile was observed
stopped; no API request, replay, retained data/backup mutation or GitHub write.
Synthetic responses cannot authenticate the server, clear strict admission or
grant operator acceptance. Revert only the observer guard/test if necessary;
preserve pending intents. Original Vault sync remains pending outside checkout.

## 2026-10-05: non-representable local source numbers (offline)

The local source decoder previously accepted valid JSON exponent tokens that
Python rounded to infinity or zero, despite rejecting literal NaN. A seven-item
synthetic inventory reproduced three such cases before repair; the decoder now
refuses overflow and nonzero underflow while retaining ordinary finite floats.
`make test-local-policy` passed 95 tests and `make verify-local-experiment`
passed. Dedicated Colima was observed stopped; no API request, policy
activation, unknown-request replay, retained data/backup mutation or GitHub
write. This does not authenticate the decoder producer, prove live admission or
grant operator acceptance. Revert only the scoped float parser/test if the
local decoder contract changes; preserve the manifest and saved intents.
Original Vault sync remains pending outside the checkout.

## 2026-10-05: local CEL source decoder boundary (offline)

The local kubectl YAML-to-JSON source decoder now checks UTF-8 byte length,
integer-zero exit status, unique JSON keys and object/metadata inventory before
rendering dedicated native policies. Fake malformed, oversized, duplicate-key,
non-object and non-JSON numeric outputs fail with PolicyError. The real local
source still projects the existing manifest. Native policy 94 tests and `make
verify-local-experiment` passed. No API request, policy activation, saved-intent
replay, retained data/backup mutation or GitHub write; Colima remained stopped.
This does not authenticate local tools or live policy responses, clear strict
admission or establish operator acceptance. Roll back only the source decoder
and regression if actual local kubectl output needs a revised contract; retain
the existing manifest and saved intents. Original Vault sync pending.

## 2026-10-05: scoped JSON object shape (offline)

Successful native policy reads and mutations now refuse JSON scalar/list roots;
only the exact dedicated missing-resource empty read represents absence. Fake
null, boolean, number, string and array replies fail with PolicyError instead
of propagating an unexpected shape to callers. Native policy 93 tests passed.
The dedicated Colima profile was observed stopped; no live API request,
unknown-request replay, retained data/backup mutation or GitHub write. This
does not authenticate response producers or clear strict admission/operator
acceptance. Revert only the scoped shape guard/test if necessary; preserve
saved intents. Original Vault sync remains pending outside checkout.

## 2026-10-05: empty native response scope (offline)

Only an exactly empty successful dedicated `get --ignore-not-found` reply now
represents a missing policy. A whitespace-only read or blank create/delete
reply raises PolicyError instead of allowing a missing-resource or mutation
claim. Synthetic positive/negative replies passed 92 native policy tests.
Colima was freshly observed stopped; no live API request, replay, policy
mutation, retained data or backup change. This cannot authenticate responses
or clear strict admission/operator acceptance. Roll back only the scoped
empty-reply guard and test if the actual kubectl contract differs; preserve
saved intents. The first full regression exposed a legacy observer test
expecting empty replies for an unrelated Pod read; its expectation was
restricted to fail closed, without changing observer behavior. The full local
regression then passed. Original Vault sync remains pending outside checkout.

## 2026-10-05: bounded scoped policy JSON reads (offline)

The native policy read helper bounded response characters, not UTF-8 bytes,
and accepted a boolean false subprocess status as success. It now requires an
integer zero and bounded UTF-8 text; malformed JSON and unpaired Unicode fail
with a scoped PolicyError rather than a raw parser/encoding exception. Fake
negative replies and a valid multibyte reply passed 91 policy tests and the
local regression. Dedicated Colima was observed stopped; no live API or saved
request replay. This does not authenticate an API response or clear strict
admission. Roll back only this read guard/test if necessary; preserve saved
evidence and original Vault sync remains pending outside checkout.

## 2026-10-05: bounded Gatekeeper response bytes (offline)

The advisory observer previously counted Unicode characters rather than UTF-8
bytes when bounding a native dry-run reply. A synthetic reply exceeding 2 MiB
in bytes but not characters was admitted to the response parser. It now checks
both text streams as UTF-8 bytes before classification; non-text and unpaired
surrogate replies fail with an unknown pending candidate. The fake 15-case
positive path remains valid. Gatekeeper 19 and native policy 90 tests passed.
Dedicated Colima was observed stopped; no API request, policy mutation, saved
request replay or retained data/backup change. Byte bounds do not authenticate
the producer, clear strict admission or grant operator acceptance. Roll back
only the Gatekeeper bound and regression if needed; preserve historical intents.
Direct original Vault sync remains pending outside checkout.

## 2026-10-05: Gatekeeper shares native success evidence (offline)

The advisory observer accepted a fake allowed Pod reply with the right name
and namespace but an added privileged container, whereas native smoke rejected
it. Gatekeeper now calls the same fixed-corpus success comparator as native
smoke and requires empty stdout for a denial. Fake changed-spec, warning,
multiple-document success and denial-with-stdout responses stop at the affected
candidate with unknown admission; the full 15-case positive path still passes.
Focused Gatekeeper 18 and native policy 90 tests, plus
`make verify-local-experiment` passed. Dedicated Colima was freshly observed
stopped; no live API, saved-request replay, policy mutation or retained
data/backup change. These fixtures do not authenticate the producer, prove
actual API defaulting, clear strict admission or grant operator acceptance.
Rollback is limited to the shared comparator/observer and tests; preserve
saved intents and historical evidence. Original Vault sync remains pending.

## 2026-10-05: typed success exit status (offline)

Separately from denial matching, a synthetic successful reply with boolean
`False` return status was accepted as zero by both the legacy smoke and
Gatekeeper observer. Both now require an actual integer zero before classifying
the reply as allowed. Fake first-request regressions fail/mark unknown; 94
focused tests and `make verify-local-experiment` passed. Dedicated Colima remained stopped; no live API or saved
request replay. This does not authenticate the response or establish strict
admission/operator acceptance. Roll back only the typed-status guard/tests if
necessary; preserve saved evidence. Original Vault sync remains pending.

## 2026-10-05: interrupted native dry-run is not a denial (offline)

Both the legacy smoke and Gatekeeper observer previously treated any nonzero
process return code as a policy denial if stderr matched. A signal-terminated
kubectl (`-9`), missing return code or boolean return code must not establish
admission: both paths now require a positive integer exit status. Fake request
three retains the authentic-looking denial text but records failure/unknown;
92 focused native tests passed before the separate success-path increment.
Dedicated Colima was observed stopped. No live
API request, replay or retained data/backup mutation. This cannot authenticate
the denial producer, establish strict admission or grant operator acceptance.
Roll back only the two exit-status checks/tests if needed; keep saved intents.
Original Vault synchronization remains pending outside the checkout.

## 2026-10-05: malformed native denial output (offline)

The fixed denial matcher raised a raw `TypeError` on non-text stderr rather
than recording a failed dry-run case. It now refuses non-text replies, with a
fake full-smoke regression confirming failure at request three and a recorded
failed row. Native policy tests (88), separate FinOps tests (194; seven
Linux-only skips) and `make verify-local-experiment` passed. Colima was
observed stopped; no live admission, saved-request replay or retained
data/backup mutation. This tests a malformed transport reply, not producer
authenticity, strict admission or operator acceptance. Roll back only the type
guard and test if needed; preserve saved requests. Original Vault sync remains
pending outside checkout.

## 2026-10-05: balanced native denial identifiers (offline)

The fixed smoke matcher accepted fake denial text with a single-quote opener
and double-quote closer (or the reverse) around its policy or binding name.
Four focused negatives failed before repair. The matcher now pairs each name's
quotes independently; ordinary single- and double-quoted denials still pass.
The fake full smoke rejects the malformed denial at request three. Native
policy tests (87) and `make verify-local-experiment` passed. Dedicated Colima was observed stopped; no live API,
saved-request replay, retained data/backup mutation or GitHub write. This
matches only a bounded stderr syntax, not the identity of its producer or
strict admission/operator acceptance. Revert only this matcher/test if actual
kubectl formatting requires revision; preserve saved requests. Original Vault
sync remains pending outside checkout.

## 2026-10-05: fixed Service allocated-address consistency (offline)

A synthetic successful fixed-Service reply claimed a nonempty `clusterIP`
but an explicitly empty `clusterIPs` list; the smoke verifier accepted it
before the negative test and now refuses that contradiction. The previously
supported projected response with only `clusterIPs` remains accepted; a first
attempt to require both fields failed its existing positive test and was
narrowed without weakening the new negative. Native policy tests (86),
FinOps independent focused tests (194, seven Linux-only skips), design-context
and full local regression passed. Dedicated Colima was observed stopped; no
live admission, saved-request replay, retained data/backup mutation or GitHub
write. This is fake-reply consistency, not API authenticity, present readiness,
strict admission or operator acceptance. Revert only the scoped consistency
guard/test if actual dedicated API defaulting requires it; preserve pending
requests. Original Vault sync remains pending outside checkout.

## 2026-10-05: fixed Pod spec/container key boundary (offline)

Four synthetic successful Pod replies carried an unexamined spec or container
key (null or nonempty). The first request advanced before repair; the fixed
comparator now rejects keys outside the examined Pod and container fields.
Known API defaults remain covered by the existing comparisons, but future
defaulted keys require explicit examination. Native policy tests (85) and full
local regression passed.
Dedicated Colima was freshly observed stopped; no live admission, replay,
retained data/backup mutation or GitHub write. This does not authenticate the
reply, prove complete effective-object equality or clear strict/operator
acceptance. Revert only these scoped key guards and regression if live defaulting
contradicts the offline assumption; preserve saved requests. Original Vault
sync remains pending outside checkout.

## 2026-10-05: legacy smoke response envelope (offline)

Four fake successful Pod dry-run replies added unknown top-level or metadata
claims. All advanced past the first Pod before the guard; the bounded legacy
smoke comparison now refuses unexamined keys. The negative failed first, then
84 native policy tests and full local regression passed. The dedicated Colima
profile was observed stopped; no live admission, pending-request replay,
retained data/backup mutation or GitHub write. This does not authenticate the
API or establish full effective-object equality, strict admission or operator
acceptance. Roll back only this response guard/test if needed and preserve
saved requests. Direct original Vault synchronization remains pending.

## 2026-10-05: fixed Service spec key boundary (offline)

Two fake successful fixed-Service replies added an unknown top-level spec key
with null or nonempty content; both passed before a scoped allowlist rejected
them. The negative failed first, then 83 native policy tests and the local
regression passed. This intentionally fails closed on future defaulted Service
spec keys until their effect is examined; known fields and fixed successes
remain accepted. Colima was observed stopped; no live API request, saved-request
replay, retained data/backup change or GitHub write. This does not authenticate
the response, establish full effective-object equality, clear strict admission
or grant operator acceptance. Revert only this Service key guard/test if live
defaulting invalidates the assumption; preserve saved intents. Direct original
Vault sync remains pending outside checkout.

## 2026-10-05: ServicePort presence-only routing controls (offline)

The existing ServicePort key guard also rejects unsubmitted `hostIP` and
`hostPort` when their values are null, and an unsubmitted `containerPort` key;
the independent fake-success negatives now pin this presence-only behavior.
Focused and full native policy tests (82) passed. Dedicated Colima was observed
stopped: no API dry-run, saved-request replay, retained data/backup change or
GitHub write. Fake replies cannot authenticate a producer, prove complete
effective-object equality, clear strict admission or grant operator acceptance.
Revert only these test extensions if needed; preserve saved intents. Direct
original Vault sync remains pending outside checkout.

## 2026-10-05: fixed ServicePort reply key boundary (offline)

Fake successful fixed Service replies carrying Pod-only `hostIP` or `hostPort`
on a ServicePort previously passed the second request; after restricting the
reply to known ServicePort keys, both fail there. The focused negatives failed
before repair; 82 native policy tests passed afterward. Colima was observed
stopped; no live API, saved-request replay, retained data/backup mutation or
GitHub write. This guard does not authenticate the reply or prove full effective
object equality, strict admission or operator acceptance. Roll back only this
key guard/test if needed, preserving saved intents. Original Vault sync pending
outside checkout.

## 2026-10-05: independent IPv4 boundary controls (offline)

The unchanged fixed-Service smoke rejects both cluster address fields at
`0.255.255.255` as reserved and `127.255.255.254` as loopback; focused native
policy tests (81) passed. These extend separate previously established guards,
not CIDR allocation evidence. Dedicated Colima was observed stopped; no live
admission, saved-request replay or retained data/backup mutation. Producer
authenticity, complete effective-object equality, strict admission and operator
acceptance remain unverified. Revert only these two fixture extensions if
necessary; retain saved intents. Direct original Vault sync remains pending.

## 2026-10-05: fixed Pod stop-signal reply control (offline)

Three fake successful Pod replies added an unsubmitted container `stopSignal`
and advanced to the third request before the scoped check. A focused negative
failed first; the comparator now refuses a non-null signal, retaining absent
and null defaults. Native policy tests (81) passed. Colima was observed stopped;
no live admission, saved-request replay or retained data/backup mutation.
Feature-gate/API defaulting, producer authenticity, complete effective-object
equality, strict admission and operator acceptance remain unverified. Roll back
only this reply check/test if necessary and preserve saved intents. Direct
original Vault sync remains pending outside checkout.

## 2026-10-05: Service reply address-family evidence (offline)

Two fake successful fixed-Service replies claimed a ClusterIP in one address
field without `ipFamilies`; both advanced before the fail-closed guard. The
three older positive fixtures were made explicit about the matching family;
an address-free reply still permits omitted family. Focused policy tests (80)
and local regression passed. This assumes an allocated Service reply declares
its family; actual API defaulting remains unverified. Colima remained stopped;
no live admission, saved request replay, retained data/backup mutation or
GitHub write. This does not prove response authenticity, CIDR allocation,
effective-object equality or operator acceptance. Revert only the family guard
and fixtures if live evidence contradicts the assumption; preserve saved
intents. Direct original Vault sync remains pending outside checkout.

## 2026-10-05: embedded-IPv4 IPv6 ClusterIP refusal (offline)

Four fake successful fixed-Service replies claimed IPv4-compatible or
IPv4-translated IPv6 addresses in either cluster address field. All four
advanced past the Service case before the scoped guard; native policy tests
(79) and `make verify-local-experiment` passed afterward. Plain IPv6 and
IPv4 fixture successes remain accepted. Colima was observed stopped; no live
API, saved-request replay, retained data/backup change or GitHub write. This
does not verify the Service CIDR, response provenance, effective-object
equality, strict admission or operator acceptance. Roll back only the two
IPv6 prefix checks and test if necessary; preserve historical evidence and
pending requests. Direct original Vault sync remains pending outside checkout.

## 2026-10-05: reserved IPv4 Service address refusal (offline)

Six fake successful fixed-Service replies claiming addresses in 0/8 or 240/4
advanced past the Service request before repair. The bounded address check now
rejects these special-use blocks, including limited broadcast, without broadly
rejecting documentation/test IPv4 addresses. Focused negatives failed before
the change; 78 native policy tests, 880 Go tests, vet and local regression
passed. Dedicated Colima was observed stopped; no live admission or saved
request replay, retained data/backup mutation or GitHub write. This does not
validate the actual Service CIDR, authenticate API replies, prove effective
object equality, clear strict admission or grant operator acceptance. Roll
back only the address guard/test if needed; preserve saved intents. Direct
original Vault sync remains pending outside checkout.

## 2026-10-05: limited-broadcast Service address refusal (offline)

The fixed Service smoke accepted a fake successful reply claiming
`255.255.255.255` in either cluster address field; both focused negatives
failed before repair. The scoped comparator now refuses this IPv4 limited
broadcast destination, which `ipaddress` does not mark multicast or
unspecified. The native 77-test suite and local regression passed. Colima was
observed stopped; no live API, request replay, retained data/backup mutation
or GitHub write. This does not verify actual CIDR allocation, API authenticity,
full effective-object equality or strict/operator acceptance. Revert only the
guard/test if needed; preserve saved intents. Original Vault sync pending.

## 2026-10-05: host-local dry-run ClusterIP refusal (offline)

Eight fixed-Service fake successful replies claimed IPv4/IPv6 loopback or
link-local addresses in `clusterIP` or `clusterIPs` despite matching family.
The focused negative failed in all eight cases before a scoped address guard;
the 76-test native policy suite and local regression passed after repair.
Ordinary IPv4/IPv6 positives remain accepted. Dedicated Colima was observed
stopped; no live API, saved-request replay, retained data/backup mutation or
GitHub write. The address classification is an offline assumption, not a
service-CIDR allocation check, producer authentication, full effective-object
equality, strict admission or operator acceptance. Revert only this scoped
guard/test if necessary, preserving pending intents and historical evidence.
Direct original Vault synchronization remains pending outside checkout.

## 2026-10-05: non-unicast dry-run ClusterIP refusal (offline)

Eight fake successful fixed Service replies claiming an unspecified or multicast
IPv4/IPv6 address in either cluster address field passed before repair. The
focused negative failed in all eight cases; the bounded address comparator now
refuses these destinations while ordinary IPv4/IPv6 successes still pass. Native
policy suite (75), full local regression, Go tests/vet, design-context and
secret-filename checks passed. Dedicated Colima was observed stopped; no live
API, request replay, retained data/backup mutation or GitHub write. This does
not validate the actual service CIDR, authenticate a producer, prove full
effective-object equality or clear strict/operator acceptance. Revert only the
scoped address guard/test if needed; preserve pending intents. Direct original
Vault synchronization remains pending outside checkout.

## 2026-10-05: IPv4-mapped IPv6 dry-run address refusal (offline)

Three test-owned fixed ClusterIP success replies with IPv4-mapped IPv6
addresses advanced beyond the Service case before repair (after restricting
the fixture to the allowed request). The bounded address comparator now refuses
these mapped addresses while retaining ordinary IPv4/IPv6 positives. Native
policy suite (74), Go CLI tests (878)/vet, `make verify-local-experiment`,
design-context, secret-filename and diff checks passed. No live
API call or saved-request replay occurred: this does not authenticate the
producer, verify allocation/defaulting, establish full effective-object
equality or clear strict/operator acceptance. Roll back only the address guard
and its regression if necessary; preserve saved intents. Direct original Vault
sync remains pending outside checkout.

## 2026-10-05: unscoped Service ClusterIP replies (offline)

Three fake successful replies with IPv6 interface zone identifiers advanced
beyond the Service request because Python's `ipaddress` accepted scoped IPv6.
Focused negatives failed first; the fixed Service success comparator now
refuses zone identifiers in claimed cluster addresses while preserving plain
IPv4/IPv6 positives. Native policy suite (73) and full local regression passed.
Colima was observed stopped; no live API, request replay or retained data/backup
mutation. This does not authenticate a response, establish full effective-object
equality or clear strict/operator acceptance. Roll back only the scoped address
guard and test if necessary; preserve saved intents. Original Vault sync pending.

## 2026-10-05: malformed success-text refusal (offline)

A test-owned successful reply containing a raw unpaired Unicode surrogate
raised `UnicodeEncodeError` before the smoke could report a policy mismatch.
The focused negative failed first; success parsing now refuses unencodable
text through the ordinary dry-run mismatch path. Native policy suite (72)
and full local regression passed. Colima was observed stopped: no live admission, saved-request replay,
retained data/backup mutation or GitHub write. This is defensive handling of a
malformed fake response, not evidence of live API producer authenticity,
complete effective-object equality or strict/operator acceptance. Roll back
only the scoped text guard and test if needed, preserving saved intents.
Direct original Vault sync remains pending outside checkout.

## 2026-10-05: bounded single-line denial wrapper (offline)

A fake policy denial prefixed on the same line by an unrelated RBAC claim,
BadRequest wrapper or warning passed the prior matcher; three focused negatives
failed before repair. Only the bare dedicated denial or the observed kubectl
`Error from server (Forbidden): ` wrapper now counts. Native policy tests (71)
and `make verify-local-experiment` passed. Colima was observed stopped; no live
dry-run or request replay, retained data/backup mutation or GitHub write. The
strict wrapper is an offline assumption: if live kubectl emits another valid
format, capture and evaluate it rather than widening to arbitrary prefixes.
This does not authenticate the producer, clear strict admission or grant
operator acceptance. Roll back only matcher/test if needed; preserve saved
intents. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: fixed Pod-level resource reply control (offline)

An unsubmitted nonempty Pod-level `spec.resources` in a fake successful dry-run
advanced beyond the first smoke case before repair; the focused negative failed
first. The fixed Pod reply now refuses added Pod-level requests/limits and
malformed values, while allowing absent/null/empty defaults. Native policy
suite (70) and local regression passed. Dedicated Colima was observed stopped;
no live API, pending-request replay, retained volume/backup mutation or GitHub
write. Empty defaulting is an offline assumption, not authenticated API evidence
or complete effective-object equality. Strict admission and operator acceptance
remain open. Revert only the scoped guard/test if necessary, preserving saved
intents. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: single-line policy denial evidence (offline)

The fixed native smoke previously counted a recognized admission denial even
when stderr included an unrelated error before or after it, or repeated the
denial. Three test-owned negatives failed before repair. The bounded denial
matcher now allows a kubectl single-line prefix and optional terminal newline,
but no second diagnostic line. A fake full smoke stops on its third request and
records failure; 69 native tests, local regression, design-context and secret
filename checks passed. Dedicated Colima was observed stopped; no live request,
unknown-request replay, retained data/backup mutation or GitHub write. This
cannot authenticate stderr's producer or verify live kubectl formatting;
strict admission and operator acceptance remain open. If actual kubectl emits
multiple legitimate lines, investigate the captured format before changing the
matcher, rather than treating an ambiguous denial as a pass. Revert only this
matcher/test if necessary, preserving saved intents. Original Vault sync remains
pending outside checkout.

## 2026-10-04: fixed container restart-rule reply control (offline)

A fake successful Pod reply with unsubmitted nonempty `restartPolicyRules`
advanced to later smoke cases before repair. The fixed comparator now refuses
such rules while accepting absent/empty API defaults; native 68-test policy
suite and local regression passed. Dedicated Colima was observed stopped; no
live API, replay, retained data/backup mutation or GitHub write. This does not
authenticate a producer, verify live defaulting, establish full effective-object
equality, clear strict admission or grant operator acceptance. Roll back only
the scoped comparator/test if necessary; preserve pending intents. Original
Vault sync remains pending outside checkout.

## 2026-10-04: default tolerations through complete smoke (offline)

The two exact standard 300-second NoExecute defaults, in either order, now
exercise the entire 15-case legacy smoke path rather than only the reply
comparator. Both pass while the existing invalid/duplicate toleration negatives
remain rejected; native policy tests passed (67). Dedicated Colima was observed
stopped. No live admission, saved-request replay, retained data/backup mutation
or GitHub write. Defaulting is still an offline assumption; this does not
authenticate an API producer, establish full effective-object equality, clear
strict admission or grant operator acceptance. Revert only the added test if
needed; preserve pending intents. Original Vault sync remains pending outside
checkout.

## 2026-10-04: bounded default Pod tolerations (offline)

The fixed Pod's successful fake dry-run previously accepted an added scheduling
toleration. A negative advanced to later cases before repair. The comparison
now permits absent/empty tolerations or at most one each of the exact standard
300-second NoExecute not-ready/unreachable defaults; arbitrary, duplicate,
changed-effect and boolean-duration tolerations fail. This is an offline
defaulting assumption, not a live API observation. Native policy tests and
local regression passed. Dedicated Colima was observed stopped; no request
replay, retained data/backup mutation or GitHub write. Producer authenticity,
full effective-object equality, strict admission and operator acceptance remain
open. Revert only this helper and regression if necessary, preserving saved
intents. Direct original Vault sync remains pending outside the checkout.

## 2026-10-04: fixed-name dry-run reply metadata (offline)

A fake successful Pod/Service reply could add an unsubmitted `generateName`
while keeping the submitted fixed `name`. Both reached later smoke cases before
the negative regression; the dry-run identity check now refuses the added
prefix and still allows absent/empty API defaults. The first focused invocation
used the wrong test class; the corrected negative failed before repair. Native
policy suite (66 tests) and full local regression passed. Colima was observed
stopped; no live API, saved-request replay, retained data/backup mutation or
GitHub write. This does not authenticate the producer, establish full effective
object equality, clear strict admission or grant operator acceptance. Roll back
only this guard/test if needed; preserve historical intents. Direct original
Vault synchronization remains pending outside the checkout.

## 2026-10-04: typed submitted container resource reply (offline)

A test-owned submitted resource value of integer `1` was accepted as boolean
`true` in a fake successful Pod reply because Python dict equality conflated
their values. The focused negative failed first; the scoped comparator now uses
its existing recursive typed JSON equality for submitted resources. The fixed
smoke corpus uses string quantities and was not changed. Native policy suite
(65 tests), full local regression and Go suite/vet passed. Dedicated Colima was
observed stopped; no live API, saved-request replay, retained data/backup
mutation or GitHub write. This direct helper regression does not establish
live resource defaulting, authenticated producers, full effective-object
equality, strict admission or operator acceptance. Roll back only the resource
comparison/test if necessary; preserve saved evidence. Original Vault sync
remains pending outside checkout.

## 2026-10-04: fixed Pod OS reply constraint (offline)

Four fake successful replies introduced an unsubmitted `spec.os` (Windows,
Linux, null or boolean) and advanced through the fixed smoke corpus before
repair. The fixed Pod check now requires this field to remain absent; the
negative failed first and 64 native policy tests passed afterward. This is a
conservative assumption about the fixed corpus, not a claim about live API
defaulting or all valid Pod submissions. Colima was observed stopped; no live
API, unknown-request replay, retained data/backup mutation or GitHub write.
Producer identity, effective-object equality, strict admission and operator
acceptance remain unverified. Roll back only the scoped guard/test if needed;
preserve saved intents. Direct original Vault sync remains pending outside
this checkout.

## 2026-10-04: fixed Pod preemption reply control (offline)

Three fake successful Pod replies introduced `preemptionPolicy: Never`, a
boolean or explicit null and advanced past the first case before repair. The
fixed-corpus check now requires the absent or Kubernetes default
`PreemptLowerPriority` value; the focused negative failed before repair and
63 native policy tests passed afterward. The dedicated Colima profile was
read-only observed stopped. No live API request, saved-request replay,
retained data/backup mutation or GitHub write. This default is an offline
assumption, not a live defaulting observation or proof of producer identity,
complete effective-object equality, strict admission or operator acceptance.
Rollback: revert only the scoped guard/test, retaining pending intents.
Original Vault synchronization remains pending outside this checkout.

## 2026-10-04: fixed Pod container restart/resize reply controls (offline)

Two fake successful replies introduced unsubmitted container `restartPolicy`
or `resizePolicy` and reached later smoke cases before repair. The fixed-corpus
reply check now refuses both, while accepting absent and empty resize defaults.
The negative failed before repair; native policy tests (62) passed afterward.
Dedicated Colima was read-only observed stopped; no API request, unknown-request
replay, retained data/backup mutation or GitHub write. This does not authenticate
the producer, verify live defaulting, prove full effective-object equality,
strict admission or operator acceptance. Revert only this guard/test if needed;
preserve pending intents. Original Vault sync remains pending outside checkout.

## 2026-10-04: explicit Pod host binding helper (offline)

A direct submitted-port fixture with an explicit hostPort/hostIP failed the
comparator's positive check before repair: the helper incorrectly required the
portless smoke corpus's inert host defaults even for submitted host bindings.
It now preserves explicit submitted bindings by exact typed comparison, while
still allowing only inert defaults when the input omits them. Four changed or
malformed binding replies are refused; 61 native policy tests and
`make verify-local-experiment` passed. Dedicated Colima was observed stopped;
no live API, saved-request replay, retained data/backup mutation or GitHub
write. This is helper coverage, not a new fixed smoke case or proof of live
defaulting, producer identity, full effective-object equality, strict admission
or operator acceptance. Revert only the helper/test if needed; keep historical
intents. Direct original Vault sync remains pending outside this checkout.

## 2026-10-04: Pod port comparator boundary (offline)

The fixed smoke Pod has no ports, but the comparator helper previously accepted
changed containerPort/protocol, injected hostIP and boolean hostPort when a
submitted Pod did declare a port. A direct synthetic submitted-port regression
failed in all four cases before repair. The helper now preserves submitted port
fields and permits only inert absent/default hostPort, hostIP and protocol
values. Native policy suite (60 tests) passed. Dedicated Colima was read-only
observed stopped; no live API, unknown-request replay, retained data/backup
mutation or GitHub write. This extends helper safety, not the fixed corpus or
live defaulting evidence; producer identity, full effective-object equality,
strict admission and operator acceptance remain open. Revert only this helper
and test if needed; preserve pending requests. Original Vault sync pending.

## 2026-10-04: fixed Pod termination message controls (offline)

Two fake successful replies redirected the container termination-message path
or enabled log fallback; both reached later smoke cases before the fixed reply
check refused them. Absent and normal API defaults remain accepted. An initial
focused invocation named the wrong test class; the corrected negative failed
before repair and the native 59-test suite passed after. The dedicated Colima
profile was observed stopped; no live API, unknown-request replay, retained
data/backup mutation or GitHub write. This scoped check does not authenticate
the API, prove complete effective-object equality or verify live defaulting;
strict admission and operator acceptance remain open. Revert only this guard
and test if necessary, retaining pending intents. Direct original Vault sync
remains pending outside the approved checkout.

## 2026-10-04: fixed Pod resource and scheduling controls (offline)

Two unsubmitted successful Pod replies introduced a named resource claim or a
scheduling gate and reached later smoke requests before the scoped reply check
refused them. Absent and empty API defaults remain accepted. The initial focused
command named the wrong test class; the corrected negative failed twice before
repair and the native 58-test suite passed afterward. This does not authenticate
the API, prove complete effective-object equality or verify live defaulting.
`make verify-local-experiment`, design-context, secret-filename and diff checks
passed. The dedicated Colima profile was observed stopped; no live admission, unknown
request replay, retained data/backup mutation or GitHub write. Strict admission
and operator acceptance remain open. Revert only this guard/test if necessary;
preserve pending requests. Direct original Vault sync remains pending outside
the approved checkout.

## 2026-10-04: successful Pod lifetime and priority (offline)

Three fake successful replies changed the fixed Pod's termination grace period
or scheduling priority and advanced to later smoke requests before repair. The
scoped comparator now accepts absent or expected API defaults (30 seconds and
integer zero), refusing changed or malformed values. Native 57 tests and full
local regression passed. Dedicated Colima was observed stopped; no live API,
pending-request replay, retained data/backup mutation or GitHub write. Live
defaulting, producer identity, complete object equality, strict admission and
operator acceptance remain open. Revert only this comparator/test if necessary;
preserve saved intents. Direct original Vault sync remains pending outside the
approved checkout.

## 2026-10-04: successful Pod probe injection (offline)

Three fake successful Pod replies added an unsubmitted liveness, readiness or
startup exec probe. Before repair the smoke advanced to a later request; the
fixed-corpus comparator now refuses each on the first reply. Native 56 tests,
full local regression, Go suite/vet and design-context passed. Dedicated Colima
was observed stopped; no live API, pending-request replay, retained data/backup
mutation or GitHub write. An absent probe is assumed for this fixed Pod; live
defaulting, producer identity, full effective-object equality, strict admission
and operator acceptance remain unverified. Roll back only this comparator/test
if needed, preserving saved intents. Original Vault synchronization is pending
outside the approved checkout.

## 2026-10-04: legacy success metadata controls (offline)

The fixed smoke accepted fake successful Pod and Service replies with newly
injected annotations or finalizers, even though neither was submitted. It now
requires absent or empty annotations/finalizers and retains both inert empty
defaults. Four negative fixtures reproduced acceptance before repair; native
55 tests and the local regression passed after repair. Dedicated Colima was
observed stopped; no live admission, request replay, retained data/backup
mutation or GitHub write. This fixed-corpus comparison cannot authenticate
the producer, prove full effective-object equality, clear strict admission or
establish operator acceptance. Revert only the scoped check and tests if
necessary; preserve pending intents. Original Vault sync remains pending
outside this checkout.

## 2026-10-04: legacy success selector-label and port-name boundary (offline)

The fixed smoke previously accepted fake successful Pod/Service responses with
an extra unsubmitted label, or a named port on the fixed unnamed Service.
Either can change selector-based routing while the submitted identity survives.
The reply check now requires exact submitted labels and refuses a newly named
port (absent/empty remains inert). Native 53 tests and the local regression
passed. Dedicated Colima was observed stopped; no API requests, saved-request
replay, retained data/backup mutation or GitHub write. This conservative fixed
corpus check cannot authenticate the API, validate live defaulting, establish
effective-object equality, clear strict admission or grant operator acceptance.
Roll back only the scoped comparison/tests if necessary, preserving saved
intents. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: legacy Service external routing defaults (offline)

Two fake successful fixed ClusterIP replies added `externalTrafficPolicy: Local`
or a nonzero `healthCheckNodePort` and passed the previous comparison. The smoke
now refuses both after the second request, while absent, inert `Cluster` and
integer zero defaults still pass. Native policy suite (52 tests) and local
regression passed. Dedicated Colima was observed stopped; no live admission,
unknown-request replay, retained data/backup mutation or GitHub write. These
offline replies do not authenticate the API, verify live defaulting, prove full
effective-object equality, clear strict admission or establish operator
acceptance. Revert only the scoped comparator/tests if needed, preserving
historical intents and evidence. Direct original Vault sync remains pending
outside the approved checkout.

## 2026-10-04: legacy Service address/family agreement (offline)

Five fake successful Service replies with malformed cluster addresses or an
IPv4/IPv6 family contradiction advanced through the fixed corpus before repair.
The reply comparator now parses present cluster addresses and, when a single
family is declared, requires that family to match; it retains absent and
single-family defaulted forms. Native policy suite (51 tests), full local
regression, Go suite and vet passed. Dedicated Colima was observed stopped;
no live API request, pending-request replay, retained data/backup mutation or
GitHub write. Address allocation and API authenticity, effective-object equality,
strict admission and operator acceptance remain unverified. Roll back only the
scoped comparator/tests if needed; preserve old intents. Original Vault sync
pending outside checkout.

## 2026-10-04: legacy Service cluster address agreement (offline)

The fixed-corpus comparator previously accepted successful fake replies where
`clusterIP` disagreed with the singleton `clusterIPs`, including a headless or
empty scalar paired with a nonempty list. All three negatives advanced through
the smoke corpus before repair; they now fail on the second request, while
matching addresses and absent/defaulted address fields remain supported.
Native 49 tests and the local regression passed. The dedicated Colima profile
was observed stopped; no live API request, saved-request replay, retained data
or backup mutation, or GitHub write. This does not validate IP allocation or
family, authenticate the response, establish effective-object equality, clear
strict admission or grant operator acceptance. Roll back only the scoped
comparison and two regressions if needed; preserve prior evidence and intents.
Direct original Vault synchronization remains pending outside checkout.

## 2026-10-04: legacy Service address-list reply controls (offline)

The fixed single-stack ClusterIP dry-run comparator accepted fake successful
replies with two IP families (including duplicates), an unknown family, or
multiple allocated cluster IPs. Both negative groups reached later requests
before repair. It now permits absent or one IPv4/IPv6 family and absent, empty
or singleton non-headless `clusterIPs`, rejecting multi-address and malformed
list shapes. Positive defaulted single-family and single-address fixtures pass.
This checks reply consistency, not allocation validity or the matching of an
address to its family. Native 47 tests and local regression passed. The first
regression attempt failed because a positive fixture also modified denied
Services; the fixture was scoped to the allowed case and the rerun passed.
Dedicated Colima was read-only observed stopped; no live dry-run, unknown
request replay, retained data/backup mutation or GitHub write. Live API
defaulting, authenticated producer, full effective-object equality, strict
admission and operator acceptance remain unverified. Roll back these two
scoped comparator checks and regressions only, preserving historical evidence.
Direct original Vault sync remains pending outside the approved checkout.

## 2026-10-04: legacy Service dual-stack reply control (offline)

Two fake successful ClusterIP Service replies introduced `ipFamilyPolicy` as
`RequireDualStack` or `PreferDualStack`; both advanced through the fixed smoke
corpus before repair. The local comparator now accepts an absent or default
`SingleStack` policy, refusing both injected dual-stack modes at the second
request. Focused native suite (44) and `make verify-local-experiment` passed.
Dedicated Colima was observed stopped; no live dry-run, unknown-request replay,
retained data/backup mutation or GitHub write. The fixed-corpus defaulting
assumption has not been rechecked on the live API; this does not authenticate
the producer, prove complete effective-object equality, clear strict admission
or establish operator acceptance. Revert only this comparator/test if needed;
retain historical evidence and pending intents. Direct original Vault sync
remains pending outside checkout.

## 2026-10-04: legacy dry-run identity and independent Service class (offline)

Six fake successful responses carrying a persisted-object UID, resource version,
generation, creation timestamp, owner or nonempty status previously advanced
through the fixed smoke corpus. The success path now refuses these claims and
accepts empty dry-run identity defaults. Separately, the fixed ClusterIP
Service's successful reply could gain an unsubmitted loadBalancerClass or
allocateLoadBalancerNodePorts flag; both now fail after the second request.
Each negative was reproduced before repair; a separate Service identity
regression confirms the same guard also applies to the second case. Native
43-test suite and
`make verify-local-experiment`, design-context, secret-filename and diff checks
passed. Dedicated Colima was observed stopped; no live admission, unknown
request replay, retained volume/backup mutation or GitHub write. The dry-run
identity check is a reply-consistency guard, not proof that the producer did
not persist an object. Defaulted live Kubernetes responses were not rechecked;
API authenticity, full effective-object equality, strict admission and
operator acceptance remain open. Roll back only the two scoped guards and
regressions if needed, preserving saved intents and reports. Direct original
Vault sync is pending outside this checkout.

## 2026-10-04: legacy success JSON type aliases (offline)

Two independent fixed-corpus reply boundaries: Pod security-context values
now compare JSON types recursively (a boolean cannot impersonate a submitted
integer or vice versa), and the Service's optional zero nodePort rejects a
boolean false while continuing to allow absent, null or integer zero. Fake
success replies previously advanced to subsequent smoke requests; both now
stop after their respective first reply. The native 40-test suite and local
regression passed. The dedicated Colima profile was observed stopped, with no
live admission, saved-request replay, retained data/backup mutation or GitHub
write. This is not producer authentication, full effective-object equality,
strict admission or operator acceptance. Revert only the comparator/tests if
necessary, preserving saved intents. Direct original Vault sync pending outside
this checkout.

## 2026-10-04: legacy Pod security-context additions (offline)

Five fake successful Pod replies added an unsubmitted fsGroup, supplemental
group, sysctl, SELinux or Windows security control. The old comparator advanced
past the first fake reply; the fixed comparator requires the submitted Pod
security-context keys exactly and rejects each addition immediately. Native
38-test suite passed. Dedicated Colima was observed stopped; no live API,
unknown-request replay or retained data/backup change. Assumption: this fixed
Pod does not need extra API-defaulted Pod security-context keys; live defaulting
has not been rechecked. This subset check does not authenticate the API, prove
full effective-object equality, clear strict admission or grant operator
acceptance. Revert only the scoped comparator/test if needed; retain old
intents and reports. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: legacy success scope label binding (offline)

A fake successful Pod or Service reply changed the submitted scope label while
retaining API version, kind, name, namespace and spec. Both advanced to later
fake cases before repair; the smoke now requires every submitted label to be
preserved (extra API labels remain possible). The native 37-test suite passed.
The dedicated lab was observed stopped; no live API request, unknown-request
replay or retained data/backup change. This checks a fixed input label only;
it is not API producer authentication, complete effective-object equality,
strict admission or operator acceptance. Revert only this label comparison and
test if needed; preserve old intents. Direct Vault sync remains pending.

## 2026-10-04: legacy Pod topology and overhead reply controls (offline)

Two test-owned successful Pod replies injected topology spread scheduling or
resource overhead. Both advanced to the third fake request before repair; the
fixed-corpus comparator now refuses them after the first. Native policy tests
(36) and `make verify-local-experiment` passed. Dedicated Colima was observed
stopped; no live admission, unknown-request replay, retained data/backup
mutation or GitHub write. Assumption: absent or empty topology and overhead
are inert for this fixed Pod; live defaulting was not rechecked. These subset
checks do not authenticate the producer, prove complete effective-object
equality, clear strict admission or grant operator acceptance. Revert only the
guard/test if needed, preserving prior evidence and intents. Original Vault
sync remains pending outside this checkout.

## 2026-10-04: legacy Pod lifecycle and Service traffic hints (offline)

Two fake successful Pod replies injected an active deadline or readiness gate;
two independent Service replies injected traffic distribution or a per-port
application protocol. All four advanced to later fake smoke cases before the
fixed-corpus comparison refused them after the first Pod or second Service
request. The native 35-test suite, `make verify-local-experiment`, design-context,
secret-filename and diff checks passed. Dedicated Colima was read-only
observed stopped; there was no live admission, pending-request replay, retained
data/backup mutation or GitHub write. These subset checks do not authenticate
the API, prove full effective-object equality, clear strict admission or grant
operator acceptance. Revert only these guards/tests if necessary; preserve
historical intents and evidence. Direct original Vault sync remains pending
outside this checkout.

## 2026-10-04: legacy Service internal-routing reply controls (offline)

Four separate fake successful replies added client-IP affinity, affinity
configuration, not-ready endpoint publication or Local internal traffic routing
to the fixed ClusterIP Service. Each reached later smoke cases before repair;
the bounded comparator now refuses them after the second request and tolerates
only the inert Kubernetes defaults. Native suite (33 tests), design-context and
secret-filename checks passed. Lab remained stopped; no live request, retained
data/backup mutation or unknown-request replay. This comparison does not
authenticate the producer or establish full effective-object equality, strict
admission or operator acceptance. Revert only the guard/test if necessary and
preserve old evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: legacy Pod DNS and hostname reply controls (offline)

Six test-owned successful replies changed DNS config, hostname, subdomain,
FQDN flag, service-link injection or host-user namespace without touching the
previously compared fields. Each reached later fake cases before repair; the
fixed-corpus comparator now refuses them after the first request, allowing only
inert Kubernetes defaults. Native policy tests (32) passed. Colima was observed
stopped; no live request, unknown-request replay or retained data/backup change.
This is neither complete effective-object equality nor authenticated admission,
strict-gate or operator acceptance. Revert only this comparison/test if needed;
keep historical evidence and pending intents. Direct original Vault sync remains
pending outside the checkout.

## 2026-10-04: legacy Service external-routing reply controls (offline)

Three test-owned successful Service replies added `externalName`,
`loadBalancerIP` or an unrestricted `loadBalancerSourceRanges` while preserving
the checked Service type and ports. Before repair each reached later fake smoke
cases; the fixed-corpus comparator now refuses them after the second request.
The 31 native policy tests, `make verify-local-experiment`, design-context,
secret-filename and diff checks passed. The dedicated Colima profile was observed
stopped; no live request, pending-intent replay, retained volume/backup change
or GitHub write. This subset check neither authenticates the API nor establishes
full effective-object equality, strict admission or operator acceptance.
Revert only the comparator and regression if needed, preserving saved requests
and historical evidence. Direct original Vault sync remains pending outside
the checkout.

## 2026-10-04: legacy container mount and input reply controls (offline)

Three fake successful Pod replies added a volume mount, a volume device or
`stdinOnce: true` to the fixed allowed container. Each advanced to later smoke
cases before repair; the fixed-corpus comparator now refuses them after the
first request. Native tests (30) passed. The dedicated Colima profile was
observed stopped; no live admission, unknown-request replay or retained
volume/backup mutation. This does not prove full effective-object equality,
producer authenticity, strict admission or operator acceptance. Roll back only
the comparator/test if needed, preserving old intents and reports. Direct
original Vault synchronization remains pending outside checkout.

## 2026-10-04: legacy smoke container port and security additions (offline)

Three fake successful Pod replies added an otherwise unsubmitted container
port, container runAsUser or seccomp profile. Each reached later cases before
repair. The fixed-corpus allow comparator now requires the same port count and
refuses added container security controls except explicit `privileged: false`.
The 29 native policy tests and local regression passed. The lab remained
stopped; no API request or saved unknown intent was replayed. This is a
fixed-subset comparison, not complete effective-object equality, producer
authentication, strict admission or operator acceptance. Revert only this
guard and its test if necessary; preserve pending requests and backups.
Original Vault synchronization remains pending outside this checkout.

## 2026-10-04: legacy container execution reply controls (offline)

Five fake successful Pod replies added arguments, working directory, lifecycle
hook, stdin or TTY while preserving previously checked fields. Before repair
each advanced to the third fake request; the fixed-corpus comparator now
rejects them after the first. The native policy suite passed (28 tests), as
did `make verify-local-experiment`. This comparison permits omitted or inert
Kubernetes defaults, but is not full effective-object equality. Dedicated
Colima was observed stopped; no live API, unknown-request replay, retained
request/volume/backup mutation or GitHub write. API authenticity, strict gate
and operator acceptance remain unverified. Roll back only the comparison and
test if necessary; preserve saved intents. Original Vault sync remains pending
outside this checkout.

## 2026-10-04: legacy Pod scheduling and environment reply controls (offline)

Eight test-owned successful Pod replies injected node selector, affinity,
scheduler, priority class, runtime class, container environment or secret-backed
environment while preserving previously checked fields. Each advanced to the
third fake request before repair; the fixed-corpus comparator now rejects each
after the first request. The initially constructed environment fixture also
mutated a Service and raised KeyError; corrected it before confirming the eight
negative failures. Kubernetes may add default tolerations, so arbitrary
tolerations remain outside this comparison rather than breaking live defaulting.
27 native tests and `make verify-local-experiment` passed. Dedicated Colima was
observed stopped; no live API request, unknown-request replay, retained data or
backup change. This does not prove complete effective-object equality, API
authenticity, strict gate or operator acceptance. Revert only the comparator and
regression if needed; preserve historical pending intents. Original Vault sync
remains pending outside the approved checkout.

## 2026-10-04: legacy Pod identity and placement reply controls (offline)

Four test-owned successful replies injected a service account, node binding,
image pull secret or alternate DNS policy; each advanced through the fake smoke
before repair. The fixed-corpus success check now rejects these changes, while
allowing the inert Kubernetes default service account and ClusterFirst DNS.
The regression failed first and then the 26 native tests and local regression
passed. The dedicated Colima profile was observed stopped. No live API call,
request replay, retained report/volume/backup mutation or GitHub write. This
is not full effective-object equality, authenticated admission, strict-gate or
operator acceptance. Roll back only this comparator/test if needed; preserve
pending intents. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: legacy Service routing reply comparison (offline)

A test-owned successful Service reply could change TCP to UDP or introduce a
selector while keeping the checked port numbers and identity; both advanced
through the remaining fake corpus before repair. The fixed-corpus comparison
now requires TCP (including its Kubernetes default), no added selector, and
integer port identities rather than boolean equivalents. The two negative
cases failed before repair; 25 native tests and `make verify-local-experiment`
passed after repair. Dedicated Colima was observed stopped; no live admission,
saved request replay, retained data/backup mutation or GitHub write. This is
not full effective-object equality or authenticated API/strict/operator
acceptance. Roll back only the scoped comparator and regression if needed;
preserve pending intents. Direct original Vault synchronization remains
pending outside this checkout.

## 2026-10-04: Pod namespace and host-alias dry-run controls (offline)

Two disposable successful Pod replies added `shareProcessNamespace: true` or
host aliases without changing the previous smoke fields; both progressed to
a third fake request before repair. The fixed-corpus comparator now refuses
both; it also refuses a numeric `hostNetwork` instead of treating it as false.
The new negative passed after repair (three variants), the native suite passed
(25 tests), and `make verify-local-experiment` passed. The dedicated Colima
profile was observed stopped. No live admission, replay, retained data/backup
change or GitHub write. This is not full effective-object equality or proof of
API authenticity, strict admission or operator acceptance. Revert the scoped
comparator/test if needed and retain saved intents. Original Vault sync remains
pending outside checkout.

## 2026-10-04: Pod-level security context in legacy smoke (offline)

Three fake successful Pod replies changed `runAsUser`, `runAsNonRoot`, or
`seccompProfile` without changing the identity or container controls. Before
the repair the smoke progressed to a third request; it now refuses each after
the first. The focused native suite passed (24 tests). An initial test fixture
also mutated a Service lacking Pod security context and raised KeyError; the
fixture was corrected before confirming the regression. Dedicated Colima was
observed stopped. No live request, saved replay, retained data or backup change.
This compares fixed-corpus fields only, not full effective-object equality,
API authenticity, strict admission or operator acceptance. Revert only the
comparison/test if needed, keeping historical intents. Original Vault sync
remains pending outside checkout.

## 2026-10-04: legacy native smoke policy-feature comparison (offline)

The legacy smoke accepted a successful Pod or Service reply with the same
identity but changed image, host network, resources, init container, privilege,
host port, Service type, external IP or node port. A fixed-corpus comparator
now refuses these policy-relevant changes while allowing unrelated API defaults.
Nine disposable negative replies fail after one or two fake requests; the
24-test native suite and local regression passed. Dedicated Colima was observed
stopped. No live API, saved request replay, retained volume or backup mutation.
This is **not** full effective-object equality, API authenticity, live admission,
strict gate or operator acceptance. Roll back only the comparator/tests if
needed and preserve saved intents. Original Vault sync remains pending outside
the approved checkout.

## 2026-10-04: deeply nested native success transport (offline)

A test-owned success with a 1100-level unknown extension raised an uncaught
`RecursionError` after one fake dry-run rather than recording a failed case.
The negative failed first; the bounded native success decoder now treats JSON
decoder recursion failure as an invalid reply. Focused native suite: 22 tests.
No live request was sent to the stopped dedicated lab; no saved intent, volume
or backup was changed. This does not authenticate the API, compare effective
objects, clear strict admission or establish acceptance. Roll back only the
reader guard/test if needed, preserving pending intents without replay.
Original Vault synchronization remains pending outside this checkout.

## 2026-10-04: native smoke API version identity (offline)

A disposable successful reply with a contradictory `apiVersion` but matching
kind/name/namespace passed the legacy native smoke. The focused negative failed
before repair; smoke now also requires the input API version. The native suite
passed (21 tests). This remains a shallow identity check, not effective-object
equality or proof of API authenticity, live admission, strict gate or acceptance.
The dedicated lab was observed stopped; no request or retained data/backup
mutation. Roll back only the guard/test if needed, preserving pending intents
without replay. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: malformed native smoke metadata (offline)

Three disposable successful dry-run replies with null, array or string
`metadata` caused an uncaught AttributeError instead of a recorded failed
case. A focused negative reproduced all three errors before repair; the
native smoke now checks the metadata object type before reading identity.
The 20-test native suite passed. This does not establish API authenticity,
effective-object equality, live admission or acceptance. No request went to
the stopped lab; no retained data changed. Roll back only this guard/test if
needed; keep historical records and do not replay unknown requests. Direct
original Vault sync remains pending outside checkout.

## 2026-10-04: legacy native smoke success transport (offline)

Four test-owned successful dry-run replies with nonempty stderr, duplicate
identity keys, oversized output or a Python-only numeric constant advanced
past the allowed case before repair. The legacy native smoke reader now requires
empty stderr and bounded, duplicate-free, representable JSON before checking
the reported name/namespace. The four negatives failed first and the native
policy suite passed (18 tests). The stopped dedicated lab received no request;
no retained intent, volume or backup changed. This does not strengthen the
legacy smoke's shallow effective-object comparison or establish live admission,
API authenticity, atomic inventory or acceptance. Roll back only this reader
and test if necessary; do not replay saved unknown requests. Direct original
Vault sync remains pending outside checkout.

## 2026-10-04: independent legacy smoke denial transport (offline)

A fake recognized policy denial carrying a simultaneous successful Pod payload
was counted as a pass before repair. The native smoke now requires exactly empty
stdout for a denial; the negative failed first and the 19-test native suite
passed. No request was sent to the stopped lab and no retained data changed.
This is a local transport consistency check, not proof that the API or denial
producer is authentic. Roll back this single guard/test if needed; do not
replay saved requests. Direct original Vault sync remains pending.

## 2026-10-04: scoped API numeric underflow (offline)

Three independent fake scoped API JSON replies contained nonzero exponents
rounded to zero by Python's float parser, including ignored nested extensions.
All three negative cases failed before repair. The reader now refuses such
lossy values and accepts an actual zero exponent; 58 observer tests passed.
No request was sent to the stopped dedicated lab and no retained data or
backup was touched. Fake transport checks cannot authenticate the API, prove
atomic inventory, clear strict admission or establish operator acceptance.
Roll back only the parser and regression if needed, preserving saved pending
intents without replay. Original Vault sync remains pending outside checkout.

## 2026-10-04: dry-run success JSON numeric transport (offline)

The dry-run success decoder now refuses Python-only `NaN`/`Infinity`, overflowing
exponents and nonzero numbers underflowing to zero before the Pod comparator.
An isolated parser test caught the underflow before repair; the new parser
boundary also checks finite exponents and genuine zero. Six fake malformed
success replies remain unknown/pending after exactly one simulated request,
without replay. Focused observer 57 tests passed. Dedicated Colima was observed
stopped; no live admission, retained requests, volumes or backups changed.
These malformed extensions were already refused by the Pod envelope; this
change strengthens transport validation rather than demonstrating a previously
allowed workload. Fake responses do not authenticate the API, establish atomic
inventory, clear strict admission or grant operator acceptance. Roll back only
the dry-run decoder/test if needed and preserve pending intents. Original Vault
sync remains pending outside checkout.

## 2026-10-04: overflowing JSON exponent in scoped API (offline)

Three test-owned nested/top-level `1e999` / `-1e999` responses decoded to
infinite Python floats before repair. The scoped API reader now rejects
non-finite parsed floats while accepting finite exponents; all three negatives
failed first, then 56 observer tests passed. Dedicated Colima was observed
stopped; no live admission, pending-request replay or retained volume/backup
mutation. Fake replies cannot authenticate the API, establish atomic inventory,
clear strict admission or grant operator acceptance. Roll back only the parser
and regression if necessary; retain saved intents. Original Vault sync remains
pending outside this checkout.

## 2026-10-04: non-JSON Kubernetes numeric constants (offline)

Three test-owned scoped API replies with nested or top-level `NaN`/`Infinity`
passed the transport parser before repair. The reader now rejects Python's
non-standard JSON constants before baseline interpretation; canonical numeric
JSON remains valid. All three negatives failed before repair; focused policy
observer/native tests passed (72). The dedicated Colima profile was read-only
observed stopped. No live admission, pending-request replay, retained volume or
backup mutation. Fake replies cannot authenticate the API, prove atomic
inventory, clear strict admission or establish operator acceptance. Roll back
only the parser/test if needed, retaining saved intents. Original Vault sync
pending outside this checkout.

## 2026-10-04: explicit null is not a missing Pod (offline)

The scoped kubectl JSON reader previously returned `None` for both an empty
`--ignore-not-found` response and a literal JSON `null`. Two fake transport
replies reproduced the ambiguity before repair. Explicit null now fails the
reader; empty/whitespace-only output still denotes absence. The focused
observer suite passed (54 tests). Dedicated Colima was observed stopped; no
live admission, retained request, volume or backup changed. This offline
transport check cannot authenticate the API, prove atomic inventory or clear
strict admission. Roll back only the reader/test if needed; preserve pending
intents without replay. Direct original Vault sync remains pending outside
this checkout.

## 2026-10-04: duplicate raw Kubernetes JSON claims (offline)

The scoped policy reader previously let JSON parsing silently retain the last
of two identical keys, including nested identity keys and list members. Three
test-owned replies reproduced acceptance before repair. All scoped policy API
JSON now rejects duplicate keys before baseline or dry-run interpretation;
canonical replies remain valid. The focused observer suite passed (53 tests).
The dedicated Colima profile was observed stopped; no live API/admission,
retained request, volume or backup changed. Fake transport data cannot
authenticate the API, prove atomic inventory, clear strict admission or
establish operator acceptance. Roll back the reader/test only if needed;
preserve pending requests without replay. Direct original Vault sync remains
pending outside this checkout.

## 2026-10-04: namespace selector label aliases (offline)

Three test-owned Namespace replies with a valid canonical selector label and
conflicting case-variant keys became ready before repair. The fixed baseline
now refuses these aliases before any dry-run. The negative failed in all three
cases before repair; the observer suite passed (52 tests) afterward. The
dedicated Colima profile was observed stopped; no live admission, retained
request, volume or backup changed. Fake replies cannot authenticate the API,
prove atomic inventory, clear strict admission or establish acceptance. Roll
back only this guard/test if necessary; preserve unknown requests without
replay. Direct original Vault synchronization is pending outside this checkout.

## 2026-10-04: policy resource envelope aliases (offline)

Six conflicting case-variant top-level fields on test-owned policy resources
were accepted as ready before repair. The fixed-candidate baseline now refuses
these aliases before any dry-run. The negative failed in all six cases before
repair; the observer suite passed (51 tests) afterward. Dedicated Colima was
observed stopped. No live admission, retained requests, volumes or backups
changed. These fixtures cannot authenticate the API, prove atomic inventory,
clear strict admission or establish operator acceptance. Roll back only this
guard/test if needed; retain pending intents without replay. Original Vault
handoff sync remains pending outside the approved checkout.

## 2026-10-04: namespace envelope aliases (offline)

Separately from policy-resource metadata, four fake Namespace replies with
conflicting case-variant top-level `Kind`, `KIND`, `ApiVersion` or `Metadata`
were accepted before repair. The observer now rejects these before dry-run;
the four-case negative failed first and the focused 50-test suite passed after
repair. Dedicated Colima remains stopped; no live API/admission or retained
data/backup changed. Fake replies do not authenticate the API, prove atomic
inventory, clear strict admission or establish operator acceptance. Roll back
only this Namespace guard/test if necessary; retain pending intents without
replay. Original Vault sync pending outside the approved checkout.

## 2026-10-04: policy resource identity aliases (offline)

Six test-owned policy resource metadata aliases (`Name`, `Uid`, `Generation`,
`Labels`, `Annotations`, `DeletionTimestamp`) were accepted alongside canonical
identity/ownership fields before repair. The fixed-candidate baseline now
rejects conflicting case-variant identity fields before dry-run; the six-case
negative failed before repair and observer suite (49 tests) passed afterward.
Dedicated Colima was observed stopped. No live API/admission, retained requests,
volumes or backups changed. This fixture cannot authenticate the API or prove
atomic inventory, strict admission or operator acceptance. Roll back only this
guard/test if necessary; preserve pending intents without replay. Original Vault
sync remains pending outside the approved checkout.

## 2026-10-04: namespace identity alias baseline (offline)

Four fake Namespace replies with canonical identity plus conflicting `Name`,
`Uid`, `Labels` or `DeletionTimestamp` metadata aliases incorrectly became
ready. The negative reproduced four failures before repair. The fixed-candidate
observer now rejects these aliases before dry-run; focused observer 48 tests
and native policy 17 tests passed. The dedicated Colima profile was observed
stopped; no live API/admission, existing request, volume or backup was changed.
This does not authenticate a namespace inventory or establish admission or
operator acceptance. Roll back only the alias guard/test if needed, preserving
pending intents without replay. Original Vault synchronization pending outside
the approved checkout.

## 2026-10-04: conflicting LimitRange list envelope aliases (offline)

Five test-owned empty canonical lists with conflicting case-variant `Items`,
`Metadata`, `Kind` or `ApiVersion` passed the fixed baseline before repair.
The observer now refuses these aliases before any dry-run. Focused observer
47 tests and native policy 17 tests passed. The dedicated Colima profile was
observed stopped; no live admission, existing request, volume or backup was
modified. This fake-list consistency check does not authenticate the API,
establish atomic inventory or grant operator acceptance. Roll back only the
alias guard and test if needed; retain pending requests without replay.
Original Vault sync pending outside the approved checkout.

## 2026-10-04: case-variant LimitRange pagination (offline)

Five test-owned empty lists with case-variant `Continue` or
`RemainingItemCount` metadata (alone or alongside canonical keys) passed the
baseline as complete before repair. The observer now refuses pagination key
aliases before any dry-run; omitted metadata and canonical empty/zero remain
compatible. Focused observer 46 tests and native policy 17 tests passed.
Dedicated Colima was observed stopped; no live admission, existing request,
volume or backup was modified. A fake response is not an authenticated or
atomic inventory or operator acceptance. Roll back only the baseline guard
and its test if needed; retain pending requests without replay. Original Vault
sync pending outside the approved checkout.

## 2026-10-04: empty LimitRange page with remaining items (offline)

An empty list with no continuation token but `remainingItemCount: 1` is not
proof of an empty namespace inventory. The candidate baseline now accepts an
omitted count or integer zero only; nonzero, negative, boolean and null counts
fail before dry-run. Focused observer tests (46) passed. Independently checked
the GitOps doctor list decoder: its existing full-list checks already reject
nonzero remaining counts, so no GitOps code change was warranted. Dedicated
Colima was observed Stopped; no live admission, Git/API writes or retained
request/report/volume/backup changes. Fake lists neither authenticate an API
nor provide an atomic inventory or operator acceptance. Roll back only this
baseline check and regression tests if necessary; preserve pending intents.
Original Vault synchronization pending outside the approved checkout.

## 2026-10-04: incomplete LimitRange inventory refused (offline)

A test-owned first page with zero LimitRanges and a nonempty continuation
token was incorrectly treated as proof of an empty namespace inventory. The
negative failed before repair. The fixed-candidate baseline now requires an
absent or empty-string continuation token and refuses malformed metadata; no
dry-run is sent on failure. Focused observer tests (45) passed. This only
validates the local fake list contract, not an authenticated or atomic live
inventory; LimitRange policy acceptance and operator acceptance remain open.
Dedicated Colima was observed Stopped; no retained requests, volumes or backups
were changed. Roll back the baseline check/test only if needed, preserving
previous pending requests. Original Vault synchronization pending outside the
approved checkout.

## 2026-10-04: fixed-corpus progress receipt failure (offline)

The legacy 15-case fixed-corpus verifier cleared its pending intent before a
per-case completion write; a test-owned writer failure left an in-memory known
result without a pending request. The negative failed before repair. A failed
completion or terminal write now restores the affected sent case as unknown and
pending, without replay. A failed pre-send write instead has no pending request
and zero probes. Focused 14 tests and local verification passed. This cannot
guarantee durability when the writer remains broken, authenticate a live API,
or establish policy acceptance. Dedicated Colima observed Stopped; no live
dry-run, retained request/volume/backup mutation or GitHub write. Roll back the
fixed-corpus observer and tests only if required; retain previous pending
intents and evidence. Original Vault synchronization pending outside checkout.

## 2026-10-04: pre-send intent writer failure (offline)

A test-owned failing intent writer stopped the request before send (zero API
calls), but the in-memory failure receipt reported `admission=unknown` with no
pending intent. The negative failed before repair. Pre-send failures now retain
`not-observed`/no pending; post-send failures still retain unknown/pending and
never replay. Focused observer 44 tests passed. No live admission or retained
request/volume/backup change; the dedicated lab was observed Stopped. This
does not prove durable writer behavior, API authenticity or acceptance. Roll
back only the observer and test if needed; preserve existing pending intents.
Original Vault sync pending outside this checkout.

## 2026-10-04: dry-run opposite-stream bytes (offline)

The candidate observer previously treated whitespace-only stderr on a
successful Pod response, or whitespace-only stdout on a recognized policy
denial, as an empty stream. It now requires the opposite stream to be exactly
empty; test-owned mixed responses remain unknown/pending after one request,
without replay. Focused observer 43 tests and native policy 17 tests passed;
`make verify-local-experiment` passed. This does not prove API authenticity,
general mutation coverage or live admission. Dedicated Colima was read-only
observed Stopped; no live request or retained data/backup change. Roll back
only the observer and test if necessary; do not replay saved pending intents.
Original Vault synchronization is pending outside the approved checkout.

## 2026-10-04: terminal receipt failure retains dry-run intent (offline)

A fake final `progress` writer failure after one allowed dry-run left the
in-memory receipt `failed/allowed` with no pending intent. The negative failed
before repair. On failure after a sent request, restore the unknown pending
marker before writing the failure receipt; never retry the request. The focused
observer suite passed (42 tests), as did local verification. No live admission,
saved request, volume or backup was modified; dedicated Colima was observed
Stopped. This does not make an actually failed disk writer durable, authenticate
the API or establish acceptance. Roll back the observer/test only if necessary;
keep any previously saved pending intent and do not replay unknown requests.
Original Vault sync pending outside this checkout.

## 2026-10-04: typed privilege and identity defaults (offline)

Pinned the existing refusal of numeric `0` as a container `privileged: false`
default and boolean `false` as a Pod metadata generation of zero. Two isolated
allowed-response fixtures remain unknown/pending after one request, with no
replay. Focused observer suite passed (41 tests); no observer implementation
changed. Dedicated Colima was observed Stopped; no admission, retained request,
volume or backup modification. This checks local type safety, not server
authenticity, general mutation coverage or operator acceptance. Roll back only
the test if necessary and retain pending requests. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: numeric false defaults regression (offline)

Pinned nine fake allowed responses with JSON number `0` in Pod host isolation,
automount, process sharing, hostname, or container stream controls. The existing
identity-based boolean checks already reject all nine; no observer behavior
changed. Focused observer suite passed (40 tests). The original intent remains
unknown/pending in every negative and is never replayed. Dedicated Colima was
read-only observed Stopped; no live API, retained request/volume/backup change
or GitHub write. These fixtures cannot authenticate a server, establish complete
mutation coverage or operator acceptance. Roll back only the new test if needed;
retain pending and historical evidence. Original Vault sync pending outside the
approved checkout.

## 2026-10-04: explicit null dry-run status (offline)

A disposable allowed response with `status: null` passed as though the status
field were absent. The focused negative failed before repair. The response
comparator now permits an omitted status or an empty object, not explicit null;
the original request remains unknown/pending and is not replayed. The focused
observer suite passed (39 tests). Dedicated Colima was observed Stopped; no
live admission, retained request/volume/backup change or GitHub write. Fake
replies cannot authenticate the API or establish full mutation coverage or
operator acceptance. Roll back only the comparator/test if needed, retaining
pending and historical evidence. Original Vault sync pending outside checkout.

## 2026-10-04: contradictory dry-run denial streams (offline)

Two disposable forbidden policy responses carrying nonempty stdout (an empty
object or a Pod) passed as denied before repair. A recognized stderr denial now
requires empty stdout; both mixed responses remain unknown/pending and the
original request is never retried. The observer's focused 39 tests and native
policy 17 tests passed. Dedicated Colima was read-only observed Stopped; no
live admission or retained request/volume/backup mutation. Fake replies do not
authenticate the API, prove general mutation coverage or establish acceptance.
Roll back observer/test only if necessary, preserving pending requests and old
evidence. Original Vault sync pending outside this checkout.

## 2026-10-04: duplicate allowed-response keys (offline)

Four test-owned successful JSON replies repeated a top-level Pod key or a
nested container key with a conflicting first value. Python's last-key-wins
decoder previously classified each as allowed; object-pair decoding now
refuses duplicates at every depth. The single original request remains
unknown/pending, never replayed. Focused observer suite (38 tests) passed.
Dedicated Colima was observed Stopped; no live admission, retained request,
volume or backup mutation. JSON ambiguity refusal does not authenticate an
API, prove general mutation coverage or establish operator acceptance. Roll
back parser/test only if needed, retaining pending requests and old evidence.
Original Vault sync pending outside checkout.

## 2026-10-04: top-level dry-run response claims (offline)

Two disposable allowed Pod responses with an unsubmitted top-level `items` or
`unexpected` claim passed before repair. The comparator now limits response
keys to the Pod envelope; both stay unknown/pending without retry. Focused
observer 37 tests and local verification passed. Dedicated Colima was observed
Stopped; no live API, retained request/report/volume/backup mutation or GitHub
write. Fake replies do not authenticate the server or establish full mutation
coverage or operator acceptance. Roll back comparator/test only if needed;
preserve pending requests and historical evidence. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: allowed dry-run object identity (offline)

Six disposable allowed responses added a persisted UID, resource version,
generation, creation time, managed fields or unknown metadata key. Each passed
before repair. The response comparator now refuses these claims, while empty
API defaults remain compatible. The pending original request is retained as
unknown, never replayed. Focused observer 36 tests and policy 17 tests passed.
Dedicated Colima was observed Stopped; no live admission, retained evidence,
volume or backup mutation, or GitHub write. Fake responses cannot authenticate
the API, prove complete mutation coverage or establish operator acceptance.
Roll back comparator/test only if required, preserving pending requests and
historical evidence. Original Vault sync remains pending outside this checkout.

## 2026-10-04: security boolean type in dry-run responses (offline)

Two disposable allowed responses substituted JSON number `1` for a submitted
Pod `runAsNonRoot: true` or container `allowPrivilegeEscalation: true`. Python
equality previously treated both as unchanged. Compare submitted security
values using canonical JSON representation so boolean and numeric types remain
distinct; keep the permitted explicit `privileged: false` default. Both
negatives now remain unknown/pending without replay; focused observer 35 tests
and policy 17 tests passed. Dedicated Colima observed Stopped; no live
admission, retained evidence/volume/backup mutation or GitHub write. Fake
responses cannot authenticate the API, prove arbitrary mutation coverage or
establish operator acceptance. Roll back only comparator/test if needed and
retain pending requests and historical evidence. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: dry-run response status boundary (offline)

Two disposable allowed responses claiming a Running phase or Ready condition
passed before repair. An allowed server dry-run may return an absent or empty
Pod status, but a populated status is not evidence of a created/running Pod;
the comparator now refuses it as unknown and retains the pending request.
Focused candidate/CLI suites passed (80 + 23). Dedicated Colima was observed
Stopped; no admission, replay, retained report/volume/backup change or GitHub
write. Fake responses cannot authenticate an API or prove complete mutation
coverage or operator acceptance. Roll back comparator/test only if needed,
preserving pending and historical evidence. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: container resource claims in allowed responses (offline)

Two disposable fake allowed responses added an unsubmitted container resource
`claims` field (populated or empty). Both were previously classified allowed;
the response now requires exactly the submitted `requests` and `limits` keys,
with their existing quantity equivalence checks unchanged. Mismatches remain
unknown/pending and are never replayed. Focused observer suite passed (33).
No admission, retained request/report/volume/backup mutation or VM start;
dedicated Colima was observed stopped. Fake responses do not authenticate the
API, prove arbitrary Pod mutation coverage or establish operator acceptance.
Roll back the comparator and test only if needed, preserving pending requests.
Original Vault sync pending outside this approved checkout.

## 2026-10-04: unsubmitted Pod controls and metadata lifecycle (offline)

Three disposable allowed responses inserted `hostUsers: false`, a resource
claim, or a zero termination grace period; three separate responses added a
generated name, deletion timestamp, or deletion grace period. All six passed
before repair. The spec comparison now refuses unknown keys and these controls
except inert explicit defaults; metadata lifecycle mutations also fail closed
as unknown, retaining the pending request. Focused observer suite (32 tests)
and `make verify-local-experiment` passed. Dedicated Colima was observed
Stopped. No live admission, archived replay, retained report/volume/backup
mutation or GitHub write. Fake responses neither authenticate an API nor prove
complete Pod mutation coverage or operator acceptance. If necessary roll back
comparator/tests only; preserve pending requests and existing evidence. Original
Vault synchronization remains pending outside the noninteractive checkout.

## 2026-10-04: scheduling and lifecycle response controls (offline)

Four disposable allowed dry-run responses added an unsubmitted topology spread
constraint, readiness gate, resource overhead or active deadline. All passed
before the guard and now fail closed as unknown with the original pending
request retained. Empty API defaults still match. Focused observer suite passed
(30 tests). Dedicated Colima observed Stopped; no admission, saved-request replay,
retained report/volume/backup mutation or GitHub write. Fake responses cannot
authenticate the API, cover arbitrary Pod mutations or establish acceptance.
Roll back comparator/test only; preserve unknown requests and historical evidence.
Original Vault sync pending outside the approved checkout.

## 2026-10-04: DNS and metadata response boundaries (offline)

Six disposable allowed responses redirected DNS/hostname/service links and
three injected labels, annotations or finalizers. All nine passed before the
guard and now fail closed as unknown, retaining their pending request. Explicit
inert API defaults still match. Focused candidate tests (75) and full local
verification passed. The dedicated Colima was observed Stopped; no admission,
archived request replay, retained data/backup mutation or GitHub write. Fake
responses cannot authenticate the API or prove complete Pod mutation coverage,
current runtime state or acceptance. Roll back the comparator/tests only if
needed; preserve unknown requests. Original Vault sync remains pending outside
the approved noninteractive checkout.

## 2026-10-04: process namespace and host aliases (offline)

Two disposable allowed responses added `shareProcessNamespace: true` or a
`hostAliases` entry without submission; both passed before repair. They now
fail closed as unknown with the original request pending; explicit inert API
defaults still match. Focused observer 27 tests and local verification passed.
Dedicated Colima stayed stopped; no admission, archived request replay or
retained volume/backup change. Fake responses do not authenticate the server,
prove full mutation coverage or establish acceptance. Roll back comparator and
test only, preserving pending requests. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: allowed response runtime and priority (offline)

Four disposable fake responses injected a runtime class, critical priority
class, nonzero priority or altered preemption policy. Each passed before repair;
the comparator now refuses them as unknown while retaining the pending request.
Explicit inert API defaults remain accepted. Focused observer 26 tests passed.
Dedicated Colima was observed stopped; no admission, saved-request replay,
retained data/volume/backup change or GitHub write. Fake responses do not
authenticate a server or prove complete mutation coverage or acceptance.
Roll back comparator/test only, preserving pending requests. Original Vault
sync remains pending outside the approved checkout.

## 2026-10-04: allowed response owner and pull credentials (offline)

Two disposable fake allowed responses gained a controller owner reference or
image-pull secret despite neither being submitted. Both incorrectly passed
before repair. The comparator now refuses these changes and accepts explicit
empty API forms. Focused observer 25 tests and full local verification passed.
Dedicated Colima was read-only observed stopped; no admission, archived request
replay, retained evidence/volume/backup modification or GitHub write. Unknown
responses retain pending requests. This does not authenticate a server, cover
arbitrary Pod mutation or establish operator acceptance. Roll back comparator
and test only; preserve unknown requests and historical evidence. Original
Vault sync pending outside the approved noninteractive checkout.

## 2026-10-04: allowed response Pod identity and placement (offline)

Six disposable responses injecting a service account, node name/selector,
toleration, affinity or alternate scheduler passed before repair. The allowed
comparator now refuses them while accepting the empty/default API forms.
Focused candidate CLI checks passed (70 observer/candidate and 23 CLI tests).
An unknown result retains the original pending request; never replay it.
Dedicated Colima was observed stopped; no live admission, retained evidence,
volume/backup mutation or GitHub write. Fake responses cannot authenticate the
API or prove arbitrary mutation coverage/operator acceptance. Roll back only
the comparator and test; preserve pending requests and stopped lab. Original
Vault sync pending outside this checkout.

## 2026-10-04: allowed response container execution defaults (offline)

Four disposable allowed responses injected a working directory, stdin, a
post-start lifecycle hook or a different termination message path and passed
before repair. Unsubmitted execution fields now fail closed; explicit inert
Kubernetes defaults remain supported. Focused observer tests passed (23).
The lab stayed stopped: no live admission or saved-request replay. No retained
volume/backup or GitHub write. Fake responses do not prove server authenticity,
all mutation coverage or operator acceptance. Roll back comparator/test only;
retain pending unknown requests. Original Vault sync pending outside checkout.

## 2026-10-04: allowed response port attributes (offline)

Three disposable dry-run responses injecting UDP protocol, host IP or a port
name were incorrectly classified allowed. The comparator now checks the full
supported port attribute set, preserving the harmless API TCP/empty defaults
and rejecting boolean ports. An unknown response retains its pending request;
do not replay it. Focused observer tests passed (22). Dedicated Colima was
observed stopped; no admission, saved request, retained volume/backup or GitHub
write. Fake responses cannot authenticate the API or establish general Pod
mutation coverage or operator acceptance. Roll back only the comparator/test;
preserve pending requests. Original Vault sync pending outside checkout.

## 2026-10-04: allowed response container security context (offline)

Three test-owned allowed dry-run responses injected an unsubmitted container
`allowPrivilegeEscalation: true`, `runAsUser: 0`, or `SYS_ADMIN` capability.
All passed before repair. The comparator now refuses unsubmitted container
security settings except explicit `privileged: false` (a harmless default),
and retains the pending unknown request without replay. The focused observer
suite passed (21 tests). Dedicated Colima was read-only observed stopped; no
live admission, archived request replay, retained evidence/backup mutation or
GitHub write. The fake server cannot authenticate actual admission or cover
arbitrary Pod mutations. Stop new checks or roll back the comparator/test only;
preserve pending unknown requests. Original Vault sync pending outside checkout.

## 2026-10-04: allowed response Pod security context (offline)

In a disposable fake server dry-run, changing a submitted Pod-level
`runAsUser` to 0, changing `runAsNonRoot` to false, or dropping `runAsUser`
still produced `passed/allowed` before repair. The allowed response now
requires the submitted Pod `securityContext` unchanged (or absent/empty when
none was submitted); an unexpected context fails closed as unknown with the
original pending request retained. Container checks and harmless empty API
default remain supported. Policy-focused 104 tests passed. No VM start,
admission call, archived dry-run replay, retained evidence/backup mutation or
GitHub write. This is a fake-response comparison, not independent admission
authenticity, arbitrary Pod mutation coverage or production acceptance. Stop
new candidate checks or revert this guard/test to roll back; keep pending
unknown requests without replay. Original Vault sync pending outside checkout.

## 2026-09-29: bounded live full-CLI privileged denial

専用`mac-studio-solo`をStoppedから設定SHA記録後に一時起動。固定contextでnative policy/binding 16件ready、既存loopback Qwenの期待model IDを確認し、既存の専用AgentHouse/Lokiだけを起動した。hostが`P01`だけを予測するprivileged Pod候補**1件**を新tokenのGo CLI `policy check --pod-stdin`に投入。model 1回でhost期待に一致し、server dry-run request_count=1、native拒否の報告も`P01`、`passed/denied`（CLI exit 2）。監査delivery4件・保存spans4件。保存Go `policy status`はread-onlyで`passed/denied`と`response_content_check=not-applicable`を示す。denyにallowed応答内容照合を誤って主張しない。別の固定context API readで対象Pod不存在、前後16件inventory詳細一致。入力manifest、API拒否原文、model原文は保存せず、private記録は`tmp/local-experiment/policy-full-cli-denied-checks/20260929/`および新tokenのprivate evidence。これは1候補のlive producerと同producer保存statusの整合であり、保存物の独立認証ではない。

今回起動したAgentHouse/Lokiを停止し、Colimaを通常停止してStoppedとprofile設定SHA不変を確認。起動時TCP 53 forward競合、停止時`/var/lib/cni/` busyと保持Pod volume unmountの出力あり。保持PVC/backup内容と停止後guest状態の独立照合なし。policy apply/delete・実workload作成・外部公開なし。複数違反、任意Pod、後日のcluster状態や本番/人間受入へ昇格しない。WP `local-partial`を維持し、未知dry-runは同要求を自動再POSTしない。

全回帰`make verify-local-experiment` 121 suites/3820実行＝3812PASS＋8skip、95.06秒、log SHA256 `0be1d921888b92296c1fd3c4417250fa5589aac7f637d3c372c045d4a91e79f8`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-09-28: bounded live Go CLI / Qwen / admission path

専用`mac-studio-solo`をStoppedから一時起動し、固定contextのnative policy/binding 16件readyを確認。既存loopback Qwenは期待model IDを返した。Go CLIの初回tokenはAgentHouse/Loki停止により監査preflightで失敗（model_attempts=0、dry-run request_count=0）。同tokenを再送せず、既存の専用AgentHouse/Lokiを起動して新tokenで適合Pod **1件**を`policy check --pod-stdin`へ投入した。新tokenはmodel_attempts=1、model_matched=true、4件の監査deliveryと4 spans検証、server dry-run request_count=1、`passed/allowed`、v2 producer `matched`。別のGo `policy status`で`producer-checked`、固定contextの別API readで対象Pod不存在、実行前後の16件inventory詳細が一致した。最初の失敗と成功を合算してもPod dry-runは1件のみ。private記録は`tmp/local-experiment/policy-full-cli-live-checks/20260928/`およびtoken別のprivate evidence。raw API応答・モデル出力は保存せず、保存statusは現在pathの内部整合性であり独立認証ではない。

今回起動したAgentHouse/Lokiを停止してから専用Colimaを通常停止し、Stoppedとprofile設定SHA不変を確認。起動時TCP 53 forward競合、停止時`/var/lib/cni/` busyと保持Pod volume unmountの出力あり。保持PVC/backup内容と停止後guest状態は独立照合していない。実workload作成・policy変更・外部公開なし。これは1候補のローカル全経路であり、汎用候補集合、後日のcluster状態、本番/人間受入を示さない。WP `local-partial`を維持する。

全回帰`make verify-local-experiment` 121 suites/3817実行＝3809PASS＋8skip、log SHA256 `5f8c1b3498b3eb7aef84473f29dedf82492121710c1e39b7c94bc91013db781c`。design-context/secret scan・diff check PASS。

## 2026-09-28: bounded live allowed-response recheck

停止中の専用`mac-studio-solo`を設定SHA記録後に`--activate=false --save-config=false --ssh-config=false`で一時起動。固定contextのnative policy/binding 16件readyを確認し、適合Pod候補**1件だけ**を候補observerでserver dry-runした。request_count=1、`passed/allowed`、v2 `response_content_check=matched`、Podの前後不存在・policy inventory安定を確認。別のread-only `local-policy-status`では16件のinventory詳細が事前と同一、固定名Podは独立したAPI readでも不存在。Qwen・AgentHouse・CLI全経路は実行せず、policy apply/delete・workload作成なし。private記録は`tmp/local-experiment/policy-live-response-checks/20260928/`。これは**この1回のlive API応答に対するproducer照合**であり、保存物の独立認証、後日のcluster状態、モデル連携、本番受入ではない。WP local-partialを維持する。

通常`colima stop`後に専用profileがStoppedへ戻り、host profile設定SHA不変。起動時にhost TCP 53 forwardのbind競合警告、停止時のk3s killallに`/var/lib/cni/` busy警告とpod volume unmount出力があった。停止前のpolicy inventoryと対象Pod不存在は確認したが、保持PVC/backup内容や停止後のguest状態は独立照合していない。起動に伴う保持workloadの通常稼働やguestの変化まで不変とは主張しない。再実行は新tokenと固定contextで事前inventory確認後のbounded dry-runに限り、未知結果の自動再POSTは行わない。

全回帰`make verify-local-experiment` 121 suites/3808実行＝3800PASS＋8skip、log SHA256 `1cb6a2ae8456275b1bb6d306c0842bfd07b2f245e7b0aca3160a0f2fecefb1ff`。design-context/secret scanとdiff check PASS。live run固有の持続証拠はprivate summary/progressおよび前後inventory/statusで、raw API応答は保存していない。

## 2026-09-28: saved allowed-response coverage is revision-scoped

新しく強化したallowed応答内容照合を、以前の`passed`保存物へ遡及した証拠として扱わない。新producerのverification receipt v2に`response_content_check`を保存し、新規passed/allowedに限り`matched`、deniedは`not-applicable`、未完了・失敗は`not-checked`。read-only status/Go CLIは`producer-checked`、旧v1のpassed/allowedは`legacy-unrecorded`、passed/deniedは`not-applicable`、その他は`unknown`と区別。producerが自分で残したmarkerの内部相関であり、旧raw API応答の再評価や第三者認証ではない。旧保存3件の結果・file集合/SHA/inode/mode不変を確認し、旧passed/allowedを新判定へ自動昇格させない。保存receipt・保持run・backupの移行/書換えなし。Colima起動・live admission再試行なし、WP local-partial。復旧は新入口が不適ならGo/Python protocolを対で戻す。旧保存証拠を削除・再作成しない。private記録は`tmp/local-experiment/policy-saved-coverage-checks/20260928/`。

policy focused103件PASS、Go全663件PASS・policy race20件PASS・vet PASS、全回帰121 suites/3808実行＝3800PASS＋8skip、89.609秒、log SHA256 `1a789397c3165da69a4c86798209ac4d995cb16fb0a683e4d881c73b11cb5845`。design-context/secret scan PASS、専用Colima Stopped。Go protocol初回テストは旧fake応答の必須field欠損1件で失敗、fixture修正後全件通過。最後の負例assertion追加後に全回帰を再実行した。

## 2026-09-27: pin workflow evidence writes to owned output fd

policy focused101件PASS、全回帰121 suites/3806実行＝3798PASS＋8skip、90.172秒、log SHA256 `891e4be8b0312aa866a9ddefd4e029fdabc62ec4801f4518967f26cecbafe271`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-anchored-writes-checks/20260927/`。

前増分は出力directoryの**作成**までfdに固定したが、その後の`candidate.json`/他receipt/OTel span append/再読込が再びpathを辿った。隔離tempで作成直後に親を模擬保持treeへのsymlinkへ差し替えると、旧workflowは`candidate.json`を模擬保持先へ書く負例を再現。今回、workflowの作成後から完了まで開いた出力fdを保持し、JSONのatomic replace、span append/chmod、保存span再読込を同fd相対へ変更。append対象のsymlink・複数linkを拒否。差し替え後の完全workflowでは模擬保持treeは空、元dirにだけcandidate/result/spansが残り、終了時のpath identity不一致を`OUTPUT_PARENT_INVALID`で拒否。元dirを自動削除しない。保存statusは従来どおり現pathのread-only検証であり、この修正は外部同UID actorによる元dir内の内容改変や、保存物の独立認証・live admissionを証明しない。Kubernetes/モデル/実保持run/backup変更なし、Colima起動なし。WP local-partial。

## 2026-09-27: descriptor-bound output creation

policy focused98件PASS、全回帰121 suites/3803実行＝3795PASS＋8skip、87.595秒、log SHA256 `f84bca0f904bc6e8f1ee5302a962b20394cf707c4adc4a42b65fdb7fc9e80ab7`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-fd-output-checks/20260927/`。

既存の作成前symlink検査と`Path.mkdir`の間に隔離tempの親をsymlinkへ入れ替えると、旧実装は拒否しつつリンク先に新ディレクトリを作った。親の各componentを`O_DIRECTORY|O_NOFOLLOW`で開き、欠けた階層と最終token directoryを開いた親fd相対で作成。最後にpathが同じ親/子inodeを指すことを再確認し、差し替え時は`OUTPUT_PARENT_INVALID`で拒否。隔離tempの模擬保持treeでは差し替え先への新規directoryなし、通常のprivate nested creationと既存token拒否も確認。作成後にpathベースで続く保存処理までfdに固定したわけではなく、作成終了後の別actorによる継続的な差し替えは未防御。拒否されたpathや保持物は自動削除しない。Kubernetes/モデル/保持run/backup変更なし、Colima起動なし。WP local-partial。

## 2026-09-27: reject linked output ancestor before mkdir

policy focused96件PASS、全回帰121 suites/3801実行＝3793PASS＋8skip、89.067秒、log SHA256 `ad77fdba20917904274cb424d5d4737ea9a95b7daa13b574e25b2ca161ae157a`。design-context/secret scan PASS、専用Colima Stopped。private記録は`tmp/local-experiment/policy-candidate-output-ancestor-checks/20260927/`。隔離tempの模擬保持treeのみを負例に使用し、実保持dataに変更なし。

Standalone候補workflowの`create_output()`は親pathのsymlink検査より先に`mkdir(parents=True)`を呼んでいた。保存先の祖先を別の保持treeへのsymlinkにすると、拒否エラーを返しながらリンク先へ新しいディレクトリを作ることを隔離tempで再現。既存祖先のsymlinkを作成**前**に拒否し、作成後の再検査も維持した。これにより既存リンク経由の意図しない書込みを防ぐ。競合する外部actorが検査と作成の間にpathを入れ替える状況までの原子的な防御は未証明。Kubernetes/モデル/保持run/backupには接続せず、root以外へ新しいartifactは残していない。復旧は拒否されたpathを自動で掃除せず、operatorが保存先を確認して新tokenで新規試行する。WP local-partial。

## 2026-09-27: candidate allowed response content guard (offline)

`--pod-stdin`のserver dry-runでexit 0のJSONを名前・namespaceだけで`allowed`にしていた。fake APIで同名別image、sidecar/init container、CPU request欠損、hostNetwork、hostPathを返すと誤通過することを修正前に再現。修正後は応答Podの限定policy featuresと送信したcontainer名/image、CPU・memory request/limitの意味値、ports、固定安全defaultを照合し、不一致は`unknown`とpendingを維持する。安全側のAPI defaultや等価quantity表現は受理する。生のPod/responseは保存しない。この照合は応答に含まれないcluster状態、すべてのmutation、任意のPod field、admission webhook不在の独立証明ではない。今回はColimaを起動せず、live admission受入の更新なし、WP local-partial。回復は新しいdry-run/保存証拠で行い、未知結果をallowに再分類・自動再送しない。

policy focused95件PASS、全回帰121 suites/3800実行＝3792PASS＋8skip、88.253秒、log SHA256 `b617aac864ae427ee0af592fe8de8f519578078d4a3616a8e61b4a2d7959f73d`。design-context/secret scan PASS。private検証記録は`tmp/local-experiment/policy-candidate-response-checks/20260927/`。

## Summary

Local実験では固定candidateのrule違反・適用範囲を実native admissionと照合する。
モデルは助言だけを返し、権限付与・本番Tier/PRリスク判断は未実装とする。

## 2026-09-18: bounded caller Pod candidate plan

固定15caseだけの実証から、利用者がstdinで渡す限定Pod1件を事前検査できる入口へ進める。
`policy check --pod-stdin`はhostの厳密なinput validation、safe featureだけを使うQwen分類、
同じ候補のnative server dry-runを順に照合する。権限付与・workload実作成・policy変更は行わない。

- [x] 16KiB以内のJSON1文書・Pod1件/container1個と限定fieldだけを受理する。
- [x] 不明field・Secret/環境変数/任意command等を推論前に拒否し、原候補を保存しない。
- [x] P01–P06のhost期待値を独立計算し、特にregistry portとresource値をnative CELと照合する。
- [x] モデルの全違反集合と実APIが報告する違反subsetを区別する。
- [x] protected namespaceと16policy/bindingの同一性、pending intent、dry-run後の不存在を検証する。
- [x] 適合・privileged違反・推論前拒否の3経路、保存status、取消・不明結果を実証する。

送信名はhostが生成し、固定の安全側defaultを補う場合は変換を記録する。
入力/送信bodyのhashと特徴・型付き結果だけを残し、raw manifest/image名/原API応答を保存しない。
原文を保存しないため、後日の再現には元JSONの再提出とhash一致が必要である。
未知結果をallowへ変換せず、自動再送しない。既存15case corpusと認可境界の評価は維持する。

## Context

このWork PackageはLevel L2「Agent第一陣」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Scope

- 設計・実装・設定の最小単位を定義する。
- 関連するREADME/Runbook/ADRを更新する。
- CIで検証できる形にする。

## Non-scope

- Level外の大規模OSS追加。
- 本番データ利用。
- GxP対応。
- 人間承認が必要な危険操作の自動化。

## Deliverables

- 実装または設定ファイル
- ドキュメント更新
- テストまたは検証ログ
- 必要に応じてADR/Runbook

## Acceptance Criteria

- [ ] 変更がGitHub PRとして提出される。
- [ ] CIが通る。
- [ ] Policy checkが通る。
- [ ] `shirokuma doctor`または関連コマンドで状態確認できる。
- [ ] README/Obsidian noteが更新される。
- [ ] 未解決High riskがない。

## Suggested Labels

`level:l2`, `area:policy`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L2-POL-001: Policy Gatekeeper Bear.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local development and fixed server dry-run verification.
The model identifies observations; it cannot grant authorization or weaken policies.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17 local bounded implementation plan

既存native admission baselineの15 server dry-run corpusを再利用し、
モデルが固定candidateのどのrule違反を観測するかを実APIの結果と照合する。
候補はneutral IDsとsafe featureだけにし、allow/denyを含むcase名やhost expected answerを
モデル入力に渡さない。Model出力はviolation/scope-limit/verification IDsのみ。
モデルにallow/approve/Tier付与やpolicy緩和を決定させない。

- [x] 15固定candidateのモデル入力がexpected答えを含まないことを検証する。
- [x] Native policyのsource/manifest identityとready状態を照合し、固定server dry-runだけを行う。
- [x] モデルのrule/limit/verification選択をhost truthと実Admissionへ照合する。
- [x] default namespace controlのallowを安全性や実作成権限へ変換しない。
- [x] ADR-0034の範囲内/範囲外をhostが固定分類し、modelやK8s成功で権限を昇格しない。
- [x] 実Qwen評価、実API比較、Pawprintと保存証跡、README/Vault更新を確認する。

実Pod等の作成、policy apply/delete、GitHub書込、外部公開、追加費用、保持データ削除、
global host変更、全Colima resetはこのentrypointでは実行しない。
Local commitや新規使い捨て実験データ再作成には既存の包括承認があり、
この実装は都度owner承認やGitHub review待ちを再導入しない。
K8s baselineのnamespace適用範囲、image digest syntaxと署名/CVE承認の違い、
server dry-runと実workload動作の違いを明示する。
PRリスクや本番Tier判定・一般的な権限判断の品質は未達として残す。

## 2026-09-17 local functional evidence

実Qwenはnative 15候補（5×3batch）と操作境界10候補（1batch）をhost truthと完全一致で分類。
入力3907/出力1171 tokens、推論合計33.125秒。全分類完了後だけ固定15server dry-runを実行し、
allow2/deny12/対象外namespace control1が期待と一致した。権限付与・境界操作・保存workloadは0。
7件のAgentHouse/Loki/保存OTelとcompleted checkpointを確認した。

実workflowは`tmp/local-experiment/policy-bear/f37433c5b92d462a8ea69f4465c334b5/`。
04:31 UTCの独立再観測で保持6PodのUID/phase/ready/restartsと16policy/binding identitiesは不変、
両namespaceにdry-run残存objectは0。事前/事後証跡は
`tmp/local-experiment/policy-bear-preflight/fa25204603de4af38a659279a90a3dd0/`。
Pending candidateをAPI前に保存し、確認後に結果を保存する。途中失敗・signal・保存障害では
未知の結果と既知/未知token使用を保持し、次の推論/操作を止めることをfakeで確認した。

8合成case×2の実Qwen評価は16/16成功、16監査readback。
入力12454/出力2768 tokens、推論81.187秒、retry0。
`tmp/local-experiment/policy-evaluation/11754e69d7ce4ef89e8e231b5ad5a629/`に全attemptを保存。
Core12＋Bear18の新規tests、既存native17 testsを含む全回帰486 tests、設計73documentsが成功。

再実行は`make local-policy-bear-eval`と`make local-policy-bear-smoke`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_GATEKEEPER.md)に境界・中断証跡・終了方法を記録。
今回の成功は固定25分類と15dry-runに限る。任意policy、一般的なリスク判断、
実workload作成、署名/CVE保証、本番受入、historical GitHub gatesの完了を主張しない。

## 2026-09-18 caller Pod policy CLI evidence

`policy check --pod-stdin`と`policy status TOKEN`を実装した。16KiB以内の限定Pod1件から
safe boolean featuresを作り、独立host期待値・Qwen分類・同じmemory bodyへのdry-runを照合する。
入力のraw bytesはargvや保存filesへ出さず、入力/body hash・変換ID・features・型付き結果を残す。
namespace/name、Never設定、service account token無効だけを補い、policy違反は修復しない。
limitだけでrequestがない入力と、hostNetworkの暗黙hostPortはnative defaultingで判定が変わるため
事前拒否する。resource数量は限定した正の文字列と大小関係を検証し、SI kiloは小文字`k`とする。

実compiled CLIで以下を確認した。各workflowはモデル1回、dry-run1回、retry/cloud呼出し0。

| 保存token | 結果 | Qwen入力/出力 tokens | 推論ms |
|---|---|---:|---:|
| `255b21eed54252825d90870c0811f86f` | passed / allowed、exit0 | 675 / 74 | 4080 |
| `d906b723859c1be5095da0c786ddabb6` | failed / unknown、exit1 | 675 / 84 | 3148 |
| `1e0a67ec0d8785febb8539f1580f5b9f` | passed / denied P01、exit2 | 675 / 84 | 3146 |

最初のprivileged試行はモデルP01一致後、APIのInvalid/422形式をForbidden/403専用parserが
認識できずunknownで停止した。元証拠を保持し、別の新候補によるmodel無しの診断dry-run1回も
unknownとして保持した。Kubernetes v1.33.4の一次sourceに沿ってexact Invalid単一cause形式を追加し、
新tokenで拒否を実証した。policy名・binding名・固定messageの完全一致を維持し、foreign/schema/
混在/余分な末尾は認識しない。未知の元試行を再送・成功への書換えはしていない。

未対応env入力 `02c0bd3c1089d36dd579130a0c2a7f21` はINPUT_INVALIDで推論/API/intent保存前に拒否。
namespace UID/label、空LimitRange、16policy/binding UID/generation/spec/owner/source hashを固定し、
API前のpending intentをfsync、後の不存在とbaseline不変を確認した。権限付与・保存workloadは0。
成功2回と失敗1回に各4件のAgentHouse/Loki/保存OTelを残した。モデル原文・raw API応答は保存しない。
3tokenのstatus各3回（計9回）で32のfiles/directoriesのhash/mode/mtimeが不変だった。
statusは保存features/host期待値/plan/型付き結果/spans/hash sealを検証し、原manifestの再構成や
最新admissionの再観測を主張しない。旧15case corpusは別入口のまま維持した。

Python新規78件（入力27/観測17/workflow13/CLI21）、Go91 top-level testsと59 subtests、
vet/raceが成功。実Python bridge＋実workflow/外部fakeの7経路でexit/取消/再送防止/非保存を検証。
全local回帰1658件（1657成功・既存optional OTel1skip）、設計83documents/関連14tests、secret検査も成功。
証拠は `tmp/local-experiment/policy-cli-checks/38ccaf07c7e64c93935b49fa18234d4d/`、
各workflowは `tmp/local-experiment/policy-candidate-bear/<token>/`、
独立監査は `policy-candidate-preflight/6abcff1fbcd841e9be85f43791c848b2/`。
[Runbook](../../../../tools/local_experiment/bears/POLICY_CLI.md)に入出力と停止時の扱いを記録した。
実証は限定PodのP01適合/違反と入力拒否であり、全P01–P06のcaller入力live網羅、任意manifest、
画像署名/CVE、scheduling、アプリ動作、権限判断、本番受入の完了とは区別する。

独立の軽量再観測でAgentHouseは453→465（上記3workflow各4件）、旧453件の全行hashは不変。
対応Loki各4件、保持3410files、14Pods/3Docker/MLflow identity、16policy/bindingとnamespaceは不変、
LimitRange空、candidate Pod残存0、既存tunnel閉鎖を確認した。深いDB/metadata/fixture観測は
直前のQuery/SRE監査の保存証拠を継承し、今回は各ストアの全内容を再走査していない。
