from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "docs/design/11_UI_UX"
MOCKUPS = UI_ROOT / "111_UI_Mockups_v0.2.2.md"
INTERACTION_MODEL = UI_ROOT / "113_Interaction_Model.md"
UI_REQUIREMENTS = ROOT / "docs/design/03_Requirements/038_UI_Requirements.md"
TRACEABILITY_MATRIX = ROOT / "docs/design/03_Requirements/037_Traceability_Matrix.md"
ISSUE_URL = "https://github.com/TommyKammy/Shirokuma/issues/5"


class UIDesignBaselineTests(unittest.TestCase):
    def test_repository_mockup_references_resolve(self) -> None:
        markdown = MOCKUPS.read_text(encoding="utf-8")
        references = re.findall(r"!\[[^]]*\]\(([^)]+)\)", markdown)

        self.assertEqual(len(references), 5)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertFalse(reference.startswith(("/", "~")))
                self.assertTrue(
                    (MOCKUPS.parent / reference).resolve().is_file(),
                    f"missing mockup asset: {reference}",
                )

    def test_referenced_interaction_model_is_materialized(self) -> None:
        self.assertTrue(
            INTERACTION_MODEL.is_file(),
            "UI requirements reference a missing interaction model",
        )

    def test_mockups_show_required_decision_evidence(self) -> None:
        expected = {
            "01_den_agent_mission_control.svg": (
                "Proposal queue",
                "Review proposal",
                "GitOps reconciles",
            ),
            "02_virtual_warehouse.svg": (
                "ESTIMATED COST",
                "Create pull request",
                "No direct apply",
            ),
            "03_pawprints_audit_timeline.svg": (
                "POLICY",
                "INFERENCE COST",
                "OTel trace",
            ),
            "04_catalog_lineage.svg": (
                "OWNER",
                "QUALITY",
                "Impact analysis",
                "LINEAGE",
            ),
        }

        asset_root = ROOT / "docs/design/assets/ui_mockups"
        for filename, labels in expected.items():
            content = (asset_root / filename).read_text(encoding="utf-8")
            for label in labels:
                with self.subTest(filename=filename, label=label):
                    self.assertIn(label, content)

    def test_requirements_and_rtm_link_to_work_package(self) -> None:
        self.assertIn(ISSUE_URL, UI_REQUIREMENTS.read_text(encoding="utf-8"))
        rtm = TRACEABILITY_MATRIX.read_text(encoding="utf-8")
        self.assertGreaterEqual(rtm.count(ISSUE_URL), 5)


if __name__ == "__main__":
    unittest.main()
