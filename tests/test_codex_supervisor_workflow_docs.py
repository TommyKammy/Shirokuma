from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_GUIDE = ROOT / "docs/design/04_Development/042_Codex_Supervisor_GitHub_Workflow.md"
ISSUE_CONTRACT = ROOT / "docs/design/04_Development/044_Issue_and_PR_Workflow.md"


def canonical_issue_example(document: str) -> str:
    match = re.search(
        r"<!-- canonical-runnable-issue:start -->(.*?)"
        r"<!-- canonical-runnable-issue:end -->",
        document,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError("canonical runnable issue example is missing")
    return match.group(1)


class CodexSupervisorWorkflowDocsTests(unittest.TestCase):
    def test_operating_guide_defines_fail_closed_selection_and_worktree_commands(self) -> None:
        guide = WORKFLOW_GUIDE.read_text(encoding="utf-8")

        for expected in (
            '"issueLabel": "codex"',
            '"skipTitlePrefixes": ["Epic:"]',
            '"workspacePreparationCommand": "make prepare"',
            '"localCiCommand": "make verify"',
            "CODEX_SUPERVISOR_CONFIG",
            "node dist/index.js issue-lint 2",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)

    def test_canonical_runnable_child_issue_has_contract_and_schedule(self) -> None:
        contract = ISSUE_CONTRACT.read_text(encoding="utf-8")
        example = canonical_issue_example(contract)

        for heading in (
            "Summary",
            "Scope",
            "Acceptance criteria",
            "Verification",
        ):
            with self.subTest(heading=heading):
                self.assertRegex(example, rf"(?m)^## {re.escape(heading)}\s*$")

        for metadata in (
            "Part of:",
            "Depends on:",
            "Parallelizable:",
            "Execution order:",
        ):
            with self.subTest(metadata=metadata):
                self.assertRegex(example, rf"(?m)^{re.escape(metadata)}\s*\S")

    def test_issue_contract_requires_supervisor_lint_fields(self) -> None:
        contract = ISSUE_CONTRACT.read_text(encoding="utf-8")

        for required in (
            "Summary",
            "Scope",
            "Acceptance criteria",
            "Verification",
            "Part of",
            "Depends on",
            "Parallelizable",
            "Execution order",
        ):
            with self.subTest(required=required):
                self.assertRegex(contract, rf"(?m)^- {re.escape(required)}$")


if __name__ == "__main__":
    unittest.main()
