# Shared HTTP authentication

Unconfigured Workbench and SQL endpoint are trusted single-user tools and may bind
only to loopback. `SHIROKUMA_DEFAULT_ROLE` limits this local session; request user
and role headers are selectors within that ceiling, not authentication.

For distinct admin, data engineer and BI viewer users, configure the same
`SHIROKUMA_AUTH_FILE` for both services and set `SHIROKUMA_AUTH_MODE=shared`.
The file is a JSON object mapping each login to `role`, `salt` (at least 16 random
bytes, hex encoded) and `password_hash` (PBKDF2-HMAC-SHA256, 600000 iterations,
32 bytes, hex encoded). Roles are `admin`, `data_engineer`, and `bi_viewer`.
Keep this file outside Git, readable only by the service account. Generate each
entry interactively, so the password does not enter command arguments or history:

```sh
rtk proxy env PYTHONPATH=tools/local_experiment python3 - <<'PYTHON'
import getpass, json, secrets
from request_auth import password_hash
username = input('Login: ')
role = input('Role (admin/data_engineer/bi_viewer): ')
salt = secrets.token_hex(16)
print(json.dumps({username: {'role': role, 'salt': salt,
    'password_hash': password_hash(getpass.getpass('Password: '), salt)}}))
PYTHON
```

Combine the generated entries into one JSON object. Set the environment for each
service before startup:

```sh
export SHIROKUMA_AUTH_MODE=shared
export SHIROKUMA_AUTH_FILE=/private/service-config/shirokuma-users.json
```

Browsers receive a standard Basic login challenge. Trino clients use Basic
credentials for the same account. Missing, incorrect, duplicate, or malformed
credentials and unreadable/invalid account files fail closed. The authenticated
account determines the role; `X-Trino-User` cannot impersonate another user.
A role selector may only lower that account's privileges. Workbench initializes
the role menu from the authenticated account and disables higher roles.

Basic credentials must travel over HTTPS for remote use: terminate TLS at a
trusted reverse proxy and keep these listeners privately reachable only through
that proxy. Do not expose plain HTTP listeners to a shared network. Service startup requires backend Trino password authentication, private service
credentials and a trusted CA certificate. Configure
backend Trino password authentication and role-specific service users as described
in the Trino access-control deployment configuration; protect the backend port
from direct users. The HTTP boundary alone cannot secure an independently open
Trino listener. Shared pilot acceptance still requires actual engine and client
checks with all three identities.

Data engineers may use supported writes in `raw` and `sandbox` through the SQL
endpoint; viewer writes and engineer writes into `marts`/`staging` are rejected.
Viewer SQL accepts single read statements, quoted identifiers, CTEs, joins and
comma joins against `iceberg.marts` and permitted metadata. Relation names and
aliases are consumed before clause interpretation. Table functions are rejected
because their resource access requires separate authorization semantics. Viewer
session catalog/schema are fixed to `iceberg.marts`; prepared statements and
client session/role overrides are rejected for restricted/shared requests.
Continuation and cancellation URLs require the same authenticated identity that
received them. The endpoint retains at most 4096 issued continuation paths;
expired entries and process restarts require a fresh query.
