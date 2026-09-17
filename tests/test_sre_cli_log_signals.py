"""Saved log-context compatibility through the real SRE status entry point."""
import asyncio
import copy
import fcntl
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_sre_cli import CLI, TOKEN, INNER, encode, fixture_documents, write_fixture
from test_local_sre_contract import usage_snapshots, usage_reread
from tools.local_experiment.gateway import pod_logs as L


def saved_log_snapshots(state='observed'):
    snapshots = usage_snapshots()
    pod = snapshots['k8s_read_pods']
    value = L.unknown('READ_FAILED', identity=copy.deepcopy(pod['resource_usage']['identity']), attempts=1,
                      started='2026-09-17T02:00:00.000000000Z',
                      observed='2026-09-17T02:00:01.000000000Z', since='2026-09-17T01:55:00Z')
    if state == 'observed':
        value.update(state='observed', reason=None, binding='name-time-correlated', get_attempts=2,
                     fragment=dict(returned_bytes=0, complete_lines=0, header_lines=0,
                                   warn_header_lines=0, error_header_lines=0, unparsed_lines=0,
                                   first_prefix_at=None, last_prefix_at=None))
    pod['log_signals'] = L.validate(value)
    return snapshots


def logs_documents(*, state='observed', followup='none'):
    """Saved-only fixtures shared with the offline Go/Python integration."""
    snapshots = saved_log_snapshots(state)
    after = None
    if followup != 'none':
        pod = snapshots['k8s_read_pods']['pods'][0]
        pod['ready_containers'] = 0
        pod['container_observation']['containers'][0]['ready'] = False
        after = usage_reread(snapshots)
        value = after['k8s_read_pods']['log_signals']
        value.update(request_started_at='2026-09-17T02:00:04.000000000Z',
                     observed_at='2026-09-17T02:00:05.000000000Z', since_time='2026-09-17T01:55:04Z')
        if followup == 'absent':
            after['k8s_read_pods'].pop('log_signals')
        elif followup == 'unknown':
            value.update(state='unknown', reason='READ_FAILED', binding=None, fragment=None, get_attempts=1)
        elif followup != 'same':
            raise ValueError('unsupported fixture')
    return fixture_documents(initial_snapshots=snapshots, followup_snapshots=after)


def maximum_log_projection():
    """Conservative maximum legal field lengths, not a physical log-volume claim."""
    initial = saved_log_snapshots()['k8s_read_pods']['log_signals']
    initial['identity'].update(pod_name='.'.join(['a' * 63] * 3 + ['a' * 61]),
        pod_uid='ffffffff-ffff-ffff-ffff-ffffffffffff', container_id_sha256='f' * 64,
        restart_count=2147483647, started_at='2026-09-17T01:00:00.999999Z')
    initial.update(request_started_at='2026-09-17T02:00:00.999999999Z',
        observed_at='2026-09-17T02:00:20.999999999Z', since_time='2026-09-17T01:55:00Z')
    initial['fragment'].update(returned_bytes=32767, complete_lines=4096, header_lines=2048,
        warn_header_lines=1024, error_header_lines=1024, unparsed_lines=2048,
        first_prefix_at='2026-09-17T01:55:00.000000000Z', last_prefix_at='2026-09-17T02:00:25.999999999Z')
    followup = copy.deepcopy(initial)
    followup['identity']['pod_uid'] = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
    initial, followup = L.validate(initial), L.validate(followup)
    comparison = CLI.C._compare_log_signals(initial, followup, reobserved=True)
    return CLI.projection('status', TOKEN, result='passed', execution='read-only',
        verification='saved-evidence', workflow_token=INNER, assessment='healthy-observed-scope',
        has_evidence=True, logs=True,
        log_signals=CLI.log_projection(initial, followup, comparison, verified=True))


class SRESavedLogSignalsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name, value in [('STATE', self.root / 'intents'), ('EVIDENCE', self.root / 'evidence')]:
            patcher = patch.object(CLI, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('runner', 'configured_runner', 'persist'):
            patcher = patch.object(CLI, name, side_effect=AssertionError('saved status attempted a side effect'))
            patcher.start()
            self.addCleanup(patcher.stop)
        CLI.EVIDENCE.mkdir(mode=0o700)

    def fingerprint(self):
        result = {}
        for path in [self.root, *sorted(self.root.rglob('*'))]:
            s = path.lstat()
            result[str(path.relative_to(self.root))] = (s.st_mode, s.st_mtime_ns, s.st_ctime_ns, s.st_ino,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
        return result

    def inspect(self, *, usage=False):
        before = self.fingerprint()
        output = io.StringIO()
        code = CLI.main(['status', '--evidence-token', TOKEN] + (['--usage'] if usage else []), stdout=output)
        self.assertEqual(self.fingerprint(), before)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['schema_version'], 2 if usage else 1)
        self.assertEqual(len(rows[-1]), 14 if usage else 13)
        self.assertNotIn('log_signals', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())
        return code, rows[-1]

    def inspect_logs(self):
        before = self.fingerprint()
        output = io.StringIO()
        code = CLI.main(['status', '--evidence-token', TOKEN, '--logs'], stdout=output)
        self.assertEqual(self.fingerprint(), before)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(line.encode()) + 1 <= CLI.MAX_PUBLIC_BYTES for line in lines))
        allocated, result = map(json.loads, lines)
        self.assertEqual((allocated['schema_version'], allocated['event']), (1, 'allocated'))
        self.assertEqual((result['schema_version'], set(result)), (3, CLI.FINAL_KEYS | {'log_signals'}))
        self.assertNotIn('PRIVATE', output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        return code, result

    def journal(self):
        CLI.STATE.mkdir(mode=0o700)
        directory = CLI.STATE / TOKEN
        directory.mkdir(mode=0o700)
        value = dict(schema_version=1, evidence_token=TOKEN, case=CLI.CASE,
                     invocations=1, state='returned', files=CLI.snapshot(CLI.EVIDENCE / TOKEN)[1])
        for name, raw in [('intent.json', CLI.canonical(value).encode()), ('run.lock', b'')]:
            path = directory / name
            path.write_bytes(raw)
            path.chmod(0o600)
        return directory

    def test_saved_empty_fragment_is_context_and_keeps_public_usage_protocol(self):
        snapshots = saved_log_snapshots()
        documents = fixture_documents(initial_snapshots=snapshots)
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        code, plain = self.inspect()
        self.assertEqual((code, plain['result']), (0, 'passed'))
        code, view = self.inspect(usage=True)
        self.assertEqual((code, view['result']), (0, 'passed'))
        self.assertEqual(view['resource_usage']['initial'], snapshots['k8s_read_pods']['resource_usage'])
        self.assertEqual(self.inspect()[1], plain)
        without = copy.deepcopy(snapshots)
        without['k8s_read_pods'].pop('log_signals')
        old = fixture_documents(initial_snapshots=without)['result.json']['evidence']
        self.assertEqual(documents['result.json']['evidence']['health'], old['health'])
        self.assertEqual(documents['result.json']['evidence']['findings'], old['findings'])

    def test_saved_unknown_logs_keep_null_fragment_without_reobservation(self):
        snapshots = saved_log_snapshots('unknown')
        documents = fixture_documents(initial_snapshots=snapshots)
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        self.assertIsNone(documents['result.json']['evidence']['observations']['pods']['log_signals']['fragment'])
        self.assertEqual(self.inspect(usage=True)[0], 0)

    def test_followup_reconstructs_requested_overlap_without_count_deltas(self):
        snapshots = saved_log_snapshots()
        pod = snapshots['k8s_read_pods']['pods'][0]
        pod['ready_containers'] = 0
        pod['container_observation']['containers'][0]['ready'] = False
        followup = usage_reread(snapshots)
        followup['k8s_read_pods']['log_signals'].update(
            request_started_at='2026-09-17T02:00:04.000000000Z',
            observed_at='2026-09-17T02:00:05.000000000Z', since_time='2026-09-17T01:55:04Z')
        documents = fixture_documents(initial_snapshots=snapshots, followup_snapshots=followup)
        comparison = documents['result.json']['comparisons']['sources']['k8s_read_pods']['log_signals']
        self.assertEqual(comparison['identity'], 'same')
        self.assertTrue(comparison['requested_window_overlap'])
        self.assertEqual(set(comparison), {'before_state', 'after_state', 'identity', 'requested_window_overlap', 'limitations'})
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        self.assertEqual(self.inspect(usage=True)[0], 0)

    def test_saved_log_tampering_cannot_escape_status_validation(self):
        for mode in ('raw-field', 'bool-count', 'valid-clock-hash-drift'):
            with self.subTest(mode=mode):
                snapshots = saved_log_snapshots()
                documents = fixture_documents(initial_snapshots=snapshots)
                value = documents['observations.json'][1]['snapshot']['log_signals']
                if mode == 'raw-field':
                    value['raw'] = 'PRIVATE_SQL_AND_CREDENTIALS'
                elif mode == 'bool-count':
                    value['fragment']['complete_lines'] = False
                else:
                    value['observed_at'] = '2026-09-17T02:00:02.000000000Z'
                # Each case uses a new disposable saved directory.
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    with patch.object(CLI, 'EVIDENCE', Path(temporary)):
                        write_fixture(CLI.EVIDENCE / TOKEN, documents)
                        code, view = self.inspect(usage=True)
                self.assertEqual((code, view['result'], view['error_code']), (1, 'unknown', 'SRE_EVIDENCE_INVALID'))
                self.assertEqual(view['resource_usage']['availability'], 'unverified')

    def test_pre_log_receipt_keeps_its_saved_shape(self):
        documents = fixture_documents(initial_snapshots=usage_snapshots())
        self.assertNotIn('log_signals', documents['result.json']['evidence']['observations']['pods'])
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        self.assertEqual(self.inspect()[0], 0)
        self.assertEqual(self.inspect(usage=True)[0], 0)

    def test_logs_available_empty_and_unknown_remain_distinct(self):
        for state in ('observed', 'unknown'):
            with self.subTest(state=state), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                with patch.object(CLI, 'EVIDENCE', Path(temporary)):
                    write_fixture(CLI.EVIDENCE / TOKEN, logs_documents(state=state))
                    code, result = self.inspect_logs()
                    self.assertEqual((code, result['result']), (0, 'passed'))
                    view = result['log_signals']
                    self.assertEqual(view['availability'], 'available')
                    self.assertEqual(view['initial']['state'], state)
                    self.assertIsNone(view['followup'])
                    self.assertEqual(view['comparison']['after_state'], 'not-reobserved')
                    if state == 'observed': self.assertEqual(view['initial']['fragment']['returned_bytes'], 0)
                    else: self.assertIsNone(view['initial']['fragment'])

    def test_logs_followup_comparison_distinguishes_observed_unknown_and_absent(self):
        for mode, expected in [('same', 'observed'), ('unknown', 'unknown'), ('absent', 'not-observed')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                with patch.object(CLI, 'EVIDENCE', Path(temporary)):
                    write_fixture(CLI.EVIDENCE / TOKEN, logs_documents(followup=mode))
                    code, result = self.inspect_logs()
                view = result['log_signals']
                self.assertEqual(code, 0)
                self.assertEqual(view['comparison']['after_state'], expected)
                self.assertEqual(view['comparison'], CLI.C._compare_log_signals(
                    view['initial'], view['followup'], reobserved=True))
                self.assertEqual(view['comparison']['identity'], 'same' if mode == 'same' else 'unavailable')
                self.assertIs(view['comparison']['requested_window_overlap'], True if mode == 'same' else None)

    def test_logs_legacy_absence_keeps_both_existing_serializations(self):
        write_fixture(CLI.EVIDENCE / TOKEN, fixture_documents(initial_snapshots=usage_snapshots()))
        plain, usage = self.inspect()[1], self.inspect(usage=True)[1]
        code, result = self.inspect_logs()
        self.assertEqual((code, result['result']), (0, 'passed'))
        self.assertEqual(result['log_signals'], CLI.log_projection(verified=True))
        self.assertEqual(self.inspect()[1], plain)
        self.assertEqual(self.inspect(usage=True)[1], usage)

    def test_logs_failed_incomplete_and_missing_release_no_partial_payload(self):
        code, result = self.inspect_logs()
        self.assertEqual((code, result['result']), (1, 'not_found'))
        self.assertEqual(result['log_signals'], CLI.log_projection())
        for mode in ('failed', 'incomplete'):
            documents = logs_documents(followup='same')
            if mode == 'failed':
                result = documents['result.json']
                result.update(result='failed', error_code='SRE_OBSERVATION_INFERENCE_OR_AUDIT_FAILED')
                result.pop('checkpoint')
                result['audit_records'][-1]['status'] = 'failed'
                documents['spans.jsonl'][-1]['status'] = 'ERROR'
            else: documents.pop('observations.json')
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                with patch.object(CLI, 'EVIDENCE', Path(temporary)):
                    write_fixture(CLI.EVIDENCE / TOKEN, documents)
                    code, result = self.inspect_logs()
                self.assertEqual((code, result['result']), (0, 'failed' if mode == 'failed' else 'unknown'))
                self.assertEqual(result['log_signals'], CLI.log_projection())

    def test_logs_one_validation_under_lock_and_busy_skips_payload(self):
        write_fixture(CLI.EVIDENCE / TOKEN, logs_documents(followup='same'))
        directory = self.journal()
        original = CLI.verify_documents
        def verify(raw, *, usage=False, logs=False):
            self.assertFalse(usage)
            self.assertTrue(logs)
            with (directory / 'run.lock').open('rb') as stream:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return original(raw, logs=logs)
        with patch.object(CLI, 'verify_documents', side_effect=verify) as verify_mock, \
             patch.object(CLI, 'snapshot', wraps=CLI.snapshot) as snapshot_mock:
            self.assertEqual(self.inspect_logs()[0], 0)
        self.assertEqual((verify_mock.call_count, snapshot_mock.call_count), (1, 2))
        with (directory / 'run.lock').open('rb') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(CLI, 'snapshot', side_effect=AssertionError('busy payload read')):
                code, result = self.inspect_logs()
        self.assertEqual((code, result['result']), (0, 'busy'))
        self.assertEqual(result['log_signals'], CLI.log_projection())

    def test_logs_changed_second_snapshot_discards_verified_projection(self):
        write_fixture(CLI.EVIDENCE / TOKEN, logs_documents(followup='same'))
        original = CLI.snapshot
        calls = 0
        def changed(directory):
            nonlocal calls
            calls += 1
            raw, hashes = original(directory)
            if calls == 2: hashes['observations.json'] = '0' * 64
            return raw, hashes
        with patch.object(CLI, 'snapshot', side_effect=changed):
            code, result = self.inspect_logs()
        self.assertEqual((code, result['result'], result['error_code']), (1, 'unknown', 'SRE_EVIDENCE_INVALID'))
        self.assertEqual(result['log_signals'], CLI.log_projection())

    def test_logs_public_types_bounds_raw_and_comparison_tampering_are_rejected(self):
        view = CLI.verify_documents(encode(logs_documents(followup='same')), logs=True)
        value = CLI.projection('status', TOKEN, execution='read-only', has_evidence=True, logs=True, **view)
        changes = [lambda v: v['log_signals'].update(raw='PRIVATE_LOG_BODY'),
            lambda v: v['log_signals']['initial']['fragment'].update(complete_lines=False),
            lambda v: v['log_signals']['initial']['fragment'].update(returned_bytes=32768),
            lambda v: v['log_signals']['initial']['fragment'].update(header_lines=4097),
            lambda v: v['log_signals']['initial'].update(get_attempts=True),
            lambda v: v['log_signals']['comparison'].update(requested_window_overlap=False),
            lambda v: v['log_signals']['comparison'].update(identity='different'),
            lambda v: v['log_signals'].update(availability='legacy-absent'),
            lambda v: v.update(result='failed'),
            lambda v: v.update(schema_version=2),
            lambda v: v.update(command='run', execution='invoked')]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                damaged = copy.deepcopy(value)
                change(damaged)
                with self.assertRaises(ValueError): CLI.validate_projection(damaged)
        documents = logs_documents(followup='same')
        documents['result.json']['comparisons']['sources']['k8s_read_pods']['log_signals']['identity'] = 'different'
        write_fixture(CLI.EVIDENCE / TOKEN, documents)
        code, result = self.inspect_logs()
        self.assertEqual((code, result['result']), (1, 'unknown'))
        self.assertEqual(result['log_signals'], CLI.log_projection())

    def test_logs_maximum_legal_envelope_fits_existing_frame_cap(self):
        value = maximum_log_projection()
        stream = io.StringIO()
        CLI.emit(stream, value)
        self.assertEqual(len(stream.getvalue().encode()), 3266)
        self.assertLessEqual(len(stream.getvalue().encode()), CLI.MAX_PUBLIC_BYTES)
        self.assertEqual(CLI.validate_projection(json.loads(stream.getvalue())), value)

    def test_logs_frame_overflow_falls_back_to_unverified_without_body(self):
        value = maximum_log_projection()
        value['log_signals']['initial']['identity']['pod_name'] = 'PRIVATE' * 1000
        with patch.object(CLI, 'inspect_sre', return_value=(value, 0)):
            code, result = self.inspect_logs()
        self.assertEqual((code, result['result'], result['error_code']), (1, 'unknown', 'SRE_EVIDENCE_INVALID'))
        self.assertEqual(result['log_signals'], CLI.log_projection())

    def test_logs_flags_fail_before_allocation_and_invalid_token_retains_selected_schema(self):
        for args in (['run', '--evidence-token', TOKEN, '--logs'],
                     ['status', '--evidence-token', TOKEN, '--usage', '--logs'],
                     ['status', '--evidence-token', TOKEN, '--logs=false']):
            with self.subTest(args=args):
                output = io.StringIO()
                self.assertEqual(CLI.main(args, stdout=output), 1)
                rows = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual((len(rows), rows[0]['schema_version'], rows[0]['error_code']), (1, 1, 'ARGUMENTS_INVALID'))
        output = io.StringIO()
        self.assertEqual(CLI.main(['status', '--evidence-token', '../invalid', '--logs'], stdout=output), 1)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual((len(rows), rows[0]['schema_version'], rows[0]['error_code']), (1, 3, 'EVIDENCE_TOKEN_INVALID'))
        self.assertEqual(rows[0]['log_signals'], CLI.log_projection())
        self.assertFalse(CLI.STATE.exists())

    def test_logs_cancellation_and_secondary_output_failure_preserve_first_object(self):
        cancel = asyncio.CancelledError()
        with patch.object(CLI, 'inspect_sre', side_effect=cancel):
            code, result = self.inspect_logs()
        self.assertEqual((code, result['error_code']), (130, 'INTERRUPTED'))
        self.assertEqual(result['log_signals'], CLI.log_projection())
        stream = Mock()
        stream.write.side_effect = [None, OSError('PRIVATE')]
        with patch.object(CLI, 'inspect_sre', side_effect=cancel), self.assertRaises(asyncio.CancelledError) as caught:
            CLI.main(['status', '--evidence-token', TOKEN, '--logs'], stdout=stream)
        self.assertIs(caught.exception, cancel)


if __name__ == '__main__':
    unittest.main()
