# Disposable Keycloak JWT / Trino identity experiment

This local experiment verifies a small authentication and authorization path:
Keycloak issues short-lived RS256 bearer tokens over HTTPS; Trino 483 verifies
the signature, issuer, audience, and mapped username; OPA applies the fixed
TPCH row-filter and column-mask policy. It uses five synthetic users and one
synthetic client. It does not connect to the retained Trino, catalogs, storage,
credentials, or application users.

The scope is JWT bearer authentication and authorization for fixed usernames.
Browser SSO, OAuth2 browser redirects, realm-role propagation, token revocation,
signing-key rotation, multi-node internal TLS, and production readiness remain
unverified. Trino's JWT authenticator does not automatically pass Keycloak realm
roles to OPA. A token's `exp` is checked when present; this experiment requires
the actual Keycloak tokens to have a short expiry and tests an expired token,
but does not claim that Trino's JWT configuration requires every signed token
to contain `exp`.

## Isolation and cryptographic boundaries

The run creates one fresh UUID-named NetworkPolicy, immutable ConfigMap,
immutable Secret, and Pod in `shirokuma-dev` on `colima-mac-studio-solo`.
The Pod contains native ARM64 Trino, OPA, and Keycloak. Resource limits total
4.5 CPU and 6.25 GiB: Trino 2 CPU / 4 GiB, OPA 500m / 256 MiB, and Keycloak
2 CPU / 2 GiB. Keycloak uses a 512 MiB heap. Four bounded `emptyDir` volumes
provide 3 GiB total temporary/data capacity. There is no PVC or Service.

Each run generates a private TLS CA, a localhost server certificate, an
independent realm signing key, temporary passwords/client secret, and an
internal Trino shared secret. The realm imports the signing key; Trino mounts
only its public PEM. This avoids a startup dependency on JWKS retrieval.
Signing-key rotation and JWKS refresh are not exercised.

Keycloak's issuer is exactly
`https://localhost:18443/realms/shirokuma-lab`; Trino's client endpoint is
`https://localhost:18444`. A kubectl tunnel binds those two host ports only on
127.0.0.1. The dedicated Python client trusts only this run's CA and verifies
the certificate hostname. No host keychain, global truststore, hosts file, or
TLS verification setting is changed. Redirects and proxies are disabled.
An occupied host port is refused, not replaced.

OPA listens on same-Pod loopback. Trino's internal discovery/exchange uses
same-Pod HTTP with a fresh internal shared secret; client authentication over
HTTP is explicitly disabled, and forwarded-protocol headers are not trusted.
NetworkPolicy denies ingress and egress. Containers are non-root, have
read-only root filesystems, drop all capabilities, and use `RuntimeDefault`
seccomp. Automatic service-account token mounting and service environment
injection are disabled. The Secret is projected into only the containers and
files that require it; OPA receives no mounted secrets.

## Fixed verification cases

Every query uses the same host-owned SQL:

```sql
SELECT nationkey, name FROM tpch.tiny.nation ORDER BY nationkey
```

| Cases | Evidence required |
| --- | --- |
| `admin_before`, `admin_after` | Actual Keycloak token; all 25 original rows; positive controls around the negative probes |
| `row` | Actual Keycloak token; only nation key 0 / ALGERIA |
| `mask` | Actual Keycloak token; all 25 keys with name MASKED |
| `both` | Actual Keycloak token; key 0 with name MASKED |
| `denied` | Valid Keycloak token authenticates, then OPA denies `ExecuteQuery` |
| `fixture_control` | Host-signed, otherwise valid token using the same imported realm key; all 25 rows |
| `no_token` | Administrator user header without bearer token gets HTTP 401 |
| `bad_signature` | Otherwise valid claims signed by a different key get HTTP 401 |
| `wrong_issuer`, `wrong_audience`, `expired` | Same-key signed fixtures differing in the named claim get HTTP 401 |
| `unknown_principal` | Same-key signed unregistered username fails the fixed mapping with HTTP 401 |
| `forge_user` | Masked user's valid token plus administrator `X-Trino-User` is denied by OPA `ImpersonateUser` |
| `forge_original` | Masked user's valid token plus administrator `X-Trino-Original-User` is also denied |

There are 15 fixed cases. Authentication rejection requires HTTP 401 with
Trino's JWT bearer challenge and no fresh OPA decision. Authenticated SQL
authorization rejection is a QueryResults `PERMISSION_DENIED`, normally carried
over HTTP 200; HTTP 403 alone is not accepted as proof of the intended gate.

The host independently verifies the five actual Keycloak tokens with the public
PEM, checks issuer/audience/username/issuance/expiry, and records only safe
projections and hashes. Their provenance is distinct from the host-signed
validation control and negative fixtures. The client sends no user header in
the normal cases, so the effective username comes from the verified JWT mapping.

OPA decision IDs are checked after each serial case. Non-null query IDs must
match that query and its expected username. Queryless `ExecuteQuery` and
`ImpersonateUser` decisions are associated with the serial case window; a single
decision cannot satisfy both header-forgery cases. Impersonation also requires
the exact source user, target user, and deny result. Trino and Keycloak readiness
are verified before and after the probes, with safe timestamps and TLS checks
retained in the receipt.

## Commands and evidence

Build the pinned Keycloak image locally; the already verified Trino and OPA
images are reused. No image is published:

```sh
rtk proxy python3 tools/local_experiment/trino_identity/build.py
```

Use the returned 32-hex build token:

```sh
rtk proxy python3 tools/local_experiment/trino_identity/run.py plan --build-token BUILD_TOKEN
rtk proxy python3 tools/local_experiment/trino_identity/run.py run --build-token BUILD_TOKEN
rtk proxy python3 -m unittest discover -s tests -p 'test_local_trino_identity*.py'
```

`plan` creates private fixtures and a private manifest plan but no runtime
objects. `run` always generates a fresh run and fresh fixture keys. Both modes
write under `tmp/local-experiment/trino-identity/runs/<UUID>`; build evidence is
under the sibling `builds` directory. Directories are 0700 and files 0600.
The plan and fixtures contain disposable secrets and must remain local and
outside Git. Normal receipts contain only hashes, safe identity projections,
fixed result classifications, counts, and OPA decision projections. On failure,
bounded owned-Pod diagnostic logs are redacted for known secrets, bearer JWTs,
and PEM private keys before private storage.

Pending requests are journaled before dispatch. A create response's UUID and
object identity are saved before full content validation, so a defaulting
mismatch does not lose a UID actually returned by the API. A lost create
response never causes a later GET to be adopted as proof of ownership.

Cleanup removes only objects with a saved, confirmed UID after checking identity
and content. Deletion has UID/resourceVersion preconditions and orphan
propagation. The Pod must disappear before its Secret, ConfigMap, or isolation
policy is removed. Unknown UIDs are deferred; replaced or modified objects are
preserved and reported. Failed and interrupted run receipts are retained.

```sh
rtk proxy python3 tools/local_experiment/trino_identity/run.py recover --run-token RUN_TOKEN
```

Recovery validates the saved private plan's UUID names, namespace, images,
sealed content hashes, fixed volume/security/resource scope, and receipt UIDs.
It does not regenerate manifests or require today's build inputs/configuration
to match. It writes a separate `recovery.json` and preserves the original run
receipt. A run lock prevents concurrent run/recovery. Exit 0 means all required
checks for the command passed; failures, unknown cleanup, and interruption
return nonzero. No broad image prune, retained-data deletion, or cluster reset
is performed.

### Explicit live telemetry

The standard `run` remains a standard-library experiment. To observe a new run
in the existing AgentHouse and Loki services, use the pinned local LLM Python
environment (which includes OpenTelemetry) and explicitly opt in:

```sh
rtk proxy tools/local_experiment/llm/.venv/bin/python tools/local_experiment/trino_identity/run.py run --build-token BUILD_TOKEN --telemetry
```

`--telemetry` is accepted only for a fresh `run`; it does not import earlier
receipts or change recovery. Actor `local-trino-identity` records one event per
case and a final experiment event. Case durations include query dispatch,
polling, result verification, and OPA audit collection; they are not SQL engine
latency or original OPA decision timing. Expected authentication and policy
rejections remain blocked events, with expectation matching recorded separately.

Only fixed case/result classifications, query/decision IDs, synthetic principal
names, correlation types, and counts reach telemetry. Bearer tokens, credentials,
result rows, and raw OPA logs are excluded. Private `telemetry.json` journals
pending delivery and independent AgentHouse/Loki readback; `identity-spans.jsonl`
retains this run's OTel spans without modifying the process-global provider.
The experiment does not pass until all case observations, cleanup, final
delivery, and saved spans are verified. A delivery or readback failure stops
later cases, still attempts cleanup of all confirmed UIDs, and leaves a failed
receipt. Cleanup outcomes remain recorded even when the experiment fails or is
interrupted. Existing local services are required; the command does not start
or recreate them.

The 2026-09-17 local run `8121b529b1844086bfa489a1336a7b60` passed all
15 cases and retained 61 OPA decisions: 52 correlated by query ID and user,
and 9 by the serial case window and user/operation. Five actual Keycloak tokens
had independently verified signatures and 300-second lifetimes. All six
authentication-negative cases stopped before OPA, both header forgeries were
denied, all four readiness observations passed, and all four created UIDs were
removed. The retained runtime baseline remained unchanged.

The first run, `3da115d1df454b10a139086a6460bacc`, is preserved as failed.
Its token signature passed, but required-claim validation failed. The minimal
realm omitted default client scopes and an explicit subject mapper. Adding Keycloak's explicit
`oidc-sub-mapper` restored the subject without weakening validation. That run
also removed all four created UIDs. Raw tokens and claim values were not added
to normal receipts to diagnose it; fixed claim-error classifications were added
instead.

The telemetry run `b94184c624174f0c8f01fc00cae8d7d7` also passed the 15 cases,
with 16 verified Pawprints, Loki records, and local OTel spans. Seven events
were successful and nine blocked; only the three verified OPA denials had
`blocked_by_policy=true`. The existing MCP `agenthouse_recent` tool returned
all 16 events with matching metadata and NULL model usage. Its read itself
added one separate Gateway audit event. The existing Grafana Task/Trace filter
showed all nine blocked cases. No additional model request was made.

## Primary sources

- [Trino 483 JWT configuration](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/jwt.md)
- [JWT authenticator and username mapping](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/server/security/jwt/JwtAuthenticator.java)
- [Trino 483 session impersonation checks](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/server/QuerySessionSupplier.java)
- [OPA impersonation enforcement](https://github.com/trinodb/trino/blob/483/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java)
- [Keycloak 26.7.4 imported RSA provider](https://github.com/keycloak/keycloak/blob/26.7.4/services/src/main/java/org/keycloak/keys/AbstractImportedRsaKeyProviderFactory.java)
- [JJWT 0.13.0 expiry validation](https://github.com/jwtk/jjwt/blob/0.13.0/impl/src/main/java/io/jsonwebtoken/impl/DefaultJwtParser.java)
