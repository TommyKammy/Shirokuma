"""Live identity case observations correlated with Pawprints, Loki and local OTel.

Durations cover the case observation, including decision collection, not pure
query latency or OPA evaluation time. Historical receipts cannot be imported.
No tokens, SQL, response rows, raw logs or exception messages leave this module.
"""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

from .client import CASES, QUERY_ID, SELECT, USERS, expected_rows
from .support import ROOT, canonical, digest, directory, read, token, write

CASE_MAP = {name: (user, expected) for name, user, expected in CASES}
ACTOR = 'local-trino-identity'
RESULT_KEYS = {'case', 'result', 'query_id', 'http_status', 'error_name',
               'response_complete', 'rows', 'sql_sha256'}
DECISION_KEYS = {'case', 'decision_id', 'user', 'target_user', 'query_id',
                 'operation', 'outcome', 'correlation'}
OUTCOMES = {'ExecuteQuery': {'allow', 'deny'}, 'ImpersonateUser': {'allow', 'deny'},
            'AccessCatalog': {'allow', 'deny'}, 'SelectFromColumns': {'allow', 'deny'},
            'GetRowFilters': {'row_filter', 'no_filter'}, 'GetColumnMask': {'column_mask', 'no_mask'}}


class TelemetryError(RuntimeError):
    """Only fixed local error codes are used, never external exception text."""


def require(condition, code):
    if not condition:
        raise TelemetryError(code)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _identifier(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def progress_projection(case, result):
    """Whitelist a client's partial observation; rows are never copied."""
    require(case in CASE_MAP and type(result) is dict and set(result) == RESULT_KEYS,
            'TELEMETRY_RESULT_INVALID')
    require(result['case'] == case and result['result'] in {'pending', 'success', 'denied', 'unauthenticated'}
            and type(result['response_complete']) is bool
            and result['sql_sha256'] == digest(SELECT.encode()), 'TELEMETRY_RESULT_INVALID')
    require(result['query_id'] is None or _identifier(result['query_id'], QUERY_ID.pattern), 'TELEMETRY_QUERY_ID_INVALID')
    require(result['http_status'] is None or type(result['http_status']) is int
            and result['http_status'] in {200, 401, 403}, 'TELEMETRY_HTTP_STATUS_INVALID')
    require(result['error_name'] in {None, 'PERMISSION_DENIED'}, 'TELEMETRY_ERROR_CODE_INVALID')
    require(type(result['rows']) is list and len(result['rows']) <= 25, 'TELEMETRY_ROW_COUNT_INVALID')
    return {'case': case, 'actual_result': result['result'], 'query_id': result['query_id'],
            'http_status': result['http_status'], 'response_complete': result['response_complete'],
            'observed_row_count': len(result['rows'])}


def project_case(case, result, decisions):
    value = progress_projection(case, result)
    require(value['response_complete'] and value['actual_result'] != 'pending', 'TELEMETRY_CASE_INCOMPLETE')
    require(type(decisions) is list and len(decisions) <= 128, 'TELEMETRY_DECISIONS_INVALID')
    token_key, expected = CASE_MAP[case]
    principal = 'lab_admin' if token_key == 'control' else token_key if token_key in USERS else None
    seen, safe = set(), []
    for row in decisions:
        require(type(row) is dict and set(row) == DECISION_KEYS, 'TELEMETRY_DECISION_INVALID')
        require(row['case'] == case and row['user'] == principal and principal in USERS,
                'TELEMETRY_DECISION_IDENTITY_MISMATCH')
        decision_id = row['decision_id']
        require(_identifier(decision_id, r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
                and decision_id not in seen, 'TELEMETRY_DECISION_ID_INVALID')
        seen.add(decision_id)
        operation = row['operation']
        require(operation in OUTCOMES and row['outcome'] in OUTCOMES[operation], 'TELEMETRY_DECISION_OUTCOME_INVALID')
        if row['query_id'] is None:
            require(operation in {'ExecuteQuery', 'ImpersonateUser'}
                    and row['correlation'] == 'serial-case-window-and-user-operation', 'TELEMETRY_CORRELATION_INVALID')
        else:
            require(row['query_id'] == value['query_id'] and value['query_id'] is not None
                    and row['correlation'] == 'query-id-and-user', 'TELEMETRY_CORRELATION_INVALID')
        require((operation == 'ImpersonateUser' and case in {'forge_user', 'forge_original'}
                 and row['target_user'] == 'lab_admin' and row['outcome'] == 'deny')
                or (operation != 'ImpersonateUser' and row['target_user'] is None), 'TELEMETRY_TARGET_INVALID')
        safe.append({key: row[key] for key in sorted(DECISION_KEYS)})
    actual = value['actual_result']
    if actual == 'unauthenticated':
        require(not safe and value['query_id'] is None and result['rows'] == [] and value['http_status'] == 401,
                'TELEMETRY_AUTH_REJECTION_INVALID')
    else:
        require(value['query_id'] is not None, 'TELEMETRY_QUERY_ID_INVALID')
    if actual == 'success':
        require(any(row['operation'] == 'SelectFromColumns' and row['outcome'] == 'allow' for row in safe),
                'TELEMETRY_POLICY_EVIDENCE_MISSING')
        row_match = principal in USERS and result['rows'] == expected_rows(principal)
        policy_match = (principal not in {'lab_row', 'lab_both'} or any(row['outcome'] == 'row_filter' for row in safe))
        policy_match = policy_match and (principal not in {'lab_mask', 'lab_both'} or any(row['outcome'] == 'column_mask' for row in safe))
    elif actual == 'denied':
        operation = 'ImpersonateUser' if case in {'forge_user', 'forge_original'} else 'ExecuteQuery'
        require(any(row['operation'] == operation and row['outcome'] == 'deny' for row in safe),
                'TELEMETRY_POLICY_EVIDENCE_MISSING')
        row_match, policy_match = result['rows'] == [], True
    else:
        row_match, policy_match = True, True
    value.update(expected_result=expected, expected_match=actual == expected and row_match and policy_match,
                 principal=principal if actual != 'unauthenticated' else None,
                 token_source='host-signed-validation-control' if case == 'fixture_control' else
                              'authentication-negative-fixture' if expected == 'unauthenticated' else 'keycloak-access-token',
                 decision_count=len(safe), decisions=safe, decision_timestamp_observed=False,
                 duration_kind='case-observation-including-audit-collection')
    return value


class Backend:
    """Existing bounded local AgentHouse/Loki APIs, no new endpoint or table."""
    def __init__(self):
        local = ROOT / 'tools/local_experiment'
        self.ah = _module('identity_telemetry_agenthouse', local / 'agenthouse/manage.py')
        self.loki = _module('identity_telemetry_loki', local / 'loki/manage.py')
        # Reuse the reviewed read-only runtime/credential boundary without
        # calling Store.inspect/create/insert or any ensure_runtime function.
        history = _module('identity_telemetry_runtime_guard', local / 'query_history/import_receipts.py')
        store = history.runtime_store()
        self.client, self.ah_guard = store.client, store.guard
        self.loki.check_context()
        current = self.loki.container()
        require(current is not None and current.get('State', {}).get('Running') is True,
                'TELEMETRY_LOKI_UNAVAILABLE')
        self.loki_id = current['Id']

    def guard_loki(self):
        current = self.loki.container()
        require(current is not None and current.get('State', {}).get('Running') is True
                and current['Id'] == self.loki_id, 'TELEMETRY_LOKI_IDENTITY_CHANGED')

    def event(self, **fields):
        return self.ah.event(**fields)

    def insert(self, event):
        self.ah_guard()
        self.guard_loki()
        self.ah.insert(self.client, event)

    def verify(self, event):
        self.ah_guard()
        # Existing evaluation readback compares a subset; identity must also
        # prove its actor, timestamp and blocked_by_policy distinction.
        identifier = str(uuid.UUID(event['event_id']))
        rows = json.loads(self.client.query("SELECT * FROM agenthouse.pawprints FINAL WHERE event_id = '"
                    + identifier + "' LIMIT 2 FORMAT JSON"))['data']
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict
                and set(rows[0]) == set(event), 'TELEMETRY_AUDIT_READBACK_INVALID')
        observed = rows[0]
        for key in ('duration_ms', 'prompt_tokens', 'completion_tokens', 'issue', 'pr'):
            if type(observed[key]) is str and observed[key].isascii() and observed[key].isdigit():
                observed[key] = int(observed[key])
        require(observed == event and all(type(observed[key]) is type(value) for key, value in event.items()),
                'TELEMETRY_AUDIT_READBACK_MISMATCH')

    def push_log(self, record):
        self.ah_guard()
        self.guard_loki()
        self.loki.push_record(record)

    def verify_log(self, record):
        deadline = time.monotonic() + 3
        while True:
            self.guard_loki()
            rows = [row for row in self.loki.query_task(record['task_id']) if row.get('span_id') == record['span_id']]
            if rows:
                require(rows == [record], 'TELEMETRY_LOG_READBACK_MISMATCH')
                return
            require(time.monotonic() < deadline, 'TELEMETRY_LOG_READBACK_MISSING')
            time.sleep(0.1)


def _tracing(path):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    # Reuse the project's local SpanFile contract, never configure an external
    # exporter or set a process-global OpenTelemetry provider.
    bears = ROOT / 'tools/local_experiment/bears'
    sys.path.insert(0, str(bears))
    try:
        span_module = _module('identity_telemetry_spanfile', bears / 'run_sre.py')
    finally:
        sys.path.remove(str(bears))
    provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-local-trino-identity'}))
    provider.add_span_processor(SimpleSpanProcessor(span_module.SpanFile(path)))
    return provider, provider.get_tracer('shirokuma.trino.identity', '0.1.0')


class CaseObservation:
    def __init__(self, owner, case):
        self.owner, self.case, self.complete = owner, case, False
        self.projection = None

    def progress(self, result):
        require(not self.complete, 'TELEMETRY_CASE_ALREADY_COMPLETED')
        self.owner.journal['pending_case']['observation'] = progress_projection(self.case, result)
        self.owner.save()

    def finish(self, result, decisions):
        require(not self.complete, 'TELEMETRY_CASE_ALREADY_COMPLETED')
        projected = project_case(self.case, result, decisions)
        ids = {row['decision_id'] for row in projected['decisions']}
        require(not ids & self.owner.decision_ids, 'TELEMETRY_DECISION_REUSED')
        self.owner.decision_ids.update(ids)
        self.projection, self.complete = projected, True
        self.owner.journal['pending_case']['observation'] = projected
        self.owner.save()


class IdentityTelemetry:
    def __init__(self, output, run_token, *, backend=None):
        token(run_token)
        self.output = Path(output)
        directory(self.output)
        require(self.output.name == run_token and stat.S_IMODE(self.output.stat().st_mode) == 0o700,
                'TELEMETRY_PRIVATE_RUN_REQUIRED')
        self.path = self.output / 'telemetry.json'
        self.spans = self.output / 'identity-spans.jsonl'
        require(not self.path.exists() and not self.path.is_symlink()
                and not self.spans.exists() and not self.spans.is_symlink(), 'TELEMETRY_ALREADY_EXISTS')
        with os.fdopen(os.open(self.spans, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w'):
            pass
        self.backend = backend or Backend()
        self.provider, self.tracer = _tracing(self.spans)
        self.used_cases, self.decision_ids = set(), set()
        self.started, self.completed, self.closed = None, False, False
        self.journal = {'schema_version': 1, 'run_token': run_token, 'task_id': 'trino-identity-' + run_token,
                        'scope': 'live-case-observation-not-original-opa-decision-timing', 'actor': ACTOR,
                        'result': 'running', 'records': [], 'pending_case': None,
                        'cleanup_verified': None, 'saved_spans_verified': False}

    def save(self):
        write(self.path, self.journal)

    def __enter__(self):
        require(self.started is None, 'TELEMETRY_ALREADY_STARTED')
        self.started = time.monotonic_ns()
        self.root_context = self.tracer.start_as_current_span('trino.identity.run', record_exception=False,
                                                             set_status_on_exception=False)
        self.root_span = self.root_context.__enter__()
        context = self.root_span.get_span_context()
        self.journal.update(trace_id=f'{context.trace_id:032x}', root_span_id=f'{context.span_id:016x}')
        self.root_span.set_attribute('task_id', self.journal['task_id'])
        self.root_span.set_attribute('duration_kind', 'whole-experiment-including-cleanup')
        try:
            self.save()
        except BaseException:
            self.root_context.__exit__(None, None, None)
            self.provider.shutdown()
            raise
        return self

    def _emit(self, action, status, blocked, duration, span, projection=None):
        from opentelemetry.trace import Status, StatusCode
        span_id = f'{span.get_span_context().span_id:016x}'
        event = self.backend.event(action=action, target='trino-opa', status=status,
                    agent_id=ACTOR, task_id=self.journal['task_id'], trace_id=self.journal['trace_id'],
                    blocked_by_policy=blocked, duration_ms=duration)
        record = {'event': event, 'span_id': span_id, 'delivery': 'pending', 'agenthouse_verified': False,
                  'loki_verified': False, 'observation': projection}
        self.journal['records'].append(record)
        self.save()  # Delivery never starts until its pending record is durable.
        if span.is_recording():
            span.set_status(Status(StatusCode.OK if status == 'success' else StatusCode.ERROR))
        try:
            self.backend.insert(event)
            self.backend.verify(event)
            record['agenthouse_verified'] = True
            self.save()
            log = {'task_id': event['task_id'], 'trace_id': event['trace_id'], 'span_id': span_id,
                   'action': action, 'status': status}
            self.backend.push_log(log)
            self.backend.verify_log(log)
            record.update(loki_verified=True, delivery='verified')
            self.save()
        except BaseException as error:
            record.update(delivery='failed', error_code='TELEMETRY_DELIVERY_UNVERIFIED')
            self.journal['result'] = 'failed'
            self.save()
            if not isinstance(error, Exception):
                raise
            raise TelemetryError('TELEMETRY_DELIVERY_UNVERIFIED') from None

    @contextmanager
    def case(self, case):
        require(self.started is not None and not self.closed and not self.completed and case in CASE_MAP
                and case not in self.used_cases and self.journal['pending_case'] is None, 'TELEMETRY_CASE_ORDER_INVALID')
        self.used_cases.add(case)
        started = time.monotonic_ns()
        observation = CaseObservation(self, case)
        self.journal['pending_case'] = {'case': case, 'observation': None}
        self.save()
        with self.tracer.start_as_current_span('trino.identity.case.' + case, record_exception=False,
                                              set_status_on_exception=False) as span:
            span.set_attribute('task_id', self.journal['task_id'])
            span.set_attribute('root_span_id', self.journal['root_span_id'])
            span.set_attribute('case', case)
            span.set_attribute('duration_kind', 'case-observation-including-audit-collection')
            failure = None
            try:
                yield observation
            except BaseException as error:
                failure = error
                raise
            finally:
                projected = observation.projection or self.journal['pending_case']['observation']
                actual = projected['actual_result'] if projected else 'unobserved'
                matched = observation.complete and projected['expected_match']
                span.set_attribute('actual_result', actual)
                span.set_attribute('expected_match', bool(matched))
                if projected and projected['query_id'] is not None:
                    span.set_attribute('trino.query_id', projected['query_id'])
                if observation.complete:
                    span.set_attribute('opa.decision_ids', [row['decision_id'] for row in projected['decisions']])
                    span.set_attribute('opa.correlations', [row['correlation'] for row in projected['decisions']])
                    span.set_attribute('opa.decision_count', projected['decision_count'])
                status = 'failed' if failure is not None or not observation.complete else 'success' if actual == 'success' else 'blocked'
                try:
                    self._emit('trino.identity.case.' + case, status, observation.complete and actual == 'denied',
                               max(0, (time.monotonic_ns() - started) // 1000000), span, projected)
                except BaseException:
                    if failure is None:
                        raise
                finally:
                    self.journal['pending_case'] = None
                    self.save()
            require(observation.complete and matched, 'TELEMETRY_CASE_NOT_VERIFIED')

    def record_cleanup(self, cleanup):
        """Record cleanup independently, including after a failed case/delivery."""
        require(not self.closed and type(cleanup) is dict and type(cleanup.get('verified')) is bool,
                'TELEMETRY_CLEANUP_INVALID')
        require(all(type(cleanup.get(key)) is list and len(cleanup[key]) <= 4
                    for key in ('removed', 'conflicts', 'deferred')), 'TELEMETRY_CLEANUP_INVALID')
        require(not cleanup['verified'] or not cleanup['conflicts'] and not cleanup['deferred'],
                'TELEMETRY_CLEANUP_INVALID')
        self.journal['cleanup_verified'] = cleanup['verified']
        self.journal['cleanup'] = {'verified': cleanup['verified'],
                                   **{key + '_count': len(cleanup[key]) for key in ('removed', 'conflicts', 'deferred')}}
        self.save()

    def finish(self, receipt):
        require(not self.closed and not self.completed and self.journal['pending_case'] is None,
                'TELEMETRY_RUN_STATE_INVALID')
        cleanup = receipt.get('cleanup') if type(receipt) is dict else None
        require(type(receipt) is dict and receipt.get('run_token') == self.journal['run_token']
                and receipt.get('result') in {'verified', 'passed'} and receipt.get('pending') is None
                and type(cleanup) is dict and cleanup.get('verified') is True
                and cleanup.get('conflicts') == [] and cleanup.get('deferred') == [], 'TELEMETRY_RUN_NOT_VERIFIED')
        records = self.journal['records']
        require(self.used_cases == set(CASE_MAP) and len(records) == len(CASE_MAP)
                and all(row['delivery'] == 'verified' and row['observation'] is not None
                        and row['observation'].get('expected_match') is True for row in records), 'TELEMETRY_CASES_INCOMPLETE')
        self.record_cleanup(cleanup)
        self.completed = True
        self.save()

    def _verify_spans(self):
        rows = [json.loads(line) for line in read(self.spans, 1024 * 1024).decode().splitlines()]
        expected = {row['span_id'] for row in self.journal['records']} | {self.journal['root_span_id']}
        require(len(rows) == len(expected) and {row['span_id'] for row in rows} == expected,
                'TELEMETRY_SPANS_MISSING')
        records = {row['span_id']: row for row in self.journal['records']}
        for row in rows:
            require(row['trace_id'] == self.journal['trace_id'] and type(row['start_time_unix_nano']) is int
                    and type(row['end_time_unix_nano']) is int
                    and row['end_time_unix_nano'] >= row['start_time_unix_nano'] > 0,
                    'TELEMETRY_SPAN_INVALID')
            attributes = row.get('attributes')
            require(type(attributes) is dict and attributes.get('task_id') == self.journal['task_id'],
                    'TELEMETRY_SPAN_CORRELATION_MISMATCH')
            if row['span_id'] == self.journal['root_span_id']:
                require(row['name'] == 'trino.identity.run' and row['status'] == self.root_status
                        and attributes.get('expected_match') is self.root_expected_match
                        and attributes.get('cleanup_verified') is (self.journal['cleanup_verified'] is True)
                        and attributes.get('duration_kind') == 'whole-experiment-including-cleanup',
                        'TELEMETRY_SPAN_CORRELATION_MISMATCH')
                continue
            record = records[row['span_id']]
            projection = record['observation']
            expected_match = bool(projection and projection.get('expected_match') is True)
            require(row['name'] == record['event']['action_type']
                    and row['status'] == ('OK' if record['event']['status'] == 'success' else 'ERROR')
                    and attributes.get('root_span_id') == self.journal['root_span_id']
                    and attributes.get('case') == row['name'].removeprefix('trino.identity.case.')
                    and attributes.get('expected_match') is expected_match
                    and attributes.get('actual_result') == (projection['actual_result'] if projection else 'unobserved')
                    and attributes.get('duration_kind') == 'case-observation-including-audit-collection',
                    'TELEMETRY_SPAN_CORRELATION_MISMATCH')
            require(attributes.get('trino.query_id') == (projection['query_id'] if projection else None),
                    'TELEMETRY_SPAN_CORRELATION_MISMATCH')
            if projection and 'decisions' in projection:
                require(attributes.get('opa.decision_ids') == [value['decision_id'] for value in projection['decisions']]
                        and attributes.get('opa.correlations') == [value['correlation'] for value in projection['decisions']]
                        and type(attributes.get('opa.decision_count')) is int
                        and attributes['opa.decision_count'] == projection['decision_count'],
                        'TELEMETRY_SPAN_CORRELATION_MISMATCH')
            else:
                require(not any(key.startswith('opa.') for key in attributes), 'TELEMETRY_SPAN_CORRELATION_MISMATCH')
        self.journal['saved_spans_verified'] = True

    def __exit__(self, kind, error, traceback):
        from opentelemetry.trace import Status, StatusCode
        failure = None
        self.closed = True
        success = error is None and self.completed and self.journal['result'] != 'failed'
        self.root_status = 'OK' if success else 'ERROR'
        self.root_expected_match = success
        self.root_span.set_status(Status(StatusCode.OK if success else StatusCode.ERROR))
        self.root_span.set_attribute('expected_match', success)
        self.root_span.set_attribute('cleanup_verified', self.journal['cleanup_verified'] is True)
        # Close/read back local spans before publishing the final experiment
        # event. A missing export must never produce a successful root event.
        self.root_context.__exit__(None, None, None)
        self.provider.shutdown()
        try:
            self._verify_spans()
        except BaseException as caught:
            failure, success = caught, False
        try:
            self._emit('trino.identity.run', 'success' if success else 'failed', False,
                       max(0, (time.monotonic_ns() - self.started) // 1000000), self.root_span)
        except BaseException as caught:
            failure, success = caught, False
        finally:
            self.journal['result'] = 'passed' if success else 'failed'
            self.save()
        if error is None:
            if failure is not None:
                if not isinstance(failure, Exception):
                    raise failure
                raise TelemetryError('TELEMETRY_FINALIZATION_FAILED') from None
            require(success, 'TELEMETRY_RUN_NOT_COMPLETED')
        return False


def identity_telemetry(output, run_token, *, backend=None):
    return IdentityTelemetry(output, run_token, backend=backend)
