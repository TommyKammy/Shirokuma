"""Offline scope, shared deadline and pipe-boundary checks for the fixed list."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('gateway_pod_read_test', ROOT / 'tools/local_experiment/gateway/pod_read.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
U = P.U
SENTINEL = 'private-value-not-for-errors'
SERVER = 'https://127.0.0.1:55737'


def config(server=SERVER):
    return {'clusters': [{'cluster': {'server': server}}]}


def nodes():
    return {'apiVersion': 'v1', 'kind': 'NodeList', 'metadata': {}, 'items': [
        {'status': {'nodeInfo': {'architecture': 'arm64', 'kubeletVersion': 'v1.33.4+k3s1'}}}]}


def pods():
    return {'apiVersion': 'v1', 'kind': 'PodList', 'metadata': {}, 'items': [
        {'metadata': {'namespace': 'shirokuma-dev', 'name': 'trino-local-test'}, 'status': {'phase': 'Running'}}]}


class FixedPodReadTests(unittest.TestCase):
    def invoke(self, responses):
        with patch.object(U, '_command', side_effect=responses) as command, \
             patch.object(P.time, 'monotonic', return_value=100):
            return P.fixed_list(), command

    def test_fixed_paths_shared_deadline_caps_and_private_environment(self):
        with patch.dict(P.os.environ, {'AWS_SECRET_ACCESS_KEY': SENTINEL, 'HTTPS_PROXY': SENTINEL,
                                      'KUBECONFIG': SENTINEL, 'PYTHONPATH': SENTINEL}):
            result, command = self.invoke([config(), nodes(), pods()])
        self.assertEqual(result, pods())
        calls = command.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].args[0], ['config', 'view', '--minify', '-o', 'json'])
        self.assertNotIn('_max_bytes', calls[0].kwargs)
        for call, path in zip(calls[1:], [P.NODES_PATH, P.PODS_PATH]):
            self.assertEqual(call.args[0], ['--server', SERVER, '--request-timeout=6s', 'get', '--raw', path])
            self.assertEqual(call.kwargs['_max_bytes'], 2 * 1024 * 1024)
        for call in calls:
            self.assertEqual(call.kwargs['deadline'], 120)
            self.assertNotIn(SENTINEL, json.dumps(call.kwargs['environment']))
        self.assertEqual(P.READ_SECONDS, 20)
        self.assertEqual(U.CLEANUP_SECONDS, 2)

    def test_nonlocal_and_injected_server_stop_before_any_api_get(self):
        for server in ('https://remote.example', 'http://127.0.0.1', 'https://user@localhost',
                       'https://localhost/path', 'https://localhost?x=y', 'https://localhost/#x'):
            with self.subTest(server=server), patch.object(U, '_command', return_value=config(server)) as command:
                with self.assertRaises(U.PodUsageError): P.fixed_list()
                self.assertEqual(command.call_count, 1)

    def test_invalid_node_scope_never_reads_pods(self):
        values = []
        for mutation in (lambda v: v.update(kind='PodList'), lambda v: v.update(items=[]),
                         lambda v: v['items'].append(deepcopy(v['items'][0])),
                         lambda v: v['metadata'].update({'continue': 'next'}),
                         lambda v: v['items'][0]['status']['nodeInfo'].update(architecture='amd64'),
                         lambda v: v['items'][0]['status']['nodeInfo'].update(kubeletVersion='v1.33.4'),
                         lambda v: v['items'][0]['status'].update(nodeInfo=None)):
            value = nodes(); mutation(value); values.append(value)
        for value in values:
            with self.subTest(value=value), patch.object(U, '_command', side_effect=[config(), value]) as command:
                with self.assertRaises(U.PodUsageError): P.fixed_list()
                self.assertEqual(command.call_count, 2)

    def test_invalid_pod_page_is_rejected_without_more_reads(self):
        for mutation in (lambda v: v.update(kind='NodeList'), lambda v: v.update(items=[None]),
                         lambda v: v.update(items=v['items'] * 51),
                         lambda v: v['metadata'].update({'continue': None}),
                         lambda v: v['items'][0]['metadata'].update(namespace='other'),
                         lambda v: v['items'][0]['metadata'].update(name='../private')):
            value = pods(); mutation(value)
            with self.subTest(value=value), patch.object(U, '_command', side_effect=[config(), nodes(), value]) as command:
                with self.assertRaises(U.PodUsageError): P.fixed_list()
                self.assertEqual(command.call_count, 3)

    def test_continuation_empty_or_full_page_is_preserved_without_paging(self):
        for count in (0, 50):
            value = pods(); value['items'] *= count; value['metadata']['continue'] = 'opaque-token'
            result, command = self.invoke([config(), nodes(), value])
            self.assertEqual(result, value)
            self.assertEqual(command.call_count, 3)

    def test_spent_stage_budget_prevents_later_stage_and_never_resets(self):
        for expire_stage in (0, 1, 2):
            clock = [100]
            responses = [config(), nodes(), pods()]
            def command(*args, **kwargs):
                self.assertEqual(kwargs['deadline'], 120)
                value = responses.pop(0)
                if 3 - len(responses) - 1 == expire_stage:
                    clock[0] = 120
                return value
            with self.subTest(stage=expire_stage), patch.object(U, '_command', side_effect=command) as call, \
                 patch.object(P.time, 'monotonic', side_effect=lambda: clock[0]):
                with self.assertRaises(U.PodUsageError) as caught: P.fixed_list()
                self.assertEqual(caught.exception.code, 'READ_TIMEOUT')
                self.assertEqual(call.call_count, expire_stage + 1)

    def test_first_cancellation_and_cleanup_fatal_are_never_unknown(self):
        for error in (KeyboardInterrupt(), SystemExit(143), U.PodUsageCleanupError()):
            with self.subTest(kind=type(error).__name__), patch.object(U, '_command', side_effect=[config(), error]) as command:
                with self.assertRaises(type(error)) as caught: P.fixed_list()
                self.assertIs(caught.exception, error)
                self.assertEqual(command.call_count, 2)


class FixedPodPipeTests(unittest.TestCase):
    def transport(self, chunks, *, read_error=None, kill_error=None, wait_error=None):
        process = Mock(returncode=None); process.stdout.fileno.return_value = 99; process.pid = 1234
        def wait(**kwargs):
            if wait_error is not None:
                raise wait_error
            process.returncode = 0
            return 0
        process.wait.side_effect = wait
        selector = Mock(); selector.select.return_value = [True]
        patches = [patch.object(U.subprocess, 'Popen', return_value=process),
                   patch.object(U.selectors, 'DefaultSelector', return_value=selector),
                   patch.object(U.os, 'set_blocking'), patch.object(U.os, 'read', side_effect=read_error or chunks),
                   patch.object(U.os, 'killpg', side_effect=kill_error),
                   patch.object(U.time, 'monotonic', return_value=0)]
        mocks = [item.start() for item in patches]
        for item in reversed(patches): self.addCleanup(item.stop)
        return process, selector, mocks

    def read(self):
        return U._command(['get', '--raw', P.PODS_PATH], deadline=P.READ_SECONDS,
                          environment={}, _max_bytes=P.MAX_BYTES)

    def test_two_mib_json_boundary_is_accepted_and_private_cap_does_not_change_usage(self):
        raw = b'{"x":"' + b'x' * (P.MAX_BYTES - 8) + b'"}'
        self.assertEqual(len(raw), P.MAX_BYTES)
        self.transport([raw[i:i+4096] for i in range(0, len(raw), 4096)] + [b''])
        self.assertEqual(len(self.read()['x']), P.MAX_BYTES - 8)
        self.assertEqual(U.MAX_BYTES, 64 * 1024)

    def test_overflow_is_stopped_at_limit_plus_one_and_owned_process_reaped(self):
        process, selector, mocks = self.transport([b'x' * 4096] * (P.MAX_BYTES // 4096) + [b'x'])
        with self.assertRaises(U.PodUsageError) as caught: self.read()
        self.assertEqual(caught.exception.code, 'READ_LIMIT')
        self.assertEqual(mocks[3].call_args.args[1], 1)
        mocks[4].assert_called_once_with(process.pid, signal.SIGKILL)
        process.wait.assert_called_once_with(timeout=2)
        selector.close.assert_called_once(); process.stdout.close.assert_called_once()
        self.assertEqual(mocks[0].call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_timeout_and_secondary_cleanup_keep_first_cancellation(self):
        original = KeyboardInterrupt()
        process, selector, _ = self.transport([], read_error=original, kill_error=OSError(SENTINEL),
                                             wait_error=subprocess.TimeoutExpired('private', 2))
        with self.assertRaises(KeyboardInterrupt) as caught: self.read()
        self.assertIs(caught.exception, original)
        process.wait.assert_called_once_with(timeout=2)
        selector.close.assert_called_once(); process.stdout.close.assert_called_once()

    def test_ordinary_read_failure_with_unreaped_child_is_fatal(self):
        self.transport([], read_error=U.PodUsageError('READ_TIMEOUT'),
                       wait_error=subprocess.TimeoutExpired('private', 2))
        with self.assertRaises(U.PodUsageCleanupError) as caught: self.read()
        self.assertEqual(str(caught.exception), 'POD_USAGE_CLEANUP_FAILED')
        self.assertNotIn(SENTINEL, str(caught.exception))

    def test_duplicate_or_nonfinite_json_cannot_pass_larger_limit(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'[]', b'{"x":"\xff"}'):
            with self.subTest(raw=raw):
                self.transport([raw, b''])
                with self.assertRaises(U.PodUsageError): self.read()
                self.doCleanups()


if __name__ == '__main__':
    unittest.main()
