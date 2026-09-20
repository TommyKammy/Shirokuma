"""Local Notebook assist spans and bounded Pawprints, without model payloads.

Runtime dependencies are imported only when constructing an audit. This module
does not import the MCP workflows or install a global OpenTelemetry provider.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

DESCRIBE = 'bear.notebook_preview.describe'
INFERENCE = 'llm.notebook_preview'
MATERIALIZE = 'bear.notebook_preview.materialize'
ASSESS = 'bear.notebook_preview.assess'
ACTIONS = (DESCRIBE, INFERENCE, MATERIALIZE)
FIELDS = ('event_id', 'task_id', 'trace_id', 'action_type', 'status', 'model_provider',
          'model_name', 'prompt_tokens', 'completion_tokens',
          'inference_cost_estimate', 'duration_ms')
NUMBERS = ('prompt_tokens', 'completion_tokens', 'duration_ms')


class AuditError(RuntimeError):
    def __init__(self):
        super().__init__('NOTEBOOK_AUDIT_FAILED')


def first(original, other):
    return other if original is None or isinstance(original, Exception) and not isinstance(other, Exception) else original


def require(value):
    if not value:
        raise AuditError()


def safe_metrics(value, elapsed):
    value = value if type(value) is dict else {}
    result = {key: value.get(key) if type(value.get(key)) is int and 0 <= value[key] < 2**64 else None
              for key in NUMBERS}
    result['duration_ms'] = elapsed if result['duration_ms'] is None else result['duration_ms']
    result['inference_cost_estimate'] = None
    return result


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
            raise original
    except BaseException as error:
        raise first(original, error)


def private_directory(path):
    require(path.is_absolute() and path.resolve() == path)
    value = path.stat()
    require(stat.S_ISDIR(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o700 and value.st_uid == os.getuid())


def fingerprint(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)


class NotebookAudit:
    actions = ACTIONS
    assess = ASSESS
    read_actions = (DESCRIBE,)
    execution_target = 'notebook-assets-local'

    def __init__(self, token, output, profile, progress, *, agenthouse=None, loki=None):
        require(type(token) is str and re.fullmatch(r'[0-9a-f]{32}', token) is not None)
        require(type(profile) is dict and all(type(profile.get(k)) is str and
                re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', profile[k]) for k in ('provider', 'model')))
        require(callable(progress))
        output = Path(output)
        private_directory(output)
        from tools.local_experiment.agenthouse import manage as default_agenthouse
        from tools.local_experiment.loki import manage as default_loki
        from tools.local_experiment.notebook_assist.contract import AssistError
        from opentelemetry.context import Context
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        from opentelemetry.trace import Status, StatusCode
        actions, assess = self.actions, self.assess

        class SpanFile(SpanExporter):
            def __init__(self, path):
                self.path, self.failed = path, None
                self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

            def export(self, spans):
                try:
                    for span in spans:
                        require(span.name in (*actions, assess))
                        ctx = span.get_span_context()
                        row = dict(name=span.name, trace_id=f'{ctx.trace_id:032x}', span_id=f'{ctx.span_id:016x}',
                                   start_time_unix_nano=span.start_time, end_time_unix_nano=span.end_time,
                                   status=span.status.status_code.name,
                                   attributes={'task_id': 'workflow-' + token})
                        raw = (json.dumps(row, sort_keys=True) + '\n').encode('ascii')
                        require(os.write(self.fd, raw) == len(raw))
                        os.fsync(self.fd)
                    return SpanExportResult.SUCCESS
                except BaseException as error:
                    self.failed = first(self.failed, error)
                    if not isinstance(error, Exception):
                        raise
                    return SpanExportResult.FAILURE

            def shutdown(self):
                if self.fd is not None:
                    fd, self.fd = self.fd, None
                    os.close(fd)

        self.agenthouse = default_agenthouse if agenthouse is None else agenthouse
        self.loki = default_loki if loki is None else loki
        self.client = self.agenthouse.Client()
        self.profile = {key: profile[key] for key in ('provider', 'model')}
        self.output, self.progress = output, progress
        self.Status, self.StatusCode, self.Context = Status, StatusCode, Context
        self.AssistError = AssistError
        # Resource(...) bypasses host/environment resource detectors. The local
        # provider and explicit sampler do not configure global tracing/exporters.
        self.provider = TracerProvider(resource=Resource({'service.name': 'notebook-assist-local'}),
                                       sampler=ALWAYS_ON, shutdown_on_exit=False)
        self.exporter = SpanFile(output / 'spans.jsonl')
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer('shirokuma.notebook.assist', '1.0')
        self.context = self.root = None
        self.started = time.monotonic()
        self.ready = self.closed = self.failed = False
        self.phases, self.started_phases = {}, []
        self.report = dict(task_id='workflow-' + token, trace_id=None, records=[],
                           verified=False, saved_spans_verified=False)

    def save(self):
        self.progress(self.report)

    def start(self):
        require(self.context is None and not self.closed)
        self.context = self.tracer.start_as_current_span(self.assess, context=self.Context(),
            record_exception=False, set_status_on_exception=False)
        self.root = self.context.__enter__()
        self.report['trace_id'] = f'{self.root.get_span_context().trace_id:032x}'
        self.root.set_attribute('task_id', self.report['task_id'])
        try:
            self.save()
            require(self.client.query('SELECT 1').strip() == '1')
        except BaseException:
            self.failed = True
            raise

    def _verify_rows(self, rows, records):
        require(type(rows) is list and len(rows) == len(records))
        expected = {x['event_id']: x for x in records}
        require(len(expected) == len(records) and all(type(row) is dict and set(row) == set(FIELDS) for row in rows))
        require({row['event_id'] for row in rows} == set(expected))
        for row in rows:
            for key in FIELDS:
                observed = row[key]
                if key in NUMBERS and observed is not None:
                    if type(observed) is str and observed.isascii() and observed.isdigit():
                        observed = int(observed)
                    require(type(observed) is int and 0 <= observed < 2**64)
                require(observed == expected[row['event_id']][key])

    def _read_rows(self, records):
        ids = ','.join("'" + str(uuid.UUID(row['event_id'])) + "'" for row in records)
        raw = self.client.query('SELECT ' + ', '.join(FIELDS) +
            ' FROM agenthouse.pawprints FINAL WHERE event_id IN (' + ids + ') FORMAT JSON')
        value = json.loads(raw)
        self._verify_rows(value['data'], records)

    def emit(self, action, status, metrics, span, inference=False):
        target = 'llm-local-compatible' if inference else 'trino-read-only' if action in self.read_actions else self.execution_target
        event = self.agenthouse.event(action, target, agent_id='notebook-assist-local',
            task_id=self.report['task_id'], trace_id=self.report['trace_id'], status=status,
            blocked_by_policy=status == 'blocked', model_provider=self.profile['provider'] if inference else 'none',
            model_name=self.profile['model'] if inference else '', **metrics)
        record = {key: event[key] for key in FIELDS}
        record.update(span_id=f'{span.get_span_context().span_id:016x}', audit_delivery='pending')
        self.report['records'].append(record)
        self.save()  # Durable pending identity precedes any delivery attempt.
        try:
            self.agenthouse.insert(self.client, event)
            self._read_rows([record])
            self.loki.push_record(dict(task_id=self.report['task_id'], trace_id=self.report['trace_id'],
                                       span_id=record['span_id'], action=action, status=status))
        except BaseException as error:
            record['audit_delivery'] = 'failed'
            try:
                self.save()
            except BaseException as other:
                error = first(error, other)
            if not isinstance(error, Exception):
                raise error
            raise AuditError() from None
        record['audit_delivery'] = 'delivered'
        self.save()

    def phase(self, action, call, *, inference=False, validate=None):
        require(self.root is not None and not (self.closed or self.ready or self.failed))
        require(type(inference) is bool and inference == (action == self.actions[1]))
        require(len(self.started_phases) < len(self.actions) and action == self.actions[len(self.started_phases)])
        require(all(value == 'success' for value in self.phases.values()))
        self.started_phases.append(action)
        with preserve_context(self.tracer.start_as_current_span(action, record_exception=False,
                                                                set_status_on_exception=False)) as span:
            span.set_attribute('task_id', self.report['task_id'])
            started, metrics, validating = time.monotonic(), None, False
            try:
                value = call()
                metrics = safe_metrics(value if inference else {}, int((time.monotonic() - started) * 1000))
                if validate is not None:
                    validating = True
                    value = validate(value)
            except BaseException as error:
                self.failed = True
                if metrics is None:
                    metrics = safe_metrics(getattr(error, 'metrics', {}) if inference else {},
                                           int((time.monotonic() - started) * 1000))
                span.set_status(self.Status(self.StatusCode.ERROR, 'PHASE_FAILED'))
                try:
                    self.emit(action, 'blocked' if validating and isinstance(error, self.AssistError) else 'failed', metrics, span, inference)
                except BaseException as other:
                    error = first(error, other)
                raise error
            status = 'blocked' if inference and validate is not None and value is None else 'success'
            self.phases[action] = status
            try:
                self.emit(action, status, metrics, span, inference)
            except BaseException:
                self.failed = True
                span.set_status(self.Status(self.StatusCode.ERROR, 'AUDIT_FAILED'))
                raise
            span.set_status(self.Status(self.StatusCode.OK))
            return value

    def finish(self, status='success'):
        require(self.root is not None and not (self.ready or self.closed or self.failed))
        expected = {self.actions[0]: 'success', self.actions[1]: 'blocked'} if status == 'blocked' else dict.fromkeys(self.actions, 'success')
        require(status in ('success', 'blocked') and self.phases == expected)
        require(not any(row['action_type'] == self.assess for row in self.report['records']))
        self.emit(self.assess, status, safe_metrics({}, int((time.monotonic() - self.started) * 1000)), self.root)
        self._read_rows(self.report['records'])
        logs = self.loki.query_task(self.report['task_id'])
        expected_logs = [dict(task_id=row['task_id'], trace_id=row['trace_id'], span_id=row['span_id'],
                              action=row['action_type'], status=row['status']) for row in self.report['records']]
        require(type(logs) is list and len(logs) == len(expected_logs) and
                all(type(row) is dict for row in logs) and
                sorted(logs, key=lambda row: row.get('span_id', '')) == sorted(expected_logs, key=lambda row: row['span_id']))
        self.ready = True

    def _verify_spans(self):
        require(self.exporter.failed is None)
        path = self.output / 'spans.jsonl'
        private_directory(self.output)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600 and
                    before.st_uid == os.getuid() and before.st_nlink == 1 and 0 < before.st_size <= 65536)
            raw = os.read(fd, 65537)
            after = os.fstat(fd)
            require(fingerprint(before) == fingerprint(after) == fingerprint(path.lstat()) and len(raw) == before.st_size)
        finally:
            os.close(fd)
        spans = [json.loads(line) for line in raw.splitlines()]
        expected = {row['span_id']: row for row in self.report['records']}
        require(len(spans) == len(expected) == len(self.report['records']) and
                {span['span_id'] for span in spans} == set(expected))
        for span in spans:
            record = expected[span['span_id']]
            require(set(span) == {'name', 'trace_id', 'span_id', 'start_time_unix_nano', 'end_time_unix_nano',
                                  'status', 'attributes'} and
                    span['trace_id'] == self.report['trace_id'] and span['name'] == record['action_type'] and
                    span['status'] == 'OK' and record['status'] in ('success', 'blocked') and
                    record['audit_delivery'] == 'delivered' and span['attributes'] == {'task_id': self.report['task_id']} and
                    type(span['start_time_unix_nano']) is int and type(span['end_time_unix_nano']) is int and
                    0 < span['start_time_unix_nano'] <= span['end_time_unix_nano'])

    def close(self, original=None):
        require(not self.closed)
        self.closed = True
        secondary = None
        try:
            if self.root is not None:
                if not any(row['action_type'] == self.assess for row in self.report['records']):
                    try:
                        self.emit(self.assess, 'failed', safe_metrics({}, int((time.monotonic() - self.started) * 1000)), self.root)
                    except BaseException as error:
                        secondary = first(secondary, error)
                success = original is None and secondary is None and self.ready
                self.root.set_status(self.Status(self.StatusCode.OK if success else self.StatusCode.ERROR,
                                                 None if success else 'NOTEBOOK_ASSIST_FAILED'))
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
                self._verify_spans()
                self.report.update(verified=True, saved_spans_verified=True)
            except BaseException as error:
                secondary = first(secondary, error)
        try:
            self.save()
        except BaseException as error:
            self.report.update(verified=False, saved_spans_verified=False)
            secondary = first(secondary, error)
        if secondary is not None:
            selected = first(original, secondary)
            if not isinstance(selected, Exception) or selected is original:
                raise selected
            raise AuditError() from None


class CountNotebookAudit(NotebookAudit):
    """Separate COUNT action names; unchanged delivery and measured-usage handling."""
    from tools.local_experiment.notebook_assist import count as contract
    actions = (contract.DESCRIBE, contract.INFERENCE, contract.COUNT)
    assess = contract.ASSESS
    read_actions = (contract.DESCRIBE, contract.COUNT)
    execution_target = 'notebook-count-local'
