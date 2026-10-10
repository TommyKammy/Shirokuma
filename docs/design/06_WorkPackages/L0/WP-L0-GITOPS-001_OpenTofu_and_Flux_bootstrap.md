---
project: Shirokuma
doc_id: "WP-L0-GITOPS-001"
title: "WP-L0-GITOPS-001 OpenTofu and Flux bootstrap"
status: local-partial
created: 2026-07-05
updated: 2026-10-10
version: "0.194"
area: "workpackage"
tags: [shirokuma, workpackage, l0, gitops, flux]
---

# WP-L0-GITOPS-001 OpenTofu and Flux bootstrap

## 2026-10-10: independent kustomize source/module patch; parent review pending

`gate-d1i5mq08` / `L0_KUSTOMIZE_SOURCE_REPAIR` implements
`tools/local_experiment/kustomize_source_repair.py` and24 Python regressions.
Exactly two public pinned-revision HTTPS GETs succeeded once, no retries:
revision `060e0d8dbc257a11cf49b9e626b63284dc385870`, root go.mod14403 bytes
SHA256 `14864acbf087dedecc12c143cece6599fd4c10f34e2e1528e5c26002d210deb5`,
go.sum71029 bytes SHA256
`6bb93c10a466b034c0be459dc01130f38a3590c831461973fecb98f0bbe9cf7f`.
HTTPS-observed identity is not signed tree/publisher verification. Fetch reservations
and outcome receipts are exclusive0600; unknown/failed fetches cannot replay.
Fixed URLs/no redirects/no proxy credentials,2MiB response ceiling and30second
whole-call timeout guard; no source archives, clone or dependency retrieval.

Actual retrieved-byte validation produces only two exact indirect require-line
edits: x/crypto0.53.0→0.55.0 and grpc1.81.1→1.83.2. Original go.sum remains
byte-identical; no new sums or module-graph resolution. Patch SHA256
`2897a87b12ad275cceb83e17b7dd35a9bd50ae697c0df6f7369b96ddbbfe5fad`.
Exclusive patch/patched module/provenance plus start/finish receipts are under
`tmp/local-experiment/kustomize-source-repair/plan-xagjdb5n/`. Validator rederives
exact patch, source hashes, receipt bindings and all unresolved statuses; it rejects
extra edits, fabricated success, claim inflation, missing provenance and unsafe files.
Python tests cover malformed/duplicate lines, wrong identity/hash, truncation,
oversize/timeouts/redirects, unknown no-replay, symlinks/hardlinks/permissions,
budgets and immutable writes. Initial fixture path/newline failures were corrected,
not interpreted as source/test success; private verification preserves their summary.

Only this new scope was measured:156224 charged bytes including final receipts,
free2416820051968 bytes. Parent-dated retained builds+unknown scope16571121664
plus full64MiB new reserve fits unchanged16GiB; free17GiB remains required.
No access to `tmp/rv/mis`, retained build mutation/deletion, threshold/counter reset,
Go/compiler/module download/test operation, image/runtime/VM/registry/publication,
pin/security-ledger/approval/admission change or nested runner. Lab state is only
the dated parent snapshot, not a fresh worker live observation. The unknown original
source operation remains held/no replay; independent patch repair does not reopen it.

Schema1 test-plan is `ready_for_parent_review`, not execution authority. Parent must
independently check worker terminal state/HEAD, retained hashes/unknown identity,
both fetch outcomes and measured budgets/patch before selecting a distinct graph/
sum/build capability. pcre2 HIGH1, installed HIGHs, missing replacement sums/graph,
upstream tests, binary/image tests, advisory freshness, publisher/admission/full L0
remain unresolved; no checkbox changed. Repository-only rollback; retain original
unknown operation/all failures/private evidence. Vault untouched/outside approved
access, sync pending. No later batch or runtime successor authorized by this result.

## 2026-10-10: minimum offline index seed/executor; parent execution review pending

`gate-ysazsqh6` / `L0_FLUX_MINIMAL_TEST_REPAIR`: implemented
`tools/local_experiment/flux_minimal_tests.py`, with zero-call default and no CLI.
Read-only inspection verifies pinned source archive/patched module hashes, Go1.26.6
archive and all15034 installed toolchain files (rejecting extras), selected module
zip Go h1 checksums/ziphash and retained go.sum-bound graph metadata. Conservative
platform-union import closure:40 packages/six modules; only selected package payloads,
verified archive/ziphash and offline graph .mod files enter the new seed. Unsupported
embed/cgo, missing input, checksum mismatch or unknown prior outcome holds.

Fresh charges: old builds16265342976, remaining914526208 under unchanged16GiB.
Seed25886720/source200704 bytes per unit; compile128MiB/temp128MiB/receipts4MiB/
inspection reserve32MiB give332271616 per unit,664543232 for both,249982976 margin.
These are prospective ceilings, not measured Go peaks. Cache/temp/aggregate guards
are sampled accounting, not filesystem quotas; parent must review overshoot risk
and resource sufficiency, and retain any capacity failure without replay/deletion.
Pipe output is bounded before writing. Existing sources/caches/failures stay immutable.

Two exclusive short private scopes `tmp/rv/mis` and `tmp/rv/mid`, fixed offline argv,
120s Go timeout/600s wall, independent HEAD/plan/unit/deadline-bound parent checks,
fresh inspection, no inherited environment, owned process-group termination and
terminal-group observation precede receipts. Any existing reservation prevents
replay; unknown/failed prior unit inhibits the other. Selector review also fixed
the prior `Test_writeLine` omission; eight/seven disjoint top-level tests now cover15.

55 focused Python tests and design-context/diff checks pass, including mocked
executor calls (no Go process),
closure cycles, integrity/argv/budget tampering, unknown/no-replay, bounds, wall,
output limits and owned termination. All19 parent baseline retained hashes match.
Private evidence and schema1 `ready_for_parent_review` navigation receipt:
`tmp/local-experiment/minimal-test-repair/plan-1hw1_hi3/`. Parent independently
reviews actual code/closure/integrity/session/retention/routing/counters before
at most two explicitly selected disjoint units; readiness is not execution authority.
No Go invocation/test/build/download/refetch/VM/image/runtime/registry/pin/ledger/
approval/admission/runner/successor operation. Stopped profiles remain parent-dated,
not freshly observed here. Full upstream/signature/publisher/admission/L0 unresolved.
Rollback repository changes only; retain all private/original evidence and counters.
Vault untouched/outside approved access; sync pending. No later batch authorized.

## 2026-10-09: small-unit planner implemented; capacity hold, no test execution

`gate-vpbadha7` / `L0_FLUX_TEST_CAPACITY_PLAN`: typed read-only planner/validator
and focused Python regressions, not a third binary attempt or interrupted-test
replay. Matching original receipt/output hashes distinguish old root success
(482/15), known-terminated signature (19/2) and final root (16/2), and completed
Go1.26.6 API (12/1); partial passes never complete a terminated operation.
Final root starts cache/error/helm only, not index. Propose two fixed disjoint
index groups, minimal212992-byte source skeleton, isolated short writable scopes,
offline/local toolchain environment,120s Go timeout/600s parent wall deadline.

**Hold:** old retained charged8137981952+8127361024 leaves914526208 bytes under
unchanged combined16GiB. Measured whole-module seed copy upper2173394944 alone
exceeds this; each proposed bounded unit totals3800997888 bytes (compile1GiB,
temp512MiB, receipts16MiB). Whole seed is not a proved minimum module closure;
cache/temp ceilings are proposals, not measured future peaks. Free>2249GiB does
not waive retention. Historical final/API cache/temp measured read-only, no old
cache reuse/writes, cleanup or limit/counter reset. Parent must prove a materially
smaller offline seed/resource plan before considering a fixed single-unit mission.

Detailed proposal/evidence/focused verification:
`tmp/local-experiment/test-capacity-plan/plan-nu7t5m6c/`; current
`detailed-plan-v3.json`, immutable original hashes in `evidence.json`, small
owned0600 `test-plan.json`. See `tools/local_experiment/AUTONOMOUS_RUNNER.md`
for exact scope, units, budgets, independent review and recovery requirements.
No Go invocation/build/download, source refetch, runtime/image execution, VM start,
registry/pin/ledger/approval/admission change, runner or successor. Parent baseline
records stopped profiles; no fresh runtime observation in this repository worker.
25 planner regressions pass; parent independently repeats fixed tests and reviews
terminal/diff/retention before any selection. Hold is not full gate acceptance;
upstream/signature/publisher/admission/L0 remain open, checkboxes unchanged.
Focused capacity10/design-context/diff-check also pass; all19 selected baseline
hashes match in `retained-check.json`, without altering retained inputs.
Rollback repository changes only, retain all private/original data. Vault sync pending.

## 2026-10-09: repository capacity observation repair; parent pilot review pending

`gate-_g0u5c6u` / `L0_SUPERVISION_CAPACITY_REPAIR`: implemented typed read-only
`capacity.observe`. Owned0700 root/owned regular entries, FD-relative no-follow
traversal, sum max(logical,allocated); unsafe/denied input fails closed. Hard guard
used>=8GiB/free<16GiB and bulk used<7GiB/free>=17GiB are separate booleans.
Post-worker build parent uses hard observation rather than bulk guard; write
callers and bounded256MiB–1GiB inspection reserves retain original thresholds.
API pilot guard now checks before operation launch and while its owned group runs.

Worker's fresh observations: old scopes8137981952 and8127361024 bytes, both hard
false/bulk false, free>2250GiB; independent parent-style accounting matches.
These current allocated-size snapshots do not rewrite historical capacity claims.
All19 selected retained hash bindings and full stopped-profile list match restart
baseline. No old-scope writes, counter resets, deletion, source/controller builds,
Go tests, downloads, image/runtime execution or ledger/pin/security changes.
OAuth-only routing preflight passed, not independent billing proof. Private logs,
observations and typed restart plan reside in
`tmp/local-experiment/safe-restart/restart-hr2zj9uf/`.
Focused capacity17/build19/refresh12/restart9/runner26 tests pass (83 total),
design-context14 tests/validator, secret scan and diff checks pass. Full local
verifier deliberately not run: its broader Go/runtime work exceeds this mission.
Two isolated fixture setup errors are retained in diagnostic notes, not erased.

Independent terminal/session/retention/budget review and separate API-only pilot
remain pending; ready proposal does not authorize worker execution. Full upstream
tests/publisher/admission/L0 acceptance remain false; Oct8 scans remain historical.
Rollback repository repair only, retain all original artifacts/failures/counters.
Vault left untouched/outside approved access; sync pending. No later batch scheduled.

## 2026-10-09: owner-selected safe repository repair and conditional API pilot

Next owner-requested safe continuation selects L0_KUSTOMIZE_OS_REPAIR, one45min
Sol repo repair of safe ARM64 pcre2 APK/index dependency/version/hash/provenance/
signature validation. Two bounded public Alpine3.23/main/aarch64 reads (index4MiB,
exact selected APK8MiB/30s each), new64MiB within combined retained16GiB/free17GiB.
Read retained image APK keys only as digest-bound local trust anchors; unsupported
signature proof holds. Python regressions, no install/scripts/Go/compiler/layer/
image/VM/runtime operations; parent signature/closure/input/terminal/capacity
review before successor. Preserve unknown mis group hold and all data/counters;
compare current content hashes, not changing filesystem allocation. Source graph
verified remains distinct from OS mitigation/publisher/admission/full L0. Starts
520/600 before launch/remaining80, deadline2026-10-11T01:30Z unchanged.

2026-10-10 independent kustomize milestone: gate-d1i5mq08 completed pinned two-
line source patch/provenance validator (24 Python tests). Parent verified two
HTTPS module fetches against exact candidate revision and separately fetched
pinned API module files. Checksum-verified x/crypto0.55.0/grpc1.83.2 ZIP/.mod
inputs add four go.sum lines; first offline graph known exit1 remains retained
(missing longrunning metadata). Changed second/final attempt retrieved168 .mod
files only with existing go.sum h1 anchors, bounded public HTTPS/no credentials/
retries; graph-5arqvixh exit0. Parent-result-review independently verifies3503
edges/168 hashes/root fixed versions,19 retained hashes/stopped profiles and
current unknown mis content snapshot unchanged. Original source operation stays
unknown/no replay; no fabricated group receipt, cleanup, compiler/upstream tests,
binary/image scan or runtime pin changes. Not complete go.sum tidy/build/advisory
freshness/pcre2/publisher/admission/L0 acceptance. All original failures retained;
repository-only rollback, preserve evidence/data. Starts520/600, remaining80,
deadline2026-10-11T01:30Z unchanged, no new model calls during graph repair.

2026-10-10 safe restart changes controller rather than replaying unknown source
index operation: one45minute L0_KUSTOMIZE_SOURCE_REPAIR for candidatev1.9.6 exact
source revision060e0d8dbc257a11cf49b9e626b63284dc385870. Retained Oct8 HIGH5:
pcre2 one, x/crypto one, grpc three. Source module patch targets0.55.0/1.83.2
with functional provenance/patch validation and Python regressions. Two pinned
go.mod/go.sum public reads only, private64MiB new scope within combined16GiB;
no Go/test/compiler/module downloads/archives/image/runtime changes. Unknown mis
hold/records/identity stay unchanged, no cleanup or counter reset. Source patch
does not complete graph/go.sum/build/advisory freshness, pcre2 repair or installed
mitigation; parent review needed for new graph/build capability. Repository-only
rollback; preserve all fetched receipts/source/patch evidence. Starts519/600,
remaining81 before launch, approved deadline2026-10-11T01:30Z unchanged.

Subsequent planning worker `gate-vpbadha7` at6a9a0c4 produced typed fixed
index-state/index-digest proposals and25 passing Python regressions. Parent
independently confirmed server succeeded/idle, remeasured validated v3 plan,
unchanged19 retained hashes and all profiles Stopped; separate receipt
`tmp/local-experiment/test-capacity-plan/plan-nu7t5m6c/parent-independent-review.json`.
Execution remains held: combined16GiB leaves914526208 bytes, whole offline
module-seed copy needs2173394944 before compile/temp. No Go invocation/test/
build/download or unknown/interrupted replay. Minimum package-specific offline
dependency seed and fitting budgets must be separately derived and verified;
no deletion, ceiling increase, old-cache writes or third binary attempt.
Usage518/600, remaining82, original deadline unchanged. Planning acceptance
does not authorize execution or complete full upstream/publisher/admission/L0.

Parent outcome: separate `tmp/local-experiment/safe-restart-review/review-mv27dels/`
completed terminal/retention/stopped-profile/capacity review and fixed Go1.26.6
API pilot. `tmp/rv/api-w8dk1t4k` passed12 tests/subtests and1 package, exit0;
terminal receipt2026-10-09T09:10:16Z records output SHA256. Old restart
SUPERVISOR_PROBE_FAILED remains immutable (systemPython3.9 annotation failure,
fixed by deferred annotations and launch-interpreter tests). Original build
capacity failures remain retained; both builds now independently observed below
hard8GiB but above bulk7GiB, so old additional writes still inhibited. No new
model call/worker replay, cumulative517/600 with83 remaining/original deadline.
API-only evidence does not complete interrupted root/signature tests, full
upstream suite, publisher/admission or L0 acceptance. VM/pins/data unchanged.

Owner asked to execute recommended safe restart. Select one45minute repo-only
capacity/supervision mission, not a third source binary rebuild. Introduce typed
read-only logical/allocated usage observation separating conservative7GiB bulk
write guard from8GiB hard limit/free16GiB. Preserve thresholds, old capacity
HELD summaries and all retained data/counters. Parent independently tests contract
and unchanged artifacts/profiles/terminal sessions before any successor.

Only a reviewed typed ready plan allows one new narrow upstream API-module pilot:
minimum source copy validated against the pinned source snapshot, archived Go1.26.6
checksum/executable verified before execution, old toolchain read-only, new private
HOME/Go caches/short TMPDIR under tmp/rv. Fixed go test api module argv, no worker-
proposed commands, old caches untouched. This is API-only verification never
started under the final Go1.26.6 attempt, not replay of interrupted15package/root/
signature tests or a new binary rebuild. Separate pilot maximum60minutes and
two persistent reservations; enclosing parent maximum120minutes/original week.
No test count0 accepted as success, no inference of full upstream completion.

Before launch starts516/600, remaining84, original deadline2026-10-09T23:31:54Z,
all profiles Stopped and checkout clean. No registry/runtime/source/pin/ledger/
approval/exception/admission changes, new spending, data deletion or publication.
Old scan timestamps remain historical, not a freshness renewal. Parent review,
worker launch and actual pilot outcome must be recorded separately. Repository-
only rollback; preserve failures/source/archives/cache/backups/PV/PVC/disk identities.

## 2026-10-08: parent-verified local source-controller full-image scan milestone

`image-qv4_7qqh` assembled a local archive from exact candidate child5f2e5cd1 and
the patched Go1.26.6 binary899cfcbc. No execution/import/load/push occurred. Local
image digest is `sha256:4b83002260e14b92ea5e4da0d9d40d32086900f7c1fc4098c46e3fac01084262`;
archive SHA2560bdac040. Metadata explicitly marks unsigned Shirokuma local patched
provenance, not an upstream release/signature. Full-image Trivy includes OS17
Alpine3.23.6 packages and Go317 packages with stdlibv1.26.6; **HIGH0/CRITICAL0,
MEDIUM3/LOW2/UNKNOWN1**. Full findings and independent Syft SBOM remain private.

Original parent `LOCAL_IMAGE_SCAN_UNVERIFIED` is preserved: the scanner's
stdlib Version was `v1.26.6`, while the validator expected `1.26.6`. Regression
fix accepts only those exact equivalents, rejecting wrong/RC/missing versions,
missing OS or mismatched binary scope. Parent independently reobserved completed
command dispositions/output hashes, binary/build/DB/time/config attribution and
local archive digest without retrieval/assembly/scan replay. Separate resolution:
`tmp/local-experiment/flux-local-image/image-qv4_7qqh/parent-resolution.json`.
Current pins/ledger unchanged, all profiles Stopped, no model starts (516/600).

This proves one derived archive's narrow scan evidence, not runtime functionality,
new-toolchain test completion, publisher verification or admission/L0 acceptance.
Tests remain capacity-held; two binary execution attempts exhausted, no third
scheduled. Other controllers/source/enforcement remain open. No runtime successor
or active invocation after reobservation. Next capability must be a distinct
evidence-reviewed test-capacity/other-controller approach; no counter reset.
Image4 focused tests/design-context/secret/diff pass; broader failures retained.
Recovery is repository-only rollback, preserving all archive/layers/cache/source/
failure records and retained disks/backups. No auto-cleanup or registry publication.

## 2026-10-08: parent binary review and separate offline image-archive stage

Parent independently reconciles exact gate-ayqdekwa server/idle/checkpoint/result,
clean checkout and binary ELF/hash899cfcbc. Go1.26.6/binary/SBOM/scan hashes bind
HIGH0/CRITICAL0 binary-only result with MEDIUM2/LOW2/UNKNOWN1 retained. New toolchain
unit tests are incomplete; original parent capacity HELD summary remains intact.
Two binary repair executions exhausted, no third compile/test attempt. Starts516/600,
remaining84; existing ledger/pins unchanged, all profiles Stopped, no L0 acceptance.

Next distinct parent-owned zero-model stage: `flux_local_image.py` appends only
the verified binary at usr/local/bin/source-controller to the already observed
immutable ARM64 candidate image child, saves an archive locally and corrects
labels to unsigned Shirokuma experimental attribution. Preserve upstream source
provenance separately, never claim upstream signature covers the derived image.
Collect complete archive Trivy/Syft evidence (OS and Go packages), exact local
image/archive/config/binary identities and fresh scanner/DB times. At most2
image-stage reservations, max120minutes/8GiB per mission/free16GiB, same original
week. No compile/test replay, image execution/load/import, daemon/VM/container
startup, external registry write, existing pin/ledger replacement, approval/
exception/admission activation or runtime successor. Local assembly is newly
selected under standing local development authority, not retroactively part of
the old no-assembly binary mission. Tests/publisher/other-controller repair and
runtime/source/admission gates remain held. Keep all old/new evidence/caches/
backups; repository-only rollback, no auto-cleanup. Actual image scan pending.

## 2026-10-08: final Go1.26.6 binary repair; HIGH0, tests/capacity held (gate-ayqdekwa)

Second/final same-approach `L0_FLUX_LOCAL_BUILD` execution retained under
`tmp/local-experiment/flux-local-build/build-don5ufj3/`. Official public Go release
metadata identifies stable1.26.6; darwin/arm64 archive64,772,321 bytes SHA256
2dc95ce4675829f2df0e86b28bcef3283635902062a5f0580ca659bf570f3204 verified before
private extraction. Explicit toolchain reports1.26.6, GOTOOLCHAIN=local/private
HOME/Go caches/temp, no global install/shared/old cache writes or model fallback.
Reconstructed pinned commit456fa323/tree8c7afc98 from the retained archive and
hash-checked original patched go.mod/go.sum; six-module/transitive patch unchanged.
Source tag not refetched, `.git`/old Go caches not copied. An enclosing-repository
git-apply no-op was caught by checksums; receipt/unpatched module files retained,
no replay, only verified module files copied as the corrective approach.

One owned0600 single-link static Linux/ARM64 ELF, never executed, SHA256
899cfcbcf2c9f3813a917a2b12f50f799bc81dc5dff5e99b9bd1f1423828cf36. Go/Syft
confirm stdlib1.26.6 and all six prior fixed module versions. New binary-derived
CycloneDX318 components/fresh Trivy0.72.0 scan317 packages at07:08:31UTC:
**HIGH0/CRITICAL0/MEDIUM2/LOW2/UNKNOWN1**. Exact five non-HIGH findings preserved;
eight previous stdlib HIGH absent only in this binary scan, not full-image clearance.
Old DB/hash-verified metadata copied read-only into new cache, no retrieval replay;
DB Updated2026-10-07T07:38:55UTC/downloaded2026-10-08T06:45:38UTC, no ignores.
An initial copy capacity reservation refused before transfer while compilation
held temporary files; after known build exit0 the initial copy fit. Hold retained.

Module verification/list passed. Native unit run with vet off/p1/512MiB reserve
hit the7.5GiB guard and was terminated-15 at07:10:50UTC after16 test/subtest passes
in two packages. **Incomplete, not upstream test acceptance**; no replay, API tests
not started. Signature-unit failure from attempt1 not repeated; envtest/OCI/race
remain excluded. Current new allocation approximately7.569GiB; old reservation
7.598GiB remains charged despite a lower subsequent allocated-block observation,
combined approximately15.167GiB below16GiB/free>2200GiB. No cleanup/quota reset,
old7GiB parent hold unchanged. Read-only process check found no mission compiler/
test process. Further build/test/cache writes held; this approach is exhausted.

Archive helper verifies checksum/size and rejects links/traversal/special/duplicate
members, private directories/nonexecuted source modes; focused tooling18 pass.
Final design-context/secret-scan/diff checks pass, not gate acceptance.
Broad local verifier exit2, four AF_UNIX path-length errors in warehouse115 under
private TMPDIR preserved, no unrelated fix/workaround. Full provenance, receipts,
hashes, lower findings, original exact candidate image/base/publisher gaps and other
controller HIGH5/5/9 dependencies in
[`flux_local_build.md`](../../../../tools/local_experiment/flux_local_build.md).
All profiles freshly Stopped; ledger/pins unchanged, no runtime/image assembly/
execution/startup/source/apply/unsuspend/admission/riskDecision/policy/approval
changes, retained disks/PV/PVC/backups untouched. Parent independently verifies
new metadata/scope; no publisher/image/L0/operator acceptance or checkbox edits.
Stop/no_safe_work, no later batch scheduled. Original Vault untouched/sync pending.
Rollback repository changes only; preserve all source/patch/caches/binaries/failures.

## 2026-10-08: read-only build reobservation and final same-approach toolchain repair

Parent independently reconciles gate-xezx_z6k exact terminal server, no_safe_work
checkpoint/result and clean descendant HEAD. ELF SHA2569dc840ee, source commit456fa323/
tree8c7afc98 and patch/go.mod/go.sum/SBOM/scan hashes reproduce; exact8 HIGH findings
are stdlib1.26.5 and record stable1.26.6 fix. Current logical/allocated maximum
8,157,929,472 bytes is below the first mission hard8GiB but above its conservative
7GiB bulk-write guard. Keep original parent HELD summary/failures and all bytes;
no further old-scope build/toolchain writes or cleanup. Binary evidence remains
partial, no image/operator acceptance. VM/all profiles Stopped, ledger/pins unchanged.

Second/final execution for this approach is an isolated Go1.26.6 build with the
same pinned source/dependency patch, minimal copied source and new private caches,
verified toolchain identity/checksum and fresh binary-derived SBOM/scan/tests.
Do not replay terminated signature tests, old source tag retrieval or old unknown
operations. Old inputs read-only. New mission8GiB cap plus retained prior8GiB
means bounded combined16GiB; counters are not reset and free16GiB remains required.
Same1worker/120minutes/OAuth/600/original-week limits; starts515/600 before launch.
No image assembly/execution, pin/ledger changes, risk approval, VM/container start,
policy activation or runtime successor. Record actual launch/outcome separately.

## 2026-10-08: isolated patched ARM64 binary; stdlib/capacity hold (gate-xezx_z6k)

`L0_FLUX_LOCAL_BUILD`: single upstream v1.9.6 tag fetch resolved before patching
to commit456fa323ef6a4b977f220cf2c60298bf193497ba,
tree8c7afc98517458261d6713f8c28668c5e2d14107. Unpatched tar/source, full module
patch/build list/checksums and command receipts preserved privately under
`tmp/local-experiment/flux-local-build/build-aoa39q7w/`. Installed Go1.26.5,
isolated private HOME/Go caches/temp; no global install/toolchain download.
One owned0600 nonexecuted static Linux/ARM64 source-controller ELF, 94,109,858
bytes, SHA2569dc840eea23a3c5ef6c40f07f4fbd5e930770de2d4068688b28f04f5bd2ec596.
Go metadata/Syft confirm go-git5.19.2/Fulcio1.8.6/xcrypto0.55/xmod0.40/
grpc1.83.2/oras2.6.2; original API/source behavior untouched.

Upstream host-native Helm/reconciliation/helper tests:15 tested packages,
482 test/subtest passes; API12 passes, module verification passed. Signature
unit attempt terminated-15 on monitor temporary-file race and is not replayed;
controller envtest/OCI-image-producing integrations/race tests not exercised.
Syft binary-only CycloneDX318 components, fresh Trivy0.72.0 offline SBOM scan
06:47:50UTC detects317 Go packages: **HIGH8/CRITICAL0/MEDIUM2/LOW2/UNKNOWN1**.
DB Updated2026-10-07T07:38:55.515026687Z, downloaded06:45:38.694613Z today.
Eight HIGH affect stdlib1.26.5 (DB lists stable1.26.6, unbuilt/unverified),
xcrypto0.55 retains MEDIUM/UNKNOWN; **not vulnerability clearance or image readiness**.
Empty direct rootfs-file report and unsupported SBOM flag failure preserved,
not counted as successful scans. Binary→SBOM→scan hashes and full findings bound.

Generated7.592GiB/free>2220GiB; DB download exited0 while conservative7GiB
monitor raised at exit, separately reconciled read-only with DB metadata/hash;
original unknown receipt unchanged, no retrieval replay. Monitor fixed for vanished
compiler files, allocated-block counting and guard/terminal-outcome separation.
Bounded inspection alone used256MiB reserve below8GiB; further compile/toolchain
retrieval held because safe headroom is insufficient without prohibited cleanup.
Parent's independent7GiB guard also remains held, not relaxed by worker evidence.

Fresh before/after all profiles Stopped/byte-identical; ledger/pins/runtime/old
holds/counters, retained disks/PV/PVC/backups unchanged. No OCI assembly/execution,
VM/container startup, source/apply/unsuspend, risk approval/exception/activation,
external publication or nested runner. Existing candidate image/base declarations
and other-controller HIGH5/5/9 dependencies recorded without inventing base pins;
full image/base/publisher/source/admission/live L0/operator acceptance remain open.
Eleven checkboxes unchanged; stop/no_safe_work, no successor batch authorization.
Original Vault untouched/sync pending. Repository-only rollback; preserve all
mission caches/source/binary/failures. Detailed reproduction/identity/remaining
findings: [`flux_local_build.md`](../../../../tools/local_experiment/flux_local_build.md).
Repository focused12/design-context/secret/diff checks pass. Broad
`make verify-local-experiment` exits2 with four warehouse lifecycle115 AF_UNIX
path-length errors in required isolated TMPDIR; failure retained/no workaround,
no unrelated repair. Final allocation approximately7.598GiB, within8GiB.

## 2026-10-08: upstream candidate remains rejected; isolate source-controller rebuild

Parent candidate collection completed: Flux v2.9.6 immutable ARM64 S/K/H/N
HIGH9/5/5/9, CRITICAL0, 28 occurrences versus current pin94. Full index-to-child,
fresh scans/SBOMs/DB observations in
`tmp/local-experiment/flux-pin-candidate/candidate-hn94027w/`. No publisher proof,
admission=false, all profiles Stopped, original ledger/pins unchanged. No applied
mitigation. Four additional read-only tag lists show no newer stable same-series
controller patches, receipts retained; no tag resolution/scan replay.

New bounded approach is isolated source-controller v1.9.6 source rebuild only.
Current source candidate findings require fixed go-git5.19.2, Fulcio1.8.6,
x/crypto0.55.0, x/mod0.40.0, grpc1.83.2 and oras-go2.6.2 or proven stable fixed
successors. Resolve upstream source tag once to full commit/tree, preserve patch,
compile/test Linux/ARM64 with private module/toolchain caches, retain build metadata,
SBOM/fresh binary scan and failures. One model session/120minutes, unchanged
budget/week/OAuth and old counters; new build approach at most two reservations.
Parent rechecks candidate/current ledger/terminal holds, then ELF/Go metadata
independently. Binary evidence is not a fixed full OCI image or functional/runtime
acceptance. Other three controllers/base packages and publisher/source/admission
gates remain open; no approval/exception, pin replacement, image execution/assembly,
VM/container startup, activation, source fetch/apply/unsuspend. Model launch and
actual build results recorded separately. Repository-only rollback; preserve all
source snapshots, failed results, caches and retained backups/PV/PVC/disk identities.

## 2026-10-08: parent refresh verification and isolated candidate investigation

Parent independently verified gate-3f5odnk2 terminal succeeded/idle/non-active,
valid no_safe_work and clean descendant HEAD66c8cb55. Refreshed4 reports/DB
snapshot reproduce HIGH21/36/18/19, CRITICAL0, full hashes and ARM64/Linux identity;
all profiles Stopped and old ledger unchanged. Fresh evidence does not grant
admission; parent classification FRESH_SCAN_EVIDENCE_ONLY. Starts514/600, remaining86.

Anonymous GET release metadata/install.yaml observed stable Flux2 v2.9.6,
S/K/H/N controller tags v1.9.6/v1.9.6/v1.6.5/v1.9.4. Recorded once privately in
`tmp/local-experiment/flux-pin-discovery/discovery-yp11cspa/`; no signed publisher
claim or pin change. The next parent-owned read-only investigation uses
`flux_pin_candidate.py` to resolve each tag once, retain exact index/unique
ARM64-child binding, then collect fresh full scans/SBOMs for those child digests.
Installed-tool isolation, no-replay receipts and disk guards reuse the verified
refresh collector. Max120minutes, at most2 persistent reservations, same original
week; zero new model requests. No build/image execution, VM/container startup,
policy activation, current pin/ledger overwrite or risk exception.

Candidate scan/fixed-package/publisher evidence are still pending. Even zero
HIGH/CRITICAL cannot prove publisher acceptance or authorize runtime changes.
Recovery is repository-only rollback of the candidate tool; preserve all old/new
private evidence, retained disks/backups and original failure records. No new
resident component, PV/PVC or retained storage change; generated data bounded8GiB.

## 2026-10-08: fresh exact-digest Flux scans, strict rejection retained (gate-3f5odnk2)

`L0_FLUX_EVIDENCE_REFRESH`: collected four full fresh Trivy0.72.0 reports for
only the unchanged retained Linux/ARM64 digests. Public DB UpdatedAt
2026-10-07T07:38:55.515026687Z, downloaded2026-10-08T06:15:35.041092Z.
Actual scan times06:15:41/06:15:49/06:15:58/06:16:08UTC; HIGH21/36/18/19,
CRITICAL0. Total94 HIGH occurrences/42 IDs (32 new IDs, all10 old IDs remain),
not a vulnerability-free result. Crane configs prove observed ARM64/Linux;
four new Syft SBOMs are separate. Publisher proof remains historical/unrefreshed:
signed parent indexes are outside these four authorized image inputs.

Added exclusive/no-replay installed-tool collector and focused regression tests.
Remote-only scan/SBOM collection uses empty private Docker config and isolated
HOME/cache/temp; no credential inspection, image code execution or daemon access.
Private mission evidence is
`tmp/local-experiment/flux-refresh/refresh-9k8bpz7j/`, including scanner-version,
full scans/SBOMs/configs, command receipts, DB hashes and refresh-observations.
Initial post-scan mode check stopped on Trivy's explicit0644 DB/cache metadata;
only these newly generated files were chmod0600, preserved bytes unchanged,
and the collector now narrowly normalizes generated modes/rejects unsafe links.
All checked evidence owned0600 under0700 mission directory. No retrieval replay.
Collection completed06:16:11.972430UTC, approximately1.343GiB generated and
2272GiB free, within8GiB/16GiB limits; new cache/evidence retained, no cleanup.

All profiles freshly observed Stopped before/after; VM stays stopped. Ledger hash,
pins/manifests/security evidence unchanged; old holds/results/counters, null risk
decision and strict HIGH rejection preserved. No approvals/exceptions, admission/
policy activation, source transport/fetch/apply/unsuspend or runtime mutation.
Current HIGH remediation needs separately scoped fixed-package immutable pins;
parent must independently validate refresh output and publisher binding. Source/
enforcement/applied provenance and full L0/operator acceptance remain open.
No checkbox/gate pass or runtime successor. Original Vault untouched, sync pending.

Focused12 refresh tests/design-context/diff pass. Broad verifier first failed an
AgentHouse test because inherited scanner umask077 made its unsafe fixture private
(Docker mocked, loopback read refused, no real startup). Separate child umask022
verification passed that stage/policy166, then warehouse lifecycle115 had4 errors:
AF_UNIX path too long in mission-local temp. Both logs/results preserved; no
external-temp workaround or unrelated code changes. Historical11policy failures/
2errors are not erased by this observation; broad verification is not green.

Detailed times/hashes/dependencies and repository-only rollback:
[`flux_refresh_scan.md`](../../../../tools/local_experiment/flux_refresh_scan.md).
No retained storage/PV/PVC/backup/export change or new resident component.

## 2026-10-08: exact retained Flux HIGH/freshness diagnosis (gate-5d3ur67c)

`L0_FLUX_FINDINGS_DIAGNOSIS`, flux_findings/diagnose: added commit-bound,
read-only `flux_findings.diagnose`, four regression tests and
[`FLUX_FINDINGS.md`](../../../../tools/local_experiment/FLUX_FINDINGS.md).
The document enumerates all 10 IDs/25 HIGH occurrences, exact installed/fixed
versions, controller/target scope, advisory-conditional exposure and uncertainties.
Full retained records/multiplicity and ledger/SBOM/scan/publisher hashes bind
candidate0486612d. S/K/H/N HIGH7/6/6/6, CRITICAL0 unchanged. DB age55.201 days,
scan age55.012 days at06:00:32UTC; hashes are identity, not refreshed advisories.

Fresh live51 probes05:59:12/06:00:32UTC pass complete readiness/inhibition/retention.
Read-only Flux namespace Service/Endpoints/Ingress/NetworkPolicy inspection:
three ClusterIP services (S artifacts, N events/webhook), no namespace Ingress,
metrics/health listeners and permissive configured egress/webhook/metrics rules.
No packet/enforcement/public-exposure proof or vulnerable-call reachability claim.
Suspension does not stop controller listeners. Doctor remains degraded.
No startup/runtime writes/approvals/exception/risk change/fetch/rescan/activation.

Concrete dependency is separately scoped current evidence refresh for all four
exact ARM64 digests; if findings persist, new immutable Flux pins must prove the
documented fixed packages/Go toolchain and matching SBOM/publisher/scan evidence.
No replacement digest is established; package FixedVersion is not a Flux release
recommendation. Refresh/upgrade is prohibited in this mission. Strict HIGH reject,
riskDecision null, admission/activation/full L0 false. Old exhausted scopes held.

Final fresh live51 check06:05:08UTC passes; initial/final UIDs, non-Lease specs,
full Flux/PV/PVC and namespace network objects unchanged. Diagnosis regenerated
against final observation. Further refresh/upgrade is held outside this mission;
checkpoint stops/no_safe_work, not full gate completion.

Focused41 tests pass (findings/contract/inventory/continuation/supervisor). Initial
module-style invocation had three fixture import errors; corrected tests-path
invocation passes without code changes to historical tests. Broad verifier still
policy166 failures11/errors2. Go CLI/internal, design-context and diff checks pass.
Prior stops/failures and eleven checkboxes unchanged.
Private diagnosis/network/live/doctor/test/stability records:
`tmp/local-experiment/autonomous-runner/gate-5d3ur67c/attempt-001/findings/`.
Proposal001 in `tmp/local-experiment/gate-continuation/series-te0f4f3q/` stays
flux_findings; parent selects only after independent terminal/live/budget review.
No nested launcher executed. OAuth-only explicit Sol check passed, not billing
proof. Original Vault untouched, sync pending.

Recovery: repository-only rollback of this module/tests/document/ledger notes,
not private evidence or prior failures. Small private JSON/log disk impact only;
no resident component, retained disk/volume/PV/PVC/backup/export change. Leave
safe runtime for parent probe; parent stops VM only after worker idle/non-active.
Never restore app counts or unsuspend Flux/Jobs automatically. No gate acceptance.

## 2026-10-08: complete non-activating admission contract (gate-azr7paf4)

`L0_DEPENDENCY_CONTINUATION`, local_admission/repository_repair: extended
`admission_contract` with `build_complete`/`validate_complete`, retaining the
original four-controller API. Full inhibited candidate, regenerated transitive
inventory and complete diagnosis are bound by exact records/SHA256. All nine
image occurrences/eight distinct references retain commit-bound ARM64 ledger,
SBOM/publisher/scan paths/hashes, exact publisher/scanner metadata and findings.
Five Secret identities/19 repository-unavailable references remain explicit,
not live absence. Validation regenerates everything; occurrence/finding/reference
omission, platform/publisher/artifact changes, approval or activation reject even
when the caller recomputes hashes. Fresh inhibition drift also rejects.

Private real contract uses candidate `0486612d0671c01c16d40ce445e39788d86de69b`.
Flux strict HIGH7/6/6/6 and CRITICAL0 remain blocking; five non-Flux occurrences
retain HIGH0/CRITICAL0. Risk decision missing/null, admission/activation/runtime
inputs/gate false, fetched/applied commits null, transport unselected. No fresh
crypto verification, fetched image bytes or advisory rescan claimed.

Fresh live51 final 05:41:20 UTC passes Node/Lease/four pinned controller readiness,
Flux8/Jobs3 suspension and retained app owners0. Initial/final UIDs/non-Lease specs
and full Flux/PV/PVC objects unchanged; contract revalidates against final probe.
Docker14 names are system/Flux scoped, not a whole-runtime census. Fresh doctor
remains degraded. No VM/node start, runtime resource write, source fetch/redirection/
unsuspension, transport selection, policy activation or writer start.

Admission22 (three new complete-contract tests), render11 and Go CLI/internal
tests pass. Broad local verifier preserves policy166 failures11/errors2. Initial
`check-design-context` target did not exist; failure retained, corrected
`verify-design-context` and final diff check pass. Prior stops/failures and all
eleven checkboxes unchanged.

Private evidence: `tmp/local-experiment/autonomous-runner/gate-azr7paf4/attempt-001/complete-contract/`
(complete-contract, contract-summary, final-stability, probes, doctor and test logs).
Proposal006 in `tmp/local-experiment/gate-continuation/series-fpveebm3/` requests
only read-only diagnosis of exact HIGH findings/freshness for a concrete risk-
disposition or pinned-image remediation dependency. Parent must review/select
within its persistent approach/budget limits; this does not authorize a successor
or risk approval. Full L0/operator acceptance remains open. Single OAuth/explicit
Sol routing passed, not independent billing proof. Original Vault untouched,
sync pending; supplied launch script not executed, no nested runner.

Recovery: rollback only this repository API/tests/docs/Make target, not retained
private evidence. Small private JSON/render-staging/log disk impact, no resident
component/storage/backup/export changes. Preserve disks/volumes/PV/PVC identities
and failure records. Leave safe runtime for independent parent probe; parent stops
VM only after idle/non-active worker confirmation. Never restore application
replicas or unsuspend sources/Jobs automatically.

## 2026-10-08: complete nine-occurrence admission diagnosis (gate-vq8p7nl2)

`L0_DEPENDENCY_CONTINUATION`, local_admission/diagnose: added reproducible
`tools/local_experiment/inventory_admission.py`, documentation and focused tests.
Regenerated the existing inhibited candidate/render inventory at commit
`0486612d0671c01c16d40ce445e39788d86de69b` against fresh scoped observations.
All nine occurrences (eight distinct references) match exact ARM64 ledger rows;
ledger and SBOM/publisher/scan bytes match candidate-commit blobs. Five non-Flux
occurrences are Polaris, Polaris-admin, PostgreSQL, Polaris in Iceberg bootstrap,
and SeaweedFS, not five distinct images. Their retained SBOM/publisher/strict scan
checks pass with HIGH0/CRITICAL0; Flux retains strict HIGH7/6/6/6, CRITICAL0.
Exact finding records/multiplicity, hashes, publisher identities and scan dates
are retained. PostgreSQL enters the canonical retained-bundle validator branch;
other selected versions do not. Neither branch selection nor record passes are
fresh signature verification, fetched image bytes or a current advisory rescan.

Five distinct Secret identities have 19 repository-unavailable reference
occurrences. Their workload scope remains explicit; no Secret values read and
no live absence inferred. Risk decision remains missing/null, transport unselected,
fetched/applied commits null; admission/activation/runtime-input/gate flags false.
Missing/ambiguous exact ledger identities remain gaps, inventory tampering rejects,
unavailable/changed/non-commit artifacts fail closed. No risk waiver or approval.

Fresh complete live51 probe passes, latest 05:33:13 UTC: Node/fresh Lease, four
pinned ready controllers/Pods, Flux8/Jobs3 suspended and retained app owners0.
Initial/final UIDs/non-Lease specs/full Flux/PV/PVC objects unchanged. Fresh doctor
05:33:16 UTC remains degraded (exit0 is not health acceptance). No VM/node start,
runtime resource write, fetch, transport choice, apply, binding activation or writer
start; safe inhibited runtime remains available for independent parent probe.

Focused admission19 (including three new inventory tests)/render11/source8,
Go CLI/internal, design-context and diff checks pass. Broad
local verifier still fails policy166 with 11 failures/2 errors; prior failures and
all eleven checkboxes remain unchanged. Initial missing constant failed before
runtime inspection; a later diagnostic rejected an aged initial Lease snapshot.
Both failures recorded privately; fresh read-only probe used, no request replay.

Private evidence: `tmp/local-experiment/autonomous-runner/gate-vq8p7nl2/attempt-001/inventory-admission/`,
especially `final-diagnosis.json`, `final-stability.json`, `probe-03.json`, doctor
and test logs. Proposal005 in `tmp/local-experiment/gate-continuation/series-fpveebm3/`
requests only a non-activating complete-inventory evidence contract extension,
binding all occurrences/findings/runtime-reference gaps, risk decision still null.
Parent independently reviews/selects the next mission; diagnostic completion is
not L0/operator acceptance or permission for another batch. Single OAuth/explicit
Sol preflight passed, not billing proof; original Vault untouched/sync pending.

Recovery: rollback only this repository module/tests/docs/Make target. Small
private JSON/text/log disk impact; no retained storage/backup/export or resident
component changes. Preserve disks/volumes/PV/PVC identities and original failures.
Leave safe runtime for parent probe; parent stops VM only after worker idle/non-active.
Never restore app replicas or unsuspend sources/Jobs automatically.

## 2026-10-08: commit-bound transitive inhibited inventory (gate-r_2f0fyd)

`L0_DEPENDENCY_CONTINUATION`, source_identity/repository_repair: implemented
`tools/local_experiment/render_inventory.py`, documentation, focused Make target
and regressions. Only existing commit `0486612d0671c01c16d40ce445e39788d86de69b`
objects feed local plugin-disabled `kubectl kustomize`; exact blob/SHA256 inputs,
renderer executable SHA256, canonical render/resource and inhibited projection
hashes bind the whole candidate. All seven configured paths, 33 transitive files
(including generated Java source), 57 resources, nine workloads and nine image
occurrences are inventoried. Root replacements/postBuild constants are included.
Four controller images match candidate pins; every image is digest-pinned, not
proof of fetched bytes, publisher/scan/ARM64 acceptance for the five app images.

Unknown/external configs, generators, missing/nonregular inputs, repository escapes,
cycles, unsupported substitutions/nested Flux and unsafe workload surfaces remain
gaps. A blocked child does not conceal independent available inventories. No
incomplete closure reaches the renderer. Every standard normal/init/ephemeral
container occurrence is covered. Duplicate keys/resources and pin/scope drift
reject. Committed root is still branch-only/unsuspended and unsafe to apply;
candidate projections remain suspended/replicas0 except the four controllers1,
source commit-pinned with unselected transport. Inventory returns hashes, not an
applyable manifest. This repository inventory is complete within the stated
supported dialect, **not** full Flux execution or runtime-input completeness.

Five distinct referenced Secret identities are absent from repository renders:
Flux transport, Polaris PostgreSQL/root, SeaweedFS application and storage
credentials. Their contents were not inspected. Image bytes/runtime-generated
StatefulSet PVCs remain separate inputs. `bind_admission` revalidates original
contract/fresh candidate before attaching inventory/hash; exact strict HIGH7/6/6/6,
CRITICAL0 and publisher/scan outcomes remain unchanged. Local risk decision is
missing/null; fetched/applied provenance null, admission/activation/gate false.

Fresh live51 observations on already-running dedicated runtime pass, latest
05:26:40 UTC: Node/Lease, four pinned ready controllers/Pods, Flux8/Jobs3 suspended,
retained app owners0 and original PV/PVC identities/specs. Initial/final UID sets,
non-Lease specs and complete Flux/PV/PVC objects unchanged; final bound inventory
revalidates. Doctor remains degraded. No startup, runtime write, fetch, transport
choice, apply/redirect/unsuspend/new resource/policy binding or writer start.

Render11/source8/contract9/supervisor15/continuation5/runner22/bears11 and Go CLI/
internal tests pass. Broad `make PYTHON=tools/local_experiment/llm/.venv/bin/python
verify-local-experiment` retains policy166 failures11/errors2. Initial default
Python lacked PyYAML; existing project venv succeeds, no install. Intermediate
inventory conservatively flagged Flux `force` (a reconciliation, not render field);
that incomplete record remains retained and superseded by final inventory, not
retroactively changed. Prior failures/stops and all eleven checkboxes unchanged.

Private evidence: `tmp/local-experiment/autonomous-runner/gate-r_2f0fyd/attempt-001/render-inventory/`,
especially `final-inventory.json`, `final-bound-contract.json`, `final-stability.json`
and `probe-04.json`. Proposal004 in `tmp/local-experiment/gate-continuation/series-fpveebm3/`
requests only local_admission/diagnose of the complete nine-image inventory,
candidate-commit ARM64/publisher/scan evidence and unavailable inputs. Parent must
independently select/review; no risk approval or activation inferred. OAuth-only
explicit Sol routing passed, not billing proof; original Vault untouched/sync pending.

Recovery: repository-only rollback of implementation/tests/docs/Make target. Small
temporary committed-text/private JSON/log disk impact; no resident component or
retained-storage/backup/export change. Preserve retained disks, volumes/PV/PVCs,
backups and failures. Leave inhibited runtime for parent probe/idle-worker cleanup;
never restore app counts or unsuspend sources/Jobs automatically.

## 2026-10-08: non-activating local admission evidence contract (gate-5djsi8d4)

`L0_DEPENDENCY_CONTINUATION`, local_admission/repository_repair: implemented
`tools/local_experiment/admission_contract.py`, contract documentation and a focused
Make target. Builder/validator bind the full inhibited candidate at commit
`0486612d0671c01c16d40ce445e39788d86de69b`, including commit/tree/paths and private
UID/spec guards, to four ARM64 image references, ledger hash and each retained
SBOM/publisher/scan path/hash. All input bytes must match candidate-commit blobs;
changed artifacts/ledger or contract edits reject. This is JSON evidence, not an
applyable manifest, risk approval or activation request.

Exact blocking records retain target/class/type, package/version/fix/path metadata
and multiplicity, including CRITICAL findings if present. Real retained evidence
reproduces HIGH=7/6/6/6, CRITICAL=0, strict scans blocked. Publisher record pass is
not fresh cryptographic proof; August scans are not a fresh advisory rescan.
Local risk decision and transitive render inventory remain explicitly missing/null;
admission/gate/activation always false. No expired exception renewal, invented
approval, transport choice, fetched/applied provenance or enforcement claim.

Fresh complete live51 probes on the already-running lab pass, latest 05:15:03 UTC:
Node/fresh Lease, four existing digest-pinned ready controllers/Pods, Flux8/Jobs3
suspended, retained app owners0 and original PV/PVC identities/specs. Initial/final
UID scope and non-Lease specs match; complete Flux/PV/PVC objects unchanged.
Contract revalidates against final observations. Fourteen Docker containers have
system/Flux names only, not a whole-runtime census. No VM/node startup or runtime
write, new resource, fetch/apply/redirect/unsuspend/policy change/writer start.
Fresh doctor remains degraded for intentional inhibition.

Contract8/diagnosis7/source8/supervisor15/continuation5/runner22/bears11, full Go
CLI/internal tests, design-context, secret scan and diff check pass. Initial wrong
supervisor/continuation patterns discovered zero tests and failed; logs retained,
correct filenames rerun successfully, no zero-test pass counted. Fresh
`make verify-local-experiment` still fails policy166 with 11 failures/2 errors;
prior security5 failures and safety/transport stops remain unaltered evidence.

Private contract, hashes/findings, three live probes, stability/doctor/test logs:
`tmp/local-experiment/autonomous-runner/gate-5djsi8d4/attempt-001/admission-contract/`.
Typed functional-change proposal:
`tmp/local-experiment/gate-continuation/series-fpveebm3/proposal-003.json`.
Next proposed concrete L0 dependency is source_identity/repository_repair:
commit-bound transitive rendered inputs and complete workload/image inventory,
still repository-only, with external/unavailable inputs explicit and no runtime
activation. Parent independently reviews/selects the next mission. Risk review,
transport/fetched-byte identity and scoped enforcement/applied provenance remain
open. All eleven checkboxes/full L0/operator acceptance unchanged. Single OAuth/
explicit Sol preflight passed, not independent billing proof; original Vault
untouched and direct sync pending. No nested runner or additional model request.

Recovery: rollback only repository implementation/tests/docs/Make target; no
runtime fields to restore. Small private JSON/log disk impact only, no resident
component or retained storage/export/backup change. Preserve all retained disks,
volumes/PV/PVC identities, backups and failures. Leave safe inhibited runtime for
parent probe; parent normally stops VM only after worker idle/non-active. Never
restore app counts or unsuspend sources/Jobs automatically.

## 2026-10-08: read-only local admission diagnosis (gate-n0xgt8ew)

`L0_DEPENDENCY_CONTINUATION`, local_admission/diagnose: evaluated the existing
inhibited candidate at full commit `0486612d0671c01c16d40ce445e39788d86de69b`
against fresh complete runtime observations and existing strict image validators.
`tools/local_experiment/admission_diagnosis.py` reproduces this read-only check;
its result always leaves admission/gate acceptance false. Exact candidate equality
rejects owner/spec/inhibition drift; ledger/image mismatch and changed artifact
hashes fail closed. There is no activation CLI or runtime writer.

All four image pins match the ARM64 resident ledger and Flux bootstrap inventory;
the existing bootstrap image-admission verifier passes. Sixteen configuration,
validator and retained evidence blobs match the selected candidate commit.
Strict SBOM and publisher **record** checks pass individually, but retained strict
scans reject source HIGH=7, kustomize/helm/notification HIGH=6 each (CRITICAL=0).
Exact 25 finding tuples, report hashes, scanner/DB identities are saved privately.
These are August scan findings, not a fresh advisory rescan. The historical
local-lab exception expired 2026-09-13; its verifier still fails. No renewal/waiver.

Publisher identity is upstream GitHub Actions OIDC, not the Shirokuma configuration
commit. The strict signed-index validator's canonical retained-bundle branch is
not selected for these Flux versions: a passing retained `verified:true` record
does **not** prove freshly verified signatures. No cosign/registry request was
made. Fresh API inspection finds eight native policies/eight bindings matching
owned expected content/current type-check status, no admission webhooks. Existing
bridge scope is `shirokuma-dev`, not Flux controllers in `flux-system`; it checks
digest syntax, not publisher signatures/CVE risk/source provenance. All sixteen
objects unchanged between reads; no binding activation or live allow/deny claim.

Missing local evidence is explicit: a candidate/image/evidence-bound local-only
risk decision, transitive rendered input and complete image/workload inventory,
reviewed local transport/verified fetched artifact bytes and commit, then fresh
scoped enforcement/applied revision observations. None is inferred from Ready,
matching pins or old exception approval. Candidate transport remains unselected;
the old branch-only unsuspended committed root must still not be applied.

Fresh complete live51 probes pass on the already-running dedicated VM/node,
latest 05:09:29 UTC: Node/fresh Lease, four pinned ready controllers/Pods,
Flux8/Jobs3 suspended, app owners0, original PV/PVC identities/specs. Initial/final
UID sets and non-Lease specs match; Flux/PV/PVC full objects unchanged. Fourteen
Docker containers have system/Flux names only, not a whole-runtime census. No
startup, resource mutation, transport change, fetch/apply/unsuspension or writer
start. Fresh doctor remains degraded for intentional inhibition.

Diagnosis7/source8/bootstrap40/supervisor15/continuation5/runner22/bears11, Go CLI/
internal tests, design-context and secret scan pass. Initial unittest harness
compatibility failure and exhausted mock sequence are retained and corrected;
two diagnostic control-reader size mismatches are retained (existing bounded
owner/mode/no-follow snapshot reader used instead; no permission bypass).
Security87 still fails five historical expiry/ledger-related tests; broad local
verifier still fails policy166 with 11 failures/2 errors. No unrelated repair.

Private inputs/results/probes/doctor/logs:
`tmp/local-experiment/autonomous-runner/gate-n0xgt8ew/attempt-001/admission-diagnosis/`.
Typed measured-blocker proposal:
`tmp/local-experiment/gate-continuation/series-fpveebm3/proposal-002.json`.
Next concrete dependency is local_admission/repository_repair: a **non-activating**
local evidence contract binding candidate, four publisher/scan hashes and exact
HIGH findings, leaving risk-decision/render gaps explicit. Parent review must
select it; no invented approval, exception renewal, transport selection, binding
change or runtime write. All eleven boxes/full L0/operator acceptance remain open.
Single OAuth/explicit Sol preflight passed, not independent billing proof; original
Vault untouched, direct sync pending. No nested runner/subagent/additional model
request launched.

Recovery: repository rollback of diagnostic/tests/docs only, no runtime fields to
restore. Leave safe inhibited node/controllers available for independent parent
probe and normal VM stop only after worker idle/non-active. Never restore app
counts or unsuspend sources/Jobs automatically. Preserve retained disks/volumes,
PV/PVCs, backups and all original failed operation/session evidence.

## 2026-10-08: repository-only inhibited source candidate (gate-9w3pac_6)

`L0_DEPENDENCY_CONTINUATION`, source_identity/repository_repair: implemented
`tools/local_experiment/source_candidate.py` and its contract documentation.
The offline builder binds available full commit
`0486612d0671c01c16d40ce445e39788d86de69b`, repository tree
`6fcbf835aa2d03cb2d1ae1863d139a46a46056e4`, fixed root path
`./deploy/gitops/clusters/local-lite` and all six child path trees. This is the
existing pre-increment commit, not a claim that a source fetched the eventual
worker HEAD. Git lazy fetching/replacement objects are disabled; symbolic/short
refs, unavailable/noncommit objects and missing/blob/traversal paths fail closed.
Dirty working-tree edits do not alter commit-bound identity.

Candidate source has only `ref.commit`, suspension true and **unselected local
transport**. It is a JSON contract, not an applyable Kubernetes manifest. All
seven candidate Kustomizations stay suspended; four existing pinned controller
images/replicas1 are recorded. UID/spec hashes bind observed owners, Flux, Jobs
and PV/PVCs privately. Jobs stay suspended, retained app owners zero. Existing
source URL is neither copied into candidate transport nor changed. Fetched and
applied commit evidence stays null; artifact bytes, transitive rendering, local
admission and full gate acceptance remain unverified. The old committed root
still lacks these suspensions and must not be applied.

Fresh complete probes at 04:56:18, 04:58:38 and 04:59:46 UTC pass on the
already-running dedicated VM/node: Node/fresh Lease, four digest-pinned ready
controllers/matching Pods, Flux8/Jobs3 suspended, retained application owners0,
original PV/PVC identities/specs. Initial/final 51 UID sets and non-Lease specs
match; complete Flux/PV/PVC objects unchanged. Final candidate guards match the
fresh final probe. Fourteen Docker containers are system/Flux names only; not
a whole-runtime census. No startup, fetch, artifact download, apply, source
redirection, unsuspension, writer start, new resource or policy activation.
Fresh doctor 04:59:45 UTC remains degraded for intentional suspension/replicas0.

Eight candidate regressions pass, including dirty-checkout isolation, unavailable
objects, path/blob rejection, source/owner/inhibition/retention drift and image
pins. Supervisor15, continuation5, corrected runner22, bears11, full Go CLI/
internal tests, design-context, secret scan and staged diff check pass. The initial
wrong runner test pattern discovered zero tests and is retained, not counted as a
pass; corrected filename was run separately. `make verify-local-experiment`
still fails at policy166:
11 failures/2 errors, unchanged historical scope; no waiver or unrelated repair.

Private candidate, probes, guard comparison, doctor and verification logs:
`tmp/local-experiment/autonomous-runner/gate-9w3pac_6/attempt-001/source-candidate/`.
Typed proposal:
`tmp/local-experiment/gate-continuation/series-fpveebm3/proposal-001.json`.
Next concrete dependency is read-only **local_admission/diagnose**: identify
which candidate/image identity requirements existing strict publisher checks
cannot satisfy locally, without changing policy bindings or transport. Parent
must independently review terminal worker, live scope and proposal before a
new mission. No acceptance checkbox or gate-pass claim. OAuth-only explicit Sol
preflight passed, not billing proof; original Vault untouched, direct sync pending.

Recovery: repository rollback of builder/tests/docs/Make target only; no runtime
fields changed. Leave existing inhibited node/controllers for parent independent
probe and normal VM stop only after this worker is idle/non-active. Never restore
app counts or unsuspend Jobs/sources automatically. All retained disks, volumes,
PV/PVCs, backups, original failures and stopped-run evidence remain preserved.

## 2026-10-08: suspended source provenance diagnosed (gate-x1girm_f)

`L0_DEPENDENCY_CONTINUATION`, source_identity/diagnose: the dedicated VM was
already Running; no VM/node/service startup or resource mutation was performed.
Fresh complete 51-object probe at 04:42:03 UTC passes Node/fresh Lease, four
digest-pinned ready controllers/matching Pods, Flux8/Jobs3 suspended, retained
app parent/secondary owners zero and original PV/PVC UIDs/specs. Fourteen Docker
containers are confined to system/Flux names; this is not a whole-runtime census.

Local-only Git inspection at checkout HEAD
`77748a96199dc6bff688e37d0af7f892fc4d6664` establishes:

| Suspended resource | Generation / Ready generation | Recorded revision |
| --- | --- | --- |
| GitRepository `flux-system` | 3 / 3; ArtifactInStorage=2 | `f0d804b9da8f185d86e8fa36acc0895674b228b6` |
| Root `flux-system` | 2 / 1 | `59f38dc26a1a02203df9c629360d863e4856a2ba` |
| Six children | catalog 4/3, storage 3/2, other four 2/1 | `f0d804b9da8f185d86e8fa36acc0895674b228b6` |

All eight top-level observedGeneration fields equal generation, **not** proof
of current reconciliation. Artifact lastUpdateTime remains 2026-09-16T04:17:37Z;
its recorded digest is not independently verified artifact bytes. Both commit
objects exist locally and are ancestors of HEAD (858/851 intervening commits),
neither equal HEAD. Cached `origin/main` points to the artifact commit; local
`main` points to `b2177ef4b1c6e55f225649911d2ed1bc09cd3a0b`. Neither ref is a
fresh observation of remote main. Git inspection disabled lazy fetching and
replacement objects; no fetch or artifact download was attempted.

All seven configured path tree IDs match their corresponding HEAD path trees,
with zero changed files in those paths. This is narrower than whole-commit,
transitive rendered-input, runtime state or artifact-byte equality. The offline
HEAD root render succeeds, but its GitRepository remains external branch `main`
without a commit pin; source/root/six child definitions all omit `suspend`.
Consequently neither matching path trees nor current source Ready authorizes
applying that root or resuming its prune-enabled children over retained writers.

**Concrete next dependency:** prepare/test a repository-only inhibited local
source candidate contract bound to an available full commit/tree and root path,
preserving four pinned controllers at one and all eight Flux suspensions. A
candidate is not fetched/applied provenance. Parent proposal selects
source_identity/repository_repair; no runtime redirection, unsuspension, new
resources, admission activation or writer startup is authorized by this result.

Private evidence is under
`tmp/local-experiment/autonomous-runner/gate-x1girm_f/attempt-001/source-diagnosis/`
(`probe-01.json`, `provenance.json`, `root-render.yaml`, diagnostic assertions,
routing and verification logs); typed proposal is
`tmp/local-experiment/gate-continuation/series-a6fazzwr/proposal-002.json`.
Focused bootstrap40/teardown6 and design-context pass; diagnostic assertions
confirm local object availability, condition staleness and seven path-tree matches.
Fresh `make verify-local-experiment` fails at policy166 with the same 11 failures/
2 errors (private `local-verifier.log`); no unrelated repair or waiver. Diff check
passes. No acceptance checkbox changed; doctor degradation and earlier stops remain.
Final 04:46:29 UTC complete live probe passes again: all 51 UIDs and non-Lease
specs unchanged; Flux/PV/PVC full objects unchanged. Fresh doctor remains degraded
for intentional source/child suspension and app replicas0, not protocol incompleteness.
Single OAuth/explicit Sol routing passed, not independent billing proof. Original
Vault remains untouched; direct sync pending outside checkout.

Recovery: repository evidence notes only; no runtime fields require rollback.
Leave the inhibited node/controllers for independent parent inspection; parent
owns normal VM stop after this worker is terminal/idle/non-active. Never restore
application counts, unsuspend sources/Jobs or apply saved snapshots. Retained
disks, volumes, PV/PVCs, backups and failure records remain preserved.

## 2026-10-08: live doctor aggregate-list protocol repair (gate-uc9gsp8y)

`L0_DEPENDENCY_CONTINUATION`, doctor/diagnose: fresh inhibited-lab doctor
reproduced exit 0 / degraded with healthy scope/API and incomplete Flux/app
responses. Its `execRunner` reads kubectl stdout directly, not an MCP response.
Exact host reads returned canonical `List/v1`, `metadata.resourceVersion:""`:
kubectl's aggregate JSON envelope has no shared API revision. The validator
mistook this normal envelope for truncation before evaluating suspension.

Repair in `internal/cli/doctor.go` permits the empty **list-level** version only
for explicit `List/v1`. Null/malformed versions, pagination, incomplete members,
duplicates and empty per-object versions still fail closed. Aggregation is not
an atomic snapshot or source provenance. Three new regression tests cover
healthy aggregate responses, inhibited workloads/sources and retained negative
guards; the first two failed before repair and all pass afterward. Full Go
CLI/internal tests, vet, parent-fixed bears tests (11), design-context, secret
scan and diff check pass. Final complete live probe independently passes again.

Fresh post-repair doctor at 04:38:28 UTC remains degraded, now explicitly for
suspended sources/children and four zero-desired-replica applications, not
incomplete responses. Root suspension retains its existing informational
exception; it is not root/source acceptance. Complete live probe then passed
Node/fresh Lease, four pinned ready/available controllers with matching Pods,
Flux8/Jobs3 suspended, inhibited app owners and original PV/PVC identities/specs.
Earlier post-start probes failed `FLUX_NOT_READY` during controller warmup and
remain retained; no resource mutation/restart was replayed to obtain the pass.

Before normal startup, dedicated k3s-disabled inspection confirmed inactive/
disabled k3s, Docker0 and original nineteen volume/two PVC-directory names.
API-only launch flags/MainPID and complete UID/spec/owner set matched parent's
prestart snapshot (51 objects); only expected Lease renewTime changes excluded.
An initial overly strict read-only Lease comparison failed and is recorded.
API-only stopped cleanly with MainPID0/no server before starting existing k3s.
No Kubernetes patches, source redirection/unsuspension, policy binding, app
startup, retained-data deletion or saved-request replay. Boot/service activity
writes OS/k3s metadata; disk byte-immutability/restore is not claimed.

Private evidence:
`tmp/local-experiment/autonomous-runner/gate-uc9gsp8y/attempt-001/doctor-diagnosis/`
(intent, inspection, prestart review, six probes, exact response JSON,
doctor-before/after stdout and verification logs). `make verify-local-experiment`
still fails at existing policy suite: 166 tests, 11 failures/2 errors. No waiver
or unrelated repair. Single OpenAI OAuth/explicit Sol check passed, not billing
proof. Original Vault remains untouched; direct sync pending outside checkout.

Next concrete dependency proposed to parent: read-only source-identity diagnosis
of the suspended GitRepository artifact versus root/child applied revisions.
Fresh source generation=3/Ready=3 but ArtifactInStorage=2; root generation=2/
Ready=1 with a different applied revision. These saved status fields cannot
prove a fetch of current checkout HEAD. Keep all reconcilers/writers inhibited.
Runtime remains available for independent parent probe; parent normally stops
the dedicated VM after worker terminal/idle confirmation. Repository rollback
is only this validator/test change; do not restore app counts, unsuspend Jobs/
sources or apply saved snapshots. Preserve changed disks before cold recovery.
All eleven acceptance boxes remain unchanged; no full L0/operator acceptance.

## 2026-10-08: autonomous successor and independent scoped readiness verification

Parent `loop-82wu_3jx` passed API-only inhibition verification and automatically
selected readiness worker `gate-3t2bauf_` without an owner restart instruction.
That worker subsequently safety-stopped because the supervisor incorrectly
required Node status heartbeat newer than 180 seconds despite a fresh Lease.
Kubernetes's unchanged status default is five minutes; Lease renewal is ten
seconds. Commit `3acd617` repairs freshness using Ready=true, the exact Node
UID/holder-bound Lease (renewal within duration capped at 40 seconds, future
skew <=2 seconds), plus status age <=600 seconds. Supervisor14/runner21 focused
tests pass; the broader policy suite failure remains preserved.

The parent re-observed the completed operation disposition and API-only desired
state before restarting the existing inhibited node/four-controller state.
Private `tmp/local-experiment/gate-supervisor/lease-review-brrwinpx/` contains
the prestart snapshot, independent live 51-object probe, review, summary and
normal-stop cleanup. At 04:10:57 UTC, Node Ready/Lease freshness, four pinned
Flux Deployments available/ready and corresponding Ready Pods passed; Flux8
and Jobs3 remained suspended, retained app owners zero, PV/PVC UIDs/specs
unchanged. Docker listed zero running containers (Docker-only scope, not a
whole-runtime census). No resource mutation replay or model session was added.
All profiles are Stopped after normal dedicated VM cleanup. Original failures
and safety checkpoint are immutable; separate resolution review records the
later evidence. Restore neither app counts nor source/Job suspension blindly.

This establishes the narrow Node/four-controller readiness milestone and actual
automatic mission transition, not full L0/source/reconciliation, doctor health,
application recovery or human acceptance. Doctor/source/local-admission gates
remain open; no checkbox change. Usage is 504 cumulative starts, restart4/100,
96 remaining under the original week. The finite L0 loop ended; general
multi-WP planning and arbitrary-stop remediation are still incomplete.

## 2026-10-08: scoped Node/four-controller readiness observed; parent review pending

`L0_CONTROLLER_READINESS`, `gate-3t2bauf_` attempt 001 follows the parent's
fresh `loop-82wu_3jx/reobserve-review.json` inhibition probe at 03:59:36 UTC.
Worker independently rechecked complete API-only owner chains/specs against
the repair's final snapshot: identical UID set/specs, all eight scoped parents
and eight secondary owners zero, no scoped Pods, Flux8/Jobs3 suspended,
original PV/PVC specs/UIDs and zero containers. Ordinary k3s inactive/disabled;
systemd launch flags/MainPID confirm the sole no-agent API unit.
Eight cold copies freshly match their recorded extent/data digests. Nineteen
Docker volume names and two retained PVC directories are preserved on ext4
`/dev/vdb1`; host/guest available space approximately 2.22 TiB/322 GiB.
An initial read-only backup assertion confused source and clone inodes;
inspection of the verifier resolved it, with zero runtime mutations at that point.

Stopped `shirokuma-l0-parent-reobserve.service` cleanly, checked both API/normal
units inactive with MainPID=0, no server process and zero containers, then
started **only** existing dedicated `k3s` (without enabling the disabled unit).
Fresh Node Ready observed before Flux scale, with all scoped owners still zero.
Four one-shot Deployment patches test UID, fresh resourceVersion, replicas=0
and the unchanged digest-pinned template before replacing only replicas with 1.
No source, Job, application, image, storage or policy mutation was performed.

Live complete probe at 04:03:03 UTC: dedicated node Ready with fresh heartbeat;
all four existing Flux Deployments observedGeneration=3/generation=3,
availableReplicas=readyReplicas=1, each with a matching digest-pinned Ready Pod
and one requested secondary-owner replica. Four app workloads/all app
ReplicaSets remain zero; no app Pods. Flux8/Jobs3 still suspended and retained
PV/PVC UIDs/specs unchanged. Fourteen running Docker containers are restricted
to Kubernetes system/Flux namespaces. Final handoff inventory passed the same
scoped readiness validator; runtime remains live for independent parent review.

**Final freshness hold:** a later `probe-03.json` fails
`NODE_NOT_FRESH_READY`: at 04:06:11 UTC, Node Ready=True still has
lastHeartbeatTime=04:02:17 UTC, outside the parent's 180-second requirement.
Read-only Node Lease GET shows renewTime=04:06:01.642163 UTC. This distinguishes
a fresh lease from a stale Ready-condition heartbeat; it does not waive the
existing validator or claim node failure/recovery. Earlier 04:03:03 readiness
pass is retained, not substituted for the failing final probe. No mutations
after this failure; independent parent review must resolve freshness before
accepting this gate. Runtime remains for that review/parent-controlled cleanup.

Source status is **not fresh reconciliation acceptance**: root/six child Ready
conditions still refer to previous generations, despite top-level
observedGeneration advancing. Suspended GitRepository now reports Ready=True
at generation 3, but ArtifactInStorage remains generation 2; saved status is
recorded without inferring a new fetch or approved source revision.
Read-only scoped doctor exits 0, overall **degraded**: local-scope/API healthy,
Flux response incomplete and four inhibited app workload responses incomplete.
Direct guest owner/Deployment/Pod observations support this narrower controller
readiness result; they do not waive doctor degradation. SQL/publication untested.

Private originals, intents/results, readiness/retention/source/doctor observations
and rollback live in
`tmp/local-experiment/autonomous-runner/gate-3t2bauf_/attempt-001/`:
`pre-start-owners.json`, `normal-node-owners.json`, `probe-02.json`,
`handoff-live-owners.json`, `controller-observations.json`,
`source-kustomization-observations.json`, `doctor-live.stdout`,
`cold-backup-identity-recheck.json`, four scale intent/result pairs and
`rollback-metadata.json`, plus `probe-03.json`, `final-node-lease.stdout` and
`final-freshness-gap.json`. Boot/API/controller startup changes disk metadata;
no byte-immutability, application restore or retained-volume byte measurement
is claimed. Original cold backup/rehearsal/failures remain unchanged.

Recovery: parent independently probes terminal worker and runtime, then normally
stops dedicated VM at finite-loop end. `sudo systemctl stop k3s` through explicit
`colima ssh --profile mac-studio-solo --`, then `colima stop mac-studio-solo`;
prefix shell commands with `rtk proxy`. No writer restoration/unsuspension.
If separately reviewed Flux-only scale rollback is needed, GET each same-UID
Deployment, verify unchanged template, JSON Patch tests UID/fresh version/
replicas=1/template and replace only replicas with zero, preserving intent/result
and never replaying unknown outcomes. Do not wholesale apply snapshots; retain
changed disks before any deliberate cold recovery. Normal k3s remains disabled.

Validation: supervisor 13 tests/runner 21 tests PASS; complete live inhibition,
initial readiness, retention and backup assertions, design-context, secret scan
and diff check PASS. Final readiness probe FAILED as described above.
`make verify-local-experiment`
FAILED at existing policy suite (166 tests, 11 failures/2 errors), no unrelated
repair/retry. Private log SHA-256:
`5ab500b50bd51cfb2329c7c798df7d6fb8364ce6b4551d0567e39b4574a6e3c3`.
OAuth-only explicit Sol preflight passed, not independent billing proof.
No nested supervisor/runner, Astra, GitHub writes, retained-data deletion or
saved-request replay. Original Vault sync pending outside checkout. This worker
stops further mutations at the final freshness hold for parent verification;
no later batch authorized and no gate-pass checkpoint.
All eleven WP acceptance boxes remain unchanged; full L0/source/doctor/operator
acceptance and full-nuke authorization remain open.

## 2026-10-08: API-only secondary-owner repair; parent verification pending

`L0_OWNER_CHAIN_REPAIR`, `gate-8ancmu2h` attempt 001: dedicated inspection
boot only, ordinary k3s inactive/disabled, agent/scheduler/controller-manager/
cloud-controller disabled, loopback API `/readyz=ok`, zero containers. Captured
complete Deployment/ReplicaSet/Pod owner references, UIDs/resourceVersions and
original objects before six one-shot JSON Patches. Each tests UID, observed
resourceVersion and replicas=1 before replacing replicas with zero. Six known
Polaris/Trino/four Flux secondary owners now request zero; all eight scoped
ReplicaSets and eight app/Flux parents are zero, with no app/Flux Pods.

Fresh final inventory preserves the complete object UID set and owner chains;
the only spec changes are those six replica fields. Eight Flux resources and
three completed Jobs remain suspended. Both PV/PVC pairs retain original
UIDs/specs; nineteen named Docker volumes and both retained PVC directories
remain present on the dedicated ext4 data mount. Original eight cold copies,
recovery rehearsal and earlier safety stops remain untouched. Boot/API write
disk metadata; no disk-byte immutability or application-restore claim.

Private evidence: `tmp/local-experiment/autonomous-runner/gate-8ancmu2h/attempt-001/`
(`owner-prior.json`, `owner-after.json`, `owner-final.json`, owner-chain snapshots,
`inhibition-plan.json`, six intent/result pairs, `rollback-metadata.json`,
retained mount/volume/directory and guest-headroom observations). **Left active
for independent parent probe:** `shirokuma-l0-owner-repair-8ancmu2h.service`.
No normal Node/Flux/workload startup, source unsuspension or writer restoration.
Parent must verify terminal worker and fresh runtime before another mission;
parent owns normal dedicated VM stop at finite-loop end. This worker's live
probe is not the parent's independent verification or L0 gate acceptance.

Observed k3s rewrites process argv to `/usr/local/bin/k3s server`; the initial
pre-mutation argv assertion failed without mutation. Parent probe now checks
the single running dedicated API unit's recorded ExecStart flags, active state,
Restart=no/KillMode and MainPID matched to the single live k3s server PID.
Negative tests reject missing flags, PID mismatch, non-loopback binding,
inactive/restarting unit and ambiguous units/processes. Corrected focused
supervisor/runner suite: 32 PASS; initial nonexistent test-module import error
retained in session evidence. Worker live probe/final owner-spec comparison,
design-context and diff check PASS. Broader `make verify-local-experiment`
FAILED: policy suite 166 tests, 11 failures/2 errors, no unrelated repair.
Private log SHA-256:
`c1d1512f2a42b6cf43c836bf0545fa8a546cf714e57b23f805e430dc0a6abeb4`.

Rollback is **not automatic**: after a separate writer-safety review, GET the
selected same-UID ReplicaSet, verify owner chain, then atomically test UID,
fresh resourceVersion and current replicas=0 before replacing only replicas
with captured original value 1. Record intent/result and never retry an unknown
outcome. Do not wholesale apply snapshots, unsuspend sources/Jobs, restore
writers/disks or delete retained storage. API stop (parent-controlled):
`colima ssh --profile mac-studio-solo -- sudo systemctl stop shirokuma-l0-owner-repair-8ancmu2h.service`,
then confirm inactive ordinary/API units and zero containers before normal
`colima stop mac-studio-solo`. Prefix shell commands with `rtk proxy`.
OAuth-only Sol preflight passed; no independent billing verification, Astra,
nested runner or external writes. Original Vault sync pending outside checkout.
Repair increment observed; all L0 acceptance boxes remain unchanged, normal
Node/four-controller readiness unverified. No further safe work in this repair
mission; stop for parent review, not automatic continuation within this worker.

## 2026-10-08: fresh API inhibition; secondary-owner startup blocker

`L0_GITOPS_READINESS`, batch `gate-uklhzopt` attempt 001: one dedicated
inspection VM start using DEV-04E's `--kubernetes=false --activate=false
--ssh-config=false --save-config=false` route. Explicit profile
`mac-studio-solo`; guest API observations use `sudo k3s kubectl`, not a default
host context. Ordinary k3s remained inactive/disabled; Docker had zero running
containers. Two separately recorded API-only transient services used disabled
agent/scheduler/controller-manager/cloud-controller and loopback port 55737;
both returned `/readyz=ok`. Private evidence:
`tmp/local-experiment/autonomous-runner/gate-uklhzopt/attempt-001/`.

Independent API snapshots confirm eight suspended Flux resources, three
suspended completed Jobs, four app workloads and four digest-pinned Flux
Deployments at replicas=0, no app/Flux Pods, and unchanged Node/PV/PVC resource
UIDs against the preserved original snapshots. Controller templates match the
inhibited snapshot. No reconciliation or resource mutation was performed.

**Startup NO-GO:** the prior snapshot omitted ReplicaSets. Fresh expanded
`get replicasets,replicationcontrollers,hpa -A -o json` found eleven ReplicaSets,
no ReplicationControllers/HPA. `polaris-8685578868` and
`trino-local-77d97b477f` each retain `spec.replicas=1`, with controller-owner
UIDs matching the zero-replica Deployments. Four Flux ReplicaSets also retain
one replica; two older app ReplicaSets are zero, and three system ReplicaSets
are one. With controller-manager disabled, Deployment scale-down has not
propagated to these secondary owners. Starting normal k3s can race ReplicaSet
Pod creation against Deployment scale-down. No unsafe race was executed.
Node startup and selective Flux scaling therefore stopped; there were **zero
normal Node starts and zero Flux scale operations**. This is a precisely
observed inhibition gap, not evidence that the recorded nineteen parent-field
changes drifted or that a retained writer actually ran.

Source/root/six child Kustomization statuses are captured privately without
reconciliation. All remain suspended; all Ready conditions refer to earlier
generations. GitRepository generation 3 still reports observed generation 2;
root generation 2 has a Ready condition for generation 1. Saved Ready=True
does not prove fresh source/controller readiness or acceptance.
After normal dedicated VM stop, read-only `go run ./cmd/shirokuma doctor
--profile local-experiment --output json` returned exit 0, overall degraded,
healthy loopback scope for `colima-mac-studio-solo`, and degraded cluster/Flux/
four app checks. SQL and publication were not evaluated. This is stopped-lab
diagnostics, not live readiness.

Recovery completed: both transient APIs stopped, ordinary k3s inactive,
zero containers before dedicated VM stop, all profiles freshly Stopped. No
retained storage/backup deletion, saved-request replay, original-field restore,
other-profile action or GitHub publication. Boot/API writes metadata; disk-byte
immutability and VM/application recovery are not claimed. Cold backup/rehearsal,
prior snapshots and original `gate-188rjc1d` stop remain untouched. Original
Vault sync is pending outside this checkout.

Next separately reviewed mission must include all secondary owners and a
UID/resourceVersion-preconditioned API-only inhibition of the six nonzero
app/Flux ReplicaSets, preserving prior state and never retrying an unknown
mutation. Verify complete owner inventory and zero app/Flux Pods before any
node startup; do not blindly restore writers or unsuspend children. This batch
stops at the safety boundary; no acceptance checkbox changes or later batch
authorization. OAuth-only preflight passed for explicit `openai/gpt-6.1-sol`;
no independent billing verification, nested run or Astra consultation.

Validation: private evidence assertions (six nonzero secondary owners with
matching Deployment UIDs, retained Node/PV/PVC identities, zero app/Flux Pods,
final stopped profiles), design-context and diff check PASS.
`make verify-local-experiment` FAILED at `test-local-policy`: 166 tests,
11 failures/2 errors in manifest/source read and race assertions, matching the
earlier failure counts; cause not established and no out-of-gate repair/retry.
New private `verify-local-experiment.log` SHA-256:
`696909fe4334f340797b7d9520e10cdf2ff5ff53e28742fe678b5eb94c72de8b`.
The original failure log remains unchanged.

## 2026-10-07: approved cold inspection and bounded recovery route

The owner requested resolving the prior inspection-access/retention blocker.
The authorized parent inspected the dedicated profile, made eight private
cold image/configuration copies, and compared all reported data extents plus
hole maps (53,962,405,731 data bytes). Read-only cold ext4 parsing and copied
k3s SQLite **including WAL** established stopped service/container and Flux
desired state. A k3s-disabled inspection boot confirmed zero running Docker
containers, k3s inactive/disabled, nineteen named volumes and two local-path
PVC directories; it was normally stopped afterward with source profile config
unchanged. Private evidence: `tmp/local-experiment/lab-inspection-20261007/`.

The inspected route and version-preserving file-level recovery rehearsal are
documented in [DEV-04E](../../04_Development/04E_Dedicated_Lab_Inspection_and_Recovery.md).
This resolves access and makes retained-state inspection possible without
starting writers; it does **not** prove complete host/disaster recovery or
booted application restore. Root Flux suspension is insufficient: six child
Kustomizations are unsuspended with prune enabled. Normal k3s startup remains
gated on an API-only/no-agent staging procedure and explicit child/job/writer
inhibition. A subsequent API-only stage was then tested: `/readyz=ok` with
zero containers and the ordinary k3s service inactive. Private original
resource snapshots and UID/version preconditions accompany 19 scoped
inhibition changes (eight Flux resources and three completed Jobs suspended;
four app workloads and four Flux Deployments scaled to zero). Eleven stopped
app/Flux Pod objects were retired with UID preconditions; both PVs/PVCs keep
their identities. The API-only service/VM were stopped, leaving a controlled
next-node-start baseline rather than an unknown writer state. Normal Node/Flux
readiness and application restore remain unverified. Do not replay old
requests or auto-restore newer disk state; exact metadata rollback is in DEV-04E.
Original `gate-188rjc1d` safety stop and failed broader verifier remain unchanged.
No new runner session, API/SQL backend request, production/operator acceptance
or acceptance-checkbox change is claimed by this follow-up.

## 2026-10-07: L0 owned inventory and runtime NO-GO

Gate `L0_GITOPS_READINESS`, manually selected batch `gate-188rjc1d`, attempt
001: fresh inspect-only evidence is in private
`tmp/local-experiment/autonomous-runner/gate-188rjc1d/attempt-001/l0-inventory.json`.
This is a **partial owned inventory**, not a retention or startup clearance.

| Scope | Fresh observation / limit |
| --- | --- |
| Dedicated profile | `colima list --json`: `mac-studio-solo`, Stopped, aarch64, 16 CPUs, 96 GiB memory, configured 400 GiB disk; all listed profiles Stopped. No other profile action. |
| Host disk | `df -k .` and filesystem statistics show about 2.22 TiB available; retained VM disk allocation and PVC bytes remain NOT_MEASURED. |
| Owned backups | Four `tmp/local-experiment/metadata/backup-*.dump` files, 3,387,469 bytes total, mode 0600; stable file identities during read, SHA-256/size match the saved 2026-10-02 metadata baseline. These are metadata dumps, not a whole-VM/PVC recovery inventory or a fresh restore test. |
| Known writer surfaces | Repository metadata Compose project `shirokuma-local-metadata` declares PostgreSQL/search retained volumes and a migration service; historical notes also identify retained k3s workloads and Warehouse saved requests. Current guest writers, restart policies and unknown-request disposition are UNKNOWN. No writer started or request replayed. |
| Flux scope | Checked-in v2.9.2 manifests declare four controllers and root `flux-system` Source/Kustomization, branch `main`, `prune: true`. Actual persisted scope is UNKNOWN; the 2026-10-02 suspended-root observation is stale, not startup clearance or an approved current source revision. |

**NO-GO:** the dedicated host profile configuration read returned
`permission.rejected` in this noninteractive session. No alternate tool/path
was used to obtain it. Current profile identity/storage mappings, startup
behavior, stopped-guest writer inventory and retained-state recovery are not
established. Starting k3s to discover them would itself restart unknown writers.
Consequently no VM start, Node/API/Flux/doctor probe, suspend/reconcile, migration,
reset, GitHub write or retained-state mutation was performed. No fresh live
readiness evidence exists; all eleven acceptance boxes remain unchecked.

Focused non-runtime check: `make flux-version-check` PASS (v2.9.2).
The existing zero-model-request subscription preflight passed for explicit
`openai/gpt-6.1-sol` with one OpenAI OAuth connection and no API-key/base-URL
route; billing was not independently verified. No Astra consultation or nested
runner was launched. These checks do not pass the live gate.

Record validation: design-context, inventory mode/decision/backup checks,
eleven-unchecked-criteria check and `git diff --check` PASS.
`make verify-local-experiment` exited 2 at `test-local-policy`: 166 tests,
11 failures and 2 errors in manifest/source read and race assertions.
Root cause is not established; no unrelated repair or retry was attempted.
The original private `verify-local-experiment.log` beside the inventory is
retained (SHA-256
`ab6af62cad25e1622c008c43d4964abddc9e05014434651cf0d324de7b78c1b1`).
Do not describe this batch's broader regression check as green.

Resume only in an approved inspection context that can establish the dedicated
profile/config and storage identity, enumerate retained guest workloads and
restart policies plus saved-request disposition, map backups to retained
volumes with a recovery procedure, and establish prior Flux scope/source
identity. Preserve historical failures; do not infer replay safety from saved
completion claims. Recovery for this attempt: no runtime changes to restore;
keep the dedicated profile Stopped and all volumes, backups and source-run
evidence intact. Stop this batch at the safety boundary, not `more_work`.
Original Vault remains untouched; direct synchronization is pending.

## 2026-10-06: status scope for the dedicated context (offline)

Separately from the mutating-target refusal, `make gitops-status` now rejects
an overridden non-dedicated Kubernetes context before kubectl or Flux reads.
A fake-tool invocation with `colima-other` recorded zero calls; the 46
bootstrap/teardown focused tests pass. This protects unrelated contexts from
accidental observation, not Kubernetes API authenticity or live Flux health.
The dedicated Colima profile was last freshly observed Stopped and was not
started for this check. No retained storage/backup or cluster state changed.
To restore status for another authorized profile, review and change only this
scope check; do not infer live §9, writer safety or publication acceptance.
Original Vault sync remains pending outside checkout.

## 2026-10-06: local mutating-target refusals and partial List boundary

The historical `gitops-bootstrap` and `gitops-teardown` Make recipes now
refuse in local mode **before prerequisites**. Their preserved GitHub-writing
bootstrap and Flux uninstall/OpenTofu destroy commands cannot be reached via
these targets; external delivery would require a separately reviewed change,
not an environment-variable override. A fake-tool Make invocation confirms
both refuse without running their preflights. This prevents these entrypoints
from making unauthorized writes; it does not replace a safe local bootstrap
or a data/backup-aware teardown plan. Separately the offline kubectl renderer
rejects partial Kubernetes `List` responses (continuation token or remaining
items), including nested lists, instead of interpreting a page as the entire
resource inventory. Synthetic failures were reproduced before repair; 45
bootstrap/teardown tests and the full local verifier pass. The full verifier's
fake Dagster failure output is expected. Design-context and diff checks pass.
Fresh `colima list --json` reports the dedicated aarch64 profile Stopped;
no VM start, live API, reconciliation, retained disk/backup read or change.
Inventory disk/backup, writers and Flux scope before any live §9 step;
remote Git identity, supply-chain strict admission, and human/operator
acceptance remain open. To revert, change only the scoped Make guards or
renderer contract after reviewing authorization; preserve retained evidence
and resources. Original Vault direct sync remains pending outside checkout.

## 2026-10-06: scoped reconcile prerequisite (offline)

`make gitops-reconcile` now rejects a context other than
`colima-mac-studio-solo` before its first Flux write and requires the existing
read-only `colima-status` baseline to pass before reconciliation. A fake pinned
Flux client with another context failed without recording a write; a dry run
confirmed baseline ordering for the dedicated context. This does not prove
that a context name authenticates the cluster or that a running lab is safe to
reconcile: inventory disk/backups, continuous writers and prior Flux scope
before invoking this target. The dedicated profile was freshly Stopped; no VM,
Git, Kubernetes or retained volume/backup was changed. Focused bootstrap and
teardown contracts, locked-provider validation, pinned client and design-context
passed, as did the full local verifier (its fake Dagster failure is an
intentional negative). Independently, WP-L1-LAKE-003's pinned offline renderer
passed 144 tests with the isolated Helm v4.2.2 binary; the installed v4.2.3
remains unchanged. Live §9, strict admission and operator acceptance remain open. To
recover an earlier local recipe, revert only the Make target and test; never
discard saved resources or replay an unknown reconciliation. Original Vault
sync remains pending outside checkout.

## 2026-10-06: measured offline prerequisites, live boundary retained

`make verify-gitops-bootstrap verify-gitops-teardown` passed 36+6 tests,
locked-provider OpenTofu init/validate and the normal object-storage state
check; `make flux-version-check` passed the installed v2.9.2 client. The
independent 144-test Iceberg renderer gate passed with the existing isolated
Darwin/arm64 Helm v4.2.2 archive (SHA-256
`5410a0dae3d5d91f45653b161260d9301aabc4ae80ae50a6605d66884b6df8ea`),
not the installed v4.2.3. The full local verifier also passed; its fake
Dagster worker failure is an intentional negative. `colima list --json`
reports `mac-studio-solo` Stopped, aarch64, configured 16 CPUs/96 GiB/400 GiB;
the disk number is a configured limit, not a retained disk/backup inventory.
The runbook now marks its GitHub-writing bootstrap and whole-profile reset as
outside this local execution scope. No VM start, GitHub write, reconcile,
retained disk/backup inspection or change was performed. Before live §9 work,
assess disk/backup, writer and Flux scope; remote source authenticity, strict
admission and operator acceptance remain open. Restore the previous offline
tool scope by omitting the private Helm PATH prefix; preserve saved resources
and past failures. Original Vault synchronization pending outside checkout.

## 2026-10-06: warning-bearing offline render paths

Successful Flux build and kubectl Kustomize transform previously returned
resources despite stderr warnings; isolated negatives reproduced both. All
offline render subprocess paths now reject warnings, including Helm package,
values parser and release template; errors omit untrusted warning text.
GitOps bootstrap focused 36, full local verifier and design-context passed.
Dedicated Colima freshly observed Stopped; no VM start or retained storage/
backup inspection. This is offline fail-closed rendering, not live §9,
authenticated Git/source, strict admission or operator acceptance. Revert
only the renderer guards and tests if pinned tool semantics differ, retaining
previous failures and saved data. Direct Vault sync pending outside checkout.

## 2026-10-06: kubectl conversion warnings fail closed (offline)

An isolated successful kubectl patch returning a ConfigMap plus a warning on
stderr previously passed resource conversion. Conversion now rejects stderr
before using stdout, without echoing the warning. The negative failed before
the fix; bootstrap contracts pass (33), the full local verifier and
design-context pass. Separately, WP-L6-FIN-001's Docker metadata preflight
fixtures pass (36); this does not authorize volume traversal. Fresh dedicated
Colima is Stopped; no VM, remote source, Flux or retained volume/backup was
inspected or changed. Live §9, source provenance and operator acceptance remain
open. If pinned kubectl proves warning-bearing output valid, revert only the
conversion guard/test; retain prior failures and saved resources. Original
Vault synchronization remains pending outside checkout.

## 2026-10-06: bounded nested resource lists (offline)

Separately from duplicate-key rejection, the offline kubectl renderer now
refuses a resource `List` nested beyond 16 levels instead of following an
unbounded recursive shape; an isolated 20-level negative failed before the
guard and a two-level valid control passes. Bootstrap/teardown contracts 38,
full local verifier and design-context pass. No live input or runtime state is
changed. This limit does not establish canonical tool output, live §9 or
source identity. Restore only this guard/test if actual pinned kubectl output
requires deeper nesting. Original Vault sync remains pending.

## 2026-10-06: offline renderer duplicate JSON refusal

The canonical offline renderer previously accepted conflicting duplicate keys
in kubectl resource JSON (including nested and escaped-equivalent names) and
client version JSON. Its JSON decoders now reject duplicates without echoing
their contents. Independently, a wrong client version no longer repeats
untrusted version text in diagnostic output. Two focused negatives failed
before correction; bootstrap/teardown contracts pass (37), full local verifier
and design-context pass (the fake Dagster failure is intentional). Separate
FinOps Docker metadata preflight fixtures pass (36) under discovery; they do
not authorize AgentHouse volume traversal or replace writer verification.
Fresh dedicated Colima Stopped; no VM start, remote Git/API, Flux reconcile,
credential, retained volume or backup read/change. This is not live §9,
source authenticity, strict admission, or operator acceptance. Roll back only
the renderer/tests if verified kubectl output semantics differ; preserve
historical evidence and saved resources. Original Vault sync pending outside
the permitted checkout.

## 2026-10-06: offline renderer diagnostics and kubectl warning boundary

Two independent synthetic false accepts/exposures were reproduced: kubectl
version JSON matching the pinned versions alongside a stderr warning passed,
and a failed renderer command echoed untrusted tool output and arguments into
policy errors. The version gate now refuses warnings, and failed, timed-out or
warning-bearing commands report fixed scoped errors without echoing those
inputs. Exit status remains visible for failed commands. Bootstrap/teardown
contracts pass (35); `make verify-local-experiment`, `make
verify-design-context` and diff check pass (the fake Dagster failure is an
intentional negative). Fresh `colima list`: dedicated VM Stopped. This changes
only offline diagnostics/version classification; no VM, Git remote, Flux,
retained volume/backup or credential was read or changed. Restore only the
renderer/test changes if verified tool behavior differs; retain prior evidence
and saved resources. Live §9, source authenticity, writer exclusion, strict
admission and operator acceptance remain open. Original Vault sync pending.

## 2026-10-06: offline kubectl renderer output and version shape

Two independent incomplete-tool cases are now scoped failures: nonempty YAML
whose successful kubectl invocation returns no JSON (including whitespace),
and a kubectl version response with a JSON scalar or array instead of an object.
Previously the former silently produced zero resources and the latter leaked an
unscoped attribute error. Isolated negatives and valid controls pass; bootstrap
and teardown contracts pass (33), as does `make verify-local-experiment` (the
fake Dagster worker failure is an intentional negative). Dedicated Colima was
freshly observed Stopped; no VM start, remote fetch, Flux reconcile, retained
data or backup inspection/change. An empty input still returns zero resources;
nonempty comments-only YAML now fails conservatively. Restore only these two
renderer guards and tests if verified kubectl semantics require it, leaving saved
state and historical failures untouched. Live §9, remote source identity, strict
admission and operator acceptance remain open. Original Vault sync pending
outside this checkout.

## 2026-10-06: offline renderer refuses incomplete resource lists and launch failure

An isolated Kubernetes `List` with a non-array `items` value previously
flattened to zero resources, hiding an incomplete render; four negative
fixtures now raise `RenderError`, while a valid list retains its resource.
Separately a renderer command that cannot be launched now fails with a scoped
error rather than an uncaught `OSError`. Bootstrap and teardown contract tests
pass (31). `make verify-local-experiment`, `make verify-design-context` and diff
check pass (the fake Dagster worker failure is intentional). Neither test
contacts Git or Kubernetes. Fresh `colima list` shows
the dedicated VM Stopped; no retained disk, volume, backup, writer or Flux
scope was inventoried, and no live §9, remote provenance, strict gate or
operator acceptance is claimed. Restore only these renderer guards/tests if
verified input or tool semantics differ; preserve saved resources and past
failures. Original Vault synchronization remains pending outside checkout.

## 2026-10-06: exact Helm renderer version output and stopped-lab recheck

The offline GitOps renderer previously accepted an additional Helm version line,
an empty build suffix, or leading whitespace because it stripped stdout and
only compared a prefix; stderr was unchecked. Isolated fixture negatives failed
before correction. It now requires one LF-terminated pinned v4.2.2 build line
and no stderr. Bootstrap/teardown contracts (29), the independent Iceberg gate
with the previously isolated v4.2.2 binary (144), design-context and the full
local verifier pass. The first full verifier exceeded a 120-second command
timeout before completion; the bounded 420-second rerun passed (private log
`tmp/local-experiment/attempt-231-verify.log`; fake Dagster failure is the
expected negative). No host Helm installation or pin change was made.

Independently, fresh `colima list` shows all profiles Stopped, and dedicated
`colima status` refuses a stopped profile. Host filesystem has 2.2 TiB free;
this is not a VM disk, backup, or writer inventory. No VM was started and no
retained volumes/backups or Flux state were inspected or changed. Before live
§9 measurements, inventory the dedicated VM's retained disk/backup, continuous
writers and Flux scope, then assess whether startup is safe. No remote Git
authenticity, live controller health, strict admission or operator acceptance
follows from this offline gate. To recover the prior local tool scope omit the
private PATH prefix; revert only the renderer/test if verified Helm output
semantics require it, preserving saved data. Original Vault sync remains
pending outside this checkout.

## 2026-10-06: independently supply the pinned local Helm tool

The installed Helm v4.2.3 remains unchanged. The v4.2.2 Darwin/arm64 archive
from `https://get.helm.sh/helm-v4.2.2-darwin-arm64.tar.gz` was downloaded into
ignored checkout-private `tmp/local-experiment/attempt-230-helm/`; its SHA-256
`5410a0dae3d5d91f45653b161260d9301aabc4ae80ae50a6605d66884b6df8ea`
matches the release host's `.sha256sum`. Archive entries were inspected before
extracting, and the isolated binary reports `v4.2.2+gb05881c`. With only that
directory prepended to PATH, `make verify-iceberg-table-bootstrap` passes all
144 tests (`tmp/local-experiment/attempt-230-iceberg-pinned.log`); without it,
the prior 43 version errors remain reproducible. A checksum served from the
same host is not independent release signing/provenance. This check does not
install a host tool, start the VM, apply manifests, prove remote Git identity,
or satisfy §9/live/operator acceptance. Retain the old failure logs and saved
resources; recovery is to omit the private PATH prefix, not to change the
host binary or relax the pin. Original Vault synchronization remains pending.

## 2026-10-06: separate application configuration from manifest scan

The independent Iceberg renderer was feeding checked-in routing rules, warehouse
mapping, metadata Compose/image inputs and a Flux patch array to kubectl as
Kubernetes objects. Its deployment scan now excludes only those seven explicit
relative configuration paths, after the existing symlink boundary check. A
`kind`/`apiVersion` field in the six ordinary configuration paths or a malformed
Flux patch array fails closed, rather than silently hiding a Kubernetes resource;
new deployment paths remain scanned. Focused configuration tests (15), direct
kubectl parsing of all 37 remaining deploy files, and `make
verify-local-experiment` pass. The full Iceberg gate runs 144 tests but still
fails with 43 Helm-dependent errors: installed v4.2.3 does not meet the pinned
v4.2.2 requirement. The original 44-error failure remains historical evidence,
not acceptance. Dedicated Colima freshly observed Stopped; no tool install,
VM start, retained volume or backup access/change, or remote fetch. Restore
only the scoped scanner/tests if verified deployment semantics differ; keep
saved resources. Supply and verify the pinned Helm separately before claiming
the gate, then measure live §9 and operator criteria. Original Vault sync is
pending outside checkout.

## 2026-10-06: independent Iceberg gate remains failed

After the source-context repair, a fresh `make verify-iceberg-table-bootstrap`
ran 129 tests and failed with 44 errors. The installed Helm reports v4.2.3
against the renderer's v4.2.2 pin; separately the broad deploy manifest scan
sends non-Kubernetes warehouse routing-rules YAML (without `kind`) to kubectl.
These are distinct from the 28 passing bootstrap/teardown contracts and the
passing local verifier. A future repair must separate Kubernetes manifests
from retained configuration inputs and verify the pinned tool, without
silently skipping Kubernetes resources or relaxing the version gate. No tool
install, VM start or retained-state change was made. The failed gate is not
operator acceptance; original Vault sync remains pending.

## 2026-10-06: checked-in source ref is branch-only (offline)

The checked-in context reader previously accepted a branch together with a
tag, commit or semver selector as a declared local source. An isolated fixture
reproduced three false accepts; it now requires exactly one branch ref, matching
the downstream renderer. Bootstrap/teardown contracts (28) pass. No remote
fetch or live Flux observation; dedicated Colima is Stopped. Restore only the
scoped reader/test if verified Flux semantics require it, preserving retained
resources and backups. Live §9 and operator acceptance remain open; original
Vault sync is pending outside the checkout.

## 2026-10-06: checked-in Git source completeness (offline)

An independent fixture showed that the checked-in source context silently
ignored an additional GitRepository with a missing branch while accepting a
valid neighbor. It now refuses any incomplete GitRepository URL/branch or
unsupported GitRepository API version instead of shrinking the declared source
set. Focused bootstrap/teardown contracts (27) and full local verifier pass
(intentional fake Dagster failure). The dedicated VM remains
Stopped; this offline declaration check does not fetch the remote or establish
runtime/credential authenticity, strict admission or operator acceptance.
Preserve saved resources; restore only the scoped context reader/test if
verified declaration semantics differ. Original Vault sync remains pending.

## 2026-10-06: unambiguous offline source identities

Two independent fixture failures in the local GitOps renderer were corrected:
duplicate canonical Git URL/branch declarations with different credential
reference names no longer create interchangeable source identities; malformed
URL authorities (including an unmatched IPv6 bracket) now fail as scoped
`RenderError` rather than leaking a parser exception. URL errors do not echo
potential embedded credential text. Isolated regressions failed before repair;
bootstrap/teardown contract tests (26), `make flux-version-check tofu-fmt`,
and `make verify-local-experiment` pass (fake Dagster worker failure is an
intentional negative). Dedicated `colima list` observed mac-studio-solo Stopped;
no VM start, remote fetch, credential bytes, retained data or backup accessed.
This compares local declarations only: remote identity, live Flux, Git smoke,
strict admission and operator acceptance remain open. Restore only the scoped
renderer/test if verified source semantics require it, preserving saved state.
Original Vault synchronization remains pending outside this checkout.

## 2026-10-06: checked-in source credential reference (offline)

As a second independent identity check, repository-local rendering now binds
the GitRepository `secretRef.name` to the checked-in sync contract alongside
URL/branch. A changed or omitted reference and an extra credential field are
rejected; the original source is still accepted. This compares reference
**names**, not credential bytes, remote content or authenticity. The focused
bootstrap/teardown tests pass (25), and the pinned Flux gate and OpenTofu
format check pass. An independent Iceberg suite still fails with 44 errors:
installed Helm v4.2.3 differs from renderer pin v4.2.2, and installed kubectl
rejects non-Kubernetes JSON. Do not relax pins or count that suite as passed.
No dedicated VM start or retained data/backup change. Revert only this
reference-binding change if verified semantics require it; original Vault sync
pending, live §9 and operator acceptance still open.

## 2026-10-06: offline source fetch-setting boundary

Four synthetic GitRepository variants with the declared URL/branch but an
alternate proxy CA, proxy credential, cloud provider or insecure transport
were incorrectly treated as repository-local. The offline renderer now refuses
these nonempty unmodeled fetch settings. The regression reproduced all four
false accepts before the repair; bootstrap/teardown contract tests pass after
it. This is only source classification, not remote provenance: the existing
`secretRef` remains an unverified runtime credential and no remote was fetched.
Fresh Colima inventory reports `mac-studio-solo` Stopped; no retained data,
backup, GitHub, VM or Flux operation. Revert only the renderer guard/test if
verified controller semantics require it. Original Vault sync pending outside
this checkout; §9 live and operator acceptance remain open.

## 2026-10-06: offline Git source path separator boundary

An independent check of local-source identity found that collapsing leading,
duplicate or trailing path slashes could equate a different Git repository
path to the configured source. The renderer now refuses duplicate/trailing
separators and absolute scp-style paths instead of normalizing them. Five
negative paths and ordinary scp/SSH positive remain tested. This does not
authenticate the remote repository, guarantee all URL equivalences or grant
live Flux acceptance. The dedicated VM remains Stopped; no Git remote request,
VM start, retained-data operation or GitHub write. Revert only this scoped
path refusal if verified remote semantics require it; do not modify retained
resources. Original Vault sync pending outside checkout. Live §9 and operator
acceptance remain open.

## 2026-10-06: offline Git transport and principal identity

The renderer previously identified a repository by host/path/port only: a
GitRepository switching from SSH to HTTP(S), or changing its SSH username,
could be classified as the declared local source. Source identity now includes
transport and SSH principal; scp-style `git@host:path` remains equivalent to
`ssh://git@host/path`. Embedded passwords and HTTP userinfo fail closed. Five
negative source cases and the scp positive pass, along with the existing URL
cases, the full local verifier, bootstrap 16 and teardown 6 contract tests,
the pinned Flux client gate and OpenTofu validation. OpenTofu init used only
already installed locked providers and did not apply or destroy resources.

Fresh `colima list` and stopped-profile status show the dedicated VM Stopped;
no live Git or Kubernetes request, Flux reconcile, GitHub write, retained
volume/backup inventory or deletion. URL classification is not remote source
authentication. The separate Iceberg gate still has the previously recorded
Helm pin/kubectl tool drift; no pin was relaxed. Before live work inventory
retained disk/backups, writers and Flux scope, then measure §9 gates without
replaying unknown operations. Revert only this matcher/test if verified source
semantics require it, preserving saved resources and historical failures.
Original Vault sync is pending outside this checkout; strict admission and
operator acceptance remain open.

## 2026-10-06: offline source URL identity and independent tool inventory

The GitOps renderer previously considered a GitRepository URL with a query or
fragment identical to the declared repository context because URL parsing
discarded those fields. Two direct negative fixtures reproduced the false
match. The local-source matcher now refuses these suffixes and ambiguous
path spellings (dot components, escapes, non-path punctuation), preserving
the normal source match and non-default port isolation. Focused 2 tests,
bootstrap contract 16, `make tofu-fmt`, `make flux-version-check`,
`make verify-design-context`, `make verify-local-experiment` and diff check
pass (the local verifier's fake Dagster failure is intentional). This is
offline source **text** classification, not remote repository provenance.

An independent read-only tool check found installed Helm v4.2.3 while the
renderer pins v4.2.2. `make verify-iceberg-table-bootstrap` failed with
44 errors, including that version gate and kubectl rejecting non-Kubernetes
JSON during the manifest scan. No version pin was changed or these failures
called acceptance. Fresh dedicated `colima list` shows Stopped and
`colima status --profile mac-studio-solo` confirms it is not running. No VM
start, Helm release, Flux reconcile, GitHub request or saved volume/backup
access/change. Before live work inspect retained disk/backup, writers and
Flux scope. Restore only the URL matcher/test if verified URL semantics
require it; keep saved resources and historical evidence. Original Vault
sync is pending outside checkout. Live source identity, strict admission,
Git smoke and operator acceptance remain open.

## 2026-10-06: align offline renderer with pinned client gate

The independent GitOps manifest renderer (`tests/gitops_render.py`) used to
`strip()` Flux client stdout and ignore successful stderr, even though the
Make gate refuses warnings and requires exactly `flux: v2.9.2` followed by
one LF. Four fake-client false accepts were reproduced before correction;
the renderer now fails closed for a missing/extra newline, leading whitespace
or stderr warning. It still accepts the installed client through
`make flux-version-check`. Focused fake-client tests (2), bootstrap contract
tests (16), independent teardown contract tests (6), `make tofu-fmt`,
`make verify-design-context` and `make verify-local-experiment` pass; the
fake Dagster worker failure in the latter is an intentional negative.

Fresh `colima list` and stopped-profile `colima status` confirm the dedicated
VM was not running. No VM start, render apply, source/backup/volume inventory,
writer exclusion, `flux check --pre`, Git smoke or reconciliation occurred.
This offline version check is not live or operator acceptance. Before starting
the VM, inventory retained state and Flux scope as described below. Restore
only the renderer validation/test if verified CLI compatibility requires it;
keep saved resources and historical failures. Direct original Vault sync is
pending outside this checkout.

## 2026-10-06: pinned client warning and override boundary (offline)

The local `flux-version-check` had accepted a client that printed the expected
version on stdout but a warning on stderr, because stderr was discarded. It
also allowed a caller to override `FLUX_VERSION` and pass with an unpinned
client. The gate now requires the fixed v2.9.2 input, successful exit and
exactly one combined output line with no warning. The isolated fake-command
test covers both former false positives, alongside the previous negatives;
the installed client gate passes. Independently `make tofu-fmt` and the 22
bootstrap/teardown contract tests pass without applying or destroying anything.
`make verify-local-experiment` passes (the fake Dagster worker failure is an
intentional negative), as does `git diff --check`.

Fresh `colima list` reports the dedicated profile Stopped; no VM start,
`flux check --pre`, live source read, reconcile, retained disk/volume/backup
inspection or mutation was performed. Formatting and offline contract tests
cannot attest the VM, writer exclusion, source provenance, strict publication
admission or operator acceptance. Inventory retained storage/backups, writers
and dedicated Flux scope before any future start. Roll back only the Make gate
and its fake-client test if a verified compatibility requirement changes;
preserve saved resources and historical failures. Original Vault sync remains
pending outside this checkout.

## 2026-10-06: bounded local Flux version and stopped-runtime gates

The installed `flux version --client` reported exactly `flux: v2.9.2` and
`make flux-version-check` passed. The Make gate previously extracted any
matching `flux:` line with awk, accepting appended warnings or contradictory
versions. It now requires precisely the pinned single line and a successful
client exit; seven fake-command cases pass, including extra lines, blank lines,
suffixes, wrong versions and a failed client. This is a local client-version
observation, not the full WP version/cluster acceptance criterion.

Independently, a fresh `colima list` showed `mac-studio-solo` Stopped and
`colima status --profile mac-studio-solo` refused the stopped profile. Host
filesystem free space was 2.2 TiB (not a dedicated VM disk inventory). The
read-only `go run ./cmd/shirokuma doctor --profile local-experiment --output
json` reported healthy *scope* but degraded cluster, Flux and all four named
workloads because their API reads failed while stopped. The scope result does
not authenticate a remote server. No live Flux, source revision, writer,
retained volume or backup state was measured, so `flux check --pre`, controller
readiness, Git smoke, strict publication admission, teardown/rebootstrap and
operator acceptance remain open. Before starting the VM, separately inventory
its retained disk/backup and writer state and dedicated Flux reconciliation
scope; do not use the whole-profile nuke or expired exception as a shortcut.
No VM or retained data changed here. Focused version tests, the real gate,
`make verify-local-experiment` (intentional fake Dagster failure),
`make verify-design-context` and diff check passed. Rollback is limited to
the Make gate and fake version test; preserve all retained resources and old
failure records. Direct original Vault sync remains pending outside checkout.

## 2026-10-05: encoded literal-host controls (offline)

Two fake kubeconfig server endpoints with percent-encoded digits or dots in
an apparent loopback IPv4 host degrade under the unchanged doctor. The
focused scope test, full Go CLI suite (909) and vet passed. Dedicated Colima
was observed stopped; no live Kubernetes/Git read or reconciliation. This
pins a conservative endpoint diagnostic, not server/context authentication,
atomic inventory, strict admission or operator acceptance. Revert only these
two test cases if actual dedicated endpoint requirements change; preserve
previous evidence. Original Vault sync remains pending outside checkout.

## 2026-10-05: literal loopback scope for local doctor (offline)

The local doctor previously classified `https://localhost:6443` as a loopback
Kubernetes endpoint solely from the hostname. A mutable resolver/hosts mapping
can send that hostname elsewhere. The read-only scope check now requires a
literal loopback IPv4 or IPv6 address and degrades the former localhost
fixture. The focused Go scope tests, `go test ./...`, `go vet ./...` and the
local regression passed. This intentionally tightens the diagnostic contract:
an existing context using localhost now receives a degraded scope report,
without changing its kubeconfig or issuing a request to it. Dedicated Colima
was observed stopped; no live API/Git read, reconciliation, retained
data/backup mutation or GitHub write. A literal address is not proof of API
server identity, context authenticity, remote revision provenance or strict
admission. If actual dedicated configurations need a different endpoint,
inspect their ownership and resolver mapping before revising this diagnostic;
roll back only the doctor scope/test change, preserving retained evidence.
Direct original Vault sync remains pending outside checkout.

## 2026-10-05: independent encoded-authority controls (offline)

The unchanged doctor degrades two further encoded authority separators and
accepts a middle-of-range literal IPv4 loopback in fake context output. Focused
Go test, full CLI suite (906), vet and local regression passed. Colima was observed stopped; no live API/Git inventory or
reconcile, retained data/backup mutation or GitHub write. This does not prove
context or endpoint authenticity, atomic inventory, strict admission or
operator acceptance. Revert only these fixture extensions if needed, preserving
prior evidence. Direct original Vault synchronization remains pending.

## 2026-10-05: independent encoded-authority and mapped-host controls (offline)

The unchanged doctor degrades two synthetic percent-encoded authority
lookalikes and two further IPv4-mapped IPv6 loopback hosts. Focused and full
Go CLI tests and vet passed. Colima was observed stopped; no live Git/API
read, reconciliation, retained data/backup change or GitHub write. Fake
context text cannot authenticate the actual endpoint, prove atomic inventory,
clear strict admission or grant operator acceptance. Revert only these test
cases if necessary; preserve earlier evidence. Original Vault sync pending.

## 2026-10-05: independent endpoint authority lookalikes (offline)

Four additional fake context server strings with misleading local-looking
userinfo, uppercase hostname or a scoped IPv6 address degrade under the
unchanged doctor. The focused Go test passed. Colima was observed stopped;
there was no live API/Git read or reconcile, retained data/backup mutation or
GitHub write. Text fixtures cannot establish endpoint authenticity, atomic
inventory, strict admission or operator acceptance. Revert only these test
cases if needed; keep earlier evidence. Original Vault sync pending.

## 2026-10-05: independent mapped IPv4 range endpoint controls (offline)

The unchanged doctor rejects three more synthetic IPv4-mapped IPv6 endpoints,
including the upper end of the IPv4 loopback range, while accepting a literal
IPv4 loopback near that range's end. The focused Go test passed. Colima was
observed stopped; no live Git/API read, reconciliation, saved-request replay,
retained data/backup mutation or GitHub write. Text fixtures cannot authenticate
the context or server, establish atomic inventory, clear strict admission or
grant operator acceptance. Revert only this independent test if necessary and
preserve prior evidence. Direct original Vault sync remains pending outside
the checkout.

## 2026-10-05: independent hex-mapped endpoint spellings (offline)

The unchanged local doctor rejects two additional fake IPv4-mapped IPv6
loopback endpoint spellings, including a fully expanded form. The focused Go
test passed. No live Git/API read or reconciliation; Colima was stopped.
Fake context text does not authenticate the endpoint or establish atomic
inventory, strict admission or operator acceptance. Revert only the test if
necessary, retaining earlier evidence. Original Vault sync pending.

## 2026-10-05: mapped loopback endpoint ambiguity (offline)

An independent fake context endpoint using IPv4-mapped IPv6 loopback passed
the local doctor before the literal-form guard; two shortened/numeric IPv4
lookalikes were already rejected. Focused Go tests pass after refusing the
mapped form while preserving ordinary IPv4/IPv6 literal loopbacks. This is
offline parsing only: context identity, live API/Git provenance, strict gate
and operator acceptance remain unverified. Colima stayed stopped; no reconcile,
retained data/backup change or GitHub write. Roll back only the guard/test if
necessary and preserve historical evidence. Original Vault sync pending.

## 2026-10-05: independent loopback endpoint lookalikes (offline)

The unchanged local doctor degrades seven fake remote, scoped or hostname
lookalike API endpoints even with the dedicated context name, and accepts two
additional literal loopback addresses. Focused and full Go tests (888) and vet
passed. No DNS resolution, live API/Git read or reconciliation occurred;
Colima was observed stopped. Synthetic context output cannot authenticate the
actual cluster or Git source, establish atomic inventory, clear strict admission
or grant operator acceptance. Revert only this independent test if necessary;
preserve historical evidence. Direct original Vault sync remains pending.

## 2026-10-05: independent dev ReplicaFailure ambiguity (offline)

Fake dev inventory for each fixed Deployment now covers repeated cleared and
contradictory `ReplicaFailure` conditions. The unchanged local doctor degrades
the shared dev list while retaining the independent storage check healthy.
Focused and full Go tests (880), vet and local regression passed. Colima was
observed stopped; no live API/Git read or reconciliation, retained data/backup
mutation or GitHub write. Offline lists cannot authenticate producers, prove
atomic inventory, clear strict admission or grant operator acceptance. Revert
only this test if necessary; preserve earlier evidence. Direct original Vault
sync remains pending outside checkout.

## 2026-10-05: independent cleared ReplicaFailure duplicates (offline)

An independent fake controller inventory pins the unchanged doctor's degraded
result for identical cleared or conflicting duplicate ReplicaFailure conditions
on each of the four fixed Flux Deployments; one cleared condition remains
informational. Focused and full Go tests (879), vet and full local regression
passed. Dedicated Colima was observed stopped; no live API/Git inventory or
reconciliation, retained data/backup mutation or GitHub write. Fake responses
cannot authenticate producers, establish atomic inventory or operator
acceptance. Revert only this test if needed; preserve earlier evidence. Direct
original Vault synchronization remains pending outside checkout.

## 2026-10-05: suspended root duplicate reconciliation (offline)

An independent fake resource inventory pins degradation for identical cleared
or contradictory Stalled/Reconciling entries on the suspended Flux root; one
cleared entry stays informational. The doctor is unchanged. Focused/full Go CLI
tests (878), vet and local regression passed. Offline lists do
not authenticate Git/API, prove atomic inventory or grant operator acceptance.
Revert only this regression if necessary, preserving prior evidence. Direct
original Vault sync remains pending outside checkout.

## 2026-10-05: suspended root duplicate Ready (offline)

A separate fake Flux resource list now pins degraded results for identical
stale Ready entries and contradictory stale/current Ready entries on the
suspended root. One stale Ready remains informational. The doctor is unchanged;
focused/full Go CLI tests (877), vet, design-context and diff check passed.
No live inventory or reconcile occurred; fake responses
cannot authenticate Git/API, prove atomic inventory or grant operator
acceptance. Revert only this test if needed; preserve saved evidence. Direct
original Vault sync remains pending outside checkout.

## 2026-10-05: independent StatefulSet condition ambiguity (offline)

Fake storage and dev lists now independently cover identical and conflicting
duplicate Ready conditions on both fixed StatefulSets. The unchanged doctor
degrades the affected namespace's checks while retaining the other namespace's
healthy result. Focused and full Go CLI tests (876) and vet passed. Colima was
observed stopped; no live inventory, reconciliation or retained data/backup
mutation. This cannot authenticate the producer, prove atomic inventory or
establish operator acceptance. Revert only the test if necessary; preserve
historical evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-05: independent dev workload condition duplicates (offline)

The existing local doctor now has explicit fake-list regressions for identical
and contradictory duplicate Available/Progressing conditions on both dev
Deployments. A duplicate makes the shared dev inventory incomplete: all three
dev checks degrade while the separate storage check remains healthy. A first
test incorrectly assumed only the affected Deployment would degrade; corrected
the assertion to match the actual list-level fail-closed contract. No doctor
behavior changed. Dedicated Colima was observed stopped; no live inventory,
reconciliation, retained data/backup mutation or GitHub write. These synthetic
lists do not authenticate Kubernetes, establish atomic inventory or operator
acceptance. Revert only this test if needed; retain saved evidence. Direct
original Vault sync remains pending outside checkout.

## 2026-10-05: independent condition ambiguity coverage (offline)

The unchanged doctor degrades identical and contradictory duplicate Available
conditions on each of the four fixed controllers, and duplicate cleared versus
current Stalled/Reconciling conditions on the fixed source. The expanded fake
inventories remain independent of live Git/API state. Focused and full Go tests
and vet passed. Colima was observed stopped; no live inventory or reconciliation,
retained data/backup mutation or GitHub write. No producer authenticity, atomic
inventory, strict admission or operator acceptance is established. Revert only
the test extension if necessary, preserving saved evidence. Original Vault sync
remains pending outside checkout.

## 2026-10-05: independent duplicate controller Progressing (offline)

An independent fake inventory pins the unchanged local doctor's degraded
result for identical or contradictory repeated `Progressing` conditions on
each of the four fixed Flux controllers; unique conditions remain
informational. Focused Go and full package tests passed. Colima was observed
stopped: no live Git/API inventory, reconciliation, retained data/backup
mutation or GitHub write. Fake inventory cannot authenticate producers, prove
atomic state, clear strict admission or grant operator acceptance. Revert only
the test if needed; preserve saved evidence. Original Vault sync pending.

## 2026-10-05: independent duplicate source reconciliation (offline)

An independent fake inventory now pins the unchanged doctor's degraded result
for identical repeated cleared Stalled or Reconciling conditions on the fixed
GitRepository source. Focused Go and full local regression passed. Dedicated
Colima was observed stopped: no live Git/API inventory, reconciliation, retained
data/backup mutation or GitHub write. This tests ambiguity refusal, not producer
authenticity, atomic inventory, strict admission or operator acceptance. Revert
only the test if necessary; preserve saved evidence. Original Vault sync remains
pending outside checkout.

## 2026-10-04: independent duplicate catalog Stalled (offline)

The unchanged local doctor degrades fake optional-catalog inventories containing
identical cleared or contradictory Stalled conditions. This pins refusal, not
the exact diagnostic path; focused/full Go, vet and full local regression passed. Dedicated
Colima was observed stopped; no live inventory, reconciliation, retained data/
backup mutation or GitHub write. Fake replies cannot authenticate Git/API or
prove atomic state, strict admission or operator acceptance. Revert only the
test if needed; preserve prior evidence. Original Vault sync remains pending.

## 2026-10-04: independent duplicate catalog Reconciling (offline)

An independent fake inventory with two identical current cleared or stale
Reconciling conditions degrades under the unchanged doctor. It reports an
incomplete resource inventory before the later condition-specific check; this
test establishes refusal, not which later check would fire. Focused and full Go
tests/vet passed. Dedicated Colima was observed stopped; no live inventory,
reconciliation, retained data/backup mutation or GitHub write. Fake inventory
cannot authenticate the API/Git producer, prove atomic state, clear strict
admission or establish operator acceptance. Revert only this test if needed;
preserve historical evidence. Original Vault sync remains pending.

## 2026-10-04: optional catalog reconciliation conditions (offline)

An independent fake inventory confirms the unchanged doctor degrades a
present optional catalog with current Stalled=True or Reconciling=Unknown,
even if Ready=True. Cleared Stalled and stale Reconciling retain the fixed
root's informational result. Focused/full Go tests and vet passed; Colima was
observed stopped. No live Git/API inventory, reconciliation, retained data or
backup mutation, or GitHub write. Fake responses cannot authenticate the
producer, prove atomic inventory, strict admission or operator acceptance.
Revert only this test if needed, retaining prior evidence. Direct original
Vault sync remains pending outside checkout.

## 2026-10-04: independent duplicate source Ready (offline)

An independent fake inventory confirms the unchanged local doctor degrades
both identical and contradictory duplicate `Ready` conditions on the fixed
GitRepository source. Focused and full Go tests and vet passed. Dedicated
Colima was observed stopped; no live Git/API inventory, reconciliation,
retained data/backup mutation or GitHub write. Fake responses cannot
authenticate the source or prove atomic inventory, strict admission or
operator acceptance. Revert only the test if necessary, preserving saved
evidence. Direct original Vault sync remains pending outside checkout.

## 2026-10-04: independent duplicate controller Available (offline)

A fake controller inventory confirms the unchanged local doctor degrades both
identical and contradictory duplicate `Available` conditions. Focused/full Go
tests (869), vet and local regression passed. Dedicated Colima was observed
stopped; no live inventory or
reconciliation, retained data/backup mutation or GitHub write. Fake inventory
does not authenticate producers, prove atomic state, clear strict admission or
grant operator acceptance. Revert only this test if needed, preserving prior
evidence. Direct original Vault sync pending outside checkout.

## 2026-10-04: identical optional catalog Ready duplicates (offline)

An independent fake inventory pins the unchanged doctor's degraded result for
two identical current Ready conditions on the present optional catalog: an
identical duplicate is no more authoritative than a conflicting duplicate.
Focused and full Go tests (868) and vet passed. Dedicated Colima was observed
stopped; no live Git/API inventory, reconciliation, retained data/backup
mutation or GitHub write. The fixture cannot authenticate a producer, establish
atomic inventory, clear strict admission or grant operator acceptance. Revert
only this test if needed, preserving historical evidence. Direct original
Vault synchronization remains pending outside checkout.

## 2026-10-04: optional catalog conflicting Ready (offline)

An independent fake-list regression confirms the unchanged local doctor
degrades a present optional catalog reporting a second conflicting Ready
condition (False or stale), rather than trusting the first current True. No
reader behavior changed. Focused/full Go tests (867) and vet passed.
Dedicated Colima was observed stopped; no live
inventory, reconciliation, retained data/backup mutation or GitHub write.
This fake inventory cannot authenticate API/Git or establish atomic state,
strict admission or operator acceptance. Revert only the scoped test if
needed; retain earlier evidence. Original Vault sync pending outside checkout.

## 2026-10-04: optional catalog incomplete conditions (offline)

An independent fake-list regression pins the unchanged local doctor's degraded
result for a present optional catalog with null, empty or incomplete condition
entries; a complete Ready condition remains informational. Focused and full Go
tests (866) and vet passed. Colima was observed stopped; no live inventory,
reconciliation, retained data/backup mutation or GitHub write. Fake lists do
not authenticate the API or Git, prove atomic inventory, clear strict admission
or grant operator acceptance. Revert only this test if needed, retaining prior
evidence. Original Vault synchronization remains pending outside checkout.

## 2026-10-04: optional catalog object generation (offline)

An independent fake-list regression confirms the unchanged local doctor degrades
a present catalog with absent, zero, negative or null object generation; the
positive-generation fixture remains informational. Focused/full Go tests and vet
passed. Colima was observed stopped; no live inventory, reconciliation, retained
data/backup mutation or GitHub write. A fake list cannot authenticate Git/API,
establish atomic inventory, clear strict admission or prove operator acceptance.
Revert only this test if needed; preserve historical evidence. Original Vault
sync remains pending outside this checkout.

## 2026-10-04: optional catalog impossible Ready generation (offline)

Independent fake-list variants with a present catalog reporting Ready at
generation zero or beyond its own generation degrade under the unchanged
doctor; the current Ready fixture remains informational. Focused Go, full Go
suite/vet, full local regression and design-context passed. Dedicated Colima
remained stopped; no live inventory, reconciliation, request replay, retained
data/backup mutation or GitHub write. This cannot authenticate Git/API,
establish atomic inventory, clear strict admission or prove operator acceptance.
Revert only this test extension if needed; preserve saved evidence. Direct
original Vault sync remains pending outside this checkout.

## 2026-10-04: optional catalog current Ready boundary (offline)

An independent fake-list regression pins the unchanged doctor's degraded
result when the present optional catalog has a stale, failed, or missing Ready
condition. The earlier absent and current-ready cases remain separate. Focused
Go checks, full Go suite and vet passed. Dedicated Colima stayed stopped; no
live inventory or reconciliation, request replay, retained data/backup mutation
or GitHub write. A fake status does not authenticate the producer, prove an
atomic list, clear strict admission or establish operator acceptance. Revert
only this test if necessary, preserving historical evidence. Direct original
Vault sync remains pending outside checkout.

## 2026-10-04: present optional Flux catalog readiness (offline)

The unchanged local doctor now has an independent fake-list regression for
the optional catalog: when present but suspended or terminating it degrades;
when ready it retains the informational result for the suspended root. The
separate absent-catalog test still passes. Focused Go tests, the full Go suite
(863) and vet passed. Dedicated Colima remained stopped; no live inventory,
reconciliation, saved-request replay, retained data/backup mutation or GitHub
write. The fixture cannot authenticate Kubernetes/Git, establish atomic state,
clear strict admission or grant operator acceptance. Revert only this test if
necessary; preserve historical evidence. Original Vault sync remains pending.

## 2026-10-04: absent optional catalog does not fail fixed Flux scope (offline)

An independent complete fake resource list without the optional catalog
Kustomization retains the unchanged local doctor's informational status for
the suspended root. Focused Go test, full Go suite (862) and vet passed. This
pins only the local fixed source/root/dev scope, not a live catalog installation
or correct reconciliation. Dedicated Colima was observed stopped; no live
inventory, Git access, reconciliation, retained data/backup mutation or GitHub
write. Fake replies cannot authenticate producers, prove atomic inventory,
clear strict admission or establish operator acceptance. Revert only the test
if necessary; preserve historical evidence. Direct Vault sync remains pending.

## 2026-10-04: optional catalog namespace boundary (offline)

An independent fake-list regression now checks that the optional catalog
Kustomization is not accidentally promoted into the three fixed resource
namespace identities. The unchanged local doctor remains informational when
only its namespace changes; focused and full Go tests and vet passed. This is
not evidence of a live catalog namespace, producer authenticity or atomic
inventory. Dedicated Colima was observed stopped; no reconciliation, retained
data/backup mutation or GitHub write. Revert only this test if needed and
preserve historical evidence. Original Vault sync remains pending outside
checkout.

## 2026-10-04: independent fixed Flux resource identity regression (offline)

An independent fake inventory test now changes kind for each of the source,
root, dev and optional catalog resources, and namespace for the three fixed
source/root/dev identities. The unchanged doctor degrades all seven mutations;
the complete fixture remains informational. An initial test incorrectly required
the optional catalog to live in `flux-system`; this assumption failed and was
removed before the passing focused test and full Go suite (860) and vet.
Dedicated Colima was observed stopped; no live inventory, reconciliation,
saved-request replay, retained data or backup mutation, or GitHub write.
Offline fixtures cannot authenticate producers, prove atomic inventory, clear
strict admission or establish operator acceptance. Revert only this test if
needed; preserve historical evidence. Direct original Vault sync remains
pending outside checkout.

## 2026-10-04: remaining local resource version regression (offline)

The fixed-profile version test now targets all three named Kustomizations and
the GitRepository separately, with missing, wrong and null versions. The
unchanged doctor degrades all variants; the complete fixture stays
informational. Focused and full Go tests (859), vet passed. Dedicated Colima
was observed stopped; no live inventory, reconciliation, retained data/backup
mutation or GitHub write. Fake inventory cannot authenticate its producer,
prove atomic state, strict admission or operator acceptance. Revert only this
test extension if necessary; preserve earlier evidence. Direct original Vault
sync remains pending outside checkout.

## 2026-10-04: local Flux resource API version identity (offline)

The fixed-profile doctor accepted a fake complete resource inventory with an
omitted GitRepository API version while the controllers declared theirs. A
negative failed first; the local resource check now requires the expected
version for each supported Flux kind (source v1, kustomize v1, HelmRelease v2).
Missing, mismatched and null source/kustomization versions degrade; the valid
fixture remains informational. Existing duplicate-resource tests now permit the
earlier complete-list decoder's "incomplete" diagnostic for a reported GVK,
without relaxing degraded status. `go test ./... -count=1` (859 tests), `go
vet ./...` and diff check passed. Dedicated Colima was read-only observed
Stopped; no live API, reconcile, saved-request replay, retained data/backup
mutation or GitHub write. Fake responses do not authenticate Kubernetes/Git,
prove atomic inventory, clear strict admission or establish acceptance. Revert
only the fixed-profile version guard and its fixtures/tests if needed, retaining
historical evidence. Direct original Vault synchronization remains pending
outside the approved checkout.

## 2026-10-04: independent controller kind and namespace regression (offline)

Each of the four fixed local Flux controllers now has a fake wrong-kind and
wrong-namespace test; the unchanged doctor degrades all eight, while the
unmodified inventory remains informational. The focused Go test, full Go suite
(858) and vet passed. The dedicated Colima profile was observed stopped; no
live inventory, reconciliation, unknown-request replay, retained data/backup
mutation or GitHub write. These fixtures cannot authenticate Kubernetes/Git,
prove atomic inventory, strict admission or operator acceptance. Revert only
the test extension if needed, preserving prior evidence. Direct original
Vault sync remains pending outside checkout.

## 2026-10-04: remaining controller version regression (offline)

The fixed local doctor now exercises absent, wrong and null API versions for
the kustomize and helm controllers as well as the previously covered source and
notification controllers. All fake variants degrade; the reader is unchanged.
Focused Go test, full Go suite (857) and vet passed. The dedicated Colima
profile was observed stopped; no live inventory, reconcile, unknown-request
replay, retained data/backup mutation or GitHub write. Fake responses cannot
authenticate Kubernetes/Git, prove atomic inventory, strict admission or
operator acceptance. Rollback is this test extension only, retaining earlier
evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: both namespace reads and last workload version (offline)

The fixed-profile workload API-version regression now mutates a storage
StatefulSet and the final dev Deployment as well as the first dev workload;
missing, wrong and null versions all degrade. The reader is unchanged. An
initial test-only index error panicked; corrected against the four actual
workload checks, then focused test, full Go tests (857) and vet passed.
Dedicated Colima remained stopped. No live inventory, reconciliation,
unknown-request replay or retained data/backup mutation. Fake lists do not
authenticate the API, prove atomic inventory, clear strict admission or
establish acceptance. Revert only this test extension if needed; preserve
saved evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: last Flux controller API-version regression (offline)

Independently of the policy smoke changes, the fixed-profile Flux doctor was
checked against a missing, wrong or null API version on the **last** of four
controllers, not only the first. The existing guard degrades each fake list;
the valid list remains informational. No doctor implementation changed. The
focused Go test, `go test ./... -count=1` (857 tests), `go vet ./...` and diff
check passed. Dedicated Colima remained stopped; no live inventory, Git/API
write, reconciliation, retained data/backup mutation or unknown-request replay.
These fixtures cannot authenticate remote identity, prove atomic inventory,
clear strict admission or establish acceptance. Roll back only this test
extension if needed; preserve saved evidence. Original Vault sync remains
pending outside checkout.

## 2026-10-04: local lakehouse workload API-version identity (offline)

A complete, test-owned local workload list omitted the `apiVersion` of a
ready Deployment; the doctor reported it healthy before repair. Both fixed
workload kinds now require `apps/v1`, matching the scoped kubectl request.
The focused negative failed before repair; focused lakehouse/Flux tests and
full Go tests passed after fixture updates. The dedicated Colima profile was
observed stopped. No live inventory, reconciliation, request replay, retained
data/backup change or publication. This does not authenticate the producer,
prove atomic inventory, clear strict admission or establish acceptance.
Rollback is the workload version guard and test-fixture updates only; preserve
prior evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: local Flux controller API-version identity (offline)

The local doctor previously accepted a test-owned complete controller list
whose source-controller omitted `apiVersion`. The negative failed before the
fix; the fixed-profile check now requires `apps/v1`, including on all four
controllers. Controller fixtures were updated to include their declared API
version, and two existing tests adjusted to target that updated fixture.
Focused Flux tests (80), full Go tests (856), vet, local regression,
design-context and secret-filename checks passed. Ignored regression log:
`tmp/local-experiment/autonomous-runner/run-s56x9_ej/attempt-106/verify-local.log`.
Dedicated Colima
was observed stopped; no live inventory, reconciliation, retained data or
backup change. This does not authenticate remote identity, prove an atomic
inventory, clear strict admission or establish operator acceptance. Generic
doctor behavior is unchanged. Roll back the local-profile check/test only if
needed; retain saved evidence. Original Vault synchronization remains pending
outside checkout.

## 2026-10-04: non-representable inventory numbers (offline)

Two independently constructed complete-list replies with nonzero numeric
underflow in ignored list/member extensions incorrectly passed the JSON token
walk before repair. Both negative cases failed first; the decoder now retains
number lexemes through the bounded walk and refuses zero-rounded nonzero and
overflowing values, retaining genuine zero and finite exponent compatibility.
Full Go tests (855) and vet passed. Dedicated Colima remained stopped; no Git/API inventory,
reconciliation, retained volume or backup was touched. Fake lists do not
authenticate remote state, prove atomic inventory, clear strict admission or
establish acceptance. Roll back only the decoder/test if needed; keep saved
evidence. Original Vault sync remains pending outside checkout.

## 2026-10-04: escaped duplicate inventory claims (offline)

A separate test-owned list pins existing refusal of JSON-escaped duplicate
`items` in the envelope and duplicate `uid` in an ignored nested extension;
the canonical single extension still passes. The focused test, full `go test
./... -count=1` (852 tests) and `go vet ./...` passed. No reader behavior
changed. Dedicated Colima remained stopped; no live inventory, reconciliation,
retained data or backup mutation. A fake list cannot authenticate the API,
prove atomic inventory, clear strict admission or establish operator acceptance.
Roll back only the test if needed and retain historical evidence. Direct original
Vault synchronization remains pending outside checkout.

## 2026-10-04: independent overflow exponent inventory regression (offline)

Two test-owned complete lists with overflowing exponents in ignored extensions
are already degraded as incomplete by the recursive decoder. Initial focused
assertions expected the invalid-JSON diagnostic and failed; corrected assertions
passed without a reader change. The lab was observed stopped; no live inventory,
reconciliation, retained data or backup mutation. These fixtures cannot prove
producer authenticity, atomic inventory, strict admission or acceptance. Roll
back only the new test cases if needed; keep prior evidence. Original Vault
sync remains pending outside this checkout.

## 2026-10-04: independent non-JSON inventory regression (offline)

Two separate fake lists containing `NaN` or `Infinity` in unknown extensions
already fail the Go complete-list JSON decoder; pinned the degraded result and
fixed invalid-JSON diagnostic. Focused Go test passed, no doctor behavior
change. Dedicated Colima remained stopped; no live inventory, reconciliation,
retained data or backup mutation. Fixtures cannot authenticate the producer,
prove atomic inventory, clear strict admission or establish operator
acceptance. Roll back only the two test cases if needed; keep saved evidence.
Original Vault sync pending outside this checkout.

## 2026-10-04: independent list identity review (offline)

Independently inspected fixed-profile list envelope, metadata aliases and
unknown-extension duplicate-key handling after the separate policy selector
repair. Three focused Go tests passed; no additional reader defect established
and no GitOps behavior changed. The dedicated lab was observed stopped; no
live inventory or reconciliation. These fixtures cannot authenticate remote
state, prove atomic inventory, clear strict admission or grant operator
acceptance. No runtime rollback; retain historical evidence. Original Vault
sync pending outside this checkout.

## 2026-10-04: independent complete-list reader inspection (offline)

Independently inspected the fixed-profile typed list decoder's envelope,
member aliases, reported lifecycle and pagination boundaries after the policy
resource repair. The focused inventory/list Go tests passed (86). No additional
defect was established and no GitOps reader or runtime behavior changed.
Dedicated Colima remained stopped; no live inventory, reconciliation or retained
data/backup change. These offline checks do not authenticate remote state,
establish atomic inventory, clear strict admission or grant operator acceptance.
No runtime rollback; preserve historical evidence. Direct original Vault sync
remains pending outside this checkout.

## 2026-10-04: conflicting list envelope regression (offline)

An independent fake-list test pins the existing refusal of five conflicting
case-variant top-level aliases alongside canonical `items`/GVK, while the
canonical empty list remains compatible. No GitOps reader behavior changed;
the focused Go test passed. Dedicated Colima was observed stopped; no live
inventory, reconciliation, retained data or backup changed. This fixture
cannot authenticate the producer, prove atomic inventory, strict admission or
operator acceptance. Roll back only the new regression test if necessary;
retain saved evidence. Original Vault sync pending outside checkout.

## 2026-10-04: typed remaining-count regression (offline)

Separate fake complete-list responses with boolean or fractional
`remainingItemCount` are rejected by the existing typed decoder, rather than
accepted as an empty inventory. A first regression run failed because it
expected the pagination-specific diagnostic instead of the existing invalid
JSON diagnostic; the final assertions require degraded status and the correct
fixed diagnostic. `go test ./... -count=1` passed (846 tests) and vet passed.
No reader behavior, live Git/API inventory, reconciliation or retained data
changed; dedicated Colima was observed Stopped. Fake responses cannot prove
producer authenticity, atomic inventory, strict admission or acceptance.
Roll back only the regression cases if needed; retain historical evidence.
Original Vault synchronization pending outside checkout.

## 2026-10-04: independent list pagination review (offline)

Reviewed the fixed-profile complete-list decoder's continuation and malformed
metadata handling independently of the policy LimitRange repair. Existing
negative pagination and metadata tests were present; focused inventory/list Go
tests passed (83). No GitOps reader or runtime change was justified. This is
not a fresh authenticated inventory or proof of atomic reconciliation, strict
admission or operator acceptance. Dedicated Colima was observed Stopped. No
runtime rollback is needed; keep existing saved evidence. Original Vault sync
pending outside checkout.

## 2026-10-04: independent condition-array boundary check (offline)

Inspected the unrelated-member typed condition decoder and its explicit-null
checks after the separate fixed-corpus policy repair. The two focused Go tests
passed; no further defect established or GitOps implementation changed. The
dedicated VM remained Stopped; no live Git/API read, reconciliation or retained
data/backup mutation. Offline fixtures do not authenticate a remote producer,
prove atomic inventory, clear strict admission or establish acceptance. No
GitOps runtime rollback; retain prior evidence. Original Vault sync pending
outside this checkout.

## 2026-10-04: typed unrelated conditions regression (offline)

Pinned the Go decoder's existing refusal of non-array `status.conditions` on
an unrelated complete-list member, alongside valid empty and typed conditions.
The proposed reader defect was ruled out before changing implementation; the
focused and full CLI Go suites (844 tests) and vet passed. No live inventory,
reconciliation or retained data/backup change; Colima was observed Stopped.
This cannot authenticate a remote producer, prove atomic inventory, clear
strict admission or establish acceptance. Roll back the test only if needed;
keep historical evidence. Original Vault sync pending outside this checkout.

## 2026-10-04: reported creation and condition regression (offline)

Pinned the existing complete-list refusal of an unrelated condition transition
before a reported creation time, or a malformed reported creation time. An
omitted projected creation and equal timestamp remain compatible. Focused Go
test passed; no doctor implementation changed. Dedicated Colima was observed
Stopped; no live API/Git, reconciliation, retained data/backup or GitHub write.
The fixture cannot authenticate the producer, establish atomic inventory,
clear strict admission or establish acceptance. Roll back the test only if
needed; preserve previous evidence. Original Vault sync pending outside the
approved checkout.

## 2026-10-04: condition transition before creation (offline)

An unrelated inventory member could report a condition transition preceding
its own creation timestamp. An isolated negative failed before repair; the
fixed-profile complete-list reader now compares parsed instants when both
fields are present. Equal instants, offsets and omitted projected fields remain
compatible. This is reported lifecycle consistency, not a trusted clock or
authenticated producer. Dedicated Colima was read-only observed Stopped; no
live API/Git, reconciliation, retained data/backup or GitHub write. Roll back
only the reader and test, preserving evidence and the stopped VM. Strict
admission, Git smoke and operator acceptance remain open. Original Vault sync
is pending outside the approved checkout.

## 2026-10-04: reported condition transition time (offline)

An unrelated member's condition with a null, empty or invalid reported
`lastTransitionTime` passed the complete-list check. A disposable negative
failed before repair. Reported times now require a canonical field and a
parseable RFC3339 instant; omitted projected fields and valid offset times
remain compatible. Focused CLI and full Go tests (841) and vet passed.
Dedicated Colima was read-only observed Stopped; no live API/Git,
reconciliation, retained data/backup change or GitHub write. This verifies
only local response consistency, not producer identity, atomic inventory,
strict admission or operator acceptance. Roll back the reader/test only if
necessary, preserving historical evidence. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: obscured unrelated member UID regression (offline)

A disposable complete-list Service pins the existing refusal of reported UIDs
containing whitespace, a zero-width format character or a control character;
an ordinary UID and an omitted projection remain compatible. No doctor reader
behavior changed. The focused test, `go test ./... -count=1` (840 tests) and
`go vet ./...` passed. Dedicated Colima was read-only observed Stopped; no live
API/Git, reconciliation, retained data/backup change or GitHub write. This
fixture cannot authenticate a producer, establish atomic inventory, clear
strict admission or establish operator acceptance. Roll back the test only if
needed; preserve historical evidence. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: obscured reported member GVK regression (offline)

An unrelated member reporting whitespace or a control/format character in its
kind/API version is already refused by the fixed-profile reader. A targeted
test pins that behavior alongside an ordinary Service/v1 and omitted projected
fields; it passed before any reader change. Focused test and `go test ./...
-count=1` passed (839 Go tests). Dedicated Colima was observed
Stopped; no live API/Git, reconciliation, retained data/backup mutation or
GitHub write. This offline fixture does not authenticate the producer, prove
atomic inventory, clear strict admission or establish acceptance. Roll back
only this test if needed; keep earlier evidence. Original Vault sync pending
outside the approved checkout.

## 2026-10-04: reported null generation regression (offline)

A disposable unrelated member with `metadata.generation: null` is already
refused by the fixed-profile complete-list reader; a targeted regression now
pins that boundary alongside zero, positive and omitted projected values.
No doctor implementation changed. `go test ./internal/cli -run
^TestLocalInventoryRejectsNullGeneration$ -count=1` and `go test ./...
-count=1` passed (838 Go tests). Dedicated Colima observed Stopped; no live
API/Git read, reconciliation, retained data/backup mutation or GitHub write.
This fixture cannot authenticate the producer, prove atomic inventory, clear
strict admission or establish operator acceptance. Roll back only the test if
needed, retaining historical evidence. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: reported lifecycle timestamp order (offline)

An unrelated member reporting deletion before creation passed the fixed-profile
complete-list check even though both timestamps were individually valid. The
focused negative failed before repair; when both times are reported, the reader
now compares parsed instants (including timezone offsets). Equal times and
omitted projections remain compatible. `go test ./internal/cli -count=1` passed
(837 tests). Dedicated Colima observed Stopped; no live API/Git read,
reconciliation, retained data/backup mutation or GitHub write. This local
consistency check cannot authenticate the producer, prove atomic inventory,
clear strict admission or establish operator acceptance. Roll back the reader
and test only if needed; preserve earlier evidence. Original Vault sync pending
outside this checkout.

## 2026-10-04: reported creation timestamp (offline)

An unrelated complete-list member reporting an invalid or empty
`metadata.creationTimestamp` passed as a healthy projection. A disposable
negative failed before repair; reported creation and deletion times now share
the bounded RFC3339 validation, with omission still permitted for projected
fixtures. `go test ./internal/cli -count=1` passed (836 tests). Dedicated Colima
observed Stopped; no live API/Git, reconciliation, retained data/backup mutation
or GitHub write. Fake-list consistency cannot authenticate remote provenance,
prove atomicity, clear strict admission or establish operator acceptance. Roll
back the reader/test only, preserving prior evidence. Original Vault sync
pending outside the approved checkout.

## 2026-10-04: reported empty deletion timestamp (offline)

An unrelated inventory member reporting `metadata.deletionTimestamp: ""`
previously passed as if the timestamp were omitted. A focused negative failed
before repair; explicit values now require a parseable RFC3339 timestamp,
while projected omission remains valid. Go CLI tests (835) passed. Dedicated
Colima was observed Stopped; no live API/Git read, reconciliation, retained
data/backup mutation or GitHub write. This is response-shape consistency, not
remote authenticity, atomicity, strict admission or operator acceptance. Roll
back only the reader/test if necessary, retaining saved evidence. Original
Vault sync remains pending outside this checkout.

## 2026-10-04: unambiguous reported inventory identity (offline)

The complete-list duplicate check joined API version, kind, namespace and name
with slashes; a reported kind containing a slash could collide with a distinct
API version/kind pair. The key now uses four separate fields. A disposable
distinct-pair fixture is accepted and an actual duplicate is still refused.
This does not validate the provenance or semantic GVK of a remote member;
projected fields remain optional. Dedicated Colima observed Stopped; no live
API/Git, reconciliation or retained data/backup mutation. Roll back only the
identity comparison and test, preserving prior evidence. Original Vault sync
pending outside checkout. Offline checks cannot prove atomic inventory, strict
admission or operator acceptance.

## 2026-10-04: bounded inventory extension nesting (offline)

The fixed-profile complete-list reader recursively inspected unknown extension
values without a local nesting limit. A disposable 65-level nested member was
accepted before repair; the duplicate-key/lossy-value walk now refuses nesting
past 64 composite levels, while a 16-level extension remains valid. Focused Go
CLI tests (834) and vet passed. Dedicated Colima observed Stopped; no live
API/Git read, reconciliation, retained data/backup mutation or GitHub write.
This bounds local decoding work, not remote authenticity, atomic inventory,
strict admission or operator acceptance. Roll back reader/test only if needed;
preserve earlier evidence. Original Vault sync pending outside checkout.

## 2026-10-04: explicitly empty member GVK (offline)

In a separate complete-list boundary, a reported empty kind/API version on an
unrelated member previously passed as an omitted projection. Disposable
negatives failed before repair; explicit empty values now fail closed, while
genuinely omitted member fields and a reported `Service`/`v1` remain valid.
The first full Go run exposed an older OCI/Bucket/Helm fixture that intentionally
expected an explicit empty version to pass; its expectation now records the
new incomplete-response refusal. The original failure is retained in this note.
This is list-internal consistency only: no live remote authentication, atomic
inventory, strict admission or operator acceptance. Dedicated Colima remained
Stopped; no retained data/backup or GitHub write. Roll back only this reader
and test if necessary, retaining earlier evidence. Original Vault sync pending
outside checkout.

## 2026-10-04: explicitly empty List GVK (offline)

The fixed-profile reader formerly treated a reported empty `kind` or
`apiVersion` as though it were omitted. A disposable complete-list negative
failed before repair; the reader now rejects these explicit empty claims while
retaining omission for projected fixtures and canonical `List`/`v1`. Focused
Go CLI suite and vet passed. Dedicated Colima observed Stopped; no live API,
reconciliation, GitHub write or retained data/backup mutation. This does not
authenticate the server, establish atomic inventory or clear strict admission.
Roll back only this reader/test change if necessary; preserve saved evidence.
Original Vault sync pending outside the approved checkout.

## 2026-10-04: reported list GVK regression (offline)

A disposable complete-list fixture pins the existing refusal of reported
`kind: Pod` or `apiVersion: apps/v1` on the List envelope. Canonical `List`/`v1`
and omitted projected fields remain valid. The focused negative passed without
a reader change; `go test ./internal/cli -count=1` passed (832). Dedicated
Colima observed Stopped; no live API/Git, reconciliation, retained data/backup
mutation or GitHub write. Fake-list consistency is not remote authentication,
atomicity, strict admission or operator acceptance. Roll back only the test if
necessary; preserve earlier evidence. Original Vault sync pending outside checkout.

## 2026-10-04: separate complete-list inspection after denial repair (offline)

After the policy/FinOps increment, checked `internal/cli/doctor.go` raw
complete-list envelope, member metadata, UID and per-resource-version refusal
paths. No new defect established and no GitOps behavior changed. `go test
./internal/cli -count=1` passed (831). Dedicated Colima was observed Stopped;
no live API/Git read, reconciliation, retained data/backup mutation or GitHub
write. Offline tests cannot authenticate remote inventory, prove an atomic read,
clear strict admission or establish operator acceptance. No runtime rollback;
preserve earlier evidence. Original Vault sync pending outside the checkout.

## 2026-10-04: independent complete-list review after dry-run repair (offline)

Reviewed fixed-profile complete-list identity, pagination and reported member
version boundaries in `internal/cli/doctor.go` after the separate policy
increment. No additional defect established or doctor behavior changed. `go
test ./internal/cli -count=1` passed (831). Dedicated Colima observed Stopped;
no live API/Git read, reconciliation, retained data/backup mutation or GitHub
write. Static review cannot authenticate remote state, prove atomic inventory,
strict admission or operator acceptance. No runtime rollback; retain earlier
evidence. Original Vault sync pending outside this checkout.

## 2026-10-04: independent list-identity review after policy commit (offline)

Reviewed `internal/cli/doctor.go` complete-list envelope, reported member
identity and per-object version checks after the independent policy increment.
No additional defect established or doctor behavior changed. `go test
./internal/cli -count=1` passed (831). Dedicated Colima observed Stopped; no
live API/Git read, reconciliation, retained data/backup mutation or GitHub
write. Static and fake-list checks cannot authenticate remote state, prove
atomic inventory, strict admission or operator acceptance. No runtime rollback;
preserve saved evidence. Original Vault sync pending outside the checkout.

## 2026-10-04: independent fixed-list boundary review (offline)

Rechecked reported member identity, list envelope and projected-field boundaries
in the fixed-profile doctor after the separate policy change. No new defect was
established and no doctor behavior changed. `go test ./internal/cli -count=1`
passed (831). Dedicated Colima observed Stopped; no API/Git read, reconciliation,
retained data/backup mutation or GitHub write. Fake lists cannot establish
remote authenticity, atomic inventory, strict admission or acceptance. No
runtime rollback; preserve saved evidence. Original Vault sync pending outside
the approved checkout.

## 2026-10-04: independent inventory identity review (offline)

Reviewed the current fixed-list envelope and member identity checks in
`internal/cli/doctor.go`: aliases/null/duplicate keys, malformed or duplicate
reported UIDs, resource versions and DNS identities fail closed; omitted
projected fields remain intentional. No additional defect established and no
doctor behavior changed. `go test ./internal/cli -count=1` passed. Dedicated
Colima was read-only observed Stopped; no API/Git request, reconciliation or
retained data mutation. Static review and fake tests cannot authenticate the
remote inventory or establish atomic reads, strict admission or acceptance.
No runtime rollback; keep saved evidence. Original Vault sync pending outside
checkout.

## 2026-10-03: cross-kind UID collision regression (offline)

A disposable complete inventory now checks that a Deployment and Service with
different names but the same reported UID are refused; distinct UIDs pass.
The existing validator already applied this boundary, so no doctor behavior
changed. `go test ./... -count=1` (831 passed) and `go vet ./...` passed.
No VM start, live API/Git, reconciliation or retained data mutation. A fake
complete list cannot authenticate remote inventory, ensure atomic reads, clear
strict admission or establish operator acceptance. Roll back the test only;
preserve stopped VM and historical evidence. Original Vault sync remains pending
outside checkout.

## 2026-10-03: same-namespace duplicate UID regression (offline)

A disposable fixed-profile workload response gives two separate resources in `shirokuma-dev` the same UID. The existing complete-list validator rejects that namespace, while an independent healthy `shirokuma-storage` list remains healthy. No Go reader change. Focused Go test, full Go suite and vet passed; broader checks are in Milestones. No VM start, live API/Git, reconciliation or retained data/backup mutation. Fake-list consistency is not remote authentication, atomic inventory, strict admission or operator acceptance. Roll back only this fixture; preserve stopped VM and earlier evidence. Direct original Vault synchronization remains pending outside checkout.

## 2026-10-03: explicit null replica counter regression (offline)

The existing fixed-profile complete-list reader already refuses explicit `null` in reported spec/status replica counters, distinguishing omission and valid zero. A new isolated disposable test covers all five reported counters and positive zero/omission; no reader or generic doctor behavior changed. Go tests, vet and local verification passed (Milestones). No live API/Git, dedicated VM start, reconciliation or retained data mutation. A fake list does not authenticate remote state, prove atomicity, clear strict admission or establish operator acceptance. Roll back only the regression test, preserving historical evidence. Original Vault direct sync pending outside checkout.

## 2026-10-03: same-GVK duplicate identity on unrelated list members (offline)

Fixed-profile complete lists now refuse two fully reported objects with the same API version, kind, namespace and name even when their UIDs differ or one UID is omitted. A disposable two-member negative passed as complete before repair; different namespaces and kinds remain valid, as do projected entries missing identity fields. An initial name-only guard incorrectly rejected legitimate mixed-kind Flux fixtures; the final guard binds the whole reported GVK and the Go suite passes. No generic doctor behavior changed. No live Kubernetes/Git read, reconciliation, VM start or retained data/backup mutation; dedicated Colima was read-only observed stopped. This is list-internal consistency, not authenticated or atomic remote inventory, strict admission or operator acceptance. Revert only the reader and test if needed, preserving VM and historical evidence. Original Vault direct sync pending outside checkout; verification in Milestones.

## 2026-10-03: escaped duplicate keys in ignored inventory extensions (offline)

A complete fixed-profile list already refuses nested extension keys `owner` / `ow\u006eer` after decoding, both at envelope level and on an unrelated member. A distinct-key extension stays valid. The new disposable regression pins this existing behavior; the reader and generic doctor did not change. Focused and combined verification are recorded in Milestones. Dedicated Colima was observed stopped; no live API/Git, reconciliation, VM start or retained data/backup mutation. A fake list cannot authenticate Kubernetes/Git, guarantee atomic inventory, clear strict admission or establish operator acceptance. Roll back the regression test only and preserve evidence. Direct original Vault sync pending outside checkout.

## 2026-10-03: lossy ignored inventory values (offline)

The complete-list duplicate-key walk now also refuses U+FFFD in decoded string values at any depth, including an ignored extension in an envelope or unrelated item. Go silently maps an escaped lone surrogate to this replacement character. A disposable negative failed before repair; ordinary Unicode extensions remain compatible. Generic doctor is unchanged. Focused Go, vet and repository verification are recorded in Milestones. No live API/Git, VM start, reconciliation or retained data/backup mutation. This cannot authenticate the API/Git or provide atomic inventory, strict admission or operator acceptance. Roll back this reader guard/test only, retaining previous evidence and the stopped VM. Direct original Vault sync pending outside checkout.

## 2026-10-03: lossy unknown JSON keys invalidate complete lists (offline)

The fixed-profile duplicate-key walk now refuses an escaped lone-surrogate key decoded by Go to U+FFFD, even in an otherwise ignored extension at the envelope or member level. A disposable complete-list negative failed before repair; an ordinary extension and the canonical empty list remain compatible. Generic doctor is unchanged. No live API/Git, VM start, reconciliation or retained data/backup change. Fake response shape cannot authenticate the API or Git, make separate reads atomic, clear strict admission or grant operator acceptance. Roll back the key guard/test only and preserve existing evidence; direct original Vault sync pending outside checkout. Verification is in Milestones.

## 2026-10-03: null inventory member regression (offline)

An independent fake-list regression pins the existing rejection of `items:[null]`, including when the null follows another member, while preserving `items:[]` as a valid empty envelope. No reader behavior or generic doctor changed. A valid empty envelope still does not prove expected workloads exist. Focused and broader checks are in Milestones. Dedicated VM stayed stopped; no live API/Git, reconciliation or retained data change. This cannot authenticate Kubernetes/Git, make reads atomic or clear strict admission/operator acceptance. Roll back the test only; original Vault sync pending outside checkout.

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
