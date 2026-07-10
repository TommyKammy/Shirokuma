#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DESIGN_ROOT = ROOT / "docs" / "design"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def safe_repo_path(value: str) -> bool:
    candidate = PurePosixPath(value)
    return not candidate.is_absolute() and ".." not in candidate.parts


def validate() -> list[str]:
    errors: list[str] = []
    manifest = load_json(DESIGN_ROOT / "context-manifest.json")
    issue_context = load_json(DESIGN_ROOT / "issue-context.json")

    documents = manifest.get("documents", [])
    targets: set[str] = set()
    sources: set[str] = set()
    for entry in documents:
        source = entry.get("source", "")
        target = entry.get("target", "")
        if not safe_repo_path(source):
            errors.append(f"unsafe source path: {source}")
        if not safe_repo_path(target) or not target.startswith("docs/design/"):
            errors.append(f"unsafe target path: {target}")
        if source in sources:
            errors.append(f"duplicate source path: {source}")
        if target in targets:
            errors.append(f"duplicate target path: {target}")
        sources.add(source)
        targets.add(target)
        if not (ROOT / target).is_file():
            errors.append(f"missing materialized document: {target}")

    expected_issue_numbers = {str(number) for number in range(2, 12)}
    issues = issue_context.get("issues", {})
    if set(issues) != expected_issue_numbers:
        errors.append(
            "issue-context.json must define exactly issues #2 through #11"
        )

    allowed_targets = targets | {"AGENTS.md"}
    for issue_number, references in issues.items():
        if not references:
            errors.append(f"issue #{issue_number} has no document references")
        if len(references) != len(set(references)):
            errors.append(f"issue #{issue_number} has duplicate references")
        for reference in references:
            if not safe_repo_path(reference):
                errors.append(
                    f"issue #{issue_number} has unsafe reference: {reference}"
                )
            if reference not in allowed_targets:
                errors.append(
                    f"issue #{issue_number} references an unmaterialized file: "
                    f"{reference}"
                )
            if not (ROOT / reference).is_file():
                errors.append(
                    f"issue #{issue_number} references a missing file: {reference}"
                )

    root_agents = ROOT / "AGENTS.md"
    snapshot_agents = DESIGN_ROOT / "99_Project_Files" / "AGENTS.md"
    if not root_agents.is_file():
        errors.append("missing root AGENTS.md")
    elif snapshot_agents.is_file() and root_agents.read_bytes() != snapshot_agents.read_bytes():
        errors.append(
            "root AGENTS.md differs from docs/design/99_Project_Files/AGENTS.md"
        )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"design-context: error: {error}")
        return 1

    manifest = load_json(DESIGN_ROOT / "context-manifest.json")
    issue_context = load_json(DESIGN_ROOT / "issue-context.json")
    reference_count = sum(
        len(references) for references in issue_context["issues"].values()
    )
    print(
        "design-context: ok "
        f"documents={len(manifest['documents'])} "
        f"issues={len(issue_context['issues'])} "
        f"references={reference_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
