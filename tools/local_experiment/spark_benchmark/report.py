#!/usr/bin/env python3
"""Read saved Spark benchmark evidence without SDK, database or runtime access.

This is historical receipt validation, not a fresh cluster or object-store
attestation. Every eligible terminal source yields four engine/case groups.
Failed and interrupted sources keep their validated partial measurements; they
never become successful comparisons. Missing measurements and times stay null.
Only fixed files are read. Source directories, modes, identities, content and
inventory are checked before and after projection, under an existing read lock.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
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
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.local_experiment.spark_benchmark import contract as C, workload as W

BASE = ROOT / 'tmp/local-experiment/spark-benchmark'
NATIVE_BASE = ROOT / 'tmp/local-experiment/spark-runs'
SCOPE = 'saved-spark-benchmark-evidence'
MAX_RUNS = 128
MAX_FILE = 4 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')
QUERY = re.compile(r'[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]+')
STATS = ('cpuTimeMillis', 'elapsedTimeMillis', 'physicalInputBytes', 'processedBytes',
         'processedRows', 'queuedTimeMillis', 'wallTimeMillis')
SOURCE_PATHS = {
    'tools/local_experiment/spark/run.py',
    'tools/local_experiment/spark_benchmark/client.py',
    'tools/local_experiment/spark_benchmark/contract.py',
    'tools/local_experiment/spark_benchmark/run.py',
    'tools/local_experiment/spark_benchmark/table.py',
    'tools/local_experiment/spark_benchmark/workload.py',
    'tools/local_experiment/spark_iceberg/build.py',
    'tools/local_experiment/spark_iceberg/remote_access.py',
    'tools/local_experiment/spark_iceberg/remote_contract.py',
}
LIMITS = [
    'HISTORICAL_SAVED_EVIDENCE_NOT_LIVE_ATTESTATION',
    'FIXED_TPCH_DERIVED_WORKLOAD_NOT_STANDARD_TPCH',
    'SHARED_HOST_AND_CACHE_NOT_COLD',
    'TRINO_FIRST_SPARK_SECOND_NO_RANDOMIZED_ORDER',
    'CLIENT_TIMINGS_HAVE_DIFFERENT_ENGINE_SCOPES',
    'NO_GENERAL_PERFORMANCE_OR_PRODUCTION_CLAIM',
    'IMPORT_TIMELINE_IS_NOT_SOURCE_SAMPLE_TIME',
]
FIXED_ROOT_LIMITS = [
    'LOCAL_EXPERIMENT_ONLY', 'FIXED_TPCH_DERIVED_WORKLOAD_NOT_STANDARD_TPCH',
    'ONE_PRECREATED_OWNED_TABLE', 'SHARED_HOST_AND_CACHE_NOT_COLD',
    'TRINO_FIRST_SPARK_SECOND_NO_RANDOMIZED_ORDER', 'NO_SPARK_CACHE_OR_PERSIST',
    'POLARIS_READ_ONLY_GRANTS_NOT_S3_READ_ONLY_ENFORCEMENT',
    'STATIC_BUCKET_SCOPED_S3_CREDENTIALS', 'NO_GENERAL_PERFORMANCE_OR_PRODUCTION_CLAIM',
]
TIMING_SCOPE = {
    'trino': 'host request start through complete result receipt; engine stats separate',
    'spark': 'spark.sql plus collect; normalization and oracle excluded',
    'spark_startup': 'SparkSession getOrCreate only; first query may include executor readiness',
    'native_total': 'native preflight through verified resource cleanup',
    'source_preparation': 'excluded; streams and file checks may warm shared caches',
}


class ReceiptError(ValueError):
    pass


def require(value, code):
    if not value:
        raise ReceiptError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def same(a, b):
    return canonical(a) == canonical(b)


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except ReceiptError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ReceiptError('INVALID_JSON') from None


def _stamp(value):
    require(type(value) is str and len(value) <= 40, 'INVALID_TIMESTAMP')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ReceiptError('INVALID_TIMESTAMP') from None
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, 'INVALID_TIMESTAMP')
    return parsed


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, 'INVALID_UUID')
    except (ValueError, AttributeError, TypeError):
        raise ReceiptError('INVALID_UUID') from None
    return value


def _integer(value, maximum=2**63-1, minimum=0):
    require(type(value) is int and minimum <= value <= maximum, 'INVALID_INTEGER')
    return value


def _path(path):
    for item in (path, *path.parents):
        require(not item.is_symlink(), 'UNSAFE_SOURCE_PATH')


def _directory(path):
    _path(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'NONPRIVATE_SOURCE_DIRECTORY')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_file(path):
    _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600,
                'NONPRIVATE_SOURCE_FILE')
        require(0 < before.st_size <= MAX_FILE, 'SOURCE_FILE_LIMIT')
        chunks, total = [], 0
        while True:
            part = os.read(fd, min(65536, MAX_FILE + 1 - total))
            if not part:
                break
            chunks.append(part); total += len(part)
            require(total <= MAX_FILE, 'SOURCE_FILE_LIMIT')
        require(_identity(before) == _identity(os.fstat(fd)) == _identity(path.lstat())
                and total == before.st_size, 'SOURCE_CHANGED')
        raw = b''.join(chunks)
        return raw, _identity(before)
    finally:
        os.close(fd)


@contextmanager
def _read_lock(output):
    path = output / 'run.lock'
    _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                and before.st_size == 0, 'UNSAFE_RUN_LOCK')
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReceiptError('RUN_ACTIVE') from None
        yield
        require(_identity(before) == _identity(os.fstat(fd)) == _identity(path.lstat()), 'SOURCE_CHANGED')
    finally:
        os.close(fd)


def _list(path):
    return sorted((p.name, p.lstat().st_mode, p.lstat().st_ino) for p in path.iterdir())


def _verified(value):
    require(type(value) is dict and type(value.get('verified')) is bool, 'INVALID_VERIFICATION')
    return value['verified']


def _stats(value):
    require(type(value) is dict and set(value) == set(STATS) | {'state'}
            and value['state'] == 'FINISHED', 'INVALID_QUERY_STATS')
    for key in STATS:
        if value[key] is not None:
            _integer(value[key])
    return deepcopy(value)


def _pod(pod, role, native, token):
    row = native.get('pods', {}).get(role)
    require(type(row) is dict and pod.get('kind') == 'Pod' and pod.get('apiVersion') == 'v1', 'POD_IDENTITY_MISMATCH')
    meta = pod['metadata']; ident = _uuid(meta['uid'])
    require(row['uid'] == ident and row['name'] == meta['name']
            and meta['namespace'] == 'shirokuma-spark-' + token
            and row['role'] == role and row['kind'] == 'Pod', 'POD_IDENTITY_MISMATCH')
    known = [x for x in native['known'] if x['kind'] == 'Pod' and x['name'] == meta['name']]
    require(len(known) == 1 and known[0]['uid'] == ident, 'POD_NOT_IN_KNOWN_JOURNAL')
    containers = pod['spec']['containers']
    require(type(containers) is list and len(containers) == 1
            and containers[0]['name'] == 'spark-' + role
            and containers[0]['image'] == native['profile_inputs']['image'], 'POD_CONTAINER_MISMATCH')
    resources = containers[0]['resources']
    require(type(resources) is dict and set(resources) == {'requests', 'limits'}, 'INVALID_RESOURCES')
    for kind in ('requests', 'limits'):
        row = resources[kind]
        require(type(row) is dict and set(row) == {'cpu', 'memory', 'ephemeral-storage'}
                and all(type(v) is str and re.fullmatch(r'[0-9]{1,6}(?:m|Ki|Mi|Gi)?', v)
                        for v in row.values()), 'INVALID_RESOURCES')
    return deepcopy(resources)


def _native_cleanup(native):
    cleanup = native['cleanup']
    if not _verified(cleanup):
        return False
    require(cleanup.get('deferred') in (None, []) and type(cleanup.get('deleted')) is list,
            'NATIVE_CLEANUP_INCONSISTENT')
    known = native.get('known', []) + native.get('automatic_endpoints', [])
    require(type(native.get('known')) is list and type(native.get('automatic_endpoints')) is list,
            'NATIVE_JOURNAL_INVALID')
    def identities(rows):
        require(len(rows) <= 32, 'NATIVE_JOURNAL_LIMIT')
        result = []
        for row in rows:
            require(type(row) is dict and type(row.get('kind')) is str and type(row.get('name')) is str,
                    'NATIVE_JOURNAL_INVALID')
            result.append((row['kind'], row['name'], _uuid(row['uid'])))
        require(len(result) == len(set(result)), 'NATIVE_JOURNAL_DUPLICATE')
        return set(result)
    require(identities(known) == identities(cleanup['deleted']), 'NATIVE_CLEANUP_INCONSISTENT')
    return True


def _validate(root, bundle, token):
    """Validate cross-file facts before projecting any user-visible values."""
    require(type(root) is dict and root.get('schema_version') == 1 and type(root['schema_version']) is int
            and root.get('run_token') == token and root.get('scope') == C.SCOPE
            and root.get('workload_id') == W.WORKLOAD_ID, 'ROOT_IDENTITY_INVALID')
    require(root.get('result') in ('passed', 'failed', 'interrupted', 'running'), 'ROOT_RESULT_INVALID')
    require(root['result'] != 'running' and root.get('finished_at') is not None, 'SOURCE_NOT_TERMINAL')
    require(_stamp(root['started_at']) <= _stamp(root['finished_at']), 'TIMESTAMP_ORDER_INVALID')
    require(type(root.get('source_hashes')) is dict and set(root['source_hashes']) == SOURCE_PATHS
            and all(type(v) is str and SHA.fullmatch(v) for v in root['source_hashes'].values()),
            'SOURCE_CODE_HASHES_INVALID')
    require(root.get('limits') == FIXED_ROOT_LIMITS, 'MEASUREMENT_CONDITIONS_INVALID')
    require(same(root.get('timing_scope'), TIMING_SCOPE), 'MEASUREMENT_CONDITIONS_INVALID')
    require(type(root.get('errors')) is list and len(root['errors']) <= 32, 'ROOT_ERRORS_INVALID')
    passed = root['result'] == 'passed'
    if not passed:
        require(bool(root.get('failure_code') or root.get('exit_error') or root['errors']), 'FAILURE_EVIDENCE_MISSING')
    table = root.get('table')
    require(type(table) is dict and type(table.get('schema_version')) is int
            and table['schema_version'] == 1 and table.get('run_token') == token,
            'TABLE_JOURNAL_INVALID')
    inputs = root.get('profile_inputs')
    if inputs is not None:
        C._inputs(inputs, token)
        require(same(table.get('sealed'), inputs['sealed']), 'SEALED_INPUT_MISMATCH')
        require(same(table.get('source_dataset'), inputs['sealed']['dataset']), 'SOURCE_DATASET_MISMATCH')
        validation = table['source_validation']
        require(same(validation, {'schema': [list(v) for v in zip(W.SOURCE_COLUMNS, W.RAW_SOURCE_COLUMN_TYPES)],
                               'row_count': inputs['sealed']['dataset']['row_count'],
                               'invalid_identity_rows': 0, 'invalid_numeric_rows': 0}), 'SOURCE_VALIDATION_INVALID')
        identity = table['identity']
        require(same(identity['identity'], inputs['sealed']['identity'])
                and identity['record_count'] == inputs['sealed']['dataset']['row_count']
                and identity['file_count'] == len(inputs['sealed']['data_files']), 'TABLE_IDENTITY_MISMATCH')
    else:
        require(not passed, 'SEALED_INPUT_MISSING')
    samples = table.get('measurements', [])
    require(type(samples) is list, 'MEASUREMENTS_INVALID')
    if 'trino_measurements' in root:
        require(same(root['trino_measurements'], samples), 'TRINO_MEASUREMENTS_MISMATCH')
    if inputs is None:
        require(not samples, 'UNSEALED_MEASUREMENTS')
    else:
        C.validate_measurements(samples, 'trino', token, inputs, partial=not passed)
    queries = root.get('queries', {})
    require(type(queries) is dict and set(queries) == {'worker', 'control'}
            and all(type(v) is list and len(v) <= 1024 for v in queries.values()), 'QUERY_JOURNAL_INVALID')
    seen, by_id = set(), {}
    for worker, rows in queries.items():
        for row in rows:
            require(type(row) is dict, 'QUERY_JOURNAL_INVALID')
            qid = row.get('query_id')
            if qid is not None:
                require(type(qid) is str and QUERY.fullmatch(qid) and qid not in seen, 'QUERY_ID_INVALID')
                seen.add(qid)
                if worker == 'worker': by_id[qid] = row
            if passed:
                require(row.get('response_complete') is True and row.get('result') == 'passed', 'QUERY_NOT_COMPLETE')
    for sample in samples:
        q = by_id.get(sample['query_id'])
        require(q is not None and q.get('result') == 'passed' and q.get('response_complete') is True
                and q.get('sql_sha256') == sample['sql_sha256'] and q.get('elapsed_ns') == sample['elapsed_ns']
                and same(q.get('stats'), sample['stats']), 'SAMPLE_QUERY_MISMATCH')
        _stats(sample['stats']); _stamp(q['started_at'])
    access = bundle.get('access.json')
    if access is not None:
        require(same(root.get('access'), access) and type(access.get('schema_version')) is int and access['schema_version'] == 1
                and access.get('run_token') == token and access.get('polaris_access') == 'read-only',
                'ACCESS_RECEIPT_MISMATCH')
        require(access.get('grants') in ([], ['TABLE_LIST'], ['TABLE_LIST', 'TABLE_READ_PROPERTIES']),
                'ACCESS_GRANTS_INVALID')
        if 'access_cleanup' in root:
            require(same(root['access_cleanup'], access.get('cleanup')), 'ACCESS_CLEANUP_MISMATCH')
        if access.get('table') is not None and inputs is not None:
            require(access['table'] == {k: inputs[k] for k in ('table_uuid', 'table_location')}, 'ACCESS_TABLE_MISMATCH')
    elif root.get('access') is not None and root['access'].get('events'):
        raise ReceiptError('ACCESS_RECEIPT_MISSING')
    tunnel = bundle.get('tunnel.json')
    if tunnel is not None:
        require(tunnel.get('port') == 18181 and tunnel.get('state') in ('starting', 'ready', 'verified', 'failed'),
                'TUNNEL_RECEIPT_INVALID')
        service = tunnel.get('service')
        require(type(service) is dict and service.get('name') == 'polaris'
                and service.get('namespace') == 'shirokuma-dev', 'TUNNEL_SERVICE_INVALID')
        _uuid(service['uid'])
        matches = [v for v in root.get('services', []) if v.get('name') == 'polaris']
        require(len(matches) == 1 and all(service[k] == matches[0][k] for k in ('name', 'namespace', 'uid', 'spec_sha256')),
                'TUNNEL_SERVICE_MISMATCH')
    native = bundle.get('native/summary.json')
    spark, resources = None, {'driver': None, 'executor': None, 'trino': None}
    native_clean = False
    if native is not None:
        require(root.get('spark_evidence') == 'tmp/local-experiment/spark-runs/' + token
                and type(native.get('schema_version')) is int and native['schema_version'] == 1 and native.get('run_token') == token
                and native.get('profile') == 'iceberg-benchmark' and native.get('mode') == 'success'
                and native.get('namespace') == 'shirokuma-spark-' + token
                and native.get('result') in ('passed', 'failed', 'interrupted'), 'NATIVE_IDENTITY_INVALID')
        require(inputs is not None and same(native.get('profile_inputs'), inputs), 'NATIVE_INPUTS_MISMATCH')
        if native.get('plan_sha256') is not None:
            require('native/plan.json' in bundle and digest(bundle['native/plan.json']) == native['plan_sha256'],
                    'NATIVE_PLAN_HASH_MISMATCH')
        native_clean = _native_cleanup(native)
        for role in ('driver', 'executor'):
            pod = bundle.get('native/' + role + '-pod.json')
            if pod is not None:
                resources[role] = _pod(pod, role, native, token)
            elif native.get('pods', {}).get(role) is not None:
                raise ReceiptError('POD_RECEIPT_MISSING')
        spark = native.get('output')
        if spark is not None:
            name = native.get('pods', {}).get('executor', {}).get('name', 'spark-' + token + '-exec-1')
            C.validate_output(spark, token, inputs, executor_pod_name=name)
        if 'spark_output' in root:
            require(same(root['spark_output'], spark), 'SPARK_OUTPUT_MISMATCH')
    elif root.get('spark_evidence') is not None or root.get('spark_output') is not None:
        raise ReceiptError('NATIVE_RECEIPT_MISSING')
    clean = _verified(root['cleanup'])
    drained = _verified(root['query_drain'])
    if drained:
        drain = root['query_drain']
        require(not drain.get('active') and not drain.get('errors') and not drain.get('cancel_errors')
                and drain.get('user') == 'shirokuma-local-iceberg-' + token, 'DRAIN_INCONSISTENT')
    if clean:
        require(same(root['cleanup'], table.get('cleanup')), 'TABLE_CLEANUP_MISMATCH')
        if table.get('created') is True:
            require(inputs is not None and root['cleanup'].get('table_uuid') == inputs['table_uuid']
                    and root['cleanup'].get('action') == 'same-uuid-drop', 'CLEANUP_IDENTITY_MISMATCH')
    if passed:
        require(clean and drained and root.get('spark_stopped') is True and not root['errors']
                and root.get('fixture_unchanged') is True and same(root.get('fixture_before'), root.get('fixture_after'))
                and root.get('fixture_before') is not None and table.get('pending') is None,
                'PASSED_ROOT_INCOMPLETE')
        require(access is not None and access.get('pending') is None and access.get('entities') == {}
                and access.get('bindings') == [] and access.get('policies') == {}
                and access['grants'] == ['TABLE_LIST', 'TABLE_READ_PROPERTIES']
                and _verified(access['cleanup']) and not access['cleanup'].get('deferred')
                and tunnel is not None and tunnel['state'] == 'verified', 'PASSED_ACCESS_INCOMPLETE')
        require(native is not None and native['result'] == 'passed' and native_clean
                and native.get('pending') is None and native.get('workload_verified') is True
                and native.get('driver_exit_code') == 0 and type(native['driver_exit_code']) is int
                and spark is not None and spark['result'] == 'passed'
                and all(resources[k] is not None for k in ('driver', 'executor')), 'PASSED_NATIVE_INCOMPLETE')
        actual = []
        for case in W.CASES:
            times = {engine: [v['elapsed_ns'] for v in rows if v['case_id'] == case]
                     for engine, rows in [('trino', samples), ('spark', spark['measurements'])]}
            actual.append({'case_id': case, 'expected_sha256': inputs['sealed']['dataset']['expected'][case]['sha256'],
                           'trino_elapsed_ns': times['trino'], 'spark_elapsed_ns': times['spark'],
                           'trino_repeat_median_ns': sorted(times['trino'][1:])[1],
                           'spark_repeat_median_ns': sorted(times['spark'][1:])[1]})
        comparison = {'verified': True, 'measurements': 16, 'snapshot_id': inputs['initial_snapshot_id'],
                      'table_uuid': inputs['table_uuid'], 'row_count': inputs['sealed']['dataset']['row_count'],
                      'logical_sha256': inputs['sealed']['dataset']['logical_sha256'], 'queries': actual}
        require(same(root.get('comparison'), comparison), 'COMPARISON_MISMATCH')
    total = root.get('native_total_ns')
    if total is not None: _integer(total, minimum=1)
    return inputs, samples, spark, resources, by_id, clean, drained


def _groups(root, checked, hashes, token):
    inputs, trino, spark, resources, queries, clean, drained = checked
    groups = []
    dataset = inputs['sealed']['dataset'] if inputs else None
    for engine in W.ENGINES:
        for case in W.CASES:
            rows = [v for v in (trino if engine == 'trino' else (spark or {}).get('measurements') or [])
                    if v['case_id'] == case]
            samples = [{'sample_index': r['sample_index'], 'elapsed_ns': r['elapsed_ns'],
                        'result_sha256': r['result']['sha256'], 'sql_sha256': r['sql_sha256'],
                        'query_id': r['query_id'],
                        'source_started_at': queries[r['query_id']]['started_at'] if engine == 'trino' else None,
                        'stats': deepcopy(r['stats'])} for r in rows]
            first = next((v['elapsed_ns'] for v in samples if v['sample_index'] == 0), None)
            repeats = [v['elapsed_ns'] for v in samples if v['sample_index'] in (1, 2, 3)]
            source_id = 'spark-benchmark/' + token + '/' + engine + '/' + case
            groups.append({
                'schema_version': 1, 'source_id': source_id,
                'source_key': digest({'source_id': source_id, 'artifact_sha256': hashes}),
                'run_token': token, 'artifact_sha256': deepcopy(hashes), 'source_result': root['result'],
                'source_started_at': root['started_at'], 'source_finished_at': root['finished_at'],
                'engine': engine, 'case_id': case, 'measurement_complete': len(rows) == 4,
                'comparison_eligible': root['result'] == 'passed', 'expected_samples': 4,
                'observed_samples': len(rows), 'unobserved_samples': 4-len(rows),
                'observed_version': (spark or {}).get('spark_version') if engine == 'spark' else None,
                'expected_version': '4.1.3' if engine == 'spark' else '483',
                'iceberg_observed_version': (spark or {}).get('iceberg_version') if engine == 'spark' else None,
                'workload_id': W.WORKLOAD_ID, 'scale_factor': W.SCALE_FACTOR,
                'snapshot_id': str(inputs['initial_snapshot_id']) if inputs else None,
                'table_uuid': inputs['table_uuid'] if inputs else None,
                'row_count': dataset['row_count'] if dataset else None,
                'logical_sha256': dataset['logical_sha256'] if dataset else None,
                'expected_result_sha256': dataset['expected'][case]['sha256'] if dataset else None,
                'conditions': {
                    'measurement_scope': ('host request through complete result including durable journal writes'
                                          if engine == 'trino' else 'spark.sql plus collect; normalization and oracle excluded'),
                    'cache': 'shared caches may be warmed; no cache flush; no Spark cache/persist',
                    'engine_order': 'trino-first-spark-second', 'resource_bounds': deepcopy(resources),
                    'source_preparation': 'excluded; validation streams and file checks may warm shared caches',
                    'first_sample': 'first observed sample, not cold'},
                'timings': {'spark_session_startup_ns': (spark or {}).get('startup_ns'),
                            'native_total_ns': root.get('native_total_ns')},
                'samples': samples, 'first_ns': first,
                'repeat_median_ns': sorted(repeats)[1] if len(repeats) == 3 else None,
                'cleanup_verified': clean, 'query_drain_verified': drained,
                'limits': deepcopy(LIMITS), 'inference_cost_estimate': None, 'model_usage': None,
            })
    return groups


def read_run(token, *, base=BASE, native_base=NATIVE_BASE):
    """Return an inventory row. Invalid/active/incomplete rows never yield groups."""
    result = {'run_token': token if type(token) is str and HEX.fullmatch(token) else None,
              'integrity': 'invalid', 'source_result': None, 'eligible': False, 'issues': [],
              'artifact_sha256': {}, 'groups': []}
    try:
        require(result['run_token'] is not None, 'INVALID_RUN_TOKEN')
        base, native_base = Path(base), Path(native_base)
        _directory(base); output = base / token; _directory(output)
        initial = _list(output)
        with _read_lock(output):
            paths = {'summary.json': output / 'summary.json'}
            for name in ('access.json', 'tunnel.json'):
                if (output / name).exists() or (output / name).is_symlink(): paths[name] = output / name
            native = native_base / token
            if native.exists() or native.is_symlink():
                _directory(native_base); _directory(native)
                native_initial = _list(native)
                for name in ('summary.json', 'plan.json', 'driver-pod.json', 'executor-pod.json'):
                    if (native / name).exists() or (native / name).is_symlink(): paths['native/' + name] = native / name
            else:
                native_initial = None
            bundle, captured = {}, {}
            for name, path in paths.items():
                raw, identity = read_file(path)
                captured[name] = (raw, identity)
                result['artifact_sha256'][name] = hashlib.sha256(raw).hexdigest()
                bundle[name] = parse(raw)
            root = bundle['summary.json']
            if type(root) is dict and root.get('result') in ('passed', 'failed', 'interrupted', 'running'):
                result['source_result'] = root['result']
            checked = _validate(root, bundle, token)
            groups = _groups(root, checked, result['artifact_sha256'], token)
            for name, path in paths.items():
                require(read_file(path) == captured[name], 'SOURCE_CHANGED')
            require(_list(output) == initial and (native_initial is None and not native.exists()
                    or native_initial is not None and _list(native) == native_initial), 'SOURCE_CHANGED')
            result.update(integrity='valid', eligible=True, groups=groups)
    except ReceiptError as error:
        code = str(error)
        result['integrity'] = ('active' if code == 'RUN_ACTIVE' else 'incomplete'
                               if code in ('SOURCE_NOT_TERMINAL', 'SOURCE_CHANGED') else 'invalid')
        result['issues'] = [code]
        result.update(eligible=False, groups=[])
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError):
        result.update(integrity='invalid', eligible=False, groups=[], issues=['INVALID_OR_MISSING_SOURCE'])
    return result


def projection(run):
    """Copy only groups from a validated read_run result; no file access."""
    return deepcopy(run['groups']) if run.get('integrity') == 'valid' and run.get('eligible') is True else []


def collect(base=BASE, native_base=NATIVE_BASE):
    report = {'schema_version': 1, 'scope': SCOPE, 'inventory': {
        'entries': 0, 'uuid_runs': 0, 'valid': 0, 'failed': 0, 'interrupted': 0,
        'incomplete': 0, 'invalid': 0, 'active': 0, 'unrecognized_entries': 0, 'eligible': 0, 'complete': False},
        'runs': [], 'sources': [], 'source_sha256': None, 'result': 'incomplete'}
    inventory = report['inventory']
    try:
        base = Path(base); _directory(base); before = _list(base)
        inventory['entries'] = len(before)
        require(len(before) <= MAX_RUNS, 'SOURCE_INVENTORY_LIMIT')
        for name, _, _ in before:
            if not HEX.fullmatch(name):
                inventory['unrecognized_entries'] += 1
                continue
            inventory['uuid_runs'] += 1
            row = read_run(name, base=base, native_base=native_base)
            report['runs'].append({k: deepcopy(v) for k, v in row.items() if k != 'groups'})
            inventory[row['integrity']] += 1
            if row['source_result'] in ('failed', 'interrupted'):
                inventory[row['source_result']] += 1
            if row['eligible']:
                inventory['eligible'] += 1
                report['sources'].extend(projection(row))
        require(_list(base) == before, 'SOURCE_INVENTORY_CHANGED')
        inventory['complete'] = not any(inventory[k] for k in ('incomplete', 'invalid', 'active', 'unrecognized_entries'))
        report['source_sha256'] = digest(report['sources'])
        report['result'] = 'complete' if inventory['complete'] else 'incomplete'
    except ReceiptError as error:
        report['issues'] = [str(error)]
    except OSError:
        report['issues'] = ['SOURCE_ROOT_UNAVAILABLE']
    return report


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    value = collect()
    print(json.dumps({k: value[k] for k in ('schema_version', 'result', 'inventory', 'source_sha256')}))
    return 0 if value['result'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
