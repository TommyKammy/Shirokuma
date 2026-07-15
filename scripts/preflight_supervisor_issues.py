#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath, PureWindowsPath

from verify_design_context import DESIGN_ROOT, ROOT, load_json, validate


ATX_HEADING = re.compile(
    r"^ {0,3}(?P<marks>#{2,3})(?:[ \t]+(?P<title>.*?))?[ \t]*$"
)
FENCE_OPEN = re.compile(
    r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$"
)
CODE_SPAN = re.compile(r"`(?P<value>[^`\r\n]+)`")
ISSUE_QUERY_LIMIT = 1000


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


def lines_outside_fences(body: str) -> list[str]:
    lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    inside_html_comment = False
    for line in body.splitlines():
        if fence_character is not None:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}"
                rf"{{{fence_length},}}[ \t]*",
                line,
            )
            if closing is not None:
                fence_character = None
                fence_length = 0
            continue

        visible: list[str] = []
        cursor = 0
        while cursor < len(line):
            if inside_html_comment:
                comment_end = line.find("-->", cursor)
                if comment_end == -1:
                    cursor = len(line)
                    break
                inside_html_comment = False
                cursor = comment_end + 3
                continue

            comment_start = line.find("<!--", cursor)
            if comment_start == -1:
                visible.append(line[cursor:])
                cursor = len(line)
                break
            visible.append(line[cursor:comment_start])
            inside_html_comment = True
            cursor = comment_start + 4

        visible_line = "".join(visible)
        opening = FENCE_OPEN.fullmatch(visible_line)
        if opening is not None:
            marker = opening.group("marker")
            info = opening.group("info")
            if marker[0] != "`" or "`" not in info:
                fence_character = marker[0]
                fence_length = len(marker)
                continue
        lines.append(visible_line)
    return lines


def related_sections(body: str) -> list[str]:
    sections: list[str] = []
    current: list[str] | None = None
    for line in lines_outside_fences(body):
        heading = ATX_HEADING.fullmatch(line)
        if heading is not None:
            if current is not None:
                sections.append("\n".join(current))
            title = (heading.group("title") or "").strip()
            title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
            current = [] if title.casefold() == "related docs / adr" else None
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        sections.append("\n".join(current))
    return sections


def extract_references(body: str) -> list[str]:
    sections = related_sections(body)
    if len(sections) != 1:
        return []
    return [
        match.group("value")
        for match in CODE_SPAN.finditer(sections[0])
    ]


def repo_path_error(value: str, *, require_markdown: bool) -> str | None:
    if not value or value != value.strip():
        return "path must be nonempty without surrounding whitespace"
    if "\x00" in value or "\\" in value:
        return "path must use repository-relative POSIX syntax"
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or PureWindowsPath(value).is_absolute():
        return "absolute paths are forbidden"
    if ".." in candidate.parts:
        return "parent traversal is forbidden"
    if candidate.as_posix() != value:
        return "path must use canonical repository-relative POSIX syntax"
    if require_markdown and candidate.suffix != ".md":
        return "path must end in .md"
    return None


def validate_related_docs(body: str) -> tuple[list[str], list[str]]:
    sections = related_sections(body)
    if len(sections) != 1:
        return [], [
            "must contain exactly one Related docs / ADR section "
            f"(found {len(sections)})"
        ]

    references = [
        match.group("value")
        for match in CODE_SPAN.finditer(sections[0])
    ]
    errors: list[str] = []
    if not references:
        errors.append(
            "Related docs / ADR must contain at least one "
            "backtick-delimited .md reference"
        )

    seen: set[str] = set()
    for reference in references:
        if reference in seen:
            errors.append(f"duplicate Related docs / ADR reference: {reference}")
        seen.add(reference)
        path_error = repo_path_error(reference, require_markdown=True)
        if path_error is not None:
            errors.append(
                f"invalid Related docs / ADR reference {reference!r}: {path_error}"
            )
    return references, errors


def decode_live_issues(
    payload: str,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    try:
        records = json.loads(payload)
    except json.JSONDecodeError as error:
        return {}, [f"live GitHub issue response is not valid JSON: {error.msg}"]
    if not isinstance(records, list):
        return {}, ["live GitHub issue response must be a JSON array"]

    errors: list[str] = []
    live_issues: dict[str, dict[str, object]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"live GitHub issue record {index} must be an object")
            continue
        number = record.get("number")
        title = record.get("title")
        body = record.get("body")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            errors.append(f"live GitHub issue record {index} has an invalid number")
            continue
        if not isinstance(title, str):
            errors.append(f"live GitHub issue #{number} has an invalid title")
            continue
        if not isinstance(body, str):
            errors.append(f"live GitHub issue #{number} has an invalid body")
            continue
        issue_number = str(number)
        if issue_number in live_issues:
            errors.append(f"live GitHub issue response duplicates issue #{number}")
            continue
        live_issues[issue_number] = {
            "number": number,
            "title": title,
            "body": body,
        }
    return live_issues, errors


def git_path_exists(ref: str, repo_path: str) -> bool:
    if repo_path_error(repo_path, require_markdown=False) is not None:
        return False
    result = run(
        [
            "git",
            "--literal-pathspecs",
            "ls-tree",
            "-z",
            ref,
            "--",
            repo_path,
        ],
        check=False,
    )
    if result.returncode != 0:
        return False

    records = result.stdout.split("\0")
    if records and records[-1] == "":
        records.pop()
    if len(records) != 1:
        return False

    metadata, separator, actual_path = records[0].partition("\t")
    fields = metadata.split()
    return (
        separator == "\t"
        and actual_path == repo_path
        and len(fields) == 3
        and fields[0] in {"100644", "100755"}
        and fields[1] == "blob"
    )


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
            "open",
            "--label",
            "codex",
            "--limit",
            str(ISSUE_QUERY_LIMIT),
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
        live_issues, decode_errors = decode_live_issues(issue_result.stdout)
        errors.extend(decode_errors)
        if len(live_issues) >= ISSUE_QUERY_LIMIT:
            errors.append(
                "live GitHub issue query reached its safety limit; "
                "cannot prove the complete open codex issue set"
            )

    legacy_issues = issue_context["issues"]
    checked_references = 0
    legacy_compared = 0
    for issue_number, issue in live_issues.items():
        actual_references, reference_errors = validate_related_docs(
            str(issue["body"])
        )
        errors.extend(
            f"issue #{issue_number} {reference_error}"
            for reference_error in reference_errors
        )

        expected_references = legacy_issues.get(issue_number)
        if expected_references is not None:
            legacy_compared += 1
        if (
            expected_references is not None
            and actual_references != expected_references
        ):
            errors.append(
                f"issue #{issue_number} Related docs / ADR mismatch: "
                f"expected={expected_references} actual={actual_references}"
            )

        for reference in dict.fromkeys(actual_references):
            if repo_path_error(reference, require_markdown=True) is not None:
                continue
            checked_references += 1
            if not git_path_exists(args.ref, reference):
                errors.append(
                    f"issue #{issue_number} reference is absent or not a "
                    "regular file in "
                    f"{args.ref}: {reference}"
                )

    if not git_path_exists(args.ref, "AGENTS.md"):
        errors.append(
            f"root AGENTS.md is absent or not a regular file in {args.ref}"
        )

    if errors:
        for error in errors:
            print(f"supervisor-preflight: error: {error}")
        return 1

    print(
        "supervisor-preflight: ok "
        f"repository={repository} ref={args.ref} "
        f"issues={len(live_issues)} references={checked_references} "
        f"legacy_compared={legacy_compared}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
