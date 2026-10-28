"""Standalone two-catalog lab helper. See docs/guides/database-poc.md.

No command runs on import. Runtime commands require operator-owned localhost
forwards; this helper never starts services or retries a write request.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'var/shirokuma/database-poc'
NAME = 'trino-database-poc'
CONTEXT = 'colima-mac-studio-solo'
NAMESPACE = 'shirokuma-dev'
IMAGE = 'shirokuma.local/trino@sha256:f6390682c4b0ff0241ce55cfea095df8626ea8e122ced2255ca1045830df6317'
BASE_IMAGE = 'shirokuma.local/trino@sha256:7347d14eb3a3017c351c0220c811d50b556e5c8596564d8b6195276600064670'
OPA_IMAGE = 'shirokuma.local/opa@sha256:9bf48d2d88d954b562ee2090a9887814966707ec55f676f7c5127afb4f29918b'
OPA_POLICIES = ROOT / 'deploy/local-experiment/database-poc-opa'
DATABASES = ('sales', 'finance')
SCHEMAS = ('raw', 'marts', 'sandbox', 'staging')
USERS = ('poc_admin', 'sales_engineer', 'sales_viewer', 'finance_engineer', 'finance_viewer')
S3 = 'http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333'
POLARIS = 'http://polaris.shirokuma-dev.svc.cluster.local:8181'
MANAGEMENT = '/api/management/v1'
PROPERTIES = {'shirokuma.dev/scope': 'database-poc'}
PRIVILEGES = ('CATALOG_MANAGE_CONTENT', 'TABLE_READ_DATA', 'TABLE_WRITE_DATA')


class Failure(Exception):
    """Only fixed, non-secret messages may be used here."""

    def __init__(self, message, status=None, query_id=None):
        super().__init__(message)
        self.status = status
        self.query_id = query_id


def run(arguments, input_data=None):
    # Capture even successful output: kubectl/htpasswd may return credentials.
    try:
        result = subprocess.run(['rtk', 'proxy'] + arguments, input=input_data,
                                capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('subprocess unavailable or timed out; outcome unknown') from None
    if result.returncode:
        raise Failure('subprocess failed; output suppressed')
    return result.stdout


def kube(*arguments, input_data=None):
    return run(['kubectl', '--context', CONTEXT, '-n', NAMESPACE, *arguments], input_data)


def private_write(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as out:
        os.chmod(path, 0o600)
        out.write(value.encode() if isinstance(value, str) else value)
        out.flush()
        os.fsync(out.fileno())


def save(name, value):
    private_write(STATE / name, json.dumps(value, indent=2) + '\n')


def load(name):
    path = STATE / name
    if path.stat().st_mode & 0o077:
        raise Failure('private state file permissions must be 0600')
    return json.loads(path.read_text())


def prefix(db):
    return 's3://shirokuma-lakehouse/database-poc/' + db


def catalog(db):
    return 'db_poc_' + db


def catalog_definition(db, region):
    return {'name': catalog(db), 'type': 'INTERNAL',
            'properties': {'default-base-location': prefix(db),
                           'polaris.config.drop-with-purge.enabled': 'true'},
            'storageConfigInfo': {'storageType': 'S3', 'allowedLocations': [prefix(db)],
                                  'region': region, 'endpoint': S3, 'endpointInternal': S3,
                                  'stsUnavailable': True, 'pathStyleAccess': True}}


def validate_catalog(actual, expected):
    if (actual.get('name') != expected['name'] or actual.get('type') != 'INTERNAL'
            or any(actual.get('properties', {}).get(k) != v
                   for k, v in expected['properties'].items())
            or any(actual.get('storageConfigInfo', {}).get(k) != v
                   for k, v in expected['storageConfigInfo'].items())):
        raise Failure('existing PoC catalog has mismatched storage configuration')


def acl():
    admin = {'user': 'poc_admin'}
    rules = {
        'catalogs': [dict(admin, catalog='sales|finance|system', allow='all')],
        'schemas': [dict(admin, owner=True), {'owner': False}],
        'tables': [dict(admin, privileges=['SELECT', 'INSERT', 'DELETE', 'UPDATE',
                                          'OWNERSHIP', 'GRANT_SELECT'])],
        'queries': [dict(admin, allow=['execute', 'view', 'kill']),
                    {'user': '(sales|finance)_(engineer|viewer)', 'allow': ['execute']}],
        'impersonation': [],
        'system_information': [dict(admin, allow=['read', 'write'])],
        'functions': [dict(admin, privileges=['EXECUTE', 'GRANT_EXECUTE', 'OWNERSHIP'])],
        'procedures': [dict(admin, privileges=['EXECUTE', 'GRANT_EXECUTE'])],
        'authorization': [{'original_user': 'poc_admin', 'new_user': '.*', 'allow': True}],
        'system_session_properties': [dict(admin, allow=True)],
        'catalog_session_properties': [dict(admin, allow=True)],
    }
    for db in DATABASES:
        rules['catalogs'] += [dict(user=db + '_engineer', catalog=db, allow='all'),
                              dict(user=db + '_viewer', catalog=db, allow='read-only')]
        rules['tables'] += [dict(user=db + '_engineer', catalog=db, schema='raw|sandbox',
                                 privileges=['SELECT', 'INSERT', 'DELETE', 'UPDATE', 'OWNERSHIP']),
                            dict(user=db + '_(engineer|viewer)', catalog=db, schema='marts',
                                 privileges=['SELECT'])]
    rules['catalogs'].append({'allow': 'none'})
    return rules


def config_data():
    data = {
        'config.properties': '\n'.join([
            'coordinator=true', 'node-scheduler.include-coordinator=true',
            'http-server.http.port=8080', 'discovery.uri=http://localhost:8080',
            'query.max-memory=1GB', 'query.max-memory-per-node=1GB',
            'memory.heap-headroom-per-node=512MB', 'http-server.authentication.type=PASSWORD',
            'http-server.authentication.allow-insecure-over-http=false',
            'http-server.https.enabled=true', 'http-server.https.port=8443',
            'http-server.https.keystore.path=/etc/trino-auth/tls.pem',
            'access-control.config-files=/etc/trino/access-control.properties,/etc/trino/opa-access-control.properties',
            'internal-communication.shared-secret=${ENV:TRINO_INTERNAL_SHARED_SECRET}', '']),
        'node.properties': 'node.environment=database_poc\nnode.id=database-poc\nnode.data-dir=/var/trino\n',
        'jvm.config': '-server\n-Xmx2G\n-XX:+ExplicitGCInvokesConcurrent\n'
                      '-XX:+ExitOnOutOfMemoryError\n-XX:-OmitStackTraceInFastThrow\n'
                      '-XX:ReservedCodeCacheSize=256M\n-XX:PerMethodRecompilationCutoff=10000\n'
                      '-XX:PerBytecodeRecompilationCutoff=10000\n-Djdk.attach.allowAttachSelf=true\n'
                      '-Djdk.nio.maxCachedBufferSize=2000000\n-Djava.io.tmpdir=/tmp\n'
                      '--add-modules=jdk.incubator.vector\n--enable-native-access=ALL-UNNAMED\n',
        'log.properties': 'io.trino=INFO\n',
        'password-authenticator.properties': 'password-authenticator.name=file\nfile.password-file=/etc/trino-auth/password.db\n',
        'access-control.properties': 'access-control.name=file\nsecurity.config-file=/etc/trino/rules.json\n',
        'opa-access-control.properties': 'access-control.name=opa\n'
                                         'opa.policy.uri=http://127.0.0.1:8182/v1/data/database_poc/allow\n'
                                         'opa.allow-permission-management-operations=true\n'
                                         'opa.http-client.request-timeout=5s\n'
                                         'opa.http-client.connect-timeout=2s\n',
        'rules.json': json.dumps(acl(), indent=2),
    }
    for db in DATABASES:
        env = db.upper()
        data[db + '.properties'] = '\n'.join([
            'connector.name=iceberg', 'iceberg.catalog.type=rest',
            'iceberg.rest-catalog.uri=' + POLARIS + '/api/catalog',
            'iceberg.rest-catalog.warehouse=' + catalog(db),
            'iceberg.rest-catalog.security=OAUTH2',
            'iceberg.rest-catalog.oauth2.credential=${ENV:' + env + '_CLIENT_ID}:${ENV:' + env + '_CLIENT_SECRET}',
            'iceberg.rest-catalog.oauth2.scope=PRINCIPAL_ROLE:ALL',
            'iceberg.rest-catalog.oauth2.server-uri=' + POLARIS + '/api/catalog/v1/oauth/tokens',
            'iceberg.rest-catalog.oauth2.token-exchange-enabled=false',
            'iceberg.rest-catalog.http-headers=Polaris-Realm:${ENV:POLARIS_REALM}',
            'iceberg.rest-catalog.vended-credentials-enabled=false', 'fs.s3.enabled=true',
            's3.endpoint=' + S3, 's3.region=${ENV:AWS_REGION}', 's3.path-style-access=true',
            's3.aws-access-key=${ENV:' + env + '_S3_ACCESS_KEY_ID}',
            's3.aws-secret-key=${ENV:' + env + '_S3_SECRET_ACCESS_KEY}', ''])
    return data


def resource(kind, spec=None, **extra):
    version = {'Deployment': 'apps/v1', 'NetworkPolicy': 'networking.k8s.io/v1'}.get(kind, 'v1')
    result = {'apiVersion': version, 'kind': kind,
              'metadata': {'name': NAME, 'namespace': NAMESPACE,
                           'labels': {'app.kubernetes.io/name': NAME}}}
    if spec is not None:
        result['spec'] = spec
    result.update(extra)
    return result


def manifests(credentials, realm, auth, s3_credentials):
    labels = {'app.kubernetes.io/name': NAME}
    data = config_data()
    secret = dict(auth, POLARIS_REALM=realm)
    for db in DATABASES:
        secret[db.upper() + '_CLIENT_ID'] = credentials[db]['clientId']
        secret[db.upper() + '_CLIENT_SECRET'] = credentials[db]['clientSecret']
        secret[db.upper() + '_S3_ACCESS_KEY_ID'] = s3_credentials[db]['accessKey']
        secret[db.upper() + '_S3_SECRET_ACCESS_KEY'] = s3_credentials[db]['secretKey']
    env = []
    for key in ['POLARIS_REALM', 'SALES_CLIENT_ID', 'SALES_CLIENT_SECRET',
                'FINANCE_CLIENT_ID', 'FINANCE_CLIENT_SECRET',
                'SALES_S3_ACCESS_KEY_ID', 'SALES_S3_SECRET_ACCESS_KEY',
                'FINANCE_S3_ACCESS_KEY_ID', 'FINANCE_S3_SECRET_ACCESS_KEY']:
        env.append({'name': key, 'valueFrom': {'secretKeyRef': {'name': NAME, 'key': key}}})
    for key, source in [('AWS_REGION', 'S3_REGION')]:
        env.append({'name': key, 'valueFrom': {'secretKeyRef': {
            'name': 'seaweedfs-s3-application-credentials', 'key': source}}})
    env.append({'name': 'TRINO_INTERNAL_SHARED_SECRET', 'valueFrom': {
        'secretKeyRef': {'name': NAME, 'key': 'shared-secret'}}})
    container = {
        'name': 'trino', 'image': IMAGE, 'imagePullPolicy': 'Never',
        'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}},
        'ports': [{'name': 'http', 'containerPort': 8080}, {'name': 'https', 'containerPort': 8443}],
        'resources': {'requests': {'cpu': '500m', 'memory': '2Gi'},
                      'limits': {'cpu': '2', 'memory': '4Gi'}}, 'env': env,
        'volumeMounts': [{'name': 'config', 'mountPath': '/etc/trino', 'readOnly': True},
                         {'name': 'auth', 'mountPath': '/etc/trino-auth', 'readOnly': True},
                         {'name': 'tmp', 'mountPath': '/tmp'},
                         {'name': 'data', 'mountPath': '/var/trino'}],
    }
    for probe, period in [('startupProbe', 5), ('readinessProbe', 5), ('livenessProbe', 10)]:
        container[probe] = {'httpGet': {'path': '/v1/info', 'port': 'http'},
                            'periodSeconds': period, 'failureThreshold': 60 if probe == 'startupProbe' else 6}
    volumes = [{'name': 'config', 'configMap': {'name': NAME, 'items': [
        {'key': k, 'path': 'catalog/' + k if k in ('sales.properties', 'finance.properties') else k}
        for k in data]}},
        {'name': 'auth', 'secret': {'secretName': NAME, 'defaultMode': 0o440,
                                  'items': [{'key': k, 'path': k} for k in auth]}},
        {'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}},
        {'name': 'data', 'emptyDir': {'sizeLimit': '2Gi'}}]
    policy = resource('ConfigMap', data={
        name: (OPA_POLICIES / name).read_text() for name in ('guard.rego', 'api.rego')})
    policy['metadata']['name'] = NAME + '-opa'
    volumes.append({'name': 'opa-policy', 'configMap': {'name': NAME + '-opa'}})
    opa = {
        'name': 'opa', 'image': OPA_IMAGE, 'imagePullPolicy': 'Never',
        'args': ['run', '--server', '--addr=127.0.0.1:8182', '--authorization=basic', '--log-level=error',
                 '/etc/opa/guard.rego', '/etc/opa/api.rego'],
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000,
                            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}},
        'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'},
                      'limits': {'cpu': '250m', 'memory': '128Mi'}},
        'volumeMounts': [{'name': 'opa-policy', 'mountPath': '/etc/opa', 'readOnly': True}],
    }
    pod = {'automountServiceAccountToken': False, 'terminationGracePeriodSeconds': 30,
           'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000, 'runAsGroup': 10000,
                               'fsGroup': 10000, 'seccompProfile': {'type': 'RuntimeDefault'}},
           'containers': [container, opa], 'volumes': volumes}
    egress = [
        {'to': [{'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'polaris',
                                                'app.kubernetes.io/component': 'catalog-server'}}}],
         'ports': [{'port': 8181, 'protocol': 'TCP'}]},
        {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'shirokuma-storage'}},
                 'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'seaweedfs',
                                                'app.kubernetes.io/component': 'object-storage'}}}],
         'ports': [{'port': 8333, 'protocol': 'TCP'}]},
        {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}}],
         'ports': [{'port': 53, 'protocol': p} for p in ('UDP', 'TCP')]},
    ]
    return [resource('ConfigMap', data=data), policy, resource('Secret', type='Opaque', stringData=secret),
            resource('Deployment', {'replicas': 1, 'strategy': {'type': 'Recreate'},
                     'selector': {'matchLabels': labels},
                     'template': {'metadata': {'labels': dict(labels, **{
                         'shirokuma.dev/scope': 'local-experiment',
                         'shirokuma.dev/object-storage-client': 'true'})}, 'spec': pod}}),
            resource('Service', {'selector': labels, 'ports': [{'name': 'https', 'port': 8443,
                                                               'targetPort': 'https'}]}),
            resource('NetworkPolicy', {'podSelector': {'matchLabels': labels},
                     'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': egress})]


def prepare():
    # Refuse regeneration; losing client state must not silently reset identities.
    if STATE.exists():
        raise Failure('state directory exists; preserve credentials and inspect before continuing')
    STATE.mkdir(parents=True, mode=0o700)
    clients = {u: {'username': u, 'password': secrets.token_urlsafe(32)} for u in USERS}
    save('clients.json', clients)
    hashes = []
    for user, entry in clients.items():
        hashed = run(['htpasswd', '-niB', '-C', '10', user], (entry['password'] + '\n').encode()).decode().strip()
        if not hashed.startswith(user + ':$2'):
            raise Failure('htpasswd did not return a bcrypt hash')
        hashes.append(hashed)
    private_write(STATE / 'password.db', '\n'.join(hashes) + '\n')
    private_write(STATE / 'shared-secret', secrets.token_urlsafe(48))
    run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365',
         '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
         '-keyout', str(STATE / 'tls.key'), '-out', str(STATE / 'tls.crt')])
    for name in ('tls.key', 'tls.crt'):
        (STATE / name).chmod(0o600)
    private_write(STATE / 'tls.pem', (STATE / 'tls.key').read_bytes() + (STATE / 'tls.crt').read_bytes())
    save('configmap.json', resource('ConfigMap', data=config_data()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, origin, tls=None, https=False):
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme not in (('https',) if https else ('http', 'https'))
                or parsed.hostname not in ('localhost', '127.0.0.1')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or not parsed.port):
            raise Failure('endpoint must be an explicit localhost origin with port')
        self.origin = origin.rstrip('/')
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=tls))

    def request(self, method, path, headers=None, body=None, timeout=30):
        url = path if path.startswith(('http:', 'https:')) else self.origin + path
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme + '://' + parsed.netloc != self.origin
                or parsed.username or parsed.password or parsed.fragment):
            raise Failure('response URL escaped configured localhost origin')
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            # Never include a response body, URL, reason or request headers.
            raise Failure('HTTP request rejected', status=error.code) from None
        except (OSError, ValueError):
            raise Failure('HTTP outcome unknown; request was not resent') from None


def basic(user, password):
    return 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()


class Polaris:
    def __init__(self, http, realm, credentials):
        self.http = http
        token = http.request('POST', '/api/catalog/v1/oauth/tokens',
                             {'Authorization': basic(credentials['clientId'], credentials['clientSecret']),
                              'Polaris-Realm': realm, 'Content-Type': 'application/x-www-form-urlencoded'},
                             b'grant_type=client_credentials&scope=PRINCIPAL_ROLE%3AALL')
        self.headers = {'Authorization': 'Bearer ' + token['access_token'],
                        'Polaris-Realm': realm, 'Content-Type': 'application/json'}

    def request(self, method, path, body=None):
        return self.http.request(method, path, self.headers,
                                 None if body is None else json.dumps(body).encode())

    def optional(self, path):
        try:
            return self.request('GET', path)
        except Failure as error:
            if error.status == 404:
                return None
            raise


def named(items, name):
    return next((item for item in items if item['name'] == name), None)


def validate_entity(entity, name):
    if entity.get('name') != name or entity.get('properties', {}).get('shirokuma.dev/scope') != 'database-poc':
        raise Failure('existing named Polaris entity is not marked database-poc')


def ensure_role(api, path, envelope, name):
    existing = named(api.request('GET', path)['roles'], name)
    if existing is None:
        api.request('POST', path, {envelope: {'name': name, 'properties': PROPERTIES}})
    else:
        validate_entity(existing, name)


def bootstrap(origin):
    root_data = json.loads(kube('get', 'secret', 'polaris-root-credentials', '-o', 'json'))['data']
    root = {k: base64.b64decode(root_data[k], validate=True).decode()
            for k in ('client_id', 'client_secret', 'realm')}
    region_data = json.loads(kube('get', 'secret', 'seaweedfs-s3-application-credentials', '-o', 'json'))['data']
    region = base64.b64decode(region_data['S3_REGION'], validate=True).decode()
    api = Polaris(HTTP(origin), root['realm'], {'clientId': root['client_id'], 'clientSecret': root['client_secret']})
    save('realm.json', {'realm': root['realm']})
    for db in DATABASES:
        cat = catalog(db)
        expected = catalog_definition(db, region)
        path = MANAGEMENT + '/catalogs/' + cat
        existing = api.optional(path)
        if existing is None:
            api.request('POST', MANAGEMENT + '/catalogs', {'catalog': expected})
            existing = api.request('GET', path)
        validate_catalog(existing, expected)
        principal, role, cat_role = 'poc_' + db, 'poc_' + db + '_principal', 'poc_' + db + '_content'
        principal_path = MANAGEMENT + '/principals/' + principal
        existing = api.optional(principal_path)
        credential_file = db + '-principal.json'
        if existing is None:
            if (STATE / credential_file).exists():
                raise Failure('local credentials exist but Polaris principal is missing')
            created = api.request('POST', MANAGEMENT + '/principals', {
                'principal': {'name': principal, 'properties': PROPERTIES}, 'credentialRotationRequired': False})
            # Persist before any subsequent network operation. GET cannot recover a secret.
            save(credential_file, created['credentials'])
            existing = created['principal']
        validate_entity(existing, principal)
        if not (STATE / credential_file).exists():
            raise Failure('Polaris principal exists but local credential is missing; operator recovery required')
        credentials = load(credential_file)
        if existing.get('clientId') and existing['clientId'] != credentials['clientId']:
            raise Failure('stored principal client id does not match Polaris')
        ensure_role(api, MANAGEMENT + '/principal-roles', 'principalRole', role)
        role_path = path + '/catalog-roles'
        ensure_role(api, role_path, 'catalogRole', cat_role)
        membership = principal_path + '/principal-roles'
        assigned = api.request('GET', membership)['roles']
        if any(r['name'] != role for r in assigned):
            raise Failure('PoC principal has unexpected role membership')
        if not named(assigned, role):
            api.request('PUT', membership, {'principalRole': {'name': role}})
        # Inspect all catalogs to reject unexpected cross-catalog grants on this role.
        for item in api.request('GET', MANAGEMENT + '/catalogs')['catalogs']:
            member_path = MANAGEMENT + '/principal-roles/' + role + '/catalog-roles/' + urllib.parse.quote(item['name'], safe='')
            assigned = api.request('GET', member_path)['roles']
            if any(item['name'] != cat or r['name'] != cat_role for r in assigned):
                raise Failure('PoC principal role has unexpected catalog membership')
        membership = MANAGEMENT + '/principal-roles/' + role + '/catalog-roles/' + cat
        if not named(api.request('GET', membership)['roles'], cat_role):
            api.request('PUT', membership, {'catalogRole': {'name': cat_role}})
        grants_path = role_path + '/' + cat_role + '/grants'
        grants = api.request('GET', grants_path)['grants']
        if any(g.get('type') != 'catalog' or g.get('privilege') not in PRIVILEGES for g in grants):
            raise Failure('PoC catalog role has unexpected grants')
        for privilege in PRIVILEGES:
            grant = {'type': 'catalog', 'privilege': privilege}
            if not any(all(g.get(k) == v for k, v in grant.items()) for g in grants):
                api.request('PUT', grants_path, {'grant': grant})
        # Namespace creation uses root only; the dedicated principal is checked next.
        data_path = '/api/catalog/v1/' + cat + '/namespaces'
        namespaces = api.request('GET', data_path)['namespaces']
        for schema in SCHEMAS:
            if [schema] not in namespaces:
                api.request('POST', data_path, {'namespace': [schema], 'properties': {}})
        own = Polaris(HTTP(origin), root['realm'], credentials)
        if not all([s] in own.request('GET', data_path)['namespaces'] for s in SCHEMAS):
            raise Failure('dedicated Polaris principal cannot list own namespaces')


def deploy():
    try:
        from tools.local_experiment.database_poc_s3 import load_credentials
    except ModuleNotFoundError:
        from database_poc_s3 import load_credentials
    s3_credentials = load_credentials()
    pin = (ROOT / 'deploy/local-experiment/trino/secure/kustomization.yaml').read_text()
    if BASE_IMAGE.split('@')[1] not in pin or 'newName: shirokuma.local/trino' not in pin:
        raise Failure('secure overlay image pin changed; review helper compatibility')
    if not re.fullmatch(r'shirokuma\.local/trino@sha256:[0-9a-f]{64}', IMAGE) or IMAGE == BASE_IMAGE:
        raise Failure('pin the locally built database-poc-opa image before deployment')
    auth = {k: (STATE / k).read_text() for k in ('password.db', 'tls.pem', 'shared-secret')}
    resources = manifests({db: load(db + '-principal.json') for db in DATABASES},
                          load('realm.json')['realm'], auth, s3_credentials)
    save('resources.json', {'apiVersion': 'v1', 'kind': 'List', 'items': resources})
    kube('apply', '-f', '-', input_data=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': resources}).encode())
    kube('rollout', 'status', 'deployment/' + NAME, '--timeout=180s')


class Trino:
    def __init__(self, origin):
        self.http = HTTP(origin, ssl.create_default_context(cafile=str(STATE / 'tls.crt')), https=True)
        self.clients = load('clients.json')
        self.last = {}
        self.receipts = []

    def sql(self, user, statement, password=None, claimed_user=None, missing=False,
            *, catalog=None, schema=None):
        self.last = {'status': 'UNKNOWN', 'authenticated_identity': None if missing else user,
                     'catalog': catalog, 'schema': schema, 'sql': statement}
        if claimed_user and claimed_user != user:
            self.last['claimed_identity'] = claimed_user
        try:
            return self._statement(user, statement, password, claimed_user, missing, catalog, schema)
        except Failure as error:
            if self.last.get('id') and isinstance(error.status, int) and error.status in (401, 403):
                self.last['status'] = 'UNKNOWN'
                raise Failure('query polling authorization failed; outcome unknown',
                              query_id=self.last['id']) from None
            self.last['status'] = error.status if error.status in (
                'PERMISSION_DENIED', 'CATALOG_NOT_FOUND', 'QUERY_FAILED', 401, 403) else 'UNKNOWN'
            raise
        except Exception:
            self.last['status'] = 'UNKNOWN'
            raise
        finally:
            self.receipts.append(dict(self.last))
            save('queries.json', {'queries': self.receipts})

    def _statement(self, user, statement, password, claimed_user, missing, catalog, schema):
        headers = {'X-Trino-User': claimed_user or user, 'X-Trino-Source': 'database-poc',
                   'Content-Type': 'text/plain; charset=utf-8'}
        if catalog is not None:
            headers['X-Trino-Catalog'] = catalog
        if schema is not None:
            headers['X-Trino-Schema'] = schema
        if not missing:
            headers['Authorization'] = basic(user, self.clients[user]['password'] if password is None else password)
        deadline = time.monotonic() + 120
        # Exactly one POST. Even an unknown outcome is never replayed.
        page = self.http.request('POST', '/v1/statement', headers, statement.encode())
        query_id = page.get('id')
        if not isinstance(query_id, str) or not re.fullmatch(r'[A-Za-z0-9_]+', query_id):
            raise Failure('statement returned no valid query id')
        rows = []
        while True:
            state = page.get('stats', {}).get('state')
            self.last.update({'id': query_id, 'status': state if state in (
                'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING',
                'STARTING', 'RUNNING', 'FINISHING', 'FINISHED', 'FAILED') else 'UNKNOWN'})
            save('queries.json', {'queries': self.receipts + [dict(self.last)]})
            if page.get('id') != query_id:
                raise Failure('query id changed during polling', query_id=query_id)
            if 'error' in page:
                code = page['error'].get('errorName')
                if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', code):
                    self.last['errorName'] = code
                status = code if code in ('PERMISSION_DENIED', 'CATALOG_NOT_FOUND') else 'QUERY_FAILED'
                self.last['status'] = status
                raise Failure('statement rejected', status=status, query_id=query_id)
            rows.extend(page.get('data', []))
            if not page.get('nextUri'):
                if self.last['status'] != 'FINISHED':
                    raise Failure('query terminal outcome unknown', query_id=query_id)
                return rows
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure('query polling timed out; outcome unknown', query_id=query_id)
            page = self.http.request('GET', page['nextUri'], headers, timeout=min(30, remaining))


def identifier(value):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value):
        raise Failure('table name must be a lowercase SQL identifier of at most 63 characters')
    return value


def seed(trino, table, rows):
    for db in DATABASES:
        for schema in ('raw', 'sandbox', 'marts'):
            target = db + '.' + schema + '.' + table
            trino.sql('poc_admin', 'DROP TABLE IF EXISTS ' + target)
            trino.sql('poc_admin', 'CREATE TABLE ' + target + ' (id BIGINT, label VARCHAR)')
            # Two INSERT commits produce a valid old ancestor snapshot for rollback.
            trino.sql('poc_admin', "INSERT INTO " + target + " SELECT x, 'synthetic' FROM UNNEST(sequence(BIGINT '1', BIGINT '" + str(rows) + "')) AS t(x)")
            trino.sql('poc_admin', "INSERT INTO " + target + " VALUES (0, 'second snapshot')")


def verification_targets(targets):
    """Validate the optional, exact two-database REST routing map."""
    fields = {'origin', 'realm', 'catalog', 'prefix'}
    if not isinstance(targets, dict) or set(targets) != set(DATABASES):
        raise Failure('verification targets must contain exactly sales and finance')
    for target in targets.values():
        if (not isinstance(target, dict) or set(target) != fields
                or any(not isinstance(v, str) or not v.strip() or v != v.strip()
                       for v in target.values())):
            raise Failure('verification target fields must be nonempty strings')
        try:
            parsed = urllib.parse.urlsplit(target['origin'])
            valid_origin = (parsed.scheme in ('http', 'https')
                            and parsed.hostname in ('localhost', '127.0.0.1')
                            and not parsed.username and not parsed.password
                            and parsed.path in ('', '/') and not parsed.query
                            and not parsed.fragment and bool(parsed.port))
            location = urllib.parse.urlsplit(target['prefix'])
            location_port = location.port
        except ValueError:
            raise Failure('invalid verification target origin or prefix') from None
        if (not valid_origin
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', target['catalog'])
                or location.scheme != 's3' or not location.netloc
                or not location.path.strip('/') or location.query or location.fragment
                or location.username or location.password or location_port
                or any(c.isspace() for c in target['prefix'])
                or target['prefix'].endswith('/')):
            raise Failure('invalid verification target origin, catalog or prefix')
    return {db: dict(targets[db]) for db in DATABASES}


def verify(trino, origin, table, *, targets=None):
    if targets is not None:
        targets = verification_targets(targets)
    report = {'scope': 'standalone database-poc', 'checks': []}

    def check(name, action, denied=()):
        start = len(trino.receipts)
        try:
            detail = action()
            status = 'FAILED' if denied else 'PASSED'
            entry = {'step': name, 'status': status}
            if denied:
                entry['reason'] = ('unexpected allow; known Trino table-procedure limitation'
                                   if 'rollback_to_valid_old_snapshot' in name else 'unexpected allow')
            if isinstance(detail, dict):
                entry.update(detail)
        except Failure as error:
            entry = {'step': name, 'status': 'PASSED' if error.status in denied else 'FAILED',
                     'reason': 'expected denial' if error.status in denied else str(error)}
        except Exception:
            entry = {'step': name, 'status': 'UNKNOWN', 'reason': 'prerequisite or response invalid'}
        if name.startswith('sql.'):
            entry.setdefault('query', dict(trino.last))
        entry['queries'] = [dict(q) for q in trino.receipts[start:]]
        report['checks'].append(entry)
        save('verification.json', report)
        print(entry['status'] + ' ' + name)

    def sql(name, user, statement, deny=False, **kwargs):
        check('sql.' + name, lambda: trino.sql(user, statement, **kwargs),
              ('PERMISSION_DENIED', 403) if deny else ())

    def require(condition, message):
        if not condition:
            raise Failure(message)

    check('sql.password.missing', lambda: trino.sql('poc_admin', 'SELECT 1', missing=True), (401, 403))
    check('sql.password.wrong', lambda: trino.sql('poc_admin', 'SELECT 1', password='incorrect'), (401, 403))
    sql('admin.both', 'poc_admin', 'SELECT * FROM sales.raw.' + table + ' UNION ALL SELECT * FROM finance.raw.' + table)
    for db in DATABASES:
        other = 'finance' if db == 'sales' else 'sales'
        engineer, viewer = db + '_engineer', db + '_viewer'
        sql(engineer + '.native.1part', engineer, 'SELECT * FROM ' + table,
            catalog=db, schema='raw')
        sql(engineer + '.native.2part', engineer, 'SELECT * FROM raw.' + table, catalog=db)
        sql(engineer + '.native.3part', engineer, 'SELECT * FROM ' + db + '.raw.' + table)
        for user in (engineer, viewer):
            sql(user + '.marts.read', user, 'SELECT * FROM ' + db + '.marts.' + table)
            sql(user + '.other', user, 'SELECT * FROM ' + other + '.marts.' + table, True)
            sql(user + '.staging', user, 'SHOW TABLES FROM ' + db + '.staging', True)
            sql(user + '.marts.insert', user, 'INSERT INTO ' + db + '.marts.' + table + " VALUES (9, 'denied')", True)
            sql(user + '.marts.delete', user, 'DELETE FROM ' + db + '.marts.' + table + ' WHERE id=9', True)
            sql(user + '.marts.update', user, 'UPDATE ' + db + '.marts.' + table + " SET label='denied' WHERE id=9", True)
            sql(user + '.schema.create', user, 'CREATE SCHEMA ' + db + '.poc_denied_schema', True)
            sql(user + '.schema.drop', user, 'DROP SCHEMA ' + db + '.staging', True)
            sql(user + '.schema.rename', user, 'ALTER SCHEMA ' + db + '.staging RENAME TO poc_denied_rename', True)
            sql(user + '.spoof', user, 'SELECT * FROM finance.raw.' + table, True, claimed_user='poc_admin')
            sql(user + '.procedure', user, "CALL " + db + ".system.unregister_table(schema_name => 'sandbox', table_name => '" + table + "')", True)

            def metadata(user=user, db=db):
                cats = {r[0] for r in trino.sql(user, 'SHOW CATALOGS')}
                require(cats == {db}, 'SQL catalog metadata is not restricted to own catalog')
                schemas = {r[0] for r in trino.sql(user, 'SHOW SCHEMAS FROM ' + db)}
                allowed = {'marts'} if user.endswith('_viewer') else {'raw', 'sandbox', 'marts'}
                require(allowed <= schemas and not (schemas - allowed - {'information_schema'}),
                        'schema metadata exposes an unauthorized namespace or omits allowed namespaces')
            check('sql.' + user + '.metadata', metadata)
            check('sql.' + user + '.polaris_name',
                  lambda user=user, db=db: trino.sql(user, 'SELECT * FROM ' + (targets[db]['catalog'] if targets is not None else catalog(db)) + '.raw.' + table),
                  ('CATALOG_NOT_FOUND', 'PERMISSION_DENIED'))

            def rollback(user=user, db=db):
                refs = db + '.marts."' + table + '$refs"'
                snapshots_table = db + '.marts."' + table + '$snapshots"'

                def current():
                    rows = trino.sql('poc_admin', 'SELECT snapshot_id FROM ' + refs + " WHERE name = 'main'")
                    require(len(rows) == 1 and rows[0][0] is not None,
                            'current snapshot unavailable')
                    return int(rows[0][0])

                try:
                    snapshots = trino.sql('poc_admin', 'SELECT snapshot_id FROM ' + db + '.marts."' + table + '$snapshots" ORDER BY committed_at, snapshot_id')
                    require(len(snapshots) >= 2, 'rollback requires at least two real snapshots; run seed')
                    # A previous unexpected allow may have moved the head to old.
                    # Append a real commit so each role tests a non-current ancestor.
                    trino.sql('poc_admin', 'INSERT INTO ' + db + '.marts.' + table + " VALUES (-2, 'rollback probe')")
                    parent = trino.sql('poc_admin', 'SELECT parent_id FROM ' + db + '.marts."' + table + '$snapshots" WHERE snapshot_id = (SELECT snapshot_id FROM ' + db + '.marts."' + table + "$refs\" WHERE name = 'main')")
                    require(len(parent) == 1 and parent[0][0] is not None,
                            'rollback requires the real current snapshot parent')
                    old = int(parent[0][0])
                    before = current()
                    require(old != before, 'rollback ancestor must differ from current snapshot')
                    ancestor = trino.sql('poc_admin', 'SELECT snapshot_id FROM ' + snapshots_table +
                                         ' WHERE snapshot_id = ' + str(old))
                    require(len(ancestor) == 1 and int(ancestor[0][0]) == old,
                            'rollback ancestor snapshot unavailable')
                except Failure:
                    raise Failure('rollback prerequisite failed; denial not established') from None
                result = {'old': old, 'before': before, 'after': None, 'changed': None}
                try:
                    trino.sql(user, 'ALTER TABLE ' + db + '.marts.' + table + ' EXECUTE rollback_to_snapshot(snapshot_id => ' + str(old) + ')')
                    result.update(status='FAILED', reason='unexpected allow; known Trino table-procedure limitation')
                except Failure as error:
                    denied = error.status in ('PERMISSION_DENIED', 403)
                    result.update(status='PASSED' if denied else 'FAILED',
                                  reason='expected denial' if denied else 'target operation failed or unknown')
                except Exception:
                    result.update(status='UNKNOWN', reason='target operation outcome unknown')
                result['query'] = dict(trino.last)
                try:
                    result['after'] = current()
                    result['changed'] = result['before'] != result['after']
                    if result['changed'] and result['status'] == 'PASSED':
                        result.update(status='FAILED', reason='snapshot changed despite target denial')
                except Exception:
                    result.update(status='FAILED', reason='snapshot aftercheck failed; denial not established')
                return result
            check('sql.' + user + '.marts.rollback_to_valid_old_snapshot', rollback)
        for schema in ('raw', 'sandbox'):
            target = db + '.' + schema + '.' + table
            sql(engineer + '.' + schema + '.read', engineer, 'SELECT * FROM ' + target)
            sql(viewer + '.' + schema + '.denied', viewer, 'SELECT * FROM ' + target, True)
            sql(engineer + '.' + schema + '.insert', engineer, 'INSERT INTO ' + target + " VALUES (-1, 'write check')")
            sql(engineer + '.' + schema + '.update', engineer, 'UPDATE ' + target + " SET label='updated' WHERE id=-1")
            sql(engineer + '.' + schema + '.delete', engineer, 'DELETE FROM ' + target + ' WHERE id=-1')
            probe = target + '_create_probe'

            def create_drop(engineer=engineer, probe=probe):
                trino.sql(engineer, 'CREATE TABLE ' + probe + ' (id BIGINT)')
                # Do not drop a pre-existing table after a failed CREATE.
                trino.sql(engineer, 'DROP TABLE ' + probe)
            check('sql.' + engineer + '.' + schema + '.create_drop', create_drop)

        def rest_checks(db=db, other=other):
            target = targets[db] if targets is not None else None
            api = Polaris(HTTP(target['origin'] if target else origin),
                          target['realm'] if target else load('realm.json')['realm'],
                          load(db + '-principal.json'))
            own = '/api/catalog/v1/' + (target['catalog'] if target else catalog(db))
            namespaces = api.request('GET', own + '/namespaces')['namespaces']
            require(sorted(namespaces) == sorted([[s] for s in SCHEMAS]), 'Polaris namespace list differs from four PoC namespaces')
            metadata = api.request('GET', own + '/namespaces/raw/tables/' + table)['metadata']
            table_uuid = str(uuid.UUID(metadata['table-uuid']))
            location = metadata['location']
            require(location.startswith((target['prefix'] if target else prefix(db)) + '/'), 'table location escaped expected prefix')
            return {'table_uuid': table_uuid, 'location': location, 'namespaces': list(SCHEMAS)}
        check('polaris.' + db + '.own_list_load', rest_checks)

        def cross(path, db=db):
            # Own positive must succeed before a hidden/denied other catalog counts.
            try:
                target = targets[db] if targets is not None else None
                api = Polaris(HTTP(target['origin'] if target else origin),
                              target['realm'] if target else load('realm.json')['realm'],
                              load(db + '-principal.json'))
                api.request('GET', '/api/catalog/v1/' + (target['catalog'] if target else catalog(db)) + '/namespaces')
            except Failure:
                raise Failure('own Polaris catalog prerequisite failed; cross denial not established') from None
            api.request('GET', path)
        other_base = '/api/catalog/v1/' + (targets[other]['catalog'] if targets is not None else catalog(other)) + '/namespaces'
        check('polaris.' + db + '.other_list', lambda p=other_base: cross(p), (403, 404))
        check('polaris.' + db + '.other_load', lambda p=other_base + '/raw/tables/' + table: cross(p), (403, 404))
    report['passed'] = sum(e['status'] == 'PASSED' for e in report['checks'])
    report['failed_or_unknown'] = len(report['checks']) - report['passed']
    save('verification.json', report)
    print('Verification: ' + str(report['passed']) + ' passed; ' + str(report['failed_or_unknown']) + ' failed/unknown')
    return 1 if report['failed_or_unknown'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'bootstrap', 'deploy', 'seed', 'verify'))
    parser.add_argument('--polaris-url', default='http://127.0.0.1:18181')
    parser.add_argument('--trino-url', default='https://127.0.0.1:18443')
    parser.add_argument('--table', default='orders', help='synthetic seed/verify table name (default: orders)')
    parser.add_argument('--rows', type=int, default=10, help='seed rows per first snapshot (1..10000)')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        table = identifier(args.table)
        if not 1 <= args.rows <= 10000:
            raise Failure('rows must be between 1 and 10000')
        if args.command != 'prepare':
            if not STATE.is_dir() or STATE.stat().st_mode & 0o077:
                raise Failure('prepare private state first (directory mode 0700)')
        if args.command == 'prepare':
            prepare()
        elif args.command == 'bootstrap':
            bootstrap(args.polaris_url)
        elif args.command == 'deploy':
            deploy()
        elif args.command == 'seed':
            seed(Trino(args.trino_url), table, args.rows)
        else:
            return verify(Trino(args.trino_url), args.polaris_url, table)
        print(args.command + ' complete; private state: var/shirokuma/database-poc')
        return 0
    except Failure as error:
        print('FAILED: ' + str(error), file=sys.stderr)
    except Exception:
        print('FAILED: invalid or unavailable prerequisite; details suppressed', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
