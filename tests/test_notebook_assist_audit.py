import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from tools.local_experiment.agenthouse import manage as H
from tools.local_experiment.llm.providers import ProviderError
from tools.local_experiment.notebook_assist import audit as A
from tools.local_experiment.notebook_assist.contract import AssistError


class FakeHouse:
    event = staticmethod(H.event)

    def __init__(self, saved):
        self.saved, self.events = saved, []
        self.insert_error = None
        self.duplicate_read = False
        self.progress_bound = []

    def Client(self):
        return self

    def insert(self, _client, event):
        # Delivery must never precede the safe pending record's progress save.
        record = self.saved[-1]['records'][-1]
        self.progress_bound.append(record['event_id'] == event['event_id'] and record['audit_delivery'] == 'pending')
        if self.insert_error is not None:
            raise self.insert_error
        self.events.append(copy.deepcopy(event))

    def query(self, sql):
        if sql == 'SELECT 1':
            return '1\n'
        ids = re.findall(r"'([0-9a-f-]{36})'", sql)
        rows = [{key: event[key] for key in A.FIELDS} for event in self.events if event['event_id'] in ids]
        for row in rows:
            for key in A.NUMBERS:
                if row[key] is not None:
                    row[key] = str(row[key])
        if self.duplicate_read and rows:
            rows.append(rows[0])
        return json.dumps({'data': rows})


class FakeLoki:
    def __init__(self):
        self.rows, self.error = [], None
        self.duplicate = False

    def push_record(self, value):
        if self.error is not None:
            raise self.error
        self.rows.append(copy.deepcopy(value))

    def query_task(self, task):
        rows = [copy.deepcopy(row) for row in self.rows if row['task_id'] == task]
        return rows + rows[:1] if self.duplicate else rows


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name).resolve()
        self.output.chmod(0o700)
        self.saved = []
        self.house, self.loki = FakeHouse(self.saved), FakeLoki()
        self.audit = A.NotebookAudit('a' * 32, self.output,
            {'provider': 'mtplx', 'model': 'qwen3.8-flash-next'},
            lambda value: self.saved.append(copy.deepcopy(value)), agenthouse=self.house, loki=self.loki)

    def tearDown(self):
        if not self.audit.closed:
            try:
                self.audit.close(RuntimeError('test cleanup'))
            except BaseException:
                pass
        self.temp.cleanup()

    def describe(self):
        self.audit.start()
        self.assertEqual(self.audit.phase(A.DESCRIBE, lambda: {'schema': 'private-schema-canary'}),
                         {'schema': 'private-schema-canary'})

    def accepted(self):
        self.describe()
        self.assertEqual(self.audit.phase(A.INFERENCE, lambda: {'prompt_tokens': 42,
            'completion_tokens': 9, 'duration_ms': 12, 'payload': 'private-prompt-response-canary'},
            inference=True, validate=lambda value: {'columns': ['id'], 'limit': 1}),
            {'columns': ['id'], 'limit': 1})
        self.audit.phase(A.MATERIALIZE, lambda: {'result': 'passed'})

    def test_success_four_correlated_spans_private_no_global_provider(self):
        from opentelemetry import trace
        original = trace.get_tracer_provider()
        self.accepted()
        self.audit.finish()
        self.audit.close()
        self.assertIs(trace.get_tracer_provider(), original)
        self.assertTrue(self.audit.report['verified'])
        self.assertTrue(self.audit.report['saved_spans_verified'])
        self.assertEqual([r['action_type'] for r in self.audit.report['records']], [*A.ACTIONS, A.ASSESS])
        self.assertEqual([event['target_system'] for event in self.house.events],
                         ['trino-read-only','llm-local-compatible','notebook-assets-local','notebook-assets-local'])
        self.assertTrue(all(self.house.progress_bound))
        self.assertEqual(len(self.house.events), 4)
        self.assertEqual(self.house.events[1]['prompt_tokens'], 42)
        self.assertEqual(self.house.events[1]['completion_tokens'], 9)
        self.assertIsNone(self.house.events[0]['prompt_tokens'])
        self.assertTrue(all(x['inference_cost_estimate'] is None for x in self.house.events))
        self.assertTrue(all(x['agent_id'] == 'notebook-assist-local' for x in self.house.events))
        self.assertEqual((self.output / 'spans.jsonl').stat().st_mode & 0o777, 0o600)
        raw = (self.output / 'spans.jsonl').read_text() + json.dumps(self.saved) + json.dumps(self.house.events)
        self.assertNotIn('canary', raw)
        self.assertEqual(len((self.output / 'spans.jsonl').read_text().splitlines()), 4)

    def test_normal_unsupported_blocked_is_verified_root_ok(self):
        self.describe()
        self.assertIsNone(self.audit.phase(A.INFERENCE, lambda: {'prompt_tokens': 0, 'completion_tokens': 0},
                         inference=True, validate=lambda value: None))
        with self.assertRaises(A.AuditError):
            self.audit.phase(A.MATERIALIZE, lambda: self.fail('must not materialize'))
        self.audit.finish(status='blocked')
        self.audit.close()
        self.assertTrue(self.audit.report['verified'])
        self.assertEqual([r['status'] for r in self.audit.report['records']], ['success', 'blocked', 'blocked'])
        self.assertTrue(all(x['status'] == 'OK' for x in map(json.loads, (self.output / 'spans.jsonl').read_text().splitlines())))

    def test_failed_model_preserves_safe_metrics_and_unknowns(self):
        self.describe()
        original = ProviderError('private-error-canary', {'prompt_tokens': 25, 'completion_tokens': True,
                                                        'duration_ms': 19, 'inference_cost_estimate': 5})
        with self.assertRaises(ProviderError) as raised:
            self.audit.phase(A.INFERENCE, lambda: (_ for _ in ()).throw(original), inference=True)
        self.assertIs(raised.exception, original)
        self.audit.close(original)
        record = self.audit.report['records'][1]
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['prompt_tokens'], 25)
        self.assertIsNone(record['completion_tokens'])
        self.assertIsNone(record['inference_cost_estimate'])
        self.assertEqual(record['duration_ms'], 19)
        self.assertFalse(self.audit.report['verified'])
        self.assertNotIn('canary', json.dumps(self.saved) + (self.output / 'spans.jsonl').read_text())

    def test_duplicate_phase_and_wrong_inference_do_not_call(self):
        self.describe()
        for action, inference in [(A.DESCRIBE, False), (A.MATERIALIZE, False), (A.INFERENCE, False)]:
            with self.assertRaises(A.AuditError):
                self.audit.phase(action, lambda: self.fail('invalid phase called'), inference=inference)
        self.assertEqual(len(self.house.events), 1)

    def test_validation_failure_is_blocked_but_not_normal_unsupported(self):
        self.describe()
        original = AssistError('NOTEBOOK_ASSIST_PROPOSAL_INVALID')
        with self.assertRaises(AssistError):
            self.audit.phase(A.INFERENCE, lambda: {'prompt_tokens': 5}, inference=True,
                             validate=lambda value: (_ for _ in ()).throw(original))
        with self.assertRaises(A.AuditError):
            self.audit.finish('blocked')
        self.audit.close(original)
        self.assertEqual(self.audit.report['records'][1]['status'], 'blocked')
        self.assertFalse(self.audit.report['verified'])

    def test_response_save_failure_keeps_known_usage_and_is_failed(self):
        self.describe()
        original = OSError('private-save-error-canary')
        with self.assertRaises(OSError) as raised:
            self.audit.phase(A.INFERENCE, lambda: {'prompt_tokens': 31, 'completion_tokens': 7, 'duration_ms': 22},
                inference=True, validate=lambda value: (_ for _ in ()).throw(original))
        self.assertIs(raised.exception, original)
        self.audit.close(original)
        row = self.audit.report['records'][1]
        self.assertEqual((row['status'], row['prompt_tokens'], row['completion_tokens'], row['duration_ms']),
                         ('failed', 31, 7, 22))
        self.assertFalse(self.audit.report['verified'])

    def test_response_save_cancel_keeps_known_usage_and_original_object(self):
        self.describe()
        original = KeyboardInterrupt('private-save-cancel-canary')
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.audit.phase(A.INFERENCE, lambda: {'prompt_tokens': 31, 'completion_tokens': 7},
                inference=True, validate=lambda value: (_ for _ in ()).throw(original))
        self.assertIs(raised.exception, original)
        self.audit.close(original)
        row = self.audit.report['records'][1]
        self.assertEqual((row['status'], row['prompt_tokens'], row['completion_tokens']), ('failed', 31, 7))
        self.assertFalse(self.audit.report['verified'])

    def test_cancel_identity_survives_delivery_and_cleanup_failure(self):
        self.describe()
        cancel = KeyboardInterrupt('cancel-canary')
        self.house.insert_error = RuntimeError('delivery-canary')
        with self.assertRaises(KeyboardInterrupt) as raised:
            self.audit.phase(A.INFERENCE, lambda: (_ for _ in ()).throw(cancel), inference=True)
        self.assertIs(raised.exception, cancel)
        with self.assertRaises(KeyboardInterrupt) as closed:
            self.audit.close(cancel)
        self.assertIs(closed.exception, cancel)
        self.assertFalse(self.audit.report['verified'])
        self.assertEqual(self.audit.report['records'][1]['audit_delivery'], 'failed')

    def test_delivery_failure_stays_failed_and_cannot_retry(self):
        self.audit.start()
        self.house.insert_error = RuntimeError('private-delivery-canary')
        with self.assertRaises(A.AuditError) as raised:
            self.audit.phase(A.DESCRIBE, lambda: {})
        self.assertEqual(str(raised.exception), 'NOTEBOOK_AUDIT_FAILED')
        self.assertEqual(self.saved[-1]['records'][0]['audit_delivery'], 'failed')
        with self.assertRaises(A.AuditError):
            self.audit.phase(A.DESCRIBE, lambda: self.fail('retry'))

    def test_progress_failure_prevents_any_insert(self):
        self.audit.start()
        self.audit.progress = lambda value: (_ for _ in ()).throw(RuntimeError('progress failed'))
        with self.assertRaises(RuntimeError):
            self.audit.phase(A.DESCRIBE, lambda: {})
        self.assertEqual(self.house.events, [])
        self.assertFalse(self.audit.report['verified'])

    def test_duplicate_agenthouse_readback_is_refused(self):
        self.audit.start()
        self.house.duplicate_read = True
        with self.assertRaises(A.AuditError):
            self.audit.phase(A.DESCRIBE, lambda: {})
        self.assertEqual(self.audit.report['records'][0]['audit_delivery'], 'failed')

    def test_duplicate_loki_readback_cannot_verify_finish(self):
        self.accepted()
        self.loki.duplicate = True
        with self.assertRaises(A.AuditError):
            self.audit.finish()
        self.audit.close()
        self.assertFalse(self.audit.report['verified'])

    def test_export_failure_is_not_hidden_by_sdk_processor(self):
        self.accepted()
        self.audit.finish()
        self.audit.exporter.failed = OSError('private-export-canary')
        with self.assertRaises(A.AuditError):
            self.audit.close()
        self.assertFalse(self.audit.report['verified'])

    def test_failed_start_prevents_any_phase(self):
        with patch.object(self.house, 'query', return_value='0'):
            with self.assertRaises(A.AuditError):
                self.audit.start()
        with self.assertRaises(A.AuditError):
            self.audit.phase(A.DESCRIBE, lambda: self.fail('phase after failed audit preflight'))

    def test_extra_local_span_field_cannot_be_verified(self):
        self.accepted()
        self.audit.finish()
        path = self.output / 'spans.jsonl'
        # Tamper only after all spans close, so the assertion is about
        # verification, not concurrent exporter writes.
        original = self.audit._verify_spans
        def changed():
            current = [json.loads(line) for line in path.read_text().splitlines()]
            current[0]['extra'] = 'canary'
            path.write_text(''.join(json.dumps(row) + '\n' for row in current))
            original()
        with patch.object(self.audit, '_verify_spans', side_effect=changed):
            with self.assertRaises(A.AuditError):
                self.audit.close()
        self.assertFalse(self.audit.report['verified'])

    def test_finished_audit_cannot_run_phase_or_close_twice(self):
        self.accepted()
        self.audit.finish()
        with self.assertRaises(A.AuditError):
            self.audit.phase(A.MATERIALIZE, lambda: self.fail('late materialize'))
        self.audit.close()
        with self.assertRaises(A.AuditError):
            self.audit.close()

    def test_original_cancel_wins_context_cleanup_and_suppression(self):
        cancel = KeyboardInterrupt()
        class Context:
            def __enter__(self): return self
            def __exit__(self, *args): raise RuntimeError('cleanup failed')
        with self.assertRaises(KeyboardInterrupt) as raised:
            with A.preserve_context(Context()): raise cancel
        self.assertIs(raised.exception, cancel)
        class Suppress(Context):
            def __exit__(self, *args): return True
        with self.assertRaises(KeyboardInterrupt) as raised:
            with A.preserve_context(Suppress()): raise cancel
        self.assertIs(raised.exception, cancel)

    def test_external_current_span_does_not_supply_audit_trace(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.resources import Resource
        parent = TracerProvider(resource=Resource({}), shutdown_on_exit=False)
        try:
            with parent.get_tracer('test').start_as_current_span('external') as span:
                self.audit.start()
                self.assertNotEqual(self.audit.report['trace_id'], f'{span.get_span_context().trace_id:032x}')
                self.audit.close(RuntimeError('test done'))
        finally:
            parent.shutdown()


if __name__ == '__main__':
    unittest.main()
