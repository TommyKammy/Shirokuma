#!/usr/bin/env python3
"""Project every retained local SF10 receipt into two additive AgentHouse tables."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / 'tmp/local-experiment/benchmark'
STATE = ROOT / 'tmp/local-experiment/benchmark-events'
MAX_RUNS, MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 128, 160, 16 * 1024**2, 256 * 1024**2
MAX_DATABASE_ROWS = 10000
IDENTITY = uuid.UUID('94e3697c-5f5c-5918-a577-76050733a040')
TOKEN = re.compile(r'[0-9a-f]{32}')
HASH = re.compile(r'[0-9a-f]{64}')
QUERY_ID = re.compile(r'\d{8}_\d{6}_\d{5}_[a-z0-9]{5}')
SIDES = ('reference', 'iceberg')
RUNS, QUERIES = 'benchmark_runs', 'benchmark_queries'
SCHEMAS = {
    RUNS: [
        ('record_id', 'UUID'), ('payload_sha256', 'FixedString(64)'),
        ('run_token', 'String'), ('source_sha256', 'FixedString(64)'),
        ('source_schema_version', 'UInt16'), ('result', 'LowCardinality(String)'),
        ('started_at', "Nullable(DateTime64(3, 'UTC'))"),
        ('completed_at', "Nullable(DateTime64(3, 'UTC'))"),
        ('wall_seconds', 'Nullable(Float64)'), ('scale_factor', 'UInt16'),
        ('expected_pairs', 'UInt16'), ('recorded_pairs', 'UInt16'),
        ('comparison_unknown_pairs', 'UInt16'), ('observed_query_sides', 'UInt16'),
        ('tables_recorded', 'UInt8'), ('errors_recorded', 'UInt16'),
        ('fixture_unchanged', 'Nullable(Bool)'), ('query_drain_verified', 'Nullable(Bool)'),
        ('cleanup_sql_verified', 'Nullable(Bool)'), ('trino_version', 'String'),
        ('input_manifest_sha256', 'Nullable(FixedString(64))'),
        ('environment_sha256', 'Nullable(FixedString(64))'),
        ('provenance_status', 'LowCardinality(String)'), ('provenance_group', 'String'),
    ],
    QUERIES: [
        ('record_id', 'UUID'), ('payload_sha256', 'FixedString(64)'),
        ('run_token', 'String'), ('query_number', 'UInt8'), ('side', 'LowCardinality(String)'),
        ('query_id', 'String'), ('receipt_sha256', 'FixedString(64)'),
        ('comparison_status', 'LowCardinality(String)'), ('result_rows', 'UInt64'),
        ('wall_seconds', 'Nullable(Float64)'), ('engine_state', 'Nullable(String)'),
        ('engine_elapsed_ms', 'Nullable(UInt64)'), ('cpu_ms', 'Nullable(UInt64)'),
        ('processed_rows', 'Nullable(UInt64)'), ('processed_bytes', 'Nullable(UInt64)'),
        ('peak_memory_bytes', 'Nullable(UInt64)'), ('spilled_bytes', 'Nullable(UInt64)'),
        ('sql_sha256', 'Nullable(FixedString(64))'),
        ('provenance_status', 'LowCardinality(String)'),
    ],
}
STATS = {'engine_elapsed_ms': 'elapsedTimeMillis', 'cpu_ms': 'cpuTimeMillis',
         'processed_rows': 'processedRows', 'processed_bytes': 'processedBytes',
         'peak_memory_bytes': 'peakMemoryBytes', 'spilled_bytes': 'spilledBytes'}
SELECT_SETTINGS = (' SETTINGS max_execution_time=20, max_threads=2, max_memory_usage=268435456, '
                   'max_result_rows=10000, max_result_bytes=8388608, result_overflow_mode=\'throw\', '
                   'output_format_json_quote_64bit_integers=0 FORMAT JSONEachRow')


class ImportError(RuntimeError):
    """Only a fixed code is exposed; source rows, SQL and backend errors stay private."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ImportError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ImportError('NONFINITE_JSON')))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ImportError):
            raise
        raise ImportError('INVALID_JSON') from None


def directory(path, private=False):
    """Check every existing path component, including the fixed root's parents."""
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
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and not before.st_mode & 0o022, 'UNSAFE_SOURCE_FILE')
        require(before.st_size <= MAX_FILE_BYTES, 'SOURCE_FILE_TOO_LARGE')
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(data) <= MAX_FILE_BYTES and (before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns), 'SOURCE_CHANGED')
    return data


def snapshot(source=SOURCE):
    directory(source)
    entries = sorted(source.iterdir())
    require(len(entries) <= 256, 'SOURCE_INVENTORY_TOO_LARGE')
    tokens = [path for path in entries if TOKEN.fullmatch(path.name)]
    require(len(tokens) <= MAX_RUNS, 'TOO_MANY_RUNS')
    files, issues, total = {}, [], 0
    for path in tokens:
        token = path.name
        try:
            directory(path)
            children = sorted(path.iterdir())
            require(len(children) <= MAX_FILES, 'TOO_MANY_SOURCE_FILES')
            files[token] = {}
            for child in children:
                require(re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', child.name), 'UNSAFE_SOURCE_NAME')
                data = read_file(child)
                total += len(data)
                require(total <= MAX_TOTAL_BYTES, 'SOURCE_TOTAL_TOO_LARGE')
                files[token][child.name] = data
        except (ImportError, OSError) as error:
            issues.append({'run_token': token, 'code': error.code if isinstance(error, ImportError) else 'SOURCE_UNREADABLE'})
    hashes = {token: {name: digest(data) for name, data in sorted(items.items())}
              for token, items in files.items()}
    return {'files': files, 'hashes': hashes, 'issues': issues,
            'inventory': [path.name for path in tokens],
            'non_run_entries': len(entries) - len(tokens)}


def integer(value, maximum=2**64 - 1):
    require(type(value) is int and 0 <= value <= maximum, 'INVALID_INTEGER')
    return value


def number(value):
    require(type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 86400000, 'INVALID_DURATION')
    return float(value)


def optional_bool(value):
    require(value is None or type(value) is bool, 'INVALID_BOOLEAN')
    return value


def stamp(value):
    if value is None:
        return None
    require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value), 'INVALID_TIMESTAMP')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d %H:%M:%S.000')
    except ValueError:
        raise ImportError('INVALID_TIMESTAMP') from None


def row(table, identity, values):
    result = {'record_id': str(uuid.uuid5(IDENTITY, identity)), **values}
    result['payload_sha256'] = digest(result)
    require(set(result) == {name for name, _ in SCHEMAS[table]}, 'PROJECTION_SCHEMA_ERROR')
    return result


def benchmark_module():
    spec = importlib.util.spec_from_file_location('benchmark_event_source', HERE.parent / 'benchmark/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_run(token, files, hashes):
    require('summary.json' in files, 'SUMMARY_MISSING')
    summary = strict_json(files['summary.json'])
    require(type(summary) is dict and summary.get('schema_version') == 1
            and type(summary['schema_version']) is int and summary.get('run_token') == token
            and summary.get('scope') == 'local-experiment-only'
            and summary.get('query_user') == 'shirokuma-local-tpch-' + token
            and summary.get('scale_factor') == 10 and type(summary['scale_factor']) is int
            and summary.get('queries_expected') == 22 and type(summary['queries_expected']) is int
            and summary.get('official_tpc_result') is False, 'SUMMARY_IDENTITY_MISMATCH')
    require(summary.get('result') in {'passed', 'failed', 'running'}, 'INVALID_RESULT')
    require(summary['result'] != 'running' and summary.get('completed_at') is not None, 'RUN_INCOMPLETE')
    require(summary.get('version') == [['483']], 'VERSION_UNOBSERVED')
    started, completed = stamp(summary.get('started_at')), stamp(summary.get('completed_at'))
    require(started is not None and completed >= started, 'INVALID_TIMESTAMP_ORDER')
    seconds = (datetime.fromisoformat(completed) - datetime.fromisoformat(started)).total_seconds()
    tables, comparisons, errors = summary.get('tables'), summary.get('queries'), summary.get('errors')
    require(type(tables) is list and len(tables) <= 8 and type(comparisons) is list and len(comparisons) <= 22
            and type(errors) is list and len(errors) <= 1000, 'INVALID_SUMMARY_COLLECTION')
    benchmark = benchmark_module()
    seen_tables = set()
    for table in tables:
        require(type(table) is dict and table.get('table') in benchmark.TABLES
                and table['table'] not in seen_tables, 'INVALID_TABLE_RECORD')
        name = table['table']
        require(table.get('relation') == f'iceberg.local_experiment.tpch_{token}_{name}'
                and table.get('rows') == benchmark.SF10_COUNTS[name]
                and type(table['rows']) is int, 'INVALID_TABLE_RECORD')
        seen_tables.add(name)
    by_query = {}
    for comparison in comparisons:
        require(type(comparison) is dict and re.fullmatch(r'q(?:0[1-9]|1[0-9]|2[0-2])', comparison.get('query', ''))
                and comparison['query'] not in by_query, 'INVALID_QUERY_RECORD')
        require(type(comparison.get('comparison')) is dict
                and comparison['comparison'].get('matched') is True, 'INVALID_COMPARISON')
        by_query[comparison['query']] = comparison
    query_rows, captured, sql_hashes, result_objects = [], set(), {}, {}
    for name, data in sorted(files.items()):
        match = re.fullmatch(r'q(0[1-9]|1[0-9]|2[0-2])-(reference|iceberg)\.json', name)
        if not match:
            require(not re.match(r'q\d.*-(?:reference|iceberg)\.json$', name), 'INVALID_QUERY_ARTIFACT')
            continue
        q, side = 'q' + match[1], match[2]
        value = strict_json(data)
        require(type(value) is dict and QUERY_ID.fullmatch(value.get('query_id', ''))
                and value['query_id'] not in captured, 'INVALID_QUERY_IDENTITY')
        captured.add(value['query_id'])
        columns, rows, kinds = value.get('columns'), value.get('rows'), value.get('column_types')
        require(type(columns) is list and 1 <= len(columns) <= 64 and type(rows) is list and len(rows) <= 100000
                and type(kinds) is list and len(kinds) == len(columns)
                and all(type(c) is str and 0 < len(c) <= 128 for c in columns + kinds)
                and all(type(r) is list and len(r) == len(columns) for r in rows), 'INVALID_RESULT_SHAPE')
        stats = value.get('stats', {})
        require(type(stats) is dict and stats.get('state') in {None, 'FINISHED', 'FAILED', 'CANCELED'}, 'INVALID_QUERY_STATS')
        wall = number(value['wall_seconds']) if value.get('wall_seconds') is not None else None
        comparison = by_query.get(q)
        if comparison:
            require(comparison.get(side + '_query_id') == value['query_id']
                    and comparison.get(side + '_stats', {}) == stats
                    and comparison.get(side + '_wall_seconds') == value.get('wall_seconds'), 'SUMMARY_QUERY_MISMATCH')
        sql_hash = comparison.get(side + '_sql_sha256') if comparison else None
        if sql_hash is not None:
            require(type(sql_hash) is str and HASH.fullmatch(sql_hash), 'INVALID_SQL_HASH')
            sql_name = q + '-' + side + '.sql'
            require(sql_name in files and files[sql_name].endswith(b';\n')
                    and digest(files[sql_name][:-2]) == sql_hash, 'SQL_HASH_MISMATCH')
            # The run token is not a query variant. All other literal SQL differences remain in the group.
            sql_hashes[q + '-' + side] = digest(files[sql_name].replace(token.encode(), b'<run-token>'))
        metrics = {key: integer(stats[field]) if stats.get(field) is not None else None for key, field in STATS.items()}
        query_rows.append(row(QUERIES, token + ':' + q + ':' + side, {
            'run_token': token, 'query_number': int(match[1]), 'side': side,
            'query_id': value['query_id'], 'receipt_sha256': hashes[name],
            'comparison_status': 'matched' if comparison else 'unobserved', 'result_rows': len(rows),
            'wall_seconds': wall, 'engine_state': stats.get('state'), **metrics,
            'sql_sha256': sql_hash, 'provenance_status': 'recorded' if sql_hash else 'unknown',
        }))
        result_objects[q + '-' + side] = value
    for q, comparison in by_query.items():
        require(all(q + '-' + side in result_objects for side in SIDES), 'QUERY_RESULT_MISSING')
        try:
            checked = benchmark.compare_results(*(result_objects[q + '-' + side] for side in SIDES))
        except (benchmark.BenchmarkError, KeyError, TypeError, ValueError):
            raise ImportError('RESULT_COMPARISON_MISMATCH') from None
        require(comparison['comparison'].get('rows') == checked['rows']
                and type(comparison['comparison']['rows']) is int, 'COMPARISON_COUNT_MISMATCH')
    cleanup, drain = summary.get('cleanup'), summary.get('query_drain')
    cleanup_verified = None
    if cleanup is not None:
        require(type(cleanup) is dict, 'INVALID_CLEANUP')
        allowed = {f'tpch_{token}_{name}' for name in benchmark.TABLES}
        for field in ('removed', 'remaining'):
            if field in cleanup:
                require(type(cleanup[field]) is list and len(set(cleanup[field])) == len(cleanup[field])
                        and all(name in allowed for name in cleanup[field]), 'INVALID_CLEANUP')
        if 'errors' in cleanup and 'remaining' in cleanup:
            require(type(cleanup['errors']) is list, 'INVALID_CLEANUP')
            cleanup_verified = not cleanup['errors'] and not cleanup['remaining']
        elif cleanup.get('status') == 'deferred':
            cleanup_verified = False
    require(drain is None or type(drain) is dict, 'INVALID_QUERY_DRAIN')
    drained = optional_bool(drain.get('verified')) if drain else None
    if drained:
        require(drain.get('user') == 'shirokuma-local-tpch-' + token and drain.get('active') == []
                and drain.get('errors') == [], 'INVALID_QUERY_DRAIN')
    fixture = optional_bool(summary.get('fixture_unchanged'))
    if fixture:
        require(summary.get('fixture_before', {}).get('rows') == benchmark.FIXTURE_ROWS
                and summary.get('fixture_after', {}).get('rows') == benchmark.FIXTURE_ROWS, 'FIXTURE_CLAIM_MISMATCH')
    if summary['result'] == 'passed':
        require(len(tables) == 8 and len(by_query) == 22 and len(query_rows) == 44
                and not errors and fixture is True and drained is True and cleanup_verified is True
                and all(q['engine_state'] == 'FINISHED' for q in query_rows), 'FALSE_PASS_CLAIM')
    inputs, environment, adaptations = summary.get('inputs'), summary.get('environment'), summary.get('runtime_query_adaptations')
    require(inputs is None or type(inputs) is dict, 'INVALID_INPUT_METADATA')
    require(environment is None or type(environment) is dict, 'INVALID_ENVIRONMENT_METADATA')
    require(adaptations is None or type(adaptations) is list, 'INVALID_ADAPTATION_METADATA')
    known = bool(query_rows) and len(sql_hashes) == len(query_rows) and inputs is not None and adaptations is not None
    provenance = {'inputs': inputs, 'adaptations': adaptations, 'sql': sql_hashes, 'version': '483'}
    group = digest(provenance) if known else 'unknown:' + token
    run_row = row(RUNS, token + ':run', {
        'run_token': token, 'source_sha256': digest(hashes), 'source_schema_version': 1,
        'result': summary['result'], 'started_at': started, 'completed_at': completed, 'wall_seconds': seconds,
        'scale_factor': 10, 'expected_pairs': 22, 'recorded_pairs': len(by_query),
        'comparison_unknown_pairs': 22 - len(by_query), 'observed_query_sides': len(query_rows),
        'tables_recorded': len(tables), 'errors_recorded': len(errors),
        'fixture_unchanged': fixture, 'query_drain_verified': drained, 'cleanup_sql_verified': cleanup_verified,
        'trino_version': '483', 'input_manifest_sha256': digest(inputs) if inputs is not None else None,
        'environment_sha256': digest(environment) if environment is not None else None,
        'provenance_status': 'recorded' if known else 'unknown', 'provenance_group': group,
    })
    return run_row, query_rows


def collect(source=SOURCE):
    snap = snapshot(source)
    rows, issues, inventory = {RUNS: [], QUERIES: []}, list(snap['issues']), []
    invalid = {item['run_token'] for item in issues}
    for token in snap['inventory']:
        if token in invalid:
            inventory.append({'run_token': token, 'state': 'invalid'})
            continue
        try:
            run, queries = project_run(token, snap['files'][token], snap['hashes'][token])
            rows[RUNS].append(run)
            rows[QUERIES].extend(queries)
            inventory.append({'run_token': token, 'state': run['result'],
                              'recorded_pairs': run['recorded_pairs'], 'observed_query_sides': len(queries),
                              'comparison_unknown_pairs': run['comparison_unknown_pairs'],
                              'provenance_status': run['provenance_status']})
        except (ImportError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
            code = error.code if isinstance(error, ImportError) else 'MALFORMED_RECEIPT'
            issues.append({'run_token': token, 'code': code})
            inventory.append({'run_token': token, 'state': 'incomplete' if code in {'RUN_INCOMPLETE', 'SUMMARY_MISSING'} else 'invalid'})
    require(len({r['query_id'] for r in rows[QUERIES]}) == len(rows[QUERIES]), 'CROSS_RUN_QUERY_ID_COLLISION')
    return {'schema_version': 1, 'source': 'retained-local-tpch-sf10', 'ready': bool(inventory) and not issues,
            'source_hashes': snap['hashes'], 'source_inventory': snap['inventory'],
            'source_sha256': digest(snap['hashes']), 'non_run_entries': snap['non_run_entries'],
            'inventory': inventory, 'issues': issues, 'rows': rows,
            'totals': {'runs': len(inventory), 'passed': sum(x['state'] == 'passed' for x in inventory),
                       'failed': sum(x['state'] == 'failed' for x in inventory),
                       'invalid': sum(x['state'] == 'invalid' for x in inventory),
                       'incomplete': sum(x['state'] == 'incomplete' for x in inventory),
                       'expected_pairs_valid_runs': 22 * len(rows[RUNS]),
                       'recorded_pairs': sum(r['recorded_pairs'] for r in rows[RUNS]),
                       'comparison_unknown_pairs': sum(r['comparison_unknown_pairs'] for r in rows[RUNS]),
                       'observed_query_sides': len(rows[QUERIES])}}


def recheck(plan, source=SOURCE):
    current = snapshot(source)
    require(not current['issues'] and current['hashes'] == plan['source_hashes']
            and current['inventory'] == plan['source_inventory'], 'SOURCE_CHANGED')


def marker(table):
    require(table in SCHEMAS, 'UNKNOWN_TABLE')
    return 'shirokuma-local-benchmark-events:v1:' + table


def ddl(table):
    require(table in SCHEMAS, 'UNKNOWN_TABLE')
    return ('CREATE TABLE IF NOT EXISTS agenthouse.' + table + ' (\n'
            + ',\n'.join(name + ' ' + kind for name, kind in SCHEMAS[table])
            + ") ENGINE = ReplacingMergeTree ORDER BY record_id COMMENT '" + marker(table) + "'")


def database_rows(text):
    require(type(text) is str and len(text.encode()) <= 8 * 1024**2, 'DATABASE_RESPONSE_TOO_LARGE')
    lines = text.splitlines()
    require(len(lines) <= MAX_DATABASE_ROWS, 'DATABASE_RESPONSE_TOO_LARGE')
    return [strict_json(line) for line in lines if line.strip()]


class Store:
    def __init__(self, client):
        self.client = client

    def inspect(self):
        names = "('benchmark_runs','benchmark_queries')"
        tables = database_rows(self.client.query("SELECT name, engine, engine_full, sorting_key, primary_key, "
            "partition_key, sampling_key, create_table_query, comment FROM system.tables "
            "WHERE database='agenthouse' AND name IN " + names + SELECT_SETTINGS))
        columns = database_rows(self.client.query("SELECT table, name, type, position, default_kind, default_expression FROM system.columns "
            "WHERE database='agenthouse' AND table IN " + names + ' ORDER BY table, position' + SELECT_SETTINGS))
        seen = set()
        for table in tables:
            name = table.get('name')
            require(name in SCHEMAS and name not in seen and table.get('engine') == 'ReplacingMergeTree'
                    and table.get('sorting_key') == 'record_id' and table.get('comment') == marker(name), 'TABLE_OWNERSHIP_CONFLICT')
            require(table.get('primary_key') == 'record_id' and table.get('partition_key') == ''
                    and table.get('sampling_key') == ''
                    and re.fullmatch(r'ReplacingMergeTree\s+ORDER BY record_id(?:\s+SETTINGS index_granularity = 8192)?',
                                     table.get('engine_full', ''))
                    and type(table.get('create_table_query')) is str
                    and not re.search(r'\b(TTL|PROJECTION|INDEX|CONSTRAINT|CODEC|DEFAULT|MATERIALIZED|ALIAS)\b',
                                      table['create_table_query'], re.IGNORECASE), 'TABLE_ENGINE_CONFLICT')
            seen.add(name)
            actual = [(c.get('name'), c.get('type')) for c in columns if c.get('table') == name]
            require(actual == SCHEMAS[name] and all(c.get('default_kind') == '' and c.get('default_expression') == ''
                                                   for c in columns if c.get('table') == name), 'TABLE_SCHEMA_CONFLICT')
        require(all(c.get('table') in seen for c in columns), 'TABLE_SCHEMA_CONFLICT')
        return seen

    def create(self, table):
        self.client.query(ddl(table))

    def rows(self, table):
        require(table in SCHEMAS, 'UNKNOWN_TABLE')
        # Read physical rows: FINAL alone could hide conflicting retained payloads with the same ID.
        values = database_rows(self.client.query('SELECT * FROM agenthouse.' + table + SELECT_SETTINGS))
        result = {}
        for value in values:
            require(type(value) is dict and set(value) == {name for name, _ in SCHEMAS[table]}, 'DATABASE_ROW_SCHEMA_CONFLICT')
            # JSON prints an integral Float64 as e.g. 1, whereas Python originally encoded 1.0.
            for name, kind in SCHEMAS[table]:
                if 'Float64' in kind and value[name] is not None:
                    value[name] = number(value[name])
            identity = value['record_id']
            require(type(identity) is str and str(uuid.UUID(identity)) == identity, 'DATABASE_ROW_ID_CONFLICT')
            require(value['payload_sha256'] == digest({k: v for k, v in value.items() if k != 'payload_sha256'}), 'DATABASE_ROW_HASH_CONFLICT')
            require(identity not in result or result[identity] == value, 'DATABASE_ROW_CONFLICT')
            result[identity] = value
        return result

    def insert(self, table, rows):
        require(table in SCHEMAS and 0 < len(rows) <= MAX_RUNS * 44, 'INVALID_INSERT')
        self.client.query('INSERT INTO agenthouse.' + table + ' FORMAT JSONEachRow\n'
                          + '\n'.join(canonical(value) for value in rows))


def apply(plan, store, progress, source=SOURCE):
    """No rollback/deletion: failed writes retain an unknown batch for exact retry."""
    receipt = {'schema_version': 1, 'source_sha256': plan['source_sha256'], 'result': 'running',
               'pending': None, 'confirmed': [], 'inserted': {RUNS: 0, QUERIES: 0},
               'existing': {RUNS: 0, QUERIES: 0}, 'readback_verified': False, 'source_unchanged': False}
    def save():
        progress(json.loads(canonical(receipt)))
    try:
        save()
        require(plan['ready'], 'INVENTORY_NOT_READY')
        # Regenerate the exact projection before trusting even a host-supplied in-memory plan.
        require(collect(source) == plan, 'PLAN_CHANGED')
        present = store.inspect()
        existing = {table: store.rows(table) if table in present else {} for table in SCHEMAS}
        for table, wanted in plan['rows'].items():
            for value in wanted:
                identity = value['record_id']
                require(identity not in existing[table] or existing[table][identity] == value, 'RETAINED_ROW_CONFLICT')
            receipt['existing'][table] = sum(v['record_id'] in existing[table] for v in wanted)
        for table in SCHEMAS:
            if table not in present:
                recheck(plan, source)
                receipt['pending'] = {'operation': 'create_table', 'table': table}
                save()
                recheck(plan, source)
                store.create(table)
                require(table in store.inspect(), 'TABLE_READBACK_MISMATCH')
                recheck(plan, source)
                receipt['confirmed'].append(receipt['pending'])
                receipt['pending'] = None
                save()
        for table, wanted in plan['rows'].items():
            # Recheck retained rows directly before the batch; the local CLI lock serializes this importer.
            fresh = store.rows(table)
            require(all(key in fresh and fresh[key] == value for key, value in existing[table].items()), 'RETAINED_ROW_CHANGED')
            for value in wanted:
                require(value['record_id'] not in fresh or fresh[value['record_id']] == value, 'RETAINED_ROW_CONFLICT')
            missing = [value for value in wanted if value['record_id'] not in fresh]
            if missing:
                recheck(plan, source)
                require(store.inspect() == set(SCHEMAS), 'TABLE_OWNERSHIP_CONFLICT')
                receipt['pending'] = {'operation': 'insert', 'table': table, 'record_ids': [v['record_id'] for v in missing]}
                save()
                recheck(plan, source)
                store.insert(table, missing)
                readback = store.rows(table)
                require(all(readback.get(v['record_id']) == v for v in wanted), 'INSERT_READBACK_MISMATCH')
                require(all(readback.get(key) == value for key, value in fresh.items()), 'RETAINED_ROW_CHANGED')
                recheck(plan, source)
                receipt['inserted'][table] = len(missing)
                receipt['confirmed'].append(receipt['pending'])
                receipt['pending'] = None
                save()
        require(store.inspect() == set(SCHEMAS), 'TABLE_READBACK_MISMATCH')
        for table, wanted in plan['rows'].items():
            readback = store.rows(table)
            require(all(readback.get(v['record_id']) == v for v in wanted), 'FINAL_READBACK_MISMATCH')
        receipt['readback_verified'] = True
        recheck(plan, source)
        receipt['source_unchanged'] = True
        receipt['result'] = 'passed'
        save()
    except BaseException as error:
        receipt['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        receipt['failure_code'] = error.code if isinstance(error, ImportError) else 'IMPORT_OPERATION_FAILED'
        receipt['pending_outcome'] = 'unknown' if receipt['pending'] else None
        save()
        raise
    return receipt


def private_write(path, value):
    directory(path.parent, private=True)
    require(not path.is_symlink(), 'UNSAFE_OUTPUT_PATH')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def importer_lock():
    directory(STATE, private=True)
    fd = os.open(STATE / 'import.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'UNSAFE_LOCK')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportError('IMPORT_ACTIVE') from None
        yield


def validate_runtime(module, owned):
    require(owned is not None and owned.get('State', {}).get('Running') is True, 'AGENTHOUSE_NOT_READY')
    config, host, mounts = owned.get('Config', {}), owned.get('HostConfig', {}), owned.get('Mounts', [])
    expected = {'Type': 'volume', 'Name': module.VOLUME, 'Destination': '/var/lib/clickhouse', 'RW': True}
    require(config.get('Image') == module.IMAGE
            and config.get('Labels', {}).get(module.LABEL) == 'agenthouse-local'
            and host.get('PortBindings') == {'8123/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18123'}]}
            and host.get('NanoCpus') == 2_000_000_000 and host.get('Memory') == 4 * 1024**3
            and len(mounts) == 1 and all(mounts[0].get(key) == value for key, value in expected.items()),
            'AGENTHOUSE_SCOPE_CONFLICT')


def runtime_store():
    spec = importlib.util.spec_from_file_location('benchmark_event_agenthouse', HERE.parent / 'agenthouse/manage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_context()
    owned = module.inspect_owned('container', module.NAME)
    validate_runtime(module, owned)
    require(module.inspect_owned('volume', module.VOLUME) is not None, 'AGENTHOUSE_VOLUME_MISSING')
    # Validate the existing credential's private path; never rotate credentials or start services.
    credential = module.STATE / 'credential.json'
    read_file(credential)
    require(stat.S_IMODE(credential.stat().st_mode) == 0o600, 'NONPRIVATE_CREDENTIAL')
    return Store(module.Client())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'apply'), nargs='?', default='plan')
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
                             'inserted': result['inserted'] if result else None,
                             'evidence': str(output.relative_to(ROOT))}))
        return 0
    except BaseException as error:
        code = error.code if isinstance(error, ImportError) else 'IMPORT_OPERATION_FAILED'
        if output is not None:
            private_write(output / 'failure.json', {'result': 'failed', 'failure_code': code})
        print(canonical({'result': 'failed', 'failure_code': code,
                         'evidence': str(output.relative_to(ROOT)) if output else None}))
        return 128 + error.signum if hasattr(error, 'signum') else 1


if __name__ == '__main__':
    raise SystemExit(main())
