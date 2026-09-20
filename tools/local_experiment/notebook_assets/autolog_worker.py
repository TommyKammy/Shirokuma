"""Own one fixed autolog worker without importing the ML SDK in DataOps."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time

from tools.local_experiment.mlflow import autolog as A

N, T, R = A.T.N, A.T, A.R
ROOT = A.ROOT
STATE = ROOT / 'tmp/local-experiment/notebook-training-assets'
PROFILE = 'owned-autolog-worker-v1'
SECONDS, GRACE_SECONDS, MAX_ATTEMPTS = 180, 60, 128
MAX_STDOUT, MAX_STDERR = 65536, 16384
STATUS_KEYS = {'schema_version', 'state', 'training_token', 'source_sha256', 'intent_sha256',
               'pid', 'pgid', 'process_start', 'exit_code', 'child_exited', 'group_empty',
               'cleanup_verified', 'reused', 'result_sha256', 'error_code'}


class WorkerError(ValueError):
    """Fixed local error codes, without worker stderr or private data."""


def require(condition, code='ASSET_AUTOLOG_INVALID'):
    if not condition:
        raise WorkerError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _private(path):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    value = path.stat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.getuid()
            and stat.S_IMODE(value.st_mode) == 0o700)


def _output(value):
    path = Path(value)
    require(path.is_absolute() and path.name == 'autolog' and path.parent.parent == STATE
            and re.fullmatch(r'[0-9a-f]{32}', path.parent.name) is not None)
    for parent in (STATE, path.parent, path):
        _private(parent)
    return path


def _read(path, maximum):
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= maximum)
    fd, raw, first = None, b'', None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        require(T._signature(os.fstat(fd)) == T._signature(before))
        while len(raw) <= maximum:
            chunk = os.read(fd, maximum + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
        require(len(raw) <= maximum and T._signature(os.fstat(fd)) == T._signature(before))
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    require(T._signature(path.stat()) == T._signature(before))
    return raw, T._signature(before)


def _save(path, value, *, replace=False):
    temporary = path.with_name('.' + path.name + '.pending') if replace else path
    fd, first = None, None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        raw = N._canonical(value)
        while raw:
            written = os.write(fd, raw)
            require(written > 0)
            raw = raw[written:]
        os.fsync(fd)
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first
    if replace: os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    except BaseException as error: first = error
    finally:
        try: os.close(fd)
        except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first


def _backup_name(value):
    require(type(value) is str and value.startswith('tmp/local-experiment/'))
    path = Path(value)
    require(not path.is_absolute() and path.as_posix() == value and '..' not in path.parts
            and path.name == 'backup-manifest.json')
    return value


def arguments(token, backup_manifest):
    return [str(R.VENV_PYTHON), '-I', '-B', str(Path(A.__file__).resolve()), '--worker',
            '--training-token', A.C.token(token), '--backup-manifest', _backup_name(backup_manifest)]


def status(output):
    output = _output(output)
    if not (output / 'intent.json').exists() and not (output / 'intent.json').is_symlink():
        require(not list(output.iterdir()))
        return None
    raw, _ = _read(output / 'intent.json', 8192)
    intent = N._parse(raw)
    require(raw == N._canonical(intent) and type(intent) is dict and set(intent) == {
        'schema_version', 'profile', 'training_token', 'source_sha256', 'backup', 'argv', 'reused'})
    require(type(intent['schema_version']) is int and intent['schema_version'] == 1
            and intent['profile'] == PROFILE and A.C.token(intent['training_token']) == intent['training_token']
            and type(intent['source_sha256']) is str and re.fullmatch('[0-9a-f]{64}', intent['source_sha256'])
            and type(intent['reused']) is bool)
    if intent['reused']:
        require(intent['backup'] is None and intent['argv'] is None)
    else:
        backup = intent['backup']
        require(type(backup) is dict and set(backup) == {'manifest', 'sha256', 'store_id'}
                and type(backup['sha256']) is str and re.fullmatch('[0-9a-f]{64}', backup['sha256'])
                and type(backup['store_id']) is str)
        require(intent['argv'] == arguments(intent['training_token'], backup['manifest']))
    raw_status, _ = _read(output / 'status.json', 8192)
    value = N._parse(raw_status)
    require(raw_status == N._canonical(value) and type(value) is dict and set(value) == STATUS_KEYS
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['state'] in ('pending', 'running', 'passed', 'failed', 'interrupted', 'unknown')
            and value['training_token'] == intent['training_token'] and value['source_sha256'] == intent['source_sha256']
            and value['intent_sha256'] == _sha(raw) and value['reused'] is intent['reused']
            and all(type(value[k]) is bool for k in ('child_exited', 'group_empty', 'cleanup_verified'))
            and all(value[k] is None or type(value[k]) is int and value[k] > 1 for k in ('pid', 'pgid'))
            and value['pid'] == value['pgid']
            and (value['process_start'] is None or type(value['process_start']) is str
                 and 1 <= len(value['process_start']) <= 64 and '\n' not in value['process_start'])
            and (value['exit_code'] is None or type(value['exit_code']) is int)
            and value['child_exited'] == (value['exit_code'] is not None)
            and value['cleanup_verified'] == (value['child_exited'] and value['group_empty'])
            and (value['error_code'] is None or value['error_code'] in {
                'ASSET_AUTOLOG_FAILED', 'ASSET_AUTOLOG_INTERRUPTED', 'ASSET_AUTOLOG_CLEANUP_UNKNOWN'}))
    require(not value['cleanup_verified'] or value['state'] in ('passed', 'failed', 'interrupted'))
    if value['state'] == 'passed':
        require(value['cleanup_verified'] and value['exit_code'] == 0 and value['error_code'] is None
                and (value['reused'] or value['pid'] is not None and value['process_start'] is not None))
        result_raw, _ = _read(output / 'result.json', MAX_STDOUT + 4096)
        result = N._parse(result_raw)
        require(result_raw == N._canonical(result) and value['result_sha256'] == _sha(result_raw)
                and set(result) == {'schema_version', 'profile', 'result', 'training_token', 'source_sha256',
                                    'reused', 'cleanup_verified', 'autolog'}
                and result == dict(schema_version=1, profile=PROFILE, result='passed',
                    training_token=intent['training_token'], source_sha256=intent['source_sha256'],
                    reused=intent['reused'], cleanup_verified=True, autolog=result['autolog']))
    else:
        require(value['result_sha256'] is None)
    return value


def closed(output):
    """Saved terminal ownership only; historical PIDs are never signalled or probed."""
    try:
        value = status(output)
        return value is None or value['cleanup_verified']
    except Exception:
        return False


@contextmanager
def _lock(output):
    _output(output)
    fd, first = None, None
    try:
        fd = os.open(STATE / '.autolog-owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise WorkerError('ASSET_AUTOLOG_BUSY') from None
        entries = [p for p in STATE.iterdir() if p.name != '.autolog-owner.lock']
        require(len(entries) <= MAX_ATTEMPTS)
        for entry in entries:
            require(re.fullmatch('[0-9a-f]{32}', entry.name) is not None)
            _private(entry)
            candidate = entry / 'autolog'
            if candidate.exists() or candidate.is_symlink():
                require(closed(candidate), 'ASSET_AUTOLOG_CLEANUP_UNKNOWN')
        yield
    except BaseException as error:
        first = error
    finally:
        if fd is not None:
            try: os.close(fd)
            except BaseException as error: first = T._keep(first, error)
    if first is not None: raise first


def _sizes(output):
    for name, limit in (('worker.stdout.json', MAX_STDOUT), ('worker.stderr.log', MAX_STDERR)):
        path = output / name
        require(not path.is_symlink() and path.stat().st_size <= limit, 'ASSET_AUTOLOG_OUTPUT_LIMIT')


def _interrupt(child, process_start, first):
    try:
        if child.poll() is None:
            require(process_start is not None and N._process_start(child.pid) == process_start,
                    'ASSET_AUTOLOG_CLEANUP_UNKNOWN')
            os.killpg(child.pid, signal.SIGINT)
        deadline = time.monotonic() + GRACE_SECONDS
        while child.poll() is None and time.monotonic() < deadline:
            try: child.wait(timeout=min(.2, max(.001, deadline - time.monotonic())))
            except subprocess.TimeoutExpired: pass
            except BaseException as error: first = T._keep(first, error)
    except BaseException as error:
        first = T._keep(first, error)
    return first


def run(output, training_token, backup_manifest=None, *, source_sha256):
    """Run once, or cold-reuse a completed receipt; caller owns ML stop/restore."""
    token = A.C.token(training_token)
    require(type(source_sha256) is str and re.fullmatch('[0-9a-f]{64}', source_sha256))
    output = _output(output)
    with _lock(output):
        require(not list(output.iterdir()), 'ASSET_AUTOLOG_ALREADY_ATTEMPTED')
        snapshot = A.Snapshot(token)
        source = snapshot.load()
        require(A.C.digest(source) == source_sha256, 'ASSET_AUTOLOG_SOURCE_CHANGED')
        old = A.recorded(token)  # Pending/unknown raises before any child allocation.
        backup, backup_file, backup_saved = None, None, None
        if old is None:
            backup_manifest = _backup_name(backup_manifest)
            stopped = R.require_stopped()
            require(stopped['store_id'] == source['binding']['store_id'], 'ASSET_AUTOLOG_SOURCE_CHANGED')
            backup = A.G.validate_backup(backup_manifest, stopped['store_id'])
            backup_file = ROOT / backup_manifest
            backup_saved = _read(backup_file, 2 * 1024 * 1024)
            require(_sha(backup_saved[0]) == backup['sha256'], 'ASSET_AUTOLOG_BACKUP_CHANGED')
        intent = dict(schema_version=1, profile=PROFILE, training_token=token, source_sha256=source_sha256,
                      backup=backup, argv=None if old is not None else arguments(token, backup_manifest), reused=old is not None)
        snapshot.check()
        _save(output / 'intent.json', intent)
        value = dict(schema_version=1, state='pending', training_token=token, source_sha256=source_sha256,
                     intent_sha256=_sha(N._canonical(intent)), pid=None, pgid=None, process_start=None,
                     exit_code=None, child_exited=False, group_empty=False, cleanup_verified=False,
                     reused=old is not None, result_sha256=None, error_code=None)
        child, descriptors, first, public = None, [], None, None
        try:
            _save(output / 'status.json', value, replace=True)
            snapshot.check()
            if old is None:
                require(_read(backup_file, 2 * 1024 * 1024) == backup_saved, 'ASSET_AUTOLOG_BACKUP_CHANGED')
                for name in ('worker.stdout.json', 'worker.stderr.log'):
                    descriptors.append(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
                child = subprocess.Popen(intent['argv'], cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=descriptors[0], stderr=descriptors[1], env=R.environment(), start_new_session=True, umask=0o077)
                value.update(state='running', pid=child.pid, pgid=child.pid)
                value['process_start'] = N._process_start(child.pid)
                _save(output / 'status.json', value, replace=True)
                deadline = time.monotonic() + SECONDS
                while True:
                    _sizes(output)
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'ASSET_AUTOLOG_TIMEOUT')
                    try:
                        child.wait(timeout=min(.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
        except BaseException as error:
            first = error
            if child is not None:
                first = _interrupt(child, value['process_start'], first)
        finally:
            for descriptor in reversed(descriptors):
                try: os.close(descriptor)
                except BaseException as error: first = T._keep(first, error)
            try:
                if child is None:
                    value.update(exit_code=0 if old is not None and first is None else 1, child_exited=True, group_empty=True)
                else:
                    code = child.poll()
                    value.update(exit_code=code, child_exited=code is not None)
                    value['group_empty'] = not any(row['pgid'] == child.pid for row in R.processes().values())
                value['cleanup_verified'] = value['child_exited'] and value['group_empty']
                require(value['cleanup_verified'], 'ASSET_AUTOLOG_CLEANUP_UNKNOWN')
                snapshot.check()
                if backup_saved is not None:
                    require(_read(backup_file, 2 * 1024 * 1024) == backup_saved, 'ASSET_AUTOLOG_BACKUP_CHANGED')
                if first is None:
                    require(value['exit_code'] == 0, 'ASSET_AUTOLOG_FAILED')
                    receipt = A.recorded(token)
                    require(receipt is not None, 'ASSET_AUTOLOG_FAILED')
                    observed = A.public(receipt)
                    if old is not None:
                        require(receipt == old, 'ASSET_AUTOLOG_SOURCE_CHANGED')
                    else:
                        _sizes(output)
                        raw, _ = _read(output / 'worker.stdout.json', MAX_STDOUT)
                        require(raw == N._canonical(observed), 'ASSET_AUTOLOG_RESULT_INVALID')
                    public = dict(schema_version=1, profile=PROFILE, result='passed', training_token=token,
                                  source_sha256=source_sha256, reused=old is not None, cleanup_verified=True, autolog=observed)
                    snapshot.check()
                    _save(output / 'result.json', public)
                    value['result_sha256'] = _sha(N._canonical(public))
            except BaseException as error:
                first = T._keep(first, error)
            value.update(state='passed' if first is None else 'unknown' if not value['cleanup_verified'] else
                         'interrupted' if not isinstance(first, Exception) else 'failed',
                         error_code=None if first is None else 'ASSET_AUTOLOG_INTERRUPTED' if not isinstance(first, Exception)
                         else 'ASSET_AUTOLOG_CLEANUP_UNKNOWN' if not value['cleanup_verified'] else 'ASSET_AUTOLOG_FAILED')
            try: _save(output / 'status.json', value, replace=True)
            except BaseException as error: first = T._keep(first, error)
        if first is not None:
            if not isinstance(first, Exception): raise first
            raise WorkerError(value['error_code'] or 'ASSET_AUTOLOG_FAILED') from None
        return public
