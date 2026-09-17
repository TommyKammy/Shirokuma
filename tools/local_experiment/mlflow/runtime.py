#!/usr/bin/env python3
"""One private MLflow 3.16.1 store and loopback server; never deletes retained data.

SDK writers must hold store_lock() and require_stopped() throughout their work.
This module imports no MLflow code. Only isolated child processes may do so.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[3]
VENV = Path(__file__).resolve().parent / '.venv'
VENV_PYTHON = VENV / 'bin/python'
STATE = ROOT / 'tmp/local-experiment/mlflow-tracking'
STORE = STATE / 'store'
ARTIFACT_ROOT = STORE / 'artifacts'
BACKEND_URI = 'sqlite:///' + str(STORE / 'tracking.sqlite')
PORT = 18550
URL = 'http://127.0.0.1:' + str(PORT)
VERSION = '3.16.1'
OWNER = 'shirokuma-local-mlflow'


class RuntimeError(Exception):
    """Fixed safe code only; never report subprocess output or host environment."""


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def no_symlinks(path):
    for item in (path, *path.parents):
        require(not item.is_symlink(), 'SYMLINK_REFUSED')


def private_dir(path, *, create=False):
    no_symlinks(path)
    if create and not path.exists():
        path.mkdir(mode=0o700)
    value = path.stat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.getuid()
            and stat.S_IMODE(value.st_mode) == 0o700, 'UNSAFE_DIRECTORY')


def read_private(path):
    no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600,
                'UNSAFE_FILE')
        require(before.st_size <= 65536, 'FILE_TOO_LARGE')
        data = os.read(fd, 65537)
        after = os.fstat(fd)
        require(len(data) <= 65536 and (before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns), 'FILE_CHANGED')
        return data
    finally:
        os.close(fd)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private(path, value):
    private_dir(path.parent)
    if path.exists() or path.is_symlink():
        read_private(path)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(value, sort_keys=True, allow_nan=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


def _state():
    no_symlinks(STATE)
    require(STATE.parent.is_dir(), 'PARENT_DIRECTORY_MISSING')
    existed = STATE.exists()
    private_dir(STATE, create=True)
    if not existed:
        sync_dir(STATE.parent)


@contextmanager
def store_lock():
    """Exclusive, nonblocking lock shared by start/stop/prepare and SDK importer."""
    _state()
    path = STATE / 'store.lock'
    no_symlinks(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        value = os.fstat(fd)
        require(value.st_uid == os.getuid() and value.st_nlink == 1
                and stat.S_ISREG(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o600,
                'UNSAFE_LOCK')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('STORE_BUSY') from None
        yield
    finally:
        os.close(fd)


def environment():
    """Fresh environment, independent of parent cloud credentials/proxies/HOME."""
    return {
        'HOME': str(STATE / 'home'), 'XDG_CONFIG_HOME': str(STATE / 'home/config'),
        'XDG_CACHE_HOME': str(STATE / 'home/cache'), 'XDG_DATA_HOME': str(STATE / 'home/data'),
        'TMPDIR': str(STATE / 'tmp'), 'TMP': str(STATE / 'tmp'), 'TEMP': str(STATE / 'tmp'),
        'PATH': str(VENV / 'bin') + ':/usr/bin:/bin', 'LC_ALL': 'C',
        '__CF_USER_TEXT_ENCODING': f'0x{os.getuid():X}:0x0:0x0',
        'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
        'MLFLOW_DISABLE_TELEMETRY': 'true', 'DO_NOT_TRACK': 'true',
        'MLFLOW_SERVER_ENABLE_JOB_EXECUTION': 'false', 'MLFLOW_ENABLE_REMOTE_ASSISTANT': 'false',
        'MLFLOW_TRACKING_URI': BACKEND_URI, 'MLFLOW_REGISTRY_URI': BACKEND_URI,
        '_MLFLOW_SERVER_ARTIFACT_ROOT': ARTIFACT_ROOT.as_uri(),
        'MLFLOW_SERVER_ALLOWED_HOSTS': '127.0.0.1:' + str(PORT),
        'MLFLOW_SERVER_CORS_ALLOWED_ORIGINS': URL,
    }


def _marker(store_id):
    return {'schema_version': 1, 'owner': OWNER, 'store_id': store_id,
            'mlflow_version': VERSION, 'backend_uri': BACKEND_URI,
            'artifact_root': str(ARTIFACT_ROOT), 'url': URL}


def require_store():
    private_dir(STATE)
    value = json.loads(read_private(STATE / 'store.json'))
    try:
        store_id = str(uuid.UUID(value['store_id']))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError('STORE_MARKER_CONFLICT') from None
    require(value == _marker(store_id), 'STORE_MARKER_CONFLICT')
    for path in (STORE, ARTIFACT_ROOT, STATE/'home', STATE/'home/config',
                 STATE/'home/cache', STATE/'home/data', STATE/'tmp'):
        private_dir(path)
    for path in STORE.iterdir():
        require(path.name in ('artifacts', 'tracking.sqlite', 'tracking.sqlite-wal',
                             'tracking.sqlite-shm', 'tracking.sqlite-journal'), 'STORE_CONTENT_CONFLICT')
        if path != ARTIFACT_ROOT:
            no_symlinks(path)
            s = path.stat()
            require(stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
                    and stat.S_IMODE(s.st_mode) == 0o600, 'UNSAFE_DATABASE')
    return value


def _isolated_runtime():
    require(VENV_PYTHON.is_file(), 'VENV_MISSING')
    packages = list((VENV / 'lib').glob('python*/site-packages/mlflow-' + VERSION + '.dist-info'))
    require(len(packages) == 1, 'MLFLOW_VERSION_MISMATCH')
    env = environment()
    for executable in ('docker', 'codex', 'claude', 'gemini'):
        require(shutil.which(executable, path=env['PATH']) is None, 'UNSAFE_EXECUTABLE_VISIBLE')
    return env


def server_argv():
    return [str(VENV_PYTHON), '-m', 'mlflow', 'server', '--host', '127.0.0.1',
            '--port', str(PORT), '--workers', '1', '--backend-store-uri', BACKEND_URI,
            '--registry-store-uri', BACKEND_URI, '--default-artifact-root', ARTIFACT_ROOT.as_uri(),
            '--artifacts-destination', str(ARTIFACT_ROOT), '--no-serve-artifacts',
            '--allowed-hosts', '127.0.0.1:' + str(PORT), '--cors-allowed-origins', URL,
            '--uvicorn-opts', '--no-proxy-headers']


def worker_argv():
    packages = list((VENV / 'lib').glob('python*/site-packages/mlflow/server/uvicorn_log_config.yaml'))
    require(len(packages) == 1, 'MLFLOW_VERSION_MISMATCH')
    return [str(VENV_PYTHON), '-m', 'uvicorn', '--no-proxy-headers', '--log-config',
            str(packages[0]), '--host', '127.0.0.1', '--port', str(PORT), '--workers', '1',
            'mlflow.server.fastapi_app:app']


def _command(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    require(len(result.stdout) <= 2 * 1024 * 1024, 'PROCESS_INSPECTION_TOO_LARGE')
    return result


def processes():
    result = _command(['/bin/ps', '-ax', '-o', 'pid=,ppid=,pgid=,uid=,stat=,lstart=,command='])
    require(result.returncode == 0, 'PROCESS_INSPECTION_FAILED')
    rows = {}
    for line in result.stdout.decode().splitlines():
        parts = line.split(None, 10)
        require(len(parts) == 11, 'PROCESS_INSPECTION_INVALID')
        pid, ppid, pgid, uid = map(int, parts[:4])
        rows[pid] = {'pid':pid, 'ppid':ppid, 'pgid':pgid, 'uid':uid,
                     'state':parts[4], 'start_time':' '.join(parts[5:10]), 'command':parts[10]}
    return rows


def listeners():
    result = _command(['/usr/sbin/lsof', '-nP', '-iTCP:' + str(PORT), '-sTCP:LISTEN', '-Fpn'])
    require(result.returncode in (0, 1), 'LISTENER_INSPECTION_FAILED')
    rows, pid = [], None
    for line in result.stdout.decode().splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('n'):
            require(pid is not None, 'LISTENER_INSPECTION_INVALID')
            rows.append({'pid':pid, 'address':line[1:]})
    require(result.returncode == 0 or not rows, 'LISTENER_INSPECTION_FAILED')
    return rows


def _same(current, saved):
    return current is not None and all(current.get(k) == saved.get(k)
                                       for k in ('pid','pgid','uid','start_time','command'))


def _known_zombie(current, saved):
    # ps replaces argv for an exited zombie. Only a previously observed identity
    # may be treated as exited; a reused PID or an unrecorded zombie still fails.
    return current is not None and current.get('state','').startswith('Z') and all(
        current.get(k) == saved.get(k) for k in ('pid','pgid','uid','start_time'))


def _record():
    path = STATE / 'runtime.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = json.loads(read_private(path))
    require(set(value) == {'schema_version','store_id','argv','leader','members'}
            and value['schema_version'] == 1 and value['store_id'] == require_store()['store_id']
            and value['argv'] == server_argv() and isinstance(value['members'], list), 'RUNTIME_RECORD_CONFLICT')
    leader = value['leader']
    require(isinstance(leader, dict) and type(leader.get('pid')) is int
            and leader['pid'] > 1 and leader.get('pgid') == leader['pid']
            and leader.get('uid') == os.getuid() and leader.get('command') == ' '.join(server_argv())
            and isinstance(leader.get('start_time'), str), 'RUNTIME_RECORD_CONFLICT')
    return value


def _owned_group(record, rows):
    leader = record['leader']
    members = [row for row in rows.values() if row['pgid'] == leader['pgid']]
    if not members:
        return []
    parent_live = _same(rows.get(leader['pid']), leader)
    saved = {row['pid']:row for row in record['members']}
    saved[leader['pid']] = leader
    allowed = (' '.join(server_argv()), ' '.join(worker_argv()))
    active = []
    for row in members:
        if _known_zombie(row, saved.get(row['pid'], {})):
            continue
        require(row['uid'] == os.getuid() and row['command'] in allowed, 'PROCESS_IDENTITY_CONFLICT')
        if row['pid'] == leader['pid']:
            require(parent_live, 'PROCESS_IDENTITY_CONFLICT')
        elif parent_live:
            require(row['ppid'] == leader['pid'] and row['command'] == allowed[1], 'PROCESS_GROUP_CONFLICT')
        else:
            require(_same(row, saved.get(row['pid'], {})), 'PROCESS_IDENTITY_CONFLICT')
        active.append(row)
    return active


def _healthy():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL + '/version', timeout=2) as response:
            return response.status == 200 and response.read(128).decode().strip() == VERSION
    except (OSError, ValueError):
        return False


def status():
    """Read-only; does not create directories, import the SDK, or repair state."""
    result = {'schema_version':1,'state':'uninitialized','store_id':None,'identity':None,'healthy':False}
    no_symlinks(STATE)
    port = listeners()
    if not STATE.exists():
        result['state'] = 'unknown' if port else 'uninitialized'
        return result
    marker = require_store()
    result['store_id'] = marker['store_id']
    intent = STATE/'start-intent.json'
    if intent.exists() or intent.is_symlink():
        launch=json.loads(read_private(intent))
        require(set(launch)=={'schema_version','store_id','launch_id','state'}
                and launch['schema_version']==1 and launch['store_id']==marker['store_id']
                and str(uuid.UUID(launch['launch_id']))==launch['launch_id']
                and launch['state'] in ('pending','identified','not-started'), 'START_INTENT_CONFLICT')
        if launch['state']=='pending':
            result['state']='unknown'
            return result
    record = _record()
    if record is None:
        result['state'] = 'unknown' if port else 'stopped'
        return result
    rows = processes()
    try:
        members = _owned_group(record, rows)
    except RuntimeError:
        result['state'] = 'unknown'
        return result
    # A reused leader PID outside the saved group also fails closed.
    if record['leader']['pid'] in rows and not (
            _same(rows[record['leader']['pid']], record['leader']) or
            _known_zombie(rows[record['leader']['pid']], record['leader'])):
        result['state'] = 'unknown'
        return result
    if not members:
        result['state'] = 'unknown' if port else 'stopped'
        return result
    result['identity'] = {'pid':record['leader']['pid'],'pgid':record['leader']['pgid'],
                          'start_time':record['leader']['start_time'],'argv':record['argv']}
    if port and any(row['pid'] not in {m['pid'] for m in members}
                    or row['address'] != '127.0.0.1:' + str(PORT) for row in port):
        result['state'] = 'unknown'
        return result
    result['state'] = 'running'
    result['healthy'] = bool(port) and _healthy()
    return result


def require_stopped():
    require_store()
    current = status()
    require(current['state'] == 'stopped', 'SERVER_NOT_STOPPED')
    # Detect a socket collision even when it is not a listening TCP socket yet.
    try:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', PORT))
    except OSError:
        raise RuntimeError('PORT_UNAVAILABLE') from None
    return current


def prepare():
    with store_lock():
        marker = STATE / 'store.json'
        if marker.exists() or marker.is_symlink():
            require_store()
            return status()
        require(not listeners(), 'PORT_UNAVAILABLE')
        require({p.name for p in STATE.iterdir()} == {'store.lock'}, 'UNOWNED_STORE_CONTENT')
        for path in (STORE, ARTIFACT_ROOT, STATE/'home', STATE/'home/config',
                     STATE/'home/cache', STATE/'home/data', STATE/'tmp'):
            private_dir(path, create=True)
        write_private(marker, _marker(str(uuid.uuid4())))
        return status()


def _terminate(record):
    members = _owned_group(record, processes())
    require(members, 'OWNED_PROCESS_MISSING')
    known = {row['pid']:row for row in members}
    os.killpg(record['leader']['pgid'], signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        rows = processes()
        # Darwin can discard argv before STAT changes from S to Z. After the
        # sole TERM, wait for these captured process identities without trusting
        # that transient command display. No further signal is sent in this path.
        group = [row for row in rows.values() if row['pgid'] == record['leader']['pgid']]
        require(all(row['pid'] in known for row in group), 'STOP_PROCESS_GROUP_CHANGED')
        remaining = []
        for pid, saved in known.items():
            current = rows.get(pid)
            if current is None:
                continue
            require(all(current.get(k) == saved.get(k) for k in ('pid','pgid','uid','start_time')),
                    'STOP_PROCESS_IDENTITY_CHANGED')
            if not current.get('state','').startswith('Z'):
                remaining.append(current)
        if not remaining:
            return
        time.sleep(0.1)
    # Never escalate to KILL, and never kill an unknown or changed group.
    raise RuntimeError('STOP_TIMEOUT')


def start():
    with store_lock():
        require_store()
        current = status()
        if current['state'] == 'running':
            require(current['healthy'], 'OWNED_SERVER_UNHEALTHY')
            return current
        require_stopped()
        env = _isolated_runtime()
        launch={'schema_version':1,'store_id':require_store()['store_id'],
                'launch_id':str(uuid.uuid4()),'state':'pending'}
        logpath = STATE / 'server.log'
        if logpath.exists() or logpath.is_symlink():
            no_symlinks(logpath)
            s=logpath.stat()
            require(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1
                    and stat.S_IMODE(s.st_mode)==0o600, 'UNSAFE_LOG')
        fd=os.open(logpath,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        try:
            write_private(STATE/'start-intent.json',launch)
            try:
                child=subprocess.Popen(server_argv(), stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                       cwd=STORE, env=env, start_new_session=True, umask=0o077)
            except OSError:
                launch['state']='not-started'
                write_private(STATE/'start-intent.json',launch)
                raise
        finally:
            os.close(fd)
        record=None
        try:
            for _ in range(20):
                leader=processes().get(child.pid)
                if leader and leader['command']==' '.join(server_argv()):
                    break
                time.sleep(0.05)
            require(leader is not None and leader['pgid']==child.pid
                    and leader['uid']==os.getuid() and leader['command']==' '.join(server_argv()),
                    'START_IDENTITY_UNCONFIRMED')
            record={'schema_version':1,'store_id':require_store()['store_id'],'argv':server_argv(),
                    'leader':leader,'members':[leader]}
            write_private(STATE/'runtime.json',record)
            launch['state']='identified'
            write_private(STATE/'start-intent.json',launch)
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                members=_owned_group(record,processes())
                if members != record['members']:
                    record['members']=members
                    write_private(STATE/'runtime.json',record)
                require(child.poll() is None,'SERVER_EXITED')
                observed=status()
                require(observed['state']=='running','START_IDENTITY_CONFLICT')
                if observed['healthy']:
                    record['members']=_owned_group(record,processes())
                    write_private(STATE/'runtime.json',record)
                    return observed
                time.sleep(0.2)
            raise RuntimeError('START_TIMEOUT')
        except BaseException:
            if record is not None:
                try:
                    _terminate(record)
                except (RuntimeError,OSError):
                    pass
            raise


def stop():
    with store_lock():
        require_store()
        current=status()
        require(current['state'] in ('running','stopped'),'STOP_IDENTITY_UNCONFIRMED')
        if current['state']=='running':
            _terminate(_record())
        return require_stopped()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','start','status','stop'))
    args=parser.parse_args(argv)
    try:
        result=globals()[args.command]()
        print(json.dumps(result,sort_keys=True))
        return 0 if result['state']!='unknown' else 1
    except (RuntimeError,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        print(json.dumps({'result':'failed','error_code':str(error) if isinstance(error,RuntimeError) else 'RUNTIME_OPERATION_FAILED'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
