"""Fixed-scope read transport. No arbitrary SQL, retry, or persisted raw responses.

Trino JSON fractions use Decimal; IDs and integer cells stay exact integers.
Unknown POSTs wait from the failed exchange plus the 30-second server bound.
Only IDs captured from this worker's response may be cancelled, after matching
the dedicated user AND source in a fresh runtime observation. Uncaptured IDs
are never adopted for cancellation. QueryResource's fixed DELETE is documented
in Trino 483 core/trino-main/src/main/java/io/trino/server/QueryResource.java.
The synthetic query identity is ownership correlation, not authentication.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tools.local_experiment.query_read import contract as C
from tools.local_experiment.iceberg_refs import client as AUTH
from tools.local_experiment.iceberg_refs import run as REFS

MAX_ROWS = 128
PAGE_BYTES = 1024**2
MAX_BYTES = 4 * PAGE_BYTES
MAX_PAGES = 128
SERVER_SECONDS = 30
QUERY_ID = re.compile(r'[0-9]{8}_[0-9]{6}_[0-9]{5}_[a-z0-9]{5}\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
STATES = {'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING',
          'RUNNING', 'FINISHING', 'FINISHED', 'FAILED'}
TERMINAL = {'FINISHED', 'FAILED'}
PAGE_KEYS = {'id', 'infoUri', 'partialCancelUri', 'nextUri', 'columns', 'data', 'stats',
             'warnings', 'error', 'updateType', 'updateCount'}
ERROR_CODES = frozenset('''AUTHENTICATION_RETRY_FORBIDDEN CANCEL_ID_NOT_OWNED
CANCEL_PROVENANCE_INVALID CANCEL_RESPONSE_INVALID CANCEL_RETRY_FORBIDDEN CANCEL_UNCERTAIN
CATALOG_AUTHENTICATION_FAILED CATALOG_CONTENT_TYPE_INVALID CATALOG_HTTP_FAILED
CATALOG_HTTP_ORIGIN_INVALID CATALOG_ORIGIN_INVALID CATALOG_READ_FAILED CATALOG_READ_LIMIT
CATALOG_READ_NOT_READY CATALOG_RESPONSE_INVALID CATALOG_RESPONSE_LIMIT CATALOG_SCOPE_INVALID
CONTROL_INVALID CONTROL_SQL_INVALID DRAIN_IDENTITY_INVALID DRAIN_INVENTORY_INVALID
DRAIN_PROVENANCE_INVALID DRAIN_SCHEMA_INVALID DRAIN_TIMEOUT_INVALID DRAIN_UNTRACKED_QUERY
DRAIN_DEADLINE DRAIN_UNVERIFIED JOURNAL_AND_GUARD_REQUIRED JOURNAL_REQUIRED
QUERY_CELL_TYPE_INVALID QUERY_COLUMNS_CHANGED QUERY_COLUMNS_INVALID QUERY_CONTENT_TYPE_INVALID
QUERY_CONTINUATION_CHANGED QUERY_DEADLINE QUERY_ERROR_INVALID QUERY_HTTP_ORIGIN_INVALID
QUERY_ID_INVALID QUERY_INPUT_INVALID QUERY_LABEL_INVALID QUERY_NEXT_INVALID QUERY_NOT_READ_ONLY
QUERY_ORIGIN_CHANGED QUERY_OUTCOME_UNRESOLVED QUERY_PAGE_BOUND QUERY_PAGE_BYTES_BOUND
QUERY_RESPONSE_INVALID QUERY_ROWS_INVALID QUERY_ROW_BOUND QUERY_SCHEMA_MISSING QUERY_STATE_INVALID
QUERY_TERMINAL_INVALID QUERY_TOTAL_BYTES_BOUND READ_SQL_INVALID RESPONSE_BYTES_BOUND
RESPONSE_DEADLINE RESPONSE_DUPLICATE_KEY RESPONSE_JSON_INVALID RESPONSE_NONFINITE
TIMEOUT_INVALID TOKEN_INVALID TRINO_QUERY_FAILED TRINO_SERVICE_CHANGED TRINO_SERVICE_INVALID
URL_INVALID QUERY_FAILED INTERRUPTED'''.split())


class ClientError(RuntimeError):
    """Fixed public code only; no upstream error text."""


def require(condition, code):
    if not condition:
        raise ClientError(code)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def first_error(original, later):
    """The earliest non-Exception cancellation outranks ordinary failures."""
    return later if original is None or isinstance(original, Exception) and not isinstance(later, Exception) else original


def save_failure(error, callback):
    try:
        callback()
    except BaseException as later:
        return first_error(error, later)
    return error


def token_id(token):
    require(type(token) is str and TOKEN.fullmatch(token), 'TOKEN_INVALID')
    return token


def identity(token, control=False):
    require(type(control) is bool, 'CONTROL_INVALID')
    token_id(token)
    return ('shirokuma-query-read-' + ('control-' if control else '') + token,
            'shirokuma-query-read-' + ('control-' if control else '') + token)


def decode(raw, *, decimals=False):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'RESPONSE_DUPLICATE_KEY')
            result[key] = value
        return result
    def fraction(value):
        parsed = Decimal(value) if decimals else float(value)
        require(parsed.is_finite() if decimals else math.isfinite(parsed), 'RESPONSE_NONFINITE')
        return parsed
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_float=fraction,
                          parse_constant=lambda _: (_ for _ in ()).throw(ClientError('RESPONSE_NONFINITE')))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ClientError('RESPONSE_JSON_INVALID') from None


def bounded_read(response, maximum, deadline):
    result = bytearray()
    read = getattr(response, 'read1', response.read)
    while len(result) <= maximum:
        require(time.monotonic() < deadline, 'RESPONSE_DEADLINE')
        chunk = read(min(65536, maximum + 1 - len(result)))
        require(time.monotonic() <= deadline, 'RESPONSE_DEADLINE')
        if not chunk:
            break
        result.extend(chunk)
    require(len(result) <= maximum, 'RESPONSE_BYTES_BOUND')
    return bytes(result)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(url):
    require(type(url) is str, 'URL_INVALID')
    try:
        p = urllib.parse.urlsplit(url)
        require(p.scheme == 'http' and p.hostname == '127.0.0.1' and p.username is None and
                p.password is None and not p.path and not p.query and not p.fragment and
                p.port is not None and 1024 <= p.port <= 65535 and
                url == 'http://127.0.0.1:' + str(p.port), 'URL_INVALID')
    except ValueError:
        raise ClientError('URL_INVALID') from None
    return p.scheme, p.netloc


def inspection_sql(token):
    user, _ = identity(token)
    # Include source in result validation, not the WHERE, so changed provenance
    # cannot disappear from the observation. Include terminal states too.
    return ('SELECT query_id, state, "user", source FROM system.runtime.queries '
            f'WHERE "user" = \'{user}\' ORDER BY query_id LIMIT 129')


def allowed_sql(sql, label, token, control):
    require(type(sql) is str and 0 < len(sql.encode()) <= 16384 and type(label) is str, 'QUERY_INPUT_INVALID')
    if control:
        require(label == 'drain' and sql == inspection_sql(token), 'CONTROL_SQL_INVALID')
        return
    require(label in ('inventory', 'schema_before', 'schema_after', 'select'), 'QUERY_LABEL_INVALID')
    if label == 'inventory':
        require(sql in [C.discovery_sql(C.request('tables', schema=s)) for s in C.SCHEMAS], 'READ_SQL_INVALID')
        return
    if label in ('schema_before', 'schema_after'):
        match = re.fullmatch(r'.* WHERE table_schema = \'(smoke|local_experiment)\' AND table_name = \'([A-Za-z_][A-Za-z0-9_]{0,62})\' ORDER BY ordinal_position LIMIT 33', sql)
        require(match is not None and sql == C.schema_sql(C.request('describe', table='.'.join(match.groups()))), 'READ_SQL_INVALID')
        return
    match = re.fullmatch(r'SELECT (.+) FROM "iceberg"\."(smoke|local_experiment)"\."([A-Za-z_][A-Za-z0-9_]{0,62})" FOR VERSION AS OF ([1-9][0-9]{0,18}) LIMIT ([1-9][0-9]{0,2})', sql)
    require(match is not None, 'READ_SQL_INVALID')
    raw_columns, schema, table, snapshot, limit = match.groups()
    columns = raw_columns.split(', ')
    require(1 <= len(columns) <= C.MAX_SELECTED_COLUMNS and len(set(columns)) == len(columns) and
            all(re.fullmatch(r'"[A-Za-z_][A-Za-z0-9_]{0,62}"', name) for name in columns) and
            0 < int(snapshot) <= 2**63 - 1 and 2 <= int(limit) <= 101, 'READ_SQL_INVALID')
    C.normalize_request({'command': 'select', 'table': schema + '.' + table,
                         'columns': [s[1:-1] for s in columns], 'limit': int(limit)-1})


def cell(value, kind):
    if value is None:
        return
    if kind in ('integer', 'bigint'):
        width = 31 if kind == 'integer' else 63
        valid = type(value) is int and -2**width <= value < 2**width
    elif kind == 'boolean':
        valid = type(value) is bool
    elif kind == 'varchar':
        valid = type(value) is str and len(value.encode('utf-8')) <= C.MAX_CELL_BYTES
    elif kind == 'date':
        try:
            valid = type(value) is str and date.fromisoformat(value).isoformat() == value
        except ValueError:
            valid = False
    else:
        valid = type(value) in (Decimal, int) or kind.startswith('decimal(') and type(value) is str
        if valid:
            try:
                valid = Decimal(value).is_finite()
            except Exception:
                valid = False
    require(valid, 'QUERY_CELL_TYPE_INVALID')


class QueryClient:
    def __init__(self, url, token, *, control=False, progress=lambda: None, guard=lambda: None,
                 opener=None, timeout=45):
        self.origin = local_url(url)
        self.user, self.source = identity(token, control)
        require(callable(progress) and callable(guard), 'JOURNAL_AND_GUARD_REQUIRED')
        require(type(timeout) in (int, float) and 0 < timeout <= 45, 'TIMEOUT_INVALID')
        self.url, self.token, self.control, self.timeout = url, token, control, timeout
        self.progress, self.guard = progress, guard
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.records, self.cancellations = [], []

    def receipt(self):
        return {'user': self.user, 'source': self.source,
                'records': [{k: deepcopy(v) for k, v in row.items() if not k.startswith('_')} for row in self.records],
                'cancellations': deepcopy(self.cancellations)}

    def _headers(self):
        return {'Accept': 'application/json', 'X-Trino-User': self.user, 'X-Trino-Source': self.source,
                'X-Trino-Session': 'query_max_execution_time=30s,query_max_run_time=30s'}

    def _validate_sql(self, sql, label):
        """Fixed Reader policy; bounded observation profiles may narrow this."""
        allowed_sql(sql, label, self.token, self.control)

    def _validate_cell(self, value, kind, label):
        cell(value, kind)

    def _column_type(self, value, label):
        """Reader scalar policy; observation profiles may normalize fixed types."""
        return C.scalar_type(value)

    def query(self, sql, label):
        self._validate_sql(sql, label)
        require(not any(r['post_attempted'] and not r['response_complete'] for r in self.records), 'QUERY_OUTCOME_UNRESOLVED')
        row = {'operation_id': str(uuid.uuid4()), 'label': label, 'query_id': None,
               'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(), 'started_at': stamp(),
               'result': 'pending', 'response_complete': False, 'post_attempted': False,
               'server_max_run_seconds': SERVER_SECONDS, 'http_status': None,
               'row_count': 0, 'response_bytes': 0, 'pages': 0, 'error_code': None}
        self.records.append(row)
        started = time.monotonic(); deadline = started + self.timeout
        following, first, sent = self.url + '/v1/statement', True, False
        columns, types, rows = None, None, []
        visited = set()
        try:
            self.guard(); self.progress()
            row['post_attempted'] = True
            self.progress()  # Durable intent must precede the sole POST.
            while following is not None:
                require(row['pages'] < MAX_PAGES and following not in visited, 'QUERY_PAGE_BOUND')
                visited.add(following)
                target = urllib.parse.urlsplit(following)
                require((target.scheme, target.netloc) == self.origin and target.username is None and
                        target.password is None and not target.query and not target.fragment, 'QUERY_ORIGIN_CHANGED')
                if not first:
                    match = re.fullmatch(r'/v1/statement/(?:queued|executing)/([^/]+)/[A-Za-z0-9_-]{1,256}/[0-9]{1,12}', target.path)
                    require(match is not None and match.group(1) == row['query_id'], 'QUERY_CONTINUATION_CHANGED')
                self.guard()
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'QUERY_DEADLINE')
                req = urllib.request.Request(following, data=sql.encode() if first else None,
                                             headers=self._headers(), method='POST' if first else 'GET')
                sent = True
                with self.opener.open(req, timeout=min(remaining, 10)) as response:
                    row['http_status'] = response.status
                    require(response.status == 200 and response.geturl() == following, 'QUERY_HTTP_ORIGIN_INVALID')
                    require(response.headers.get('Content-Type', '').split(';')[0].strip().lower() == 'application/json', 'QUERY_CONTENT_TYPE_INVALID')
                    raw = bounded_read(response, PAGE_BYTES, deadline)
                require(time.monotonic() <= deadline, 'QUERY_DEADLINE')
                require(len(raw) <= PAGE_BYTES, 'QUERY_PAGE_BYTES_BOUND')
                row['response_bytes'] += len(raw); row['pages'] += 1
                require(row['response_bytes'] <= MAX_BYTES, 'QUERY_TOTAL_BYTES_BOUND')
                page = decode(raw, decimals=True)
                require(type(page) is dict and set(page) <= PAGE_KEYS, 'QUERY_RESPONSE_INVALID')
                qid = page.get('id')
                require(type(qid) is str and QUERY_ID.fullmatch(qid) and row['query_id'] in (None, qid), 'QUERY_ID_INVALID')
                row['query_id'] = qid
                self.progress()  # Capture accepted ID before decoding results.
                stats = page.get('stats')
                require(type(stats) is dict and type(stats.get('state')) is str and stats['state'] in STATES, 'QUERY_STATE_INVALID')
                following = page.get('nextUri')
                require(following is None or type(following) is str and 0 < len(following) <= 2048, 'QUERY_NEXT_INVALID')
                terminal = following is None and stats['state'] in TERMINAL
                require((following is None) == terminal, 'QUERY_TERMINAL_INVALID')
                require(page.get('updateType') is None and page.get('updateCount') is None, 'QUERY_NOT_READ_ONLY')
                if 'error' in page:
                    require(terminal and stats['state'] == 'FAILED' and type(page['error']) is dict, 'QUERY_ERROR_INVALID')
                    row['response_complete'] = True
                    raise ClientError('TRINO_QUERY_FAILED')
                require(stats['state'] != 'FAILED', 'QUERY_TERMINAL_INVALID')
                if 'columns' in page:
                    cs = page['columns']
                    require(type(cs) is list and 1 <= len(cs) <= 32 and all(type(c) is dict and
                            set(c) <= {'name', 'type', 'typeSignature'} and type(c.get('name')) is str and
                            0 < len(c['name']) <= 128 and type(c.get('type')) is str for c in cs), 'QUERY_COLUMNS_INVALID')
                    new_columns = [c['name'] for c in cs]
                    new_types = [self._column_type(c['type'], label) for c in cs]
                    require(len(set(new_columns)) == len(new_columns) and (columns is None or
                            columns == new_columns and types == new_types), 'QUERY_COLUMNS_CHANGED')
                    columns, types = new_columns, new_types
                data = page.get('data', [])
                require(type(data) is list and (not data or columns is not None), 'QUERY_ROWS_INVALID')
                for item in data:
                    require(type(item) is list and len(item) == len(columns), 'QUERY_ROWS_INVALID')
                    require(len(rows) < MAX_ROWS, 'QUERY_ROW_BOUND')
                    for value, kind in zip(item, types): self._validate_cell(value, kind, label)
                    rows.append(item)
                row['row_count'] = len(rows)
                if terminal:
                    require(columns is not None, 'QUERY_SCHEMA_MISSING')
                    row['response_complete'] = True
                first = False
            self.guard()
            row['result'] = 'finished'; self.progress()
            return {'query_id': row['query_id'], 'columns': columns, 'column_types': types, 'rows': rows}
        except BaseException as error:
            if isinstance(error, urllib.error.HTTPError):
                row['http_status'] = error.code; error.close()
            row['result'] = ('failed' if row['response_complete'] else 'unknown') if sent else 'not-sent'
            if not isinstance(error, Exception): row['result'] = 'interrupted'
            if not sent: row['post_attempted'] = False
            if sent and not row['response_complete']:
                row['_not_before'] = time.monotonic() + SERVER_SECONDS + 2
            row['error_code'] = str(error) if isinstance(error, ClientError) and str(error) in ERROR_CODES else 'QUERY_FAILED'
            error = save_failure(error, self.progress)
            if not isinstance(error, Exception):
                row['result'] = 'interrupted'; row['error_code'] = 'INTERRUPTED'
                raise error
            raise ClientError(row['error_code']) from None

    def _cancel(self, query_id, observed):
        require(not self.control and any(r['query_id'] == query_id and r['post_attempted'] for r in self.records), 'CANCEL_ID_NOT_OWNED')
        require(observed == [query_id, observed[1], self.user, self.source] and observed[1] in STATES - TERMINAL,
                'CANCEL_PROVENANCE_INVALID')
        require(not any(r['query_id'] == query_id for r in self.cancellations), 'CANCEL_RETRY_FORBIDDEN')
        record = {'query_id': query_id, 'result': 'pending', 'http_status': None}
        self.cancellations.append(record)
        try:
            self.guard(); self.progress()
            url = self.url + '/v1/query/' + query_id
            with self.opener.open(urllib.request.Request(url, headers=self._headers(), method='DELETE'), timeout=10) as response:
                record['http_status'] = response.status
                require(response.status == 204 and response.geturl() == url and not response.read(1), 'CANCEL_RESPONSE_INVALID')
            record['result'] = 'accepted'; self.progress()
        except BaseException as error:
            if isinstance(error, urllib.error.HTTPError): error.close()
            record['result'] = 'unknown'
            error = save_failure(error, self.progress)
            if not isinstance(error, Exception): raise error
            raise ClientError('CANCEL_UNCERTAIN') from None


def drain(control, worker, token, timeout=75):
    require(worker.token == control.token == token and not worker.control and control.control and
            (worker.user, worker.source) == identity(token) and (control.user, control.source) == identity(token, True), 'DRAIN_IDENTITY_INVALID')
    require(type(timeout) in (int, float) and 0 < timeout <= 75, 'DRAIN_TIMEOUT_INVALID')
    result = {'verified': False, 'user': worker.user, 'source': worker.source, 'active': [],
              'cancelled': [], 'cancel_errors': [], 'errors': [], 'observed_query_ids': []}
    deadline = time.monotonic() + timeout
    unknown = any(r['post_attempted'] and r['query_id'] is None for r in worker.records)
    unknown_until = max((r.get('_not_before', 0) for r in worker.records if r['post_attempted'] and
                        r['query_id'] is None and not r['response_complete']), default=0)
    known = {r['query_id'] for r in worker.records if r['query_id'] is not None}
    try:
        while time.monotonic() < deadline:
            old_timeout = control.timeout
            control.timeout = min(old_timeout, max(.01, deadline-time.monotonic()))
            try: observed = control.query(inspection_sql(token), 'drain')
            finally: control.timeout = old_timeout
            require(observed['columns'] == ['query_id', 'state', 'user', 'source'] and
                    observed['column_types'] == ['varchar'] * 4 and len(observed['rows']) <= 128, 'DRAIN_SCHEMA_INVALID')
            rows = observed['rows']; ids = []
            for row in rows:
                require(type(row) is list and len(row) == 4 and type(row[0]) is str and QUERY_ID.fullmatch(row[0]) and
                        row[1] in STATES and row[2:] == [worker.user, worker.source], 'DRAIN_PROVENANCE_INVALID')
                require(row[0] in known or unknown, 'DRAIN_UNTRACKED_QUERY')
                ids.append(row[0])
            require(ids == sorted(set(ids)), 'DRAIN_INVENTORY_INVALID')
            result['observed_query_ids'] = sorted(set(result['observed_query_ids']) | set(ids))
            active = [row for row in rows if row[1] not in TERMINAL]
            result['active'] = [row[0] for row in active]
            if not active and time.monotonic() >= unknown_until:
                worker.guard(); control.guard()
                result['verified'] = True
                return result
            for row in active:
                qid = row[0]
                if qid not in known or qid in result['cancelled'] or qid in result['cancel_errors']:
                    continue
                try:
                    worker._cancel(qid, row); result['cancelled'].append(qid)
                except Exception:
                    result['cancel_errors'].append(qid)
            time.sleep(min(.25, max(0, deadline-time.monotonic())))
        result['errors'].append('DRAIN_DEADLINE')
    except Exception:
        result['errors'].append('DRAIN_UNVERIFIED')
    return result


class _ReadAuthentication(AUTH._Authentication):
    """Keep the fixed existing OAuth flow; add only bounded table GETs."""
    def exchange(self, method, path, body=None, headers=None):
        if method == 'POST':
            require(path == AUTH.TOKEN_PATH, 'CATALOG_SCOPE_INVALID')
            return super().exchange(method, path, body, headers)
        require(method == 'GET' and body is None and headers is None and
                re.fullmatch(r'/api/catalog/v1/shirokuma_l1/namespaces/(?:smoke|local_experiment)/tables/[A-Za-z_][A-Za-z0-9_]{0,62}', path),
                'CATALOG_SCOPE_INVALID')
        require(self.base == AUTH.BASE_URL, 'CATALOG_ORIGIN_INVALID')
        self.guard()
        url = AUTH.BASE_URL + path
        request = urllib.request.Request(url, method='GET', headers={
            'Accept': 'application/json', 'Polaris-Realm': AUTH._header(self.realm, 'REALM_INVALID', 256),
            'Authorization': 'Bearer ' + AUTH._header(self.token, 'TOKEN_INVALID', 16384)})
        deadline = time.monotonic() + 20
        try:
            with self.opener.open(request, timeout=10) as response:
                require(response.status == 200 and response.geturl() == url, 'CATALOG_HTTP_ORIGIN_INVALID')
                require(response.headers.get('Content-Type', '').split(';')[0].strip().lower() == 'application/json', 'CATALOG_CONTENT_TYPE_INVALID')
                value = decode(bounded_read(response, C.MAX_METADATA_BYTES, deadline))
            require(type(value) is dict, 'CATALOG_RESPONSE_INVALID')
            return value
        except urllib.error.HTTPError as error:
            error.close()
            raise ClientError('CATALOG_HTTP_FAILED') from None


class CatalogRead:
    def __init__(self, guard, *, progress=lambda: None, opener=None):
        require(callable(guard) and callable(progress), 'JOURNAL_AND_GUARD_REQUIRED')
        self.guard, self.progress = guard, progress
        self._auth = _ReadAuthentication(guard, opener)
        self._journal = {'authenticated': False, 'authentication_attempted': False, 'reads': []}

    def receipt(self):
        return deepcopy(self._journal)

    def authenticate(self):
        require(not self._journal['authentication_attempted'], 'AUTHENTICATION_RETRY_FORBIDDEN')
        self.guard(); self._journal['authentication_attempted'] = True; self.progress()
        try:
            self._auth.authenticate(); self.guard()
            self._journal['authenticated'] = True; self.progress()
        except BaseException as error:
            if not isinstance(error, Exception): raise
            raise ClientError('CATALOG_AUTHENTICATION_FAILED') from None

    def load(self, request):
        request = C.normalize_request(request)
        require(request['command'] != 'tables' and self._journal['authenticated'], 'CATALOG_READ_NOT_READY')
        require(len(self._journal['reads']) < 2, 'CATALOG_READ_LIMIT')
        schema, table = request['table'].split('.')
        path = '/api/catalog/v1/shirokuma_l1/namespaces/' + schema + '/tables/' + table
        record = {'request_sha256': C.digest(request), 'result': 'pending', 'metadata_sha256': None}
        self._journal['reads'].append(record)
        try:
            self.guard(); self.progress()
            response = self._auth.exchange('GET', path)
            require(type(response) is dict and set(response) <= {'metadata', 'metadata-location', 'config', 'storage-credentials'} and
                    type(response.get('metadata')) is dict and type(response.get('metadata-location')) is str, 'CATALOG_RESPONSE_INVALID')
            # Drop REST config and credential material before returning to owner.
            safe = {k: response[k] for k in ('metadata', 'metadata-location')}
            require(len(C.canonical(safe)) <= C.MAX_METADATA_BYTES, 'CATALOG_RESPONSE_LIMIT')
            self.guard(); record['metadata_sha256'] = C.digest(safe); record['result'] = 'verified'; self.progress()
            return safe
        except BaseException as error:
            record['result'] = 'failed'
            error = save_failure(error, self.progress)
            if not isinstance(error, Exception): raise error
            raise ClientError('CATALOG_READ_FAILED') from None

    def close(self):
        self._auth.token = ''; self._auth.realm = ''


def trino_service_identity():
    raw = REFS.command('kubectl', '--context', REFS.CONTEXT, '-n', REFS.NAMESPACE,
                       '--request-timeout=20s', 'get', 'service', 'trino-local', '-o', 'json')
    value = decode(raw); meta, spec = value.get('metadata', {}), value.get('spec', {})
    require(value.get('kind') == 'Service' and value.get('apiVersion') == 'v1' and
            meta.get('name') == 'trino-local' and meta.get('namespace') == REFS.NAMESPACE and
            not meta.get('deletionTimestamp') and type(meta.get('uid')) is str, 'TRINO_SERVICE_INVALID')
    require(str(uuid.UUID(meta['uid'])) == meta['uid'] and spec.get('type') == 'ClusterIP' and
            spec.get('selector') == {'app.kubernetes.io/name': 'trino-local'} and
            type(spec.get('ports')) is list and len(spec['ports']) == 1 and
            spec['ports'][0].get('port') == 8080 and spec['ports'][0].get('targetPort') == 'http' and
            spec['ports'][0].get('protocol') == 'TCP', 'TRINO_SERVICE_INVALID')
    return {'name': meta['name'], 'namespace': meta['namespace'], 'uid': meta['uid'], 'spec_sha256': C.digest(spec)}


@contextmanager
def runtime(token, progress, *, _worker_factory=None):
    token_id(token); require(callable(progress), 'JOURNAL_REQUIRED')
    worker_factory = QueryClient if _worker_factory is None else _worker_factory
    require(callable(worker_factory), 'JOURNAL_AND_GUARD_REQUIRED')
    journal = {'state': 'pending', 'cleanup': {'verified': False}, 'trino': None, 'polaris': None}
    stack = ExitStack(); clients = None; error = None; cleanup_error = None
    def snapshot():
        result = deepcopy(journal)
        if clients:
            result.update(worker=clients[0].receipt(), control=clients[1].receipt(), catalog=clients[2].receipt())
        progress(result)
    def polaris_progress(value):
        journal['polaris'] = deepcopy(value); snapshot()
    try:
        REFS.SESSION.check_context(); before = trino_service_identity()
        journal['trino'] = {'service': before, 'state': 'pending'}; snapshot()
        url = stack.enter_context(REFS.SESSION.trino_tunnel())
        def trino_guard():
            require(trino_service_identity() == before, 'TRINO_SERVICE_CHANGED')
        trino_guard(); journal['trino']['state'] = 'ready'; snapshot()
        polaris_guard = stack.enter_context(REFS.polaris_tunnel(polaris_progress))
        clients = (worker_factory(url, token, progress=snapshot, guard=trino_guard),
                   QueryClient(url, token, control=True, progress=snapshot, guard=trino_guard),
                   CatalogRead(polaris_guard, progress=snapshot))
        journal['state'] = 'ready'; snapshot()
        yield clients
        trino_guard(); polaris_guard()
    except BaseException as caught:
        error = caught
    finally:
        if clients:
            try:
                trino_guard(); polaris_guard()
            except BaseException as caught:
                cleanup_error = caught; error = first_error(error, caught)
        try:
            stack.close()
        except BaseException as caught:
            cleanup_error = first_error(cleanup_error, caught); error = first_error(error, caught)
        if clients: clients[2].close()
        journal['state'] = 'verified' if error is None else 'failed'
        journal['cleanup']['verified'] = cleanup_error is None
        if journal['trino']: journal['trino']['state'] = journal['state']
        try: snapshot()
        except BaseException as caught:
            error = first_error(error, caught)
    if error is not None:
        raise error
