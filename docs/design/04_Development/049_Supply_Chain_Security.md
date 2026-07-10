---
project: Shirokuma
doc_id: "DEV-049"
title: "Supply Chain Security"
status: draft
created: 2026-07-05
updated: 2026-07-05
version: "0.2"
area: "development"
tags: [shirokuma, security, supply-chain]
---

# Supply Chain Security

## Threat model

AI Coding Agentは、善意で悪性コードを実行するリスクがあります。特に、unknown repository、postinstall scripts、curl|bash、obfuscated scripts、malicious branch names、package typosquattingに注意します。

## Controls

| Control | Tool/Practice |
|---|---|
| Dependency pinning | lock files, digest pinning |
| SBOM | syft |
| Vulnerability scan | osv-scanner, grype, trivy |
| Secret scan | gitleaks |
| Sandbox | devcontainer, no host mount secrets |
| Install review | dependency changes require human review |
| Script allowlist | only known scripts in AGENTS.md |
| Network controls | no arbitrary outbound in CI where possible |

## Agent rules

- Unknown install instructionsをそのまま実行しない。
- 依存追加はPRで理由を書く。
- postinstall hooksがある場合はSecurity labelを付ける。
- `curl|bash`は禁止。
- generated codeにlicense header/third-party attributionが必要な場合は明記する。
