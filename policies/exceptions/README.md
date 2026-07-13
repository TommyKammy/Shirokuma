# Policy exceptions

Policy exceptions are disabled by default. A temporary exception must be a
`policies.kyverno.io/v1` `PolicyException` JSON file in this directory and
must pass `scripts/verify_policy_exceptions.py`.

Required annotations:

- `shirokuma.dev/exception-owner`
- `shirokuma.dev/exception-reviewer` (different from owner)
- `shirokuma.dev/exception-issue` (a Shirokuma GitHub issue URL)
- `shirokuma.dev/exception-expires-at` (RFC 3339, no more than 30 days ahead)
- `shirokuma.dev/exception-reason`

`spec.policyRefs` must name individual `ValidatingPolicy` resources without
wildcards, and `spec.matchConditions` must narrowly select resource metadata.
Enabling PolicyException support in a live Kyverno deployment requires a
separate reviewed change after Kyverno controller images pass admission.
