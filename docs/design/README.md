# Repository-owned design context

This directory contains the repository-owned snapshot of the approved
Shirokuma design notes used by GitHub issues and Codex worktrees.

- `context-manifest.json` records the Obsidian-relative source and checked-in
  target for every materialized document.
- `issue-context.json` records the exact document paths expected in issues
  #2 through #11.
- `make verify-design-context` verifies the local snapshot without network
  access.
- `make supervisor-preflight` compares live GitHub issue bodies with the
  mapping and proves every referenced file exists on `origin/main`.

Obsidian remains the planning source. Changes to a materialized note must be
applied to the Obsidian source and this snapshot in the same change.

Do not add workstation-local absolute paths to GitHub issues or committed
documentation.
