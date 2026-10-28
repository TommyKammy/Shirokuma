# P4-2 bounded implementation brief (2026-10-21)

Purpose: implement the P4-2 section of the Phase 4 plan in the existing local
checkout, preserving previous dirty work. Real runtime acceptance is a separate
Commander responsibility. No push, publication, live mutation, or private
operator configuration in the browser.

## Fixed contract

* Current platform administrator with selected `admin` is required for every
  management read/mutation/replay/inspection. Reuse `catalog_admin.require_admin`.
* GET `/api/v1/admin/databases` returns `{databases:[...]}` including every
  registry state, safe Database metadata, fixed Schema metadata, and safe
  binding identity/state/revisions/generation. No credentials or private paths.
  This read model does not relax the ordinary Explorer inventory.
* PATCH `/api/v1/databases/{id}/display` accepts exactly
  `{display_name,description,expected_revision}`. PATCH
  `/api/v1/schemas/{id}/display` accepts exactly
  `{display_name,expected_revision}`. Both require `Idempotency-Key`.
* `display_name` is a string of 1–128 Unicode code points, contains a non-whitespace
  character, and has no Unicode control characters (category Cc). Preserve its
  exact spelling, whitespace, case, and Unicode sequence. `description` is a
  string of 0–2048 code points; allow LF/CR/TAB, reject other Cc characters.
  Duplicate display labels are allowed and remain distinct by SQL name/ID.
* `expected_revision` is an exact positive JSON integer. CAS conflict is 409;
  unknown resource is 404; malformed/type/extra fields are 400. All validation
  occurs before mutation. Schema purpose, SQL names, IDs, incarnation, physical
  identity, binding desired/applied revision/generation, endpoint and READY are
  unchanged. Metadata revision alone may stale an existing generation plan.
* Transactionally commit CAS, terminal `APPLIED` Operation, durable request
  idempotency, and before/after audit. Same actor/route/key+payload replays the
  original response; changed payload conflicts. Operation inspection exposes
  safe metadata/audit only after current authorization. Reuse P4-1 patterns and
  existing operations, adding a small metadata request/audit ledger (schema v11)
  rather than a generic workflow framework.
* Success response: `{operation_id,state:'APPLIED',resource_type:'database'|
  'schema',resource_id,revision}`. Operation GET uses existing common route.
* Normal CLI `database display list`, `database display set --database-id ...
  --display-name ... --description ... --expected-revision ... --idempotency-key
  ...`, `database display schema --schema-id ... --display-name ...
  --expected-revision ... --idempotency-key ...`, and `database display operation
  --operation-id ...` use authenticated HTTP and ordinary client profiles,
  `--api-url` defaulting to port 8888. No private operator config.
* DB SQL names retain `[a-z][a-z0-9_]{0,30}`, reserved 4 plus DB-only `iceberg`
  rejection; exact fixed four Schema profile. Do not spread 31-character limits
  to native SQL identifiers, ResourceRefs, model IDs or source aliases.

## Backend worker ownership

Allowed source: `tools/local_experiment/{data_management.py,control_store.py,
catalog_admin.py,workbench_server.py,display_management.py,display_client.py}`,
`internal/cli/{database.go,database_display.go,database_test.go,
database_display_test.go}`, focused tests
`tools/local_experiment/tests/test_{data_management,control_store,catalog_admin,
workbench_database,display_management,display_client,warehouse_operations}.py`.
If tests actually live elsewhere, use their existing equivalent paths only.
Do not modify frontend, plan/report, or runtime. Check migration atomicity and
current maximum/selected role. PATCH needs origin check, duplicate JSON field
rejection, role_scope and CORS method/header. Avoid broad refactors.

Required focused tests: CAS/race/replay/conflict/restart, auth and revoked admin,
Unicode/HTML/type/length/empty/extra fields, schema purpose immutable, all binding
fields immutable, metadata plan staleness with explicit replan, migration, CLI
argument/HTTP wiring, ordinary inventory unchanged. Run relevant Python tests
and `go test ./internal/cli` as appropriate, with compact logs.

## Frontend worker ownership

Allowed source: `tools/local_experiment/workbench/index.html` and existing/new
focused Workbench UI tests only. Do not modify backend/Go/docs/runtime. Follow
existing PRODUCT.md, calm dense product tokens, Japanese/English labels,
accessible labels and focus states. No new palette or decorative dashboards.

Add a static Database-mode-only DB management nav/panel, gated on current
platform_admin + maximum admin + selected admin. Keep its DB selection separate
from Explorer and Worksheet. Show SQL name/ID/state and readonly binding data,
DB display/description CAS form, fixed Schema display CAS forms, P4-1 grant
desired/effective/revisions/epoch and explicit revoke, DB registration, public
bootstrap/full plan, operation inspect and apply-request handoff. Explain pending
backend verification and private operator waiting accurately; no automatic apply
or replan. Existing public routes remain authoritative for generation.

Use a dedicated admin request helper with method/key. Snapshot identity/role,
inventory epoch, management selection/request epochs and DB ID; discard stale
responses. On current admin loss, clear cached management payload and invalidate
inflight work. CAS conflict retains draft/focus and offers explicit refresh;
never auto-resubmit with fresh revision. Preserve frozen mutation payload/key
for retry on uncertain response. Show metadata via textContent. Refresh must not
overwrite drafts or Worksheet SQL/tab/context, including periodic refresh and
language changes. Cover these in the existing Node UI harness.

## Worker delivery

Run the required codex-cli worker (`gpt-6.1-sol`, medium, workspace-write) with
this brief and explicit file ownership. Save logs under ignored
`var/shirokuma/p4-2-acceptance`. Review resulting diffs and focused test results.
Report changed files, test command/result, limitations, and backend/frontend
contract mismatches. Do not count fixture tests as real browser/backend proof.

## Review-authorized integration fixes

The Commander also authorized focused regression integration in `Makefile`,
`tests/test_grant_management.py`, `tests/test_catalog_admin_management.py`,
`tests/test_workbench_management_api.py`, and `tests/test_workbench.py` (use the
existing exact equivalent test filename where applicable). These cover v9
fixture fabrication after v11, current-admin inspection expectations, navigation
and appended bilingual dictionaries, and discovery of the two new backend tests.
Terraform format checking is scoped to `opentofu`, which contains every tracked
Terraform source; ignored historical acceptance workspaces are preserved.

The live browser found metadata receipt invalidation being confused with loss of
platform management capability. The focused correction preserves the management
panel, draft, focus and expected revision on metadata refresh, while current
account/platform/maximum/selected-role loss and management 401/403 still clear it.
Unpaired Unicode surrogates are malformed input (400). The normal CLI passes
metadata strings using `--flag=value` so leading hyphens remain literal text.
