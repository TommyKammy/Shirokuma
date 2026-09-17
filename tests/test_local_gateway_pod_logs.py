"""Synthetic console text and fake processes only; no real logs or API calls."""
from __future__ import annotations

import copy
import json
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from tests.test_local_gateway_pod_usage import G, pod as usage_pod, metrics, REQUEST, OBSERVED

L = G.POD_LOGS
U = L.U
SINCE = '2026-09-18T01:54:59Z'
CANARY = 'PRIVATE_SQL_TOKEN_CANARY'


def pod():
    return usage_pod()


def log_bytes(level='WARN', *, prefix='2026-09-18T01:59:55.123456789Z',
              app='2026-09-18T01:59:55.123Z', message=CANARY):
    return f'{prefix} {app}\t{level}\tprivate-thread\tprivate.logger\t{message}\n'.encode()


def observed():
    return L.project(pod(), log_bytes(), pod(), request_started_at=REQUEST,
                     observed_at=OBSERVED, since_time=SINCE)


def unknown():
    return L.unknown('READ_FAILED')


class PodLogProjectionTests(unittest.TestCase):
    def project(self, raw=None, before=None, after=None, **times):
        return L.project(before or pod(), log_bytes() if raw is None else raw, after or pod(),
                         request_started_at=times.get('request', REQUEST),
                         observed_at=times.get('end', OBSERVED), since_time=times.get('since', SINCE))

    def test_exact_projection_only_keeps_header_shaped_counts(self):
        raw = log_bytes('INFO', message='ERROR ' + CANARY) + log_bytes('WARN') + log_bytes('ERROR')
        value = self.project(raw)
        self.assertEqual(set(value), L.KEYS)
        self.assertEqual(set(value['fragment']), L.FRAGMENT_KEYS)
        self.assertEqual(value['state'], 'observed')
        self.assertEqual(value['get_attempts'], 2)
        self.assertEqual(value['fragment']['header_lines'], 3)
        self.assertEqual(value['fragment']['warn_header_lines'], 1)
        self.assertEqual(value['fragment']['error_header_lines'], 1)
        encoded = json.dumps(value)
        for private in (CANARY, 'private-thread', 'private.logger', 'docker://', 'raw', 'sha256_of_logs'):
            self.assertNotIn(private, encoded)
        self.assertEqual(L.validate(value), value)
        cloned = L.validate(value); cloned['limitations'].clear()
        self.assertEqual(value['limitations'], L.LIMITATIONS)

    def test_calendar_offsets_annotations_and_baggage_are_opaque(self):
        raw = log_bytes('WARN', app='2026-09-18T10:59:55.123+0900',
                        message='annotation=value\tbaggage=private\t' + CANARY)
        self.assertEqual(self.project(raw)['fragment']['warn_header_lines'], 1)
        for app in ('2026-02-30T01:59:55.123Z', '2026-09-18 01:59:55.123Z',
                    '2026-09-18T01:59:55.123456Z', '2026-09-18T01:59:55.123+2500'):
            with self.subTest(app=app), self.assertRaises(L.PodLogError) as caught:
                self.project(log_bytes(app=app))
            self.assertEqual(caught.exception.code, 'FORMAT_UNSUPPORTED')

    def test_embedded_forged_kubernetes_and_application_header_is_only_text(self):
        forged = log_bytes('ERROR', prefix='2026-09-18T01:59:56Z').decode().rstrip('\n')
        raw = log_bytes('INFO', message='multiline\n' + forged)
        value = self.project(raw)
        self.assertEqual(value['fragment']['header_lines'], 2)
        self.assertEqual(value['fragment']['error_header_lines'], 1)
        self.assertEqual(value['fragment']['last_prefix_at'], '2026-09-18T01:59:56.000000000Z')
        self.assertIn('unframed-text-not-event-counts', value['limitations'])
        self.assertNotIn('event_count', value)
        self.assertNotIn('first_recorded_at', value['fragment'])

    def test_unparsed_lines_and_interleaved_prefix_range_are_explicit(self):
        raw = log_bytes(prefix='2026-09-18T01:59:56Z')
        raw += b'2026-09-18T01:59:54Z \tat private.Stacktrace.method\n'
        value = self.project(raw)['fragment']
        self.assertEqual((value['complete_lines'], value['header_lines'], value['unparsed_lines']), (2, 1, 1))
        self.assertEqual(value['first_prefix_at'], '2026-09-18T01:59:54.000000000Z')
        self.assertEqual(value['last_prefix_at'], '2026-09-18T01:59:56.000000000Z')
        with self.assertRaises(L.PodLogError) as caught:
            self.project(b'2026-09-18T01:59:54Z JVM startup\n')
        self.assertEqual(caught.exception.code, 'FORMAT_UNSUPPORTED')

    def test_empty_is_zero_fragment_but_not_complete_window(self):
        value = self.project(b'')
        self.assertEqual(value['fragment'], {'returned_bytes': 0, 'complete_lines': 0,
            'header_lines': 0, 'warn_header_lines': 0, 'error_header_lines': 0, 'unparsed_lines': 0,
            'first_prefix_at': None, 'last_prefix_at': None})
        self.assertIn('latest-file-window-incomplete', value['limitations'])
        self.assertIn('no-negative-health-or-cause-claim', value['limitations'])

    def test_byte_cap_and_framing_fail_without_returning_raw(self):
        prefix = log_bytes(message='')[:-1]
        below = prefix + b'x' * (L.MAX_BYTES - 2 - len(prefix)) + b'\n'
        self.assertEqual(len(below), L.MAX_BYTES - 1)
        self.assertEqual(self.project(below)['fragment']['returned_bytes'], L.MAX_BYTES - 1)
        for raw, reason in [(below + b'\n', 'READ_LIMIT'), (below + b'xx', 'READ_LIMIT'),
                            (log_bytes()[:-1], 'LOG_INVALID'), (log_bytes(message='\x1b[31m' + CANARY), 'LOG_INVALID'),
                            (log_bytes(message='\x00' + CANARY), 'LOG_INVALID'),
                            (log_bytes()[:-1] + b'\xff\n', 'LOG_INVALID'),
                            (b'partial ' + log_bytes(), 'TIME_INVALID')]:
            with self.subTest(reason=reason), self.assertRaises(L.PodLogError) as caught:
                self.project(raw)
            self.assertEqual(caught.exception.code, reason)
            self.assertNotIn(CANARY, str(caught.exception))

    def test_exact_since_window_future_and_collection_bounds(self):
        self.assertEqual(L.since_time('2026-09-18T01:59:59.999999999Z'), SINCE)
        for prefix, valid in [('2026-09-18T01:54:59Z', True), ('2026-09-18T01:54:58.999999999Z', False),
                              ('2026-09-18T02:00:05Z', True), ('2026-09-18T02:00:05.000000001Z', False)]:
            with self.subTest(prefix=prefix):
                if valid: self.project(log_bytes(prefix=prefix))
                else:
                    with self.assertRaises(L.PodLogError): self.project(log_bytes(prefix=prefix))
        self.project(end='2026-09-18T02:00:19Z')
        for times in ({'end': '2026-09-18T02:00:19.000000001Z'}, {'end': '2026-09-18T01:59:58Z'},
                      {'since': '2026-09-18T01:54:58Z'}, {'since': SINCE[:-1] + '.000000001Z'}):
            with self.subTest(times=times), self.assertRaises(L.PodLogError): self.project(**times)

    def test_identity_raw_nanoseconds_and_creation_are_bound(self):
        edits = [lambda p: p['metadata'].update(uid='22222222-2222-4222-8222-222222222222'),
                 lambda p: p['metadata'].update(creationTimestamp='2026-09-18T00:00:00.000000001Z'),
                 lambda p: p['status']['containerStatuses'][0].update(restartCount=2),
                 lambda p: p['status']['containerStatuses'][0].update(containerID='docker://' + 'b' * 64),
                 lambda p: p['status']['containerStatuses'][0]['state']['running'].update(startedAt='2026-09-18T01:00:00.000000001Z')]
        for edit in edits:
            after = pod(); edit(after)
            with self.assertRaises(L.PodLogError) as caught: self.project(after=after)
            self.assertEqual(caught.exception.code, 'IDENTITY_CHANGED')
        before = pod(); before['status']['containerStatuses'][0]['state']['running']['startedAt'] = '2026-09-18T01:59:55.123456Z'
        with self.assertRaises(L.PodLogError): self.project(before=before, after=copy.deepcopy(before))

    def test_validator_rejects_tampered_fields_without_fabricated_zero(self):
        edits = [lambda v: v.update(schema_version=True), lambda v: v.update(get_attempts=1),
                 lambda v: v.update(scope='other'), lambda v: v.update(body=CANARY),
                 lambda v: v['fragment'].update(warn_header_lines=True),
                 lambda v: v['fragment'].update(error_header_lines=1),
                 lambda v: v['fragment'].update(complete_lines=2),
                 lambda v: v['fragment'].update(returned_bytes=L.MAX_BYTES),
                 lambda v: v['fragment'].update(first_prefix_at=None),
                 lambda v: v['fragment'].update(last_prefix_at='2026-09-18T02:00:06Z'),
                 lambda v: v.update(limitations=list(reversed(L.LIMITATIONS))),
                 lambda v: v.update(state='unknown'), lambda v: v.update(reason='READ_FAILED')]
        for edit in edits:
            v = observed(); edit(v)
            with self.assertRaises(L.PodLogError): L.validate(v)
        self.assertEqual(L.validate(unknown()), unknown())
        for reason in (None, CANARY, [], 1):
            v = unknown(); v['reason'] = reason
            with self.assertRaises(L.PodLogError): L.validate(v)


class PodLogCollectionTests(unittest.TestCase):
    def collect(self, fetch=None, items=None, now=None, limit=50, metadata=None):
        return L.collect({'items': [pod()] if items is None else items, 'metadata': metadata or {}},
                         limit=limit, fetch=fetch or Mock(side_effect=[log_bytes(), pod()]),
                         now=now or Mock(side_effect=[REQUEST, OBSERVED]))

    def test_two_intents_and_exact_fixed_since_on_success(self):
        fetch = Mock(side_effect=[log_bytes(), pod()]); value = self.collect(fetch)
        self.assertEqual(value, observed())
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[0].args, ('logs', pod()['metadata']['name']))
        self.assertEqual(fetch.call_args_list[0].kwargs, {'since_time': SINCE})
        self.assertEqual(fetch.call_args_list[1].args, ('pod', pod()['metadata']['name']))
        self.assertEqual(fetch.call_args_list[1].kwargs, {})

    def test_failed_acquisition_has_no_followup_or_retry(self):
        for error, reason in [(L.PodLogError('READ_TIMEOUT'), 'READ_TIMEOUT'),
                              (L.PodLogError('READ_LIMIT'), 'READ_LIMIT'), (OSError(CANARY), 'READ_FAILED')]:
            fetch = Mock(side_effect=error); value = self.collect(fetch)
            self.assertEqual((value['state'], value['reason'], value['get_attempts']), ('unknown', reason, 1))
            self.assertIsNone(value['fragment']); self.assertIsNone(value['binding'])
            self.assertEqual(fetch.call_count, 1)
            self.assertNotIn(CANARY, json.dumps(value))

    def test_raw_success_requires_after_even_for_empty_and_parse_failure(self):
        for raw, state in [(b'', 'observed'), (log_bytes()[:-1], 'unknown')]:
            fetch = Mock(side_effect=[raw, pod()]); value = self.collect(fetch)
            self.assertEqual(value['state'], state); self.assertEqual(fetch.call_count, 2)
        fetch = Mock(side_effect=[log_bytes(), OSError(CANARY)])
        value = self.collect(fetch)
        self.assertEqual(value['reason'], 'READ_FAILED'); self.assertIsNone(value['fragment'])
        self.assertEqual(value['get_attempts'], 2)

    def test_first_cancel_and_unconfirmed_cleanup_propagate(self):
        for error in (KeyboardInterrupt(), SystemExit(143), L.PodLogCleanupError()):
            fetch = Mock(side_effect=error); now = Mock(side_effect=[REQUEST])
            with self.assertRaises(type(error)) as caught: self.collect(fetch, now=now)
            self.assertIs(caught.exception, error)
            self.assertEqual(fetch.call_count, 1); self.assertEqual(now.call_count, 1)
        original = KeyboardInterrupt()
        fetch = Mock(side_effect=[log_bytes(), original])
        with self.assertRaises(KeyboardInterrupt) as caught: self.collect(fetch)
        self.assertIs(caught.exception, original); self.assertEqual(fetch.call_count, 2)

    def test_invalid_or_incomplete_target_does_not_acquire(self):
        bad = pod(); bad['status']['phase'] = 'Pending'
        for items, metadata, limit, reason in [([], {}, 50, 'TARGET_NOT_FOUND'),
                ([pod(), pod()], {}, 50, 'TARGET_AMBIGUOUS'), ([pod()], {'continue': 'opaque'}, 50, 'POD_LIST_INCOMPLETE'),
                ([pod()], {}, 0, 'POD_LIST_INCOMPLETE'), ([bad], {}, 50, 'POD_NOT_RUNNING')]:
            fetch = Mock(side_effect=AssertionError('unexpected acquisition'))
            value = self.collect(fetch, items=items, metadata=metadata, limit=limit)
            self.assertEqual(value['reason'], reason); self.assertEqual(value['get_attempts'], 0)
            fetch.assert_not_called()
        sidecar = pod(); sidecar['spec']['containers'].append({'name': 'other'})
        self.assertEqual(self.collect(items=[sidecar])['reason'], 'CONTAINER_UNSUPPORTED')

    def test_bad_or_reversed_clock_remains_unknown(self):
        for clocks in ([REQUEST, 'bad'], [REQUEST, '2026-09-18T01:59:58Z']):
            value = self.collect(now=Mock(side_effect=clocks))
            self.assertEqual(value['state'], 'unknown')
            self.assertIsNone(value['request_started_at']); self.assertIsNone(value['observed_at'])
            self.assertIsNone(value['since_time']); self.assertIsNone(value['fragment'])

    def test_core_optional_projection_keeps_pod_state_and_separate_scopes(self):
        backend = G.Backends.__new__(G.Backends)
        backend.command_json = Mock(return_value={'items': [pod()]})
        backend.pod_usage_get = Mock(side_effect=[metrics(), pod()])
        backend.pod_logs_get = Mock(side_effect=[log_bytes(), pod()])
        clocks = ['2026-09-18T01:59:58Z', REQUEST, OBSERVED, '2026-09-18T02:00:01Z', '2026-09-18T02:00:02Z']
        with patch.object(G, 'observation_time', side_effect=clocks):
            value = backend.k8s_read_pods(U.NAMESPACE, 50)
        self.assertEqual(value['observed_at'], clocks[0])
        self.assertEqual(value['resource_usage']['state'], 'observed')
        self.assertEqual(value['log_signals']['state'], 'observed')
        self.assertEqual(value['log_signals']['request_started_at'], '2026-09-18T02:00:01.000000000Z')
        self.assertEqual(value['pods'][0]['container_observation'],
                         G.CONTAINER_STATUS.project_container_observation(pod(), timestamp=G.safe_timestamp))
        self.assertNotIn(CANARY, json.dumps(value))


class PodLogTransportTests(unittest.TestCase):
    def process(self, chunks, *, error=None):
        process = Mock(); process.stdout.fileno.return_value = 99
        process.poll.return_value = None; process.pid = 9876; process.wait.return_value = 0
        selector = Mock(); selector.select.return_value = [True]
        patches = [patch.object(U.subprocess, 'Popen', return_value=process),
                   patch.object(U.selectors, 'DefaultSelector', return_value=selector),
                   patch.object(U.os, 'set_blocking'), patch.object(U.os, 'read', side_effect=error or chunks),
                   patch.object(U.os, 'killpg'), patch.object(U.time, 'monotonic', return_value=0)]
        mocks = [item.start() for item in patches]
        for item in reversed(patches): self.addCleanup(item.stop)
        return process, selector, mocks

    def test_actual_shared_byte_helper_returns_no_json_and_discards_stderr(self):
        raw = log_bytes(); process, selector, mocks = self.process([raw, b''])
        value = U._command_bytes(['get', '--raw', 'fixed'], deadline=8, environment={'PATH': '/fake'}, max_bytes=L.MAX_BYTES)
        self.assertEqual(value, raw)
        options = mocks[0].call_args.kwargs
        self.assertEqual(options['stderr'], subprocess.DEVNULL)
        self.assertTrue(options['start_new_session'])
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        selector.close.assert_called_once(); process.stdout.close.assert_called_once()
        mocks[4].assert_called_once_with(process.pid, signal.SIGKILL)

    def test_pipe_limit_timeout_failure_and_original_cancel(self):
        scenarios = [('limit', [b'x' * 4096] * 9, None), ('timeout', [], None),
                     ('cancel', [], KeyboardInterrupt())]
        for kind, chunks, error in scenarios:
            with self.subTest(kind=kind):
                process, selector, mocks = self.process(chunks, error=error)
                if kind == 'timeout': selector.select.return_value = []
                if kind == 'cancel':
                    mocks[4].side_effect = OSError(CANARY); process.stdout.close.side_effect = OSError(CANARY)
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        U._command_bytes([], deadline=8, environment={}, max_bytes=L.MAX_BYTES)
                    self.assertIs(caught.exception, error)
                else:
                    with self.assertRaises(U.PodUsageError) as caught:
                        U._command_bytes([], deadline=8, environment={}, max_bytes=L.MAX_BYTES)
                    self.assertEqual(caught.exception.code, 'READ_LIMIT' if kind == 'limit' else 'READ_TIMEOUT')
                process.wait.assert_called(); selector.close.assert_called_once(); process.stdout.close.assert_called_once()
                self.doCleanups()

    def test_fixed_logs_path_and_deadline_reuse_local_guard(self):
        config = {'clusters': [{'cluster': {'server': 'https://127.0.0.1:6443'}}]}
        with patch.object(U, '_command', side_effect=[config, log_bytes()]) as command, \
                patch.object(L.time, 'monotonic', return_value=100):
            raw = L.fixed_get('logs', pod()['metadata']['name'], since_time=SINCE)
        self.assertEqual(raw, log_bytes()); self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args_list[0].args[0], ['config', 'view', '--minify', '-o', 'json'])
        args = command.call_args_list[1].args[0]; path = urlsplit(args[-1])
        self.assertEqual(args[:5], ['--server', 'https://127.0.0.1:6443', '--request-timeout=6s', 'get', '--raw'])
        self.assertEqual(path.path, '/api/v1/namespaces/shirokuma-dev/pods/' + pod()['metadata']['name'] + '/log')
        self.assertEqual(parse_qs(path.query), {'container': ['trino'], 'follow': ['false'], 'previous': ['false'],
            'timestamps': ['true'], 'sinceTime': [SINCE], 'limitBytes': ['32768']})
        self.assertTrue(all(call.kwargs['deadline'] == 108 for call in command.call_args_list))
        self.assertEqual(command.call_args.kwargs['_max_bytes'], 32768)
        self.assertTrue(command.call_args.kwargs['_raw'])

    def test_nonlocal_injected_or_unscoped_paths_never_send(self):
        with patch.object(U, '_command', side_effect=AssertionError('must not start')) as command:
            for operation, name, since in [('metrics', 'safe', SINCE), ('logs', '../unsafe', SINCE),
                                          ('logs', 'safe', 'not-time'), ('pod', 'safe', SINCE)]:
                with self.assertRaises(L.PodLogError): L.fixed_get(operation, name, since_time=since)
            command.assert_not_called()
        config = {'clusters': [{'cluster': {'server': 'https://external.invalid'}}]}
        with patch.object(U, '_command', return_value=config) as command:
            with self.assertRaises(L.PodLogError): L.fixed_get('logs', 'safe', since_time=SINCE)
            self.assertEqual(command.call_count, 1)

    def test_exact_cap_and_cleanup_failure_remain_fatal_or_unknown(self):
        with patch.object(U, '_local_server', return_value='https://localhost:6443'), \
                patch.object(U, '_command_bytes', return_value=b'x' * L.MAX_BYTES):
            with self.assertRaises(L.PodLogError) as caught: L.fixed_get('logs', 'safe', since_time=SINCE)
        self.assertEqual(caught.exception.code, 'READ_LIMIT')
        process, selector, mocks = self.process([], error=OSError(CANARY))
        process.wait.side_effect = subprocess.TimeoutExpired('private', 2)
        with self.assertRaises(L.PodLogCleanupError):
            U._command_bytes([], deadline=8, environment={}, max_bytes=L.MAX_BYTES)
        self.assertTrue(process.stdout.close.called)


if __name__ == '__main__':
    unittest.main()
