# SF10 storage memory override

On 2026-09-16 the second SF10 run failed while closing a Parquet file. SeaweedFS
was OOMKilled (exit 137) at 09:40:10 UTC with its original 2 GiB memory limit.
Trino's S3 connection failures occurred at the same time. This was a runtime
storage failure, separate from the earlier Q15 floating-point equality defect.

`flux-patches.json` contains the narrowly scoped memory override: SeaweedFS
request 1 GiB, limit 8 GiB. CPU remains 250m requested / 2 limited. The Colima VM
remains 16 CPUs / 96 GiB. No image, PVC, credential, storage path or host setting
changes. The dedicated Flux child `shirokuma-object-storage` uses this value as
`spec.patches`, so its normal reconciliation maintains the local resource limit.
The root `flux-system` Kustomization remains intentionally suspended; the storage
child continues reconciling and becomes Ready normally.

Before applying, save the live Kustomization and StatefulSet, verify this fixed
context and object identity, and merge with any preexisting child patches rather
than overwriting them. This application found no preexisting `spec.patches`.
It used a JSON Patch testing the current UID and resourceVersion, followed by
server dry-run and the same atomic patch. Live private receipts are under
`tmp/local-expansion/{storage-flux-before,storage-flux-patch,seaweedfs-before,seaweedfs-after}.json`.

Wait for the child's new observedGeneration, Ready condition and StatefulSet
rollout before retrying the benchmark. The new pod was Ready with restartCount 0
and the exact 8 GiB cap. `make local-platform-status` then passed for all five
component groups. Benchmarks retain separate query/correctness evidence.

Rollback removes only this exact local patch after checking that it has not been
edited and preserving any later patches. Use UID/resourceVersion tests again.
The child then reconciles its source's former 512 MiB request / 2 GiB limit.
Do not roll back during an SF10 write, because that limit reproduced the OOM.
Restoring the root Flux reconciliation is a separate handoff and may also remove
the child's local override. Neither rollback deletes a PVC or data.

The failed CTAS had not registered a table, yet its failed rollback left 15
incomplete multipart uploads. The exact generated run prefix was inspected,
those 15 uploads alone were aborted, and readback showed no uploads or completed
objects there. SQL `SHOW TABLES` alone does not prove object-store cleanup.
The failed run and `orphan-cleanup.json` receipt remain in its private evidence
directory; existing datasets, other prefixes and retained backups were preserved.
