---
project: Shirokuma
doc_id: "WP-L2-CH-001"
title: "ClickHouse minimal AgentHouse"
status: local-verified
created: 2026-07-05
updated: 2026-10-06
version: "0.11"
area: "workpackage"
tags: [shirokuma, wp, l2, observability]
---

# ClickHouse minimal AgentHouse

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
