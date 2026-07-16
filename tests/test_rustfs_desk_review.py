import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_PACKAGE = (
    ROOT
    / "docs/design/06_WorkPackages/L1/WP-L1-LAKE-004_RustFS_maturity_review.md"
)
LICENSE_NOTES = ROOT / "docs/design/10_Research/105_License_Notes.md"
ASSESSMENT = ROOT / "docs/design/10_Research/108_RustFS_Maturity_Assessment.md"
ARM64_MATRIX = (
    ROOT / "docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md"
)


class RustfsDeskReviewTests(unittest.TestCase):
    def test_materialized_review_covers_the_issue_contract(self) -> None:
        review = ASSESSMENT.read_text(encoding="utf-8")
        for field in (
            "License",
            "Governance",
            "Release cadence",
            "Maintainers",
            "Issue / PR activity",
            "Security policy",
            "Known vulnerabilities",
            "linux/arm64",
            "S3 compatibility",
            "Iceberg suitability",
            "Recommendation",
            "Owner",
            "Follow-up criteria",
        ):
            with self.subTest(field=field):
                self.assertRegex(review, rf"(?im)^#+\s+.*{re.escape(field)}")

        self.assertIn("2026-07-16", review)
        self.assertGreaterEqual(review.count("https://"), 12)
        self.assertIn("remain experimental", review)
        self.assertIn("High=0/Critical=0", review)

    def test_license_and_arm64_surfaces_record_the_same_fail_closed_decision(self) -> None:
        license_notes = LICENSE_NOTES.read_text(encoding="utf-8")
        matrix = ARM64_MATRIX.read_text(encoding="utf-8")
        self.assertIn("RustFS", license_notes)
        self.assertIn("remain experimental", license_notes)
        self.assertIn("RustFS", matrix)
        self.assertIn("linux/arm64", matrix)
        self.assertIn("remain experimental", matrix)

    def test_work_package_keeps_runtime_and_cluster_changes_out_of_scope(self) -> None:
        work_package = WORK_PACKAGE.read_text(encoding="utf-8")
        self.assertIn("Issue: [#34]", work_package)
        self.assertIn("Depends on: `#8`, `#32`, `#33`", work_package)
        self.assertIn("Execution order: `10 of 10`", work_package)
        self.assertIn("installing a RustFS image", work_package)
        self.assertIn("cluster mutation", work_package)


if __name__ == "__main__":
    unittest.main()
