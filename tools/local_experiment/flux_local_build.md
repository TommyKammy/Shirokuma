# Isolated source-controller build observation (2026-10-08)

## Final Go toolchain repair (`gate-ayqdekwa`)

Second/final same-approach execution, retained separately in
`tmp/local-experiment/flux-local-build/build-don5ufj3/`. **Binary-only evidence;
image, publisher, admission and L0 acceptance remain false.**

Public `https://go.dev/dl/?mode=json&include=all` lists stable `go1.26.6`.
Downloaded once from `https://go.dev/dl/go1.26.6.darwin-arm64.tar.gz`:
64,772,321 bytes, official metadata SHA256
`2dc95ce4675829f2df0e86b28bcef3283635902062a5f0580ca659bf570f3204`,
verified before extraction. Private toolchain reports `go1.26.6 darwin/arm64`;
all Go commands use its explicit path and `GOTOOLCHAIN=local`. No global install,
automatic alternate toolchain, shared cache or old-scope write.

Reconstructed from the retained unpatched archive (SHA25663cec74b) of the exact
commit456fa323ef6a4b977f220cf2c60298bf193497ba/tree8c7afc98, without fetching
the source tag or copying `.git`/old Go caches. Retained full patch SHA25654419682.
`git apply` inside the enclosing checkout returned0 but skipped the root-relative
patch; checksum validation caught the no-op before compilation. Receipt and
unpatched module files preserved; no replay. Copied only the two prior patched
go.mod/go.sum files after verifying their recorded hashes, with all other source
files from the verified archive. This preserves exactly the original six-module
and transitive patch, with no new source/dependency behavior changes.

Same Linux/ARM64 build flags as the first attempt. One owned0600, single-link,
static AArch64 ELF, 94,175,394 bytes, never executed, SHA256
`899cfcbcf2c9f3813a917a2b12f50f799bc81dc5dff5e99b9bd1f1423828cf36`.
Go metadata and binary-derived Syft CycloneDX agree on stdlib1.26.6 and all six
fixed versions listed below. New SBOM318 components; fresh Trivy0.72.0 offline
scan317 packages at **2026-10-08T07:08:31UTC**: **HIGH0/CRITICAL0/MEDIUM2/LOW2/
UNKNOWN1**. The exact five non-HIGH findings below are preserved, not ignored.
This is disappearance of the eight stdlib HIGH findings in the **binary** scan,
not full-image clearance or runtime functionality evidence.

Copied only the necessary prior DB/metadata into a private new scanner cache,
verified both hashes, and scanned anew; no DB retrieval or old-cache writes.
DB UpdatedAt2026-10-07T07:38:55.515026687Z/downloaded2026-10-08T06:45:38.694613Z,
unchanged from the retained first snapshot. Initial DB-copy reservation was
refused while compiler temporary files consumed headroom; no file-copy operation
started then. After known build exit0 and temporary-file retirement, the initial
copy fit the guard. Both preflight hold and subsequent copy provenance retained.

`go mod verify` and full module-list observation passed. Host-native upstream
tests used `-mod=readonly -vet=off -p=1 -count=1 -json`, GOMAXPROCS2 and the existing
helper's512MiB reserve (7.5GiB write guard below hard8GiB). This guard terminated
the run with known exit-15 after **16 test/subtest passes/two package passes**;
this is an incomplete test run, not upstream test acceptance. Receipt/stdout/stderr
remain, no replay. API tests were **not started** after the capacity hold. Controller
envtest/OCI integrations/race/signature tests remain excluded; no signature replay.
Final allocation approximately7.569GiB, with the old7.598GiB reservation retained
for combined accounting (approximately15.167GiB, below16GiB); no quota reset or
cleanup. No scope-specific compiler/test process remained at the read-only check.
The later old-scope allocation observation was lower than the prior reservation;
keep the larger booked count, not a quota reset. Named old source/patch/module/
binary/SBOM/scan hashes reproduce; full old-cache byte identity was not independently
proved, and no explanation for the allocation difference is asserted.
Tests and final scope observation are recorded in the matching WP/Milestones.
Archive extraction now verifies checksum/size before writes and rejects traversal,
links, special files and duplicate names; source files are nonexecutable and every
extracted directory private. Focused tooling18 tests pass. Broad local verifier
exit2: warehouse lifecycle115 tests have four `AF_UNIX path too long` errors in
the required private TMPDIR, retained without unrelated repairs or an external
temp workaround. No terminated signature-test replay.
Final design-context, repository secret-scan and diff checks pass.
New/old mission scopes each remain bounded8GiB, combined16GiB, free16GiB required;
old conservative7GiB hold/counters are not reset or erased. Preserve all generated
inputs/caches/failed receipts. Original Vault sync pending; repository-only rollback.

The exact candidate image identities/base declarations/other-controller dependencies
below remain unchanged: no immutable base digest, assembled image, image digest or
publisher proof established. No OCI assembly/execution, VM/container start,
pin/ledger/riskDecision changes, approval, policy/source/apply/unsuspend/activation,
nested runner or later-batch authorization. Parent must independently inspect the
new binary metadata and scope. This second attempt exhausts the unchanged approach;
stop/no_safe_work for this batch, with incomplete tests/capacity/full-image gates
held. No successor is scheduled.

## Retained first attempt

WP-L0-GITOPS-001 / `L0_FLUX_LOCAL_BUILD`, worker `gate-xezx_z6k`.
Private retained evidence: `tmp/local-experiment/flux-local-build/build-aoa39q7w/`.
**Patched binary evidence only; remaining vulnerabilities and image gate held.**

## Identity and reproduction

Anonymous single tag fetch of `https://github.com/fluxcd/source-controller.git`
`refs/tags/v1.9.6` resolved to commit
`456fa323ef6a4b977f220cf2c60298bf193497ba`, tree
`8c7afc98517458261d6713f8c28668c5e2d14107`. Both were recorded before patching.
`source-snapshot.stdout` is the unpatched `git archive --format=tar` output;
`source-final-diff.stdout` preserves the complete dependency-only patch.
The retained candidate config revision independently matches that source commit;
this is not signature/publisher verification.

Installed Go `go1.26.5 darwin/arm64`; no toolchain download/global install.
All Go operations use private HOME/GOCACHE/GOMODCACHE/GOPATH/TMPDIR, `GOENV=off`,
public `proxy.golang.org`/`sum.golang.org`, `CGO_ENABLED=0`, isolated Git config.
Command receipts record exact argv/environment/cwd/times/returncodes/output hashes.

Dependency patch command, followed by `go mod tidy` and `go mod verify`:

```text
go get github.com/go-git/go-git/v5@v5.19.2 github.com/sigstore/fulcio@v1.8.6
  golang.org/x/crypto@v0.55.0 golang.org/x/mod@v0.40.0
  google.golang.org/grpc@v1.83.2 oras.land/oras-go/v2@v2.6.2
```

Retain the recorded full module build list/go.mod/go.sum: minimal version selection
also upgraded transitive dependencies. No source/API/CRD behavior patch was made.
From the detached patched source, with GOOS=linux, GOARCH=arm64, GOMAXPROCS=4:

```text
go build -mod=readonly -p=4 -trimpath -tags=netgo,osusergo,static_build
  '-ldflags=-s -w' -o <mission>/source-controller ./main.go
```

One nonexecuted owned0600, single-link, statically linked AArch64 ELF64 binary,
94,109,858 bytes, SHA256
`9dc840eea23a3c5ef6c40f07f4fbd5e930770de2d4068688b28f04f5bd2ec596`.
`go version -m` and Syft agree on all six requested fixed-version dependencies.
`build-observation.json` binds source/archive/patch/metadata/SBOM/scan hashes.
This cross-compiled binary was never executed; no Linux runtime functionality claim.

## Tests and fresh scan

Host-native upstream `go test -mod=readonly -p=3 -count=1 -json -timeout=8m`
passed cache/error, all internal Helm packages, index/object/predicates,
reconcile/summarize and util: **15 tested packages, 482 test/subtest passes**.
Separate API module tests passed **12 tests/subtests**. CGO/race not exercised.
Controller envtest was not run: required API/etcd assets are not installed host
tools. OCI signature integration tests assemble/push fixture images and are
outside this mission. A narrowed signature-unit test attempt terminated -15
after the monitor encountered a concurrently removed Go compiler file; logs and
receipt retained, no replay. Monitor now ignores only vanished entries, not
permission/other errors; regressions cover the race and no-replay behavior.

Syft file cataloging produced CycloneDX with **317 Go components plus the main
application**, including stdlib1.26.5. Trivy0.72.0 offline SBOM scan at
06:47:50UTC (exact CreatedAt in report) detected **317 gobinary packages**:
**HIGH8, CRITICAL0, MEDIUM2, LOW2, UNKNOWN1**. DB UpdatedAt
2026-10-07T07:38:55.515026687Z, DownloadedAt2026-10-08T06:45:38.694613Z.
DB hash/metadata and binary-to-SBOM-to-scan binding retained. No vulnerability
ignore, approval, exception or riskDecision changed.

All eight HIGH findings concern stdlib1.26.5: CVE-2026-33818, -39821, -46600,
-56853, -56858, -56859, -56860, -56862. DB lists stable1.26.6 fixes, not retrieved
or independently built. x/crypto0.55.0 retains MEDIUM CVE-2026-56855/-78662
(listed fixed0.56.0) and UNKNOWN GO-2026-5932. LOW findings affect sigstore-go1.2.0
and OTel SDK1.44.0. Requested dependency versions therefore do **not** imply
vulnerability clearance; binary-only findings are not an image-to-image comparison.

Preserved scan failures: passing a regular ELF file to Trivy `rootfs` emitted no
Results, **not** a zero-finding scan. The first SBOM invocation rejected the
unsupported `--skip-check-update` flag before scanning. A distinct corrected
SBOM invocation with offline/telemetry-disabled flags produced the above findings.
Neither failed/empty report was overwritten or represented as clearance.

## Capacity hold, remaining scope and recovery

DB download exited0 with completion log and valid DB metadata, but the monitor's
conservative7GiB guard raised at process exit, leaving original disposition
`unknown`. A separate read-only reconciliation records known exit0, DB hash and
completion; **no download replay**. The helper now records guard error and known
terminal outcome separately, and counts allocated blocks as well as logical size.
Only bounded inspection used a256MiB reserve below the hard8GiB limit after the
bulk7GiB guard. **7.592GiB retained**, over2220GiB free. No cleanup. Further builds/
toolchain retrieval are held: insufficient safe headroom for Go1.26.6 and a new
cache while preserving all inputs. The parent supervisor's separate7GiB guard
will also hold its acceptance path; this observation does not alter that guard.

Candidate image, not an adopted pin: source-controller parent index
`sha256:6a6693172589f8ff26123a231d5fa6ceb194a6efb4dc647cdf057c959f76a2e3`,
ARM64 child `sha256:5f2e5cd101b7b5e22afa9d555ba58afb54c5bbc099ac814deb993ee2e982592f`.
Dockerfile declares `alpine:3.23`, `golang:1.26-alpine`, `tonistiigi/xx:1.9.0`;
candidate history says Alpine minirootfs3.23.6. **No immutable base digest proven**,
no locally assembled image/image digest, no publisher proof. Do not invent pins.
Kustomize1.9.6/Helm1.6.5/notification1.9.4 candidate HIGH5/5/9 remain independent
repair dependencies; their exact parent/child identities remain in the original
candidate-inputs.json, untouched. Base packages/full-image scan, publisher binding,
source/admission/enforcement and live L0 acceptance remain open.

Repository verification:12 focused build/supervisor tests, design-context, secret
scan and diff checks passed. `make verify-local-experiment` exited2: warehouse
lifecycle115 tests had four `AF_UNIX path too long` errors under required mission
TMPDIR. No shorter outside-cache workaround or unrelated repair; original broader
failures remain retained. Final generated allocation approximately7.598GiB.

Fresh before/after profiles are byte-identical and all Stopped. Ledger/pins,
retained evidence/disks/PV/PVC/backups and old holds/counters unchanged. No image
assembly/execution, VM/container start, source/apply/unsuspend, policy/admission
activation or nested runner. Original Vault untouched; sync pending.
Rollback repository changes only; retain all generated mission evidence/caches
and failed logs. Stop/no_safe_work; no later batch scheduled by this observation.
