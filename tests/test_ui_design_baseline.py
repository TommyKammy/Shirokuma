from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "docs/design/11_UI_UX"
MOCKUPS = UI_ROOT / "111_UI_Mockups_v0.2.2.md"
INTERACTION_MODEL = UI_ROOT / "113_Interaction_Model.md"
UI_REQUIREMENTS = ROOT / "docs/design/03_Requirements/038_UI_Requirements.md"
TRACEABILITY_MATRIX = ROOT / "docs/design/03_Requirements/037_Traceability_Matrix.md"
MOCKUP_ASSET_ROOT = ROOT / "docs/design/assets/ui_mockups"
ISSUE_URL = "https://github.com/TommyKammy/Shirokuma/issues/5"


class UIDesignBaselineTests(unittest.TestCase):
    def test_repository_mockup_references_resolve(self) -> None:
        markdown = MOCKUPS.read_text(encoding="utf-8")
        references = re.findall(r"!\[[^]]*\]\(([^)]+)\)", markdown)

        self.assertEqual(len(references), 5)
        for reference in references:
            with self.subTest(reference=reference):
                self.assertFalse(reference.startswith(("/", "~")))
                resolved_reference = (MOCKUPS.parent / reference).resolve()
                self.assertTrue(
                    resolved_reference.is_relative_to(MOCKUP_ASSET_ROOT.resolve()),
                    f"mockup asset escapes repository asset root: {reference}",
                )
                self.assertTrue(
                    resolved_reference.is_file(),
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
                "Core status",
                "k3s healthy",
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
                "DESCRIPTION",
                "AI EXPLANATION",
                "QUALITY",
                "Impact analysis",
                "LINEAGE",
            ),
        }

        for filename, labels in expected.items():
            content = (MOCKUP_ASSET_ROOT / filename).read_text(encoding="utf-8")
            for label in labels:
                with self.subTest(filename=filename, label=label):
                    self.assertIn(label, content)

    def test_warehouse_diff_uses_semantic_css_classes(self) -> None:
        warehouse = MOCKUP_ASSET_ROOT / "02_virtual_warehouse.svg"
        content = warehouse.read_text(encoding="utf-8")
        root = ET.fromstring(content)
        text_nodes = {
            (node.text or "").strip(): set(node.attrib.get("class", "").split())
            for node in root.findall("{http://www.w3.org/2000/svg}text")
        }

        self.assertIn(".diff-remove{fill:#fca5a5}", content)
        self.assertIn(".diff-add{fill:#86efac}", content)
        self.assertIn("diff-remove", text_nodes["- size: L"])
        self.assertIn("diff-add", text_nodes["+ size: M"])

    def test_mockups_show_policy_rule_and_git_provenance(self) -> None:
        def rendered_text(filename: str) -> set[str]:
            root = ET.parse(MOCKUP_ASSET_ROOT / filename).getroot()
            return {
                (node.text or "").strip()
                for node in root.findall("{http://www.w3.org/2000/svg}text")
            }

        warehouse_text = rendered_text("02_virtual_warehouse.svg")
        self.assertIn(
            "Rule: OPA · warehouse.change.standard-review",
            warehouse_text,
        )
        self.assertIn(
            "Affects analytics-prod only · no storage change",
            warehouse_text,
        )

        catalog_text = rendered_text("04_catalog_lineage.svg")
        self.assertIn("Git definition ↗", catalog_text)
        self.assertIn(
            "catalog/analytics/orders_daily.yaml @ main",
            catalog_text,
        )

    def test_mockups_link_authoritative_evidence_in_lifecycle_order(self) -> None:
        pawprints = ET.parse(
            MOCKUP_ASSET_ROOT / "03_pawprints_audit_timeline.svg"
        ).getroot()
        pawprint_text = {
            (node.text or "").strip(): float(node.attrib["y"])
            for node in pawprints.findall("{http://www.w3.org/2000/svg}text")
            if (node.text or "").strip()
        }
        lifecycle = (
            "PULL REQUEST OPEN",
            "CI PASSED",
            "POLICY ALLOWED",
            "AWAITING MERGE",
        )
        self.assertEqual(
            [pawprint_text[label] for label in lifecycle],
            sorted(pawprint_text[label] for label in lifecycle),
        )

        composite = ET.parse(
            MOCKUP_ASSET_ROOT
            / "shirokuma_ui_mockups_v0.2.2_business_composite.svg"
        ).getroot()
        composite_text = {
            (node.text or "").strip(): float(node.attrib["y"])
            for node in composite.iter("{http://www.w3.org/2000/svg}text")
            if (node.text or "").strip() and "y" in node.attrib
        }
        composite_lifecycle = (
            "PR #184 · opened",
            "CI passed · 18 checks",
            "Policy allowed · pol_718",
            "Awaiting merge approval",
        )
        self.assertEqual(
            [composite_text[label] for label in composite_lifecycle],
            sorted(composite_text[label] for label in composite_lifecycle),
        )

        for evidence_link in (
            "✓ Allowed · decision pol_718 ↗",
            "✓ Passed · CI run 184.18 ↗",
            "8,420 tokens · ¥12.40 · cost_441 ↗",
        ):
            self.assertIn(evidence_link, pawprint_text)

        catalog = ET.parse(MOCKUP_ASSET_ROOT / "04_catalog_lineage.svg").getroot()
        catalog_text = {
            (node.text or "").strip()
            for node in catalog.findall("{http://www.w3.org/2000/svg}text")
        }
        for object_link in (
            "Git definition ↗",
            "Latest PR / Issue ↗",
            "Runtime status ↗",
            "Pawprint ↗",
            "Policy decision ↗",
            "Impact analysis ↗",
        ):
            self.assertIn(object_link, catalog_text)

    def test_requirements_and_rtm_link_to_work_package(self) -> None:
        self.assertIn(ISSUE_URL, UI_REQUIREMENTS.read_text(encoding="utf-8"))
        rtm = TRACEABILITY_MATRIX.read_text(encoding="utf-8")
        self.assertGreaterEqual(rtm.count(ISSUE_URL), 5)


if __name__ == "__main__":
    unittest.main()
