from __future__ import annotations

import base64
import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_UPSTREAM_INDEX_REFERENCE = (
    "chrislusf/seaweedfs@"
    "sha256:c7d6c721b30ae711db766bbbfd40192776e263d4e51e22f57baef7bef93c12c6"
)
EXPECTED_UPSTREAM_MANIFEST_DIGEST = (
    "sha256:22fe8c99253508a3d4bf2fb3c66130d9c3e238506b42c41aa3aee3bfbe3a6906"
)
EXPECTED_RELEASE_COMMIT = "db42bb49757b459551607939807017d7a9d5a94a"
EXPECTED_RELEASE_TREE = "da91641fdd520e465c68fa48af3b3ad07ad86822"
EXPECTED_DOCKERFILE_FRONTEND = (
    "docker/dockerfile:1.7.0@"
    "sha256:dbbd5e059e8a07ff7ea6233b213b36aa516b4c53c645f1817a4dd18b83cbea56"
)
EXPECTED_TRUSTED_DIGEST = (
    "sha256:cbf49d40f1d879dd4baba866fb2f203aba971023f3843253fbd4028469093e96"
)
EXPECTED_TRUSTED_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-seaweedfs@" + EXPECTED_TRUSTED_DIGEST
)
BLOCKED_GITOPS_MARKERS = ("seaweedfs", "object-storage", "object_storage")


class ObjectStorageProfileContractTests(unittest.TestCase):
    def test_trusted_arm64_source_build_contract_is_present(self) -> None:
        evidence_path = ROOT / "bootstrap/seaweedfs/v4.39/source.json"
        release_path = ROOT / "bootstrap/seaweedfs/v4.39/release-evidence.json"
        admission_path = ROOT / "bootstrap/seaweedfs/v4.39/admission.json"
        workflow_path = ROOT / ".github/workflows/seaweedfs-arm64.yml"
        gitleaks_path = ROOT / ".gitleaks.toml"
        containerfile_path = ROOT / "bootstrap/seaweedfs/v4.39/Containerfile"
        durable_evidence_dir = ROOT / "bootstrap/seaweedfs/v4.39/evidence"
        decision_path = (
            ROOT
            / "docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md"
        )

        required_paths = (
            evidence_path,
            release_path,
            admission_path,
            workflow_path,
            gitleaks_path,
            containerfile_path,
            decision_path,
            durable_evidence_dir / "cosign-verify.json",
            durable_evidence_dir / "seaweedfs-4.39-arm64.cdx.json",
            durable_evidence_dir / "slsa-verify.json",
            durable_evidence_dir / "trivy-version.json",
            durable_evidence_dir / "trivy.json",
        )
        missing = [
            path.relative_to(ROOT).as_posix()
            for path in required_paths
            if not path.is_file()
        ]
        self.assertFalse(missing, f"missing trusted build contract: {', '.join(missing)}")

        source = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(source["version"], "4.39")
        self.assertEqual(source["commit"], EXPECTED_RELEASE_COMMIT)
        self.assertEqual(source["tree"], EXPECTED_RELEASE_TREE)
        self.assertRegex(source["git_archive_sha256"], r"^[0-9a-f]{64}$")

        workflow = workflow_path.read_text(encoding="utf-8")
        for required in (
            "permissions:",
            "contents: read",
            "packages: write",
            "id-token: write",
            "attestations: write",
            "linux/arm64",
            "cosign sign --yes",
            '--certificate-github-workflow-sha "${GITHUB_SHA}"',
            "actions/attest-build-provenance@",
            "gh attestation verify",
            "githubWorkflowSHA",
            "cyclonedx-json",
            "trivy version --format json",
            "TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy",
            "VulnerabilityDB",
            "severity: HIGH,CRITICAL",
            "actual_inputs",
            "source evidence build_inputs do not match Containerfile",
            "quarantine-${{ github.run_id }}-${{ github.run_attempt }}",
            "Promote the fully verified digest to the trusted tag",
            "name: seaweedfs-4.39-arm64-${{ github.run_id }}",
            "Smoke-test non-root weed mini on the exact digest",
            'runtime_user=$(docker image inspect "${IMAGE}@${DIGEST}"',
            "--user 65532:65532",
            "--read-only",
            "--tmpfs /tmp:",
            "--tmpfs /data:",
            "runtime-smoke.json",
            "runtime-smoke.log",
            "version: v0.21.7",
            "b6ee979d9411dfb05ce35ab9e156fe5de7def11a230764a7856ffa2eb971fa88",
            "sha256sum --check --strict",
            EXPECTED_RELEASE_COMMIT,
            EXPECTED_RELEASE_TREE,
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)

        action_refs = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        self.assertTrue(action_refs)
        for action_ref in action_refs:
            with self.subTest(action_ref=action_ref):
                self.assertRegex(action_ref, r"^[^@]+@[0-9a-f]{40}$")
        self.assertNotRegex(workflow, r"secrets\.(COSIGN|SIGNING|PRIVATE_KEY)")
        self.assertNotIn(r"\$(", workflow)
        self.assertIn('json.dumps(record, indent=2) + "\\n"', workflow)
        self.assertNotIn('json.dumps(record, indent=2) + "\\\\n"', workflow)
        scan_step = workflow.index(
            "- name: Scan the exact digest and block High or Critical findings"
        )
        sign_step = workflow.index(
            "- name: Keyless-sign the scanned immutable image"
        )
        provenance_step = workflow.index(
            "- name: Publish SLSA provenance for the scanned exact digest"
        )
        self.assertLess(scan_step, sign_step)
        self.assertLess(sign_step, provenance_step)
        platform_step = workflow.index("- name: Verify the published platform")
        smoke_step = workflow.index(
            "- name: Smoke-test non-root weed mini on the exact digest"
        )
        sbom_step = workflow.index("- name: Generate CycloneDX SBOM")
        self.assertLess(platform_step, smoke_step)
        self.assertLess(smoke_step, sbom_step)
        smoke = workflow[smoke_step:sbom_step]
        self.assertIn('"${IMAGE}@${DIGEST}"', smoke)
        self.assertIn("timeout-minutes: 2", smoke)
        self.assertIn("trap cleanup EXIT", smoke)
        self.assertIn("sustained_running_seconds", smoke)
        self.assertIn("if not matches:", workflow)
        self.assertNotIn(
            'if len(matches) != 1:\n              raise SystemExit("SLSA provenance',
            workflow,
        )
        build_step = workflow[
            workflow.index("- name: Build and publish only linux/arm64") :
            workflow.index("- name: Verify the published platform")
        ]
        self.assertIn(
            "quarantine-${{ github.run_id }}-${{ github.run_attempt }}",
            build_step,
        )
        self.assertNotIn(":4.39-arm64", build_step)
        retain_step = workflow.index(
            "- name: Retain independently downloadable evidence"
        )
        promote_step = workflow.index(
            "- name: Promote the fully verified digest to the trusted tag"
        )
        self.assertLess(retain_step, promote_step)
        promotion = workflow[promote_step:]
        self.assertIn("imjasonh/setup-crane@", workflow)
        self.assertIn(
            "imjasonh/setup-crane@feee3b6bb0d4c68370f256a4502498c9227e5c6b",
            workflow,
        )
        self.assertIn("version: v0.21.7", workflow)
        self.assertNotIn("latest-release", workflow)
        self.assertIn('crane tag "${IMAGE}@${DIGEST}" 4.39-arm64', promotion)
        self.assertIn('crane digest "${IMAGE}:4.39-arm64"', promotion)
        self.assertNotIn('docker push "${IMAGE}:4.39-arm64"', promotion)
        self.assertIn('test "${promoted_digest}" = "${DIGEST}"', promotion)
        generated_evidence = workflow[
            workflow.index('"artifacts": {') : workflow.index(
                'Path("release-evidence.json")'
            )
        ]
        self.assertIn('"cosign-verify.json"', generated_evidence)
        self.assertIn('"runtime-smoke.json"', generated_evidence)
        self.assertIn('"runtime-smoke.log"', generated_evidence)
        self.assertIn('"promotion_tool": {', workflow)

        gitleaks = gitleaks_path.read_text(encoding="utf-8")
        self.assertIn(
            "Public SHA-1 package hashes in retained SeaweedFS 4.39 CycloneDX evidence",
            gitleaks,
        )
        self.assertIn(
            r"^bootstrap/seaweedfs/v4\.39/evidence/seaweedfs-4\.39-arm64\.cdx\.json$",
            gitleaks,
        )
        self.assertIn(
            r"^bootstrap/seaweedfs/v4\.39/evidence/trivy\.json$",
            gitleaks,
        )
        self.assertNotIn(r"^bootstrap/seaweedfs/v4\.39/evidence/.*", gitleaks)

        decision = decision_path.read_text(encoding="utf-8")
        self.assertIn("status: accepted", decision)

        containerfile = containerfile_path.read_text(encoding="utf-8")
        self.assertNotIn(r"\${", containerfile)
        for image in source["build_inputs"].values():
            with self.subTest(build_input=image):
                self.assertIn(image, containerfile)
        self.assertEqual(
            source["build_inputs"]["dockerfile_frontend"],
            EXPECTED_DOCKERFILE_FRONTEND,
        )
        first_from = containerfile.index("FROM ")
        self.assertLess(containerfile.index("ARG GO_IMAGE="), first_from)
        self.assertLess(containerfile.index("ARG RUNTIME_IMAGE="), first_from)
        self.assertIn("EXPOSE 7333 8333 8888 9333 9340 19333 23646", containerfile)
        self.assertNotIn("EXPOSE 7333 8080", containerfile)
        self.assertIn("mkdir -p /out/data /out/tmp", containerfile)
        self.assertIn(
            "COPY --from=builder --chown=65532:65532 /out/tmp /tmp",
            containerfile,
        )

        release = json.loads(release_path.read_text(encoding="utf-8"))
        self.assertEqual(release["reference"], EXPECTED_TRUSTED_REFERENCE)
        self.assertEqual(release["source"]["commit"], EXPECTED_RELEASE_COMMIT)
        self.assertEqual(release["source"]["tree"], EXPECTED_RELEASE_TREE)
        self.assertEqual(release["vulnerabilities"], {"critical": 0, "high": 0})
        self.assertEqual(release["builder"]["run_id"], "29359679038")
        self.assertEqual(
            release["builder"]["workflow_sha"],
            "159e8601302cd6306d9d3bd9d847ea39275a9bf8",
        )
        self.assertEqual(release["admission_status"], "approved")
        self.assertEqual(release["scanner"]["version"], "0.72.0")
        self.assertEqual(
            release["scanner"]["vulnerability_db"]["updated_at"],
            "2026-07-13T19:09:56.237113526Z",
        )
        self.assertEqual(
            release["scanner"]["vulnerability_db"]["downloaded_at"],
            "2026-07-14T01:41:51.785604274Z",
        )
        self.assertEqual(release["github_actions_artifact"]["id"], "8321634543")
        self.assertEqual(
            release["github_actions_artifact"]["role"],
            "short-term downloadable mirror of the Git-retained evidence",
        )
        for name, artifact in release["artifacts"].items():
            with self.subTest(durable_artifact=name):
                path = ROOT / artifact["path"]
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    artifact["sha256"],
                )
        sbom = json.loads(
            (durable_evidence_dir / "seaweedfs-4.39-arm64.cdx.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["metadata"]["component"]["version"], EXPECTED_TRUSTED_DIGEST)

        scan = json.loads(
            (durable_evidence_dir / "trivy.json").read_text(encoding="utf-8")
        )
        self.assertIn(EXPECTED_TRUSTED_REFERENCE, scan["Metadata"]["RepoDigests"])
        blocking_findings = [
            finding
            for result in scan["Results"]
            for finding in result.get("Vulnerabilities") or []
            if finding.get("Severity") in {"HIGH", "CRITICAL"}
        ]
        self.assertEqual(blocking_findings, [])

        scanner = json.loads(
            (durable_evidence_dir / "trivy-version.json").read_text(encoding="utf-8")
        )
        self.assertEqual(scanner["Version"], release["scanner"]["version"])
        self.assertEqual(
            scanner["VulnerabilityDB"]["UpdatedAt"],
            release["scanner"]["vulnerability_db"]["updated_at"],
        )
        self.assertEqual(
            scanner["VulnerabilityDB"]["DownloadedAt"],
            release["scanner"]["vulnerability_db"]["downloaded_at"],
        )

        cosign = json.loads(
            (durable_evidence_dir / "cosign-verify.json").read_text(encoding="utf-8")
        )
        self.assertTrue(
            any(
                record.get("critical", {})
                .get("image", {})
                .get("docker-manifest-digest")
                == EXPECTED_TRUSTED_DIGEST
                for record in cosign
            )
        )

        slsa = json.loads(
            (durable_evidence_dir / "slsa-verify.json").read_text(encoding="utf-8")
        )
        expected_workflow_sha = release["builder"]["workflow_sha"]
        self.assertTrue(
            any(
                record["verificationResult"]["signature"]["certificate"].get(
                    "githubWorkflowSHA"
                )
                == expected_workflow_sha
                and any(
                    subject.get("digest", {}).get("sha256")
                    == EXPECTED_TRUSTED_DIGEST.removeprefix("sha256:")
                    for subject in json.loads(
                        base64.b64decode(
                            record["attestation"]["bundle"]["dsseEnvelope"]["payload"]
                        )
                    ).get("subject", [])
                )
                for record in slsa
            )
        )
        self.assertEqual(
            release["artifacts"]["cosign-verify.json"]["sha256"],
            "e631b3b84f7456bfa3b47e1743838145cb0d91f59585ea7344e12d492515c0fc",
        )
        self.assertEqual(
            release["publication"]["trusted_tag_digest"],
            EXPECTED_TRUSTED_DIGEST,
        )
        self.assertIs(
            release["publication"]["promoted_after_evidence_retention"],
            True,
        )
        self.assertRegex(release["slsa_provenance"], r"/attestations/[0-9]+$")
        self.assertEqual(
            release["issuer"],
            "https://token.actions.githubusercontent.com",
        )
        self.assertEqual(
            release["identity"],
            "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
            "seaweedfs-arm64.yml@refs/heads/codex/issue-41",
        )

    def test_hardened_source_build_candidate_is_admitted(self) -> None:
        admission_path = ROOT / "bootstrap/seaweedfs/v4.39/admission.json"
        self.assertTrue(admission_path.is_file())
        admission = json.loads(admission_path.read_text(encoding="utf-8"))

        self.assertEqual(admission["schema_version"], 2)
        self.assertEqual(admission["component"], "seaweedfs")
        self.assertEqual(admission["version"], "4.39")
        self.assertEqual(admission["platform"], "linux/arm64")
        self.assertEqual(admission["assessment"]["admission"], "approved")
        self.assertIs(admission["assessment"]["exception_eligible"], False)
        self.assertEqual(admission["assessment"]["blockers"], [])
        self.assertIs(admission["runtime_manifests"]["permitted"], False)
        self.assertEqual(
            {
                blocker["control"]
                for blocker in admission["runtime_manifests"]["blockers"]
            },
            {"resident_evidence_contract"},
        )

        self.assertEqual(
            admission["upstream_candidate"]["index_reference"],
            EXPECTED_UPSTREAM_INDEX_REFERENCE,
        )
        self.assertEqual(
            admission["upstream_candidate"]["manifest_digest"],
            EXPECTED_UPSTREAM_MANIFEST_DIGEST,
        )

        upstream_blockers = admission["upstream_assessment"]["blockers"]
        self.assertEqual(
            {blocker["control"] for blocker in upstream_blockers},
            {"signature", "source_revision_signature", "slsa_provenance"},
        )
        for blocker in upstream_blockers:
            with self.subTest(control=blocker["control"]):
                self.assertEqual(blocker["status"], "missing")
                self.assertTrue(blocker["evidence"].strip())

        trusted = admission["admitted_candidate"]
        self.assertEqual(trusted["reference"], EXPECTED_TRUSTED_REFERENCE)
        self.assertEqual(trusted["manifest_digest"], EXPECTED_TRUSTED_DIGEST)
        self.assertEqual(
            trusted["release_evidence"],
            "bootstrap/seaweedfs/v4.39/release-evidence.json",
        )
        controls = {control["control"]: control for control in trusted["controls"]}
        self.assertEqual(
            set(controls),
            {
                "source_adoption",
                "signature",
                "transparency_log",
                "workflow_revision",
                "slsa_provenance",
                "sbom",
                "vulnerability_scan",
                "runtime_tmp",
                "tag_promotion",
            },
        )
        for control in controls.values():
            with self.subTest(control=control["control"]):
                self.assertEqual(control["status"], "verified")
        self.assertEqual(controls["vulnerability_scan"]["critical"], 0)
        self.assertEqual(controls["vulnerability_scan"]["high"], 0)
        self.assertEqual(
            controls["vulnerability_scan"]["vulnerability_db_updated_at"],
            "2026-07-13T19:09:56.237113526Z",
        )

        for relative_path in admission["runtime_manifests"]["paths"]:
            path = ROOT / relative_path
            with self.subTest(runtime_manifest=relative_path):
                self.assertFalse(
                    path.exists(),
                    f"source-build child emitted runtime manifest {relative_path}",
                )

        gitops_root = ROOT / "deploy/gitops"
        premature_gitops_matches = []
        for path in gitops_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            relative_path = path.relative_to(ROOT).as_posix()
            searchable = f"{relative_path}\n{path.read_text(encoding='utf-8')}".casefold()
            matched_markers = [
                marker for marker in BLOCKED_GITOPS_MARKERS if marker in searchable
            ]
            if matched_markers:
                premature_gitops_matches.append(
                    f"{relative_path} ({', '.join(matched_markers)})"
                )
        self.assertEqual(
            premature_gitops_matches,
            [],
            "source-build child added SeaweedFS/object-storage GitOps resources: "
            + "; ".join(premature_gitops_matches),
        )

        resident_ledger_path = ROOT / "security/resident-images.json"
        resident_ledger = json.loads(resident_ledger_path.read_text(encoding="utf-8"))
        premature_resident_entries = []
        for image in resident_ledger["images"]:
            searchable = "\n".join(
                str(image.get(field, ""))
                for field in ("component", "source", "reference")
            ).casefold()
            if "seaweedfs" in searchable or EXPECTED_TRUSTED_DIGEST in searchable:
                premature_resident_entries.append(image.get("component", "<unknown>"))
        self.assertEqual(
            premature_resident_entries,
            [],
            "source-build child added SeaweedFS to resident image ledger: "
            + ", ".join(premature_resident_entries),
        )

        self.assertEqual(
            admission["next_action"]["mode"],
            "implement-object-storage-profile",
        )
        self.assertIs(admission["next_action"]["decision_record_required"], False)
        self.assertGreaterEqual(len(admission["next_action"]["requirements"]), 4)


if __name__ == "__main__":
    unittest.main()
