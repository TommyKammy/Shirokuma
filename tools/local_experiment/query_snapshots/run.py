#!/usr/bin/env python3
"""List retained snapshots or read one explicitly selected local snapshot."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_read import run as BASE
from tools.local_experiment.query_read import client as Q
from tools.local_experiment.query_snapshots import contract as C
from tools.local_experiment.trino_opa import common as FS
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/query-snapshots'
OWN_CODES = frozenset('''ARGUMENTS_INVALID COMMAND_MISMATCH REQUEST_INVALID INTERRUPTED
OBSERVATION_FAILED SNAPSHOT_READ_FAILED SNAPSHOT_READ_RETRY_FORBIDDEN
SNAPSHOT_SOURCE_CHANGED SNAPSHOT_CLEANUP_UNVERIFIED EVIDENCE_ALREADY_EXISTS'''.split())


class SnapshotError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SnapshotError(message)


def codes(_contract=C):
    return BASE.PUBLIC_CODES | _contract.ERROR_CODES | OWN_CODES


def code(error, _contract=C):
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    from tools.local_experiment.query_snapshots.observe import ObservationError
    value = str(error)
    return value if type(error) in (SnapshotError, C.ContractError, R.ContractError,
        Q.ClientError, FS.ExperimentError, ObservationError) and value in codes(_contract) else 'SNAPSHOT_READ_FAILED'


def sources(_contract=C):
    value = BASE.sources()
    for name in ('contract.py', 'observe.py', 'run.py'):
        path = Path(__file__).parent / name
        value[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if _contract is not C:
        for name in ('contract.py', 'run.py'):
            path = ROOT / 'tools/local_experiment/query_filter' / name
            value[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(value.items()))


def _profile(contract):
    from tools.local_experiment.query_snapshots.observe import _checked_contract
    _checked_contract(contract)
    return (2, 'query-snapshots') if contract is C else (3, 'query-filter')


class Owner:
    def __init__(self, token, request, output, *, _contract=C):
        version, _ = _profile(_contract)
        C = self.contract = _contract
        self.sources = (lambda: sources()) if version == 2 else lambda: sources(_contract)
        self.token, self.request, self.output = FS.token(token), C.normalize_request(request), output
        self.public = None
        self.report = {'schema_version': version, 'evidence_token': token, 'request': self.request,
            'request_sha256': R.digest(self.request), 'result': 'running', 'invocations': 0,
            'started_at': BASE.stamp(), 'finished_at': None, 'observation': None,
            'cleanup_verified': False, 'error_code': None, 'source_hashes': self.sources(),
            'model_calls': 0, 'cloud_calls': 0, 'retries': 0,
            'limits': ['LOCAL_EXPERIMENT_ONLY', 'FIXED_CATALOG_AND_SCHEMAS',
                       'RETAINED_SNAPSHOT_MEMBERSHIP_REQUIRED', 'SAME_SCHEMA_ONLY',
                       'CURRENT_HEAD_IS_DISTINCT_FROM_SELECTED_SNAPSHOT',
                       'NO_ARBITRARY_SQL_OR_MODEL_CALLS', 'NO_POST_RETRY_OR_FALLBACK',
                       'ROWS_ARE_EXPLICIT_OUTPUT_NOT_SAVED_EVIDENCE',
                       'NO_TENANT_AUTHORIZATION_OR_PRODUCTION_CLAIM']}
        if version == 3:
            self.report['profile'] = C.PROFILE

    def save(self):
        FS.write(self.output / 'summary.json', self.report)
        BASE.sync(self.output)

    def progress(self, observation):
        self.report['observation'] = deepcopy(observation)
        self.report['cleanup_verified'] = observation.get('cleanup_verified') is True
        self.save()

    def execute(self, *, observer=None):
        C = self.contract
        require(self.report['invocations'] == 0, 'SNAPSHOT_READ_RETRY_FORBIDDEN')
        self.report['invocations'] = 1
        original, candidate = None, None
        try:
            self.save()
            if observer is None:
                from tools.local_experiment.query_snapshots.observe import observe
                observer = (lambda request, token, progress: observe(request, token, progress, _contract=C))
            observation, candidate = observer(self.request, self.token, self.progress)
            require(observation['state'] == 'verified' and observation['cleanup_verified'] is True,
                    'SNAPSHOT_CLEANUP_UNVERIFIED')
            self.progress(observation)
            candidate = C.validate_public(candidate, self.request)
            projected = C.receipt_projection(candidate)
            require(observation['selection'] == projected if self.request['command'] == 'select'
                    else observation['selection'] is None, 'OBSERVATION_FAILED')
            require(self.report['source_hashes'] == self.sources(), 'SNAPSHOT_SOURCE_CHANGED')
        except BaseException as error:
            original = error
        finally:
            try:
                require(self.report['source_hashes'] == self.sources(), 'SNAPSHOT_SOURCE_CHANGED')
                if original is None:
                    require(self.report['cleanup_verified'], 'SNAPSHOT_CLEANUP_UNVERIFIED')
            except BaseException as error:
                original = BASE.retain(original, error)
            passed = original is None and candidate is not None
            self.report.update(result='passed' if passed else 'interrupted' if original is not None
                and not isinstance(original, Exception) else 'failed',
                error_code=None if passed else code(original, C), finished_at=BASE.stamp())
            try:
                self.save()
            except BaseException as error:
                original = BASE.retain(original, error)
                self.report.update(result='interrupted' if not isinstance(original, Exception) else 'failed',
                                   error_code=code(original, C))
                try:
                    self.save()
                except BaseException as secondary:
                    original = BASE.retain(original, secondary)
                passed = False
            self.public = candidate if passed else None
        if original is not None and not isinstance(original, Exception):
            raise original
        return deepcopy(self.report), deepcopy(self.public)


def event(token, action, result, observation, error, cleanup, *, _contract=C):
    version, directory = _profile(_contract)
    value = {'schema_version': version, 'event': 'result', 'command': 'query ' + action,
        'evidence_token': token, 'result': result, 'observation': observation,
        'error_code': error, 'cleanup_verified': cleanup,
        'evidence': f'tmp/local-experiment/{directory}/{token}/summary.json'}
    require(len(R.canonical(value)) <= R.MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
    return value


@contextmanager
def owner_lock(output):
    context = FS.run_lock(output)
    context.__enter__()
    original = None
    try:
        yield
    except BaseException as error:
        original = error
    finally:
        try:
            context.__exit__(None, None, None)
        except BaseException as error:
            original = BASE.retain(original, error)
    if original is not None:
        raise original


def invoke(token, request, emit, *, _contract=C, _state=None, **dependencies):
    """Emit both protocol events under the owner lock; never adopt old evidence."""
    version, directory = _profile(_contract)
    state = _state if _state is not None else STATE if version == 2 else ROOT / 'tmp/local-experiment' / directory
    token, request = FS.token(token), _contract.normalize_request(request)
    FS.directory(state, create=True)
    output = state / token
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        raise SnapshotError('EVIDENCE_ALREADY_EXISTS') from None
    BASE.sync(state)
    owner = Owner(token, request, output, _contract=_contract)
    try:
        with owner_lock(output):
            owner.save()
            emit({'schema_version': version, 'event': 'allocated', 'command': 'query ' + request['command'],
                  'evidence_token': token})
            report, public = owner.execute(**dependencies)
            result = event(token, request['command'], report['result'], public, report['error_code'],
                           report['cleanup_verified'], _contract=_contract)
            emit(result)
            return result
    except BaseException as error:
        owner.public = None
        owner.report.update(result='interrupted' if not isinstance(error, Exception) else 'failed',
            error_code=code(error, _contract), finished_at=BASE.stamp())
        try:
            owner.save()
        except BaseException as secondary:
            error = BASE.retain(error, secondary)
        raise error


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise SnapshotError('ARGUMENTS_INVALID')


def main(argv=None, *, stdin=None, stdout=None, invoker=None, _contract=C, _state=None):
    version, _ = _profile(_contract)
    C = _contract
    stream, token, action = stdout or sys.stdout, '0' * 32, 'snapshots' if version == 2 else 'select'
    def emit(value):
        raw = R.canonical(value)
        require(len(raw) <= R.MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
        stream.write(raw.decode('ascii') + '\n'); stream.flush()
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('command', choices=('snapshots', 'select') if version == 2 else ('select',))
        parser.add_argument('--evidence-token', required=True)
        parser.add_argument('--request-stdin', required=True, action='store_true')
        try:
            args = parser.parse_args(argv)
        except SystemExit as error:
            if error.code == 0:
                return 0
            raise
        token, action = FS.token(args.evidence_token), args.command
        request = C.parse_request((stdin or sys.stdin.buffer).read(R.MAX_INPUT_BYTES + 1))
        require(request['command'] == action, 'COMMAND_MISMATCH')
        with SESSION.interruption_handlers():
            result = invoker(token, request, emit) if invoker is not None else invoke(
                token, request, emit, _contract=C, _state=_state)
        return 0 if result['result'] == 'passed' else 1
    except BaseException as error:
        try:
            emit(event(token, action, 'interrupted' if not isinstance(error, Exception) else 'failed',
                       None, code(error, C), False, _contract=C))
        except BaseException as secondary:
            error = BASE.retain(error, secondary)
        if not isinstance(error, Exception):
            signum = getattr(error, 'signum', 2)
            return 128 + signum if type(signum) is int and signum in (2, 15) else 130
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
