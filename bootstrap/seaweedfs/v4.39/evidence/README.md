# SeaweedFS 4.39 admission evidence

This directory is the durable, repository-retained evidence for the exact
SeaweedFS image named by `../release-evidence.json`. The GitHub Actions artifact
is a 90-day downloadable mirror; it is not the only retained copy.

Each file path and SHA-256 digest is recorded in `../release-evidence.json`.
`tests/test_object_storage_profile.py` verifies that the files exist, are not
symlinks, and still match those hashes. The SBOM, Trivy report, scanner metadata,
Cosign verification, SLSA verification, and bounded runtime-smoke evidence must
be replaced together after a new publication run.

These files approve the repository-controlled build artifact only. Runtime
manifests remain blocked until the parent work adds a source-build supply-chain
record and proves its proposed resident-image ledger entry passes
`scripts/verify_supply_chain.py check-images`.
