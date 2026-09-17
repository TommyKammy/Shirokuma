#!/usr/bin/env python3
"""Owned Iceberg snapshot/time-travel/restore experiment, never a retained-table tool.

Uses Trino 483 snapshot metadata, FOR VERSION AS OF and rollback_to_snapshot:
https://github.com/trinodb/trino/blob/483/docs/src/main/sphinx/connector/iceberg.md
The restore is a physical CTAS copy, not a branch, tag or zero-copy clone.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
SPEC = importlib.util.spec_from_file_location('sandbox_trino', HERE.parent / 'local_trino_smoke.py')
TRINO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRINO)
SESSION_SPEC = importlib.util.spec_from_file_location('sandbox_session', HERE.parent / 'local_session.py')
SESSION = importlib.util.module_from_spec(SESSION_SPEC)
SESSION_SPEC.loader.exec_module(SESSION)
NAMESPACE = 'iceberg.local_experiment'
FIXTURE = 'iceberg.smoke.fixture_v1'
ROWS_A = [[1, 'shirokuma'], [2, 'polaris']]
ROWS_B = ROWS_A + [[3, 'sandbox']]
LIMITS = ['DISPOSABLE_TABLES_ONLY', 'PHYSICAL_CTAS_COPY', 'NO_BRANCH_OR_TAG_CREATION', 'NO_ZERO_COPY_CLONE']
QUERY_ID = re.compile(r'[A-Za-z0-9_]{1,128}')


class SandboxError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SandboxError(message)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def token_names(token):
    require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token), 'invalid sandbox run token')
    return (f'sandbox_{token}_source', f'sandbox_{token}_restore')


def run_user(token, control=False):
    token_names(token)
    return 'shirokuma-local-iceberg-' + ('control-' if control else '') + token


def write_json(path, value):
    """Atomic private evidence; callers never supply paths through the CLI."""
    temporary = path.with_name(path.name + '.new')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write('\n')
    os.replace(temporary, path)


class SandboxClient(TRINO.TrinoClient):
    """Same loopback/no-proxy base, with owned users and early query-ID receipts."""
    def __init__(self, url, token, *, control=False, timeout=45):
        super().__init__(url, timeout)
        require(urllib.parse.urlsplit(url).port is not None, 'explicit loopback port is required')
        self.user = run_user(token, control)
        self.records = []
        self.progress = None

    def query(self, sql):
        started = time.monotonic()
        deadline = started + self.timeout
        server_seconds = max(1, min(30, math.ceil(self.timeout)))
        record = {'query_id': None, 'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
                  'started_at': stamp(), 'result': 'running', 'response_complete': False,
                  'server_max_run_seconds': server_seconds}
        self.records.append(record)
        # Kept in memory only, to wait out an accepted POST whose response was lost.
        record['_not_before'] = started + server_seconds + 2
        def notify():
            if self.progress:
                self.progress()
        next_uri, first = self.url + '/v1/statement', True
        rows, columns, types = [], [], []
        received = 0
        try:
            notify()
            while next_uri:
                parsed = urllib.parse.urlsplit(next_uri)
                require((parsed.scheme, parsed.netloc) == self.origin and parsed.username is None
                        and parsed.password is None and not parsed.fragment and not parsed.query,
                        'statement polling escaped the fixed loopback origin')
                if not first:
                    require(re.fullmatch(r'/v1/statement/(?:queued|executing)/' + re.escape(record['query_id']) + r'/[^?]*', parsed.path),
                            'statement polling changed query identity')
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'bounded sandbox query timed out')
                request = urllib.request.Request(next_uri, data=sql.encode() if first else None,
                    headers={'X-Trino-User': self.user, 'X-Trino-Source': 'shirokuma-local-iceberg-sandbox',
                             'X-Trino-Session': f'query_max_run_time={server_seconds}s'},
                    method='POST' if first else 'GET')
                with self.opener.open(request, timeout=min(remaining, 10)) as response:
                    raw = response.read(1024 * 1024 + 1)
                received += len(raw)
                require(len(raw) <= 1024 * 1024 and received <= 4 * 1024 * 1024, 'sandbox response budget exceeded')
                page = json.loads(raw)
                require(type(page) is dict and type(page.get('id')) is str and QUERY_ID.fullmatch(page['id']),
                        'sandbox query ID is missing or invalid')
                require(record['query_id'] in (None, page['id']), 'sandbox query ID changed between pages')
                record['query_id'] = page['id']
                notify()
                require('error' not in page, 'sandbox SQL request failed')
                if 'columns' in page:
                    current_columns = [column['name'] for column in page['columns']]
                    current_types = [column['type'] for column in page['columns']]
                    require(not columns or (columns == current_columns and types == current_types), 'query columns changed')
                    columns, types = current_columns, current_types
                data = page.get('data', [])
                require(type(data) is list and all(type(row) is list for row in data), 'query rows are malformed')
                rows.extend(data)
                require(len(rows) <= 128, 'sandbox result row budget exceeded')
                next_uri = page.get('nextUri')
                require(next_uri is None or type(next_uri) is str, 'invalid query continuation')
                first = False
            record.update(result='finished', response_complete=True)
            notify()
            return {'query_id': record['query_id'], 'columns': columns, 'column_types': types, 'rows': rows}
        except BaseException as error:
            record['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
            if record['query_id'] is None:
                # Acceptance may occur near the end of the initial HTTP wait.
                # Its server deadline must start no earlier than this observation.
                record['_not_before'] = time.monotonic() + server_seconds + 2
            try:
                notify()
            except Exception:
                pass
            if not isinstance(error, Exception):
                raise
            raise SandboxError('bounded local sandbox SQL failed') from None


def bounded_query(client, sql, deadline):
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'sandbox cleanup deadline exhausted')
    previous = client.timeout
    try:
        client.timeout = min(previous, remaining, 10)
        return client.query(sql)
    finally:
        client.timeout = previous


def drain_queries(control, worker, token, timeout=90):
    """Match the sandbox user, including a lost POST response; never a shared user."""
    require(worker.user == run_user(token) and control.user == run_user(token, True), 'unexpected drain identities')
    deadline = time.monotonic() + timeout
    unknown_until = max((record.get('_not_before', 0) for record in worker.records
                         if record.get('query_id') is None and not record.get('response_complete')), default=0)
    result = {'verified': False, 'user': worker.user, 'active': [], 'cancelled': [], 'cancel_errors': [], 'errors': []}
    sql = ('SELECT query_id, state, "user" FROM system.runtime.queries '
           f'WHERE "user" = \'{worker.user}\' AND state NOT IN (\'FINISHED\', \'FAILED\')')
    try:
        while True:
            active = bounded_query(control, sql, deadline)['rows']
            require(type(active) is list, 'malformed owned query inspection')
            for row in active:
                require(type(row) is list and len(row) == 3 and type(row[0]) is str
                        and QUERY_ID.fullmatch(row[0]) and row[2] == worker.user
                        and row[1] in ('QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING'),
                        'unexpected identity during owned query drain')
            result['active'] = copy.deepcopy(active)
            if not active and time.monotonic() >= unknown_until:
                result['verified'] = True
                return result
            for query_id, _, _ in active:
                if query_id in result['cancelled']:
                    continue
                try:
                    bounded_query(control, f"CALL system.runtime.kill_query(query_id => '{query_id}', message => 'Shirokuma disposable Iceberg sandbox cleanup')", deadline)
                    result['cancelled'].append(query_id)
                except Exception:
                    result['cancel_errors'].append({'query_id': query_id, 'error_code': 'CANCEL_FAILED_OR_ALREADY_FINISHED'})
            require(time.monotonic() < deadline, 'owned query drain timed out')
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
    except Exception:
        result['errors'].append('OWNED_QUERY_DRAIN_UNVERIFIED')
    return result


def table_set(client, deadline):
    result = bounded_query(client, f'SHOW TABLES FROM {NAMESPACE}', deadline)
    require(all(type(row) is list and len(row) == 1 and type(row[0]) is str for row in result['rows']), 'malformed table inventory')
    return {row[0] for row in result['rows']}


def marker(token):
    token_names(token)
    return f'shirokuma-local-iceberg-sandbox:{token}'


def verify_marker(control, name, token, deadline):
    require(name in token_names(token), 'ownership inspection escaped sandbox names')
    ddl = bounded_query(control, f'SHOW CREATE TABLE {NAMESPACE}.{name}', deadline)['rows']
    require(len(ddl) == 1 and len(ddl[0]) == 1 and type(ddl[0][0]) is str
            and re.search(r"\bCOMMENT\s+'" + re.escape(marker(token)) + r"'", ddl[0][0]),
            'ownership marker changed; table preserved')


def cleanup(control, names, token, attempted, timeout=60):
    require(names == token_names(token) and set(attempted) <= set(names), 'cleanup ownership scope differs')
    result = {'removed': [], 'remaining': [], 'errors': [], 'verified': False}
    deadline = time.monotonic() + timeout
    try:
        present = table_set(control, deadline)
        for name in reversed(names):
            if name not in present or name not in attempted:
                continue
            verify_marker(control, name, token, deadline)
            bounded_query(control, f'DROP TABLE {NAMESPACE}.{name}', deadline)
            result['removed'].append(name)
        result['remaining'] = sorted(set(attempted) & table_set(control, deadline))
        result['verified'] = not result['remaining']
    except Exception:
        result['errors'].append('OWNED_TABLE_CLEANUP_UNVERIFIED')
    return result


def check_rows(result, expected):
    require(result['columns'] == ['id', 'name'] and result['column_types'] == ['bigint', 'varchar'], 'fixture columns or types differ')
    rows = result['rows']
    require(type(rows) is list and all(type(row) is list and len(row) == 2
            and type(row[0]) is int and type(row[1]) is str for row in rows) and rows == expected,
            'independent sandbox row expectation differs')


def snapshot_id(value):
    require(type(value) is int and 0 < value < 2**63, 'invalid Iceberg snapshot ID')
    return value


def metadata(name, execute):
    table = f'{NAMESPACE}."{name}'
    snapshots = execute('snapshots', f'SELECT snapshot_id, parent_id, operation FROM {table}$snapshots" ORDER BY snapshot_id')
    history = execute('history', f'SELECT snapshot_id, parent_id, is_current_ancestor FROM {table}$history" ORDER BY made_current_at')
    refs = execute('refs', f'SELECT name, "type", snapshot_id FROM {table}$refs" ORDER BY name')
    for result, columns, types in [(snapshots, ['snapshot_id', 'parent_id', 'operation'], ['bigint', 'bigint', 'varchar']),
                                  (history, ['snapshot_id', 'parent_id', 'is_current_ancestor'], ['bigint', 'bigint', 'boolean']),
                                  (refs, ['name', 'type', 'snapshot_id'], ['varchar', 'varchar', 'bigint'])]:
        require(result['columns'] == columns and result['column_types'] == types, 'snapshot metadata schema differs')
    for row in snapshots['rows']:
        require(len(row) == 3 and row[2] == 'append', 'unexpected snapshot operation')
        snapshot_id(row[0])
        if row[1] is not None:
            snapshot_id(row[1])
    for row in history['rows']:
        require(len(row) == 3 and type(row[2]) is bool, 'invalid snapshot history')
        snapshot_id(row[0])
        if row[1] is not None:
            snapshot_id(row[1])
    require(len(refs['rows']) == 1 and refs['rows'][0][:2] == ['main', 'BRANCH'], 'unexpected table references')
    snapshot_id(refs['rows'][0][2])
    return {'snapshots': snapshots['rows'], 'history': history['rows'], 'head': refs['rows'][0][2]}


def run_experiment(worker, control, output, token):
    names = token_names(token)
    require(worker.user == run_user(token) and control.user == run_user(token, True), 'unexpected sandbox clients')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {'schema_version': 1, 'run_token': token, 'started_at': stamp(), 'result': 'running',
              'tables': {'source': names[0], 'restore': names[1]}, 'limits': LIMITS,
              'steps': [], 'attempted_tables': [], 'fixture_unchanged': False}
    def save():
        report['queries'] = {role: [{key: value for key, value in record.items() if not key.startswith('_')}
                                   for record in client.records] for role, client in [('worker', worker), ('control', control)]}
        write_json(output / 'summary.json', report)
    worker.progress = control.progress = save
    def execute(label, sql, expected=None):
        step = {'name': label, 'result': 'pending', 'sql_sha256': hashlib.sha256(sql.encode()).hexdigest()}
        report['steps'].append(step)
        save()
        value = worker.query(sql)
        step['response'] = value
        if expected is not None:
            check_rows(value, expected)
        step['result'] = 'passed'
        save()
        return value
    def meta(label, name):
        value = metadata(name, lambda part, sql: execute(label + '.' + part, sql))
        report[label] = value
        save()
        return value
    def owned_source(action):
        verify_marker(control, names[0], token, time.monotonic() + 10)
        report.setdefault('ownership_checks', []).append({'before': action, 'name': names[0], 'verified': True})
        save()
    error = None
    try:
        save()
        report['fixture_before'] = execute('fixture.before', f'SELECT id, name FROM {FIXTURE} ORDER BY id', ROWS_A)
        fixture_refs_sql = 'SELECT name, "type", snapshot_id FROM iceberg.smoke."fixture_v1$refs" ORDER BY name'
        report['fixture_refs_before'] = execute('fixture.refs_before', fixture_refs_sql)
        before_refs = report['fixture_refs_before']
        require(before_refs['columns'] == ['name', 'type', 'snapshot_id']
                and before_refs['column_types'] == ['varchar', 'varchar', 'bigint']
                and len(before_refs['rows']) == 1 and before_refs['rows'][0][:2] == ['main', 'BRANCH'], 'retained fixture reference differs')
        snapshot_id(before_refs['rows'][0][2])
        present = table_set(control, time.monotonic() + 15)
        require(not (set(names) & present), 'sandbox table collision; existing data preserved')
        source, restore = [f'{NAMESPACE}.{name}' for name in names]
        report['attempted_tables'].append(names[0])
        execute('source.create', f"CREATE TABLE {source} COMMENT '{marker(token)}' AS SELECT id, name FROM {FIXTURE}")
        execute('source.rows_a', f'SELECT id, name FROM {source} ORDER BY id', ROWS_A)
        a = meta('snapshot_a', names[0])
        require(a['snapshots'] == [[a['head'], None, 'append']] and a['history'] == [[a['head'], None, True]], 'initial snapshot is not a single root')
        owned_source('source.append')
        execute('source.append', f"INSERT INTO {source} VALUES (BIGINT '3', 'sandbox')")
        execute('source.rows_b', f'SELECT id, name FROM {source} ORDER BY id', ROWS_B)
        b = meta('snapshot_b', names[0])
        expected_snapshots = sorted([[a['head'], None, 'append'], [b['head'], a['head'], 'append']])
        require(a['head'] != b['head'] and b['snapshots'] == expected_snapshots
                and b['history'] == [[a['head'], None, True], [b['head'], a['head'], True]], 'append snapshot ancestry differs')
        execute('source.time_travel_a', f"SELECT id, name FROM {source} FOR VERSION AS OF {a['head']} ORDER BY id", ROWS_A)
        owned_source('restore.create')
        report['attempted_tables'].append(names[1])
        execute('restore.create', f"CREATE TABLE {restore} COMMENT '{marker(token)}' AS SELECT id, name FROM {source} FOR VERSION AS OF {a['head']}")
        execute('restore.rows', f'SELECT id, name FROM {restore} ORDER BY id', ROWS_A)
        restored = meta('restored_copy', names[1])
        require(restored['snapshots'] == [[restored['head'], None, 'append']]
                and restored['history'] == [[restored['head'], None, True]], 'restore is not an independent CTAS snapshot')
        owned_source('source.rollback')
        execute('source.rollback', f"ALTER TABLE {source} EXECUTE rollback_to_snapshot({a['head']})")
        execute('source.rows_rolled_back', f'SELECT id, name FROM {source} ORDER BY id', ROWS_A)
        rolled = meta('rolled_back', names[0])
        # Trino 483 HistoryTable iterates Table.snapshots(), not Table.history():
        # rollback changes current ancestry but does not add a second A row.
        # https://github.com/trinodb/trino/blob/483/plugin/trino-iceberg/src/main/java/io/trino/plugin/iceberg/system/HistoryTable.java
        require(rolled['head'] == a['head'] and rolled['snapshots'] == expected_snapshots
                and rolled['history'] == [[a['head'], None, True], [b['head'], a['head'], False]],
                'rollback did not move main to A while preserving B history')
        execute('source.time_travel_b_after_rollback', f"SELECT id, name FROM {source} FOR VERSION AS OF {b['head']} ORDER BY id", ROWS_B)
        report['functional_verified'] = True
    except BaseException as caught:
        error = caught
        report['error_code'] = 'SANDBOX_INTERRUPTED' if not isinstance(caught, Exception) else 'SANDBOX_STEP_FAILED'
    finally:
        try:
            report['query_drain'] = drain_queries(control, worker, token)
            save()
            if report['query_drain']['verified']:
                report['cleanup'] = cleanup(control, names, token, report['attempted_tables'])
            else:
                report['cleanup'] = {'verified': False, 'removed': [], 'errors': ['QUERY_DRAIN_REQUIRED'], 'remaining': report['attempted_tables'][:]}
            after = bounded_query(control, f'SELECT id, name FROM {FIXTURE} ORDER BY id', time.monotonic() + 10)
            check_rows(after, ROWS_A)
            report['fixture_after'] = after
            refs_after = bounded_query(control, 'SELECT name, "type", snapshot_id FROM iceberg.smoke."fixture_v1$refs" ORDER BY name', time.monotonic() + 10)
            report['fixture_refs_after'] = refs_after
            report['fixture_unchanged'] = ('fixture_before' in report and 'fixture_refs_before' in report
                and after['rows'] == report['fixture_before']['rows']
                and all(refs_after[key] == report['fixture_refs_before'][key] for key in ('columns', 'column_types', 'rows')))
        except BaseException as caught:
            report['cleanup_error_code'] = 'SANDBOX_FINALIZATION_UNVERIFIED'
            if error is None:
                error = caught
        report['result'] = 'passed' if (error is None and report.get('functional_verified') is True
            and report.get('query_drain', {}).get('verified') is True
            and report.get('cleanup', {}).get('verified') is True and report['fixture_unchanged']) else 'failed'
        report['finished_at'] = stamp()
        try:
            save()
        finally:
            worker.progress = control.progress = None
    if error is not None and not isinstance(error, Exception):
        raise error
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:18080')
    args = parser.parse_args()
    token = uuid.uuid4().hex
    output = ROOT / 'tmp/local-experiment/iceberg-sandbox' / token
    try:
        with SESSION.interruption_handlers():
            worker = SandboxClient(args.url, token)
            control = SandboxClient(args.url, token, control=True, timeout=15)
            report = run_experiment(worker, control, output, token)
        print(json.dumps({'run_token': token, 'result': report['result'], 'fixture_unchanged': report['fixture_unchanged'],
                          'cleanup_verified': report.get('cleanup', {}).get('verified', False), 'limits': LIMITS}))
        return 0 if report['result'] == 'passed' else 1
    except SESSION.SessionInterrupted as error:
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        interruption = {'run_token': token, 'result': 'interrupted', 'signal': error.signum,
                        'cleanup': 'inspect summary.json; completion is not implied by interruption'}
        write_json(output / 'interruption.json', interruption)
        print(json.dumps(interruption))
        return 128 + error.signum
    except Exception:
        print(json.dumps({'run_token': token, 'result': 'failed', 'error_code': 'SANDBOX_SETUP_OR_EVIDENCE_FAILED'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
