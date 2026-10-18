"""Restore namespace-scoped data/purge privileges in the dedicated local lab.

Run after catalog bootstrap: python3 tools/local_experiment/polaris_permissions.py
Metadata/drop authority remains in catalog_admin; this adds only TABLE_WRITE_DATA
to the application namespaces, as required by Polaris DROP_TABLE_WITH_PURGE.
"""
import base64
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


CATALOG = 'shirokuma_l1'
ROLE = 'local_experiment_purge'
NAMESPACES = ('raw', 'sandbox', 'marts', 'staging')


def ensure_permissions(request):
    """Idempotently wire the bounded role and add only missing namespace grants."""
    base = '/api/management/v1'
    roles_path = base + '/catalogs/' + CATALOG + '/catalog-roles'
    roles = request('GET', roles_path)['roles']
    if not any(role['name'] == ROLE for role in roles):
        request('POST', roles_path, {'catalogRole': {'name': ROLE,
                'properties': {'shirokuma.dev/scope': 'local-experiment'}}})
    membership = base + '/principal-roles/service_admin/catalog-roles/' + CATALOG
    if not any(role['name'] == ROLE for role in request('GET', membership)['roles']):
        request('PUT', membership, {'catalogRole': {'name': ROLE}})
    grant_path = roles_path + '/' + ROLE + '/grants'
    existing = request('GET', grant_path)['grants']
    added = []
    for namespace in NAMESPACES:
        grant = dict(type='namespace', namespace=[namespace], privilege='TABLE_WRITE_DATA')
        if not any(all(old.get(key) == value for key, value in grant.items()) for old in existing):
            request('PUT', grant_path, {'grant': grant})
            added.append(namespace)
    verified = request('GET', grant_path)['grants']
    for namespace in NAMESPACES:
        if not any(g.get('type') == 'namespace' and g.get('namespace') == [namespace]
                   and g.get('privilege') == 'TABLE_WRITE_DATA' for g in verified):
            raise RuntimeError('Polaris namespace permission verification failed')
    return added


def main():
    kube = ['kubectl', '--context', os.environ.get('KUBE_CONTEXT', 'colima-mac-studio-solo'),
            '-n', 'shirokuma-dev']
    result = subprocess.run(kube + ['get', 'secret', 'polaris-root-credentials', '-o', 'json'],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('cannot read dedicated Polaris credential Secret')
    try:
        data = json.loads(result.stdout)['data']
        credentials = {key: base64.b64decode(data[key]).decode()
                       for key in ('client_id', 'client_secret', 'realm')}
    except (ValueError, KeyError):
        raise RuntimeError('invalid dedicated Polaris credential Secret') from None
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    forward = subprocess.Popen(kube + ['port-forward', '--address', '127.0.0.1',
                                      'svc/polaris', str(port) + ':8181'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if forward.poll() is not None:
                raise RuntimeError('Polaris local port-forward failed')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Polaris local port-forward timed out')
        url = 'http://127.0.0.1:' + str(port)
        headers = {'Polaris-Realm': credentials['realm'], 'Content-Type': 'application/json'}
        basic = base64.b64encode((credentials['client_id'] + ':' + credentials['client_secret']).encode()).decode()
        token_request = urllib.request.Request(url + '/api/catalog/v1/oauth/tokens',
            urllib.parse.urlencode({'grant_type': 'client_credentials', 'scope': 'PRINCIPAL_ROLE:ALL'}).encode(),
            {**headers, 'Authorization': 'Basic ' + basic,
             'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(token_request, timeout=20) as response:
            headers['Authorization'] = 'Bearer ' + json.load(response)['access_token']

        def request(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            try:
                with urllib.request.urlopen(urllib.request.Request(url + path, data=data,
                        headers=headers, method=method), timeout=20) as response:
                    content = response.read()
                    return json.loads(content) if content else {}
            except urllib.error.HTTPError as error:
                raise RuntimeError('Polaris permissions API returned HTTP ' + str(error.code)) from None

        added = ensure_permissions(request)
        print('Verified TABLE_WRITE_DATA on raw/sandbox/marts/staging; added:', ', '.join(added) or 'none')
    finally:
        forward.terminate()
        forward.wait(timeout=10)


if __name__ == '__main__':
    main()
