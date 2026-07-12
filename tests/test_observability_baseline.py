import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "observability" / "pawprint.schema.json"
FIXTURE = ROOT / "observability" / "fixtures" / "failed-reconciliation.json"
BOUND_EVIDENCE = ROOT / "scripts" / "bound_evidence.py"


class ObservabilityBaselineTests(unittest.TestCase):
    def test_failed_reconciliation_fixture_matches_l0_shape(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(fixture["schema_version"], "1")
        self.assertEqual(set(schema["required"]), set(fixture))
        self.assertEqual(
            {"repository", "issue", "branch", "pull_request"},
            set(fixture["work"]),
        )
        self.assertIn(fixture["outcome"], {"succeeded", "failed", "blocked", "cancelled"})
        self.assertLessEqual(len(fixture["verification"]), 32)
        self.assertLessEqual(len(fixture["evidence"]), 32)

    def test_fixture_does_not_persist_sensitive_or_unbounded_fields(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        serialized = json.dumps(fixture).lower()
        for forbidden in (
            "credential",
            "kubeconfig",
            "secret_value",
            "raw_prompt",
            "command_output",
            "environment",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_runbook_uses_bounded_collection(self) -> None:
        runbook = (
            ROOT / "docs/design/08_Runbooks/RB-002_Diagnose_failed_Flux_reconciliation.md"
        ).read_text(encoding="utf-8")
        for required in (
            "set -o errexit -o nounset -o pipefail",
            "bound_evidence.py --max-bytes 1048576",
            "--since=30m",
            "GitRepository",
            "Kustomization",
            "1 MiB",
            "30 days",
        ):
            self.assertIn(required, runbook)

    def test_bound_evidence_preserves_small_input(self) -> None:
        result = subprocess.run(
            [sys.executable, str(BOUND_EVIDENCE), "--max-bytes", "128"],
            input=b"bounded log\n",
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout, b"bounded log\n")

    def test_bound_evidence_truncates_large_single_line(self) -> None:
        result = subprocess.run(
            [sys.executable, str(BOUND_EVIDENCE), "--max-bytes", "128"],
            input=b"x" * 4096,
            capture_output=True,
            check=True,
        )
        self.assertEqual(len(result.stdout), 128)
        self.assertTrue(result.stdout.endswith(b"[shirokuma evidence truncated]\n"))


if __name__ == "__main__":
    unittest.main()
