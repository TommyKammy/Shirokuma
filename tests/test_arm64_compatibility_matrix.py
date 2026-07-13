import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md"

REQUIRED_COMPONENTS = {
    "Trino",
    "Apache Polaris",
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
        lines = MATRIX.read_text(encoding="utf-8").splitlines()
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

    def test_all_issue_25_components_have_complete_evidence_rows(self) -> None:
        self.assertEqual(REQUIRED_COMPONENTS, REQUIRED_COMPONENTS & self.rows.keys())
        ambiguous = re.compile(r"\b(?:unknown|verify|tbd|todo)\b", re.IGNORECASE)
        for component in sorted(REQUIRED_COMPONENTS):
            row = self.rows[component]
            with self.subTest(component=component):
                self.assertRegex(row["Upstream release"], r"\d")
                self.assertIn("linux/arm64", row["linux/arm64 evidence"])
                self.assertEqual("Apache-2.0", row["License"])
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


if __name__ == "__main__":
    unittest.main()
