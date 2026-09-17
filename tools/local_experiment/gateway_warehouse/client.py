"""Bounded Gateway-only workloads and independent per-backend routing oracles."""
import copy
from datetime import datetime, timezone
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .support import ExperimentError, canonical, digest, require, token
from .fixtures import backend_records

ORIGINS = {'gateway': 'http://127.0.0.1:18580', 'xs': 'http://127.0.0.1:18581', 's': 'http://127.0.0.1:18582'}
HEADER = 'X-Shirokuma-Warehouse'
QUERY_ID = re.compile(r'\d{8}_\d{6}_\d{5}_[a-z0-9]{5}')
NATIONS = ('ALGERIA', 'ARGENTINA', 'BRAZIL', 'CANADA', 'EGYPT', 'ETHIOPIA', 'FRANCE', 'GERMANY',
           'INDIA', 'INDONESIA', 'IRAN', 'IRAQ', 'JAPAN', 'JORDAN', 'KENYA', 'MOROCCO', 'MOZAMBIQUE',
           'PERU', 'CHINA', 'ROMANIA', 'SAUDI ARABIA', 'VIETNAM', 'RUSSIA', 'UNITED KINGDOM', 'UNITED STATES')
SELECT = ('SELECT n.node_id,n.node_version,n.coordinator,n.state,t.nationkey,t.name '
          'FROM system.runtime.nodes n CROSS JOIN tpch.tiny.nation t ORDER BY t.nationkey')
DROP = 'DROP TABLE tpch.tiny.nation'
SELECT_COLUMNS = [('node_id', 'varchar'), ('node_version', 'varchar'), ('coordinator', 'boolean'),
                  ('state', 'varchar'), ('nationkey', 'bigint'), ('name', 'varchar(25)')]
HISTORY_COLUMNS = [('query_id', 'varchar'), ('state', 'varchar'), ('user', 'varchar'), ('source', 'varchar'), ('error_code', 'varchar')]
CASES = (('xs_before', 'xs', 'success'), ('s_before', 's', 'success'),
         ('unknown', None, 'unmatched'), ('missing', None, 'unmatched'),
         ('xs_readonly', 'xs', 'denied'), ('s_readonly', 's', 'denied'),
         ('xs_after', 'xs', 'success'), ('s_after', 's', 'success'))
CASE_MAP = {name: (backend, outcome) for name, backend, outcome in CASES}
QUERY_SECONDS = 35
REGISTER_PATH = '/gateway/backend/modify/add'
HEALTH_PATH = '/webapp/getAllBackends'


def stamp():
    return datetime.now(timezone.utc).isoformat()


def node_id(run_token, backend):
    require(backend in {'xs', 's'}, 'BACKEND_INVALID')
    return 'shirokuma-vw-' + token(run_token) + '-' + backend


def source(run_token, case):
    require(case in CASE_MAP, 'CASE_INVALID')
    return 'shirokuma-vw-' + token(run_token) + '-' + case


def expected_rows(run_token, backend):
    return [[node_id(run_token, backend), '483', True, 'active', index, name] for index, name in enumerate(NATIONS)]


def next_uri(value, target, query_id):
    require(target in ORIGINS and type(value) is str and len(value) <= 1024
            and type(query_id) is str and QUERY_ID.fullmatch(query_id), 'UNSAFE_NEXT_URI')
    parsed = urllib.parse.urlsplit(value)
    require(parsed.scheme + '://' + parsed.netloc == ORIGINS[target] and not parsed.query and not parsed.fragment
            and re.fullmatch(r'/v1/statement/(queued|executing)/' + re.escape(query_id) + r'/[A-Za-z0-9_-]{1,256}/[0-9]{1,12}', parsed.path),
            'UNSAFE_NEXT_URI')
    return parsed.path


def parse_json(body):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'JSON_DUPLICATE_KEY')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite')
    try:
        return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError):
        raise ExperimentError('JSON_INVALID') from None


def registry_matches(actual, expected):
    if type(actual) is not list or len(actual) != len(expected):
        return False
    keys = {'name', 'proxyTo', 'externalUrl', 'routingGroup', 'active'}
    if any(type(row) is not dict or set(row) != keys or type(row['active']) is not bool
           or any(type(row[key]) is not str for key in keys - {'active'}) for row in actual):
        return False
    return canonical(sorted(actual, key=lambda row: row['name'])) == canonical(sorted(expected, key=lambda row: row['name']))


def history_oracle(run_token, cases, backends):
    require(type(cases) is list and [row.get('case') for row in cases] == [v[0] for v in CASES]
            and type(backends) is dict and set(backends) == {'xs', 's'}, 'HISTORY_INPUT_INVALID')
    expected, all_ids = {}, set()
    for row, (case, backend, outcome) in zip(cases, CASES):
        require(row.get('source') == source(run_token, case) and row.get('result') == outcome
                and row.get('response_complete') is True and row.get('matched') is True, 'CASE_NOT_VERIFIED')
        query_id = row.get('query_id')
        if backend is None:
            require(query_id is None and row.get('http_status') == 500, 'UNMATCHED_ROUTE_NOT_VERIFIED')
        else:
            require(type(query_id) is str and QUERY_ID.fullmatch(query_id) and query_id not in all_ids,
                    'HISTORY_QUERY_ID_INVALID')
            all_ids.add(query_id)
            expected[query_id] = {'backend': backend, 'source': row['source'],
                'state': 'FINISHED' if outcome == 'success' else 'FAILED',
                'error_code': None if outcome == 'success' else 'PERMISSION_DENIED'}
    observations, seen = {}, set()
    for backend, rows in backends.items():
        require(type(rows) is list and len(rows) <= len(CASES), 'HISTORY_ROWS_LIMIT')
        observations[backend] = []
        for row in rows:
            require(type(row) is list and len(row) == 5 and all(type(v) is str for v in row[:4])
                    and (row[4] is None or type(row[4]) is str), 'HISTORY_SCHEMA_INVALID')
            query_id, state, user, observed_source, error_code = row
            require(query_id in expected and query_id not in seen, 'HISTORY_UNEXPECTED_QUERY')
            wanted = expected[query_id]
            require(wanted['backend'] == backend and observed_source == wanted['source']
                    and user == 'shirokuma-vw-' + token(run_token) + '-worker', 'HISTORY_IDENTITY_MISMATCH')
            require(state == wanted['state'] and error_code == wanted['error_code'], 'HISTORY_OUTCOME_MISMATCH')
            seen.add(query_id)
            observations[backend].append({'query_id': query_id, 'source': observed_source, 'state': state, 'error_code': error_code})
    require(seen == all_ids, 'HISTORY_INCOMPLETE')
    return {'verified': True, 'observed_at': stamp(), 'query_count': len(seen), 'backends': observations,
            'unknown_source_counts': {'xs': 0, 's': 0}, 'missing_source_counts': {'xs': 0, 's': 0},
            'scope': 'fixed-workload-sources-on-both-disposable-backends'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ExperimentError('REDIRECT_REFUSED')


class Client:
    def __init__(self, run_token):
        self.run_token = token(run_token)
        self.worker = 'shirokuma-vw-' + self.run_token + '-worker'
        self.observer = 'shirokuma-vw-' + self.run_token + '-observer'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.callback = None
        self.journal = {'schema_version': 1, 'run_token': self.run_token, 'result': 'pending', 'cases': [],
                        'requests': [], 'pending': None, 'readiness': [], 'history': None,
                        'registration': None,
                        'identity_scope': 'synthetic-http-headers-not-authentication',
                        'server_max_run_seconds': 30, 'query_cleanup_verified': False}

    def receipt(self):
        value = copy.deepcopy(self.journal)
        value['uncaptured_requests'] = sum(row['query_id'] is None and not row['response_complete']
                                           for row in value['requests'])
        registration = value['registration']
        value['unconfirmed_registrations'] = sum(row['result'] != 'confirmed' for row in registration['attempts']) if registration else 0
        return value

    def notify(self):
        if self.callback is not None:
            self.callback(self.receipt())

    def fail(self, error):
        self.journal['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        self.journal['failure_code'] = str(error) if isinstance(error, ExperimentError) else 'WAREHOUSE_CLIENT_FAILED'
        try:
            self.notify()
        except BaseException:
            pass

    def request(self, target, path, *, data=None, headers=None, timeout=10):
        require(target in ORIGINS and (path in {'/v1/info', '/gateway/backend/all', '/v1/statement', REGISTER_PATH, HEALTH_PATH}
                or re.fullmatch(r'/v1/statement/(queued|executing)/[A-Za-z0-9_/-]{1,512}', path)), 'HTTP_TARGET_INVALID')
        require(path not in {'/gateway/backend/all', REGISTER_PATH, HEALTH_PATH} or target == 'gateway', 'HTTP_TARGET_INVALID')
        if path == REGISTER_PATH:
            require(data in {canonical(record).encode() for record in backend_records(self.run_token)}, 'REGISTRATION_SCOPE_INVALID')
        elif path == HEALTH_PATH:
            require(data == b'{}', 'HEALTH_REQUEST_INVALID')
        else:
            require(data is None or (path == '/v1/statement' and (data in {SELECT.encode(), DROP.encode()} if target == 'gateway'
                    else data == self._history_sql().encode())), 'QUERY_SCOPE_INVALID')
        require(type(timeout) in (int, float) and 0 < timeout <= 10, 'HTTP_TIMEOUT_INVALID')
        request = urllib.request.Request(ORIGINS[target] + path, data=data, headers=headers or {})
        try:
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(1024**2 + 1)
                status = response.status
        except (TimeoutError, OSError, urllib.error.URLError):
            raise ExperimentError('HTTP_TRANSPORT_FAILED') from None
        require(len(body) <= 1024**2, 'HTTP_RESPONSE_LIMIT')
        return {'status': status, 'body': body}

    def register_backends(self, progress=None, guard=None, timeout=120):
        require(callable(guard), 'REGISTRATION_GUARD_REQUIRED')
        require(self.journal['result'] == 'pending' and self.journal['registration'] is None
                and not self.journal['requests'] and type(timeout) in (int, float) and 0 < timeout <= 180,
                'REGISTRATION_SEQUENCE_INVALID')
        self.callback = progress
        registration = {'result': 'running', 'initial_registry_empty': False, 'attempts': [], 'readback_verified': False}
        self.journal['registration'] = registration
        deadline = time.monotonic() + timeout
        def get_registry():
            require(time.monotonic() < deadline, 'REGISTRATION_DEADLINE')
            result = self.request('gateway', '/gateway/backend/all', timeout=max(0.01, min(10, deadline - time.monotonic())))
            require(result['status'] == 200, 'REGISTRY_HTTP_INVALID')
            return parse_json(result['body'])
        try:
            self.journal['pending'] = {'operation': 'empty-registry-preflight'}
            self.notify()
            while True:
                try:
                    values = get_registry()
                    break
                except ExperimentError as error:
                    if str(error) != 'HTTP_TRANSPORT_FAILED' or time.monotonic() >= deadline:
                        raise
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            require(values == [], 'REGISTRY_NOT_EMPTY')
            registration['initial_registry_empty'] = True
            self.journal['pending'] = None
            self.notify()
            confirmed = []
            for record in backend_records(self.run_token):
                require(registry_matches(get_registry(), confirmed), 'REGISTRY_CHANGED_BEFORE_WRITE')
                attempt = {'name': record['name'], 'record_sha256': digest(record), 'result': 'pending', 'http_status': None}
                registration['attempts'].append(attempt)
                self.journal['pending'] = {'operation': 'register-backend', 'name': record['name']}
                self.notify()
                guard()  # The runner rechecks the port-forwarded Pod's recorded UID and scope.
                require(time.monotonic() < deadline, 'REGISTRATION_DEADLINE')
                response = self.request('gateway', REGISTER_PATH, data=canonical(record).encode(),
                                        headers={'Content-Type': 'application/json'},
                                        timeout=max(0.01, min(10, deadline - time.monotonic())))
                attempt.update(result='response-observed', http_status=response['status'], response_sha256=digest(response['body']))
                self.notify()
                # v21 HaGatewayResource.addBackend returns HTTP 200 and the
                # created ProxyBackendConfiguration, not a transport-only ack.
                require(response['status'] == 200 and registry_matches([parse_json(response['body'])], [record]),
                        'REGISTRATION_RESPONSE_INVALID')
                require(registry_matches(get_registry(), [*confirmed, record]), 'REGISTRATION_READBACK_MISMATCH')
                confirmed.append(record)
                attempt['result'] = 'confirmed'
                self.journal['pending'] = None
                self.notify()
            registration.update(result='verified', readback_verified=True, observed_at=stamp())
            self.notify()
            return copy.deepcopy(registration)
        except BaseException as error:
            registration['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
            self.fail(error)
            raise
        finally:
            self.callback = None

    def ready(self, timeout=180):
        require(type(timeout) in (int, float) and 0 < timeout <= 300, 'READINESS_TIMEOUT_INVALID')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                backends = {}
                for backend in ('xs', 's'):
                    response = self.request(backend, '/v1/info', timeout=max(0.01, min(10, deadline - time.monotonic())))
                    require(response['status'] == 200, 'READINESS_PENDING')
                    value = parse_json(response['body'])
                    require(type(value) is dict and value.get('nodeVersion', {}).get('version') == '483'
                            and type(value.get('starting')) is bool, 'TRINO_IDENTITY_MISMATCH')
                    require(value['starting'] is False, 'READINESS_PENDING')
                    backends[backend] = {'version': '483', 'starting': False}
                response = self.request('gateway', '/gateway/backend/all', timeout=max(0.01, min(10, deadline - time.monotonic())))
                require(response['status'] == 200, 'READINESS_PENDING')
                values = parse_json(response['body'])
                require(registry_matches(values, backend_records(self.run_token)), 'GATEWAY_REGISTRY_MISMATCH')
                # Registration marks a backend PENDING. A live backend and an
                # active registry row do not prove the router can select it yet.
                response = self.request('gateway', HEALTH_PATH, data=b'{}', headers={'Content-Type': 'application/json'},
                                        timeout=max(0.01, min(10, deadline - time.monotonic())))
                require(response['status'] == 200, 'READINESS_PENDING')
                value = parse_json(response['body'])
                require(type(value) is dict and type(value.get('code')) is int and value['code'] == 200
                        and type(value.get('data')) is list, 'GATEWAY_HEALTH_INVALID')
                health = value['data']
                require(all(type(v) is dict and set(v) == {'name', 'proxyTo', 'externalUrl', 'active', 'routingGroup',
                                                         'status', 'queued', 'running'} for v in health), 'GATEWAY_HEALTH_INVALID')
                registry = [{key: v[key] for key in ('name', 'proxyTo', 'externalUrl', 'active', 'routingGroup')} for v in health]
                require(registry_matches(registry, backend_records(self.run_token)), 'GATEWAY_HEALTH_IDENTITY_MISMATCH')
                require(all(v['status'] in {'HEALTHY', 'PENDING', 'UNHEALTHY', 'UNKNOWN'} for v in health), 'GATEWAY_HEALTH_INVALID')
                require(all(v['status'] == 'HEALTHY' for v in health), 'READINESS_PENDING')
                result = {'observed_at': stamp(), 'gateway_registered_backends': 2,
                          'gateway_backend_health': {size: 'HEALTHY' for size in ('xs', 's')}, 'backends': backends}
                self.journal['readiness'].append(result)
                self.notify()
                return copy.deepcopy(result)
            except ExperimentError as error:
                if str(error) not in {'HTTP_TRANSPORT_FAILED', 'READINESS_PENDING'}:
                    self.fail(error)
                    raise
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        error = ExperimentError('WAREHOUSE_NOT_READY')
        self.fail(error)
        raise error

    def _query(self, target, sql, case, query_source, user, route, *, expected_columns, row_limit,
               swap_route=False, unmatched=False, timeout=QUERY_SECONDS):
        require(target in ORIGINS and ((target == 'gateway' and case in CASE_MAP and sql in {SELECT, DROP})
                or (target in {'xs', 's'} and case == 'history' and sql == self._history_sql())), 'QUERY_SCOPE_INVALID')
        require(0 < timeout <= QUERY_SECONDS, 'QUERY_TIMEOUT_INVALID')
        row = {'case': case, 'target': target, 'source': query_source, 'query_id': None, 'http_status': None,
               'result': 'pending', 'response_complete': False, 'rows_observed': 0, 'followup_gets': 0,
               'opposite_route_gets': 0, 'sql_sha256': digest(sql.encode()), 'error_name': None}
        if case == 'unknown':
            row['legacy_routing_group'] = 'local-xs'
        self.journal['requests'].append(row)
        self.journal['pending'] = {'request_index': len(self.journal['requests']) - 1}
        self.notify()  # Durable pending receipt precedes even the first POST.
        headers = {'X-Trino-User': user, 'X-Trino-Source': query_source,
                   'X-Trino-Session': 'query_max_execution_time=20s,query_max_run_time=30s'}
        if case == 'unknown':
            headers['X-Trino-Routing-Group'] = 'local-xs'
        if route is not None:
            headers[HEADER] = route
        rows, columns, first, path, total = [], None, True, '/v1/statement', 0
        deadline, pages = time.monotonic() + timeout, 0
        while True:
            require(time.monotonic() < deadline and pages < 128, 'QUERY_DEADLINE')
            active_headers = dict(headers)
            if not first and swap_route:
                active_headers[HEADER] = 'S' if route == 'XS' else 'XS'
            response = self.request(target, path, data=sql.encode() if first else None, headers=active_headers,
                                    timeout=max(0.01, min(10, deadline - time.monotonic())))
            row['http_status'] = response['status']
            pages += 1
            total += len(response['body'])
            require(total <= 2 * 1024**2, 'QUERY_BYTES_LIMIT')
            if unmatched:
                # Gateway 21 throws when both the selected group and its default
                # have zero eligible backends. HTTP 500 alone is insufficient;
                # final verification also requires positive controls and no
                # workload source on either independent backend.
                require(first and response['status'] == 500, 'UNMATCHED_ROUTE_HTTP_INVALID')
                row.update(result='unmatched', response_complete=True, response_sha256=digest(response['body']))
                self.journal['pending'] = None
                self.notify()
                return copy.deepcopy(row), []
            require(response['status'] == 200, 'QUERY_HTTP_INVALID')
            if not first:
                row['followup_gets'] += 1
                row['opposite_route_gets'] += int(swap_route)
            first = False
            value = parse_json(response['body'])
            require(type(value) is dict, 'QUERY_RESPONSE_INVALID')
            query_id = value.get('id')
            require(type(query_id) is str and QUERY_ID.fullmatch(query_id)
                    and row['query_id'] in {None, query_id}, 'QUERY_ID_MISMATCH')
            row['query_id'] = query_id
            self.notify()  # Preserve a captured ID before validating later fields.
            data = value.get('data', [])
            require(type(data) is list and len(rows) + len(data) <= row_limit, 'QUERY_ROWS_LIMIT')
            rows.extend(data)
            row['rows_observed'] = len(rows)
            if value.get('columns') is not None:
                declared = value['columns']
                require(type(declared) is list and all(type(v) is dict for v in declared), 'QUERY_COLUMNS_INVALID')
                current = [(v.get('name'), v.get('type')) for v in declared]
                require(columns is None or current == columns, 'QUERY_COLUMNS_CHANGED')
                columns = current
            if value.get('error') is not None:
                error = value['error']
                require(type(error) is dict and error.get('errorName') == 'PERMISSION_DENIED'
                        and sql == DROP and rows == [] and value.get('nextUri') is None, 'QUERY_ERROR_UNEXPECTED')
                row.update(result='denied', error_name='PERMISSION_DENIED', response_complete=True)
                self.journal['pending'] = None
                self.notify()
                return copy.deepcopy(row), []
            uri = value.get('nextUri')
            if uri is None:
                require(columns == expected_columns, 'QUERY_RESULT_SCHEMA_INVALID')
                row.update(result='success', response_complete=True)
                self.journal['pending'] = None
                self.notify()
                return copy.deepcopy(row), rows
            path = next_uri(uri, target, query_id)
            self.notify()
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def run_cases(self, progress=None):
        require(self.journal['result'] == 'pending' and not self.journal['cases'] and self.journal['readiness'], 'CASE_SEQUENCE_INVALID')
        self.callback = progress
        self.journal['result'] = 'running'
        try:
            for case, backend, expected in CASES:
                started = time.monotonic_ns()
                route = backend.upper() if backend else 'UNKNOWN' if case == 'unknown' else None
                before = case.endswith('_before')
                result, rows = self._query('gateway', DROP if expected == 'denied' else SELECT, case,
                    source(self.run_token, case), self.worker, route, expected_columns=SELECT_COLUMNS,
                    row_limit=25, swap_route=before, unmatched=expected == 'unmatched')
                require(result['result'] == expected, 'CASE_RESULT_MISMATCH')
                if expected == 'success':
                    require(rows == expected_rows(self.run_token, backend)
                            and all(type(r[2]) is bool and type(r[4]) is int for r in rows), 'CASE_ROWS_MISMATCH')
                else:
                    require(rows == [], 'REJECTED_ROWS_OBSERVED')
                if before:
                    require(result['opposite_route_gets'] > 0, 'STICKINESS_UNOBSERVED')
                result.update(backend=backend, matched=True, rows_sha256=digest(rows),
                              duration_ms=max(0, (time.monotonic_ns() - started) // 1000000))
                self.journal['cases'].append(result)
                self.notify()
            self.journal['result'] = 'cases-verified'
            self.notify()
            return self.receipt()
        except BaseException as error:
            self.fail(error)
            raise
        finally:
            self.callback = None

    def _history_sql(self):
        sources = ','.join("'" + source(self.run_token, case) + "'" for case, _, _ in CASES)
        # Source is a fixed per-run namespace. Do not hide a misrouted request
        # merely because a proxy also changed its user; validate users afterward.
        return ('SELECT query_id,state,"user",source,error_code FROM system.runtime.queries WHERE source IN ('
                + sources + ') ORDER BY source,query_id')

    def verify_history(self, progress=None, timeout=10):
        require(self.journal['result'] == 'cases-verified' and type(timeout) in (int, float)
                and 0 < timeout <= 30, 'HISTORY_SEQUENCE_INVALID')
        self.callback = progress
        deadline = time.monotonic() + timeout
        try:
            for attempt in range(8):
                observations = {}
                for backend in ('xs', 's'):
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'HISTORY_DEADLINE')
                    _, observations[backend] = self._query(backend, self._history_sql(), 'history',
                        'shirokuma-vw-' + self.run_token + '-observe-' + backend + '-' + str(attempt), self.observer, None,
                        expected_columns=HISTORY_COLUMNS, row_limit=len(CASES), timeout=min(QUERY_SECONDS, remaining))
                try:
                    projected = history_oracle(self.run_token, self.journal['cases'], observations)
                except ExperimentError as error:
                    if str(error) != 'HISTORY_INCOMPLETE':
                        raise
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                    continue
                self.journal['history'] = projected
                self.journal['result'] = 'verified'
                self.journal['query_cleanup_verified'] = all(row['response_complete'] for row in self.journal['requests'])
                self.notify()
                return copy.deepcopy(projected)
            raise ExperimentError('HISTORY_INCOMPLETE')
        except BaseException as error:
            self.fail(error)
            raise
        finally:
            self.callback = None


WarehouseClient = Client
