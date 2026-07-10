from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEV = ROOT / "docs/design/04_Development/04A_Local_Dev_Environment.md"
TOPOLOGIES = ROOT / "docs/design/02_Architecture/02C_Deployment_Topologies.md"


def normalized_markdown(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"\\\s*\n\s*", " ", content)
    return re.sub(r"\s+", " ", content)


class ColimaBaselineDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.local_dev = normalized_markdown(LOCAL_DEV)
        cls.topologies = normalized_markdown(TOPOLOGIES)

    def test_lite_profile_is_the_accepted_linux_arm64_baseline(self) -> None:
        self.assertIn(
            "colima start --profile mac-studio-solo --vm-type=vz --arch aarch64 "
            "--cpu 16 --memory 96 --disk 400 --kubernetes --runtime docker",
            self.local_dev,
        )

    def test_resource_limits_are_explicit(self) -> None:
        self.assertIn("Minimum host reserve: 192GB", self.local_dev)
        self.assertIn("VM memory maximum: 320GB", self.local_dev)

    def test_rosetta_requires_wp_evidence_and_cloud_lab_is_non_default(self) -> None:
        self.assertIn("Work Package decision", self.local_dev)
        self.assertIn(
            "docs/design/10_Research/106_ARM64_Container_Image_Compatibility.md",
            self.local_dev,
        )
        self.assertIn("cloud-lab", self.topologies)
        self.assertIn("non-default", self.topologies)

    def test_lifecycle_and_recovery_commands_are_repeatable(self) -> None:
        for command in (
            "colima status --profile mac-studio-solo",
            "colima list --json",
            "colima stop --profile mac-studio-solo",
            "colima delete --profile mac-studio-solo -f",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.local_dev)


if __name__ == "__main__":
    unittest.main()
