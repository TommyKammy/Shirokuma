from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.verify_supply_chain import deployed_image_references


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/verify_supply_chain.py"
POLICY = ROOT / "security/resident-images.json"
EXCEPTIONS = ROOT / "security/resident-image-exceptions.json"
WORKFLOW = ROOT / ".github/workflows/security.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
SECURITY_DOC = ROOT / "docs/design/04_Development/049_Supply_Chain_Security.md"
LAB_ADR = "docs/design/07_ADR/ADR-0019_Allow_time_boxed_resident_image_exceptions_for_local_lab.md"


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
            "supply_chain_artifact": "fixture.supply-chain.json",
            "sbom_generator": "syft 1.46.0",
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
    def write_valid_supply_chain(root: Path, image: dict[str, str]) -> None:
        digest = image["reference"].rsplit("@", 1)[1]
        repository = image["reference"].rsplit("@", 1)[0]
        (root / "fixture.supply-chain.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "images": [
                        {
                            "component": image["component"],
                            "version": image["version"],
                            "source": image["source"],
                            "platform": "linux/arm64",
                            "reference": image["reference"],
                            "verified_at": "2026-07-01T00:00:00Z",
                            "signature": {
                                "verified": True,
                                "signed_index": repository + "@sha256:" + "b" * 64,
                                "arm64_in_signed_index": True,
                                "arm64_manifest_digest": digest,
                                "issuer": "https://token.actions.githubusercontent.com",
                                "identity": "https://github.com/example/release.yml@refs/tags/v1",
                                "workflow_repository": "example/fixture",
                                "workflow_ref": "refs/tags/v1.0.0",
                                "commit": "c" * 40,
                                "transparency_log_index": 1,
                            },
                            "provenance": {
                                "predicate_type": "https://slsa.dev/provenance/v1",
                                "subject_digest": digest,
                                "source": image["source"],
                                "version": image["version"],
                                "revision": "c" * 40,
                                "builder": "https://example.invalid/actions/runs/1",
                                "attestation_manifest": "sha256:" + "d" * 64,
                            },
                            "upstream_sbom": {
                                "predicate_type": "https://spdx.dev/Document",
                                "subject_digest": digest,
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def write_valid_evidence(cls, root: Path, image: dict[str, str]) -> None:
        cls.write_valid_sbom(root)
        cls.write_valid_supply_chain(root, image)

    @staticmethod
    def write_repository_source_build_fixture(root: Path) -> Path:
        image = next(
            image
            for image in json.loads(POLICY.read_text(encoding="utf-8"))["images"]
            if image["component"] == "seaweedfs"
        )
        manifest = root / "security/resident-images.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"schema_version": 1, "images": [image]}),
            encoding="utf-8",
        )
        paths = (
            "bootstrap/seaweedfs/v4.39/admission.json",
            "bootstrap/seaweedfs/v4.39/release-evidence.json",
            "bootstrap/seaweedfs/v4.39/evidence/seaweedfs-4.39-arm64.cdx.json",
            "bootstrap/seaweedfs/v4.39/evidence/trivy.json",
            "security/evidence/seaweedfs-v4.39/seaweedfs-4.39-arm64.cdx.json",
            "security/evidence/seaweedfs-v4.39/trivy.json",
            "security/evidence/seaweedfs-v4.39/supply-chain.json",
        )
        for relative in paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        return manifest.resolve()

    @staticmethod
    def valid_exception(
        image: dict[str, str],
        cves: list[dict[str, str]],
    ) -> dict[str, object]:
        today = date.today()
        return {
            "component": image["component"],
            "reference": image["reference"],
            "scope": "mac-studio-solo/local-lab",
            "max_severity": "HIGH",
            "decision_record": LAB_ADR,
            "approved_on": today.isoformat(),
            "expires_on": (today + timedelta(days=30)).isoformat(),
            "risk_acceptance": "Bounded development-only acceptance.",
            "compensating_controls": [
                "local lab only",
                "trusted sources only",
                "no public exposure",
            ],
            "replacement_plan": "Replace with a clean upstream image.",
            "cves": cves,
        }

    @staticmethod
    def write_exceptions(root: Path, entries: list[dict[str, object]]) -> Path:
        path = root / "resident-image-exceptions.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile": "local-lab",
                    "exceptions": entries,
                }
            ),
            encoding="utf-8",
        )
        return path

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
            root = Path(directory).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            image = self.valid_image()
            self.write_valid_evidence(root, image)
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

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_lab_exception_accepts_one_exact_high_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            image = self.valid_image()
            finding = {
                "VulnerabilityID": "CVE-2099-1001",
                "Severity": "HIGH",
                "PkgName": "example.invalid/module",
                "InstalledVersion": "v1.0.0",
            }
            exception_cve = {
                "id": "CVE-2099-1001",
                "severity": "HIGH",
                "package": "example.invalid/module",
                "installed_version": "v1.0.0",
                "fixed_version": "",
            }
            self.write_valid_evidence(root, image)
            self.write_trivy_report(root, image["reference"], [finding])
            exceptions = self.write_exceptions(
                root,
                [self.valid_exception(image, [exception_cve])],
            )
            decision_record = root / LAB_ADR
            decision_record.parent.mkdir(parents=True)
            decision_record.write_text("# Fixture decision record\n", encoding="utf-8")
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
                "--profile",
                "local-lab",
                "--exceptions",
                str(exceptions),
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_lab_exception_rejects_unapproved_high_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            approved_finding = {
                "VulnerabilityID": "CVE-2099-1001",
                "Severity": "HIGH",
                "PkgName": "example.invalid/module",
                "InstalledVersion": "v1.0.0",
            }
            new_finding = {
                "VulnerabilityID": "CVE-2099-1002",
                "Severity": "HIGH",
                "PkgName": "example.invalid/other",
                "InstalledVersion": "v2.0.0",
            }
            exception_cve = {
                "id": "CVE-2099-1001",
                "severity": "HIGH",
                "package": "example.invalid/module",
                "installed_version": "v1.0.0",
                "fixed_version": "",
            }
            self.write_valid_evidence(root, image)
            self.write_trivy_report(
                root,
                image["reference"],
                [approved_finding, new_finding],
            )
            exceptions = self.write_exceptions(
                root,
                [self.valid_exception(image, [exception_cve])],
            )
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--profile",
                "local-lab",
                "--exceptions",
                str(exceptions),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unapproved=CVE-2099-1002", result.stderr)

    def test_local_lab_exception_never_allows_critical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            finding = {
                "VulnerabilityID": "CVE-2099-1001",
                "Severity": "CRITICAL",
                "PkgName": "example.invalid/module",
                "InstalledVersion": "v1.0.0",
            }
            exception_cve = {
                "id": "CVE-2099-1001",
                "severity": "HIGH",
                "package": "example.invalid/module",
                "installed_version": "v1.0.0",
                "fixed_version": "",
            }
            self.write_valid_evidence(root, image)
            self.write_trivy_report(root, image["reference"], [finding])
            exceptions = self.write_exceptions(
                root,
                [self.valid_exception(image, [exception_cve])],
            )
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--profile",
                "local-lab",
                "--exceptions",
                str(exceptions),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CRITICAL=1", result.stderr)

    def test_local_lab_exception_rejects_stale_cve_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            exception_cve = {
                "id": "CVE-2099-1001",
                "severity": "HIGH",
                "package": "example.invalid/module",
                "installed_version": "v1.0.0",
                "fixed_version": "1.0.1",
            }
            self.write_valid_evidence(root, image)
            self.write_trivy_report(root, image["reference"])
            exceptions = self.write_exceptions(
                root,
                [self.valid_exception(image, [exception_cve])],
            )
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--profile",
                "local-lab",
                "--exceptions",
                str(exceptions),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale=CVE-2099-1001", result.stderr)

    def test_local_lab_exception_rejects_expired_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            exception_cve = {
                "id": "CVE-2099-1001",
                "severity": "HIGH",
                "package": "example.invalid/module",
                "installed_version": "v1.0.0",
                "fixed_version": "",
            }
            entry = self.valid_exception(image, [exception_cve])
            entry["approved_on"] = (date.today() - timedelta(days=31)).isoformat()
            entry["expires_on"] = (date.today() - timedelta(days=1)).isoformat()
            self.write_valid_evidence(root, image)
            self.write_trivy_report(root, image["reference"])
            exceptions = self.write_exceptions(root, [entry])
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--profile",
                "local-lab",
                "--exceptions",
                str(exceptions),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has expired", result.stderr)

    def test_strict_profile_rejects_exception_input(self) -> None:
        result = self.run_checker(
            "check-images",
            "--manifest",
            str(POLICY),
            "--exceptions",
            str(EXCEPTIONS),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strict resident image profile does not allow exceptions", result.stderr)

    def test_committed_local_lab_images_remain_blocked_by_strict_profile(self) -> None:
        result = self.run_checker("check-images", "--manifest", str(POLICY))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Trivy blocking threshold crossed: HIGH=2", result.stderr)

    def test_supply_chain_subject_must_match_ledger_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            self.write_valid_evidence(root, image)
            evidence_path = root / "fixture.supply-chain.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["images"][0]["provenance"]["subject_digest"] = "sha256:" + "f" * 64
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            self.write_trivy_report(root, image["reference"])
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provenance subject does not match", result.stderr)

    def test_signed_index_repository_must_match_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self.valid_image()
            self.write_valid_evidence(root, image)
            evidence_path = root / "fixture.supply-chain.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["images"][0]["signature"]["signed_index"] = (
                "registry.example.invalid/other@sha256:" + "b" * 64
            )
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            self.write_trivy_report(root, image["reference"])
            manifest = root / "resident-images.json"
            manifest.write_text(
                json.dumps({"schema_version": 1, "images": [image]}),
                encoding="utf-8",
            )

            result = self.run_checker("check-images", "--manifest", str(manifest))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("signed index repository does not match", result.stderr)

    def test_repository_source_build_evidence_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_repository_source_build_fixture(root)

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repository_source_build_admission_path_must_be_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_repository_source_build_fixture(root)
            original = root / "bootstrap/seaweedfs/v4.39/admission.json"
            substitute = root / "bootstrap/seaweedfs/v4.39/admission-copy.json"
            shutil.copy2(original, substitute)
            evidence_path = root / "security/evidence/seaweedfs-v4.39/supply-chain.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["images"][0]["repository_source_build"]["admission"]["path"] = (
                "bootstrap/seaweedfs/v4.39/admission-copy.json"
            )
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("admission path is not canonical", result.stderr)

    def test_repository_source_build_release_artifacts_must_be_canonical(self) -> None:
        cases = (
            (
                ("release_evidence",),
                "bootstrap/seaweedfs/v4.39/release-evidence.json",
                "bootstrap/seaweedfs/v4.39/release-evidence-copy.json",
                "release evidence path is not canonical",
            ),
            (
                ("resident_evidence", "sbom", "source"),
                "bootstrap/seaweedfs/v4.39/evidence/seaweedfs-4.39-arm64.cdx.json",
                "bootstrap/seaweedfs/v4.39/evidence/sbom-copy.json",
                "sbom source path is not canonical",
            ),
            (
                ("resident_evidence", "scan", "source"),
                "bootstrap/seaweedfs/v4.39/evidence/trivy.json",
                "bootstrap/seaweedfs/v4.39/evidence/trivy-copy.json",
                "scan source path is not canonical",
            ),
        )
        for keys, original, substitute, expected in cases:
            with self.subTest(binding=".".join(keys)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = self.write_repository_source_build_fixture(root)
                shutil.copy2(root / original, root / substitute)
                evidence_path = root / "security/evidence/seaweedfs-v4.39/supply-chain.json"
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                binding = evidence["images"][0]["repository_source_build"]
                for key in keys:
                    binding = binding[key]
                binding["path"] = substitute
                evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

                result = self.run_checker(
                    "check-images",
                    "--manifest",
                    str(manifest),
                    "--repo",
                    str(root),
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)

    def test_repository_source_build_parent_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_repository_source_build_fixture(root)
            evidence_path = root / "security/evidence/seaweedfs-v4.39/supply-chain.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["images"][0]["repository_source_build"]["admission"]["path"] = (
                "../admission.json"
            )
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a repository-relative path", result.stderr)

    def test_repository_source_build_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_repository_source_build_fixture(root)
            admission_path = root / "bootstrap/seaweedfs/v4.39/admission.json"
            target = root / "external-admission.json"
            shutil.copy2(admission_path, target)
            admission_path.unlink()
            admission_path.symlink_to(target)

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link repository_source_build.admission.path", result.stderr)

    def test_symlinked_manifest_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository_source_build_fixture(root)
            alias = root / "security-alias"
            alias.symlink_to(root / "security", target_is_directory=True)
            manifest = alias / "resident-images.json"

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link ancestor", result.stderr)

    def test_symlinked_manifest_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_repository_source_build_fixture(root)
            alias = root / "repository-alias"
            alias.symlink_to(root, target_is_directory=True)
            manifest = alias / "security/resident-images.json"

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link ancestor", result.stderr)

    def test_verify_security_runs_canonical_trusted_image_audit(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        verify_security = makefile.split(
            "verify-security: verify-cosign\n",
            1,
        )[1].split("\n\n", 1)[0]
        self.assertIn("verify-security: verify-cosign", makefile)
        self.assertIn(
            "scripts/verify_trusted_image.py audit --root .",
            verify_security,
        )

    def test_repository_source_build_resident_bytes_are_bound_to_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_repository_source_build_fixture(root)
            resident_sbom = (
                root
                / "security/evidence/seaweedfs-v4.39/seaweedfs-4.39-arm64.cdx.json"
            )
            resident_sbom.write_bytes(resident_sbom.read_bytes() + b"\n")
            evidence_path = root / "security/evidence/seaweedfs-v4.39/supply-chain.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            resident_binding = evidence["images"][0]["repository_source_build"][
                "resident_evidence"
            ]["sbom"]["resident"]
            resident_binding["sha256"] = hashlib.sha256(resident_sbom.read_bytes()).hexdigest()
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository source-build sbom hashes do not match", result.stderr)

    def test_repository_source_build_source_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_repository_source_build_fixture(root)
            source_scan = root / "bootstrap/seaweedfs/v4.39/evidence/trivy.json"
            source_scan.write_bytes(source_scan.read_bytes() + b"\n")

            result = self.run_checker(
                "check-images",
                "--manifest",
                str(manifest),
                "--repo",
                str(root),
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "repository_source_build.resident_evidence.scan.source hash mismatch",
            result.stderr,
        )

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
            "supply_chain_artifact",
            "sbom_generator",
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

    def test_generated_flux_images_are_effectively_resolved_only_at_canonical_path(self) -> None:
        required = (
            "opentofu/dev/bootstrap-images.json",
            "bootstrap/flux/v2.9.2/components.json",
            "security/resident-images.json",
            "deploy/gitops/clusters/local-lite/flux-system/kustomization.yaml",
            "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml",
            "deploy/gitops/clusters/local-lite/flux-system/gotk-sync.yaml",
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            for relative in required:
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            subprocess.run(
                ["git", "-C", str(repository), "add", *required], check=True
            )

            references = deployed_image_references(repository)
            candidates = json.loads(
                (repository / "opentofu/dev/bootstrap-images.json").read_text(
                    encoding="utf-8"
                )
            )
            expected = {candidate["reference"] for candidate in candidates.values()}
            self.assertEqual({reference for _, reference in references}, expected)
            self.assertEqual(
                {path for path, _ in references},
                {
                    "deploy/gitops/clusters/local-lite/flux-system/"
                    "gotk-components.yaml"
                },
            )

            canonical = repository / required[-2]
            noncanonical = canonical.with_name("copied-gotk-components.yaml")
            canonical.rename(noncanonical)
            subprocess.run(
                ["git", "-C", str(repository), "add", "-A"], check=True
            )
            untrusted = deployed_image_references(repository)

        self.assertEqual(len(untrusted), 4)
        self.assertTrue(all("@sha256:" not in reference for _, reference in untrusted))
        self.assertEqual(
            {path for path, _ in untrusted},
            {
                "deploy/gitops/clusters/local-lite/flux-system/"
                "copied-gotk-components.yaml"
            },
        )

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
        result = self.run_checker(
            "check-images",
            "--manifest",
            str(POLICY),
            "--profile",
            "local-lab",
            "--exceptions",
            str(EXCEPTIONS),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6",
            "persist-credentials: false",
            "sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2",
            "cosign-release: v3.1.1",
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
            "local-lab",
            "Critical",
            "30 days",
        ):
            with self.subTest(required=required):
                self.assertIn(required, documentation)

    def test_retained_evidence_policy_exceptions_are_exact_path_only(self) -> None:
        config = GITLEAKS_CONFIG.read_text(encoding="utf-8")

        def allowlist_paths(
            description: str,
            secret_pattern: str,
            target_rule: str = "sourcegraph-access-token",
        ) -> set[str]:
            start = config.index(f'description = "{description}"')
            end = config.find("[[allowlists]]", start)
            block = config[start:] if end == -1 else config[start:end]
            self.assertIn('condition = "AND"', block)
            self.assertIn('regexTarget = "secret"', block)
            self.assertIn(f"regexes = ['''{secret_pattern}''']", block)
            self.assertIn(f'targetRules = ["{target_rule}"]', block)
            paths = block.split("paths = [", 1)[1].split("]", 1)[0]
            return {
                line.strip().rstrip(",").strip("'")
                for line in paths.splitlines()
                if line.strip()
            }

        self.assertEqual(
            allowlist_paths(
                "Public SHA-1 package hashes in retained SeaweedFS 4.39 CycloneDX evidence",
                r"^[0-9a-f]{40}$",
            ),
            {
                r"^bootstrap/seaweedfs/v4\.39/evidence/seaweedfs-4\.39-arm64\.cdx\.json$",
                r"^bootstrap/seaweedfs/v4\.39/evidence/trivy\.json$",
                r"^security/evidence/seaweedfs-v4\.39/seaweedfs-4\.39-arm64\.cdx\.json$",
            },
        )
        self.assertEqual(
            allowlist_paths(
                "Public Sigstore bundle material retained for SeaweedFS 4.39 attestation verification",
                r"^(db42bb49757b459551607939807017d7a9d5a94a|311b400a3baa667ba1727949a95ae0d2e70d41d2)$",
            ),
            {
                r"^bootstrap/seaweedfs/v4\.39/evidence/sbom-attestation-bundle\.json$",
                r"^bootstrap/seaweedfs/v4\.39/evidence/trivy-attestation-bundle\.json$",
            },
        )
        self.assertEqual(
            allowlist_paths(
                "Public source and checksum hashes in the retained SeaweedFS Go module manifest",
                r"^db42bb49757b459551607939807017d7a9d5a94a$",
            ),
            {
                r"^bootstrap/seaweedfs/v4\.39/go-module-inputs\.json$",
                r"^bootstrap/seaweedfs/v4\.39/evidence/go-module-inputs\.json$",
            },
        )
        self.assertEqual(
            allowlist_paths(
                "SeaweedFS S3 secret variable references in OpenTofu and their contract assertions",
                r"^(var\.seaweedfs_s3_operator_secret_key|var\.seaweedfs_s3_application_secret_key)$",
                "generic-api-key",
            ),
            {
                r"^opentofu/dev/object-storage\.tf$",
                r"^tests/test_object_storage_profile\.py$",
            },
        )
        self.assertEqual(
            allowlist_paths(
                "Public immutable GitHub Action commit pins asserted by supply-chain tests",
                r"^(df4cb1c069e1874edd31b4311f1884172cec0e10|e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e|6f9f17788090df1f26f669e9d70d6ae9567deba6)$",
            ),
            {r"^tests/test_supply_chain_security\.py$"},
        )
        self.assertEqual(
            allowlist_paths(
                "Public Apache Polaris 1.6.0 release-signing key fingerprint",
                r"^F2EEEB06110BEE1397EC74CBB8960FF52D9B1312$",
                "generic-api-key",
            ),
            {
                r"^bootstrap/polaris/v1\.6\.0/trusted-build-contract\.json$",
                r"^scripts/verify_polaris_trusted_image\.py$",
            },
        )
        self.assertEqual(
            allowlist_paths(
                "Public 40-hex Gradle SHA-1 package hashes in retained Apache Polaris 1.6.0 descriptor",
                r"^[0-9a-f]{40}$",
            ),
            {
                r"^bootstrap/polaris/v1\.6\.0/evidence/gradle-dependency-inputs\.json$",
            },
        )
        self.assertEqual(
            allowlist_paths(
                "Public SeaweedFS 4.39 source commit in retained Trivy evidence",
                r"^db42bb49757b459551607939807017d7a9d5a94a$",
            ),
            {r"^security/evidence/seaweedfs-v4\.39/trivy\.json$"},
        )
        self.assertNotIn(r"^bootstrap/seaweedfs/v4\.39/evidence/.*$", config)
        self.assertNotIn(r"^opentofu/dev/.*$", config)
        self.assertNotIn(r"^tests/.*$", config)
        self.assertNotIn(r"^security/evidence/seaweedfs-v4\.39/.*$", config)
        self.assertNotIn(r"^bootstrap/polaris/v1\.6\.0/evidence/.*$", config)

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for path in (
            "bootstrap/seaweedfs/v4.39/evidence/cosign-signature-bundle.json",
            "bootstrap/seaweedfs/v4.39/evidence/image-manifest.json",
            "bootstrap/seaweedfs/v4.39/evidence/sbom-attestation-bundle.json",
            "bootstrap/seaweedfs/v4.39/evidence/trivy-attestation-bundle.json",
        ):
            with self.subTest(path=path):
                self.assertIn(path, makefile)
        self.assertNotIn("bootstrap/seaweedfs/v4.39/evidence/*.json", makefile)

        newline_check = makefile.split("check-newlines:", 1)[1].split(
            "\ncheck-trailing-whitespace:", 1
        )[0]
        polaris_newline_exception_lines = [
            line.strip().removesuffix("\\").strip()
            for line in newline_check.splitlines()
            if "bootstrap/polaris/v1.6.0/evidence/" in line
        ]
        self.assertEqual(
            polaris_newline_exception_lines,
            [
                "bootstrap/polaris/v1.6.0/evidence/cosign-signature-bundle.json"
                "|bootstrap/polaris/v1.6.0/evidence/oci-manifest.json) continue ;;"
            ],
        )
        self.assertNotIn(
            "bootstrap/polaris/v1.6.0/evidence/*.json", newline_check
        )


if __name__ == "__main__":
    unittest.main()
