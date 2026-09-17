"""Live lifecycle receipts, Pawprints and audit-delivery spans; no event replay.

The controller supplies its measured decision duration. Child OTel spans measure
only this callback's journal/delivery work; their clocks are never backdated to
pretend that the controller operation was traced.
"""
import copy
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

from .support import canonical, digest, directory, read, token, write
from tools.local_experiment.trino_identity.telemetry import Backend

ACTOR = 'local-warehouse-lifecycle'
ACTIONS = ('busy-defer', 'chill', 'wake')
STATUSES = {'busy-defer': 'blocked', 'chill': 'success', 'wake': 'success'}
PROJECTION_KEYS = {'warehouse', 'reason', 'active_queries', 'pending_posts', 'open_handles',
                   'replicas_before', 'replicas_after', 'old_pod_uid', 'new_pod_uid', 'backend_active'}
REASONS = {'host-handle-open', 'idle-drained', 'wake-ready', 'verification-failed', 'interrupted'}


class TelemetryError(RuntimeError):
    """Only fixed safe codes, never a response, credential or exception message."""


def require(value, code):
    if not value:
        raise TelemetryError(code)


def project_decision(action, status, value, duration_ms):
    require(type(action) is str and action in ACTIONS and type(status) is str
            and status in {STATUSES[action], 'failed'}, 'LIFECYCLE_ACTION_INVALID')
    require(type(duration_ms) is int and 0 <= duration_ms <= 2**64 - 1, 'LIFECYCLE_DURATION_INVALID')
    require(type(value) is dict and set(value) == PROJECTION_KEYS and value['warehouse'] == 'XS'
            and type(value['reason']) is str and value['reason'] in REASONS, 'LIFECYCLE_PROJECTION_INVALID')
    require(value['active_queries'] is None or type(value['active_queries']) is int
            and 0 <= value['active_queries'] <= 1024, 'LIFECYCLE_COUNT_INVALID')
    for key in ('pending_posts', 'open_handles'):
        require(type(value[key]) is int and 0 <= value[key] <= 1024, 'LIFECYCLE_COUNT_INVALID')
    for key in ('replicas_before', 'replicas_after'):
        require(value[key] is None or type(value[key]) is int and value[key] in (0, 1),
                'LIFECYCLE_REPLICAS_INVALID')
    for key in ('old_pod_uid', 'new_pod_uid'):
        item = value[key]
        if item is not None:
            try:
                require(type(item) is str and str(uuid.UUID(item)) == item, 'LIFECYCLE_UID_INVALID')
            except (ValueError, AttributeError):
                raise TelemetryError('LIFECYCLE_UID_INVALID') from None
    require(value['backend_active'] is None or type(value['backend_active']) is bool,
            'LIFECYCLE_ACTIVE_INVALID')
    require(value['replicas_before'] == (0 if action == 'wake' else 1), 'LIFECYCLE_REPLICAS_INVALID')
    if status == 'failed':
        require(value['reason'] in {'verification-failed', 'interrupted'}, 'LIFECYCLE_REASON_INVALID')
    elif action == 'busy-defer':
        require(value['reason'] == 'host-handle-open' and value['open_handles'] > 0
                and value['replicas_after'] == 1, 'LIFECYCLE_BUSY_EVIDENCE_INVALID')
    elif action == 'chill':
        require(value['reason'] == 'idle-drained' and value['active_queries'] == 0
                and value['pending_posts'] == value['open_handles'] == 0 and value['replicas_after'] == 0
                and value['backend_active'] is False and value['old_pod_uid'] is not None,
                'LIFECYCLE_CHILL_EVIDENCE_INVALID')
    else:
        require(value['reason'] == 'wake-ready' and value['pending_posts'] == value['open_handles'] == 0
                and value['replicas_after'] == 1 and value['backend_active'] is True
                and value['old_pod_uid'] is not None and value['new_pod_uid'] is not None
                and value['old_pod_uid'] != value['new_pod_uid'], 'LIFECYCLE_WAKE_EVIDENCE_INVALID')
    return copy.deepcopy(value)


def _tracing(path):
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    class PrivateSpans(SpanExporter):
        def export(self, spans):
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW), 'a') as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                        and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 1024**2,
                        'LIFECYCLE_SPAN_FILE_INVALID')
                for span in spans:
                    context = span.get_span_context()
                    stream.write(canonical({'name': span.name, 'trace_id': f'{context.trace_id:032x}',
                        'span_id': f'{context.span_id:016x}',
                        'parent_span_id': f'{span.parent.span_id:016x}' if span.parent else None,
                        'start_time_unix_nano': span.start_time, 'end_time_unix_nano': span.end_time,
                        'status': span.status.status_code.name, 'attributes': dict(span.attributes or {})}) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            return SpanExportResult.SUCCESS

    provider = TracerProvider(resource=Resource({'service.name': 'shirokuma-local-warehouse-lifecycle'}))
    provider.add_span_processor(SimpleSpanProcessor(PrivateSpans()))
    return provider, provider.get_tracer('shirokuma.warehouse.lifecycle', '0.1.0')


class LifecycleTelemetry:
    def __init__(self, output, run_token, *, backend=None):
        token(run_token)
        self.output = Path(output)
        directory(self.output)
        require(self.output.name == run_token and stat.S_IMODE(self.output.stat().st_mode) == 0o700,
                'LIFECYCLE_PRIVATE_RUN_REQUIRED')
        self.path = self.output / 'telemetry.json'
        self.spans = self.output / 'lifecycle-spans.jsonl'
        require(not self.path.exists() and not self.path.is_symlink()
                and not self.spans.exists() and not self.spans.is_symlink(), 'LIFECYCLE_TELEMETRY_EXISTS')
        with os.fdopen(os.open(self.spans, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w'):
            pass
        self.backend = backend or Backend()
        self.provider, self.tracer = _tracing(self.spans)
        self.started, self.closed, self.completed = None, False, False
        self.journal = {'schema_version': 1, 'run_token': run_token, 'task_id': 'warehouse-lifecycle-' + run_token,
            'actor': ACTOR, 'scope': 'live-controller-decisions-and-audit-delivery-not-backdated-decision-spans',
            'result': 'running', 'records': [], 'pending_action': None, 'cleanup': None,
            'cleanup_verified': None, 'saved_spans_verified': False}

    def save(self):
        write(self.path, self.journal)

    def receipt(self):
        return copy.deepcopy(self.journal)

    def __enter__(self):
        require(self.started is None and not self.closed, 'LIFECYCLE_TELEMETRY_STATE_INVALID')
        self.started = time.monotonic_ns()
        self.root_context = self.tracer.start_as_current_span('warehouse.lifecycle.run',
            record_exception=False, set_status_on_exception=False)
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

    def _emit(self, action, status, duration_ms, span, projection=None):
        event = self.backend.event(action=action, target='trino-gateway', status=status,
            agent_id=ACTOR, task_id=self.journal['task_id'], trace_id=self.journal['trace_id'],
            blocked_by_policy=False, duration_ms=duration_ms)
        record = {'event': event, 'span_id': f'{span.get_span_context().span_id:016x}',
            'projection': projection, 'delivery': 'pending', 'agenthouse_verified': False,
            'loki_verified': False}
        self.journal['records'].append(record)
        self.save()  # No remote write until this exact intended event is durable.
        try:
            self.backend.insert(event)
            self.backend.verify(event)
            record['agenthouse_verified'] = True
            self.save()
            log = {'task_id': event['task_id'], 'trace_id': event['trace_id'], 'span_id': record['span_id'],
                   'action': action, 'status': status}
            self.backend.push_log(log)
            self.backend.verify_log(log)
            record.update(loki_verified=True, delivery='verified')
            self.save()
        except BaseException as error:
            record.update(delivery='failed', error_code='LIFECYCLE_DELIVERY_UNVERIFIED')
            self.journal['result'] = 'failed'
            try:
                self.save()
            except BaseException:
                if not isinstance(error, Exception):
                    raise error
                raise
            if not isinstance(error, Exception):
                raise
            raise TelemetryError('LIFECYCLE_DELIVERY_UNVERIFIED') from None

    def decision(self, action, status, projection, duration_ms):
        from opentelemetry.trace import Status, StatusCode
        require(self.started is not None and not self.closed and not self.completed
                and self.journal['result'] == 'running' and self.journal['pending_action'] is None,
                'LIFECYCLE_TELEMETRY_STATE_INVALID')
        previous = [r['event']['action_type'].removeprefix('warehouse.lifecycle.') for r in self.journal['records']]
        require(len(previous) < len(ACTIONS) and action == ACTIONS[len(previous)], 'LIFECYCLE_ACTION_ORDER_INVALID')
        value = project_decision(action, status, projection, duration_ms)
        self.journal['pending_action'] = action
        self.save()
        try:
            with self.tracer.start_as_current_span('warehouse.lifecycle.audit.' + action,
                    record_exception=False, set_status_on_exception=False) as span:
                attributes = {'task_id': self.journal['task_id'], 'action': action, 'decision_status': status,
                    'duration_kind': 'audit-delivery-not-controller-decision',
                    'controller_duration_ms': duration_ms, 'projection_sha256': digest(value)}
                for key, item in attributes.items():
                    span.set_attribute(key, item)
                try:
                    self._emit('warehouse.lifecycle.' + action, status, duration_ms, span, value)
                    span.set_attribute('delivery_verified', True)
                    span.set_status(Status(StatusCode.OK))
                except BaseException:
                    span.set_attribute('delivery_verified', False)
                    span.set_status(Status(StatusCode.ERROR))
                    raise
            if status == 'failed':
                self.journal['result'] = 'failed'
        except BaseException:
            self.journal['result'] = 'failed'
            raise
        finally:
            failure = sys.exc_info()[1]
            self.journal['pending_action'] = None
            try:
                self.save()
            except BaseException:
                if failure is not None and not isinstance(failure, Exception):
                    raise failure
                raise

    def record_cleanup(self, cleanup):
        require(not self.closed and type(cleanup) is dict and type(cleanup.get('verified')) is bool,
                'LIFECYCLE_CLEANUP_INVALID')
        require(all(type(cleanup.get(k)) is list and len(cleanup[k]) <= 64
                    for k in ('removed', 'conflicts', 'deferred')), 'LIFECYCLE_CLEANUP_INVALID')
        require(not cleanup['verified'] or not cleanup['conflicts'] and not cleanup['deferred'],
                'LIFECYCLE_CLEANUP_INVALID')
        safe = {'verified': cleanup['verified'],
                **{k + '_count': len(cleanup[k]) for k in ('removed', 'conflicts', 'deferred')}}
        self.journal.update(cleanup=safe, cleanup_verified=cleanup['verified'])
        self.save()

    def finish(self, receipt):
        require(self.started is not None and not self.closed and not self.completed
                and self.journal['result'] == 'running' and self.journal['pending_action'] is None,
                'LIFECYCLE_RUN_NOT_VERIFIED')
        require(type(receipt) is dict and receipt.get('run_token') == self.journal['run_token']
                and receipt.get('result') == 'verified' and receipt.get('pending') is None
                and type(receipt.get('observations')) is dict
                and receipt['observations'].get('result') == 'verified', 'LIFECYCLE_RUN_NOT_VERIFIED')
        cleanup = receipt.get('cleanup')
        require(type(cleanup) is dict and cleanup.get('verified') is True
                and cleanup.get('conflicts') == [] and cleanup.get('deferred') == [], 'LIFECYCLE_RUN_NOT_VERIFIED')
        records = self.journal['records']
        require(len(records) == len(ACTIONS) and all(r['event']['action_type'] == 'warehouse.lifecycle.' + action
                and r['event']['status'] == STATUSES[action] and r['delivery'] == 'verified'
                and r['agenthouse_verified'] is True and r['loki_verified'] is True
                for r, action in zip(records, ACTIONS)), 'LIFECYCLE_DECISIONS_INCOMPLETE')
        self.record_cleanup(cleanup)
        self.completed = True
        self.save()

    def _verify_spans(self):
        rows = [json.loads(line) for line in read(self.spans, 1024**2).decode().splitlines()]
        records = {r['span_id']: r for r in self.journal['records']}
        require(len(rows) == len(records) + 1 and {r['span_id'] for r in rows}
                == set(records) | {self.journal['root_span_id']}, 'LIFECYCLE_SPANS_MISSING')
        for row in rows:
            attributes = row.get('attributes')
            require(row['trace_id'] == self.journal['trace_id'] and type(row['start_time_unix_nano']) is int
                    and type(row['end_time_unix_nano']) is int
                    and row['end_time_unix_nano'] >= row['start_time_unix_nano'] > 0
                    and type(attributes) is dict and attributes.get('task_id') == self.journal['task_id'],
                    'LIFECYCLE_SPAN_INVALID')
            if row['span_id'] == self.journal['root_span_id']:
                require(row['name'] == 'warehouse.lifecycle.run' and row['status'] == self.root_status
                        and attributes.get('controller_verified') is self.root_verified
                        and attributes.get('cleanup_verified') is (self.journal['cleanup_verified'] is True)
                        and attributes.get('duration_kind') == 'whole-experiment-including-cleanup',
                        'LIFECYCLE_SPAN_CORRELATION_MISMATCH')
                continue
            record = records[row['span_id']]
            action = record['event']['action_type'].removeprefix('warehouse.lifecycle.')
            delivered = record['delivery'] == 'verified'
            require(row['parent_span_id'] == self.journal['root_span_id']
                    and row['name'] == 'warehouse.lifecycle.audit.' + action
                    and row['status'] == ('OK' if delivered else 'ERROR')
                    and attributes.get('action') == action and attributes.get('decision_status') == record['event']['status']
                    and attributes.get('controller_duration_ms') == record['event']['duration_ms']
                    and type(attributes.get('controller_duration_ms')) is int
                    and attributes.get('duration_kind') == 'audit-delivery-not-controller-decision'
                    and attributes.get('projection_sha256') == digest(record['projection'])
                    and attributes.get('delivery_verified') is delivered, 'LIFECYCLE_SPAN_CORRELATION_MISMATCH')
        self.journal['saved_spans_verified'] = True

    def __exit__(self, kind, error, traceback):
        from opentelemetry.trace import Status, StatusCode
        self.closed = True
        success = error is None and self.completed and self.journal['result'] == 'running'
        self.root_verified, self.root_status = success, 'OK' if success else 'ERROR'
        self.root_span.set_status(Status(StatusCode.OK if success else StatusCode.ERROR))
        self.root_span.set_attribute('controller_verified', success)
        self.root_span.set_attribute('cleanup_verified', self.journal['cleanup_verified'] is True)
        duration = max(0, (time.monotonic_ns() - self.started) // 1000000)
        self.root_context.__exit__(None, None, None)
        self.provider.shutdown()
        failure = None
        try:
            self._verify_spans()
        except BaseException as caught:
            failure, success = caught, False
        try:
            self._emit('warehouse.lifecycle.run', 'success' if success else 'failed', duration, self.root_span)
        except BaseException as caught:
            failure, success = caught, False
        finally:
            self.journal['result'] = 'passed' if success else 'failed'
            try:
                self.save()
            except BaseException as caught:
                failure, success = caught, False
                self.journal['result'] = 'failed'
        if error is None:
            if failure is not None:
                if not isinstance(failure, Exception):
                    raise failure
                raise TelemetryError('LIFECYCLE_FINALIZATION_FAILED') from None
            require(success, 'LIFECYCLE_RUN_NOT_COMPLETED')
        return False
