#!/usr/bin/env python3
"""Retain captured Query Doctor history from the fixed local evidence inventory.

This is provenance for saved observer projections, not server-side attestation or
whole-cluster history. Missing measurements remain NULL. No Trino query is run.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / 'tmp/local-experiment/query-bear'
STATE = ROOT / 'tmp/local-experiment/query-history'
TOKEN = re.compile('[0-9a-f]{32}')
QUERY_ID = re.compile(r'\d{8}_\d{6}_\d{5}_[a-z0-9]{5}')
NAMESPACE = uuid.UUID('7f50a084-8820-5414-a539-4111d5574ce9')
MAX_RUNS, MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 128, 32, 8 * 1024**2, 128 * 1024**2
RUNS, QUERIES = 'query_history_runs', 'query_history_queries'
STATES = {'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING', 'FINISHED', 'FAILED'}
PAIRS = [('projection', 'select'), ('projection', 'explain'), ('filtered', 'select'), ('filtered', 'explain')]
HISTORY_KEYS = {'query_id', 'observed_at', 'completeness', 'durations_complete', 'missing_query_ids', 'entries'}
TIMES = ('created_at', 'started_at', 'ended_at')
DURATIONS = ('queued_ms', 'analysis_ms', 'planning_ms', 'created_to_end_ms', 'started_to_end_ms')
ENTRY_KEYS = {'query_id', 'case_id', 'kind', 'state', 'error_type', 'error_code', *TIMES, *DURATIONS}
PROVENANCE = 'saved-observer-projection-not-server-attestation'
SCHEMAS = {
    RUNS: [('record_id', 'UUID'), ('payload_sha256', 'FixedString(64)'), ('evidence_token', 'String'),
           ('query_run_token', 'String'), ('query_user', 'String'), ('source_sha256', 'FixedString(64)'),
           ('provenance', 'LowCardinality(String)'), ('workflow_result', 'LowCardinality(String)'),
           ('observer_outcome', 'LowCardinality(String)'), ('source_terminal', 'Bool'), ('cleanup_verified', 'Bool'), ('target_history_verified', 'Bool'),
           ('server_max_run_seconds', 'UInt16'), ('request_count', 'UInt16'), ('captured_count', 'UInt16'),
           ('uncaptured_count', 'UInt16'), ('history_snapshot_count', 'UInt16'), ('observed_history_count', 'UInt16'),
           ('history_missing_count', 'UInt16'), ('history_unobserved_count', 'UInt16'), ('auxiliary_query_count', 'UInt16')],
    QUERIES: [('record_id', 'UUID'), ('payload_sha256', 'FixedString(64)'), ('evidence_token', 'String'),
              ('query_run_token', 'String'), ('query_user', 'String'), ('source_sha256', 'FixedString(64)'),
              ('query_id', 'String'), ('request_index', 'UInt16'), ('case_id', 'Nullable(String)'),
              ('kind', 'LowCardinality(String)'), ('response_complete', 'Bool'), ('response_state', 'Nullable(String)'),
              ('history_status', 'LowCardinality(String)'), ('history_query_id', 'Nullable(String)'),
              ('history_observed_at', "Nullable(DateTime64(6, 'UTC'))"), ('history_state', 'Nullable(String)')]
             + [(name, "Nullable(DateTime64(6, 'UTC'))") for name in TIMES]
             + [(name, 'Nullable(UInt64)') for name in DURATIONS],
}
SETTINGS = (" SETTINGS max_execution_time=20, max_threads=2, max_memory_usage=268435456, max_result_rows=10000, "
            "max_result_bytes=8388608, result_overflow_mode='throw', output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow")


class ImportFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ImportFailure(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ImportFailure('NONFINITE_JSON')))
    except (ValueError, TypeError, RecursionError):
        raise ImportFailure('INVALID_JSON') from None


def directory(path, private=False):
    require(path.is_absolute(), 'UNSAFE_DIRECTORY')
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'UNSAFE_DIRECTORY')
    info = path.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o022, 'UNSAFE_DIRECTORY')
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o700, 'NONPRIVATE_DIRECTORY')


def read_file(path):
    directory(path.parent)
    require(not path.is_symlink(), 'UNSAFE_SOURCE_FILE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and not before.st_mode & 0o022, 'UNSAFE_SOURCE_FILE')
        require(before.st_size <= MAX_FILE_BYTES, 'SOURCE_FILE_TOO_LARGE')
        raw = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) <= MAX_FILE_BYTES and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'SOURCE_CHANGED_DURING_READ')
    return raw


def snapshot(source=SOURCE):
    directory(source)
    runs = sorted(source.iterdir())
    require(0 < len(runs) <= MAX_RUNS, 'SOURCE_INVENTORY_COUNT')
    result, hashes, total = {}, {}, 0
    for run in runs:
        require(TOKEN.fullmatch(run.name) is not None, 'UNEXPECTED_SOURCE_ENTRY')
        directory(run, private=True)
        files = sorted(run.iterdir())
        require(0 < len(files) <= MAX_FILES, 'SOURCE_FILE_COUNT')
        result[run.name], hashes[run.name] = {}, {}
        for file in files:
            require(re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', file.name) is not None, 'UNEXPECTED_SOURCE_FILE')
            raw = read_file(file)
            total += len(raw)
            require(total <= MAX_TOTAL_BYTES, 'SOURCE_TOTAL_TOO_LARGE')
            result[run.name][file.name] = raw
            hashes[run.name][file.name] = digest(raw)
    return result, hashes


def integer(value):
    require(type(value) is int and 0 <= value <= 2**64 - 1, 'INVALID_COUNT_OR_DURATION')
    return value


def timestamp(value):
    if value is None:
        return None
    require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z', value), 'INVALID_TIMESTAMP')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%Y-%m-%d %H:%M:%S.%f')
    except ValueError:
        raise ImportFailure('INVALID_TIMESTAMP') from None


def state(value):
    require(value is None or type(value) is str and value in STATES, 'INVALID_QUERY_STATE')
    return value


def identifier(value):
    require(type(value) is str and QUERY_ID.fullmatch(value) is not None, 'INVALID_QUERY_ID')
    return value


def make_row(table, identity, values):
    result = {'record_id': str(uuid.uuid5(NAMESPACE, identity)), **values}
    result['payload_sha256'] = digest(result)
    require(set(result) == {name for name, _ in SCHEMAS[table]}, 'PROJECTION_SCHEMA_ERROR')
    return result


def history(value, requests):
    require(type(value) is dict and set(value) == HISTORY_KEYS, 'HISTORY_SCHEMA_MISMATCH')
    qid = identifier(value['query_id'])
    require(qid in requests and requests[qid]['kind'] == 'own_query_history'
            and requests[qid]['response_complete'] is True, 'HISTORY_SOURCE_IDENTITY_MISMATCH')
    observed = timestamp(value['observed_at'])
    require(observed is not None and type(value['entries']) is list and len(value['entries']) <= 4
            and type(value['missing_query_ids']) is list, 'HISTORY_SHAPE_MISMATCH')
    targets = {q: v for q, v in requests.items() if v['kind'] in ('select', 'explain')}
    require({(r['case_id'], r['kind']) for r in targets.values()} == set(PAIRS), 'HISTORY_TARGETS_INCOMPLETE')
    entries = {}
    for item in value['entries']:
        require(type(item) is dict and set(item) == ENTRY_KEYS, 'HISTORY_ENTRY_SCHEMA_MISMATCH')
        q = identifier(item['query_id'])
        require(q in targets and q not in entries and (item['case_id'], item['kind']) ==
                (targets[q]['case_id'], targets[q]['kind']), 'HISTORY_QUERY_IDENTITY_MISMATCH')
        require(state(item['state']) is not None, 'HISTORY_STATE_MISSING')
        for key in ('error_type', 'error_code'):
            require(item[key] is None or type(item[key]) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,127}', item[key]),
                    'UNSAFE_ERROR_ENUM')
        projected = {'history_query_id': qid, 'history_observed_at': observed, 'history_state': item['state']}
        projected.update({key: timestamp(item[key]) for key in TIMES})
        projected.update({key: None if item[key] is None else integer(item[key]) for key in DURATIONS})
        for left, right in [('created_at', 'started_at'), ('created_at', 'ended_at'), ('started_at', 'ended_at')]:
            require(projected[left] is None or projected[right] is None or projected[left] <= projected[right], 'HISTORY_TIMESTAMP_ORDER')
        for begin, duration in [('created_at', 'created_to_end_ms'), ('started_at', 'started_to_end_ms')]:
            expected = None
            if item['state'] in ('FINISHED', 'FAILED') and projected[begin] and projected['ended_at']:
                delta = datetime.fromisoformat(projected['ended_at']) - datetime.fromisoformat(projected[begin])
                expected = delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
            require(projected[duration] == expected, 'HISTORY_DURATION_MISMATCH')
        entries[q] = projected
    missing = sorted(set(targets) - set(entries))
    require(value['missing_query_ids'] == missing, 'HISTORY_MISSING_IDS_MISMATCH')
    expected = 'complete' if not missing else 'missing' if not entries else 'partial'
    require(value['completeness'] == expected and type(value['durations_complete']) is bool
            and value['durations_complete'] == (not missing and all(e['created_to_end_ms'] is not None
                                                and e['started_to_end_ms'] is not None for e in entries.values())),
            'HISTORY_COMPLETENESS_MISMATCH')
    return {'query_id': qid, 'observed_at': observed, 'entries': entries, 'missing': missing}


def source_terminal(result, receipt, files):
    """The producer initially writes result=failed even while it is running."""
    if result is None or receipt['outcome'] == 'incomplete' or result.get('result') in ('running', 'interrupted'):
        return False
    trace = result.get('trace_id')
    task = 'workflow-' + receipt['run_token']
    if type(trace) is not str or not TOKEN.fullmatch(trace) or result.get('task_id') != task or 'spans.jsonl' not in files:
        return False
    lines = files['spans.jsonl'].splitlines()
    require(len(lines) <= 64, 'SPAN_COUNT_LIMIT')
    spans = [strict_json(line) for line in lines]
    roots = [s for s in spans if type(s) is dict and s.get('name') == 'bear.query.assess']
    if len(roots) != 1:
        return False
    root = roots[0]
    if (root.get('trace_id') != trace or root.get('attributes', {}).get('task_id') != task
            or type(root.get('start_time_unix_nano')) is not int or type(root.get('end_time_unix_nano')) is not int
            or not 0 < root['start_time_unix_nano'] <= root['end_time_unix_nano']):
        return False
    if result['result'] == 'passed':
        return (result.get('checkpoint') == 'completed-in-memory' and root.get('status') == 'OK'
                and type(result.get('saved_otel_spans_verified')) is int and type(result.get('audit_records')) is list
                and result['saved_otel_spans_verified'] == len(result['audit_records']) > 0)
    if result.get('error_code') not in ('QUERY_OBSERVATION_PLAN_FOLLOWUP_OR_AUDIT_FAILED', 'QUERY_SAVED_TRACE_VERIFICATION_FAILED'):
        return False
    if root.get('status') != ('OK' if result['error_code'] == 'QUERY_SAVED_TRACE_VERIFICATION_FAILED' else 'ERROR'):
        return False
    if receipt['outcome'] == 'interrupted':
        if 'interruption.json' not in files:
            return False
        interruption = strict_json(files['interruption.json'])
        return (type(interruption) is dict and interruption.get('result') == 'interrupted'
                and type(interruption.get('signal')) is int and interruption['signal'] in (2, 15))
    return True


def project_run(evidence_token, files, hashes):
    require('observer-receipt.json' in files, 'OBSERVER_RECEIPT_MISSING')
    receipt = strict_json(files['observer-receipt.json'])
    keys = {'schema_version', 'run_token', 'outcome', 'server_max_run_seconds', 'cleanup_verified',
            'target_history_verified', 'requests', 'uncaptured_requests'}
    require(type(receipt) is dict and set(receipt) == keys and type(receipt['schema_version']) is int
            and receipt['schema_version'] == 1 and TOKEN.fullmatch(receipt.get('run_token', '')),
            'OBSERVER_RECEIPT_SCHEMA_MISMATCH')
    token = receipt['run_token']
    require(receipt['outcome'] in ('completed', 'failed', 'interrupted', 'incomplete')
            and type(receipt['server_max_run_seconds']) is int and receipt['server_max_run_seconds'] == 30
            and type(receipt['cleanup_verified']) is bool and type(receipt['target_history_verified']) is bool,
            'OBSERVER_RECEIPT_STATUS_MISMATCH')
    sequence = receipt['requests']
    require(type(sequence) is list and len(sequence) <= 7, 'OBSERVER_REQUEST_COUNT')
    captured, uncaptured, auxiliary = {}, 0, []
    for index, request in enumerate(sequence):
        require(type(request) is dict and set(request) == {'case_id', 'kind', 'query_id', 'state', 'response_complete'},
                'REQUEST_SCHEMA_MISMATCH')
        if index < 4:
            require((request['case_id'], request['kind']) == PAIRS[index], 'REQUEST_SEQUENCE_MISMATCH')
        else:
            require(request['case_id'] is None and request['kind'] in ('own_query_history', 'fixture_stats'), 'REQUEST_SEQUENCE_MISMATCH')
            if index == 4:
                require(request['kind'] == 'own_query_history', 'REQUEST_SEQUENCE_MISMATCH')
            else:
                require(request['kind'] not in auxiliary, 'REQUEST_SEQUENCE_MISMATCH')
                auxiliary.append(request['kind'])
        require(type(request['response_complete']) is bool, 'REQUEST_COMPLETENESS_MISMATCH')
        state(request['state'])
        if request['query_id'] is None:
            uncaptured += 1
            require(request['response_complete'] is False and request['state'] is None, 'UNCAPTURED_REQUEST_CONTRADICTION')
        else:
            qid = identifier(request['query_id'])
            require(qid not in captured, 'DUPLICATE_QUERY_ID')
            captured[qid] = {**request, 'request_index': index}
    require(integer(receipt['uncaptured_requests']) == uncaptured, 'UNCAPTURED_COUNT_MISMATCH')
    completed = (receipt['target_history_verified'] and bool(sequence)
                 and all(r['response_complete'] and r['state'] in ('FINISHED', 'FAILED') for r in sequence))
    require((receipt['outcome'] == 'completed') == receipt['cleanup_verified']
            and (not receipt['cleanup_verified'] or completed), 'OBSERVER_COMPLETION_CONTRADICTION')
    workflow = 'unobserved'
    result = None
    if 'result.json' in files:
        result = strict_json(files['result.json'])
        require(type(result) is dict and result.get('query_run_token') == token
                and result.get('scope') == 'fixed-two-row-fixture-only'
                and result.get('result') in ('passed', 'failed', 'running', 'interrupted'), 'WORKFLOW_IDENTITY_MISMATCH')
        workflow = result['result']
        require(workflow != 'passed' or receipt['cleanup_verified'] and result.get('cleanup_verified') is True,
                'WORKFLOW_COMPLETION_CONTRADICTION')
    snapshots = []
    if 'observation.json' in files:
        observation = strict_json(files['observation.json'])
        require(type(observation) is dict and type(observation.get('schema_version')) is int
                and observation['schema_version'] == 1 and observation.get('run_token') == token
                and observation.get('performance_gain') == 'unmeasured', 'OBSERVATION_IDENTITY_MISMATCH')
        timestamp(observation.get('observed_at'))
        cases = observation.get('cases')
        require(type(cases) is list and len(cases) == 2 and [c.get('id') for c in cases] == ['projection', 'filtered'],
                'OBSERVATION_CASE_MISMATCH')
        for case in cases:
            for kind in ('select', 'explain'):
                qid = case.get(kind, {}).get('query_id')
                require(qid in captured and (captured[qid]['case_id'], captured[qid]['kind']) == (case['id'], kind),
                        'OBSERVATION_QUERY_IDENTITY_MISMATCH')
        snapshots.append(history(observation.get('history'), captured))
    if 'own_query_history.json' in files:
        snapshots.append(history(strict_json(files['own_query_history.json']), captured))
    require(len({s['query_id'] for s in snapshots}) == len(snapshots), 'DUPLICATE_HISTORY_SNAPSHOT')
    snapshots.sort(key=lambda s: s['observed_at'])
    if receipt['target_history_verified']:
        require(snapshots and not snapshots[-1]['missing']
                and all(e['history_state'] in ('FINISHED', 'FAILED') for e in snapshots[-1]['entries'].values()),
                'VERIFIED_HISTORY_MISSING')
    # A terminal response cannot become a different terminal history state.
    for sample in snapshots:
        for q, entry in sample['entries'].items():
            response_state = captured[q]['state']
            require(response_state not in ('FINISHED', 'FAILED') or entry['history_state'] not in ('FINISHED', 'FAILED')
                    or response_state == entry['history_state'], 'HISTORY_RESPONSE_STATE_MISMATCH')
    source_hash = digest(hashes)
    common = {'evidence_token': evidence_token, 'query_run_token': token,
              'query_user': 'shirokuma-query-doctor-' + token, 'source_sha256': source_hash}
    query_rows = []
    counts = {'observed': 0, 'missing': 0, 'unobserved': 0, 'not_target': 0}
    latest = snapshots[-1] if snapshots else None
    for qid, request in captured.items():
        values = {'history_query_id': None, 'history_observed_at': None, 'history_state': None,
                  **dict.fromkeys(TIMES), **dict.fromkeys(DURATIONS)}
        status = 'not_target' if request['kind'] not in ('select', 'explain') else 'unobserved' if latest is None else 'missing'
        if status != 'not_target' and latest is not None:
            values.update(history_query_id=latest['query_id'], history_observed_at=latest['observed_at'])
            if qid in latest['entries']:
                values.update(latest['entries'][qid])
                status = 'observed'
        counts[status] += 1
        query_rows.append(make_row(QUERIES, 'query:' + qid, {**common, 'query_id': qid,
            'request_index': request['request_index'], 'case_id': request['case_id'], 'kind': request['kind'],
            'response_complete': request['response_complete'], 'response_state': request['state'], 'history_status': status, **values}))
    run_row = make_row(RUNS, 'run:' + token, {**common, 'provenance': PROVENANCE, 'workflow_result': workflow,
        'observer_outcome': receipt['outcome'], 'source_terminal': source_terminal(result, receipt, files), 'cleanup_verified': receipt['cleanup_verified'],
        'target_history_verified': receipt['target_history_verified'], 'server_max_run_seconds': 30,
        'request_count': len(sequence), 'captured_count': len(captured), 'uncaptured_count': uncaptured,
        'history_snapshot_count': len(snapshots), 'observed_history_count': counts['observed'],
        'history_missing_count': counts['missing'], 'history_unobserved_count': counts['unobserved'],
        'auxiliary_query_count': counts['not_target']})
    return run_row, query_rows


def collect(source=SOURCE):
    files, hashes = snapshot(source)
    rows, inventory, issues = {RUNS: [], QUERIES: []}, [], []
    seen_tokens, seen_queries = set(), set()
    for token, run_files in files.items():
        try:
            run, queries = project_run(token, run_files, hashes[token])
            require(run['query_run_token'] not in seen_tokens, 'DUPLICATE_QUERY_RUN_TOKEN')
            require(not ({q['query_id'] for q in queries} & seen_queries), 'QUERY_ID_REUSED_ACROSS_RUNS')
            seen_tokens.add(run['query_run_token'])
            seen_queries.update(q['query_id'] for q in queries)
            rows[RUNS].append(run)
            rows[QUERIES].extend(queries)
            if not run['source_terminal']:
                issues.append({'evidence_token': token, 'code': 'SOURCE_NOT_TERMINAL'})
            inventory.append({'evidence_token': token, 'status': 'projected' if run['source_terminal'] else 'pending_terminal', 'observer_outcome': run['observer_outcome'],
                              'captured_count': len(queries), 'uncaptured_count': run['uncaptured_count']})
        except Exception as error:
            code = error.code if isinstance(error, ImportFailure) else 'SOURCE_PROJECTION_INVALID'
            issues.append({'evidence_token': token, 'code': code})
            inventory.append({'evidence_token': token, 'status': 'invalid', 'code': code})
    return {'schema_version': 1, 'ready': not issues, 'inventory': inventory, 'issues': issues,
            'source_hashes': hashes, 'source_sha256': digest(hashes), 'rows': rows,
            'totals': {'inventory_runs': len(inventory), 'projected_runs': len(rows[RUNS]), 'captured_queries': len(rows[QUERIES]),
                       'uncaptured_requests': sum(r['uncaptured_count'] for r in rows[RUNS])}}


def recheck(plan, source=SOURCE):
    require(snapshot(source)[1] == plan['source_hashes'], 'SOURCE_CHANGED')


def marker(table):
    require(table in SCHEMAS, 'UNKNOWN_TABLE')
    return 'shirokuma-local-query-history-v1:' + table


def ddl(table):
    return ('CREATE TABLE agenthouse.' + table + ' (' + ', '.join(name + ' ' + typ for name, typ in SCHEMAS[table])
            + ") ENGINE=ReplacingMergeTree ORDER BY record_id COMMENT '" + marker(table) + "'")


def database_rows(raw):
    require(type(raw) is str and len(raw.encode()) <= 8 * 1024**2, 'DATABASE_RESPONSE_TOO_LARGE')
    lines = raw.splitlines()
    require(len(lines) <= 10000, 'DATABASE_ROW_LIMIT')
    return [strict_json(line) for line in lines if line]


class Store:
    def __init__(self, client, guard=lambda: None):
        self.client, self.guard = client, guard

    def inspect(self):
        self.guard()
        names = "('query_history_runs','query_history_queries')"
        tables = database_rows(self.client.query('SELECT name, engine, engine_full, sorting_key, primary_key, partition_key, '
            "sampling_key, create_table_query, comment FROM system.tables WHERE database='agenthouse' AND name IN " + names + SETTINGS))
        columns = database_rows(self.client.query('SELECT table, name, type, position, default_kind, default_expression FROM system.columns '
            "WHERE database='agenthouse' AND table IN " + names + ' ORDER BY table, position' + SETTINGS))
        seen = set()
        for table in tables:
            name = table.get('name')
            require(name in SCHEMAS and name not in seen and table.get('engine') == 'ReplacingMergeTree'
                    and table.get('sorting_key') == 'record_id' and table.get('primary_key') == 'record_id'
                    and table.get('comment') == marker(name), 'TABLE_OWNERSHIP_CONFLICT')
            require(table.get('partition_key') == '' and table.get('sampling_key') == ''
                    and re.fullmatch(r'ReplacingMergeTree\s+ORDER BY record_id(?:\s+SETTINGS index_granularity = 8192)?', table.get('engine_full', ''))
                    and type(table.get('create_table_query')) is str
                    and not re.search(r'\b(TTL|PROJECTION|INDEX|CONSTRAINT|CODEC|DEFAULT|MATERIALIZED|ALIAS)\b', table['create_table_query'], re.I),
                    'TABLE_ENGINE_CONFLICT')
            actual = [c for c in columns if c.get('table') == name]
            require([(c.get('name'), c.get('type')) for c in actual] == SCHEMAS[name]
                    and [c.get('position') for c in actual] == list(range(1, len(SCHEMAS[name]) + 1))
                    and all(c.get('default_kind') == '' and c.get('default_expression') == '' for c in actual), 'TABLE_SCHEMA_CONFLICT')
            seen.add(name)
        require(all(c.get('table') in seen for c in columns), 'TABLE_SCHEMA_CONFLICT')
        return seen

    def create(self, table):
        self.guard()
        self.client.query(ddl(table))

    def rows(self, table):
        require(table in SCHEMAS, 'UNKNOWN_TABLE')
        values = database_rows(self.client.query('SELECT * FROM agenthouse.' + table + SETTINGS))
        result, query_ids, run_tokens = {}, {}, {}
        for value in values:
            require(type(value) is dict and set(value) == {name for name, _ in SCHEMAS[table]}, 'DATABASE_ROW_SCHEMA_CONFLICT')
            identity = value['record_id']
            require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'DATABASE_ROW_ID_CONFLICT')
            require(value['payload_sha256'] == digest({k: v for k, v in value.items() if k != 'payload_sha256'}), 'DATABASE_ROW_HASH_CONFLICT')
            require(identity not in result or result[identity] == value, 'DATABASE_PHYSICAL_ROW_CONFLICT')
            token = value['query_run_token']
            require(type(token) is str and TOKEN.fullmatch(token) and value['query_user'] == 'shirokuma-query-doctor-' + token,
                    'DATABASE_QUERY_USER_CONFLICT')
            if table == QUERIES:
                qid = identifier(value['query_id'])
                require(identity == str(uuid.uuid5(NAMESPACE, 'query:' + qid))
                        and (qid not in query_ids or query_ids[qid] == token), 'DATABASE_QUERY_ID_CONFLICT')
                query_ids[qid] = token
            else:
                require(identity == str(uuid.uuid5(NAMESPACE, 'run:' + token))
                        and (token not in run_tokens or run_tokens[token] == value['evidence_token']), 'DATABASE_RUN_ID_CONFLICT')
                run_tokens[token] = value['evidence_token']
            result[identity] = value
        return result

    def insert(self, table, rows):
        require(table in SCHEMAS and 0 < len(rows) <= MAX_RUNS * 7, 'INVALID_INSERT')
        self.guard()
        self.client.query('INSERT INTO agenthouse.' + table + ' SETTINGS max_execution_time=20, max_threads=2, '
            'max_memory_usage=268435456 FORMAT JSONEachRow\n' + '\n'.join(canonical(row) for row in rows))


def apply(plan, store, progress, source=SOURCE):
    receipt = {'schema_version': 1, 'source_sha256': plan['source_sha256'], 'result': 'running', 'pending': None,
               'confirmed': [], 'inserted': {RUNS: 0, QUERIES: 0}, 'existing': {RUNS: 0, QUERIES: 0},
               'source_unchanged': False, 'readback_verified': False}
    def save():
        progress(strict_json(canonical(receipt)))
    try:
        save()
        require(plan['ready'] and collect(source) == plan, 'INVENTORY_OR_PLAN_INVALID')
        present = store.inspect()
        original = {t: store.rows(t) if t in present else {} for t in SCHEMAS}
        for table, wanted in plan['rows'].items():
            require(all(row['record_id'] not in original[table] or original[table][row['record_id']] == row for row in wanted),
                    'RETAINED_ROW_CONFLICT')
            receipt['existing'][table] = sum(row['record_id'] in original[table] for row in wanted)
        for table in SCHEMAS:
            if table not in present:
                recheck(plan, source)
                receipt['pending'] = {'operation': 'create_table', 'table': table, 'outcome': 'pending'}
                save()
                recheck(plan, source)
                store.create(table)
                require(table in store.inspect(), 'TABLE_READBACK_MISMATCH')
                receipt['confirmed'].append({**receipt['pending'], 'outcome': 'confirmed'})
                receipt['pending'] = None
                save()
        for table, wanted in plan['rows'].items():
            require(store.inspect() == set(SCHEMAS), 'TABLE_OWNERSHIP_CONFLICT')
            current = store.rows(table)
            require(all(current.get(k) == v for k, v in original[table].items()), 'RETAINED_ROW_CHANGED')
            require(all(row['record_id'] not in current or current[row['record_id']] == row for row in wanted), 'RETAINED_ROW_CONFLICT')
            missing = [row for row in wanted if row['record_id'] not in current]
            if missing:
                recheck(plan, source)
                receipt['pending'] = {'operation': 'insert', 'table': table, 'record_ids': [r['record_id'] for r in missing], 'outcome': 'pending'}
                save()
                recheck(plan, source)
                store.insert(table, missing)
                actual = store.rows(table)
                require(all(actual.get(row['record_id']) == row for row in wanted)
                        and all(actual.get(k) == v for k, v in current.items()), 'INSERT_READBACK_MISMATCH')
                recheck(plan, source)
                receipt['inserted'][table] = len(missing)
                receipt['confirmed'].append({**receipt['pending'], 'outcome': 'confirmed'})
                receipt['pending'] = None
                save()
        require(store.inspect() == set(SCHEMAS), 'TABLE_READBACK_MISMATCH')
        for table, wanted in plan['rows'].items():
            actual = store.rows(table)
            require(all(actual.get(row['record_id']) == row for row in wanted)
                    and all(actual.get(k) == v for k, v in original[table].items()), 'FINAL_READBACK_MISMATCH')
        recheck(plan, source)
        receipt.update(result='passed', source_unchanged=True, readback_verified=True)
        save()
        return receipt
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt['failure_code'] = error.code if isinstance(error, ImportFailure) else 'IMPORT_OPERATION_FAILED'
        if receipt['pending'] is not None:
            receipt['pending']['outcome'] = 'unknown'
        save()
        raise


def private_write(path, value):
    directory(path.parent, private=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def importer_lock():
    directory(STATE, private=True)
    path = STATE / 'import.lock'
    require(not path.is_symlink(), 'UNSAFE_LOCK')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600, 'NONPRIVATE_LOCK')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportFailure('IMPORT_ALREADY_RUNNING') from None
        yield
    finally:
        os.close(fd)


def validate_runtime(module, owned):
    require(owned is not None and owned.get('State', {}).get('Running') is True, 'AGENTHOUSE_NOT_READY')
    config, host, mounts = owned.get('Config', {}), owned.get('HostConfig', {}), owned.get('Mounts', [])
    expected = {'Type': 'volume', 'Name': module.VOLUME, 'Destination': '/var/lib/clickhouse', 'RW': True}
    require(config.get('Image') == module.IMAGE and config.get('Labels', {}).get(module.LABEL) == 'agenthouse-local'
            and host.get('PortBindings') == {'8123/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18123'}]}
            and host.get('NanoCpus') == 2_000_000_000 and host.get('Memory') == 4 * 1024**3
            and len(mounts) == 1 and all(mounts[0].get(key) == val for key, val in expected.items()), 'AGENTHOUSE_SCOPE_CONFLICT')


def runtime_store():
    spec = importlib.util.spec_from_file_location('query_history_agenthouse', HERE.parent / 'agenthouse/manage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_context()
    original = module.inspect_owned('container', module.NAME)
    validate_runtime(module, original)
    require(module.inspect_owned('volume', module.VOLUME) is not None, 'AGENTHOUSE_VOLUME_MISSING')
    path = module.STATE / 'credential.json'
    directory(path.parent, private=True)
    read_file(path)
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, 'NONPRIVATE_CREDENTIAL')
    def guard():
        current = module.inspect_owned('container', module.NAME)
        validate_runtime(module, current)
        require(current['Id'] == original['Id'], 'AGENTHOUSE_IDENTITY_CHANGED')
    return Store(module.Client(), guard)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inventory', 'plan', 'apply'), nargs='?', default='inventory')
    args = parser.parse_args(argv)
    output = None
    try:
        directory(STATE.parent)
        STATE.mkdir(mode=0o700, exist_ok=True)
        directory(STATE, private=True)
        output = STATE / uuid.uuid4().hex
        output.mkdir(mode=0o700)
        with importer_lock():
            plan = collect()
            private_write(output / 'plan.json', plan)
            require(plan['ready'], 'INVENTORY_NOT_READY')
            result = None
            if args.command == 'apply':
                sys.path.insert(0, str(HERE.parent))
                from local_session import interruption_handlers
                with interruption_handlers():
                    result = apply(plan, runtime_store(), lambda value: private_write(output / 'apply.json', value))
            print(canonical({'result': 'passed', 'mode': args.command, **plan['totals'],
                             'inserted': result['inserted'] if result else None, 'run_token': output.name}))
        return 0
    except BaseException as error:
        code = error.code if isinstance(error, ImportFailure) else 'IMPORT_OPERATION_FAILED'
        if output is not None:
            private_write(output / 'failure.json', {'result': 'failed', 'failure_code': code})
        print(canonical({'result': 'failed', 'failure_code': code, 'run_token': output.name if output else None}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
