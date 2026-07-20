from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts/verify_polaris_admin_build_inputs.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_module(
    "verify_polaris_admin_build_inputs",
    VERIFIER_PATH,
)


class PolarisAdminBuildInputsTests(unittest.TestCase):
    REQUIRED_PATHS = (
        verifier.CONTRACT_PATH,
        verifier.SOURCE_PATH,
        verifier.PARENT_DESCRIPTOR_PATH,
        verifier.PARENT_VERIFICATION_PATH,
        verifier.WORKFLOW_PATH,
        verifier.PACKAGER_PATH,
        verifier.SOURCE_VALIDATOR_PATH,
    )

    def _copy_root(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for relative in self.REQUIRED_PATHS:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        return root

    def _mutate_contract(
        self,
        root: Path,
        mutator: Callable[[dict], None],
    ) -> None:
        path = root / verifier.CONTRACT_PATH
        value = json.loads(path.read_text(encoding="utf-8"))
        mutator(value)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    def _assert_code(
        self,
        root: Path,
        expected: str,
    ) -> None:
        with self.assertRaises(verifier.ContractError) as raised:
            verifier.audit(root)
        self.assertEqual(expected, raised.exception.code)

    def test_repository_contract_passes(self) -> None:
        verifier.audit(ROOT)

    def test_cli_passes(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(VERIFIER_PATH),
                "audit",
                "--root",
                str(ROOT),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("admin image/runtime remain disabled", result.stdout)

    def test_duplicate_contract_key_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.CONTRACT_PATH
        contract = path.read_text(encoding="utf-8").replace(
            '{\n  "schema_version": 1,',
            '{\n  "schema_version": 999,\n  "schema_version": 1,',
            1,
        )
        path.write_text(contract, encoding="utf-8")
        self._assert_code(root, "JSON_INVALID")

    def test_lifecycle_must_retire_in_evidence_review_pr(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["lifecycle"].__setitem__(
                "retire_in_evidence_review_pr",
                False,
            ),
        )
        self._assert_code(root, "LIFECYCLE_STATE")

    def test_candidate_attempt_policy_is_closed_world(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["publication_policy"][
                "candidate_attempt_policy"
            ].__setitem__(
                "failed_attempt_admitted",
                True,
            ),
        )
        self._assert_code(root, "CANDIDATE_ATTEMPT_POLICY")

    def test_visibility_bootstrap_forbids_authenticated_fallback(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["publication_policy"][
                "visibility_bootstrap"
            ].__setitem__(
                "authenticated_fallback",
                True,
            ),
        )
        self._assert_code(root, "VISIBILITY_BOOTSTRAP")

    def test_relational_only_claim_is_rejected(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["admin_dependency_surface"].__setitem__(
                "relational_only",
                True,
            ),
        )
        self._assert_code(root, "ADMIN_SURFACE")

    def test_nosql_review_state_is_required(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["admin_dependency_surface"].__setitem__(
                "review_state",
                "approved",
            ),
        )
        self._assert_code(root, "ADMIN_SURFACE")

    def test_mongodb_dependency_cannot_be_omitted(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["admin_dependency_surface"].__setitem__(
                "unconditional_external_dependencies",
                [],
            ),
        )
        self._assert_code(root, "ADMIN_SURFACE")

    def test_admin_image_gate_cannot_be_enabled(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["downstream_gates"].__setitem__(
                "admin_image_publication_enabled",
                True,
            ),
        )
        self._assert_code(root, "DOWNSTREAM_GATE")

    def test_runtime_gate_cannot_be_enabled(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["downstream_gates"].__setitem__(
                "admin_runtime_enabled",
                True,
            ),
        )
        self._assert_code(root, "DOWNSTREAM_GATE")

    def test_parent_reference_is_exact(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["parent_snapshot"].__setitem__(
                "artifact_reference",
                value["parent_snapshot"]["artifact_reference"].replace(
                    "fa889d2",
                    "0a889d2",
                ),
            ),
        )
        self._assert_code(root, "PARENT_SNAPSHOT")

    def test_parent_descriptor_bytes_are_pinned(self) -> None:
        root = self._copy_root()
        path = root / verifier.PARENT_DESCRIPTOR_PATH
        path.write_text(
            path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "PARENT_DESCRIPTOR_HASH")

    def test_admin_build_preimage_is_exact(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["source"]["admin_build_preimage"].__setitem__(
                "sha256",
                "0" * 64,
            ),
        )
        self._assert_code(root, "ADMIN_BUILD_PREIMAGE")

    def test_server_regression_task_cannot_be_removed(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["source"]["tasks"].remove(
                ":polaris-server:quarkusAppPartsBuild"
            ),
        )
        self._assert_code(root, "SOURCE_CONTRACT")

    def test_floating_action_ref_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            (
                "sigstore/cosign-installer@"
                "6f9f17788090df1f26f669e9d70d6ae9567deba6"
            ),
            "sigstore/cosign-installer@v4",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "ACTION_NOT_SHA_PINNED")

    def test_duplicate_installer_action_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        action = (
            "        uses: sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2"
        )
        workflow = path.read_text(encoding="utf-8").replace(
            action,
            f"{action}\n{action}",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_ACTION_CLOSED_WORLD")

    def test_action_before_privileged_trust_gate_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        boundary = (
            "    steps:\n"
            "      - name: Enforce the main-source trust boundary"
        )
        injected = (
            "    steps:\n"
            "      - uses: actions/checkout@"
            "df4cb1c069e1874edd31b4311f1884172cec0e10\n"
            "      - name: Enforce the main-source trust boundary"
        )
        workflow = path.read_text(encoding="utf-8").replace(
            boundary,
            injected,
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_STEP_NAME")

    def test_unnamed_run_before_privileged_trust_gate_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        boundary = (
            "    steps:\n"
            "      - name: Enforce the main-source trust boundary"
        )
        injected = (
            "    steps:\n"
            "      - shell: bash\n"
            "        env:\n"
            "          GHCR_TOKEN: ${{ github.token }}\n"
            "        run: echo pre-gate-write-capable-step\n"
            "      - name: Enforce the main-source trust boundary"
        )
        workflow = path.read_text(encoding="utf-8").replace(
            boundary,
            injected,
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_STEP_NAME")

    def test_source_workflow_sha_equality_is_required(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            '          test "${GITHUB_SHA}" = "${GITHUB_WORKFLOW_SHA}"',
            '          test -n "${GITHUB_WORKFLOW_SHA}"',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_TRUST_GATE")

    def test_privileged_trust_gate_is_exactly_bound(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "          set -euo pipefail\n"
            '          test "${GITHUB_REPOSITORY}" = "TommyKammy/Shirokuma"\n',
            "          set -euo pipefail\n"
            "          # Reviewed trust gate may not carry dead code.\n"
            '          test "${GITHUB_REPOSITORY}" = "TommyKammy/Shirokuma"\n',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_TRUST_GATE_STEP")

    def test_static_audit_cannot_be_lifecycle_gated(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "      - name: Validate the publication-pending contract\n"
            "        shell: bash\n",
            "      - name: Validate the publication-pending contract\n"
            "        if: steps.lifecycle.outputs.active == 'true'\n"
            "        shell: bash\n",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_STATIC_AUDIT_ORDER")

    def test_heavy_steps_remain_lifecycle_gated(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "      - name: Verify the exact builder toolchain\n"
            "        if: steps.lifecycle.outputs.active == 'true'\n",
            "      - name: Verify the exact builder toolchain\n"
            "        if: always()\n",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_HEAVY_STEP_GATE")

    def test_builder_toolchain_is_gated_before_resolution(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            '          test "${builder_gradle}" = "${EXPECTED_GRADLE_VERSION}"',
            '          test -n "${builder_gradle}"',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_BUILDER_TOOLCHAIN")

    def test_builder_observation_is_retained_in_evidence(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            'os.environ["OBSERVED_BUILDER_PLATFORM"]',
            'os.environ["EXPECTED_BUILDER_PLATFORM"]',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_BUILDER_EVIDENCE")

    def test_parent_evidence_must_trigger_both_workflow_events(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            (
                "      - bootstrap/polaris/v1.6.0/evidence/"
                "gradle-dependency-inputs.json\n"
            ),
            "",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_PATH_FILTER")

    def test_oras_digest_is_validated_before_workflow_export(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            '          if ! [[ "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then',
            '          if [[ -z "${digest}" ]]; then',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_DIGEST_VALIDATION")

    def test_cosign_bundle_must_bind_the_raw_manifest(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "          cosign verify-blob \\\n",
            "          true # detached bundle verification removed\n",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_COSIGN_BINDING")

    def test_cosign_verification_requires_exact_workflow_sha(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            (
                "            --certificate-github-workflow-sha "
                '"${GITHUB_WORKFLOW_SHA}" \\\n'
            ),
            "",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_COSIGN_IDENTITY")

    def test_pull_request_target_is_rejected(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "  pull_request:",
            "  pull_request_target:",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_TRIGGER")

    def test_network_none_proof_is_required(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "            --network none \\\n",
            "",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_OFFLINE_STEP")

    def test_network_none_comment_cannot_mask_a_networked_build(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        boundary = (
            "          docker run --rm \\\n"
            "            --platform linux/arm64 \\\n"
            "            --network none \\\n"
        )
        networked = (
            "          # verifier marker: --network none\n"
            "          docker run --rm \\\n"
            "            --platform linux/arm64 \\\n"
            "            --network bridge \\\n"
        )
        workflow = path.read_text(encoding="utf-8").replace(
            boundary,
            networked,
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_OFFLINE_STEP")

    def test_downloaded_candidate_is_bound_to_read_only_outputs(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "needs.validate.outputs.archive_sha256",
            "steps.candidate.outputs.archive_sha256",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_REQUIRED_LITERAL")

    def test_candidate_artifact_name_is_bound_across_jobs(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "name: ${{ needs.validate.outputs.candidate_artifact_name }}",
            (
                "name: polaris-admin-candidate-${{ github.run_id }}-"
                "${{ github.run_attempt }}"
            ),
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_CANDIDATE_ARTIFACT_BINDING")

    def test_run_scoped_candidate_tag_cannot_be_reused(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            (
                'tag="${ARTIFACT_REPOSITORY}:1.6.0-'
                '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"'
            ),
            'tag="${ARTIFACT_REPOSITORY}:1.6.0-${GITHUB_RUN_ID}"',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_CANDIDATE_ATTEMPT")

    def test_anonymous_pull_cannot_gain_a_credential_fallback(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            "          oras logout ghcr.io\n",
            "          oras logout ghcr.io\n"
            "          oras login ghcr.io # forbidden fallback\n",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_VISIBILITY_BOOTSTRAP")

    def test_nosql_surface_must_be_carried_to_publication_record(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            '                  ":polaris-persistence-nosql-api",\n',
            "",
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_REQUIRED_LITERAL")

    def test_credential_gate_is_carried_to_publication_record(self) -> None:
        root = self._copy_root()
        path = root / verifier.WORKFLOW_PATH
        workflow = path.read_text(encoding="utf-8").replace(
            '"credential_material_permitted": False,',
            '"credential_material_permitted": True,',
            1,
        )
        path.write_text(workflow, encoding="utf-8")
        self._assert_code(root, "WORKFLOW_CREDENTIAL_GATE")

    def test_legacy_publisher_cannot_be_reintroduced(self) -> None:
        root = self._copy_root()
        legacy = root / verifier.LEGACY_WORKFLOW_PATH
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("name: forbidden legacy publisher\n", encoding="utf-8")
        self._assert_code(root, "LEGACY_WORKFLOW_PRESENT")

    def test_review_pending_state_does_not_republish(self) -> None:
        root = self._copy_root()
        self._mutate_contract(
            root,
            lambda value: value["lifecycle"].__setitem__(
                "state",
                "admin_dependency_snapshot_review_pending",
            ),
        )
        self._assert_code(root, "LIFECYCLE_STATE")


if __name__ == "__main__":
    unittest.main()
