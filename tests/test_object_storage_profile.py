from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ObjectStorageProfileContractTests(unittest.TestCase):
    def test_blocked_candidate_is_recorded_without_runtime_manifests(self) -> None:
        admission_path = ROOT / "bootstrap/seaweedfs/v4.39/admission.json"
        self.assertTrue(admission_path.is_file())
        admission = json.loads(admission_path.read_text(encoding="utf-8"))

        self.assertEqual(admission["schema_version"], 1)
        self.assertEqual(admission["component"], "seaweedfs")
        self.assertEqual(admission["version"], "4.39")
        self.assertEqual(admission["platform"], "linux/arm64")
        self.assertEqual(admission["assessment"]["admission"], "blocked")
        self.assertIs(admission["assessment"]["exception_eligible"], False)
        self.assertIs(admission["runtime_manifests"]["permitted"], False)

        index_reference = admission["candidate"]["index_reference"]
        self.assertRegex(index_reference, r"@sha256:[0-9a-f]{64}$")
        self.assertRegex(
            admission["candidate"]["manifest_digest"],
            r"^sha256:[0-9a-f]{64}$",
        )

        blockers = admission["assessment"]["blockers"]
        self.assertEqual(
            {blocker["control"] for blocker in blockers},
            {"signature", "source_revision_signature", "slsa_provenance"},
        )
        for blocker in blockers:
            with self.subTest(control=blocker["control"]):
                self.assertEqual(blocker["status"], "missing")
                self.assertTrue(blocker["evidence"].strip())

        for relative_path in admission["runtime_manifests"]["paths"]:
            path = ROOT / relative_path
            with self.subTest(runtime_manifest=relative_path):
                self.assertFalse(path.exists(), f"blocked candidate emitted {relative_path}")

        self.assertEqual(
            admission["next_action"]["mode"],
            "approved-source-build-or-signed-upstream-release",
        )
        self.assertIs(admission["next_action"]["decision_record_required"], True)
        self.assertGreaterEqual(len(admission["next_action"]["requirements"]), 4)


if __name__ == "__main__":
    unittest.main()
