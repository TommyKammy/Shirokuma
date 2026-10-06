---
project: Shirokuma
doc_id: "WP-L2-CH-001"
title: "ClickHouse minimal AgentHouse"
status: local-verified
created: 2026-07-05
updated: 2026-09-16
version: "0.3"
area: "workpackage"
tags: [shirokuma, wp, l2, observability]
---

# ClickHouse minimal AgentHouse

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
