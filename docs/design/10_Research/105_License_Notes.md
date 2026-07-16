---
project: Shirokuma
doc_id: "RES-105"
title: "License Notes and Watchlist"
status: draft
created: 2026-07-05
updated: 2026-07-16
version: "0.3"
area: "research"
tags: [shirokuma, license, watchlist]
---

# License Notes and Watchlist

This note is not legal advice. It records engineering assumptions and review
triggers.

## Key watchlist

| Component | License / boundary | v0.2 interpretation | Primary source |
|---|---|---|---|
| SeaweedFS | Apache-2.0 | Primary single-node object store for `mac-studio-solo`; its optional built-in Iceberg catalog does not replace mainline Polaris. | [license](https://github.com/seaweedfs/seaweedfs/blob/master/LICENSE) |
| MinIO | AGPL-3.0; public CE repository archived and source-only | Pinned-image or source-build fallback only, with exact digest, CVE state, and replacement plan. | [repository](https://github.com/minio/minio) |
| RustFS | Apache-2.0 repository license; upstream trademark and commercial-service boundaries are separate | **Desk review result dated 2026-07-16: remain experimental.** The permissive code license does not establish governance maturity, vulnerability acceptability, S3/Iceberg interoperability, or resident-image admission. | [license](https://github.com/rustfs/rustfs/blob/main/LICENSE), [repository](https://github.com/rustfs/rustfs) |

## RustFS license review

The repository reports SPDX `Apache-2.0` and carries the standard Apache License
2.0 text. That is acceptable for source use and modification under the license
conditions, but it is only one promotion input. RustFS branding remains a
trademark, and the repository does not turn hosted services or third-party
dependencies into Apache-2.0 merely by aggregation. A later implementation must
retain the exact release SBOM and review dependency licenses independently.

No resident image or distribution decision is made here. The complete maturity,
security, and interoperability rationale is in
`docs/design/10_Research/108_RustFS_Maturity_Assessment.md`.

