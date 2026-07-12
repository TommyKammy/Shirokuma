import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "observability" / "pawprint.schema.json"
FIXTURE = ROOT / "observability" / "fixtures" / "failed-reconciliation.json"


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
            ROOT / "docs/design/08_Runbooks/RB-002_Diagnose_failed_Argo_CD_sync.md"
        ).read_text(encoding="utf-8")
        for required in (
            "--tail=200",
            ".items[:100][]",
            ".items[-100:][]",
            "conditions:[(.status.conditions // [])[:10][]",
            "1 MiB",
            "30 days",
        ):
            self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
