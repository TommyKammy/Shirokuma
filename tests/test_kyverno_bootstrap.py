from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "bootstrap/kyverno/v1.18.2/images.json"
DIGEST_REFERENCE = re.compile(
    r"^[^:@\s]+(?:/[^:@\s]+)+@sha256:[0-9a-f]{64}$"
)


class KyvernoBootstrapContractTests(unittest.TestCase):
    def test_local_lab_admission_blocks_the_seven_noncritical_candidates(self) -> None:
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))

        admission = inventory["local_lab_admission"]
        self.assertEqual(admission["profile"], "local-lab")
        self.assertEqual(admission["status"], "blocked")
        self.assertEqual(admission["components"], [])
        self.assertCountEqual(
            admission["candidate_components"],
            [
                "admission-controller",
                "background-controller",
                "cleanup-controller",
                "reports-controller",
                "kyverno-cli",
                "kyvernopre",
                "readiness-checker-cleanup-hook",
            ],
        )

        images = {image["component"]: image for image in inventory["images"]}
        candidates = [
            images[component] for component in admission["candidate_components"]
        ]
        self.assertTrue(
            all(image["scan_summary"]["critical"] == 0 for image in candidates)
        )
        self.assertEqual(inventory["admission_status"], "blocked")
        self.assertEqual(
            admission["excluded"],
            {
                "component": "readiness-checker-test-hook",
                "reason": "critical finding",
                "helm_test_enabled": False,
            },
        )

    def test_v1_18_2_inventory_covers_every_chart_image(self) -> None:
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))

        self.assertEqual(inventory["kyverno_version"], "v1.18.2")
        self.assertEqual(inventory["chart_version"], "3.8.2")
        self.assertEqual(inventory["admission_status"], "blocked")
        images = inventory["images"]
        expected_versions = {
            "admission-controller": "v1.18.2",
            "background-controller": "v1.18.2",
            "cleanup-controller": "v1.18.2",
            "reports-controller": "v1.18.2",
            "kyverno-cli": "v1.18.2",
            "kyvernopre": "v1.18.2",
            "readiness-checker-cleanup-hook": "v1.18.2",
            "readiness-checker-test-hook": "latest",
        }
        self.assertEqual(len(images), len(expected_versions))
        self.assertEqual(
            {image["component"]: image["version"] for image in images},
            expected_versions,
        )
        for image in images:
            with self.subTest(component=image["component"]):
                self.assertEqual(image["platform"], "linux/arm64")
                self.assertRegex(image["reference"], DIGEST_REFERENCE)
                self.assertGreater(image["scan_summary"]["high"], 0)

        images_by_component = {image["component"]: image for image in images}
        cleanup_hook = images_by_component["readiness-checker-cleanup-hook"]
        test_hook = images_by_component["readiness-checker-test-hook"]
        self.assertNotEqual(cleanup_hook["reference"], test_hook["reference"])
        self.assertEqual(cleanup_hook["scan_summary"]["critical"], 0)
        self.assertGreater(test_hook["scan_summary"]["critical"], 0)


if __name__ == "__main__":
    unittest.main()
