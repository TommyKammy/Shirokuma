from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GITOPS_ROOT = ROOT / "deploy/gitops"


class IcebergTableBootstrapPrerequisiteTests(unittest.TestCase):
    def test_polaris_runtime_dependency_is_materialized(self) -> None:
        manifests = tuple(GITOPS_ROOT.rglob("*.yaml"))
        polaris_manifests = []
        for path in manifests:
            text = path.read_text(encoding="utf-8")
            if re.search(
                r"(?m)^\s*(?:app\.kubernetes\.io/name:|name:)\s*polaris(?:\s|[-_])",
                text,
            ):
                polaris_manifests.append(path.relative_to(ROOT))

        self.assertTrue(
            polaris_manifests,
            "Iceberg bootstrap must remain blocked until the approved Polaris "
            "runtime is materialized through deploy/gitops",
        )


if __name__ == "__main__":
    unittest.main()
