from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_trusted_image as verifier  # noqa: E402


STATIC_PATHS = (
    Path(".github/workflows/seaweedfs-arm64.yml"),
    Path("bootstrap/seaweedfs/v4.39/Containerfile"),
    Path("bootstrap/seaweedfs/v4.39/admission.json"),
    Path("bootstrap/seaweedfs/v4.39/source.json"),
    Path("bootstrap/seaweedfs/v4.39/trusted-build-contract.json"),
)


class TrustedImageContractTests(unittest.TestCase):
    def _copy_static_tree(self, destination: Path) -> None:
        for relative in STATIC_PATHS:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
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
            (unpin_action, "ACTION_NOT_SHA_PINNED"),
            (add_anonymous_unpinned_action, "ACTION_NOT_SHA_PINNED"),
            (add_unapproved_pinned_action, "WORKFLOW_ACTION_CLOSED_WORLD"),
            (expose_credentials_before_buildx_verification, "BUILDX_CREDENTIAL_BOUNDARY"),
            (change_final_retention, "FINAL_RETENTION"),
            (redirect_image_repository, "IMAGE_WORKFLOW_BINDING"),
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

    def test_retained_sbom_and_scan_are_bound_to_the_release(self) -> None:
        contract = verifier.load_contract(ROOT)
        release = json.loads((ROOT / verifier.RELEASE_PATH).read_text(encoding="utf-8"))
        release.setdefault("digest", "sha256:" + verifier._digest_hex(release["reference"]))
        evidence = ROOT / "bootstrap/seaweedfs/v4.39/evidence"
        verifier._validate_sbom(
            release,
            contract,
            evidence / "seaweedfs-4.39-arm64.cdx.json",
        )
        verifier._validate_trivy(
            release,
            contract,
            evidence / "trivy.json",
            evidence / "trivy-version.json",
        )

        with tempfile.TemporaryDirectory() as directory:
            mutated = Path(directory) / "trivy.json"
            report = json.loads((evidence / "trivy.json").read_text(encoding="utf-8"))
            report["Results"][0].setdefault("Vulnerabilities", []).append(
                {"Severity": "HIGH"}
            )
            mutated.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(verifier.ContractError) as caught:
                verifier._validate_trivy(
                    release,
                    contract,
                    mutated,
                    evidence / "trivy-version.json",
                )
            self.assertEqual(caught.exception.code, "TRIVY_VULNERABILITIES")

    def test_runtime_smoke_is_bound_to_raw_docker_inspect(self) -> None:
        release = {
            "reference": "ghcr.io/tommykammy/shirokuma-seaweedfs@sha256:" + "a" * 64,
            "digest": "sha256:" + "a" * 64,
            "builder": {"run_id": "12345", "run_attempt": "2"},
        }
        inspect_payload = [
            {
                "Config": {"User": "65532:65532"},
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
