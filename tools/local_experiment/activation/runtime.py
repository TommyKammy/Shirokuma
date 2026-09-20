#!/usr/bin/env python3
"""Enable local Grafana Alerting while retaining the original container/data.

Resume is explicit and reconciles fixed names and operation labels. Rollback
restores the original disabled configuration using the same volume; it does
not rewind its database or delete either container or the cold backup.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import sqlite3
import stat
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.local_experiment.grafana import manage as G

PROFILE = 'local-grafana-alerting-migration-v1'
MAX_BACKUP_BYTES = 1024 ** 3
MAX_DATABASE_BYTES = 128 * 1024 ** 2
LABEL = 'shirokuma.dev.activation'


class ActivationError(RuntimeError):
    """No Docker configuration, credentials or upstream error text."""


def require(condition):
    if not condition:
        raise ActivationError('ACTIVATION_RUNTIME_UNVERIFIED')


def keep(first, later):
    return later if first is None or isinstance(first, Exception) and not isinstance(later, Exception) else first


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def close_preserving(stream):
    first = None
    try:
        yield stream
    except BaseException as error:
        first = error
    finally:
        try:
            stream.close()
        except BaseException as error:
            first = keep(first, error)
    if first is not None:
        raise first


def private_directory(path, *, create=False):
    if create and not path.exists():
        path.mkdir(mode=0o700)
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode))
        if parent == G.STATE or G.STATE in parent.parents:
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)


def read_bytes(path, maximum=65536):
    private_directory(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and before.st_size <= maximum)
    with close_preserving(os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb')) as stream:
        require(signature(os.fstat(stream.fileno())) == signature(before))
        raw = stream.read(maximum + 1)
    require(signature(path.lstat()) == signature(before) and len(raw) <= maximum)
    return raw


@contextmanager
def lock():
    private_directory(G.STATE)
    path = G.STATE / 'runtime.lock'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    first = None
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BaseException as error:
        first = error
    finally:
        try:
            os.close(fd)
        except BaseException as error:
            first = keep(first, error)
    if first is not None:
        raise first


def directory(token):
    require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token) is not None)
    return G.STATE / 'alerting-operations' / token


def save(record):
    G.private_write(directory(record['token']) / 'receipt.json', record)


def configuration(value):
    # Docker may enumerate the same mount map in a different order per inspect.
    return digest(dict(Config=value['Config'], HostConfig=value['HostConfig'],
                       Mounts=sorted(value['Mounts'], key=lambda item: json.dumps(item, sort_keys=True))))


def rebind_mount_order(record, credential):
    """Retain a pre-effect receipt before correcting only its mount ordering."""
    old = G.inspect('container', record['original_id'])
    require(old is not None)
    expected = configuration(old)
    if record['configuration_sha256'] == expected:
        return
    mode = G.alerting_mode(credential)
    require(record['result'] == 'failed' and record['pending'] is None
            and record['new_id'] is None and record['backup'] is None
            and mode['state'] == 'pending' and mode['alerting'] is False
            and mode['container_id'] == old['Id']
            and old['Name'] == '/' + G.NAME and old['State']['Running'] is True
            and type(old['Mounts']) is list and 0 < len(old['Mounts']) <= 5)
    require(any(digest(dict(Config=old['Config'], HostConfig=old['HostConfig'], Mounts=list(order)))
                == record['configuration_sha256'] for order in itertools.permutations(old['Mounts'])))
    updated = dict(record, configuration_sha256=expected)
    validate(updated, credential)
    base = directory(record['token'])
    raw = read_bytes(base / 'receipt.json')
    require(json.loads(raw) == record)
    prior = base / 'receipt-before-mount-order.json'
    if prior.exists() or prior.is_symlink():
        require(read_bytes(prior) == raw)
    else:
        with close_preserving(os.fdopen(os.open(prior, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')) as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    save(updated)
    record.update(updated)


def known_environment(value, credential):
    image = json.loads(G.docker('image', 'inspect', G.IMAGE))[0]
    require(image['Architecture'] == 'arm64' and image['Os'] == 'linux')
    expected = dict(item.split('=', 1) for item in image['Config'].get('Env', []))
    expected.update(G.environment(credential))
    actual = value['Config'].get('Env', [])
    require(len(actual) == len(dict(item.split('=', 1) for item in actual))
            and dict(item.split('=', 1) for item in actual) == expected)


def volume_identity():
    value = G.inspect('volume', G.VOLUME)
    require(value is not None and value.get('Name') == G.VOLUME and value.get('Driver') == 'local' and not value.get('Options'))
    return digest({key: value.get(key) for key in ('Name', 'Driver', 'Options', 'Labels', 'CreatedAt')})


def env_text(credential, alerting):
    return ''.join(key + '=' + value + '\n' for key, value in G.environment(credential, alerting=alerting).items()).encode()


def validate(record, credential):
    require(record['profile'] == PROFILE and record['owner_token'] == credential['owner_token']
            and record['credential_sha256'] == digest(credential) and record['volume_sha256'] == volume_identity())
    old = G.inspect('container', record['original_id'])
    require(old is not None and old['Id'] == record['original_id'])
    G.validate_container(old, credential, alerting=False)
    require(configuration(old) == record['configuration_sha256']
            and old.get('Name') in ('/' + G.NAME, '/' + record['retained_name']))
    for name, identity in record['networks'].items():
        network = G.inspect('network', name)
        require(network is not None and network['Id'] == identity)
    require(read_bytes(directory(record['token']) / 'before-container.env') == env_text(credential, False))
    require(read_bytes(G.STATE / 'container.env') in (env_text(credential, False), env_text(credential, True)))
    require(hashlib.sha256(read_bytes(G.STATE / 'reader-users.xml')).hexdigest() == record['reader_sha256'])
    return old


def select(record, *, alerting, state, container_id):
    G.private_write(G.STATE / 'alerting-mode.json', dict(schema_version=1, owner_token=record['owner_token'],
        operation_token=record['token'], state=state, alerting=alerting, container_id=container_id,
        original_id=record['original_id'], retained_name=record['retained_name'],
        backup_sha256=record['backup']['sha256'] if record['backup'] else None))


def pending(record, operation):
    record.update(result='running', pending=operation, error_code=None)
    save(record)


def stopped(identity):
    value = G.inspect('container', identity)
    require(value is not None and value['Id'] == identity and value['State']['Running'] is False)
    return value


def archive_projection(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.getuid() and info.st_nlink == 1 and 1024 <= info.st_size <= MAX_BACKUP_BYTES)
    files, entries, total, found = 0, 0, 0, False
    with path.open('rb') as stream:
        stream.seek(-1024, os.SEEK_END)
        require(stream.read() == b'\0' * 1024)
    with tarfile.open(path, 'r:') as archive:
        for member in archive:
            entries += 1
            name = PurePosixPath(member.name)
            require(entries <= 100000 and not name.is_absolute() and '..' not in name.parts
                    and (member.isdir() or member.isfile() or member.issym() or member.islnk()))
            if member.isfile():
                files += 1
                stream = archive.extractfile(member)
                require(stream is not None)
                size = 0
                with stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        size += len(chunk); total += len(chunk)
                        require(total <= MAX_BACKUP_BYTES)
                require(size == member.size)
                found |= name.name == 'grafana.db' and size > 0
    require(found and signature(path.lstat()) == signature(info))
    digest_value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest_value.update(chunk)
    require(signature(path.lstat()) == signature(info))
    return dict(sha256=digest_value.hexdigest(), bytes=info.st_size, entries=entries, files=files)


def zero_rules(record, archive_path):
    """Inspect a private cold DB copy before the previously disabled engine runs."""
    database = directory(record['token']) / 'before-grafana.db'
    with tarfile.open(archive_path, 'r:') as archive:
        members = list(archive)
        selected = [m for m in members if PurePosixPath(m.name).parts == ('grafana.db',)]
        require(len(selected) == 1 and selected[0].isfile() and 0 < selected[0].size <= MAX_DATABASE_BYTES)
        require(not any(PurePosixPath(m.name).name in ('grafana.db-wal', 'grafana.db-journal') and m.size
                        for m in members))
        with archive.extractfile(selected[0]) as stream:
            raw = stream.read(MAX_DATABASE_BYTES + 1)
        require(len(raw) == selected[0].size)
    if not database.exists() and not database.is_symlink():
        with close_preserving(os.fdopen(os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')) as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    require(read_bytes(database, MAX_DATABASE_BYTES) == raw)
    before = signature(database.lstat())
    connection, first, count = None, None, None
    try:
        connection = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
        connection.execute('PRAGMA query_only=ON')
        require(connection.execute('PRAGMA quick_check').fetchall() == [('ok',)])
        require(connection.execute("SELECT type FROM sqlite_master WHERE name='alert_rule'").fetchall() == [('table',)])
        count = connection.execute('SELECT count(*) FROM alert_rule').fetchone()[0]
        require(type(count) is int and count == 0)
    except BaseException as error:
        first = error
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException as error:
                first = keep(first, error)
    if first is not None:
        raise first
    require(signature(database.lstat()) == before and read_bytes(database, MAX_DATABASE_BYTES) == raw)
    return dict(alert_rule_count=count, database_sha256=hashlib.sha256(raw).hexdigest())


def backup(record):
    base = directory(record['token'])
    path, receipt = base / 'volume.tar', base / 'backup.json'
    stopped(record['original_id'])
    if receipt.exists():
        value = json.loads(read_bytes(receipt))
        require(value == dict(source_id=record['original_id'], volume_sha256=record['volume_sha256'],
                              **archive_projection(path), **zero_rules(record, path)))
        return value
    # A partial archive remains for inspection; never overwrite or automatically
    # reinterpret an unknown copy as a successful cold backup.
    require(not path.exists() and not path.is_symlink())
    pending(record, 'cold-backup')
    require(not G.docker('ps', '--no-trunc', '--filter', 'volume=' + G.VOLUME, '--format', '{{.ID}}').strip())
    def limit():
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BACKUP_BYTES, MAX_BACKUP_BYTES))
    with close_preserving(os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb')) as stream:
        result = subprocess.run(['rtk', 'proxy', 'docker', '--context', G.CONTEXT, 'cp',
            record['original_id'] + ':/var/lib/grafana/.', '-'], cwd=ROOT, stdout=stream,
            stderr=subprocess.DEVNULL, timeout=120, preexec_fn=limit)
        require(result.returncode == 0)
        stream.flush(); os.fsync(stream.fileno())
    stopped(record['original_id'])
    require(not G.docker('ps', '--no-trunc', '--filter', 'volume=' + G.VOLUME, '--format', '{{.ID}}').strip())
    value = dict(source_id=record['original_id'], volume_sha256=record['volume_sha256'],
                 **archive_projection(path), **zero_rules(record, path))
    G.private_write(receipt, value)
    return value


def new_container(record, credential):
    own = G.inspect('container', G.NAME)
    if own is None:
        return None
    require(own['Id'] != record['original_id'] and (record['new_id'] is None or record['new_id'] == own['Id']))
    G.validate_container(own, credential, alerting=True)
    require(own['Config']['Labels'].get(LABEL) == record['token'])
    return own


def public(record):
    return dict(result=record['result'], operation_token=record['token'],
        alerting=record['result'] == 'passed', original_container_retained=True,
        backup_verified=record['backup'] is not None, volume_retained=True,
        rollback_scope='configuration-only-same-volume',
        evidence=str((directory(record['token']) / 'receipt.json').relative_to(ROOT)),
        error_code=record['error_code'])


def forward(record, credential):
    old = validate(record, credential)
    if record['result'] == 'rolled-back':
        return public(record)
    if record['result'] == 'passed' and G.alerting_mode(credential)['state'] == 'active':
        require(G.status().get('ready') is True)
        stopped(record['original_id'])
        return public(record)
    if old['State']['Running']:
        require(old['Name'] == '/' + G.NAME and record['new_id'] is None)
        pending(record, 'stop-original')
        G.docker('stop', '--time', '30', old['Id'])
    stopped(old['Id'])
    record['backup'] = backup(record); record['pending'] = None; save(record)
    old = validate(record, credential)
    if old['Name'] == '/' + G.NAME:
        pending(record, 'retain-original')
        G.docker('rename', old['Id'], record['retained_name'])
    require(stopped(old['Id'])['Name'] == '/' + record['retained_name'])
    own = new_container(record, credential)
    if own:
        record['new_id'] = own['Id']; save(record)
    select(record, alerting=True, state='pending', container_id=record['new_id'])
    pending(record, 'start-alerting')
    ready = G.up(alerting=True)
    require(ready.get('ready') is True)
    own = new_container(record, credential)
    require(own is not None and own['State']['Running'] is True)
    record['new_id'] = own['Id']
    validate(record, credential); stopped(record['original_id'])
    select(record, alerting=True, state='active', container_id=own['Id'])
    record.update(result='passed', pending=None, error_code=None); save(record)
    return public(record)


def load(token, credential, *, rebind=False):
    path = directory(token)
    private_directory(path)
    record = json.loads(read_bytes(path / 'receipt.json'))
    require(type(record) is dict and set(record) == {'schema_version','profile','token','owner_token',
            'credential_sha256','original_id','new_id','configuration_sha256','volume_sha256','networks',
            'reader_sha256','retained_name','retained_new_name','backup','result','pending','error_code'}
            and type(record['schema_version']) is int and record['schema_version'] == 1
            and set(record['networks']) == {G.NETWORK, G.FRONTEND_NETWORK}
            and record['result'] in {'running','passed','failed','rolled-back'}
            and record['token'] == token and record['retained_name'] == G.NAME + '-before-alerting-' + token
            and record['retained_new_name'] == G.NAME + '-alerting-' + token)
    mode = G.alerting_mode(credential)
    require(mode is not None and mode['operation_token'] == token and mode['original_id'] == record['original_id'])
    if rebind:
        rebind_mount_order(record, credential)
    validate(record, credential)
    return record


def run_record(record, call):
    try:
        return call()
    except BaseException as error:
        record.update(result='failed', error_code='ACTIVATION_RUNTIME_UNVERIFIED')
        try:
            save(record)
        except BaseException as later:
            error = keep(error, later)
        raise error


def enable():
    with lock():
        G.check_context(); credential = G.credentials()
        mode = G.alerting_mode(credential)
        if mode:
            require(mode['state'] == 'active')
            record = load(mode['operation_token'], credential)
            require(G.status().get('ready') is True)
            stopped(record['original_id'])
            return public(record)
        own = G.inspect('container', G.NAME)
        require(own is not None and re.fullmatch('[0-9a-f]{64}', own['Id']))
        G.validate_container(own, credential, alerting=False)
        known_environment(own, credential)
        require(own.get('Name') == '/' + G.NAME)
        require(read_bytes(G.STATE / 'container.env') == env_text(credential, False))
        reader = read_bytes(G.STATE / 'reader-users.xml')
        require(reader.decode() in (G.reader_xml(credential), G.reader_xml(credential, G.HISTORY_READER_TABLES)))
        base = G.STATE / 'alerting-operations'; private_directory(base, create=True)
        token = uuid.uuid4().hex; output = directory(token); private_directory(output, create=True)
        G.private_write(output / 'before-container.env', env_text(credential, False).decode())
        record = dict(schema_version=1, profile=PROFILE, token=token, owner_token=credential['owner_token'],
            credential_sha256=digest(credential), original_id=own['Id'], new_id=None,
            configuration_sha256=configuration(own), volume_sha256=volume_identity(),
            networks={name:own['NetworkSettings']['Networks'][name]['NetworkID'] for name in (G.NETWORK,G.FRONTEND_NETWORK)},
            reader_sha256=hashlib.sha256(reader).hexdigest(), retained_name=G.NAME+'-before-alerting-'+token,
            retained_new_name=G.NAME+'-alerting-'+token, backup=None, result='running', pending=None, error_code=None)
        save(record); select(record, alerting=False, state='pending', container_id=own['Id'])
        return run_record(record, lambda: forward(record, credential))


def resume(token):
    with lock():
        G.check_context(); credential = G.credentials(); record = load(token, credential, rebind=True)
        return run_record(record, lambda: forward(record, credential))


def rollback(token):
    with lock():
        G.check_context(); credential = G.credentials(); record = load(token, credential)
        def restore():
            old = validate(record, credential)
            own = G.inspect('container', G.NAME)
            if own and own['Id'] != old['Id']:
                own = new_container(record, credential); record['new_id'] = own['Id']; save(record)
                pending(record, 'stop-alerting')
                if own['State']['Running']:
                    G.docker('stop', '--time', '30', own['Id'])
                stopped(own['Id']); pending(record, 'retain-alerting')
                G.docker('rename', own['Id'], record['retained_new_name'])
            if record['new_id']:
                retained = stopped(record['new_id'])
                require(retained['Name'] == '/' + record['retained_new_name'])
                G.validate_container(retained, credential, alerting=True)
                require(retained['Config']['Labels'].get(LABEL) == token)
            own = G.inspect('container', G.NAME)
            require(own is None or own['Id'] == old['Id'])
            if old['Name'] != '/' + G.NAME:
                pending(record, 'restore-original-name')
                G.docker('rename', old['Id'], G.NAME)
            select(record, alerting=False, state='pending', container_id=old['Id'])
            pending(record, 'start-original')
            require(G.up(alerting=False).get('ready') is True)
            validate(record, credential)
            select(record, alerting=False, state='rolled-back', container_id=old['Id'])
            record.update(result='rolled-back', pending=None, error_code=None); save(record)
            return public(record)
        return run_record(record, restore)


def status():
    with lock():
        G.check_context(); credential = G.credentials(); mode = G.alerting_mode(credential)
        if mode is None:
            return dict(result='not-enabled', alerting=False, evidence=None)
        return public(load(mode['operation_token'], credential))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ['enable']:
            result = enable()
        elif args == ['status']:
            result = status()
        elif len(args) == 2 and args[0] in ('resume', 'rollback'):
            result = (resume if args[0] == 'resume' else rollback)(args[1])
        else:
            raise ActivationError('ACTIVATION_RUNTIME_ARGUMENTS_INVALID')
        print(json.dumps(result, sort_keys=True))
        return 0 if result['result'] in ('passed','rolled-back','not-enabled') else 1
    except Exception:
        print(json.dumps(dict(result='failed', error_code='ACTIVATION_RUNTIME_UNVERIFIED')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
