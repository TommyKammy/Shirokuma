# Read-only local admission diagnosis (WP-L0-GITOPS-001)

`admission_diagnosis.diagnose(root, candidate, items, baseline)` checks the
existing inhibited source contract against fresh complete runtime observations,
then evaluates the four exact ARM64 resident-ledger identities with the existing
strict SBOM, scan and publisher-record validators. No exception is supplied.
Each result retains input hashes and a separate pass/blocker for each artifact.
Run `python3 -m unittest discover -s tests -p 'test_admission_diagnosis.py'`.

This function has no activation CLI and makes no runtime writes or network
requests. It always reports admission and gate acceptance **false**, even if all
artifact records pass. Keep its output private with exclusive 0600 creation and
retain the separate live probe. Candidate/UID/spec or image-ledger drift rejects
evaluation rather than silently rebuilding the selected candidate.

## Requirements are distinct

- Full Shirokuma commit/tree/path identity identifies configuration, not the
  upstream Flux publisher's build commit. Controller records identify the
  GitHub Actions OIDC issuer/workflow, signed index, ARM64 subject and SLSA/SPDX
  subjects. Do not replace either identity with the other.
- Existing Flux records pass the strict publisher **record** validator, whose
  canonical retained cryptographic bundle branch is not selected for these
  Flux versions. Their `verified:true` statements are retained historical
  assertions, not freshly verified signatures. This diagnostic performs no
  cosign/registry request and cannot establish cryptographic authenticity.
- The four retained August scans have HIGH counts 7/6/6/6, CRITICAL zero; exact
  report hashes/findings remain authoritative inputs to diagnosis, not current
  advisory completeness. Strict admission rejects these findings. The legacy
  local-lab exception expired 2026-09-13 and must not be renewed implicitly.
- ADR-0035's native bridge covers Pod/Service/Ingress baseline semantics only in
  `shirokuma-dev`. Digest syntax neither admits publisher identity nor accepts
  CVE risk. It does not cover `flux-system` controller templates or the
  GitRepository source. Existing bindings are not changed by this diagnosis.
- The source candidate is not a Kubernetes manifest. Transitive rendered inputs
  and complete image/workload inventory are not yet bound; transport remains
  unselected, verified fetched/applied provenance absent. A source Ready flag
  or exact image pin cannot fill these gaps.

The repository now provides a [non-activating local evidence contract](ADMISSION_CONTRACT.md)
binding this candidate, retained publisher/scan hashes and exact findings, with
explicit local-only risk-decision and render-inventory gaps. Missing decisions
remain missing, not fabricated as acceptance. Parent review selects the next
render-inventory dependency separately. This does not authorize source transport
selection, fetch/apply, policy activation or writer start. Later artifact,
transport and live enforcement observations need their own scoped review.

Recovery is repository rollback of the diagnostic/tests/docs only. Never
restore app counts, unsuspend sources/Jobs or replay captured requests. Retain
all disks, volumes, PV/PVCs, backups and original failures.
