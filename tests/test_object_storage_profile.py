from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ObjectStorageProfileContractTests(unittest.TestCase):
    def test_flux_owns_seaweedfs_with_dependency_prune_and_readiness(self) -> None:
        workload_path = ROOT / "deploy/gitops/object-storage/kustomization.yaml"
        reconciliation_path = (
            ROOT / "deploy/gitops/clusters/local-lite/object-storage.yaml"
        )

        self.assertTrue(workload_path.is_file(), f"missing {workload_path.relative_to(ROOT)}")
        self.assertTrue(
            reconciliation_path.is_file(),
            f"missing {reconciliation_path.relative_to(ROOT)}",
        )

        reconciliation = reconciliation_path.read_text(encoding="utf-8")
        for required in (
            "kind: Kustomization",
            "name: shirokuma-object-storage",
            "path: ./deploy/gitops/object-storage",
            "prune: true",
            "dependsOn:",
            "name: shirokuma-dev",
            "wait: true",
            "healthChecks:",
            "kind: StatefulSet",
            "name: seaweedfs",
            "namespace: shirokuma-storage",
        ):
            with self.subTest(required=required):
                self.assertIn(required, reconciliation)


if __name__ == "__main__":
    unittest.main()
