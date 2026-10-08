# Complete inventory admission diagnosis

`inventory_admission.diagnose` regenerates the commit-only inhibited render
inventory and candidate against fresh complete scoped observations, then checks
every image occurrence against an exact-reference ARM64 resident ledger row.
The ledger and SBOM/scan/publisher artifact bytes must match candidate-commit
blobs. Missing/ambiguous ledger identities remain per-occurrence gaps; invalid
or unavailable artifacts fail closed rather than producing acceptance.

The current candidate has nine occurrences, eight distinct image references:
four Flux controllers, Polaris, Polaris-admin, PostgreSQL, Polaris again in the
Iceberg bootstrap Job, and SeaweedFS. Do not deduplicate away workload evidence.
Exact HIGH/CRITICAL records, artifact hashes, scanner/advisory dates, publisher
identity and canonical retained-bundle branch selection are recorded separately
from fresh cryptographic or image-byte verification, which are always false.

Repository-unavailable runtime references are retained with workload scope.
Their absence from rendered repository resources does not prove live absence;
no Secret values are read. Local risk decision remains missing/null and all
runtime-input completeness, admission, activation and gate flags remain false.
There is no CLI for fetching, selecting transport, applying manifests or policy.

Run `make PYTHON=tools/local_experiment/llm/.venv/bin/python test-local-inventory-admission`.
Tests use synthetic owner observations and real retained repository evidence,
not runtime acceptance. Private live observations must be collected separately.
