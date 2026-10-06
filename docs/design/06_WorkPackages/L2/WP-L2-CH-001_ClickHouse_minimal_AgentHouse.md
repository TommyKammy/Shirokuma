---
project: Shirokuma
doc_id: "WP-L2-CH-001"
title: "ClickHouse minimal AgentHouse"
status: local-verified
created: 2026-07-05
updated: 2026-10-06
version: "0.58"
area: "workpackage"
tags: [shirokuma, wp, l2, observability]
---

# ClickHouse minimal AgentHouse

## 2026-10-06 private state directory stat failure (offline)

After opening the private state directory, a failed `fstat` previously left
its directory descriptor open. The reader now closes that descriptor and
reports the existing scoped open failure, without yielding an unverified
directory to any startup/read/export caller. A synthetic failed stat checks
that the descriptor is closed; 101 focused AgentHouse tests and combined local
verifier/design-context/diff checks pass (fake Dagster failure intentional). This does
not prove saved inode provenance, live ClickHouse startup or deduplication.
Dedicated Colima remained Stopped; no live HTTP/Docker or retained report,
credential, volume or backup accessed. Roll back only the scoped descriptor
cleanup/test if necessary, retaining saved resources and never replaying an
uncertain insert. Original Vault synchronization pending outside checkout;
operator acceptance remains open.

## 2026-10-06 short pinned staging read (offline follow-up)

The exporter also refuses a short read from the pinned staging inode even if
that read returns a valid first JSONL row: it must match the entire validated
response before link publication. The synthetic short-read fixture leaves a
private pending file and no public report; 100 focused tests pass. The earlier
full local verifier preceded this follow-up. Dedicated Colima last observed
Stopped; no live HTTP/Docker or retained resources accessed. This is an
offline transport boundary, not proof of atomic exclusion, power-loss
durability, deduplication or operator acceptance. Keep pending files for
inspection; revert only this test/byte guard if necessary, never replay an
unknown write. Original Vault synchronization remains pending.

## 2026-10-06 staged export byte readback before publication (offline)

The exporter now compares bytes read from its pinned, fsynced staging inode
with the validated response before publishing it. A same-size in-place edit
after file sync previously returned a changed report as success; the isolated
negative reproduced this and now leaves the pending file unpublished. Focused
99 AgentHouse tests and `make verify-local-experiment` pass (the fake Dagster
worker failure is an intentional negative). Dedicated Colima freshly Stopped;
no live Docker/HTTP or retained report, credential, volume or backup accessed.
One failed export can retain up to 8 MiB plus directory overhead. This check
does not atomically exclude a same-UID writer after readback, establish power-
loss durability or prove live deduplication. Preserve ambiguous pending files,
never replay unknown inserts; restore only this guard/test if compatibility
requires it. Original Vault synchronization pending outside checkout; live and
operator acceptance remain open.

## 2026-10-06 staged export size after file sync (offline)

The publisher now compares the pinned staging inode size to the validated
response length after file fsync, before linking a public report. A synthetic
append after fsync previously published an extra JSONL row as a successful
export; the isolated negative now refuses publication and retains the pending
inode. 98 focused tests, `make verify-local-experiment`, design-context and
diff checks pass (the fake Dagster failure is intentional). This size check cannot detect same-size edits or
atomically exclude same-UID interference. No live Docker/HTTP or retained
report/credential/volume/backup read/change; dedicated Colima last observed
Stopped. Preserve the ambiguous pending file and never replay an uncertain
insert. Revert only this guard/test if needed. Original Vault sync remains
pending outside checkout; operator acceptance remains open.

## 2026-10-06 readback parser recursion boundary (offline)

A deeply nested, private JSONL report previously escaped the smoke readback
with a raw `RecursionError` instead of a scoped failure. The readback now
refuses parser recursion as an invalid report, without replaying an insert or
removing the saved file. The isolated negative failed before the repair; 97
focused tests and `make verify-local-experiment`, design-context and diff
checks pass (the fake Dagster failure is an intentional negative). Dedicated
Colima freshly Stopped, with no live HTTP/Docker
or retained report, credential, volume or backup read/change. A synthetic
parser rejection cannot authenticate the report producer or prove live
deduplication; operator acceptance remains open. Restore only the scoped
exception guard/test if needed, preserving saved material. Original Vault
sync remains pending outside this checkout.

## 2026-10-06 independent export removal and readback identity checks (offline)

After removing the pending export name and syncing its directory, the publisher
rechecks the public inode/size/mode/link count before returning success. A
synthetic public-name swap during staging removal formerly returned the
substitute as successful; it now fails while retaining both files for inspection.
Separately the smoke reader checks private mode and link count after reading,
then checks the public entry's complete identity: synthetic hardlink creation
during read and mode change after close formerly passed but now fail. These
checks do not atomically exclude a same-UID writer or prove power-loss durability;
an ambiguous file may remain visible. 96 focused tests and the combined local
verifier pass after replacing a test-only `Path.hardlink_to` (absent in the Make
Python) with `os.link`. The first full verifier failed on that fixture error and
was not counted as a pass. Dedicated Colima freshly Stopped; no live HTTP/Docker
or saved report, credential, volume or backup read/change. Preserve ambiguous
files, never replay an uncertain insert. Revert only the scoped checks/tests if
verified compatibility requires it, keeping saved records. Original Vault sync
pending outside checkout; live deduplication and operator acceptance remain open.

## 2026-10-06 post-link export identity (offline)

The private exporter now checks both staging and published names against the
fsynced, still-open inode after the hardlink and directory sync, before removing
the staging name. An isolated source swap *inside* link previously returned the
substitute JSONL as a successful export; it now refuses success and retains both
ambiguous names plus the original pending inode for inspection. 93 focused tests
and the combined local verifier pass. This check is not atomic exclusion of a same-UID actor: the wrong hardlink
can be briefly visible or remain on failure, and a later swap remains possible.
Do not read or delete an ambiguous report automatically or replay an uncertain
insert. Dedicated Colima was freshly Stopped; no live HTTP/Docker, saved report,
credential, volume or backup accessed or changed. Restore only the scoped guard
and test if verified compatibility requires it, retaining all saved files.
Original Vault sync pending outside checkout; live deduplication and operator
acceptance remain open.

## 2026-10-06 staged export identity and startup short-write checks (offline)

The exporter now holds the written staging inode through directory sync and
refuses a substituted same-name inode before linking the public report. A
synthetic replacement with a different private JSONL file previously published
that replacement; now it fails and leaves the original and substitute for
inspection. Independently, a short write of a newly created private credential
or container environment file fails before fsync/consumer startup, retaining
the partial file for investigation instead of claiming a durable credential.
92 focused tests, `make verify-local-experiment`, design-context and diff check
pass. Both checks are local fail-closed conditions, not atomic
exclusion of a concurrent same-UID actor or a guarantee across power loss.
The dedicated Colima profile remained Stopped; no saved reports, credentials,
volumes or backups were accessed, and no HTTP/Docker request was made. A
pending/partial private file consumes at most the scoped payload size plus
directory overhead; do not remove or overwrite ambiguous retained material
automatically. Revert only these two scoped guards/tests if compatibility
requires it, preserving saved records; original Vault sync pending outside
checkout. Live ClickHouse, deduplication and operator acceptance remain open.

## 2026-10-06 private export short-write refusal (offline)

The publisher previously ignored the number of bytes written into its private
staging file. An isolated writer accepting only the first valid JSONL row
returned a truncated published report as success before repair. The writer now
requires the full payload length before flush/fsync/link; a short write keeps
the private pending inode for inspection, without publishing the `.jsonl` name.
90 focused tests and the full local verifier pass. This does not guarantee
power-loss atomicity, same-UID isolation or live ClickHouse completeness. A
failed attempt can leave one pending file of up to the 8 MiB payload cap plus
directory overhead; inspect
it without deleting saved exports or replaying unknown inserts. Dedicated
Colima Stopped, no live request or retained report/credential/volume/backup
read or change. Restore only this writer guard/test if necessary; original
Vault sync pending outside checkout and operator acceptance remains open.

## 2026-10-06 private smoke readback short-read refusal (offline)

A bounded file read may return a valid LF-framed first row before EOF while the
saved report inode still contains further rows. The smoke readback previously
accepted that prefix and could misstate export consistency; an isolated short
read reproduced the false success before repair. Readback now requires exactly
the pre-read inode size in addition to the existing post-read inode/entry
checks; 89 focused AgentHouse tests and the full local verifier pass. Dedicated
Colima freshly Stopped; no retained report, credential, volume or backup was
read/changed and no live HTTP/Docker operation occurred. This does not prove
atomic readback against same-UID writers, authentic ClickHouse replies or live
deduplication. Inspect ambiguous saved exports; never replay an uncertain
insert. Restore only the scoped guard/test if necessary, retaining saved files.
Original Vault sync pending outside checkout; operator acceptance remains open.

## 2026-10-06 HTTP attachment metadata (offline)

The local query client refuses any `Content-Disposition` on HTTP 200 before
reading the body or acknowledging an insert. Three synthetic 200 responses
previously succeeded, including an empty and an inline disposition; the clean
control and 88 focused tests pass. This is a conservative signature, not peer
authentication or proof of live completion. Dedicated Colima freshly Stopped;
no live request or saved credential/report/volume/backup read/change. Live
compatibility, deduplication and operator acceptance remain open. Never replay
an uncertain insert; revert only this scoped guard/test if verified runtime
behavior requires it, preserving saved resources. Original Vault sync pending
outside checkout. Combined local verifier, design-context and diff check pass.

## 2026-10-06 HTTP authentication challenge on 200 (offline)

Independently refuse a nominal 200 query response carrying `WWW-Authenticate`
or `Proxy-Authenticate` before reading its body or acknowledging an insert.
Two synthetic bad responses previously succeeded; 87 focused tests pass with
a challenge-free control. Combined `make verify-local-experiment`,
`make verify-design-context` and `git diff --check` pass (intentional fake
Dagster worker failure). This is a conservative header signature, not peer
authentication or proof against challenges hidden by an intermediary. Dedicated
Colima remains Stopped; no live query or retained credential/report/volume/
backup read/change. Never retry an uncertain insert. Revert only this scoped
guard/test if verified live compatibility requires it, preserving saved data.
Original Vault sync remains pending outside checkout; operator acceptance open.

## 2026-10-06 HTTP navigation and body type boundaries (offline)

Two independent acknowledgement checks: a 200 reply carrying `Location`,
`Content-Location` or `Refresh` is refused before body read, and a transport
returning non-byte body data is refused with a scoped error instead of an
unhandled exception or accidental success. Eight synthetic negative cases
previously reached success or escaped the scoped error; 86 focused tests pass.
The combined `make verify-local-experiment`, `make verify-design-context` and
`git diff --check` pass; the fake Dagster worker error is an intentional test.
The redirect check is deliberately conservative and requires live ClickHouse
compatibility verification. Dedicated Colima freshly Stopped; no live HTTP,
saved credential, volume, report or backup was accessed or changed. Neither
offline check authenticates the producer, establishes live deduplication or
grants operator acceptance. Never replay an uncertain insert; revert only the
scoped guards/tests if verified compatibility differs, preserving saved data.
Original Vault synchronization pending outside this checkout.

## 2026-10-06 explicit HTTP media type boundary (offline)

The query client refuses a 200 reply explicitly labeled HTML/XML, repeated
Content-Type or comma-combined media types before treating a write as complete.
It accepts absent type and the plain/tab-separated/JSON types used by its
current queries. Four synthetic bad acknowledgements were previously accepted;
84 focused tests pass. This is a conservative header check, not authentication
of ClickHouse or protection against an incorrectly labeled response. Live
Content-Type compatibility and deduplication remain unverified while the
dedicated Colima profile is Stopped; no retained data or credentials were read.
Do not replay an uncertain insert. Restore only this guard/test if verified
runtime behavior requires it, retaining saved records. Original Vault sync
pending outside checkout; no operator acceptance.

## 2026-10-06 CR-separated and indented-BOM exception text (offline)

The HTTP 200 query guard now refuses ClickHouse exception lines separated by
CR alone, or with a BOM after leading spaces/tabs. Three synthetic inserts
were incorrectly acknowledged before repair; 83 focused tests pass, including
a clean `Code:` text control. No automatic retry. Dedicated Colima freshly
Stopped; no live HTTP/Docker or saved credential, report, volume or backup
access/change. This signature cannot authenticate a producer, exclude other
unmarked errors, prove live deduplication or grant operator acceptance. Retain
saved records; revert only the scoped pattern/test if runtime compatibility
requires it, never replay an uncertain insert. Original Vault sync pending
outside checkout.

## 2026-10-06 indented streamed exception refusal (offline)

The HTTP 200 body guard also refuses a ClickHouse `Code: N. DB::Exception:`
line preceded by ASCII spaces/tabs, optionally after a BOM or earlier streamed
text. Three formerly acknowledged synthetic writes now fail without retry;
ordinary `Code:` output without `DB::Exception:` still passes (82 focused
tests). Dedicated Colima freshly Stopped; no live HTTP/Docker, saved credential,
report, volume or backup access/change. This is a conservative text signature,
not authenticated producer evidence, live deduplication or operator acceptance.
Preserve saved data and never replay an uncertain insert; revert only this
guard/test if verified live behavior requires it. Original Vault sync pending
outside checkout.

## 2026-10-06 BOM-prefixed streamed exception (offline)

The 200-response guard also recognizes ClickHouse's exception line when a
UTF-8 BOM prefixes it at the start of a line. Two synthetic failures previously
looked like successful inserts; a clean reply still passes (81 focused tests).
This does not detect arbitrary unmarked errors, authenticate ClickHouse, prove
live deduplication or authorize replay of an uncertain write. Dedicated Colima
freshly Stopped; no live HTTP/Docker or retained credentials, reports, volumes
or backups accessed or changed. Restore only this guard/test if verified live
behavior requires it, preserving saved data. Original Vault sync pending outside
checkout; operator acceptance remains open.

## 2026-10-06 streamed exception body refusal (offline)

A 200 response can contain ClickHouse's `Code: N. DB::Exception:` line
without an exception header, including after streamed result text. The query
client now refuses those bodies rather than acknowledging an insert. Two
negative fixtures and a clean reply pass (focused AgentHouse 80 tests). This
recognizes only that exact textual form: it is not server authentication or
proof against other unmarked failures. Dedicated Colima freshly Stopped; no
live HTTP/Docker or saved credential, report, volume or backup accessed or
changed. Never replay uncertain writes; live deduplication and operator
acceptance remain open. Revert only this scoped body guard/test if verified
runtime behavior requires it, retaining saved records. Original Vault sync
pending outside checkout.

## 2026-10-06 bounded SQL request and partial-range refusal (offline)

Two independent client boundaries: reject empty or >8 MiB UTF-8 SQL before
HTTP, and refuse a 200 response carrying any `Content-Range` before treating
an insert as acknowledged. Both guards have isolated negative fixtures and
valid short-query/clean-reply controls; focused AgentHouse 79 tests pass.
The request cap bounds local memory/transfer, not server resource consumption;
the range guard does not authenticate a peer or detect unmarked streamed errors.
Dedicated Colima freshly Stopped. No live HTTP/Docker, retained credential,
report, volume or backup was read or changed. Live compatibility, deduplication
and operator acceptance remain open; preserve saved records and never replay
an uncertain insert. Revert only these client guards/tests if verified runtime
behavior requires it. Original Vault sync pending outside checkout.

## 2026-10-06 HTTP content encoding boundary (offline)

The plain-text query client refuses an HTTP 200 response with non-identity or
multiple Content-Encoding fields before reading its body or acknowledging an
insert. Two synthetic encoded replies previously succeeded; an explicit single
identity encoding still succeeds. Focused AgentHouse 77 tests pass. This does
not authenticate the producer or exclude unmarked streamed errors, and no
uncertain insert may be replayed. Dedicated Colima freshly Stopped; no live
HTTP/Docker, retained credential/report/volume or backup access or change.
Restore only this guard/test if verified runtime behavior requires it; preserve
saved records. Original Vault sync pending outside checkout, with live
deduplication and operator acceptance still unproved.

## 2026-10-06 HTTP exception indicator (offline)

The client refuses a 200 response carrying `X-ClickHouse-Exception-Code`
before reading its body or treating a write as acknowledged. Three synthetic
header values (including empty and zero) previously returned success; focused
76 AgentHouse tests pass. A clean 200 remains accepted. This protects only
responses that carry the indicator; it does not authenticate the server or
rule out errors concealed in a streamed body. The dedicated Colima profile
was freshly Stopped; no live HTTP/Docker, retained credential, report, volume
or backup was accessed or changed. Never replay an uncertain insert. Roll
back only this guard/test if verified live behavior requires it, preserving
saved records. Original Vault sync pending outside checkout; no operator
acceptance claimed.

## 2026-10-06 ambiguous HTTP response framing (offline)

The client now refuses duplicate Content-Length fields and any Transfer-Encoding
on a 200 query response before reading the body. Three isolated HTTPMessage
fixtures previously treated these as completed write acknowledgements; the
focused 75 AgentHouse tests pass after repair. This conservative framing rule
does not authenticate the peer, prove that a response without either header is
complete, or establish live deduplication. Dedicated Colima was freshly Stopped;
no live HTTP/Docker or retained credential, report, volume or backup was read
or changed. Do not retry an uncertain insert. Restore only the framing guard
and fixture if verified live behavior requires it, retaining saved records.
Original Vault sync remains pending outside checkout.

## 2026-10-06 declared HTTP body length (offline)

The local HTTP client now refuses a 200 response whose declared Content-Length
is malformed, exceeds the export cap, or differs from the bytes actually read.
An EOF after a short `read(amt)` need not raise a transport exception; returning
that body as a completed write acknowledgement was unsafe. Six negative
fixtures and a valid exact-length reply pass (focused AgentHouse 74 tests).
No retry of an ambiguous insert. The dedicated Colima profile was freshly
Stopped; no HTTP/Docker or retained credential, report, volume or backup was
read or changed. Missing Content-Length and dishonest equal-length responses,
producer authenticity, live deduplication and operator acceptance remain open.
Restore only the scoped reply guard/test if needed; preserve saved resources
and inspect ambiguous writes rather than replaying them. Original Vault sync
pending outside checkout.

## 2026-10-06 HTTP connection reset after status (offline)

An isolated response that returns HTTP 200 then raises a socket reset while
reading the body previously escaped as a raw `ConnectionResetError`. The client
now reports a scoped request failure without retrying the uncertain write.
The negative failed before repair; focused 73 AgentHouse tests and full
`make verify-local-experiment` pass (fake Dagster failure is intentional).
Dedicated Colima freshly Stopped; no HTTP/Docker, saved credential/report/volume
or backup accessed or changed. Status and exception handling cannot prove that
ClickHouse committed an insert, authenticate the producer, establish live
deduplication or grant operator acceptance. Inspect an ambiguous write without
replay; roll back only the exception guard/test while retaining saved records.
Original Vault sync pending outside checkout.

## 2026-10-06 partial startup and query acknowledgement (offline)

When both owned Docker resources are absent, first-time startup now refuses
either preexisting private credential or environment file before creating any
new file or Docker resource. Such a file may be left by an interrupted or
failed creation; even a valid-looking credential is not evidence that the
previous attempt completed. Independently, the HTTP client requires status
200 before reading a query reply, refusing other 2xx statuses rather than
claiming an insert/schema request finished. It does not retry uncertain writes.
Focused 72 AgentHouse tests pass. Dedicated Colima freshly Stopped; no saved
credential, report, volume or backup read/change and no live Docker/HTTP
request. Existing container reuse still requires its original credential;
offline response fixtures cannot authenticate ClickHouse or establish live
deduplication, ARM64 behavior, completed writes or operator acceptance.
Partial private files may occupy up to two small files plus overhead; retain
and inspect them before an explicit recovery, not automatic deletion or replay.
Roll back only the two guards/tests if verified live behavior requires it,
preserving all saved files and volume. Original Vault sync pending outside
checkout.

## 2026-10-06 credential short-read refusal (offline)

The private credential reader now requires the number of bytes returned by its
bounded read to match the unchanged inode's recorded size. An isolated short
read that omitted only the final LF previously returned valid but incomplete
JSON; it now refuses with a scoped error. Focused AgentHouse 70 tests and full
`make verify-local-experiment` pass (intentional fake Dagster failure). Dedicated
Colima freshly Stopped; no retained credential, volume, report or backup was
read/changed and no Docker/HTTP request occurred. This does not establish
credential provenance, prevent same-UID substitutions outside the checks or
prove live startup/deduplication or operator acceptance. Restore only this
guard/test on rollback, preserve saved files and never replay unknown inserts.
Original Vault sync pending outside checkout.

## 2026-10-06 credential read identity recheck (offline)

The private credential reader now rechecks its open inode metadata and the
directory entry after reading. Isolated same-size in-place modification and
entry replacement during the read are refused instead of returning ambiguous
authentication bytes (69 focused tests and full local verifier pass). The
dedicated Colima profile was freshly Stopped; no retained credential was read
or changed, no Docker/HTTP or saved report/volume/backup access. This is not
protection against same-UID substitution before open or after the final check,
nor credential provenance or live/operator acceptance. Roll back only the read
guard/test, preserving saved files; do not rotate or replay unknown inserts.
Original Vault synchronization remains pending outside the checkout.

## 2026-10-06 export directory durability (offline)

The private JSONL report publisher now syncs its staging directory entry,
exclusive published link and pending-entry removal as well as report bytes.
Three isolated directory-sync failures previously returned a published path;
they now refuse and leave any partial/private names for inspection rather than
deleting or retrying. Focused AgentHouse 68 tests and full local verifier pass
(fake Dagster worker failure intentional). Dedicated Colima freshly Stopped;
no live query, retained report/volume/backup or credential accessed/changed.
Disk impact on failure is one report-sized pending file and/or published link
plus directory overhead; backup/export remains private and no saved report is
replaced. This is not power-loss atomicity, producer authenticity, live
deduplication or operator acceptance. Revert only scoped sync/test if needed,
retain ambiguous reports and never replay uncertain inserts. Original Vault
sync pending outside checkout.

## 2026-10-06 directory entry sync before first Docker creation (offline)

After syncing each newly created credential/environment file, AgentHouse now
fsyncs its already-open private state directory before proceeding. Synthetic
failure at each of four file/directory sync points refuses startup before
Docker actions and leaves the private 0600 files for inspection, not automatic
retry or deletion. Focused 67 tests and `make verify-local-experiment` pass
(intentional fake Dagster failure). Fresh dedicated Colima state: Stopped; no
live Docker/HTTP, retained credentials, volume, report or backup accessed or
changed. Disk impact remains up to two small private files plus filesystem
overhead on partial first-time startup; neither file/dir sync proves whole-
machine power-loss atomicity or live ClickHouse/deduplication. Inspect partial
files before explicit recovery, preserve unknown inserts; revert only the
directory sync and its tests if incompatible. Original Vault sync pending.

## 2026-10-06 private startup-file sync before Docker creation (offline)

New AgentHouse credential and container environment files now flush and fsync
before startup proceeds. Synthetic sync failure on either file stops before any
Docker image/volume/container action; the already-created private 0600 file
remains for inspection, never automatically deleted or overwritten. Focused
AgentHouse 66 tests and the full local verifier pass (the fake Dagster failure
is intentional). The dedicated Colima profile was freshly Stopped; no live
Docker/HTTP, retained credential, volume, report or backup read/change.
This is file-content sync, **not** directory fsync, power-loss atomicity,
daemon provenance, live startup/deduplication or operator acceptance. A failed
first-time startup may leave up to one private credential and one private env
file plus filesystem overhead; inspect them before explicit recovery, do
not rotate/retry blindly or replay unknown inserts. Roll back only this scoped
write guard/tests while preserving saved files. Original Vault sync pending
outside checkout.

## 2026-10-06 explicit credential input uses saved-file limit (offline)

The AgentHouse Client's explicit credential-byte argument now has the same
16 KiB limit as its private-file read. A synthetic oversized URL-safe password
previously reached Basic auth construction; the focused AgentHouse 64 tests
pass after repair. This protects caller-supplied bytes, not only saved-file
inputs; no secret was read. Dedicated Colima was last freshly observed Stopped;
no live HTTP/Docker, retained credential/report/volume/backup read or change.
This does not authenticate the producer, prove live startup/deduplication or
grant operator acceptance. Restore only the size check/fixture if necessary,
preserving saved resources and avoiding unknown-request replay. Original Vault
sync pending outside checkout.

## 2026-10-06 Docker process failures are not absence (offline)

The fixed AgentHouse Docker wrapper now refuses a timed-out or unlaunchable
subprocess as a scoped runtime error; neither outcome implies the named
container or volume is missing. Six synthetic failures (timeout, missing
binary and permission error for each resource) are covered by 64 focused
tests. Dedicated Colima was freshly Stopped; no daemon, retained credential,
volume, report or backup was read or changed. Subprocess capture still lacks
an OS-level output cap, and live startup, deduplication, daemon identity and
operator acceptance remain unproved. Restore only the wrapper/test on rollback;
preserve saved resources and do not replay uncertain inserts. Original Vault
sync pending outside this checkout.

## 2026-10-06 undecodable Docker inspection is not absence (offline)

Python's Docker subprocess text decoding can fail before returning an exit
status or stderr. The AgentHouse wrapper now reports that failure as a scoped
runtime refusal rather than leaking a decoder exception; no absence or creation
is inferred. Synthetic undecodable container/volume replies are covered by
63 focused tests and `make verify-local-experiment` (intentional fake Dagster
failure). The dedicated Colima profile was freshly Stopped; no live Docker,
credential, report, volume or backup access/change. This does not authenticate
the daemon, prove ARM64/deduplication, bound the pipe at OS level or confer
operator acceptance. Restore only the decoding guard/test if needed, preserving
saved data and never replaying unknown inserts. Original Vault sync pending.

## 2026-10-06 ambiguous Docker inspect failure is not absence (offline)

The startup inventory previously treated **any** failed container/volume
inspect as absent; simultaneous daemon failures could enter first-time
credential and container creation. It now recognizes only exit 1, empty
stdout and an exact Docker `No such container/volume/object` diagnostic for
the requested name. Other failures, partial replies and empty successful
inspections refuse startup. Focused AgentHouse 62 tests and full local verifier
pass (fake Dagster worker failure intentional); private verifier log
`tmp/local-experiment/attempt-191-agenthouse-verifier.log`. Dedicated
Colima freshly observed Stopped, so Docker's actual diagnostic spelling and
live startup compatibility are not established; a different spelling fails
closed and requires operator inspection, not a broadened fallback. No live
Docker call, retained credential/report/volume/backup access or change.
Rollback only the inspect classification if required, preserving saved data
and not replaying unknown inserts. ARM64/deduplication and operator acceptance
remain open; original Vault sync pending outside checkout.

## 2026-10-06 orphaned retained volume refuses recreation (offline)

The startup path previously created a new container and credentials when the
fixed owned volume existed but its container did not. An isolated fixture
reached readiness (and failed after its bounded 90-second wait) before repair.
Now this asymmetric inventory refuses recreation before allocating state,
reading credentials or starting Docker; the converse missing-volume case still
refuses. Focused AgentHouse 61 tests pass. Dedicated Colima was freshly observed
Stopped; no live container, credential, retained volume/report/backup was
accessed or changed. Ownership labels cannot prove that an orphaned volume is
safe to adopt; inspect identity and retained contents separately before any
explicit recovery, never silently recreate or replay an unknown insert.
This fixture does not prove daemon authenticity, live ARM64/deduplication or
operator acceptance. Revert only this guard/test if required, preserving the
retained volume and saved evidence. Original Vault sync pending outside checkout.

## 2026-10-06 bounded Docker inspection input (offline)

The standalone Docker context/owned-resource decoder previously parsed an
arbitrarily large unused inspection field before checking nested shape. An
oversized synthetic context reply reached the next `docker info` read before
repair. Now non-text, unencodable or >2 MiB UTF-8 inspection replies fail
scoped before JSON parsing or runtime access. Focused AgentHouse 60 tests and
`make verify-local-experiment` pass (intentional fake Dagster failure fixture).
Dedicated Colima was observed not running; no Docker, credential, saved
volume/report/backup accessed or changed, usage `null / NOT_MEASURED`.
This does not independently authenticate Docker, bound subprocess capture at
the OS pipe level, prove live startup/deduplication or grant operator acceptance.
Revert only the decoder guard/fixture if needed, retaining saved resources;
never replay an unknown insert. Original Vault sync pending outside checkout.

## 2026-10-06 saved-container stopped-state guard (offline)

An existing owned container with Docker status `running` (or absent/contradictory
running, paused, restarting or dead flags) previously reached `docker start`
and subsequent setup SQL. Fifteen fixture negatives reproduced acceptance before
repair. Startup now requires explicit `created` or `exited` with all four flags
false, before credential or filesystem access. Focused AgentHouse 59 tests and
full local verification passed (the fake Dagster failure is intentional).
Dedicated Colima was freshly observed Stopped; no Docker start, saved credential,
report, volume or backup accessed or changed. This snapshot check cannot
authenticate Docker or eliminate inspection-to-start races; saved-container
compatibility, live ARM64, deduplication and operator acceptance remain open.
Inspect live identity/state before future startup; if incompatible, revert only
this guard/test, preserve saved records and never replay unknown inserts.
Original Vault synchronization pending outside the checkout.

## 2026-10-06 context identity and unattended restart boundary (offline)

Two independent startup fixtures exposed gaps: a Docker context inspection
reporting another (or missing) context name with the expected socket reached
`docker info`, and an existing container with `always`, `unless-stopped`, or
`on-failure` restart policy reached `docker start`. Context inspection now
requires the fixed requested name as well as the socket. Saved-container
startup requires Docker's explicit `no` restart policy with integer zero retry
count; missing/ambiguous fields fail before credential read or start. Focused
AgentHouse 58 tests and `make verify-local-experiment` pass; the verifier's fake
Dagster failure is intentional. Dedicated Colima was observed Stopped, so
actual saved-container compatibility, daemon identity, ARM64 execution, live
deduplication and operator acceptance remain unverified. No daemon, retained
credential/report/volume/backup accessed or changed; usage stays `null /
NOT_MEASURED`. Before future live startup, inspect saved context/container
identity and policy without altering retained resources. Revert only these
two guards/fixtures if incompatible, preserving saved records; never replay
unknown inserts. Original Vault sync pending outside checkout.

## 2026-10-06 exact architecture diagnostic (offline)

The dedicated-context check previously stripped a Docker `info` architecture
reply and accepted leading padding, double LF, CRLF and TAB as ARM64. Four
negative replies failed before repair; a single optional LF now passes.
Focused AgentHouse 56 tests pass. Dedicated Colima remained Stopped; no Docker
daemon, credential, retained data or backup was accessed. This is reply syntax,
not daemon/host provenance or live ARM64 compatibility proof. Revert only the
diagnostic comparison/test, preserve saved resources; original Vault sync
pending outside checkout. Operator acceptance remains open.

## 2026-10-06 existing-container IPC/UTS isolation (offline)

The saved-container startup check previously accepted host or foreign-container
IPC and UTS namespaces despite pinned image, port and owned volume. Eight
negative fixture variants (including absent fields) reached startup before
repair. Existing startup now requires private/default IPC and default UTS;
the default empty IPC spelling remains accepted. Focused AgentHouse 55 tests
and `make verify-local-experiment` pass (fake Dagster failure intentional).
Dedicated Colima observed Stopped; no Docker start, retained credential,
volume, report or backup accessed or changed. Docker replies are not independently
authenticated, and saved-container compatibility, live deduplication and operator
acceptance remain unproved. Revert only the scoped isolation guard/test after
inspecting saved runtime identity; do not delete resources or replay unknown
inserts. Original Vault sync pending outside checkout.

## 2026-10-06 fixed Docker inspect name correlation (offline)

The owned-label inspection accepted a Docker reply for another named container
or volume (or no name) as the fixed AgentHouse resource. Six negative fixtures
now fail; a matching container `/name` or volume `name` still passes. Focused
AgentHouse 54 tests and `make verify-local-experiment` pass. An initial
`make test-local-agenthouse` failed because that target does not exist; the
direct unittest discovery succeeded. Dedicated Colima observed Stopped; no
Docker startup, live query, saved credential, report, volume or backup accessed
or changed. This checks reply consistency, not daemon provenance, live
deduplication or operator acceptance. Revert only the scoped identity check
and test, preserve retained resources, and never replay an uncertain insert.
Original Vault sync pending outside checkout.

## 2026-10-06 exact startup readiness reply (offline)

Existing-container startup used `strip()` on `SELECT 1` and accepted five
noncanonical fixture replies (leading space, extra LF, CRLF, TAB, NUL) as
readiness before running setup SQL. It now accepts only `1` or ClickHouse's
plain-text `1\n`; otherwise its bounded readiness loop fails without running
schema setup. Focused AgentHouse 53 tests and full local verifier pass (fake
Dagster failure intentional). Dedicated Colima was Stopped; no actual Docker
start/SQL, credential read or retained report/volume/backup change. This
does not authenticate the reply producer or prove live readiness, deduplication
or operator acceptance. Revert only this scoped comparison/test; retain saved
resources and never replay an ambiguous insert. Original Vault sync pending.

## 2026-10-06 count reply underflow (offline)

An unused count-reply float such as `1e-999` or `-1e-999` previously
rounded silently to zero and allowed the smoke path to reach export. Two
negative fixtures now refuse nonzero mantissas rounded to zero, consistent
with the export reader. Focused AgentHouse 52 tests and the full local
verifier pass (its fake Dagster failure is intentional). Dedicated Colima
freshly Stopped; no live insert, saved credential/report/volume/backup access
or change. This does not authenticate ClickHouse or prove live deduplication
or operator acceptance. Revert only this number guard/test, preserve saved
evidence, and do not replay ambiguous inserts. Original Vault sync pending.

## 2026-10-06 count reply nested values (offline)

Smoke previously accepted a valid count alongside an ignored escaped lone
surrogate or a >64-level nested field, then proceeded to export. Two negative
fixtures exposed the gap; smoke now bounds nesting and checks decoded strings
throughout the count reply before export. Focused AgentHouse 52 tests and full
local verifier pass (fake Dagster failure intentional). Dedicated Colima freshly
Stopped; no live insert, saved credential/report/volume/backup access or change.
This does not authenticate replies, prove live retry deduplication or grant
operator acceptance. Preserve saved evidence and never replay an ambiguous
insert; restore only the scoped guard/test. Original Vault sync pending.

## 2026-10-06 status version reply boundary (offline)

The standalone `status` path previously published an unbounded `version()`
reply after stripping whitespace, unlike `smoke`. Both paths now use the same
bounded numeric diagnostic; four invalid offline replies fail and a valid LF
reply succeeds. Focused AgentHouse 51 tests and the local verifier pass. The
dedicated Colima profile was freshly Stopped; no live request, insert, retained
report, credential, volume or backup accessed or changed. This diagnostic is
not ClickHouse identity or deduplication proof. Revert only this shared guard
and status test; preserve saved evidence and do not replay unknown inserts.
Original Vault sync pending outside checkout; operator acceptance remains open.

## 2026-10-06 smoke version diagnostic output boundary (offline)

After matching count/export readback, the smoke path previously returned an
unbounded raw `version()` reply. It now accepts only a bounded dotted numeric
version with at most one trailing LF; control text, extra lines, oversized and
non-text replies fail closed without a public result. Four negative fixtures
and the valid reply are covered; focused AgentHouse 50 tests and full local
verifier pass (fake Dagster failure intentional). Dedicated Colima Stopped;
no live insert, retained report/credential/volume/backup access or change.
The version reply is diagnostic, not proof of server identity or deduplication.
On ambiguous smoke outcomes inspect saved evidence without replay; roll back
only this reply guard/test and preserve reports. Original Vault sync pending
outside checkout; no operator acceptance.

## 2026-10-06 smoke matching-row identity correlation (offline)

The saved-export smoke readback used to accept a successful row with the same
event ID but a changed timestamp, agent/task, action or target. It now checks
those five stable identity fields against the event sent on both intentional
inserts; five isolated corrupt-row fixtures fail without a hidden third insert.
Focused AgentHouse 49 tests and `make verify-local-experiment` pass (the fake
Dagster worker failure is an intentional negative). Dedicated Colima was
freshly Stopped. No live insert, retained report, credential, volume or backup
access/change. This correlates caller-supplied replies, not producer
authenticity or live deduplication. On ambiguous insert evidence, inspect the
saved record without replay; roll back only the scoped check/test and preserve
reports. Original Vault sync pending outside checkout; operator acceptance
remains unproved.

## 2026-10-06 smoke export nested-value boundary (offline)

Private smoke readback previously accepted a valid matching row alongside an
unrelated JSON row with an escaped lone surrogate in a key/value or more than
64 nested levels, although the exporter refuses those forms. Three synthetic
negative rows reproduced the gap before repair; readback now bounds depth and
requires UTF-8-encodable strings throughout every row. AgentHouse focused 48
tests and the full local verifier pass (intentional fake Dagster failure).
Dedicated Colima was freshly Stopped; no live insert, retained export, volume,
credential or backup touched. The reader cannot authenticate its producer or
prevent same-UID substitution, and this does not prove live deduplication or
operator acceptance. Inspect ambiguous saved evidence without replay; revert
only the scoped reader/test, keeping saved records. Original Vault sync pending
outside checkout.

## 2026-10-06 smoke export readback framing and ambiguity (offline)

The private smoke readback previously used permissive JSON decoding after
opening its bounded report: a matching successful event could mask a malformed
other row, duplicate keys, nonstandard/overflowing numbers, CRLF or a missing
final LF. Nine isolated corrupted suffixes exposed the gap before repair.
Readback now requires LF-framed JSON objects and bounded finite unambiguous
numbers and keys throughout the report. Focused AgentHouse 47 tests and the
full local verifier pass (intentional fake Dagster failure). Dedicated Colima
was freshly Stopped; no live insertion, saved report, credential, volume or
backup accessed or changed. This is not report producer authentication or
same-UID substitution protection, nor does it prove live retry deduplication.
Inspect an ambiguous saved report without replaying inserts; revert only the
readback guard/test, preserving all reports. Original Vault sync pending
outside checkout; no operator acceptance.

## 2026-10-06 private smoke report readback (offline)

Smoke no longer reopens its just-published export through an unrestricted
pathname. It requires the fixed report name under the private state directory,
opens the entry no-follow relative to that directory, bounds it to 8 MiB and
checks owner, mode, link count and inode stability before assessing the saved
row. Symlink, public-mode, oversized and out-of-scope fixtures fail closed;
focused 46 tests and the full local verifier pass (fake Dagster failure is an
intentional test). The dedicated Colima profile was freshly Stopped. No live
inserts, saved export, volume or backup were accessed or changed. A same-UID
writer can still substitute content before open; this does not authenticate
ClickHouse or prove live retry deduplication. On ambiguous smoke failure inspect
the retained report, never replay an unknown insert. Restore only this readback
guard/test, preserving saved resources. Original Vault sync pending outside
checkout; no operator acceptance.

## 2026-10-06 independent smoke count and export consistency (offline)

The two deliberate same-ID smoke inserts previously passed on a JSON `true`
count (Python `True == 1`) or duplicate count keys, and an exported row with
the matching ID but failed status or a second matching row still passed. Six
isolated negative variants exposed both gaps before repair; count JSON is now
bounded and rejects duplicate keys/nonstandard or oversized numbers, and smoke
requires one successful exported match. Focused AgentHouse 45 tests and the full
local verifier pass (the fake Dagster worker failure is intentional). Dedicated
Colima freshly Stopped; no live insert, unknown-request replay, retained report,
volume, credential or backup mutation. The query/export producers are not
authenticated; export path substitution and real retry deduplication remain
unproved. Inspect saved evidence before any live retry. Restore only the scoped
smoke checks/tests; preserve reports and pending files. Original Vault sync
pending outside checkout; no operator acceptance is claimed.

## 2026-10-06 smoke readback failure boundary (offline)

After its two intentional same-ID inserts, `smoke` previously raised raw
`KeyError` for a missing count `data` or an export row without `event_id`.
Three isolated malformed-reply cases now produce scoped AgentHouse failures;
the fixture confirms exactly two inserts per call and no hidden replay.
Focused AgentHouse 44 tests and the full local verifier pass (intentional fake
Dagster failure). Dedicated Colima freshly Stopped; no live insert, unknown
request replay, saved credential, report, volume or backup access or mutation.
The response still does not authenticate its producer or prove live retry
deduplication/operator acceptance. Following a failed live smoke, inspect saved
evidence and do not replay the request. Revert only scoped guard/test, retaining
saved data. Original Vault sync pending outside checkout.

## 2026-10-06 export object framing (offline)

The exporter previously saved a JSON object with leading or trailing ASCII
whitespace as a JSONEachRow line. Three synthetic replies published before
repair; rows now require an object starting at column one and ending at the
last column before LF. Escaped whitespace inside JSON strings remains valid.
Focused 43 tests and `make verify-local-experiment` pass (intentional fake
Dagster worker failure). Dedicated Colima freshly Stopped; no live query,
retained report, credential, volume or backup changed. This does not prove
export field schema or producer authenticity and is not operator acceptance.
Revert only the scoped parser/test, preserving saved reports and pending files;
original Vault sync pending outside checkout.

## 2026-10-06 bounded Docker inspection structure (offline)

Context and owned-volume JSON inspection now refuse metadata nested beyond 64
levels and escaped lone-surrogate text in keys or values before lending a
socket or ownership label to runtime actions. Four negative fixtures crossed
the boundary before repair; focused 43 AgentHouse tests and the local verifier
pass. Colima freshly Stopped; no live daemon/start, retained volume, credential,
report or backup touched. This does not authenticate the Docker producer or
establish saved-container compatibility. Revert only the decoder/test, preserving
saved resources; original Vault sync pending outside checkout.

## 2026-10-06 credential encoding ambiguity (offline)

Python's JSON byte decoder accepted UTF-16, UTF-32 and UTF-8 BOM credential
files although this component generates plain UTF-8 JSON. Three negative
fixtures reached client setup before repair; only BOM-free UTF-8 is now
decoded before the existing duplicate-key and token checks. Focused AgentHouse
42 tests and the full local verifier pass (the fake Dagster failure is an
intentional fixture). Dedicated Colima freshly Stopped; no live credential read,
container start, HTTP, retained report/volume or backup change. Existing
non-UTF-8 retained credentials would require non-destructive inspection before
startup; do not rotate automatically. This does not authenticate the credential
producer or grant operator acceptance. Revert only this decoder/test if needed,
preserving saved files. Original Vault sync pending outside checkout.

## 2026-10-06 export CRLF row refusal (offline)

JSONEachRow export accepted a CRLF-terminated object because JSON decoding
ignored the trailing CR; the private report was published. A negative fixture
failed before repair; literal CR now fails before any file creation, while
canonical LF remains accepted. Focused AgentHouse 41 tests pass. Dedicated
Colima freshly Stopped; no live query or retained report, volume or backup
mutation. This does not authenticate the producer or grant operator acceptance.
Restore only the scoped export guard/test, preserving saved records; original
Vault sync pending outside checkout.

## 2026-10-06 saved-container security and inspection shape (offline)

Two independent startup gaps: a container with matching image/port/volume could
lose `no-new-privileges`, gain capabilities, devices or host binds; malformed
saved Config/HostConfig/Mounts raised raw Python exceptions. Isolated negatives
reproduced ten unauthorized starts and six raw exceptions before repair. Existing
containers now require the pinned security option and no extra host access;
malformed inspection fails with a scoped error before private state creation.
The canonical fixture still starts through the fake Docker client. Focused 41
AgentHouse tests and `make verify-local-experiment` pass (the fake Dagster
worker failure is an intentional negative fixture). Dedicated Colima freshly
Stopped; no live Docker start, credential, volume, backup or report mutation.
Docker replies and daemon identity are not independently authenticated; this
does not prove saved-container compatibility or operator acceptance. For
recovery, inspect the actual retained container before a live start; revert
only these guards/tests if needed, never delete saved resources. Original Vault
sync pending outside checkout.

## 2026-10-06 existing container isolation before startup (offline)

An existing owned container with the expected image, port binding, volume and
resource limits could still have host networking, host PID namespace, privileged
execution or publish-all enabled. Startup now requires the Docker default/bridge
network, an explicit unprivileged state, an empty PID mode and publish-all false;
missing fields fail closed. Isolated negative fixtures refuse startup before
credential/client use and Docker start; the ordinary existing fixture still
passes. Focused AgentHouse 39 tests, the full local verifier and design-context
pass (the verifier's fake Dagster worker error is an intentional negative).
Dedicated Colima freshly Stopped; no
live inspect/start, retained data, volume, credential or backup change. Docker
inspection is not independently authenticated; inspect the actual saved
container before any future live startup. Restore only the scoped startup guard
and tests if needed, never remove retained resources. Original Vault sync
pending outside checkout; this is not operator acceptance.

## 2026-10-06 credential token and Pawprint control boundary (offline)

Direct client initialization accepts only the generated single-field, URL-safe
password credential; unrelated fields and ambiguous env/Basic-auth characters
fail before HTTP. Independently Pawprint string fields reject C0/C1 controls
and DEL before insert HTTP, not just CR/LF. Offline negative fixtures and the
focused suite (38 tests) and `make verify-local-experiment` pass; its fake
Dagster worker failure is an intentional negative fixture. The dedicated
Colima profile was freshly
Stopped; no retained credential, report, volume or backup was changed. Existing
non-token credentials would need explicit, non-destructive inspection before a
new start; do not rotate them automatically. Neither fixture verifies the
producer, live integration or operator acceptance. Roll back only these scoped
input guards/tests while preserving saved records. Original Vault sync pending
outside checkout.

## 2026-10-06 Float64 metric integer boundary (offline)

Pawprint `event` and `insert` now refuse integer cost/CPU/memory measurements
above 2^53: larger integers can silently round in Float64, while very large
integers previously raised an unscoped `OverflowError` in `isfinite`. Exact
boundary integers and finite floats remain accepted. A synthetic negative
reproduced both paths; AgentHouse focused 36 tests and the local verifier pass.
Dedicated Colima was freshly Stopped, so no live insertion, retained data,
volume or backup changed. This is input validation, not producer authentication
or live/operator acceptance. Roll back only the metric guard/test; preserve
saved records. Original Vault sync pending outside checkout.

## 2026-10-06 bounded Docker numeric replies and credential ambiguity (offline)

Context and owned-volume inspection now refuse nonrepresentable floating-point
exponents and overlong integer/float tokens even in unused metadata. Direct
client authentication independently refuses repeated credential JSON keys,
including nested duplicates, and nonstandard numbers. Synthetic negatives and
ordinary values pass the focused 35 tests; the full local verifier passes (its
fake Dagster worker failure is an intentional negative fixture). Dedicated
Colima freshly Stopped; no live Docker or HTTP call, retained credential,
report, volume or backup changed. Parsing cannot authenticate the daemon or
credential producer, and neither live behavior nor operator acceptance was
reverified. Restore only these scoped decoders/tests, preserving saved files;
original Vault sync remains pending outside this checkout.

## 2026-10-06 Docker nonstandard number refusal (offline)

Context and owned-volume inspection reject `NaN`, `Infinity` and `-Infinity`
even in unused metadata before authorizing Docker info or owned-resource use.
Synthetic negatives passed before repair. Focused AgentHouse 33 tests and full
`make verify-local-experiment` pass. Dedicated Colima freshly Stopped; no live
Docker call, credential, retained volume, report or backup change. JSON syntax
checks do not authenticate the daemon or establish operator acceptance. Revert
only the scoped decoder/fixtures and preserve saved resources and reports.
Original Vault sync pending outside checkout.

## 2026-10-06 bounded export numeric tokens (offline)

JSONEachRow export refuses integer tokens over 128 digits and decimal tokens
over 128 characters before publishing a report. Synthetic 129-digit integer,
long zero fraction and exponent published before repair; afterward focused
AgentHouse 32 tests and the full local verifier pass. Dedicated Colima freshly
Stopped; no live export or retained report, volume or backup touched. This
does not authenticate the producer or prove operator acceptance. Revert only
the scoped parser/fixtures; retain saved reports and pending files. Original
Vault sync pending outside checkout.

## 2026-10-06 ambiguous Docker inspection fields (offline)

Context and owned container/volume inspection now reject duplicate JSON keys
at any depth, including a foreign label or socket overwritten by a canonical
last value. Synthetic negatives crossed the ownership/endpoint boundary before
repair; focused AgentHouse 32 tests and full local verifier pass. Dedicated
Colima was freshly Stopped. No live startup, credential, retained report,
volume or backup changed. This does not authenticate the Docker daemon or
prove operator acceptance. Restore only the scoped decoder/tests, preserving
saved resources; original Vault sync pending outside checkout.

## 2026-10-06 offline JSONEachRow LF framing

Export now splits records only at LF, not at Python `splitlines()` Unicode
separators. Synthetic U+2028 and U+0085 separators between otherwise valid
objects published a report before repair; they are refused before publication
afterward. Focused 30 tests and the full local verifier pass. Dedicated Colima
was freshly Stopped; no live export, retained report, volume or backup changed.
This only checks framing, not Pawprint schema completeness, producer identity
or operator acceptance. Revert only this parser/test while retaining saved
reports and pending files. Original Vault sync pending outside checkout.

## 2026-10-06 offline decoded export text

JSONEachRow export now rejects escaped lone surrogates in decoded object keys
or nested string values before publishing a report. The negative fixtures
published a file before the guard; focused 30 AgentHouse tests and
`make verify-local-experiment` pass after repair. Dedicated Colima was freshly
Stopped; no live export, retained data or backup change. This guards text
representation only, not producer authenticity or operator acceptance. Roll
back only the parser guard/fixtures, preserving saved reports and pending
files. Original Vault sync pending outside checkout.

## 2026-10-06 offline export nesting limit

An 8 MiB-bounded JSONEachRow response could still contain 65 nested levels.
Export now rejects rows deeper than 64 and catches JSON parser recursion failure
before creating any report; a shallower fixture remains accepted. Focused 30
AgentHouse tests and `make verify-local-experiment` pass. Dedicated Colima was
observed stopped; no live export or retained data/backup change. This is not
producer authentication or operator acceptance. Revert only the scoped guard
and fixtures; preserve reports and pending files. Original Vault sync pending.

## 2026-10-06 offline export publication on disk failure

The bounded JSONEachRow export now writes a private 0600 `.pending` inode,
flushes and fsyncs it, and publishes by exclusive hard link only after the
write succeeds. A simulated fsync failure leaves no `.jsonl` report and retains
the incomplete pending file for inspection; an existing report or symlink is
never replaced. Focused AgentHouse 30 tests and `make verify-local-experiment`
pass (the fake Dagster worker failure is an intentional negative fixture).
A failed publish after staging also retains the pending file;
each may consume up to the 8 MiB response cap plus filesystem overhead. There
is no automatic cleanup of ambiguous data. This does not guarantee directory
durability across power loss, prevent same-UID state substitution, or prove
live export integrity. No retained report/volume/backup was changed; Colima
was freshly Stopped. Revert only this writer/tests, retaining pending and saved
reports for inspection. Original Vault sync pending outside checkout; this is
not operator acceptance.

## 2026-10-06 offline extreme export exponent guard

An export containing a finite-looking decimal exponent that decodes to
infinity or rounds a nonzero value to zero is refused before private report
creation. Synthetic overflow and underflow cases failed before repair; focused
AgentHouse 28 tests, local verifier and design-context pass. Dedicated Colima
was freshly Stopped. No live export, retained report, credential, volume or
backup changed. This does not authenticate producers or validate all Pawprint
rows. Restore only the scoped parser/fixtures without deleting saved reports;
original Vault sync pending outside checkout; no operator acceptance.

## 2026-10-06 offline export nonstandard-number guard

The JSONEachRow export parser now rejects `NaN` and signed infinity tokens,
including nested fields, instead of saving nonstandard JSON as a private
report. Focused AgentHouse 28 tests pass. No live export or retained
credential/report/volume/backup changed; the dedicated Colima profile was
freshly Stopped. This does not authenticate producers or validate the full
Pawprint schema. Restore only this number guard and its negative fixtures;
preserve saved reports. Original Vault sync pending outside checkout; no
operator acceptance.

## 2026-10-06 offline export duplicate-field guard

The bounded JSONEachRow export now refuses repeated keys within any object
before creating a private report; a repeated event identity is no longer
silently collapsed. AgentHouse focused 28 tests and the local verifier passed.
The dedicated Colima profile was freshly Stopped: no live export, retained
credential/report/volume or backup was modified. This check does not authenticate
the producer, prove schema completeness or make disk writes atomic. Restore
only the parser/test if necessary; retain saved records and inspect any failed
export before recovery. Original Vault sync pending outside checkout; no
operator acceptance is claimed.

## 2026-10-06 offline existing-volume retention and Docker reply boundaries

An existing owned container now refuses startup when its named volume is absent,
before creating state files or allowing Docker to recreate a volume under the
same name. A new container may still create its own volume. Separately malformed
context and container/volume inspection replies fail closed rather than being
mistaken for absence or escaping as raw indexing/type errors. Isolated negative
fixtures cover absent volume, invalid JSON, wrong cardinality and nested shapes;
focused tests pass (28). The dedicated Colima profile was freshly Stopped; no
live daemon, credential, volume, retained report or backup was changed. Docker
reply checks do not authenticate the daemon or prove live volume contents. Keep
the existing volume and container for manual identity investigation if either
inspect fails; never recreate or delete retained data as recovery. Revert only
the scoped guards/tests if necessary. Original Vault sync remains pending
outside checkout; no operator acceptance is claimed.

## 2026-10-06 offline dedicated Docker context path check

Docker context inspection now requires the exact current-home Colima profile
socket URI, not any Unix socket with the same directory suffix. Synthetic
alternate-root contexts fail before Docker info or startup; the exact candidate
reaches the ARM64 check. Focused AgentHouse tests pass. The live Docker context
metadata was unavailable (`context not found`) while the dedicated profile was
stopped, so actual integration and socket inode/provenance were not verified.
No profile, container, credential, retained volume or backup was changed.
Verifier outcomes are in Milestones. Restore only the context check if needed;
do not start a different context or delete retained data. Original Vault sync
pending outside checkout; no live/operator acceptance is claimed.

## 2026-10-06 offline event identity and export-shape guards

Externally supplied Pawprint IDs must now use the canonical lowercase hyphenated
UUID spelling and timestamps must use the exact millisecond UTC-shaped spelling
emitted by `event()`. Noncanonical spellings no longer reach insert, avoiding
multiple textual identities for the same parsed value. Export checks the bounded
reply as newline-terminated JSON object rows (or empty for zero rows) before
creating a private report. Invalid UTF-8, malformed/truncated/non-object and
oversized fixture replies leave no report. Focused AgentHouse tests (25) pass;
local verifier and design-context results are recorded in the matching Milestones
entry. Dedicated Colima was stopped; no live query, retained report, volume or
backup was touched. This is structural validation, not producer authenticity,
schema completeness, atomicity on disk failure or operator acceptance. Restore
only these guards if needed, without deleting existing reports. Original Vault
sync remains pending outside checkout.

## 2026-10-06 offline new-container files and startup ordering

New-container startup now creates the credential and `container.env` exclusively
at creation-time mode 0600 relative to the checked private state directory.
An existing entry, including a dangling credential link or env symlink, is
refused instead of overwritten or followed. The credential is validated before
use in the env file; newline, carriage return and NUL cannot inject container
environment lines. Independently, an existing container with an unsafe
credential is refused before Docker start, after its runtime configuration is
validated. The two overwrite fixtures and unsafe-start fixture failed before
repair; focused tests pass (24), as do the local verifier and design-context
(the verifier's fake Dagster worker failure is an expected negative test).
No live container, retained credential, env,
volume or backup changed; the dedicated Colima profile was freshly stopped.
These checks do not authenticate Docker, resolve same-UID path substitutions,
make partial writes atomic or verify a live new-container create. A failed
creation can leave a newly created private file; inspect its identity rather
than deleting or rotating it automatically. Roll back only the startup and
reader changes while preserving saved files. Original Vault sync pending
outside the checkout; no operator acceptance follows.

## 2026-10-06 offline existing-container startup retention

An existing container no longer reads the credential or rewrites `container.env`
before validation or start: its Docker environment is already captured at
creation. The negative resource-limit fixtures showed the previous premature
write; valid and invalid existing-container cases now leave that file absent
in isolated tests. New-container creation retains its scoped env-file path.
Focused AgentHouse/dashboard tests pass (25); no live startup or saved file
was changed, and the dedicated Colima profile was stopped. The post-change
local verifier, design-context and diff check pass; its fake Dagster worker
failure diagnostic is intentional. This does not
secure the new-container credential/env creation path or prove runtime identity.
Restore only this branching change if needed, keeping existing credentials,
env files and volumes. Original Vault sync remains pending outside checkout.

## 2026-10-06 offline input and export encoding follow-up

Non-string SQL now fails before HTTP; Pawprint fields with lone Unicode
surrogates fail before insert even when JSON escaping would conceal them.
Separately, export pre-encodes the query reply before exclusively creating a
report, so an unencodable reply cannot leave an empty report. These independent
negative fixtures failed before repair; focused AgentHouse/dashboard tests pass
(25), as do the post-change local verifier, design-context, secret scan and diff
check. The verifier's fake Dagster worker error is an intentional negative
diagnostic. No live runtime query, credential read, retained report/volume/backup
change, or producer authentication was performed; the dedicated Colima profile
was freshly stopped. Partial writes from disk errors and same-UID state changes
remain unresolved. Restore only these guards if needed, preserving saved
reports. Original Vault sync remains pending outside checkout; no operator
acceptance is inferred.

## 2026-10-06 offline client input and event validation

The direct client now reports malformed Unicode credential passwords and SQL
as scoped failures, with invalid SQL rejected before HTTP. Independently,
malformed Pawprint UUIDs and timestamps fail as AgentHouse errors rather than
escaping raw parser exceptions; insertion rejects them before querying. The
negative fixtures failed before repair; focused 15 tests and the local verifier
passed afterward (the verifier's fake Dagster worker failure is intentional).
The dedicated Colima profile was freshly Stopped. No live query, retained
credential/report/volume/backup change or runtime authentication occurred.
This does not prove live service behavior or operator acceptance. Restore only
these guards/tests if required, retaining all saved evidence. Original Vault
sync remains pending outside checkout.

## 2026-10-06 offline state binding and export retention

The direct credential reader now opens the final state directory with no-follow,
requires its current owner and 0700 mode, and opens the credential relative to
that descriptor. An independently tested export path uses exclusive no-follow
creation relative to the same private directory and creates 0600 files without
overwriting a retained report or following a report symlink. Invalid UTF-8
query replies now return a scoped error rather than an uncaught decoding error.
Three new negative fixture classes failed before repair; focused AgentHouse and
dashboard tests (20), local verifier, design-context and diff check passed.
The dedicated Colima profile was freshly Stopped. No live HTTP/volume, saved
report, credential or backup was read or mutated. A parent-path substitution,
same-UID state mutation, partial export on write failure and daemon provenance
are not solved by these checks. Existing reports must be preserved; roll back
only the reader/export/response changes if needed. Original Vault sync remains
pending outside checkout; this does not establish operator acceptance.

## 2026-10-06 offline direct-client credential boundary

The direct AgentHouse client now refuses an oversized, symlinked, non-private
or non-regular credential before forming its authorization header. The
independent FinOps collector may pass its own pinned credential bytes instead
of reopening the path. Synthetic private-file, mode, size and symlink cases
passed in the focused AgentHouse tests; the full local verifier passed. An
initial attempt to use a nonexistent `make test-local-agenthouse` target did
not run tests; the focused unittest command was used instead. The dedicated
Colima profile was freshly Stopped. No retained credential contents, live
Docker/query, backup or volume were accessed or changed. This bounds a local
file read, not same-UID pre-open mutation, runtime authentication or operator
acceptance. Restore only the client read if necessary while retaining saved
credentials/data; original Vault sync pending outside checkout.

| Field | Value |
|---|---|
| WP ID | WP-L2-CH-001 |
| Level | L2 |
| Workstream | Observability |
| Depends-on | WP-L0-OBS-001 |

## Scope

Deploy a minimal single-instance ClickHouse for Pawprint and AgentHouse telemetry.

## Acceptance Criteria

- ClickHouse runs native arm64.
- Resource limits are set.
- Pawprint schema exists.
- Sample agent event can be inserted and queried.
- Backup/export path is documented.

## Notes

This resolves the previous dependency bug where L2 Pawprint telemetry depended on L3 ClickHouse.

## Definition of Done

- Work is represented as a GitHub Issue linked to `WP-L2-CH-001`.
- Changes are committed through PR.
- Required CI and policy checks pass.
- Documentation and WBS are updated.
- Any deviation from ARM64-native execution is recorded in [[10_Research/106_ARM64_Container_Image_Compatibility]].

## 2026-09-16 local functional evidence

ClickHouse 26.8.5.13 native ARM64を2 CPU / 4 GiB、loopback限定で導入した。Pawprint schema、insert/query/export、event IDによる論理的重複排除、再起動後の保持を確認した。9 focused tests成功。未知のusage/costはNULL。

[ADR-0034](../../07_ADR/ADR-0034_Use_autonomous_local_experimental_development.md)に基づくlocal functional検証。旧PR/CI/Flux公開gateの履歴は保持し、GitHub完了や本番受入へ読み替えない。実行手順・証跡・復元・未達事項は[Local Experiment Milestones](../061_Local_Experiment_Milestones.md)を参照。
