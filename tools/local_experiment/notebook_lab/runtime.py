#!/usr/bin/env python3
"""Owned, single-user loopback JupyterLab. User code has host-user privileges.

Stop assumes the owner is not concurrently creating kernels. It uses the kernel
shutdown protocol, confirms captured process identities have exited, and only
then removes the server's kernel entries and shuts down the server. An unknown
process or timeout is retained for inspection, never killed or deleted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from jupyter_client.kernelspec import KernelSpecManager

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VENV = Path(__file__).resolve().parent / '.venv'
PYTHON = VENV / 'bin/python'
STATE = ROOT / 'tmp/local-experiment/notebook-lab'
TEMPLATE = ROOT / 'examples/local_training_workbench.ipynb'
QUERY_TEMPLATE = ROOT / 'examples/local_query_workbench.ipynb'
COUNT_TEMPLATE = ROOT / 'examples/local_count_workbench.ipynb'
TABLE_TEMPLATE = ROOT / 'examples/local_table_workbench.ipynb'
SNAPSHOT_TEMPLATE = ROOT / 'examples/local_snapshot_workbench.ipynb'
DIAGNOSE_TEMPLATE = ROOT / 'examples/local_query_diagnose_workbench.ipynb'
CATALOG_TEMPLATE = ROOT / 'examples/local_catalog_workbench.ipynb'
COMPARISON_TEMPLATE = ROOT / 'examples/local_ridge_comparison.ipynb'
COMPARISON_PREDICTION_TEMPLATE = ROOT / 'examples/local_comparison_prediction.ipynb'
REGISTRY_PREDICTION_TEMPLATE = ROOT / 'examples/local_registry_prediction.ipynb'
LAKE_TRAINING_TEMPLATE = ROOT / 'examples/local_lake_training_workbench.ipynb'
COMPARISON_RECORDING_TEMPLATE = ROOT / 'examples/local_comparison_recording.ipynb'
PORT = 18560
URL = 'http://127.0.0.1:' + str(PORT)
VERSION = '4.6.3'
KERNEL = 'shirokuma-local'
OWNER = 'shirokuma-local-notebook-lab'
MAX_KERNELS = 16
START_SECONDS = 45
STOP_SECONDS = 20


class PrivateKernelSpecManager(KernelSpecManager):
    """Search only this instance's kernels, without reading global specs."""

    def _kernel_dirs_default(self):
        return [str(STATE / 'data/kernels')]

# Reuse only private-file and process-inspection primitives; no MLflow import,
# initialization, store environment, HTTP, or lifecycle operation is invoked.
_spec = importlib.util.spec_from_file_location('_lab_private', ROOT / 'tools/local_experiment/mlflow/runtime.py')
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


class LabError(Exception):
    """Fixed code; never include server output, paths from input, or a token."""


def require(ok, code):
    if not ok:
        raise LabError(code)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'LAB_RECORD_INVALID')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(LabError('LAB_RECORD_INVALID')))


def _read(path):
    return M.read_private(path)


def _private_dir(path, *, create=False):
    if path != STATE / 'runtime':
        return M.private_dir(path, create=create)
    # jupyter_client.write_connection_file sets sticky on this private parent
    # so connection files remain removable only by their owner (connect.py).
    M.no_symlinks(path)
    if create and not path.exists():
        path.mkdir(mode=0o700)
    value = path.stat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.getuid()
            and stat.S_IMODE(value.st_mode) in (0o700, 0o1700), 'LAB_RUNTIME_DIRECTORY_INVALID')


def _save(name, value):
    M.write_private(STATE / name, value)


def _token():
    raw = _read(STATE / 'token')
    require(re.fullmatch(rb'[0-9a-f]{64}', raw) is not None, 'LAB_TOKEN_INVALID')
    return raw.decode('ascii')


def _exclusive(path, raw):
    _private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    M.sync_dir(path.parent)


def environment():
    """No inherited credentials, proxies, request variables or global config."""
    return {
        'PATH': str(VENV / 'bin') + ':/usr/bin:/bin', 'LC_ALL': 'C',
        'TMPDIR': str(STATE / 'tmp'), 'TMP': str(STATE / 'tmp'), 'TEMP': str(STATE / 'tmp'),
        'XDG_CONFIG_HOME': str(STATE / 'config'), 'XDG_DATA_HOME': str(STATE / 'data'),
        'XDG_CACHE_HOME': str(STATE / 'cache'), 'XDG_RUNTIME_DIR': str(STATE / 'runtime'),
        'JUPYTER_NO_CONFIG': '1', 'JUPYTER_CONFIG_DIR': str(STATE / 'config'),
        'JUPYTER_DATA_DIR': str(STATE / 'data'), 'JUPYTER_RUNTIME_DIR': str(STATE / 'runtime'),
        'IPYTHONDIR': str(STATE / 'ipython'), 'JUPYTERLAB_SETTINGS_DIR': str(STATE / 'settings'),
        'JUPYTERLAB_WORKSPACES_DIR': str(STATE / 'workspaces'),
        'PYTHONPATH': str(ROOT), 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1', 'JUPYTER_SERVER_ALLOW_UNAUTHENTICATED_ACCESS': 'false',
        'DO_NOT_TRACK': 'true', 'MLFLOW_DISABLE_TELEMETRY': 'true',
    }


def server_argv():
    return [str(PYTHON), '-B', '-m', 'jupyterlab',
            '--config=' + str(STATE / 'config/jupyter_server_config.json')]


def kernel_argv(kernel_id):
    return [str(PYTHON), '-B', '-m', 'ipykernel_launcher', '-f',
            str(STATE / ('runtime/kernel-' + kernel_id + '.json'))]


def _config(token):
    return {
        'ServerApp': {
            'ip': '127.0.0.1', 'port': PORT, 'port_retries': 0, 'open_browser': False,
            'root_dir': str(STATE / 'workspace'), 'default_url': '/lab',
            'allow_remote_access': False, 'allow_origin': '', 'allow_origin_pat': '',
            'allow_credentials': False, 'trust_xheaders': False, 'disable_check_xsrf': False,
            'allow_unauthenticated_access': False, 'allow_external_kernels': False,
            'kernel_spec_manager_class':
                'tools.local_experiment.notebook_lab.runtime.PrivateKernelSpecManager',
            'terminals_enabled': False, 'use_redirect_file': False,
            'cookie_secret_file': str(STATE / 'runtime/cookie_secret'),
            'jpserver_extensions': {'jupyterlab': True, 'jupyter_lsp': False,
                                    'jupyter_server_terminals': False, 'notebook_shim': False},
            'reraise_server_extension_failures': True, 'log_level': 'WARN',
        },
        'IdentityProvider': {'token': token},
        'KernelSpecManager': {'allowed_kernelspecs': [KERNEL], 'ensure_native_kernel': False},
        'MappingKernelManager': {'default_kernel_name': KERNEL},
        'KernelManager': {'autorestart': False},
        'LabApp': {'extension_manager': 'readonly', 'news_url': None,
                   'check_for_updates_class': 'jupyterlab.handlers.announcements.NeverCheckForUpdate'},
        'LabServerApp': {'extra_labextensions_path': [], 'allowed_extensions_uris': '',
                         'blocked_extensions_uris': ''},
    }


def _kernelspec():
    return {'argv': [str(PYTHON), '-B', '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
            'display_name': 'Shirokuma local', 'language': 'python',
            'env': {'PYTHONPATH': str(ROOT), 'PYTHONNOUSERSITE': '1',
                    'PYTHONDONTWRITEBYTECODE': '1'}}


DIRS = ('config', 'data', 'data/kernels', 'data/kernels/' + KERNEL, 'runtime',
        'workspace', 'tmp', 'cache', 'ipython', 'settings', 'workspaces')


def _prepare():
    for relative in DIRS:
        _private_dir(STATE / relative, create=True)
    owner_path = STATE / 'owner.json'
    if not owner_path.exists():
        require(not any((STATE / p).exists() for p in ('token', 'runtime.json', 'start-intent.json')),
                'LAB_OWNER_INVALID')
        _exclusive(STATE / 'token', secrets.token_hex(32).encode('ascii'))
        _save('owner.json', {'schema_version': 1, 'owner': OWNER, 'instance_id': uuid.uuid4().hex,
                             'jupyterlab_version': VERSION, 'kernel_name': KERNEL})
        _save('config/jupyter_server_config.json', _config(_token()))
        _save('data/kernels/' + KERNEL + '/kernel.json', _kernelspec())
    # A user-edited notebook is retained byte-for-byte, including saved outputs.
    for name, template in (('training_workbench.ipynb', TEMPLATE),
                           ('query_workbench.ipynb', QUERY_TEMPLATE),
                           ('ridge_comparison.ipynb', COMPARISON_TEMPLATE),
                           ('comparison_prediction.ipynb', COMPARISON_PREDICTION_TEMPLATE),
                           ('lake_training_workbench.ipynb', LAKE_TRAINING_TEMPLATE),
                           ('comparison_recording.ipynb', COMPARISON_RECORDING_TEMPLATE),
                           ('count_workbench.ipynb', COUNT_TEMPLATE),
                           ('table_workbench.ipynb', TABLE_TEMPLATE),
                           ('snapshot_workbench.ipynb', SNAPSHOT_TEMPLATE),
                           ('registry_prediction.ipynb', REGISTRY_PREDICTION_TEMPLATE),
                           ('query_diagnose_workbench.ipynb', DIAGNOSE_TEMPLATE),
                           ('catalog_workbench.ipynb', CATALOG_TEMPLATE)):
        destination = STATE / 'workspace' / name
        if not destination.exists() and not destination.is_symlink():
            _exclusive(destination, template.read_bytes())
    return _owner()


def _queries_idle():
    path = STATE / 'queries'
    M.no_symlinks(path)
    if not path.exists():
        return True
    from tools.local_experiment.notebook_lab import query
    require(query.LAB_STATE == STATE, 'LAB_QUERY_STATE_CHANGED')
    return query.check_idle()


def _comparisons_idle():
    path = STATE / 'comparisons'
    M.no_symlinks(path)
    if not path.exists():
        return True
    from tools.local_experiment.notebook_lab import compare
    require(compare.LAB_STATE == STATE, 'LAB_COMPARISON_STATE_CHANGED')
    return compare.check_idle()


def _trainings_idle():
    path = STATE / 'trainings'
    M.no_symlinks(path)
    if not path.exists():
        return True
    from tools.local_experiment.notebook_lab import train
    require(train.LAB_STATE == STATE, 'LAB_TRAINING_STATE_CHANGED')
    return train.check_idle()


def _recordings_idle():
    path = STATE / 'comparison-recordings'
    M.no_symlinks(path)
    if not path.exists():
        return True
    from tools.local_experiment.notebook_lab import record_comparison
    require(record_comparison.LAB_STATE == STATE, 'LAB_RECORD_STATE_CHANGED')
    return record_comparison.check_idle()


def _owner():
    M.private_dir(STATE)
    for relative in DIRS:
        _private_dir(STATE / relative)
    value = _json(_read(STATE / 'owner.json'))
    require(type(value) is dict and set(value) == {'schema_version', 'owner', 'instance_id',
            'jupyterlab_version', 'kernel_name'} and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['owner'] == OWNER
            and value['jupyterlab_version'] == VERSION and value['kernel_name'] == KERNEL
            and re.fullmatch('[0-9a-f]{32}', value['instance_id'] or '') is not None, 'LAB_OWNER_INVALID')
    require(_json(_read(STATE / 'config/jupyter_server_config.json')) == _config(_token())
            and _json(_read(STATE / ('data/kernels/' + KERNEL + '/kernel.json'))) == _kernelspec(),
            'LAB_CONFIG_CHANGED')
    return value


@contextmanager
def _lock():
    M.no_symlinks(STATE)
    require(STATE.parent.is_dir(), 'LAB_PARENT_MISSING')
    M.private_dir(STATE, create=True)
    fd = os.open(STATE / 'owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
                and stat.S_IMODE(s.st_mode) == 0o600, 'LAB_LOCK_INVALID')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LabError('LAB_BUSY') from None
        yield
    finally:
        os.close(fd)


def listeners():
    result = M._command(['/usr/sbin/lsof', '-nP', '-iTCP:' + str(PORT), '-sTCP:LISTEN', '-Fpn'])
    require(result.returncode in (0, 1), 'LAB_PROCESS_INSPECTION_FAILED')
    rows, pid = [], None
    for line in result.stdout.decode().splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('n'):
            require(pid is not None, 'LAB_PROCESS_INSPECTION_FAILED')
            rows.append({'pid': pid, 'address': line[1:]})
    return rows


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise LabError('LAB_HTTP_REDIRECT_REFUSED')


def _api(method, path):
    require((method, path) in (('GET', '/api/kernels'), ('POST', '/api/shutdown'))
            or (method == 'DELETE' and re.fullmatch('/api/kernels/[0-9a-f-]{36}', path)),
            'LAB_HTTP_SCOPE_INVALID')
    request = urllib.request.Request(URL + path, method=method,
                                     data=b'' if method == 'POST' else None,
                                     headers={'Authorization': 'token ' + _token()})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            require(response.status in (200, 204), 'LAB_HTTP_FAILED')
            raw = response.read(65537)
            require(len(raw) <= 65536, 'LAB_HTTP_TOO_LARGE')
            return _json(raw) if raw else None
    except urllib.error.HTTPError as error:
        error.close()
        raise LabError('LAB_HTTP_FAILED') from None


def _kernels():
    models = _api('GET', '/api/kernels')
    require(type(models) is list and len(models) <= MAX_KERNELS, 'LAB_KERNEL_INVENTORY_INVALID')
    ids = []
    for model in models:
        require(type(model) is dict and model.get('name') == KERNEL and type(model.get('id')) is str,
                'LAB_KERNEL_INVENTORY_INVALID')
        value = model['id']
        require(str(uuid.UUID(value)) == value and value not in ids, 'LAB_KERNEL_INVENTORY_INVALID')
        ids.append(value)
    return sorted(ids)


def _record(owner):
    path = STATE / 'runtime.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _json(_read(path))
    require(type(value) is dict and set(value) == {'schema_version', 'instance_id', 'argv', 'leader'}
            and value['schema_version'] == 1 and type(value['schema_version']) is int
            and value['instance_id'] == owner['instance_id'] and value['argv'] == server_argv(),
            'LAB_RUNTIME_INVALID')
    p = value['leader']
    require(type(p) is dict and type(p.get('pid')) is int and p['pid'] > 1
            and p.get('pgid') == p['pid'] and p.get('uid') == os.getuid()
            and p.get('command') == ' '.join(server_argv()) and type(p.get('start_time')) is str,
            'LAB_RUNTIME_INVALID')
    return value


def _live(row):
    return row is not None and not row.get('state', '').startswith('Z')


def _leader(record, rows):
    saved = record['leader']
    current = rows.get(saved['pid'])
    require(current is None or M._same(current, saved) or M._known_zombie(current, saved),
            'LAB_PROCESS_IDENTITY_CHANGED')
    return current if _live(current) else None


def _kernel_processes(record, ids, rows):
    leader = record['leader']
    expected = {kernel_id: ' '.join(kernel_argv(kernel_id)) for kernel_id in ids}
    bound = {}
    for kernel_id, command in expected.items():
        matches = [p for p in rows.values() if _live(p) and p['command'] == command]
        require(len(matches) == 1, 'LAB_KERNEL_IDENTITY_UNKNOWN')
        p = matches[0]
        require(p['ppid'] == leader['pid'] and p['pgid'] == p['pid'] and p['uid'] == os.getuid(),
                'LAB_KERNEL_IDENTITY_UNKNOWN')
        # User-created child processes are outside this bounded stop profile.
        require(not any(_live(q) and q['pid'] != p['pid'] and q['pgid'] == p['pgid']
                        for q in rows.values()), 'LAB_KERNEL_CHILDREN_UNKNOWN')
        bound[kernel_id] = p
    candidates = [p for p in rows.values() if _live(p) and
                  (p['ppid'] == leader['pid'] or str(STATE / 'runtime/kernel-') in p['command'])]
    require({p['pid'] for p in candidates} == {p['pid'] for p in bound.values()},
            'LAB_KERNEL_IDENTITY_UNKNOWN')
    return bound


def _public(state, *, healthy=False, pid=None, kernels=None, error=None):
    return {'schema_version': 1, 'state': state, 'url': URL + '/lab',
            'workspace': str(STATE.relative_to(ROOT) / 'workspace'), 'kernel_name': KERNEL,
            'healthy': healthy, 'server_pid': pid, 'kernel_count': kernels,
            'cleanup_verified': state in ('stopped', 'uninitialized'), 'error_code': error}


def status():
    """Read-only inspection. No private directory, token, or runtime repair."""
    M.no_symlinks(STATE)
    port = listeners()
    if not STATE.exists():
        return _public('unknown', error='LAB_PORT_CONFLICT') if port else _public('uninitialized', kernels=0)
    owner = _owner()
    launch_path = STATE / 'start-intent.json'
    if launch_path.exists():
        launch = _json(_read(launch_path))
        require(type(launch) is dict and set(launch) == {'schema_version', 'instance_id', 'state'}
                and type(launch['schema_version']) is int and launch['schema_version'] == 1
                and launch['instance_id'] == owner['instance_id']
                and launch['state'] in ('pending', 'identified', 'not-started'), 'LAB_INTENT_INVALID')
        if launch['state'] == 'pending':
            return _public('unknown', error='LAB_START_UNCONFIRMED')
    record = _record(owner)
    rows = M.processes()
    if record is None or _leader(record, rows) is None:
        orphan = any(_live(p) and str(STATE / 'runtime/kernel-') in p['command'] for p in rows.values())
        return _public('unknown', error='LAB_PROCESS_IDENTITY_CHANGED') if port or orphan else _public('stopped', kernels=0)
    leader = record['leader']
    require(not any(p['address'] != '127.0.0.1:' + str(PORT) or p['pid'] != leader['pid'] for p in port),
            'LAB_PORT_CONFLICT')
    if not port:
        return _public('running', pid=leader['pid'])
    ids = _kernels()
    _kernel_processes(record, ids, rows)
    require(_leader(record, M.processes()) is not None, 'LAB_PROCESS_IDENTITY_CHANGED')
    return _public('running', healthy=True, pid=leader['pid'], kernels=len(ids))


def _port_free():
    require(not listeners(), 'LAB_PORT_CONFLICT')
    try:
        with socket.socket() as sock:
            # Match the server bind: a stopped server's TIME_WAIT connections
            # do not occupy a listener and should not block immediate restart.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', PORT))
    except OSError:
        raise LabError('LAB_PORT_CONFLICT') from None


def start():
    with _lock():
        require(_recordings_idle(), 'LAB_RECORD_ACTIVE_OR_UNKNOWN')
        require(_trainings_idle(), 'LAB_TRAINING_ACTIVE_OR_UNKNOWN')
        owner = _prepare()
        current = status()
        if current['state'] == 'running':
            require(current['healthy'], 'LAB_SERVER_UNHEALTHY')
            return current
        require(current['state'] == 'stopped', 'LAB_START_UNCONFIRMED')
        require(_queries_idle(), 'LAB_QUERY_ACTIVE_OR_UNKNOWN')
        require(_comparisons_idle(), 'LAB_COMPARISON_ACTIVE_OR_UNKNOWN')
        _port_free()
        require(PYTHON.is_file(), 'LAB_ENVIRONMENT_MISSING')
        log = STATE / 'server.log'
        if log.exists() or log.is_symlink():
            # Logs can grow beyond the bounded JSON-reader limit.
            M.no_symlinks(log)
            s = log.stat()
            require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
                    and stat.S_IMODE(s.st_mode) == 0o600, 'LAB_LOG_INVALID')
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        launch = {'schema_version': 1, 'instance_id': owner['instance_id'], 'state': 'pending'}
        try:
            _save('start-intent.json', launch)
            try:
                child = subprocess.Popen(server_argv(), cwd=STATE / 'workspace', env=environment(),
                                         stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                         start_new_session=True, umask=0o077)
            except OSError:
                launch['state'] = 'not-started'
                _save('start-intent.json', launch)
                raise
        finally:
            os.close(fd)
        deadline = time.monotonic() + START_SECONDS
        record = None
        while time.monotonic() < deadline:
            require(child.poll() is None, 'LAB_SERVER_EXITED')
            p = M.processes().get(child.pid)
            if p is not None and p['command'] == ' '.join(server_argv()):
                require(p['pgid'] == child.pid and p['uid'] == os.getuid(), 'LAB_START_UNCONFIRMED')
                record = {'schema_version': 1, 'instance_id': owner['instance_id'],
                          'argv': server_argv(), 'leader': p}
                _save('runtime.json', record)
                launch['state'] = 'identified'
                _save('start-intent.json', launch)
                break
            time.sleep(0.05)
        require(record is not None, 'LAB_START_UNCONFIRMED')
        while time.monotonic() < deadline:
            require(child.poll() is None, 'LAB_SERVER_EXITED')
            current = status()
            require(current['state'] == 'running', 'LAB_START_UNCONFIRMED')
            if current['healthy']:
                return current
            time.sleep(0.2)
        raise LabError('LAB_START_TIMEOUT')


def _connection(kernel_id):
    value = _json(_read(STATE / ('runtime/kernel-' + kernel_id + '.json')))
    ports = ('shell_port', 'iopub_port', 'stdin_port', 'control_port', 'hb_port')
    require(type(value) is dict and value.get('transport') == 'tcp'
            and value.get('ip') == '127.0.0.1' and value.get('signature_scheme') == 'hmac-sha256'
            and type(value.get('key')) is str and 1 <= len(value['key']) <= 256
            and all(type(value.get(k)) is int and 1 <= value[k] <= 65535 for k in ports),
            'LAB_CONNECTION_INVALID')
    return value


def _request_shutdown(kernel_id):
    from jupyter_client import BlockingKernelClient
    client = BlockingKernelClient()
    client.load_connection_info(_connection(kernel_id))
    error = None
    try:
        client.start_channels(shell=False, iopub=False, stdin=False, hb=False, control=True)
        message_id = client.shutdown(restart=False)
        deadline = time.monotonic() + STOP_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'LAB_STOP_TIMEOUT')
            reply = client.get_control_msg(timeout=remaining)
            if reply.get('parent_header', {}).get('msg_id') == message_id:
                require(reply.get('header', {}).get('msg_type') == 'shutdown_reply',
                        'LAB_SHUTDOWN_REPLY_INVALID')
                break
    except BaseException as caught:
        error = caught
    finally:
        try:
            client.stop_channels()
        except BaseException as caught:
            if error is None or (isinstance(error, Exception) and not isinstance(caught, Exception)):
                error = caught
    if error is not None:
        raise error


def _gone(saved, rows):
    current = rows.get(saved['pid'])
    require(current is None or M._same(current, saved) or M._known_zombie(current, saved),
            'LAB_PROCESS_IDENTITY_CHANGED')
    return not _live(current)


def _wait_gone(saved):
    deadline = time.monotonic() + STOP_SECONDS
    last_error = None
    while time.monotonic() < deadline:
        try:
            rows = M.processes()
            gone = _gone(saved, rows)
        except (LabError, M.RuntimeError) as error:
            # ps may briefly expose a teardown argv before the zombie state.
            # An untrusted snapshot authorizes nothing; read again within the
            # same deadline and retain an eventual mismatch as a failure.
            if str(error) not in ('LAB_PROCESS_IDENTITY_CHANGED', 'PROCESS_INSPECTION_INVALID'):
                raise
            last_error = error
        else:
            last_error = None
            if gone:
                require(not any(_live(p) and p['pgid'] == saved['pgid'] for p in rows.values()),
                        'LAB_PROCESS_GROUP_REMAINS')
                return
        time.sleep(0.1)
    if last_error is not None:
        raise last_error
    raise LabError('LAB_STOP_TIMEOUT')


def _stop_intent(record):
    """Use a prior stop only for this instance and this exact server process."""
    path = STATE / 'stop-intent.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = _json(_read(path))
    require(type(value) is dict and set(value) == {'schema_version','instance_id','leader','kernels'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['instance_id'] == record['instance_id'] and type(value['leader']) is dict
            and type(value['kernels']) is dict and len(value['kernels']) <= MAX_KERNELS,
            'LAB_STOP_INTENT_INVALID')
    if value['leader'] != record['leader']:
        # A completed previous server's journal is not authority over a new one.
        return None
    seen = set()
    for kernel_id, saved in value['kernels'].items():
        require(type(kernel_id) is str and re.fullmatch('[0-9a-f-]{36}', kernel_id) is not None,
                'LAB_STOP_INTENT_INVALID')
        try:
            valid_id = str(uuid.UUID(kernel_id)) == kernel_id
        except ValueError:
            valid_id = False
        require(valid_id and type(saved) is dict and set(saved) == {
                    'pid','ppid','pgid','uid','state','start_time','command'}
                and all(type(saved[key]) is int for key in ('pid','ppid','pgid','uid'))
                and saved['pid'] > 1 and saved['pid'] == saved['pgid']
                and saved['ppid'] == record['leader']['pid'] and saved['uid'] == os.getuid()
                and type(saved['state']) is str and bool(saved['state']) and _live(saved)
                and type(saved['start_time']) is str and 0 < len(saved['start_time']) <= 64
                and '\n' not in saved['start_time'] and saved['command'] == ' '.join(kernel_argv(kernel_id))
                and saved['pid'] not in seen, 'LAB_STOP_INTENT_INVALID')
        seen.add(saved['pid'])
    return value['kernels']


def _resume_kernels(record, ids, rows, saved):
    """Gone kernels need prior ownership; never adopt a new ID or reused PID."""
    require(set(ids) <= set(saved), 'LAB_KERNEL_INVENTORY_CHANGED')
    live_ids = []
    for kernel_id, identity in saved.items():
        if _gone(identity, rows):
            require(not any(_live(p) and p['pgid'] == identity['pgid'] for p in rows.values()),
                    'LAB_PROCESS_GROUP_REMAINS')
        else:
            require(kernel_id in ids, 'LAB_KERNEL_IDENTITY_UNKNOWN')
            live_ids.append(kernel_id)
    live = _kernel_processes(record, live_ids, rows)
    require(all(M._same(live[key], saved[key]) for key in live_ids), 'LAB_KERNEL_IDENTITY_UNKNOWN')
    return saved


def stop():
    if not STATE.exists() and not STATE.is_symlink():
        current = status()
        require(current['state'] == 'uninitialized', 'LAB_STOP_UNCONFIRMED')
        return current
    with _lock():
        require(_recordings_idle(), 'LAB_RECORD_ACTIVE_OR_UNKNOWN')
        require(_trainings_idle(), 'LAB_TRAINING_ACTIVE_OR_UNKNOWN')
        require(_queries_idle(), 'LAB_QUERY_ACTIVE_OR_UNKNOWN')
        require(_comparisons_idle(), 'LAB_COMPARISON_ACTIVE_OR_UNKNOWN')
        owner_path = STATE / 'owner.json'
        if not owner_path.exists() and not owner_path.is_symlink():
            require({path.name for path in STATE.iterdir()} == {'owner.lock'}, 'LAB_STOP_UNCONFIRMED')
            require(not listeners(), 'LAB_PORT_CONFLICT')
            require(not any(_live(p) and (p['command'] == ' '.join(server_argv())
                            or str(STATE / 'runtime/kernel-') in p['command']) for p in M.processes().values()),
                    'LAB_PROCESS_IDENTITY_CHANGED')
            return _public('uninitialized', kernels=0)
        record = _record(_owner())
        previous = _stop_intent(record) if record is not None else None
        if previous is None:
            current = status()
            require(current['state'] in ('running', 'stopped'), 'LAB_STOP_UNCONFIRMED')
            if current['state'] == 'stopped':
                return current
            require(current['healthy'], 'LAB_SERVER_UNHEALTHY')
            record = _record(_owner())
            ids = _kernels()
            rows = M.processes()
            require(_leader(record, rows) is not None, 'LAB_PROCESS_IDENTITY_CHANGED')
            kernels = _kernel_processes(record, ids, rows)
            _save('stop-intent.json', {'schema_version': 1, 'instance_id': record['instance_id'],
                                       'leader': record['leader'], 'kernels': kernels})
        else:
            rows = M.processes()
            leader = _leader(record, rows)
            port = listeners()
            if leader is None:
                require(not port, 'LAB_PORT_CONFLICT')
                _resume_kernels(record, [], rows, previous)
                require(not any(_live(p) and p['pgid'] == record['leader']['pgid'] for p in rows.values()),
                        'LAB_PROCESS_GROUP_REMAINS')
                result = status()
                require(result['state'] == 'stopped', 'LAB_STOP_UNCONFIRMED')
                _save('stop-result.json', result)
                return result
            require(bool(port) and all(p['pid'] == leader['pid'] and p['address'] == '127.0.0.1:' + str(PORT)
                                       for p in port), 'LAB_PORT_CONFLICT')
            ids = _kernels()
            rows = M.processes()
            require(_leader(record, rows) is not None, 'LAB_PROCESS_IDENTITY_CHANGED')
            kernels = _resume_kernels(record, ids, rows, previous)
        for kernel_id, saved in kernels.items():
            rows = M.processes()
            require(_leader(record, rows) is not None, 'LAB_PROCESS_IDENTITY_CHANGED')
            if not _gone(saved, rows):
                require(M._same(rows.get(saved['pid']), saved), 'LAB_KERNEL_IDENTITY_UNKNOWN')
                _request_shutdown(kernel_id)
            _wait_gone(saved)
            if kernel_id in ids:
                require(_leader(record, M.processes()) is not None, 'LAB_PROCESS_IDENTITY_CHANGED')
                _api('DELETE', '/api/kernels/' + kernel_id)
        require(not _kernels(), 'LAB_KERNEL_INVENTORY_CHANGED')
        rows = M.processes()
        require(_leader(record, rows) is not None, 'LAB_PROCESS_IDENTITY_CHANGED')
        _kernel_processes(record, [], rows)
        _api('POST', '/api/shutdown')
        _wait_gone(record['leader'])
        require(not listeners(), 'LAB_PORT_CONFLICT')
        result = status()
        require(result['state'] == 'stopped', 'LAB_STOP_UNCONFIRMED')
        _save('stop-result.json', result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('command', choices=('start', 'status', 'stop'))
    args = parser.parse_args(argv)
    try:
        result = globals()[args.command]()
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 1 if result['state'] == 'unknown' else 0
    except Exception as error:
        code = str(error) if isinstance(error, LabError) else 'LAB_OPERATION_FAILED'
        print(json.dumps(_public('unknown', error=code), sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
