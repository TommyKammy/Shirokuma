#!/usr/bin/env python3
"""Observe historical Data Plan lineage without changing catalog or source files.

The denominator is fixed by verified source artifacts at the start. Unknown
observations never become missing edges or disappear from that denominator.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote
import uuid

LOCAL = Path(__file__).resolve().parents[1]
ROOT = LOCAL.parents[1]
sys.path.insert(0, str(LOCAL))
from metadata import curate, data_plan_evidence as evidence
from metadata.client import APIError, NoRedirect, URL

SOURCE_ROOT = ROOT / 'tmp/local-experiment/data-plans'
STATE = ROOT / 'tmp/local-experiment/metadata'
OUTPUT_ROOT = ROOT / 'tmp/local-experiment/lineage-coverage'
TOKEN = re.compile(r'[0-9a-f]{32}')
MAX_RUNS, MAX_ENTRIES, MAX_FILES = 64, 128, 512
MAX_FILE_BYTES, MAX_RUN_BYTES, MAX_RESPONSE = 16 * 1024 * 1024, 64 * 1024 * 1024, 2 * 1024 * 1024
STATUSES = ('present', 'missing', 'conflict', 'unknown')
LIMITS = [
    'Only verified historical Data Plan runs define the expected lineage domain; this is not whole-catalog coverage.',
    'Generated SQL tables were removed by verified cleanup. Historical catalog entities do not imply live tables.',
    'Eligible runs are fixed at the first artifact verification. Later drift preserves their two expected edges as unknown.',
    'A run-level catalog ownership, schema or lineage conflict conservatively marks both expected edges as conflict.',
    'Unknown observations are not missing. Coverage rate is unavailable while any expected edge is unknown.',
    'Two bounded observations detect observed changes, not changes that occur and revert between reads.',
    'Artifacts establish local consistency, not cryptographic provenance against an actor replacing all evidence.',
]


class CoverageError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise CoverageError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'INVALID_JSON')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(CoverageError('INVALID_JSON')))
    except (ValueError, UnicodeError, RecursionError):
        raise CoverageError('INVALID_JSON') from None


def directory(path, *, private=True):
    # Do not silently traverse a substituted parent of a fixed host path.
    if path.is_relative_to(ROOT):
        for parent in (path, *path.parents):
            require(not parent.is_symlink(), 'SOURCE_BOUNDARY_INVALID')
            if parent == ROOT:
                break
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid == os.getuid(),
            'SOURCE_BOUNDARY_INVALID')
    require(not private or stat.S_IMODE(info.st_mode) == 0o700, 'SOURCE_PRIVACY_INVALID')
    return info


def read_file(path, limit=MAX_FILE_BYTES):
    path = Path(path)
    parent_fd = _open_source_dir(path.parent)
    try:
        raw = _source_file_at(parent_fd, path.name, os.fstat(parent_fd).st_dev, limit)
        _current_source_path(path.parent, parent_fd)
        return raw
    finally:
        os.close(parent_fd)


SOURCE_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
SOURCE_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _current_source_path(path, fd):
    opened = os.fstat(fd)
    for current in (path, *path.parents):
        info = current.lstat()
        require(stat.S_ISDIR(info.st_mode), 'SOURCE_BOUNDARY_INVALID')
        if current == path:
            require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino), 'SOURCE_CHANGED')


def _open_source_dir(path, *, private=True):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'SOURCE_BOUNDARY_INVALID')
    # Open every component from /, rather than checking ancestors and later
    # opening the full pathname through a substituted symlink.
    fd = os.open('/', SOURCE_DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, SOURCE_DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
                (not private or stat.S_IMODE(info.st_mode) == 0o700), 'SOURCE_PRIVACY_INVALID')
        _current_source_path(path, fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _source_file_at(directory_fd, name, device, limit, *, expected=None):
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and
            stat.S_IMODE(before.st_mode) == 0o600 and before.st_dev == device and
            before.st_size <= limit and before.st_nlink == 1, 'SOURCE_FILE_INVALID')
    if expected is not None:
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns, expected.st_ctime_ns),
                'SOURCE_CHANGED')
    fd = os.open(name, SOURCE_FILE_FLAGS, dir_fd=directory_fd)
    try:
        opened = os.fstat(fd)
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino) and
                stat.S_ISREG(opened.st_mode), 'SOURCE_CHANGED')
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            require(size <= limit, 'SOURCE_LIMIT_EXCEEDED')
        raw = b''.join(chunks)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        require(len(raw) <= limit and
                (before.st_dev, before.st_ino, before.st_mode, before.st_uid,
                 before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) ==
                (after.st_dev, after.st_ino, after.st_mode, after.st_uid,
                 after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink) and
                (after.st_dev, after.st_ino) == (current.st_dev, current.st_ino) and
                stat.S_ISREG(current.st_mode) and current.st_uid == before.st_uid and
                after.st_nlink == current.st_nlink == 1 and
                stat.S_IMODE(current.st_mode) == 0o600 and
                (current.st_mode, current.st_size, current.st_mtime_ns, current.st_ctime_ns) ==
                (after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'SOURCE_CHANGED')
        return raw
    finally:
        os.close(fd)


def _bounded_names(fd, limit, code):
    names = []
    with os.scandir(fd) as entries:
        for entry in entries:
            names.append(entry.name)
            require(len(names) <= limit, code)
    return sorted(names)


def _source_directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def source_snapshot(path):
    """Hash every retained file, including artifacts not consumed by the verifier."""
    path = Path(path)
    root_fd = _open_source_dir(path)
    initial = os.fstat(root_fd)
    files, total, nodes = {}, 0, 0

    def visit(fd, relative, depth, device):
        nonlocal total, nodes
        require(depth <= 20, 'SOURCE_LIMIT_EXCEEDED')
        # Include the opened directory identity, including empty directories:
        # replacing one between the two source reads must invalidate stability.
        opened_dir = os.fstat(fd)
        files[relative + '/'] = {
            'directory': True, 'device': opened_dir.st_dev, 'inode': opened_dir.st_ino,
            'mode': stat.S_IMODE(opened_dir.st_mode), 'owner': opened_dir.st_uid,
            'size': opened_dir.st_size, 'mtime_ns': opened_dir.st_mtime_ns,
            'ctime_ns': opened_dir.st_ctime_ns, 'links': opened_dir.st_nlink,
        }
        names = _bounded_names(fd, 1024, 'SOURCE_LIMIT_OR_SYMLINK')
        identities = {}
        for name in names:
            nodes += 1
            require(nodes <= 1024, 'SOURCE_LIMIT_OR_SYMLINK')
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            identities[name] = (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                                info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
            require(info.st_dev == device, 'SOURCE_BOUNDARY_INVALID')
            item = str(Path(relative) / name)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, SOURCE_DIR_FLAGS, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino) and
                            opened.st_uid == os.getuid() and stat.S_IMODE(opened.st_mode) == 0o700,
                            'SOURCE_CHANGED')
                    visit(child, item, depth + 1, device)
                    current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino),
                            'SOURCE_CHANGED')
                    final = os.fstat(child)
                    require(_source_directory_identity(info) == _source_directory_identity(opened) ==
                            _source_directory_identity(final) == _source_directory_identity(current) and
                            stat.S_ISDIR(current.st_mode) and current.st_uid == os.getuid() and
                            stat.S_IMODE(current.st_mode) == 0o700,
                            'SOURCE_CHANGED')
                finally:
                    os.close(child)
            else:
                require(sum('sha256' in entry for entry in files.values()) < MAX_FILES, 'SOURCE_LIMIT_EXCEEDED')
                raw = _source_file_at(fd, name, device, MAX_FILE_BYTES, expected=info)
                total += len(raw)
                require(total <= MAX_RUN_BYTES, 'SOURCE_LIMIT_EXCEEDED')
                files[item] = {
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                    'device': info.st_dev, 'inode': info.st_ino,
                    'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
                    'mode': stat.S_IMODE(info.st_mode), 'owner': info.st_uid,
                    'links': info.st_nlink}
        # A new or removed sibling after enumeration would otherwise yield a
        # plausible hash of an incomplete directory. This is not an atomic
        # snapshot against an actor that restores the original entries.
        require(names == _bounded_names(fd, 1024, 'SOURCE_LIMIT_OR_SYMLINK'), 'SOURCE_CHANGED')
        for name in names:
            current = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(identities[name] == (current.st_dev, current.st_ino, current.st_mode,
                                         current.st_uid, current.st_size, current.st_mtime_ns,
                                         current.st_ctime_ns, current.st_nlink), 'SOURCE_CHANGED')
    try:
        visit(root_fd, '.', 0, os.fstat(root_fd).st_dev)
        _current_source_path(path, root_fd)
        final = os.fstat(root_fd)
        require(_source_directory_identity(final) == _source_directory_identity(initial) and
                final.st_uid == os.getuid() and stat.S_IMODE(final.st_mode) == 0o700,
                 'SOURCE_CHANGED')
        return digest(files)
    finally:
        os.close(root_fd)


def listing(root):
    root = Path(root)
    fd = _open_source_dir(root, private=False)  # Collection root is 0755; runs are private.
    try:
        def identity(name):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)

        initial = os.fstat(fd)
        names = _bounded_names(fd, MAX_ENTRIES, 'SOURCE_INVENTORY_LIMIT')
        # Preserve identity even when an entry is replaced under the same name.
        # Do not follow a symlink here: invalid entries remain visible to the
        # later per-run validator rather than vanishing from the inventory.
        identities = {name: identity(name) for name in names}
        # A newly created/removed run during enumeration cannot be included in
        # a supposedly complete denominator. This does not make the read atomic.
        require(names == _bounded_names(fd, MAX_ENTRIES, 'SOURCE_INVENTORY_LIMIT'),
                'SOURCE_INVENTORY_CHANGED')
        require(all(identities[name] == identity(name) for name in names),
                'SOURCE_INVENTORY_CHANGED')
        _current_source_path(root, fd)
        final = os.fstat(fd)
        current = root.lstat()
        require(_source_directory_identity(final) == _source_directory_identity(initial) and
                final.st_uid == current.st_uid and
                stat.S_IMODE(final.st_mode) == stat.S_IMODE(initial.st_mode) == stat.S_IMODE(current.st_mode),
                'SOURCE_CHANGED')
        return names
    finally:
        os.close(fd)


def _entity_path(collection, name, owners=False):
    return f'/{collection}/name/{quote(name, safe="")}?include=all' + ('&fields=owners' if owners else '')


ANCHORS = {
    _entity_path('teams', curate.TEAM),
    _entity_path('services/databaseServices', curate.SERVICE),
    _entity_path('databases', curate.DATABASE),
    *(_entity_path('databaseSchemas', name) for name in (curate.SCHEMA, curate.SOURCE_SCHEMA)),
    _entity_path('tables', curate.SOURCE, True),
}


def _safe_path(path):
    return type(path) is str and (path in ANCHORS or re.fullmatch(
        r'/tables/name/shirokuma_local\.iceberg\.local_experiment\.dataops_[0-9a-f]{32}_(stg|mart)\?include=all&fields=owners', path)
        or re.fullmatch(r'/lineage/table/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\?upstreamDepth=1&downstreamDepth=1', path))


class ReadOnlyClient:
    """One login using existing private credentials, followed only by fixed GETs.

    Does not inherit Client.authenticate: no password fallback/change or marker
    touch. Proxy environment and redirects cannot select another destination.
    """
    def __init__(self, *, opener=None, state=STATE, timeout=300):
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.state, self.token = state, None
        self.deadline = time.monotonic() + timeout
        self.calls, self.login_attempted, self.login_error = 0, False, None

    def _request(self, method, path, data=None):
        require(self.calls < MAX_RUNS * 20 + 1, 'API_REQUEST_LIMIT')
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'API_DEADLINE')
        self.calls += 1
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(URL + path, method=method, headers=headers,
                                         data=canonical(data).encode() if data is not None else None)
        try:
            with self.opener.open(request, timeout=min(10, remaining)) as response:
                require(response.status == 200, 'API_RESPONSE_INVALID')
                chunks, count = [], 0
                while True:
                    require(time.monotonic() < self.deadline, 'API_DEADLINE')
                    chunk = response.read(min(65536, MAX_RESPONSE + 1 - count))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                    require(count <= MAX_RESPONSE, 'API_RESPONSE_TOO_LARGE')
                return decode(b''.join(chunks))
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise APIError(method, path, status) from None
        except (OSError, RuntimeError):
            raise CoverageError('API_UNAVAILABLE') from None

    def login_existing(self):
        require(not self.login_attempted, 'LOGIN_ALREADY_ATTEMPTED')
        self.login_attempted = True
        try:
            directory(self.state)
            raw = read_file(self.state / '.env', 4096)
            values = {}
            for line in raw.decode('utf-8').splitlines():
                key, sep, value = line.partition('=')
                require(sep and key not in values and value and len(value) <= 1024, 'CREDENTIALS_INVALID')
                values[key] = value
            require(set(values) == {'OM_STATE', 'OM_DB_PASSWORD', 'OM_POSTGRES_PASSWORD', 'OM_ADMIN_PASSWORD', 'OM_FERNET_KEY'}
                    and values['OM_STATE'] == str(self.state), 'CREDENTIALS_INVALID')
            password = values['OM_ADMIN_PASSWORD']
            require(16 <= len(password) <= 256 and all(33 <= ord(char) <= 126 for char in password), 'CREDENTIALS_INVALID')
            result = self._request('POST', '/users/login', {'email': 'admin@open-metadata.org',
                                   'password': base64.b64encode(password.encode()).decode()})
            token = result.get('accessToken') if type(result) is dict else None
            require(type(token) is str and 1 <= len(token) <= 8192 and re.fullmatch(r'[A-Za-z0-9_.~+/=-]+', token),
                    'LOGIN_RESPONSE_INVALID')
            require(read_file(self.state / '.env', 4096) == raw, 'CREDENTIALS_CHANGED')
            self.token = token
        except Exception:
            self.login_error = 'LOGIN_UNAVAILABLE'
            raise CoverageError(self.login_error) from None

    def request(self, method, path, data=None):
        require(method == 'GET' and data is None and _safe_path(path), 'READ_ONLY_BOUNDARY')
        if not self.login_attempted:
            self.login_existing()
        require(self.login_error is None and self.token is not None, 'LOGIN_UNAVAILABLE')
        return self._request(method, path)


class Observation:
    """Scope even injected clients to the validated run and retain hashes only."""
    def __init__(self, client, projection):
        self.client, self.hashes, self.ids = client, {}, set()
        self.allowed = ANCHORS | {_entity_path('tables', item['name'], True) for item in projection['entities']}

    def request(self, method, path, data=None):
        lineage = {f'/lineage/table/{identity}?upstreamDepth=1&downstreamDepth=1' for identity in self.ids}
        require(method == 'GET' and data is None and path in self.allowed | lineage, 'READ_ONLY_BOUNDARY')
        try:
            value = self.client.request(method, path)
        except APIError as error:
            self.hashes[path] = digest({'http_status': error.status})
            raise
        require(type(value) is dict, 'CATALOG_RESPONSE_INVALID')
        self.hashes[path] = digest(value)
        if '/name/' in path:
            try:
                require(type(value.get('id')) is str and str(uuid.UUID(value['id'])) == value['id']
                        and type(value.get('name')) is str and type(value.get('fullyQualifiedName')) is str,
                        'CATALOG_RESPONSE_INVALID')
                curate._revision(value)
            except (ValueError, KeyError, AttributeError):
                raise CoverageError('CATALOG_RESPONSE_INVALID') from None
            if path not in ANCHORS:
                self.ids.add(value['id'])
        else:
            # Curator's optional-field convenience must not turn a malformed
            # response (false, {}, empty text) into evidence of edge absence.
            require(all(type(value.get(key)) is list for key in ('upstreamEdges', 'downstreamEdges')),
                    'CATALOG_RESPONSE_INVALID')
            entity = value.get('entity')
            require(type(entity) is dict and type(entity.get('id')) is str
                    and type(entity.get('fullyQualifiedName')) is str and entity.get('type') == 'table',
                    'CATALOG_RESPONSE_INVALID')
        return copy.deepcopy(value)


CONFLICTS = {
    'catalog entity identity or deletion state differs', 'catalog parent reference differs',
    'catalog owner differs', 'catalog column schema differs', 'generated catalog column was edited',
    'generated catalog description or evidence marker differs', 'generated catalog has conflicting lineage',
    'generated catalog lineage was edited or has another source', 'catalog owner is not the existing experiment group',
    'catalog service is not the existing Trino service', 'catalog identities are not distinct',
    'catalog lineage exceeds the generated scope',
}


def observe(client, projection):
    observation = Observation(client, projection)
    try:
        state = curate.preflight(observation, projection)
        statuses = {key: ('present' if state['targets'][key]['lineage'] else 'missing') for key in curate.TARGETS}
        reasons = {key: ('VERIFIED_PRESENT' if statuses[key] == 'present' else 'VERIFIED_ABSENT') for key in curate.TARGETS}
    except curate.CurateError as error:
        conflict = str(error) in CONFLICTS
        statuses = dict.fromkeys(curate.TARGETS, 'conflict' if conflict else 'unknown')
        reasons = dict.fromkeys(curate.TARGETS, 'CATALOG_CONFLICT' if conflict else 'CATALOG_RESPONSE_INVALID')
    except Exception:
        statuses = dict.fromkeys(curate.TARGETS, 'unknown')
        reasons = dict.fromkeys(curate.TARGETS, 'CATALOG_UNAVAILABLE')
    return {'statuses': statuses, 'reasons': reasons, 'hashes': observation.hashes}


def _source(path, reader):
    status, code, projection, sha = 'invalid', 'SOURCE_INVALID', None, None
    try:
        fd = _open_source_dir(path)
        try:
            sha = source_snapshot(path)
            _current_source_path(path, fd)
            if 'summary.json' not in _bounded_names(fd, 1024, 'SOURCE_LIMIT_EXCEEDED'):
                _current_source_path(path, fd)
                return 'incomplete', 'SUMMARY_MISSING', None, sha
            summary = decode(_source_file_at(fd, 'summary.json', os.fstat(fd).st_dev, MAX_FILE_BYTES))
            require(type(summary) is dict and summary.get('run_token') == path.name, 'SOURCE_IDENTITY_INVALID')
            require(type(summary.get('result')) is str, 'SOURCE_RESULT_INVALID')
            if summary.get('result') in {'failed', 'interrupted'}:
                _current_source_path(path, fd)
                return 'failed', 'RECORDED_RUN_FAILED', None, sha
            if summary.get('result') != 'passed':
                _current_source_path(path, fd)
                return 'incomplete', 'RUN_NOT_COMPLETE', None, sha
            projection = evidence.validate_projection(reader(path))
            _current_source_path(path, fd)
            require(projection['run_token'] == path.name, 'SOURCE_IDENTITY_INVALID')
            require(source_snapshot(path) == sha, 'SOURCE_CHANGED')
            _current_source_path(path, fd)
            status, code = 'valid', 'SOURCE_VERIFIED'
        finally:
            os.close(fd)
    except CoverageError as error:
        code = error.code
        if code == 'SOURCE_CHANGED':
            status = 'incomplete'
    except evidence.EvidenceError:
        code = 'ARTIFACT_VERIFICATION_FAILED'
    except OSError:
        status, code = 'incomplete', 'SOURCE_UNAVAILABLE'
    return status, code, projection if status == 'valid' else None, sha


def collect(client, *, source_root=SOURCE_ROOT, evidence_reader=evidence.read_evidence):
    """Bounded read-only collection. Optional dependencies are for offline tests."""
    started = now()
    report = {'schema_version': 1, 'observed_at': started, 'scope': 'verified-historical-data-plan-lineage',
              'observation': {'status': 'incomplete', 'source_stable': False, 'metadata_stable': None, 'reason_codes': []},
              'inventory': {'entries': 0, 'uuid_runs': 0, 'eligible': 0, 'valid': 0, 'failed': 0,
                            'incomplete': 0, 'invalid': 0, 'unrecognized_entries': 0, 'complete': False},
              'coverage': {}, 'runs': [], 'limits': list(LIMITS)}
    projections, initial, names = {}, {}, None
    try:
        names = listing(source_root)
        tokens = [name for name in names if TOKEN.fullmatch(name)]
        report['inventory'].update(entries=len(names), uuid_runs=len(tokens), unrecognized_entries=len(names) - len(tokens), complete=True)
        if len(names) != len(tokens):
            report['observation']['reason_codes'].append('UNRECOGNIZED_SOURCE_ENTRIES')
        for token in tokens:
            path = source_root / token
            if len(tokens) > MAX_RUNS:
                status, code, projection, sha = 'incomplete', 'SOURCE_RUN_LIMIT', None, None
            else:
                status, code, projection, sha = _source(path, evidence_reader)
            row = {'run_token': token, 'operation': projection['operation'] if projection else None,
                   'source_status': status, 'eligible': status == 'valid', 'reason_codes': [code],
                   'source_sha256_before': sha, 'source_sha256_after': None,
                   'metadata_sha256_before': None, 'metadata_sha256_after': None, 'edges': []}
            if projection:
                projections[token] = projection
                row['edges'] = [{'target': target, **edge, 'status': 'unknown', 'reason_code': 'NOT_OBSERVED'}
                                for target, edge in zip(curate.TARGETS, projection['lineage'])]
            report['runs'].append(row)
        # All first observations precede all final observations: shared anchor
        # revisions changed during another run are also detected.
        for token, projection in projections.items():
            initial[token] = observe(client, projection)
        stable = []
        for row in report['runs']:
            token = row['run_token']
            if row['eligible']:
                first, final = initial[token], observe(client, projections[token])
                row['metadata_sha256_before'], row['metadata_sha256_after'] = digest(first['hashes']), digest(final['hashes'])
                metadata_same = first == final and all(value != 'unknown' for value in final['statuses'].values())
                stable.append(metadata_same)
                for edge in row['edges']:
                    key = edge['target']
                    edge['status'] = final['statuses'][key] if first == final else 'unknown'
                    edge['reason_code'] = final['reasons'][key] if first == final else 'CATALOG_CHANGED'
            try:
                row['source_sha256_after'] = source_snapshot(source_root / token)
            except (CoverageError, OSError):
                pass
            if row['source_sha256_before'] is not None and row['source_sha256_before'] != row['source_sha256_after']:
                row['source_status'] = 'incomplete'
                row['reason_codes'].append('SOURCE_CHANGED_OR_UNVERIFIABLE')
                for edge in row['edges']:
                    edge.update(status='unknown', reason_code='SOURCE_CHANGED')
        report['observation']['metadata_stable'] = all(stable) if stable else None
        final_names = listing(source_root)
        inventory_same = names == final_names
        report['observation']['source_stable'] = inventory_same and all(
            row['source_sha256_before'] is not None and row['source_sha256_before'] == row['source_sha256_after'] for row in report['runs'])
        if not inventory_same:
            report['inventory']['complete'] = False
            report['observation']['reason_codes'].append('SOURCE_INVENTORY_CHANGED')
    except (CoverageError, OSError):
        report['observation']['reason_codes'].append('SOURCE_INVENTORY_UNAVAILABLE')
        report['inventory']['complete'] = False
    if names is not None and not report['inventory']['complete']:
        # The first inventory still fixes the denominator, but a changed or
        # unavailable final inventory cannot certify any edge in that domain.
        for row in report['runs']:
            for edge in row['edges']:
                edge.update(status='unknown', reason_code='SOURCE_INVENTORY_CHANGED')
    inv = report['inventory']
    for row in report['runs']:
        inv[row['source_status']] += 1
        inv['eligible'] += int(row['eligible'])
    counts = {status: sum(edge['status'] == status for row in report['runs'] for edge in row['edges']) for status in STATUSES}
    denominator, known = inv['eligible'] * 2, sum(counts[status] for status in STATUSES if status != 'unknown')
    report['coverage'] = {'denominator': denominator, **counts, 'known': known,
                          'rate': counts['present'] / denominator if denominator and not counts['unknown'] else None,
                          'observation_rate': known / denominator if denominator else None,
                          'complete': denominator > 0 and counts['present'] == denominator}
    if inv['invalid'] or inv['incomplete']:
        report['observation']['reason_codes'].append('UNVERIFIED_SOURCE_RUNS')
    if counts['unknown']:
        report['observation']['reason_codes'].append('UNKNOWN_EXPECTED_EDGES')
    if not report['observation']['reason_codes'] and report['observation']['source_stable']:
        report['observation']['status'] = 'complete'
    return report


def write_report(report):
    from metadata.coverage_render import render
    json_body, html_body = canonical(report) + '\n', render(report)
    require(OUTPUT_ROOT.is_absolute() and '..' not in OUTPUT_ROOT.parts, 'OUTPUT_BOUNDARY_INVALID')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = OUTPUT_ROOT.parent
    # The normal checkout path may be absent on a fresh clone. Create only its
    # relative parents from the already opened checkout, never via a pathname.
    if parent.is_relative_to(ROOT):
        parent_info = directory(ROOT, private=False)
        parent_fd = os.open(ROOT, flags)
        components = parent.relative_to(ROOT).parts
    else:
        parent_info = directory(parent, private=False)
        parent_fd = os.open(parent, flags)
        components = ()  # Test-only output roots have an existing parent.
    try:
        require((parent_info.st_dev, parent_info.st_ino) == (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino),
                'OUTPUT_BOUNDARY_INVALID')
        for component in components:
            try:
                os.mkdir(component, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            child_fd = os.open(component, flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
            require(os.fstat(parent_fd).st_uid == os.getuid(), 'OUTPUT_BOUNDARY_INVALID')
        current_parent = directory(parent, private=False)
        require((current_parent.st_dev, current_parent.st_ino) == (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino),
                'OUTPUT_BOUNDARY_INVALID')
        try:
            os.mkdir(OUTPUT_ROOT.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        output_root_fd = os.open(OUTPUT_ROOT.name, flags, dir_fd=parent_fd)
        try:
            root_info = directory(OUTPUT_ROOT)
            require((root_info.st_dev, root_info.st_ino) == (os.fstat(output_root_fd).st_dev, os.fstat(output_root_fd).st_ino),
                    'OUTPUT_BOUNDARY_INVALID')
            name = uuid.uuid4().hex
            os.mkdir(name, mode=0o700, dir_fd=output_root_fd)
            run_fd = os.open(name, flags, dir_fd=output_root_fd)
            try:
                run_info = os.fstat(run_fd)
                entry_info = os.stat(name, dir_fd=output_root_fd, follow_symlinks=False)
                require(run_info.st_uid == os.getuid() and stat.S_IMODE(run_info.st_mode) == 0o700 and
                        (run_info.st_dev, run_info.st_ino) == (entry_info.st_dev, entry_info.st_ino),
                        'OUTPUT_BOUNDARY_INVALID')
                for filename, body in (('report.json', json_body), ('report.html', html_body)):
                    fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=run_fd)
                    with os.fdopen(fd, 'w') as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                os.fsync(run_fd)
                os.fsync(output_root_fd)
                os.fsync(parent_fd)
                output = OUTPUT_ROOT / name
                current_root = directory(OUTPUT_ROOT)
                current_run = directory(output)
                require((current_root.st_dev, current_root.st_ino) ==
                        (os.fstat(output_root_fd).st_dev, os.fstat(output_root_fd).st_ino) and
                        (current_run.st_dev, current_run.st_ino) == (run_info.st_dev, run_info.st_ino),
                        'OUTPUT_BOUNDARY_INVALID')
                return output
            finally:
                os.close(run_fd)
        finally:
            os.close(output_root_fd)
    finally:
        os.close(parent_fd)


def main():
    require(len(sys.argv) == 1, 'NO_ARGUMENTS_ALLOWED')
    report = collect(ReadOnlyClient())
    output = write_report(report)
    print(canonical({'observation': report['observation']['status'], 'coverage': report['coverage'],
                     'evidence': str(output.relative_to(ROOT))}))
    # Incomplete observation is an execution limitation. Fully observed missing
    # or conflicting lineage is a valid report, not a collector transport error.
    return 0 if report['observation']['status'] == 'complete' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (CoverageError, OSError):
        print('{"observation":"incomplete","reason":"REPORT_UNAVAILABLE"}')
        raise SystemExit(1)
