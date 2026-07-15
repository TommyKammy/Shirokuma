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
EXPECTED_GO_MOD_SHA256 = (
    "640ea9c352d46a1a444fed027adf3440cc63023afdef96c302533ecb89d7409a"
)
EXPECTED_GO_SUM_SHA256 = (
    "aad1bb8e81de6f2dee8481cc9df387efdf87012c28207d5af5d6d19a16562f6e"
)
EXPECTED_VENDOR_BUNDLE_SHA256 = (
    "62703c68abf35ea13f4b3f9d80a452b3c988fd49033dd59aeddf950326992445"
)
EXPECTED_TRUSTED_DIGEST = (
    "sha256:cde502bffee14bdcd735cb253c86a3ea56d0634a9a75574ff0b4657ca2daf299"
)
EXPECTED_TRUSTED_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-seaweedfs@" + EXPECTED_TRUSTED_DIGEST
)
EXPECTED_RUN_ID = "29376271915"
EXPECTED_RUN_ATTEMPT = "1"
EXPECTED_WORKFLOW_SHA = "d0977813fde644a2eead942444c1cb8c626ab3b6"
EXPECTED_ATTESTATION = (
    "https://github.com/TommyKammy/Shirokuma/attestations/35357720"
)
EXPECTED_TRIVY_DB_UPDATED_AT = "2026-07-14T19:03:26.337699315Z"
EXPECTED_TRIVY_DB_DOWNLOADED_AT = "2026-07-14T23:30:08.329365967Z"
BLOCKED_GITOPS_MARKERS = ("seaweedfs", "object-storage", "object_storage")


class ObjectStorageProfileContractTests(unittest.TestCase):
    def test_trusted_arm64_source_build_contract_is_present(self) -> None:
        evidence_path = ROOT / "bootstrap/seaweedfs/v4.39/source.json"
        release_path = ROOT / "bootstrap/seaweedfs/v4.39/release-evidence.json"
        admission_path = ROOT / "bootstrap/seaweedfs/v4.39/admission.json"
        workflow_path = ROOT / ".github/workflows/seaweedfs-arm64.yml"
        gitleaks_path = ROOT / ".gitleaks.toml"
        containerfile_path = ROOT / "bootstrap/seaweedfs/v4.39/Containerfile"
        module_manifest_path = (
            ROOT / "bootstrap/seaweedfs/v4.39/go-module-inputs.json"
        )
        vendor_bundle_path = ROOT / "bootstrap/seaweedfs/v4.39/go-vendor.tar.xz"
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
            module_manifest_path,
            vendor_bundle_path,
            decision_path,
            durable_evidence_dir / "candidate-release-evidence.json",
            durable_evidence_dir / "cosign-signature-bundle.json",
            durable_evidence_dir / "cosign-verify.json",
            durable_evidence_dir / "image-manifest.json",
            durable_evidence_dir / "promotion-evidence.json",
            durable_evidence_dir / "registry-signature-bundles.jsonl",
            durable_evidence_dir / "rekor-entry.json",
            durable_evidence_dir / "runtime-container-inspect.json",
            durable_evidence_dir / "runtime-smoke.json",
            durable_evidence_dir / "seaweedfs-4.39-arm64.cdx.json",
            durable_evidence_dir / "slsa-bundles.jsonl",
            durable_evidence_dir / "slsa-verify.json",
            durable_evidence_dir / "toolchain.json",
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
        self.assertEqual(source["schema_version"], 3)
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
            '--certificate-github-workflow-sha "${GITHUB_WORKFLOW_SHA}"',
            '--signer-digest "${GITHUB_WORKFLOW_SHA}"',
            '--source-digest "${GITHUB_SHA}"',
            "actions/attest-build-provenance@",
            "gh attestation verify",
            "githubWorkflowSHA",
            "cyclonedx-json",
            "trivy version --format json",
            "TRIVY_CACHE_DIR: ${{ github.workspace }}/.cache/trivy",
            "VulnerabilityDB",
            "severity: HIGH,CRITICAL",
            "trusted-build-contract.json",
            "go-module-inputs.json",
            "go-vendor.tar.xz",
            "python3 scripts/verify_trusted_image.py contract",
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
            "CRANE_VERSION: v0.21.7",
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
            "- name: Retain candidate evidence before trusted-tag promotion"
        )
        promote_step = workflow.index(
            "- name: Promote the fully verified digest to the trusted tag"
        )
        self.assertLess(retain_step, promote_step)
        promotion = workflow[promote_step:]
        self.assertNotIn("imjasonh/setup-crane@", workflow)
        self.assertNotIn("docker/setup-buildx-action@", workflow)
        self.assertIn("Install and verify pinned Crane without credentials", workflow)
        self.assertIn("CRANE_VERSION: v0.21.7", workflow)
        self.assertNotIn("latest-release", workflow)
        self.assertIn(
            '"${CRANE_BIN}" tag "${IMAGE}@${DIGEST}" "${TRUSTED_TAG}"',
            promotion,
        )
        self.assertIn(
            '"${CRANE_BIN}" digest "${IMAGE}:${TRUSTED_TAG}"', promotion
        )
        self.assertNotIn('docker push "${IMAGE}:4.39-arm64"', promotion)
        self.assertIn('test "${promoted_digest}" = "${DIGEST}"', promotion)
        generated_evidence = workflow[
            workflow.index("evidence_names = (") : workflow.index(
                'Path("release-evidence.json")'
            )
        ]
        self.assertIn('"cosign-verify.json"', generated_evidence)
        self.assertIn('"runtime-smoke.json"', generated_evidence)
        self.assertIn('"cosign-signature-bundle.json"', generated_evidence)
        self.assertIn('"toolchain": toolchain["tools"]', workflow)
        self.assertIn('"go-module-inputs.json"', generated_evidence)
        self.assertIn('"go-vendor.tar.xz"', generated_evidence)

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
        self.assertIn(
            "Public source and checksum hashes in the retained SeaweedFS Go module manifest",
            gitleaks,
        )
        self.assertIn(
            r"^bootstrap/seaweedfs/v4\.39/go-module-inputs\.json$",
            gitleaks,
        )

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
        for required in (
            "RUN --network=none",
            "GOFLAGS=-mod=vendor",
            "GOPROXY=off",
            "GOSUMDB=off",
            "GOTOOLCHAIN=local",
            "'GOVCS=*:off'",
            "GO_VENDOR_BUNDLE_SHA256",
            "dev.shirokuma.go-vendor-bundle.sha256",
        ):
            with self.subTest(vendor_policy=required):
                self.assertIn(required, containerfile)
        self.assertIn(
            "COPY --from=builder --chown=65532:65532 /out/tmp /tmp",
            containerfile,
        )

        module_inputs = source["module_inputs"]
        self.assertEqual(module_inputs["go_mod_sha256"], EXPECTED_GO_MOD_SHA256)
        self.assertEqual(module_inputs["go_sum_sha256"], EXPECTED_GO_SUM_SHA256)
        self.assertEqual(
            module_inputs["bundle_sha256"], EXPECTED_VENDOR_BUNDLE_SHA256
        )
        self.assertEqual(module_inputs["module_count"], 1152)
        self.assertEqual(module_inputs["replacement_count"], 2)
        self.assertEqual(module_inputs["file_count"], 18934)
        module_manifest = json.loads(
            module_manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(len(module_manifest["modules"]), 1152)
        self.assertEqual(
            sum(
                module["replacement"] is not None
                for module in module_manifest["modules"]
            ),
            2,
        )
        self.assertEqual(len(module_manifest["archive"]["files"]), 18934)
        self.assertEqual(
            hashlib.sha256(vendor_bundle_path.read_bytes()).hexdigest(),
            EXPECTED_VENDOR_BUNDLE_SHA256,
        )

        release = json.loads(release_path.read_text(encoding="utf-8"))
        self.assertEqual(release["reference"], EXPECTED_TRUSTED_REFERENCE)
        self.assertEqual(release["source"]["commit"], EXPECTED_RELEASE_COMMIT)
        self.assertEqual(release["source"]["tree"], EXPECTED_RELEASE_TREE)
        self.assertEqual(release["vulnerabilities"], {"critical": 0, "high": 0})
        self.assertEqual(release["builder"]["run_id"], EXPECTED_RUN_ID)
        self.assertEqual(release["builder"]["run_attempt"], EXPECTED_RUN_ATTEMPT)
        self.assertEqual(release["builder"]["workflow_sha"], EXPECTED_WORKFLOW_SHA)
        self.assertEqual(release["builder"]["source_sha"], EXPECTED_WORKFLOW_SHA)
        self.assertEqual(release["admission_status"], "approved")
        self.assertEqual(release["scanner"]["version"], "0.72.0")
        self.assertEqual(
            release["scanner"]["vulnerability_db"]["updated_at"],
            EXPECTED_TRIVY_DB_UPDATED_AT,
        )
        self.assertEqual(
            release["scanner"]["vulnerability_db"]["downloaded_at"],
            EXPECTED_TRIVY_DB_DOWNLOADED_AT,
        )
        self.assertEqual(
            release["actions_artifact"],
            {
                "role": "retained mirror only",
                "final_name": f"seaweedfs-4.39-arm64-{EXPECTED_RUN_ID}-1",
                "retention_days": 90,
            },
        )
        self.assertEqual(
            set(release["artifacts"]),
            {
                "candidate-release-evidence.json",
                "cosign-signature-bundle.json",
                "cosign-verify.json",
                "go-module-inputs.json",
                "go-vendor.tar.xz",
                "image-manifest.json",
                "promotion-evidence.json",
                "registry-signature-bundles.jsonl",
                "rekor-entry.json",
                "runtime-container-inspect.json",
                "runtime-smoke.json",
                "seaweedfs-4.39-arm64.cdx.json",
                "slsa-bundles.jsonl",
                "slsa-verify.json",
                "toolchain.json",
                "trivy-version.json",
                "trivy.json",
            },
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
        self.assertEqual(cosign["schema_version"], 1)
        self.assertEqual(cosign["reference"], EXPECTED_TRUSTED_REFERENCE)
        self.assertEqual(
            cosign["certificate_constraints"],
            {
                "issuer": "https://token.actions.githubusercontent.com",
                "identity": (
                    "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
                    "seaweedfs-arm64.yml@refs/heads/codex/issue-41"
                ),
                "github_workflow_name": "SeaweedFS 4.39 trusted arm64 build",
                "github_workflow_repository": "TommyKammy/Shirokuma",
                "github_workflow_ref": "refs/heads/codex/issue-41",
                "github_workflow_sha": EXPECTED_WORKFLOW_SHA,
                "github_workflow_trigger": "push",
            },
        )
        self.assertIs(cosign["detached_bundle_verified"], True)
        self.assertIs(cosign["registry_signature_verified"], True)
        self.assertEqual(cosign["registry_bundle"]["exact_matches"], 1)
        signed_bundle = json.loads(
            (durable_evidence_dir / "cosign-signature-bundle.json").read_text(
                encoding="utf-8"
            )
        )
        registry_bundles = [
            json.loads(line)
            for line in (
                durable_evidence_dir / "registry-signature-bundles.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.assertEqual(registry_bundles, [signed_bundle])
        self.assertEqual(len(cosign["verified_payloads"]), 1)
        self.assertEqual(
            cosign["verified_payloads"][0]["critical"]["image"][
                "docker-manifest-digest"
            ],
            EXPECTED_TRUSTED_DIGEST,
        )
        self.assertEqual(cosign["rekor_entries"], release["transparency_log"]["entries"])

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
            "a362a36c44b512fdbfa75d31ccac19adb94ff35521e566d08ff222fcfe206881",
        )
        self.assertEqual(
            release["toolchain"]["crane"],
            {
                "version": "v0.21.7",
                "archive_sha256": (
                    "b6ee979d9411dfb05ce35ab9e156fe5de7def11a230764a7856ffa2eb971fa88"
                ),
                "execution": "deferred_to_promotion_job",
            },
        )
        promotion = json.loads(
            (durable_evidence_dir / "promotion-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(promotion["run_id"], EXPECTED_RUN_ID)
        self.assertEqual(promotion["run_attempt"], EXPECTED_RUN_ATTEMPT)
        self.assertEqual(promotion["trusted_tag_digest"], EXPECTED_TRUSTED_DIGEST)
        self.assertEqual(promotion["trusted_tag_role"], "non_authoritative_pointer")
        self.assertIs(promotion["tool"]["verified_before_registry_login"], True)
        runtime_smoke = json.loads(
            (durable_evidence_dir / "runtime-smoke.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(runtime_smoke["reference"], EXPECTED_TRUSTED_REFERENCE)
        self.assertEqual(runtime_smoke["user"], "65532:65532")
        self.assertEqual(
            runtime_smoke["command"], ["/usr/bin/weed", "mini", "-dir=/data"]
        )
        self.assertIs(runtime_smoke["read_only_rootfs"], True)
        self.assertEqual(runtime_smoke["tmpfs"], ["/tmp", "/data"])
        self.assertEqual(runtime_smoke["capabilities_dropped"], "ALL")
        self.assertIs(runtime_smoke["no_new_privileges"], True)
        self.assertEqual(runtime_smoke["sustained_running_seconds"], 10)
        self.assertEqual(runtime_smoke["run_id"], release["builder"]["run_id"])
        self.assertEqual(
            runtime_smoke["run_attempt"], release["builder"]["run_attempt"]
        )
        self.assertEqual(
            runtime_smoke["runtime_inspect"]["sha256"],
            release["artifacts"]["runtime-container-inspect.json"]["sha256"],
        )
        self.assertEqual(
            release["promotion"]["trusted_tag_digest"],
            EXPECTED_TRUSTED_DIGEST,
        )
        self.assertEqual(
            release["promotion"]["trusted_tag_role"],
            "non_authoritative_pointer",
        )
        self.assertEqual(
            promotion["candidate"]["release_evidence_sha256"],
            release["promotion"]["candidate_release_sha256"],
        )
        self.assertEqual(release["slsa_provenance"], EXPECTED_ATTESTATION)
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
        self.assertEqual(trusted["builder"]["workflow_sha"], EXPECTED_WORKFLOW_SHA)
        self.assertEqual(trusted["builder"]["source_sha"], EXPECTED_WORKFLOW_SHA)
        self.assertEqual(trusted["builder"]["run_id"], EXPECTED_RUN_ID)
        self.assertEqual(trusted["builder"]["run_attempt"], EXPECTED_RUN_ATTEMPT)
        self.assertEqual(
            trusted["builder"]["run"],
            f"https://github.com/TommyKammy/Shirokuma/actions/runs/"
            f"{EXPECTED_RUN_ID}/attempts/{EXPECTED_RUN_ATTEMPT}",
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
            EXPECTED_TRIVY_DB_UPDATED_AT,
        )
        self.assertEqual(
            controls["signature"]["workflow_sha"], EXPECTED_WORKFLOW_SHA
        )
        self.assertEqual(
            controls["slsa_provenance"]["provenance"], EXPECTED_ATTESTATION
        )
        self.assertEqual(
            controls["runtime_tmp"]["inspect_sha256"],
            "c7f41727df999e1d20d8792b9abc2dd54573c5018a20dc5fe806d46c26ba5833",
        )
        self.assertEqual(
            controls["tag_promotion"]["trusted_tag_role"],
            "non_authoritative_pointer",
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
