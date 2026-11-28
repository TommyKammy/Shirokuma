---
project: Shirokuma
doc_id: "ADR-0040"
title: "Retire Non-Database Runtime Mode"
status: accepted
created: 2026-11-28
updated: 2026-11-28
version: "0.1"
area: adr
tags: [shirokuma, adr, runtime-profile, legacy, retirement, workbench]
---

# ADR-0040: Retire Non-Database Runtime Mode

## Context

Shirokuma previously had two execution modes. Database mode required a database
runtime profile, activated by `SHIROKUMA_RUNTIME_PROFILE` or
`var/shirokuma/runtime-profile.json`; profile version 1 has `mode: database` and
version 2 has `mode: logical_v1`. Non-database (legacy) mode ran without a
profile and used the shared Iceberg lake and a single Trino directly.

Legacy mode owned the Workbench server's old `GET_ROUTES` / `POST_ROUTES`
dispatch tables, twelve legacy backend modules (`stages_resident`,
`marts_resident`, `jobs_resident`, `alerts_resident`, `schedule_resident`,
`stage_exports`, `lake_ingest`, `lake_pipeline`, `lake_seed`, `bears_resident`,
`sqlserver_sync`, `wap_resident`), the Workbench UI `databaseMode === false`
branches, the profile-less `warehouse_resident` paths, and legacy-only CLI and
Make targets (`shirokuma bear`, `bears-smoke`, `bear-*`, `lake-build`,
`lake-test`, `lake-seed`, `lake-status`).

The old Shirokuma cluster environment was removed on 2026-11-23, so no
Shirokuma environment remained to run legacy mode. Database mode still borrowed
helpers from legacy modules (`jobs_resident`, `schedule_resident`,
`lake_ingest`, `bears_resident`, `wap_resident`), so the modules could not
simply be deleted first. No ADR or design document recorded a decision to
retire legacy mode. The implementation merged as PRs #187-#193 between
2026-11-26 and 2026-11-28; this ADR records the decision after the
implementation.

## Decision

Require a database runtime profile at every entry point. No entry point falls
back to legacy mode.

- `workbench_server.py`: the GET and POST fallback returns HTTP 503 with code
  `RUNTIME_PROFILE_REQUIRED` and message `Database runtime profile required`
  when no profile is active. `main()` prints `Shirokuma Workbench requires a
  database runtime profile (SHIROKUMA_RUNTIME_PROFILE or
  var/shirokuma/runtime-profile.json)` to stderr and exits with status 2 before
  binding a port.
- `workbench/index.html`: `initializeSession` throws `Database runtime profile
  required` when the session's `data_mode` is not `database`.
- `shirokuma lake` (`dataops_client.cli`): without a profile it prints
  `shirokuma lake requires a database runtime profile: set
  SHIROKUMA_RUNTIME_PROFILE or provide var/shirokuma/runtime-profile.json` to
  stderr and returns 1.
- `shirokuma warehouse` (`warehouse_resident.main`): without a profile it prints
  `shirokuma warehouse requires a database runtime profile: set
  SHIROKUMA_RUNTIME_PROFILE or provide var/shirokuma/runtime-profile.json` to
  stderr and returns 1. `serve_endpoint` and `query` raise
  `ValueError('Database runtime profile required')`.

The retirement was delivered in five phases:

1. **#187 (phase 1) - move shared helpers.** Move the helpers database mode
   used out of legacy modules: `job_support.py` (SQL rendering and job lock),
   `schedule_calendar.py` (schedule validation and next-run calculation),
   `ingest_types.py` (CSV type inference), `ai_config.py` (AI settings and
   completion), and `state_files.py` (`state_path` / `persist_json`). The move
   has no behavior change; the legacy modules re-import the moved names.
2. **#188 (phase 2) - remove the old server dispatch.** Remove `GET_ROUTES` /
   `POST_ROUTES`, their handlers and helpers, and the legacy startup branch
   (auto-chill, persistent Trino tunnel, jobs scheduler) from
   `workbench_server.py`.
3. **#191 (phase 3) - delete the legacy modules.** Before phase 3, #189 gave
   `db_schedules` its own scheduler loop (`start_scheduler` / `stop_scheduler`)
   and #190 made `db_jobs` use its own `_lost` instead of
   `jobs_resident._lost`. Delete the twelve modules listed above. Remove the
   `shirokuma bear` command and the `bears-smoke`, `bear-*`, `lake-build`,
   `lake-test`, `lake-seed`, and `lake-status` Make targets. `shirokuma lake`
   runs `dataops_client.py`.
4. **#192 (phase 4) - remove the non-database UI.** Remove the legacy catalog,
   the legacy jobs/schedules/alerts UI, the localStorage v1 worksheets and
   history, and the legacy query and Copilot handlers.
5. **#193 (phase 5) - remove the profile-less warehouse paths.** Remove
   `execute`, `warehouse_operation`, `legacy_operation`, `_query`, the
   non-native `serve_endpoint` branch, and the fallback in `check_auto_chill`
   from `warehouse_resident.py`. Delete `disaster_recovery.py` and
   `ai_benchmark.py`.

Keep the shared parts of `warehouse_resident.py`: the `_execute` Trino client,
`persistent_trino` (used by `query_service`), and the deployment-based `status`
/ `transition` path used by database v1 profiles. Keep the positive
`databaseMode &&` guards in the Workbench UI; they are false before a database
session is initialized and do not select legacy behavior.

The remaining `legacy` identifiers, such as `creation_mode='legacy'` on database
creations, name a database creation mode rather than the retired runtime mode.

## Consequences

- Running the application for development, manual testing, or a browser check
  now requires a database runtime profile. The Workbench cannot be previewed
  without one: the server exits 2 before binding, and requests return 503. The
  automated unit suite still runs without a profile.
- Workbench UI checks after phase 4 use the Node DOM/fetch adapter in
  `tests/test_workbench_database_ui.py` and the other `tests/test_workbench*_ui.py`
  files. A live browser check requires an environment with a database runtime
  profile; #192 was verified only through these tests.
- The regression suite shrank. `make verify-local-experiment` went from 161
  groups / 6,326 tests at phase 1 to 152 groups / 5,782 tests / 45 skipped / 0
  failures after phase 5.
- `tests/test_workbench_legacy_removal_ui.py` verifies that the removed legacy
  UI ids, functions, state, i18n keys, and API calls stay absent.
- Older guides under `docs/guides` and work-package records still mention
  deleted tools, modules, and Make targets. They are left unchanged as
  historical records.
- Deleted code can be recovered from git history (PRs #187-#193). Reintroducing
  a non-database mode requires a new ADR.

## Rejected alternatives

- **Keep legacy mode as read-only or deprecated.** No environment could run it,
  and the two-mode branches and shared helpers were ongoing maintenance cost.
- **Keep the deleted modules for a future cluster rebuild.** A rebuilt cluster
  runs database mode with a database runtime profile.
- **Delete everything in one pull request.** Database mode still borrowed legacy
  helpers. Moving the helpers first (phase 1), stopping the server paths
  (phase 2), and then deleting the modules (phase 3) kept each step reviewable
  and green.

## References

- [ADR-0036](ADR-0036_Adopt_Commander_Worker_Model_and_Pragmatic_Core_Reset.md)
- [ADR-0038](ADR-0038_Separate_Logical_Database_Creation_from_Platform_Provisioning.md)
- [PR #187](https://github.com/TommyKammy/Shirokuma/pull/187)
- [PR #188](https://github.com/TommyKammy/Shirokuma/pull/188)
- [PR #189](https://github.com/TommyKammy/Shirokuma/pull/189)
- [PR #190](https://github.com/TommyKammy/Shirokuma/pull/190)
- [PR #191](https://github.com/TommyKammy/Shirokuma/pull/191)
- [PR #192](https://github.com/TommyKammy/Shirokuma/pull/192)
- [PR #193](https://github.com/TommyKammy/Shirokuma/pull/193)
