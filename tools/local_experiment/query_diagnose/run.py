#!/usr/bin/env python3
"""Diagnose one completed Reader SELECT without executing it again."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_diagnose import contract as D
from tools.local_experiment.query_read import contract as C, client as Q, run as R
from tools.local_experiment.trino_opa import common as FS
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/query-diagnose'
READ_STATE = ROOT / 'tmp/local-experiment/query-read'
SNAPSHOT_READ_STATE = ROOT / 'tmp/local-experiment/query-snapshots'
FILTER_READ_STATE = ROOT / 'tmp/local-experiment/query-filter'
ERROR_CODES = frozenset('''ARGUMENTS_INVALID REQUEST_INVALID SOURCE_NOT_FOUND SOURCE_BUSY SOURCE_INVALID
SOURCE_CHANGED ENVIRONMENT_NOT_READY OBSERVATION_FAILED MODEL_FAILED MODEL_REJECTED AUDIT_FAILED
DIAGNOSE_FAILED INTERRUPTED EVIDENCE_ALREADY_EXISTS'''.split())
SOURCE_KEYS = frozenset('''catalog cleanup error_code errors evidence_token finished_at identity_after
identity_before identity_unchanged inventory invocations limits observation queries query_drain request
request_sha256 result runtime schema_version source_hashes started_at'''.split())


class DiagnoseError(ValueError):
    pass


def require(value, error='SOURCE_INVALID'):
    if not value:
        raise DiagnoseError(error)


def code(error):
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    return str(error) if type(error) is DiagnoseError and str(error) in ERROR_CODES else 'DIAGNOSE_FAILED'


def token(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value), 'REQUEST_INVALID')
    return value


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def private_directory(path):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700)


def read_private(path, maximum=2*1024*1024):
    require(path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    duplicate, stream, original, result = None, None, None, None
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= maximum)
        duplicate = os.dup(fd)
        stream = os.fdopen(duplicate, 'rb')
        duplicate = None
        raw = stream.read(maximum + 1)
        require(len(raw) == before.st_size and fingerprint(before) == fingerprint(os.fstat(fd)))
        result = raw, fingerprint(before)
    except BaseException as error:
        original = error
    finally:
        releases = [stream.close] if stream is not None else [lambda: os.close(duplicate)] if duplicate is not None else []
        for release in [*releases, lambda: os.close(fd)]:
            try:
                release()
            except BaseException as error:
                original = R.retain(original, error)
    if original is not None:
        raise original
    return result


def validate_transport(read_token, queries, runtime, drain, catalog, request):
    """Shared terminal-query, provenance and read-only catalogue receipt checks."""
    require(type(queries) is dict and set(queries) == {'worker', 'control'}
            and type(queries['worker']) is list and len(queries['worker']) == 4
            and type(queries['control']) is list and 1 <= len(queries['control']) <= 32)
    require([row['label'] for row in queries['worker']] == ['inventory', 'schema_before', 'select', 'schema_after'])
    require(runtime['state'] == 'verified' and runtime['cleanup'] == {'verified': True})
    for component, name in (('trino', 'trino-local'), ('polaris', 'polaris')):
        saved = runtime[component]
        service = saved['service']
        require(saved['state'] == 'verified' and service['name'] == name and service['namespace'] == 'shirokuma-dev'
                and type(service['uid']) is str and re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', service['uid'])
                and type(service['spec_sha256']) is str and re.fullmatch('[0-9a-f]{64}', service['spec_sha256']))
    identities = []
    operation_ids = []
    for role, control in (('worker', False), ('control', True)):
        user, source = Q.identity(read_token, control)
        saved = runtime[role]
        require(saved['user'] == user and saved['source'] == source and saved['records'] == queries[role]
                and saved['cancellations'] == [])
        for row in queries[role]:
            require(row['result'] == 'finished' and row['response_complete'] is True and row['post_attempted'] is True
                    and row['error_code'] is None and row['http_status'] == 200
                    and type(row['pages']) is int and 1 <= row['pages'] <= Q.MAX_PAGES
                    and type(row['response_bytes']) is int and 0 < row['response_bytes'] <= Q.MAX_BYTES
                    and type(row['server_max_run_seconds']) is int and row['server_max_run_seconds'] == 30)
            require(type(row['query_id']) is str and re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}', row['query_id']))
            require(type(row['operation_id']) is str and re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', row['operation_id']))
            identities.append(row['query_id']); operation_ids.append(row['operation_id'])
            if control:
                require(row['label'] == 'drain' and row['sql_sha256'] == hashlib.sha256(Q.inspection_sql(read_token).encode()).hexdigest())
    require(len(identities) == len(set(identities)) and len(operation_ids) == len(set(operation_ids)))
    user, source_name = Q.identity(read_token)
    require(drain['verified'] is True and drain['user'] == user and drain['source'] == source_name
            and all(drain[k] == [] for k in ('active', 'cancelled', 'cancel_errors', 'errors'))
            and sorted(drain['observed_query_ids']) == sorted(row['query_id'] for row in queries['worker']))
    require(catalog == runtime['catalog'] and catalog['authenticated'] is True
            and catalog['authentication_attempted'] is True and len(catalog['reads']) == 2
            and catalog['reads'][0] == catalog['reads'][1] and catalog['reads'][0]['result'] == 'verified')
    for read in catalog['reads']:
        require(set(read) == {'result', 'request_sha256', 'metadata_sha256'}
                and read['request_sha256'] == C.digest(request)
                and type(read['metadata_sha256']) is str and re.fullmatch('[0-9a-f]{64}', read['metadata_sha256']))


def validate_source(read_token, value, digest):
    require(type(value) is dict and set(value) == SOURCE_KEYS)
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['evidence_token'] == read_token and value['result'] == 'passed'
            and type(value['invocations']) is int and value['invocations'] == 1
            and value['errors'] == [] and value['error_code'] is None)
    start, end = (datetime.fromisoformat(value[k]) for k in ('started_at', 'finished_at'))
    require(start.tzinfo is not None and end.tzinfo is not None and end >= start)
    request = C.normalize_request(value['request'])
    require(request['command'] == 'select' and value['request_sha256'] == C.digest(request))
    C.verify_identity(value['identity_before'], value['identity_after'])
    require(value['identity_unchanged'] is True and value['cleanup'] == {'verified': True})
    hashes = value['source_hashes']
    # Historical source digests remain provenance; legitimate code evolution
    # does not turn an immutable, completed read into a fresh execution.
    require(type(hashes) is dict and set(hashes) == set(R.sources()) and all(
        type(h) is str and re.fullmatch('[0-9a-f]{64}', h) for h in hashes.values()))
    queries, runtime, drain = value['queries'], value['runtime'], value['query_drain']
    validate_transport(read_token, queries, runtime, drain, value['catalog'], request)
    selected = queries['worker'][2]
    source = D.source_context(read_token, request, value['inventory'], value['identity_after'],
                              value['observation'], selected['query_id'], digest)
    expected_sql = [C.discovery_sql(request), C.schema_sql(request),
                    C.select_sql(request, value['inventory'], value['identity_after']['columns'], value['identity_after']),
                    C.schema_sql(request)]
    require(all(row['sql_sha256'] == hashlib.sha256(sql.encode()).hexdigest()
                for row, sql in zip(queries['worker'], expected_sql)))
    require(selected['row_count'] == value['observation']['fetched_row_count'])
    return source


class SourceLease:
    def __init__(self, read_token):
        self.token = token(read_token)
        self.folder, self.fd, self.source = READ_STATE / read_token, None, None
        self.raw = self.stat = None

    def locate(self):
        candidates = []
        for base in (READ_STATE, SNAPSHOT_READ_STATE, FILTER_READ_STATE):
            try:
                base.lstat()
            except FileNotFoundError:
                continue
            private_directory(base)
            folder = base / self.token
            try:
                folder.lstat()
            except FileNotFoundError:
                continue
            candidates.append((base, folder))
        require(len(candidates) <= 1)
        require(candidates, 'SOURCE_NOT_FOUND')
        return candidates[0]

    def __enter__(self):
        try:
            base, self.folder = self.locate()
            private_directory(self.folder)
            self.fd = os.open(self.folder / 'run.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(self.fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DiagnoseError('SOURCE_BUSY') from None
            self.raw, self.stat = read_private(self.folder / 'summary.json')
            value, digest = Q.decode(self.raw), hashlib.sha256(self.raw).hexdigest()
            if base == READ_STATE:
                self.source = validate_source(self.token, value, digest)
            else:
                from tools.local_experiment.query_diagnose.snapshot_source import validate
                self.source = validate(self.token, value, digest, validate_transport,
                                       filtered=base == FILTER_READ_STATE)
            return self
        except BaseException as error:
            try:
                self.__exit__(None, None, None)
            except BaseException as other:
                error = R.retain(error, other)
            if not isinstance(error, Exception) or isinstance(error, DiagnoseError):
                raise error
            raise DiagnoseError('SOURCE_NOT_FOUND' if isinstance(error, FileNotFoundError) else 'SOURCE_INVALID') from None

    def unchanged(self):
        try:
            require(self.locate()[1] == self.folder, 'SOURCE_CHANGED')
            raw, info = read_private(self.folder / 'summary.json')
            require(raw == self.raw and info == self.stat, 'SOURCE_CHANGED')
        except Exception:
            raise DiagnoseError('SOURCE_CHANGED') from None

    def __exit__(self, *_):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            error = None
            for release in (lambda: fcntl.flock(fd, fcntl.LOCK_UN), lambda: os.close(fd)):
                try:
                    release()
                except BaseException as other:
                    error = R.retain(error, other)
            if error is not None:
                raise error


def environment():
    sys.path.insert(0, str(ROOT / 'tools/local_experiment/llm'))
    from providers import load_config, validate_config
    from tools.local_experiment.query_diagnose.provider import DiagnoseProvider
    path = ROOT / 'tools/local_experiment/llm/providers.qwen-local.json'
    config = load_config(path)
    validate_config(config)
    require(config['active_provider'] == 'local', 'ENVIRONMENT_NOT_READY')
    profile = config['providers']['local']
    return profile, DiagnoseProvider(profile)


def sources():
    paths = [*Path(__file__).parent.glob('*.py')]
    result = R.sources()
    from tools.local_experiment.query_snapshots.run import sources as snapshot_sources
    result.update(snapshot_sources())
    from tools.local_experiment.query_filter.run import sources as filter_sources
    result.update(filter_sources())
    for name in ('providers.py', 'evaluation.py', 'workflow.py'):
        paths.append(ROOT / 'tools/local_experiment/llm' / name)
    for name in ('run_sre.py', 'run_sre_llm.py', 'run_catalog.py'):
        paths.append(ROOT / 'tools/local_experiment/bears' / name)
    for path in sorted(paths):
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class Owner:
    def __init__(self, token_value, read_token, output):
        self.token, self.read_token, self.output = token(token_value), token(read_token), output
        require(self.token != self.read_token, 'REQUEST_INVALID')
        self.report = {'schema_version': 1, 'evidence_token': self.token, 'read_token': self.read_token,
            'started_at': R.stamp(), 'finished_at': None, 'result': 'running', 'error_code': None,
            'source': None, 'observation': None, 'model_evidence': None, 'diagnosis': None,
            'model_calls': 0, 'invocations': 0, 'original_select_reexecuted': False, 'cloud_calls': 0, 'retries': 0,
            'audit': {'verified': False}, 'cleanup_verified': False, 'source_hashes': sources(),
            'limits': ['LOCAL_EXPERIMENT_ONLY', 'HISTORICAL_QUERY_NOT_REPLAYED', 'OBSERVATION_WINDOW_BEFORE_INFERENCE',
                       'NO_RAW_SQL_ROWS_OR_ENTITY_NAMES_TO_MODEL', 'NO_ROOT_CAUSE_OR_PERFORMANCE_CLAIM']}

    def save(self):
        FS.write(self.output / 'summary.json', self.report); R.sync(self.output)

    def observation_progress(self, value):
        self.report['observation'] = deepcopy(value)
        self.report['cleanup_verified'] = value.get('cleanup_verified') is True
        self.save()

    def audit_progress(self, value):
        self.report['audit'] = deepcopy(value); self.save()

    def execute(self, *, lease_factory=SourceLease, environment_factory=environment, observer=None, audit_factory=None):
        require(self.report['invocations'] == 0, 'DIAGNOSE_FAILED')
        self.report['invocations'] = 1
        original, lease, entered, audit, public = None, None, False, None, None
        try:
            self.save()
            lease = lease_factory(self.read_token); lease.__enter__(); entered = True
            source = lease.source
            self.report['source'] = deepcopy(source); self.save()
            try:
                profile, model = environment_factory()
            except Exception:
                raise DiagnoseError('ENVIRONMENT_NOT_READY') from None
            if observer is None:
                from tools.local_experiment.query_diagnose.observe import observe
                observer = observe
            if audit_factory is None:
                from tools.local_experiment.query_diagnose.audit import Audit
                audit_factory = Audit
            try:
                audit = audit_factory(self.token, self.output, profile, self.audit_progress)
                audit.start()
            except Exception:
                raise DiagnoseError('AUDIT_FAILED') from None

            def collect():
                try:
                    value = observer(source, self.token, self.observation_progress)
                    require(value['state'] == 'verified' and value['cleanup_verified'] is True, 'OBSERVATION_FAILED')
                    self.observation_progress(value); lease.unchanged()
                    return value
                except DiagnoseError:
                    raise
                except Exception:
                    raise DiagnoseError('OBSERVATION_FAILED') from None

            try:
                value = audit.phase('bear.query_diagnose.observe', collect)
            except DiagnoseError:
                raise
            except Exception:
                raise DiagnoseError('AUDIT_FAILED') from None
            evidence = D.build_evidence(source, value['plan'], value['history'], value['stats'])
            self.report['model_evidence'] = evidence; self.save()
            lease.unchanged()

            def infer():
                require(self.report['model_calls'] == 0, 'DIAGNOSE_FAILED')
                self.report['model_calls'] = 1; self.save()  # Durable intent precedes the only model call.
                return model.complete('query_diagnose', evidence)

            def validate(response):
                try:
                    return D.validate_diagnosis(response['content'], evidence)
                except Exception:
                    raise DiagnoseError('MODEL_REJECTED') from None

            try:
                diagnosis = audit.phase('llm.query_diagnose', infer, inference=True, validate=validate)
            except DiagnoseError:
                raise
            except Exception as error:
                from tools.local_experiment.query_diagnose.audit import AuditError
                raise DiagnoseError('AUDIT_FAILED' if isinstance(error, AuditError) else 'MODEL_FAILED') from None
            self.report['diagnosis'] = diagnosis; self.save()
            lease.unchanged()
            require(self.report['source_hashes'] == sources(), 'SOURCE_CHANGED')
            try:
                audit.finish()
            except Exception:
                raise DiagnoseError('AUDIT_FAILED') from None
            public = D.public_summary(source, evidence, diagnosis)
        except BaseException as error:
            original = error
        finally:
            if audit is not None:
                try:
                    audit.close(original)
                except BaseException as error:
                    original = R.retain(original, error if not isinstance(error, Exception) else DiagnoseError('AUDIT_FAILED'))
            if entered:
                try:
                    lease.unchanged(); lease.__exit__(None, None, None)
                except BaseException as error:
                    original = R.retain(original, error)
                    try:
                        lease.__exit__(None, None, None)
                    except BaseException as other:
                        original = R.retain(original, other)
            try:
                require(self.report['source_hashes'] == sources(), 'SOURCE_CHANGED')
                if original is None:
                    require(self.report['audit'].get('verified') is True and self.report['cleanup_verified'], 'AUDIT_FAILED')
            except BaseException as error:
                original = R.retain(original, error)
            passed = original is None and public is not None
            self.report.update(result='passed' if passed else 'interrupted' if original is not None and not isinstance(original, Exception) else 'failed',
                               error_code=None if passed else code(original), finished_at=R.stamp())
            try:
                self.save()
            except BaseException as error:
                original = R.retain(original, error)
                self.report.update(result='interrupted' if not isinstance(original, Exception) else 'failed', error_code=code(original))
                try:
                    self.save()
                except BaseException as other:
                    original = R.retain(original, other)
                passed = False
        if original is not None and not isinstance(original, Exception):
            raise original
        return self.report, public if passed else None


def event(identity, result, observation, error, cleanup):
    value = {'schema_version': 1, 'event': 'result', 'command': 'query diagnose', 'evidence_token': identity,
        'result': result, 'observation': observation, 'error_code': error, 'cleanup_verified': cleanup,
        'evidence': f'tmp/local-experiment/query-diagnose/{identity}/summary.json'}
    require(len(C.canonical(value)) <= 4096, 'DIAGNOSE_FAILED')
    return value


def invoke(identity, read_token, emit, **dependencies):
    token(identity); token(read_token); require(identity != read_token, 'REQUEST_INVALID')
    FS.directory(STATE, create=True)
    output = STATE / identity
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        raise DiagnoseError('EVIDENCE_ALREADY_EXISTS') from None
    R.sync(STATE)
    owner = Owner(identity, read_token, output)
    with FS.run_lock(output):
        owner.save()
        emit({'schema_version': 1, 'event': 'allocated', 'command': 'query diagnose', 'evidence_token': identity})
        report, public = owner.execute(**dependencies)
        try:
            return event(identity, report['result'], public, report['error_code'], report['cleanup_verified'])
        except BaseException as error:
            owner.report.update(result='interrupted' if not isinstance(error, Exception) else 'failed', error_code=code(error))
            try:
                owner.save()
            except BaseException as other:
                error = R.retain(error, other)
            raise error


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise DiagnoseError('ARGUMENTS_INVALID')


def main(argv=None, *, stdin=None, stdout=None, invoker=None):
    stream, identity = stdout or sys.stdout, '0' * 32
    def emit(value):
        raw = C.canonical(value)
        require(len(raw) <= 4096, 'DIAGNOSE_FAILED')
        stream.write(raw.decode('ascii') + '\n'); stream.flush()
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('command', choices=('diagnose',))
        parser.add_argument('--evidence-token', required=True)
        parser.add_argument('--request-stdin', required=True, action='store_true')
        try:
            args = parser.parse_args(argv)
        except SystemExit as error:
            if error.code == 0:
                return 0
            raise
        identity = token(args.evidence_token)
        raw = (stdin or sys.stdin.buffer).read(1025)
        try:
            require(len(raw) <= 1024, 'REQUEST_INVALID')
            request = Q.decode(raw)
            require(type(request) is dict and set(request) == {'read_token'}, 'REQUEST_INVALID')
            source_token = token(request['read_token'])
            require(source_token != identity, 'REQUEST_INVALID')
        except Exception:
            raise DiagnoseError('REQUEST_INVALID') from None
        with SESSION.interruption_handlers():
            value = (invoker or invoke)(identity, source_token, emit)
        emit(value)
        return 0 if value['result'] == 'passed' else 1
    except BaseException as error:
        try:
            emit(event(identity, 'interrupted' if not isinstance(error, Exception) else 'failed', None, code(error), False))
        except BaseException as other:
            error = R.retain(error, other)
        if not isinstance(error, Exception):
            signal = getattr(error, 'signum', 2)
            return 128 + signal if type(signal) is int and signal in (2, 15) else 130
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
