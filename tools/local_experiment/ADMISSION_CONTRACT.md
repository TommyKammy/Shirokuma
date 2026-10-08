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

The next concrete dependency is a commit-bound transitive rendered-input and
complete image/workload inventory, still repository-only and non-activating.
Risk disposition requires separate explicit review bound to this evidence; do
not fabricate approval to fill the field. Fetched artifact bytes/commit, scoped
enforcement and applied provenance also remain unverified.

Recovery: revert only the repository implementation/tests/docs if needed. No
runtime/storage fields change; disk/backup/export impact is limited to small
private JSON/test logs. Preserve disks/volumes/PV/PVC identities, original failures
and backups; never restore writers or unsuspend sources/Jobs automatically.
