# Flux v2.9.2 resident-image evidence

This directory retains the 2026-07-14 `linux/arm64` admission evidence for the
four-controller Shirokuma Flux profile. The audit used Crane 0.21.7, Cosign
3.1.1, Syft 1.46.0, and Trivy 0.72.0 with vulnerability DB timestamp
`2026-07-13T19:09:56.237113526Z`.

`supply-chain.json` records the exact signed OCI index, GitHub Actions OIDC
identity, transparency-log index, SLSA provenance subject, and upstream SPDX
SBOM subject for each platform digest. The `*.cdx.json` files are CycloneDX 1.7
image SBOMs generated from those exact digests. The `*.trivy.json` files are the
retained vulnerability scans bound through `Metadata.RepoDigests`.

The observed blocking findings were:

| Component | High | Critical | Exception |
|---|---:|---:|---|
| source-controller v1.9.3 | 2 | 0 | CVE-2026-49478, CVE-2026-50163 |
| kustomize-controller v1.9.3 | 0 | 0 | none |
| helm-controller v1.6.2 | 2 | 0 | CVE-2026-39822, CVE-2026-50163 |
| notification-controller v1.9.2 | 1 | 0 | CVE-2026-39822 |

These images are approved only for the `mac-studio-solo/local-lab` profile
under ADR-0019 and `security/resident-image-exceptions.json`. A strict profile,
an expired exception, a changed package/version, any additional High finding,
or any Critical finding remains fail-closed.

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
```
