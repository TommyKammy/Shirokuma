"""Exercise the telemetry log boundary without running Docker or an HTTP server."""
from contextlib import nullcontext
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_database_platform_telemetry_readiness as readiness
import db_platform_telemetry_logs as logs
from warehouse_operations import OperationError


ID = 'a' * 64
STAMP = b'2026-11-04T00:00:00'


def projection(source):
    name, manager, port, host_port, target, binds = source
    return ['/' + manager.NAME, ID, name + '-local', manager.IMAGE,
            {port: [{'HostIp': '127.0.0.1', 'HostPort': host_port}]},
            [['volume', manager.VOLUME, target, True], *map(list, binds)]]


class TelemetryLogsTests(unittest.TestCase):
    patch = readiness.TelemetryReadinessTests.patch
    files = readiness.TelemetryReadinessTests.files
    handlers = readiness.TelemetryReadinessTests.handlers

    def setUp(self):
        readiness.base.BaselineTests.setUp(self)
        self.read = self.patch(patch.object(logs, '_docker', side_effect=self.read_owned))

    def read_owned(self, args, deadline):
        if args[0] == 'logs':
            return b'', b''
        source = next(source for source in logs._SOURCES if args[-1] == source[1].NAME)
        return json.dumps(projection(source)).encode() + b'\n', b''

    def observe(self):
        return logs.observe(self.principal, 'admin')

    def test_exact_commands_closed_scope_and_owned_empty(self):
        with patch.object(readiness.base.audit, 'snapshot', side_effect=AssertionError('store read')):
            result = self.observe()
        self.assertEqual(result['scope'], dict(since_minutes=10, tail_lines=100, event_limit=20,
                                               streams=['stdout', 'stderr']))
        self.assertEqual(set(result), {'scope', 'agenthouse', 'loki'})
        commands = [call.args[0] for call in self.read.call_args_list]
        read_logs = ['logs', '--since=10m', '--tail=100', '--timestamps', ID]
        self.assertEqual(commands, [
            ['container', 'inspect', '--format', logs._PROJECTION, 'shirokuma-local-agenthouse'], read_logs,
            ['container', 'inspect', '--format', logs._PROJECTION, 'shirokuma-local-loki'], read_logs,
        ])
        self.assertNotIn('.Config.Env', logs._PROJECTION)
        self.assertNotIn('json .Mounts', logs._PROJECTION)
        for name in ('agenthouse', 'loki'):
            item = result[name]
            self.assertEqual(set(item), {'status', 'observed_at', 'line_count', 'other_count', 'level_counts', 'events'})
            self.assertEqual((item['status'], item['line_count'], item['events']), ('available', 0, []))

    def test_unsafe_and_missing_ownership_never_reads_logs(self):
        for index, invalid in enumerate(['/other', ID.upper(), 'wrong', 'other-image', {}, []]):
            with self.subTest(field=index):
                row = projection(logs._SOURCES[0])
                row[index] = invalid
                self.read.side_effect = [(json.dumps(row).encode(), b''), OSError()]
                self.read.reset_mock()
                result = self.observe()
                self.assertEqual([result[name]['status'] for name in ('agenthouse', 'loki')],
                                 ['unavailable', 'unavailable'])
                self.assertEqual([call.args[0][1] for call in self.read.call_args_list], ['inspect', 'inspect'])
        row = projection(logs._SOURCES[1])
        row[-1][-1][-1] = True
        self.read.side_effect = [(b'', b''), (json.dumps(row).encode(), b'')]
        result = self.observe()
        self.assertEqual(result['loki']['status'], 'unavailable')

    def test_scalar_or_null_inspect_leaves_other_service_available(self):
        for malformed in (b'42\n', b'null\n'):
            with self.subTest(malformed=malformed):
                self.read.reset_mock()
                self.read.side_effect = [
                    (malformed, b''),
                    self.read_owned(['container', 'inspect', logs.loki.NAME], None),
                    (b'', b''),
                ]

                result = self.observe()

                self.assertEqual(result['agenthouse']['status'], 'unavailable')
                self.assertEqual(result['loki']['status'], 'available')
                self.assertEqual(result['loki']['line_count'], 0)
                self.assertEqual([call.args[0] for call in self.read.call_args_list], [
                    ['container', 'inspect', '--format', logs._PROJECTION, logs.agenthouse.NAME],
                    ['container', 'inspect', '--format', logs._PROJECTION, logs.loki.NAME],
                    ['logs', '--since=10m', '--tail=100', '--timestamps', ID],
                ])

    def test_classification_exact_precision_stable_order_and_no_raw_data(self):
        prefix = b'2026.11.04 00:00:00.000000 [ 123 ] '
        agent = logs._sample('agenthouse', (
            STAMP + b'.1Z ' + prefix + b'{query} <Warning> synthetic\n'
            + STAMP + b'.100000000Z ' + prefix + b'<Fatal> synthetic\n'
            + STAMP + b'Z ' + prefix + b'{} <Error> synthetic\n'
            + STAMP + b'Z ' + b'noise <Fatal> synthetic\n',
            STAMP + b'.100000001Z ' + prefix + b'{} <Error> synthetic\n',
        ))
        self.assertEqual(agent['level_counts'], dict(warning=1, error=2, fatal=1))
        self.assertEqual(agent['other_count'], 1)
        self.assertEqual([e['timestamp'][19:] for e in agent['events']], ['.100000001Z', '.1Z', '.100000000Z', 'Z'])
        self.assertEqual([e['severity'] for e in agent['events']], ['error', 'warning', 'fatal', 'error'])
        loki = logs._sample('loki', (STAMP + b'Z level=warn msg=synthetic\n'
            + STAMP + b'Z x=1 level=fatal msg=synthetic\n'
            + STAMP + b'Z level=errors msg=synthetic\n'
            + b'2026-02-29T00:00:00Z level=error msg=synthetic\n'
            + STAMP + b'.1234567890Z level=error msg=synthetic\n', b''))
        self.assertEqual((loki['line_count'], loki['other_count'], loki['level_counts']['warning']), (5, 4, 1))
        self.assertFalse('synthetic' in json.dumps([agent, loki]))
        self.assertEqual(set(loki['events'][0]), {'timestamp', 'severity'})

    def test_tail_limit_and_incomplete_results(self):
        line = STAMP + b'Z level=error msg=synthetic\n'
        result = logs._sample('loki', (line * 100, b''))
        self.assertEqual((result['line_count'], result['level_counts']['error'], len(result['events'])), (100, 100, 20))
        for streams in ((line * 101, b''), (line[:-1], b''), (b'', b'x' * 65536 + b'\n')):
            with self.assertRaises(ValueError):
                logs._sample('loki', streams)

    def test_fingerprint_before_between_and_after_success_or_failure(self):
        for fail_at, failure in ((1, False), (1, True), (2, False), (2, True), (4, True)):
            self.read.reset_mock()
            self.users['p']['password_hash'] = 'before'

            def read(args, deadline):
                if self.read.call_count == fail_at:
                    self.users['p']['password_hash'] = 'changed'
                    if failure:
                        raise subprocess.SubprocessError()
                return self.read_owned(args, deadline)

            self.read.side_effect = read
            with self.assertRaises(OperationError) as denied:
                self.observe()
            self.assertEqual(denied.exception.status, 403)
            self.assertEqual(self.read.call_count, fail_at)
        self.read.reset_mock()
        for principal, role in ((replace(self.principal, platform_admin=1), 'admin'),
                                (replace(self.principal, maximum_role='data_engineer'), 'admin'),
                                (self.principal, 'bi_viewer')):
            with self.assertRaises(OperationError):
                logs.observe(principal, role)
        self.read.assert_not_called()

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None):
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        raw=(method+' /api/v1/bears/platform-telemetry-logs'+suffix+' HTTP/1.1\r\n'
            'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(readiness.base.runtime_profile,'active',return_value=True),patch.object(readiness.auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin()
        return response.status, json.loads(response.read())

    def test_both_strict_inputless_routes(self):
        cases = [({}, 200), ({'headers': 'Content-Length: 0\r\n'}, 200),
                 ({'method': 'POST'}, 404), ({'role': 'bi_viewer'}, 403),
                 ({'suffix': '?'}, 400), ({'suffix': '?database_id=x'}, 400),
                 ({'suffix': '#fragment'}, 400), ({'headers': 'Content-Length: 1\r\n', 'body': 'x'}, 400),
                 ({'headers': 'Transfer-Encoding: chunked\r\n'}, 400),
                 ({'headers': 'X-Test: a\r\nX-Test: b\r\n'}, 400)]
        for handler in self.handlers():
            for options, expected in cases:
                self.read.reset_mock()
                status, result = self.request(handler, **options)
                self.assertEqual(status, expected)
                if expected == 200:
                    self.assertEqual(set(result), {'scope', 'agenthouse', 'loki'})
                else:
                    self.read.assert_not_called()


class DockerCaptureTests(unittest.TestCase):
    def test_fixed_host_sanitized_environment_and_bounded_cleanup(self):
        for scenario in ('success', 'oversize', 'timeout', 'partial', 'exit'):
            with self.subTest(scenario=scenario):
                process = Mock()
                process.wait.return_value = 1 if scenario == 'exit' else 0
                process.poll.return_value = None if scenario in ('oversize', 'timeout') else 0
                selector = Mock()
                pending = [SimpleNamespace(fd=1, fileobj=process.stdout),
                           SimpleNamespace(fd=2, fileobj=process.stderr)]
                selector.get_map.side_effect = lambda: pending
                selector.unregister.side_effect = lambda stream: pending.pop(
                    next(i for i, key in enumerate(pending) if key.fileobj is stream))
                selector.select.side_effect = lambda _: [] if scenario == 'timeout' else [(key, 1) for key in pending]
                stdout = b'x' * 32768 if scenario == 'oversize' else b'x' if scenario == 'partial' else b'x\n'
                stderr = b'x' * 32769 if scenario == 'oversize' else b'y\n'
                buffers = {1: stdout, 2: stderr}

                def consume(fd, size):
                    chunk, buffers[fd] = buffers[fd][:size], buffers[fd][size:]
                    return chunk

                with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://other', 'DOCKER_CONTEXT': 'other',
                                             'DOCKER_TLS_VERIFY': '1', 'DOCKER_TLS': '1', 'DOCKER_CERT_PATH': '/other'}), \
                     patch.object(logs.subprocess, 'Popen', return_value=process) as launch, \
                     patch.object(logs.selectors, 'DefaultSelector', return_value=nullcontext(selector)), \
                     patch.object(logs.os, 'read', side_effect=consume):
                    deadline = logs.time.monotonic() + 1
                    if scenario == 'success':
                        self.assertEqual(logs._docker(['test'], deadline), (b'x\n', b'y\n'))
                    else:
                        with self.assertRaises((ValueError, TimeoutError)):
                            logs._docker(['test'], deadline)
                    self.assertEqual(launch.call_args.args[0], ['docker', '--host',
                        'unix://' + str(Path.home() / '.colima/mac-studio-solo/docker.sock'), 'test'])
                    self.assertFalse(any(key.startswith('DOCKER_') for key in launch.call_args.kwargs['env']))
                self.assertEqual(process.kill.called, scenario in ('oversize', 'timeout'))
                process.stdout.close.assert_called_once()
                process.stderr.close.assert_called_once()

    def test_elapsed_deadline_applies_while_output_keeps_arriving(self):
        process = Mock()
        process.poll.return_value = None
        selector = Mock()
        selector.select.return_value = [(SimpleNamespace(fd=1, fileobj=process.stdout), 1)]
        with patch.object(logs.time, 'monotonic', side_effect=[0, 0, 1, 2]), \
             patch.object(logs.subprocess, 'Popen', return_value=process), \
             patch.object(logs.selectors, 'DefaultSelector', return_value=nullcontext(selector)), \
             patch.object(logs.os, 'read', return_value=b'x\n') as read:
            with self.assertRaises(TimeoutError):
                logs._docker(['test'], 2)
        self.assertEqual(read.call_count, 2)
        process.kill.assert_called_once()
        process.wait.assert_called_once()
