from __future__ import annotations

import unittest

from test_trino_bootstrap import (
    _admitted_trino_image_references,
    _trino_workload_manifests,
)


class SupersetBootstrapPrerequisiteTests(unittest.TestCase):
    def test_approved_trino_runtime_is_materialized(self) -> None:
        trino_images = _admitted_trino_image_references()
        checks = {
            "admitted Trino image": trino_images,
            "Trino workload using admitted image": _trino_workload_manifests(
                admitted_images=trino_images
            ),
        }
        missing = [name for name, evidence in checks.items() if not evidence]

        self.assertEqual(
            [],
            missing,
            "SUPERSET_TRINO_RUNTIME_PREREQUISITE_MISSING "
            "Superset bootstrap stays blocked until the repository contains "
            "the approved Trino runtime prerequisite; missing: "
            + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
