---
project: Shirokuma
doc_id: "WP-L5-LINEAGE-001"
title: "WP-L5-LINEAGE-001 Lineage completeness dashboard"
status: local-partial
created: 2026-07-05
updated: 2026-10-04
version: "1.53"
area: "workpackage"
tags: [shirokuma, workpackage, l5, metadata]
---

# WP-L5-LINEAGE-001 Lineage completeness dashboard

## 2026-10-04: malformed saved edge targets (offline)

Separately from the known-edge evidence check, direct rendering now returns
`RenderError` for list/dict edge targets instead of leaking `TypeError` from
set construction. Two disposable negatives raised raw exceptions before repair;
normal and malformed scalar targets are covered. Focused lineage and full local
verification passed afterward. No VM start, retained data/backup mutation or
catalog access. This bounds a presentation input, not provenance, catalog
atomicity, whole-catalog/column coverage or operator acceptance. Roll back the
target guard/test only; preserve saved reports. Original Vault sync pending.

## 2026-10-04: known edges require per-run catalog evidence (offline)

The direct renderer rejects a known edge without matching non-null saved
before/after catalog digests for that run, even when aggregate catalog stability
is null because another observation was incomplete. Three disposable reports
rendered before repair; focused lineage 130 tests passed after repair. No VM
start, catalog access or retained source/report/backup changes. This checks
internal consistency, not catalog authenticity or atomicity, whole-catalog/
column coverage or operator acceptance. Roll back only renderer/test; preserve
historical reports. Original Vault synchronization pending outside checkout.

## 2026-10-04: incomplete inventory cannot claim stable population (offline)

In a separate follow-on, the renderer refuses `inventory.complete=false` with
`source_stable=true`: the collector marks source stability false when its final
listing changes or cannot be read. A disposable inconsistent report rendered
before repair; a partial inventory with false stability still renders. Focused
lineage tests passed; broader result is in Milestones. No live catalog/VM,
retained evidence/backup change or replay. This saved-claim check is not source
authenticity, atomicity, whole-catalog/column coverage or operator acceptance.
Roll back only renderer/test, preserve reports. Original Vault sync pending.

## 2026-10-04: saved stability claims versus completed observations (offline)

The direct renderer refuses `metadata_stable=false` with no unknown expected
edge: the collector leaves an unknown edge when catalog snapshots differ or a
target is unobserved. It also refuses `source_stable=false` when the saved
inventory is complete and every run has matching non-null source digests;
incomplete inventories remain displayable. Both test-owned contradictory
reports rendered before repair. Focused results and the broader check are in
Milestones. Only offline renderer/tests changed; the dedicated VM remains
stopped. This does not authenticate the report producer or establish atomic,
whole-catalog/column or operator acceptance. Roll back renderer/tests only;
preserve saved reports and backups. Original Vault sync pending outside checkout.

## 2026-10-04: offline catalog denominator and fixed edge identities

The direct renderer refuses a true/false catalog-stability claim with zero
eligible runs: the collector records null when there were no catalog reads.
Independently, eligible edges must name the fixed fixture → token-specific stg
→ token-specific mart paths, even when an edge is unknown. Five disposable
negative cases rendered before repair; focused lineage tests pass afterward.
Only offline presentation validation changed; the dedicated VM stayed stopped.
No retained reports, sources or backups were rewritten. These checks do not
authenticate the producer, prove atomic/whole-catalog or column coverage, or
establish operator acceptance. Roll back only renderer/tests, preserving saved
evidence. Original Vault synchronization remains pending outside checkout.

## 2026-10-04: saved complete catalog and operation claims (offline)

The renderer rejects `complete` with an eligible run and no explicit true
catalog-stability result; the zero-eligible case retains its null result.
Separately an eligible run without a saved supported operation is rejected.
Both disposable negatives rendered before repair and fail closed afterward;
focused and repository verification are recorded in Milestones. The stopped
dedicated VM, retained reports/sources and backups were not changed. These
checks do not authenticate the producer or prove whole-catalog/column lineage,
atomicity or operator acceptance. Roll back the renderer/tests alone, keeping
historical evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: eligible source demotion needs changed or unreadable evidence (offline)

After the final-valid-source check, a separate disposable report still claimed
an initially eligible run became `incomplete` while its before/after source
digests matched. This cannot arise from the collector's final snapshot check;
the offline renderer now refuses it. A missing final digest remains a valid
incomplete/unknown case. The negative failed before repair and focused lineage
passed afterward. No VM start, catalog access, retained source/report/backup
mutation or replay. This is only internal saved-claim consistency, not source
provenance, atomicity, whole-catalog/column coverage or operator acceptance.
Roll back renderer/test only; retain historical evidence. Original Vault sync
pending outside checkout.

## 2026-10-04: final evidence for valid saved sources (offline)

The direct renderer previously allowed a `valid` source with a missing final
digest whenever the overall observation was incomplete for another reason. A
disposable negative reproduced this; the renderer now requires matching initial
and final digests for every `valid` run, even in an incomplete observation. A
separate incomplete observation with intact source evidence remains displayable.
Focused lineage and Warehouse checks passed after repair; full verification is
recorded in Milestones. This is saved-claim consistency, not provenance,
atomicity, whole-catalog/column coverage or operator acceptance. No VM start,
catalog access, retained source/report/backup mutation or replay. Roll back
renderer/test alone; preserve archived evidence. Original Vault sync pending
outside this checkout.

## 2026-10-04: complete-population and initial-evidence claims (offline)

The saved HTML renderer refuses `observation.status=complete` if the inventory
contains failed, invalid, incomplete or unrecognized source entries, even when
all eligible edges are present. It also requires an initial source digest for
eligible runs and refuses a `valid` source with unequal non-null before/after
digests. An interrupted final read may still have a null final digest; this is
not promoted to a successful observation. Disposable negatives failed before
the guards; the focused lineage suite passed (121). No catalog access, VM start,
saved source/report/backup mutation or unknown-request replay. This checks
internal report claims, not producer identity, atomicity, whole-catalog/column
coverage or operator acceptance. Roll back renderer/tests only, preserving
historical evidence. Original Vault sync remains pending outside this checkout.

## 2026-10-04: saved catalog stability and edge explanations (offline)

The offline renderer now refuses `metadata_stable=true` when an eligible run's
before/after catalog digests are missing or unequal. It also refuses known
collector edge reason codes that contradict the displayed status (for example,
`CATALOG_UNAVAILABLE` with a present edge); unrecognized future codes remain
escaped and displayable. Two disposable catalog negatives failed before repair.
Focused tests and broader verification are recorded in Milestones. Dedicated
Colima was observed stopped; no live catalog, saved source/report/backup change,
or unknown-request replay. These internal consistency checks cannot authenticate
the catalog, prove atomicity or whole-catalog/column coverage, or establish
operator acceptance. Roll back renderer/tests alone, preserving retained reports.
Original Vault sync remains pending outside this checkout.

## 2026-10-04: saved source-stability claim (offline)

The direct offline renderer rejects `source_stable=true` unless every reported
run has matching non-null before/after source digests. Two test-owned negative
reports were accepted before repair; focused lineage tests (117) and full local
verification passed afterward. This checks an internal saved claim, not source
authenticity or an atomic observation. No VM start, live API, saved source/
report/backup mutation, unknown-request replay or GitHub write. Retain the
historical evidence and roll back only renderer/test if necessary. Logs:
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-033/`.
Whole-catalog/column coverage and operator acceptance remain outstanding;
original Vault sync pending outside this checkout.

## 2026-10-04: saved lineage source eligibility consistency (offline)

The offline renderer now rejects a `valid` source omitted from the initial
eligible denominator and an `invalid`/`failed` source included in it. Only an
initially eligible run that later becomes `incomplete` may retain its two
unknown edges. Disposable contradictory reports passed before this check;
116 focused lineage tests now pass. No catalog login, VM start, retained
source/report/backup change or archived request replay. This validates one
internal claim, not provenance, atomicity, whole-catalog/column coverage or
operator acceptance. Full local verification, design-context, secret scan and
diff check passed; private logs:
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-032/`.
Roll back this guard and test only while preserving
historical evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: offline report integrity at the rendering boundary

The collector preserves two expected edges as unknown when a previously
eligible source changes or the final saved-run inventory is incomplete. The
offline renderer formerly accepted a contradictory report with present edges
and a 100% rate under either condition. It now refuses those claims rather
than presenting a misleading percentage. Independently, a report cannot label
the observation complete if source stability is false, catalog stability is
explicitly false, any expected edge is unknown, or observation failure reasons
are present. The first negative and the complete-observation negative failed
before their respective guards; focused 115 tests and the local regression
passed after repair. The retained seven-run/fourteen-edge report still renders
read-only, without rewriting its JSON or HTML. The dedicated Colima profile
was read-only observed stopped. No API login, VM start, retained source/report/
backup mutation or replay. Renderer validation is internal report consistency,
not producer authentication, live catalog observation, atomicity, whole-catalog
or column coverage, or operator acceptance. Revert only the renderer/tests if
necessary; keep saved reports and the stopped VM. Original Vault sync remains
pending outside this noninteractive checkout.

## 2026-10-04: opened catalog context-entry failure (offline)

An opener returning a stream whose context entry raises an exact-path 404
previously treated that error as missing evidence and left the returned stream
open. Mark the stream as opened before context entry and close it on entry
failure. Timely injected entry failures are unavailable; late ones are deadline,
never missing. The isolated negative failed before repair; focused lineage
passed afterward. No catalog login, VM start or retained source/report/backup
change. This does not authenticate the producer, establish atomic or complete
catalog/column coverage, or grant operator acceptance. Roll back only the
entry boundary and fixture, retaining saved evidence. Original Vault sync
remains pending outside the noninteractive checkout.

## 2026-10-04: post-open HTTP error is not absence (offline)

A disposable returned catalog stream raising an exact-path 404 after opening
previously entered the opener's missing-object handler (and exposed an unclosed
error stream in the negative). Only errors before response entry can describe
missing catalog objects now; a post-open HTTP error closes both streams and is
`API_UNAVAILABLE` (or `API_DEADLINE` if late). The initial negative failed;
focused lineage and local verification passed after repair. No VM start or
retained evidence/backup change. This does not authenticate the producer, prove
atomic or whole-catalog/column coverage, or establish operator acceptance.
Roll back only this boundary and fixture; preserve saved reports. Original Vault
sync remains pending outside this checkout.

## 2026-10-04: returned catalog cleanup classification (offline)

A returned 200 whose response context closes with `TypeError` or `ValueError`
previously leaked the raw error after JSON decoding. Test-owned timely/late
failures now yield `API_RESPONSE_INVALID`/`API_DEADLINE`; intentional
`CoverageError` subclasses retain their own codes. Four negatives failed
before repair; the initial full focused run exposed the subclass regression,
which was fixed before the passing rerun (111 tests). Local and design checks
passed. No live catalog, VM start, saved source/report/backup mutation or replay.
This cannot authenticate a producer, prove atomic or whole-catalog/column
coverage, or establish operator acceptance. Roll back only the returned-response
guard and fixture; preserve saved evidence. Original Vault sync remains pending
outside this checkout.

## 2026-10-04: returned catalog status accessor failures (offline)

An injected returned response whose `status` accessor raises `TypeError` or
`ValueError` previously escaped as a raw exception. Four timely/late negative
cases failed before repair; the bounded reader now closes the stream and
classifies timely failures as `API_RESPONSE_INVALID`, late ones as
`API_DEADLINE`. No live catalog login or VM start; this does not authenticate
the producer, prove atomic or whole-catalog/column coverage, or establish
operator acceptance. Roll back only this status guard and fixture, preserving
saved evidence. Original Vault sync remains pending outside this checkout.

## 2026-10-04: value failures in catalog header access (offline)

Both a returned response and a thrown exact-path 404 can expose a callable
`get_all` that raises `ValueError`. This formerly escaped raw; the stream now
closes and the bounded reader reports timely `API_RESPONSE_INVALID` or late
`API_DEADLINE`. Four added cases failed before repair; 109 focused lineage
tests and local/design verification passed after repair. No VM start, catalog login, retained report/source/backup
change or replay. This does not authenticate the producer, establish atomic or
whole-catalog/column coverage, or grant operator acceptance. Roll back only
these header guards/fixtures, preserving historical evidence. Original Vault
sync remains pending outside this checkout.

## 2026-10-04: returned catalog stream read failure (offline)

An injected response whose `read` raises `TypeError` or `ValueError` used to
escape as a raw exception. The bounded reader now closes it and reports
`API_RESPONSE_INVALID` if timely, `API_DEADLINE` if late. Four disposable
negatives failed before repair; focused lineage and Warehouse suites passed
after repair. No VM start, catalog login, retained source/report/backup change
or replay. This does not authenticate the producer, prove atomic/whole-catalog
or column coverage, or establish operator acceptance. Roll back this read guard
and fixture only; preserve historical evidence. Original Vault sync is pending
outside this checkout.

## 2026-10-04: catalog error cleanup type failures (offline)

An injected exact-path 404 closing with `TypeError` or `ValueError` after
closing its stream formerly leaked a raw exception. Cleanup now reports
`API_UNAVAILABLE` if timely, `API_DEADLINE` if late, and never promotes the
404 to missing evidence. The disposable negatives failed before repair; 108
focused tests passed after repair. No live catalog/login, VM start, retained
source/report/backup change or replay. This cannot authenticate the producer,
prove atomic or whole-catalog/column coverage, or grant operator acceptance.
Roll back the cleanup guard/test only; preserve saved evidence. Original Vault
sync remains pending outside this checkout.

## 2026-10-04: invalid final URL value (offline)

A test-owned returned catalog response with a callable `geturl` raising
`ValueError` formerly leaked a raw exception. It now closes the response and
reports `API_RESPONSE_INVALID` when timely or `API_DEADLINE` when late. The
negative failed before repair; focused tests passed after repair. Dedicated
Colima was observed stopped; no live catalog, retained source/report/backup
mutation or request replay. This cannot authenticate a producer, prove atomic
or whole-column coverage, or grant operator acceptance. Roll back this guard
and fixture alone; preserve saved evidence. Original Vault sync is pending
outside this checkout.

## 2026-10-04: failing returned final-URL accessor (offline)

A disposable returned catalog response whose callable `geturl` raises `TypeError`
formerly leaked the exception. Timely failures now close the stream and yield
`API_RESPONSE_INVALID`; failures crossing the deadline yield `API_DEADLINE`.
Both negative cases failed before repair. Focused lineage and local verification
passed after repair. Dedicated Colima was observed stopped; no live catalog,
credential use, saved source/report/backup change or unknown-request replay.
This checks client-side classification, not remote producer identity, atomic
reads, whole-catalog/column coverage or operator acceptance. Roll back only
this guard and disposable test; preserve historical evidence. Original Vault
sync remains pending outside the noninteractive checkout.

## 2026-10-04: failing catalog header accessors (offline)

Callable but failing `get_all` on a returned catalog response or thrown 404
formerly leaked `TypeError`. Test-owned timely failures now become
`API_RESPONSE_INVALID`, late failures `API_DEADLINE`; both streams close, and a
404 with uninspectable headers is not missing evidence. Disposable negatives
failed before repair; 107 focused tests passed afterward. No VM start, real
catalog/login, retained source/report/backup change or replay. This does not
authenticate a producer or establish atomic, whole-catalog or column coverage.
Roll back only these accessor guards/tests, keeping saved evidence. Original
Vault sync remains pending outside this noninteractive checkout.

## 2026-10-04: unusable catalog error header accessor (offline)

An injected exact-path HTTP 404 exposing a non-callable `get_all` formerly
leaked `TypeError` while checking for alternate-location metadata. The bounded
reader now refuses the uninspectable headers as `API_RESPONSE_INVALID` after
closing the stream; normal HTTPMessage and mapping headers remain supported.
The disposable negative failed before repair, then focused tests passed. No
live catalog, VM start, retained source/report/backup change or replay. This
cannot authenticate the producer or establish atomic, whole-catalog or column
coverage. Roll back only this accessor guard/test, preserving saved reports.
Original Vault sync pending outside this checkout.

## 2026-10-04: unusable final catalog URL accessor (offline)

An injected returned response that advertises a non-callable `geturl` no longer
silently skips the fixed-path identity check. An absent accessor remains
compatible with simpler response fixtures. Focused lineage tests passed; no
live catalog, VM start or saved source/report/backup change. This offline
check does not authenticate the producer or establish atomic, whole-catalog or
column coverage. Roll back only the accessor guard/test and preserve reports.
Original Vault sync pending outside this checkout.

## 2026-10-04: mapping headers on returned catalog responses (offline)

An injected successful fixed-path response with plain-dict headers raised a raw
`AttributeError` rather than inspecting case-varied `Content-Range`, `Location`
and `Content-Length`. The returned-response reader now folds dict field names,
preserves duplicate-case detection and rejects uninspectable header containers;
ordinary HTTPMessage and a valid mapping still work. The disposable negative
failed before repair; focused lineage checks passed after repair. No live
catalog/VM start or retained report/source/backup change. This cannot establish
catalog authenticity, atomicity or whole-column coverage. Roll back only the
header reader and fixture, retaining saved evidence. Original Vault sync pending
outside checkout.

## 2026-10-04: partial-representation metadata on thrown 404 (offline)

An injected fixed-path HTTP 404 with `Content-Range` was accepted as missing
catalog evidence before repair. The error reader now refuses that declaration
for both HTTPMessage and plain mapping headers; the disposable negative failed
before repair and focused lineage checks passed afterward. It still closes the
error stream. No VM start, live catalog, retained source/report/backup mutation
or replay. This conservative response check does not authenticate a producer,
prove atomic reads or whole-catalog/column lineage, or grant operator acceptance.
Roll back only this guard/test; keep saved evidence. Original Vault sync pending
outside this checkout.

## 2026-10-04: case-insensitive thrown-error location metadata (offline)

Plain-dict injected HTTP errors with lower/mixed-case `Location`,
`Content-Location` or `Refresh` previously converted a fixed-path 404 into
missing evidence. The reader now folds mapping keys as HTTP field names and
refuses uninspectable error headers after closing the stream. Disposable
negatives failed before repair; focused lineage and local verification passed.
This does not authenticate a live producer or establish atomic, whole-catalog
or column coverage. No VM start or retained report/source/backup mutation.
Roll back only this error-header guard/test; preserve saved evidence. Original
Vault sync pending outside checkout.

## 2026-10-04: error response alternate-location refusal (offline)

An exact-path HTTP 404 advertising `Location`, `Content-Location` or `Refresh`
was formerly classified as a missing catalog entry. Disposable negatives
reproduced this; the bounded reader now refuses alternate-location metadata
on thrown errors, while a plain exact-path 404 remains valid. The error stream
is closed and the deadline remains authoritative. Focused lineage (100 tests)
and local verification passed; logs under
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-016/`.
This is not producer authentication, current catalog observation, atomic or
whole-column coverage. No VM start, retained source/report/backup mutation or
replay. Roll back only the error metadata guard/test, preserving saved evidence.
Original Vault sync pending outside checkout.

## 2026-10-04: failed HTTP error cleanup remains unknown (offline)

A test-owned exact-path 404 whose response close raises after closing formerly
leaked the raw transport exception. The client now classifies timely cleanup
failure as `API_UNAVAILABLE` and late cleanup failure as `API_DEADLINE`, never
as missing-object evidence. The negative failed before repair; 99 focused
lineage tests and local verification on rerun passed. Private logs are under
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-015/`.
No VM start, live catalog/login, archived replay, retained source/credential/
report/backup change or GitHub write. This does not establish producer
authenticity, atomicity, whole-catalog/column coverage or operator acceptance.
Roll back only the cleanup guard and fixture; preserve reports. Original Vault
sync pending outside this checkout.

## 2026-10-04: thrown 404 closure deadline regression (offline)

A disposable HTTPError with the requested URL/status but a close that crosses
the observation deadline remains `API_DEADLINE`, not timely missing evidence.
The existing post-close check already enforced this; the new regression fixture
passes without a reader change (98 focused tests). No live catalog GET/login,
VM start, retained source/credential/report/backup change or producer
authentication. Roll back the fixture only, preserving reports; original Vault
sync pending outside this checkout.

## 2026-10-03: HTTP error URL bound to requested catalog entity (offline)

An injected 404 for a different URL previously became missing-object evidence
for the requested fixed path. The error branch now checks `geturl()` against the
exact request after closing the error stream and before returning the status;
the observation deadline still takes precedence. An exact-path 404 remains
valid. The isolated negative failed before repair and 97 focused tests passed
afterward. This is an offline opener boundary, not remote producer identity or
atomic/whole-catalog/column lineage evidence. No live login, VM start, retained
source/credential/report/backup change or replay. Roll back only this guard and
fixture, retaining reports. Direct original Vault sync pending outside checkout.

## 2026-10-03: refresh metadata on fixed catalog responses (offline)

The bounded reader refuses `Refresh` on a returned 200 rather than promoting
JSON that advertises a second target. A disposable alternate-path response was
accepted before repair; an undecorated response still passes. The assumption is
conservative and has not been checked against a live producer. Focused lineage
tests pass. No VM start, live login/GET, source/credential/report/backup change
or replay. This does not authenticate the producer or establish atomic,
whole-catalog or column lineage or operator acceptance. Roll back only the
header guard and fixture, preserving reports. Original Vault sync pending.

## 2026-10-03: catalog response final URL (offline)

When a response exposes urllib's final URL, the fixed-path reader checks it
against the exact requested URL within the observation deadline. A disposable
200 response reporting another catalog path was accepted before repair; a
matching URL remains valid. Responses without a `geturl` accessor retain
fixture compatibility, so this guard does not establish identity for such
openers. Focused lineage tests pass. No VM start, live login/GET, retained
credential/source/report/backup mutation or replay. Final URL metadata cannot
authenticate the producer, guarantee atomicity or establish whole-catalog/
column lineage or operator acceptance. Roll back only the URL guard and fixture;
retain historical reports. Direct original Vault sync pending outside checkout.

## 2026-10-03: successful catalog GET with redirect metadata (offline)

The fixed-path reader now refuses `Location` on a returned HTTP 200, including
a self-reference, before accepting JSON as catalog evidence. This is a
conservative compatibility assumption, not an observed live producer contract.
Disposable negatives failed before repair; focused lineage (94 tests), local
verification and design-context checks passed. Dedicated Colima was observed
stopped; no VM start, live login/GET, retained source/credential/report/backup
mutation or replay. This response check does not authenticate the producer,
guarantee atomicity, or establish whole-catalog/column lineage or operator
acceptance. Roll back only the redirect guard and fixture, retaining saved
reports. Direct original Vault sync pending outside this checkout.

## 2026-10-03: declared catalog content location (offline)

A fixed-path catalog GET now refuses any `Content-Location` declaration before
promoting its JSON body to evidence. This conservative assumption avoids
attributing a representation explicitly located elsewhere to the requested
entity; even a self-referential declaration is refused until live compatibility
is established. Disposable negatives failed before repair, then 93 focused tests
passed. No live login, VM start, retained report/source/credential/backup mutation
or replay. Header refusal does not authenticate the catalog or establish atomic,
whole-catalog or column lineage or operator acceptance. Roll back the location
guard and fixture only, retaining saved evidence. Original Vault sync pending
outside this checkout.

## 2026-10-03: partial catalog representation metadata (offline)

The bounded reader now refuses `Content-Range` on a returned HTTP 200 before
using a JSON-looking body as a complete catalog observation. A disposable
partial-range and unsatisfied-range fixture fail closed; the same body with
matching length and no range remains valid (92 focused tests). No live login,
VM start, retained source/credential/report/backup change or replay. This
response-shape check cannot authenticate the producer, guarantee atomicity or
establish whole-catalog/column lineage or operator acceptance. Roll back only
the header guard and fixture, preserving saved reports. Original Vault sync
pending outside checkout.

## 2026-10-03: conflicting catalog media declarations (offline)

Disposable GET responses pin refusal of a repeated UTF-8/non-UTF-8 charset
parameter and a comma-combined JSON/plain media declaration; the existing
bounded reader rejects both before their JSON-looking bodies become evidence.
The focused lineage suite passed (91 tests); no reader change, live login/GET,
VM start or retained source/credential/report/backup mutation. This is not
catalog authentication, atomicity, whole-catalog/column lineage or operator
acceptance. Roll back the two fixture rows only, keeping historical reports.
Direct original Vault sync pending outside checkout.

## 2026-10-03: quoted UTF-8 catalog charset (offline)

The read-only JSON reader now accepts the ordinary HTTP quoted UTF-8 charset
alongside the unquoted form; a quoted non-UTF-8 charset remains invalid.
Disposable response fixtures and 91 focused tests passed. No VM start, live
login/GET, retained source/credential/report/backup changes or request replay.
The label does not authenticate the catalog or establish atomic, whole-catalog
or column lineage or operator acceptance. Roll back only the media-type matcher
and fixtures, preserving reports. Direct original Vault sync pending outside
checkout.

## 2026-10-03: declared catalog media type (offline)

The bounded read-only client now refuses a response explicitly labeled as a
non-JSON media type (or conflicting duplicate types), even when the exposed
bytes parse as JSON. A single `application/json` with optional UTF-8 charset
remains supported; undeclared types retain compatibility. Disposable fixture
and 91 focused lineage tests passed. No live login/GET, VM start or retained
source/credential/report/backup modification. This cannot authenticate the
catalog, make separate reads atomic, or establish whole-catalog/column lineage
or operator acceptance. Roll back only the media-type guard/test while keeping
historical reports. Direct original Vault sync pending outside checkout.

## 2026-10-03: catalog transfer-token boundaries (offline)

Additional disposable response fixtures pin the existing rule that a single
case-insensitive `chunked` token is accepted, while a trailing blank or
parameterized token is refused before JSON can become evidence. The focused
lineage suite passed; no client behavior changed. No live API/login, VM start,
retained source/credential/report/backup mutation or request replay. This does
not authenticate the catalog, ensure atomic observations or establish whole-
catalog/column lineage or operator acceptance. Roll back only the fixture,
preserving reports; direct original Vault sync pending outside checkout.

## 2026-10-03: encoded catalog responses are not decoded evidence (offline)

The bounded read-only GET refuses a declared `Content-Encoding`, including
`identity`, rather than treating JSON-looking bytes as an already decoded
representation. A disposable response with `gzip` or `identity` is rejected
and closed; a plain response remains accepted. Focused lineage tests pass.
No VM start, live catalog login/GET or retained source/report/backup change.
This does not authenticate the catalog, provide atomic observations or establish
whole-catalog/column lineage or operator acceptance. Roll back this header
guard and disposable fixture only; preserve historical reports. Original Vault
sync remains pending outside this checkout; broader checks are in Milestones.

## 2026-10-03: unsupported catalog transfer coding refused (offline)

The bounded catalog reader accepts a single `chunked` transfer coding (decoded
by urllib), but rejects unsupported and repeated codings without a declared
length before treating JSON as evidence. Disposable decoded responses cover
identity, repeated and compound codings plus the valid chunked case. No live
login/GET, VM start or retained source/credential/report/backup modification.
This framing check cannot authenticate the catalog or establish atomicity,
whole-catalog/column lineage or operator acceptance. Roll back only the reader
guard and fixture, keeping saved reports. Original Vault sync pending outside
checkout; broader verification is in Milestones.

## 2026-10-03: conflicting catalog HTTP framing refused (offline)

The bounded catalog reader refuses a response declaring both `Content-Length`
and `Transfer-Encoding`, even if the exposed body is complete JSON of the
declared length. A disposable conflicting-header response fails closed and
closes its stream. Focused lineage and combined verification passed (Milestones).
No live catalog login/GET, VM start, retained credential/source/report/backup
mutation or request replay. This does not authenticate HTTP peers, guarantee
atomic observations, whole-catalog/column lineage or operator acceptance. Roll
back only the framing guard/test, preserving historical reports; direct original
Vault synchronization remains pending outside checkout.

## 2026-10-03: response header access within catalog deadline (offline)

The bounded GET reader now checks the observation clock after reading declared
length headers, before classifying malformed framing. A disposable header
accessor expiring the deadline cannot be reported as a timely malformed header;
the focused lineage suite passed (87
tests). No live login/API, VM start, retained source/credential/report/backup
mutation or replay. This does not authenticate the catalog, establish atomicity,
whole-catalog/column lineage or operator acceptance. Roll back the deadline
guard and disposable fixture only; preserve saved reports. Direct original
Vault synchronization remains pending outside checkout.

## 2026-10-03: declared catalog body length (offline)

The bounded GET reader now checks a single declared `Content-Length` against
the exact bytes received before treating valid JSON as evidence. A truncated
but syntactically complete prefix, conflicting or malformed lengths, and an
over-limit declaration fail closed; a matching length remains valid. The
disposable focused test covers these cases; combined verification is recorded
in Milestones. No live catalog login/GET, VM start, retained credential/source/
report/backup mutation or request replay. This only checks declared HTTP
framing, not catalog authenticity, atomicity, whole-catalog/column lineage or
operator acceptance. Roll back reader/test only; preserve saved reports.
Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: malformed catalog read chunk is not JSON evidence (offline)

An isolated disposable response returning text instead of bytes repeatedly hit the response-size limit rather than rejecting the malformed stream. The reader now checks both the post-read deadline and exact byte type before counting chunks. The negative failed before repair and the 85-test lineage suite passed afterward. No live login/API, VM start or retained credential/source/report/backup change. This guards client framing only, not catalog authenticity, atomicity, whole-catalog/column lineage or operator acceptance. Roll back only the chunk guard and disposable fixture, retaining historical reports. Direct original Vault synchronization remains pending outside checkout; broader checks are in Milestones.

## 2026-10-03: catalog response close must finish within deadline (offline)

A disposable decoded GET response whose close crosses the observation deadline was previously returned as catalog evidence. The reader now checks time after leaving the response context, so it refuses late closure as `API_DEADLINE`; the isolated negative failed before repair and the 84-test focused suite passed afterward. No live login/API, VM start, credential/source/report/backup modification or replay. This client-side check neither authenticates the catalog nor makes separate observations atomic or establishes whole-catalog/column lineage or operator acceptance. Roll back only this deadline check and fixture, preserving historical reports. Broader verification is in Milestones; direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: non-integer catalog HTTP status is not evidence (offline)

An isolated disposable response with status `200.0` passed Python's equality check as HTTP 200 before repair; a thrown HTTP error with code `404.0` could likewise be mistaken for missing catalog evidence. Require exact integer 200 on returned responses and an exact integer 4xx/5xx on thrown HTTP errors, after the existing deadline checks and stream closure. The isolated negative failed before repair; 83 focused lineage tests and combined verification passed afterward (Milestones). No live login/API, VM start, retained source/credential/report/backup mutation or request replay. This does not authenticate the producer, provide atomic observations, whole-catalog/column lineage or operator acceptance. Roll back the two status guards and disposable test only, preserving historical reports. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: thrown status accessor closes its stream (offline)

A disposable HTTP error whose `code` accessor raises an I/O error previously escaped without closing its body. The bounded reader now closes the error in a `finally` block and classifies the timely failure as `API_UNAVAILABLE`; a late failure remains `API_DEADLINE`. The isolated negative failed before repair; focused and broader results are in Milestones. No live catalog, credential, retained source/report/backup or VM mutation. This does not authenticate the catalog, provide atomic observations or establish whole-catalog/column lineage or operator acceptance. Roll back only the error handler and disposable test, preserving historical evidence. Original Vault sync remains pending outside checkout.

## 2026-10-03: late HTTP-error code access (offline)

A disposable thrown 404 whose code accessor expires the observation confirms the existing post-error deadline check returns `API_DEADLINE` and closes the stream; it cannot count as missing catalog evidence. No reader change or real login/GET. Focused checks and limits are recorded in Milestones. This does not authenticate the API or provide atomic, whole-catalog or column lineage evidence. Roll back the fixture only, retaining reports; original Vault synchronization remains pending outside checkout.

## 2026-10-03: status access stays within catalog deadline (offline)

The bounded reader checks time again after obtaining the response status. A disposable response whose status access ends after expiry is refused as `API_DEADLINE`, rather than classifying its late 404 as an on-time invalid response. Timely status behavior is unchanged. No real catalog login/GET, VM start or retained credential/source/report/backup change; this test-owned delayed status is not remote producer authentication, atomicity, whole-catalog/column lineage or operator acceptance. Roll back this additional guard and fixture only, retaining saved reports. Focused and broader evidence is in Milestones; direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: late invalid and oversized catalog responses (offline)

The bounded client now checks its deadline before classifying an oversized chunk and when decoding fails. Disposable late oversized bytes and malformed JSON discovered after expiry no longer receive timely size/JSON classifications; on-time failures keep their existing codes. The isolated malformed negative failed before repair. No live catalog login/GET, VM start or retained credential/source/report/backup mutation. This does not authenticate the catalog, make observations atomic or establish whole-catalog/column lineage or operator acceptance. Roll back the two deadline guards/tests only, preserving saved reports. Direct original Vault synchronization remains pending outside checkout; verification is in Milestones.

## 2026-10-03: late transport error respects catalog deadline (offline)

A disposable opener raises `OSError` only after the bounded observation deadline. Previously it became `API_UNAVAILABLE`; the client now classifies it as `API_DEADLINE`. A timely transport error still yields `API_UNAVAILABLE`. The negative failed before repair; focused and broader results are in Milestones. No live login/API, VM start, retained credential/source/report/backup change or request replay. Both failures remain unknown catalog evidence; this does not authenticate the producer or establish atomicity, whole-catalog/column lineage or operator acceptance. Roll back the transport exception guard/test only; preserve saved reports. Direct original Vault sync pending outside checkout.

## 2026-10-03: returned HTTP status after catalog deadline (offline)

The bounded client now checks its deadline immediately after an opener returns a response, before inspecting its status. A disposable opener returning a late non-200 response is classified as `API_DEADLINE`, while an on-time non-200 response retains `API_RESPONSE_INVALID`; both streams close. This complements the thrown-HTTPError guard without a live login/GET, VM start, or retained credential/source/report/backup change. It cannot authenticate catalog state, establish atomicity or whole-catalog/column lineage, or grant operator acceptance. Roll back this guard and test only, preserving saved reports. Original Vault direct sync pending outside checkout; verification in Milestones.

## 2026-10-03: late catalog 404 is not absence evidence (offline)

The bounded GET client now checks its deadline when an HTTP error arrives, after closing its response stream. A disposable 404 returned only after expiry previously escaped as a normal `APIError` (potential missing-edge evidence); it now fails with `API_DEADLINE`. An on-time 404 retains its prior meaning. The negative failed before repair; focused and full checks are in Milestones. No live login/API, VM start or retained source/report/credential/backup change. This client-side limit cannot authenticate the catalog, guarantee an atomic observation, establish whole-catalog/column lineage or grant operator acceptance. Roll back only the HTTP-error deadline guard and its test; preserve historical reports. Original Vault direct sync pending outside checkout.

## 2026-10-03: expired login response cannot authorize a catalog GET (offline)

A disposable login response completing after the bounded client deadline is rejected before token assignment or the first GET. This independently pins the existing POST-to-GET boundary; no reader change was required. Focused lineage and combined local verification are recorded in Milestones. No live login/catalog, VM start or retained credentials/source/report/backup change. The test does not undo a sent POST, authenticate a catalog, establish atomicity, whole-catalog/column lineage or operator acceptance. Roll back only the disposable test; retain historical evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: valid escaped surrogate pair remains catalog data (offline)

An independent disposable HTTP response confirms the bounded decoder preserves a valid escaped surrogate pair as its Unicode scalar while continuing to reject lone surrogates; no reader behavior changed. Focused lineage and combined local verification passed (Milestones). No live login/API, VM start or retained source/credential/report/backup change. This is JSON compatibility, not catalog authentication, atomicity, whole-catalog/column lineage or operator acceptance. Roll back only the test, preserving historical evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: client deadline cannot be disabled by constructor input (offline)

The read-only catalog client now refuses nonfinite, nonpositive, boolean or above-300-second timeout inputs before a login attempt. A disposable infinite-timeout negative previously constructed an unbounded client; a small positive timeout remains valid. Focused lineage and broader verification are recorded in Milestones. The default 300-second bound and existing one-login/get-only behavior are unchanged. No live API/login, VM start, retained credential/source/report/backup mutation. A client-side deadline does not authenticate catalog producers, make reads atomic or establish whole-catalog/column lineage or operator acceptance. Roll back the constructor guard/test only while retaining historical reports. Original Vault direct sync pending outside checkout.

## 2026-10-03: response completion stays inside the catalog deadline (offline)

The read-only client now checks its deadline after the final response read and after JSON decoding. A disposable response whose EOF arrives after the deadline must not become catalog evidence. Focused and broader checks are in Milestones. The deadline is a client-side bound, not remote API authentication or an atomic catalog snapshot. No live login/GET, saved source/report/credential/backup mutation or VM start. Roll back reader/test only while preserving historical reports; direct original Vault sync pending outside checkout.

## 2026-10-03: escaped duplicate extension keys in saved and catalog JSON (offline)

A disposable saved summary with an ignored nested `owner` / `ow\u006eer` pair is invalid rather than eligible; a disposable catalog response with the same duplicate pair is refused before it can become catalog evidence. The existing bounded decoder already checks keys after escape decoding, so this is regression coverage only, not a reader change. Focused checks and combined verification are recorded in Milestones. The dedicated VM was read-only observed stopped; no real login/API, retained source/report/backup mutation or request replay. This does not authenticate source/catalog producers, make observations atomic, or establish whole-catalog/column lineage or operator acceptance. Roll back the disposable tests only; preserve saved evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: saved-summary Unicode boundary regression (offline)

An independent disposable saved-source test pins the existing decoder's rejection of escaped lone surrogates in ignored values and keys and invalid raw UTF-8. These cannot make a Data Plan eligible or cause catalog reads. No reader behavior changed. The focused suite and repository checks passed; see Milestones. No actual credential, saved source, report or backup was changed and the dedicated VM stayed stopped. This is local JSON integrity, not source/catalog authentication, atomicity, whole-catalog/column lineage or operator acceptance. Roll back the regression test only; preserve retained evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: nonzero JSON underflow cannot become zero (offline)

The shared bounded decoder now refuses a nonzero floating JSON mantissa that Python rounds to zero (for example `1e-999`), including unused catalog response fields and saved-summary fields. Both disposable negatives failed before repair; literal zero with a large negative exponent and finite ordinary numbers remain valid. An invalid saved summary cannot become eligible, and an invalid catalog response cannot become present evidence. No live login/API, VM start, retained source/credential/report/backup change or request replay. This does not authenticate producer/catalog provenance, guarantee atomicity or establish whole-catalog/column lineage or operator acceptance. Roll back decoder/tests only; preserve stopped VM and historical reports. Direct original Vault sync pending outside checkout; verification is in Milestones.

## 2026-10-03: reject nonfinite exponent overflow (offline)

The shared bounded JSON decoder now rejects numbers such as `1e999` and `-1e999` that Python would otherwise parse as infinity, even in unused members. Disposable HTTP negatives failed before repair and passed afterward. A separate saved-summary fixture confirms an overflow in an unused field cannot enter the eligible source domain; no retained source was edited. Finite JSON values retain existing behavior. Focused and broader verification are in Milestones. This is not independent producer/API provenance, atomicity, whole-catalog or column lineage, or operator acceptance. Roll back only decoder/tests; preserve historical reports and the stopped VM. Direct original Vault sync pending outside checkout.

## 2026-10-03: credential parent identity across login (offline)

The existing one-POST credential login now compares the pinned private directory's full metadata before reading `.env` and after the second file read. A disposable POST fixture changed only the directory mtime with the file bytes/identity unchanged; previously the first GET proceeded, now login fails closed with no GET or retry. This cannot retract the POST or prevent a same-UID actor restoring metadata between checks, nor authenticate the credential producer or remote API. No live login/GET, retained credential/source/report/backup or VM change. Focused checks and full regression are in Milestones; rollback the reader/test only, preserve evidence. Original Vault sync remains pending outside checkout.

## 2026-10-03: collection path identity at return (offline)

The final collection inventory check now compares the pathname entry's full directory identity with the opened descriptor and initial metadata. A disposable replacement reported at the final path lookup, after the earlier path check, previously returned the old inventory when the replacement had the same owner and mode; it now refuses `SOURCE_CHANGED`. The negative failed before repair. This is a bounded offline observation, not atomicity against a restoring actor or independent source/catalog provenance. No retained source, report, credential, backup or VM was changed; direct original Vault sync remains pending outside checkout. Revert only the reader/test change and retain historical reports. Verification and other gaps are recorded in Milestones.

## 2026-10-03: non-lossy catalog JSON (offline)

The bounded read-only catalog client now rejects raw invalid UTF-8 and escaped lone surrogates anywhere in a response, including ignored fields/keys. Previously those could be decoded with replacement or carried as invalid Unicode into the evidence comparison. Disposable HTTP negatives failed before the guard; ordinary multibyte Unicode and surrogate-pair characters remain accepted. Malformed responses cannot become present/missing evidence; the existing observation path marks unavailable catalog reads unknown. No live login/GET, VM start, saved credential/source/report or backup mutation. This is response shape validation, not authenticated API/producer provenance or atomic catalog observation; whole-catalog/column lineage and operator acceptance remain open. Roll back decoder and tests only, retaining historical reports. Direct original Vault sync pending outside checkout; checks in Milestones.

## 2026-10-03: credential read-to-identity and parent stability (offline)

The private credential reader now returns identity captured by the verified open-file read and checks that the entry still matches it before returning. A disposable touch immediately after that read used to pair old bytes with new metadata. Separately, it checks the opened parent directory's full metadata across the read; a disposable change from 0700 to 0755 previously returned credential bytes. Both new negatives pass, alongside 64 focused tests and local verification. No live login, GET, VM start, retained credential, saved source, report or backup change occurred. Same-UID restoration between observations, credential producer/API provenance and whole-catalog/column lineage remain unproved. Roll back reader and temporary tests only; preserve retained evidence. Direct original Vault sync pending outside checkout; broader results in Milestones.

## 2026-10-03: credential identity across login (offline)

The private `.env` reader now compares file device/inode/mode/owner/size/mtime/ctime/link count across its two bounded reads around the one existing login, as well as the bytes. A disposable fixture touched its mtime after the POST while leaving the bytes identical; before repair the GET proceeded, now login fails closed without a GET or retry. This does not undo the already-sent login, authenticate the credential producer or detect a same-UID actor that restores metadata between observations. No real credential, retained source/report/backup or VM was changed; read-only Colima listing showed the dedicated profile stopped. Roll back only this reader/test, preserving existing evidence. Direct original Vault sync pending outside checkout; broader checks and unresolved criteria are in Milestones.

## 2026-10-03: credential read bound to original directory (offline)

The OpenMetadata read-only client now reads its existing private `.env` through a no-follow directory descriptor, and rechecks that the directory is still at its original path before using the bytes. A disposable parent-directory rename and symlink substitution after file open previously returned credential bytes without error; the negative failed before repair and passes afterward. Existing login fixtures still exercise a single login and no retry. This does not authenticate the credential producer, rule out a restoring same-UID actor, or prove catalog/API authenticity. No real credential, saved source, report, backup or VM was modified; the dedicated VM was read-only observed stopped. Revert the reader/test only if needed, preserving retained evidence. Original Vault direct sync pending outside checkout; verification and remaining boundaries are recorded in Milestones.

## 2026-10-03: metadata stability during a saved-file read (offline)

The snapshot now compares an opened file's full device/inode/mode/owner/size/mtime/ctime/link-count metadata before and after reading, and compares its final entry mode as well. A disposable fixture changed a file during `os.read` and restored its mtime and bytes; previously the snapshot succeeded, while the tightened reader refuses it. `make test-local-lineage-coverage` passed (60 tests). No saved source, report, catalog or backup was modified; read-only Colima listing showed the dedicated VM stopped. This is conservative against concurrent writes and cannot detect a same-UID actor restoring all metadata between checks, authenticate a producer, establish atomicity or prove whole-catalog/column lineage. Roll back reader/test only, preserving retained evidence; original Vault sync remains pending outside checkout. Broader verification is recorded in Milestones.

## 2026-10-03: bind source metadata across separate reads (offline)

The saved-source digest now includes directory owner/mode/size/mtime/ctime/link count and file device/owner/ctime/link count, in addition to existing identities and file bytes. Two disposable negatives showed that changing an empty directory's mtime, or changing a file's metadata and restoring its mtime without changing its bytes, previously produced the same digest across separate source reads; both now yield unknown edges with no coverage rate. Intra-read metadata checks remain in place. This is a digest format change: do not compare new digests to historical report hashes or rewrite retained reports. Focused (59 tests), full local verification and design-context checks passed; see Milestones. Dedicated Colima was read-only observed stopped; no live API/SQL, retained source/report/backup mutation or request replay. Metadata restoration between observations and independent producer/API provenance remain unproved; whole-catalog/column lineage and operator acceptance are still open. Roll back only the reader and disposable tests while preserving retained evidence; original Vault direct sync pending outside checkout.

## 2026-10-03: inventory drift and empty-directory replacement (offline)

The initial eligible run inventory still fixes the denominator, but a changed or unavailable final collection listing now makes every initially observed edge unknown rather than reporting complete coverage or a success rate. A disposable new run during catalog observation and an injected failed final listing both reproduced the old misleading complete coverage before repair. Separately the source digest now binds opened directory device/inode, including an empty nested directory: replacing a disposable empty directory between source reads previously left the digest unchanged and present edges intact. All three negatives pass after repair. This changes snapshot digest values; compare newly computed snapshots within the same reader version, not old report hashes. No saved source/report/catalog/backup or VM was changed; read-only Colima status was stopped. These checks cannot detect restored identities between reads, authenticate producer or API, provide an atomic inventory, or establish whole-catalog/column lineage or operator acceptance. Roll back reader/tests only, preserving saved reports and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: nested saved-run directory metadata (offline)

The snapshot now compares each opened nested directory's full device/inode/mode/owner/size/mtime/ctime/link-count identity before and after its traversal and against its no-follow parent entry. A disposable nested-directory mtime change during traversal is refused, even when names and file hashes remain unchanged. The 54-test focused suite and full local verification passed; see Local Experiment Milestones. This assumes concurrent legitimate changes should fail closed. Reverting this reader/test change leaves retained source and reports untouched. No live catalog, VM, retained source or backup was changed. Restored metadata, independent provenance, atomicity, whole-catalog/column lineage and operator acceptance remain unproved. Original Vault direct sync pending outside checkout.

## 2026-10-03: collection run-entry metadata stability (offline)

The descriptor-relative collection listing now compares each no-follow run entry's size, mtime, ctime and link count as well as identity/owner/mode across its two name reads. A disposable run whose mtime changed after enumeration is refused with `SOURCE_INVENTORY_CHANGED`; normal listings still pass. This conservatively rejects concurrent edits, but cannot detect restored metadata, authenticate a producer, or establish an atomic inventory. Focused lineage tests passed (53); broader verification is in Local Experiment Milestones. The dedicated VM was read-only observed stopped; no saved sources, reports, backups or catalog were mutated. Roll back only the listing and test, leaving retained evidence intact. Original Vault direct sync pending outside checkout.

## 2026-10-03: source and collection root metadata stability (offline)

The saved-run snapshot and collection listing now compare the opened root directory's device, inode, mode, owner, size, mtime, ctime and link count across their respective bounded reads. Two disposable negatives changed only the root mtime after traversal/listing while leaving the entry names and privacy unchanged; both were accepted before repair and refused afterward. `make test-local-lineage-coverage` passed (52 tests). This is a conservative check: concurrent metadata updates may reject an otherwise readable run. A same-UID actor restoring metadata between observations remains undetectable; neither atomicity, producer authentication, whole-catalog/column lineage nor operator acceptance follows. The dedicated VM was read-only observed stopped; no retained source, report, backup or catalog was changed. Roll back reader/tests only, preserving evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: external hard links in saved source (offline)

The run snapshot refuses regular files with more than one hard link, both when opening and after reading; the final entry comparison includes link count. A disposable hard link from a file outside the run was previously hashed as run evidence; the isolated negative failed before repair and the focused suite passed (50 tests). This assumes saved Data Plan artifacts are single-link files; valid single-link fixture verification still passes. It does not authenticate the producer or defeat a same-UID actor restoring state between checks. No retained source, report, backup, catalog or VM was changed. Roll back the snapshot guard/test only and preserve evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: source snapshot same-name replacement (offline)

The descriptor-relative snapshot now compares no-follow entry identities and metadata after its second directory listing. A disposable source file replaced at the same name after hashing previously produced a digest; the isolated negative failed before repair and the 49-test focused suite passed afterward. Only test-owned fixtures changed; the dedicated VM remained stopped. This cannot detect a replacement restored between checks, authenticate source producers, establish an atomic catalog observation or satisfy whole-catalog/column lineage. Roll back reader/test only, retaining reports, sources and backups. Direct original Vault sync pending outside checkout.

## 2026-10-03: same-name collection entry replacement (offline)

An independently enumerated run name now retains a no-follow device/inode/mode/owner identity through the second listing. Replacing a test-owned directory with another directory at the same name previously passed both name lists; the isolated negative failed before repair, and the focused suite passes (48 tests). Unexpected/symlink entries remain visible to per-run validation rather than being silently discarded. A restored entry between reads is still undetectable; no atomic snapshot or producer authentication is claimed. No retained source, report, catalog, API, SQL, backup or VM was touched. Roll back reader/test only and preserve saved evidence; direct original Vault sync pending outside checkout.

## 2026-10-03: collection listing stability (offline)

The open-descriptor collection inventory now re-enumerates run names before returning and refuses additions/removals during the read. Independently it compares the opened collection directory's owner/mode before and after enumeration and against its current path. Two disposable negatives (new run and mode change) failed before repair; the focused suite passes (47 tests). The fixed collection may be 0755; individual source runs remain private. No retained source, report, catalog, API, SQL, backup or VM was changed. These observations are not atomic against a same-UID actor restoring state between checks and do not establish provenance, whole-catalog/column coverage or operator acceptance. Roll back reader/tests only and preserve retained data; original Vault direct sync pending outside checkout.

## 2026-10-03: source directory membership recheck (offline)

After visiting a bounded source directory, re-enumerate its entries through the same open descriptor and reject a changed set rather than hashing an incomplete snapshot. A disposable run gained a new sibling immediately after the first listing and incorrectly returned a digest before repair; the focused suite then passed (45 tests). No saved Data Plan, catalog, API, SQL, VM, report or backup was changed. This does not detect a same-UID actor that restores entries between checks, prove atomicity or source provenance, or satisfy whole-catalog/column lineage and operator acceptance. Roll back reader/test only; preserve retained sources and reports. Original Vault direct sync pending outside checkout.

## 2026-10-03: source directory privacy after traversal (offline)

The source snapshot now checks both the opened descriptor and current entry for 0700 ownership/mode after visiting each nested directory, and checks the root descriptor again after the walk. Two disposable negative fixtures change a nested directory or the root to 0755 after its initial inspection; the nested fixture failed before repair. `make test-local-lineage-coverage` passed (44 tests). The first `make verify-local-experiment` stopped at an unchanged Warehouse saved-completion assertion (expected `SAVED_COMPLETION_UNVERIFIED`, observed `JOURNAL_UNVERIFIED`); focused Warehouse rerun and second full verification passed, without modifying the test or claiming to repair its intermittency. `make verify-design-context` passed (89 documents). The dedicated Colima profile was read-only observed stopped; no saved Data Plan, catalog, SQL, backup or VM change. An actor who restores a mode between observations remains outside this guarantee; this is neither source-producer authentication nor current catalog/whole-catalog acceptance. Roll back the reader/tests only and preserve retained sources, reports and backups. Direct original Vault sync pending outside checkout.

## 2026-10-03: source file privacy recheck after read (offline)

The descriptor-relative source reader now checks current entry owner, 0600 mode and size/mtime/ctime against the opened file after reading. A test-owned source chmod after the file read but before final entry stat previously yielded a snapshot; the isolated negative failed before repair and passes afterward. `make test-local-lineage-coverage` (42 tests) and `make verify-local-experiment` passed. Only temporary fixture files were changed and restored; the dedicated Colima profile was observed stopped. No retained Data Plan, catalog, SQL, API, backup or VM was mutated. A same-UID actor modifying and restoring evidence between checks remains possible, and this does not establish independent provenance, whole-catalog/column lineage or operator acceptance. Roll back reader/test only, preserving existing source, reports and backups. Original Vault sync pending outside checkout.

## 2026-10-02: current catalog reread for seven historical Data Plans — bounded live

source読取のfd固定後、停止中の専用ColimaとOpenMetadata既存Composeの状態を確認。起動前にprofile設定hash、Metadata host状態15files（`.env`/鍵/4件backupを含む）、既存report3件、保存Data Plan7件のhash・inode/mode/mtimeとsnapshotをprivate記録。VM起動後、固定Docker context/socket、3既存容器のproject/service label、固定digest=image ID、mount/loopback port、exited状態を確認。`runtime.py up`・Compose `up`・migration・credential rotationは使わず、元の3 container IDだけPostgreSQL→search→serverの順に起動。healthyになってから既存`make local-lineage-coverage`を実行した。既存credentialによる認証POST1回の後は固定GETのみで、新report `tmp/local-experiment/lineage-coverage/bb0ade41206241248ec0fe7e5c81cd76/`をprivate作成。

実reportはeligible7・期待14 table-level edges、`present=14/missing=conflict=unknown=0`、分母/観測ともcomplete、sourceとmetadataの再読は安定。独立した別の認証済みGET session（32 API calls）で、保存Data Plan7件に対する生成table14件のUUID/owner/schema/column/evidence markerと、期待14edgeのlineage source/descriptionを再照合。これはcollector保存物とは別呼出しだが**同じOpenMetadata API**であり、VM/producerやDB volumeの独立認証ではない。旧2026-09-17の5run/10edge・前増分の模擬API不達14 unknownと混ぜない。現存SQL relationの実在やcolumn lineage、全catalog・常時dashboardの完成も主張しない。

完了後は元の3 container IDをserver→search→PostgreSQL順に停止し専用Colimaを通常停止、全てStoppedへ復帰。profile設定hash・Metadata host状態15files・既存report3件・保存source7件の起動前後不変を確認、新reportの2file/dirはprivate。DB/search named volumeは起動中に通常書込みがあり得、内容と保持PVC/backup全体の独立不変証明はない。停止ログには保持PodのPVC/secret volume unmountと`/var/lib/cni/` busyを記録。保持data/backup削除・全VM reset・GitHub書込みなし。private実行記録は`tmp/local-experiment/lineage-live-recheck/20261002/`、WP `local-partial`。回復は3容器とVMを追加起動せず停止状態を維持し、作成したprivate reportを根拠ごと保持する（旧report・backupを消して戻さない）。

Lineage focused1 suite/41件PASS（SHA256 `f2f724a31b2e70e369cf1e1424bfd7a69ad9228e03ad114f98ad10b51feaac64`）。初回全回帰はWarehouseの短いpartial-request testに`BrokenPipe`で停止し、失敗log（SHA256 `b1da2e49b865cba54a134495a459dce4d6cbd11288cfc59f3e61251c6f7402cd`）を保存。owner実装ではなくtestの期限競合を別WP-L3-VW-002増分で修正、再検証した最終全回帰121 suites/3839実行＝3831 PASS＋8 skip、90.39秒、log SHA256 `158330c6b663b60b9c48bfa43878ef8421efda2f17f6636993f2c1c042e18599`。design-context/secret scan・diff check PASS。

## 2026-10-02: saved source inventory/snapshot pinned to directory descriptors — offline

前増分はreport**出力**をfd固定したが、読取側`source_snapshot`は私有runの親を検査した後に`iterdir`/`read_file`をpathで再解決していた。checkout内隔離tempで親pathを検査直後に別treeへのsymlinkへ差し替えると、旧実装が別treeの保存runをhashして戻す負例を再現。source collection列挙・run snapshot・summary file読取を`/`からcomponent毎のno-follow directory fdとfd相対の有界走査・file読取へ変更。`_source`は元run fdをevidence validatorの呼出しまで保持して現pathとのinode照合を行い、途中で親が退避されたら正常なData Planに昇格させない。入力内のfile entryをstat/open間に別inodeへ替える負例も拒否。出力writerやcatalog APIの振舞いは変えない。元directory内部の同UID actor改変、途中で元に戻す競合、producer認証や全catalog網羅は未解決。

実保存Data PlanをAPI不達の**模擬client**でoffline読取すると、従来2026-09-17の5 run/10 edgeではなく、今は7 run/期待14 edgeが検証済み。14件すべて`unknown`、missing0、rate NULL、source snapshots前後一致。これを「現catalogの14/14存在」と読み替えない。metadata API/VMを起動・認証せず、現entity/lineageは未観測。credential・既存report・保存source・backupは書換え/削除しない（読取atime除外せず）。private記録は`tmp/local-experiment/lineage-source-reader-checks/20261002/`。初回smokeでは過去の分母10を期待して失敗し、実際の7/14を確認して修正した。WP `local-partial`。停止・復旧はこのsource reader差分のみ以前のローカルrevisionへ戻し、既存reportや元runは保持する。

focused1 suite/41件PASS、log SHA256 `b702b74d6db90eb41a8c8a7c9e11355b85ff1dcc54a811726ab26855e9158e7f`。全回帰121 suites/3839実行＝3831 PASS＋8 skip、91.09秒、log SHA256 `c987e1b3898ff11a2ed7666ded2add534b48d1fdae63c07e18b42500770305d6`。design-context/secret scan・diff check PASS、専用Colima Stopped。

## 2026-10-02: lineage report output pinned to owned directory descriptors

`write_report`の従来の`directory(OUTPUT_ROOT)`確認とpathname `output.mkdir`間に親pathを差し替えると、隔離tempの模擬別treeへ新report JSON/HTMLを作成してしまうことを負例で再現。checkout内の未作成親は開いたdirectory fdから相対作成し、既存親/出力root/新runの所有・mode・inodeを照合する。新runと2固定fileはfd相対作成・fsyncし、最後に現pathと開いたfdのidentityを再照合してからreport pathを返す。差し替え後の模擬別treeは空、元inodeに作成されたreportは失敗時も消さず、手動確認に残す。既存rootへのsymlink親も書込み前に拒否。既存report・catalog・Data Plan保存物・backupは削除/再生成しない。模擬同UID actorが既に開いたprivate root内の新run entryを作成直後に改変する競合と、保存source/catalogの独立認証は未解決。

実metadata API/VMは停止状態のまま使用せず、tempで生成したreportのみの機能検査。従来の歴史的分母10edge/10presentは再観測していない。既存のL5未達（全catalog・常時更新・column lineage・本番/人間受入）を残し`local-partial`。戻す場合は出力writer差分だけを以前のローカルrevisionへ戻し、差し替え負例で作ったprivate runも同一inodeを確認するまで削除しない。focused・全回帰の実測は[Milestones](../061_Local_Experiment_Milestones.md)へ記録する。

## Summary

Lineage充足率を可視化する

## Context

このWork PackageはLevel L5「OSS Fabric再現」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

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

`level:l5`, `area:metadata`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L5-LINEAGE-001: Lineage completeness dashboard.
Read AGENTS.md, related ADRs, and this Work Package.
Use ADR-0034 local commits and a bounded read-only report for verified historical Data Plan evidence.
Include tests, docs, risk notes, and rollback instructions.
Do not add unrelated dependencies or bypass policy checks.
```

## Definition of Done

- [ ] PR本文にIntent/Risk/Test/Rollbackがある。
- [ ] 変更対象がIssue範囲内である。
- [ ] Security/Policy gateを通過する。
- [ ] Agent Pawprintが記録される、または記録設計が更新される。

## 2026-09-17: bounded local implementation plan

依存WP-L1-META-001 / WP-L2-OBS-001はローカル実験で充足している。既存OpenMetadataと
Data Engineerの成功証跡を使い、履歴上のlineage充足状況を読み取り専用で可視化する。
歴史的PR/CI gatesは外部deliveryの条件であり、ADR-0034のlocal実行条件にはしない。

- [x] private Data Plan証跡を列挙し、既存のdbt/Dagster/cleanup検証を通ったrunを特定する。
- [x] 検証済みrunあたりfixture→stg→martの期待2edgesを既知分母にする。
- [x] OpenMetadataの同一identity/owner/schema/lineage観測をpresent/missing/conflict/unknownへ分類する。
- [x] invalid/failed/incompleteな入力を無言で消さず、件数と理由を明示する。
- [x] API不達、取得途中の証跡・entity revision変化、観測上限到達をmissingと混同しない。
- [x] private JSONと自己完結HTMLを生成し、分母・観測時刻・各runの根拠を辿れるようにする。
- [x] 元証跡、保持metadata、稼働環境の前後不変を検証し、実結果を追記する。

この分母はこのcheckoutにある検証済み実験履歴だけであり、組織全体の全table/column/pipelineを
網羅しない。削除済みSQL relationのhistorical catalog recordをlive tableとして表示しない。
ログイン以外はGETのみで、metadata修復/削除・password変更・再取り込みを行わない。
新service/依存パッケージ/LLM呼出は不要。JSON/HTMLはホスト上のprivate証跡で外部公開しない。
初期増分はtable-level 2edge chainの充足可視化であり、任意queryの完全なcolumn lineage、
常時更新dashboard、cross-platform lineage、本番受入を主張しない。

## 2026-09-17: first read-only coverage result

run `4ef6d7943baf4e8c914f32f63381b9c9`（09:09:24 UTC観測開始）は初回で成功。
保存済み5 Data Plansすべてのdbt/Dagster/cleanup証跡を検証し、期待10edges中present6/missing4、
conflict0/unknown0を確認。観測はcomplete、登録率60.0%、coverage completeはfalseと分けた。
未登録は`396f97befadd41b3af49bf4db9be4b1c`と`eb8902c0a4e54c8591dcbfee02550927`の各2edge。
API不達を模擬した実source結合では、分母10を維持したunknown10/missing0/rate NULLとなった。

private JSON/自己完結HTMLを`tmp/local-experiment/lineage-coverage/<run-token>/`に保存。
ブラウザ1280pxで6/10・各分類・既登録/未登録の詳細展開を確認し、document/tableの横はみ出し0、
console error0。検証用loopback serverは終了した。mobile viewportの実画面検証は未実施。

独立監査 `metadata-coverage-preflight/18d58ceea4b34635be2e267336669a9f/verification.json` は
31checks成功。23API endpointsのUUID/version/full semantic hashと全5source projections、
既存6table/6lineage、保護892files（hash/mode/mtime）、資格情報/.envとadmin markerが前後一致。
Metadata3serviceの状態も再観測済み。他のKube/Docker/Pawprints366は直近Iceberg refs監査を参照し、
今回再読したという主張はしない。catalog mutation0、model/cloud呼出0、新service/dependency0。

focused34 tests、全local949件（948成功＋標準環境optional OTel1skip）、設計80documents/14testsが成功。
次は確認した2runの不足履歴を既存Catalog Curatorで限定登録し、新しいreportで変化を再確認する。
常時更新/全catalog/column lineage/本番受入はこの増分の達成条件ではなく、local-partialを維持する。

## 2026-09-17: observed gaps completed with Catalog Curator

初回reportでmissingだったData Plans `396f97befadd41b3af49bf4db9be4b1c` と
`eb8902c0a4e54c8591dcbfee02550927` に既存Curatorを適用し、historical4table/4edgesだけ追加した。
Catalog runs `48c698b868ad4b18bc69c5c3bebc65de` / `1bfb893944c04f98a463484e7386affb` は成功。
各初回は2table/2edgeの4writes、再適用は0writes。同一taskの実Qwen推論は各314入力/96出力tokens、
4106ms / 2950ms。cloud/retry0、cost NULL、2run合計628入力/192出力tokens。
traceは`a5898cccc00b61dd68f7d5a00fecca87` / `8ec47c336227cc55a056a130a960bd5f`。

補完後のread-only report `ea83feab78db4e2189d8adab339af346`（09:16:39 UTC）は5run・10/10edges、
missing/conflict/unknown0、observation/coverageともcomplete。元の60%reportは変更せず保持した。
独立監査 `catalog-completion-audit/fdcf070415da47f082d8e0e5a39521dc/verification.json` の84checksと
metadata比較45checksが成功。既存6tables/6edges/anchorsは不変、新4tables/4edgesだけを帰属させた。
旧Pawprints366行のhash不変＋新10行（現376）、Loki10件/保存実OTel10spansの相関・usageを照合。
保持Kube/3Docker、履歴4tables/5DDL不変。934保護files中933は完全不変、既存認証入口の
admin-initialized markerだけmtime更新（content/mode/.env不変）を既知差として記録した。
reportの全10edge verdictとmetadata aggregate hashも独立APIから再計算済み。

本増分は既存実装を使う実験結果の更新で、直前の全949tests/設計80documents/14testsを継承する。
次はData→Catalogの一連実行と中断再開を専用の小さな入口へまとめる。Data開始intentと保存先を
先に固定し、同handoffでDataを二度呼ばず、成功証跡を検証できた場合だけ同Data PlanをCatalogへ渡す。
