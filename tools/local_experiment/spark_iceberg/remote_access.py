"""Ephemeral table-scoped Polaris access and two owned ingress policies.

The management credential never enters the workload. S3 credentials are existing
static bucket credentials: the Secret/namespace lifetime does NOT downscope or
expire them. Polaris expires_in is server-controlled; deleting a principal is
not claimed to instantly revoke an already issued token. Polaris name-based
deletes have no UID precondition, unlike the Kubernetes deletes below. A known
create response, exact metadata readback and no concurrent mutation are required.
Lost responses remain unknown; this module never retries/adopts an unknown create.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request
import uuid

from tools.local_experiment.iceberg_refs import client as AUTH
from tools.local_experiment.spark import prepare as P
from tools.local_experiment.trino_opa import common as C

CATALOG = 'shirokuma_l1'
NAMESPACE = 'local_experiment'
MANAGEMENT = '/api/management/v1'
CATALOG_ROLES = MANAGEMENT + '/catalogs/' + CATALOG + '/catalog-roles'
POLARIS_URI = 'http://polaris.shirokuma-dev.svc.cluster.local:8181/api/catalog'
S3_ENDPOINT = 'http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333'
S3_REGION = 'us-east-1'
PRIVILEGES = ('TABLE_LIST', 'TABLE_READ_PROPERTIES', 'TABLE_WRITE_PROPERTIES')
MAX_RESPONSE = 2 * 1024**2
SERVICES = (
    ('shirokuma-dev', 'polaris', 'polaris', 'catalog-server', 8181, 'http'),
    ('shirokuma-storage', 'seaweedfs-s3', 'seaweedfs', 'object-storage', 8333, 's3'),
)


class AccessError(RuntimeError):
    def __init__(self, code, *, outcome='unknown', http_status=None):
        self.code, self.outcome, self.http_status = code, outcome, http_status
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise AccessError(code)


def token(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value), 'RUN_TOKEN_INVALID')
    return value


def canonical_uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, 'UUID_INVALID')
    except (ValueError, AttributeError):
        raise AccessError('UUID_INVALID') from None
    return value


def credential(value, limit=16384):
    require(type(value) is str and 1 <= len(value) <= limit and value.isascii()
            and all(32 < ord(c) < 127 for c in value), 'CREDENTIAL_INVALID')
    return value


def paths(run_token):
    name = 'spark_' + token(run_token)
    p = MANAGEMENT + '/principals/' + name
    r = MANAGEMENT + '/principal-roles/' + name
    c = CATALOG_ROLES + '/' + name
    return {'principal': p, 'principal_role': r, 'catalog_role': c,
            'principal_binding': p + '/principal-roles',
            'catalog_binding': r + '/catalog-roles/' + CATALOG,
            'grants': c + '/grants',
            'table': '/api/catalog/v1/' + CATALOG + '/namespaces/' + NAMESPACE + '/tables/' + name}


class AdminClient:
    """Fixed-loopback host-only transport; no arbitrary catalog or entity APIs."""
    def __init__(self, run_token, guard, *, opener=None):
        self.run_token, self.guard = token(run_token), guard
        self.auth = AUTH._Authentication(guard, opener)
        self.p = paths(run_token)

    @property
    def realm(self):
        return self.auth.realm

    def authenticate(self):
        try:
            self.auth.authenticate()
        except Exception:
            raise AccessError('ADMIN_AUTHENTICATION_FAILED', outcome='not-sent') from None

    def _exchange(self, method, path, body=None, headers=None, *, statuses=(200,)):
        self.guard()
        require(self.auth.base == AUTH.BASE_URL, 'ADMIN_ORIGIN_INVALID')
        request_headers = {'Accept': 'application/json', 'Polaris-Realm': credential(self.realm, 256)}
        if self.auth.token:
            request_headers['Authorization'] = 'Bearer ' + credential(self.auth.token)
        if headers:
            request_headers.update(headers)
        data = body
        if isinstance(body, dict):
            data = C.canonical(body).encode()
            request_headers['Content-Type'] = 'application/json'
        require(data is None or type(data) is bytes and len(data) <= 32768, 'REQUEST_BODY_LIMIT')
        req = Request(AUTH.BASE_URL + path, data=data, headers=request_headers, method=method)
        try:
            with self.auth.opener.open(req, timeout=20) as response:
                require(response.geturl() == AUTH.BASE_URL + path and response.status in statuses,
                        'RESPONSE_STATUS_OR_ORIGIN_INVALID')
                raw = response.read(MAX_RESPONSE + 1)
                require(len(raw) <= MAX_RESPONSE, 'RESPONSE_TOO_LARGE')
                if not raw and response.status in (201, 204):
                    return {}
                require(response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                        == 'application/json', 'RESPONSE_CONTENT_TYPE_INVALID')
                value = json.loads(raw, object_pairs_hook=AUTH._object,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                require(type(value) is dict, 'RESPONSE_JSON_INVALID')
                return value
        except HTTPError as error:
            status = error.code
            error.close()
            outcome = 'rejected' if status in (400, 401, 403, 404, 405, 409) else 'unknown'
            raise AccessError('POLARIS_HTTP_FAILED', outcome=outcome, http_status=status) from None
        except (URLError, OSError, ValueError, RecursionError):
            raise AccessError('POLARIS_RESPONSE_UNKNOWN') from None

    def request(self, method, path, body=None):
        p, name = self.p, 'spark_' + self.run_token
        reads = {p[k] for k in ('principal', 'principal_role', 'catalog_role', 'principal_binding',
                                'catalog_binding', 'grants', 'table')}
        reads |= {p['principal_role'] + '/principals', p['catalog_role'] + '/principal-roles'}
        create = {MANAGEMENT + '/principals': 'principal',
                  MANAGEMENT + '/principal-roles': 'principalRole', CATALOG_ROLES: 'catalogRole'}
        deletes = {p[k] for k in ('principal', 'principal_role', 'catalog_role')}
        deletes |= {p['principal_binding'] + '/' + name, p['catalog_binding'] + '/' + name}
        valid = method == 'GET' and path in reads and body is None
        if method == 'POST' and path in create:
            key = create[path]
            valid = (type(body) is dict and set(body) == ({key, 'credentialRotationRequired'}
                     if key == 'principal' else {key}) and body[key] == {
                         'name': name, 'properties': {'shirokuma.run-token': self.run_token}}
                     and (key != 'principal' or body['credentialRotationRequired'] is False))
        if method == 'PUT':
            valid = ((path == p['principal_binding'] and body == {'principalRole': {'name': name}})
                     or (path == p['catalog_binding'] and body == {'catalogRole': {'name': name}})
                     or (path == p['grants'] and body in [{'grant': grant(self.run_token, x)} for x in PRIVILEGES]))
        if method == 'DELETE':
            valid = path in deletes and body is None
        require(valid, 'ADMIN_REQUEST_SCOPE_INVALID')
        return self._exchange(method, path, body, statuses={'GET': (200,), 'POST': (201,),
                              'PUT': (200, 201, 204), 'DELETE': (204,)}[method])

    def issue_token(self, client_id, client_secret):
        credential(client_id, 512); credential(client_secret, 4096)
        require(':' not in client_id, 'CREDENTIAL_INVALID')
        basic = base64.b64encode((client_id + ':' + client_secret).encode()).decode()
        return self._exchange('POST', AUTH.TOKEN_PATH, urlencode({
            'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:spark_' + self.run_token}).encode(),
            {'Authorization': 'Basic ' + basic, 'Content-Type': 'application/x-www-form-urlencoded'})


def grant(run_token, privilege):
    require(privilege in PRIVILEGES, 'PRIVILEGE_INVALID')
    return {'type': 'table', 'namespace': [NAMESPACE], 'tableName': 'spark_' + token(run_token),
            'privilege': privilege}


def _selector(item):
    return {'app.kubernetes.io/name': item[2], 'app.kubernetes.io/component': item[3]}


def _service(value, expected):
    ns, name, _, _, port, target = expected
    require(type(value) is dict and value.get('kind') == 'Service' and value.get('apiVersion') == 'v1',
            'SERVICE_INVALID')
    m, s = value.get('metadata', {}), value.get('spec', {})
    require(m.get('name') == name and m.get('namespace') == ns and not m.get('deletionTimestamp'),
            'SERVICE_IDENTITY_INVALID')
    canonical_uuid(m.get('uid'))
    require(s.get('type', 'ClusterIP') == 'ClusterIP' and s.get('selector') == _selector(expected)
            and not s.get('externalIPs') and not s.get('externalName'), 'SERVICE_SCOPE_INVALID')
    ports = s.get('ports')
    require(type(ports) is list and len(ports) == 1 and ports[0].get('port') == port
            and ports[0].get('targetPort') in (target, port) and ports[0].get('protocol', 'TCP') == 'TCP'
            and not ports[0].get('nodePort'), 'SERVICE_PORT_INVALID')
    try:
        ip = ipaddress.ip_address(s.get('clusterIP'))
        require(ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_unspecified,
                'SERVICE_IP_INVALID')
    except ValueError:
        raise AccessError('SERVICE_IP_INVALID') from None
    return {'namespace': ns, 'name': name, 'uid': m['uid'], 'spec_sha256': C.digest(s),
            'cluster_ip': str(ip), 'pod_selector': _selector(expected), 'port': port}


def discover_services(kube):
    return [_service(kube.get('Service', item[0], item[1]), item) for item in SERVICES]


def service_guard(kube, services):
    require(discover_services(kube) == services, 'SERVICE_DRIFT')


def ingress_policies(run_token):
    run_token = token(run_token)
    labels = {'shirokuma.io/experiment': 'spark-iceberg-access', 'shirokuma.io/run-token': run_token}
    return [{'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {
        'name': 'spark-iceberg-' + run_token, 'namespace': item[0], 'labels': labels}, 'spec': {
        'podSelector': {'matchLabels': _selector(item)}, 'policyTypes': ['Ingress'], 'ingress': [{
            'from': [{'namespaceSelector': {'matchLabels': {
                'kubernetes.io/metadata.name': 'shirokuma-spark-' + run_token}},
                'podSelector': {'matchLabels': {'shirokuma.io/experiment': 'spark-native',
                    'shirokuma.io/run-token': run_token}}}],
            'ports': [{'protocol': 'TCP', 'port': item[4]}]}]}} for item in SERVICES]


class AccessKube:
    def __init__(self, run_token, *, run=P.command):
        self.run_token, self.run = token(run_token), run

    def _request(self, args, *, body=None):
        try:
            raw = self.run(['kubectl', '--context', P.CONTEXT, '--request-timeout=10s', *args],
                           body=None if body is None else C.canonical(body), timeout=15, output_limit=MAX_RESPONSE)
            return json.loads(raw, object_pairs_hook=AUTH._object) if raw.strip() else None
        except Exception:
            raise AccessError('ACCESS_KUBE_REQUEST_FAILED') from None

    def get(self, kind, namespace, name):
        valid = kind == 'Service' and (namespace, name) in [(x[0], x[1]) for x in SERVICES]
        valid |= kind == 'NetworkPolicy' and namespace in [x[0] for x in SERVICES] and name == 'spark-iceberg-' + self.run_token
        require(valid, 'ACCESS_KUBE_SCOPE_INVALID')
        return self._request(['-n', namespace, 'get', kind, name, '--ignore-not-found', '-o', 'json'])

    def create(self, obj):
        require(obj in ingress_policies(self.run_token), 'INGRESS_SCOPE_INVALID')
        return self._request(['-n', obj['metadata']['namespace'], 'create', '-f', '-', '-o', 'json'], body=obj)

    def delete(self, obj):
        expected = next((x for x in ingress_policies(self.run_token)
                         if x['metadata']['namespace'] == obj.get('metadata', {}).get('namespace')), None)
        require(expected is not None, 'INGRESS_SCOPE_INVALID')
        verify_policy(obj, expected)
        m = obj['metadata']
        require(type(m.get('resourceVersion')) is str and m['resourceVersion'].isdigit(), 'RESOURCE_VERSION_INVALID')
        path = '/apis/networking.k8s.io/v1/namespaces/' + m['namespace'] + '/networkpolicies/' + m['name']
        return self._request(['delete', '--raw', path, '-f', '-'], body={'apiVersion': 'v1',
            'kind': 'DeleteOptions', 'propagationPolicy': 'Orphan',
            'preconditions': {'uid': m['uid'], 'resourceVersion': m['resourceVersion']}})


def verify_policy(value, expected, uid=None):
    require(type(value) is dict and value.get('apiVersion') == expected['apiVersion']
            and value.get('kind') == 'NetworkPolicy' and value.get('spec') == expected['spec'],
            'INGRESS_POLICY_CONFLICT')
    m = value.get('metadata', {})
    require(all(m.get(k) == v for k, v in expected['metadata'].items())
            and not m.get('ownerReferences') and (uid is None or m.get('uid') == uid), 'INGRESS_IDENTITY_CONFLICT')
    return canonical_uuid(m.get('uid'))


def read_s3_credentials():
    """One fixed Secret, 15 s deadline and 64 KiB combined pipes; no disk/logs."""
    args = ['kubectl', '--context', P.CONTEXT, '--request-timeout=10s', '-n', 'shirokuma-dev',
            'get', 'secret', 'seaweedfs-s3-application-credentials', '-o', 'json']
    process, raw, received = None, bytearray(), 0
    deadline = time.monotonic() + 15
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                require(time.monotonic() < deadline, 'S3_SECRET_READ_TIMEOUT')
                for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    received += len(chunk)
                    require(received <= 65536, 'S3_SECRET_TOO_LARGE')
                    if key.data:
                        raw.extend(chunk)
        require(process.wait(timeout=max(.01, deadline - time.monotonic())) == 0, 'S3_SECRET_READ_FAILED')
        data = json.loads(raw, object_pairs_hook=AUTH._object)['data']
        decoded = {k: base64.b64decode(data[k], validate=True).decode('ascii')
                   for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'S3_REGION')}
        require(decoded['S3_REGION'] == S3_REGION, 'S3_REGION_INVALID')
        return {'access_key': credential(decoded['AWS_ACCESS_KEY_ID'], 512),
                'secret_key': credential(decoded['AWS_SECRET_ACCESS_KEY'], 4096)}
    except Exception:
        raise AccessError('S3_SECRET_READ_FAILED', outcome='not-sent') from None
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()


class RemoteAccess:
    def __init__(self, run_token, output, *, admin, kube, table_guard, secret_reader=read_s3_credentials):
        self.token, self.output = token(run_token), Path(output)
        self.admin, self.kube, self.table_guard, self.secret_reader = admin, kube, table_guard, secret_reader
        self.p, self.name = paths(run_token), 'spark_' + run_token
        self._credentials, self._material, self._secrets = None, None, set()
        self._identifiers = set()
        self.services, self.table = None, None
        self.journal = {'schema_version': 1, 'run_token': run_token, 'result': 'new', 'pending': None,
            'entities': {}, 'policies': {}, 'bindings': [], 'grants': [], 'events': [],
            'token': {'issued': False, 'expires_in': None, 'scope': 'PRINCIPAL_ROLE:' + self.name},
            'cleanup': {'verified': False, 'removed': [], 'deferred': []},
            'limits': ['static-bucket-s3-credentials', 'polaris-server-controlled-token-ttl',
                       'polaris-delete-has-no-atomic-identity-precondition', 'no-instant-token-revocation-claim']}

    def receipt(self):
        return deepcopy(self.journal)

    def _save(self):
        C.write(self.output / 'access.json', self.journal)

    def _guard(self):
        self.table_guard()
        if self.services is not None:
            service_guard(self.kube, self.services)

    def _mutate(self, action, target, call):
        require(self.journal['pending'] is None, 'ACCESS_PENDING_UNKNOWN')
        self._guard()
        self.journal['pending'] = {'action': action, 'target': target, 'outcome': 'pending'}
        self._save()
        try:
            return call()
        except BaseException as error:
            rejected = isinstance(error, AccessError) and error.outcome == 'rejected'
            event = {'action': action, 'target': target, 'outcome': 'rejected' if rejected else 'unknown'}
            self.journal['events'].append(event)
            self.journal['pending'] = None if rejected else event
            self.journal['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
            try:
                self._save()
            finally:
                raise error

    def _confirmed(self):
        self.journal['events'].append(dict(self.journal['pending'], outcome='confirmed'))
        self.journal['pending'] = None
        self._save()

    def _entity(self, value, kind):
        require(type(value) is dict and value.get('name') == self.name
                and value.get('properties') == {'shirokuma.run-token': self.token}, 'ENTITY_IDENTITY_INVALID')
        allowed = {'name', 'properties', 'createTimestamp', 'lastUpdateTimestamp', 'entityVersion'}
        if kind == 'principal':
            allowed.add('clientId'); credential(value.get('clientId'), 512)
        if kind == 'principal_role':
            allowed.add('federated')
            # Polaris 1.6 OpenAPI PrincipalRole defaults this field to false.
            require(value.get('federated', False) is False, 'FEDERATED_ROLE_FORBIDDEN')
        require(set(value) <= allowed, 'ENTITY_FIELDS_INVALID')
        for key in ('createTimestamp', 'lastUpdateTimestamp', 'entityVersion'):
            require(type(value.get(key)) is int and value[key] >= 0, 'ENTITY_VERSION_INVALID')
        result = deepcopy(value)
        if kind == 'principal_role':
            result['federated'] = False
        return result

    def _verify_entities(self):
        for kind, saved in self.journal['entities'].items():
            require(self._entity(self.admin.request('GET', self.p[kind]), kind) == saved,
                    'ENTITY_DRIFT')

    def _absent(self, path):
        try:
            self.admin.request('GET', path)
        except AccessError as error:
            if error.http_status == 404:
                return
            raise
        raise AccessError('ENTITY_NAME_COLLISION', outcome='not-sent')

    def prepare(self, table, services):
        require(self.journal['result'] == 'new', 'ACCESS_ALREADY_STARTED')
        require(type(table) is dict and set(table) == {'table_uuid', 'table_location'}, 'TABLE_SCOPE_INVALID')
        canonical_uuid(table['table_uuid'])
        require(table['table_location'] == 's3://shirokuma-lakehouse/l1/local_experiment/' + self.name,
                'TABLE_LOCATION_INVALID')
        self.services, self.table = deepcopy(services), deepcopy(table)
        self._guard()
        self.journal.update(result='preparing', table=deepcopy(table), services=deepcopy(services))
        self._save()
        for kind in ('principal', 'principal_role', 'catalog_role'):
            self._absent(self.p[kind])
        policies = ingress_policies(self.token)
        for obj in policies:
            require(self.kube.get('NetworkPolicy', obj['metadata']['namespace'], obj['metadata']['name']) is None,
                    'INGRESS_NAME_COLLISION')
        for kind, key, collection in (('principal', 'principal', MANAGEMENT + '/principals'),
                                     ('principal_role', 'principalRole', MANAGEMENT + '/principal-roles'),
                                     ('catalog_role', 'catalogRole', CATALOG_ROLES)):
            body = {key: {'name': self.name, 'properties': {'shirokuma.run-token': self.token}}}
            if kind == 'principal':
                body['credentialRotationRequired'] = False
            value = self._mutate('create-entity', kind, lambda: self.admin.request('POST', collection, body))
            entity = self._entity(value['principal'] if kind == 'principal' else value, kind)
            # Persist only the public create identity before any additional read.
            self.journal['entities'][kind] = entity
            self._save()
            if kind == 'principal':
                creds = value.get('credentials', {})
                client_id, client_secret = credential(creds.get('clientId'), 512), credential(creds.get('clientSecret'), 4096)
                require(client_id == entity['clientId'], 'PRINCIPAL_CREDENTIAL_ID_INVALID')
                self._credentials = (client_id, client_secret)
                self._identifiers.add(client_id)
                self._secrets.add(client_secret)
            self._verify_entities()
            self._confirmed()
        for privilege in PRIVILEGES:
            self._verify_entities()
            self._mutate('grant-table', privilege, lambda: self.admin.request('PUT', self.p['grants'],
                                                                           {'grant': grant(self.token, privilege)}))
            self.journal['grants'].append(privilege)
            self._verify_grants()
            self._confirmed()
        for target, key in (('principal_binding', 'principalRole'), ('catalog_binding', 'catalogRole')):
            self._verify_entities()
            require(self.admin.request('GET', self.p[target]).get('roles') == [], 'BINDING_NOT_EMPTY')
            self._mutate('bind-role', target, lambda: self.admin.request('PUT', self.p[target], {key: {'name': self.name}}))
            self.journal['bindings'].append(target)
            self._verify_binding(target)
            self._confirmed()
        for obj in policies:
            ns = obj['metadata']['namespace']
            value = self._mutate('create-ingress', ns, lambda: self.kube.create(obj))
            uid = verify_policy(value, obj)
            self.journal['policies'][ns] = uid
            self._save()
            verify_policy(self.kube.get('NetworkPolicy', ns, obj['metadata']['name']), obj, uid)
            self._confirmed()
        self.journal['result'] = 'prepared'
        self._save()
        return {'polaris_uri': POLARIS_URI, 'polaris_realm': self.admin.realm, 'warehouse': CATALOG,
                'scope': 'PRINCIPAL_ROLE:' + self.name, 's3_endpoint': S3_ENDPOINT, 's3_region': S3_REGION,
                'data_egress': [{'namespace': x['namespace'], 'pod_selector': x['pod_selector'], 'port': x['port']}
                                for x in self.services], **table}

    def _verify_grants(self):
        rows = self.admin.request('GET', self.p['grants']).get('grants')
        expected = [grant(self.token, p) for p in self.journal['grants']]
        require(type(rows) is list and len(rows) == len(expected)
                and sorted(rows, key=C.canonical) == sorted(expected, key=C.canonical), 'GRANTS_MISMATCH')

    def _verify_binding(self, target):
        rows = self.admin.request('GET', self.p[target]).get('roles')
        kind = 'principal_role' if target == 'principal_binding' else 'catalog_role'
        require(type(rows) is list and len(rows) == 1 and self._entity(rows[0], kind)
                == self.journal['entities'][kind], 'BINDING_MISMATCH')

    def _verify_memberships(self):
        """No other principal/role may have acquired this temporary capability."""
        checks = ((self.p['principal_role'] + '/principals', 'principals', 'principal', 'principal_binding'),
                  (self.p['catalog_role'] + '/principal-roles', 'roles', 'principal_role', 'catalog_binding'))
        for path, field, kind, binding in checks:
            owner_kind = 'principal_role' if field == 'principals' else 'catalog_role'
            if owner_kind not in self.journal['entities']:
                continue
            rows = self.admin.request('GET', path).get(field)
            expected = [self.journal['entities'][kind]] if binding in self.journal['bindings'] else []
            require(type(rows) is list and [self._entity(x, kind) for x in rows] == expected,
                    'FOREIGN_ROLE_MEMBERSHIP')

    def secret_provider(self):
        require(self.journal['result'] == 'prepared' and self.journal['pending'] is None, 'ACCESS_NOT_PREPARED')
        self._guard(); self._verify_entities(); self._verify_grants()
        for target in self.journal['bindings']:
            self._verify_binding(target)
        self._verify_memberships()
        if self._material is not None:
            return dict(self._material)
        require(self._credentials is not None and not self.journal['token']['issued'], 'TOKEN_REISSUE_FORBIDDEN')
        keys = self.secret_reader()
        require(type(keys) is dict and set(keys) == {'access_key', 'secret_key'}, 'S3_CREDENTIAL_SHAPE_INVALID')
        access, secret = credential(keys['access_key'], 512), credential(keys['secret_key'], 4096)
        self._identifiers.add(access)
        self._secrets.add(secret)
        value = self._mutate('issue-token', 'principal', lambda: self.admin.issue_token(*self._credentials))
        bearer = credential(value.get('access_token'))
        self._secrets.add(bearer)
        require(value.get('token_type', '').lower() == 'bearer' and type(value.get('expires_in')) is int
                and 0 < value['expires_in'] <= 86400, 'TOKEN_LIFETIME_INVALID')
        if 'scope' in value:
            require(value['scope'] == 'PRINCIPAL_ROLE:' + self.name, 'TOKEN_SCOPE_INVALID')
        self.journal['token'].update(issued=True, expires_in=value['expires_in'])
        self._material = {'aws-credentials': '[default]\naws_access_key_id = ' + access
                          + '\naws_secret_access_key = ' + secret + '\n', 'polaris-token': bearer}
        self._confirmed()
        return dict(self._material)

    def redact(self, text):
        require(type(text) is str, 'REDACTION_INPUT_INVALID')
        # IDs are not authentication secrets and may equal a public namespace
        # prefix (e.g. "shirokuma"). Redact their credential fields, not every
        # occurrence in operational identities or the fixed job-result schema.
        for value in self._identifiers:
            text = re.sub(r'(?i)([\"\']?(?:aws_access_key_id|access_key|accessKeyId|client_id|clientId)'
                          r'[\"\']?\s*[:=]\s*[\"\']?)' + re.escape(value)
                          + r'(?=[\"\'\s,;}\]]|$)', r'\1[REDACTED]', text)
        variants = set(self._secrets)
        for value in self._secrets:
            variants.add(base64.b64encode(value.encode()).decode())
            variants.add(quote(value, safe=''))
            variants.add(json.dumps(value)[1:-1])
        for value in sorted(variants, key=len, reverse=True):
            text = text.replace(value, '[REDACTED]')
        return text

    def cleanup(self):
        """Caller must first stop Spark and prove query drain; never drops data."""
        self._material, self._credentials = None, None
        cleanup = self.journal['cleanup']
        cleanup['deferred'] = []
        cleanup['verified'] = False
        if (self.journal['pending'] is None and not self.journal['entities']
                and not self.journal['bindings'] and not self.journal['policies']):
            cleanup['verified'] = True
            self._save()
            return deepcopy(cleanup)
        # An unknown mutation is deliberately not reconciled by adopting GET state.
        if self.journal['pending'] is not None:
            cleanup['deferred'].append('pending-unknown')
            self._save()
            return deepcopy(cleanup)
        try:
            self._guard()
            self._verify_entities()
            self._verify_memberships()
            for obj in reversed(ingress_policies(self.token)):
                ns, name = obj['metadata']['namespace'], obj['metadata']['name']
                if ns not in self.journal['policies']:
                    continue
                value = self.kube.get('NetworkPolicy', ns, name)
                uid = self.journal['policies'][ns]
                if value is not None:
                    verify_policy(value, obj, uid)
                    self._mutate('delete-ingress', ns, lambda: self.kube.delete(value))
                    require(self.kube.get('NetworkPolicy', ns, name) is None, 'INGRESS_DELETE_UNCONFIRMED')
                    self._confirmed()
                cleanup['removed'].append({'kind': 'NetworkPolicy', 'namespace': ns, 'uid': uid})
                del self.journal['policies'][ns]
                self._save()
            for target in reversed(self.journal['bindings'][:]):
                self._verify_entities(); self._verify_binding(target)
                self._mutate('revoke-role', target,
                    lambda: self.admin.request('DELETE', self.p[target] + '/' + self.name))
                require(self.admin.request('GET', self.p[target]).get('roles') == [], 'REVOKE_UNCONFIRMED')
                self.journal['bindings'].remove(target)
                self._confirmed()
            for kind in ('catalog_role', 'principal_role', 'principal'):
                if kind not in self.journal['entities']:
                    continue
                self._verify_entities()
                self._mutate('delete-entity', kind, lambda: self.admin.request('DELETE', self.p[kind]))
                self._absent(self.p[kind])
                cleanup['removed'].append({'kind': kind, 'name': self.name,
                    'createTimestamp': self.journal['entities'][kind]['createTimestamp']})
                del self.journal['entities'][kind]
                self._confirmed()
            cleanup['verified'] = True
        except BaseException as error:
            cleanup['deferred'].append('cleanup-unverified')
            try:
                self._save()
            finally:
                if not isinstance(error, Exception):
                    raise error
        self._save()
        return deepcopy(cleanup)
