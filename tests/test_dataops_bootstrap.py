from __future__ import annotations

import unittest

from test_iceberg_table_bootstrap import _admitted_polaris_image_references
from test_trino_bootstrap import (
    _admitted_postgresql_image_references,
    _admitted_trino_image_references,
    _polaris_prerequisite_workload_manifests,
    _postgresql_workload_manifests,
    _trino_workload_manifests,
)


class DataOpsBootstrapPrerequisiteTests(unittest.TestCase):
    def test_approved_trino_iceberg_path_is_materialized(self) -> None:
        checks = {
            "admitted Polaris image": _admitted_polaris_image_references(),
            "Polaris workload": _polaris_prerequisite_workload_manifests(),
            "admitted PostgreSQL image": _admitted_postgresql_image_references(),
            "PostgreSQL workload": _postgresql_workload_manifests(),
            "admitted Trino image": _admitted_trino_image_references(),
            "Trino workload": _trino_workload_manifests(),
        }
        missing = [name for name, evidence in checks.items() if not evidence]

        self.assertEqual(
            [],
            missing,
            "Dagster/dbt bootstrap stays blocked until the repository contains "
            "the approved Trino/Polaris/Iceberg runtime path; missing: "
            + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
