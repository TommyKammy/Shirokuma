from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESIDENT_IMAGES = ROOT / "security/resident-images.json"
REQUIRED_COMPONENTS = {"polaris", "postgresql"}
DIGEST_REFERENCE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
EVIDENCE_FIELDS = (
    "sbom_artifact",
    "scan_artifact",
    "supply_chain_artifact",
)


class PolarisRuntimeAdmissionTests(unittest.TestCase):
    def test_required_runtime_images_are_admitted_with_retained_evidence(self) -> None:
        ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
        entries = {
            entry["component"]: entry
            for entry in ledger["images"]
            if entry.get("component") in REQUIRED_COMPONENTS
        }

        self.assertEqual(
            REQUIRED_COMPONENTS,
            entries.keys(),
            "Polaris and PostgreSQL must both be admitted before their Flux "
            "runtime is materialized",
        )
        for component, entry in entries.items():
            with self.subTest(component=component):
                self.assertEqual("linux/arm64", entry.get("platform"))
                self.assertRegex(entry.get("reference", ""), DIGEST_REFERENCE)
                for field in EVIDENCE_FIELDS:
                    artifact = entry.get(field)
                    self.assertIsInstance(artifact, str)
                    self.assertTrue(artifact)
                    self.assertTrue(
                        (ROOT / artifact).is_file(),
                        f"{component} {field} is not retained: {artifact}",
                    )


if __name__ == "__main__":
    unittest.main()
