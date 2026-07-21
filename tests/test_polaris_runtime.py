from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import verify_polaris_runtime as verifier


ROOT = Path(__file__).resolve().parents[1]


class PolarisRuntimeActivationTests(unittest.TestCase):
    def _fixture(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="polaris-runtime-"))
        self.addCleanup(shutil.rmtree, root)
        contract = json.loads((ROOT / verifier.CONTRACT).read_text(encoding="utf-8"))
        paths = [
            verifier.CONTRACT,
            Path("Makefile"),
            *map(Path, contract["manifests"]),
            *map(Path, contract["documentation"]),
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

    def test_repository_runtime_activation_is_valid(self) -> None:
        verifier.audit(ROOT)

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

    def test_live_acceptance_cannot_self_approve(self) -> None:
        root = self._fixture()
        contract_path = root / verifier.CONTRACT
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["live_acceptance"]["complete"] = True
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        self._assert_code(root, "RUNTIME_ACCEPTANCE")


if __name__ == "__main__":
    unittest.main()
