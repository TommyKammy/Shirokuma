# Helm scope

The root `AGENTS.md` applies here. Charts target Linux/arm64 on Colima built-in k3s.

- Do not make Ceph, Apache Ranger, or a multi-node physical cluster part of the default path.
- Prefer SeaweedFS; use MinIO only under the root fallback constraints.
- Include lint evidence and a rollback or nuke/rebuild note for infrastructure changes.
