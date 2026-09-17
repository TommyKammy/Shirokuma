#!/usr/bin/env python3
"""Describe one retained catalog table through existing read-only metadata APIs."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parents[3]
LOCAL = ROOT / 'tools/local_experiment'
if str(LOCAL) not in sys.path:
    sys.path.insert(0, str(LOCAL))
from catalog_read import contract as C
from metadata import coverage as V, runtime as M

STATE = ROOT / 'tmp/local-experiment/catalog-read'
MAX_SECONDS, HTTP_SECONDS, MAX_FRAME = 180, 120, 96 * 1024
PUBLIC_CODES = C.ERROR_CODES | frozenset({
    'CATALOG_RUNTIME_INVALID', 'CATALOG_RUNTIME_CHANGED', 'CATALOG_STATE_CHANGED',
    'CATALOG_SOURCE_CHANGED', 'CATALOG_READ_FAILED', 'CATALOG_DEADLINE',
    'CATALOG_REQUEST_LIMIT', 'CATALOG_READ_ONLY_BOUNDARY', 'EVIDENCE_ALREADY_EXISTS',
    'EVIDENCE_INVALID', 'INTERRUPTED', 'READ_FAILED', 'ARGUMENTS_INVALID'})
COLLECTIONS = {'team': 'teams', 'service': 'services/databaseServices', 'database': 'databases',
               'schema': 'databaseSchemas', 'table': 'tables'}
PATHS = {key: V._entity_path(collection, C.ANCHOR_NAMES[key], key == 'table')
         for key, collection in COLLECTIONS.items()}


class ReadError(ValueError):
    pass


class Terminated(BaseException):
    signum = 15


class Deadline(BaseException):
    """Bypass the legacy login's Exception wrapper without treating a timeout as SIGINT."""


def interrupted(error):
    return not isinstance(error, (Exception, Deadline))


def require(condition, code):
    if not condition:
        raise ReadError(code)


def retain(first, later):
    return first if first is not None and (interrupted(first) or not interrupted(later)) else later


def error_code(error):
    if isinstance(error, Deadline):
        return 'CATALOG_DEADLINE'
    if interrupted(error):
        return 'INTERRUPTED'
    code = error.code if isinstance(error, C.ContractError) else str(error) if isinstance(error, ReadError) else None
    return code if code in PUBLIC_CODES else 'CATALOG_READ_FAILED'


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def sources():
    paths = [Path(__file__), Path(C.__file__), Path(V.__file__), Path(M.__file__), M.COMPOSE,
             LOCAL / 'metadata/client.py', LOCAL / 'metadata/curate.py',
             LOCAL / 'metadata/data_plan_evidence.py', LOCAL / 'bears/data_contract.py']
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def private_directory(path):
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode) and not path.is_symlink() and value.st_uid == os.getuid()
            and stat.S_IMODE(value.st_mode) == 0o700, 'EVIDENCE_INVALID')


def save(path, value):
    """Atomic private receipt; no raw API responses enter this function."""
    raw = (C.canonical(value) + '\n').encode()
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    original = None
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException as error:
        original = error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except BaseException as error:
            original = retain(original, error)
    if original is not None:
        raise original


def state_identity(state=M.STATE):
    V.directory(state)
    directory = state.stat()
    files = {}
    for name in ('.env', 'admin-initialized', 'private_key.der', 'public_key.der', 'db-init.sql'):
        path = state / name
        before = path.lstat()
        mode = 0o600 if name in ('.env', 'admin-initialized') else 0o444
        require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == mode
                and before.st_uid == os.getuid() and before.st_nlink == 1
                and before.st_size <= 65536, 'CATALOG_STATE_CHANGED')
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(65537)
            info = os.fstat(stream.fileno())
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                                s.st_mode, s.st_uid, s.st_nlink)
        require(len(raw) <= 65536 and signature(before) == signature(opened) == signature(info)
                == signature(path.lstat()), 'CATALOG_STATE_CHANGED')
        files[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'inode': info.st_ino,
                       'mtime_ns': info.st_mtime_ns, 'mode': stat.S_IMODE(info.st_mode)}
    return {'directory_inode': directory.st_ino, 'directory_mtime_ns': directory.st_mtime_ns,
            'entries': sorted(x.name for x in state.iterdir()), 'files': files}


def inspect_runtime(*, check=None, docker=None):
    """Existing context/ARM64 guard, then a narrowly projected fixed server inspect."""
    (check or M.check_runtime)()
    docker = docker or M.docker
    raw = docker('ps', '--all', '--quiet', '--no-trunc', '--filter',
                 'label=com.docker.compose.project=' + M.PROJECT, '--filter',
                 'label=com.docker.compose.service=server', timeout=10)
    ids = raw.decode('ascii').split()
    require(len(ids) == 1 and re.fullmatch(r'[0-9a-f]{64}', ids[0]), 'CATALOG_RUNTIME_INVALID')
    form = ('{"id":{{json .Id}},"image":{{json .Config.Image}},"image_id":{{json .Image}},'
            '"labels":{{json .Config.Labels}},"ports":{{json .NetworkSettings.Ports}},'
            '"state":{{json .State.Status}},"running":{{json .State.Running}}}')
    value = json.loads(docker('inspect', '--format', form, ids[0], timeout=10))
    image = json.loads(M.COMPOSE.read_bytes())['services']['server']['image']
    require(re.fullmatch(r'docker\.getcollate\.io/openmetadata/server@sha256:[0-9a-f]{64}', image),
            'CATALOG_RUNTIME_INVALID')
    require(value['id'] == ids[0] and value['image'] == image
            and re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_id'])
            and value['labels'].get('com.docker.compose.project') == M.PROJECT
            and value['labels'].get('com.docker.compose.service') == 'server'
            and value['state'] == 'running' and value['running'] is True
            and value['ports'].get('8585/tcp') == [{'HostIp': '127.0.0.1', 'HostPort': '18585'}]
            and all(k == '8585/tcp' or v is None for k, v in value['ports'].items()),
            'CATALOG_RUNTIME_INVALID')
    return {'container_id': ids[0], 'image': image, 'image_id': value['image_id'],
            'project': M.PROJECT, 'service': 'server', 'port': '127.0.0.1:18585->8585/tcp'}


class Response:
    """Keep the first cancellation even if a response context fails or suppresses it."""
    def __init__(self, response, owner):
        self.response, self.owner = response, owner

    def __enter__(self):
        try:
            return self.response.__enter__()
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def __exit__(self, kind, error, traceback):
        try:
            self.response.__exit__(kind, error, traceback)
        except BaseException as later:
            self.owner.close_failed = True
            raise retain(error, later)
        self.owner.active -= 1
        return False


class Opener:
    def __init__(self, opener):
        self.opener = opener
        self.active, self.close_failed = 0, False

    def open(self, *args, **kwargs):
        try:
            response = self.opener.open(*args, **kwargs)
        except urllib.error.HTTPError as error:
            # HTTPError owns a response even though open() did not return it.
            self.active += 1
            try:
                error.close()
            except BaseException as later:
                self.close_failed = True
                if interrupted(later) or isinstance(later, Deadline):
                    raise
                raise ReadError('CATALOG_READ_FAILED') from None
            self.active -= 1
            # Do not let the legacy handler close this response a second time.
            raise ReadError('CATALOG_READ_FAILED') from None
        except BaseException:
            # A redirect or interrupted open can hide a response owned by urllib.
            self.close_failed = True
            raise
        self.active += 1
        return Response(response, self)


class Client(V.ReadOnlyClient):
    """One existing login and exactly the registered six paths, at most twice each."""
    def __init__(self, *, progress=None, **kwargs):
        super().__init__(timeout=HTTP_SECONDS, **kwargs)
        self.opener = Opener(self.opener)
        self.progress = progress or (lambda: None)
        self.records, self.path_counts, self.lineage = [], {}, None

    def bind_root(self, table):
        root = C._anchor(table, C.ANCHOR_NAMES['table'])['id']
        path = '/lineage/table/' + root + '?upstreamDepth=1&downstreamDepth=1'
        require(self.lineage is None or self.lineage == path, 'CATALOG_CHANGED')
        self.lineage = path
        return path

    def _request(self, method, path, data=None):
        if time.monotonic() >= self.deadline:
            raise Deadline()
        require(self.calls < 13, 'CATALOG_REQUEST_LIMIT')
        require(all(x['result'] == 'finished' for x in self.records), 'CATALOG_READ_ONLY_BOUNDARY')
        login = method == 'POST' and path == '/users/login' and self.calls == 0 and self.login_attempted
        key = next((name for name, value in PATHS.items() if value == path), None)
        if self.lineage is not None and path == self.lineage:
            key = 'lineage'
        require(login or (method == 'GET' and data is None and key is not None
                          and self.path_counts.get(path, 0) < 2), 'CATALOG_READ_ONLY_BOUNDARY')
        self.path_counts[path] = self.path_counts.get(path, 0) + 1
        record = {'method': method, 'scope': 'login' if login else key,
                  'attempt': self.path_counts[path], 'result': 'pending', 'response_sha256': None}
        self.records.append(record)
        self.progress()  # Durable intent before each possible communication.
        original = None
        result = None
        try:
            result = super()._request(method, path, data)
            if time.monotonic() >= self.deadline:
                raise Deadline()
            record['result'] = 'finished'
            if not login:
                record['response_sha256'] = C.digest(result)
        except BaseException as error:
            original = error
            record['result'] = 'interrupted' if interrupted(error) else 'failed'
        try:
            self.progress()
        except BaseException as error:
            original = retain(original, error)
        if original is not None:
            raise original
        return result

    def request(self, method, path, data=None):
        require(method == 'GET' and data is None and (path in PATHS.values() or
                (self.lineage is not None and path == self.lineage)), 'CATALOG_READ_ONLY_BOUNDARY')
        return super().request(method, path, data)


class Owner:
    def __init__(self, token, request, output, *, client_factory=None, runtime_guard=None,
                 state_guard=None, source_reader=None, writer=None):
        self.token, self.request, self.output = token, C.normalize_request(request), output
        self.client_factory, self.runtime_guard = client_factory or Client, runtime_guard or inspect_runtime
        self.state_guard, self.source_reader, self.writer = state_guard or state_identity, source_reader or sources, writer or save
        self.summary = {'schema_version': 1, 'case': C.PROFILE, 'evidence_token': token,
                        'request': self.request, 'request_sha256': C.digest(self.request),
                        'result': 'running', 'started_at': now(), 'finished_at': None,
                        'source_hashes': self.source_reader(), 'state_before': None, 'state_after': None,
                        'runtime_before': None, 'runtime_after': None, 'snapshots': [], 'reads': [],
                        'observation': None, 'error_code': None, 'cleanup_verified': False,
                        'invocations': 1, 'retries': 0, 'model_calls': 0, 'sql_queries': 0}

    def persist(self):
        self.writer(self.output / 'summary.json', self.summary)

    def execute(self):
        self.persist()
        original, candidate, client = None, None, None
        try:
            self.summary['state_before'] = self.state_guard()
            self.summary['runtime_before'] = self.runtime_guard()
            self.persist()
            client = self.client_factory(progress=self.persist)
            self.summary['reads'] = client.records
            client.login_existing()
            for _ in range(2):
                raw = {key: client.request('GET', path) for key, path in PATHS.items()}
                path = client.bind_root(raw['table'])
                raw['lineage'] = client.request('GET', path)
                self.summary['snapshots'].append(C.project_snapshot(raw))
                del raw
                self.persist()
            candidate = C.project_description(*self.summary['snapshots'], self.request)
            require(client.calls == 13 and len(client.records) == 13
                    and all(x['result'] == 'finished' for x in client.records), 'CATALOG_READ_FAILED')
        except BaseException as error:
            original = error
        finally:
            if client is not None:
                client.token = None  # No authenticated token is retained.
        # No remaining GETs after failure and no additional subprocesses after cancellation/deadline.
        if self.summary['runtime_before'] is not None and isinstance(original, (Exception, type(None))) \
                and not (isinstance(original, ReadError) and str(original) == 'CATALOG_DEADLINE'):
            try:
                self.summary['runtime_after'] = self.runtime_guard()
                self.summary['state_after'] = self.state_guard()
                require(self.summary['runtime_after'] == self.summary['runtime_before'], 'CATALOG_RUNTIME_CHANGED')
                require(self.summary['state_after'] == self.summary['state_before'], 'CATALOG_STATE_CHANGED')
                require(self.source_reader() == self.summary['source_hashes'], 'CATALOG_SOURCE_CHANGED')
                require(client is None or (client.opener.active == 0 and not client.opener.close_failed),
                        'CATALOG_READ_FAILED')
                self.summary['cleanup_verified'] = True
            except BaseException as error:
                original = retain(original, error)
        self.summary['result'] = 'passed' if original is None else ('interrupted' if interrupted(original) else 'failed')
        self.summary['finished_at'] = now()
        self.summary['error_code'] = None if original is None else error_code(original)
        self.summary['observation'] = C.receipt_projection(candidate) if original is None else None
        try:
            self.persist()
        except BaseException as error:
            original = retain(original, error)
        if original is not None:
            raise original
        require(self.summary['cleanup_verified'], 'CATALOG_READ_FAILED')
        return candidate


def event(token, result, observation=None, code=None, cleanup=False):
    return {'schema_version': 1, 'event': 'result', 'command': 'catalog describe', 'evidence_token': token,
            'result': result, 'observation': observation, 'error_code': code,
            'cleanup_verified': cleanup, 'evidence': 'tmp/local-experiment/catalog-read/' + token + '/summary.json'}


def invoke(token, request, emit, *, state=None, **dependencies):
    require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token), 'ARGUMENTS_INVALID')
    request = C.normalize_request(request)
    state = state or STATE
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_directory(state)
    output = state / token
    require(not output.exists() and not output.is_symlink(), 'EVIDENCE_ALREADY_EXISTS')
    output.mkdir(mode=0o700)
    fd = os.open(str(output / 'run.lock'), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    owner, original, final = None, None, None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = Owner(token, request, output, **dependencies)
        emit({'schema_version': 1, 'event': 'allocated', 'command': 'catalog describe', 'evidence_token': token})
        public = owner.execute()
        final = event(token, 'passed', public, None, True)
    except BaseException as error:
        original = error
        final = event(token, 'interrupted' if interrupted(error) else 'failed', None,
                      error_code(error), bool(owner and owner.summary['cleanup_verified']))
    try:
        os.close(fd)
    except BaseException as error:
        original = retain(original, error)
        final = event(token, 'interrupted' if interrupted(original) else 'failed', None, error_code(original))
    try:
        emit(final)
    except BaseException as error:
        original = retain(original, error)
    if original is not None and (interrupted(original) or final['result'] == 'passed'):
        raise original
    return final


@contextmanager
def execution_deadline(seconds=MAX_SECONDS):
    old_alarm, old_term = signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGTERM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    require(old_timer == (0.0, 0.0), 'CATALOG_READ_FAILED')
    def expired(_signum, _frame):
        raise Deadline()
    def terminated(_signum, _frame):
        raise Terminated()
    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGTERM, terminated)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    original = None
    try:
        yield
    except BaseException as error:
        original = error
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_alarm)
            signal.signal(signal.SIGTERM, old_term)
        except BaseException as error:
            original = retain(original, error)
    if original is not None:
        raise original


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ReadError('ARGUMENTS_INVALID')


def main(argv=None, *, stdin=None, stdout=None, invoker=None):
    stream, token, pending = stdout or sys.stdout, '0' * 32, None
    def write(value):
        raw = C.canonical(value) + '\n'
        require(len(raw.encode()) <= MAX_FRAME, 'CATALOG_LIMIT')
        stream.write(raw); stream.flush()
    def emit(value):
        nonlocal pending
        if value['event'] == 'result':
            pending = value
        else:
            write(value)
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('command', choices=['describe'])
        parser.add_argument('--evidence-token', required=True)
        parser.add_argument('--request-stdin', required=True, action='store_true')
        args = parser.parse_args(argv)
        require(re.fullmatch(r'[0-9a-f]{32}', args.evidence_token), 'ARGUMENTS_INVALID')
        token = args.evidence_token
        request = C.parse_request((stdin or sys.stdin.buffer).read(C.MAX_INPUT_BYTES + 1))
        with execution_deadline():
            final = (invoker or invoke)(token, request, emit)
        require(pending == final, 'CATALOG_READ_FAILED')
        write(final)
        return 0 if final['result'] == 'passed' else 1
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code == 0:
            return 0
        final = event(token, 'interrupted' if interrupted(error) else 'failed', None,
                      error_code(error), bool(pending and pending['cleanup_verified']))
        try:
            write(final)
        except BaseException as later:
            error = retain(error, later)
        return 128 + getattr(error, 'signum', 2) if interrupted(error) else 1


if __name__ == '__main__':
    raise SystemExit(main())
