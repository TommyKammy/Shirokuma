# Exact ARM64 Flux evidence refresh (2026-10-08)

WP-L0-GITOPS-001, `L0_FLUX_EVIDENCE_REFRESH`, worker `gate-3f5odnk2`.
This is **evidence only**, not vulnerability admission or L0 acceptance.
Single OpenAI OAuth/no API-key routing was checked for explicit
`openai/gpt-6.1-sol`; this is not independent billing verification. No other
model, provider, account, nested run, registry write or publication was used.

## Observed result

Installed Trivy 0.72.0 downloaded its public vulnerability DB at
`2026-10-08T06:15:35.041092Z`. DB UpdatedAt is
`2026-10-07T07:38:55.515026687Z`, NextUpdate
`2026-10-08T07:38:55.515026457Z`. The DB is about 22.6 hours old at collection,
not the approximately 55-day-old retained baseline. Scans use this isolated DB
with automatic DB updates disabled; full fresh JSON reports remain private.

| Exact retained component | Scan CreatedAt (UTC) | HIGH | CRITICAL |
| --- | --- | ---: | ---: |
| source-controller | 2026-10-08T06:15:41.096231Z | 21 | 0 |
| kustomize-controller | 2026-10-08T06:15:49.296716Z | 36 | 0 |
| helm-controller | 2026-10-08T06:15:58.587474Z | 18 | 0 |
| notification-controller | 2026-10-08T06:16:08.175438Z | 19 | 0 |

**94 HIGH occurrences / 42 distinct HIGH IDs**, versus retained 25 / 10:
32 additional IDs, no old HIGH ID removed. This is an advisory-evidence delta,
not new image bytes or proof of exploitability. Every reference and ARM64/Linux
image config matches the existing ledger; no pin or ledger evidence is replaced.
Strict HIGH rejection, missing/null riskDecision, admission=false and gate=false
remain. No exception or risk approval was added.

## Private evidence and provenance

Directory: `tmp/local-experiment/flux-refresh/refresh-9k8bpz7j/` (0700):

- `scanner-version.json`, four `<component>.trivy.json` full scan reports;
- four `<component>.config.json` anonymous Crane registry observations;
- four `<component>.sbom.json` independently generated Syft CycloneDX reports;
- command start/finish receipts, stdout/stderr and isolated `scan-work/` cache;
- `collection-started.json`, `collection-result.json`, `refresh-observations.json`;
- `generated-mode-observation.json` preserves the privacy-check finding: Trivy
  explicitly created two DB cache files 0644 despite umask077. Only those newly
  generated cache files were normalized to 0600; bytes were not changed. The
  collector now normalizes generated cache modes and rejects links/foreign owners.

All checked regular files are owned0600. Reports were exclusive-created, and
prior retained evidence was neither overwritten nor deleted. Post-scan DB hash:
`5f4b978a55284b1997dc31e9f2fc3f4f1abae80829f51451ade221d5675a69b9`;
metadata hash `f39615f304c18b519f3a7cb0aa2f77bb923a43177a3977bfdf260f6b410f63cc`.
Ledger hash remains
`f47d1032edd212bed88599803bd5137e363f1c3d7454f642abab5c2e7b52cf3f`.
Observation JSON retains individual scan/SBOM hashes, exact findings and times.

Publisher observations remain **separate and historical**. Cosign is installed,
but no fresh signature verification is claimed: retained publisher signatures
bind parent indexes, outside the four authorized ARM64 image inputs. Their
record hash is retained separately; successful scans/SBOMs do not renew publisher
proof or weaken issuer/identity/transparency requirements.

Verification: **12 focused refresh tests pass**, design-context and diff checks
pass. `make verify-local-experiment` is not green. Its first invocation inherited
scanner umask077: an intentionally unsafe credential fixture became private,
so the mocked-Docker test attempted a refused loopback readiness read and failed
its expected error assertion. No real Docker startup occurred. A separate
fixture-correct child umask022 run passed that stage and the policy166 tests,
then failed four warehouse lifecycle tests with `AF_UNIX path too long` under
the required mission-local temporary directory. Both logs/results are retained;
no short external-temp workaround, unrelated code repair or runtime replay.
Earlier policy failures remain historical evidence, not retroactively erased.

## Collection and recovery

`flux_refresh_scan.collect(root, mission_directory)` is an explicitly called
installed-tool collector, not a runner or scheduler. Its command plan uses one
public DB repository, exact image digests, `--image-src remote`, and
`syft registry:`; no Docker/containerd/Podman source or image execution. An empty
private Docker config prevents host credential/helper use. HOME, XDG caches,
Syft/Cosign cache settings and all temporary files are mission-local. No global
install/settings or shared scanner-cache writes. Operation receipts reserve work
exclusively; failures preserve bytes and inhibit replay. Polling guards stop at
7 GiB generated/17 GiB free, conservatively before the 8/16 GiB mission limits.
The public DB reference uses its schema tag; **image inputs never use tags**.

Collection completed at 06:16:11.972430 UTC. Scanner evidence occupied about
1.343 GiB; free space remained about 2272 GiB. All Colima profiles, including
the dedicated VM, were independently observed Stopped before/after collection;
no VM/runtime/API changes were made. No resident component or retained storage,
PV/PVC, backup/export change. Keep all new cache/report bytes; no auto-cleanup.
Rollback is repository-only removal/reversion of this increment by a later
authorized change, not deletion of private evidence or rewriting old failures.

Remaining dependencies: independent parent review of exact digest/DB/time evidence;
separately scoped immutable-pin/fixed-package remediation for the current HIGHs;
fresh publisher proof with the proper index-to-ARM64 binding; the still-missing
local risk/admission decision and enforced/fetched/applied source provenance.
None is authorized by this evidence-only mission. Runtime successors, transport
selection, apply/unsuspend, policy activation and full L0 acceptance remain held.
Original Vault is untouched; sync is pending through an authorized parent.
