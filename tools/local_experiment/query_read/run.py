#!/usr/bin/env python3
"""Read a bounded, discovered Iceberg table through the local product CLI."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.query_read import contract as C
from tools.local_experiment.query_read import client as Q
from tools.local_experiment.trino_opa import common as FS
from tools.local_experiment import local_session as SESSION

STATE = ROOT / 'tmp/local-experiment/query-read'
PUBLIC_CODES = frozenset('''
ARGUMENTS_INVALID AUTHENTICATION_RETRY_FORBIDDEN BUILD_IDENTITY_MISMATCH CANCEL_ID_NOT_OWNED
CANCEL_PROVENANCE_INVALID CANCEL_RESPONSE_INVALID CANCEL_RETRY_FORBIDDEN CANCEL_UNCERTAIN
CATALOG_AUTHENTICATION_FAILED CATALOG_READ_FAILED CATALOG_READ_LIMIT CATALOG_READ_NOT_READY
CATALOG_RESPONSE_INVALID CATALOG_RESPONSE_LIMIT CELL_BOOLEAN_INVALID CELL_DATE_INVALID
CELL_DECIMAL_INVALID CELL_FLOAT_INVALID CELL_INTEGER_INVALID CELL_LIMIT CELL_NULL_INVALID
CELL_NUMBER_INVALID CELL_STRING_INVALID COLUMNS_INVALID COLUMN_NOT_DISCOVERED COMMAND_INVALID
COMMAND_MISMATCH CONTROL_INVALID CONTROL_SQL_INVALID DRAIN_DEADLINE DRAIN_IDENTITY_INVALID
DRAIN_INVENTORY_INVALID DRAIN_PROVENANCE_INVALID DRAIN_SCHEMA_INVALID DRAIN_TIMEOUT_INVALID
DRAIN_UNTRACKED_QUERY DRAIN_UNVERIFIED DUPLICATE_COLUMNS DUPLICATE_JSON_KEY FILE_TOO_LARGE
FORMAT_UNSUPPORTED IDENTIFIER_INVALID IDENTITY_CHANGED IDENTITY_INVALID IDENTITY_SCHEMA_DIFFER
INPUT_LIMIT INTERRUPTED INVALID_RUN_TOKEN INVENTORY_INCOMPLETE INVENTORY_INVALID
JOURNAL_AND_GUARD_REQUIRED JOURNAL_REQUIRED JSON_INVALID LIMIT_INVALID LOCAL_COMMAND_FAILED
METADATA_INVALID METADATA_LIMIT METADATA_LOCATION_INVALID NONPRIVATE_EVIDENCE NO_CURRENT_SNAPSHOT
PROCESS_OUTPUT_LIMIT PUBLIC_RESULT_INVALID PUBLIC_RESULT_LIMIT QUERY_CELL_TYPE_INVALID
QUERY_COLUMNS_CHANGED QUERY_COLUMNS_INVALID QUERY_CONTENT_TYPE_INVALID QUERY_CONTINUATION_CHANGED
QUERY_DEADLINE QUERY_DRAIN_UNVERIFIED QUERY_ERROR_INVALID QUERY_FAILED QUERY_HTTP_ORIGIN_INVALID
QUERY_ID_INVALID QUERY_INPUT_INVALID QUERY_LABEL_INVALID QUERY_NEXT_INVALID QUERY_NOT_READ_ONLY
QUERY_ORIGIN_CHANGED QUERY_OUTCOME_UNRESOLVED QUERY_PAGE_BOUND QUERY_PAGE_BYTES_BOUND
QUERY_RESPONSE_INVALID QUERY_ROWS_INVALID QUERY_ROW_BOUND QUERY_SCHEMA_MISSING QUERY_STATE_INVALID
QUERY_TERMINAL_INVALID QUERY_TOTAL_BYTES_BOUND READ_CLEANUP_UNVERIFIED READ_FAILED READ_RETRY_FORBIDDEN
READ_SOURCE_CHANGED READ_SQL_INVALID REFS_INVALID REQUEST_INVALID REQUEST_KEYS_INVALID
RESPONSE_DUPLICATE_KEY RESPONSE_JSON_INVALID RESPONSE_NONFINITE REST_TRINO_SCHEMA_DIFFER
RESULT_INPUT_LIMIT RESULT_SCHEMA_INVALID RUN_ACTIVE SCHEMA_COLUMNS_INVALID SCHEMA_COLUMN_LIMIT
SCHEMA_INVALID SCHEMA_UNSUPPORTED SNAPSHOT_INVALID SPEC_INVALID TABLE_INVALID TABLE_LOCATION_INVALID
TABLE_NOT_DISCOVERED TABLE_UUID_INVALID TIMEOUT_INVALID TOKEN_INVALID TRINO_QUERY_FAILED
TRINO_SERVICE_CHANGED TRINO_SERVICE_INVALID TYPE_UNSUPPORTED UNSAFE_FILE UNSAFE_PATH UNSAFE_RUN_LOCK
URL_INVALID
'''.split())


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def retain(original, error):
    return error if original is None or isinstance(original, Exception) and not isinstance(error, Exception) else original


def code(error):
    if not isinstance(error, Exception):
        return 'INTERRUPTED'
    value = str(error)
    return value if type(error) in (C.ContractError, Q.ClientError, FS.ExperimentError) and value in PUBLIC_CODES else 'READ_FAILED'


def sources():
    paths = [*Path(__file__).parent.glob('*.py')]
    paths += [ROOT / 'tools/local_experiment' / name for name in (
        'trino_opa/common.py', 'local_session.py', 'local_trino_smoke.py', 'local_experiment.py',
        'iceberg_sandbox/run.py',
        'iceberg_refs/run.py', 'iceberg_refs/client.py', 'iceberg_refs/contract.py',
        'polaris_permissions.py')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def safe_observation(public, action):
    return C.receipt_projection(public) if action == 'select' else deepcopy(public)


class Owner:
    def __init__(self, token, request, output):
        FS.token(token)
        self.token, self.request, self.output = token, C.normalize_request(request), output
        self.worker = self.control = self.catalog = None
        self.public = None
        self.original = None
        self.report = {'schema_version': 1, 'evidence_token': token, 'request': self.request,
            'request_sha256': C.digest(self.request), 'result': 'running', 'started_at': stamp(),
            'finished_at': None, 'invocations': 0, 'observation': None, 'queries': {},
            'runtime': {}, 'query_drain': {'verified': False}, 'cleanup': {'verified': False},
            'error_code': None, 'errors': [], 'source_hashes': sources(),
            'limits': ['LOCAL_EXPERIMENT_ONLY', 'READ_ONLY_FIXED_CATALOG_AND_SCHEMAS',
                       'NO_ARBITRARY_SQL_OR_MODEL_CALLS', 'NO_POST_RETRY',
                       'ROWS_ARE_EXPLICIT_OUTPUT_NOT_SAVED_EVIDENCE', 'ORDERING_UNSPECIFIED',
                       'NO_TENANT_AUTHORIZATION_OR_PRODUCTION_CLAIM']}

    def persist(self):
        if self.worker is not None:
            self.report['queries'] = {name: [{k: deepcopy(v) for k, v in row.items() if not k.startswith('_')}
                for row in client.records] for name, client in [('worker', self.worker), ('control', self.control)]}
        if self.catalog is not None:
            self.report['catalog'] = self.catalog.receipt()
        FS.write(self.output / 'summary.json', self.report)
        sync(self.output)

    def runtime_progress(self, value):
        self.report['runtime'] = deepcopy(value)
        self.persist()

    def attach(self, clients):
        self.worker, self.control, self.catalog = clients
        self.worker.progress = self.control.progress = self.persist
        self.catalog.progress = self.persist
        self.persist()

    def observe(self):
        C.require(self.report['invocations'] == 0, 'READ_RETRY_FORBIDDEN')
        self.report['invocations'] = 1
        self.persist()
        request, action = self.request, self.request['command']
        schema = request['schema'] if action == 'tables' else request['table'].split('.')[0]
        inventory = C.project_inventory(self.worker.query(C.discovery_sql(request), 'inventory'), schema)
        self.report['inventory'] = deepcopy(inventory)
        self.persist()
        if action == 'tables':
            return inventory
        C.require_discovered(request, inventory)
        columns = C.project_schema(self.worker.query(C.schema_sql(request), 'schema_before'))
        if action == 'select':
            C.selected_columns(request, columns)
        self.catalog.authenticate()
        before = C.project_identity(self.catalog.load(request), request, columns)
        self.report['identity_before'] = before
        self.persist()
        if action == 'select':
            sql = C.select_sql(request, inventory, columns, before)
            public = C.project_rows(self.worker.query(sql, 'select'), request, columns, before)
        else:
            public = deepcopy(before)
        after_columns = C.project_schema(self.worker.query(C.schema_sql(request), 'schema_after'))
        after = C.project_identity(self.catalog.load(request), request, after_columns)
        C.verify_identity(before, after)
        self.report['identity_after'] = after
        self.report['identity_unchanged'] = True
        self.persist()
        return public

    def execute(self, runtime_factory, drain):
        C.require(self.worker is None and self.report['invocations'] == 0, 'READ_RETRY_FORBIDDEN')
        context, entered, candidate = None, False, None
        try:
            self.persist()
            context = runtime_factory(self.token, self.runtime_progress)
            clients = context.__enter__()
            entered = True
            self.attach(clients)
            candidate = self.observe()
            self.report['observation'] = safe_observation(candidate, self.request['command'])
            self.persist()
        except BaseException as error:
            self.original = retain(self.original, error)
        finally:
            if self.worker is not None:
                try:
                    self.report['query_drain'] = {'verified': False}
                    self.report['query_drain'] = drain(self.control, self.worker, self.token)
                    C.require(self.report['query_drain'].get('verified') is True, 'QUERY_DRAIN_UNVERIFIED')
                except BaseException as error:
                    self.report['errors'].append(code(error))
                    self.original = retain(self.original, error)
            if entered:
                try:
                    # Keep the original exception ourselves: a context manager
                    # cannot suppress it or replace it with a teardown failure.
                    context.__exit__(None, None, None)
                except BaseException as error:
                    self.report['errors'].append(code(error))
                    self.original = retain(self.original, error)
            runtime = self.report['runtime']
            self.report['cleanup'] = {'verified': self.report['query_drain'].get('verified') is True
                and runtime.get('state') == 'verified' and runtime.get('cleanup', {}).get('verified') is True}
            try:
                C.require(self.report['source_hashes'] == sources(), 'READ_SOURCE_CHANGED')
                C.require(self.report['cleanup']['verified'], 'READ_CLEANUP_UNVERIFIED')
            except BaseException as error:
                self.original = retain(self.original, error)
            passed = self.original is None and candidate is not None
            self.report.update(result='passed' if passed else 'interrupted' if self.original is not None
                               and not isinstance(self.original, Exception) else 'failed',
                               error_code=None if passed else code(self.original), finished_at=stamp())
            try:
                self.persist()
            except BaseException as error:
                self.original = retain(self.original, error)
                self.report.update(result='interrupted' if not isinstance(self.original, Exception) else 'failed',
                                   error_code=code(self.original))
                # A rename may have completed before directory fsync failed.
                # Best effort marks the uncertain receipt; never repeat a query.
                try:
                    self.persist()
                except BaseException as secondary:
                    self.original = retain(self.original, secondary)
                passed = False
            self.public = candidate if passed else None
        if self.original is not None and not isinstance(self.original, Exception):
            raise self.original
        return deepcopy(self.report), deepcopy(self.public)


def event(token, action, result, observation, error_code, cleanup):
    value = {'schema_version': 1, 'event': 'result', 'command': 'query ' + action,
        'evidence_token': token, 'result': result, 'observation': observation,
        'error_code': error_code, 'cleanup_verified': cleanup,
        'evidence': f'tmp/local-experiment/query-read/{token}/summary.json'}
    C.require(len(C.canonical(value)) <= C.MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
    return value


def invoke(token, request, emit, *, runtime_factory=None, drain=None):
    """Dependency injection keeps real CLI/owner tests offline; no replay API."""
    token, request = FS.token(token), C.normalize_request(request)
    FS.directory(STATE, create=True)
    output = STATE / token
    output.mkdir(mode=0o700)  # existing evidence is never adopted or overwritten
    sync(STATE)
    owner = Owner(token, request, output)
    with FS.run_lock(output):
        owner.persist()
        emit({'schema_version': 1, 'event': 'allocated', 'command': 'query ' + request['command'],
              'evidence_token': token})
        try:
            report, public = owner.execute(runtime_factory or Q.runtime, drain or Q.drain)
        except BaseException:
            raise
        try:
            return event(token, request['command'], report['result'], public, report['error_code'],
                         report['cleanup']['verified'])
        except BaseException as error:
            owner.public = None
            owner.report.update(result='interrupted' if not isinstance(error, Exception) else 'failed', error_code=code(error))
            try:
                owner.persist()
            except BaseException as secondary:
                error = retain(error, secondary)
            raise error


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise C.ContractError('ARGUMENTS_INVALID')


def main(argv=None, *, stdout=None, stdin=None, invoker=None):
    stream = stdout or sys.stdout
    identity, action = '0' * 32, 'tables'
    def emit(value):
        encoded = C.canonical(value)
        C.require(len(encoded) <= C.MAX_PUBLIC_BYTES, 'PUBLIC_RESULT_LIMIT')
        stream.write(encoded.decode('ascii') + '\n')
        stream.flush()
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('command', choices=('tables', 'describe', 'select'))
        parser.add_argument('--evidence-token', required=True)
        parser.add_argument('--request-stdin', required=True, action='store_true')
        try:
            args = parser.parse_args(argv)
        except SystemExit as error:
            if error.code == 0:
                return 0  # argparse --help only; never a workflow exit.
            raise
        identity, action = FS.token(args.evidence_token), args.command
        raw = (stdin or sys.stdin.buffer).read(C.MAX_INPUT_BYTES + 1)
        request = C.parse_request(raw)
        C.require(request['command'] == action, 'COMMAND_MISMATCH')
        with SESSION.interruption_handlers():
            result = (invoker or invoke)(identity, request, emit)
        emit(result)
        return 0 if result['result'] == 'passed' else 1
    except BaseException as error:
        try:
            result = event(identity, action, 'interrupted' if not isinstance(error, Exception) else 'failed', None, code(error), False)
            emit(result)
        except BaseException as secondary:
            error = retain(error, secondary)
        if not isinstance(error, Exception):
            signum = getattr(error, 'signum', 2)
            return 128 + signum if type(signum) is int and signum in (2, 15) else 130
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
