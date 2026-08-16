# Flux v2.9.2 resident-image evidence

This directory retains the 2026-07-14 `linux/arm64` signature, provenance, and
SBOM evidence for the four-controller Shirokuma Flux profile. The vulnerability
reports were refreshed on 2026-08-14 against the same immutable digests with
Trivy 0.72.0 and vulnerability DB timestamp
`2026-08-14T01:10:44.597550261Z`.

`supply-chain.json` records the exact signed OCI index, GitHub Actions OIDC
identity, transparency-log index, SLSA provenance subject, and upstream SPDX
SBOM subject for each platform digest. The `*.cdx.json` files are CycloneDX 1.7
image SBOMs generated from those exact digests. The `*.trivy.json` files are the
retained vulnerability scans bound through `Metadata.RepoDigests`.

The exact refreshed reports and observed blocking findings are:

| Component | Scan SHA-256 | High | Critical |
|---|---|---:|---:|
| source-controller v1.9.3 | `e30437cbd9f82ed6a6f8388d534f8e5f4aa41445b870fd542e37a8e363f62752` | 7 | 0 |
| kustomize-controller v1.9.3 | `e818eadafd8de0bfbd3817462ba9b27539044624ea3a309288b64ad4187dcb33` | 6 | 0 |
| helm-controller v1.6.2 | `a84640be06b89e07c30d602dec8be8194ed76371c801245a39c1ce911b38feb1` | 6 | 0 |
| notification-controller v1.9.2 | `81ef8a0a8f23cf0d320866ba5a4d5b6327c0feee1b2a68807cb9e57ca97cdb07` | 6 | 0 |

The same refresh retains the exact RBAC blocking projection for the canonical
generated manifest. `gotk-components-v2.9.2.trivy-config.json` has SHA-256
`00e87fef815ac9a99401f2a450e71c47555fd991ec6d2cfc7313e1f0dbe3bd7a`
and binds manifest SHA-256
`ed307189fd1f9e49819a50843bb6f3c9257fe6d4d8359d1950b38207c26c3854`.
It contains one Kubernetes config result with exactly `KSV-0041` Critical x1
and `KSV-0046` Critical x8. The raw `trivy --version --format json` evidence is
`gotk-components-v2.9.2.trivy-version.json`, SHA-256
`a82d05e076fd54c9bd2e57fd1be00891a2384a3f618e9d72037bfd940a5406ea`;
it records Trivy 0.72.0 and check bundle
`sha256:1583562f8b90ed2a071b99f0e5ffff6b57e4ceb6ca3e4796577b4e6a339eb74c`.
The verifier closes the report path, raw hashes, tool/check metadata, result
shape, IDs, titles, severity, status, namespace, query, and counts. This
retained Critical-only projection does not replace the unfiltered all-severity
CI reporting scan.

These images are approved only for the `mac-studio-solo/local-lab` profile
under ADR-0019, the exact [Issue #150 final corrective OWNER authorization](https://github.com/TommyKammy/Shirokuma/issues/150#issuecomment-5290345820),
and `security/resident-image-exceptions.json`. Its `expires_on` is 2026-09-13,
which the verifier treats as fail-closed on that date. The accepted set is
exactly 25 `(advisory, severity, package, installed version, fixed version)`
tuples. A strict profile, an expired exception, tuple or scan-hash drift, a
duplicate or additional High finding, or any Critical finding remains
fail-closed. Automatic renewal is forbidden.

The exception gate policy-binds the exact OWNER login, association, comment,
comment timestamp, and Issue body hash. Each controller exception also binds
the retained report path and bytes, report creation time, Trivy version, and
vulnerability DB timestamp to the resident ledger. CI separately captures a
fresh unfiltered config JSON and verifies the exact nine RBAC finding
identities and counts before the blocking scan applies `.trivyignore.yaml`.

The evidence can be reproduced with commands of this form:

```bash
crane digest --platform linux/arm64 ghcr.io/fluxcd/<controller>:<version>
cosign verify \
  --certificate-identity-regexp='^https://github\.com/fluxcd/gha-workflows/\.github/workflows/controller-release\.yaml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com \
  ghcr.io/fluxcd/<controller>@<signed-index-digest>
syft scan --platform linux/arm64 ghcr.io/fluxcd/<controller>@<arm64-digest> \
  -o cyclonedx-json=<controller>.cdx.json
trivy image --platform linux/arm64 --severity HIGH,CRITICAL --format json \
  ghcr.io/fluxcd/<controller>@<arm64-digest>
trivy config --format json --severity CRITICAL \
  --output security/evidence/flux-v2.9.2/gotk-components-v2.9.2.trivy-config.json \
  deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml
trivy --version --format json
```
