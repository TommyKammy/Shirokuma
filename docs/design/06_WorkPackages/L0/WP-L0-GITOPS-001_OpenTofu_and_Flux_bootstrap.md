---
project: Shirokuma
doc_id: "WP-L0-GITOPS-001"
title: "WP-L0-GITOPS-001 OpenTofu and Flux bootstrap"
status: local-partial
created: 2026-07-05
updated: 2026-10-03
version: "0.71"
area: "workpackage"
tags: [shirokuma, workpackage, l0, gitops, flux]
---

# WP-L0-GITOPS-001 OpenTofu and Flux bootstrap

## 2026-10-03: explicit null inventory regression (offline)

The fixed-profile list reader already rejects `items:null` and permits an empty `items:[]` as a complete list envelope; an isolated regression now pins both behaviors without changing the diagnostic implementation. An empty list does not imply healthy fixed workloads. Go CLI tests (823 passed) and vet passed; combined verification is in Milestones. The dedicated VM was observed stopped without starting it or reading live API/Git/SQL, reconciling or changing retained data. Fake list framing cannot authenticate API/Git, establish actual rollout, strict admission or operator acceptance. Roll back only the new test if necessary, retaining VM and evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: reported DNS name grammar (offline)

The fixed-profile complete-list reader now applies the existing Kubernetes DNS subdomain/label validator to any explicitly reported item name/namespace, including unrelated members; length alone previously admitted punctuation, uppercase and overlong labels. A disposable invalid-name negative failed before repair; dotted valid resource names and valid namespace labels pass. Older resource-specific malformed-name and empty-name fixtures now fail at the earlier incomplete-list boundary; generic doctor is unchanged. Read-only Colima status was stopped; no live API/Git, VM start, reconciliation or retained data/backup mutation. This does not authenticate API/Git, provide atomic inventory or historical cleanup, clear strict admission or establish operator acceptance. Roll back inventory guard/tests only, preserving VM/evidence. Original Vault direct sync pending outside checkout; checks in Milestones.

## 2026-10-03: bounded reported Kubernetes names (offline)

The fixed-profile complete inventory rejects a reported item name longer than 253 bytes or namespace longer than 63 bytes, including unrelated list members. An isolated 254-byte negative failed before repair; boundary-length names remain compatible. Focused Go (821 tests), vet and local verification passed. Generic doctor unchanged. Dedicated Colima was read-only observed stopped; no live API/Git, reconciliation or retained data/backup changes. This checks reported shape, not authenticated/atomic Kubernetes or Git state, historical cleanup, strict admission or operator acceptance. Roll back reader/test only, preserving VM/evidence. Original Vault direct sync pending outside checkout; broader results in Milestones.

## 2026-10-03: fixed-list envelope contract review (offline)

Independently rechecked the current fixed-profile list reader and Go tests while the dedicated VM was stopped. Canonical `kind`/`apiVersion`, incomplete pagination and reported list resource versions already fail closed; omission of projected fields remains intentional. No behavioral change is justified by this review, and no live API/Git or reconciliation was attempted. `go test ./internal/cli -count=1` passed; full local verification and limits are recorded in Milestones. Remote provenance, atomic inventory, historical cleanup, strict admission and operator acceptance remain open. There is no runtime rollback; preserve earlier evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: reported object resource versions (offline)

The fixed-profile inventory now refuses an explicitly reported per-object `metadata.resourceVersion` that is empty, null, over 1024 bytes, case-aliased, lossy or contains invisible characters, including on unrelated members. Omitted versions remain compatible with projected fixtures. A focused fake negative failed before repair; `go test ./... -count=1` (820 passed) and `go vet ./...` passed after. The generic doctor is unchanged. Read-only Colima listing showed the dedicated VM stopped; no VM start, API/Git/SQL, reconciliation, replay or retained data/backup changes. This offline field consistency does not authenticate API/Git, establish atomic inventory or historical cleanup, clear strict admission or grant operator acceptance. Roll back reader/test only; preserve VM/evidence. Original Vault direct sync pending outside checkout; broader verification is in Milestones.

## 2026-10-03: invisible reported resource identities (offline)

Two independent fixed-profile inventory checks reject whitespace/control/format characters in reported item names and namespaces, and in item kinds and API versions, including escaped newline and zero-width characters on unrelated members. A disposable negative failed before repair; visible identifiers and omitted projected fields pass. Earlier newline fixtures now fail at the complete-list boundary rather than their later resource-specific checks. `go test ./internal/cli -count=1` (819 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed; private logs are under `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-060/`. Read-only `colima list` reported the dedicated VM stopped; no VM start, API/Git/SQL, reconciliation, retained source/session/report/backup mutation or request replay. Fake identifier validation cannot authenticate API or Git, guarantee atomic inventory or historical cleanup, clear strict admission or grant operator/production acceptance. Roll back only the local reader/tests, preserving VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: explicit zero generation is not omission (offline)

Fixed-profile inventories now reject a positive status or condition observed generation when an unrelated member explicitly reports metadata generation zero. Omitted generation remains compatible with projected fixtures, and zero observed generation remains valid. The isolated negative failed before repair; a pre-existing zero-generation Flux fixture now fails earlier as an incomplete inventory. Focused Go and vet passed; broader verification is in Milestones. Dedicated Colima was observed stopped; no live API/Git/SQL, reconciliation, saved request replay or retained data/backup change. This checks only reported consistency, not API provenance, atomicity, historical cleanup, strict admission or operator acceptance. Roll back reader/tests only, retaining VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: invisible characters in reported Git refs and revisions (offline)

The fixed-profile complete inventory now refuses whitespace and Unicode control/format characters in reported Git selectors, artifact revisions and applied revisions, even on unrelated members. An isolated escaped-newline branch was accepted before repair and rejected afterward; canonical revisions and omitted fields remain compatible. `go test ./internal/cli -count=1` (817 passed), `go vet ./...` and local milestone verification passed. Generic doctor is unchanged. Dedicated Colima was read-only observed stopped; no VM start, API/Git/SQL, reconciliation, archived request replay or retained data/backup mutation. This checks reported shape, not remote provenance, atomicity, historical cleanup, strict admission or operator acceptance. Roll back the local reader/test only, preserving VM/evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: invisible characters in reported locations (offline)

The fixed-profile complete inventory now refuses whitespace and Unicode control/format characters in a reported Git source URL, Kustomization path or artifact URL, including escaped newline, carriage return and zero-width characters on unrelated items. A focused fake response was accepted before the repair and rejected afterward; the existing canonical positives still pass. Focused Go tests, vet and full local verification passed; see Milestones. The generic doctor remains unchanged. No VM start, live API/Git/SQL, reconciliation, retained data/backup mutation or replay; the dedicated Colima profile was read-only observed stopped. This validates only reported shape, not the URL destination or Git/API provenance, atomic inventory, historical cleanup, strict admission or operator acceptance. Roll back reader/tests only, preserving VM/evidence; original Vault direct sync pending outside checkout.

## 2026-10-03: reported Flux artifact location shape (offline)

The fixed-profile complete inventory now rejects an artifact `url` containing U+FFFD after lossy JSON decoding, explicit null, or differently cased `URL` (which Go's JSON decoder may otherwise accept as a canonical field). A focused disposable response was accepted before the guard and refused afterward; canonical and omitted URL fixtures remain compatible. This is reported-field consistency, not verification of the URL destination, remote Git, API identity, historical cleanup, strict admission or operator acceptance. Generic doctor is unchanged. No VM start, API/Git/SQL access, reconciliation, retained source/backup mutation or replay; the dedicated Colima profile was read-only observed stopped. Roll back reader/test only and preserve evidence/VM; direct original Vault sync pending outside checkout. Focused and broader test results are recorded in Local Experiment Milestones.

## 2026-10-03: non-lossy reported source locations (offline)

Fixed-profile inventories refuse U+FFFD in reported Git URL and Kustomization path, including Go's lossy decoding of escaped unpaired surrogates on unrelated members. The isolated URL negative failed before the repair; source location and omitted-field positives and the focused Go suite passed. Generic doctor remains unchanged. Dedicated Colima was observed stopped; no VM/API/Git read, reconciliation or retained data/backup mutation. This does not authenticate the remote Git source, establish atomic inventory or historical cleanup, clear strict admission or prove operator acceptance. Roll back the local reader/test only; preserve VM/evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: non-lossy inventory envelope, references and revisions (offline)

Three independent fixed-profile safeguards refuse U+FFFD after JSON decoding in reported item `kind`/`apiVersion`, nested source/secret reference identities and Git selector/artifact/applied revision strings, including escaped unpaired surrogates on unrelated list members. Omitted fields and valid reported strings remain compatible; generic doctor is unchanged. Focused negatives failed before each repair; full Go tests, vet and milestone verification are recorded in Local Experiment Milestones. Read-only dedicated Colima list: stopped. No VM start, API/Git/SQL observation, reconciliation, request replay or retained evidence/backup mutation. These offline guards cannot authenticate API or remote Git, make separate reads atomic, prove historical cleanup, clear strict admission or establish operator/production acceptance. Roll back reader/tests only and preserve VM/evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: non-lossy item names and namespaces (offline)

Fixed-profile inventories now reject U+FFFD in explicitly reported item names and namespaces, including an unpaired escaped JSON surrogate replaced by Go's decoder. The focused negative failed before repair; `go test ./internal/cli -count=1` (810 passed) and `go vet ./...` passed after. Omitted projected identities and generic doctor remain unchanged. The dedicated Colima profile was observed stopped; no VM start, live API/Git/SQL, reconciliation or retained data/backup change. Offline response consistency is not authenticated or atomic inventory, remote provenance, historical cleanup, strict admission or operator acceptance. Roll back local reader/test only; retain VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: bounded UID and non-lossy condition type (offline)

Two independent fixed-profile inventory checks refuse reported UIDs longer than 1024 bytes and condition types containing U+FFFD (including an unpaired escaped JSON surrogate silently replaced by Go). Omitted UIDs, a 1024-byte visible opaque UID and normal conditions remain compatible; generic doctor is unchanged. Both focused negatives failed before repair; `go test ./internal/cli -count=1` (809 passed) and `go vet ./...` passed afterward. The dedicated Colima profile was observed stopped. No VM start, API/Git/SQL read, reconciliation or retained data/backup change. Offline field validation does not authenticate API/Git, establish atomic inventory or historical cleanup, clear strict admission or prove operator/production acceptance. Roll back only local reader/tests; preserve stopped VM and saved evidence. Original Vault sync pending outside checkout.

## 2026-10-03: lossy JSON identity characters (offline)

The fixed-profile reader refuses U+FFFD in reported resource UIDs and list resource versions. Go's JSON decoder replaces an escaped unpaired surrogate with that character, so an otherwise complete inventory could silently use a lossy identity. Focused UID/version negatives failed before the guard; visible opaque fixtures and omitted fields remain compatible. `go test ./internal/cli -count=1` (808 passed) and `go vet ./...` passed. This does not authenticate API/Git, prove atomicity, historical cleanup, strict admission or operator acceptance. Dedicated Colima was read-only observed stopped; no VM start, API/SQL, reconciliation or retained data/backup change. Roll back only the local reader/tests, preserving VM/evidence; original Vault direct sync pending outside checkout.

## 2026-10-03: bounded deletion-time precision (offline)

Fixed-profile inventory rejects a reported deletion time with more than nine fractional-second digits: Go's RFC3339Nano parser otherwise silently discards excess precision. Omitted/empty and valid nanosecond timestamps remain compatible. The focused negative failed before the fix; `go test ./internal/cli -count=1` (807 passed), `go vet ./...`, `make verify-local-experiment` and `make verify-design-context` passed afterward. Dedicated Colima was read-only observed stopped. No live API/Git/SQL, VM start, reconciliation, replay or retained data/backup changes. This offline shape check does not authenticate API/Git, prove atomicity, historical cleanup, strict publication admission or operator acceptance. Roll back the reader/test only, preserving the VM and evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-03: deletion time syntax and condition/status generation (offline)

Two independent complete-inventory guards: a nonempty reported deletion timestamp must have RFC3339 dot-fraction syntax and a timezone within the stated offset range, rather than relying on Go's permissive time parser (which accepts comma fractions and +24:00); and an unrelated member's explicitly reported condition observed generation cannot be ahead of its explicitly reported top-level status observed generation. Omitted generation/timestamp fields and canonical examples remain compatible. Focused negatives failed before repair; `go test ./internal/cli -count=1` (807 passed), `go vet ./...`, `make verify-local-experiment` and `git diff --check` passed afterward. An existing explicit-zero fixture now expects earlier incomplete-inventory refusal instead of the later target-specific mismatch. Read-only `colima status --profile mac-studio-solo` reported stopped; no VM start, API/SQL, reconciliation, unknown-request replay or retained data/backup mutation. Fake reported fields cannot authenticate Kubernetes/Git, make separate reads atomic, prove historical cleanup, clear strict publication admission or establish operator/production acceptance. Roll back only the reader and tests, preserving stopped VM and saved evidence. Original Vault direct sync remains pending outside checkout.

## 2026-10-03: reported deletion timestamps (offline)

Fixed-profile inventories now refuse malformed nonempty `metadata.deletionTimestamp` values even on unrelated members; RFC3339 timestamps, empty/omitted fixture fields remain compatible. The isolated negative failed before the repair; `go test ./internal/cli -count=1` passed (806 tests). Dedicated `mac-studio-solo` Colima was read-only observed stopped. No live API/SQL, reconciliation, VM start or retained data/backup mutation. This is reported-field consistency, not authenticated/atomic Kubernetes or Git evidence, historical cleanup, strict admission or operator acceptance. Roll back only the reader/test; preserve the stopped VM and saved evidence. Original Vault direct sync remains pending outside checkout.

## 2026-10-03: invisible reported resource UID (offline)

The fixed-profile inventory now refuses a reported UID containing whitespace or Unicode control/format characters, including an escaped zero-width separator; omitted UIDs and visible opaque values remain compatible. The focused negative failed before the repair and `go test ./internal/cli -count=1` passed (805 tests). Read-only dedicated Colima status reported stopped. No VM start, live API/SQL, reconciliation, replay or retained data/backup mutation. Fake response validation is not authenticated/atomic API or Git evidence, historical cleanup, strict admission or operator acceptance. Roll back only reader/test; retain VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: Flux controller/resource UID collision (offline)

The fixed local Flux doctor now rejects a UID reported by both a controller Deployment and a Flux custom resource in its two separate inventories. The fake negative failed before repair; the focused Go suite (805 tests) passed afterward. Omitted UIDs remain compatible; the generic doctor is unchanged. Separate reads cannot prove an atomic/authenticated cluster snapshot or Git provenance, historical cleanup, strict admission or operator acceptance. No VM start, live API/SQL, reconciliation, unknown-request replay or retained data/backup change; dedicated Colima remained stopped. Revert reader/test only, preserving VM/evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: workload UIDs across two namespace reads (offline)

The fixed local doctor refuses a reported UID reused by objects in its two separately read Lakehouse namespaces, degrading both scopes without exposing the UID. A focused fake collision failed before repair; the focused Go suite (804 tests) and vet passed afterward. Omitted UIDs remain compatible. These reads are not atomic or authenticated and do not establish Git provenance, historical cleanup, strict admission or operator acceptance. The dedicated Colima profile was read-only observed stopped; no live API/SQL, reconciliation, replay or retained data/backup change. Revert reader/test only, retaining VM/evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: available and unavailable capacity cannot overlap (offline)

The fixed-profile reader now refuses an unrelated workload reporting available plus unavailable replicas above explicitly desired capacity; available surplus during rolling updates remains compatible only with zero unavailable. Missing fields remain optional. The focused negative failed before repair; the first full Go run exposed an older target-specific failure message superseded by the earlier incomplete-list check, and the final suite passed. This is reported-field consistency, not authenticated or atomic Kubernetes/Git evidence, historical cleanup, strict admission or operator acceptance. Dedicated Colima was read-only observed stopped; no VM start, API/SQL, replay, reconciliation or retained storage/backup mutation. Roll back only the reader/test, preserving VM and evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: reported UID consistency in fixed inventories (offline)

The fixed-profile complete-inventory reader rejects two members reporting the same nonempty Kubernetes UID, even under different names/kinds, and rejects explicitly empty/null or noncanonical `UID`/`Uid` metadata keys. Omitted UIDs remain compatible with older projected fixtures. A broader duplicate-name check was abandoned after six existing tests exposed its earlier failure summary; their existing resource-specific duplicate rejection remains unchanged. `go test ./... -count=1` (802 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed after narrowing the fix; milestone regression is recorded in Local Experiment Milestones. Dedicated Colima read-only status reported stopped; no VM start, API/SQL, reconciliation, unknown request replay or retained data/backup mutation. Offline UID consistency is not authenticated/atomic API inventory, Git provenance, historical cleanup, strict publication admission or operator/production acceptance. Roll back only the reader/tests, retaining the stopped VM and saved evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: unavailable replicas bounded by desired capacity (offline)

The fixed-profile complete inventory refuses an unrelated workload explicitly reporting more unavailable replicas than its explicitly declared desired replica count, including zero desired. An isolated negative failed before repair; omitted fields and equality remain compatible. Rolling total replicas may still exceed desired. Focused Go tests (800 passed) and vet passed; milestone regression is recorded in Local Experiment Milestones. The dedicated Colima profile was observed stopped; no VM start, API/SQL, reconciliation, unknown UUID replay or retained session/backup mutation. Fake counters cannot authenticate API/Git, provide an atomic inventory, prove historical cleanup, clear strict admission or establish operator/production acceptance. Roll back reader/test only; preserve VM/evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-03: available replicas cannot exceed ready replicas (offline)

The fixed-profile complete-inventory reader rejects an unrelated member explicitly reporting more available than ready replicas, even when both remain below total. Missing counters remain optional and rolling total may exceed desired. An isolated negative failed before repair; a prior missing-ready fixture now fails earlier as incomplete inventory instead of at the target rollout boundary. Focused Go tests and milestone regression are recorded in Local Experiment Milestones. Dedicated Colima read-only status was stopped; no VM start, live API/SQL, reconciliation, unknown UUID replay or retained session/backup mutation. This is reported-field consistency, not authenticated or atomic API/Git evidence, historical cleanup, strict publication admission or operator/production acceptance. Roll back reader/test only; preserve VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: impossible unrelated replica totals (offline)

The fixed-profile inventory now refuses an unrelated member whose explicitly reported ready, updated or available replicas exceed its explicitly reported total. A focused negative failed before repair. Omitted counters remain compatible; a rolling update whose total exceeds desired is valid. `go test ./internal/cli -count=1` (798 passed), `go vet ./...`, and Warehouse focused tests passed; milestone regression is recorded in Local Experiment Milestones. The dedicated Colima profile was observed stopped; no VM start, API/SQL, reconciliation, archived UUID replay, retained data or backup modification. Reported-counter consistency is not authenticated/atomic inventory, Git provenance, historical cleanup, strict publication admission or operator/production acceptance. Roll back only the reader/test; preserve VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: negative replica counters on unrelated inventory members (offline)

The fixed-profile complete-inventory reader now rejects negative declared `spec.replicas` and reported `status` replica counters on any member, including unrelated resources. The isolated negative failed before repair; explicit zero remains valid, omitted fields retain fixture compatibility. A pre-existing negative-unavailable-replica fixture now fails earlier at the incomplete-list boundary rather than the target rollout check. `go test ./internal/cli -count=1` (797 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed after repair. Dedicated Colima read-only status: stopped. No live API/SQL, VM start, reconciliation, archived UUID replay, retained data or backup modification. Fake response consistency cannot authenticate API/Git or establish atomicity, historical cleanup, strict publication admission or operator/production acceptance. Roll back the reader and tests only; preserve stopped VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: duplicated conditions on unrelated list members (offline)

The fixed-profile complete inventory reader now refuses two conditions with the same type on *any* member, not only on the expected controller/source/workload. An unrelated contradictory or repeated Ready/Available previously passed the list-level check; the isolated negative failed before repair. Distinct types and omitted conditions still pass. Existing target-specific duplicate tests now expect the earlier incomplete-list refusal; generic doctor is unchanged. `go test ./internal/cli -count=1`, `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed after repair. Private logs under `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-034/`. Dedicated Colima remained stopped; no API/SQL, reconciliation, unknown-request replay or retained data/backup mutation. This fake-response guard does not authenticate Git/API, establish atomic observation, historical cleanup, strict publication admission or operator/production acceptance. Roll back only local reader/tests, preserving VM and evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-03: invalid inventory UTF-8 and unrelated observed generation (offline)

Two independent fixed-profile guards: reject raw invalid UTF-8 before Go JSON replacement can normalize a claimed complete list; reject negative or future top-level `status.observedGeneration` on any item, including unrelated members. Valid UTF-8, omitted/zero status generation and an older reported generation remain compatible; the generic profile is unchanged. Isolated negatives failed before the fixes. `go test ./internal/cli -count=1` (795 passed), `go vet ./...`, and `make verify-local-experiment` passed after repair. Two older Flux mismatch tests now expect the earlier incomplete-list refusal for future generations while explicit zero retains the specific mismatch. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay or retained data/backup mutation. Offline shape tests do not authenticate or atomically observe API/Git, prove historical cleanup, clear strict publication admission or establish operator/production acceptance. Roll back only the reader/tests; preserve VM and evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: negative generation on unrelated inventory members (offline)

Fixed-profile controller, Flux resource and Lakehouse workload inventories now reject a negative `metadata.generation` even for an unrelated item without conditions; zero remains compatible with optional/projected fixture fields. The regression was added after the fix, so its pre-fix failure was not separately executed; focused test, full `go test ./internal/cli -count=1` (792 passed), `go vet ./...` and `make verify-local-experiment` passed. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconcile, unknown-request replay or retained storage/backup mutation. This is offline shape consistency, not authenticated/atomic inventory, historical cleanup, strict publication admission or operator/production acceptance. Roll back only the local reader/test; preserve the VM and evidence. Direct original Vault handoff sync pending outside checkout.

## 2026-10-03: unrelated condition type and generation bounds (offline)

Two independent fixed-profile inventory checks now refuse condition types with whitespace/control characters or more than 316 bytes, and negative or future `observedGeneration` relative to a reported positive item generation. Previously a malformed condition on an unrelated resource could coexist with healthy expected resources. Omitted condition generation remains compatible; a zero item generation continues through the existing resource-specific rejection. Fake controller, Flux-resource and Lakehouse workload negatives failed before repair and passed afterward (`go test ./internal/cli -count=1`: 791 passed; `go vet ./...`). Dedicated Colima was observed stopped; no VM start, API/SQL read, reconciliation, unknown-request replay or retained storage/backup mutation. Offline consistency cannot authenticate API/Git identity, establish atomic inventory or historical cleanup, clear strict admission or prove operator/production acceptance. Roll back only this reader/tests; retain stopped VM and saved evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: nested reference nulls and unrelated conditions (offline)

Two independent fixed-profile inventory checks reject explicit null scalar members of `spec.ref`, `spec.secretRef`, `spec.sourceRef` and `status.artifact`, and malformed condition types/statuses on unrelated list items. The former could decode as omission; the latter could be ignored when the expected object was healthy. Isolated negatives failed before repair; a malformed condition on a target now fails earlier as an incomplete list rather than at the target-specific condition check. Omitted optional fields, canonical non-null references and valid `Unknown` conditions remain compatible; generic doctor is unchanged. `go test ./internal/cli -count=1` (789 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed. Dedicated Colima was read-only observed stopped; no live Kubernetes/SQL read, VM start, reconciliation, request replay or retained data/backup mutation. Offline fake responses are not authenticated/atomic inventory, historical cleanup, Git provenance, strict admission or operator/production acceptance. Roll back only the local inventory reader/tests while retaining the stopped VM and evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: null inventory members (offline)

Two independent fixed-profile checks reject explicit null list items and null condition entries, including on otherwise unrelated inventory members. Go's JSON decoder previously treated these as zero-value objects, allowing the list to appear complete. Omitted optional fields and ordinary non-null members remain compatible; generic doctor is unchanged. Focused negatives failed before repair; `go test ./internal/cli -count=1` (787 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed afterward. Dedicated Colima was read-only observed stopped; no VM start, API/SQL read, reconciliation, unknown-request replay or retained data/backup change. This checks fake response consistency, not authenticated or atomic Kubernetes inventory, Git provenance, historical cleanup, strict admission or operator/production acceptance. Roll back only the local reader/tests, preserving the stopped VM and saved evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: null nested scalar and condition fields (offline)

The fixed-profile inventory reader now rejects explicit null identity fields in item metadata, declared scalar fields in item spec/status, and type/status/generation fields within conditions. Omitted optional fields and canonical non-null objects remain compatible; generic doctor is unchanged. Two focused negatives failed before repair; `go test ./internal/cli -count=1` (783 passed) and `go vet ./...` passed after repair. Dedicated Colima status reported stopped; no live API/SQL, reconciliation, unknown-request replay or retained data/backup change. These offline response checks do not authenticate Kubernetes/Git, establish atomicity or historical cleanup, clear strict admission or establish operator/production acceptance. Roll back only the local inventory reader/tests, preserving the stopped VM and saved evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: explicit null nested inventory objects (offline)

The fixed-profile doctor now refuses explicit `null` in item `spec.ref`, `spec.secretRef`, `spec.sourceRef` or `status.artifact`, rather than decoding it as an omitted struct on any inventory member. Omitted fields remain compatible with older projections; valid objects pass. A focused negative failed before repair and passed afterward. This is read-only fake-response validation, not authenticated API/Git identity, atomic inventory, live readiness, strict publication admission or operator/production acceptance. Dedicated Colima remained stopped; no live API/SQL, reconciliation, retained data/backup mutation or unknown-request replay. Roll back only this reader/test, retaining VM and evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-03: nested keys and explicit null item fields (offline)

Two further fixed-profile inventory increments: nested `spec`, `status`, source-ref, artifact and condition keys now reject case-insensitive aliases (including aliases alongside canonical fields). Independently, explicitly null item `kind`, `apiVersion`, `metadata`, `spec` and `status` are not interpreted as omitted fields, even on unrelated members of a supposedly complete list. Omitted optional fields remain compatible, and generic doctor is unchanged. Fake controller, Flux resource and namespace workload negatives and canonical positives passed `go test ./internal/cli -count=1` (780 passed), `go vet ./...`, `make verify-local-experiment` and `git diff --check`. Dedicated Colima was observed stopped; no VM start, actual API/SQL, reconciliation, saved-request replay, retained data or backup mutation. Offline shape checks do not authenticate API/Git identity, establish atomic inventory or historical cleanup, clear strict publication admission or prove operator/production acceptance. Roll back only local reader/tests; preserve stopped VM and historical evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: canonical item envelope keys (offline)

As another independent fixed-profile check, each returned inventory item rejects differently cased aliases of `kind`, `apiVersion`, `metadata`, `spec` and `status` even when canonical fields are also present. This prevents Go's case-insensitive decoder from accepting a conflicting secondary representation of a fixed controller, Flux resource or namespace workload. Fake aliases and canonical fixtures are covered; generic doctor remains unchanged. `go test ./internal/cli -count=1` (778 passed), `go vet ./...`, `make verify-design-context` (89 documents) and `git diff --check` passed; the earlier nested-metadata increment passed `make verify-local-experiment` before this subsequent edit. This is offline shape validation, not authenticated API/Git identity, atomic observation, historical cleanup, strict admission or operator/production acceptance. Dedicated Colima remained stopped; no VM start, live API/SQL, reconciliation, request replay or retained data/backup change. Revert only reader/test changes to roll back; retain VM and evidence. Direct Vault handoff synchronization remains pending outside checkout.

## 2026-10-03: canonical nested inventory identities (offline)

Two independent fixed-profile inventory checks reject case-insensitive aliases of the list metadata pagination/version fields and of item metadata identity fields (name, namespace, generation, deletion timestamp). Go's JSON decoder would otherwise populate the canonical struct from differently cased keys; an empty aliased continuation or a positive aliased generation could appear complete/current. Fake controller, source and Lakehouse inventories exercise each alias and canonical positives. Omitted optional fields and the generic doctor retain their existing behavior. `go test ./internal/cli -count=1` (777 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay or retained data/backup mutation. These offline response checks cannot authenticate Kubernetes/Git, provide an atomic snapshot, prove historical cleanup, clear publication admission or establish operator/production acceptance. Roll back only the local inventory reader/tests; preserve VM and evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: canonical list envelope keys (offline)

Verification: `go test ./internal/cli -count=1` (775 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents), and `git diff --check` passed. Private full-regression log: `tmp/local-experiment/autonomous-runner/run-iiqtlurv/attempt-023/verify-second.log`.

Independent local-only inventory increment: fixed-profile controller, Flux resource and namespace workload lists now require the exact `items` key and refuse case-insensitive aliases of the top-level `items`, `metadata`, `kind`, and `apiVersion` keys. Go's decoder otherwise accepts `Items` as `items` and may interpret aliased or conflicting fields as a complete Kubernetes list. Fake list negatives and canonical positives pass; the generic doctor remains unchanged. Dedicated Colima was observed stopped, without VM start, API/SQL observation, reconciliation, unknown-request replay or retained storage/backup change. Shape validation is not authenticated or atomic inventory, source provenance, strict admission or production/operator acceptance. Revert only the local inventory reader/test to recover; preserve VM and evidence. Original Vault handoff direct sync remains pending outside checkout.

## 2026-10-03: incomplete diagnostic conditions (offline)

Two independent fixed-profile diagnostic paths now reject condition entries with an empty type or a status other than Kubernetes `True`, `False`, or `Unknown`, even when another entry claims Ready/Available. This covers Flux controller Deployments and queried Flux resources, and Lakehouse namespace workloads; `null`, `{}`, missing type/status, and an unrelated condition with an invalid status were checked with fake lists. The generic Flux profile remains unchanged. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay, retained data or backup mutation. These checks cannot authenticate the API or Git, prove actual readiness, clear strict admission or establish operator/production acceptance. Revert only the local diagnostic and test changes to recover, preserving the stopped VM and saved evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: duplicated condition types (offline)

Two independent fixed-profile checks reject repeated Kubernetes condition types even when both entries claim `True`: Flux controller Deployments and Flux source/Kustomization resources, and Lakehouse Deployments/StatefulSets. Previously a repeated `Ready` or `Available` could contribute to a healthy diagnosis despite an ambiguous response. The generic Flux doctor is unchanged; a suspended root with a single stale Ready condition remains informational. Fake duplicate and healthy cases passed `go test ./internal/cli -count=1` (772 tests), `go vet ./...`, `make verify-local-experiment` and `make verify-design-context` (89 documents); an existing suspended-root fixture with two Ready conditions failed on the first focused run and was corrected to test a single condition. Dedicated Colima reported stopped; no start, live API/SQL, reconciliation, unknown-request replay, retained data or backup mutation. Offline condition consistency is not API/Git authentication, actual readiness, smoke, strict publication admission or operator/production acceptance. Roll back only the condition readers/tests and retain the stopped VM and historical evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: invisible inventory versions (offline)

As a separate local-only increment, fixed-profile inventory versions now reject Unicode control/format and whitespace characters, including an escaped zero-width character, rather than checking only ASCII control/space. Opaque bounded visible versions and omitted fixture fields remain compatible. Focused negative tests failed before repair; `go test ./internal/cli -count=1` (770 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` and `git diff --check` passed after repair. Dedicated Colima remained stopped; no live API/SQL, reconciliation, unknown-request replay, retained session or backup mutation. A visible response field is not authenticated inventory or Git provenance, atomicity, historical cleanup, strict admission or operator/production acceptance. Revert only the reader/tests and retain VM and evidence. Original Vault direct handoff sync pending outside checkout.

## 2026-10-03: null continuation and null list identity (offline)

Two independent fixed-profile response-shape checks: an explicitly null `metadata.continue` can no longer masquerade as an omitted pagination field; separately, explicitly null top-level `kind` or `apiVersion` can no longer masquerade as an omitted projected fixture field. Controller, Flux-resource and both Lakehouse namespace inventories reject these before claiming a complete inventory. Omitted optional fields and the generic doctor remain compatible. Focused negatives failed before the fix; `go test ./internal/cli -count=1` (770 passed), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed after repair. The dedicated Colima profile reported stopped; it was not started and no API/SQL read, reconciliation, unknown-request replay, retained session or backup mutation occurred. These fake-response checks do not authenticate the API or Git, prove atomicity or historical cleanup, clear strict publication admission, or establish operator/production acceptance. Roll back only the local reader/tests; keep the VM stopped and saved evidence intact. Original Vault handoff direct synchronization remains pending outside this checkout.

## 2026-10-03: explicit null list fields (offline)

Fixed-profile controller, Flux-resource and Lakehouse inventory reads now reject explicit null `metadata.resourceVersion` or `metadata.remainingItemCount`, rather than equating null with an omitted optional field. Omitted fields and bounded values remain compatible with older fixtures; an invalid controller response stops before reading source resources. Negative tests failed before repair and `go test ./internal/cli -count=1` (770 passed), `go vet ./...` and Warehouse focused tests passed afterward. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay, saved session or backup mutation. This is offline response consistency, not authenticated API/Git identity, atomic inventory, strict admission or operator/production acceptance. Roll back only the diagnostic reader/tests and preserve the stopped VM and evidence. Original Vault direct handoff sync pending outside checkout.

## 2026-10-03: duplicate JSON fields and bounded inventory versions (offline)

Milestone regression: `make verify-local-experiment` and `make verify-design-context` (89 documents) passed after the focused checks.

Two independent fixed-profile diagnostic increments: controller, Flux-resource and Lakehouse workload lists now reject duplicate JSON object keys at any depth (including nested identities/status and escaped equivalent keys), rather than accepting the later decoded value as a complete inventory. An ambiguous controller response stops before the source read; no untrusted pagination token is echoed. Separately, explicitly reported list resource versions with embedded whitespace/control bytes or more than 1024 bytes are rejected; nonempty opaque bounded versions remain valid. Missing optional versions and the generic-profile doctor retain their previous behavior. Fake positives/negatives, `go test ./internal/cli -count=1` (770 passed), `go vet ./...`, and `git diff --check` passed. Dedicated Colima was read-only observed stopped; no VM start, actual API/SQL, reconciliation, unknown-request replay, retained session/volume or backup mutation. The diagnostics check decoded response shape, not API/Git authenticity, atomic inventory, strict admission, historical cleanup or production/operator acceptance. Roll back only these local checks/tests; preserve saved evidence and stopped VM. Original Vault handoff direct sync remains pending outside checkout.

## 2026-10-03: explicitly null diagnostic list metadata (offline)

The fixed-profile reader now rejects an explicit `metadata: null` on Flux controller/source and Lakehouse workload lists rather than treating it like omitted metadata in older projected fixtures. Fake negative cases cover both Flux reads and both namespace reads; omitted metadata and previously valid envelopes still pass. `go test ./internal/cli -count=1` passed (769 tests). Dedicated Colima was read-only observed stopped; no VM start or API/SQL call, reconciliation, retained request replay, backup or volume change. This only tests offline response interpretation, not authenticated inventory, remote Git provenance, strict admission or production acceptance. Roll back the local list check and tests only; preserve stopped VM and saved evidence. Original Vault handoff sync pending outside checkout.

## 2026-10-03: explicitly blank list resource versions (offline)

The fixed-profile read-only doctor now rejects an explicitly empty or whitespace-only `metadata.resourceVersion` in controller, Flux-resource and namespace workload lists, rather than treating it like an omitted fixture field. A nonempty opaque version and legacy omitted fields remain compatible. Fake negative/positive cases across all three inventories, `go test ./internal/cli -count=1` (769 tests), `go vet ./...`, and Warehouse focused tests passed. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay or retained data/backup mutation. The reported version is not authenticated API identity or an atomic snapshot; Git provenance, strict admission and operator/production acceptance remain open. Roll back only the local list check and tests, preserving the VM and saved evidence. Original Vault handoff direct sync remains pending outside checkout.

## 2026-10-03: explicit Deployment progress in fixed-profile diagnostics (offline)

Two independent local-only checks require `Progressing=True` as well as `Available=True` and complete counters on the four fixed Flux controller Deployments and on Lakehouse Deployments. An absent Progressing condition previously passed; StatefulSets remain replica-based and the generic doctor is unchanged. Fake missing/healthy cases, `go test ./internal/cli -count=1` (768 tests), `go vet ./...` and `make test-local-warehouse-lifecycle` passed. Dedicated Colima was observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay, retained session or backup change. Reported condition consistency is not authenticated runtime/Git identity, smoke, strict publication admission or operator/production acceptance. Roll back only the two local diagnostics and their fixture/tests, preserving the stopped VM and saved evidence. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: competing source refs and other Flux API versions (offline)

Two independent fixed-profile doctor increments: the declared `main` GitRepository now rejects simultaneous `tag`, `semver`, `commit` or `name` selectors, which could otherwise override the branch; other queried Flux resources (OCIRepository, Bucket, HelmRepository, HelmChart and HelmRelease) reject explicit API versions different from the pinned CRDs (`source.toolkit.fluxcd.io/v1`, respectively `helm.toolkit.fluxcd.io/v2`). Absent item versions remain compatible with projected fixtures. Fake negative/positive tests cover each selector and kind without echoing untrusted revision/version; the generic doctor remains unchanged. `go test ./internal/cli -count=1` (766 tests), `go test ./...`, `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) passed. Dedicated Colima was read-only observed stopped; no VM start, live API/SQL, reconciliation, unknown-request replay, retained data or backup mutation. Reported configuration checks are not authenticated Git provenance, live rollout, smoke, strict publication gate or operator/production acceptance. Revert the diagnostic parser/checks and their tests only; preserve the stopped VM and retained evidence. Original Vault handoff direct sync remains pending outside this checkout.

## 2026-10-03: declared Flux versions and active-root revision (offline)

Milestone regression: `make verify-local-experiment`, `make verify-design-context` (89 documents), and `git diff --check` passed after both changes.

Two independent fixed-profile read-only checks: explicitly reported API versions of the declared GitRepository, root Kustomization and dev Kustomization must match their checked-in v1 manifests; absent versions remain compatible with existing projected fixtures. An **active** (not suspended) root must report a nonempty last-applied revision matching the reported source artifact; the intentionally suspended root retains its stale-revision allowance. Fake positive/negative cases and generic-profile isolation passed `go test ./internal/cli` (764 tests) and `go vet ./...`. Dedicated Colima was observed stopped; no VM start, actual API/SQL read, reconciliation, unknown-request replay, retained volume or backup mutation. Reported version/revision consistency does not authenticate the API, Git origin or actual rollout, and does not meet smoke, strict publication or operator/production acceptance. Roll back only these local reader/tests, preserving the stopped VM and saved evidence. Direct original Vault handoff sync remains pending outside checkout.

## 2026-10-03: item API versions in read-only inventories (offline)

Two independent local-only diagnostic checks reject a Flux controller Deployment or a Lakehouse Deployment/StatefulSet item that explicitly reports an API version other than `apps/v1`, despite otherwise healthy rollout fields. Absent item versions remain compatible with existing projected fixtures; `apps/v1` passes. The controller failure does not echo the untrusted version, and the Lakehouse failure remains namespace-scoped. Fake positive/negative cases, `go test ./internal/cli` (762 tests), `go vet ./...`, and `make verify-local-experiment` passed. Dedicated Colima was observed stopped and was not started. No live API/SQL read, reconciliation, unknown request replay, retained volume or backup change. Reported API versions do not authenticate Kubernetes, Git origin or actual readiness; strict publication, smoke and operator/production acceptance remain open. Roll back only these local readers/tests; preserve the VM and saved evidence. Direct original Vault handoff synchronization remains pending outside checkout.

## 2026-10-03: bounded DNS identities in local diagnostic inventories (offline)

Two independent fixed-profile read-only checks: Flux resource names now require nonempty DNS subdomain labels of at most 63 bytes each (total at most 253), while namespaces require a single DNS label of at most 63 bytes. Previously `a..b`, a 64-byte component, or a dotted namespace could pass the loose regex. Separately, namespace-scoped Lakehouse inventories reject malformed *unrelated* workload names rather than accepting them alongside healthy fixed workloads. Fake malformed/healthy fixtures and `go test ./internal/cli` (760 tests), `go vet ./...`, `make verify-local-experiment`, `make verify-design-context` (89 documents) and `git diff --check` passed. Dedicated Colima read-only status reported stopped; no live API/SQL, reconciliation, retained request replay, volume or backup change. These are response-shape checks, not API/Git authentication, atomic inventory, strict publication gate or operator/production acceptance. Revert only the local readers/tests to recover; leave saved evidence and stopped VM intact. Direct original Vault handoff synchronization remains pending outside checkout.

## 2026-10-03: fixed-profile readiness response framing (offline)

The local-only doctor now requires exactly `ok` or `ok\n` from the read-only Kubernetes `/readyz` request. Previously `strings.TrimSpace` accepted padded and extra-newline responses as readiness; the generic doctor retains that compatibility. Fake positive/negative cases, `go test ./internal/cli` (759 passed) and `go vet ./...` passed. Dedicated Colima was read-only observed stopped, with no live API, reconciliation, retained data or backup mutation. Exact returned text does not authenticate the endpoint, Git revision or real readiness, and does not meet strict publication or production acceptance. Revert the local-only check/test to roll back; preserve historical evidence and the VM. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: exact local API endpoint projection (offline)

The fixed local doctor no longer trims whitespace around the reported Kubernetes API URL before checking the loopback HTTPS endpoint. Whitespace-padded responses, including an appended newline, now degrade before subsequent API reads; exact loopback IPv4, IPv6 and localhost fixtures still pass. `go test ./internal/cli` (758 passed) and `go vet ./...` passed. Dedicated Colima was read-only observed stopped; no live API, reconciliation, retained data or backup change. This is a fake-response diagnostic boundary, not API authenticity, remote Git provenance or acceptance. Roll back only local scope validation/test changes; preserve the VM and historical evidence. Original Vault direct handoff sync remains pending outside checkout.

## 2026-10-03: bounded doctor child stdout (offline)

The doctor command runner now caps each child stdout at 1 MiB and discards stderr instead of buffering it; excess stdout fails with no partial result. An initial oversized-output regression exposed `bytes.Buffer.ReadFrom` bypassing an embedded writer's limit; the writer was changed to a private buffer and the regression passed. `go test ./internal/cli`, `go vet ./...` and `make verify-local-experiment` passed. Dedicated Colima was read-only observed stopped; no live API, reconciliation, retained data or backup changes. This bounds child output memory, not remote source authenticity, runtime readiness or production acceptance. Rollback only the runner/test change; preserve historical evidence. Original Vault handoff sync pending outside checkout.

## 2026-10-03: paused deployments in local diagnostics (offline)

Two independent fixed-profile checks now degrade an otherwise fully replicated Flux controller Deployment or Lakehouse Deployment when `spec.paused: true` is reported. An absent/false pause remains compatible; StatefulSets are not Deployments. Fake negative and healthy cases passed `go test ./internal/cli` (755 tests) and `go vet ./...`; the initial focused test used the first dev workload (a StatefulSet) as its replacement target and failed, then the targeted Deployment fixture passed. Dedicated Colima read-only status was stopped; no VM start, real API/SQL, reconciliation, retained request replay or backup/volume mutation. A reported pause is not remote Git authenticity or actual runtime readiness; strict publication, Git smoke, recovery and production/operator acceptance remain open. Roll back only the local diagnostic parsing/checks and tests, preserving retained evidence and stopped VM. Original Vault handoff direct sync remains pending outside this checkout.

## 2026-10-03: bounded local list envelopes (offline)

Two independent response-shape checks now degrade fixed-profile controller, Flux resource and namespace workload inventories with nonzero `metadata.remainingItemCount` (including negative contradictory counts), or with an explicit `apiVersion` other than `v1`. The previous `metadata.continue` and kind checks remain. Absent optional fields and explicit v1/zero count pass, matching kubectl's v1 List envelope; the generic Flux profile remains unchanged. The controller check stops before a source read, and diagnostics never echo these untrusted fields. Fake positive/negative cases passed `go test ./internal/cli` (753 tests). Dedicated Colima status was read-only observed stopped; no live API, reconcile, retained session, backup or volume mutation. This verifies the reader's conservative handling of reported fields, not authenticated API/Git identity, actual runtime convergence, strict publication gate or production/operator acceptance. Roll back only the local reader/test changes and preserve saved evidence and the stopped VM. Direct original Vault handoff sync remains pending outside this noninteractive checkout.

## 2026-10-03: bounded local Flux diagnostic identity (offline)

The fixed-profile doctor now degrades malformed resource kind/name/namespace without echoing those untrusted fields into the human-readable failure summary. A malformed API identity could previously place control characters or arbitrary text in a reported resource ID. Fake kind, name and namespace negatives and a healthy case passed `go test ./internal/cli` (749 tests); generic-profile behavior is unchanged. Dedicated Colima remained stopped; no live API, Flux reconciliation, retained data or backup mutation. This improves the local diagnostic only; it cannot authenticate the API, remote Git or a publication identity, nor satisfy runtime/production acceptance. Revert doctor/test changes only to recover, preserving saved evidence and the stopped VM. Original Vault handoff direct sync remains pending outside this noninteractive checkout.

## 2026-10-03: dev applied revision must match reported source (offline)

The fixed local doctor now compares the declared dev Kustomization's nonempty `status.lastAppliedRevision` with the declared GitRepository's `status.artifact.revision`. A Ready dev object that still reports an older applied revision (or no applied revision) no longer counts as reconciled to the current reported source. An isolated missing/older fake test passed after the check; the first package run caught a test-fixture mistake (the missing case retained the shared fixture field), then the corrected package suite passed (748 tests), as did `go vet ./...` and `make verify-local-experiment`. Generic doctor remains unchanged. Dedicated Colima was read-only observed stopped; no VM start, live API/SQL read, Flux reconcile, retained request replay, volume or backup mutation. Matching reported revisions do **not** authenticate remote Git/source identity, prove the running workload was applied from this checkout, or satisfy the strict publication gate and operator/production acceptance. Rollback: revert the local comparison/fixture/tests only, preserving stopped VM and saved evidence. Original Vault handoff direct sync remains pending outside this noninteractive checkout.

## 2026-10-03: declared source identity and active revision (offline)

Two independent fixed-profile read-only checks now reject a Ready `flux-system` GitRepository if its reported `spec.url` or `spec.secretRef.name` differs from the checked-in `gotk-sync.yaml` (including absent values), and if it is suspended or its artifact revision uses a ref other than the declared `main`. Diagnostics do not print either URL or credential reference. The generic doctor remains unchanged. Fake positive/negative cases: `go test ./internal/cli` (745 passed), `go vet ./...`, `git diff --check`, and `make verify-local-experiment` passed; the first focused run failed four assertions that incorrectly expected the generic doctor to treat a suspended root as healthy, corrected to test only that it does not enforce fixed declaration fields. Dedicated Colima read-only status: stopped; no VM start, live API/SQL reads, reconciliation, unknown-request replay or retained data/backup mutation. This checks reported fields and revision syntax, **not** remote origin authentication, SSH credential contents, revision publication identity, actual readiness, strict admission or operator/production acceptance. Roll back only these doctor checks and their tests; preserve retained evidence and the stopped VM. Original Vault handoff synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: declared source branch and dev wait

Two independent offline fixed-profile checks now reject drift from the repository declarations: the `flux-system` GitRepository must report `spec.ref.branch: main` (missing/other branch fails), and the dev Kustomization must report `spec.wait: true` (missing/false fails). Previously either could be Ready and pass the local doctor. The generic profile is unchanged. Fake negative/positive cases passed `go test ./internal/cli` (737 tests), `go vet ./...`, `git diff --check`, and `make verify-local-experiment`; the first focused run found two stale test replacements after updating the shared fixture and passed after correction. Dedicated Colima read-only status was stopped; no start, live API/SQL, reconciliation, retained request replay, volume or backup mutation. Reported fields do not authenticate remote Git, confirm live convergence, clear the historical strict gate, or establish operator/production acceptance. Roll back only these doctor checks/tests, retaining the stopped VM and saved evidence. Original Vault handoff direct synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: declared root and dev pruning

After the path increment, an independent fixed-profile offline check now rejects the repository-declared root or dev Kustomization when its reported `spec.prune` is absent or false despite `Ready=True`. Both declarations require `prune: true`; without it, deletions from Git need not remove obsolete resources. An isolated absent-prune test failed before repair; absent/false negative cases and healthy fake fixtures passed after repair (`go test ./internal/cli`: 732 tests; `go vet ./...`; `make verify-local-experiment`). The generic doctor profile is unchanged. This checks a reported field, not actual deletion, remote provenance, or runtime readiness. Dedicated Colima remained stopped; no VM, API/SQL, reconciliation, retained run or backup change. Revert only this diagnostic/test increment to roll back, retaining historical evidence and the stopped VM. Original Vault handoff direct sync remains pending outside this checkout.

## 2026-10-03: declared root and dev reconciliation paths

Two independent offline fixed-profile diagnostic checks now require the suspended root Kustomization's `spec.path` to be `./deploy/gitops/clusters/local-lite` and the dev Kustomization's to be `./deploy/gitops/dev`, matching their repository declarations. A Ready resource pointing at a different tree (or lacking a path) previously passed the read-only diagnostic. Dev and root negative tests each failed before their respective repair; empty, absent and wrong paths now degrade, while healthy fake fixtures and the generic profile remain compatible. `go test ./internal/cli` (731 tests), `go vet ./...`, `make verify-local-experiment` and `git diff --check` passed. Dedicated Colima read-only status was stopped; no VM start, live API or SQL read, reconciliation, retained request replay, backup or volume mutation. This compares reported configuration against declared paths, not remote Git authenticity, live rollout, strict publication gate or production/operator acceptance. Rollback: revert only the doctor path checks and their tests; keep saved evidence and stopped VM. Direct original Vault handoff synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: declared dev Kustomization is required

The local-only read-only doctor previously reported the suspended root as informational even if the repository-declared `Kustomization/flux-system/shirokuma-dev` was missing, or if it was Ready while referencing an unrelated source. It now requires exactly one dev Kustomization and its `GitRepository/flux-system/flux-system` source reference (omitted or matching source namespace), as declared by `deploy/gitops/clusters/local-lite/dev.yaml`. A fake negative test failed before the fix; missing/wrong-source/wrong-namespace negative and healthy positive cases pass afterward. Generic-profile behavior remains unchanged. `go test ./...`, `go vet ./...`, `make verify-local-experiment` and `make verify-design-context` passed. Dedicated Colima was read-only observed stopped; no VM start, real API/SQL read, reconciliation, request replay, retained data or backup change. This does not prove live readiness, Git provenance, smoke reconciliation, strict gate, or production acceptance. Roll back only doctor/test changes while preserving saved evidence and the stopped VM. Direct synchronization of the original Vault handoff remains pending outside the noninteractive checkout.

## 2026-10-03: suspended root current Ready failure

Separately, fixed-profile Flux controller/source and Lakehouse workload inventories now reject a reported outer list kind other than `List`; absent outer kind remains compatible with earlier fixtures, while explicit `List` passes. A malformed controller inventory stops before reading the Flux source. Fake wrong-kind and positive cases pass focused tests. This checks reported response shape, not Kubernetes API identity or authenticity.

The fixed local doctor now degrades an intentionally suspended root Kustomization if it explicitly reports `Ready=False` or `Ready=Unknown` for its current generation. Previously the suspended-root early exit hid those failures; stale Ready conditions and a current `Ready=True` remain informational. Offline fake positive/negative cases, `go test ./internal/cli` (725 tests), `go vet ./...`, `make verify-local-experiment`, and `git diff --check` passed; an initial focused test failed because its fixture replacement did not match, then passed after correcting the fixture. Dedicated Colima was read-only observed stopped; no start, live API/SQL, reconciliation, saved request replay, retained volume or backup mutation. Actual Flux/remote revision authenticity, strict publication gate and production/operator acceptance remain unverified. Roll back only the local doctor condition/list checks and tests, retaining the stopped VM and historical evidence. Original Vault handoff direct synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: suspended-root failures and unavailable-replica contradictions

Two independent offline fixed-profile doctor increments: a suspended root Kustomization no longer hides a current-generation `Stalled` or `Reconciling` condition other than `False`, while its intentionally stale Ready condition remains informational; separately, a fixed Flux controller or Lakehouse workload reporting nonzero `unavailableReplicas` cannot pass despite complete rollout counters. Absent or explicitly zero unavailable replicas remain valid. A new fake suspended-root case failed before the fix and passed afterward; `go test ./internal/cli` (723 tests), `go vet ./...`, `make verify-local-experiment`, and `git diff --check` passed after both changes. Dedicated Colima was observed stopped via read-only status; no start, API/SQL read, reconciliation, saved request replay, retained data or backup mutation. Runtime/source authenticity, strict gate and production/operator acceptance remain unverified. Rollback is limited to doctor code/tests, preserving retained evidence and the VM. Original Vault handoff synchronization remains pending outside the noninteractive checkout.

## 2026-10-03: offline reconciliation and unrelated inventory duplicates

Two independent local-only doctor fixes: a current-generation Flux `Stalled` or `Reconciling` condition other than explicitly `False` now degrades even alongside `Ready=True`; stale-generation conditions and explicit `False` do not override readiness. A namespace-scoped workload list containing duplicate **unrelated** kind/name identities (or an unnamed item) now degrades that namespace instead of silently trusting the fixed healthy workloads. Fake positive/negative cases and Go focused test/vet, local full regression, and design-context checks passed; the initial focused run caught a malformed new test fixture and passed after its repair. Dedicated Colima was read-only observed stopped and was not started. These checks do not prove live Flux readiness, API/remote provenance, strict publication gate or production/operator acceptance; no saved run, SQL, retained data, backup or VM change. Roll back only the doctor code/tests and preserve retained evidence. Original Vault handoff sync remains pending outside this noninteractive checkout.

## 2026-10-03: reported zero generation and out-of-scope workload inventory

Two independent offline local-doctor increments: an explicitly reported Flux `status.observedGeneration=0` is no longer mistaken for an omitted optional field, and a namespace-scoped Lakehouse workload list containing a foreign namespace or unexpected kind now degrades that namespace, even when the fixed workloads are otherwise ready. Absent optional Flux status generation and healthy fixed inventories remain valid; generic Flux checks are unchanged. Fake positive/negative tests: `go test ./internal/cli` (719 passed), `go vet ./...`, `git diff --check`, `make verify-local-experiment` passed. Dedicated Colima read-only status returned not running; no VM start, live API/SQL read, saved request replay, reconciliation, retained data or backup mutation. This does not establish live health, remote provenance, strict publication gate or production/operator acceptance. Revert only the local doctor code/tests for rollback; preserve retained evidence and the stopped VM. Original Vault handoff is outside this noninteractive checkout; direct synchronization remains pending.

## 2026-10-03: unresolved rollout failure and contradictory Flux generation

Two independent local-only diagnostic increments, using fake Kubernetes inventories while the dedicated Colima profile was observed stopped: `ReplicaFailure=Unknown` (or an empty status) now degrades a fixed Flux controller or Lakehouse workload despite complete replicas and `Available=True`; explicit `ReplicaFailure=False` is permitted. Separately, if a Flux resource reports top-level `status.observedGeneration`, it must equal its metadata generation even if a `Ready=True` condition claims the current generation. An absent top-level field remains permitted. The generic Flux profile retains its previous contract. Focused `go test ./internal/cli` (715 tests), `go test ./...` (715 tests), `go vet ./...`, and `make verify-local-experiment` passed with positive and negative fake cases. No live API/SQL read, reconcile, saved run, backup, or VM mutation occurred. These offline checks do not establish actual rollout health, source authenticity, strict admission or production acceptance. Roll back only the local doctor code/tests; preserve the stopped VM and retained evidence. Original Vault handoff synchronization is pending outside this noninteractive checkout.

## 2026-10-03: fixed Deployment availability must be explicit

Two independent offline increments require `Available=True` for the four fixed Flux controller Deployments and the two fixed Lakehouse Deployments, even when replica counters are complete. StatefulSets retain their replica-based check (they do not expose Deployment availability conditions). A missing condition is now degraded; a conflicting non-True condition remains degraded. Fake missing-condition and healthy-inventory tests pass; `go test ./internal/cli` (709 tests) and `go vet ./...` pass. Dedicated Colima was observed stopped and not started. No live API/SQL read, reconciliation, saved run or backup mutation. This does not establish runtime health, remote source authenticity, strict supply-chain admission or production acceptance. Rollback is limited to the local doctor and fake fixtures; preserve the stopped VM and retained evidence. Direct Vault handoff synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: conservative conditions and Flux inventory kind

Two independent offline diagnostic increments: a fixed Flux controller or Lakehouse workload with complete replica counters but `Available=Unknown` or `Progressing=Unknown` is degraded rather than treated as healthy. The local Flux resource inventory also rejects a kind outside the seven kinds explicitly requested from kubectl; generic-profile behavior is unchanged. Fake negative and healthy cases pass (`go test ./internal/cli`, `go test ./...`, `go vet ./...`, `make verify-local-experiment`). Read-only Colima status reported stopped; no live cluster/SQL read, reconcile, saved run or backup change. These checks do not prove remote revision provenance, runtime health or production acceptance. Roll back only the local doctor and its tests; preserve VM and retained evidence. Direct Vault handoff synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: current Ready conflicts and empty URL suffixes

Two offline increments in the read-only local doctor: a non-suspended Flux resource with both `Ready=True` and `Ready=False` at its current generation can no longer pass by list order; a terminating suspended root is explicitly reported as terminating (it was already degraded). The fixed loopback API endpoint now rejects empty `?` and `#` suffixes before downstream reads. A duplicate fixed controller name remains rejected by the existing missing-controller guard; a regression test verifies no source read follows. Fake positive/negative tests pass (`go test ./...`: 704, `go vet ./...`, `make verify-local-experiment`); local verification result is recorded in Milestones. Dedicated Colima was observed stopped and not started. No live API, Flux reconcile, saved run or backup mutation. These checks do not establish remote revision authenticity, actual workload/SQL readiness, strict supply-chain admission, or production acceptance. Roll back only these local diagnostic/test changes, preserving retained data. Direct Vault handoff synchronization remains pending outside this noninteractive checkout.

## 2026-10-03: contradictory Available condition is not readiness

Second independent offline increment: local doctor rejects fixed Flux controller and Lakehouse workload `Available=False` even with apparently complete replica counters. Fake negative cases and normal healthy fixtures pass (`go test ./internal/cli`: 698 tests); `make verify-local-experiment` exits successfully. The generic Flux doctor contract is unchanged. Dedicated Colima remains stopped; no runtime observation, reconciler write, retained volume or backup change. This is not proof of actual service readiness or production acceptance. Roll back only the diagnostic and tests; Vault source handoff direct sync remains pending outside the permitted checkout.

## 2026-10-03: reject ambiguous non-root Flux inventory

Dedicated Colima is stopped; no live API reads or reconciliation. The local-only read-only doctor now rejects missing kind/name/namespace, zero generation, and duplicate kind/namespace/name for **all** Flux resources, including non-root resources; previously a zero-generation Ready condition or duplicated non-root Kustomization could be reported as reconciled. Fake positive/negative tests and generic-profile compatibility test pass (`go test ./internal/cli`, `go test ./...`, `go vet ./...`). This verifies only the offline diagnostic, not runtime authenticity, Git revision provenance, strict admission, or WP acceptance. No retained data or backups changed. Roll back only the local diagnostic and its tests; preserve the stopped VM and saved evidence. Vault handoff direct sync remains pending because the original is outside this noninteractive checkout's permitted filesystem.

## 2026-10-03: fixed local diagnostic identity and port bounds

停止中の専用Colimaは起動せず、固定local doctorのloopback HTTPS endpointに数値port `1..65535`を要求し、0/範囲外/文字列は後続read前に拒否。別の独立増分として固定Flux controller4件は名前だけでなく`Deployment/flux-system`を要求する。fakeの正負例、`go test ./internal/cli`、`go test ./...`、`go vet ./...`、`make verify-local-experiment`は成功。汎用doctorの契約は変更しない。live API・remote revision・strict gate・Git reconciliationの受入は未検証。保持VM/backup/保存runの変更はなく、rollbackは局所doctor差分のみを戻す。非対話CLIからcheckout外Vault原本への直接同期は権限外のため保留。

## 2026-10-03: local doctor inventory and API endpoint fail-closed checks

同日の独立した追加点：固定Flux controller一覧とFlux source/Kustomization一覧でも`items`欠損・`metadata.continue`ありをdegradedとして拒否する。controller一覧が不完全ならsource APIを呼ばない。fakeの途中ページ/正常一覧を検証し、汎用doctorは従来契約のまま。実Fluxのlive再観測はしていない。Vault同期保留・rollback/残余は下記と同じ。

固定local profileのread-only doctorで、Lakehouseのnamespace別workload listが`items`欠損、`metadata.continue`あり、または固定kind/name/namespaceの重複を含むときは、最初のReady資源を採用してhealthyとしない。別namespaceの正常な診断は保持し、pagination tokenをsummaryへ出さない。local-scopeのloopback HTTPS API URLはport必須、path/query/fragmentを許さず、異常時は後続API readに進まない。fake応答の負例と正例、Go全package test/vet、`make verify-local-experiment`を実行。専用Colimaは停止中でlive診断なし。これはAPI真正性、Flux revisionのprovenance、SQL/保存状態、strict supply-chain gate、Git smoke/再bootstrapや本番受入を証明しない。戻す場合はdoctorの検証差分のみ戻し、保持VM/backupや既存保存結果を変更しない。Vault直接同期はこの非対話sessionのcheckout外権限がないため保留。

## 2026-10-02: fixed Flux source identity in read-only local doctor

`shirokuma doctor --profile local-experiment`は従来4 controller・Ready条件を見たが、固定root `GitRepository/flux-system/flux-system`の不在・artifact revision欠損・root `Kustomization`の別source参照でも、別のGitRepositoryがあればFluxをinformationalと報告し得た。fake応答の負例4件で修正前誤通過を再現。local profileに限り、root GitRepositoryとroot Kustomizationがそれぞれ1件、rootのsourceRefが同namespaceの固定GitRepository、GitRepositoryが現generationでReady・非削除、artifact revisionが有界の`ref@sha1:<40 lowercase hex>`形式であることを追加検査する。欠損/重複/別namespace/不正revision/別参照は`degraded`、generic doctorの旧契約は維持。root suspendedのinformational文言は「意図・source revisionのprovenance未認証」を明示し、local profile指定だけを意図の証明にしない。revisionは**形式**のみでremoteへの独立認証・このcheckoutのHEADとの一致ではない。`doctor`はread-onlyでreconcile/applyなし。

停止中の専用Colimaをprofile設定SHA記録後に一時起動し、固定contextでcompiled Go doctorをread-only実行。初回は起動後のsource-controllerと子Kustomization Ready未達で全体`degraded`、保存したまま再読した2回目は全体`healthy`・Flux `informational`（root suspended）。別API readで固定GitRepositoryは現generation Ready、artifact revision形式一致、root Kustomization sourceRef一致・suspended。取得したrevisionはprivate記録内でもhashだけ保持。専用VM・保持データ/backupは削除せず、GitHubへ書き込みなし。private記録は`tmp/local-experiment/gitops-doctor-source-checks/20261002/`。これは限定diagnosticであり、Git経由のsmoke reconcile、現checkoutのremote publication identity、strict supply-chain gate、再bootstrap/teardownの実証ではない。WP `local-partial`を維持する。

通常停止後は専用profileがStoppedへ戻りhost設定SHA不変。起動時TCP 53 forward競合、停止時k3s killallの`/var/lib/cni/` busyおよび保持Pod volume unmountの出力あり。保持PVC/backupの**内容不変は独立照合していない**。本診断を戻す場合は新しいdoctor validationだけを以前のローカルrevisionへ戻し、保存run/VM/backupに復旧目的の削除や再作成を行わない。

修正後Go CLI focused 674 testsと`go vet ./...`成功。最終全回帰`make verify-local-experiment`は121 suites/3820実行＝3812 PASS＋8 skip、90.39秒、private log SHA256 `a919c55e87b4c09117e93bf4fc3d9a4c7eb33563bce1a3ca4de1e301e252829a`。`make verify-design-context`、secret scan、diff check成功。実VMの診断実行後に追加したgenerationゼロ負例2件はfake応答・全回帰でのみ確認し、実VMを再起動していない。

## Summary

OpenTofuでlocal Kubernetesの前提条件を構築し、固定したFlux v2リリースを公式bootstrap経路で導入して、Gitからdev desired stateを継続的にreconcileできるようにする。

旧Argo CD前提のWork Packageは[[07_ADR/ADR-0018_Use_Flux_v2_as_the_GitOps_reconciler|ADR-0018]]によりsupersededされた。`doc_id`と依存関係は維持する。

## Context

このWork PackageはLevel L0「Agentic GitOps土台」の一部です。ShirokumaのAgentic OSS Data Cloudを段階的に構築するため、Issue化してCodex/Agentに割り当て可能な粒度にしています。

## Depends on

- `WP-L0-PLAT-001`

## Scope

- OpenTofuとFlux bootstrapの責務境界を実装する。
- `fluxcd/flux2`の承認済み安定版をversion固定する。初期local-lab版は`v2.9.2`とする。
- `source-controller`、`kustomize-controller`、`helm-controller`、`notification-controller`を`flux-system`へ導入する。
- repositoryのcluster pathにFlux自己管理manifest、`GitRepository`、root `Kustomization`を配置する。
- dev overlayとGit-reconciled smoke resourceをroot `Kustomization`から管理する。
- controller imageのdigest、署名、SBOM、provenance、ARM64 manifest、脆弱性scan evidenceを検証する。
- ADR-0019に従い、strict gateを維持しながらexact digest/CVE/package/versionへ限定した30日以内のlocal-lab例外を検証する。
- non-interactiveなbootstrap/status/teardown Make targetとbounded evidenceを整備する。

## Non-scope

- Image Automation controllersとsource-watcher
- Flux Operator
- community Flux Helm chartを標準bootstrapにすること
- 複数cluster、multi-tenant sharding、production HA
- Secret値またはDeploy Key private materialのrepository保存
- direct `kubectl apply`を正常なGitOps変更経路として扱うこと

## Deliverables

- 固定されたFlux CLI/distribution versionとcontroller image inventory
- OpenTofuによるcluster bootstrap prerequisites
- `flux bootstrap git`または`flux bootstrap github`のnon-interactive wrapper
- `clusters/local-lite/flux-system`のgenerated bootstrap manifests
- dev root `Kustomization`とsmoke manifest
- `make gitops-bootstrap`、`make gitops-status`、`make gitops-reconcile`、`make gitops-teardown`
- supply-chain verification evidenceとfailed reconciliation fixture
- 更新されたRunbook、CLI doctor contract、rollback手順

## Acceptance Criteria

- [ ] `flux version --client`がrepositoryで固定したversionと一致する。
- [ ] `flux check --pre`がbootstrap前に成功する。
- [ ] Fluxの標準4controllerが`flux-system`でAvailableになる。
- [ ] `flux check`が成功する。
- [ ] cluster entrypointの`GitRepository`が`Ready=True`で承認済みrevisionを報告する。
- [ ] rootとdevの`Kustomization`が`Ready=True`になる。
- [ ] Git経由のsmoke変更がdirect `kubectl apply`なしでclusterへ反映される。
- [ ] controller imagesの署名、SBOM、provenance、ARM64を検証し、strict High/Critical gateまたはADR-0019のlocal-lab限定High例外を評価できる。Criticalは常に拒否する。
- [ ] `shirokuma doctor`がFlux controllers、Source、Kustomizationの状態をJSON/Markdownで報告する。
- [ ] teardown後に同じ手順で再bootstrapし、同一のdesired stateへ収束できる。
- [ ] Secret値、token、Deploy Key private materialがevidence、Issue、PR、logへ含まれない。

## Suggested Labels

`level:l0`, `area:gitops`, `agent-ready`, `risk:normal`

## Suggested Agent Prompt

```text
You are working on Shirokuma WP WP-L0-GITOPS-001: OpenTofu and Flux bootstrap.
Follow ADR-0018 and the repository AGENTS.md.
Pin the approved fluxcd/flux2 stable release; do not track main or runtime latest.
Keep OpenTofu responsible for cluster prerequisites and use the official Flux bootstrap path for self-management.
Implement GitRepository and Kustomization readiness, Git-reconciled smoke evidence, supply-chain verification, bounded diagnostics, teardown, and rollback.
Do not introduce Image Automation, Flux Operator, the community Helm chart, or direct kubectl mutation.
```

## Definition of Done

- Code、declarative manifests、tests、docs、Runbook、rollbackが同一PRに含まれる。
- Acceptance CriteriaをCIまたはbounded local evidenceで検証している。
- GitHub Issueの依存、Scope、Non-scope、Evidence、Rollbackが実装と一致する。
- `issue-lint`とrepositoryのpre-PR verificationが成功する。

## Migration note

2026-07-12以前のIssue、PR、Pawprint、fixtureにあるArgo CD blockerは履歴証跡として保持する。現在の実装契約と新しいevidenceはFlux resource modelを使用する。

## Current implementation evidence

- Flux v2.9.2の標準4controllerについて公式linux/arm64 platform digest、signed OCI index、SLSA provenance v1、upstream SPDX SBOM subjectを確定した。
- 初回Trivy 0.72.0、DB timestamp `2026-07-13T19:09:56.237113526Z`ではHighがsource=2、kustomize=0、helm=2、notification=1、Criticalは全て0だった。この5件のdecisionは2026-08-13に失効した。
- CycloneDX 1.7 SBOM、Trivy JSON、署名・provenance summaryを`security/evidence/flux-v2.9.2/`へretained evidenceとして保存した。
- Issue #150とfinal corrective OWNER comment `5290345820`は、同じ4 digestをTrivy 0.72.0、DB `2026-08-14T01:10:44.597550261Z`で再scanしたsource=7、kustomize=6、helm=6、notification=6（合計High=25、Critical=0）の完全一致tupleだけを2026-09-13までlocal-lab限定で承認した。
- 同じdecisionはgenerated manifest（SHA-256 `ed307189fd1f9e49819a50843bb6f3c9257fe6d4d8359d1950b38207c26c3854`）のexact pathにある`KSV-0041` 1件と`KSV-0046` 8件を`2026-09-13T00:00:00Z`直前まで承認する。retained config report SHA-256は`00e87fef815ac9a99401f2a450e71c47555fd991ec6d2cfc7313e1f0dbe3bd7a`、raw Trivy metadata SHA-256は`a82d05e076fd54c9bd2e57fd1be00891a2384a3f618e9d72037bfd940a5406ea`であり、all-severity scanはunfiltered、blocking scanだけがignoreを使用する。
- strict profileは引き続き不適合を返す。新規/欠落High、Critical、stale exception、digest/advisory/severity/package/installed/fixed version、scan hash、OWNER record、expiryのdriftはbootstrapをfail closedにする。自動更新は禁止する。
- Flux v2.9.4は20 Highまで改善し公式SLSA v1/SPDX証跡もあるが、digest、CRD/RBAC、deploymentとlive runtimeを変えるため別のmigration decisionとする。
