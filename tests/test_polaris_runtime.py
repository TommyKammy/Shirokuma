from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = ROOT / "deploy"
RESIDENT_IMAGES = ROOT / "security/resident-images.json"
REQUIRED_COMPONENTS = {"polaris", "postgresql"}
DIGEST_REFERENCE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
RUNTIME_IDENTITY = re.compile(r"(?:polaris|postgres(?:ql)?)", re.IGNORECASE)
MANIFEST_SUFFIXES = {".json", ".yaml", ".yml"}
EVIDENCE_FIELDS = (
    "sbom_artifact",
    "scan_artifact",
    "supply_chain_artifact",
)


class PolarisRuntimeAdmissionTests(unittest.TestCase):
    def test_runtime_admission_is_atomic_and_retains_evidence(self) -> None:
        ledger = json.loads(RESIDENT_IMAGES.read_text(encoding="utf-8"))
        entries = {
            entry["component"]: entry
            for entry in ledger["images"]
            if entry.get("component") in REQUIRED_COMPONENTS
        }

        admitted_components = set(entries)
        self.assertIn(
            admitted_components,
            (set(), REQUIRED_COMPONENTS),
            "Polaris and PostgreSQL admission must be atomic; partial "
            f"admission found: {sorted(admitted_components)}",
        )

        runtime_manifests = sorted(
            str(path.relative_to(ROOT))
            for path in DEPLOY_ROOT.rglob("*")
            if path.is_file()
            and path.suffix in MANIFEST_SUFFIXES
            and RUNTIME_IDENTITY.search(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            [],
            runtime_manifests,
            "Polaris and PostgreSQL runtime manifests must remain blocked "
            "until the separate runtime-acceptance boundary is complete",
        )
        if not entries:
            return

        for component, entry in entries.items():
            with self.subTest(component=component):
                self.assertEqual("linux/arm64", entry.get("platform"))
                self.assertRegex(entry.get("reference", ""), DIGEST_REFERENCE)
                for field in EVIDENCE_FIELDS:
                    artifact = entry.get(field)
                    self.assertIsInstance(artifact, str)
                    self.assertTrue(artifact)
                    self.assertTrue(
                        (RESIDENT_IMAGES.parent / artifact).is_file(),
                        f"{component} {field} is not retained: {artifact}",
                    )


if __name__ == "__main__":
    unittest.main()
