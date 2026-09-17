"""Bounded loopback Trino pages; stream rows without retaining the dataset.

Query users and records retain the existing sandbox drain contract. This module
does not expose a CLI, retry POSTs, accept server redirects, or log raw errors.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tools.local_experiment.iceberg_sandbox import run as SB
from tools.local_experiment.spark_benchmark import workload as W

MAX_ROWS = 1_000_000
MAX_BYTES = 256 * 1024**2
PAGE_BYTES = 8 * 1024**2
MAX_PAGES = 4096
SERVER_SECONDS = 120
STATES = {'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING',
          'RUNNING', 'FINISHING', 'FINISHED', 'FAILED'}
STATS = ('elapsedTimeMillis', 'queuedTimeMillis', 'cpuTimeMillis', 'wallTimeMillis',
         'processedRows', 'processedBytes', 'physicalInputBytes')


class ClientError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise ClientError(code)


def stats_projection(value):
    require(type(value) is dict, 'QUERY_STATS_INVALID')
    result = {}
    for key in STATS:
        item = value.get(key)
        require(item is None or type(item) is int and 0 <= item < 2**63, 'QUERY_STATS_INVALID')
        result[key] = item
    state = value.get('state')
    require(state is None or type(state) is str and state in STATES, 'QUERY_STATS_INVALID')
    result['state'] = state
    return result


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_RESPONSE_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_float=Decimal,
                      parse_constant=lambda _: (_ for _ in ()).throw(ClientError('NONFINITE_RESPONSE')))


class Client(SB.SandboxClient):
    def __init__(self, url, token, *, control=False, timeout=180, opener=None):
        require(type(timeout) in (int, float) and 0 < timeout <= 180, 'QUERY_TIMEOUT_INVALID')
        super().__init__(url, token, control=control, timeout=timeout)
        if opener is not None:
            self.opener = opener

    def query(self, sql):
        rows = []
        def consume(row):
            require(len(rows) < 4096, 'SMALL_QUERY_ROW_BOUND')
            rows.append(deepcopy(row))
        result = self.stream(sql, consume)
        return result | {'rows': rows}

    def stream(self, sql, on_row, *, expected_columns=None, expected_types=None):
        require(type(sql) is str and 0 < len(sql.encode()) <= 16384 and callable(on_row), 'QUERY_INPUT_INVALID')
        require(not any(r['result'] in {'unknown', 'interrupted'} and not r['response_complete']
                        for r in self.records), 'QUERY_OUTCOME_UNRESOLVED')
        server_seconds = min(SERVER_SECONDS, max(1, int(self.timeout)))
        started = time.monotonic()
        deadline = started + self.timeout
        record = {'operation_id': str(uuid.uuid4()), 'query_id': None,
                  'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
                  'started_at': datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
                  'result': 'pending', 'response_complete': False, 'post_attempted': False,
                  'server_max_run_seconds': server_seconds, 'row_count': 0, 'response_bytes': 0,
                  'http_status': None, 'stats': None, 'elapsed_ns': None}
        self.records.append(record)
        sent, first, pages = False, True, 0
        next_uri = self.url + '/v1/statement'
        columns, types = None, None
        clock_start = None
        def notify():
            if self.progress:
                self.progress()
        try:
            # The durable pending callback must succeed before the POST.
            notify()
            record['post_attempted'] = True
            notify()
            clock_start = time.perf_counter_ns()
            while next_uri is not None:
                require(pages < MAX_PAGES, 'QUERY_PAGE_BOUND')
                target = urllib.parse.urlsplit(next_uri)
                require((target.scheme, target.netloc) == self.origin and target.username is None
                        and target.password is None and not target.query and not target.fragment,
                        'QUERY_ORIGIN_CHANGED')
                if not first:
                    match = re.fullmatch(r'/v1/statement/(?:queued|executing)/([A-Za-z0-9_]{1,128})/[A-Za-z0-9_-]+/[0-9]+', target.path)
                    require(match is not None and match.group(1) == record['query_id'], 'QUERY_CONTINUATION_CHANGED')
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'QUERY_DEADLINE')
                request = urllib.request.Request(next_uri, data=sql.encode() if first else None,
                    headers={'X-Trino-User': self.user, 'X-Trino-Source': 'shirokuma-spark-benchmark',
                             'X-Trino-Session': f'query_max_execution_time={server_seconds}s,query_max_run_time={server_seconds}s'},
                    method='POST' if first else 'GET')
                sent = True
                with self.opener.open(request, timeout=min(remaining, 15)) as response:
                    require(response.status == 200, 'QUERY_HTTP_STATUS')
                    record['http_status'] = response.status
                    raw = response.read(PAGE_BYTES + 1)
                require(len(raw) <= PAGE_BYTES, 'QUERY_PAGE_BYTES_BOUND')
                record['response_bytes'] += len(raw)
                require(record['response_bytes'] <= MAX_BYTES, 'QUERY_TOTAL_BYTES_BOUND')
                page = decode(raw)
                require(type(page) is dict, 'QUERY_RESPONSE_INVALID')
                query_id = page.get('id')
                require(type(query_id) is str and SB.QUERY_ID.fullmatch(query_id), 'QUERY_ID_INVALID')
                require(record['query_id'] is None or record['query_id'] == query_id, 'QUERY_ID_CHANGED')
                record['query_id'] = query_id
                # Capture accepted query identity before any row processing.
                notify()
                if 'stats' in page:
                    record['stats'] = stats_projection(page['stats'])
                following = page.get('nextUri')
                require(following is None or type(following) is str and len(following) <= 2048, 'QUERY_NEXT_INVALID')
                if following is None:
                    record['response_complete'] = True
                if 'error' in page:
                    require(following is None, 'QUERY_ERROR_NOT_TERMINAL')
                    raise ClientError('TRINO_QUERY_FAILED')
                if following is None and record['stats'] is not None:
                    require(record['stats']['state'] in (None, 'FINISHED'), 'QUERY_TERMINAL_STATE_INVALID')
                if 'columns' in page:
                    raw_columns = page['columns']
                    require(type(raw_columns) is list and len(raw_columns) <= 32
                            and all(type(c) is dict and type(c.get('name')) is str and len(c['name']) <= 128
                                    and type(c.get('type')) is str and len(c['type']) <= 128 for c in raw_columns),
                            'QUERY_COLUMNS_INVALID')
                    new_columns = [c['name'] for c in raw_columns]
                    new_types = W.normalize_column_types('trino', [c['type'] for c in raw_columns])
                    require(columns is None or (columns == new_columns and types == new_types), 'QUERY_COLUMNS_CHANGED')
                    columns, types = new_columns, new_types
                rows = page.get('data', [])
                require(type(rows) is list and (not rows or columns is not None), 'QUERY_ROWS_INVALID')
                if expected_columns is not None and columns is not None:
                    require(columns == list(expected_columns), 'QUERY_SCHEMA_MISMATCH')
                if expected_types is not None and types is not None:
                    require(types == list(expected_types), 'QUERY_SCHEMA_MISMATCH')
                for row in rows:
                    require(type(row) is list and len(row) == len(columns), 'QUERY_ROWS_INVALID')
                    record['row_count'] += 1
                    require(record['row_count'] <= MAX_ROWS, 'QUERY_ROW_BOUND')
                    on_row(row)
                pages += 1
                next_uri, first = following, False
            require(expected_columns is None or columns == list(expected_columns), 'QUERY_SCHEMA_MISSING')
            require(expected_types is None or types == list(expected_types), 'QUERY_SCHEMA_MISSING')
            record['elapsed_ns'] = time.perf_counter_ns() - clock_start
            record['result'] = 'passed'
            notify()
            return {'query_id': record['query_id'], 'columns': columns or [], 'column_types': types or [],
                    'row_count': record['row_count'], 'response_bytes': record['response_bytes'],
                    'elapsed_ns': record['elapsed_ns'], 'stats': deepcopy(record['stats'])}
        except BaseException as error:
            if isinstance(error, urllib.error.HTTPError):
                record['http_status'] = error.code
                error.close()
            if not sent:
                record['post_attempted'] = False
                record['result'] = 'not-sent'
            else:
                record['result'] = ('failed' if record['response_complete'] else 'unknown') if isinstance(error, Exception) else 'interrupted'
                if not record['response_complete']:
                    # A request accepted at the end of a network wait may still
                    # execute for the complete server limit after this point.
                    record['_not_before'] = time.monotonic() + server_seconds + 2
            if clock_start is not None:
                record['elapsed_ns'] = time.perf_counter_ns() - clock_start
            try:
                notify()
            except BaseException:
                pass
            if not isinstance(error, Exception):
                raise
            raise ClientError(str(error) if isinstance(error, ClientError) else 'QUERY_FAILED_OR_UNKNOWN') from None

    def receipt(self):
        return [{key: deepcopy(value) for key, value in row.items() if not key.startswith('_')}
                for row in self.records]
