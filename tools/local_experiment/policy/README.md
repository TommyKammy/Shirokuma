# Local admission baseline

The dedicated `colima-mac-studio-solo` k3s experiment uses eight native
`admissionregistration.k8s.io/v1` policies to enforce the seven CEL rules from
`policies/kyverno/baseline.yaml` in `shirokuma-dev`.
The public-exposure rule is split into separate Service and Ingress policies
so that each kind passes native static type checking without warnings.
See [ADR-0035](../../../docs/design/07_ADR/ADR-0035_Use_native_admission_baseline_for_local_experiments.md).

```bash
rtk make test-local-policy
rtk make local-policy-status
rtk make local-policy-apply
rtk make local-policy-smoke
```

`status` checks the dedicated object inventory, ownership, exact policy content
and current-generation CEL type checking. `apply` checks the fixed local
cluster, server-dry-runs missing objects, creates the policies, and waits for
warning-free type checking before enabling their Deny bindings. Repeating it
with the same content is safe. An unexpected owner or content change fails
without overwriting the conflicting object. The manifest must match the
repository source projection; a changed policy needs explicit implementation
and verification rather than a blind apply over existing state.

The rules reject privileged containers, host namespaces, hostPath, missing
CPU/memory requests or limits, hostPort, mutable image references, public
Services and Ingresses. Both policy and binding select only `shirokuma-dev`.
Smoke cases exercise allowed and rejected requests through the API server and
verify that each rejection identifies its expected local policy and binding.
They create no persisted Pod, Service or Ingress. Evidence is stored in private
ignored `tmp/local-experiment/policy/<run-id>/summary.json`.

To withdraw this bridge's enforcement:

```bash
rtk make local-policy-withdraw
```

Withdrawal removes only the eight owned bindings after content and identity
checks, with UID/resource-version deletion preconditions. Policy definitions
and evidence remain available; retained workloads are untouched. A partial
apply or withdrawal is reported as failure with completed steps in the receipt.
Inspect `status` and the receipt before choosing the next operation.

This local bridge adds no resident controller or image. The original Kyverno
inventory and security verdicts are unchanged. Native policies validate actual
Pod admission; they do not provide Kyverno's controller-template autogeneration
or background policy reports. An accepted Deployment may subsequently fail to
create its Pod. The selected ephemeral-container subresource matches follow
the source baseline, but the smoke does not inject ephemeral containers into
retained Pods. Image digest syntax does not establish image approval,
signatures or vulnerability status. These results concern local admission
behavior, not production acceptance.
