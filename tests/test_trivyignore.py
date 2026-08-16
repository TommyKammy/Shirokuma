from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.verify_trivyignore import (
    APPROVAL_COMMENT_ID,
    APPROVAL_COMMENT_URL,
    APPROVAL_CREATED_AT,
    APPROVED_EXPIRY,
    APPROVED_AFFECTED_RULES,
    APPROVED_FINDINGS,
    APPROVED_MANIFEST_SHA256,
    APPROVED_ON,
    APPROVED_SCAN_REPORT_SHA256,
    APPROVED_STATEMENTS,
    APPROVED_TRIVY_METADATA_SHA256,
    ContractError,
    canonical_document,
    expected_trivy_metadata,
    validate_authorization_window,
    validate_live_scan_evidence,
    validate_scan_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/verify_trivyignore.py"
APPROVED_PATH = "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
MANIFEST_PATH = ROOT / APPROVED_PATH
MANIFEST = MANIFEST_PATH.read_bytes()
SCAN_REPORT_PATH = (
    ROOT
    / "security/evidence/flux-v2.9.2/gotk-components-v2.9.2.trivy-config.json"
)
TRIVY_METADATA_PATH = (
    ROOT
    / "security/evidence/flux-v2.9.2/gotk-components-v2.9.2.trivy-version.json"
)
SCAN_REPORT = SCAN_REPORT_PATH.read_bytes()
TRIVY_METADATA = TRIVY_METADATA_PATH.read_bytes()
NOW = "2026-08-14T06:43:14Z"
CANONICAL = canonical_document()


class TrivyIgnoreContractTests(unittest.TestCase):
    def run_checker(
        self,
        raw: bytes = CANONICAL,
        *,
        now: str = NOW,
        symlink: bool = False,
        manifest: bytes = MANIFEST,
        manifest_symlink: bool = False,
        scan_report: bytes = SCAN_REPORT,
        scan_report_symlink: bool = False,
        trivy_metadata: bytes = TRIVY_METADATA,
        trivy_metadata_symlink: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.yaml"
            target.write_bytes(raw)
            ignore_file = root / ".trivyignore.yaml"
            if symlink:
                ignore_file.symlink_to(target)
            else:
                ignore_file.write_bytes(target.read_bytes())
            manifest_target = root / "manifest-target.yaml"
            manifest_target.write_bytes(manifest)
            manifest_file = root / "gotk-components.yaml"
            if manifest_symlink:
                manifest_file.symlink_to(manifest_target)
            else:
                manifest_file.write_bytes(manifest_target.read_bytes())
            scan_report_target = root / "scan-report-target.json"
            scan_report_target.write_bytes(scan_report)
            scan_report_file = root / "scan-report.json"
            if scan_report_symlink:
                scan_report_file.symlink_to(scan_report_target)
            else:
                scan_report_file.write_bytes(scan_report_target.read_bytes())
            trivy_metadata_target = root / "trivy-metadata-target.json"
            trivy_metadata_target.write_bytes(trivy_metadata)
            trivy_metadata_file = root / "trivy-metadata.json"
            if trivy_metadata_symlink:
                trivy_metadata_file.symlink_to(trivy_metadata_target)
            else:
                trivy_metadata_file.write_bytes(trivy_metadata_target.read_bytes())
            return subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--ignore-file",
                    str(ignore_file),
                    "--manifest-file",
                    str(manifest_file),
                    "--scan-report-file",
                    str(scan_report_file),
                    "--trivy-metadata-file",
                    str(trivy_metadata_file),
                    "--now",
                    now,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_repository_ignore_file_is_valid_at_review_date(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--ignore-file",
                str(ROOT / ".trivyignore.yaml"),
                "--manifest-file",
                str(MANIFEST_PATH),
                "--now",
                NOW,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"approval_comment={APPROVAL_COMMENT_ID}", result.stdout)
        self.assertIn(f"manifest_sha256={APPROVED_MANIFEST_SHA256}", result.stdout)
        self.assertIn(f"scan_sha256={APPROVED_SCAN_REPORT_SHA256}", result.stdout)
        self.assertIn(
            f"trivy_metadata_sha256={APPROVED_TRIVY_METADATA_SHA256}",
            result.stdout,
        )
        self.assertIn(
            "approved_findings=KSV-0041:CRITICAL:1,KSV-0046:CRITICAL:8",
            result.stdout,
        )

    def test_exact_owner_authorization_is_pinned(self) -> None:
        self.assertEqual(APPROVAL_COMMENT_ID, 5290345820)
        self.assertEqual(
            APPROVAL_COMMENT_URL,
            "https://github.com/TommyKammy/Shirokuma/issues/150"
            "#issuecomment-5290345820",
        )
        self.assertEqual(
            APPROVAL_CREATED_AT,
            datetime(2026, 8, 14, 6, 43, 14, tzinfo=timezone.utc),
        )
        self.assertEqual(APPROVED_ON, date(2026, 8, 14))
        self.assertEqual(APPROVED_EXPIRY, date(2026, 9, 13))

    def test_exact_thirty_day_authorization_boundary_is_valid(self) -> None:
        expiry = validate_authorization_window(
            APPROVED_ON, APPROVAL_CREATED_AT, APPROVED_EXPIRY
        )
        self.assertEqual(expiry, datetime(2026, 9, 13, tzinfo=timezone.utc))

    def test_more_than_thirty_days_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "30-day maximum"):
            validate_authorization_window(
                APPROVED_ON,
                APPROVAL_CREATED_AT,
                date(2026, 9, 14),
            )

    def test_approval_time_must_match_approved_on(self) -> None:
        with self.assertRaisesRegex(ContractError, "approved_on date differ"):
            validate_authorization_window(
                APPROVED_ON,
                datetime(2026, 8, 15, 6, 8, 3, tzinfo=timezone.utc),
                APPROVED_EXPIRY,
            )

    def test_future_owner_approval_is_rejected(self) -> None:
        result = self.run_checker(now="2026-08-14T06:08:02Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owner approval is not yet effective", result.stderr)

    def test_exact_finding_contract_is_pinned(self) -> None:
        self.assertEqual(
            APPROVED_FINDINGS,
            (
                (
                    "KSV-0041",
                    "CRITICAL",
                    "Manage secrets",
                    "FAIL",
                    "builtin.kubernetes.KSV041",
                    "data.builtin.kubernetes.KSV041.deny",
                    1,
                ),
                (
                    "KSV-0046",
                    "CRITICAL",
                    "Manage all resources",
                    "FAIL",
                    "builtin.kubernetes.KSV046",
                    "data.builtin.kubernetes.KSV046.deny",
                    8,
                ),
            ),
        )
        self.assertEqual(len(APPROVED_AFFECTED_RULES), 9)
        self.assertEqual(
            {(rule[0], rule[1], rule[2], rule[3]) for rule in APPROVED_AFFECTED_RULES},
            {
                (
                    finding["ID"],
                    finding["Message"],
                    finding["CauseMetadata"]["StartLine"],
                    finding["CauseMetadata"]["EndLine"],
                )
                for finding in json.loads(SCAN_REPORT)["Results"][0][
                    "Misconfigurations"
                ]
            },
        )

    def test_unapproved_id_is_rejected(self) -> None:
        result = self.run_checker(CANONICAL.replace(b"KSV-0041", b"KSV-0001", 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bytes differ", result.stderr)

    def test_unapproved_path_is_rejected(self) -> None:
        result = self.run_checker(
            CANONICAL.replace(APPROVED_PATH.encode(), b"deploy/other.yaml", 1)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bytes differ", result.stderr)

    def test_empty_statement_is_rejected(self) -> None:
        result = self.run_checker(
            CANONICAL.replace(APPROVED_STATEMENTS[0].encode(), b"", 1)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bytes differ", result.stderr)

    def test_expired_entry_is_rejected(self) -> None:
        result = self.run_checker(now="2026-09-13T00:00:01Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exception is expired", result.stderr)

    def test_effective_expiry_instant_is_rejected(self) -> None:
        result = self.run_checker(now="2026-09-13T00:00:00Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exception is expired", result.stderr)

    def test_one_second_before_expiry_is_valid(self) -> None:
        result = self.run_checker(now="2026-09-12T23:59:59Z")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_now_requires_canonical_utc_timestamp(self) -> None:
        for now in ("2026-08-14", "2026-08-14T15:08:03+09:00"):
            with self.subTest(now=now):
                result = self.run_checker(now=now)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("canonical RFC 3339 UTC timestamp", result.stderr)

    def test_expiry_byte_drift_is_rejected(self) -> None:
        result = self.run_checker(CANONICAL.replace(b"2026-09-13", b"2026-09-12", 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bytes differ", result.stderr)

    def test_manifest_identity_drift_is_rejected(self) -> None:
        result = self.run_checker(manifest=MANIFEST + b"\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest SHA-256 differs", result.stderr)

    def test_retained_scan_and_metadata_are_exact(self) -> None:
        self.assertEqual(
            hashlib.sha256(SCAN_REPORT).hexdigest(), APPROVED_SCAN_REPORT_SHA256
        )
        self.assertEqual(
            hashlib.sha256(TRIVY_METADATA).hexdigest(),
            APPROVED_TRIVY_METADATA_SHA256,
        )
        self.assertEqual(
            json.loads(TRIVY_METADATA),
            expected_trivy_metadata(),
        )
        self.assertEqual(
            validate_scan_evidence(SCAN_REPORT, TRIVY_METADATA),
            APPROVED_SCAN_REPORT_SHA256,
        )

    def test_retained_scan_finding_count_drift_is_rejected(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["Results"][0]["Misconfigurations"].pop()
        report["Results"][0]["MisconfSummary"]["Failures"] = 8
        raw = json.dumps(report).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ContractError, "summary changed|finding set changed"):
            validate_scan_evidence(
                raw,
                TRIVY_METADATA,
                expected_report_sha256=digest,
            )

    def test_retained_scan_severity_drift_is_rejected(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["Results"][0]["Misconfigurations"][0]["Severity"] = "HIGH"
        raw = json.dumps(report).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ContractError, "finding set changed"):
            validate_scan_evidence(
                raw,
                TRIVY_METADATA,
                expected_report_sha256=digest,
            )

    def test_retained_scan_path_drift_is_rejected(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["ArtifactName"] = "deploy/other.yaml"
        raw = json.dumps(report).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ContractError, "report path changed"):
            validate_scan_evidence(
                raw,
                TRIVY_METADATA,
                expected_report_sha256=digest,
            )

    def test_retained_scan_metadata_drift_is_rejected(self) -> None:
        metadata = json.loads(TRIVY_METADATA)
        metadata["CheckBundle"]["Digest"] = "sha256:" + "0" * 64
        raw = json.dumps(metadata).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ContractError, "metadata content changed"):
            validate_scan_evidence(
                SCAN_REPORT,
                raw,
                expected_trivy_metadata_sha256=digest,
            )

    def test_fresh_live_scan_with_exact_findings_is_accepted(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["CreatedAt"] = "2026-08-16T10:39:54.464204+09:00"
        raw = json.dumps(report).encode("utf-8")
        now = datetime(2026, 8, 16, 1, 40, tzinfo=timezone.utc)
        self.assertEqual(
            validate_live_scan_evidence(raw, now),
            hashlib.sha256(raw).hexdigest(),
        )

    def test_live_scan_finding_count_drift_is_rejected(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["CreatedAt"] = "2026-08-16T10:39:54.464204+09:00"
        report["Results"][0]["Misconfigurations"].append(
            report["Results"][0]["Misconfigurations"][0]
        )
        raw = json.dumps(report).encode("utf-8")
        now = datetime(2026, 8, 16, 1, 40, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ContractError, "finding set changed"):
            validate_live_scan_evidence(raw, now)

    def test_live_scan_affected_rule_swap_is_rejected(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["CreatedAt"] = "2026-08-16T10:39:54.464204+09:00"
        finding = next(
            item
            for item in report["Results"][0]["Misconfigurations"]
            if item["ID"] == "KSV-0046"
        )
        finding["CauseMetadata"]["StartLine"] = 246
        finding["CauseMetadata"]["EndLine"] = 251
        raw = json.dumps(report).encode("utf-8")
        now = datetime(2026, 8, 16, 1, 40, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ContractError, "affected rule set changed"):
            validate_live_scan_evidence(raw, now)

    def test_stale_or_future_live_scan_is_rejected(self) -> None:
        report = json.loads(SCAN_REPORT)
        report["CreatedAt"] = "2026-08-16T10:39:54.464204+09:00"
        raw = json.dumps(report).encode("utf-8")
        for now, message in (
            (datetime(2026, 8, 16, 2, 0, tzinfo=timezone.utc), "stale"),
            (
                datetime(2026, 8, 16, 1, 39, tzinfo=timezone.utc),
                "in the future",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                ContractError, message
            ):
                validate_live_scan_evidence(raw, now)

    def test_additional_entry_or_field_is_rejected(self) -> None:
        mutations = (
            CANONICAL + b"  - id: KSV-0001\n",
            CANONICAL.replace(b"    paths:\n", b"    owner: platform\n    paths:\n", 1),
        )
        for raw in mutations:
            with self.subTest(raw=raw[-40:]):
                result = self.run_checker(raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bytes differ", result.stderr)

    def test_duplicate_yaml_field_is_rejected(self) -> None:
        result = self.run_checker(
            CANONICAL.replace(
                b"    paths:\n", b"    paths:\n    paths:\n", 1
            )
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bytes differ", result.stderr)

    def test_line_endings_and_final_newline_are_closed(self) -> None:
        for raw in (CANONICAL.replace(b"\n", b"\r\n"), CANONICAL.rstrip(b"\n")):
            with self.subTest(raw=raw[-20:]):
                result = self.run_checker(raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bytes differ", result.stderr)

    def test_symbolic_link_is_rejected(self) -> None:
        result = self.run_checker(symlink=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link", result.stderr)

    def test_manifest_symbolic_link_is_rejected(self) -> None:
        result = self.run_checker(manifest_symlink=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Flux manifest must not be a symbolic link", result.stderr)

    def test_scan_report_symbolic_link_is_rejected(self) -> None:
        result = self.run_checker(scan_report_symlink=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Trivy config report must not be a symbolic link", result.stderr)

    def test_trivy_metadata_symbolic_link_is_rejected(self) -> None:
        result = self.run_checker(trivy_metadata_symlink=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Trivy metadata must not be a symbolic link", result.stderr)

    def test_workflow_keeps_reporting_unfiltered_and_scopes_blocking_ignore(self) -> None:
        workflow = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
        report = workflow.split(
            "- name: Report all dependency and configuration findings", 1
        )[1].split(
            "- name: Block High or Critical dependency and configuration findings", 1
        )[0]
        blocking = workflow.split(
            "- name: Block High or Critical dependency and configuration findings", 1
        )[1].split("- name: Generate and retain source SBOM", 1)[0]
        capture = workflow.split(
            "- name: Capture current Flux RBAC findings before ignores", 1
        )[1].split("- name: Verify current Flux RBAC findings before ignores", 1)[0]
        verify_live = workflow.split(
            "- name: Verify current Flux RBAC findings before ignores", 1
        )[1].split(
            "- name: Block High or Critical dependency and configuration findings", 1
        )[0]
        self.assertNotIn("trivyignores:", report)
        self.assertIn("trivy-live-flux-rbac.json", capture)
        self.assertIn("severity: CRITICAL", capture)
        self.assertNotIn("trivyignores:", capture)
        self.assertIn(
            "--live-scan-report-file trivy-live-flux-rbac.json", verify_live
        )
        self.assertEqual(blocking.count("trivyignores: .trivyignore.yaml"), 1)
        self.assertEqual(workflow.count("trivyignores: .trivyignore.yaml"), 1)

    def test_verify_security_runs_contract_tests_and_validator(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        verify_security = makefile.split(
            "verify-security: verify-cosign\n",
            1,
        )[1].split("\n\n", 1)[0]
        self.assertIn("test_trivyignore.py", verify_security)
        self.assertIn("scripts/verify_trivyignore.py", verify_security)


if __name__ == "__main__":
    unittest.main()
