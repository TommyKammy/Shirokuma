# Local Policy candidate CLI

Check one Pod JSON against the protected `shirokuma-dev` native policy baseline.
The host validates the input, sends safe boolean features to the existing local
Qwen profile, compares its classification with the host's complete expected
violation set, then submits the same in-memory body once with
`kubectl create --dry-run=server`. No workload is persisted or authorized.

```sh
rtk go build -o tmp/shirokuma ./cmd/shirokuma
rtk proxy tmp/shirokuma policy check --pod-stdin < candidate.json
rtk proxy tmp/shirokuma policy status <evidence-token>
```

Both commands require the `local-experiment` profile and the existing dedicated
Python environment. `--repo-root` selects this checkout when launched elsewhere.
There are no arbitrary namespace, endpoint, prompt, policy or shell options.

The input is one UTF-8 JSON document, at most 16 KiB, with `apiVersion: v1`,
`kind: Pod`, a DNS-label name and exactly one named container with an image.
Unknown fields, duplicate keys, nulls, nonfinite numbers and malformed values
are rejected before model/API access or durable invocation intent. YAML,
multi-object input, arbitrary commands/arguments, environment variables, Secret
references, PVCs, volume mounts and arbitrary metadata are outside this subset.
See `policy/candidate.py` for the exact supported fields and quantity grammar.

The host supplies a generated dry-run name, the fixed namespace,
`restartPolicy: Never`, `automountServiceAccountToken: false` and
`imagePullPolicy: Never`; explicit conflicting values are rejected. Each actual
transformation is recorded. Supported privileged, host-namespace, `/tmp`
hostPath, hostPort, missing resource and nondigest image violations are preserved.
Limits without their matching requests and hostNetwork ports without explicit
equal hostPorts are unsupported because Kubernetes defaulting changes their
policy features. The protected namespace must have no LimitRanges.

Before and after the dry-run, the host verifies the namespace UID/selector,
16 native policy/binding identities, generations, source/spec/owner hashes and
readiness, and confirms the generated Pod is absent. It fsyncs a pending receipt
before its sole request. Timeout, foreign denial, malformed response or changed
baseline stays unknown; it never causes an automatic retry or deletion.
The API may report only a nonempty subset of the full expected violation set.
Only denials naming the exact current policy, binding and message are accepted.

The evidence token names `tmp/local-experiment/policy-candidate-bear/TOKEN`;
invocation intent and lock are separate under `policy-cli/TOKEN`. The workflow
token identifies the audit task. Allocation alone is not proof of invocation.
Do not remove linked or retained paths to recover from a rejected output path.
Output directory creation traverses components with `O_NOFOLLOW` and uses
parent descriptors for each `mkdir`, rejecting symlink redirection even if a
parent is swapped after preflight. It rechecks pathname identity before using
the new directory. Later evidence writes still use pathnames, so continuous
adversarial swaps *after* this check are not covered; do not treat directory
creation as a full race-proof transaction.
Raw input, names, images, API responses, model text and exception messages are
not retained. Private receipts contain hashes, transforms, safe features, the
host answer, validated plan, admission result and four local audit spans.
The host answer and hashes are not passed to the model.

`status` makes no API/model calls and changes no evidence. It validates saved
features, classifications, typed admission evidence, audit/span correlation and
sealed file hashes. It cannot reconstruct the original manifest, re-observe
admission or independently prove that the retained hashes represent that input.
There is no fallback to the separate historical fixed 15-case corpus.

For `check`, exit 0 means verified admission allowed; exit 2 means the workflow
passed and verified admission denied; exit 1 means failed/unverified execution
or invalid input. For `status`, exit 0 means saved/journal state was inspected,
including denied, failed, busy or unknown states; missing/invalid evidence exits
1. Cancellation uses 130/143 when established. Only the owned child is signaled,
with up to 240 seconds for completion; no SIGKILL, resume or automatic resubmit.

Digest checks establish syntax only, not signature/CVE approval. A dry-run does
not test image availability, scheduling or application behavior. The model and
successful admission never grant authorization or production acceptance.
