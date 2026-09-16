#!/usr/bin/env python3
"""Build, run and verify the isolated Superset local experiment."""
from __future__ import annotations

import argparse
import fcntl
import http.cookiejar
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-superset'
CONTEXT = 'colima-mac-studio-solo'
NAME = 'shirokuma-local-superset'
VOLUME = NAME + '-metadata'
LABEL = 'io.shirokuma.local-experiment'
IMAGE = 'shirokuma.local/superset:6.1.0-experiment'
URL = 'http://127.0.0.1:18088'


class LocalError(RuntimeError):
    pass


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.chmod(0o600)


def command(args: list[str], *, data: str | None = None, timeout: int = 120,
            log: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(['rtk', 'proxy', *args], input=data, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if log:
        path = STATE / log
        path.write_text(result.stdout + result.stderr)
        path.chmod(0o600)
    if check and result.returncode:
        raise LocalError(f'{args[0]} operation failed (exit {result.returncode}); inspect {log or "local state"}')
    return result


def docker(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return command(['docker', '--context', CONTEXT, *args], **kwargs)


def inspect(kind: str, name: str) -> dict[str, Any] | None:
    result = docker(kind, 'inspect', name, check=False)
    if result.returncode:
        if 'no such' in result.stderr.lower() or 'not found' in result.stderr.lower():
            return None
        raise LocalError(f'could not inspect {kind} {name}')
    return json.loads(result.stdout)[0]


def require_owned(resource: dict[str, Any], kind: str) -> None:
    labels = resource.get('Labels', {}) if kind == 'volume' else resource.get('Config', {}).get('Labels', {})
    if (labels or {}).get(LABEL) != 'superset':
        raise LocalError(f'{kind} name collision: ownership label missing')


def require_local_runtime(resource: dict[str, Any]) -> None:
    require_owned(resource, 'container')
    cmd = resource.get('Config', {}).get('Cmd', [])
    host = resource.get('HostConfig', {})
    if ('--bind' not in cmd or cmd[cmd.index('--bind') + 1:cmd.index('--bind') + 2] != ['127.0.0.1:18088']
            or host.get('NetworkMode') != 'host' or host.get('Privileged')
            or not host.get('ReadonlyRootfs')):
        raise LocalError('existing Superset container does not match the local listener/isolation contract')


def image_identity() -> dict[str, Any]:
    image = inspect('image', IMAGE)
    if image is None or image['Architecture'] != 'arm64' or image['Os'] != 'linux':
        raise LocalError('build the pinned ARM64 local Superset image first')
    return {'id': image['Id'], 'architecture': image['Architecture'], 'os': image['Os'],
            'size_bytes': image['Size'], 'repo_digests': image.get('RepoDigests', [])}


def initialize_container(image: str, label: str, args: list[str], *,
                         private: str | None = None) -> None:
    name = NAME + '-init-' + label
    if inspect('container', name) is not None:
        raise LocalError(f'initialization container already exists: {name}; inspect it before retry')
    options = ['run', '--rm', '--name', name, '--label', LABEL + '=superset',
               '--network', 'none', '--cpus', '2', '--memory', '3g',
               '--mount', f'type=volume,src={VOLUME},dst=/app/superset_home']
    if private is not None:
        options += ['-i', '--user', 'root']
    try:
        docker(*options, '--entrypoint', args[0], image, *args[1:],
               data=private, timeout=180, log=label + '.log')
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        resource = inspect('container', name)
        if resource is not None:
            require_owned(resource, 'container')
            if resource['Image'] != image:
                raise LocalError('initialization container identity changed; preserved it')
            docker('rm', '--force', name)
        raise


def build() -> dict[str, Any]:
    docker('build', '--platform', 'linux/arm64', '--tag', IMAGE, str(HERE), timeout=300, log='build.log')
    identity = image_identity()
    save(STATE / 'image.json', identity)
    return identity


def trino_endpoint() -> str:
    result = command(['kubectl', '--context', CONTEXT, '-n', 'shirokuma-dev',
                      'get', 'service', 'trino-local', '-o', 'json'])
    service = json.loads(result.stdout)
    address = service['spec']['clusterIP']
    if not ipaddress.ip_address(address).is_private:
        raise LocalError('expected a private Trino ClusterIP')
    ports = service['spec']['ports']
    port = next(p['port'] for p in ports if p['name'] == 'http')
    return f'{address}:{port}'


def wait_ready(timeout: int = 90) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(URL + '/health', timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise LocalError('Superset did not become ready on loopback port 18088')


def start() -> dict[str, Any]:
    image = image_identity()
    container = inspect('container', NAME)
    if container is not None:
        require_local_runtime(container)
        if container['Image'] != image['id']:
            raise LocalError('existing container uses another image; stop/remove it without deleting metadata first')
        if not container['State']['Running']:
            docker('start', NAME)
        wait_ready()
        return status()
    volume = inspect('volume', VOLUME)
    if volume is not None:
        require_owned(volume, 'volume')
    else:
        docker('volume', 'create', '--label', LABEL + '=superset', VOLUME)
    private_path = STATE / 'private.json'
    if private_path.exists():
        private = json.loads(private_path.read_text())
    elif volume is not None:
        raise LocalError('metadata exists but local credentials are absent; preserve the volume and recover credentials')
    else:
        private = {'secret_key': secrets.token_urlsafe(64), 'admin_password': secrets.token_urlsafe(32)}
        save(private_path, private)
    # The secret is passed on stdin, never CLI arguments or container environment.
    initializer = (
        'import json,os,pathlib,sys; p=pathlib.Path("/app/superset_home/local-private.json"); '
        'raw=sys.stdin.read(); json.loads(raw); '
        'p.write_text(raw) if not p.exists() else None; '
        'os.chown("/app/superset_home",1000,1000); os.chmod("/app/superset_home",0o700); '
        'os.chown(p,1000,1000); os.chmod(p,0o600)'
    )
    initialize_container(image['id'], 'private', ['/app/.venv/bin/python', '-c', initializer],
                         private=json.dumps(private))
    for label, args in [('migration', ['superset', 'db', 'upgrade']),
                        ('initialization', ['superset', 'init']),
                        ('admin', ['/app/.venv/bin/python', '/app/local-experiment/bootstrap.py'])]:
        initialize_container(image['id'], label, args)
    docker('run', '--detach', '--name', NAME, '--label', LABEL + '=superset',
           '--network', 'host', '--cpus', '2', '--memory', '3g', '--pids-limit', '256',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
           '--tmpfs', '/tmp:rw,noexec,nosuid,size=256m',
           '--mount', f'type=volume,src={VOLUME},dst=/app/superset_home',
           image['id'], '/app/.venv/bin/gunicorn', '--bind', '127.0.0.1:18088',
           '--workers', '1', '--threads', '4', '--timeout', '120', 'superset.app:create_app()')
    wait_ready()
    return status()


def status() -> dict[str, Any]:
    resource = inspect('container', NAME)
    if resource is None:
        return {'running': False, 'container': NAME, 'metadata_preserved': inspect('volume', VOLUME) is not None}
    require_owned(resource, 'container')
    result = {'container': NAME, 'running': resource['State']['Running'], 'image': resource['Image'],
              'url': URL, 'network': resource['HostConfig']['NetworkMode'],
              'memory_bytes': resource['HostConfig']['Memory'],
              'nano_cpus': resource['HostConfig']['NanoCpus'], 'metadata_volume': VOLUME}
    save(STATE / 'runtime.json', result)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalError('local Superset API must not redirect authenticated requests')


class SupersetAPI:
    def __init__(self, password: str):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                  urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.token = ''
        self.csrf = ''
        self.token = self.request('POST', '/api/v1/security/login', {
            'username': 'local-admin', 'password': password, 'provider': 'db', 'refresh': False})['access_token']
        self.csrf = self.request('GET', '/api/v1/security/csrf_token/')['result']

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not path.startswith('/api/v1/') or '://' in path:
            raise LocalError('only local Superset API paths are allowed')
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.csrf:
            headers['X-CSRFToken'] = self.csrf
        req = urllib.request.Request(URL + path, method=method, headers=headers,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with self.opener.open(req, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Do not expose credential-bearing request payloads or login responses.
            raise LocalError(f'Superset {method} {path.split("?")[0]} returned HTTP {error.code}') from error


def first_named(api: SupersetAPI, kind: str, field: str, name: str) -> dict | None:
    items = api.request('GET', f'/api/v1/{kind}/?q=(page_size:100)')['result']
    matches = [item for item in items if item.get(field) == name]
    if len(matches) > 1:
        raise LocalError(f'ambiguous {kind} experiment objects')
    return matches[0] if matches else None


def smoke() -> dict[str, Any]:
    resource = inspect('container', NAME)
    if resource is None or not resource['State']['Running']:
        raise LocalError('start the owned Superset container before API verification')
    require_local_runtime(resource)
    wait_ready()
    private = json.loads((STATE / 'private.json').read_text())
    api = SupersetAPI(private['admin_password'])
    uri = f'trino://shirokuma-local-bi@{trino_endpoint()}/iceberg/smoke'
    db_name = 'Shirokuma local Iceberg'
    database = first_named(api, 'database', 'database_name', db_name)
    if database is None:
        database = api.request('POST', '/api/v1/database/', {
            'database_name': db_name, 'sqlalchemy_uri': uri, 'expose_in_sqllab': True,
            'allow_dml': False, 'allow_ctas': False, 'allow_cvas': False,
            'extra': json.dumps({'engine_params': {'connect_args': {'request_timeout': 30,
                'session_properties': {'query_max_run_time': '60s'}}}})})
    database_id = database['id']
    dataset = first_named(api, 'dataset', 'table_name', 'fixture_v1')
    if dataset is None:
        dataset = api.request('POST', '/api/v1/dataset/', {
            'database': database_id, 'catalog': 'iceberg', 'schema': 'smoke', 'table_name': 'fixture_v1'})
    dataset_id = dataset['id']
    saved_dataset = api.request('GET', f'/api/v1/dataset/{dataset_id}')['result']
    if (saved_dataset['database']['id'] != database_id or saved_dataset['schema'] != 'smoke'
            or saved_dataset['table_name'] != 'fixture_v1' or saved_dataset['catalog'] != 'iceberg'):
        raise LocalError('existing dataset name collides with another database or fixture')
    query_context = {'datasource': {'id': dataset_id, 'type': 'table'}, 'force': True,
                     'queries': [{'columns': ['id', 'name'], 'metrics': [], 'row_limit': 100,
                                  'orderby': [['id', True]]}],
                     'result_format': 'json', 'result_type': 'full'}
    data = api.request('POST', '/api/v1/chart/data', query_context)
    rows = data['result'][0]['data']
    if rows != [{'id': 1, 'name': 'shirokuma'}, {'id': 2, 'name': 'polaris'}]:
        raise LocalError('Superset query did not return the retained Iceberg fixture')
    chart_name = 'Shirokuma retained Iceberg fixture'
    chart = first_named(api, 'chart', 'slice_name', chart_name)
    params = {'viz_type': 'table', 'datasource': f'{dataset_id}__table', 'query_mode': 'raw',
              'all_columns': ['id', 'name'], 'row_limit': 100, 'order_by_cols': []}
    if chart is None:
        chart = api.request('POST', '/api/v1/chart/', {
            'slice_name': chart_name, 'viz_type': 'table', 'datasource_id': dataset_id,
            'datasource_type': 'table', 'params': json.dumps(params),
            'query_context': json.dumps(query_context)})
    chart_id = chart['id']
    dashboard_name = 'Shirokuma Local Lakehouse'
    dashboard = first_named(api, 'dashboard', 'dashboard_title', dashboard_name)
    if dashboard is None:
        dashboard = api.request('POST', '/api/v1/dashboard/', {
            'dashboard_title': dashboard_name, 'published': True, 'slug': 'shirokuma-local-lakehouse'})
    dashboard_id = dashboard['id']
    position = {
        'DASHBOARD_VERSION_KEY': 'v2',
        'ROOT_ID': {'id': 'ROOT_ID', 'type': 'ROOT', 'children': ['GRID_ID']},
        'GRID_ID': {'id': 'GRID_ID', 'type': 'GRID', 'children': ['ROW-local'], 'parents': ['ROOT_ID']},
        'ROW-local': {'id': 'ROW-local', 'type': 'ROW', 'children': ['CHART-local'],
                      'parents': ['ROOT_ID', 'GRID_ID'], 'meta': {'background': 'BACKGROUND_TRANSPARENT'}},
        'CHART-local': {'id': 'CHART-local', 'type': 'CHART', 'children': [],
                        'parents': ['ROOT_ID', 'GRID_ID', 'ROW-local'],
                        'meta': {'chartId': chart_id, 'width': 12, 'height': 50, 'sliceName': chart_name}},
    }
    api.request('PUT', f'/api/v1/dashboard/{dashboard_id}', {
        'position_json': json.dumps(position), 'json_metadata': json.dumps({'chart_configuration': {}})})
    api.request('PUT', f'/api/v1/chart/{chart_id}', {'dashboards': [dashboard_id]})
    saved_chart = api.request('GET', f'/api/v1/chart/{chart_id}')['result']
    saved_dashboard = api.request('GET', f'/api/v1/dashboard/{dashboard_id}')['result']
    if saved_chart['slice_name'] != chart_name or saved_dashboard['dashboard_title'] != dashboard_name:
        raise LocalError('saved BI objects did not round-trip')
    stored_context = json.loads(saved_chart['query_context'])
    if stored_context['datasource'] != query_context['datasource']:
        raise LocalError('saved chart points to another dataset')
    stored_context['force'] = True
    stored_rows = api.request('POST', '/api/v1/chart/data', stored_context)['result'][0]['data']
    layout = json.loads(saved_dashboard['position_json'])
    if (stored_rows != rows or layout['CHART-local']['meta']['chartId'] != chart_id
            or chart_name not in saved_dashboard['charts']):
        raise LocalError('saved dashboard chart association or query results differ')
    result = {'result': 'passed', 'scope': 'local-experiment-only', 'superset_version': '6.1.0',
              'database_id': database_id, 'dataset_id': dataset_id, 'chart_id': chart_id,
              'dashboard_id': dashboard_id, 'rows': rows, 'dashboard_saved': True,
              'saved_chart_query_verified': True,
              'dashboard_url': URL + '/superset/dashboard/shirokuma-local-lakehouse/',
              'retained_fixture_read_only': True}
    save(STATE / 'smoke.json', result)
    return result


def export() -> dict[str, Any]:
    resource = inspect('container', NAME)
    if resource is None or not resource['State']['Running']:
        raise LocalError('start the owned Superset container before consistent export')
    require_local_runtime(resource)
    destination = STATE / 'exports' / time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    backup_name = 'local-export-' + destination.name + '.db'
    script = (
        'import sqlite3; source=sqlite3.connect("/app/superset_home/superset.db"); '
        f'target=sqlite3.connect("/app/superset_home/{backup_name}"); '
        'source.backup(target); target.close(); source.close()'
    )
    docker('exec', NAME, '/app/.venv/bin/python', '-c', script)
    docker('cp', NAME + ':/app/superset_home/' + backup_name, str(destination / 'superset.db'))
    shutil.copyfile(STATE / 'private.json', destination / 'private.json')
    for file in destination.iterdir():
        file.chmod(0o600)
    return {'result': 'exported', 'private_export_directory': str(destination),
            'includes': ['superset.db', 'private.json']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'start', 'status', 'smoke', 'restart', 'stop', 'remove', 'export'])
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    lock = (STATE / 'lifecycle.lock').open('a')
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LocalError('another local Superset operation is running')
        if args.action in ('build', 'start', 'status', 'smoke', 'export'):
            result = globals()[args.action]()
        else:
            resource = inspect('container', NAME)
            if resource is not None:
                require_owned(resource, 'container')
                if args.action == 'remove':
                    docker('stop', '--time', '30', NAME)
                    docker('rm', NAME)
                else:
                    docker(args.action, NAME)
                if args.action == 'restart':
                    wait_ready()
            result = status()
        print(json.dumps(result))
        return 0
    except (LocalError, subprocess.TimeoutExpired, OSError) as error:
        print(json.dumps({'result': 'failed', 'error': str(error)}))
        return 1
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
