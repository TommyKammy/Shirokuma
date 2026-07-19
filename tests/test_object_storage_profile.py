from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "bootstrap/seaweedfs/v4.39"
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trusted_image as trusted_image_verifier  # noqa: E402
import object_storage_backup as object_storage_backup  # noqa: E402
import object_storage_s3 as object_storage_s3  # noqa: E402
from verify_gitops_teardown import (  # noqa: E402
    OBJECT_STORAGE_MANIFEST,
    validate_object_storage_gitops_state,
)
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
EXPECTED_RUNTIME_REFERENCE = (
    "ghcr.io/tommykammy/shirokuma-seaweedfs@"
    "sha256:d1339701907587c93c6af8740388226ac2277cbbfd3df581c0e85d815c90e421"
)


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
        object_storage_target = makefile.split(
            "verify-object-storage-profile:",
            1,
        )[1].split("\n\n", 1)[0]
        self.assertIn("cosign-release: v3.1.1", ci)
        self.assertIn("scripts/verify_trusted_image.py audit --root .", makefile)
        self.assertNotIn("command -v $(COSIGN)", object_storage_target)
        self.assertNotIn("COSIGN_VERSION", object_storage_target)
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

    def test_artifact_admission_remains_separate_from_the_resident_runtime_gate(
        self,
    ) -> None:
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

        self.assertEqual(
            admission["runtime_manifests"]["paths"],
            [
                "deploy/gitops/object-storage/kustomization.yaml",
                "deploy/gitops/clusters/local-lite/object-storage.yaml",
            ],
        )
        gitops_state = validate_object_storage_gitops_state(ROOT)
        for relative in admission["runtime_manifests"]["paths"]:
            if (
                relative == OBJECT_STORAGE_MANIFEST.as_posix()
                and gitops_state.mode == "issue-26-teardown"
            ):
                self.assertEqual(gitops_state.missing_path, relative)
            else:
                self.assertTrue((ROOT / relative).is_file())

        resident = json.loads(
            (ROOT / "security/resident-images.json").read_text(encoding="utf-8")
        )
        seaweedfs = [
            image for image in resident["images"] if image.get("component") == "seaweedfs"
        ]
        self.assertEqual(len(seaweedfs), 1)
        self.assertEqual(
            seaweedfs[0]["reference"],
            EXPECTED_RUNTIME_REFERENCE,
        )
        supply_chain = json.loads(
            (ROOT / "security" / seaweedfs[0]["supply_chain_artifact"]).read_text(
                encoding="utf-8"
            )
        )
        record = next(
            image
            for image in supply_chain["images"]
            if image.get("component") == "seaweedfs"
        )
        self.assertEqual(record["evidence_mode"], "repository_source_build")
        self.assertEqual(record["reference"], seaweedfs[0]["reference"])
        self.assertEqual(
            record["repository_source_build"]["admission"]["path"],
            "bootstrap/seaweedfs/v4.39/admission.json",
        )


class ObjectStorageGitOpsRuntimeTests(unittest.TestCase):
    def test_flux_runtime_has_explicit_dependency_readiness_and_prune_contract(self) -> None:
        state = validate_object_storage_gitops_state(ROOT)
        if state.mode == "issue-26-teardown":
            self.assertEqual(
                state.missing_path,
                "deploy/gitops/clusters/local-lite/object-storage.yaml",
            )
            return
        flux = (
            ROOT / "deploy/gitops/clusters/local-lite/object-storage.yaml"
        ).read_text(encoding="utf-8")
        for required in (
            "name: shirokuma-object-storage",
            "namespace: flux-system",
            "dependsOn:\n    - name: shirokuma-dev",
            "interval: 10m",
            "retryInterval: 1m",
            "timeout: 10m",
            "path: ./deploy/gitops/object-storage",
            "prune: true",
            "wait: true",
            "healthChecks:",
            "kind: StatefulSet",
            "name: seaweedfs",
            "namespace: shirokuma-storage",
        ):
            with self.subTest(required=required):
                self.assertIn(required, flux)
        self.assertIn("kind: GitRepository", flux)
        self.assertIn("name: flux-system", flux)

    def test_statefulset_is_pinned_nonroot_readonly_and_storage_safe(self) -> None:
        stateful = (
            ROOT / "deploy/gitops/object-storage/statefulset.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn(f"image: {EXPECTED_RUNTIME_REFERENCE}", stateful)
        self.assertNotRegex(stateful, r"(?m)^\s*image:\s*[^\s@]+:[^\s@]+\s*$")
        self.assertLess(
            stateful.index("- -logtostderr=true"), stateful.index("- mini")
        )
        for argument in (
            "-dir=/data",
            "-bucket=$(S3_BUCKET)",
            "-s3.config=/etc/seaweedfs/credentials/s3.json",
            "-s3.allowDeleteBucketNotEmpty=false",
            "-s3.iam.readOnly=true",
            "-s3.port=8333",
            "-s3.port.iceberg=0",
            "-s3.metricsPort=0",
            "-master.telemetry=false",
            "-webdav=false",
            "-admin.ui=false",
            "-metricsPort=0",
        ):
            with self.subTest(argument=argument):
                self.assertIn(f"- {argument}", stateful)

        for security_control in (
            "automountServiceAccountToken: false",
            "runAsNonRoot: true",
            "runAsUser: 65532",
            "runAsGroup: 65532",
            "fsGroup: 65532",
            "type: RuntimeDefault",
            "allowPrivilegeEscalation: false",
            "readOnlyRootFilesystem: true",
            "drop:\n                - ALL",
        ):
            with self.subTest(security_control=security_control):
                self.assertIn(security_control, stateful)

        for runtime_control in (
            "startupProbe:\n            httpGet:\n              path: /healthz\n              port: s3\n              scheme: HTTP",
            "readinessProbe:\n            httpGet:\n              path: /readyz\n              port: s3\n              scheme: HTTP",
            "livenessProbe:\n            httpGet:\n              path: /healthz\n              port: s3\n              scheme: HTTP",
            "initialDelaySeconds: 5",
            "requests:\n              cpu: 250m\n              memory: 512Mi",
            'limits:\n              cpu: "2"\n              memory: 2Gi',
            "name: tmp\n          emptyDir:\n            sizeLimit: 64Mi",
            "mountPath: /tmp",
            "mountPath: /data",
            "secretName: seaweedfs-s3-credentials",
            "defaultMode: 0440",
            'shirokuma.dev/s3-credential-generation: "1"',
        ):
            with self.subTest(runtime_control=runtime_control):
                self.assertIn(runtime_control, stateful)

        for persistence_control in (
            "persistentVolumeClaimRetentionPolicy:",
            "whenDeleted: Retain",
            "whenScaled: Retain",
            "name: seaweedfs-data",
            "kustomize.toolkit.fluxcd.io/prune: disabled",
            "storage: 20Gi",
            "- ReadWriteOnce",
        ):
            with self.subTest(persistence_control=persistence_control):
                self.assertIn(persistence_control, stateful)
        self.assertNotIn("hostPath:", stateful)

    def test_internal_service_and_consumer_contract_are_not_public(self) -> None:
        service = (ROOT / "deploy/gitops/object-storage/service.yaml").read_text(
            encoding="utf-8"
        )
        contract = (
            ROOT / "deploy/gitops/object-storage/contract-configmap.yaml"
        ).read_text(encoding="utf-8")
        kustomization = (
            ROOT / "deploy/gitops/object-storage/kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("type: ClusterIP", service)
        self.assertIn("port: 8333", service)
        self.assertNotIn("NodePort", service)
        self.assertNotIn("LoadBalancer", service)
        self.assertNotIn("externalIPs", service)
        self.assertIn("namespace: shirokuma-storage", service)
        self.assertIn("namespace: shirokuma-storage", contract)
        self.assertIn("namespace: shirokuma-storage", kustomization)
        self.assertIn(
            "S3_ENDPOINT: http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333",
            contract,
        )
        self.assertIn("S3_BUCKET: shirokuma-lakehouse", contract)
        self.assertIn("S3_REGION: us-east-1", contract)
        self.assertIn("S3_PATH_STYLE: \"true\"", contract)
        self.assertIn("S3_LIFECYCLE_POLICY: none-local-lite-placeholder", contract)
        self.assertIn('S3_DELETE_BUCKET_NOT_EMPTY: "false"', contract)
        self.assertEqual(
            re.findall(r"^  - (.+\.yaml)$", kustomization, re.MULTILINE),
            [
                "contract-configmap.yaml",
                "networkpolicy.yaml",
                "service.yaml",
                "statefulset.yaml",
            ],
        )

    def test_network_policy_only_allows_labeled_local_clients_to_s3(self) -> None:
        policy = (
            ROOT / "deploy/gitops/object-storage/networkpolicy.yaml"
        ).read_text(encoding="utf-8")
        baseline = (ROOT / "scripts/colima_baseline.sh").read_text(encoding="utf-8")
        for required in (
            "kind: NetworkPolicy",
            "name: seaweedfs-s3-ingress",
            "namespace: shirokuma-storage",
            "kubernetes.io/metadata.name: shirokuma-dev",
            'shirokuma.dev/object-storage-client: "true"',
            "protocol: TCP\n          port: 8333",
        ):
            with self.subTest(required=required):
                self.assertIn(required, policy)
        self.assertEqual(policy.count("- from:"), 1)
        self.assertEqual(policy.count("port:"), 1)
        for forbidden_port in ("9333", "9340", "8888"):
            self.assertNotIn(forbidden_port, policy)
        self.assertIn("--kubernetes", baseline)
        self.assertNotIn("--disable-network-policy", baseline)

    def test_opentofu_owns_namespace_and_secret_boundaries_without_values(self) -> None:
        main = (ROOT / "opentofu/dev/main.tf").read_text(encoding="utf-8")
        variables = (ROOT / "opentofu/dev/variables.tf").read_text(encoding="utf-8")
        secret = (ROOT / "opentofu/dev/object-storage.tf").read_text(
            encoding="utf-8"
        )
        outputs = (ROOT / "opentofu/dev/outputs.tf").read_text(encoding="utf-8")
        stateful = (
            ROOT / "deploy/gitops/object-storage/statefulset.yaml"
        ).read_text(encoding="utf-8")
        for required in (
            'resource "kubernetes_namespace_v1" "dev"',
            'name = "shirokuma-dev"',
            'resource "kubernetes_namespace_v1" "storage"',
            'name = "shirokuma-storage"',
        ):
            with self.subTest(namespace_contract=required):
                self.assertIn(required, main)
        for name in (
            "seaweedfs_s3_operator_access_key",
            "seaweedfs_s3_operator_secret_key",
            "seaweedfs_s3_application_access_key",
            "seaweedfs_s3_application_secret_key",
        ):
            match = re.search(
                rf'variable "{name}" \{{(?P<body>.*?)(?=\nvariable |\Z)',
                variables,
                re.DOTALL,
            )
            self.assertIsNotNone(match)
            body = match.group("body")
            self.assertIn("sensitive   = true", body)
            self.assertIn("nullable    = false", body)
            self.assertNotRegex(body, r"(?m)^\s*default\s*=")
        for required in (
            'resource "kubernetes_secret_v1" "seaweedfs_s3_credentials"',
            'name      = "seaweedfs-s3-credentials"',
            '"s3.json" = local.seaweedfs_s3_config',
            'name = "shirokuma-local-lite-operator"',
            "accessKey = var.seaweedfs_s3_operator_access_key",
            "secretKey = var.seaweedfs_s3_operator_secret_key",
            'actions = ["Admin"]',
            'name = "shirokuma-lakehouse-application"',
            "accessKey = var.seaweedfs_s3_application_access_key",
            "secretKey = var.seaweedfs_s3_application_secret_key",
            '"Read:${local.seaweedfs_s3_bucket}"',
            '"List:${local.seaweedfs_s3_bucket}"',
            '"Tagging:${local.seaweedfs_s3_bucket}"',
            '"Write:${local.seaweedfs_s3_bucket}"',
            'resource "kubernetes_secret_v1" "seaweedfs_s3_application_credentials"',
            'name      = "seaweedfs-s3-application-credentials"',
            "AWS_ACCESS_KEY_ID     = var.seaweedfs_s3_application_access_key",
            "AWS_SECRET_ACCESS_KEY = var.seaweedfs_s3_application_secret_key",
            '"shirokuma.dev/s3-credential-generation" = var.seaweedfs_s3_credential_generation',
            "var.seaweedfs_s3_operator_access_key !=",
            "operator and application access keys must be distinct",
        ):
            with self.subTest(required=required):
                self.assertIn(required, secret)
        server_secret = re.search(
            r'resource "kubernetes_secret_v1" "seaweedfs_s3_credentials" '
            r"\{(?P<body>.*?)(?=\nresource )",
            secret,
            re.DOTALL,
        )
        self.assertIsNotNone(server_secret)
        self.assertIn(
            "namespace = kubernetes_namespace_v1.storage.metadata[0].name",
            server_secret.group("body"),
        )
        self.assertNotIn(
            "namespace = kubernetes_namespace_v1.dev.metadata[0].name",
            server_secret.group("body"),
        )
        self.assertIn("secretName: seaweedfs-s3-credentials", stateful)
        self.assertIn("namespace: shirokuma-storage", stateful)
        self.assertNotIn("optional: true", stateful)
        self.assertIn('shirokuma.dev/s3-credential-generation: "1"', stateful)
        self.assertNotIn("stringData:", stateful)
        self.assertNotIn("local-smoke", secret)
        application_secret = re.search(
            r'resource "kubernetes_secret_v1" "seaweedfs_s3_application_credentials" '
            r"\{(?P<body>.*)\n\}\s*\Z",
            secret,
            re.DOTALL,
        )
        self.assertIsNotNone(application_secret)
        self.assertNotIn("seaweedfs_s3_operator_", application_secret.group("body"))
        self.assertIn(
            "namespace = kubernetes_namespace_v1.dev.metadata[0].name",
            application_secret.group("body"),
        )
        self.assertIn(
            'S3_ENDPOINT           = "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333"',
            application_secret.group("body"),
        )

        for required in (
            'output "seaweedfs_s3_secret_namespace"',
            'output "seaweedfs_s3_application_secret_namespace"',
            'output "seaweedfs_s3_endpoint"',
            'value       = "http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333"',
        ):
            with self.subTest(output_contract=required):
                self.assertIn(required, outputs)

        generation = re.search(
            r'variable "seaweedfs_s3_credential_generation" '
            r"\{(?P<body>.*?)(?=\nvariable |\Z)",
            variables,
            re.DOTALL,
        )
        self.assertIsNotNone(generation)
        self.assertIn('default     = "1"', generation.group("body"))
        self.assertIn("positive decimal integer", generation.group("body"))

    def test_smoke_uses_authenticated_stdlib_sigv4_without_direct_apply(self) -> None:
        wrapper = (ROOT / "scripts/object_storage_smoke.sh").read_text(
            encoding="utf-8"
        )
        client = (ROOT / "scripts/object_storage_s3.py").read_text(encoding="utf-8")
        for required in (
            'KUBE_NAMESPACE="${KUBE_NAMESPACE:-shirokuma-storage}"',
            "port-forward --address 127.0.0.1",
            'service/${S3_SERVICE}',
            "object_storage_s3.py\" smoke",
        ):
            self.assertIn(required, wrapper)
        self.assertNotIn("kubectl apply", wrapper)
        self.assertNotRegex(wrapper, r"\b(?:aws|mc|s5cmd|curl)\b")
        self.assertIn('"AWS4-HMAC-SHA256"', client)
        self.assertIn("urllib.request.ProxyHandler({})", client)
        self.assertIn("AWS_ACCESS_KEY_ID", client)
        self.assertIn("S3_CREDENTIALS_FILE", client)
        self.assertNotIn("--access-key", client)
        self.assertNotIn("--secret-key", client)

    def test_sigv4_uses_the_exact_encoded_wire_path(self) -> None:
        class FakeResponse:
            status = 200

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b""

        class RecordingOpener:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def open(self, request: object, *, timeout: float) -> FakeResponse:
                del timeout
                self.urls.append(request.full_url)
                return FakeResponse()

        client = object_storage_s3.SigV4S3Client(
            "http://127.0.0.1:8333/base/",
            object_storage_s3.S3Credentials("test-access", "test-secret"),
        )
        opener = RecordingOpener()
        client._opener = opener
        cases = (
            (("bucket", "folder", ""), "/base/bucket/folder/"),
            (("bucket", "folder", "", "object"), "/base/bucket/folder//object"),
            (("bucket", "space key"), "/base/bucket/space%20key"),
            (("bucket", "100%"), "/base/bucket/100%25"),
            (("bucket", "雪"), "/base/bucket/%E9%9B%AA"),
        )
        for segments, expected_path in cases:
            with self.subTest(segments=segments):
                with mock.patch.object(
                    client, "_authorization", wraps=client._authorization
                ) as authorization:
                    client.request("GET", segments)
                self.assertEqual(client._canonical_uri(segments), expected_path)
                self.assertEqual(
                    urllib.parse.urlsplit(opener.urls[-1]).path, expected_path
                )
                self.assertEqual(authorization.call_args.args[1], expected_path)


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.puts: list[str] = []

    def list_objects(
        self, bucket: str, prefix: str = ""
    ) -> list[object_storage_s3.S3Object]:
        del bucket
        return [
            object_storage_s3.S3Object(key, len(body), hashlib.md5(body).hexdigest())
            for key, body in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def get_object(self, bucket: str, key: str) -> bytes:
        del bucket
        return self.objects[key]

    def put_object(self, bucket: str, key: str, body: bytes) -> None:
        del bucket
        self.objects[key] = body
        self.puts.append(key)


class ObjectStorageBackupContractTests(unittest.TestCase):
    def test_secret_file_is_owner_only_and_credential_sources_are_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s3.json"
            value = {
                "identities": [
                    {
                        "name": object_storage_s3.DEFAULT_IDENTITY_NAME,
                        "credentials": [
                            {"accessKey": "test-access", "secretKey": "test-secret"}
                        ],
                    },
                    {
                        "name": "shirokuma-lakehouse-application",
                        "credentials": [
                            {"accessKey": "app-access", "secretKey": "app-secret"}
                        ],
                    },
                ]
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o600)
            credential = object_storage_s3.load_credentials(
                {"S3_CREDENTIALS_FILE": str(path)}
            )
            self.assertEqual(credential.access_key, "test-access")
            application_credential = object_storage_s3.load_credentials(
                {
                    "S3_CREDENTIALS_FILE": str(path),
                    "S3_IDENTITY_NAME": "shirokuma-lakehouse-application",
                }
            )
            self.assertEqual(application_credential.access_key, "app-access")
            with mock.patch.object(
                object_storage_s3.os, "open", wraps=object_storage_s3.os.open
            ) as safe_open:
                object_storage_s3.load_credentials(
                    {"S3_CREDENTIALS_FILE": str(path)}
                )
            self.assertTrue(
                safe_open.call_args.args[1] & object_storage_s3.os.O_NOFOLLOW
            )
            path.chmod(0o640)
            with self.assertRaisesRegex(object_storage_s3.S3ClientError, "owner-only"):
                object_storage_s3.load_credentials({"S3_CREDENTIALS_FILE": str(path)})
        with self.assertRaisesRegex(object_storage_s3.S3ClientError, "choose either"):
            object_storage_s3.load_credentials(
                {
                    "AWS_ACCESS_KEY_ID": "not-printed-access",
                    "AWS_SECRET_ACCESS_KEY": "not-printed-secret",
                    "S3_CREDENTIALS_FILE": "/not/read",
                }
            )

    def test_export_deduplicates_equal_content_without_losing_key_records(self) -> None:
        payload = b"same payload"
        client = FakeS3Client({"a": payload, "b": payload})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / "export"
            manifest = object_storage_backup.export_bucket(
                client, "shirokuma-lakehouse", "", destination, root
            )
            self.assertEqual(manifest["object_count"], 2)
            self.assertEqual(manifest["total_bytes"], len(payload) * 2)
            self.assertEqual(
                len(list((destination / "objects").rglob("*.bin"))), 1
            )
            restored = FakeS3Client()
            self.assertEqual(
                object_storage_backup.restore_bucket(
                    restored, "shirokuma-lakehouse", destination, root
                ),
                2,
            )
            self.assertEqual(restored.objects, {"a": payload, "b": payload})

    def test_restore_rejects_intermediate_symlink_before_any_upload(self) -> None:
        body = b"outside"
        digest = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "export"
            outside = root / "outside"
            source.mkdir()
            outside.mkdir()
            (outside / f"{digest}.bin").write_bytes(body)
            (source / "objects").mkdir()
            (source / "objects" / digest[:2]).symlink_to(outside, target_is_directory=True)
            manifest = {
                "schema_version": 1,
                "kind": object_storage_backup.EXPORT_KIND,
                "bucket": "shirokuma-lakehouse",
                "object_count": 1,
                "total_bytes": len(body),
                "objects": [
                    {
                        "key": "object",
                        "size": len(body),
                        "sha256": digest,
                        "file": f"objects/{digest[:2]}/{digest}.bin",
                    }
                ],
            }
            (source / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            client = FakeS3Client()
            with self.assertRaisesRegex(object_storage_backup.BackupError, "symlink"):
                object_storage_backup.restore_bucket(
                    client, "shirokuma-lakehouse", source, root
                )
            self.assertEqual(client.puts, [])

    def test_restore_validates_manifest_totals_keys_and_all_blobs_before_upload(self) -> None:
        body = b"payload"
        digest = hashlib.sha256(body).hexdigest()
        invalid_records = (
            (
                [
                    {"key": "", "size": len(body), "sha256": digest},
                ],
                len(body),
                "malformed",
            ),
            (
                [
                    {"key": "same", "size": len(body), "sha256": digest},
                    {"key": "same", "size": len(body), "sha256": digest},
                ],
                len(body) * 2,
                "duplicate",
            ),
            (
                [{"key": "one", "size": len(body), "sha256": digest}],
                len(body) + 1,
                "total bytes",
            ),
        )
        for records, total_bytes, message in invalid_records:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = root / "export"
                object_path = source / "objects" / digest[:2] / f"{digest}.bin"
                object_path.parent.mkdir(parents=True)
                object_path.write_bytes(body)
                complete_records = [
                    {
                        **record,
                        "etag": "unused",
                        "file": f"objects/{digest[:2]}/{digest}.bin",
                    }
                    for record in records
                ]
                manifest = {
                    "schema_version": 1,
                    "kind": object_storage_backup.EXPORT_KIND,
                    "bucket": "shirokuma-lakehouse",
                    "object_count": len(complete_records),
                    "total_bytes": total_bytes,
                    "objects": complete_records,
                }
                (source / "manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )
                client = FakeS3Client()
                with self.assertRaisesRegex(
                    object_storage_backup.BackupError, message
                ):
                    object_storage_backup.restore_bucket(
                        client, "shirokuma-lakehouse", source, root
                    )
                self.assertEqual(client.puts, [])

    def test_host_export_guard_rejects_colima_temporary_and_non_darwin_paths(self) -> None:
        with mock.patch.object(object_storage_backup.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(object_storage_backup.BackupError, "macOS"):
                object_storage_backup.host_export_root(
                    {"SHIROKUMA_HOST_EXPORT_ROOT": str(Path.home())}
                )
        with mock.patch.object(object_storage_backup.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(object_storage_backup.BackupError, "temporary"):
                object_storage_backup.host_export_root(
                    {"SHIROKUMA_HOST_EXPORT_ROOT": "/tmp/shirokuma-backup"}
                )
            colima = Path.home() / ".colima"
            if colima.is_dir():
                with self.assertRaisesRegex(object_storage_backup.BackupError, "Colima"):
                    object_storage_backup.host_export_root(
                        {"SHIROKUMA_HOST_EXPORT_ROOT": str(colima)}
                    )


if __name__ == "__main__":
    unittest.main()
