#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from verify_design_context import DESIGN_ROOT, ROOT, load_json, validate


RELATED_SECTION = re.compile(
    r"^## Related docs / ADR\s*$\n(?P<body>.*?)(?=^## |\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
MARKDOWN_PATH = re.compile(r"`([^`]+\.md)`")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate live Codex issues and prove every Related docs / ADR path "
            "exists on the selected git ref before creating an issue worktree."
        )
    )
    parser.add_argument("--ref", default="origin/main")
    return parser.parse_args()


def extract_references(body: str) -> list[str]:
    match = RELATED_SECTION.search(body)
    if match is None:
        return []
    return MARKDOWN_PATH.findall(match.group("body"))


def git_path_exists(ref: str, repo_path: str) -> bool:
    if PurePosixPath(repo_path).is_absolute() or ".." in PurePosixPath(repo_path).parts:
        return False
    result = run(["git", "cat-file", "-e", f"{ref}:{repo_path}"], check=False)
    return result.returncode == 0


def main() -> int:
    args = parse_args()
    errors = validate()
    issue_context = load_json(DESIGN_ROOT / "issue-context.json")
    repository = issue_context["repository"]

    if args.ref == "origin/main":
        fetched = run(["git", "fetch", "origin", "main"], check=False)
        if fetched.returncode != 0:
            errors.append(
                "unable to refresh origin/main: "
                f"{fetched.stderr.strip() or fetched.stdout.strip()}"
            )

    issue_result = run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "all",
            "--label",
            "codex",
            "--limit",
            "100",
            "--json",
            "number,title,body",
        ],
        check=False,
    )
    if issue_result.returncode != 0:
        errors.append(
            "unable to read live GitHub issues: "
            f"{issue_result.stderr.strip() or issue_result.stdout.strip()}"
        )
        live_issues: dict[str, dict] = {}
    else:
        live_issues = {
            str(issue["number"]): issue
            for issue in json.loads(issue_result.stdout)
            if 2 <= issue["number"] <= 11
        }

    expected_issues = issue_context["issues"]
    if set(live_issues) != set(expected_issues):
        errors.append(
            "live codex issues must contain exactly #2 through #11 "
            "for the L0 preflight"
        )

    checked_references = 0
    for issue_number, expected_references in expected_issues.items():
        issue = live_issues.get(issue_number)
        if issue is None:
            continue
        actual_references = extract_references(issue["body"])
        if actual_references != expected_references:
            errors.append(
                f"issue #{issue_number} Related docs / ADR mismatch: "
                f"expected={expected_references} actual={actual_references}"
            )
        for reference in actual_references:
            checked_references += 1
            if not git_path_exists(args.ref, reference):
                errors.append(
                    f"issue #{issue_number} reference is absent from "
                    f"{args.ref}: {reference}"
                )

    if not git_path_exists(args.ref, "AGENTS.md"):
        errors.append(f"root AGENTS.md is absent from {args.ref}")

    if errors:
        for error in errors:
            print(f"supervisor-preflight: error: {error}")
        return 1

    print(
        "supervisor-preflight: ok "
        f"repository={repository} ref={args.ref} "
        f"issues={len(expected_issues)} references={checked_references}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
