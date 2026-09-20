from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import uuid

from tools.local_experiment.activation import fixture as F

TASK = 'activation-' + 'a' * 32


class Store:
    def __init__(self):
        self.client = self
        self.rows = []
        self.calls = []
        self.guard_calls = 0
        self.guard_failures = {}
        self.reply_error = None
        self.readback = None

    def guard(self):
        self.guard_calls += 1
        self.calls.append('guard')
        error = self.guard_failures.get(self.guard_calls)
        if error is not None:
            raise error

    def query(self, sql):
        if sql.startswith('INSERT '):
            self.calls.append('insert')
            self.rows.append(json.loads(sql.split('\n', 1)[1]))
            if self.reply_error is not None:
                raise self.reply_error
            return ''
        assert sql.startswith('SELECT ') and 'LIMIT 2 SETTINGS' in sql
        self.calls.append('select')
        values = deepcopy(self.rows)
        if values and self.readback is not None:
            return self.readback(values)
        return '\n'.join(json.dumps(value) for value in values)


class ActivationFixtureTests(unittest.TestCase):
    def test_build_fixed_event_new_identity_and_no_runtime(self):
        with patch.object(F.H, 'runtime_store') as factory:
            failed = F.build(TASK, 'failed')
            success = F.build(TASK, 'success')
        factory.assert_not_called()
        self.assertNotEqual(failed['event_id'], success['event_id'])
        self.assertEqual(uuid.UUID(failed['event_id']).version, 4)
        self.assertEqual((failed['agent_id'], failed['action_type'], failed['target_system']),
                         ('local-activation-fixture', 'activation_fixture', 'grafana'))
        self.assertEqual(success['status'], 'success')
        self.assertIsNone(failed['inference_cost_estimate'])

    def test_invalid_task_status_or_event_refused_before_store(self):
        with patch.object(F.H, 'runtime_store') as factory:
            for task, status in [(TASK.upper(), 'failed'), (TASK + '\n', 'failed'),
                                 ('a' * 32, 'failed'), (TASK, 'blocked'), (TASK, True)]:
                with self.subTest(task=task, status=status), self.assertRaises(F.FixtureError):
                    F.build(task, status)
            event = F.build(TASK, 'failed')
            for key, value in [('agent_id', 'other'), ('action_type', 'arbitrary'),
                               ('target_system', 'other'), ('trace_id', 'extra'),
                               ('duration_ms', False), ('blocked_by_policy', 0),
                               ('prompt_tokens', 0), ('issue', 1),
                               ('event_id', str(uuid.uuid1())),
                               ('timestamp', '2026-09-20 00:00:00.123456')]:
                with self.subTest(key=key), self.assertRaises(F.FixtureError):
                    F.append(dict(event, **{key: value}))
            with self.assertRaises(F.FixtureError):
                F.append(dict(event, extra='not allowed'))
        factory.assert_not_called()

    def test_one_insert_full_readback_scope_checks_and_caller_unchanged(self):
        store = Store()
        event = F.build(TASK, 'failed')
        original = deepcopy(event)
        with patch.object(F.H, 'runtime_store', return_value=store) as factory:
            returned = F.append(event)
        factory.assert_called_once_with()
        self.assertEqual(returned, event)
        self.assertEqual(event, original)
        self.assertEqual(store.calls, ['guard', 'select', 'guard', 'insert', 'select', 'guard'])
        returned['status'] = 'success'
        self.assertEqual(store.rows[0], original)

    def test_existing_id_is_never_written_again(self):
        store = Store()
        event = F.build(TASK, 'failed')
        store.rows = [event]
        with self.assertRaisesRegex(F.FixtureError, 'EVENT_EXISTS'):
            F.append(event, store=store)
        self.assertNotIn('insert', store.calls)
        self.assertEqual(store.rows, [event])

    def test_readback_uint64_string_normalizes_but_bool_or_float_does_not(self):
        for value, accepted in [('0', True), (False, False), (0.0, False), ('00', False)]:
            with self.subTest(value=value):
                store = Store()
                store.readback = lambda rows: json.dumps(dict(rows[0], duration_ms=value))
                event = F.build(TASK, 'success')
                if accepted:
                    self.assertEqual(F.append(event, store=store), event)
                else:
                    with self.assertRaises(F.FixtureError):
                        F.append(event, store=store)
                self.assertEqual(store.calls.count('insert'), 1)

    def test_changed_missing_duplicate_or_malformed_readback_refused(self):
        callbacks = [lambda rows: '', lambda rows: '\n'.join([json.dumps(rows[0])] * 2),
                     lambda rows: json.dumps(dict(rows[0], status='success')),
                     lambda rows: json.dumps({k: v for k, v in rows[0].items() if k != 'repo'}),
                     lambda rows: '{"event_id":"x","event_id":"y"}',
                     lambda rows: 'x' * 16385]
        for callback in callbacks:
            with self.subTest(callback=callback):
                store = Store()
                store.readback = callback
                with self.assertRaises(F.FixtureError):
                    F.append(F.build(TASK, 'failed'), store=store)
                self.assertEqual(store.calls.count('insert'), 1)
                self.assertEqual(store.calls[-1], 'guard')

    def test_lost_reply_never_retries_and_retains_original_error(self):
        store = Store()
        first = OSError('lost INSERT response')
        store.reply_error = first
        store.guard_failures[3] = RuntimeError('scope changed later')
        with self.assertRaises(OSError) as raised:
            F.append(F.build(TASK, 'failed'), store=store)
        self.assertIs(raised.exception, first)
        self.assertEqual(store.calls.count('insert'), 1)
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.calls.count('select'), 1)

    def test_first_cancellation_survives_final_guard_failure(self):
        store = Store()
        first = KeyboardInterrupt()
        store.reply_error = first
        store.guard_failures[3] = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as raised:
            F.append(F.build(TASK, 'failed'), store=store)
        self.assertIs(raised.exception, first)
        self.assertEqual(store.calls.count('insert'), 1)

    def test_guard_failure_prevents_write_and_final_failure_prevents_success(self):
        for stage in (1, 2, 3):
            with self.subTest(stage=stage):
                store = Store()
                store.guard_failures[stage] = RuntimeError('owned runtime changed')
                with self.assertRaises(RuntimeError):
                    F.append(F.build(TASK, 'success'), store=store)
                self.assertEqual(store.calls.count('insert'), int(stage == 3))


if __name__ == '__main__':
    unittest.main()
