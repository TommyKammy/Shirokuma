from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trusted_image as verifier  # noqa: E402


STATIC_PATHS = (
    Path(".github/workflows/seaweedfs-arm64.yml"),
    Path("bootstrap/seaweedfs/v4.39/Containerfile"),
    Path("bootstrap/seaweedfs/v4.39/admission.json"),
    Path("bootstrap/seaweedfs/v4.39/go-module-inputs.json"),
    Path("bootstrap/seaweedfs/v4.39/go-vendor.tar.xz"),
    Path("bootstrap/seaweedfs/v4.39/source.json"),
    Path("bootstrap/seaweedfs/v4.39/trusted-build-contract.json"),
    Path("bootstrap/seaweedfs/v4.39/evidence/README.md"),
)


class TrustedImageContractTests(unittest.TestCase):
    def _copy_static_tree(self, destination: Path) -> None:
        for relative in STATIC_PATHS:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.name == "go-vendor.tar.xz":
                try:
                    os.link(ROOT / relative, target)
                except OSError:
                    shutil.copy2(ROOT / relative, target)
            else:
                shutil.copy2(ROOT / relative, target)

    def _assert_mutation_fails(
        self,
        mutate: Callable[[Path], None],
        expected_code: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_static_tree(root)
            mutate(root)
            with self.assertRaises(verifier.ContractError) as caught:
                verifier.validate_static_contract(root)
            self.assertEqual(caught.exception.code, expected_code)

    def test_repository_static_contract_is_closed_and_valid(self) -> None:
        contract = verifier.validate_static_contract(ROOT)
        self.assertEqual(
            set(contract["toolchain"]),
            {"buildx", "buildkit", "syft", "trivy", "cosign", "crane"},
        )
        workflow = (ROOT / contract["workflow"]["path"]).read_text(encoding="utf-8")
        self.assertIn("needs: verify", workflow)
        self.assertNotIn("imjasonh/setup-crane@", workflow)
        self.assertNotIn("docker/setup-buildx-action@", workflow)

    def test_pending_repository_audit_is_explicit_and_fail_closed(self) -> None:
        admission = verifier.validate_repository_audit(ROOT)
        self.assertEqual(
            admission["assessment"]["admission"], "pending_main_publication"
        )
        with self.assertRaises(verifier.ContractError) as caught:
            verifier.validate_release_bundle(ROOT, require_promotion=True)
        self.assertEqual(caught.exception.code, "EVIDENCE_MISSING")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._copy_static_tree(root)
            path = root / verifier.ADMISSION_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["bootstrap_observation"]["disposition"] = "approved"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier.validate_repository_audit(root)
            self.assertEqual(caught.exception.code, "BOOTSTRAP_OBSERVATION")

    def test_contract_mutations_fail_with_stable_error_codes(self) -> None:
        def remove_buildkit(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            del data["toolchain"]["buildkit"]
            path.write_text(json.dumps(data), encoding="utf-8")

        def switch_rekor_api_without_migration(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["transparency_log"]["major_api_version"] = 2
            path.write_text(json.dumps(data), encoding="utf-8")

        def alter_containerfile(root: Path) -> None:
            path = root / "bootstrap/seaweedfs/v4.39/Containerfile"
            path.write_text(path.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

        def redirect_source_build_input(root: Path) -> None:
            path = root / verifier.SOURCE_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["build_inputs"]["go"] = (
                "docker.io/library/golang@sha256:" + "a" * 64
            )
            path.write_text(json.dumps(data), encoding="utf-8")

        def alter_module_bundle_hash(root: Path) -> None:
            for relative in (verifier.SOURCE_PATH, verifier.CONTRACT_PATH):
                path = root / relative
                data = json.loads(path.read_text(encoding="utf-8"))
                if relative == verifier.SOURCE_PATH:
                    data["module_inputs"]["bundle_sha256"] = "0" * 64
                else:
                    data["source"]["module_inputs"]["bundle_sha256"] = "0" * 64
                path.write_text(json.dumps(data), encoding="utf-8")

        def permit_networked_module_build(root: Path) -> None:
            container_path = root / "bootstrap/seaweedfs/v4.39/Containerfile"
            container = container_path.read_text(encoding="utf-8").replace(
                "      GOPROXY=off \\\n",
                "      GOPROXY=https://proxy.golang.org \\\n",
                1,
            )
            container_path.write_text(container, encoding="utf-8")
            container_hash = hashlib.sha256(container.encode("utf-8")).hexdigest()
            source_path = root / verifier.SOURCE_PATH
            source = json.loads(source_path.read_text(encoding="utf-8"))
            source["containerfile_sha256"] = container_hash
            source_path.write_text(json.dumps(source), encoding="utf-8")
            contract_path = root / verifier.CONTRACT_PATH
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["source"]["containerfile"]["sha256"] = container_hash
            contract_path.write_text(json.dumps(contract), encoding="utf-8")

        def permit_legacy_cosign_records(root: Path) -> None:
            path = root / verifier.CONTRACT_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["toolchain"]["cosign"][
                "legacy_signature_records_permitted"
            ] = True
            path.write_text(json.dumps(data), encoding="utf-8")

        def unpin_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            workflow = workflow.replace(
                "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10",
                "actions/checkout@v6",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def add_anonymous_unpinned_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    steps:\n",
                "    steps:\n      - uses: attacker/action@main\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def add_unapproved_pinned_action(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    steps:\n",
                f"    steps:\n      - uses: attacker/action@{'a' * 40}\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def expose_credentials_before_buildx_verification(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          actual_commit=$(git -C seaweedfs-src rev-parse HEAD)",
                "          docker login ghcr.io\n"
                "          actual_commit=$(git -C seaweedfs-src rev-parse HEAD)",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def change_final_retention(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            prefix, marker, suffix = workflow.rpartition("retention-days: 90")
            self.assertTrue(marker)
            path.write_text(prefix + "retention-days: 1" + suffix, encoding="utf-8")

        def redirect_image_repository(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "  IMAGE: ghcr.io/tommykammy/shirokuma-seaweedfs",
                "  IMAGE: ghcr.io/attacker/seaweedfs",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def restore_mutable_gha_cache(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "          no-cache: true\n",
                "          no-cache: true\n"
                "          cache-from: type=gha,scope=seaweedfs-4.39-arm64\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def permit_issue_branch_publication(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "      - main\n",
                "      - codex/issue-41\n      - main\n",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def remove_promotion_dependency(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "    needs: verify\n",
                "",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def detach_buildx_from_docker_plugin_discovery(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                'plugin_dir="${DOCKER_CONFIG}/cli-plugins"',
                'plugin_dir="${RUNNER_TEMP}/docker-cli-plugins"',
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def conflate_workflow_and_source_sha(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                '--signer-digest "${GITHUB_WORKFLOW_SHA}"',
                '--signer-digest "${GITHUB_SHA}"',
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        def remove_run_attempt_from_candidate_artifact(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8").replace(
                "seaweedfs-4.39-arm64-candidate-${{ github.run_id }}-${{ github.run_attempt }}",
                "seaweedfs-4.39-arm64-candidate-${{ github.run_id }}",
            )
            path.write_text(workflow, encoding="utf-8")

        def permit_runtime(root: Path) -> None:
            path = root / verifier.ADMISSION_PATH
            data = json.loads(path.read_text(encoding="utf-8"))
            data["runtime_manifests"]["permitted"] = True
            path.write_text(json.dumps(data), encoding="utf-8")

        def inject_command_into_approved_run_step(root: Path) -> None:
            path = root / ".github/workflows/seaweedfs-arm64.yml"
            workflow = path.read_text(encoding="utf-8")
            marker = "          set -euo pipefail"
            self.assertIn(marker, workflow)
            workflow = workflow.replace(
                marker,
                marker + "\n          echo attacker-controlled-command",
                1,
            )
            path.write_text(workflow, encoding="utf-8")

        cases = (
            (remove_buildkit, "TOOLCHAIN_CLOSED_WORLD"),
            (switch_rekor_api_without_migration, "REKOR_API_CONTRACT"),
            (alter_containerfile, "CONTAINERFILE_HASH"),
            (redirect_source_build_input, "SOURCE_BUILD_INPUT_USE"),
            (alter_module_bundle_hash, "MODULE_INPUT_HASH"),
            (permit_networked_module_build, "MODULE_BUILD_POLICY"),
            (permit_legacy_cosign_records, "COSIGN_FORMAT_CONTRACT"),
            (unpin_action, "ACTION_NOT_SHA_PINNED"),
            (add_anonymous_unpinned_action, "ACTION_NOT_SHA_PINNED"),
            (add_unapproved_pinned_action, "WORKFLOW_ACTION_CLOSED_WORLD"),
            (expose_credentials_before_buildx_verification, "BUILDX_CREDENTIAL_BOUNDARY"),
            (change_final_retention, "FINAL_RETENTION"),
            (redirect_image_repository, "IMAGE_WORKFLOW_BINDING"),
            (restore_mutable_gha_cache, "BUILD_CACHE_POLICY"),
            (permit_issue_branch_publication, "MAIN_ONLY_PUBLICATION"),
            (detach_buildx_from_docker_plugin_discovery, "BUILDX_PLUGIN_DISCOVERY"),
            (conflate_workflow_and_source_sha, "WORKFLOW_SHA_SEMANTICS"),
            (remove_run_attempt_from_candidate_artifact, "WORKFLOW_CONTRACT_LITERAL"),
            (remove_promotion_dependency, "PROMOTION_DEPENDENCY"),
            (permit_runtime, "ADMISSION_RUNTIME_STATE"),
            (inject_command_into_approved_run_step, "WORKFLOW_HASH"),
        )
        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self._assert_mutation_fails(mutate, expected_code)

    def test_rekor_uuid_accepts_both_official_lengths(self) -> None:
        for value in ("a" * 64, "B" * 80):
            with self.subTest(value=value):
                self.assertIsNotNone(verifier.REKOR_UUID_RE.fullmatch(value))
        for value in ("a" * 63, "a" * 65, "g" * 64):
            with self.subTest(value=value):
                self.assertIsNone(verifier.REKOR_UUID_RE.fullmatch(value))

    def test_repository_cosign_reverification_is_pinned_and_cryptographic(self) -> None:
        contract = {
            "toolchain": {"cosign": {"version": "v3.1.1"}},
        }
        release = {
            "identity": (
                "https://github.com/TommyKammy/Shirokuma/.github/workflows/"
                "seaweedfs-arm64.yml@refs/heads/main"
            ),
            "issuer": "https://token.actions.githubusercontent.com",
            "builder": {
                "workflow_name": "SeaweedFS 4.39 trusted arm64 build",
                "ref": "refs/heads/main",
                "repository": "TommyKammy/Shirokuma",
                "workflow_sha": "a" * 40,
            },
        }
        version = mock.Mock(
            returncode=0,
            stdout=json.dumps({"gitVersion": "v3.1.1"}),
            stderr="",
        )
        verified = mock.Mock(returncode=0, stdout="Verified OK\n", stderr="")
        with mock.patch.object(verifier.shutil, "which", return_value="/bin/cosign"), mock.patch.object(
            verifier.subprocess, "run", side_effect=[version, verified]
        ) as run:
            verifier._verify_retained_cosign_bundle(
                contract,
                release,
                Path("bundle.json"),
                Path("manifest.json"),
            )
        command = run.call_args_list[1].args[0]
        self.assertEqual(command[:2], ["/bin/cosign", "verify-blob"])
        self.assertIn("--bundle", command)
        self.assertIn("--certificate-identity", command)
        self.assertIn("--certificate-oidc-issuer", command)
        self.assertIn("--certificate-github-workflow-sha", command)
        self.assertEqual(command[-1], "manifest.json")

        rejected = mock.Mock(returncode=1, stdout="", stderr="invalid signature")
        with mock.patch.object(verifier.shutil, "which", return_value="/bin/cosign"), mock.patch.object(
            verifier.subprocess, "run", side_effect=[version, rejected]
        ):
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._verify_retained_cosign_bundle(
                    contract,
                    release,
                    Path("bundle.json"),
                    Path("manifest.json"),
                )
        self.assertEqual(caught.exception.code, "COSIGN_CRYPTO_VERIFY")

    def test_runtime_smoke_is_bound_to_raw_docker_inspect(self) -> None:
        release = {
            "reference": "ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:" + "a" * 64,
            "digest": "sha256:" + "a" * 64,
            "builder": {"run_id": "12345", "run_attempt": "2"},
            "source": {
                "module_inputs": {"bundle_sha256": "b" * 64},
            },
        }
        inspect_payload = [
            {
                "Config": {
                    "User": "65532:65532",
                    "Labels": {
                        "dev.shirokuma.go-vendor-bundle.sha256": "b" * 64,
                    },
                },
                "Path": "/usr/bin/weed",
                "Args": ["mini", "-dir=/data"],
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "Tmpfs": {
                        "/tmp": "rw,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=1777",
                        "/data": "rw,nosuid,nodev,size=64m,uid=65532,gid=65532,mode=0755",
                    },
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PidsLimit": 256,
                    "Memory": 536870912,
                },
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            inspect_path = evidence / "runtime-inspect.json"
            smoke_path = evidence / "runtime-smoke.json"
            inspect_path.write_text(json.dumps(inspect_payload), encoding="utf-8")
            smoke = {
                "schema_version": 2,
                "result": "passed",
                "reference": release["reference"],
                "digest": release["digest"],
                "user": "65532:65532",
                "command": ["/usr/bin/weed", "mini", "-dir=/data"],
                "read_only_rootfs": True,
                "tmpfs": ["/tmp", "/data"],
                "capabilities_dropped": "ALL",
                "no_new_privileges": True,
                "sustained_running_seconds": 10,
                "run_id": "12345",
                "run_attempt": "2",
                "runtime_inspect": {
                    "file": inspect_path.name,
                    "sha256": verifier._sha256(inspect_path),
                },
            }
            smoke_path.write_text(json.dumps(smoke), encoding="utf-8")

            verifier._validate_runtime(release, smoke_path, inspect_path)

            inspect_payload[0]["Config"]["User"] = "0:0"
            inspect_path.write_text(json.dumps(inspect_payload), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_runtime(release, smoke_path, inspect_path)
            self.assertEqual(caught.exception.code, "RUNTIME_INSPECT_USER")

    def test_promotion_is_bound_to_the_candidate_release_snapshot(self) -> None:
        contract = verifier.load_contract(ROOT)
        digest = "sha256:" + "b" * 64
        builder = {"run_id": "67890", "run_attempt": "3"}
        candidate_artifact = "seaweedfs-4.39-arm64-candidate-67890-3"

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            candidate_path = evidence / "candidate-release-evidence.json"
            promotion_path = evidence / "promotion-evidence.json"
            candidate_release = {
                "schema_version": 2,
                "component": contract["component"],
                "version": contract["version"],
                "platform": contract["platform"],
                "reference": contract["image"]["repository"] + "@" + digest,
                "digest": digest,
                "builder": builder,
                "contract": {"sha256": "c" * 64},
                "promotion": {
                    "status": "pending",
                    "trusted_tag": contract["image"]["trusted_tag"],
                    "tool": "crane",
                },
                "actions_artifact": {
                    "role": contract["evidence"]["actions_artifact_role"],
                    "candidate_name": candidate_artifact,
                    "retention_days": contract["evidence"]["candidate_retention_days"],
                },
                "artifacts": {"source.json": "d" * 64},
            }
            candidate_path.write_text(json.dumps(candidate_release), encoding="utf-8")
            candidate_hash = verifier._sha256(candidate_path)
            release = dict(candidate_release)
            release["promotion"] = {
                "status": "verified",
                "run_id": builder["run_id"],
                "run_attempt": builder["run_attempt"],
                "trusted_tag": contract["image"]["trusted_tag"],
                "trusted_tag_role": contract["image"]["trusted_tag_role"],
                "trusted_tag_digest": digest,
                "evidence": promotion_path.name,
                "candidate_artifact": candidate_artifact,
                "candidate_release_evidence": candidate_path.name,
                "candidate_release_sha256": candidate_hash,
            }
            release["actions_artifact"] = {
                "role": contract["evidence"]["actions_artifact_role"],
                "final_name": "seaweedfs-4.39-arm64-67890-3",
                "retention_days": contract["evidence"]["final_retention_days"],
            }
            release["artifacts"] = {
                **candidate_release["artifacts"],
                candidate_path.name: candidate_hash,
                promotion_path.name: "e" * 64,
            }
            promotion = {
                "schema_version": 1,
                "status": "verified",
                "reference": release["reference"],
                "trusted_tag": (
                    contract["image"]["repository"]
                    + ":"
                    + contract["image"]["trusted_tag"]
                ),
                "trusted_tag_role": contract["image"]["trusted_tag_role"],
                "trusted_tag_digest": digest,
                "promoted_at": "2026-07-15T00:00:00Z",
                "run_id": builder["run_id"],
                "run_attempt": builder["run_attempt"],
                "tool": {
                    "name": "crane",
                    "version": contract["toolchain"]["crane"]["version"],
                    "archive_sha256": contract["toolchain"]["crane"][
                        "linux_arm64_archive_sha256"
                    ],
                    "verified_before_registry_login": True,
                },
                "candidate": {
                    "artifact_name": candidate_artifact,
                    "snapshot_file": candidate_path.name,
                    "release_evidence_sha256": candidate_hash,
                    "contract_sha256": release["contract"]["sha256"],
                },
            }
            promotion_path.write_text(json.dumps(promotion), encoding="utf-8")

            verifier._validate_promotion(
                release,
                contract,
                promotion_path,
                candidate_path,
            )

            candidate_release["component"] = "mutated-after-validation"
            candidate_path.write_text(json.dumps(candidate_release), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_promotion(
                    release,
                    contract,
                    promotion_path,
                    candidate_path,
                )
            self.assertEqual(caught.exception.code, "PROMOTION_CANDIDATE_HASH")


if __name__ == "__main__":
    unittest.main()
