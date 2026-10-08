# Retained Flux HIGH diagnosis (2026-10-08)

WP-L0-GITOPS-001 / L0_FLUX_FINDINGS_DIAGNOSIS. This is a dependency diagnosis,
not risk acceptance. `flux_findings.diagnose` binds retained ledger/SBOM/scan/
publisher bytes to candidate commit `0486612d0671c01c16d40ce445e39788d86de69b`,
checks fresh complete inhibition/readiness, and preserves full finding records,
target/class/type, multiplicity, hashes, clocks and observed controller exposure.
It cannot fetch, scan, approve, select transport or activate anything.

## Exact retained findings

S=source-controller v1.9.3, K=kustomize-controller v1.9.3,
H=helm-controller v1.6.2, N=notification-controller v1.9.2.
All references remain the exact ARM64 digests in `security/resident-images.json`.
Versions below are **retained scanner claims**, not independently refreshed
advisories or proof that a currently available Flux image contains the fix.

| Finding ID | Package | Affected installed version / controllers | Recorded fixed version |
| --- | --- | --- | --- |
| CVE-2026-71556 | github.com/go-git/go-git/v5 | v5.19.1 / S,N | 5.19.2 |
| CVE-2026-49478 | github.com/sigstore/fulcio | v1.8.5 / S | 1.8.6 |
| CVE-2026-56852 | golang.org/x/text | v0.38.0 / S,K,H,N | 0.39.0 |
| GHSA-hrxh-6v49-42gf | google.golang.org/grpc | v1.81.1 / S,K,N; v1.80.0 / H | 1.82.1 |
| CVE-2026-50163 | oras.land/oras-go/v2 | v2.6.1 / S,H | 2.6.2 |
| CVE-2026-39821 | stdlib | v1.26.5 / S,K; v1.26.4 / H,N | 1.25.13, 1.26.6, 1.27.0-rc.3 |
| CVE-2026-39822 | stdlib | v1.26.4 / H,N | 1.25.12, 1.26.5, 1.27.0-rc.2 |
| CVE-2026-46600 | stdlib | v1.26.5 / S,K; v1.26.4 / H,N | 1.26.6, 1.27.0-rc.3 |
| CVE-2026-5773 | libcurl | 8.19.0-r0 / K | 8.20.0-r0 |
| CVE-2026-6276 | libcurl | 8.19.0-r0 / K | 8.20.0-r0 |

Totals S/K/H/N = HIGH7/6/6/6, CRITICAL0 (25 occurrences, 10 IDs).
Go module/stdlib findings target `usr/local/bin/<controller>`; libcurl findings
target the K Alpine 3.23.5 result. No package path is recorded in these findings;
that absence must not be converted into an inferred executable path for libcurl.

## Exposure and uncertainty

Fresh dedicated observations at 05:59:12 and 06:00:32 UTC passed all 51-object
readiness/inhibition checks. Four pinned controllers remain replicas1 with Ready
Pods; source/root/six children and three Jobs suspended, retained app owners0.
No startup or runtime writes occurred. Suspension inhibits these reconciliations,
**not** running controller code, API watches, HTTP listeners or other callers.

Namespace-scoped Service/Endpoints/Ingress/NetworkPolicy inspection found three
ClusterIP Services: source artifact HTTP and notification events/webhook HTTP.
Ports: S9090, N9090/9292, all controllers metrics8080/health9440; H/K have no
matching Service in this response. No Ingress in this namespace response.
Controllers use their own service accounts and watch-all-namespaces=true.
Container security contexts drop capabilities, require non-root, prohibit
privilege escalation and use read-only root filesystems/RuntimeDefault seccomp;
S still has writable data/temp and others writable temp volumes.
Policies allow egress, same-namespace ingress, metrics from any namespace and
notification ingress from any namespace. This is configured exposure, **not**
verified packet reachability/enforcement or proof of no external ingress. Other
namespaces, host forwarding, actual gRPC/xDS listeners and controller RBAC rules
were not investigated. HTTP/metrics ports do not themselves establish gRPC use.

Retained advisory descriptions identify conditions, not observed exploitation:

- go-git: malicious repository symlink/worktree operations. S source suspension
  inhibits the configured source reconcile, not every possible go-git caller;
  N package presence does not prove worktree operations are used.
- Fulcio: OIDC discovery redirects/token attachment. S service account is present,
  but this does not prove the vulnerable Fulcio discovery code executes.
- x/text: invalid UTF-8 normalization iterator; untrusted text reaching that API
  is unproved. grpc: HTTP/2 reset and xDS RBAC paths; active vulnerable server/
  xDS use is unproved in every controller.
- oras: crafted tar hardlinks during unpack with the relevant unpack annotation;
  S/H dependency presence does not prove that unpack path is exercised.
- stdlib: IDNA hostname processing, os.Root symlink/trailing-slash handling and
  malformed DNS SVCB/HTTPS parsing. Exact vulnerable calls/inputs are unproved.
- libcurl: SMB connection reuse and stale custom Host/cookie state on reused easy
  handles. K's package presence is not evidence of curl invocation or these modes.

No exploitability conclusion, exception or risk decision follows from inhibition,
non-root settings or lack of demonstrated vulnerable calls. Strict rejection stays.

## Freshness and concrete dependency

Retained scan CreatedAt values are 2026-08-14 05:43:24–05:43:43 UTC
(stored with +09:00 offsets). DB UpdatedAt is
2026-08-14T01:10:44.597550261Z, scanner trivy0.72.0. At the 2026-10-08
06:00:32 UTC observation, scans are about 55.012 days old and DB55.201 days old.
Commit/hash agreement proves identity and retention, **not** current advisories,
fetched image bytes, fresh signatures or valid approval. Doctor remains degraded.

Concrete held dependency: independently select a separately authorized refresh
mission for **each of the four exact ARM64 digests**, recording current scanner/DB
identity, scan timestamp, full target/package/finding records, SBOM and publisher
binding. If findings persist, separately establish new immutable Flux image pins
whose ARM64 SBOM/build evidence replaces x/text0.38.0 with the recorded0.39.0 fix,
grpc1.80.0/1.81.1 with1.82.1, go-git5.19.1 with5.19.2, Fulcio1.8.5 with1.8.6,
oras2.6.1 with2.6.2, Go1.26.4/1.26.5 with the stable1.26.6 fix branch, and
K libcurl8.19.0-r0 with8.20.0-r0 (or an independently verified fixed successor).
These are constraints to verify, **not** authorized upgrades or a guessed digest.
No replacement digest is established by retained evidence. Current diagnosis
does not authorize refresh, build/pull, rescan or policy/runtime activation.

`riskDecision=null`, strict HIGH rejection, admission/activation/full L0 false.
The old admission/repository scopes remain exhausted/held; parent must review
the exact terminal worker and evidence before any new mission. No gate pass.
