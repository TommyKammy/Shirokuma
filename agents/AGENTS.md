# Agent runtime scope

The root `AGENTS.md` applies here. Agent behavior must remain inside repository policy and authorization boundaries.

- Use the root ADR-0034 local experimental scope; its standing approval replaces
  legacy GitHub PR/CI/CODEOWNERS scheduling gates for local work only. Preserve
  authenticated scope checks and all separately authorized external-delivery gates.
- Treat missing provenance, authorization, or scope as a blocking condition.
- Keep tests deterministic and independent of workstation-local secrets.
