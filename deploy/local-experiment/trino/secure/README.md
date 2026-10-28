# Authenticated single-host lab

This opt-in overlay requires passwords at Trino itself and applies service-role
file access control. The base manifest remains the explicit unauthenticated local
lab. Do not enable multi-user access until both Workbench and SQL endpoint select
backend credentials from the authenticated caller role.

The three fixed service users are `shirokuma_admin`, `shirokuma_engineer`, and
`shirokuma_viewer`. Viewer can read Iceberg marts and filtered metadata. Engineer
can write raw/sandbox and read marts/tpch_tiny. Only admin can publish marts,
manage other roles' queries, or invoke connector procedures/functions. Requests
cannot impersonate another service user. Users sharing one role share that
backend identity; application audit records retain the authenticated person.

## Image supply and database guard

The active supply is the full official Trino 483 linux/arm64 image, pinned
directly in this overlay. It uses `/usr/lib/trino/bin/run-trino` and includes
password and OPA plugins. The local `Dockerfile` and
`tools/local_experiment/trino_auth_image.py` restoration recipe are historical;
password plugin restoration is not needed for this image. The parent preloads
the pinned image into the node; `imagePullPolicy: Never` remains required.

`iceberg` uses Polaris realm SALES / catalog `shirokuma_l1`; `finance` uses
the separate `polaris-finance` endpoint, realm FINANCE / catalog `finance`.
Create service-principal Secrets `polaris-trino-credentials` and
`polaris-finance-trino-credentials`, each with `client_id` and `client_secret`.
S3 Secrets `seaweedfs-s3-sales-credentials` and
`seaweedfs-s3-finance-credentials` each require `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `S3_REGION`. No root principal or shared S3 fallback
is used. Finance remains admin only under the unchanged file ACL.

The sidecar uses the official OPA 1.20.2 linux/arm64 image from
`docker.io/openpolicyagent/opa`, pinned by digest in `deployment.yaml`.
Both file and OPA access controls must allow each request. File rules remain
authoritative for ordinary access; the loopback OPA sidecar additionally limits
all table procedures (including future/unknown names) to admin in iceberg/finance
or engineer in iceberg raw/sandbox. Viewer cannot execute table procedures.
Unsupported OPA permission-management checks delegate to the file ACL. OPA has
no fail-open fallback, request/decision logging, Service, or externally exposed
port; its API permits only the decision POST and health GET. Static mounted
configuration/policy edits require an explicit rollout restart.

## Prepare credentials

From the repository root, create new credentials in the ignored state directory:

```sh
rtk proxy python3 tools/local_experiment/trino_auth.py init var/shirokuma/trino-auth
```

This requires Apache `htpasswd` with bcrypt support and OpenSSL. Random passwords are sent to
`htpasswd` over stdin; files are mode 0600 and the directory is 0700. The command
refuses an existing directory. Keep `backend.json` on the application host only.
It contains exactly three role entries, each with `username` and `password`.
The generator also creates a 365-day localhost TLS certificate and private key.
Deploy only the bcrypt password database, cluster shared secret, and combined TLS PEM:

```sh
rtk proxy kubectl --context colima-mac-studio-solo -n shirokuma-dev create secret generic trino-local-auth --from-file=password.db=var/shirokuma/trino-auth/password.db --from-file=shared-secret=var/shirokuma/trino-auth/shared-secret --from-file=tls.pem=var/shirokuma/trino-auth/tls.pem
rtk proxy kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/local-experiment/trino/secure
```

The load option permits reuse of the three parent resource manifests and common
config files. Confirm the pinned password-enabled image is present in the node's
runtime store before applying. The overlay contains no credential values or
version upgrade. Coordinate the
Trino rollout and application restart together. A missing Kubernetes Secret or
missing password database prevents the secure pod from becoming ready.

Set both application processes to:

```sh
export SHIROKUMA_TRINO_AUTH_MODE=password
export SHIROKUMA_TRINO_AUTH_FILE=/absolute/path/to/var/shirokuma/trino-auth/backend.json
export SHIROKUMA_TRINO_CA_FILE=/absolute/path/to/var/shirokuma/trino-auth/tls.crt
```

The credential helper reloads this file for every request and rejects missing,
malformed, public-readable, or mismatched-role configuration. It removes client
authentication/impersonation headers and sends the selected service credential.
The default `SHIROKUMA_TRINO_AUTH_MODE=local` preserves the disposable lab mode;
it must not be used to claim authenticated shared access.

## Transport and acceptance

The overlay denies ingress from all other pods; local applications use the
existing `kubectl port-forward --address 127.0.0.1` path. Service port 8080 maps
to the pod's HTTPS port 8443. Clients must use `https://127.0.0.1:<forwarded-port>`
and verify the generated certificate using `backend_ssl_context()`. SQL over the
pod's HTTP port is rejected; existing public `/v1/info` health probes still work.
Do not set `allow-insecure-over-http=true`: Trino 483 selects its insecure
authenticator for HTTP in that mode, permitting claimed usernames and rejecting
passwords. It does not enable password authentication over plaintext HTTP.
This certificate covers localhost only. Use a properly issued certificate with
the deployment hostname for a network deployment. Restrict Kubernetes
exec/port-forward and Secret permissions to operators.

After coordinated activation, prove on the real backend: missing/wrong password
rejects SQL; viewer reads marts but cannot read raw/sandbox, mutate marts, read
`system.runtime.queries`, or impersonate admin; engineer writes raw/sandbox but
cannot mutate marts; admin retains publication and monitoring. Repeat through
both application entrypoints with their three authenticated users. Verify marts
views only expose intentionally published data. These checks are required before
asserting the backend boundary is active.

Trino 483 references: [password hashes and authenticator](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/password-file.md),
[HTTP properties and shared secret](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/admin/properties-http-server.md),
[secure authentication selection](https://github.com/trinodb/trino/blob/483/core/trino-main/src/main/java/io/trino/server/security/AuthenticationFilter.java),
[ACL semantics and impersonation](https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/security/file-system-access-control.md).
