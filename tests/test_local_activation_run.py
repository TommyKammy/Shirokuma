import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

R = importlib.import_module('tools.local_experiment.activation.run')


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.calls = []
        self.tick_count = 0
        self.start(patch.object(R, 'ROOT', self.root))
        self.start(patch.object(R, 'STATE', self.root / 'activation'))
        self.start(patch.object(R.R, 'status', return_value={'alerting': True}))
        self.start(patch.object(R.G, 'status', return_value={'ready': True}))
        self.start(patch.object(R.A, 'Client'))
        for method in ('create_rule', 'configure_notifications', 'set_paused'):
            self.start(patch.object(R.A, method, side_effect=lambda *a, _m=method: self.calls.append((_m, a))))
        self.start(patch.object(R, 'wait_state', side_effect=self.wait))
        self.start(patch.object(R.F, 'build', side_effect=lambda task, status: {'event_id': status, 'task_id': task, 'status': status}))
        self.start(patch.object(R.F, 'append', side_effect=lambda value: self.calls.append(('append', value['status']))))
        self.start(patch.object(R.S, 'tick', side_effect=self.tick))

    def start(self, p):
        value = p.start(); self.addCleanup(p.stop); return value

    def wait(self, client, task, expected, directory, name):
        self.calls.append(('state', expected))
        return {'state': expected, 'fingerprint': 'same-fingerprint' if expected == 'firing' else None}

    def tick(self, observation, directory):
        self.tick_count += 1
        self.calls.append(('tick', observation['state']))
        return {'result': 'passed' if self.tick_count == 2 else 'skipped',
                'dispatched': self.tick_count == 2, 'diagnostic': {'bounded': True},
                'dagster_run_id': 'same-job', 'run_key': 'same-key'}

    def test_cycle_orders_suppression_before_unpause_and_exactly_one_dispatch(self):
        result = R.cycle()
        self.assertEqual(result['result'], 'passed')
        self.assertEqual(result['fixture_event_ids'], ['failed', 'success'])
        self.assertEqual(result['duplicate_dispatches'], 0)
        self.assertEqual([x[0] for x in self.calls], [
            'create_rule','configure_notifications','set_paused','state','tick','append',
            'state','tick','state','tick','append','state','tick'])
        self.assertFalse(self.calls[2][1][2])
        self.assertEqual(json.loads((R.STATE / 'active.json').read_bytes())['result'], 'passed')

    def test_unknown_previous_attempt_blocks_every_external_call(self):
        R.STATE.mkdir(mode=0o700)
        R.S.N._write(R.STATE / 'active.json', {'result': 'unknown'})
        with self.assertRaisesRegex(R.S.ActivationError, 'PREVIOUS_RUN_UNKNOWN'):
            R.cycle()
        self.assertEqual(self.calls, [])

    def test_uncertain_insert_is_preserved_not_retried_and_rule_paused(self):
        with patch.object(R.F, 'append', side_effect=RuntimeError('lost reply')) as write:
            with self.assertRaisesRegex(RuntimeError, 'lost reply'):
                R.cycle()
        self.assertEqual(write.call_count, 1)
        active = json.loads((R.STATE / 'active.json').read_bytes())
        self.assertEqual(active['result'], 'unknown')
        self.assertTrue(active['rule_paused_after_failure'])
        output = self.root / active['evidence']
        self.assertTrue((output / 'failed-intent.json').exists())
        self.assertFalse((output / 'failed-event.json').exists())
        with self.assertRaisesRegex(R.S.ActivationError, 'PREVIOUS_RUN_UNKNOWN'):
            R.cycle()

    def test_original_cancellation_survives_pause_failure(self):
        original = KeyboardInterrupt('first')
        with patch.object(R.F, 'append', side_effect=original), patch.object(
                R.A, 'set_paused', side_effect=[None, RuntimeError('pause')]):
            with self.assertRaises(KeyboardInterrupt) as error:
                R.cycle()
        self.assertIs(error.exception, original)

    def test_result_publication_cancellation_cannot_publish_active_passed(self):
        original = KeyboardInterrupt('cancelled during result save')
        real_save = R.save
        def failing(directory, name, value):
            if name == 'result.json':
                raise original
            return real_save(directory, name, value)
        with patch.object(R, 'save', side_effect=failing), self.assertRaises(KeyboardInterrupt) as caught:
            R.cycle()
        self.assertIs(caught.exception, original)
        active = json.loads((R.STATE / 'active.json').read_bytes())
        self.assertEqual(active['result'], 'unknown')
        self.assertEqual(active['completed_result'], 'passed')
        before = len(self.calls)
        with self.assertRaisesRegex(R.S.ActivationError, 'PREVIOUS_RUN_UNKNOWN'):
            R.cycle()
        self.assertEqual(len(self.calls), before)

    def test_active_write_failure_after_replace_republishes_unknown_and_keeps_error(self):
        original = OSError('directory fsync failed after replacement')
        real_save = R.S._save
        def failing(path, value):
            real_save(path, value)
            if path.name == 'active.json' and value['result'] == 'passed':
                raise original
        with patch.object(R.S, '_save', side_effect=failing), self.assertRaises(OSError) as caught:
            R.cycle()
        self.assertIs(caught.exception, original)
        active = json.loads((R.STATE / 'active.json').read_bytes())
        self.assertEqual(active['result'], 'unknown')
        self.assertEqual(active['completed_result'], 'passed')

    def test_original_failure_survives_all_later_result_writes(self):
        original = KeyboardInterrupt('first fixture interrupt')
        real_save = R.save
        def failing(directory, name, value):
            if name == 'result.json':
                raise OSError('later result failure')
            return real_save(directory, name, value)
        with patch.object(R.F, 'append', side_effect=original), patch.object(R, 'save', side_effect=failing), \
                self.assertRaises(KeyboardInterrupt) as caught:
            R.cycle()
        self.assertIs(caught.exception, original)
        self.assertEqual(json.loads((R.STATE / 'active.json').read_bytes())['result'], 'unknown')

    def test_wrong_arguments_do_not_start_cycle(self):
        with patch.object(R, 'cycle') as cycle:
            self.assertEqual(R.main(['unexpected']), 1)
        cycle.assert_not_called()


class PollTests(unittest.TestCase):
    def test_known_not_ready_and_stale_are_polled_until_fresh_expected_state(self):
        client = Mock(); client.request.return_value = {'saved': 'raw rules'}
        normal = {'state': 'normal'}
        with patch.object(R.A, 'observe', side_effect=[R.A.GrafanaError('ACTIVATION_STATE_NOT_READY'),
                 R.A.GrafanaError('ACTIVATION_STATE_STALE'), normal]), patch.object(R, 'save') as save, \
                patch.object(R.time, 'monotonic', side_effect=[0, 1, 2]), patch.object(R.time, 'sleep') as sleep:
            actual = R.wait_state(client, 'task', 'normal', Path('/unused'), 'normal')
        self.assertIs(actual, normal)
        self.assertEqual(client.request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(save.call_count, 2)

    def test_unverified_state_fails_immediately_without_poll_or_saved_normal(self):
        client = Mock()
        error = R.A.GrafanaError('ACTIVATION_STATE_UNVERIFIED')
        with patch.object(R.A, 'observe', side_effect=error), patch.object(R, 'save') as save, \
                patch.object(R.time, 'sleep') as sleep, self.assertRaises(R.A.GrafanaError) as caught:
            R.wait_state(client, 'task', 'normal', Path('/unused'), 'normal')
        self.assertIs(caught.exception, error)
        client.request.assert_called_once()
        self.assertEqual([call.args[1] for call in save.call_args_list],
                         ['normal-rejected-grafana.json', 'normal-error.json'])
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
