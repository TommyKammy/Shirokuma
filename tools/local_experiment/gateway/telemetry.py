"""OTel spans and normalized per-call Pawprints; no raw requests are retained."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from core import LOCAL, STATE, load_module


class Telemetry:
    def __init__(self):
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        STATE.chmod(0o700)
        self.lock = threading.Lock()
        self.agenthouse = load_module('gateway_agenthouse_audit', LOCAL / 'agenthouse/manage.py')
        self.loki = load_module('gateway_loki_audit', LOCAL / 'loki/manage.py')
        owner = self

        class JSONSpanExporter(SpanExporter):
            def export(self, spans):
                for span in spans:
                    context = span.get_span_context()
                    owner.append('spans.jsonl', {'name': span.name,
                        'trace_id': f'{context.trace_id:032x}', 'span_id': f'{context.span_id:016x}',
                        'start_time_unix_nano': span.start_time, 'end_time_unix_nano': span.end_time,
                        'attributes': dict(span.attributes or {})})
                return SpanExportResult.SUCCESS

        self.provider = TracerProvider(resource=Resource.create({'service.name': 'shirokuma-local-gateway'}))
        self.provider.add_span_processor(SimpleSpanProcessor(JSONSpanExporter()))
        self.tracer = self.provider.get_tracer('shirokuma.gateway', '0.1.0')

    def append(self, filename: str, row: dict) -> None:
        with self.lock:
            fd = os.open(STATE / filename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(json.dumps(row, separators=(',', ':')) + '\n')

    def audit(self, fields: dict, span_id: str) -> str:
        action, target = fields['action_type'], fields['target_system']
        value = self.agenthouse.event(action, target, **{
            key: item for key, item in fields.items() if key not in {'action_type', 'target_system'}},
            agent_id='local-mcp-gateway', model_provider='none', model_name='')
        record = {key: value[key] for key in ('event_id', 'task_id', 'trace_id', 'action_type',
                                             'target_system', 'status', 'blocked_by_policy', 'duration_ms')}
        record['span_id'] = span_id
        try:
            self.agenthouse.insert(self.agenthouse.Client(), value)
            self.loki.push_record({'task_id': value['task_id'], 'trace_id': value['trace_id'],
                                   'span_id': span_id, 'action': action, 'status': value['status']})
            self.append('events.jsonl', {**record, 'audit_delivery': 'delivered'})
        except Exception:
            self.append('events.jsonl', {**record, 'audit_delivery': 'failed'})
            raise
        return value['event_id']

    def close(self):
        self.provider.shutdown()
