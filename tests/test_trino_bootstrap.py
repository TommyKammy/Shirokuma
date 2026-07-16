from __future__ import annotations

import unittest

from test_iceberg_table_bootstrap import (
    _admitted_polaris_image_references,
    _polaris_workload_manifests,
)


class TrinoBootstrapPrerequisiteTests(unittest.TestCase):
    def test_missing_polaris_runtime_keeps_trino_bootstrap_blocked(self) -> None:
        admitted_images = _admitted_polaris_image_references()
        workloads = _polaris_workload_manifests()

        self.assertFalse(
            admitted_images and workloads,
            "Replace this blocker regression with Trino admission, Flux profile, "
            "connector, and SELECT smoke checks only after an admitted Polaris image "
            "and its Deployment or StatefulSet are materialized",
        )


if __name__ == "__main__":
    unittest.main()
