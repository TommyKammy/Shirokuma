from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preflight_supervisor_issues as preflight  # noqa: E402


def completed(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def related_body(*references: str) -> str:
    lines = ["## Related docs / ADR", ""]
    lines.extend(f"- `{reference}`" for reference in references)
    lines.extend(("", "## Risk and rollback", "", "- Revert the pull request."))
    return "\n".join(lines)


def live_issue(number: int, body: str) -> dict[str, object]:
    return {"number": number, "title": f"Issue {number}", "body": body}


class ExtractReferencesTests(unittest.TestCase):
    def test_extracts_references_from_repository_authored_h2_section(self) -> None:
        body = related_body("docs/design/01_Product/010_Project_Charter.md")

        self.assertEqual(
            preflight.extract_references(body),
            ["docs/design/01_Product/010_Project_Charter.md"],
        )

    def test_extracts_references_from_github_issue_form_h3_section(self) -> None:
        body = """\
### Related docs / ADR

- `docs/design/04_Development/044_Issue_and_PR_Workflow.md`
- `docs/design/07_ADR/ADR-0015_Use_AGENTS_md_as_mandatory_repository_instruction.md`

### Risk tier

T0 - docs or metadata only
"""

        self.assertEqual(
            preflight.extract_references(body),
            [
                "docs/design/04_Development/044_Issue_and_PR_Workflow.md",
                "docs/design/07_ADR/ADR-0015_Use_AGENTS_md_as_mandatory_repository_instruction.md",
            ],
        )

    def test_rejects_non_contract_heading_levels(self) -> None:
        body = """\
#### Related docs / ADR

- `docs/design/01_Product/010_Project_Charter.md`
"""

        self.assertEqual(preflight.extract_references(body), [])

    def test_requires_exactly_one_related_docs_section(self) -> None:
        duplicate = related_body("docs/design/first.md") + "\n" + related_body(
            "docs/design/second.md"
        )

        references, errors = preflight.validate_related_docs(duplicate)

        self.assertEqual(references, [])
        self.assertEqual(
            errors,
            ["must contain exactly one Related docs / ADR section (found 2)"],
        )

    def test_requires_at_least_one_backtick_markdown_reference(self) -> None:
        references, errors = preflight.validate_related_docs(
            "## Related docs / ADR\n\nNo repository reference yet.\n"
        )

        self.assertEqual(references, [])
        self.assertEqual(
            errors,
            [
                "Related docs / ADR must contain at least one "
                "backtick-delimited .md reference"
            ],
        )

    def test_rejects_duplicate_references(self) -> None:
        reference = "docs/design/duplicate.md"

        references, errors = preflight.validate_related_docs(
            related_body(reference, reference)
        )

        self.assertEqual(references, [reference, reference])
        self.assertEqual(
            errors,
            [f"duplicate Related docs / ADR reference: {reference}"],
        )

    def test_rejects_unsafe_or_non_markdown_references(self) -> None:
        cases = {
            "/tmp/absolute.md": "absolute paths are forbidden",
            "docs/design/../escape.md": "parent traversal is forbidden",
            "docs\\design\\windows.md": (
                "path must use repository-relative POSIX syntax"
            ),
            "docs/design/not-markdown.txt": "path must end in .md",
            "./docs/design/noncanonical.md": (
                "path must use canonical repository-relative POSIX syntax"
            ),
        }

        for reference, reason in cases.items():
            with self.subTest(reference=reference):
                references, errors = preflight.validate_related_docs(
                    related_body(reference)
                )
                self.assertEqual(references, [reference])
                self.assertEqual(len(errors), 1)
                self.assertIn(reason, errors[0])

    def test_accepts_unique_repo_relative_markdown_references(self) -> None:
        references, errors = preflight.validate_related_docs(
            related_body("AGENTS.md", "docs/design/work-package.md")
        )

        self.assertEqual(references, ["AGENTS.md", "docs/design/work-package.md"])
        self.assertEqual(errors, [])


class DecodeLiveIssuesTests(unittest.TestCase):
    def test_keeps_all_open_query_results_without_l0_number_filtering(self) -> None:
        payload = json.dumps(
            [
                live_issue(11, related_body("docs/design/l0.md")),
                live_issue(26, related_body("docs/design/l1.md")),
                live_issue(104, related_body("docs/design/later.md")),
            ]
        )

        issues, errors = preflight.decode_live_issues(payload)

        self.assertEqual(list(issues), ["11", "26", "104"])
        self.assertEqual(errors, [])

    def test_rejects_malformed_json_and_non_array_roots(self) -> None:
        issues, errors = preflight.decode_live_issues("not-json")
        self.assertEqual(issues, {})
        self.assertIn("not valid JSON", errors[0])

        issues, errors = preflight.decode_live_issues("{}")
        self.assertEqual(issues, {})
        self.assertEqual(errors, ["live GitHub issue response must be a JSON array"])

    def test_rejects_duplicate_numbers_and_invalid_records(self) -> None:
        payload = json.dumps(
            [
                live_issue(26, related_body("docs/design/l1.md")),
                live_issue(26, related_body("docs/design/duplicate.md")),
                {"number": 27, "title": "Issue 27", "body": None},
                "not-an-object",
            ]
        )

        issues, errors = preflight.decode_live_issues(payload)

        self.assertEqual(list(issues), ["26"])
        self.assertEqual(len(errors), 3)
        self.assertIn("duplicates issue #26", errors[0])
        self.assertIn("issue #27 has an invalid body", errors[1])
        self.assertIn("record 3 must be an object", errors[2])


class GitPathTests(unittest.TestCase):
    def test_requires_a_blob_at_the_selected_ref(self) -> None:
        with patch.object(
            preflight,
            "run",
            return_value=completed(["git"], stdout="blob\n"),
        ) as run_mock:
            self.assertTrue(
                preflight.git_path_exists("origin/main", "docs/design/valid.md")
            )
        run_mock.assert_called_once_with(
            ["git", "cat-file", "-t", "origin/main:docs/design/valid.md"],
            check=False,
        )

        with patch.object(
            preflight,
            "run",
            return_value=completed(["git"], stdout="tree\n"),
        ):
            self.assertFalse(
                preflight.git_path_exists("origin/main", "docs/design/tree.md")
            )

    def test_rejects_unsafe_paths_before_invoking_git(self) -> None:
        with patch.object(preflight, "run") as run_mock:
            self.assertFalse(
                preflight.git_path_exists("origin/main", "docs/../escape.md")
            )
        run_mock.assert_not_called()


class MainTests(unittest.TestCase):
    def run_main(
        self,
        *,
        issues: list[dict[str, object]],
        legacy: dict[str, list[str]] | None = None,
        ref: str = "test-ref",
        github_returncode: int = 0,
        git_path_side_effect: object = True,
        fetch_returncode: int = 0,
    ) -> tuple[int, str, list[list[str]]]:
        commands: list[list[str]] = []

        def fake_run(
            command: list[str], *, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            del check
            commands.append(command)
            if command[:3] == ["git", "fetch", "origin"]:
                return completed(
                    command,
                    returncode=fetch_returncode,
                    stderr="fetch failed" if fetch_returncode else "",
                )
            self.assertEqual(command[:3], ["gh", "issue", "list"])
            return completed(
                command,
                returncode=github_returncode,
                stdout=json.dumps(issues) if github_returncode == 0 else "",
                stderr="GitHub unavailable" if github_returncode else "",
            )

        mapping = {
            "repository": "TommyKammy/Shirokuma",
            "issues": {} if legacy is None else legacy,
        }
        output = io.StringIO()
        with (
            patch.object(
                preflight,
                "parse_args",
                return_value=argparse.Namespace(ref=ref),
            ),
            patch.object(preflight, "validate", return_value=[]),
            patch.object(preflight, "load_json", return_value=mapping),
            patch.object(preflight, "run", side_effect=fake_run),
            patch.object(
                preflight,
                "git_path_exists",
                side_effect=git_path_side_effect
                if callable(git_path_side_effect)
                else None,
                return_value=git_path_side_effect
                if not callable(git_path_side_effect)
                else False,
            ),
            redirect_stdout(output),
        ):
            result = preflight.main()
        return result, output.getvalue(), commands

    def test_dynamic_l1_issue_set_succeeds_and_legacy_match_is_optional(self) -> None:
        l0_reference = "docs/design/l0.md"
        result, output, commands = self.run_main(
            issues=[
                live_issue(11, related_body(l0_reference)),
                live_issue(26, related_body("docs/design/l1.md")),
            ],
            legacy={"11": [l0_reference]},
        )

        self.assertEqual(result, 0)
        self.assertIn("issues=2 references=2 legacy_compared=1", output)
        gh_command = commands[0]
        self.assertEqual(gh_command[gh_command.index("--state") + 1], "open")
        self.assertEqual(gh_command[gh_command.index("--label") + 1], "codex")
        self.assertEqual(
            gh_command[gh_command.index("--limit") + 1],
            str(preflight.ISSUE_QUERY_LIMIT),
        )

    def test_legacy_issue_still_requires_exact_reference_order(self) -> None:
        result, output, _ = self.run_main(
            issues=[live_issue(11, related_body("docs/design/actual.md"))],
            legacy={"11": ["docs/design/expected.md"]},
        )

        self.assertEqual(result, 1)
        self.assertIn("issue #11 Related docs / ADR mismatch", output)

    def test_missing_reference_at_ref_fails_closed(self) -> None:
        def path_exists(_ref: str, path: str) -> bool:
            return path == "AGENTS.md"

        result, output, _ = self.run_main(
            issues=[live_issue(26, related_body("docs/design/missing.md"))],
            git_path_side_effect=path_exists,
        )

        self.assertEqual(result, 1)
        self.assertIn("reference is absent from test-ref", output)

    def test_missing_agents_file_fails_closed(self) -> None:
        def path_exists(_ref: str, path: str) -> bool:
            return path != "AGENTS.md"

        result, output, _ = self.run_main(
            issues=[live_issue(26, related_body("docs/design/l1.md"))],
            git_path_side_effect=path_exists,
        )

        self.assertEqual(result, 1)
        self.assertIn("root AGENTS.md is absent from test-ref", output)

    def test_github_and_origin_fetch_failures_remain_blocking(self) -> None:
        result, output, _ = self.run_main(
            issues=[],
            ref="origin/main",
            github_returncode=1,
            fetch_returncode=1,
        )

        self.assertEqual(result, 1)
        self.assertIn("unable to refresh origin/main", output)
        self.assertIn("unable to read live GitHub issues", output)


if __name__ == "__main__":
    unittest.main()
