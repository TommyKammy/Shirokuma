"""Three bounded local audit phases; no raw queries, model text or exception text."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time
import uuid

LOCAL = Path(__file__).resolve().parents[1]


class AuditError(RuntimeError):
    pass


def first(original, other):
    return other if original is None or isinstance(original, Exception) and not isinstance(other, Exception) else original


@contextmanager
def preserve_context(context):
    original = None
    try:
        with context as value:
            try:
                yield value
            except BaseException as error:
                original = error
                raise
        if original is not None:
            raise original  # A context manager cannot suppress the phase failure.
    except BaseException as error:
        raise first(original, error)


class Audit:
    def __init__(self, token, output, profile, progress, *, agenthouse=None, loki=None):
        # Keep heavy runtime imports out of source validation and offline CLI parsing.
        sys.path.insert(0, str(LOCAL / 'llm'))
        sys.path.insert(0, str(LOCAL / 'bears'))
        from evaluation import safe_metrics, verify_audit
        from providers import unknown_metrics
        from tools.local_experiment.query_diagnose.provider import ProviderError
        from workflow import agenthouse_module, disable_external_tracing
        from run_sre import module, SpanFile
        from run_sre_llm import AUDIT_FIELDS, verify_audits
        from run_catalog import verify_saved_spans
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.trace import Status, StatusCode

        disable_external_tracing()
        self.safe_metrics, self.unknown_metrics, self.ProviderError = safe_metrics, unknown_metrics, ProviderError
        self.verify_audit, self.verify_audits, self.verify_spans = verify_audit, verify_audits, verify_saved_spans
        self.Status, self.StatusCode, self.fields = Status, StatusCode, AUDIT_FIELDS
        self.agenthouse = agenthouse or agenthouse_module()
        self.loki = loki or module('query_diagnose_loki', LOCAL / 'loki/manage.py')
        self.client = self.agenthouse.Client()
        self.profile, self.output, self.progress = profile, output, progress
        self.provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-query-diagnose'}))
        self.provider.add_span_processor(SimpleSpanProcessor(SpanFile(output / 'spans.jsonl')))
        self.tracer = self.provider.get_tracer('shirokuma.query.diagnose', '1.0')
        self.context = self.root = None
        self.started = time.monotonic()
        self.ready = self.closed = False
        self.report = {'task_id': 'workflow-' + token, 'trace_id': None, 'records': [],
                       'verified': False, 'saved_spans_verified': False}

    def save(self):
        self.progress(self.report)

    def start(self):
        if self.context is not None:
            raise AuditError('AUDIT_FAILED')
        self.context = self.tracer.start_as_current_span('bear.query_diagnose.assess', record_exception=False,
                                                       set_status_on_exception=False)
        self.root = self.context.__enter__()
        self.report['trace_id'] = f'{self.root.get_span_context().trace_id:032x}'
        self.root.set_attribute('task_id', self.report['task_id'])
        self.save()
        if self.client.query('SELECT 1').strip() != '1':
            raise AuditError('AUDIT_FAILED')

    def emit(self, action, status, metrics, span, inference=False):
        event = self.agenthouse.event(action, 'llm-local-compatible' if inference else 'trino-read-only',
            agent_id='query-diagnose-local-llm', task_id=self.report['task_id'], trace_id=self.report['trace_id'],
            status=status, blocked_by_policy=status == 'blocked',
            model_provider=self.profile['provider'] if inference else 'none',
            model_name=self.profile['model'] if inference else '', **metrics)
        record = {key: event[key] for key in self.fields}
        record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
        self.report['records'].append(record)
        self.save()
        try:
            self.agenthouse.insert(self.client, event)
            self.verify_audit(self.client, event)
            self.loki.push_record({'task_id': self.report['task_id'], 'trace_id': self.report['trace_id'],
                'span_id': record['span_id'], 'action': action, 'status': status})
        except BaseException as error:
            record['audit_delivery'] = 'failed'
            try:
                self.save()
            except BaseException as other:
                error = first(error, other)
            if not isinstance(error, Exception):
                raise error
            raise AuditError('AUDIT_FAILED') from None
        record['audit_delivery'] = 'delivered'
        self.save()

    def phase(self, action, call, *, inference=False, validate=None):
        if action not in ('bear.query_diagnose.observe', 'llm.query_diagnose') or self.root is None or self.closed:
            raise AuditError('AUDIT_FAILED')
        if any(row['action_type'] == action for row in self.report['records']):
            raise AuditError('AUDIT_FAILED')
        with preserve_context(self.tracer.start_as_current_span(action, record_exception=False, set_status_on_exception=False)) as span:
            started = time.monotonic()
            metrics, validating = None, False
            try:
                value = call()
                elapsed = int((time.monotonic() - started) * 1000)
                metrics = self.safe_metrics(value, elapsed) if inference else self.unknown_metrics(elapsed)
                if validate is not None:
                    validating = True
                    value = validate(value)
            except BaseException as error:
                elapsed = int((time.monotonic() - started) * 1000)
                if metrics is None:
                    metrics = self.safe_metrics(error.metrics if isinstance(error, self.ProviderError) else {}, elapsed) if inference else self.unknown_metrics(elapsed)
                span.set_status(self.Status(self.StatusCode.ERROR, 'PHASE_FAILED'))
                try:
                    self.emit(action, 'blocked' if validating and isinstance(error, Exception) else 'failed', metrics, span, inference)
                except BaseException as other:
                    error = first(error, other)
                raise error
            self.emit(action, 'success', metrics, span, inference)
            span.set_status(self.Status(self.StatusCode.OK))
            return value

    def finish(self):
        if self.ready or self.closed or self.root is None:
            raise AuditError('AUDIT_FAILED')
        self.emit('bear.query_diagnose.assess', 'success',
                  self.unknown_metrics(int((time.monotonic() - self.started) * 1000)), self.root)
        quoted = ','.join("'" + str(uuid.UUID(row['event_id'])) + "'" for row in self.report['records'])
        rows = json.loads(self.client.query('SELECT ' + ', '.join(self.fields) +
            ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + quoted + ') FORMAT JSON'))['data']
        self.verify_audits(rows, self.loki.query_task(self.report['task_id']), [], self.report['records'],
                           self.report['task_id'], self.report['trace_id'])
        self.ready = True

    def close(self, original=None):
        if self.closed:
            raise AuditError('AUDIT_FAILED')
        self.closed = True
        secondary = None
        try:
            if self.root is not None:
                if not any(row['action_type'] == 'bear.query_diagnose.assess' for row in self.report['records']):
                    try:
                        self.emit('bear.query_diagnose.assess', 'failed',
                                  self.unknown_metrics(int((time.monotonic() - self.started) * 1000)), self.root)
                    except BaseException as error:
                        secondary = first(secondary, error)
                success = original is None and secondary is None and self.ready
                self.root.set_status(self.Status(self.StatusCode.OK if success else self.StatusCode.ERROR,
                                                 None if success else 'DIAGNOSE_FAILED'))
                try:
                    self.context.__exit__(None, None, None)
                except BaseException as error:
                    secondary = first(secondary, error)
        finally:
            try:
                self.provider.shutdown()
            except BaseException as error:
                secondary = first(secondary, error)
        if original is None and secondary is None and self.ready:
            try:
                self.verify_spans(self.output / 'spans.jsonl', self.report['records'], self.report['trace_id'])
                self.report.update(verified=True, saved_spans_verified=True)
            except BaseException as error:
                secondary = first(secondary, error)
        try:
            self.save()
        except BaseException as error:
            secondary = first(secondary, error)
        if secondary is not None:
            if not isinstance(secondary, Exception):
                raise secondary
            raise AuditError('AUDIT_FAILED') from None
