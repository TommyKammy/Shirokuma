# Policy scope

The root `AGENTS.md` applies here. Use OPA and native RBAC for the default policy path.

- Do not add Apache Ranger unless an ADR changes the architecture.
- Fail closed when identity, provenance, authorization, or scope is missing or malformed.
- Add focused policy tests for allow and deny paths.
