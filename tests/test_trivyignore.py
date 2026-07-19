from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.verify_trivyignore import APPROVED_STATEMENTS, canonical_document


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/verify_trivyignore.py"
APPROVED_PATH = "deploy/gitops/clusters/local-lite/flux-system/gotk-components.yaml"
NOW = "2026-07-15T00:00:00Z"
CANONICAL = canonical_document()


class TrivyIgnoreContractTests(unittest.TestCase):
    def run_checker(
        self,
        raw: bytes = CANONICAL,
        *,
        now: str = NOW,
        symlink: bool = False,
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
            return subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    "--ignore-file",
                    str(ignore_file),
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
                "--now",
                NOW,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_thirty_day_boundary_is_valid(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
        result = self.run_checker(now="2026-08-14T00:00:01Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exception is expired", result.stderr)

    def test_effective_expiry_instant_is_still_valid(self) -> None:
        result = self.run_checker(now="2026-08-14T00:00:00Z")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_more_than_thirty_days_is_rejected(self) -> None:
        result = self.run_checker(now="2026-07-14T23:59:59Z")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("30-day maximum", result.stderr)

    def test_now_requires_canonical_utc_timestamp(self) -> None:
        for now in ("2026-07-15", "2026-07-15T09:00:00+09:00"):
            with self.subTest(now=now):
                result = self.run_checker(now=now)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("canonical RFC 3339 UTC timestamp", result.stderr)

    def test_expiry_byte_drift_is_rejected(self) -> None:
        result = self.run_checker(CANONICAL.replace(b"2026-08-14", b"2026-08-13", 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bytes differ", result.stderr)

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
        self.assertNotIn("trivyignores:", report)
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
