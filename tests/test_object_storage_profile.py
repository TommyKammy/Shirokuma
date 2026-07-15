from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "bootstrap/seaweedfs/v4.39"
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trusted_image as trusted_image_verifier  # noqa: E402
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
BLOCKED_GITOPS_MARKERS = ("seaweedfs", "object-storage", "object_storage")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ObjectStorageProfileContractTests(unittest.TestCase):
    def test_main_only_closed_world_build_contract_is_present(self) -> None:
        source_path = PROFILE / "source.json"
        contract_path = PROFILE / "trusted-build-contract.json"
        admission_path = PROFILE / "admission.json"
        containerfile_path = PROFILE / "Containerfile"
        manifest_path = PROFILE / "go-module-inputs.json"
        vendor_path = PROFILE / "go-vendor.tar.xz"
        workflow_path = ROOT / ".github/workflows/seaweedfs-arm64.yml"
        ci_path = ROOT / ".github/workflows/ci.yml"
        makefile_path = ROOT / "Makefile"
        evidence_readme = PROFILE / "evidence/README.md"
        decision_path = (
            ROOT
            / "docs/design/07_ADR/ADR-0020_Adopt_SeaweedFS_4_39_source_for_arm64_build.md"
        )
        for path in (
            source_path,
            contract_path,
            admission_path,
            containerfile_path,
            manifest_path,
            vendor_path,
            workflow_path,
            ci_path,
            makefile_path,
            evidence_readme,
            decision_path,
        ):
            with self.subTest(required_path=path.relative_to(ROOT)):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())

        release_path = PROFILE / "release-evidence.json"
        self.assertTrue(release_path.is_file())
        release = json.loads(release_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {path.name for path in (PROFILE / "evidence").iterdir()},
            {"README.md", *release["artifacts"]},
        )

        source = json.loads(source_path.read_text(encoding="utf-8"))
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual(source["schema_version"], 3)
        self.assertEqual(source["commit"], EXPECTED_RELEASE_COMMIT)
        self.assertEqual(source["tree"], EXPECTED_RELEASE_TREE)
        self.assertEqual(
            source["build_inputs"]["dockerfile_frontend"],
            EXPECTED_DOCKERFILE_FRONTEND,
        )
        self.assertEqual(
            contract["workflow"]["allowed_refs"], ["refs/heads/main"]
        )
        self.assertEqual(
            contract["workflow"]["build_cache"],
            {
                "mode": "disabled",
                "no_cache": True,
                "cache_from": [],
                "cache_to": [],
            },
        )
        self.assertEqual(
            contract["workflow"]["build_arguments"],
            ["SOURCE_COMMIT", "GO_VENDOR_BUNDLE_SHA256"],
        )
        self.assertEqual(
            contract["workflow"]["build_action_inputs"],
            [
                "builder",
                "context",
                "file",
                "platforms",
                "push",
                "provenance",
                "sbom",
                "no-cache",
                "tags",
                "build-args",
            ],
        )
        self.assertEqual(
            contract["workflow"]["trivy_action_inputs"],
            {
                "version": "v0.72.0",
                "image-ref": "${{ env.IMAGE }}@${{ steps.build.outputs.digest }}",
                "format": "json",
                "output": "trivy.json",
                "scanners": "vuln",
                "severity": "HIGH,CRITICAL",
                "ignore-unfixed": "false",
                "vuln-type": "os,library",
                "exit-code": "1",
            },
        )
        self.assertEqual(contract["workflow"]["allowed_jobs"], ["verify", "promote"])
        self.assertEqual(
            contract["admission"],
            {
                "approval_state_source": "bootstrap/seaweedfs/v4.39/admission.json",
                "required_approved_state": "approved",
                "pending_state": "pending_main_publication",
                "publisher_ref": "refs/heads/main",
                "evidence_transition": "follow-up-evidence-only-pr",
                "runtime_manifests_permitted": False,
                "runtime_unblocker": (
                    "parent issue #26 must add source-build evidence and pass "
                    "scripts/verify_supply_chain.py check-images"
                ),
            },
        )

        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertNotIn("codex/issue-41", workflow)
        self.assertGreaterEqual(
            workflow.count("github.ref == 'refs/heads/main'"), 2
        )
        self.assertIn("      - main", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("attestations: write", workflow)
        self.assertIn("no-cache: true", workflow)
        self.assertNotIn("cache-from:", workflow)
        self.assertNotIn("cache-to:", workflow)
        self.assertIn("needs.verify.result == 'success'", workflow)
        self.assertEqual(sha256(workflow_path), contract["workflow"]["sha256"])

        ci = ci_path.read_text(encoding="utf-8")
        makefile = makefile_path.read_text(encoding="utf-8")
        self.assertIn("cosign-release: v3.1.1", ci)
        self.assertIn("scripts/verify_trusted_image.py audit --root .", makefile)
        self.assertNotIn("command -v $(COSIGN)", makefile)
        self.assertNotIn("COSIGN_VERSION", makefile)
        self.assertIn("scripts/package_go_vendor.py reproduce", ci)
        self.assertIn("go-version: 1.25.12", ci)
        self.assertIn("actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16", ci)

        for required in (
            "linux/arm64",
            "cosign sign --yes",
            "cosign verify-blob-attestation",
            "sbom-attestation-bundle.json",
            "trivy-attestation-bundle.json",
            '--certificate-github-workflow-sha "${GITHUB_WORKFLOW_SHA}"',
            "actions/attest-build-provenance@",
            "go-module-inputs.json",
            "go-vendor.tar.xz",
            "python3 scripts/verify_trusted_image.py contract",
            "python3 scripts/package_go_vendor.py reproduce",
            "Set up exact Go for vendor provenance regeneration",
            "Smoke-test non-root weed mini on the exact digest",
            "--user 65532:65532",
            "--read-only",
            "--tmpfs /tmp:",
            "--tmpfs /data:",
            "Promote the fully verified digest to the trusted tag",
            'contract["admission"]["required_approved_state"]',
        ):
            with self.subTest(workflow_literal=required):
                self.assertIn(required, workflow)

        action_refs = re.findall(
            r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE
        )
        self.assertTrue(action_refs)
        for action_ref in action_refs:
            with self.subTest(action_ref=action_ref):
                self.assertRegex(action_ref, r"^[^@]+@[0-9a-f]{40}$")

        scan_step = workflow.index(
            "- name: Scan the exact digest and block High or Critical findings"
        )
        sign_step = workflow.index(
            "- name: Keyless-sign the scanned immutable image"
        )
        provenance_step = workflow.index(
            "- name: Publish SLSA provenance for the scanned exact digest"
        )
        retain_step = workflow.index(
            "- name: Retain candidate evidence before trusted-tag promotion"
        )
        promote_step = workflow.index(
            "- name: Promote the fully verified digest to the trusted tag"
        )
        self.assertLess(scan_step, sign_step)
        self.assertLess(sign_step, provenance_step)
        self.assertLess(retain_step, promote_step)

        containerfile = containerfile_path.read_text(encoding="utf-8")
        trusted_image_verifier.validate_containerfile_build_inputs(
            containerfile,
            source["build_inputs"],
            contract["source"]["containerfile"]["frontend"],
        )
        for required in (
            "RUN --network=none",
            "GOFLAGS=-mod=vendor",
            "GOPROXY=off",
            "GOSUMDB=off",
            "GOTOOLCHAIN=local",
            "'GOVCS=*:off'",
            "GO_VENDOR_BUNDLE_SHA256",
            "dev.shirokuma.go-vendor-bundle.sha256",
            "COPY --from=builder --chown=65532:65532 /out/tmp /tmp",
        ):
            with self.subTest(container_policy=required):
                self.assertIn(required, containerfile)

        module_inputs = source["module_inputs"]
        self.assertEqual(module_inputs["go_mod_sha256"], EXPECTED_GO_MOD_SHA256)
        self.assertEqual(module_inputs["go_sum_sha256"], EXPECTED_GO_SUM_SHA256)
        self.assertEqual(
            module_inputs["bundle_sha256"], EXPECTED_VENDOR_BUNDLE_SHA256
        )
        self.assertEqual(module_inputs["module_count"], 496)
        self.assertEqual(module_inputs["replacement_count"], 2)
        self.assertEqual(module_inputs["file_count"], 18934)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["modules"]), 496)
        self.assertEqual(
            sum(module["replacement"] is not None for module in manifest["modules"]),
            2,
        )
        self.assertEqual(len(manifest["archive"]["files"]), 18934)
        self.assertEqual(sha256(vendor_path), EXPECTED_VENDOR_BUNDLE_SHA256)

        decision = decision_path.read_text(encoding="utf-8")
        self.assertIn("status: accepted", decision)
        self.assertIn("pending_main_publication", decision)
        self.assertIn("follow-up evidence-only PR", decision)

    def test_main_publication_is_approved_but_runtime_remains_blocked(self) -> None:
        admission_path = PROFILE / "admission.json"
        admission = json.loads(admission_path.read_text(encoding="utf-8"))
        release = json.loads(
            (PROFILE / "release-evidence.json").read_text(encoding="utf-8")
        )

        self.assertEqual(admission["schema_version"], 2)
        self.assertEqual(admission["assessment"]["admission"], "approved")
        self.assertIs(admission["assessment"]["exception_eligible"], False)
        self.assertEqual(admission["assessment"]["blockers"], [])
        self.assertEqual(
            admission["admitted_candidate"]["reference"], release["reference"]
        )
        self.assertEqual(
            admission["admitted_candidate"]["manifest_digest"],
            release["digest"],
        )
        self.assertEqual(
            admission["admitted_candidate"]["builder"]["ref"], "refs/heads/main"
        )
        self.assertEqual(
            admission["admitted_candidate"]["builder"]["run_id"], "29418029340"
        )
        self.assertEqual(
            {
                item["control"]
                for item in admission["admitted_candidate"]["controls"]
            },
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
        self.assertIs(admission["runtime_manifests"]["permitted"], False)
        self.assertEqual(
            {item["control"] for item in admission["runtime_manifests"]["blockers"]},
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
        self.assertEqual(
            {item["control"] for item in admission["upstream_assessment"]["blockers"]},
            {"signature", "source_revision_signature", "slsa_provenance"},
        )
        self.assertEqual(
            admission["next_action"]["mode"],
            "implement-object-storage-profile",
        )

        for relative in admission["runtime_manifests"]["paths"]:
            self.assertFalse((ROOT / relative).exists())

        gitops_root = ROOT / "deploy/gitops"
        matches = []
        for path in gitops_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            searchable = f"{path.relative_to(ROOT)}\n{path.read_text(encoding='utf-8')}".casefold()
            if any(marker in searchable for marker in BLOCKED_GITOPS_MARKERS):
                matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

        resident = json.loads(
            (ROOT / "security/resident-images.json").read_text(encoding="utf-8")
        )
        self.assertFalse(
            any(
                "seaweedfs"
                in "\n".join(
                    str(image.get(field, ""))
                    for field in ("component", "source", "reference")
                ).casefold()
                for image in resident["images"]
            )
        )


if __name__ == "__main__":
    unittest.main()
