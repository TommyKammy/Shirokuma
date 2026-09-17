"""Bounded read-only observations for the pinned Trino 483 local experiment.

Two different SELECTs validate query execution, not a performance comparison.
EXPLAIN estimates are not actual resource usage. Histories are coordinator
snapshots, not a durable audit store. No API accepts SQL, paths or another user.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CASES = {
    'projection': ('SELECT id FROM iceberg.smoke.fixture_v1', [[1], [2]]),
    'filtered': ('SELECT id FROM iceberg.smoke.fixture_v1 WHERE id = 1', [[1]]),
}
ESTIMATES = {'outputRowCount': 'rows', 'outputSizeInBytes': 'bytes', 'cpuCost': 'cpu_cost',
             'memoryCost': 'memory_cost', 'networkCost': 'network_cost'}
HISTORY_COLUMNS = ['query_id', 'state', 'user', 'queued_ms', 'analysis_ms', 'planning_ms',
                   'created_at', 'started_at', 'ended_at', 'error_type', 'error_code']
STATS_COLUMNS = ['column_name', 'data_size', 'distinct_values_count', 'nulls_fraction',
                 'row_count', 'low_value', 'high_value']
STATES = {'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING',
          'RUNNING', 'FINISHING', 'FINISHED', 'FAILED'}
MAX_PLAN_BYTES = 128 * 1024


class ObservationError(ValueError):
    """Only a safe fixed reason is returned; no server error or SQL is exposed."""


def _require(condition, message):
    if not condition:
        raise ObservationError(message)


def _id(value):
    _require(type(value) is str and re.fullmatch('[A-Za-z0-9_]{1,128}', value), 'invalid captured query identity')
    return value


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate JSON observation key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError, RecursionError):
        raise ObservationError('malformed JSON observation') from None


def _number(value, *, integer=False):
    if value is None or (not integer and type(value) is str and value in {'NaN', 'Infinity', '-Infinity'}):
        return None
    _require(type(value) in ((int,) if integer else (int, float)), 'invalid numeric observation')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        raise ObservationError('numeric observation exceeds supported range') from None
    if not finite:
        return None
    _require(value >= 0, 'negative numeric observation')
    _require(not integer or value < 2**63, 'history duration exceeds bigint range')
    return value


def _result(result, columns, max_rows):
    _require(type(result) is dict, 'invalid query observation')
    _id(result.get('query_id'))
    _require(result.get('columns') == columns and type(result.get('rows')) is list
             and len(result['rows']) <= max_rows, 'unexpected query result shape')
    for row in result['rows']:
        _require(type(row) is list and len(row) == len(columns), 'unexpected query result row')


def project_plan(result, case_id):
    """Drop free-form details, descriptors and SQL; preserve every estimate."""
    _require(case_id in CASES, 'unsupported fixed query case')
    _result(result, ['Query Plan'], 1)
    _require(len(result['rows']) == 1 and type(result['rows'][0][0]) is str
             and len(result['rows'][0][0].encode()) <= MAX_PLAN_BYTES, 'invalid or oversized query plan')
    plan = _loads(result['rows'][0][0])
    _require(type(plan) is dict and set(plan) == {'0'}, 'unsupported fixed-query fragment shape')
    nodes, ids = [], set()
    wanted_columns = [{'name': 'id', 'type': 'bigint'}]

    def visit(node, depth=0):
        _require(type(node) is dict and depth <= 8 and len(nodes) < 16, 'query plan exceeds the bounded shape')
        node_id, kind = node.get('id'), node.get('name')
        _require(type(node_id) is str and re.fullmatch('[0-9]{1,12}', node_id) and node_id not in ids,
                 'invalid or duplicate plan node identity')
        ids.add(node_id)
        _require(kind in {'Output', 'TableScan', 'ScanFilter'}, 'unsupported query plan operator')
        _require(type(node.get('outputs')) is list and node['outputs'] == wanted_columns,
                 'query plan columns differ from the fixed SELECT')
        descriptor = node.get('descriptor')
        _require(type(descriptor) is dict, 'invalid query plan descriptor')
        if kind in {'TableScan', 'ScanFilter'}:
            table = descriptor.get('table')
            _require(type(table) is str and re.fullmatch(r'iceberg:smoke\.fixture_v1\$data@[0-9]+', table),
                     'query plan reads an unexpected source')
        predicate = descriptor.get('filterPredicate')
        if kind == 'ScanFilter':
            _require(case_id == 'filtered' and predicate == "(id = bigint '1')", 'query plan filter differs from the fixed SELECT')
        else:
            _require(predicate is None, 'unexpected filter in query plan')
        raw_estimates = node.get('estimates', [])
        _require(type(raw_estimates) is list and len(raw_estimates) <= 4, 'invalid plan estimate sequence')
        estimates = []
        for estimate in raw_estimates:
            _require(type(estimate) is dict, 'invalid query plan estimate')
            estimates.append({target: _number(estimate.get(source)) for source, target in ESTIMATES.items()})
        children = node.get('children')
        _require(type(children) is list and len(children) <= 1, 'unsupported fixed query plan tree')
        nodes.append({'id': f'p{len(nodes) + 1:02}', 'kind': kind, 'columns': wanted_columns,
                      'filter_present': kind == 'ScanFilter', 'estimates': estimates})
        for child in children:
            visit(child, depth + 1)
    visit(plan['0'])
    expected = ['Output', 'TableScan' if case_id == 'projection' else 'ScanFilter']
    _require([node['kind'] for node in nodes] == expected, 'query plan does not match the fixed statement case')
    return {'format': 'trino-483-distributed-json', 'fragment_count': 1, 'nodes': nodes,
            'estimates_complete': all(node['estimates'] and all(all(v is not None for v in e.values())
                                      for e in node['estimates']) for node in nodes)}


def _timestamp(value):
    if value is None:
        return None, None
    _require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value),
             'invalid query history timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        raise ObservationError('invalid query history timestamp') from None
    return parsed.isoformat(timespec='microseconds').replace('+00:00', 'Z'), parsed


def _error_enum(value):
    _require(value is None or (type(value) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,127}', value)), 'invalid history error classification')
    return value


def project_history(result, captured, user):
    """Accept only this observer's captured IDs; missing history remains unknown."""
    _result(result, HISTORY_COLUMNS, 4)
    _require(type(captured) is dict and len(captured) == 4
             and type(user) is str and re.fullmatch('shirokuma-query-doctor-[0-9a-f]{32}', user), 'invalid history scope')
    for query_id, identity in captured.items():
        _id(query_id)
        _require(type(identity) is dict and set(identity) == {'case_id', 'kind'}
                 and identity['case_id'] in CASES and identity['kind'] in {'select', 'explain'}, 'invalid captured query mapping')
    _require({(x['case_id'], x['kind']) for x in captured.values()} == {(case, kind) for case in CASES for kind in ('select', 'explain')},
             'captured query cases are incomplete')
    seen, entries = set(), []
    for row in result['rows']:
        query_id, state, owner, queued, analysis, planning, created, started, ended, error_type, error_code = row
        _require(type(query_id) is str and query_id in captured and query_id not in seen and owner == user,
                 'query history contains a foreign, duplicate or uncaptured identity')
        seen.add(query_id)
        _require(type(state) is str and state in STATES, 'unknown query history state')
        created_s, created_t = _timestamp(created)
        started_s, started_t = _timestamp(started)
        ended_s, ended_t = _timestamp(ended)
        for earlier, later in [(created_t, started_t), (created_t, ended_t), (started_t, ended_t)]:
            _require(earlier is None or later is None or earlier <= later, 'query history timestamps are reversed')
        terminal = state in {'FINISHED', 'FAILED'}
        def duration(begin):
            if not (terminal and ended_t and begin):
                return None
            delta = ended_t - begin
            return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
        entries.append({'query_id': query_id, **captured[query_id], 'state': state,
                        'queued_ms': _number(queued, integer=True), 'analysis_ms': _number(analysis, integer=True),
                        'planning_ms': _number(planning, integer=True), 'created_at': created_s,
                        'started_at': started_s, 'ended_at': ended_s,
                        'created_to_end_ms': duration(created_t), 'started_to_end_ms': duration(started_t),
                        'error_type': _error_enum(error_type), 'error_code': _error_enum(error_code)})
    missing = sorted(set(captured) - seen)
    return {'query_id': result['query_id'], 'observed_at': _stamp(),
            'completeness': 'complete' if not missing else 'missing' if not seen else 'partial',
            'missing_query_ids': missing, 'entries': sorted(entries, key=lambda x: x['query_id']),
            'durations_complete': not missing and all(x['created_to_end_ms'] is not None and x['started_to_end_ms'] is not None for x in entries)}


def project_stats(result):
    _result(result, STATS_COLUMNS, 3)
    seen, columns, row_count = set(), [], None
    for row in result['rows']:
        name, size, distinct, nulls, rows, _low, _high = row
        _require(name in {'id', 'name', None} and name not in seen, 'unexpected or duplicate fixture statistics column')
        seen.add(name)
        if name is None:
            row_count = _number(rows)
        else:
            fraction = _number(nulls)
            _require(fraction is None or fraction <= 1, 'invalid null fraction estimate')
            columns.append({'name': name, 'data_size': _number(size), 'distinct_values_count': _number(distinct), 'nulls_fraction': fraction})
    _require(seen == {'id', 'name', None}, 'fixture statistics are incomplete')
    return {'query_id': result['query_id'], 'observed_at': _stamp(), 'table': 'fixture_v1',
            'row_count_estimate': row_count, 'columns': sorted(columns, key=lambda x: x['name']),
            'estimates_complete': row_count is not None and all(all(v is not None for k, v in c.items() if k != 'name') for c in columns)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ObservationError('Trino redirect is not allowed')


class QueryObserver:
    def __init__(self, url, run_token=None):
        parsed = urllib.parse.urlsplit(url)
        try:
            port = parsed.port
        except ValueError:
            raise ObservationError('invalid loopback Trino endpoint') from None
        _require(parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
                 and port is not None and 1 <= port <= 65535 and parsed.username is None and parsed.password is None
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment, 'invalid loopback Trino endpoint')
        token = uuid.uuid4().hex if run_token is None else run_token
        _require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token), 'invalid observer run token')
        self.url = url.rstrip('/')
        self.origin = (parsed.scheme, parsed.netloc)
        self.run_token = token
        self.user = 'shirokuma-query-doctor-' + token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.captured = {}
        self.collected = False
        self.followups = set()
        self.requests = []
        self.history_verified = False
        self.failure = None

    def receipt(self):
        """Safe lifecycle evidence, including failed or interrupted requests.

        There is no broad kill or cleanup claim after a transport failure. The
        server-side 30s run limit remains in force if a request was accepted.
        """
        verified = (self.history_verified and bool(self.requests) and self.failure is None
                    and all(r['response_complete'] and r['state'] in {'FINISHED', 'FAILED'} for r in self.requests))
        return {'schema_version': 1, 'run_token': self.run_token,
                'outcome': self.failure or ('completed' if verified else 'incomplete'),
                'server_max_run_seconds': 30, 'cleanup_verified': verified,
                'target_history_verified': self.history_verified,
                'requests': [dict(r) for r in self.requests],
                'uncaptured_requests': sum(r['query_id'] is None for r in self.requests)}

    def _history_sql(self):
        _require(len(self.captured) == 4, 'query history requested before fixed queries completed')
        identities = ', '.join("'" + _id(query_id) + "'" for query_id in sorted(self.captured))
        return ('SELECT query_id, state, "user", queued_time_ms AS queued_ms, analysis_time_ms AS analysis_ms, '
                'planning_time_ms AS planning_ms, to_iso8601(created) AS created_at, '
                'to_iso8601(started) AS started_at, to_iso8601("end") AS ended_at, error_type, error_code '
                'FROM system.runtime.queries WHERE "user" = \'' + self.user + "' AND query_id IN (" + identities + ') ORDER BY query_id')

    def _query(self, sql):
        identity = None
        for case_id, (statement, _) in CASES.items():
            if sql == statement:
                identity = {'case_id': case_id, 'kind': 'select'}
            elif sql == 'EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + statement:
                identity = {'case_id': case_id, 'kind': 'explain'}
        if sql == 'SHOW STATS FOR iceberg.smoke.fixture_v1':
            identity = {'case_id': None, 'kind': 'fixture_stats'}
        elif identity is None and sql == self._history_sql():
            identity = {'case_id': None, 'kind': 'own_query_history'}
        _require(identity is not None, 'query is outside the fixed read-only set')
        record = {**identity, 'query_id': None, 'state': None, 'response_complete': False}
        self.requests.append(record)
        try:
            result = self._transport(sql, record)
            record['response_complete'] = True
            return result
        except BaseException as error:
            # The managed tunnel also raises its own BaseException signal type.
            self.failure = 'failed' if isinstance(error, Exception) else 'interrupted'
            raise

    def _transport(self, sql, record):
        deadline = time.monotonic() + 35
        uri, query_id, columns, rows, first = self.url + '/v1/statement', None, None, [], True
        while uri:
            parsed = urllib.parse.urlsplit(uri)
            _require((parsed.scheme, parsed.netloc) == self.origin and parsed.username is None
                     and parsed.password is None and not parsed.query and not parsed.fragment,
                     'Trino polling endpoint escaped the loopback origin')
            if first:
                _require(parsed.path == '/v1/statement', 'invalid initial statement endpoint')
            else:
                parts = parsed.path.split('/')
                _require(len(parts) >= 6 and parts[:3] == ['', 'v1', 'statement']
                         and parts[3] in {'queued', 'executing'} and parts[4] == query_id,
                         'Trino polling query identity differs')
            remaining = deadline - time.monotonic()
            _require(remaining > 0, 'fixed query timed out')
            request = urllib.request.Request(uri, data=sql.encode() if first else None,
                headers={'X-Trino-User': self.user, 'X-Trino-Source': 'shirokuma-query-doctor',
                         'X-Trino-Session': 'query_max_execution_time=20s,query_max_run_time=30s'},
                method='POST' if first else 'GET')
            try:
                with self.opener.open(request, timeout=min(remaining, 10)) as response:
                    raw = response.read(1024 * 1024 + 1)
                _require(len(raw) <= 1024 * 1024, 'Trino response exceeds observation limit')
                page = _loads(raw)
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ObservationError('local query transport failed') from None
            _require(type(page) is dict, 'invalid Trino response envelope')
            identity = _id(page.get('id'))
            _require(query_id is None or query_id == identity, 'Trino changed query identity while polling')
            if query_id is None:
                _require(all(r is record or r['query_id'] != identity for r in self.requests), 'Trino reused an owned query identity')
                record['query_id'] = identity
                if record['kind'] in {'select', 'explain'}:
                    _require(identity not in self.captured, 'Trino reused a captured query identity')
                    self.captured[identity] = {'case_id': record['case_id'], 'kind': record['kind']}
            query_id = identity
            stats = page.get('stats')
            if isinstance(stats, dict) and stats.get('state') in STATES:
                record['state'] = stats['state']
            _require('error' not in page, 'fixed read-only query failed')
            if 'columns' in page:
                _require(type(page['columns']) is list and all(type(c) is dict and type(c.get('name')) is str for c in page['columns']), 'invalid Trino columns')
                names = [c['name'] for c in page['columns']]
                _require(columns is None or columns == names, 'Trino columns changed while polling')
                columns = names
            data = page.get('data', [])
            _require(type(data) is list and len(rows) + len(data) <= 16, 'Trino rows exceed observation limit')
            rows.extend(data)
            uri = page.get('nextUri')
            _require(uri is None or (type(uri) is str and bool(uri)), 'invalid Trino polling link')
            first = False
        return {'query_id': query_id, 'columns': columns, 'rows': rows}

    def _capture(self, result, case_id, kind):
        query_id = _id(result.get('query_id'))
        identity = {'case_id': case_id, 'kind': kind}
        _require(query_id not in self.captured or self.captured[query_id] == identity,
                 'Trino reused a captured query identity')
        self.captured[query_id] = identity

    def _history(self):
        result = self._query(self._history_sql())
        _require(result.get('query_id') not in self.captured, 'history query reused a captured identity')
        projected = project_history(result, self.captured, self.user)
        self.history_verified = projected['completeness'] == 'complete' and all(e['state'] in {'FINISHED', 'FAILED'} for e in projected['entries'])
        for entry in projected['entries']:
            for record in self.requests:
                if record['query_id'] == entry['query_id']:
                    record['state'] = entry['state']
        return projected

    def collect(self):
        _require(not self.collected and not self.captured, 'observer collection cannot be repeated')
        cases = []
        for case_id, (sql, expected) in CASES.items():
            selected = self._query(sql)
            _result(selected, ['id'], 2)
            _require(all(type(row[0]) is int for row in selected['rows']) and sorted(selected['rows']) == expected,
                     'fixed SELECT differs from the independent row oracle')
            self._capture(selected, case_id, 'select')
            explained = self._query('EXPLAIN (TYPE DISTRIBUTED, FORMAT JSON) ' + sql)
            plan = project_plan(explained, case_id)
            self._capture(explained, case_id, 'explain')
            cases.append({'id': case_id, 'select': {'query_id': selected['query_id'], 'row_count': len(expected), 'matched': True},
                          'explain': {'query_id': explained['query_id'], 'plan': plan}})
        history = self._history()
        self.collected = True
        return {'schema_version': 1, 'run_token': self.run_token, 'observed_at': _stamp(),
                'cases': cases, 'history': history, 'performance_gain': 'unmeasured'}

    def followup(self, kind):
        _require(self.collected and type(kind) is str and kind in {'fixture_stats', 'own_query_history'}
                 and kind not in self.followups, 'unsupported or repeated query followup')
        self.followups.add(kind)
        if kind == 'fixture_stats':
            return project_stats(self._query('SHOW STATS FOR iceberg.smoke.fixture_v1'))
        return self._history()
