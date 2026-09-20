"""Actual Dagster evaluation/job with fake diagnostics; no runtime/API calls."""
from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.local_experiment.activation import sensor as A


TASK = 'activation-' + 'a' * 32
FINGERPRINT = 'b' * 64


def observation(state='firing', fingerprint=FINGERPRINT):
    return {'schema_version': 1, 'rule_uid': A.RULE_UID, 'task_id': TASK, 'state': state,
            'fingerprint': fingerprint if state == 'firing' else None}


def diagnostic():
    return {'result': 'passed', 'mode': 'deterministic-read-only', 'llm_inference': False,
            'task_id': 'workflow-' + 'c' * 32, 'evidence': str(A.SRE_STATE / ('c' * 32)),
            'trace_id': 'd' * 32, 'span_id': 'e' * 16, 'audit': 'delivered', 'correlated_events': 4,
            'event_id': '00000000-0000-4000-8000-000000000004',
            'tool_calls': [{'tool': name, 'event_id': '00000000-0000-4000-8000-' + f'{i:012d}',
                            'span_id': str(i) * 16} for i, name in enumerate(
                            ['shirokuma_doctor', 'k8s_read_pods', 'k8s_read_events'], 1)],
            'diagnosis': {'mode': 'deterministic-read-only', 'llm_inference': False,
                          'health': 'healthy-observed-scope', 'actions_executed': []}}


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.directory = self.base / 'activation'

    def test_normal_firing_repeat_normal_runs_one_actual_dagster_job(self):
        calls = []
        def execute(directory):
            cursor = A._read(self.directory / 'cursor.json')
            self.assertIn(A.run_key(observation()), cursor['claimed_keys'])
            self.assertTrue((directory / 'intent.json').is_file())
            calls.append(directory)
            return diagnostic()
        normal = A.tick(observation('normal'), self.directory, diagnose=execute)
        first = A.tick(observation(), self.directory, diagnose=execute)
        raw = (calls[0] / 'result.json').read_bytes()
        second = A.tick(observation(), self.directory, diagnose=execute)
        final = A.tick(observation('normal'), self.directory, diagnose=execute)
        self.assertEqual([normal['result'], first['result'], second['result'], final['result']],
                         ['skipped', 'passed', 'skipped', 'skipped'])
        self.assertEqual(len(calls), 1)
        self.assertTrue(first['dispatched'])
        self.assertTrue(first['cleanup_verified'])
        self.assertEqual(first['dagster_run_id'], second['dagster_run_id'])
        self.assertEqual((calls[0] / 'result.json').read_bytes(), raw)
        from dagster import DagsterInstance
        with DagsterInstance.local_temp(str(self.directory / 'dagster'),
                                       overrides={'telemetry': {'enabled': False}}) as instance:
            runs = instance.get_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].tags['dagster/run_key'], A.run_key(observation()))
            self.assertEqual(runs[0].tags['shirokuma/alert_task_id'], TASK)

    def test_new_fingerprint_allowed_only_after_known_completed_diagnostic(self):
        execute = Mock(side_effect=diagnostic)
        # Callback accepts its private claim directory.
        execute.side_effect = lambda _: diagnostic()
        A.tick(observation(), self.directory, diagnose=execute)
        A.tick(observation('normal'), self.directory, diagnose=execute)
        second = A.tick(observation(fingerprint='d' * 64), self.directory, diagnose=execute)
        self.assertEqual(second['result'], 'passed')
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(len(A._read(self.directory / 'cursor.json')['claimed_keys']), 2)

    def test_actual_sensor_cursor_suppresses_repeated_firing_without_running_job(self):
        from dagster import build_sensor_context
        execute = Mock()
        job, sensor = A.definitions(observation(), execute)
        initial = {'schema_version': 1, 'rule_uid': A.RULE_UID, 'task_id': TASK, 'claimed_keys': []}
        with build_sensor_context(cursor=A.canonical(initial).decode()) as context:
            first = sensor.evaluate_tick(context)
        self.assertEqual(len(first.run_requests), 1)
        self.assertEqual(first.run_requests[0].run_key, A.run_key(observation()))
        with build_sensor_context(cursor=first.cursor) as context:
            second = sensor.evaluate_tick(context)
        self.assertEqual(len(second.run_requests), 0)
        execute.assert_not_called()

    def test_unverified_claim_blocks_same_different_and_normal_without_retry(self):
        failure = RuntimeError('fake unavailable diagnostic')
        execute = Mock(side_effect=failure)
        with self.assertRaises(RuntimeError) as caught:
            A.tick(observation(), self.directory, diagnose=execute)
        self.assertIs(caught.exception, failure)
        raw = (self.directory / 'claims' / FINGERPRINT / 'result.json').read_bytes()
        for value in (observation(), observation(fingerprint='d' * 64), observation('normal')):
            actual = A.tick(value, self.directory, diagnose=execute)
            self.assertEqual(actual['result'], 'unknown')
            self.assertFalse(actual['dispatched'])
            self.assertFalse(actual['cleanup_verified'])
        self.assertEqual(execute.call_count, 1)
        self.assertEqual((self.directory / 'claims' / FINGERPRINT / 'result.json').read_bytes(), raw)

    def test_first_interruption_survives_dagster_and_later_receipt_failure(self):
        interruption = KeyboardInterrupt('original')
        save = A.N._write
        def injected(path, value, **kwargs):
            if path.name == 'result.json':
                raise OSError('later save failure')
            return save(path, value, **kwargs)
        with patch.object(A.N, '_write', side_effect=injected), self.assertRaises(KeyboardInterrupt) as caught:
            A.tick(observation(), self.directory, diagnose=Mock(side_effect=interruption))
        self.assertIs(caught.exception, interruption)
        execute = Mock()
        value = A.tick(observation(fingerprint='d' * 64), self.directory, diagnose=execute)
        self.assertEqual(value['result'], 'unknown')
        execute.assert_not_called()

    def test_cursor_failure_after_claim_never_dispatches_or_retries(self):
        save = A._save
        def injected(path, value):
            if path.name == 'cursor.json' and value['claimed_keys']:
                raise OSError('before dispatch')
            return save(path, value)
        execute = Mock()
        with patch.object(A, '_save', side_effect=injected), self.assertRaises(OSError):
            A.tick(observation(), self.directory, diagnose=execute)
        value = A.tick(observation(), self.directory, diagnose=execute)
        self.assertEqual(value['result'], 'unknown')
        execute.assert_not_called()

    def test_invalid_observation_rejected_before_allocation(self):
        invalid = []
        for key, value in [('schema_version', True), ('rule_uid', 'another-rule'), ('task_id', 'workflow-' + 'a' * 32),
                           ('state', 'pending'), ('fingerprint', 'x' * 64)]:
            invalid.append(dict(observation(), **{key: value}))
        invalid += [dict(observation('normal'), fingerprint=FINGERPRINT), dict(observation(), extra='raw')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(A.ActivationError):
                A.tick(value, self.directory, diagnose=Mock())
        self.assertFalse(self.directory.exists())

    def test_shared_directory_cannot_be_reused_for_another_task(self):
        A.tick(observation('normal'), self.directory, diagnose=Mock())
        value = dict(observation(), task_id='activation-' + 'f' * 32)
        with self.assertRaises(A.ActivationError):
            A.tick(value, self.directory, diagnose=Mock())

    def test_existing_lock_blocks_without_job(self):
        self.directory.mkdir(mode=0o700)
        fd = os.open(self.directory / 'sensor.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(A.ActivationError, 'ACTIVATION_BUSY'):
                A.tick(observation(), self.directory, diagnose=Mock())
            self.assertFalse((self.directory / 'cursor.json').exists())
        finally:
            os.close(fd)

    def test_forged_non_readonly_diagnostic_remains_unknown(self):
        value = diagnostic()
        value['diagnosis']['actions_executed'] = ['mutation']
        with self.assertRaises(A.ActivationError):
            A.tick(observation(), self.directory, diagnose=lambda _: value)
        retry = A.tick(observation(), self.directory, diagnose=Mock())
        self.assertEqual(retry['result'], 'unknown')


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.directory = self.base / 'claim'
        self.directory.mkdir(mode=0o700)
        self.saved = self.base / 'bears'
        self.saved.mkdir(mode=0o700)
        self.patch = patch.object(A, 'SRE_STATE', self.saved)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def factory(self, *, timeout=False, mismatched=False, cancelled=None):
        child = Mock(pid=54321)
        child.poll.return_value = None if timeout or cancelled else 0
        def wait(timeout=None):
            if cancelled:
                child.poll.return_value = 1
                raise cancelled
            if child.poll.return_value is None:
                child.poll.return_value = 1
                raise subprocess.TimeoutExpired('sre', 600)
            return child.poll.return_value
        child.wait.side_effect = wait
        def start(argv, **kwargs):
            self.assertEqual(argv, [str(A.PYTHON), '-B', str(A.SRE)])
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['umask'], 0o077)
            self.assertNotIn('HTTP_PROXY', kwargs['env'])
            self.assertNotIn('SHIROKUMA_GATEWAY_TASK_ID', kwargs['env'])
            self.assertEqual(kwargs['env']['HOME'], os.environ['HOME'])
            value = diagnostic()
            kwargs['stdout'].write(json.dumps(value).encode() + b'\n')
            directory = Path(value['evidence'])
            directory.mkdir(mode=0o700)
            saved = copy.deepcopy(value)
            if mismatched:
                saved['trace_id'] = 'f' * 32
            A.N._write(directory / 'result.json', saved)
            return child
        return child, start

    def test_fixed_process_with_private_logs_and_saved_result_match(self):
        child, start = self.factory()
        original = dict(os.environ)
        with patch.object(A.subprocess, 'Popen', side_effect=start) as popen, patch.object(A, '_group_empty', return_value=True):
            value = A.diagnose_once(self.directory)
        self.assertEqual(value['result'], 'passed')
        popen.assert_called_once()
        child.wait.assert_called_once_with(timeout=600)
        self.assertEqual(dict(os.environ), original)
        for path in self.directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        state = A._read(self.directory / 'sre-process.json')
        self.assertTrue(state['child_exited'] and state['cleanup_verified'])

    def test_missing_inherited_home_refused_without_files_or_process(self):
        with patch.dict(A.os.environ, {}, clear=True), patch.object(A.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(A.ActivationError, 'ACTIVATION_ENVIRONMENT_INVALID'):
                A.diagnose_once(self.directory)
        start.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_saved_result_mismatch_or_live_group_refuses_success(self):
        for mismatch, empty in [(True, True), (False, False)]:
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as raw:
                directory = Path(raw).resolve()
                child, start = self.factory(mismatched=mismatch)
                target = self.saved / ('c' * 32)
                if target.exists():
                    (target / 'result.json').unlink()
                    target.rmdir()
                with patch.object(A.subprocess, 'Popen', side_effect=start), patch.object(A, '_group_empty', return_value=empty):
                    with self.assertRaises(A.ActivationError):
                        A.diagnose_once(directory)

    def test_process_interruption_retained_after_status_save_failure(self):
        interruption = KeyboardInterrupt('first')
        child, start = self.factory(cancelled=interruption)
        with patch.object(A.subprocess, 'Popen', side_effect=start), patch.object(A, '_group_empty', return_value=True), \
                patch.object(A, '_save', side_effect=OSError('later status write')), self.assertRaises(KeyboardInterrupt) as caught:
            A.diagnose_once(self.directory)
        self.assertIs(caught.exception, interruption)

    def test_original_cancellation_survives_process_poll_failure(self):
        interruption = KeyboardInterrupt('original interruption')
        child = Mock(pid=54321)
        child.wait.side_effect = interruption
        child.poll.side_effect = OSError('process inspection failed later')
        with patch.object(A.subprocess, 'Popen', return_value=child), self.assertRaises(KeyboardInterrupt) as caught:
            A.diagnose_once(self.directory)
        self.assertIs(caught.exception, interruption)
        self.assertFalse(A._read(self.directory / 'sre-process.json')['cleanup_verified'])

    def test_graceful_interrupt_targets_only_owned_child_and_never_kills_group(self):
        child = Mock(pid=54321)
        child.poll.side_effect = [None, None, 1]
        original = KeyboardInterrupt('original')
        with patch.object(A.os, 'kill') as kill:
            result = A._interrupt(child, original)
        self.assertIs(result, original)
        kill.assert_called_once_with(54321, signal.SIGINT)
        child.wait.assert_called_once()


if __name__ == '__main__':
    unittest.main()
