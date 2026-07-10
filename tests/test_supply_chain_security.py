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
    @staticmethod
    def valid_image(reference: str | None = None) -> dict[str, str]:
        return {
            "component": "fixture",
            "reference": reference
            or "registry.example.invalid/fixture@sha256:" + "a" * 64,
            "platform": "linux/arm64",
            "version": "1.0.0",
            "source": "https://example.invalid/fixture",
            "sbom_artifact": "fixture.cdx.json",
            "scan_artifact": "fixture.trivy.json",
            "scanner_version": "0.72.0",
            "vulnerability_db_updated_at": "2026-07-01T00:00:00Z",
        }

    def run_checker(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def write_valid_sbom(root: Path) -> None:
        (root / "fixture.cdx.json").write_text(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.5",
                    "version": 1,
                    "components": [],
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def write_trivy_report(
        root: Path,
        reference: str,
        vulnerabilities: list[dict[str, str]] | None = None,
    ) -> None:
        (root / "fixture.trivy.json").write_text(
            json.dumps(
                {
                    "ArtifactName": reference,
                    "Metadata": {"RepoDigests": [reference]},
                    "Results": [
                        {
                            "Target": reference,
                            "Vulnerabilities": vulnerabilities or [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
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

    def test_secret_scan_reads_symlink_blob_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            token = "AKIA" + "1234567890ABCDEF"
            external = root / "external.txt"
            external.write_text(f"cloud_access_key={token}\n", encoding="utf-8")
            (repository / "external-link").symlink_to(external)
            subprocess.run(
                ["git", "-C", str(repository), "add", "external-link"], check=True
            )

            result = self.run_checker("scan-secrets", "--repo", str(repository))

        self.assertEqual(result.returncode, 0, result.stderr)
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

    def test_trivy_report_without_results_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "trivy.json"
            report.write_text("{}\n", encoding="utf-8")

            result = self.run_checker("check-trivy", "--report", str(report))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing Results", result.stderr)

    def test_empty_malformed_trivy_sections_are_rejected(self) -> None:
        for category in ("Vulnerabilities", "Misconfigurations", "Secrets"):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "trivy.json"
                report.write_text(
                    json.dumps({"Results": [{category: {}}]}), encoding="utf-8"
                )

                result = self.run_checker("check-trivy", "--report", str(report))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"{category} must be a list", result.stderr)

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

    def test_tag_qualified_resident_image_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "resident-images.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "images": [
                            self.valid_image(
                                "registry.example.invalid/fixture:latest@sha256:"
                                + "a" * 64
                            )
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact repository@sha256", result.stderr)

    def test_resident_image_without_scan_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "resident-images.json"
            image = self.valid_image()
            del image["scan_artifact"]
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing scan_artifact", result.stderr)

    def test_symlinked_resident_image_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "external-policy.json"
            target.write_text(
                json.dumps({"schema_version": 1, "images": []}), encoding="utf-8"
            )
            manifest = root / "resident-images.json"
            manifest.symlink_to(target)

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link", result.stderr)

    def test_blocking_resident_image_scan_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "resident-images.json"
            image = self.valid_image()
            self.write_valid_sbom(root)
            self.write_trivy_report(
                root,
                image["reference"],
                [
                    {
                        "VulnerabilityID": "CVE-2099-0003",
                        "Severity": "HIGH",
                    }
                ],
            )
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HIGH=1", result.stderr)

    def test_missing_resident_image_scan_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_sbom(root)
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [self.valid_image()]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot read valid JSON", result.stderr)

    def test_symlinked_resident_image_scan_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_valid_sbom(root)
            target = root / "external.trivy.json"
            target.write_text(json.dumps({"Results": []}), encoding="utf-8")
            (root / "fixture.trivy.json").symlink_to(target)
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [self.valid_image()]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link scan_artifact", result.stderr)

    def test_low_resident_image_scan_artifact_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            self.write_valid_sbom(root)
            self.write_trivy_report(
                root,
                image["reference"],
                [
                    {
                        "VulnerabilityID": "CVE-2099-0004",
                        "Severity": "LOW",
                    }
                ],
            )
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_resident_image_sbom_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            self.write_trivy_report(root, image["reference"])
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid sbom_artifact", result.stderr)

    def test_parent_traversal_resident_image_sbom_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_root = root / "policy"
            policy_root.mkdir()
            self.write_valid_sbom(root)
            image = self.valid_image()
            image["sbom_artifact"] = "../fixture.cdx.json"
            self.write_trivy_report(policy_root, image["reference"])
            manifest = policy_root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sbom_artifact must be relative", result.stderr)

    def test_symlinked_resident_image_sbom_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "external.cdx.json"
            external.write_text(
                json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8"
            )
            (root / "fixture.cdx.json").symlink_to(external)
            image = self.valid_image()
            self.write_trivy_report(root, image["reference"])
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link sbom_artifact", result.stderr)

    def test_scan_artifact_for_different_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            other_reference = "registry.example.invalid/other@sha256:" + "b" * 64
            self.write_valid_sbom(root)
            self.write_trivy_report(root, other_reference)
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match ledger reference", result.stderr)

    def test_scan_repo_digest_overrides_conflicting_artifact_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            other_reference = "registry.example.invalid/other@sha256:" + "b" * 64
            self.write_valid_sbom(root)
            self.write_trivy_report(root, other_reference)
            report_path = root / "fixture.trivy.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["ArtifactName"] = image["reference"]
            report_path.write_text(json.dumps(report), encoding="utf-8")
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match ledger reference", result.stderr)

    def test_null_resident_image_evidence_is_rejected(self) -> None:
        for field in (
            "version",
            "source",
            "sbom_artifact",
            "scan_artifact",
            "scanner_version",
            "vulnerability_db_updated_at",
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / "resident-images.json"
                image = self.valid_image()
                image[field] = None
                manifest.write_text(
                    json.dumps({"schema_version": 1, "images": [image]}),
                    encoding="utf-8",
                )

                result = self.run_checker("check-images", "--manifest", str(manifest))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"missing {field}", result.stderr)

    def test_future_vulnerability_database_timestamp_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "resident-images.json"
            image = self.valid_image()
            image["vulnerability_db_updated_at"] = "2099-01-01T00:00:00Z"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be in the future", result.stderr)

    def test_fallback_image_expiry_must_be_a_future_iso_date(self) -> None:
        for expires_on in ("never", "2000-01-01"):
            with self.subTest(expires_on=expires_on), tempfile.TemporaryDirectory() as directory:
                manifest = Path(directory) / "resident-images.json"
                image = self.valid_image()
                image.update(
                    {
                        "fallback": True,
                        "cve_risk": "Accepted for a bounded compatibility experiment.",
                        "replacement_plan": "Replace with the mainline image.",
                        "expires_on": expires_on,
                    }
                )
                manifest.write_text(
                    json.dumps({"schema_version": 1, "images": [image]}),
                    encoding="utf-8",
                )

                result = self.run_checker("check-images", "--manifest", str(manifest))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("future YYYY-MM-DD date", result.stderr)

    def test_minio_image_must_be_an_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "resident-images.json"
            image = self.valid_image(
                "quay.io/minio/minio@sha256:" + "c" * 64
            )
            image["component"] = "MinIO"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MinIO entries require fallback: true", result.stderr)

    def test_unlisted_resident_deployment_image_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            deployment = repository / "deploy" / "fixture.yaml"
            deployment.parent.mkdir()
            reference = "registry.example.invalid/fixture@sha256:" + "b" * 64
            deployment.write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: fixture\n"
                f"          image: {reference}\n",
                encoding="utf-8",
            )
            manifest = repository / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": []}), encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "deploy/fixture.yaml"], check=True
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(repository),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deploy/fixture.yaml", result.stderr)
        self.assertIn("missing from resident image ledger", result.stderr)

    def test_inline_yaml_image_field_is_rejected_instead_of_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            deployment = repository / "deploy" / "fixture.yaml"
            deployment.parent.mkdir()
            deployment.write_text(
                "containers: [{name: app, image: registry.example.invalid/app:latest}]\n",
                encoding="utf-8",
            )
            manifest = repository / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": []}), encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "deploy/fixture.yaml"], check=True
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(repository),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inline flow-style image fields are unsupported", result.stderr)

    def test_quoted_yaml_image_key_is_not_ignored(self) -> None:
        for key in ('"image"', "'image'"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                subprocess.run(["git", "init", "-q", str(repository)], check=True)
                deployment = repository / "deploy" / "fixture.yaml"
                deployment.parent.mkdir()
                deployment.write_text(
                    f"{key}: registry.example.invalid/app:latest\n",
                    encoding="utf-8",
                )
                manifest = repository / "resident-images.json"
                manifest.write_text(
                    json.dumps({"schema_version": 1, "images": []}), encoding="utf-8"
                )
                subprocess.run(
                    ["git", "-C", str(repository), "add", "deploy/fixture.yaml"],
                    check=True,
                )

                result = self.run_checker(
                    "check-images",
                    "--manifest",
                    str(manifest),
                    "--repo",
                    str(repository),
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing from resident image ledger", result.stderr)

    def test_unlisted_resident_helm_image_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            deployment = repository / "charts" / "fixture" / "templates" / "deployment.yaml"
            deployment.parent.mkdir(parents=True)
            reference = "registry.example.invalid/fixture@sha256:" + "d" * 64
            deployment.write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "spec:\n"
                "  template:\n"
                "    spec:\n"
                "      containers:\n"
                "        - name: fixture\n"
                f"          image: {reference}\n",
                encoding="utf-8",
            )
            manifest = repository / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": []}), encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "charts/fixture/templates/deployment.yaml"],
                check=True,
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(repository),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("charts/fixture/templates/deployment.yaml", result.stderr)
        self.assertIn("missing from resident image ledger", result.stderr)

    def test_committed_policy_and_ci_define_the_blocking_gate(self) -> None:
        result = self.run_checker("check-images", "--manifest", str(POLICY))
        self.assertEqual(result.returncode, 0, result.stderr)

        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6",
            "persist-credentials: false",
            "gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e # v3",
            "aquasecurity/trivy-action@",
            "anchore/sbom-action@",
            "severity: HIGH,CRITICAL",
            "severity: UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL",
            "exit-code: 1",
            "if: ${{ !cancelled() }}",
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
