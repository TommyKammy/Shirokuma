from __future__ import annotations

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
        containerfile_path = ROOT / "bootstrap/seaweedfs/v4.39/Containerfile"
        decision_path = (
            ROOT
            / "docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md"
        )

        required_paths = (
            evidence_path,
            release_path,
            admission_path,
            workflow_path,
            containerfile_path,
            decision_path,
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
        self.assertEqual(release["builder"]["run_id"], "29357344875")
        self.assertEqual(
            release["builder"]["workflow_sha"],
            "2ff065f3ee9fe53edc4bc6c21daf855eaac8c04b",
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
        self.assertEqual(release["github_actions_artifact"]["id"], "8320799708")
        self.assertEqual(
            release["artifacts"]["cosign-verify.json"]["sha256"],
            "e631b3b84f7456bfa3b47e1743838145cb0d91f59585ea7344e12d492515c0fc",
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
        self.assertIs(admission["runtime_manifests"]["permitted"], True)

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
