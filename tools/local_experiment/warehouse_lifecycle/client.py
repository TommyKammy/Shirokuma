"""Fixed Gateway control and complete host query ledger for the local lifecycle drill.

Only the generated Gateway receives SQL. Direct Trino requests are bounded,
read-only /v1/info and unfiltered /v1/query observations. This is a single
trusted producer experiment, not a general admission or authentication system.
"""
import copy
import ipaddress
import re
import time
import urllib.error
import urllib.request
import uuid

from tools.local_experiment.trino_opa.common import ExperimentError, canonical, digest, require, token
from tools.local_experiment.gateway_warehouse.client import (
    HEADER, NATIONS, QUERY_ID, SELECT, SELECT_COLUMNS, NoRedirect, next_uri,
    parse_json, registry_matches, stamp,
)

ORIGINS = {'gateway': 'http://127.0.0.1:18580', 'xs': 'http://127.0.0.1:18581', 's': 'http://127.0.0.1:18582'}
CASES = {'xs_before': 'xs', 's_before': 's', 'xs_hold': 'xs', 's_chilled': 's',
         'xs_unavailable': 'xs', 'xs_after': 'xs', 's_after': 's'}
TERMINAL = {'FINISHED', 'FAILED'}
STATES = TERMINAL | {'QUEUED', 'WAITING_FOR_RESOURCES', 'DISPATCHING', 'PLANNING', 'STARTING', 'RUNNING', 'FINISHING'}
REGISTER = '/gateway/backend/modify/add'
HEALTH = '/webapp/getAllBackends'
MAX_BODY = 1024 * 1024
MAX_QUERIES = 64


def name(run_token, size):
    require(size in {'xs', 's'}, 'WAREHOUSE_INVALID')
    return 'shirokuma-wl-' + token(run_token) + '-' + size


def records(run_token, service_ips, active=None):
    require(type(service_ips) is dict and set(service_ips) == {'xs', 's'}, 'SERVICE_IPS_INVALID')
    active = {'xs': True, 's': True} if active is None else active
    require(type(active) is dict and set(active) == {'xs', 's'}
            and all(type(v) is bool for v in active.values()), 'ACTIVE_MAP_INVALID')
    result = []
    for size, port in [('xs', 18581), ('s', 18582)]:
        value = service_ips[size]
        try:
            address = ipaddress.ip_address(value)
        except (ValueError, TypeError):
            raise ExperimentError('SERVICE_IPS_INVALID') from None
        require(type(value) is str and isinstance(address, ipaddress.IPv4Address)
                and str(address) == value and address.is_private and not address.is_loopback
                and not address.is_unspecified and not address.is_link_local and not address.is_multicast,
                'SERVICE_IPS_INVALID')
        url = 'http://' + value + ':' + str(port)
        result.append({'name': name(run_token, size), 'proxyTo': url, 'externalUrl': url,
                       'routingGroup': 'local-' + size, 'active': active[size]})
    require(service_ips['xs'] != service_ips['s'], 'SERVICE_IPS_INVALID')
    return result


def expected_rows(run_token, size):
    return [[name(run_token, size), '483', True, 'active', index, nation]
            for index, nation in enumerate(NATIONS)]


class Client:
    cases = CASES

    def __init__(self, run_token, service_ips):
        self.run_token = token(run_token)
        self.service_ips = copy.deepcopy(service_ips)
        records(self.run_token, service_ips)
        self.worker = 'shirokuma-wl-' + self.run_token + '-worker'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.active = {'xs': True, 's': True}
        self.progress = None
        self.handles = {}
        self.epoch = 0
        self.last_activity = time.monotonic()
        self.journal = {'schema_version': 1, 'run_token': self.run_token, 'result': 'pending',
                        'registration': None, 'controls': [], 'requests': [], 'readiness': [],
                        'query_snapshots': [], 'pending': None, 'server_client_timeout_seconds': 20,
                        'scope': 'one-host-producer-with-all-post-intents-and-exclusive-run-lock'}

    def receipt(self):
        value = copy.deepcopy(self.journal)
        value['ledger'] = self.ledger()
        return value

    def notify(self):
        if self.progress is not None:
            self.progress(self.receipt())

    def ledger(self):
        rows = self.journal['requests']
        return {'epoch': self.epoch, 'pending_handles': sum(not r['response_complete'] for r in rows),
                'uncaptured_requests': sum(not r['response_complete'] and r['query_id'] is None for r in rows),
                'all_results_consumed': all(r['response_complete'] for r in rows)}

    def request(self, target, path, *, data=None, headers=None, timeout=10):
        require(target in ORIGINS and type(timeout) in (int, float) and 0 < timeout <= 10, 'HTTP_TARGET_INVALID')
        controls = {'/gateway/backend/' + operation + '/' + name(self.run_token, 'xs')
                    for operation in ('activate', 'deactivate')}
        if target == 'gateway':
            if path == REGISTER:
                require(data in {canonical(row).encode() for row in records(self.run_token, self.service_ips)}, 'REGISTRATION_SCOPE_INVALID')
            elif path == HEALTH or path in controls:
                require(data == b'{}' if path == HEALTH else data == b'', 'CONTROL_BODY_INVALID')
            elif path == '/gateway/backend/all':
                require(data is None, 'HTTP_TARGET_INVALID')
            elif path == '/v1/statement':
                require(data == SELECT.encode(), 'SQL_SCOPE_INVALID')
            else:
                require(re.fullmatch(r'/v1/statement/(queued|executing)/[A-Za-z0-9_/-]{1,512}', path)
                        and data is None, 'HTTP_TARGET_INVALID')
        else:
            require(path in {'/v1/info', '/v1/query'} and data is None, 'HTTP_TARGET_INVALID')
        request = urllib.request.Request(ORIGINS[target] + path, data=data, headers=headers or {})
        try:
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(MAX_BODY + 1)
                status = response.status
        except (TimeoutError, OSError, urllib.error.URLError):
            raise ExperimentError('HTTP_TRANSPORT_FAILED') from None
        require(len(body) <= MAX_BODY, 'HTTP_RESPONSE_LIMIT')
        return {'status': status, 'body': body}

    def registry(self):
        response = self.request('gateway', '/gateway/backend/all')
        require(response['status'] == 200, 'REGISTRY_HTTP_INVALID')
        values = parse_json(response['body'])
        require(registry_matches(values, records(self.run_token, self.service_ips, self.active)), 'REGISTRY_MISMATCH')
        return values

    def health(self):
        self.registry()
        response = self.request('gateway', HEALTH, data=b'{}', headers={'Content-Type': 'application/json'})
        require(response['status'] == 200, 'HEALTH_HTTP_INVALID')
        value = parse_json(response['body'])
        require(type(value) is dict and type(value.get('code')) is int and value['code'] == 200
                and type(value.get('data')) is list, 'HEALTH_INVALID')
        rows = value['data']
        keys = {'name', 'proxyTo', 'externalUrl', 'active', 'routingGroup'}
        require(all(type(row) is dict and set(row) == keys | {'status', 'queued', 'running'} for row in rows), 'HEALTH_INVALID')
        require(registry_matches([{k: row[k] for k in keys} for row in rows],
                                 records(self.run_token, self.service_ips, self.active)), 'HEALTH_IDENTITY_MISMATCH')
        require(all(row['status'] in {'HEALTHY', 'PENDING', 'UNHEALTHY', 'UNKNOWN'} for row in rows), 'HEALTH_INVALID')
        return {size: next(row['status'] for row in rows if row['name'] == name(self.run_token, size)) for size in ('xs', 's')}

    def register_backends(self, *, guard, timeout=120):
        require(callable(guard) and self.journal['registration'] is None and not self.journal['requests'], 'REGISTER_SEQUENCE_INVALID')
        require(type(timeout) in (int, float) and 0 < timeout <= 180, 'REGISTER_TIMEOUT_INVALID')
        deadline = time.monotonic() + timeout
        state = {'result': 'pending', 'initial_registry_empty': False, 'attempts': [], 'readback_verified': False}
        self.journal['registration'] = state
        while True:
            try:
                response = self.request('gateway', '/gateway/backend/all', timeout=max(.01, min(10, deadline - time.monotonic())))
                require(response['status'] == 200, 'REGISTRY_HTTP_INVALID')
                require(parse_json(response['body']) == [], 'REGISTRY_NOT_EMPTY')
                break
            except ExperimentError as error:
                if str(error) != 'HTTP_TRANSPORT_FAILED' or time.monotonic() >= deadline:
                    raise
                time.sleep(.25)
        state['initial_registry_empty'] = True
        confirmed = []
        for row in records(self.run_token, self.service_ips):
            response = self.request('gateway', '/gateway/backend/all')
            require(response['status'] == 200 and registry_matches(parse_json(response['body']), confirmed), 'REGISTRY_CHANGED')
            attempt = {'name': row['name'], 'result': 'pending', 'record_sha256': digest(row), 'http_status': None}
            state['attempts'].append(attempt)
            self.journal['pending'] = {'operation': 'register', 'name': row['name']}
            self.notify()
            guard()
            require(time.monotonic() < deadline, 'REGISTER_DEADLINE')
            response = self.request('gateway', REGISTER, data=canonical(row).encode(), headers={'Content-Type': 'application/json'})
            attempt.update(result='response-observed', http_status=response['status'])
            self.notify()
            require(response['status'] == 200 and registry_matches([parse_json(response['body'])], [row]), 'REGISTER_RESPONSE_INVALID')
            response = self.request('gateway', '/gateway/backend/all')
            require(response['status'] == 200 and registry_matches(parse_json(response['body']), [*confirmed, row]), 'REGISTER_READBACK_INVALID')
            confirmed.append(row)
            attempt['result'] = 'confirmed'
            self.journal['pending'] = None
            self.notify()
        state.update(result='verified', readback_verified=True, observed_at=stamp())
        self.notify()
        return copy.deepcopy(state)

    def ready(self, sizes=('xs', 's'), *, require_healthy=True, timeout=180):
        require(type(sizes) in (tuple, list) and tuple(sizes) in {('xs', 's'), ('xs',), ('s',)}
                and type(require_healthy) is bool and type(timeout) in (int, float) and 0 < timeout <= 240, 'READY_SCOPE_INVALID')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                infos = {}
                for size in sizes:
                    response = self.request(size, '/v1/info', timeout=max(.01, min(10, deadline - time.monotonic())))
                    require(response['status'] == 200, 'READINESS_PENDING')
                    value = parse_json(response['body'])
                    require(type(value) is dict and value.get('nodeId') == name(self.run_token, size)
                            and value.get('nodeVersion') == {'version': '483'} and value.get('coordinator') is True
                            and type(value.get('starting')) is bool and type(value.get('coordinatorId')) is str
                            and re.fullmatch(r'[A-Za-z0-9_-]{5,128}', value['coordinatorId']), 'TRINO_IDENTITY_MISMATCH')
                    require(value['starting'] is False and value.get('state') == 'ACTIVE', 'READINESS_PENDING')
                    infos[size] = {'node_id': value['nodeId'], 'version': '483', 'starting': False,
                                   'coordinator_id': value['coordinatorId']}
                health = self.health()
                if require_healthy:
                    require(all(self.active[size] and health[size] == 'HEALTHY' for size in sizes), 'READINESS_PENDING')
                result = {'observed_at': stamp(), 'backends': infos, 'gateway_active': dict(self.active), 'gateway_health': health}
                self.journal['readiness'].append(result)
                self.notify()
                return copy.deepcopy(result)
            except ExperimentError as error:
                if str(error) not in {'HTTP_TRANSPORT_FAILED', 'READINESS_PENDING'}:
                    raise
            time.sleep(.25)
        raise ExperimentError('WAREHOUSE_NOT_READY')

    def set_active(self, active, *, guard):
        require(type(active) is bool and callable(guard) and self.active['xs'] is not active,
                'ACTIVE_SEQUENCE_INVALID')
        self.registry()
        operation = 'activate' if active else 'deactivate'
        attempt = {'operation': operation, 'size': 'xs', 'result': 'pending', 'http_status': None}
        self.journal['controls'].append(attempt)
        self.journal['pending'] = {'operation': operation, 'size': 'xs'}
        self.notify()
        guard()
        response = self.request('gateway', '/gateway/backend/' + operation + '/' + name(self.run_token, 'xs'), data=b'')
        attempt.update(result='response-observed', http_status=response['status'])
        self.notify()
        require(response['status'] == 200 and response['body'] == b'', 'ACTIVE_RESPONSE_INVALID')
        self.active['xs'] = active
        self.registry()
        health = self.health()
        # Routing first filters the registry's active flag. A concurrent monitor
        # can retain/restore HEALTHY while the still-running backend is inactive.
        # Registry readback above must be false; health remains an observation.
        attempt.update(result='verified', active=active, health=health['xs'], observed_at=stamp())
        self.journal['pending'] = None
        self.notify()
        return copy.deepcopy(attempt)

    def _source(self, case):
        require(case in self.cases, 'CASE_INVALID')
        return 'shirokuma-wl-' + self.run_token + '-' + case

    def _headers(self, case, *, followup=False):
        route = self.cases[case].upper()
        return {'X-Trino-User': self.worker, 'X-Trino-Source': self._source(case),
                'X-Trino-Session': 'query_max_execution_time=20s,query_max_run_time=30s',
                HEADER: ('S' if route == 'XS' else 'XS') if followup else route}

    def start(self, case, *, guard=None):
        require(case in self.cases and case not in self.handles and self.journal['pending'] is None
                and (guard is None or callable(guard)), 'QUERY_SEQUENCE_INVALID')
        row = {'case': case, 'size': self.cases[case], 'source': self._source(case), 'query_id': None,
               'http_status': None, 'response_complete': False, 'result': 'pending', 'rows_observed': 0,
               'followup_gets': 0, 'opposite_route_gets': 0, 'sql_sha256': digest(SELECT.encode())}
        handle = {'row': row, 'path': None, 'rows': [], 'columns': None, 'pages': 0, 'bytes': 0,
                  'started': time.monotonic(), 'finished': False}
        self.handles[case] = handle
        self.journal['requests'].append(row)
        self.journal['pending'] = {'operation': 'query-post', 'case': case}
        self.epoch += 1
        self.last_activity = time.monotonic()
        self.notify()  # All POST intentions are durable before any request, even if no ID returns.
        if guard is not None:
            guard()
        response = self.request('gateway', '/v1/statement', data=SELECT.encode(), headers=self._headers(case))
        self._page(case, response, first=True)
        self.journal['pending'] = None
        self.notify()
        return copy.deepcopy(row)

    def _page(self, case, response, *, first):
        handle = self.handles[case]
        row = handle['row']
        row['http_status'] = response['status']
        handle['pages'] += 1
        handle['bytes'] += len(response['body'])
        require(handle['pages'] <= 128 and handle['bytes'] <= 2 * MAX_BODY, 'QUERY_RESPONSE_LIMIT')
        if case == 'xs_unavailable':
            require(first and response['status'] == 500, 'UNAVAILABLE_NOT_PROVEN')
            row.update(result='unavailable', response_complete=True, response_sha256=digest(response['body']))
            handle['finished'] = True
        else:
            require(response['status'] == 200, 'QUERY_HTTP_INVALID')
            value = parse_json(response['body'])
            require(type(value) is dict, 'QUERY_RESPONSE_INVALID')
            query_id = value.get('id')
            require(type(query_id) is str and QUERY_ID.fullmatch(query_id)
                    and row['query_id'] in {None, query_id}, 'QUERY_ID_MISMATCH')
            require(not first or all(r is row or r['query_id'] != query_id for r in self.journal['requests']), 'QUERY_ID_DUPLICATE')
            row['query_id'] = query_id
            self.notify()
            require(value.get('error') is None, 'QUERY_FAILED')
            values = value.get('data', [])
            require(type(values) is list and len(handle['rows']) + len(values) <= 25, 'QUERY_ROWS_LIMIT')
            handle['rows'].extend(values)
            row['rows_observed'] = len(handle['rows'])
            if value.get('columns') is not None:
                declared = value['columns']
                require(type(declared) is list and all(type(v) is dict for v in declared), 'QUERY_SCHEMA_INVALID')
                columns = [(v.get('name'), v.get('type')) for v in declared]
                require(handle['columns'] in (None, columns) and columns == SELECT_COLUMNS, 'QUERY_SCHEMA_INVALID')
                handle['columns'] = columns
            uri = value.get('nextUri')
            if uri is None:
                rows = handle['rows']
                require(handle['columns'] == SELECT_COLUMNS and rows == expected_rows(self.run_token, self.cases[case])
                        and all(type(r[2]) is bool and type(r[4]) is int for r in rows), 'QUERY_ORACLE_MISMATCH')
                row.update(result='success', response_complete=True, rows_sha256=digest(rows), matched=True)
                handle['finished'] = True
                handle['path'] = None
            else:
                handle['path'] = next_uri(uri, 'gateway', query_id)
            if not first:
                row['followup_gets'] += 1
                row['opposite_route_gets'] += 1
        self.epoch += 1
        self.last_activity = time.monotonic()
        if handle['finished']:
            row['duration_ms'] = max(0, int((time.monotonic() - handle['started']) * 1000))

    def finish(self, case):
        require(case in self.handles and self.journal['pending'] is None, 'QUERY_SEQUENCE_INVALID')
        handle = self.handles[case]
        while not handle['finished']:
            # The held first response must be consumed inside the configured 20s client timeout.
            require(time.monotonic() - handle['started'] < 18, 'QUERY_CLIENT_DEADLINE')
            self.journal['pending'] = {'operation': 'query-get', 'case': case}
            self.notify()
            response = self.request('gateway', handle['path'], headers=self._headers(case, followup=True),
                                    timeout=max(.01, min(10, 18 - (time.monotonic() - handle['started']))))
            self._page(case, response, first=False)
            self.journal['pending'] = None
            self.notify()
        require(case == 'xs_unavailable' or handle['row']['followup_gets'] > 0, 'CONTINUATION_UNOBSERVED')
        return copy.deepcopy(handle['row'])

    def select(self, case):
        self.start(case)
        return self.finish(case)

    def queries(self, size):
        require(size in {'xs', 's'}, 'WAREHOUSE_INVALID')
        response = self.request(size, '/v1/query', headers={'X-Trino-User': self.worker})
        observed_monotonic = time.monotonic()
        require(response['status'] == 200, 'QUERY_LIST_HTTP_INVALID')
        values = parse_json(response['body'])
        require(type(values) is list and len(values) <= MAX_QUERIES, 'QUERY_LIST_INVALID')
        known = {row['query_id']: row for row in self.journal['requests'] if row['query_id'] is not None}
        seen, rows = set(), []
        for value in values:
            require(type(value) is dict and type(value.get('queryId')) is str and QUERY_ID.fullmatch(value['queryId'])
                    and value['queryId'] not in seen and value.get('state') in STATES
                    and type(value.get('session')) is dict, 'QUERY_LIST_INVALID')
            query_id, state, session = value['queryId'], value['state'], value['session']
            seen.add(query_id)
            require(query_id in known and known[query_id]['size'] == size
                    and session.get('user') == self.worker and session.get('source') == known[query_id]['source'],
                    'QUERY_LIST_FOREIGN_OR_MISMATCH')
            require(state != 'FAILED', 'OBSERVED_QUERY_FAILED')
            rows.append({'query_id': query_id, 'state': state, 'case': known[query_id]['case']})
        result = {'size': size, 'observed_at': stamp(), 'observed_monotonic': observed_monotonic,
                  'complete': True, 'rows': rows,
                  'nonterminal_count': sum(row['state'] not in TERMINAL for row in rows),
                  'scope': 'unfiltered-full-query-list-all-users-and-sources'}
        require(len(self.journal['query_snapshots']) < 64, 'QUERY_SNAPSHOT_LIMIT')
        self.journal['query_snapshots'].append(result)
        self.notify()
        return copy.deepcopy(result)

    def require_seen_terminal(self, snapshot, cases):
        require(type(cases) in (tuple, list) and all(case in self.cases for case in cases), 'QUERY_EXPECTATION_INVALID')
        found = {row['case']: row['state'] for row in snapshot['rows']}
        require(all(found.get(case) == 'FINISHED' for case in cases), 'QUERY_TERMINAL_EVIDENCE_MISSING')
        return True


class QueryClient(Client):
    """Opt-in, one fixed caller SELECT; no arbitrary SQL or request identity."""
    cases = {**CASES, 'xs_caller': 'xs'}

    def __init__(self, run_token, service_ips):
        super().__init__(run_token, service_ips)
        self.caller_request_id = None

    def bind_caller(self, request_id):
        try:
            valid = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
        except (ValueError, TypeError, AttributeError):
            valid = False
        require(valid and self.caller_request_id is None, 'CALLER_REQUEST_INVALID')
        self.caller_request_id = request_id

    def _source(self, case):
        source = super()._source(case)
        if case == 'xs_caller':
            require(self.caller_request_id is not None, 'CALLER_REQUEST_UNBOUND')
            source += '-' + uuid.UUID(self.caller_request_id).hex
        return source

    def start(self, case, *, guard=None):
        require(case != 'xs_caller' or callable(guard), 'CALLER_GUARD_REQUIRED')
        return super().start(case, guard=guard)

    def caller(self, *, guard):
        self.start('xs_caller', guard=guard)
        return self.finish('xs_caller')
