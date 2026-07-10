#!/usr/bin/env python3
"""Verify the repository-owned monorepo and Go CLI skeleton."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DIRECTORIES = (
    "cmd/shirokuma",
    "internal",
    "api/v1alpha1",
    "controllers",
    "agents",
    "mcp",
    "charts",
    "deploy",
    "opentofu",
    "policies",
    "observability",
    "examples",
    "benchmarks",
    "docs",
    "obsidian",
    ".github",
)

REQUIRED_FILES = (
    "agents/AGENTS.md",
    "benchmarks/AGENTS.md",
    "charts/AGENTS.md",
    "cmd/shirokuma/AGENTS.md",
    "controllers/AGENTS.md",
    "go.mod",
    "go.sum",
    "cmd/shirokuma/main.go",
    "internal/cli/root.go",
    "policies/AGENTS.md",
)


def main() -> int:
    missing = [
        path
        for path in REQUIRED_DIRECTORIES
        if not (ROOT / path).is_dir()
    ]
    missing.extend(
        path
        for path in REQUIRED_FILES
        if not (ROOT / path).is_file()
    )

    if missing:
        for path in missing:
            print(f"missing repository skeleton path: {path}")
        return 1

    print(
        "repository-skeleton: ok "
        f"directories={len(REQUIRED_DIRECTORIES)} files={len(REQUIRED_FILES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
