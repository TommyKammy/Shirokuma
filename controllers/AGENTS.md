# Controller scope

The root `AGENTS.md` applies here. Controllers target Linux/arm64 on Colima built-in k3s.

- Do not assume a multi-node physical cluster.
- Keep reconciliation boundaries explicit and cover failure and rollback behavior with tests.
- Update manifests and ARM64 compatibility notes when controller dependencies change.
