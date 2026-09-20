#!/usr/bin/env python3
"""Own one loopback safe-JSON Ridge server with an explicit Registry version."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from tools.local_experiment.mlflow import runtime as M, serving as S
from tools.local_experiment.mlflow import registry_contract as K

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / 'tmp/local-experiment/ridge-serving'
PYTHON = M.VENV_PYTHON
HOST, PORT = S.HOST, S.PORT
URL = f'http://{HOST}:{PORT}'
OWNER = 'shirokuma-local-ridge-serving'
START_SECONDS, STOP_SECONDS = 30, 20


class ServingRuntimeError(Exception):
    """Fixed public error codes only."""


def require(value, code='SERVING_RUNTIME_INVALID'):
    if not value: raise ServingRuntimeError(code)


def _json(path):
    return json.loads(M.read_private(path))


def _save(name, value):
    M.write_private(STATE/name, value)


@contextmanager
def _lock():
    M.no_symlinks(STATE); require(STATE.parent.is_dir(), 'SERVING_PARENT_MISSING')
    M.private_dir(STATE, create=True)
    fd = os.open(STATE/'owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600, 'SERVING_LOCK_INVALID')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ServingRuntimeError('SERVING_BUSY') from None
        yield
    finally:
        os.close(fd)


def _owner():
    M.private_dir(STATE); value = _json(STATE/'owner.json')
    require(type(value) is dict and set(value) == {'schema_version','owner','profile','instance_id','url'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['owner'] == OWNER and value['profile'] == S.PROFILE and value['url'] == URL
            and str(uuid.UUID(value['instance_id'])) == value['instance_id'], 'SERVING_OWNER_INVALID')
    return value


def _token():
    value = _json(STATE/'token.json')
    require(type(value) is dict and set(value) == {'token'} and type(value['token']) is str
            and len(value['token']) == 64 and all(c in '0123456789abcdef' for c in value['token']),
            'SERVING_TOKEN_INVALID')
    return value['token']


def _prepare():
    if (STATE/'owner.json').exists() or (STATE/'owner.json').is_symlink():
        owner = _owner(); _token()
        for path in (STATE/'home', STATE/'tmp'): M.private_dir(path)
        return owner
    require({p.name for p in STATE.iterdir()} == {'owner.lock'}, 'SERVING_UNOWNED_STATE')
    for path in (STATE/'home', STATE/'tmp'): M.private_dir(path, create=True)
    _save('token.json', {'token':secrets.token_hex(32)})
    owner = dict(schema_version=1, owner=OWNER, profile=S.PROFILE, instance_id=str(uuid.uuid4()), url=URL)
    _save('owner.json', owner)
    return owner


def environment():
    return {'HOME':str(STATE/'home'), 'TMPDIR':str(STATE/'tmp'), 'TMP':str(STATE/'tmp'),
        'TEMP':str(STATE/'tmp'), 'PATH':str(PYTHON.parent)+':/usr/bin:/bin', 'LC_ALL':'C',
        '__CF_USER_TEXT_ENCODING':f'0x{os.getuid():X}:0x0:0x0', 'PYTHONNOUSERSITE':'1',
        'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONUNBUFFERED':'1',
        'MLFLOW_DISABLE_TELEMETRY':'true', 'DO_NOT_TRACK':'true'}


def server_argv(version, launch_id):
    number = K.version(version)
    require(type(launch_id) is str and str(uuid.UUID(launch_id)) == launch_id)
    return [str(PYTHON), '-m', 'tools.local_experiment.mlflow.serving_runtime', 'serve',
            '--version', number, '--launch-id', launch_id]


def listeners():
    result = M._command(['/usr/sbin/lsof','-nP','-iTCP:'+str(PORT),'-sTCP:LISTEN','-Fpn'])
    require(result.returncode in (0,1), 'SERVING_LISTENER_UNKNOWN')
    found, pid = [], None
    for line in result.stdout.decode().splitlines():
        if line.startswith('p'): pid = int(line[1:])
        elif line.startswith('n'):
            require(pid is not None, 'SERVING_LISTENER_UNKNOWN')
            found.append({'pid':pid,'address':line[1:]})
    require(result.returncode == 0 or not found, 'SERVING_LISTENER_UNKNOWN')
    return found


def _intent(owner):
    value = _json(STATE/'start-intent.json')
    require(type(value) is dict and set(value) == {'schema_version','instance_id','launch_id','version','state'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['instance_id'] == owner['instance_id'] and value['version'] == K.version(value['version'])
            and str(uuid.UUID(value['launch_id'])) == value['launch_id']
            and value['state'] in ('pending','identified','not-started'), 'SERVING_INTENT_INVALID')
    return value


def _config(owner, launch):
    value = _json(STATE/'config.json')
    require(type(value) is dict and set(value) == {'schema_version','instance_id','launch_id','binding','health'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['instance_id'] == owner['instance_id'] and value['launch_id'] == launch['launch_id'],
            'SERVING_CONFIG_INVALID')
    S.validate_binding(value['binding'])
    require(value['binding']['version'] == launch['version'] and value['health'] == S.expected_health(value['binding']),
            'SERVING_CONFIG_INVALID')
    return value


def _record(owner, launch):
    value = _json(STATE/'runtime.json'); argv = server_argv(launch['version'], launch['launch_id'])
    require(type(value) is dict and set(value) == {'schema_version','instance_id','launch_id','argv','leader'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['instance_id'] == owner['instance_id'] and value['launch_id'] == launch['launch_id']
            and value['argv'] == argv, 'SERVING_RECORD_INVALID')
    leader = value['leader']
    require(type(leader) is dict and type(leader.get('pid')) is int and leader['pid'] > 1
            and type(leader.get('ppid')) is int and leader['ppid'] > 1
            and leader.get('pgid') == leader['pid'] and leader.get('uid') == os.getuid()
            and leader.get('command') == ' '.join(argv) and type(leader.get('start_time')) is str
            and 1 <= len(leader['start_time']) <= 64, 'SERVING_RECORD_INVALID')
    return value


def _owned(record, rows):
    leader = record['leader']; current = rows.get(leader['pid'])
    require(current is None or M._same(current, leader) or M._known_zombie(current, leader),
            'SERVING_PROCESS_IDENTITY_CHANGED')
    require(not any(row['pgid'] == leader['pgid'] and row['pid'] != leader['pid'] for row in rows.values()),
            'SERVING_PROCESS_GROUP_CHANGED')
    return current if current is not None and not current.get('state','').startswith('Z') else None


def _owned_listeners(record, port):
    require(not any(row['pid'] != record['leader']['pid'] or row['address'] != f'{HOST}:{PORT}' for row in port),
            'SERVING_PORT_CONFLICT')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ServingRuntimeError('SERVING_REDIRECT_REFUSED')


def _healthy(expected):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(URL+'/health', headers={'Authorization':'Bearer '+_token(), 'Host':f'{HOST}:{PORT}'})
    try:
        with opener.open(request, timeout=5) as response:
            raw = response.read(65537)
            return response.status == 200 and len(raw) <= 65536 and json.loads(raw) == expected
    except (OSError, ValueError, ServingRuntimeError):
        return False


def _public(state, *, version=None, health=None, leader=None, healthy=False, error=None):
    return dict(schema_version=1, profile=S.PROFILE, state=state, url=URL, version=version,
        binding_sha256=None if health is None else health['binding_sha256'], healthy=healthy,
        identity=None if leader is None else {k:leader[k] for k in ('pid','pgid','start_time')},
        cleanup_verified=state in ('stopped','uninitialized'), error_code=error)


def status():
    """Inspect ownership and authenticated health without changing state or loading a model."""
    M.no_symlinks(STATE); port = listeners()
    if not STATE.exists(): return _public('unknown', error='SERVING_PORT_CONFLICT') if port else _public('uninitialized')
    M.private_dir(STATE)
    if not (STATE/'owner.json').exists():
        clean = {p.name for p in STATE.iterdir()} <= {'owner.lock'}
        return _public('uninitialized') if clean and not port else _public('unknown', error='SERVING_UNOWNED_STATE')
    owner = _owner()
    if not (STATE/'start-intent.json').exists():
        return _public('unknown', error='SERVING_START_UNCONFIRMED') if port or (STATE/'config.json').exists() else _public('stopped')
    launch = _intent(owner)
    if launch['state'] == 'pending': return _public('unknown', version=launch['version'], error='SERVING_START_UNCONFIRMED')
    config = _config(owner, launch)
    if launch['state'] == 'not-started':
        return _public('unknown' if port else 'stopped', version=launch['version'], health=config['health'])
    record = _record(owner, launch)
    try:
        live = _owned(record, M.processes()); _owned_listeners(record, port)
        if live is None:
            require(not port, 'SERVING_PORT_CONFLICT')
            return _public('stopped', version=launch['version'], health=config['health'])
        healthy = bool(port) and _healthy(config['health'])
        # Do not accept health from an identity or listener that changed while reading.
        require(_owned(record, M.processes()) is not None, 'SERVING_PROCESS_IDENTITY_CHANGED')
        after = listeners(); _owned_listeners(record, after); healthy = healthy and bool(after)
        return _public('running', version=launch['version'], health=config['health'], leader=live, healthy=healthy)
    except ServingRuntimeError as error:
        return _public('unknown', version=launch['version'], error=str(error))


def _port_free():
    require(not listeners(), 'SERVING_PORT_CONFLICT')
    try:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); sock.bind((HOST, PORT))
    except OSError: raise ServingRuntimeError('SERVING_PORT_CONFLICT') from None


def _terminate(record):
    require(_owned(record, M.processes()) is not None, 'SERVING_PROCESS_MISSING')
    _owned_listeners(record, listeners())
    leader = record['leader']; os.killpg(leader['pgid'], signal.SIGTERM)
    deadline = time.monotonic() + STOP_SECONDS
    while time.monotonic() < deadline:
        rows = M.processes(); current = rows.get(leader['pid'])
        require(not any(p['pgid'] == leader['pgid'] and p['pid'] != leader['pid'] for p in rows.values()),
                'SERVING_STOP_GROUP_CHANGED')
        if current is None or M._known_zombie(current, leader):
            require(not listeners(), 'SERVING_PORT_CONFLICT'); return
        # Darwin may discard argv during exit. After the single TERM, observe
        # the already captured process identity without sending another signal.
        require(all(current.get(k) == leader[k] for k in ('pid','pgid','uid','start_time')),
                'SERVING_STOP_IDENTITY_CHANGED')
        time.sleep(0.1)
    raise ServingRuntimeError('SERVING_STOP_TIMEOUT')


def start(version):
    version = K.version(version)
    with _lock():
        current = status()
        if current['state'] == 'running':
            require(current['version'] == version, 'SERVING_VERSION_ALREADY_RUNNING')
            require(current['healthy'], 'SERVING_UNHEALTHY'); return current
        require(current['state'] in ('stopped','uninitialized'), 'SERVING_START_UNCONFIRMED')
        _port_free(); require(PYTHON.is_file(), 'SERVING_PYTHON_MISSING'); owner = _prepare()
        binding = S.load_binding(version); health = S.health(binding)
        require(health == S.expected_health(binding), 'SERVING_BINDING_CHANGED')
        launch = dict(schema_version=1, instance_id=owner['instance_id'], launch_id=str(uuid.uuid4()), version=version, state='pending')
        log = STATE/'server.log'
        if log.exists() or log.is_symlink():
            M.no_symlinks(log); info = log.stat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600, 'SERVING_LOG_INVALID')
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            _save('start-intent.json', launch)
            _save('config.json', dict(schema_version=1, instance_id=owner['instance_id'], launch_id=launch['launch_id'], binding=binding, health=health))
            try:
                child = subprocess.Popen(server_argv(version, launch['launch_id']), stdin=subprocess.DEVNULL,
                    stdout=fd, stderr=fd, cwd=ROOT, env=environment(), start_new_session=True, umask=0o077)
            except OSError:
                launch['state'] = 'not-started'; _save('start-intent.json', launch); raise
        finally:
            os.close(fd)
        record = None; deadline = time.monotonic() + START_SECONDS
        try:
            while time.monotonic() < deadline:
                require(child.poll() is None, 'SERVING_PROCESS_EXITED')
                leader = M.processes().get(child.pid)
                if leader is not None and leader['command'] == ' '.join(server_argv(version, launch['launch_id'])):
                    require(leader['pgid'] == child.pid and leader['uid'] == os.getuid(), 'SERVING_START_UNCONFIRMED')
                    record = dict(schema_version=1, instance_id=owner['instance_id'], launch_id=launch['launch_id'],
                                  argv=server_argv(version, launch['launch_id']), leader=leader)
                    _save('runtime.json', record); launch['state'] = 'identified'; _save('start-intent.json', launch)
                    break
                time.sleep(0.05)
            require(record is not None, 'SERVING_START_UNCONFIRMED')
            while time.monotonic() < deadline:
                require(child.poll() is None, 'SERVING_PROCESS_EXITED')
                current = status(); require(current['state'] == 'running', 'SERVING_START_UNCONFIRMED')
                if current['healthy']: return current
                time.sleep(0.1)
            raise ServingRuntimeError('SERVING_START_TIMEOUT')
        except BaseException as original:
            if record is not None:
                try: _terminate(record); child.wait(timeout=2)
                except BaseException as secondary:
                    if isinstance(original, Exception) and not isinstance(secondary, Exception): raise secondary
            raise


def stop():
    with _lock():
        current = status()
        require(current['state'] in ('running','stopped','uninitialized'), 'SERVING_STOP_UNCONFIRMED')
        if current['state'] == 'running':
            owner = _owner(); _terminate(_record(owner, _intent(owner)))
        result = status(); require(result['cleanup_verified'], 'SERVING_STOP_UNCONFIRMED'); return result


def serve(version, launch_id):
    """Internal worker: wait for the parent's saved ownership before binding."""
    version = K.version(version); owner = _owner(); deadline = time.monotonic() + START_SECONDS
    while True:
        launch = _intent(owner)
        require(launch['launch_id'] == launch_id and launch['version'] == version, 'SERVING_START_UNCONFIRMED')
        if launch['state'] == 'identified': break
        require(launch['state'] == 'pending' and time.monotonic() < deadline, 'SERVING_START_UNCONFIRMED')
        time.sleep(0.05)
    config = _config(owner, launch); record = _record(owner, launch)
    # The parent has already observed argv/start time and saved our identity.
    # Native self checks avoid creating a ps child in this group while the
    # parent's status/start/stop paths inspect every group member externally.
    require(record['leader']['pid'] == os.getpid() and os.getpgrp() == os.getpid()
            and record['leader']['uid'] == os.getuid() and record['leader']['ppid'] == os.getppid(),
            'SERVING_START_UNCONFIRMED')
    binding = S.load_binding(version)
    require(binding == config['binding'], 'SERVING_BINDING_CHANGED')
    stopping = False
    def stop_requested(_signum, _frame):
        nonlocal stopping
        stopping = True
    previous = {signum:signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    try:
        for signum in previous: signal.signal(signum, stop_requested)
        S.serve(binding, _token(), should_stop=lambda:stopping)
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start','status','stop','serve'))
    parser.add_argument('--version'); parser.add_argument('--launch-id')
    args = parser.parse_args(argv)
    try:
        require((args.version is not None) == (args.command in ('start','serve'))
                and (args.launch_id is not None) == (args.command == 'serve'), 'SERVING_ARGUMENTS_INVALID')
        if args.command == 'serve': return serve(args.version, args.launch_id)
        result = start(args.version) if args.command == 'start' else globals()[args.command]()
        print(json.dumps(result, sort_keys=True)); return 1 if result['state'] == 'unknown' else 0
    except (Exception, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, ServingRuntimeError) else 'SERVING_OPERATION_FAILED'
        print(json.dumps({'result':'failed','error_code':code}, sort_keys=True)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
