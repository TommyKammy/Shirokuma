# Inhibited local-source candidate (WP-L0-GITOPS-001)

`source_candidate.build(root, full_commit, items, baseline)` produces a
repository-only JSON contract from available local Git objects and a complete
fresh inhibited-runtime observation. It has no activation CLI. Test with
`make test-local-source-candidate`.

The selected commit is exactly forty lowercase hexadecimal characters and must
already exist as a commit, with a repository tree and all seven configured path
trees. Git lazy fetching and replacement objects are disabled. Working-tree
edits and branch names cannot alter this selected identity. Each source path
must be a tree, not a missing path, blob, symlink or traversal alias.

The contract pins the candidate source `ref.commit` (no branch/tag), sets source,
root and six children `suspend=true`, and records the four observed digest-pinned
controller images/replicas=1. The existing source URL is **not** copied or
changed. Local transport remains **unselected**: no listener, registry, new
resource, fetch or source redirection is created. Selecting/validating a local
transport and any later inhibited manifest generation need separate parent
review/capability; this contract authorizes neither.

UID/spec hashes bind the observed source, reconcilers, owners, Jobs and retained
PV/PVCs. They are snapshot guards, not future clearance. The builder checks the
existing complete inhibition/readiness validator; the caller retains the raw
probe and independently reviews it. Do not promote saved Ready/artifact/applied
status into candidate evidence. `fetched_commit`/`applied_commit` stay null;
artifact-byte, transitive-render, admission and gate acceptance remain false.

This is **not a Kubernetes manifest**. The committed `local-lite` root still
contains branch-only external source configuration and omits suspension; it
must not be applied. Path-tree identity alone does not validate transitive
rendering, controller behavior, transport availability or retained-writer
safety. All Jobs remain suspended and app owners zero; no policy binding is
activated. The next dependency is read-only local-admission evaluation of this
candidate versus existing strict publisher requirements, not activation.

Store generated contracts privately with exclusive 0600 creation under ignored
`tmp/local-experiment/`; keep runtime identities and machine paths out of Git.
Recovery is repository rollback of this builder/tests/docs only. No runtime
field requires restoration; never restore app counts or unsuspend sources/Jobs
automatically. Preserve disks, volumes, PV/PVCs, backups and failure records.
