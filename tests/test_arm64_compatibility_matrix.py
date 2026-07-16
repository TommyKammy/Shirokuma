import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md"
POLARIS_WORK_PACKAGE = (
    ROOT
    / "docs/design/06_WorkPackages/L1/WP-L1-LAKE-002_Polaris_catalog_bootstrap.md"
)

REQUIRED_COMPONENTS = {
    "Trino",
    "Apache Polaris",
    "PostgreSQL",
    "OpenMetadata",
    "SeaweedFS",
    "StarRocks",
    "Apache Doris",
    "ClickHouse",
    "Apache Gravitino",
    "Apache Amoro",
    "Trino Gateway",
    "Apache Spark",
    "Apache DataFusion Comet",
    "Apache Gluten",
}
CRITICAL_PATH = {
    "SeaweedFS",
    "Apache Polaris",
    "PostgreSQL",
    "Trino",
    "Apache Spark",
    "OpenMetadata",
}
EXPECTED_HEADER = [
    "Component",
    "Upstream release",
    "Image or build path",
    "linux/arm64 evidence",
    "License",
    "Signature / provenance",
    "v0.2 decision",
    "Fallback owner / risk / replacement",
    "Primary sources",
]


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


class Arm64CompatibilityMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix_text = MATRIX.read_text(encoding="utf-8")
        lines = cls.matrix_text.splitlines()
        header_index = next(
            index for index, line in enumerate(lines) if table_cells(line) == EXPECTED_HEADER
        )
        cls.rows = {}
        for line in lines[header_index + 2 :]:
            if not line.startswith("|"):
                break
            cells = table_cells(line)
            if len(cells) == len(EXPECTED_HEADER):
                cls.rows[cells[0]] = dict(zip(EXPECTED_HEADER, cells))

    def test_all_required_components_have_complete_evidence_rows(self) -> None:
        self.assertEqual(REQUIRED_COMPONENTS, REQUIRED_COMPONENTS & self.rows.keys())
        ambiguous = re.compile(r"\b(?:unknown|verify|tbd|todo)\b", re.IGNORECASE)
        for component in sorted(REQUIRED_COMPONENTS):
            row = self.rows[component]
            with self.subTest(component=component):
                self.assertRegex(row["Upstream release"], r"\d")
                self.assertIn("linux/arm64", row["linux/arm64 evidence"])
                expected_license = "PostgreSQL" if component == "PostgreSQL" else "Apache-2.0"
                self.assertEqual(expected_license, row["License"])
                self.assertNotRegex(row["Signature / provenance"], ambiguous)
                self.assertRegex(row["v0.2 decision"], r"^(?:mainline|fallback|scope-out)\b")
                self.assertGreaterEqual(row["Primary sources"].count("https://"), 2)

    def test_critical_path_is_explicitly_mainline(self) -> None:
        for component in sorted(CRITICAL_PATH):
            with self.subTest(component=component):
                self.assertTrue(self.rows[component]["v0.2 decision"].startswith("mainline"))

    def test_acceleration_order_is_fail_closed(self) -> None:
        self.assertTrue(
            self.rows["Apache DataFusion Comet"]["v0.2 decision"].startswith("mainline")
        )
        self.assertIn("first-line", self.rows["Apache DataFusion Comet"]["v0.2 decision"])
        self.assertTrue(self.rows["Apache Gluten"]["v0.2 decision"].startswith("scope-out"))
        self.assertIn("bonus-only", self.rows["Apache Gluten"]["v0.2 decision"])

    def test_fallbacks_record_owner_risk_and_replacement(self) -> None:
        for component in sorted(REQUIRED_COMPONENTS):
            fallback = self.rows[component]["Fallback owner / risk / replacement"]
            with self.subTest(component=component):
                self.assertIn("Owner:", fallback)
                self.assertIn("Risk:", fallback)
                self.assertIn("Replace:", fallback)

    def test_polaris_source_build_decision_remains_fail_closed(self) -> None:
        row = self.rows["Apache Polaris"]
        self.assertIn("upstream", row["Image or build path"])
        self.assertIn("rejected", row["Image or build path"])
        self.assertIn("repository build", row["Image or build path"])
        self.assertIn("runtime remains blocked", row["v0.2 decision"])
        self.assertIn("main publication", row["Fallback owner / risk / replacement"])

    def test_postgresql_candidate_evidence_remains_fail_closed(self) -> None:
        row = self.rows["PostgreSQL"]
        self.assertIn("@sha256:", row["Image or build path"])
        self.assertIn(
            "sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8",
            row["linux/arm64 evidence"],
        )
        self.assertIn("Cosign", row["Signature / provenance"])
        self.assertIn("High=0", row["Signature / provenance"])
        self.assertIn("Critical=0", row["Signature / provenance"])
        self.assertIn("runtime remains blocked", row["v0.2 decision"])

    def test_postgresql_follow_up_inventory_and_owner_remain_explicit(self) -> None:
        registry_section = self.matrix_text.split(
            "### Registry inspection method", 1
        )[1].split("### Focused image-smoke follow-up", 1)[0]
        self.assertIn("crane digest", registry_section)
        self.assertIn("crane manifest", registry_section)
        self.assertIn("Chainguard PostgreSQL 18.4", registry_section)
        self.assertIn("2026-07-16", registry_section)
        self.assertIn(
            "sha256:3dc629a917612f1630c6f8e7a17f23a42cbd5917b9b3080972b70b1583daff34",
            registry_section,
        )
        self.assertIn(
            "sha256:c455ec159d05d99ee031d471b8692668562fed8e8c9c37be5e0dbdbee8e5f7b8",
            registry_section,
        )
        follow_up_section = self.matrix_text.split(
            "### Focused image-smoke follow-up", 1
        )[1].split("### Unchanged later-scope rows", 1)[0]
        self.assertIn(
            "WP-L1-LAKE-002 for\nPolaris and PostgreSQL", follow_up_section
        )

    def test_polaris_work_package_keeps_storage_prerequisites_explicit(self) -> None:
        work_package = POLARIS_WORK_PACKAGE.read_text(encoding="utf-8")
        self.assertIn(
            "docs/design/06_WorkPackages/L1/WP-L1-LAKE-001_Object_storage_profile.md",
            work_package,
        )
        self.assertIn("metadata-storage host SSD impact", work_package)


if __name__ == "__main__":
    unittest.main()
