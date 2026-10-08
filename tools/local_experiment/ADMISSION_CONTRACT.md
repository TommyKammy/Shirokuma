# Non-activating local evidence contract (WP-L0-GITOPS-001)

`admission_contract.build(root, candidate, items, baseline)` reads only local
Git objects and repository evidence. Supply a fresh complete inhibited-runtime
observation and the retained resource baseline; save the returned record privately
with exclusive 0600 creation. There is no activation CLI or runtime writer.
Run `make test-local-admission-contract` for regression tests.

The contract binds the **entire** inhibited candidate (including private UID/spec
guards), full commit/tree/paths, four exact ARM64 image references, ledger SHA256,
and each SBOM/publisher/scan artifact's repository path and SHA256. All evidence
bytes must match blobs in the selected candidate commit, not merely dirty checkout
files. Repeated reads reject artifact/ledger changes during construction.

Each controller retains strict validator outcomes, recorded publisher identity,
scanner/DB version/date and scan creation time. Exact HIGH **and CRITICAL** finding
records preserve target/class/type, package/version/fix/path metadata and repeated
findings. Retained August HIGH counts 7/6/6/6 and CRITICAL0 remain rejected by strict
scan checks. No exception is supplied or renewed. A passing publisher record is
not freshly verified cryptography; old scans are not a current advisory rescan.

Risk decision is `missing`/null and transitive render inventory `missing`/null.
Admission, gate acceptance and activation authorization always remain false.
`validate(root, contract, candidate, items, baseline)` requires exact regeneration
against fresh observations and unchanged evidence. Missing findings, altered
hashes, foreign candidates, added approvals or any other record change reject.
A saved contract never authorizes fetch/apply, transport selection, unsuspension,
new resources, policy binding changes or retained writer start.

## Complete inventory extension

`build_complete(root, candidate, items, baseline, staging, binary='kubectl')`
regenerates the commit-bound transitive inventory and admission diagnosis, then
binds their full records and SHA256 values to the full inhibited candidate.
`validate_complete` requires exact regeneration with fresh scoped observations;
recomputing hashes after omitting an occurrence, finding or unavailable reference
does not pass. Run `make test-local-complete-admission-contract`.

All nine rendered image occurrences (eight distinct references, including the
repeated Polaris image) retain their exact workload/container scope, commit-bound
ARM64 ledger identity, SBOM/publisher/scan artifact paths/hashes, publisher identity,
scanner/DB dates and complete blocking findings/multiplicity. Five distinct Secret
identities/19 repository-unavailable references remain explicit, not claims of live
absence. Missing ledger identities remain diagnostic gaps, never silent omissions.
The ledger ARM64 label is not verified image bytes. Strict Flux HIGH7/6/6/6 remains
rejected; five non-Flux occurrences have retained HIGH0/CRITICAL0 checks.

Risk decision stays missing/null; admission/runtime-input completeness/activation/
gate flags stay false, fetched/applied commits null and transport unselected.
This extension replaces neither the original four-controller contract API nor
its existing saved record format. There is still no network or runtime-write API.

The next concrete dependency is disposition of the exact blocking findings and
missing local-only risk decision against this complete evidence, without activation.
Risk disposition requires separate explicit review bound to this evidence; do
not fabricate approval to fill the field. Fetched artifact bytes/commit, scoped
enforcement and applied provenance also remain unverified.

Recovery: revert only the repository implementation/tests/docs if needed. No
runtime/storage fields change; disk/backup/export impact is limited to small
private JSON/test logs. Preserve disks/volumes/PV/PVC identities, original failures
and backups; never restore writers or unsuspend sources/Jobs automatically.
