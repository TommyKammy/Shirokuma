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
    def test_v1_18_2_inventory_covers_every_chart_image(self) -> None:
        inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))

        self.assertEqual(inventory["kyverno_version"], "v1.18.2")
        self.assertEqual(inventory["chart_version"], "3.8.2")
        self.assertEqual(inventory["admission_status"], "blocked")
        images = inventory["images"]
        self.assertEqual(
            {image["component"] for image in images},
            {
                "admission-controller",
                "background-controller",
                "cleanup-controller",
                "reports-controller",
                "kyverno-cli",
                "kyvernopre",
                "readiness-checker",
            },
        )
        for image in images:
            with self.subTest(component=image["component"]):
                self.assertEqual(image["platform"], "linux/arm64")
                self.assertRegex(image["reference"], DIGEST_REFERENCE)
                self.assertEqual(image["scan_summary"]["critical"], 0)
                self.assertGreater(image["scan_summary"]["high"], 0)


if __name__ == "__main__":
    unittest.main()
