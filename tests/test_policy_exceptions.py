from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-07-13T00:00:00Z"


class PolicyExceptionContractTests(unittest.TestCase):
    def run_verifier(self, documents: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, document in enumerate(documents):
                (root / f"exception-{index}.json").write_text(
                    json.dumps(document), encoding="utf-8"
                )
            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify_policy_exceptions.py"),
                    "--directory",
                    str(root),
                    "--now",
                    NOW,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    @staticmethod
    def valid_exception() -> dict[str, object]:
        return {
            "apiVersion": "policies.kyverno.io/v1",
            "kind": "PolicyException",
            "metadata": {
                "name": "temporary-debugger",
                "namespace": "policy-exceptions",
                "annotations": {
                    "shirokuma.dev/exception-owner": "platform-team",
                    "shirokuma.dev/exception-reviewer": "security-team",
                    "shirokuma.dev/exception-issue": "https://github.com/TommyKammy/Shirokuma/issues/11",
                    "shirokuma.dev/exception-expires-at": "2026-07-20T00:00:00Z",
                    "shirokuma.dev/exception-reason": "Bounded debugger experiment",
                },
            },
            "spec": {
                "policyRefs": [
                    {"name": "disallow-host-path", "kind": "ValidatingPolicy"}
                ],
                "matchConditions": [
                    {
                        "name": "specific-pod",
                        "expression": "object.metadata.name == 'debugger-11'",
                    }
                ],
            },
        }

    def test_empty_directory_is_valid(self) -> None:
        result = self.run_verifier([])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok files=0", result.stdout)

    def test_narrow_reviewed_exception_is_valid(self) -> None:
        result = self.run_verifier([self.valid_exception()])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_expired_exception_is_rejected(self) -> None:
        document = self.valid_exception()
        document["metadata"]["annotations"][
            "shirokuma.dev/exception-expires-at"
        ] = "2026-07-12T00:00:00Z"
        result = self.run_verifier([document])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exception is expired", result.stdout)

    def test_wildcard_policy_and_self_review_are_rejected(self) -> None:
        document = self.valid_exception()
        document["metadata"]["annotations"][
            "shirokuma.dev/exception-reviewer"
        ] = "platform-team"
        document["spec"]["policyRefs"][0]["name"] = "*"
        result = self.run_verifier([document])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owner and reviewer must differ", result.stdout)
        self.assertIn("exact policy name", result.stdout)

    def test_unknown_policy_reference_is_rejected(self) -> None:
        document = self.valid_exception()
        document["spec"]["policyRefs"][0]["name"] = "misspelled-policy"
        result = self.run_verifier([document])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reference a policy in the bundle", result.stdout)

    def test_broad_metadata_predicates_are_rejected(self) -> None:
        for expression in (
            "has(object.metadata.name)",
            "object.metadata.namespace != ''",
        ):
            with self.subTest(expression=expression):
                document = self.valid_exception()
                document["spec"]["matchConditions"][0]["expression"] = expression
                result = self.run_verifier([document])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("narrowly match resource metadata", result.stdout)


if __name__ == "__main__":
    unittest.main()
