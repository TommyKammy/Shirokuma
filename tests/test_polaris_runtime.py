from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import verify_polaris_runtime as verifier


ROOT = Path(__file__).resolve().parents[1]


class PolarisRuntimeActivationTests(unittest.TestCase):
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
            Path(contract["live_acceptance"]["receipt"]),
        ]
        for relative in paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
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

    def test_repository_runtime_activation_is_valid(self) -> None:
        verifier.audit(ROOT)

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

    def test_inline_storage_credential_fails_closed(self) -> None:
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
        with self.assertRaises(AssertionError):
            self._assert_storage_env_contract(path.read_text(encoding="utf-8"))

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

    def test_live_acceptance_cannot_revert_to_incomplete(self) -> None:
        root = self._fixture()
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["live_acceptance"]["complete"] = False
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_ACCEPTANCE")

    def test_live_acceptance_receipt_drift_fails_closed(self) -> None:
        root = self._fixture()
        receipt = root / "security/evidence/polaris-runtime-acceptance.json"
        receipt.write_text(
            receipt.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_ACCEPTANCE")

    def test_live_acceptance_rejects_unbound_producer(self) -> None:
        root = self._fixture()
        receipt_path = root / "security/evidence/polaris-runtime-acceptance.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["acceptance_tool_sha256"] = "0" * 64
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["live_acceptance"]["receipt_sha256"] = hashlib.sha256(
            receipt_path.read_bytes()
        ).hexdigest()
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n",
            encoding="utf-8",
        )
        self._assert_code(root, "RUNTIME_ACCEPTANCE")

    def test_acceptance_tooling_drift_fails_closed(self) -> None:
        root = self._fixture()
        tool = root / "scripts/polaris_runtime_acceptance.py"
        tool.write_text(tool.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_TOOLING")


if __name__ == "__main__":
    unittest.main()
