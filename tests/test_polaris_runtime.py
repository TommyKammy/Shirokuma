from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import verify_polaris_runtime as verifier


ROOT = Path(__file__).resolve().parents[1]


class PolarisRuntimeActivationTests(unittest.TestCase):
    def test_ci_checkout_retains_accepted_revision_history(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        checkout = (
            "      - name: Checkout\n"
            "        uses: actions/checkout@"
        )
        start = workflow.find(checkout)
        self.assertGreaterEqual(start, 0)
        end = workflow.find("\n      - name:", start + len(checkout))
        self.assertGreater(end, start)
        checkout_step = workflow[start:end]
        self.assertEqual(1, checkout_step.count("          fetch-depth: 0\n"))
        self.assertEqual(
            1,
            checkout_step.count("          persist-credentials: false\n"),
        )

    def _assert_storage_env_contract(self, deployment: str) -> None:
        expected = {
            "AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
            "AWS_REGION": "S3_REGION",
        }
        for variable, key in expected.items():
            block = (
                f"            - name: {variable}\n"
                "              valueFrom:\n"
                "                secretKeyRef:\n"
                "                  name: seaweedfs-s3-application-credentials\n"
                f"                  key: {key}\n"
            )
            self.assertEqual(1, deployment.count(block))
            self.assertEqual(1, deployment.count(f"- name: {variable}\n"))
        self.assertNotIn("AWS_SESSION_TOKEN", deployment)

    def _fixture(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="polaris-runtime-"))
        self.addCleanup(shutil.rmtree, root)
        contract = json.loads((ROOT / verifier.CONTRACT).read_text(encoding="utf-8"))
        paths = [
            verifier.CONTRACT,
            Path("Makefile"),
            *map(Path, contract["manifests"]),
            *map(Path, contract["documentation"]),
            *map(Path, contract["tooling"]),
        ]
        for relative in paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        contract["state"] = "runtime_acceptance_pending"
        contract["live_acceptance"] = {
            "complete": False,
            "required": contract["live_acceptance"]["required"],
        }
        (root / verifier.CONTRACT).write_text(
            json.dumps(contract, indent=2) + "\n",
            encoding="utf-8",
        )
        return root

    def _assert_code(self, root: Path, code: str) -> None:
        with self.assertRaises(verifier.RuntimeContractError) as raised:
            verifier.audit(root)
        self.assertEqual(code, raised.exception.code)

    def _rehash_manifest(self, root: Path, relative: str) -> None:
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(root / relative)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )

    def _rehash_documentation(self, root: Path, relative: str) -> None:
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["documentation"][relative] = verifier._sha256(root / relative)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )

    def _commit_fixture(self, root: Path) -> str:
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "--force", "."],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Shirokuma Tests",
                "-c",
                "user.email=tests@shirokuma.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_repository_runtime_activation_is_valid(self) -> None:
        verifier.audit(ROOT)

    def _iceberg_receipt_fixture(self) -> tuple[Path, dict, str]:
        root = Path(tempfile.mkdtemp(prefix="iceberg-acceptance-"))
        self.addCleanup(shutil.rmtree, root)
        relative = Path(
            "security/evidence/iceberg-table-bootstrap-runtime-acceptance.json"
        )
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, path)
        primary_relative = Path(
            "security/evidence/polaris-runtime-acceptance.json"
        )
        shutil.copy2(ROOT / primary_relative, root / primary_relative)
        contract = json.loads((ROOT / verifier.CONTRACT).read_text(encoding="utf-8"))
        primary = json.loads(
            (
                ROOT / "security/evidence/polaris-runtime-acceptance.json"
            ).read_text(encoding="utf-8")
        )
        return path, contract, primary["repository_revision"]

    def _assert_iceberg_receipt_rejected(
        self,
        path: Path,
        contract: dict,
        revision: str,
    ) -> None:
        with self.assertRaises(verifier.RuntimeContractError) as raised:
            verifier._audit_iceberg_acceptance_receipt(path, contract, revision)
        self.assertEqual("RUNTIME_ACCEPTANCE", raised.exception.code)

    def test_repository_iceberg_acceptance_receipt_is_valid(self) -> None:
        path, contract, revision = self._iceberg_receipt_fixture()
        verifier._audit_iceberg_acceptance_receipt(path, contract, revision)

    def test_iceberg_acceptance_receipt_rejects_non_json_content(self) -> None:
        path, contract, revision = self._iceberg_receipt_fixture()
        path.write_text("arbitrary receipt content\n", encoding="utf-8")
        self._assert_iceberg_receipt_rejected(path, contract, revision)

    def test_live_acceptance_rejects_rehashed_non_json_iceberg_receipt(self) -> None:
        path, contract, _ = self._iceberg_receipt_fixture()
        path.write_text("arbitrary receipt content\n", encoding="utf-8")
        contract["live_acceptance"]["additional_receipts"][0][
            "receipt_sha256"
        ] = verifier._sha256(path)
        with mock.patch.object(verifier, "_audit_accepted_revision_binding"):
            with self.assertRaises(verifier.RuntimeContractError) as raised:
                verifier._audit_live_acceptance(path.parents[2], contract)
        self.assertEqual("RUNTIME_ACCEPTANCE", raised.exception.code)

    def test_iceberg_acceptance_receipt_rejects_semantic_tampering(self) -> None:
        path, contract, revision = self._iceberg_receipt_fixture()
        original = json.loads(path.read_text(encoding="utf-8"))
        cases = (
            ("schema", ("schema_version",), 2),
            ("revision", ("cluster", "repository_revision"), "0" * 40),
            ("flux-ready", ("flux", "kustomizations", 1, "ready"), False),
            (
                "restart",
                ("initial", "polaris_pod_uid"),
                original["rerun_after_polaris_restart"]["polaris_pod_uid"],
            ),
            ("idempotence", ("rerun_after_polaris_restart", "summary", "created"), True),
            ("summary-digest", ("initial", "summary_canonical_sha256"), "0" * 64),
            ("storage", ("storage_inventory_after_rerun", "object_count"), 9),
            ("capacity", ("capacity", "host_available_kib"), 0),
            ("assertion", ("assertions", "storage_guard_passed"), False),
            ("secret", ("secrets", "environment_retained"), True),
        )
        for label, keys, value in cases:
            with self.subTest(label=label):
                receipt = json.loads(json.dumps(original))
                target = receipt
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                path.write_text(
                    json.dumps(receipt, indent=2) + "\n",
                    encoding="utf-8",
                )
                self._assert_iceberg_receipt_rejected(path, contract, revision)

    def test_repository_storage_environment_is_secret_ref_only(self) -> None:
        deployment = (
            ROOT / "deploy/gitops/catalog/server/deployment.yaml"
        ).read_text(encoding="utf-8")
        self._assert_storage_env_contract(deployment)

    def test_manifest_hash_drift_fails_closed(self) -> None:
        root = self._fixture()
        path = root / "deploy/gitops/catalog/server/deployment.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_MANIFEST")

    def test_recovery_runbook_hash_drift_fails_closed(self) -> None:
        root = self._fixture()
        path = root / "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "# drift\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_DOCUMENTATION")

    def test_bootstrap_cleanup_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "docs/design/08_Runbooks/RB-001_Bootstrap_local_lite_lab.md"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "unset TF_VAR_polaris_postgresql_password\n", "", 1
            ),
            encoding="utf-8",
        )
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["documentation"][relative] = verifier._sha256(path)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        self._assert_code(root, "RUNTIME_SECRET")

    def test_unregistered_runtime_file_fails_closed(self) -> None:
        root = self._fixture()
        path = root / "deploy/gitops/catalog/neutral.yaml"
        path.write_text("kind: ConfigMap\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_MANIFEST")

    def test_flux_root_omission_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/clusters/local-lite/kustomization.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "  - catalog-database.yaml\n", ""
            ),
            encoding="utf-8",
        )
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        self._assert_code(root, "RUNTIME_FLUX")

    def test_generation_divergence_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "${POLARIS_CREDENTIAL_GENERATION}", "2"
            ),
            encoding="utf-8",
        )
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        self._assert_code(root, "RUNTIME_GENERATION")

    def test_numeric_generation_annotation_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/database/statefulset.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "generation-${POLARIS_CREDENTIAL_GENERATION}",
                "${POLARIS_CREDENTIAL_GENERATION}",
            ),
            encoding="utf-8",
        )
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        self._assert_code(root, "RUNTIME_GENERATION")

    def test_generation_replacement_omission_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/clusters/local-lite/kustomization.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "          - spec.postBuild.substitute.POLARIS_CREDENTIAL_GENERATION\n",
                "",
                1,
            ),
            encoding="utf-8",
        )
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        self._assert_code(root, "RUNTIME_GENERATION")

    def test_inline_secret_manifest_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/service.yaml"
        path = root / relative
        path.write_text(path.read_text(encoding="utf-8") + "---\nkind: Secret\n", encoding="utf-8")
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_SECRET")

    def test_storage_secret_name_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "name: seaweedfs-s3-application-credentials",
                "name: unreviewed-storage-credentials",
                1,
            ),
            encoding="utf-8",
        )
        self._rehash_manifest(root, relative)
        self._assert_code(root, "RUNTIME_SECRET")

    def test_storage_secret_key_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "                  key: S3_REGION\n",
                "                  key: AWS_DEFAULT_REGION\n",
                1,
            ),
            encoding="utf-8",
        )
        self._rehash_manifest(root, relative)
        self._assert_code(root, "RUNTIME_SECRET")

    def test_storage_network_label_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '        shirokuma.dev/object-storage-client: "true"\n', "", 1
            ),
            encoding="utf-8",
        )
        self._rehash_manifest(root, relative)
        self._assert_code(root, "RUNTIME_NETWORK")

    def test_storage_generation_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '        shirokuma.dev/s3-credential-generation: "1"\n',
                '        shirokuma.dev/s3-credential-generation: "2"\n',
                1,
            ),
            encoding="utf-8",
        )
        self._rehash_manifest(root, relative)
        self._assert_code(root, "RUNTIME_GENERATION")

    def test_storage_rotation_runbook_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = (
            "docs/design/08_Runbooks/"
            "RB-013_Nuke_and_Rebuild_mac_studio_solo.md"
        )
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "   kubectl -n shirokuma-dev rollout status "
                "deployment/polaris --timeout=10m\n",
                "",
                1,
            ),
            encoding="utf-8",
        )
        self._rehash_documentation(root, relative)
        self._assert_code(root, "RUNTIME_GENERATION")

    def test_duplicate_inline_storage_credential_fails_closed_even_if_rehashed(
        self,
    ) -> None:
        root = self._fixture()
        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "          ports:\n",
                "            - name: AWS_ACCESS_KEY_ID\n"
                "              value: forbidden-inline-value\n"
                "          ports:\n",
                1,
            ),
            encoding="utf-8",
        )
        self._rehash_manifest(root, relative)
        self._assert_code(root, "RUNTIME_SECRET")

    def test_admin_argument_fallback_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/catalog/bootstrap/job.yaml"
        path = root / relative
        path.write_text(path.read_text(encoding="utf-8").replace("bootstrap\n", "bootstrap\n            - --print-credentials\n", 1), encoding="utf-8")
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_ADMIN")

    def test_flux_dependency_drift_fails_closed_even_if_rehashed(self) -> None:
        root = self._fixture()
        relative = "deploy/gitops/clusters/local-lite/catalog.yaml"
        path = root / relative
        path.write_text(path.read_text(encoding="utf-8").replace("shirokuma-catalog-bootstrap", "shirokuma-object-storage"), encoding="utf-8")
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["manifests"][relative] = verifier._sha256(path)
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_FLUX")

    def test_pending_runtime_cannot_claim_complete_without_current_evidence(self) -> None:
        root = self._fixture()
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["live_acceptance"]["complete"] = True
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_ACCEPTANCE")

    def test_pending_runtime_rejects_stale_receipt_binding(self) -> None:
        root = self._fixture()
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["live_acceptance"]["receipt"] = (
            "security/evidence/polaris-runtime-acceptance.json"
        )
        contract["live_acceptance"]["receipt_sha256"] = "0" * 64
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_ACCEPTANCE")

    def test_accepted_runtime_requires_receipt_binding(self) -> None:
        root = self._fixture()
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["state"] = "runtime_accepted"
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        self._assert_code(root, "RUNTIME_ACCEPTANCE")

    def test_accepted_revision_must_contain_contracted_desired_state(self) -> None:
        root = self._fixture()
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        revision = self._commit_fixture(root)
        verifier._audit_accepted_revision_binding(root, contract, revision)

        relative = "deploy/gitops/catalog/server/deployment.yaml"
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8") + "# unaccepted drift\n",
            encoding="utf-8",
        )
        contract["manifests"][relative] = verifier._sha256(path)
        with self.assertRaises(verifier.RuntimeContractError) as raised:
            verifier._audit_accepted_revision_binding(root, contract, revision)
        self.assertEqual("RUNTIME_ACCEPTANCE", raised.exception.code)

    def test_acceptance_tooling_drift_fails_closed(self) -> None:
        root = self._fixture()
        tool = root / "scripts/polaris_runtime_acceptance.py"
        tool.write_text(tool.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_TOOLING")


if __name__ == "__main__":
    unittest.main()
