# PostgreSQL 18.4 evidence checkpoint

The exact Chainguard index and linux/arm64 manifest are selected candidates, not
resident admission. The observations in `../admission.json` are deliberately
non-authoritative because their cryptographic bundles and scan artifacts have
not yet been retained in Git.

Before an evidence-only admission review, retain and independently reverify the
signed index and arm64 manifest, transparency material, SLSA v1 and SPDX
attestations, CycloneDX SBOM, and a fresh Trivy result with zero High and
Critical findings. Confirm the immutable digest remains anonymously retrievable
or that an approved access entitlement exists. PostgreSQL must enter the
resident-image ledger atomically with Polaris.
