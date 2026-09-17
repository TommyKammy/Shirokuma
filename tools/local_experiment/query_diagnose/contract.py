"""Pure contract for one saved Reader SELECT and one advisory model call.

The runner validates the private, terminal Reader receipt and binds its bytes
to summary_sha256 before calling source_context. This module validates internal
consistency, not filesystem authenticity or current service identity. Historical
source hashes need not equal today's code. Before/after catalog identity checks,
query draining, audit delivery, and source-file stability belong to the runner.

statements returns only schema / EXPLAIN / exact captured history / SHOW STATS.
The original SELECT is reconstructed for a digest and EXPLAIN, never executed.
Raw plans, query texts, descriptors, statistics low/high values and row data
must remain in memory. Model evidence contains only counts, estimates, timing,
and fixed finding IDs. Missing retained history is not proof of why it is absent.

Reader v1 source_context keeps its original eleven fields and current-table
statistics. snapshot_source_context adds an explicit snapshot-reader-v2 profile
and retained snapshot catalog; its identity remains the real current head.
Only this profile's SHOW STATS uses a fixed snapshot-pinned subquery. Estimates
are not exact data validation: Trino may use NDV statistics from the selected
snapshot's ancestors. No ANALYZE, current-head fallback or extra data read occurs.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re

from tools.local_experiment.query_read import contract as R
from tools.local_experiment.query_snapshots import contract as S

SCOPE = 'saved-reader-query-diagnosis-v1'
MAX_PLAN_BYTES = 128 * 1024
MAX_EVIDENCE_BYTES = 6144
MAX_PUBLIC_BYTES = 4096
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
QUERY_ID = re.compile(r'[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}\Z')
PHASES = ('schema_before', 'explain', 'history', 'stats', 'schema_after')
HISTORY_COLUMNS = ['query_id', 'state', 'user', 'source', 'query', 'queued_ms',
    'analysis_ms', 'planning_ms', 'created_at', 'started_at', 'ended_at', 'error_type', 'error_code']
HISTORY_TYPES = ['varchar'] * 5 + ['bigint'] * 3 + ['varchar'] * 5
STATS_COLUMNS = ['column_name', 'data_size', 'distinct_values_count', 'nulls_fraction',
                 'row_count', 'low_value', 'high_value']
STATS_TYPES = ['varchar'] + ['double'] * 4 + ['varchar'] * 2
ESTIMATES = ('outputRowCount', 'outputSizeInBytes', 'cpuCost', 'memoryCost', 'networkCost')
# Trino 483 PlanPrinter names (including its LimitPartial spelling).
OPERATORS = frozenset(('Output', 'Limit', 'LimitPartial', 'TableScan', 'Project',
                      'RemoteSource', 'LocalExchange', 'RemoteExchange', 'Values'))
FINDING_STEPS = {
    'RESULT_LIMIT_REACHED': 'REVIEW_READER_LIMIT',
    'HISTORY_NOT_RETAINED': 'CAPTURE_HISTORY_PROMPTLY',
    'HISTORY_TIMING_INCOMPLETE': 'CHECK_HISTORY_TIMING',
    'QUEUE_TIME_OBSERVED': 'REVIEW_QUEUE_TIMING',
    'PLAN_ESTIMATES_INCOMPLETE': 'REVIEW_TABLE_STATISTICS',
    'TABLE_STATISTICS_INCOMPLETE': 'REVIEW_TABLE_STATISTICS',
}
NEXT_STEPS = frozenset(FINDING_STEPS.values()) | {'MEASURE_BEFORE_OPTIMIZING'}
LIMITATIONS = ['ESTIMATES_ARE_NOT_MEASURED_USAGE', 'HISTORY_IS_NOT_DURABLE_AUDIT',
              'NO_CAUSE_OR_GAIN_ESTABLISHED', 'NO_SQL_REWRITE_OR_EXECUTION_ADVICE']
SOURCE_KEYS = {'schema_version', 'read_token', 'request', 'inventory', 'identity', 'selection',
               'query_id', 'user', 'source', 'sql_sha256', 'summary_sha256'}
SNAPSHOT_PROFILE = 'snapshot-reader-v2'
SNAPSHOT_SOURCE_KEYS = SOURCE_KEYS | {'profile', 'snapshot_catalog'}
PLAN_KEYS = {'query_id', 'format', 'fragment_count', 'node_count', 'operator_counts',
             'estimate_slots', 'estimate_unknown_count', 'estimate_zero_count',
             'empty_estimate_nodes', 'estimates_complete'}
HISTORY_KEYS = {'query_id', 'status', 'state', 'queued_ms', 'analysis_ms', 'planning_ms',
                'created_to_end_ms', 'started_to_end_ms'}
TIMINGS = ('queued_ms', 'analysis_ms', 'planning_ms', 'created_to_end_ms', 'started_to_end_ms')
STATS_KEYS = {'query_id', 'column_count', 'row_count_estimate', 'numeric_slots',
              'unknown_count', 'zero_count', 'estimates_complete'}


class ContractError(ValueError):
    """Only fixed error codes, without caller text or server bodies."""


def require(condition, code):
    if not condition:
        raise ContractError(code)


def canonical(value):
    try:
        return R.canonical(value)
    except R.ContractError:
        raise ContractError('DIAGNOSIS_JSON_INVALID') from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _keys(value, expected, code):
    require(type(value) is dict and set(value) == set(expected), code)


def _uint(value, maximum=2**63-1):
    require(type(value) is int and 0 <= value <= maximum, 'DIAGNOSIS_NUMBER_INVALID')
    return value


def _query_id(value):
    require(type(value) is str and QUERY_ID.fullmatch(value), 'DIAGNOSIS_QUERY_ID_INVALID')
    return value


def _sha(value):
    require(type(value) is str and R.SHA256.fullmatch(value), 'DIAGNOSIS_HASH_INVALID')
    return value


def source_context(read_token, request, inventory, identity, selection, query_id, summary_sha256):
    """Bind a runner-verified Reader v1 summary without retaining SQL or rows."""
    require(type(read_token) is str and TOKEN.fullmatch(read_token), 'READ_TOKEN_INVALID')
    _query_id(query_id); _sha(summary_sha256)
    try:
        request = R.normalize_request(request)
        require(request['command'] == 'select', 'READ_SOURCE_NOT_SELECT')
        require(type(identity) is dict and type(identity.get('columns')) is list, 'READ_SOURCE_INVALID')
        identity = R.validate_identity(identity, request, identity['columns'])
        sql = R.select_sql(request, inventory, identity['columns'], identity)
        selected = R.selected_columns(request, identity['columns'])
    except R.ContractError:
        raise ContractError('READ_SOURCE_INVALID') from None
    _selection(selection, request, identity, selected, identity['current_snapshot_id'], 1, R.RESULT_KEYS)
    owner = 'shirokuma-query-read-' + read_token
    return deepcopy({'schema_version': 1, 'read_token': read_token, 'request': request,
        'inventory': inventory, 'identity': identity, 'selection': selection, 'query_id': query_id,
        'user': owner, 'source': owner, 'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
        'summary_sha256': summary_sha256})


def _selection(selection, request, identity, selected, snapshot_id, version, keys):
    """Shared rowless receipt facts; no reconstruction of discarded rows."""
    _keys(selection, keys - {'rows'}, 'READ_SOURCE_SELECTION_INVALID')
    require(type(selection['schema_version']) is int and selection['schema_version'] == version
            and selection['table'] == 'iceberg.' + request['table']
            and selection['table_uuid'] == identity['table_uuid']
            and selection['schema_sha256'] == identity['schema_sha256']
            and selection['snapshot_id'] == snapshot_id
            and canonical(selection['columns']) == canonical(selected)
            and type(selection['requested_limit']) is int and selection['requested_limit'] == request['limit']
            and selection['ordering'] == 'unspecified', 'READ_SOURCE_SELECTION_INVALID')
    count, fetched, limit = selection['row_count'], selection['fetched_row_count'], request['limit']
    _uint(count, limit); _uint(fetched, limit+1); _sha(selection['rows_sha256'])
    require(count == min(fetched, limit) and type(selection['truncated']) is bool
            and selection['truncated'] == (fetched > limit), 'READ_SOURCE_SELECTION_INVALID')


def snapshot_source_context(read_token, request, inventory, identity, selection, query_id,
                            summary_sha256, snapshot_catalog):
    """Bind a terminal snapshot Reader v2 receipt; never rewrite current identity.

    The runner supplies authenticated receipt facts. Identity is the observed
    current head B, selection is the actual read A, and snapshot_catalog is the
    original complete retained-snapshot projection. A can equal B explicitly.
    """
    require(type(read_token) is str and TOKEN.fullmatch(read_token), 'READ_TOKEN_INVALID')
    _query_id(query_id); _sha(summary_sha256)
    try:
        request = S.normalize_request(request)
        require(request['command'] == 'select', 'READ_SOURCE_NOT_SELECT')
        require(type(identity) is dict and type(identity.get('columns')) is list, 'READ_SOURCE_INVALID')
        window = S.validate_window({'schema_version':2, 'identity':identity, 'snapshots':snapshot_catalog}, request)
        identity = window['identity']
        sql = S.select_sql(request, inventory, identity['columns'], window)
        selected = S.selected_columns(request, identity['columns'])
        snapshot = S.select_snapshot(window, request)
    except R.ContractError:
        raise ContractError('READ_SOURCE_INVALID') from None
    _selection(selection, request, identity, selected, snapshot['snapshot_id'], 2, S.RESULT_KEYS)
    require(selection['current_snapshot_id'] == identity['current_snapshot_id']
            and canonical(selection['selected_snapshot']) == canonical(snapshot), 'READ_SOURCE_SELECTION_INVALID')
    owner = 'shirokuma-query-read-' + read_token
    return deepcopy({'schema_version':2, 'profile':SNAPSHOT_PROFILE,
        'snapshot_catalog':window['snapshots'], 'read_token':read_token, 'request':request,
        'inventory': inventory, 'identity': identity, 'selection': selection, 'query_id': query_id,
        'user': owner, 'source': owner, 'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
        'summary_sha256': summary_sha256})


def validate_source(value):
    require(type(value) is dict and type(value.get('schema_version')) is int, 'READ_SOURCE_INVALID')
    version = value['schema_version']
    require(version in (1, 2), 'READ_SOURCE_INVALID')
    _keys(value, SOURCE_KEYS if version == 1 else SNAPSHOT_SOURCE_KEYS, 'READ_SOURCE_INVALID')
    arguments = tuple(value[k] for k in ('read_token', 'request', 'inventory', 'identity',
                                        'selection', 'query_id', 'summary_sha256'))
    if version == 1:
        expected = source_context(*arguments)
    else:
        require(value['profile'] == SNAPSHOT_PROFILE, 'READ_SOURCE_INVALID')
        expected = snapshot_source_context(*arguments, value['snapshot_catalog'])
    require(canonical(expected) == canonical(value), 'READ_SOURCE_INVALID')
    return expected


def base_request(source):
    """Return the existing fixed Reader request used by catalog/schema reads."""
    source = validate_source(source)
    return S.base_request(source['request']) if source['schema_version'] == 2 else deepcopy(source['request'])


def _source_window(source):
    return {'schema_version':source['schema_version'], 'identity':deepcopy(source['identity']),
            'snapshots':deepcopy(source.get('snapshot_catalog'))}


def select_sql(source):
    """Reconstruct the source SELECT for hashing/EXPLAIN, never a new execution."""
    source = validate_source(source)
    if source['schema_version'] == 2:
        return S.select_sql(source['request'], source['inventory'], source['identity']['columns'], _source_window(source))
    return R.select_sql(source['request'], source['inventory'], source['identity']['columns'], source['identity'])


def project_source_window(response, source, columns):
    """Return {schema_version, identity, snapshots}; v1 snapshots is always None."""
    source = validate_source(source)
    try:
        if source['schema_version'] == 2:
            return S.project_window(response, source['request'], columns)
        return {'schema_version':1, 'identity':R.project_identity(response, source['request'], columns), 'snapshots':None}
    except R.ContractError:
        raise ContractError('READ_SOURCE_INVALID') from None


def verify_source_window(source, before, after=None):
    """Require saved source == first fresh window == optional final fresh window.

    Expiration, current-head movement, schema/spec/ref or metadata changes fail;
    there is no fallback to a different snapshot. Inputs must be fresh guarded
    projections, not trusted merely because their hashes have the right shape.
    """
    source = validate_source(source)
    expected = _source_window(source)
    for observed in (before,) if after is None else (before, after):
        _keys(observed, {'schema_version', 'identity', 'snapshots'}, 'READ_SOURCE_INVALID')
        require(type(observed['schema_version']) is int and observed['schema_version'] == source['schema_version'],
                'READ_SOURCE_INVALID')
        try:
            if source['schema_version'] == 2:
                S.verify_window(expected, observed)
            else:
                require(observed['snapshots'] is None, 'READ_SOURCE_INVALID')
                R.verify_identity(expected['identity'], observed['identity'])
        except R.ContractError:
            raise ContractError('READ_SOURCE_INVALID') from None
    return True


def statements(source):
    """Exact five phase statements; never includes a standalone data SELECT."""
    source = validate_source(source)
    request = base_request(source)
    select = select_sql(source)
    stats = 'SHOW STATS FOR ' + R.qualified(request['table'])
    if source['schema_version'] == 2:
        # Trino 483 ShowStatsRewrite plans this subquery without executing the
        # data SELECT. Iceberg TableStatisticsReader uses the selected snapshot's
        # data manifests; NDV can come from that snapshot or its ancestors.
        # Full compatible schema and no LIMIT preserve whole-snapshot estimates.
        stats = ('SHOW STATS FOR (SELECT ' + ', '.join(R.quote(c['name']) for c in source['identity']['columns'])
                 + ' FROM ' + R.qualified(request['table'])
                 + " FOR VERSION AS OF BIGINT '" + source['selection']['snapshot_id'] + "')")
    # Select by ID alone: foreign provenance must fail validation, not disappear
    # behind an owner predicate and be mislabeled as absent retained history.
    history = ('SELECT query_id, state, "user", source, query, queued_time_ms AS queued_ms, '
        'analysis_time_ms AS analysis_ms, planning_time_ms AS planning_ms, '
        'to_iso8601(created) AS created_at, to_iso8601(started) AS started_at, '
        'to_iso8601("end") AS ended_at, error_type, error_code FROM system.runtime.queries '
        "WHERE query_id = '" + source['query_id'] + "' LIMIT 2")
    return {'schema_before': R.schema_sql(request),
            'explain': 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + select,
            'history': history, 'stats': stats,
            'schema_after': R.schema_sql(request)}


def _result(value, names, types, count):
    require(type(value) is dict and set(value) == {'query_id', 'columns', 'column_types', 'rows'}
            and value['columns'] == names and value['column_types'] == types
            and type(value['rows']) is list and len(value['rows']) <= count
            and all(type(row) is list and len(row) == len(names) for row in value['rows']),
            'DIAGNOSIS_RESULT_INVALID')
    _query_id(value['query_id'])
    return value['rows']


def _number(value):
    if value is None or type(value) is str and value in ('NaN', 'Infinity', '-Infinity'):
        return None
    require(type(value) in (int, float, Decimal), 'DIAGNOSIS_NUMBER_INVALID')
    try:
        if not math.isfinite(value):
            return None
        require(0 <= value <= 1e100, 'DIAGNOSIS_NUMBER_INVALID')
        return value if type(value) is int else float(value)
    except (ValueError, OverflowError):
        raise ContractError('DIAGNOSIS_NUMBER_INVALID') from None


def _loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DIAGNOSIS_JSON_INVALID')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=Decimal,
            parse_constant=lambda _: (_ for _ in ()).throw(ContractError('DIAGNOSIS_JSON_INVALID')))
    except (ValueError, TypeError, RecursionError):
        raise ContractError('DIAGNOSIS_JSON_INVALID') from None


def project_plan(result, source):
    """Keep bounded operator/count facts, drop raw descriptors and plan text."""
    source = validate_source(source)
    rows = _result(result, ['Query Plan'], ['varchar'], 1)
    require(len(rows) == 1 and type(rows[0][0]) is str
            and len(rows[0][0].encode()) <= MAX_PLAN_BYTES, 'DIAGNOSIS_PLAN_LIMIT')
    raw = _loads(rows[0][0])
    require(type(raw) is dict and 1 <= len(raw) <= 8
            and all(re.fullmatch(r'[0-9]{1,6}', key) for key in raw), 'DIAGNOSIS_PLAN_UNSUPPORTED')
    ids, counts, values = set(), {}, []
    empty = 0
    expected_table = 'iceberg:' + source['request']['table'] + '$data@' + source['selection']['snapshot_id']

    def visit(node, depth=0):
        nonlocal empty
        require(type(node) is dict and depth <= 16 and len(ids) < 64, 'DIAGNOSIS_PLAN_LIMIT')
        node_id, kind = node.get('id'), node.get('name')
        require(type(node_id) is str and re.fullmatch(r'[0-9]{1,12}', node_id) and node_id not in ids
                and type(kind) is str and kind in OPERATORS, 'DIAGNOSIS_PLAN_UNSUPPORTED')
        ids.add(node_id); counts[kind] = counts.get(kind, 0) + 1
        descriptor = node.get('descriptor')
        require(type(descriptor) is dict, 'DIAGNOSIS_PLAN_UNSUPPORTED')
        if kind == 'TableScan':
            # IcebergTableHandle.toString (Trino 483) appends the pushed-down
            # limit. It must be precisely the Reader's limit+1 sentinel bound.
            require(descriptor.get('table') in (expected_table,
                expected_table + ' LIMIT ' + str(source['request']['limit'] + 1)),
                'DIAGNOSIS_PLAN_SOURCE_MISMATCH')
        if kind in ('Limit', 'LimitPartial'):
            require(descriptor.get('count') == str(source['request']['limit'] + 1), 'DIAGNOSIS_PLAN_LIMIT_MISMATCH')
        estimates = node.get('estimates')
        require(type(estimates) is list and len(estimates) <= 4, 'DIAGNOSIS_PLAN_UNSUPPORTED')
        empty += not estimates
        for estimate in estimates:
            require(type(estimate) is dict and set(estimate) == set(ESTIMATES), 'DIAGNOSIS_PLAN_UNSUPPORTED')
            values.extend(_number(estimate[field]) for field in ESTIMATES)
        children = node.get('children')
        require(type(children) is list and len(children) <= 8, 'DIAGNOSIS_PLAN_UNSUPPORTED')
        for child in children:
            visit(child, depth + 1)
    for fragment in sorted(raw, key=int):
        visit(raw[fragment])
    require(counts.get('TableScan', 0) == 1 or counts.get('TableScan', 0) == 0
            and counts.get('Values', 0) == 1 and source['selection']['fetched_row_count'] == 0,
            'DIAGNOSIS_PLAN_UNSUPPORTED')
    projected = {'query_id': result['query_id'], 'format': 'trino-483-distributed-json',
        'fragment_count': len(raw), 'node_count': len(ids),
        'operator_counts': [{'kind': k, 'count': counts[k]} for k in sorted(counts)],
        'estimate_slots': len(values), 'estimate_unknown_count': values.count(None),
        'estimate_zero_count': sum(v == 0 for v in values if v is not None),
        'empty_estimate_nodes': empty, 'estimates_complete': not empty and None not in values}
    return validate_plan(projected)


def validate_plan(value):
    _keys(value, PLAN_KEYS, 'DIAGNOSIS_PLAN_INVALID'); _query_id(value['query_id'])
    require(value['format'] == 'trino-483-distributed-json', 'DIAGNOSIS_PLAN_INVALID')
    fragments, nodes = _uint(value['fragment_count'], 8), _uint(value['node_count'], 64)
    require(1 <= fragments <= nodes and type(value['operator_counts']) is list, 'DIAGNOSIS_PLAN_INVALID')
    names, total = [], 0
    for row in value['operator_counts']:
        _keys(row, ('kind', 'count'), 'DIAGNOSIS_PLAN_INVALID')
        require(type(row['kind']) is str and row['kind'] in OPERATORS, 'DIAGNOSIS_PLAN_INVALID')
        require(_uint(row['count'], nodes) > 0, 'DIAGNOSIS_PLAN_INVALID')
        names.append(row['kind']); total += row['count']
    require(names == sorted(set(names)) and total == nodes, 'DIAGNOSIS_PLAN_INVALID')
    slots = _uint(value['estimate_slots'], nodes * 4 * 5)
    missing = _uint(value['estimate_unknown_count'], slots)
    zeros = _uint(value['estimate_zero_count'], slots)
    empty = _uint(value['empty_estimate_nodes'], nodes)
    require(slots % 5 == 0 and (nodes-empty)*5 <= slots <= (nodes-empty)*20
            and missing+zeros <= slots and type(value['estimates_complete']) is bool
            and value['estimates_complete'] == (empty == 0 and missing == 0), 'DIAGNOSIS_PLAN_INVALID')
    return deepcopy(value)


def _timestamp(value):
    if value is None:
        return None
    require(type(value) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value),
        'DIAGNOSIS_HISTORY_INVALID')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        raise ContractError('DIAGNOSIS_HISTORY_INVALID') from None


def project_history(result, source):
    source = validate_source(source)
    rows = _result(result, HISTORY_COLUMNS, HISTORY_TYPES, 1)
    projected = {'query_id': result['query_id'], 'status': 'not-retained', 'state': None,
                 **{key: None for key in TIMINGS}}
    if not rows:
        return projected
    qid, state, user, origin, sql, queued, analysis, planning, created, started, ended, error_type, error_code = rows[0]
    require(qid == source['query_id'] and user == source['user'] and origin == source['source']
            and type(sql) is str and len(sql.encode()) <= 16384
            and hashlib.sha256(sql.encode()).hexdigest() == source['sql_sha256'],
            'DIAGNOSIS_HISTORY_PROVENANCE_INVALID')
    require(state == 'FINISHED' and error_type is None and error_code is None, 'DIAGNOSIS_HISTORY_CONTRADICTION')
    times = [_timestamp(v) for v in (created, started, ended)]
    for a, b in ((0,1), (1,2), (0,2)):
        require(times[a] is None or times[b] is None or times[a] <= times[b], 'DIAGNOSIS_HISTORY_INVALID')
    def elapsed(begin):
        if times[begin] is None or times[2] is None:
            return None
        delta = times[2] - times[begin]
        return _uint(delta.days*86400000 + delta.seconds*1000 + delta.microseconds//1000)
    projected.update(status='observed', state=state, queued_ms=None if queued is None else _uint(queued),
        analysis_ms=None if analysis is None else _uint(analysis),
        planning_ms=None if planning is None else _uint(planning),
        created_to_end_ms=elapsed(0), started_to_end_ms=elapsed(1))
    return validate_history(projected)


def validate_history(value):
    _keys(value, HISTORY_KEYS, 'DIAGNOSIS_HISTORY_INVALID'); _query_id(value['query_id'])
    require(value['status'] in ('observed', 'not-retained'), 'DIAGNOSIS_HISTORY_INVALID')
    require(value['state'] == 'FINISHED' if value['status'] == 'observed' else value['state'] is None,
            'DIAGNOSIS_HISTORY_INVALID')
    for field in TIMINGS:
        if value[field] is not None:
            _uint(value[field]); require(value['status'] == 'observed', 'DIAGNOSIS_HISTORY_INVALID')
    a, b = value['created_to_end_ms'], value['started_to_end_ms']
    require(a is None or b is None or a >= b, 'DIAGNOSIS_HISTORY_INVALID')
    return deepcopy(value)


def project_stats(result, source):
    source = validate_source(source)
    columns = source['identity']['columns']
    rows = _result(result, STATS_COLUMNS, STATS_TYPES, len(columns)+1)
    known = {c['name'] for c in columns}; seen, values = set(), []
    row_count = None
    for name, size, distinct, nulls, count, _low, _high in rows:
        require((name is None or type(name) is str and name in known) and name not in seen,
                'DIAGNOSIS_STATS_INVALID')
        seen.add(name)
        if name is None:
            require(size is None and distinct is None and nulls is None, 'DIAGNOSIS_STATS_INVALID')
            row_count = _number(count); values.append(row_count)
        else:
            require(count is None, 'DIAGNOSIS_STATS_INVALID')
            fraction = _number(nulls)
            require(fraction is None or fraction <= 1, 'DIAGNOSIS_STATS_INVALID')
            values.extend((_number(size), _number(distinct), fraction))
    require(seen == known | {None}, 'DIAGNOSIS_STATS_INVALID')
    return validate_stats({'query_id': result['query_id'], 'column_count': len(columns),
        'row_count_estimate': row_count, 'numeric_slots': len(values), 'unknown_count': values.count(None),
        'zero_count': sum(v == 0 for v in values if v is not None), 'estimates_complete': None not in values})


def validate_stats(value):
    _keys(value, STATS_KEYS, 'DIAGNOSIS_STATS_INVALID'); _query_id(value['query_id'])
    columns = _uint(value['column_count'], 32)
    require(columns > 0 and type(value['numeric_slots']) is int and value['numeric_slots'] == columns*3+1,
            'DIAGNOSIS_STATS_INVALID')
    slots = value['numeric_slots']; missing = _uint(value['unknown_count'], slots)
    zeros = _uint(value['zero_count'], slots)
    require(_number(value['row_count_estimate']) == value['row_count_estimate']
            and not isinstance(value['row_count_estimate'], Decimal) and missing+zeros <= slots
            and (value['row_count_estimate'] is not None or missing > 0)
            and (value['row_count_estimate'] != 0 or zeros > 0)
            and type(value['estimates_complete']) is bool and value['estimates_complete'] == (missing == 0),
            'DIAGNOSIS_STATS_INVALID')
    return deepcopy(value)


def _findings(reader, plan, history, stats):
    ids = []
    if reader['truncated']: ids.append('RESULT_LIMIT_REACHED')
    if history['status'] == 'not-retained': ids.append('HISTORY_NOT_RETAINED')
    else:
        if any(history[key] is None for key in TIMINGS): ids.append('HISTORY_TIMING_INCOMPLETE')
        if history['queued_ms'] is not None and history['queued_ms'] > 0: ids.append('QUEUE_TIME_OBSERVED')
    if not plan['estimates_complete']: ids.append('PLAN_ESTIMATES_INCOMPLETE')
    if not stats['estimates_complete']: ids.append('TABLE_STATISTICS_INCOMPLETE')
    return [{'id': code, 'next_step_id': FINDING_STEPS[code]} for code in sorted(ids)]


def build_evidence(source, plan, history, stats):
    source = validate_source(source)
    plan, history, stats = validate_plan(plan), validate_history(history), validate_stats(stats)
    require(len({plan['query_id'], history['query_id'], stats['query_id'], source['query_id']}) == 4,
            'DIAGNOSIS_QUERY_ID_REUSED')
    require(stats['column_count'] == len(source['identity']['columns']), 'DIAGNOSIS_STATS_INVALID')
    reader = {key: source['selection'][key] for key in ('row_count', 'fetched_row_count', 'requested_limit', 'truncated')}
    reader['selected_column_count'] = len(source['selection']['columns'])
    projected = {key: {k:v for k,v in value.items() if k != 'query_id'}
                 for key, value in (('plan',plan), ('history',history), ('statistics',stats))}
    result = {'schema_version': 1, 'scope': SCOPE, 'reader': reader, **projected,
              'findings': _findings(reader, plan, history, stats), 'limitations': list(LIMITATIONS)}
    return validate_evidence(result)


def validate_evidence(value):
    _keys(value, ('schema_version', 'scope', 'reader', 'plan', 'history', 'statistics', 'findings', 'limitations'),
          'DIAGNOSIS_EVIDENCE_INVALID')
    require(len(canonical(value)) <= MAX_EVIDENCE_BYTES and type(value['schema_version']) is int
            and value['schema_version'] == 1 and value['scope'] == SCOPE and value['limitations'] == LIMITATIONS,
            'DIAGNOSIS_EVIDENCE_INVALID')
    reader = value['reader']
    _keys(reader, ('row_count', 'fetched_row_count', 'requested_limit', 'truncated', 'selected_column_count'),
          'DIAGNOSIS_EVIDENCE_INVALID')
    limit = _uint(reader['requested_limit'], 100)
    require(limit > 0 and _uint(reader['selected_column_count'], 16) > 0, 'DIAGNOSIS_EVIDENCE_INVALID')
    require(_uint(reader['row_count'], limit) == min(_uint(reader['fetched_row_count'],limit+1), limit)
            and type(reader['truncated']) is bool and reader['truncated'] == (reader['fetched_row_count'] > limit),
            'DIAGNOSIS_EVIDENCE_INVALID')
    fake_id = '20000101_000000_00000_aaaaa'
    for key, keys, validator in (('plan', PLAN_KEYS, validate_plan), ('history', HISTORY_KEYS, validate_history),
                                 ('statistics', STATS_KEYS, validate_stats)):
        _keys(value[key], keys-{'query_id'}, 'DIAGNOSIS_EVIDENCE_INVALID')
        validator({'query_id':fake_id, **value[key]})
    require(reader['selected_column_count'] <= value['statistics']['column_count'], 'DIAGNOSIS_EVIDENCE_INVALID')
    require(canonical(value['findings']) == canonical(_findings(reader, value['plan'], value['history'], value['statistics'])),
            'DIAGNOSIS_EVIDENCE_INVALID')
    return deepcopy(value)


def expected_diagnosis(evidence):
    evidence = validate_evidence(evidence)
    return {'schema_version': 1, 'finding_ids': [f['id'] for f in evidence['findings']],
            'next_step_ids': sorted({f['next_step_id'] for f in evidence['findings']} | {'MEASURE_BEFORE_OPTIMIZING'}),
            'root_cause': 'undetermined', 'performance_gain': 'unmeasured'}


def validate_diagnosis(value, evidence):
    require(canonical(value) == canonical(expected_diagnosis(evidence)), 'DIAGNOSIS_MODEL_MISMATCH')
    return deepcopy(value)


def public_summary(source, evidence, diagnosis):
    source = validate_source(source); evidence = validate_evidence(evidence)
    diagnosis = validate_diagnosis(diagnosis, evidence)
    # Bind the retained result facts even though the model never sees identity.
    require({k:source['selection'][k] for k in ('row_count','fetched_row_count','requested_limit','truncated')}
            == {k:evidence['reader'][k] for k in ('row_count','fetched_row_count','requested_limit','truncated')}
            and evidence['reader']['selected_column_count'] == len(source['selection']['columns'])
            and evidence['statistics']['column_count'] == len(source['identity']['columns']),
            'DIAGNOSIS_EVIDENCE_INVALID')
    value = {'schema_version': 1, 'read_token': source['read_token'], 'query_id': source['query_id'],
             'history': evidence['history']['status'], 'findings': diagnosis['finding_ids'],
             'next_steps': diagnosis['next_step_ids'], 'root_cause': 'undetermined',
             'performance_gain': 'unmeasured', 'original_select_reexecuted': False, 'model_calls': 1}
    require(len(canonical(value)) <= MAX_PUBLIC_BYTES, 'DIAGNOSIS_PUBLIC_LIMIT')
    return value
