from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/verify_supply_chain.py"
POLICY = ROOT / "security/resident-images.json"
WORKFLOW = ROOT / ".github/workflows/security.yml"
SECURITY_DOC = ROOT / "docs/design/04_Development/049_Supply_Chain_Security.md"


class SupplyChainSecurityTests(unittest.TestCase):
    def run_checker(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_secret_like_tracked_content_is_rejected_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            token = "AKIA" + "1234567890ABCDEF"
            (repository / "settings.txt").write_text(
                f"cloud_access_key={token}\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "settings.txt"], check=True
            )

            result = self.run_checker("scan-secrets", "--repo", str(repository))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret-like content", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_high_or_critical_trivy_findings_are_rejected(self) -> None:
        for severity in ("HIGH", "CRITICAL"):
            with self.subTest(severity=severity), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "trivy.json"
                report.write_text(
                    json.dumps(
                        {
                            "Results": [
                                {
                                    "Target": "fixture",
                                    "Vulnerabilities": [
                                        {"VulnerabilityID": "CVE-2099-0001", "Severity": severity}
                                    ],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                result = self.run_checker("check-trivy", "--report", str(report))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(severity, result.stderr)

    def test_low_trivy_findings_do_not_cross_the_blocking_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "trivy.json"
            report.write_text(
                json.dumps(
                    {
                        "Results": [
                            {
                                "Target": "fixture",
                                "Vulnerabilities": [
                                    {"VulnerabilityID": "CVE-2099-0002", "Severity": "LOW"}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_checker("check-trivy", "--report", str(report))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_resident_image_without_digest_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "resident-images.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "images": [
                            {
                                "component": "fixture",
                                "reference": "registry.example.invalid/fixture:latest",
                                "platform": "linux/arm64",
                                "sbom_artifact": "fixture.cdx.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sha256 digest", result.stderr)

    def test_committed_policy_and_ci_define_the_blocking_gate(self) -> None:
        result = self.run_checker("check-images", "--manifest", str(POLICY))
        self.assertEqual(result.returncode, 0, result.stderr)

        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "gitleaks/gitleaks-action@",
            "aquasecurity/trivy-action@",
            "anchore/sbom-action@",
            "severity: HIGH,CRITICAL",
            "exit-code: 1",
            "upload-artifact-retention: 30",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)

        documentation = SECURITY_DOC.read_text(encoding="utf-8")
        for required in (
            "High or Critical",
            "30 days",
            "CVE risk",
            "replacement plan",
            "fail closed",
        ):
            with self.subTest(required=required):
                self.assertIn(required, documentation)


if __name__ == "__main__":
    unittest.main()
