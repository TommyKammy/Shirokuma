# Local Policy Gatekeeper Bear

This bounded WP-L2-POL-001 experiment asks the existing local Qwen model to
classify 15 fixed Kubernetes candidates and ten local development actions.
The host compares those classifications with separately held expectations,
then verifies every Kubernetes candidate with a server-side dry-run. Neither
the model nor an admission allow result grants authorization.

```sh
rtk make local-policy-bear-eval
rtk make local-policy-bear-smoke
```

The observer requires the dedicated Shirokuma context, the eight owned native
policies and eight bindings, current-generation type checking, and unchanged
source and resource identities. Model inputs contain neutral candidate IDs and
bounded features. They omit expected decisions, raw manifests, names and
policy messages. Three batches of five native candidates and one batch of ten
boundary candidates fit the existing 8 KiB request and 512 output-token bounds.

The typed response contains violation, scope-limit and verification IDs only.
Unknown IDs, omitted candidates, duplicate candidates, authorization fields,
and disagreements with host expectations stop the workflow before native
verification. The model cannot select a manifest, namespace, candidate subset,
policy change or boundary action. The verifier always runs its fixed 15 cases.

Twelve native cases require denial by the exact owned policy, binding and
message; two protected-namespace cases must be allowed. A separate outside-
namespace control demonstrates the baseline's scope limit. An allow result
does not establish that an image is trusted or that an operation is authorized.
The image rule checks digest syntax only. Controller-template autogeneration,
ephemeral-container live probes and background policy reports are not covered.

The ten boundary cases classify two operations already within standing local
authorization and eight outside that scope. No boundary action is executed,
and successful classification adds no new approval or per-attempt gate.
Existing local development authorization continues to govern actual work.

Every native request is `--dry-run=server`: no workload is retained. A private
progress receipt records the pending candidate before each request and its
confirmed result afterward. A timeout or interruption preserves the pending
outcome as unknown. Source or identity drift, unrelated admission failures,
audit failures and incomplete classifications cannot become success evidence.

The LangGraph workflow saves its observations, four model classifications,
verification receipt and completed checkpoint. AgentHouse, Loki and saved
OpenTelemetry spans are correlated for each phase. Synthetic evaluation uses
eight fixed cases twice and performs no Kubernetes calls. All evidence remains
under private ignored `tmp/local-experiment/policy-*` directories.

No resident component is added. Stop invoking this entrypoint to stop the
experiment. Native baseline lifecycle remains in the separate policy tools;
the Bear does not apply, withdraw or replace policies. This is bounded local
functional evidence, not general policy advice, production acceptance or an
authorization service.
