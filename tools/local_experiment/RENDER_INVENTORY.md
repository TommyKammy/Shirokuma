# Commit-bound inhibited render inventory (WP-L0-GITOPS-001)

Use `render_inventory.build(root, candidate, private_staging_directory)` with an
already validated `source_candidate` contract. No CLI fetch/apply path exists.
Save the returned record with exclusive private 0600 creation. The renderer uses
the installed `kubectl kustomize`, hashes its executable, enables no plugins and
receives only files extracted from available commit objects in a temporary tree.
Git replacement objects/lazy fetch are disabled. Dirty checkout files are ignored.

The conservative supported dialect covers local resources, inline/file patches,
replacements, namespace and ConfigMap file/literal generation. Unknown fields,
external inputs, repository escapes, symlinks/submodules, missing/ambiguous config,
cycles, executable generators, external Flux substitutions and nested Flux inputs
remain explicit gaps; an incomplete closure is never sent to the renderer.
Simple `${NAME}` substitutions use the root-rendered Flux postBuild constants;
other syntax is a gap, not an assertion of complete Flux-controller equivalence.
This is intentionally not a generic substitute for Flux/Kustomize execution.

All seven configured paths, including resources outside the root path and
generated source files, bind exact blob/SHA256 inputs and canonical render hashes.
Resource identities/hashes, every standard workload and normal/init/ephemeral
container image occurrence are inventoried. Unknown custom image-bearing workloads,
charts and workloads lacking safe inhibition cannot qualify as complete. Duplicate
resources/keys and controller pin drift reject. Workload projections keep the four
existing controllers at one, other replica owners zero and Jobs/CronJobs suspended.
Flux projections keep all reconcilers suspended and source identity pinned with
**unselected** transport. Only hashes are returned: neither committed renders nor
projections are an applyable manifest. The committed root remains unsafe to apply.

Repository inventory completeness is separate from runtime inputs: referenced
Secrets/ConfigMaps/PVCs/service accounts may be unavailable, StatefulSet PVCs are
runtime-generated, and image bytes are not fetched/verified. Image digest syntax
does not establish scan/publisher/architecture/admission acceptance for app images.
The inventory covers configured candidate paths, not arbitrary observed workloads.

`validate` regenerates exactly. `bind_admission` first revalidates the existing
non-activating contract against fresh inhibited observations, then binds the full
inventory/hash. All original strict findings and validator outcomes remain intact;
local risk decision stays missing/null, admission/activation/gate false, fetched
and applied provenance null. No exception renewal, transport selection or runtime
write is permitted. Parent independently reviews any successor proposal.

Run `make PYTHON=tools/local_experiment/llm/.venv/bin/python test-local-render-inventory`
(the existing project environment provides PyYAML). Rollback only repository
implementation/tests/docs/Make target. Temporary extracted committed text and
private JSON/logs have small disk impact; no resident component, retained-volume,
backup/export or runtime change. Never restore writers or unsuspend Flux/Jobs.
