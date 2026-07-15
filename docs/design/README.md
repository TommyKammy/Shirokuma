# Repository-owned design context

This directory contains the repository-owned snapshot of the approved
Shirokuma design notes used by GitHub issues and Codex worktrees.

- `context-manifest.json` records the Obsidian-relative source and checked-in
  target for every materialized document.
- `issue-context.json` retains the exact legacy document mapping for issues
  #2 through #11. It is an offline compatibility contract, not the live
  supervisor candidate set.
- `make verify-design-context` verifies the local snapshot without network
  access and intentionally preserves the legacy mapping schema.
- `make supervisor-preflight` dynamically queries every open issue carrying
  `codex`. Each issue must contain exactly one nonempty `Related docs / ADR`
  section with unique, canonical repository-relative `.md` references. The
  preflight rejects absolute paths and parent traversal, proves every reference
  and root `AGENTS.md` is a blob on `origin/main`, and exact-compares the legacy
  mapping only when that issue number is present there.

Obsidian remains the planning source. Changes to a materialized note must be
applied to the Obsidian source and this snapshot in the same change.

Do not add workstation-local absolute paths to GitHub issues or committed
documentation.
