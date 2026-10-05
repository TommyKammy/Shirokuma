---
project: Shirokuma
doc_id: "WP-L6-FIN-001"
title: "FinOps Gatekeeper with inference and disk watermark cost"
status: local-partial
created: 2026-07-05
updated: 2026-10-06
version: "0.138"
area: "workpackage"
tags: [shirokuma, wp, l6, finops]
---

# FinOps Gatekeeper with inference and disk watermark cost

## 2026-10-06: separate saved-history boundary verification (offline)

Independently checked the existing detached direct-render selection/digest
tests and Docker volume reference-versus-usage contract after the policy
source-isolation change. `make test-local-finops` passed 214 cases (seven
Linux-only skips); the broader local verifier also passed. No collection,
report or reader mutation was needed;
AgentHouse volume usage remains `null / NOT_MEASURED`. This is not saved
producer authentication, present storage measurement, backup recovery or
operator acceptance. Preserve saved reports; original Vault sync pending.

## 2026-10-06: independent saved-history and volume-boundary recheck (offline)

After the separate policy pull-policy repair, reviewed the unchanged direct
history snapshot/digest handling and Docker volume reference-versus-usage
boundary. `make test-local-finops` passed 214 cases (seven Linux-only skips),
and the local verifier passed. No collection, report reader change or retained
report/volume/backup mutation. Dedicated Colima was freshly observed stopped;
AgentHouse bytes remain `null / NOT_MEASURED`. Offline checks do not authenticate
saved producers, measure current usage or establish backup recovery/operator
acceptance. No FinOps rollback; retain saved reports. Original Vault sync
pending outside checkout.

## 2026-10-06: earlier selection replacement regression (offline)

A direct-render caller replacing its earlier selection while the later detached
report is validated cannot redirect the already pinned first sample: the
unchanged reader preserves both original input indices in HTML. Focused FinOps
suite passed 214 tests (seven Linux-only skips); no collection, saved report,
volume or backup mutation. This is not concurrent atomic serialization or
producer authentication; AgentHouse bytes remain `null / NOT_MEASURED`.
Rollback only the test if necessary, retaining historical reports. Original
Vault sync pending outside checkout.

## 2026-10-06: earlier history sample stays pinned during later validation (offline)

A new two-report regression changes the caller-owned first report while the
detached second report is validated. The unchanged renderer displays the
original first snapshot, not the injected model name; FinOps focused suite
passes 213 cases (seven Linux-only skips). The test covers a sequential
mutation at this boundary, not atomic serialization or saved producer
authentication. No collection or retained report, volume or backup change;
AgentHouse bytes remain `null / NOT_MEASURED`. Revert only the regression if
necessary, preserving saved reports. Original Vault sync pending outside checkout.

## 2026-10-06: independent offline boundary recheck

After the policy image-source label repair, the unchanged FinOps history,
Docker volume reference and usage-boundary tests passed 212 cases (seven
Linux-only skips). No collection, reader change or retained report, volume or
backup mutation. Colima was freshly observed stopped. AgentHouse usage remains
`null / NOT_MEASURED`; this check is not live measurement, trusted VM
authentication or backup recovery. No FinOps rollback needed; retain reports.
Original Vault sync pending outside checkout.

## 2026-10-06: independent saved-history and volume boundary recheck (offline)

After the separate policy image-source repair, reviewed the unchanged detached
history reader and Docker volume reference-versus-usage boundary. `make
test-local-finops` passed 212 tests (seven Linux-only skips); no collection,
reader modification or retained report/volume/backup mutation. Dedicated
Colima was freshly observed stopped. AgentHouse bytes remain `null /
NOT_MEASURED`; this does not authenticate saved producers or prove current
usage, cleanup or backup recovery. No rollback required; preserve reports.
Original Vault sync pending outside checkout.

## 2026-10-06: independent caller selection replacement regression (offline)

The direct history renderer refuses a caller replacing its second selected
entry with the first while the first detached snapshot is being validated.
The new regression passed against the unchanged reader; focused FinOps suite
passed 212 tests (seven Linux-only skips). This does not make concurrent
serialization atomic or authenticate saved producer data. No collection or
retained report, volume or backup mutation; AgentHouse bytes remain `null /
NOT_MEASURED`. Revert only this regression if needed, preserving saved reports.
Original Vault sync pending outside checkout.

## 2026-10-06: later saved-history report mutation regression (offline)

An independent test changes the second caller-owned report while validation
of the first report is in progress. The direct history renderer rejects the
second report's now-stale digest before emitting HTML; the existing reader
needed no modification. The FinOps offline suite passes 211 tests (seven
Linux-only skips). This does not make an individual concurrent serialization
atomic or authenticate saved producer data. No collection or saved reports,
volumes or backups were modified; AgentHouse bytes remain `null /
NOT_MEASURED`. Revert only this test if necessary, retaining saved reports.
Original Vault sync pending outside checkout.

## 2026-10-06: direct history validation uses detached report (offline)

Direct history rendering previously validated a caller-owned dict before
copying its canonical bytes. An isolated mutation during validation caused
the checked report to diverge from the later digest input; the regression
failed before the change. Rendering now canonicalizes first and uses the same
detached report for validation, observation ordering, digest and HTML. FinOps
focused suite: 210 cases (seven Linux-only skips), including the original
post-digest mutation negative. No collection, saved report, retained volume or
backup change; dedicated Colima observed stopped. This does not make concurrent
serialization atomic or authenticate producer data; AgentHouse bytes remain
`null / NOT_MEASURED`. Roll back only the history reader/test, keeping saved
reports. Original Vault sync pending outside checkout.

## 2026-10-06: independent single-report snapshot regression (offline)

A direct renderer mutation test confirms the existing `validate_public` deep
copy prevents later changes to a caller-owned model name from reaching HTML.
The test passed before any renderer edit; no new defect or collection is claimed.
The FinOps suite passed 209 tests (seven Linux-only skips), and the local
verifier passed with the new regression. Colima was observed stopped;
saved reports, volumes and backups were untouched. This does not authenticate
report provenance or measure AgentHouse (`null / NOT_MEASURED`). Revert only the
regression test if necessary; retain saved reports. Original Vault sync pending.

## 2026-10-06: detached saved-history rendering (offline)

The direct offline renderer validated a caller-owned report and its saved
digest, then read that mutable report again to produce HTML. A test-owned
mutation between those steps put a different model name under the original
digest; the negative failed before repair. Rendering now uses a detached
canonical JSON snapshot made when each digest is checked. The FinOps suite
passed 208 tests (seven Linux-only skips). No collection or saved report,
volume or backup modification; dedicated Colima was observed stopped. This
increment also passed `make verify-local-experiment`, design-context, secret
scan and diff check. The initial negative used a wrong test helper reference;
after correction it reproduced the mutated output before repair. This does
not authenticate saved report provenance, establish atomic external
reads, measure AgentHouse (`null / NOT_MEASURED`), or imply operator acceptance.
Rollback: restore only the direct-render snapshot/test; keep saved reports.
Original Vault sync pending outside checkout.

## 2026-10-06: independent offline suite after policy rollback result change

The unchanged 207-test FinOps suite passed (seven Linux-only skips), separate
from WP-L2-POL-001's rollback retention claim correction. No collection,
saved-report or reader change; dedicated Colima was observed stopped. AgentHouse
volume bytes remain `null / NOT_MEASURED` pending authenticated VM measurement,
worker cleanup and backup recovery. No FinOps rollback needed; preserve saved
reports. Original Vault sync pending outside checkout.

## 2026-10-06: independent saved-history boundary recheck (offline)

Independently of the policy rollback repair, reviewed the existing bounded
saved-history parser, digest/order checks and per-report storage coverage;
`make test-local-finops` passed 207 tests (seven Linux-only skips), and the
local verifier passed. No new collection or FinOps code change, and no saved
report, retained volume or backup modification; dedicated Colima was freshly
observed stopped. An AgentHouse Docker volume reference is not measured
usage: bytes remain `null / NOT_MEASURED` pending authenticated VM measurement,
worker cleanup and backup recovery. No rollback needed; preserve prior reports.
Original Vault sync pending outside checkout.

## 2026-10-06: independent saved-history and volume-scope verification (offline)

After the separate binding-continuity repair, reviewed the unchanged saved
history digest/selection and per-report storage scope. `make test-local-finops`
passed 207 cases (seven Linux-only skips), and the local verifier passed. No
FinOps reader, collection, retained report, volume or backup changed; Colima
was observed stopped. Docker volume reference remains distinct from measured
bytes: AgentHouse stays `null / NOT_MEASURED` pending authenticated VM
measurement, worker cleanup and backup recovery. No FinOps rollback; preserve
prior reports. Original Vault sync pending outside checkout.

## 2026-10-06: independent saved FinOps regression (offline)

Following the separate policy pre-binding repair, `make test-local-finops`
passed its saved history and volume-scope suite (seven Linux-only skips), and
`make verify-local-experiment` passed. No FinOps reader or retained report,
volume or backup was changed; no new collection. Dedicated Colima was observed
stopped. A volume reference is not measured bytes: AgentHouse remains `null /
NOT_MEASURED` pending authenticated VM measurement, worker cleanup and backup
recovery. Preserve prior reports; no FinOps rollback needed. Original Vault sync
pending outside checkout.

## 2026-10-06: independent saved-history boundary review (offline)

Reviewed the unchanged history parser's bounded input, canonical digest and
observation-time ordering, and its per-report storage coverage independently
of the policy presence repair. `make test-local-finops` passed 207 tests
(seven Linux-only skips); the local verifier passed. No report collection,
reader change or retained report/volume/backup mutation. Dedicated Colima was
observed stopped. A Docker reference is not measured usage: AgentHouse stays
`null / NOT_MEASURED` pending authenticated VM measurement, worker cleanup and
backup recovery. Preserve saved reports; no FinOps rollback required. Original
Vault sync pending outside checkout.

## 2026-10-06: independent saved-history/volume boundary check (offline)

Reviewed the unchanged `history.py` parser, canonical digest/order and
per-report storage scope independently of the activation fix. `make
test-local-finops` passed 207 tests (seven Linux-only skips), and `make
verify-local-experiment` passed. No report collection or reader change; Colima
was freshly observed stopped. Docker volume reference remains distinct from
measured bytes: AgentHouse is `null / NOT_MEASURED` until authenticated VM
measurement, worker cleanup and backup recovery are established. No retained
volume, saved report or backup was modified. No FinOps rollback; preserve saved
reports. Original Vault sync pending outside checkout.

## 2026-10-06: separate saved-history and storage recheck (offline)

Reviewed the unchanged history renderer's per-report storage coverage and
canonical digest/selection ordering after the independent policy change.
`make test-local-finops` passed 207 tests (seven Linux-only skips); the local
verifier also passed. No collection, saved report, retained volume or backup
mutation; dedicated Colima was observed stopped. A Docker volume reference
does not supply used bytes: AgentHouse remains `null / NOT_MEASURED` until
authenticated VM measurement, worker cleanup and backup recovery are checked.
No FinOps rollback; preserve prior reports. Original Vault sync pending
outside this checkout.

## 2026-10-06: independent offline history/storage regression

After the separate policy activation UID fix, the unchanged FinOps suite
passed 207 tests (seven Linux-only skips) via `make test-local-finops`; the
full local verifier also passed. A direct unittest invocation of the history
module failed because its sibling fixture import needs the Make target's test
path; it was not a product failure. No reader, report, collection or retained
volume/backup changed; Colima was observed stopped. Docker reference is not
used bytes: AgentHouse remains `null / NOT_MEASURED` pending authenticated VM
measurement, worker cleanup and backup recovery. No FinOps rollback; preserve
reports. Original Vault sync pending outside checkout.

## 2026-10-06: separate saved-history boundary recheck (offline)

Reviewed saved-history canonical digest and ordered selection, plus per-report
storage coverage without editing the reader or collecting data. `make
test-local-finops` passed 207 cases (seven Linux-only skips); the full local
verifier also passed. Dedicated Colima was observed stopped. Docker volume
reference is not used bytes: AgentHouse remains `null / NOT_MEASURED` until a
trusted VM measurement, worker cleanup and backup recovery can be verified.
No saved report, retained volume or backup was modified; no FinOps rollback.
Original Vault sync pending outside the checkout.

## 2026-10-06: independent saved-history and storage boundary review (offline)

After the policy terminating-resource repair, reviewed the unchanged saved
history selection/digest/order and per-report storage coverage. A Docker
volume reference is not used bytes or proof of current VM identity. `make
test-local-finops` passed 207 tests (seven Linux-only skips); the full local
verifier passed independently. Dedicated Colima was observed stopped; no
collection, reader change, saved report, retained volume or backup mutation.
AgentHouse bytes remain `null / NOT_MEASURED`; authenticated VM measurement,
worker cleanup and backup recovery remain open. No FinOps rollback; preserve
reports. Original Vault sync pending outside this checkout.

## 2026-10-06: separate saved-history/storage boundary review (offline)

After the independent rollback change, reviewed the unchanged saved-history
digest, index/order and per-report storage coverage: a Docker volume reference
is not measured bytes. `make test-local-finops` passed 207 tests (seven
Linux-only skips); `make verify-local-experiment` passed. No new collection,
reader change, saved report, volume or
backup mutation; Colima was observed stopped. AgentHouse usage remains `null /
NOT_MEASURED`; producer authenticity, current VM measurement, worker cleanup
and backup recovery remain open. No FinOps rollback; preserve saved reports.
Original Vault sync pending outside checkout.

## 2026-10-06: independent offline storage/history check

After the separate policy reply/status guards, the unchanged FinOps suite
passed 207 tests (seven Linux-only skips). Saved history keeps digest/order
and Docker volume reference separate from measured used bytes. Dedicated
Colima was freshly observed stopped; no collection, report, retained volume
or backup mutation. AgentHouse usage remains `null / NOT_MEASURED`; producer
identity, current VM measurement, worker cleanup and backup recovery remain
open. No FinOps rollback; preserve reports. Original Vault sync pending.

## 2026-10-06: independent offline FinOps regression at policy milestone

After the separate policy operation-inventory repair, `make test-local-finops`
passed 207 tests (seven Linux-only skips); the full local verifier passed.
Saved-history digest/order and per-report Docker reference versus measured usage
remain distinct under the unchanged reader. No new collection, saved report,
retained volume or backup mutation. Dedicated Colima was freshly observed
stopped. AgentHouse bytes remain `null / NOT_MEASURED`; no producer
authentication, current VM measurement, worker cleanup or backup recovery was
established. Preserve historical reports; no FinOps rollback needed. Original
Vault sync pending outside this checkout.

## 2026-10-06: independent offline saved-history boundary check

Alongside the separate policy rollback change, inspected the saved-history
digest/selection ordering and per-report storage coverage (Docker reference
versus measured usage); `make test-local-finops` passed 207 tests with seven
Linux-only skips. No FinOps reader, collection, saved report, retained volume
or backup changed. Dedicated Colima was freshly observed stopped. AgentHouse
used bytes remain `null / NOT_MEASURED`; this does not authenticate the saved
producer or measure current VM usage, cleanup or backup recovery. Preserve
historical reports; no FinOps rollback. Original Vault sync pending.

## 2026-10-06: separate saved-history/volume review (offline)

After the rollback preflight change, reviewed the independent saved history
parser/render digest, selection ordering and per-report Docker reference versus
measured usage boundaries. `make test-local-finops` passed 207 tests (seven
Linux-only skips), and the full local verifier passed. No FinOps code or saved
report changed; Colima was freshly observed stopped, without new collection or
retained volume/backup mutation. AgentHouse used bytes remain `null /
NOT_MEASURED`; no authenticated current VM measurement, cleanup or backup
recovery is established. No FinOps rollback; preserve prior reports. Original
Vault sync pending outside checkout.

## 2026-10-06: independent saved-history and volume boundary check (offline)

After the separate policy rollback repair, reviewed the saved-history digest,
selection and ordering boundaries and the per-report Docker reference/usage
distinction. `make test-local-finops` passed 207 tests (seven Linux-only skips),
and `make verify-local-experiment` passed;
no FinOps reader, collection, saved report, retained volume or backup changed.
Colima was freshly observed stopped. AgentHouse used bytes remain `null /
NOT_MEASURED`; this does not establish producer identity, authenticated VM
usage, worker cleanup or backup recovery. Preserve historical reports; no
FinOps rollback needed. Original Vault sync pending outside checkout.

## 2026-10-06: independent saved history and volume boundary (offline)

Alongside the separate native policy projection repair, reran 207 FinOps
tests (seven Linux-only skips) and the full local verifier. The saved history
digest/order and Docker volume reference versus measured bytes remain separate;
no FinOps reader or collection code changed. Dedicated Colima was observed
stopped, with no retained report, volume or backup mutation. AgentHouse usage
remains `null / NOT_MEASURED`; independent VM authentication, remote cleanup
and backup recovery remain open. Preserve saved reports; no FinOps rollback.
Original Vault sync pending outside checkout.

## 2026-10-06: independent offline history/storage recheck

After the separate native policy ownership change, the saved history/storage
scope and Docker reference-versus-used-byte distinction remained under the
unchanged reader. `make test-local-finops` passed 207 tests (seven Linux-only
skips); the full local verifier passed. Dedicated Colima was observed stopped;
no collection, saved report, retained volume or backup mutation was attempted.
AgentHouse used bytes remain `null / NOT_MEASURED`; producer identity, present
VM usage, worker cleanup and backup recovery remain open. No FinOps rollback;
preserve reports. Original Vault sync pending outside checkout.

## 2026-10-06: independent history/storage boundary check (offline)

Reviewed the direct saved-history index/digest/order checks and per-report
Docker reference versus usage display after the separate policy boundary
change. `make test-local-finops` passed 207 tests (seven Linux-only skips).
No reader change, new collection, retained volume/report/backup access or
usage measurement was justified. Dedicated Colima was observed stopped;
AgentHouse bytes remain `null / NOT_MEASURED`. Producer identity, authenticated
VM usage and backup recovery remain open. Preserve historical reports; no
FinOps rollback needed. Original Vault sync pending outside checkout.

## 2026-10-06: direct saved signed-zero digest control (offline)

An independent direct-render regression verifies that two validated reports
whose float-zero sign differs have the same history-only canonical digest and
cannot be displayed as separate observations. A distinct two-report control
still renders without collecting. The existing reader was unchanged; the first
test attempt addressed a nonexistent fixture field and failed, then the
corrected test and all 207 FinOps tests passed (seven Linux-only skips).
Dedicated Colima was observed stopped; no collection, retained report/volume
or backup mutation. AgentHouse bytes remain `null / NOT_MEASURED`; saved inputs
do not authenticate producers or measure current usage. Revert only this test
if necessary, preserving reports. Original Vault sync pending outside checkout.

## 2026-10-06: independent saved-history and volume gate review (offline)

Reviewed the history-only signed-zero canonicalization, selected digest binding,
per-report Docker reference/usage separation and Linux-only worker fixture
boundary without changing the FinOps reader. `make test-local-finops` passed
206 tests (seven Linux-only skips). Dedicated Colima was freshly observed
stopped; no collection, retained report/volume/backup mutation or new usage
measurement. AgentHouse bytes remain `null / NOT_MEASURED`; independent VM
identity, cleanup and backup recovery remain open. No rollback for this review;
preserve saved reports. Original Vault sync pending outside this checkout.

## 2026-10-05: signed float zero saved-history correlation (offline)

The saved-history parser previously counted otherwise identical reports with
`0.0` and `-0.0` in a numeric field as two observations because their canonical
JSON digests differed. History-only canonicalization now maps both to positive
float zero in the parser and direct renderer; deep malformed direct input still
fails closed. A distinct two-report control still renders; 206 FinOps tests
and full local verifier passed (seven Linux-only FinOps skips). No collection, saved report rewrite or
retained-volume/backup change. Dedicated Colima was observed stopped; AgentHouse
used bytes remain `null / NOT_MEASURED`. Saved identity cannot authenticate a
producer, measure present usage or establish operator acceptance. Restore only
the history reader/test if needed, retaining original reports. Original Vault
sync pending outside this checkout.

## 2026-10-05: independent saved coverage and volume boundary review (offline)

After the policy renderer increment, separately reviewed the saved history's
per-report Docker reference/usage distinction and the Linux-only volume worker
fixture gates; `make test-local-finops` passed 205 tests (seven Linux-only
skips). No FinOps code, saved reports, retained volumes or backups changed.
Dedicated Colima was freshly observed stopped; AgentHouse used bytes remain
`null / NOT_MEASURED`. No authenticated VM measurement, safe worker cleanup or
backup recovery has been established. Preserve prior reports; no rollback for
this independent review. Original Vault sync pending outside this checkout.

## 2026-10-05: separate offline storage boundary check

Alongside the native policy reader increment, independently reran `make
test-local-finops` (205 tests, seven Linux-only skips) and checked the existing
saved-history/storage coverage and Docker reference-versus-usage gates. No
FinOps reader, saved report, retained volume or backup changed. The dedicated
Colima profile was observed stopped, so AgentHouse used bytes remain `null /
NOT_MEASURED`. Trusted VM binding, safe worker/cleanup proof and backup
recovery remain open; fixture results do not establish current usage or operator
acceptance. No rollback for this check; preserve historical reports. Original
Vault sync pending outside this checkout.

## 2026-10-05: independent saved-history and volume gate recheck (offline)

After the policy CLI increment, reviewed the saved-history selected-report
scope and Docker-volume reference versus usage gate independently. The focused
FinOps suite passed 205 tests (seven Linux-only skips); no reader change or
volume measurement was justified. Dedicated Colima was observed stopped, with
no SSH enrollment, retained volume/backup scan, report mutation or new
collection. AgentHouse `used_bytes=null / NOT_MEASURED` remains unchanged;
trusted VM binding, remote worker/cleanup verification and backup recovery
remain open. No rollback is needed for this review; preserve historical reports.
Original Vault sync pending outside checkout.

## 2026-10-05: independent volume acceptance boundary recheck (offline)

After the policy source increment, independently checked the AgentHouse
Docker reference/usage separation, Linux-only worker/supervisor fixture gates,
receipt provenance limits and saved history coverage. `make test-local-finops`
passed 205 tests (seven Linux-only skips). This is a review, not a new volume
measurement or a reader change; dedicated Colima remained stopped. No SSH
enrollment, VM `/proc` probe, retained volume/backup scan or deletion. The
independent trusted VM binding, remote worker and cleanup verification and
backup recovery remain open; `used_bytes=null / NOT_MEASURED` is unchanged.
No rollback for this review; retain reports and backups. Original Vault sync
pending outside checkout.

## 2026-10-05: independent saved-history scope review (offline)

Reviewed the saved input bound, strict duplicate-key and numeric decoding,
canonical digest/selection binding, and per-report storage coverage separately
from the native policy increment. No FinOps reader change was justified;
`make test-local-finops` passed 205 tests with seven Linux-only skips. Dedicated
Colima was observed stopped and no collection or retained report/volume/backup
mutation was performed. Current AgentHouse bytes remain `null / NOT_MEASURED`;
saved report consistency neither authenticates its producer nor grants operator
acceptance. No rollback for this review; preserve historical reports. Original
Vault sync pending outside checkout.

## 2026-10-05: independent saved integer spelling control (offline)

The unchanged saved-history reader treats `-0` and `0` for a saved restart
count as the same canonical report, refusing the pair as two observations.
The negative and a distinct two-report control passed; 205 FinOps tests passed
(seven Linux-only skips). No collection, retained report/volume/backup change
or current AgentHouse measurement (`null / NOT_MEASURED`); Colima was freshly
observed stopped. Saved values do not authenticate producers or grant operator
acceptance. Revert only the test if necessary; preserve saved reports. Original
Vault sync pending outside checkout.

## 2026-10-05: saved-history boundary recheck alongside policy parsing (offline)

Independently checked the history input cap, canonical digest/index/order
binding and per-report storage coverage; existing 204 FinOps tests passed
(seven Linux-only skips). No FinOps behavior changed and no collection was
started; dedicated Colima was observed stopped. Saved reports do not
authenticate their producers or provide current AgentHouse bytes (`null /
NOT_MEASURED`), and no operator acceptance is claimed. No FinOps rollback;
preserve retained reports and backups. Original Vault sync pending.

## 2026-10-05: independent saved-history review (offline)

Reviewed the strict saved JSON bound, direct renderer's digest/index and
chronological checks, and per-report storage coverage. No FinOps code change
was justified; `make test-local-finops` passed 204 tests (seven Linux-only
skips) and the local verifier completed. Dedicated Colima was observed
stopped; no collection or retained report/volume/backup mutation. AgentHouse
usage remains `null / NOT_MEASURED`; saved values cannot authenticate their
producers or establish operator acceptance. No rollback is needed for this
read-only review; preserve saved reports. Original Vault sync pending.

## 2026-10-05: separate saved-history verification (offline)

Reviewed direct saved selection ordering, digest binding, byte caps and
per-report storage scope; no reader or renderer change was warranted. The
204-test FinOps suite passed (seven Linux-only skips), alongside the local
verifier for the separate policy increment. Colima was observed stopped; no
collection or retained report/volume/backup mutation. AgentHouse usage remains
`null / NOT_MEASURED`; saved input cannot authenticate its producer or grant
operator acceptance. Preserve existing reports; no FinOps rollback. Original
Vault sync pending outside checkout.

## 2026-10-05: independent saved-history recheck (offline)

Rechecked the bounded strict-JSON input, saved digest/index binding, ordered
rendering and per-report coverage separation without modifying the reader.
The 204 FinOps tests passed (seven Linux-only skips), including saved input
negatives. No new collection or current-volume measurement was performed;
AgentHouse bytes remain `null / NOT_MEASURED`. Saved values cannot establish
their producer's authenticity or operator acceptance. No code rollback for
this read-only review; preserve reports and backups. Original Vault sync
pending outside checkout.

## 2026-10-05: independent saved-history boundary review (offline)

Reviewed the direct renderer's validated index/digest binding and chronological
ordering, parser input cap, and per-report storage coverage without changing
the reader. The 204 FinOps tests passed (seven Linux-only skips), as did the
local verifier. No collection,
saved report/volume/backup mutation or current usage measurement; AgentHouse
volume bytes remain `null / NOT_MEASURED`. Saved inputs do not authenticate
their producers or grant operator acceptance. No code rollback for this review;
preserve the reports. Original Vault sync pending outside checkout.

## 2026-10-05: nested escaped saved-key control (offline)

An escaped duplicate model-name key nested inside a saved inference group is
rejected by the unchanged strict history reader before rendering; a valid
two-report control renders. 204 FinOps tests passed (seven Linux-only skips),
as did the local verifier. Colima was observed stopped; no collection or
retained report/volume/backup mutation. Current AgentHouse usage remains
`null / NOT_MEASURED`; offline input does not authenticate a producer or grant
operator acceptance. Revert only this regression if needed; preserve saved
reports. Original Vault sync pending outside checkout.

## 2026-10-05: independent saved-history BOM control (offline)

The unchanged strict UTF-8 JSON reader rejects a BOM-prefixed saved two-report
payload; the same reports without a BOM parse. The fixture forbids collection;
203 FinOps tests passed (seven Linux-only skips), as did the local verifier.
Dedicated Colima was observed stopped; no retained report, volume or backup
mutation. Current AgentHouse usage remains `null / NOT_MEASURED`; synthetic
saved input cannot authenticate a producer or grant operator acceptance.
Rollback only the new regression; preserve saved reports. Original Vault sync
pending outside checkout.

## 2026-10-05: independent noncanonical saved UTF-8 control (offline)

The unchanged saved-history parser rejects an overlong UTF-8 spelling inside
a model name and accepts the valid two-report control; collection is forbidden
in the regression. 202 FinOps tests passed (seven Linux-only skips), as did the
full local verifier. Dedicated Colima remained stopped; no saved report,
volume or backup change. This does not authenticate report producers, measure
current AgentHouse bytes (`null / NOT_MEASURED`) or grant operator acceptance.
Rollback only the independent regression; preserve saved reports. Original
Vault sync pending outside checkout.

## 2026-10-05: independent saved model Unicode control (offline)

The unchanged saved-history validator rejects escaped lone surrogate model
names after decoding, while a valid surrogate pair in a distinct report
parses. The 201-test FinOps suite passed (seven Linux-only skips). No
collection, retained report/volume/backup mutation or VM start; dedicated
Colima was freshly observed stopped. Current AgentHouse bytes remain `null /
NOT_MEASURED`; fixtures cannot authenticate producers or grant operator
acceptance. Revert only the independent regression if needed; preserve saved
reports. Original Vault sync pending outside checkout.

## 2026-10-05: malformed direct saved selection index (offline)

A direct renderer selection with an unhashable list or object index is
rejected as `HistoryError`, not leaked as an uncaught Python exception. The
existing typed response boundary needed no change; a valid two-report control
still renders. The 200-test FinOps suite passed (seven Linux-only skips).
No collection or retained report/volume/backup mutation; dedicated Colima
was observed stopped. This fixture cannot authenticate saved producers,
measure present AgentHouse bytes (`null / NOT_MEASURED`) or grant operator
acceptance. Revert only this independent regression if needed; preserve
reports. Original Vault sync remains pending outside checkout.

## 2026-10-05: malformed saved history UTF-8 (offline)

Independent saved-history fixtures now pin rejection of stray and incomplete
UTF-8 sequences while a valid two-report control still parses. Collection is
disabled during the negative tests; the unchanged parser emits HistoryError.
Focused history 30 tests and the full local verifier passed. No current usage collection, retained report,
volume or backup mutation; dedicated Colima was observed stopped. AgentHouse
bytes remain `null / NOT_MEASURED`. This input boundary cannot authenticate
saved producers or grant operator acceptance. Revert only the regression if
needed; preserve retained reports. Original Vault sync pending outside checkout.

## 2026-10-05: saved-history CLI complete-output byte cap (offline)

The direct renderer correctly bounded its HTML bytes, but the CLI appended a
newline after the check and could emit one byte past the configured limit. The
CLI now checks the full UTF-8 payload before writing; an exact-cap negative
failed before repair and cap-plus-one succeeds. The 198-test FinOps suite
passed (seven Linux-only skips), as did the full local verifier. No collection,
saved report, volume or backup mutation; dedicated Colima observed stopped.
AgentHouse bytes remain `null / NOT_MEASURED`. This does not authenticate
producers, measure current usage or grant operator acceptance. Revert only
this CLI check/test if the output contract changes; preserve reports. Original
Vault sync pending outside checkout.

## 2026-10-05: independent saved HTML byte-limit control (offline)

The unchanged direct history renderer accepts output at the UTF-8 byte cap and
refuses one byte below the actual output size, even though its character count
is smaller. An independent fixture forbids collection; 197 FinOps tests passed
(seven Linux-only skips). No new collection or retained report/volume/backup
mutation; dedicated Colima was observed stopped. AgentHouse bytes remain
`null / NOT_MEASURED`. This does not authenticate saved producers, measure
current usage or establish operator acceptance. Revert only the regression if
needed; preserve saved reports. Original Vault sync remains pending.

## 2026-10-05: independent saved-selection numeric aliases (offline)

The direct saved-history renderer refuses both boolean and float aliases for
each selected index (0 and 1), even though Python compares them equal to the
integer indices. The existing guard needed no change. `make test-local-finops`
passed 196 tests (seven Linux-only skips); full local regression passed. Colima
was observed stopped; no collection or retained report/volume/backup mutation.
AgentHouse bytes remain `null / NOT_MEASURED`. Offline saved fixtures cannot
authenticate producers, measure current usage or grant operator acceptance.
Rollback is the independent test only; preserve saved reports. Original Vault
sync remains pending outside checkout.

## 2026-10-05: independent UTF-8 history input bound (offline)

A multibyte test-owned payload exceeds the saved-history byte cap while its
character count remains below it. The existing reader refuses it before
decoding/rendering; a valid two-report control still parses, and collection is
disabled in the test. Focused history 27 tests, `make test-local-finops` 196
tests (seven Linux-only skips), and `make verify-local-experiment` passed. The
dedicated Colima profile was observed stopped; no new collection or retained
report/volume/backup mutation. AgentHouse bytes remain `null / NOT_MEASURED`.
Offline bounds do not authenticate saved producers, measure present usage or
grant operator acceptance. Revert only the independent test if needed; preserve
saved reports. Original Vault sync remains pending outside checkout.

## 2026-10-05: independent offline history boundary review

Inspected `history.py` parse and direct render: UTF-8 input/report/output bounds,
canonical digest and distinctness, numeric overflow/underflow rejection,
selected-index chronology and per-report storage scope. No justified reader
change emerged. `make test-local-finops` passed 195 tests (seven Linux-only
skips). Dedicated Colima was observed stopped; no collection or retained
report, volume or backup mutation. AgentHouse bytes remain `null /
NOT_MEASURED`; offline checks neither authenticate saved producers nor measure
current usage or grant operator acceptance. Preserve reports; no FinOps code
rollback. Original Vault sync remains pending outside checkout.

## 2026-10-05: distinct saved-history count boundary (offline)

An independent upper-bound fixture now uses eight distinct valid observations,
renders all eight without collection or truncation, and rejects a ninth
distinct report. The older nine-identical-reports negative could also fail
on duplicate identity, so it did not isolate the count boundary. Focused
FinOps suite passed. No collection, saved report, retained volume or backup
mutation; Colima was observed stopped. AgentHouse bytes remain `null /
NOT_MEASURED`; this synthetic bound does not authenticate producers, measure
present usage or grant operator acceptance. Roll back only the new test if
necessary; preserve saved reports. Original Vault sync remains pending.

## 2026-10-05: independent saved-history boundary check

Following the scoped policy-read change, inspected the offline history parser
and direct renderer: selected indices, chronological tie ordering, canonical
digest binding, bounded input/output, and per-report storage scope. No reader
change was justified. `make test-local-finops` passed 194 tests (seven
Linux-only skips). No new collection, report/volume/backup mutation or VM
start; Colima was observed stopped. AgentHouse bytes remain `null /
NOT_MEASURED`; this does not establish producer authenticity, current usage
or operator acceptance. No FinOps rollback; preserve existing reports. Original
Vault sync remains pending outside checkout.

## 2026-10-05: separate saved-history audit after advisory bound (offline)

Reviewed the direct history reader's selected-index completeness, chronological
tie behavior, canonical report digest binding and per-report storage coverage.
No justified reader change emerged. The offline FinOps suite passed 194 tests
(seven Linux-only skips) and the full local regression completed successfully.
No collection or retained report/volume/backup mutation occurred; the dedicated
Colima profile was observed stopped. AgentHouse usage remains `null /
NOT_MEASURED`. Fixture checks do not authenticate saved producers, establish
current usage or grant operator acceptance. No FinOps rollback is needed;
preserve saved reports. Direct original Vault sync remains pending.

## 2026-10-05: independent offline FinOps regression

After the separate policy observer fix, `make test-local-finops` passed 194
tests (seven Linux-only skips). This was an independent fixture check only:
no report collection, reader change, retained volume or backup mutation.
Dedicated Colima was observed stopped. AgentHouse usage remains
`null / NOT_MEASURED`; saved producer identity and current usage were not
verified. No FinOps rollback is needed; preserve saved reports. Original Vault
sync remains pending outside checkout.

## 2026-10-05: independent saved-history scope review (offline)

Rechecked direct selected-index completeness, observed-time order, canonical
digest binding and per-report storage cells in the saved-history renderer.
No justified reader change emerged; 194 FinOps tests passed (seven Linux-only
skips). Colima was observed stopped; no collection, saved report or volume/
backup mutation. AgentHouse usage remains `null / NOT_MEASURED`. These tests
cannot authenticate saved producers, measure present usage or grant operator
acceptance. No runtime rollback; preserve reports. Original Vault sync pending.

## 2026-10-05: separate saved-history selection check (offline)

Reviewed the direct renderer's selection indices, chronology, canonical
digest binding and per-report storage rendering; no justified reader change.
`make test-local-finops` passed 194 tests (seven Linux-only skips). Colima was
observed stopped; no collection or saved report/volume/backup mutation.
AgentHouse usage stays `null / NOT_MEASURED`; neither saved producer identity
nor present usage or operator acceptance was established. No runtime rollback;
retain saved reports. Original Vault sync pending outside checkout.

## 2026-10-05: independent saved-history review during Service repair (offline)

Inspected direct history selection, canonical digest binding, ordering and
per-report storage scope independently of the policy change. No new reader
defect was established; no report, collector or volume was changed. The 194
FinOps tests passed (seven Linux-only skips), as did the local regression.
Colima was observed stopped. AgentHouse usage remains `null / NOT_MEASURED`;
producer authenticity, current usage and operator acceptance remain open.
No runtime rollback; retain saved reports. Original Vault sync pending.

## 2026-10-05: independent saved-history inspection after Service guard (offline)

Inspected direct selection, canonical digest, observation ordering and
per-report storage cells; no reader defect or change was justified. `make
test-local-finops` passed 194 tests (seven Linux-only skips). Colima was
observed stopped; no collection or retained report/volume/backup mutation.
AgentHouse usage remains `null / NOT_MEASURED`. Saved producer identity,
present usage and operator acceptance remain open. No runtime rollback;
preserve saved reports. Original Vault sync remains pending outside checkout.

## 2026-10-05: independent direct history review (offline)

Reviewed direct saved selection shape, canonical digest binding, timestamp
ordering and per-report storage coverage without a justified reader change.
`make test-local-finops` passed 194 tests (seven Linux-only skips); the wider
local regression passed. Dedicated Colima was observed stopped; no collection
or retained report, volume or backup mutation. AgentHouse usage remains
`null / NOT_MEASURED`; saved producer authenticity, present usage and operator
acceptance remain open. No runtime rollback; preserve saved reports. Direct
original Vault synchronization remains pending outside checkout.

## 2026-10-05: separate saved-history inspection (offline)

Independently reviewed direct saved-report selection, digest validation,
chronology and per-report storage rendering. No new defect established and no
reader or retained report changed. `make test-local-finops` passed 194 tests
(seven Linux-only skips). Colima was observed stopped; no collection or retained
volume/backup mutation. AgentHouse usage remains `null / NOT_MEASURED`;
producer authenticity, present usage and operator acceptance remain unverified.
No runtime rollback; preserve saved reports. Original Vault sync pending.

## 2026-10-04: separate saved-history review after resource guard (offline)

Independently inspected saved report selection, canonical digest binding,
chronological order and per-report storage cells. No new reader defect was
established and no FinOps implementation or saved report changed. `make
test-local-finops` passed 194 tests (seven Linux-only skips). Dedicated Colima
was observed stopped; no collection or retained data/backup mutation. AgentHouse
volume usage remains `null / NOT_MEASURED`. Saved-producer authenticity,
current usage and operator acceptance remain open. No runtime rollback;
preserve historical reports. Original Vault sync remains pending outside checkout.

## 2026-10-04: independent saved-history review (offline)

After the separate policy denial fix, inspected direct saved selection, digest
binding, chronology and per-report storage coverage. No further reader defect
was established; no FinOps implementation, saved report or collection changed.
`make test-local-finops` passed 194 tests (seven Linux-only skips), and the
local regression passed. Dedicated Colima was observed stopped. AgentHouse
volume usage stays `null / NOT_MEASURED`; neither saved producer authenticity,
current usage nor operator acceptance was verified. No runtime rollback;
preserve historical reports. Original Vault sync remains pending outside checkout.

## 2026-10-04: equivalent numeric saved-history identity (offline)

An independent saved-history regression presents the same valid report with
`0.5` versus `5e-1` wire encodings. The unchanged canonical digest refuses
the duplicate pair and a distinct pair still renders; focused history tests
passed (25). No collection, retained report/volume/backup mutation or reader
change; dedicated Colima was observed stopped. AgentHouse usage remains
`null / NOT_MEASURED`. Saved-producer authenticity, present usage and operator
acceptance remain open. Revert only the test if necessary; preserve reports.
Original Vault sync remains pending outside checkout.

## 2026-10-04: equivalent Unicode saved-history identity (offline)

An independent regression feeds the same valid saved report once with literal
Unicode and once with JSON Unicode escapes. The unchanged canonical digest
rejects the duplicate pair and renders a distinct pair; focused history tests
passed (24). This is test-owned input only: no new collection, retained report,
volume or backup mutation. Dedicated Colima was observed stopped; AgentHouse
usage remains `null / NOT_MEASURED`. It does not authenticate saved producers,
measure current usage or establish operator acceptance. Roll back only the test
if needed; preserve saved reports. Direct original Vault sync pending outside
checkout.

## 2026-10-04: canonical duplicate saved history (offline)

An independent history regression verifies that reversing JSON object member
order does not turn a saved observation into a second observation. The existing
parser rejects the pair by canonical digest, while a genuinely distinct pair
renders. `make test-local-finops` passed 192 tests (seven Linux-only skips).
No reader change, new collection or retained report/volume/backup mutation;
the dedicated Colima profile was observed stopped. AgentHouse usage stays
`null / NOT_MEASURED`. This does not authenticate saved producers, measure
present usage or establish operator acceptance. Revert only the test if needed;
preserve reports. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: separate saved-history reader review (offline)

Inspected `history.py` parsing, direct-render selection/digest/chronology
binding and per-report storage scope independently of the policy fix. No new
reader defect established or FinOps implementation/report changed. The local
regression includes the 191-test FinOps suite; Colima was observed stopped.
No new collection, retained report/volume/backup mutation or runtime rollback.
AgentHouse volume usage remains `null / NOT_MEASURED`; producer authenticity,
current usage and operator acceptance remain unverified. Preserve saved reports;
direct original Vault synchronization remains pending outside checkout.

## 2026-10-04: separate offline history boundary review

Inspected direct saved-history selection type, chronological ordering, digest
rebinding and per-report storage rendering after the independent policy change.
No reader defect was established or FinOps implementation/report changed.
`make test-local-finops` passed 191 tests (seven Linux-only skips). The dedicated
Colima profile was observed stopped; no collection or retained volume/backup
mutation. AgentHouse usage remains `null / NOT_MEASURED`. These tests cannot
authenticate saved producers, measure current usage or establish operator
acceptance. No runtime rollback; preserve reports. Direct original Vault sync
remains pending outside this checkout.

## 2026-10-04: independent saved-history scope review (offline)

Following the separate policy smoke change, inspected direct saved selection,
canonical digest binding, chronological ordering and per-report storage cells.
No new reader defect was established or FinOps code/report changed. `make
test-local-finops` passed 191 tests (seven Linux-only skips). Dedicated Colima
was observed stopped; no collection or retained report/volume/backup mutation.
AgentHouse usage remains `null / NOT_MEASURED`. This cannot authenticate saved
producers, measure current usage or establish operator acceptance. No runtime
rollback; preserve reports. Direct original Vault sync remains pending.

## 2026-10-04: independent equal-time saved selection order (offline)

A direct-render regression now pins refusal of reversing two different saved
reports with equal observation timestamps: selection index remains the
tie-breaker. The unchanged ordered pair renders. `make test-local-finops`
passed 191 tests (seven Linux-only skips); no reader, report or collection
changed. Dedicated Colima was observed stopped; AgentHouse usage stays `null /
NOT_MEASURED`. This cannot authenticate saved producers, measure present usage
or establish operator acceptance. Revert only the test if needed and retain
reports. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: separate saved-history inspection after scope label fix (offline)

Inspected direct renderer digest binding, ordered selection and per-report
storage coverage after the independent policy label change. No additional
FinOps reader defect was established or code/report changed. `make
test-local-finops` passed 190 tests (seven Linux-only skips). The dedicated lab
was observed stopped; no collection or retained volume/backup mutation.
AgentHouse usage remains `null / NOT_MEASURED`. This inspection cannot
authenticate a saved producer, measure present usage or establish operator
acceptance. No runtime rollback; preserve reports. Direct original Vault sync
remains pending outside this checkout.

## 2026-10-04: independent saved-history render boundary review (offline)

After the Service smoke fix, independently inspected direct `render()` selection
shape, report validation, canonical digest binding, chronological order and
per-report storage cells. No further defect was established, and no FinOps
reader or saved report was changed. `make test-local-finops` passed 190 tests
(seven Linux-only skips). The dedicated Colima profile was observed stopped;
no collection or retained report/volume/backup change. AgentHouse volume usage
remains `null / NOT_MEASURED`. This review cannot authenticate producers,
measure present usage or establish operator acceptance. No runtime rollback;
preserve historical reports. Original Vault sync remains pending outside the
checkout.

## 2026-10-04: independent saved-history scope inspection (offline)

After the separate policy comparator commit, inspected `history.py`'s direct
selection shape/index, validated report digest, chronological ordering and
per-report storage cells. No additional reader defect was established or
implementation changed. `make test-local-finops` passed 190 tests (seven
Linux-only skips). Dedicated Colima was observed stopped; no collection or
retained report/volume/backup mutation. AgentHouse usage remains `null /
NOT_MEASURED`; saved producer authenticity, current usage and operator
acceptance remain unverified. No runtime rollback; retain historical reports.
Original Vault synchronization remains pending outside this checkout.

## 2026-10-04: independent saved-history review after policy guard

Inspected the direct renderer's selection indices, canonical digest binding,
ordering and size checks without modifying its reader or retained reports.
`make test-local-finops` passed 190 tests (seven Linux-only skips); no further
defect was established. Dedicated Colima was observed stopped; no collection,
report/volume/backup mutation or request replay. AgentHouse usage stays `null /
NOT_MEASURED`; producer authenticity, present usage and operator acceptance
remain unverified. No runtime rollback is needed; preserve saved reports.
Original Vault synchronization remains pending outside this checkout.

## 2026-10-04: direct saved-history report binding (offline)

A separate direct-render regression changes a valid selected report's model
name after parsing while retaining the original digest. Existing binding
rejects it; the unchanged pair still renders. Focused history (21 tests) and
`make test-local-finops` (190 tests, seven Linux-only skips) passed. No FinOps
reader or retained report changed, no collection or retained volume/backup
mutation; AgentHouse usage remains `null / NOT_MEASURED`. This cannot
authenticate saved producers, measure current usage or establish operator
acceptance. Revert only this test if needed, retaining historical reports.
Original Vault sync remains pending outside checkout.

## 2026-10-04: independent saved-history read-only review (offline)

Inspected direct report selection, canonical digest binding, chronological
ordering and storage-coverage cells in `finops/history.py` after the separate
policy increment. No new defect established and no FinOps reader or saved report
changed; `make test-local-finops` passed. No collection, retained volume or backup
change; AgentHouse usage remains `null / NOT_MEASURED`. This review cannot
authenticate saved producers, measure current usage or establish acceptance.
There is no FinOps runtime rollback; retain reports. Direct original Vault sync
remains pending outside the checkout.

## 2026-10-04: direct saved-history selection type (offline)

An independent direct-render regression pins the existing rejection of
boolean and floating selection indices that compare equal to integer zero.
The original two reports still render; 20 focused history tests and 189 FinOps
tests passed (seven Linux-only skips). No
reader or renderer behavior changed, no collection or retained report/volume/
backup mutation. The dedicated lab was observed stopped and AgentHouse usage
remains `null / NOT_MEASURED`. This fixture cannot authenticate saved reports,
measure current usage or establish operator acceptance. Revert only the test
if necessary; preserve saved reports. Original Vault sync remains pending
outside this checkout.

## 2026-10-04: direct saved-history deep-extension check (offline)

An independent direct `render()` fixture now pins fail-closed handling of a
1100-level untrusted report extension, while the original two reports still
render. Existing validation needed no implementation change; 188 focused
FinOps tests passed (seven Linux-only skips). No collection or retained report,
volume or backup mutation. AgentHouse usage stays `null / NOT_MEASURED`.
This does not authenticate saved producers, measure present usage or establish
acceptance. Roll back only this test if needed and retain saved reports.
Original Vault synchronization remains pending outside the checkout.

## 2026-10-04: independent saved-history nesting regression (offline)

A test-owned two-report input with a 1100-level unknown extension is rejected
as `FINOPS_HISTORY_INVALID` by the existing parser; the original pair still
renders. Focused history: 18 tests. No FinOps reader or retained report changed,
no collection or retained volume/backup mutation occurred. AgentHouse usage
remains `null / NOT_MEASURED`. This cannot authenticate saved producers, measure
present usage or establish acceptance. Roll back only the test if necessary;
keep saved reports. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: representable saved-history subnormal boundary (offline)

An independent two-report fixture pins the existing distinction between
`5e-324` (nonzero and representable) and `2e-324` (nonzero but rounded to zero)
in a saved measurement. Focused history suite passed (17 tests); no parser,
renderer or saved report changed. No collection or retained volume/backup
mutation; AgentHouse usage remains `null / NOT_MEASURED`. Offline fixtures
cannot authenticate a producer, measure current usage or establish acceptance.
Roll back only this test if needed and preserve historical reports. Direct
original Vault sync remains pending outside checkout.

## 2026-10-04: escaped duplicate saved-history key (offline)

An independently encoded two-report input with `infer\u0065nce` and `inference`
on the same saved report is rejected by the existing decoded-key uniqueness
check; the original reports still parse. Focused history 16 tests and FinOps
185 tests passed (seven Linux-only skips). No parser, renderer or retained
report changed. Dedicated Colima remained stopped; no collection or volume/
backup mutation. AgentHouse retained usage remains `null / NOT_MEASURED`.
This fixture does not authenticate the producer, measure present-day usage or
establish acceptance. Roll back only the regression test if necessary; retain
saved reports. Original Vault sync remains pending outside checkout.

## 2026-10-04: independent saved-history overflow regression (offline)

An explicitly selected two-report fixture now pins the existing rejection of
positive and negative overflowing JSON exponents in a saved measurement. A
direct malformed selection-index shape remains rejected. No parser, renderer or
saved report changed; focused history 15 tests and FinOps 184 tests passed
(seven Linux-only skips). Dedicated Colima remained stopped; no collection or
retained report, volume or backup mutation. AgentHouse volume remains `null /
NOT_MEASURED`. Fixtures cannot authenticate a producer, measure current usage
or establish acceptance. Roll back only the test additions if needed, retaining
historical reports. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: separate direct-history inspection (offline)

Inspected direct selected-index validation, canonical digest and chronological
binding, per-report storage cells and output bound in `finops/history.py` after
the independent policy reader repair. No additional defect established or
FinOps behavior changed; `make test-local-finops` passed 183 tests (seven
Linux-only skips). Dedicated Colima was observed stopped; no collection or
retained report, volume or backup changed. AgentHouse usage remains `null /
NOT_MEASURED`. Offline checks do not authenticate producers, measure current
usage or establish acceptance. No FinOps runtime rollback; preserve saved
reports. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-04: independent saved-history review (offline)

After the policy transport fix, separately inspected direct report selection,
duplicate-key rejection, canonical digest/order binding, per-report storage
cells and output limits in `finops/history.py`. No new defect established or
FinOps behavior changed; `make test-local-finops` passed 183 tests (seven
Linux-only skips). No collection, retained reports, volume or backup mutation;
the dedicated lab was observed stopped. AgentHouse usage stays `null /
NOT_MEASURED`. Offline review cannot authenticate a producer, measure current
usage or establish operator acceptance. No FinOps runtime rollback; preserve
saved reports. Original Vault sync pending outside checkout.

## 2026-10-04: nested duplicate history claim regression (offline)

Independently pinned the existing rejection of a duplicate nested `inference`
claim in an explicitly selected saved-history JSON input; the original valid
two-report input still parses. The focused history suite passed (14 tests).
No parser or renderer behavior, saved report, volume or backup changed. The
dedicated lab remained stopped; AgentHouse retained usage is still `null /
NOT_MEASURED`. This synthetic input cannot authenticate a report producer,
measure current usage or establish acceptance. Roll back only the regression
test if needed and preserve historical reports. `make test-local-finops`
passed 183 tests (seven Linux-only skips); design-context (89 documents),
secret-filename check and diff check passed. Original Vault sync pending
outside checkout.

## 2026-10-04: independent saved-history boundary check (offline)

Inspected direct saved selection, canonical digest/order binding, per-report
storage coverage and bounded HTML in `finops/history.py`. No new defect
established or FinOps behavior changed; `make test-local-finops` passed 182
tests (seven Linux-only skips). Dedicated Colima was observed stopped; no
collection or retained report/volume/backup mutation. AgentHouse usage remains
`null / NOT_MEASURED`. Offline fixtures cannot authenticate a producer,
measure current usage or establish operator acceptance. No runtime rollback;
preserve saved reports. Original Vault sync pending outside checkout.

## 2026-10-04: independent saved-history validation review (offline)

Reviewed `finops/history.py` direct selection indices, canonical digest/order
validation, per-report storage cells and bounded HTML after the independent
policy pagination fix. No new defect established or FinOps behavior changed;
`make test-local-finops` passed 182 tests (seven Linux-only skips). Dedicated
Colima was observed stopped; no collection or retained report, volume or
backup mutation. AgentHouse usage remains `null / NOT_MEASURED`. Offline
validation cannot establish producer authenticity, current usage or operator
acceptance. No runtime rollback; retain saved reports. Original Vault sync
pending outside the approved checkout.

## 2026-10-04: independent saved-history check (offline)

Inspected direct report selection, digest/order and per-report storage coverage
in `finops/history.py` following the independent policy receipt repair. No new
defect established or FinOps behavior changed; `make test-local-finops` passed
182 tests (seven Linux-only skips). Dedicated Colima was observed Stopped;
no collection or retained report, volume or backup change. AgentHouse usage
remains `null / NOT_MEASURED`. Offline checks do not authenticate the producer,
measure current usage or establish acceptance. No runtime rollback; keep saved
reports. Original Vault sync pending outside the approved checkout.

## 2026-10-04: independent saved-history inspection (offline)

Inspected `finops/history.py` direct saved-history parse, digest/order binding,
per-report storage coverage and bounded output after the separate policy test.
No additional defect established or FinOps behavior changed. Focused suite
passed 182 tests (seven Linux-only skips); no collection, saved report, VM,
volume or backup change. Dedicated Colima was read-only observed Stopped and
AgentHouse usage remains `null / NOT_MEASURED`. Offline checks cannot establish
producer authenticity, current usage, Linux runtime behavior or operator
acceptance. No runtime rollback; preserve saved evidence. Original Vault sync
pending outside checkout.

## 2026-10-04: equal-time history storage order (offline)

Pinned both selection orders for two distinct valid v8/v9 reports with equal
observation times: Serving TMP's schema-absent and measured logical-zero cells
stay attached to their original reports, in selection order. The focused five
storage-history tests passed. No renderer, saved report, VM, volume or backup
changed; AgentHouse remains `null / NOT_MEASURED`. This does not prove producer
authenticity, current usage or acceptance. Roll back only this test if needed;
preserve saved reports. Original Vault sync pending outside checkout.

## 2026-10-04: reordered history storage scope (offline)

Added a synthetic direct-history regression: reverse-selected v9/v8 reports
with distinct valid observation times must display the v8 schema-absent Serving
TMP cell before the v9 logical-zero cell. The first fixture failed public
validation because its timestamp was edited without rebuilding; rebuilding
through the public contract preserved the original sample windows and the
focused 182 tests passed (seven Linux-only skips). No collector, saved report,
VM, volume or backup was changed; AgentHouse remains `null / NOT_MEASURED`.
This pins presentation order, not producer authenticity, present-day usage or
operator acceptance. Roll back only the test if needed. Original Vault sync
remains pending outside the approved checkout.

## 2026-10-04: independent direct-history review (offline)

Reviewed direct selected-index/order and canonical digest validation,
per-report storage coverage and bounded HTML in `finops/history.py`. No new
defect established; no FinOps behavior or saved report changed. `make
test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated Colima
remained Stopped; no collection or retained report/volume/backup mutation.
AgentHouse retained usage stays `null / NOT_MEASURED`. Offline tests cannot
authenticate producers, run Linux fixtures, measure current usage or establish
operator acceptance. No runtime rollback; preserve saved evidence. Original
Vault sync pending outside the approved checkout.

## 2026-10-04: separate direct-history consistency check (offline)

Inspected direct selection/digest/order checks and per-report storage coverage
in `finops/history.py` independently of the GitOps UID regression. No further
defect established and no FinOps behavior or saved reports changed. `make
test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated Colima
remained Stopped; no collection or retained report/volume/backup change.
AgentHouse usage stays `null / NOT_MEASURED`. Offline tests do not authenticate
producers, exercise Linux fixtures, measure current usage or establish operator
acceptance. No runtime rollback; preserve saved evidence. Original Vault sync
pending outside the approved checkout.

## 2026-10-04: independent direct history inspection (offline)

Inspected `finops/history.py` direct selection, validation/digest/order binding,
bounded HTML and per-report storage coverage independently of the GitOps
regression. No new defect established or FinOps behavior changed.
`make test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated
Colima observed Stopped; no collection or saved report/volume/backup mutation.
AgentHouse retained usage remains `null / NOT_MEASURED`. Offline checks cannot
authenticate producers, exercise Linux fixtures, measure current usage or
establish operator acceptance. No runtime rollback; preserve saved reports.
Original Vault sync pending outside checkout.

## 2026-10-04: independent saved-history boundary review (offline)

Checked direct `finops/history.py` selection, canonical digest binding,
chronological tie ordering, bounded output and per-report storage coverage.
No separate defect established; no FinOps implementation or saved report
changed. `make test-local-finops` passed 181 tests (seven Linux-only skips).
Dedicated Colima observed Stopped; no new collection or retained report,
volume or backup mutation. AgentHouse usage remains `null / NOT_MEASURED`.
Offline checks cannot authenticate producers, execute Linux fixtures, measure
current usage or establish acceptance. No runtime rollback; preserve reports.
Original Vault sync pending outside this checkout.

## 2026-10-04: separate saved-history review after lifecycle guard (offline)

Inspected direct selection, digest/order binding, bounded HTML and per-report
storage coverage in `finops/history.py` independently of the GitOps repair.
No additional defect established; FinOps implementation and saved reports
unchanged. `make test-local-finops` passed 181 tests (seven Linux-only skips).
Dedicated Colima remained Stopped; no collection, retained report/volume/backup
mutation or GitHub write. AgentHouse usage remains `null / NOT_MEASURED`.
Offline tests cannot authenticate producers, run Linux-only fixtures, measure
current usage or establish acceptance. No runtime rollback; preserve reports.
Original Vault sync pending outside checkout.

## 2026-10-04: independent saved-history review (offline)

After the separate GitOps creation-time change, inspected direct selection,
canonical digest binding, bounded HTML and the per-report storage matrix in
`finops/history.py`. No additional defect established; FinOps behavior and
saved reports unchanged. `make test-local-finops` passed 181 tests (seven
Linux-only skips). Dedicated Colima was observed Stopped; no collection or
retained report/volume/backup mutation. AgentHouse usage remains `null /
NOT_MEASURED`. Offline checks cannot authenticate saved producers, run Linux
fixtures, measure current usage or establish operator acceptance. No runtime
rollback; preserve historical reports. Original Vault sync pending outside
checkout.

## 2026-10-04: separate saved-history and storage review (offline)

Following the GitOps timestamp repair, inspected `finops/history.py` and its
direct-render tests: selection indices, chronological order, digest binding,
duplicate refusal and per-report storage coverage remain bounded. No separate
defect established, and no FinOps behavior or saved report changed. `make
test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated Colima
was observed Stopped; no collection or retained report/volume/backup mutation.
AgentHouse usage remains `null / NOT_MEASURED`. Offline fixtures cannot
authenticate producers, exercise Linux integration, measure current usage or
establish operator acceptance. No runtime rollback; preserve historical
reports. Original Vault sync pending outside checkout.

## 2026-10-04: independent saved-history boundary check (offline)

After the inventory identity commit, inspected `finops/history.py` direct
selection, canonical digest binding and per-report storage display. No further
defect established; no FinOps implementation or saved report changed. `make
test-local-finops` passed 181 tests with seven Linux-only skips. Dedicated
Colima observed Stopped; no collection or retained report, volume or backup
mutation. AgentHouse volume usage stays `null / NOT_MEASURED`. This cannot
authenticate saved producers, exercise Linux fixtures, measure current usage
or establish operator acceptance. No runtime rollback; preserve saved reports.
Original Vault sync pending outside checkout.

## 2026-10-04: separate saved-history review after GitOps depth guard (offline)

Reviewed direct saved-history parsing, digest/order binding, bounded HTML and
per-report storage coverage after the independent GitOps commit. No further
defect established; no FinOps implementation or saved report changed. `make
test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated Colima
observed Stopped; no collection, retained report/volume/backup mutation or
GitHub write. AgentHouse volume usage remains `null / NOT_MEASURED`. These
offline checks do not establish producer authenticity, Linux runtime behavior,
current usage or operator acceptance. No runtime rollback; preserve saved
reports. Original Vault sync pending outside checkout.

## 2026-10-04: independent history boundary review (offline)

Inspected direct history parsing, duplicate/digest and chronological selection
in `finops/history.py`, and per-report storage coverage independently of the
GitOps regression. No further defect established and no FinOps reader or saved
report changed. `make test-local-finops` passed 181 tests (seven Linux-only
skips). Dedicated Colima observed Stopped; no collection or retained report,
volume or backup mutation. AgentHouse volume usage stays `null / NOT_MEASURED`.
Offline checks do not authenticate the producer, exercise Linux fixtures,
measure current usage or establish acceptance. No runtime rollback; retain
saved reports. Original Vault sync pending outside checkout.

## 2026-10-04 Independent saved-history review after denial-stream guard

Reviewed `finops/history.py` direct parsing, canonical digest and selected
ordering, plus per-report storage display, separately from the policy repair.
No additional defect established and no FinOps implementation or retained
report changed. `make test-local-finops` passed 181 tests (seven Linux-only
skips). Dedicated Colima observed Stopped; no collection or retained
report/volume/backup mutation. AgentHouse retained volume remains `null /
NOT_MEASURED`. Offline checks cannot authenticate producers, exercise Linux
fixtures, measure current usage or establish acceptance. No runtime rollback;
retain saved reports. Original Vault sync pending outside the checkout.

## 2026-10-04 Independent direct-history presentation check (offline)

After the separate policy JSON repair, reviewed direct history parsing,
chronological selection, digest binding and per-report storage display. No
additional defect established and no FinOps code or saved report changed.
`make test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated
Colima observed Stopped; no collection or retained report/volume/backup
mutation. AgentHouse retained volume remains `null / NOT_MEASURED`. Offline
checks cannot authenticate producers, exercise Linux fixtures, measure current
usage or establish acceptance. No runtime rollback; keep historical reports.
Original Vault sync pending outside checkout.

## 2026-10-04 Independent direct-history review after policy envelope guard

Inspected direct-render selection/digest binding and per-report storage
presentation after the separate policy correction. No new defect established
and no FinOps behavior or saved report changed. `make test-local-finops` passed
181 tests (seven Linux-only skips). Dedicated Colima was observed Stopped; no
collection or retained report/volume/backup mutation. AgentHouse usage remains
`null / NOT_MEASURED`. This offline check cannot establish producer identity,
Linux runtime behavior, current usage or acceptance. No FinOps rollback;
retain saved evidence. Original Vault sync pending outside this checkout.

## 2026-10-04 Independent saved-history output review

After the separate policy correction, inspected `finops/history.py` for
per-report identity/storage display, bounded HTML and direct-render validation.
No additional defect established or FinOps behavior changed. `make
test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated Colima
observed Stopped; no collection or retained report/volume/backup mutation.
AgentHouse retained volume stays `null / NOT_MEASURED`. Offline review does
not authenticate producers, exercise Linux fixtures or prove current usage or
operator acceptance. No FinOps rollback; retain existing saved reports.
Original Vault sync pending outside the checkout.

## 2026-10-04 Independent saved-history presentation review

After the separate policy guard commit, inspected `finops/history.py` for
direct-render selection shape/order, canonical digest binding, output size,
and per-report storage coverage without aggregation. No further defect was
established; FinOps implementation and saved reports remain unchanged.
`make test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated
Colima remained stopped; no collection or retained report/volume/backup
mutation. AgentHouse retained-volume usage remains `null / NOT_MEASURED`.
This offline review does not authenticate the producer, exercise Linux runtime
fixtures, measure current usage or establish acceptance. No FinOps rollback;
retain saved reports. Original Vault sync pending outside the checkout.

## 2026-10-04 Independent direct-history selection check

After the separate policy commit, inspected `finops/history.py` and its direct
renderer tests for selected-index uniqueness, chronological tie-breaking,
canonical digest binding and duplicate refusal. No further defect was
established; FinOps code and saved reports were not changed. Focused
`make test-local-finops` passed 181 tests, including seven Linux-only skips.
Dedicated Colima remained stopped. No live collection or retained volume,
report or backup mutation. AgentHouse volume usage remains `null /
NOT_MEASURED`; producer authenticity, actual Linux behavior, current usage
and operator acceptance remain open. No FinOps rollback needed. Original Vault
sync pending outside this checkout.

## 2026-10-04 Independent storage-boundary check after policy repair

Reviewed the fixed-volume preflight/receipt constraints and focused FinOps
fixtures; no defect was established or FinOps implementation changed.
`make test-local-finops` passed 181 tests, seven Linux-only skipped. The
dedicated VM stayed stopped; no measurement, collection or saved report,
volume/backup mutation. AgentHouse usage remains `null / NOT_MEASURED`;
SSH trust, real Linux cleanup, saved producer authenticity and operator
acceptance remain open. No FinOps rollback needed. Original Vault sync pending.

## 2026-10-04 Independent saved-history check after policy repair

Reviewed direct-history digest binding, selection ordering and duplicate
refusal; no additional defect was established and no FinOps behavior changed.
`make test-local-finops` passed 181 tests (seven Linux-only skips). Dedicated
Colima remained stopped; no new collection, report/volume/backup mutation or
request replay. AgentHouse retained-volume usage remains `null / NOT_MEASURED`;
saved producer authenticity, Linux runtime behavior, current usage and operator
acceptance are unverified. No FinOps rollback needed; preserve saved reports.
Original Vault sync pending outside checkout.

## 2026-10-04 Independent saved-history review after lineage exclusion repair

Reviewed the selected-report digest, chronological ordering, duplicate refusal,
and display paths in the offline history parser/renderer. No further defect was
established, so no FinOps implementation or retained report changed. Focused
FinOps checks passed (181 tests, including seven Linux-only skips). The dedicated
VM was read-only observed stopped; no collection, volume/backup mutation or
unknown-request replay. AgentHouse retained-volume usage remains `null /
NOT_MEASURED`; saved producer authenticity, current usage, Linux fixtures and
operator acceptance are not established. Original Vault synchronization remains
pending outside this noninteractive checkout.

## 2026-10-04 Independent offline history contract check

After the separate saved-lineage repair, inspected the history parser/direct
renderer and reran `make test-local-finops`: 181 tests passed, with seven
Linux-only fixtures skipped. No FinOps defect was established or code changed.
The stopped dedicated VM was not started; there was no collection or retained
report/volume/backup mutation. AgentHouse retained volume remains `null /
NOT_MEASURED`; report provenance, current usage, Linux integration and operator
acceptance remain unverified. Original Vault sync pending outside checkout.

## 2026-10-04 Independent offline history/measurement review

Inspected the saved-history parser/renderer and public report validator; no
new defect was established and no FinOps implementation or saved report was
changed. `make test-local-finops` passed 181 tests (seven Linux-only fixture
skips) after the separate lineage increment. Dedicated Colima was read-only
observed stopped; no VM start, live collection, retained volume/report/backup
mutation or unknown-request replay. This does not measure AgentHouse retained-
volume bytes (still `null / NOT_MEASURED`), authenticate saved producers,
exercise skipped Linux fixtures or establish operator acceptance. Original
Vault sync pending outside the checkout.

## 2026-10-04 Direct history cyclic-input regression (offline)

A disposable cyclic in-memory report is already rejected with bounded
`HistoryError` by the direct history renderer; a distinct valid saved pair still
renders. This pins existing behavior without changing collection, saved data,
or the renderer. Focused checks and local verification are in Milestones.
No VM start, retained report/volume/backup mutation or GitHub write. It does
not authenticate reports, measure current usage or establish operator
acceptance. Roll back this regression test only, preserving saved records.
Original Vault sync pending outside this noninteractive checkout.

## 2026-10-04 Direct history malformed report fields (offline)

Direct saved-history rendering now converts missing public report fields and
malformed nested rows to bounded `HistoryError` rather than exposing Python
index/key errors. Disposable malformed reports are rejected; legitimate
selected reports remain displayable. Focused FinOps and local verification
results are in Milestones; private logs are under
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-043/`.
This is offline presentation robustness, not report provenance, current usage,
AgentHouse retained-volume measurement or operator acceptance. No VM start,
collection, retained report/volume/backup changes or GitHub write. Roll back
only this exception boundary and test; preserve saved records. Original Vault
sync pending outside the approved checkout.

## 2026-10-04 Direct history selection shape (offline)

The direct saved-history renderer now refuses malformed selection envelopes
with its bounded `HistoryError` before unpacking/indexing them; it still accepts
the parsed pair format. This is offline presentation robustness, not independent
report provenance, current usage, retained-volume measurement or operator
acceptance. Focused and local verification results are in Milestones. No VM
start, collection or retained report/volume/backup change. Roll back this
preflight and test alone while preserving saved evidence. Original Vault sync
pending outside checkout.

## 2026-10-04 Duplicate saved observation refusal (offline)

The history parser and direct renderer now reject repeated canonical report
content, rather than displaying one saved report as two independent observations.
The disposable negative failed before repair. Focused FinOps tests passed (178
tests; 7 Linux-only skips), followed by full local verification. An existing
workspace-usage fixture that selected the same report twice was changed to use
two distinct valid reports; the intermediate failure is retained in the private
log. This is a content-identity check, not proof that different reports came
from independent producers or that current usage was measured. No VM start,
collection, retained report/volume/backup mutation or GitHub write. Roll back
only the history guard and fixture changes; preserve saved evidence. Logs:
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-033/`.
Original Vault sync pending outside this checkout.

## 2026-10-04 Saved FinOps HTML browser policy (offline)

Standalone and history HTML now declare a restrictive CSP: no default network
resources or script, with inline CSS retained for self-contained display.
Both disposable negatives found the missing policy before repair; focused
FinOps 177 tests passed (7 Linux-only skips). This is defense in depth for
rendered offline pages, not proof of browser enforcement, producer authenticity,
live usage or operator acceptance. No new collection, VM start or retained
data/backup mutation. Roll back only the two template lines and tests, retaining
saved reports. Full local verification, design-context, secret scan and diff
check passed; private logs are under
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-032/`.
Original Vault sync remains pending outside checkout.

## 2026-10-04 Direct saved-history selection order (offline)

Direct callers of `history.render` can no longer duplicate or omit a selected
report index, or display reports out of observation-time order while the page
claims chronological order. The renderer checks the exact index set and the
same timestamp/index ordering used by the parser before producing HTML. A
disposable negative reproduced both misleading cases before the fix; 175
focused FinOps tests passed (7 Linux-only skips). No collection or VM start;
stored reports, AgentHouse volume and backups were not changed. This is display
integrity, not saved-report provenance or current usage. Roll back renderer and
test only, retaining evidence; original Vault sync remains pending.

## 2026-10-04 Direct saved-history renderer digest binding (offline)

The history parser validates selected reports and calculates SHA-256, but a
direct call to `history.render` formerly accepted an arbitrary displayed
digest, including HTML markup. It now validates each report and binds the
displayed digest to its canonical content before producing HTML. Disposable
forged-digest negatives failed before repair; the focused FinOps suite passes.
No collector/schema change or saved-report rewrite, VM start, AgentHouse
volume scan or backup change. This guards an offline presentation boundary,
not independent report provenance or current resource usage. Retain AgentHouse
`null / NOT_MEASURED` and WP local-partial. Roll back only the renderer and
fixture, preserving saved reports and backups. Direct original Vault sync is
pending outside this noninteractive checkout.

## 2026-10-04 Standalone saved HTML underflow boundary (offline)

The standalone saved-report HTML parser still accepted a nonzero `1e-999`
measurement as zero after the history parser had been fixed. A disposable
valid report reproduced the error before repair. Both offline renderers now
use one finite-float decoder; literal `0e-999` and normal reports still pass.
No public schema, collector, original saved report, retained volume or backup
changed. Focused FinOps tests (173; 7 Linux-only skips) and full local
verification passed on rerun; the first full run failed in an unrelated
Warehouse saved-journal fixture, whose focused suite and unchanged full rerun
passed. Logs: `tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-015/`.
This protects only offline JSON interpretation, not current usage, report
authenticity or backup recovery. Roll back the decoder/renderer/test changes
only; preserve saved data. Original Vault sync pending outside checkout.

## 2026-10-04 保存FinOps履歴の非ゼロ浮動小数underflow拒否（offline）

保存した2–8報を読む`history.parse`はJSONの極小非ゼロ数`1e-999`をPythonの
floatで0に丸め、妥当な0の測定値としてHTMLへ渡し得た。隔離された2件の
valid reportの既存0.5測定値を置換した負例で修正前の誤受入を再現。非ゼロ
mantissaが0へunderflowした場合だけ拒否し、真の`0e-999`と通常の保存履歴を
維持する。public schema/collector・保存report原本は変更しない。これは
選択したJSON数値のdecode境界であり、現在の実使用量やレポートの独立
producer真正性・継続量・backup復旧を証明しない。実volume、VM、保持data、
backupを走査/変更せず、AgentHouse volumeは`null / NOT_MEASURED`、WP
local-partial。focusedと全回帰の結果はMilestones、private logは
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-014/`。
戻す場合はhistory decoder/test差分のみ戻し、保存report/backupを保持する。
原Vault同期は非対話checkout外で保留。

## 保存レポートの保存領域観測範囲表（2026-09-27・offline履歴）

保存v1–v9の履歴HTMLにAgentHouse Docker volume、MLflow store、Notebook Lab workspace、Serving HOME/TMPの固定5行×選択観測の範囲表を追加。各reportのvalidated JSONだけで、schema対象外・参照未取得・参照確認/未計測・論理bytes未取得・観測した通常ファイル`st_size`を分ける。0は**当該directoryの実際に保存された論理0**のセルだけに表示。AgentHouseのvolumeは参照が成功していても使用量`NOT_MEASURED`を維持。合計/増減、観測間の連続性、TMP削除可能量、物理占有、backupは推定しない。公開schema・collector・保存JSONは変更せず、選択2–8報をoffline表示するだけ。

実v8/v9保存レポートをread-onlyでレンダリングし、v8ではServing TMPがschema対象外、v9では論理0 bytes、AgentHouseは両方`READ_FAILED / NOT_MEASURED`の別セルとなることをbrowser accessibility tableでも確認。保存原本SHA256 v8 `5ac068bafbc2f86d49165ba3dee2838f41c48057b12daa32fa1e831e0b2eb71b`、v9 `d6f188631b4fd446bd3a8f4472fded5475e430cb489ccd81174a0a68d1a176a6`の一致を前後で確認。見た目のpixel受入・実VM認証・保持volume数値化ではない。Colima/Serving起動・runtime変更なし、AgentHouse `null / NOT_MEASURED`、WP local-partial。private証拠は`tmp/local-experiment/finops-history-coverage-checks/20260927/`。

保存データ・backup/exportへの変更なし。scope表を使わない場合はrendererの呼出しを止めればよく、回復のために保存reportやbackupを削除・再作成しない。旧schemaで参照失敗した対象を「測定失敗」へ誤昇格させない負例も固定した。

focused171件＝164PASS＋Linux専用7skip。全回帰121suites/3786実行＝3778PASS＋8skip（既存1＋Linux専用7）、87.537秒、最終log SHA256 `c1cf06b26ad3ff467147353280a8af2ca79f7a03e8debac307d812f71a19cdbc`。design-context/secret scan PASS。private HTMLは32,268 bytes、SHA256 `22d4b9d80fc145fba270cc169ad7f8df76a1c6b6d428a40a3e97f6956a354d84`。

## Model Serving TMPの独立論理bytes（2026-09-27・v9 local増分）

AgentHouseについて停止中の専用profileの既存記録・設定key名/ファイル種類をread-onlyで再棚卸ししたが、独立して信頼済みのSSH server key bindingや、認証済みguest receiptを返す別経路は確認できなかった。profile設定テキストはkey名を抽出し値を出力・保存・trust採用せず、`vz-identifier`や`guestSocket`設定名をserver認証とはみなさない。別の秘密鍵/credentialファイル、VM disk/iso/guest logは未読。live receipt/保持volume走査を引き続き保留し、独立して進められるServing `TMPDIR/TMP/TEMP`固定rootを選択。

既存Serving ownerと固定environment参照、private0700/root device-inode、process停止を確認してから、no-follow・上限付きの二走査metadata測定をhost schema v5/report v9の**第四行**に追加。停止中の実01はTMP通常ファイル0件/子directory0件/論理0 bytes。別の後続metadata走査も同じで、HOMEは別行0 bytes、MLflow store 1,767,968 bytes・Lab workspace 244,438 bytesは独立したまま。保存v5–v8は元の3行、v1–v4はhost行なしという旧範囲を維持し、旧v8へTMP行を注入しても拒否。全v9レポートは他source未取得で`partial`。TMPは**削除可能量を意味しない**。Serving sibling logs/runtime records/共有model artifacts、物理占有・backup復旧・全component footprintは対象外。Colima/Serving起動・保持データ変更なし、AgentHouse `null / NOT_MEASURED`、WP local-partial。証拠は`tmp/local-experiment/finops-serving-tmp-checks/20260927/`。

focused167件＝160PASS＋Linux専用7skip。全回帰121suites/3782実行＝3774PASS＋8skip（既存1＋Linux専用7）、85.712秒、log SHA256 `57808a4f30c36b962995d901d8ccd4d2da71bbd8fe994adf58aa4f701366ee6b`。design-context/secret scan PASS。保存v8 report原本SHA `5ac068bafbc2f86d49165ba3dee2838f41c48057b12daa32fa1e831e0b2eb71b`不変。v8/v9のoffline HTML/historyとbrowser accessibility tableの4独立行を確認（pixel visual・本番受入ではない）。

## AgentHouse VM procfs visibility（2026-09-27・offline fixture）

`linux_volume_procfs_contract.py`で**callerが渡した模擬値のみ**を対象に、`/proc`のmountinfo/fdinfoのmount ID、procfs root/`hidepid`・PID path overrideなし、自己PIDとPID 1のstat・単一`NSpid`、PID/mount namespace linkとstart tickを有界検査。target不在は明示された`ENOENT`だけを候補とし、`EACCES`・欠損自己可視性・別namespace/不正mount・改ざんstatを拒否。既存receipt wireへのshape接続もfixtureで確認したが、**実VMの`/proc`は未読**。模擬`ENOENT`や`query_complete=True`はkernel由来の証明でもremote child cleanupでもない。認証済みVMでdescriptorをpinし、filesystem/namespace・PID可視性を実確認するまでlive receiptと保持volume走査は保留。Colima停止、AgentHouse `used_bytes=null / NOT_MEASURED`、WP local-partial。

focused163件＝156PASS＋Linux専用7skip、全回帰121suites/3778実行＝3770PASS＋8skip、log SHA256 `12b19f4eba69c0c1420875a5128197d86981d09f03c24fb9edda5ede2b571468`。design-context/secret scan PASS、保存v7原本不変。限定private証拠は`tmp/local-experiment/finops-procfs-contract-checks/20260927/`。

## AgentHouse host sleep/boot時刻境界（2026-09-27・offline fixture）

`host_clock.py`でDarwin boot session UUID、sleepを除く`mach_absolute_time`と含む`mach_continuous_time`を同一timebaseで読取り。boot読取を両時計で挟むlocal sampleと、operation開始・probe・終了の3 sampleを照合するprivate契約を追加。boot変化、逆行、30秒operation/10秒probe age超、両時計の経過差50ms超、probe側のhost時刻不一致を拒否してから、従来のreceipt gateを適用する。synthetic sleep/reboot/逆行/古いprobeをnegative試験し、macOS時計をread-only smoke。**実際のhost sleep/rebootは誘発していない。** 50ms許容差内の短いsleep・不真正な注入sample・VM側実行/cleanupを排除できず、live採用にはrunner自身が操作全期間のclockとbootを取得する必要がある。専用Colima起動/SSH/保持volume走査なし。AgentHouse `used_bytes=null / NOT_MEASURED`、WP local-partial。

focused159件＝152PASS＋Linux専用7skip、全回帰121suites/3774実行＝3766PASS＋8skip、log SHA256 `e16315604b12d387cca9b0e936bb391b46344c16be10135a9672be16a6c862b7`。design-context/secret scan PASS。限定private証拠は`tmp/local-experiment/finops-host-clock-checks/20260927/`。ColimaはStopped、保存v7原本SHA不変。

## AgentHouse worker source bundle相関（2026-09-27・offline）

`linux_volume_source_bundle.py`でVM側予定の固定3 Python source（`linux_openat2.py`・`linux_volume_worker.py`・`linux_volume_supervisor.py`）をno-followなroot descriptorから2回限定readし、owner・mode・link・size・前後identity/内容hash一致を条件にprivate manifestとdomain-separated SHA256を作成。欠損・symlink/hardlink・特殊file・別内容/途中変更・過大fileをfixtureで拒否。実checkoutのlocal sourceは3件・bundle digest `113e05272aef1fd0d5e4f02aa9cc5b86105b1d3c6c6a0d099ec43ebab4c7ab40`。これは**local sourceの相関値のみ**で、今のsupervisor/wireは呼出元が自由に`worker_digest`を渡せる。VMへの転送・そこでのimport/実行、Python interpreter/依存source、receiptと実行bytesの同定を証明せず、公開レポートには含めない。専用Colima起動/SSH/保持volume走査なし。AgentHouseは`null / NOT_MEASURED`、WPはlocal-partial。設計境界は`tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md`。

focused154件＝147PASS＋Linux専用7skip、全回帰121suites/3769実行＝3761PASS＋8skip、log SHA256 `252e7430b006432e53c58d816f6b5dacdf48bfe9d20a40ada3d9f67fa5adae26`。design-context/secret scan PASS。独立SHA計算も一致し、保存v7原本不変。限定private証拠は`tmp/local-experiment/finops-source-bundle-checks/20260927/`。

## AgentHouse known-hosts descriptor snapshot（2026-09-27・offline fixture）

既存のpathname検査→SSH再openの競合を小さくするため、**独立してenroll済みのkeyをcallerが既に持つことを前提**にした`pinned_ssh_prefix`を追加。検証済みknown-hosts/identity fileを必要とし、一致するfixture key entryをunlinked read-only descriptorに固定して、子processへ`pass_fds`と`UserKnownHostsFile=/dev/fd/N`の候補を渡す。元pathnameの差替え後にもfixture子processが固定entryを読めること、`ssh -G`によるoffline option解釈、失敗時にsnapshotを作らないことを検証。保持volume、既存VMの鍵やcredential内容、SSH接続には触れていない。OpenSSHが実接続でdescriptorを保持/複数回readする挙動は未検証で、same-user攻撃者への分離やserver keyの真正なenrollmentも未証明。認証済みreceipt取得の受入・走査許可ではなく、AgentHouseのbytesは`null / NOT_MEASURED`、WPはlocal-partial。境界と残余は`tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md`。

focused151件＝144PASS＋Linux専用7skip、全回帰121suites/3766実行＝3758PASS＋8skip、log SHA256 `3dc8cce81b9bc0e228efc7c69d9db536aa2eba319fde0aa13a00125af1062bed`。design-context/secret scan PASS。private証拠は`tmp/local-experiment/finops-known-hosts-snapshot-checks/20260927/`。

## Model Serving HOMEの限定論理bytes（2026-09-27・v8 local増分）

AgentHouse volumeのauthenticated SSH server key bindingは保存済み専用profile/設計記録に見つからず、live依存作業はunknownのままとした。独立して進められる固定Model Serving HOMEを、既存owner/configの`HOME`参照とroot device/inodeを確認後、サービス停止中にbounded/no-follow二走査で測定した。通常ファイル**0件・子directory 0件・論理0 bytes**。別の後続metadata走査でも同値、前後のowner/root identityとServing停止状態も一致。Notebook LabとMLflow storeは各行の限定値を維持し合算しない。全v8レポートは他の取得項目が未取得で`partial`。旧v1–v7のServingは未計測のままで、旧レポートの数値注入を拒否する。詳細は`tools/local_experiment/finops/README.md`。

HOMEの0はServing全体の0ではない。sibling tmp/log/runtime/venvや共有モデルartifactは対象外で、filesystemの物理占有・APFS共有extent・backup/restore・削除可能量、内容不変を示さない。directory metadata読取によるatime影響も排除していない。専用ColimaとServingは起動せず、保持HOMEを削除/修復していない。保存v7 report原本SHAは不変。限定private証拠は`tmp/local-experiment/finops-serving-usage-checks/20260927/`。WPはlocal-partial。

focused149件＝142PASS＋Linux専用7skip。全回帰121suites/3764実行＝3756PASS＋8skip（既存1＋Linux専用7）、log SHA256 `cf70fbb1635a5dff3e08b89acbbd11b852b76da2ab20570fdc3dc47c475b30cd`。design-context/secret scan PASS。既存v7と新規v8のoffline HTML/history契約を確認し、ブラウザaccessibility tableでも3行の分離とServing HOME 0 bytesを確認（pixel visualの受入ではない）。

## AgentHouse receipt wire / SSH設定境界（2026-09-27・offline）

bounded 16KiBのprivate responseでversion・nonce・VM boot/namespace/procfs前後identity、operation receiptと独立process probeを照合し、重複JSON key・oversize・不正数値・稼働中の同一childを拒否。既存の**信頼済み**server keyを前提にした固定loopback OpenSSH argv候補も作り、user/global config・proxy・agent・forwarding・対話認証を無効化。既知host fileをbounded no-followで検査し、`ssh -G`のoffline設定解釈を確認。ただしhost keyの真正なenrollmentは未確立、SSH自体の起動なし。検証したknown-hosts pathnameをSSHが再openする競合とworker実行source照合、host sleep/boot、VM `/proc`可視性は未達。wireの成功だけでは接続先認証・終了確認・保持volume走査許可と呼ばず、数値は引き続き`null / NOT_MEASURED`。

専用profileの既存`ssh.config`はread-onlyで設定項目のみ確認し、`StrictHostKeyChecking no`/`UserKnownHostsFile /dev/null`を確認。鍵の内容や秘密credentialは読んでいない。この既存接続は信頼済みserver keyのenrollment根拠にならず、first-contact鍵採用や無検証でのlive transportを行わない。別の真正なkey provenanceが必要。

focused145件＝138PASS＋Linux専用7skip、最終全回帰121suites/3760実行＝3752PASS＋8skip（既存1＋Linux専用7、log SHA256 `b7aaaabc7b0b4bc5cc04ce022255da5d00c974dbabb5e5e9f1f2552715bca540`）。design-context/secret scan PASS。限定private証拠は`tmp/local-experiment/finops-receipt-wire-checks/20260927/`。live SSH/remote process・保持volumeは観測していない。

## AgentHouse host receipt gate（2026-09-27・offline）

VM supervisorのterminal receiptと、別に取得した同じbootのprocess probeを照合するprivate受理ゲートを追加。操作UUID、worker相関digest、VM/host boot、子PID/start tick、cleanup、結果上限と、同一host monotonic clockで操作開始から30秒以内の窓を条件とする。@architectレビューでstart tickの先頭ゼロ別名、古い測定＋新規probe、巨大数値の変換を拒否した。これは**入力が真正なVMから取得されたことや実際にremote workerが停止したことの証明ではない**。receipt/probeの安全な取得・再接続とworker sourceの検証は未実装で、保持volumeを走査せず`null / NOT_MEASURED`を維持。専用Colimaは停止したまま。

focused139件＝132PASS＋Linux専用7skip、全回帰121suites/3754実行＝3746PASS＋8skip（既存1＋Linux専用7）、log SHA256 `79f9e64fcdfed52e08d380b676085dbe722e8dcc7ad2e618d62ee5f7e6319d7b`。設計文脈/secret scan PASS、既存v7 report原本不変。限定証拠は`tmp/local-experiment/finops-receipt-gate-checks/20260927/`。次は既存認証済みVM接続でのbounded receipt/probe transport、host sleep/bootとworker source同定の検証。

## AgentHouse openat2 / supervisorのfixture-only実機検証（2026-09-27）

Linux workerのroot/子directory/file metadataを`openat2`の`NO_XDEV|NO_SYMLINKS|BENEATH`で固定rootから解決し、anchorのdevice/inode/mount IDも検査する。VM-side fixture supervisorは子の登録後起動、期限超過TERM/KILL、reap、terminal receipt照合まで数値を受理しない。専用Colimaを一時起動し、disposable fixtureのLinux17件＝15PASS＋macOS専用2skipを確認した。bind mountの隔離namespaceは`uid_map: Operation not permitted`で拒否され、mountは作らずカーネルのbind越境拒否は**未検証**。再起動後は通常停止でStoppedへ復帰し、設定SHA/retained VM disk device/inode一致を確認したが、k3s通常停止のpod mountpoint cleanupと`/var/lib/cni` busyを伴い、guest保持内容・backup不変は証明しない。supervisorのdisk I/O無期限block、host切断後のreceipt再取得、VM-host writerの継続停止も未達。保持volumeは読取・走査せずpublic Docker bytesは`null / NOT_MEASURED`、WPはlocal-partial。限定証拠は`tmp/local-experiment/finops-openat2-supervisor-checks/20260927/`。

最終macOS focused136件＝129PASS＋Linux専用7skip。全回帰121suites/3751実行＝3743PASS＋8skip（既存1＋Linux専用7）、log SHA256 `3f604c264e5a1b83ab48e810bc526ead0b6390a16e081ef89a92915c67f38335`。設計文脈とsecret scanもPASS。Linux fixture15PASSはその時点の実装に対するもので、後続のmacOS側receipt symlink拒否の追加、未実行bind fixtureは別扱い。保存v7 report原本SHAは不変。

## AgentHouse private Docker metadata transport（2026-09-27・offline）

前増分のoffline preflightへ渡すprivate transportを追加。固定contextのUnix socketを**daemon読取より前**に照合し、以後は`--host`で固定する。daemon/root/ARM64・volumeのdriver/Options/固定pathを確認してから、label有無にかかわらず全container IDを上限付きでinspectし、環境変数・余分なlabel・state error等を投影時に捨てる。前後listとdaemon identityを比較し、全処理20秒上限と開始/終了時刻を保持する。誤context・Options内credential・欠損volume・一覧/daemon driftをfake backendで拒否。専用Colimaは停止のまま、live preflight/保持volume走査は行わない。workerのremote cleanupとper-open mount境界、VM-host writer確認は引き続き未達。Docker公開schemaはv1のまま、`null / NOT_MEASURED`を維持し、WPはlocal-partial。

focused124tests＝121PASS＋Linux専用3skip、全回帰121suites/3739実行＝3735PASS＋4skip（既存1＋Linux専用3、log SHA256 `dda8e0a964e7fef1b76a91399e30657883f8bb8eb202ad35e83f2dcaa4235fb6`）。design-context/secret scan PASS、専用Colima停止・保存v7原本SHA不変。限定private証拠は`tmp/local-experiment/finops-volume-transport-checks/20260927/`。transportのlive検証・数値測定とは区別する。

## AgentHouse volume fixture-only worker / offline preflight（2026-09-27）

前回の設計ゲートから継続し、Linux専用の非接続metadata workerと、Docker参照のoffline二時点preflightを実装。macOSではworkerが列挙前に拒否する。VM-local使い捨てfixtureのLinux試験でno-atime二走査、root/子directory atime不変、sparse/hiddenの論理値、symlink/hardlink/特殊file/変更/上限拒否を確認した。初回Linux試験は`PosixPath`型判定で失敗し、修正後に再実行して成功。VMは専用Colimaを明示起動/通常停止し、AgentHouseはexited/restart=no、volumeのlocal driver/Options nullをmetadataだけで確認した。設定SHA/VM disk identityは停止後も一致。k3sの通常停止にpod mountpoint cleanupと`/var/lib/cni` busy warningがあり、保持データやbackup不変の証明ではない。追加起動はremote終了確認とmount越境防止の実装まで見送る。

offline preflightは全Docker container ID・mount参照、bind overlap・固定daemon/volume/container identityと時刻鮮度を検査するが、結果は`volume_traversal_authorized=False`のまま。VM-host writerや同一device bind mountの走査中の出現・消滅、remote cleanupの未確立を理由に**保持volumeの走査、公開schema変更、使用bytesの数値化は未実施**。現行v4–v7の`null / NOT_MEASURED`を維持。コードと未達条件は[測定境界計画](../../../../tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md)を参照。WPはlocal-partial。

focused117tests＝114PASS＋Linux専用3skip（macOS）、Linux VM-local fixture5tests＝4PASS＋macOS用1skip。全回帰121suites/3732実行＝3728PASS＋4skip（既存1＋Linux専用3）、`make verify-design-context`とsecret scanもPASS。VM起動/停止以外のruntime設定・GitHub変更なし。証拠は`tmp/local-experiment/finops-volume-worker-checks/20260927/`。

preflightの他container mount・稼働履歴の比較漏れを@architect再レビューで修正し、最終全回帰も同じ121suites/3732実行＝3728PASS＋4skip（log SHA256 `361101fe47433e510df0c1895f50f446b5afab4ab7ee37a6cdc3c742ed9f0764`）。二時点の一致は中間のwriter不在を証明しない。修正後のLinux実機再試験は追加起動を避け未実施で、先行fixture結果をその範囲で保持する。

## AgentHouse named-volume実使用量の測定境界（2026-09-26・設計のみ）

@architectと相談し、この増分は**inspect-onlyの設計ゲート**とした。専用Colimaが停止しており、Docker inspectは参照関係だけを示し、Linux volumeの論理ファイルbytesや排他的所有を示さない。Colima起動は他workloadの保持領域書込みを伴い得る。Docker `system df`、ClickHouse起動後のexec、read-onlyと称するhelper container、macOSからのVM disk mountは、安全で限定された無変更走査を現時点で証明できない。今回は起動・volume走査・bytes発表をせず、既存Docker schema v1/report v4–v7の`used_bytes=null / NOT_MEASURED`を維持する。欠測を0と呼ばない。

後続数値化の条件は、固定daemon/volume/停止containerのidentityと他のrunning/stopped container/bind参照を前後照合、driver options/実mount topologyを確認し、Linux側のdescriptor相対・no-follow・`O_NOATIME`走査とremote worker終了確認を備えること。symlink/hardlink・別device・走査中の変化や上限/cleanup不明ではunknownを返す。成立時も論理通常ファイル`st_size`だけで、共有物理extent、VM/APFS占有・ClickHouse table帰属・backupや回収可能量ではない。詳しいgo/no-go・negative test・回復条件は[測定境界計画](../../../../tools/local_experiment/finops/AGENTHOUSE_VOLUME_MEASUREMENT_PLAN.md)。この設計記録は数値計測の機能受入ではなく、WPはlocal-partial。

offlineのDocker size hint/既存v7への数値注入拒否を追加してfocused108tests PASS。全回帰121suites/3723実行＝3722PASS＋既存1skip。回帰後も専用Colima停止と保存v7原本SHA不変を確認した。私有証拠は`tmp/local-experiment/finops-agenthouse-usage-boundary/20260926/`。volume内部の保持状態やbackupは観測しておらず、回復操作も不要だった。

## MLflow storeの限定論理bytes（2026-09-26・v7 local増分）

v6のNotebook workspace行とは別に、所有markerのSQLite backend URI・artifact rootを確認した固定`tmp/local-experiment/mlflow-tracking/store`を対象とする。`tracking.sqlite`通常ファイルと`artifacts`ディレクトリの存在を2回のdescriptor相対metadata走査で要求し、DB側car（存在する`-wal`/`-shm`/`-journal`）およびartifacts配下も含む全通常ファイルの`st_size`を合計する。内容・SQL・DB lockを読まず、MLflowの起動/backup export/復元も行わない。symlink/hardlink/特殊file・別owner/device・root差し替え・sidecarやartifactsの走査中変化・件数/名前/深さ/時間の上限超過ではMLflow bytes/countをnull/理由付きunknownとする。参照identityとNotebookの独立数値は維持し得る。host schema v3/report v7で行ごとの観測windowと基準を持たせ、保存v1–v6の意味は変えない。

停止中の実02はMLflow storeの通常ファイル25件、子ディレクトリ55件、**論理1,767,968 bytes**。別の後続metadata走査でも同値、Lab workspaceも独立して論理244,438 bytes。Serving HOMEは未計測。Kubernetes等が未取得で全体partial。focused106tests PASS、全回帰121suites/3721実行＝3720PASS＋既存1skip。回帰後の両root identity・metadata manifest一致とColima停止を確認したが、内容/backupの不変や復旧の証明ではない。両行は合算せず、SQLiteの整合性、MLflow run/artifact論理帰属、冷バックアップの復旧可能性、APFSの一意物理占有・snapshot/圧縮・quota・削除可能量を主張しない。原観測/検証は`tmp/local-experiment/finops-mlflow-usage-checks/20260926/`に保存し、WPはlocal-partialのまま。

## Notebook workspaceの限定論理bytes（2026-09-26・v6 local増分）

@architectと相談し、実使用量の最初の数値対象を**Notebook Labの固定workspaceだけ**に限定した。既存owner/config・root device/inodeを確認し、非追従のdirectory descriptorを保持して2回のmetadata走査で通常ファイル`st_size`の合計とfile/子directory件数を比較する。hidden/checkpoint/旧・archiveファイルも含むが内容は開かない。symlink、hardlink、特殊file、別device/owner、identity・metadataの変化、走査上限（10,000 entries、depth32、name data4MiB、10秒）超過ではbytesをnullにする。18秒のworker alarmと20秒の親transport timeoutを持つ。MLflow store・Serving HOMEは意図的に未計測のまま。保存v1–v5の公開契約を保持し、新規host schema v2/report v6で基準・観測window・件数と失敗理由を区別する。

Colima/サービス停止中の実02でNotebook workspaceは**26通常ファイル・子directory1件・論理244,438 bytes**。別のread-only metadata走査でも同じ件数・論理合計だが、独立観測は原子的同時刻比較ではない。host3件の参照はobserved、他のDocker/Kubernetes等の未取得により全reportはpartial。focused102tests、全回帰121suites/3717実行＝3716PASS＋既存1skip。回帰後のworkspace root identity・metadata manifestも独立列挙時と一致し、Colimaは停止したまま。内容bytesやbackup復旧は確認していない。数値はAPFSの物理占有・clone共有・snapshot/圧縮・quota・回収可能量・MLflow/Servingや全Labの容量・backupの証明ではなく、他disk値とも合算しない。限定証跡は`tmp/local-experiment/finops-workspace-usage-checks/20260926/`。WPはlocal-partial。

## 固定host directory参照（2026-09-26・限定local増分）

v5は、MLflowのstore marker内の固定SQLite/artifact-root→`tmp/local-experiment/mlflow-tracking/store`、Notebook Labの所有record/configのroot_dir→`tmp/local-experiment/notebook-lab/workspace`、Model Servingのowner/固定HOME設定→`tmp/local-experiment/ridge-serving/home`の3参照だけを検証する。専用Lab仮想環境の固定read-only workerがprivate owner/config validatorを再利用し、所有情報のdigestと非symlink/0700ディレクトリのdevice/inodeを前後で照合する。owner token・設定内容・絶対pathは公開しない。配下ファイル・shared store・Lab cache/実験・Servingのモデル実体、実使用bytes・物理占有・backupやquotaは未確認。`used_bytes=null / NOT_MEASURED`を保持する。

Colimaと3つのサービスを起動せずに、停止中の実v5レポートは3対象すべて`observed`、Docker/Kubernetes等はunknownで全体partialとなった。実取得と保存v4→v5 offline HTML/historyの原本不変・旧schema未収録区別を確認。symlink・permission・owner/directory差し替えのnegative testを含む。focused 92tests、全回帰121suites/3707実行＝3706PASS＋既存1skip。回帰後も所有digest・ディレクトリdevice/inode・直下entry数の前後一致、Colima停止を確認。直下entry数は配下内容・bytesの証明ではない。この増分は全ホストdirectory列挙ではなく、容量評価や実使用量の測定判断は後続に分ける。証拠は`tmp/local-experiment/finops-host-storage-checks/20260926/`。

## AgentHouse Docker named-volume参照（2026-09-26・限定local増分）

v4は専用Docker contextの固定コンテナ`shirokuma-local-agenthouse`とnamed volume `shirokuma-local-agenthouse-data`を、二度のread-only inspectで検証する。container ID/所有label/pinned imageとvolume名/label/local driver/作成時刻/mountpoint、単一mountのsource・宛先`/var/lib/clickhouse`を照合する。Docker作成時刻のoffsetをUTCへ正規化する。コンテナ停止中でも保存されたmount参照は観測できるが、実際のmount健全性・排他的所有・他コンテナの参照やvolume内の使用量は分からない。`used_bytes=null / NOT_MEASURED`を維持する。設定環境変数・host側mountpoint・credentialは公開レポートに出さない。

停止中v4の対象はunknown/null、専用profile一時起動後の実01はDocker作成時刻の`+09:00`でSAMPLE_INVALIDとして保持し、UTC正規化修正後の実02では停止中の既存AgentHouseコンテナとvolumeの参照を確認した。profile/Fluxに変更を加えず、AgentHouseコンテナやvolumeを起動・作成・削除しない。実レポート全体はAgentHouse推論記録等の未取得でpartial、参照限定の観測成功と区別する。元v1/v2/v3の契約は保持し、v4 HTML/historyはvolume参照/観測identityと旧schemaの未収録を区別する。focused 87testsと全回帰121suites/3702実行（3701PASS＋既存1skip）を確認。終了時にDocker identityとFluxを再読取し、通常stopでColimaを起動前の停止状態に復帰、設定SHA・保持host directoryを確認した。停止後のDocker API再確認はできない。証拠は`tmp/local-experiment/finops-docker-storage-checks/20260926/`。次の固定host directory・実使用量・他のDocker参照の網羅は別増分とする。

## Fixed Kubernetes storage references（2026-09-26・限定local受入）

@architectの判断により、まず固定4workloadのAPI参照関係をread-onlyで検証するv3を追加した。SeaweedFSとPolaris PostgreSQLはworkload→Pod owner→container mount→名前空間付きPVC→PV claimRef UIDを照合し、Trino/Polaris本体は直接PVC参照なしを確認する。要求容量とPVC/PV宣言容量は別々で、実使用量は常に`null / NOT_MEASURED`。20Gi＋5Giは実占有・物理予約・quota・削除可能容量ではない。S3利用者にSeaweedFSの容量を重複帰属しない。保存v1/v2の契約と表示は維持し、履歴ではv3のUID/観測windowを確認できる。

起動前の専用profile停止・構成SHA・保持領域・空き容量を保存し、既存`mac-studio-solo`を`--activate=false --save-config=false --ssh-config=false`で起動した。起動直後のroot Flux suspend=true、storage child suspend=false/patch1、PVC UID・既存volume名を記録。Fluxには変更を加えなかった。実05は2026-09-26T10:38:40.979Zに2PVCがBound、SeaweedFS 20Gi/Polaris PostgreSQL 5Gi（各request/PVC status/PV spec）、Polaris/Trinoは`no_direct_pvc`。PV reclaimはいずれもDeleteで、StatefulSetのwhenDeleted/whenScaled Retainやtemplate pruneとは別の制御。MLflow/Lab/Servingは停止中、AgentHouse宣言eventはSCOPE_INVALIDのため全reportはpartialだがstorage4行の限定観測は成功。実使用bytes、mount健全性や保存データの内訳は未確認。最終sourceの実06でも同じstorage4状態・PVC identity/宣言容量を確認した（`live06.json`、report SHA256 `a1513f0d659dece8c121af4f53d9a6b697dbc5db72f2e9779a83321e4279e70d`）。

停止時v3は全storageがunknown/null、実初回のPodList kind省略とPostgreSQL container名の投影失敗は原reportを保持して狭く修正した。live PVC/PV UIDとclaimRefの独立readback、Flux patch・構成不変、保持directoryの存在を確認。保存v1/v2/v3のHTML/history互換とnegative fixtureで誤owner・別namespace・stale UID・量/retention drift・cleanup境界を検証した。実際のPVC/PV削除やresizeは行わない。手順・証拠と残る制約は[runbook](../../../../tools/local_experiment/finops/README.md)と[Milestones](../061_Local_Experiment_Milestones.md)。focused FinOps 81tests、最終全回帰121suites/3696実行＝3695PASS＋既存1skip。保存public JSON＋2HTMLは69,299 logical bytes／73,728 allocated bytes（0600、物理一意容量ではない）。起動前の状態に戻すため専用Colimaを通常stopし、保持VM disk/backupを削除せず、profile config SHAと保持host directoryを再確認した。停止後のPVC API照合は行えない。WPはlocal-partial、Bound PVCのPV読取失敗時は要求値も未取得に倒す保守的な境界を維持する。

## Saved-report history（2026-09-26・限定offline受入）

明示選択した保存public report 2–8件（v1/v2）だけを静的HTMLへ時刻順に表示する。旧v1のlocal process欠落を停止/0と誤認せず、v2の停止・unknown・observedを区別する。identityとsample時刻、host/Colimaの別diskと当時の助言、各24時間宣言event windowを保持し、異なる区間の使用量・費用・event数を合算/差分化しない。選択原本と表示に秘密のpathを埋め込まず、入力不正ならHTMLを出さず固定エラーで終了。自動収集・継続dashboard・idle/chill判断は未達。

Colima停止時のread-only CLIはpartialで、所有3群はSTOPPED/null、Kubernetes・Colima disk・宣言eventは未取得、host diskのみobserved。保存v1/稼働時v2/停止時v2の3レポートから、原本不変・決定的なHTMLをoffline生成した。focused FinOps 72tests、全回帰121suites/3687実行＝3686PASS＋既存1skip。ブラウザーでの見た目は未確認。操作・容量・証拠は[runbook](../../../../tools/local_experiment/finops/README.md)と[Milestones](../061_Local_Experiment_Milestones.md)を参照。WPはlocal-partialのまま。

## Local owned-process observation（2026-09-20・限定受入完了）

新規収集をv2へ拡張し、固定3群の `local_processes` を追加する。対象はMLflow、Notebook Labのserverと所有kernel、固定model-serving。既存のKubernetes 3components・disk・宣言model eventと、保存済みv1 JSON/HTMLの契約は維持する。収集は既存Lab専用環境の固定read-only workerに委譲し、前後のPID/start identityが一致する所有processだけを集計する。

1秒のCPU累積時間差とRSS合計を観測する。短いCPU sampleは量子化の影響があり、RSSは共有ページを重複計上し得るため、Kubernetes working-setや物理一意メモリとは同一視しない。停止状態を確定できた群はidentity/sampleをnullとして「停止中」と表示し、0へ置き換えない。他の取得が成功していれば全体completeにできる。所有関係や計測が不明なら理由を保持してpartialとする。

2026-09-20T14:38:57.093Zの実01は4.931秒でcompleteとなり、Kubernetes3件とローカル3群がすべてobservedだった。MLflowは2process/487.40625MiB RSS、Labはserver＋13kernelの14process/1354.859375MiB、Servingは1process/32.375MiB。約1.0314595秒のCPU増分は3群とも0msで、量子化を含む観測値として保持し、idleとは判定しない。

1134保護files、ML24runs/1393points/24artifacts、Registry1/1、AgentHouse585行、各runtime identityは当時の前後で不変。保存v1 reportの再検証・HTML生成も互換を保持した。新HTMLの構造・数値・外部resourceなしを検証したが、Browserのfile URL policyで画面表示は未確認であり迂回していない。全回帰は121suites・3682実行＝3681PASS＋既存1skip（93.749秒）。保存public JSON＋HTMLは2files/33,232 logical bytes／36,864 allocated bytes（0600）。検証証拠は別、物理一意容量ではない。2026-09-25のread-only確認では当時のserver PIDは見つからず、現在の稼働を主張しない。

新依存・サービス・start/stop・model実行・MLflow/AgentHouse書込は追加しない。今回の限定3群を超える全component/PVC帰属、idle判断・費用/電力換算、budget強制、継続dashboardは未達のままとする。操作は[FinOps runbook](../../../../tools/local_experiment/finops/README.md)、実測は[Milestones](../061_Local_Experiment_Milestones.md)へ集約する。以下は先行v1の観測時点と受入結果を保持する。

## Local read-only increment（2026-09-20・限定受入完了）

`rtk proxy make local-finops-report`で、固定Trino/Polaris/SeaweedFSのCPU平均・memory working setと観測window/identity、host workspace filesystemと専用Colima filesystemの空き容量、AgentHouseの固定24時間モデル宣言eventをJSONで読む。修正後の実02は0.931秒で`complete`/exit0となった。
[実装runbook](../../../../tools/local_experiment/finops/README.md)の範囲で、新service/model呼出し/AgentHouse書込・stop/delete・budget overrideは行わない。読取失敗は理由とnullを保持する`partial`/exit1、全取得成功は`complete`/exit0であり、FinOps全体の完成を意味しない。

### Observation contract

- CPUはsample windowの平均、memoryはworking set。componentはprimary containerだけを対象とし、identity/start timeとwindowを照合する。欠測・不整合は0に置換しない。
- hostとColimaの空き容量は分け、25%/20%/15%以下をwarning/low/criticalとして固定助言を返す。共有physical storageなので合算せず、APFS reclaimable量や削除可能量を推定しない。
- model/provider別の宣言eventにはsynthetic実験記録も含む。event数を実modelcall数と同一視せず、tokens/costの既知小計と全件coverageを区別する。未知total/currencyはnullのまま、costはproducer-defined estimate、event durationは推論latencyそのものではないproxyとする。

### Local observation

2026-09-20T08:57:46.723Zの実02で3componentsがobserved、host空き61.670289%・Colima81.9312%はいずれもnormal。24時間の宣言model eventは10件/1groupで、prompt4000・completion178 tokens、event duration14288msを記録した。これらは実験記録を含むevent集計であり、実呼出し数や純推論latencyの保証ではない。cost/CPU/memory proxyの全件値とcurrencyはnullを維持した。
初回はbootstrap podがPolaris対象へ混在し、`TARGET_AMBIGUOUS`としてpartialとなった。元report/logを保持し、固定対象の選択を修正して別実02を行った。詳細・最終検証は[Milestones](../061_Local_Experiment_Milestones.md)を参照する。

### Cleanup correction and saved HTML（2026-09-20）

補助processで親rtkが先に終了し子がstdoutを保持する場合のtimeout cleanupを修正した。修正後transport26testsがPASS（0.080秒）、実03も0.995秒でcomplete。2026-09-20T09:06:21.343Zに3components observed、host空き61.669668%/Colima81.929112%がnormal、宣言10eventsとcost/proxyのunknownを維持した。実02とその時点の回帰結果は別記録として残す。
`rtk proxy make local-finops-html < report.json > report.html`は、保存JSONの公開契約を検証して静的HTMLへ表示する入口。追加収集を行わず、観測時刻・partial・欠測を保つ。保存先は`umask 077`のprivate directoryとする。保存した実02の通常HTMLと初回partialのHTMLをdesktop/390px幅で表示し、3components/2disks/10events・duration/cost unknown・Polaris TARGET_AMBIGUOUSを確認した。tableのkeyboard横scrollも動作し、追加した一時preview serverは終了した。修正後の最終回帰は3340PASS＋既存1skip、既存44files/MLflow/AgentHouse/runtimeも元baselineと一致した。

### Remaining acceptance

固定3componentsを超える全component/PVC別帰属、idle判断とchill実行、価格/電力換算、budget強制・override、履歴/自動更新/task別などの継続dashboardとPRあたりの費用は未実装。静的reportの表示とは区別する。観測レポートの成功から本番FinOpsや費用削減効果を主張しない。
[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)下の承認済みlocal開発では、旧Issue/PR gateは待機条件にしない。以下の元計画は歴史として保持する。

## 2026-07-05 historical plan

| Field | Value |
|---|---|
| WP ID | WP-L6-FIN-001 |
| Level | L6 |
| Workstream | FinOps |
| Depends-on | WP-L2-OBS-001, WP-L3-CH-001 |

## Scope

Monitor and enforce resource budgets for CPU, memory, Kubernetes PVCs, host SSD free-space watermark, query cost, idle services, cloud token spend, and local inference cost proxies.

## Acceptance Criteria

- Captures CPU/memory/storage usage by component.
- Captures host free-space watermark from macOS/Colima context and emits alerts at 25%/20%/15% free-space thresholds.
- Captures prompt/completion tokens and estimated cost by model/provider.
- Captures local inference latency and resource proxy where available.
- Provides `chill` recommendations.
- Can block or require approval for PRs that raise resource, disk, model-cache, benchmark-data, or token budgets.
- Dashboard shows task inference cost and token cost per accepted PR.

## Notes

FinOps is rule-based; do not let an LLM decide budget override alone.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L6-FIN-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].
