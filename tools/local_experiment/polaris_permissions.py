"""Provision permanent Polaris consumers using operator credentials."""
import argparse
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MANAGEMENT = '/api/management/v1'
S3 = 'http://seaweedfs-s3.shirokuma-storage.svc.cluster.local:8333'
# Mirror the static locals in opentofu/dev/catalog.tf, without a runtime registry.
DATABASES = {'sales': ('polaris', 'SALES', 'shirokuma_l1'),
             'finance': ('polaris-finance', 'FINANCE', 'finance')}
OWNER = {'shirokuma.dev/managed-by': 'permanent-polaris-consumers'}
# Polaris 1.8 spec/polaris-management-service.yml CatalogPrivilege enum.
TRINO = ('CATALOG_READ_PROPERTIES', 'NAMESPACE_LIST', 'NAMESPACE_READ_PROPERTIES',
         'NAMESPACE_CREATE', 'NAMESPACE_WRITE_PROPERTIES', 'NAMESPACE_DROP',
         'TABLE_CREATE', 'TABLE_LIST', 'TABLE_FULL_METADATA', 'TABLE_DROP',
         'TABLE_READ_DATA', 'TABLE_WRITE_DATA', 'VIEW_CREATE', 'VIEW_LIST',
         'VIEW_READ_PROPERTIES', 'VIEW_WRITE_PROPERTIES', 'VIEW_FULL_METADATA', 'VIEW_DROP')
BOOTSTRAP = ('NAMESPACE_LIST', 'NAMESPACE_READ_PROPERTIES', 'TABLE_CREATE',
             'TABLE_LIST', 'TABLE_READ_PROPERTIES', 'TABLE_WRITE_PROPERTIES',
             'TABLE_READ_DATA', 'TABLE_WRITE_DATA', 'TABLE_ADD_SNAPSHOT',
             'TABLE_SET_SNAPSHOT_REF', 'TABLE_SET_PROPERTIES', 'TABLE_REMOVE_PROPERTIES')


class Failure(RuntimeError):
    """Fixed messages only; never include server bodies or credentials."""


class APIError(Failure):
    def __init__(self, status):
        super().__init__('Polaris API returned HTTP ' + str(status))
        self.status = status


def optional(request, path):
    try:
        return request('GET', path)
    except APIError as error:
        if error.status != 404:
            raise
        return None


def private_save(path, state):
    temporary = path.with_name(path.name + '.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as out:
            os.chmod(temporary, 0o600)
            json.dump(state, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_credentials(path):
    if not path.exists():
        return {'version': 1, 'databases': {}}
    if path.stat().st_mode & 0o077:
        raise Failure('credential output file must have private permissions')
    value = json.loads(path.read_text())
    if value.get('version') != 1 or not isinstance(value.get('databases'), dict):
        raise Failure('invalid credential output file')
    return value


def owned(entity, name):
    if entity.get('name') != name or entity.get('properties') != OWNER:
        raise Failure('existing principal or role is not owned by this provisioner')


def ensure_role(request, path, key, name):
    entity = optional(request, path + '/' + name)
    if entity is None:
        request('POST', path, {key: {'name': name, 'properties': OWNER}})
        entity = request('GET', path + '/' + name)
    owned(entity, name)


def expected_grants(consumer):
    if consumer == 'trino':
        return [dict(type='catalog', privilege=p) for p in TRINO]
    return [dict(type='catalog', privilege='CATALOG_READ_PROPERTIES')] + [
        dict(type='namespace', namespace=['smoke'], privilege=p) for p in BOOTSTRAP]


def ensure_membership(request, path, key, name):
    roles = request('GET', path)['roles']
    if any(r['name'] != name for r in roles):
        raise Failure('unexpected consumer role membership')
    if not roles:
        request('PUT', path, {key: {'name': name}})
    if [r['name'] for r in request('GET', path)['roles']] != [name]:
        raise Failure('consumer membership verification failed')


def provision(request, database, state, persist, region='us-east-1'):
    """Root-only provisioning; save issued credentials before another API call."""
    _, realm, catalog = DATABASES[database]
    location = 's3://shirokuma-lakehouse/databases/' + database
    definition = {'name': catalog, 'type': 'INTERNAL',
                  'properties': {'default-base-location': location,
                                 'polaris.config.drop-with-purge.enabled': 'true'},
                  'storageConfigInfo': {'storageType': 'S3', 'allowedLocations': [location],
                                       'region': region, 'endpoint': S3, 'endpointInternal': S3,
                                       'stsUnavailable': True, 'pathStyleAccess': True}}
    catalog_path = MANAGEMENT + '/catalogs/' + catalog
    existing = optional(request, catalog_path)
    if existing is None:
        request('POST', MANAGEMENT + '/catalogs', {'catalog': definition})
        existing = request('GET', catalog_path)
    if (any(existing.get(k) != definition[k] for k in ('name', 'type'))
            or any(existing.get('properties', {}).get(k) != v for k, v in definition['properties'].items())
            or any(existing.get('storageConfigInfo', {}).get(k) != v for k, v in definition['storageConfigInfo'].items())):
        raise Failure('existing catalog storage binding differs from dedicated database')
    namespaces = '/api/catalog/v1/' + catalog + '/namespaces'
    existing_namespaces = request('GET', namespaces)['namespaces']
    for namespace in ('raw', 'sandbox', 'marts', 'staging', 'smoke'):
        if [namespace] not in existing_namespaces:
            request('POST', namespaces, {'namespace': [namespace], 'properties': {}})
    for consumer in ('trino', 'iceberg-bootstrap'):
        base_name = 'shirokuma_' + database + '_' + consumer.replace('-', '_')
        role, cat_role = base_name + '_principal', base_name + '_content'
        entry = state['databases'].setdefault(database, {})
        saved = entry.get(consumer)
        if saved is not None and not isinstance(saved, dict):
            raise Failure('invalid saved consumer credentials')
        name = saved.get('principal_name', base_name) if saved is not None else base_name
        if not isinstance(name, str) or not re.fullmatch(re.escape(base_name) + r'(?:__g[1-9][0-9]*)?', name):
            raise Failure('invalid saved consumer principal name')
        path = MANAGEMENT + '/principals/' + name
        principal = optional(request, path)
        if principal is None:
            if consumer in entry:
                raise Failure('saved credentials exist but principal is missing; operator recovery required')
            created = request('POST', MANAGEMENT + '/principals', {
                'principal': {'name': name, 'properties': OWNER}, 'credentialRotationRequired': False})
            credentials = created['credentials']
            saved = {'client_id': credentials['clientId'], 'client_secret': credentials['clientSecret'],
                     'realm': realm, 'principal_name': name}
            entry[consumer] = saved
            persist(state)
            principal = created['principal']
        owned(principal, name)
        if not saved:
            raise Failure('principal exists but saved credentials are missing; operator recovery required')
        if (not saved.get('client_secret') or saved.get('realm') != realm
                or principal.get('clientId') != saved.get('client_id')):
            raise Failure('saved credentials do not match dedicated principal')
        ensure_role(request, MANAGEMENT + '/principal-roles', 'principalRole', role)
        role_path = catalog_path + '/catalog-roles'
        ensure_role(request, role_path, 'catalogRole', cat_role)
        for item in request('GET', MANAGEMENT + '/catalogs')['catalogs']:
            member_path = MANAGEMENT + '/principal-roles/' + role + '/catalog-roles/' + urllib.parse.quote(item['name'], safe='')
            assigned = request('GET', member_path)['roles']
            if any(item['name'] != catalog or r['name'] != cat_role for r in assigned):
                raise Failure('unexpected cross-catalog consumer membership')
        # Validate the principal membership before granting anything further.
        assigned = request('GET', path + '/principal-roles')['roles']
        if any(r['name'] != role for r in assigned):
            raise Failure('unexpected consumer role membership')
        grants_path = role_path + '/' + cat_role + '/grants'
        wanted = expected_grants(consumer)
        grants = request('GET', grants_path)['grants']
        if any(g not in wanted for g in grants):
            raise Failure('unexpected consumer grant')
        for grant in wanted:
            if grant not in grants:
                request('PUT', grants_path, {'grant': grant})
        verified = request('GET', grants_path)['grants']
        if len(verified) != len(wanted) or any(g not in verified for g in wanted):
            raise Failure('consumer grant verification failed')
        ensure_membership(request, MANAGEMENT + '/principal-roles/' + role + '/catalog-roles/' + catalog,
                          'catalogRole', cat_role)
        ensure_membership(request, path + '/principal-roles', 'principalRole', role)


def http_json(url, headers, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url + path, data=data,
                headers=headers, method=method), timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as error:
        raise APIError(error.code) from None
    except (OSError, ValueError):
        raise Failure('Polaris request failed; response suppressed') from None


@contextmanager
def operator_api(service, realm):
    kube = ['rtk', 'proxy', 'kubectl', '--context', os.environ.get('KUBE_CONTEXT', 'colima-mac-studio-solo'),
            '-n', 'shirokuma-dev']
    result = subprocess.run(kube + ['get', 'secret', service + '-root-credentials', '-o', 'json'],
                            capture_output=True, text=True)
    if result.returncode:
        raise Failure('cannot read operator credential Secret')
    data = json.loads(result.stdout)['data']
    credentials = {k: base64.b64decode(data[k], validate=True).decode()
                   for k in ('client_id', 'client_secret', 'realm')}
    if credentials['realm'] != realm:
        raise Failure('operator credential realm mismatch')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    forward = subprocess.Popen(kube + ['port-forward', '--address', '127.0.0.1',
                                      'svc/' + service, str(port) + ':8181'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if forward.poll() is not None:
                raise Failure('Polaris local port-forward failed')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise Failure('Polaris local port-forward timed out')
        url = 'http://127.0.0.1:' + str(port)
        basic = base64.b64encode((credentials['client_id'] + ':' + credentials['client_secret']).encode()).decode()
        token_request = urllib.request.Request(url + '/api/catalog/v1/oauth/tokens',
            urllib.parse.urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            {'Polaris-Realm': realm, 'Authorization': 'Basic ' + basic,
             'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(token_request, timeout=20) as response:
                token = json.load(response)['access_token']
        except (OSError, ValueError, KeyError):
            raise Failure('operator token request failed; response suppressed') from None
        headers = {'Polaris-Realm': realm, 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
        yield lambda method, path, body=None: http_json(url, headers, method, path, body)
    finally:
        forward.terminate()
        forward.wait(timeout=10)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-out', type=Path, required=True)
    parser.add_argument('--s3-region', default='us-east-1')
    args = parser.parse_args(argv)
    try:
        state = load_credentials(args.credentials_out)
        private_save(args.credentials_out, state)
        for database, (service, realm, _) in DATABASES.items():
            with operator_api(service, realm) as request:
                provision(request, database, state, lambda value: private_save(args.credentials_out, value), args.s3_region)
            print(database + ': trino and iceberg-bootstrap verified')
        return 0
    except Failure as error:
        print('Polaris provisioning failed: ' + str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Polaris provisioning failed; output suppressed; inspect operator state', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
