# Agent runtime scope

The root `AGENTS.md` applies here. Agent behavior must remain inside repository policy and authorization boundaries.

- Do not bypass GitHub PR, CI, policy checks, CODEOWNERS, or authenticated scope checks.
- Treat missing provenance, authorization, or scope as a blocking condition.
- Keep tests deterministic and independent of workstation-local secrets.
