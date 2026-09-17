#!/usr/bin/env python3
"""Owned Grafana runtime with a bounded, SELECT-only AgentHouse reader."""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STATE = ROOT / 'tmp/local-experiment/grafana'
PINS = json.loads((HERE / 'inputs.lock.json').read_text())
IMAGE = PINS['grafana']['image']
NAME = 'shirokuma-local-grafana'
VOLUME = NAME + '-data'
NETWORK = NAME + '-agenthouse'
FRONTEND_NETWORK = NAME + '-frontend'
LABEL = 'shirokuma.dev.component'
OWNER = 'grafana-local'
URL = 'http://127.0.0.1:18030'
DATASOURCE_UID = 'shirokuma-agenthouse-readonly'
DASHBOARD_UID = 'shirokuma-agenthouse-local'
CH_CONFIG = '/etc/clickhouse-server/users.d/shirokuma-grafana-reader.xml'
CONTEXT = 'colima-mac-studio-solo'
PLUGIN_QUERY_TIMEOUT = 25
BASE_READER_TABLES = ('pawprints',)
HISTORY_READER_TABLES = ('pawprints', 'benchmark_runs', 'benchmark_queries', 'query_history_runs', 'query_history_queries')
SETTINGS = {'readonly': '1', 'max_threads': '2', 'max_memory_usage': '268435456',
            'max_execution_time': '30', 'max_rows_to_read': '1000000', 'max_result_rows': '10000',
            'max_result_bytes': '4194304', 'result_overflow_mode': 'throw', 'read_overflow_mode': 'throw'}
sys.path.insert(0, str(HERE.parent))
from local_experiment import check_context as check_kubernetes_context
spec = importlib.util.spec_from_file_location('grafana_agenthouse_runtime', HERE.parent / 'agenthouse/manage.py')
AH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AH)


class GrafanaError(RuntimeError):
    pass


class QueryRejected(GrafanaError):
    """Only a bounded HTTP status and server exception number, never SQL/body."""
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__('local database rejected the bounded query')


def require(condition, message):
    if not condition:
        raise GrafanaError(message)


def private_write(path, value):
    require(not path.is_symlink(), 'private evidence path must not be a symlink')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    content = value if isinstance(value, str) else json.dumps(value, sort_keys=True, indent=2) + '\n'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def private_read(path):
    require(not path.is_symlink() and path.is_file(), 'private credentials or receipt are missing')
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size < 65536,
            'private credentials or receipt ownership differs')
    return json.loads(path.read_text())


def docker(*args, required=True, input_text=None, timeout=180):
    result = subprocess.run(['rtk', 'proxy', 'docker', '--context', CONTEXT, *args], cwd=ROOT,
                            input=input_text, text=True, capture_output=True, timeout=timeout)
    if required and result.returncode:
        raise GrafanaError('scoped Docker operation failed: ' + args[0])
    return result.stdout if result.returncode == 0 else None


def check_context():
    check_kubernetes_context()
    context = json.loads(docker('context', 'inspect', CONTEXT))[0]
    endpoint = context['Endpoints']['docker']['Host']
    require(endpoint.startswith('unix://') and endpoint.endswith('/.colima/mac-studio-solo/docker.sock'),
            'requires the dedicated Colima Docker socket')
    require(docker('info', '--format', '{{.Architecture}}').strip() in {'arm64', 'aarch64'},
            'requires native ARM64 Docker runtime')


def inspect(kind, name):
    raw = docker(kind, 'inspect', name, required=False)
    if raw is None:
        return None
    values = json.loads(raw)
    require(type(values) is list and len(values) == 1, 'ambiguous owned Docker resource')
    value = values[0]
    labels = value.get('Config', {}).get('Labels', {}) if kind == 'container' else value.get('Labels', {})
    require(labels.get(LABEL) == OWNER, 'refusing a resource not owned by local Grafana')
    return value


def credentials(*, create=False):
    path = STATE / 'credentials.json'
    if not path.exists():
        require(create, 'original Grafana credentials are required')
        require(inspect('container', NAME) is None and inspect('volume', VOLUME) is None,
                'retained Grafana resources require their original credentials')
        value = {'schema_version': 1, 'owner_token': uuid.uuid4().hex, 'admin_password': secrets.token_hex(32),
                 'secret_key': secrets.token_hex(32), 'reader_password': secrets.token_hex(32)}
        value['reader_user'] = 'shirokuma_grafana_ro_' + value['owner_token']
        private_write(path, value)
    value = private_read(path)
    require(set(value) == {'schema_version', 'owner_token', 'admin_password', 'secret_key', 'reader_password', 'reader_user'}
            and type(value['schema_version']) is int and value['schema_version'] == 1, 'unexpected Grafana credential format')
    require(type(value['owner_token']) is str and re.fullmatch('[0-9a-f]{32}', value['owner_token']), 'invalid owner token')
    require(value['reader_user'] == 'shirokuma_grafana_ro_' + value['owner_token'], 'invalid scoped reader identity')
    require(all(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key])
                for key in ('admin_password', 'secret_key', 'reader_password')), 'invalid private credential values')
    return value


def environment(credential):
    return {'GF_SERVER_HTTP_PORT': '3000', 'GF_SERVER_ROOT_URL': URL + '/', 'GF_SERVER_DOMAIN': '127.0.0.1',
        'GF_SECURITY_ADMIN_USER': 'admin', 'GF_SECURITY_ADMIN_PASSWORD': credential['admin_password'],
        'GF_SECURITY_SECRET_KEY': credential['secret_key'], 'GF_SECURITY_COOKIE_SAMESITE': 'strict',
        'GF_USERS_ALLOW_SIGN_UP': 'false', 'GF_USERS_ALLOW_ORG_CREATE': 'false',
        'GF_USERS_DEFAULT_THEME': 'light',
        'GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH': '/etc/grafana/shirokuma-dashboards/agenthouse.json',
        'GF_SNAPSHOTS_ENABLED': 'false', 'GF_SNAPSHOTS_EXTERNAL_ENABLED': 'false',
        'GF_AUTH_ANONYMOUS_ENABLED': 'true', 'GF_AUTH_ANONYMOUS_ORG_ROLE': 'Viewer',
        'GF_ANALYTICS_REPORTING_ENABLED': 'false', 'GF_ANALYTICS_CHECK_FOR_UPDATES': 'false',
        'GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES': 'false', 'GF_PLUGINS_PREINSTALL_DISABLED': 'true',
        'GF_PLUGINS_PREINSTALL_AUTO_UPDATE': 'false', 'GF_PLUGINS_PLUGIN_ADMIN_ENABLED': 'false',
        'GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED': 'true', 'GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS': '',
        'GF_PATHS_PLUGINS': '/opt/grafana/plugins', 'GF_LOG_MODE': 'console',
        'GF_UNIFIED_ALERTING_ENABLED': 'false', 'GF_EXPLORE_ENABLED': 'false',
        'SHIROKUMA_GRAFANA_READER': credential['reader_user'],
        'SHIROKUMA_GRAFANA_READER_PASSWORD': credential['reader_password']}


def validate_archive(raw):
    pin = PINS['plugin']
    require(len(raw) == pin['archive_bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'],
            'official plugin archive differs from the locked hash')
    archive = zipfile.ZipFile(io.BytesIO(raw))
    prefix = pin['id'] + '/'
    files = {}
    require(sum(item.file_size for item in archive.infolist()) <= 128 * 1024**2, 'plugin expanded size exceeds bound')
    for item in archive.infolist():
        name = PurePosixPath(item.filename)
        require(not name.is_absolute() and '..' not in name.parts and item.filename.startswith(prefix)
                and not stat.S_ISLNK(item.external_attr >> 16), 'plugin archive path is not confined')
        if not item.is_dir():
            relative = item.filename[len(prefix):]
            require(relative and relative not in files, 'duplicate plugin file')
            files[relative] = archive.read(item)
    metadata = json.loads(files['plugin.json'])
    require(metadata['id'] == pin['id'] and metadata['info']['version'] == pin['version'] and metadata.get('backend') is True,
            'plugin identity differs from the locked version')
    signed = files['MANIFEST.txt'].decode().replace('\r\n', '\n')
    require(signed.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n') and '\n-----BEGIN PGP SIGNATURE-----' in signed,
            'official plugin must retain its signed manifest')
    payload = signed.split('\n-----BEGIN PGP SIGNATURE-----', 1)[0]
    manifest = json.loads(payload[payload.index('{'):])
    require(manifest['plugin'] == pin['id'] and manifest['version'] == pin['version']
            and manifest['signedByOrg'] == 'grafana' and manifest['signatureType'] == 'grafana',
            'plugin signed manifest identity differs')
    require(set(files) == set(manifest['files']) | {'MANIFEST.txt'}, 'plugin files differ from signed manifest')
    require(all(hashlib.sha256(files[name]).hexdigest() == digest for name, digest in manifest['files'].items()),
            'plugin file hash differs from signed manifest')
    binary = files[metadata['executable'] + '_linux_arm64']
    require(binary[:6] == b'\x7fELF\x02\x01' and int.from_bytes(binary[18:20], 'little') == 183,
            'plugin backend must be native AArch64 ELF')
    return files


def prepare_plugin():
    pin = PINS['plugin']
    archive = STATE / 'downloads/grafana-clickhouse-datasource-4.21.3-linux-arm64.zip'
    if not archive.exists():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(pin['url'], timeout=60) as response:
            raw = response.read(64 * 1024**2 + 1)
        validate_archive(raw)
        archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(raw)
    require(not archive.is_symlink(), 'plugin archive cannot be a symlink')
    files = validate_archive(archive.read_bytes())
    destination = STATE / 'plugins' / pin['id']
    if destination.exists():
        require(not destination.is_symlink(), 'plugin directory cannot be a symlink')
        require(not any(path.is_symlink() for path in destination.rglob('*')), 'plugin tree cannot contain symlinks')
        existing = {str(path.relative_to(destination)): path for path in destination.rglob('*') if path.is_file()}
        require(set(existing) == set(files) and all(not path.is_symlink() and path.read_bytes() == files[name]
                    for name, path in existing.items()), 'installed plugin differs from pinned archive')
    else:
        for name, content in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            path.write_bytes(content)
            path.chmod(0o755 if name == 'gpx_clickhouse_linux_arm64' else 0o644)
    # Grafana performs the cryptographic signature check at startup. File hashes
    # and the official archive are checked here, without accepting unsigned plugins.
    return {'id': pin['id'], 'version': pin['version'], 'sha256': pin['sha256'], 'native_arm64': True}


def prepare():
    credential = credentials(create=True)
    plugin = prepare_plugin()
    private_write(STATE / 'container.env', ''.join(key + '=' + value + '\n' for key, value in environment(credential).items()))
    return {'result': 'prepared', 'plugin': plugin, 'runtime_started': False, 'reader_created': False}


def agenthouse():
    raw = docker('container', 'inspect', AH.NAME)
    value = json.loads(raw)[0]
    config, host = value['Config'], value['HostConfig']
    mounts = value.get('Mounts', [])
    require(config.get('Labels', {}).get(AH.LABEL) == 'agenthouse-local' and config['Image'] == AH.IMAGE
            and value['State']['Running'] is True
            and host.get('PortBindings') == {'8123/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18123'}]}
            and host.get('NanoCpus') == 2 * 10**9 and host.get('Memory') == 4 * 1024**3
            and len(mounts) == 1 and all(mounts[0].get(key) == expected for key, expected in
                {'Type': 'volume', 'Name': AH.VOLUME, 'Destination': '/var/lib/clickhouse', 'RW': True}.items()),
            'existing AgentHouse scope differs; refusing runtime changes')
    return value


def reader_xml(credential, tables=BASE_READER_TABLES):
    require(tables in (BASE_READER_TABLES, HISTORY_READER_TABLES), 'reader scope is not a fixed table set')
    user = credential['reader_user']
    profile_name = user + '_profile'
    root = ET.Element('clickhouse')
    root.append(ET.Comment(' Shirokuma local Grafana owner ' + credential['owner_token'] + ' '))
    profile = ET.SubElement(ET.SubElement(root, 'profiles'), profile_name)
    for name, value in SETTINGS.items():
        ET.SubElement(profile, name).text = value
    constraints = ET.SubElement(profile, 'constraints')
    for name in SETTINGS:
        setting = ET.SubElement(constraints, name)
        if name == 'max_execution_time':
            ET.SubElement(setting, 'min').text = '1'
            ET.SubElement(setting, 'max').text = '30'
            ET.SubElement(setting, 'changeable_in_readonly')
        else:
            ET.SubElement(setting, 'readonly')
    account = ET.SubElement(ET.SubElement(root, 'users'), user)
    ET.SubElement(account, 'password_sha256_hex').text = hashlib.sha256(credential['reader_password'].encode()).hexdigest()
    ET.SubElement(ET.SubElement(account, 'networks'), 'ip').text = '::/0'
    ET.SubElement(account, 'profile').text = profile_name
    ET.SubElement(account, 'quota').text = 'default'
    grants = ET.SubElement(account, 'grants')
    for table in tables:
        ET.SubElement(grants, 'query').text = 'GRANT SELECT ON agenthouse.' + table
    return ET.tostring(root, encoding='unicode') + '\n'


def ensure_reader(credential, current):
    old = docker('exec', '--user', 'root', current['Id'], 'cat', CH_CONFIG, required=False)
    base, history = reader_xml(credential), reader_xml(credential, HISTORY_READER_TABLES)
    require(old is None or old in (base, history), 'existing reader config differs; refusing overwrite or credential rotation')
    # Restart preserves the exact known scope. Only history-enable widens it.
    content = old if old is not None else base
    private_write(STATE / 'reader-users.xml', content)
    receipt = {'agenthouse_id': current['Id'], 'config_path': CH_CONFIG,
               'config_sha256': hashlib.sha256(content.encode()).hexdigest(), 'owner_token': credential['owner_token']}
    private_write(STATE / 'reader-config-receipt.json', {**receipt, 'outcome': 'pending'})
    if old is None:
        # stdin contains only this dedicated XML; a hard link publishes it without
        # replacing a concurrent or pre-existing config. No retained config is edited.
        script = 'set -eu; umask 077; f=$(mktemp /etc/clickhouse-server/users.d/.shirokuma-grafana.XXXXXX); trap \'rm -f "$f"\' EXIT; cat > "$f"; chown clickhouse:clickhouse "$f"; chmod 600 "$f"; ln "$f" ' + CH_CONFIG
        docker('exec', '--user', 'root', '-i', current['Id'], '/bin/sh', '-c', script, input_text=content)
    AH.Client().query('SYSTEM RELOAD CONFIG')
    deadline = time.monotonic() + 30
    while True:
        try:
            result = verify_reader(credential)
            private_write(STATE / 'reader-config-receipt.json', {**receipt, 'outcome': 'verified', **result})
            return result
        except (GrafanaError, AH.AgentHouseError):
            if time.monotonic() >= deadline:
                raise GrafanaError('dedicated reader configuration was not verified') from None
            time.sleep(1)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GrafanaError('local runtime redirects are refused')


def request(url, *, data=None, username=None, password=None, timeout=35):
    require(url.startswith(URL + '/') or url.startswith(AH.URL + '/'), 'request escaped the fixed local endpoints')
    headers = {'Content-Type': 'application/json'}
    if username is not None:
        headers['Authorization'] = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(8 * 1024**2 + 1)
        require(len(raw) <= 8 * 1024**2, 'local response exceeds bound')
        return raw.decode()
    except urllib.error.HTTPError as error:
        # ClickHouse exposes its numeric exception code separately from sensitive
        # error text. No authentication, SQL or raw response is retained.
        code = error.headers.get('X-ClickHouse-Exception-Code', '') if error.headers else ''
        if url.startswith(AH.URL + '/') and re.fullmatch('[0-9]{1,5}', code):
            raise QueryRejected(error.code, int(code)) from None
        raise GrafanaError('bounded local HTTP request failed') from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise GrafanaError('bounded local HTTP request failed') from None


def reader_query(credential, sql):
    return request(AH.URL + '/?wait_end_of_query=1', data=sql.encode(), username=credential['reader_user'],
                   password=credential['reader_password'])


def verify_reader(credential):
    user = credential['reader_user']
    require(reader_query(credential, 'SELECT currentUser()').strip() == user, 'dedicated reader authentication differs')
    admin = AH.Client()
    rows = json.loads(admin.query("SELECT access_type,database,table,column,is_partial_revoke,grant_option FROM system.grants WHERE user_name='" + user + "' FORMAT JSON"))['data']
    tables = tuple(sorted(row.get('table', '') for row in rows))
    require(tables in (tuple(sorted(BASE_READER_TABLES)), tuple(sorted(HISTORY_READER_TABLES)))
            and sorted(rows, key=lambda row: row['table']) == [
                {'access_type': 'SELECT', 'database': 'agenthouse', 'table': table, 'column': None,
                 'is_partial_revoke': 0, 'grant_option': 0} for table in tables],
            'reader grants differ from a fixed read-only scope')
    roles = json.loads(admin.query("SELECT granted_role_name FROM system.role_grants WHERE user_name='" + user + "' FORMAT JSON"))['data']
    require(not roles, 'reader must not inherit roles')
    selected = ','.join("'" + key + "'" for key in SETTINGS)
    settings = json.loads(reader_query(credential, 'SELECT name,value FROM system.settings WHERE name IN (' + selected + ') FORMAT JSON'))['data']
    require({row['name']: row['value'] for row in settings} == SETTINGS, 'reader settings differ from bounded read-only profile')
    require(reader_query(credential, 'SELECT 1 SETTINGS max_execution_time=5').strip() == '1', 'plugin timeout setting is not permitted')
    for sql in ('SELECT 1 SETTINGS readonly=0', 'SELECT 1 SETTINGS max_execution_time=31', 'SELECT 1 SETTINGS max_result_rows=0'):
        try:
            reader_query(credential, sql)
        except QueryRejected as error:
            require(error.status in {400, 403, 500} and error.code in {164, 452, 472},
                    'constraint probe failed without the expected database rejection')
        else:
            raise GrafanaError('reader can bypass a mandatory query constraint')
    for table in tables:
        for privilege in ('INSERT', 'ALTER', 'DROP TABLE', 'CREATE TABLE', 'TRUNCATE'):
            require(reader_query(credential, 'CHECK GRANT ' + privilege + ' ON agenthouse.' + table).strip() == '0',
                    'reader has an unexpected write privilege')
    require(reader_query(credential, 'CHECK GRANT SELECT ON system.users').strip() == '0', 'reader has an administrative privilege')
    counts = {table: int(reader_query(credential, 'SELECT count() FROM agenthouse.' + table + ' FINAL').strip()) for table in tables}
    require(all(value >= 0 for value in counts.values()) and reader_query(credential, 'SELECT currentUser(), 1').strip() == user + '\t1',
            'positive reader confirmation failed after denial probes')
    return {'reader': user, 'select_only': True, 'logical_pawprints': counts['pawprints'], 'logical_rows': counts,
            'max_execution_seconds': 30, 'max_result_rows': 10000, 'max_memory_bytes': 268435456}


def ensure_network(current):
    network = inspect('network', NETWORK)
    if network is None:
        docker('network', 'create', '--internal', '--label', LABEL + '=' + OWNER, NETWORK)
        network = inspect('network', NETWORK)
    require(network['Driver'] == 'bridge' and network['Internal'] is True and not network.get('Options'),
            'owned network must remain an isolated internal bridge')
    own = inspect('container', NAME)
    allowed = {current['Id']} | ({own['Id']} if own else set())
    require(set(network.get('Containers', {})) <= allowed, 'owned network contains an unrelated endpoint')
    receipt_path = STATE / 'network-receipt.json'
    if receipt_path.exists():
        receipt = private_read(receipt_path)
        require(receipt['network_id'] == network['Id'] and receipt['agenthouse_id'] == current['Id'],
                'network ownership receipt no longer matches retained identities')
    else:
        require(current['Id'] not in network.get('Containers', {}), 'cannot adopt an unjournaled AgentHouse attachment')
        receipt = {'network_id': network['Id'], 'agenthouse_id': current['Id'],
                   'prior_agenthouse_networks': sorted(current['NetworkSettings']['Networks']), 'attached_by_grafana': True}
    if current['Id'] not in network.get('Containers', {}):
        private_write(receipt_path, {**receipt, 'outcome': 'pending'})
        docker('network', 'connect', '--alias', AH.NAME, network['Id'], current['Id'])
    private_write(receipt_path, {**receipt, 'outcome': 'connected'})
    return network


def ensure_frontend(credential, own):
    """Only Grafana joins this bridge; Docker needs it for host publishing."""
    network = inspect('network', FRONTEND_NETWORK)
    if network is None:
        docker('network', 'create', '--label', LABEL + '=' + OWNER, FRONTEND_NETWORK)
        network = inspect('network', FRONTEND_NETWORK)
    require(network['Driver'] == 'bridge' and network['Internal'] is False and not network.get('Options'),
            'frontend must remain the owned ordinary bridge')
    allowed = {own['Id']} if own else set()
    require(set(network.get('Containers', {})) <= allowed, 'frontend contains an unrelated endpoint')
    receipt_path = STATE / 'frontend-network-receipt.json'
    if receipt_path.exists():
        receipt = private_read(receipt_path)
        require(receipt['network_id'] == network['Id'] and receipt['owner_token'] == credential['owner_token'],
                'frontend identity differs from its ownership receipt')
    else:
        require(not network.get('Containers'), 'cannot adopt an unjournaled frontend attachment')
        private_write(receipt_path, {'network_id': network['Id'], 'owner_token': credential['owner_token'],
                                    'grafana_id': None, 'attached_by_grafana': True, 'outcome': 'prepared'})
    return network


def attach_frontend(network, own, credential):
    receipt_path = STATE / 'frontend-network-receipt.json'
    receipt = private_read(receipt_path)
    require(receipt['network_id'] == network['Id'] and receipt['owner_token'] == credential['owner_token'],
            'frontend attachment requires its original network identity')
    receipt = {**receipt, 'grafana_id': own['Id'], 'outcome': 'pending'}
    if FRONTEND_NETWORK not in own['NetworkSettings']['Networks']:
        private_write(receipt_path, receipt)
        docker('network', 'connect', network['Id'], own['Id'])
    private_write(receipt_path, {**receipt, 'outcome': 'connected'})


def validate_container(value, credential):
    config, host = value['Config'], value['HostConfig']
    mounts = {(m['Type'], m.get('Name') if m['Type'] == 'volume' else m.get('Source'), m['Destination'], m['RW'])
              for m in value['Mounts']}
    expected = {('volume', VOLUME, '/var/lib/grafana', True),
                ('bind', str(STATE / 'plugins'), '/opt/grafana/plugins', False),
                ('bind', str(HERE / 'provisioning'), '/etc/grafana/provisioning', False),
                ('bind', str(HERE / 'dashboards'), '/etc/grafana/shirokuma-dashboards', False)}
    env = dict(item.split('=', 1) for item in config.get('Env', []) if '=' in item)
    require(config['Image'] == IMAGE and config.get('User') == '472'
            and config.get('Labels', {}).get(LABEL) == OWNER and len(value['Mounts']) == len(expected) and mounts == expected
            and host.get('PortBindings') == {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18030'}]}
            and host.get('NanoCpus') == 10**9 and host.get('Memory') == 1024**3 and host.get('PidsLimit') == 256
            and host.get('ReadonlyRootfs') is True and host.get('Privileged') is False
            and host.get('CapDrop') == ['ALL'] and not host.get('CapAdd') and not host.get('Devices')
            and host.get('SecurityOpt') in (['no-new-privileges'], ['no-new-privileges:true'])
            and set(value['NetworkSettings']['Networks']) <= {NETWORK, FRONTEND_NETWORK}
            and all(env.get(key) == expected for key, expected in environment(credential).items()),
            'existing Grafana scope differs; refusing mutation')
    require(not any(key in env for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')),
            'Grafana must not inherit an external proxy')
    return value


def up():
    prepare()
    credential = credentials()
    current = agenthouse()
    own = inspect('container', NAME)
    if own:
        validate_container(own, credential)
    require((HERE / 'dashboards/agenthouse.json').is_file(), 'owned AgentHouse dashboard is not prepared')
    network = ensure_network(current)
    frontend = ensure_frontend(credential, own)
    reader = ensure_reader(credential, current)
    if own is None:
        if docker('image', 'inspect', IMAGE, required=False) is None:
            docker('pull', '--platform', 'linux/arm64', IMAGE, timeout=600)
        identity = json.loads(docker('image', 'inspect', IMAGE))[0]
        require(identity['Architecture'] == 'arm64' and identity['Os'] == 'linux', 'Grafana image must be native ARM64')
        volume = inspect('volume', VOLUME)
        if volume is None:
            docker('volume', 'create', '--label', LABEL + '=' + OWNER, VOLUME)
        else:
            require(volume['Driver'] == 'local' and not volume.get('Options'), 'owned Grafana volume has unexpected storage options')
        docker('create', '--name', NAME, '--label', LABEL + '=' + OWNER, '--platform', 'linux/arm64', '--user', '472',
            '--cpus', '1', '--memory', '1g', '--pids-limit', '256', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
            '--network', FRONTEND_NETWORK, '--publish', '127.0.0.1:18030:3000', '--env-file', str(STATE / 'container.env'),
            '--mount', 'type=volume,source=' + VOLUME + ',target=/var/lib/grafana',
            '--mount', 'type=bind,source=' + str(STATE / 'plugins') + ',target=/opt/grafana/plugins,readonly',
            '--mount', 'type=bind,source=' + str(HERE / 'provisioning') + ',target=/etc/grafana/provisioning,readonly',
            '--mount', 'type=bind,source=' + str(HERE / 'dashboards') + ',target=/etc/grafana/shirokuma-dashboards,readonly', IMAGE)
        own = inspect('container', NAME)
    if NETWORK not in own['NetworkSettings']['Networks']:
        docker('network', 'connect', network['Id'], own['Id'])
    attach_frontend(frontend, own, credential)
    if not own['State']['Running']:
        docker('start', own['Id'])
    deadline = time.monotonic() + 90
    while True:
        try:
            result = status()
            if result['ready']:
                return {**result, 'reader_permissions': reader}
        except (GrafanaError, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise GrafanaError('Grafana did not become ready within the bounded startup window')
        time.sleep(1)


def api(path, credential=None):
    credential = credentials() if credential is None else credential
    return json.loads(request(URL + path, username='admin', password=credential['admin_password'], timeout=10))


def status():
    credential = credentials()
    own = inspect('container', NAME)
    if own is None:
        return {'ready': False, 'running': False, 'url': URL, 'image': IMAGE, 'volume_retained': True}
    validate_container(own, credential)
    running = own['State']['Running'] is True
    if not running:
        return {'ready': False, 'running': False, 'url': URL, 'image': IMAGE, 'volume_retained': True}
    require(set(own['NetworkSettings']['Networks']) == {NETWORK, FRONTEND_NETWORK}
            and own['NetworkSettings'].get('Ports') == {'3000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18030'}]},
            'Grafana needs both scoped networks and an effective loopback publish')
    health = api('/api/health', credential)
    require(health.get('database') == 'ok' and health.get('version') == PINS['grafana']['version'], 'Grafana health or version differs')
    plugin = api('/api/plugins/grafana-clickhouse-datasource/settings', credential)
    require(plugin.get('signature') == 'valid' and plugin.get('info', {}).get('version') == PINS['plugin']['version'],
            'Grafana did not validate the pinned plugin signature')
    datasource = api('/api/datasources/uid/' + DATASOURCE_UID, credential)
    require(datasource.get('type') == PINS['plugin']['id'] and datasource.get('readOnly') is True
            and datasource.get('jsonData', {}).get('username') == credential['reader_user']
            and datasource.get('jsonData', {}).get('host') == AH.NAME
            and datasource.get('jsonData', {}).get('protocol') == 'http'
            and datasource.get('jsonData', {}).get('port') == 8123
            and datasource.get('jsonData', {}).get('queryTimeout') == PLUGIN_QUERY_TIMEOUT
            and datasource.get('secureJsonFields', {}).get('password') is True, 'provisioned reader datasource differs')
    datasource_health = api('/api/datasources/uid/' + DATASOURCE_UID + '/health', credential)
    require(datasource_health.get('status') == 'OK', 'read-only datasource is not healthy')
    dashboard = json.loads(request(URL + '/api/dashboards/uid/' + DASHBOARD_UID, timeout=10))
    require(dashboard.get('dashboard', {}).get('uid') == DASHBOARD_UID and dashboard.get('meta', {}).get('provisioned') is True,
            'anonymous Viewer cannot read the provisioned AgentHouse dashboard')
    return {'ready': True, 'running': True, 'url': URL, 'dashboard_url': URL + '/d/' + DASHBOARD_UID,
            'image': IMAGE, 'version': health['version'], 'plugin_version': PINS['plugin']['version'],
            'plugin_signature': 'valid', 'datasource_uid': DATASOURCE_UID, 'anonymous_role': 'Viewer', 'volume_retained': True}


def disconnect_grafana(network_name, network_id, own, credential):
    """Docker network inspect omits stopped endpoints; container inspect does not."""
    if own is None:
        return None
    attached = own['NetworkSettings']['Networks'].get(network_name)
    if attached is not None:
        require(attached.get('NetworkID') == network_id, 'Grafana attachment network identity differs')
        docker('network', 'disconnect', '--force', network_id, own['Id'])
    refreshed = inspect('container', own['Id'])
    require(refreshed is not None and refreshed['Id'] == own['Id'], 'Grafana identity changed during disconnect')
    validate_container(refreshed, credential)
    require(network_name not in refreshed['NetworkSettings']['Networks'], 'Grafana network attachment remains after disconnect')
    return refreshed


def stop(*, disconnect=False):
    credential = credentials()
    own = inspect('container', NAME)
    if own:
        validate_container(own, credential)
        if own['State']['Running']:
            docker('stop', '--time', '30', own['Id'])
    if disconnect:
        network = inspect('network', NETWORK)
        if network is not None:
            receipt = private_read(STATE / 'network-receipt.json')
            require(receipt['network_id'] == network['Id'] and receipt['attached_by_grafana'] is True,
                    'network rollback requires the original identity receipt')
            current = agenthouse()
            require(current['Id'] == receipt['agenthouse_id'], 'retained AgentHouse identity changed; refusing disconnect')
            allowed = {current['Id']} | ({own['Id']} if own else set())
            require(set(network.get('Containers', {})) <= allowed, 'network has unrelated endpoints; refusing disconnect')
            private_write(STATE / 'network-receipt.json', {**receipt, 'outcome': 'disconnecting'})
            own = disconnect_grafana(NETWORK, network['Id'], own, credential)
            attachment = current['NetworkSettings']['Networks'].get(NETWORK)
            if attachment is not None:
                require(attachment.get('NetworkID') == network['Id'], 'AgentHouse attachment network identity differs')
                docker('network', 'disconnect', network['Id'], current['Id'])
            current_after = agenthouse()
            require(current_after['Id'] == current['Id'] and NETWORK not in current_after['NetworkSettings']['Networks'],
                    'AgentHouse network attachment remains after disconnect')
            private_write(STATE / 'network-receipt.json', {**receipt, 'outcome': 'disconnected'})
        else:
            require(own is None or NETWORK not in own['NetworkSettings']['Networks'],
                    'Grafana retains an attachment to an unverified backend network')
        frontend = inspect('network', FRONTEND_NETWORK)
        if frontend is not None:
            receipt_path = STATE / 'frontend-network-receipt.json'
            receipt = private_read(receipt_path)
            require(receipt['network_id'] == frontend['Id'] and receipt['owner_token'] == credential['owner_token']
                    and receipt['attached_by_grafana'] is True,
                    'frontend rollback requires the original identity receipt')
            allowed = {own['Id']} if own else set()
            require(set(frontend.get('Containers', {})) <= allowed, 'frontend has an unrelated endpoint')
            if own and FRONTEND_NETWORK in own['NetworkSettings']['Networks']:
                require(receipt['grafana_id'] == own['Id'], 'frontend Grafana identity changed; refusing disconnect')
            private_write(receipt_path, {**receipt, 'outcome': 'disconnecting'})
            own = disconnect_grafana(FRONTEND_NETWORK, frontend['Id'], own, credential)
            private_write(receipt_path, {**receipt, 'outcome': 'disconnected'})
        else:
            require(own is None or FRONTEND_NETWORK not in own['NetworkSettings']['Networks'],
                    'Grafana retains an attachment to an unverified frontend network')
    return {'result': 'stopped', 'network_disconnected': disconnect, 'volume_retained': True,
            'reader_config_retained': True, 'credentials_retained': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'up', 'status', 'smoke', 'stop', 'disconnect', 'history-enable', 'history-disable', 'history-publish'])
    args = parser.parse_args()
    try:
        check_context()
        require(not STATE.is_symlink(), 'private state must not be a symlink')
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(STATE.stat().st_uid == os.getuid() and stat.S_IMODE(STATE.stat().st_mode) == 0o700,
                'private state ownership or permissions differ')
        lock_path = STATE / 'runtime.lock'
        require(not lock_path.is_symlink(), 'runtime lock cannot be a symlink')
        with os.fdopen(os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == 'prepare':
                result = prepare()
            elif args.command == 'up':
                result = up()
            elif args.command in {'stop', 'disconnect'}:
                result = stop(disconnect=args.command == 'disconnect')
            elif args.command in {'history-enable', 'history-disable'}:
                from history_access import change_scope
                result = change_scope(sys.modules[__name__], args.command == 'history-enable')
            elif args.command == 'history-publish':
                from history_access import publish_dashboard
                result = publish_dashboard(sys.modules[__name__])
            else:
                result = status()
                if args.command == 'smoke':
                    require(result['ready'], 'Grafana must be ready for read-only smoke')
                    result['reader_permissions'] = verify_reader(credentials())
            private_write(STATE / 'runtime.json', result)
            print(json.dumps(result))
            return 0 if result.get('ready', True) else 1
    except Exception:
        # Never surface Docker env, database SQL/errors, passwords or plugin text.
        print(json.dumps({'result': 'failed', 'error_code': 'GRAFANA_LOCAL_SCOPE_OR_VERIFICATION_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
