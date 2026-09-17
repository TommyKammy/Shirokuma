"""Offline lifecycle audit and real local OTel tests; no runtime services."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.warehouse_lifecycle import telemetry as T
from tools.local_experiment.agenthouse import manage as AH

try:
    HAS_OTEL = importlib.util.find_spec('opentelemetry.sdk.trace') is not None
except ModuleNotFoundError:
    HAS_OTEL = False

TOKEN = 'a' * 32
OLD, NEW = str(uuid.uuid4()), str(uuid.uuid4())
CLEANUP = {'verified': True, 'removed': ['owned'] * 12, 'conflicts': [], 'deferred': []}


def projection(action):
    return {'warehouse': 'XS', 'reason': {'busy-defer': 'host-handle-open', 'chill': 'idle-drained',
            'wake': 'wake-ready'}[action], 'active_queries': 0 if action == 'chill' else None,
        'pending_posts': 0, 'open_handles': 1 if action == 'busy-defer' else 0,
        'replicas_before': 0 if action == 'wake' else 1, 'replicas_after': 0 if action == 'chill' else 1,
        'old_pod_uid': OLD, 'new_pod_uid': NEW if action == 'wake' else None,
        'backend_active': action != 'chill'}


def receipt():
    return {'run_token': TOKEN, 'result': 'verified', 'pending': None,
            'observations': {'result': 'verified'}, 'cleanup': copy.deepcopy(CLEANUP)}


class FakeBackend:
    def __init__(self, fail=None, interrupt=False):
        self.events, self.logs = [], []
        self.fail, self.interrupt = fail, interrupt

    def event(self, **fields):
        return AH.event(**fields)

    def check(self, stage):
        if self.fail == stage:
            self.fail = None
            if self.interrupt:
                raise KeyboardInterrupt('PRIVATE INTERRUPT')
            raise RuntimeError('PRIVATE TOKEN SQL ROWS')

    def insert(self, event):
        self.check('insert')
        self.events.append(copy.deepcopy(event))

    def verify(self, event):
        self.check('verify')
        if self.events[-1] != event:
            raise RuntimeError('readback mismatch')

    def push_log(self, record):
        self.check('push_log')
        self.logs.append(copy.deepcopy(record))

    def verify_log(self, record):
        self.check('verify_log')
        if self.logs[-1] != record:
            raise RuntimeError('readback mismatch')


class ProjectionTests(unittest.TestCase):
    def test_unknown_is_not_zero_and_projection_is_independent(self):
        value = projection('wake')
        result = T.project_decision('wake', 'success', value, 0)
        self.assertIsNone(result['active_queries'])
        result['warehouse'] = 'changed'
        self.assertEqual(value['warehouse'], 'XS')

    def test_unbounded_extra_or_wrong_types_rejected(self):
        for key, value in [('sql', 'SELECT private'), ('warehouse', 'foreign'), ('reason', 'free text'),
                           ('pending_posts', None), ('open_handles', True), ('active_queries', -1),
                           ('active_queries', 1025), ('backend_active', 1), ('old_pod_uid', 'not-a-uid')]:
            with self.subTest(key=key, value=value):
                candidate = projection('chill')
                candidate[key] = value
                with self.assertRaises(T.TelemetryError):
                    T.project_decision('chill', 'success', candidate, 1)
        for duration in (None, True, -1, 1.2, 2**64):
            with self.subTest(duration=duration), self.assertRaises(T.TelemetryError):
                T.project_decision('chill', 'success', projection('chill'), duration)

    def test_success_requires_actual_phase_evidence(self):
        for action, key, value in [('busy-defer', 'open_handles', 0), ('chill', 'active_queries', None),
                ('chill', 'backend_active', True), ('chill', 'open_handles', 1),
                ('wake', 'new_pod_uid', OLD), ('wake', 'new_pod_uid', None), ('wake', 'replicas_after', 0)]:
            with self.subTest(action=action, key=key):
                candidate = projection(action)
                candidate[key] = value
                with self.assertRaises(T.TelemetryError):
                    T.project_decision(action, T.STATUSES[action], candidate, 1)

    def test_operational_failure_retains_unknowns_but_cannot_imitate_success(self):
        value = projection('chill')
        value.update(reason='interrupted', replicas_after=None, active_queries=None, backend_active=None)
        self.assertEqual(T.project_decision('chill', 'failed', value, 7), value)
        with self.assertRaises(T.TelemetryError):
            T.project_decision('chill', 'success', value, 7)
        with self.assertRaises(T.TelemetryError):
            T.project_decision('busy-defer', 'success', projection('busy-defer'), 1)


@unittest.skipUnless(HAS_OTEL, 'Run with the pinned local LLM venv for OpenTelemetry coverage')
class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name).resolve() / TOKEN
        self.output.mkdir(mode=0o700)
        self.backend = FakeBackend()

    def telemetry(self):
        return T.LifecycleTelemetry(self.output, TOKEN, backend=self.backend)

    def saved(self):
        return json.loads((self.output / 'telemetry.json').read_text())

    def decisions(self, audit):
        for index, action in enumerate(T.ACTIONS):
            audit.decision(action, T.STATUSES[action], projection(action), 1000000 + index)

    def test_three_live_decisions_and_root_have_real_delivery_spans(self):
        with self.telemetry() as audit:
            self.decisions(audit)
            audit.record_cleanup(CLEANUP)
            audit.finish(receipt())
        value = self.saved()
        spans = [json.loads(line) for line in (self.output / 'lifecycle-spans.jsonl').read_text().splitlines()]
        self.assertEqual(value['result'], 'passed')
        self.assertTrue(value['saved_spans_verified'])
        self.assertEqual((len(self.backend.events), len(self.backend.logs), len(spans)), (4, 4, 4))
        self.assertEqual({e['agent_id'] for e in self.backend.events}, {T.ACTOR})
        self.assertEqual({e['task_id'] for e in self.backend.events}, {'warehouse-lifecycle-' + TOKEN})
        self.assertEqual({e['trace_id'] for e in self.backend.events}, {value['trace_id']})
        self.assertEqual(self.backend.events[0]['status'], 'blocked')
        self.assertFalse(any(e['blocked_by_policy'] for e in self.backend.events))
        self.assertTrue(all(e['prompt_tokens'] is None and e['completion_tokens'] is None
                            and e['inference_cost_estimate'] is None for e in self.backend.events))
        for span in spans[:-1]:
            self.assertEqual(span['parent_span_id'], value['root_span_id'])
            self.assertEqual(span['attributes']['duration_kind'], 'audit-delivery-not-controller-decision')
            # A large operation duration is an attribute, never a forged start/end interval.
            self.assertGreater(span['attributes']['controller_duration_ms'], 999999)
            self.assertLess(span['end_time_unix_nano'] - span['start_time_unix_nano'], 10**11)
        self.assertEqual(spans[-1]['name'], 'warehouse.lifecycle.run')
        self.assertIsNone(spans[-1]['parent_span_id'])
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.output.iterdir()))

    def test_decision_order_and_duplicate_are_refused_before_delivery(self):
        with self.assertRaises(T.TelemetryError):
            with self.telemetry() as audit:
                audit.decision('wake', 'success', projection('wake'), 1)
        self.assertEqual([e['action_type'] for e in self.backend.events], ['warehouse.lifecycle.run'])
        self.assertEqual(self.saved()['result'], 'failed')

    def test_each_delivery_failure_is_durable_and_never_passes(self):
        for stage in ('insert', 'verify', 'push_log', 'verify_log'):
            with self.subTest(stage=stage):
                output = self.output / stage / TOKEN
                output.parent.mkdir(mode=0o700)
                output.mkdir(mode=0o700)
                backend = FakeBackend(stage)
                with self.assertRaises(T.TelemetryError):
                    with T.LifecycleTelemetry(output, TOKEN, backend=backend) as audit:
                        audit.decision('busy-defer', 'blocked', projection('busy-defer'), 4)
                value = json.loads((output / 'telemetry.json').read_text())
                self.assertEqual(value['result'], 'failed')
                self.assertEqual(value['records'][0]['delivery'], 'failed')
                self.assertEqual(value['records'][-1]['event']['status'], 'failed')
                self.assertNotIn('PRIVATE', json.dumps(value))
                self.assertLessEqual(len(backend.events), 2)

    def test_journal_write_failure_prevents_decision_network_write(self):
        audit = self.telemetry()
        save = audit.save
        rejected = []
        def flaky():
            if audit.journal['pending_action'] and not rejected:
                rejected.append(True)
                raise OSError('PRIVATE PATH')
            save()
        with self.assertRaises(OSError):
            with audit:
                with patch.object(audit, 'save', side_effect=flaky):
                    audit.decision('busy-defer', 'blocked', projection('busy-defer'), 1)
        self.assertEqual([e['action_type'] for e in self.backend.events], ['warehouse.lifecycle.run'])
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertNotIn('PRIVATE', json.dumps(self.saved()))

    def test_failure_cleanup_is_recorded_without_success_escalation(self):
        for verified in (True, False):
            with self.subTest(verified=verified):
                output = self.output / str(verified) / TOKEN
                output.parent.mkdir(mode=0o700)
                output.mkdir(mode=0o700)
                with self.assertRaises(KeyboardInterrupt):
                    with T.LifecycleTelemetry(output, TOKEN, backend=FakeBackend()) as audit:
                        audit.record_cleanup({'verified': verified, 'removed': ['owned'] if verified else [],
                                              'conflicts': [] if verified else ['conflict'], 'deferred': []})
                        raise KeyboardInterrupt('PRIVATE')
                value = json.loads((output / 'telemetry.json').read_text())
                self.assertEqual(value['result'], 'failed')
                self.assertEqual(value['cleanup_verified'], verified)
                self.assertEqual(value['cleanup']['conflicts_count'], int(not verified))

    def test_interrupted_delivery_preserves_type_and_unknown_usage(self):
        self.backend = FakeBackend('push_log', interrupt=True)
        with self.assertRaises(KeyboardInterrupt):
            with self.telemetry() as audit:
                audit.decision('busy-defer', 'blocked', projection('busy-defer'), 2)
        self.assertEqual(self.saved()['result'], 'failed')
        self.assertIsNone(self.backend.events[0]['prompt_tokens'])

    def test_original_interrupt_survives_subsequent_journal_outage(self):
        self.backend = FakeBackend('push_log', interrupt=True)
        audit = self.telemetry()
        save = audit.save
        def outage():
            if audit.journal['result'] == 'failed':
                raise OSError('PRIVATE disk outage')
            save()
        with self.assertRaises(KeyboardInterrupt):
            with audit, patch.object(audit, 'save', side_effect=outage):
                audit.decision('busy-defer', 'blocked', projection('busy-defer'), 1)
        self.assertEqual(audit.receipt()['result'], 'failed')

    def test_final_journal_outage_cannot_return_success(self):
        audit = self.telemetry()
        save = audit.save
        def outage():
            if audit.journal['result'] == 'passed':
                raise OSError('PRIVATE final write')
            save()
        with self.assertRaisesRegex(T.TelemetryError, 'FINALIZATION'):
            with patch.object(audit, 'save', side_effect=outage), audit:
                self.decisions(audit)
                audit.finish(receipt())
        self.assertEqual(audit.receipt()['result'], 'failed')
        self.assertNotEqual(self.saved()['result'], 'passed')

    def test_failed_action_cannot_finish(self):
        with self.assertRaises(T.TelemetryError):
            with self.telemetry() as audit:
                value = projection('busy-defer')
                value['reason'] = 'verification-failed'
                audit.decision('busy-defer', 'failed', value, 5)
                audit.finish(receipt())
        self.assertEqual([e['status'] for e in self.backend.events], ['failed', 'failed'])

    def test_cleanup_or_controller_not_verified_prevents_completion(self):
        for field in ('cleanup', 'observations', 'pending'):
            with self.subTest(field=field):
                output = self.output / field / TOKEN
                output.parent.mkdir(mode=0o700)
                output.mkdir(mode=0o700)
                with self.assertRaises(T.TelemetryError):
                    with T.LifecycleTelemetry(output, TOKEN, backend=FakeBackend()) as audit:
                        self.decisions(audit)
                        value = receipt()
                        if field == 'cleanup':
                            value[field]['verified'] = False
                        elif field == 'observations':
                            value[field]['result'] = 'running'
                        else:
                            value[field] = {'action': 'wake'}
                        audit.finish(value)
                self.assertEqual(json.loads((output / 'telemetry.json').read_text())['result'], 'failed')

    def test_saved_span_tampering_or_missing_export_prevents_successful_root(self):
        for mode in ('missing', 'attributes', 'parent'):
            with self.subTest(mode=mode):
                output = self.output / mode / TOKEN
                output.parent.mkdir(mode=0o700)
                output.mkdir(mode=0o700)
                backend = FakeBackend()
                with self.assertRaises(T.TelemetryError):
                    with T.LifecycleTelemetry(output, TOKEN, backend=backend) as audit:
                        self.decisions(audit)
                        lines = [json.loads(s) for s in audit.spans.read_text().splitlines()]
                        if mode == 'missing':
                            lines.pop()
                        elif mode == 'attributes':
                            lines[0]['attributes']['controller_duration_ms'] += 1
                        else:
                            lines[0]['parent_span_id'] = '0' * 16
                        audit.spans.write_text(''.join(json.dumps(line) + '\n' for line in lines))
                        audit.finish(receipt())
                self.assertEqual(backend.events[-1]['status'], 'failed')
                value = json.loads((output / 'telemetry.json').read_text())
                self.assertFalse(value['saved_spans_verified'])
                self.assertEqual(value['result'], 'failed')

    def test_prior_run_and_nonprivate_paths_cannot_be_replayed(self):
        (self.output / 'telemetry.json').write_text('{}')
        with self.assertRaises(T.TelemetryError):
            self.telemetry()
        self.assertEqual(self.backend.events, [])
        (self.output / 'telemetry.json').unlink()
        self.output.chmod(0o755)
        with self.assertRaises(T.TelemetryError):
            self.telemetry()


if __name__ == '__main__':
    unittest.main()
