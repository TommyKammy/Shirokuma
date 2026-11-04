"""Exercise the telemetry log boundary without running Docker or an HTTP server."""
from contextlib import nullcontext
from dataclasses import replace
from http.client import HTTPResponse
import io
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import Mock, patch

import test_database_platform_telemetry_readiness as readiness
import db_platform_loki_errors as errors
import db_platform_telemetry_logs as logs
import yaml
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

    def request(self, handler, *, method='GET', suffix='', headers='', body='', role='admin', principal=None, raw_logs=False, loki_errors=False):
        class Connection:
            def __init__(self,raw):self.incoming,self.outgoing=io.BytesIO(raw),io.BytesIO()
            def makefile(self,*args):return self.incoming
            def sendall(self,raw):self.outgoing.write(raw)
        path = logs.RAW_LOGS_PATH if raw_logs else '/api/v1/bears/platform-telemetry-logs'
        if loki_errors:
            path = errors.PATH
        raw=(method+' '+path+suffix+' HTTP/1.1\r\n'
            'Host: 127.0.0.1\r\nConnection: close\r\nX-Shirokuma-Role: '+role+'\r\n'+headers+'\r\n'+body).encode()
        conn=Connection(raw)
        with patch.object(readiness.base.runtime_profile,'active',return_value=True),patch.object(readiness.auth,'authenticate_principal',return_value=principal or self.principal),patch.object(handler,'log_message'):
            handler(conn,('127.0.0.1',1),SimpleNamespace())
        response=HTTPResponse(Connection(conn.outgoing.getvalue()));response.begin()
        result = response.status, json.loads(response.read())
        return (*result, response.getheader('Cache-Control')) if raw_logs or loki_errors else result

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


    def test_raw_exact_streams_on_both_http_adapters_and_summary_unchanged(self):
        stdout = STAMP + 'Z <script>雪🐻</script>  \r\n'.encode()
        stderr = STAMP + b'.123456789Z \n'
        self.read.side_effect = lambda args, deadline: (
            (stdout, stderr) if args[0] == 'logs' else self.read_owned(args, deadline))
        for handler in self.handlers():
            self.read.reset_mock()
            status, result, cache = self.request(handler, raw_logs=True)
            self.assertEqual((status, cache), (200, 'no-store'))
            self.assertEqual(result['scope'], dict(since_minutes=10, tail_lines=100, max_bytes=65536))
            for name in ('agenthouse', 'loki'):
                item = result[name]
                self.assertEqual(set(item), {'observed_at', 'status', 'line_count', 'stdout', 'stderr'})
                self.assertEqual((item['status'], item['line_count']), ('available', 2))
                self.assertEqual((item['stdout'], item['stderr']), (stdout.decode(), stderr.decode()))
            self.assertEqual([call.args[0][0] for call in self.read.call_args_list],
                             ['container', 'logs', 'container', 'logs'])
            _, summary = self.request(handler)
            self.assertNotIn('script', json.dumps(summary))
            self.assertEqual(summary['loki']['line_count'], 2)
            self.assertNotIn('stdout', summary['loki'])

    def test_raw_strict_input_and_platform_authority(self):
        for handler in self.handlers():
            for options, expected in (
                ({'method': 'POST'}, 404), ({'suffix': '?'}, 400),
                ({'suffix': '?since=1'}, 400), ({'suffix': '#x'}, 400),
                ({'headers': 'Content-Length: 1\r\n', 'body': 'x'}, 400),
                ({'headers': 'Transfer-Encoding: chunked\r\n'}, 400),
                ({'headers': 'X-Test: a\r\nX-Test: b\r\n'}, 400),
                ({'role': 'bi_viewer'}, 403),
                ({'principal': replace(self.principal, maximum_role='data_engineer')}, 403),
                ({'principal': replace(self.principal, platform_admin=False)}, 403),
            ):
                status, _, cache = self.request(handler, raw_logs=True, **options)
                self.assertEqual((status, cache), (expected, 'no-store'))
        self.read.assert_not_called()

    def test_raw_origin_is_checked_on_both_adapters_before_capture(self):
        for handler in self.handlers():
            for origin in ('', 'null', 'http://foreign.example', 'http://127.0.0.1/path',
                           'http://127.0.0.1\r\nOrigin: http://127.0.0.1'):
                self.read.reset_mock()
                status, error, cache = self.request(handler, raw_logs=True,
                                                    headers=f'Origin: {origin}\r\n')
                self.assertEqual((status, error['code'], cache), (403, 'INVALID_ORIGIN', 'no-store'))
                self.read.assert_not_called()
            status, _, cache = self.request(handler, raw_logs=True,
                                            headers='Origin: http://127.0.0.1\r\n')
            self.assertEqual((status, cache), (200, 'no-store'))
            self.assertEqual(self.read.call_count, 4)
            self.assertEqual(self.request(handler, headers='Origin: http://foreign.example\r\n')[0], 200)

    def test_raw_invalid_sources_fail_independently(self):
        good = STAMP + b'Z ok\n'
        for streams in ((b'Docker diagnostic\n', good), (good, b'Docker diagnostic\n'),
                        (good, good[:-1]), (good, STAMP + b'Z \xff\n'),
                        (good * 100, good), (good, STAMP + b'Z ' + b'a' * 65536 + b'\n'),
                        (b'2026-02-30T00:00:00Z invalid\n', good), None):
            self.read.reset_mock()

            def read(args, deadline):
                if self.read.call_count == 1 and streams is None:
                    return b'[]\n', b''
                if self.read.call_count == 2 and args[0] == 'logs':
                    return streams
                return self.read_owned(args, deadline)

            self.read.side_effect = read
            result = logs.observe_raw(self.principal, 'admin')
            for name, status in (('agenthouse', 'unavailable'), ('loki', 'available')):
                item = result[name]
                self.assertEqual((item['status'], item['line_count'], item['stdout'], item['stderr']),
                                 (status, 0, '', ''))
            self.assertEqual(self.read.call_count, 3 if streams is None else 4)

    def test_raw_revocation_before_read_and_before_response(self):
        for fail_at in (1, 2, 4):
            self.read.reset_mock()
            self.users['p']['password_hash'] = 'before'

            def read(args, deadline):
                if self.read.call_count == fail_at:
                    self.users['p']['password_hash'] = 'changed'
                return self.read_owned(args, deadline)

            self.read.side_effect = read
            with self.assertRaises(OperationError) as denied:
                logs.observe_raw(self.principal, 'admin')
            self.assertEqual(denied.exception.status, 403)
            self.assertEqual(self.read.call_count, fail_at)

    def prepare_loki(self):
        directory = self.enterContext(TemporaryDirectory())
        root = Path(directory)
        (root / 'tmp/local-experiment/loki').mkdir(parents=True)
        self.patch(patch.object(errors.loki, 'ROOT', root))
        self.patch(patch.object(errors, '_window', return_value=(1793750100000000000, 1793750700000000000)))
        config = (errors.loki.HERE / 'config.yaml').read_text()
        return config, self.patch(patch.object(errors.loki, 'request'))

    @staticmethod
    def streams(*streams):
        return dict(status='success', data=dict(resultType='streams', result=list(streams)))

    def test_loki_exact_severity_payload_dedup_and_loaded_policy(self):
        config, request = self.prepare_loki()
        loaded = yaml.safe_load(config)
        self.assertEqual(loaded['limits_config']['retention_period'], '0s')
        self.assertEqual(loaded['limits_config']['deletion_mode'], 'disabled')
        self.assertEqual(loaded['limits_config']['retention_stream'], [
            dict(selector='{service_name="shirokuma-local",component="sre-telemetry-severity"}',
                 priority=1, period='24h')])
        agent = (
            '2026-11-04T00:00:00.000000001Z 2026.11.04 00:00:00.000000 [ 1 ] {} <Error> 雪\n'
            '2026-11-04T00:00:00.000000002Z 2026.11.04 00:00:00.000000 [ 1 ] <Warning> <Fatal> decoy\n'
            '2026-11-04T00:00:00.000000003Z prefix <Fatal> decoy\n'
        ).encode()
        severe = b'2026-11-04T00:00:00.123456789Z level=fatal msg=secret\n'
        self.read.side_effect = [self.read_owned(['inspect', logs.agenthouse.NAME], 0), (agent, agent),
                                 self.read_owned(['inspect', logs.loki.NAME], 0), (b'', severe)]
        existing = dict(stream=dict(errors._LABELS, source='agenthouse', severity='error', detected_level='error'),
                        values=[['1793750400000000001', 'telemetry-severity']])
        loaded_config = config.replace('period: 24h', 'period: 1d') + '# '
        loaded_config = loaded_config.ljust(92244, 'x') + '\n'
        self.assertEqual(len(loaded_config.encode()), 92245)
        request.side_effect = [self.streams(existing), loaded_config, '']
        result = errors.capture(self.principal, 'admin')
        self.assertEqual(result, dict(operation='capture', status='accepted', since_minutes=10,
                                     window_end='2026-11-04T00:05:00.000000000Z', tail_lines=100,
                                     max_bytes=65536, accepted=1, sources=dict(agenthouse='available', loki='available')))
        query, retention, push = request.call_args_list
        self.assertEqual(urlsplit(query.args[0]).path, '/loki/api/v1/query_range')
        self.assertEqual(parse_qs(urlsplit(query.args[0]).query),
                         dict(query=[errors._SELECTOR], start=['1793750100000000000'],
                              end=['1793750700000000001'], direction=['backward'], limit=['1000']))
        self.assertEqual(retention.args, ('/config', None))
        self.assertEqual(push.args, ('/loki/api/v1/push', dict(streams=[
            dict(stream=dict(errors._LABELS, source='loki', severity='fatal'),
                 values=[['1793750400123456789', 'telemetry-severity']])])))
        self.assertEqual(query.kwargs, {'timeout': 2})
        self.assertEqual(retention.kwargs, {'timeout': 2, 'max_bytes': 131072})
        self.assertEqual(push.kwargs, {'timeout': 2, 'expected_status': 204})

    def test_loki_request_checks_exact_status_before_read_and_preserves_default(self):
        response = Mock()
        opener = Mock()
        opener.open.return_value = nullcontext(response)
        with patch.object(errors.loki.urllib.request, 'build_opener', return_value=opener):
            for status in (200, 202, 260):
                with self.subTest(status=status):
                    response.status = status
                    response.read.reset_mock()
                    with self.assertRaises(errors.loki.LokiError):
                        errors.loki.request('/loki/api/v1/push', {}, timeout=2, expected_status=204)
                    response.read.assert_not_called()
            response.status = 204
            response.read.return_value = b''
            self.assertEqual(errors.loki.request('/loki/api/v1/push', {}, timeout=2, expected_status=204), '')
            response.status = 260
            response.read.return_value = b'blocked'
            self.assertEqual(errors.loki.request('/loki/api/v1/push', {}), 'blocked')

    def test_loki_request_caps_config_wire_bytes_and_preserves_default(self):
        response = Mock(status=200)
        opener = Mock()
        opener.open.return_value = nullcontext(response)
        with patch.object(errors.loki.urllib.request, 'build_opener', return_value=opener):
            response.read.return_value = b'x' * 131073
            with self.assertRaises(errors.loki.LokiError):
                errors.loki.request('/config', max_bytes=131072)
            response.read.assert_called_once_with(131073)
            response.read.reset_mock()
            response.read.return_value = b'x' * 131072
            self.assertEqual(len(errors.loki.request('/config', max_bytes=131072)), 131072)
            response.read.assert_called_once_with(131073)
            response.read.reset_mock()
            self.assertEqual(len(errors.loki.request('/config')), 131072)
            response.read.assert_called_once_with(2 * 1024**2 + 1)

    def test_loki_loaded_policy_requires_manual_deletion_disabled(self):
        config, request = self.prepare_loki()
        request.return_value = config.replace('  retention_period: 0s\n', '')
        self.assertTrue(errors._retention(lambda: None))
        request.return_value = config.replace('period: 24h', 'period: 24h0m0s')
        self.assertTrue(errors._retention(lambda: None))
        for policy in (
            config.replace('  deletion_mode: disabled\n', ''),
            config.replace('deletion_mode: disabled', 'deletion_mode: filter-and-delete'),
            config.replace('retention_period: 0s', 'retention_period: 48h'),
            config.replace('delete_request_store: filesystem', 'delete_request_store: s3'),
            config.replace('period: 24h', 'period: 48h'),
            config + '# ' + '雪' * 44000,
            'limits_config: [',
            'limits_config: 42',
            {'limits_config': {}},
        ):
            request.return_value = policy
            if policy == 'limits_config: [':
                with self.assertRaises(yaml.YAMLError):
                    errors._retention(lambda: None)
            else:
                self.assertFalse(errors._retention(lambda: None))

    def test_loki_fail_closed_and_uncertain_push_reconciles_only_on_explicit_capture(self):
        config, request = self.prepare_loki()
        sample = b'2026-11-04T00:00:00.000000001Z level=error msg=private\n'
        owned = self.read.side_effect

        def read(args, deadline):
            if args[0] == 'logs':
                return sample, b''
            return owned(args, deadline)

        self.read.side_effect = read
        row = dict(stream=dict(errors._LABELS, source='loki', severity='error'),
                   values=[['1793750400000000001', 'telemetry-severity']])
        for replies, count in (
            ([self.streams(row | {'values': row['values'] * 1000})], 1),
            ([self.streams(row | {'values': [['1793750400000000001', 'private']]})], 1),
            ([self.streams(), config.replace('retention_enabled: true', 'retention_enabled: false')], 2),
            ([self.streams(), config.replace('component=', 'namespace=')], 2),
            ([self.streams(), 'limits_config: ['], 2),
            ([self.streams(), config.replace('deletion_mode: disabled', 'deletion_mode: filter-only')], 2),
        ):
            request.reset_mock()
            request.side_effect = replies
            self.assertEqual(errors.capture(self.principal, 'admin')['status'], 'unavailable')
            self.assertEqual(request.call_count, count)
        request.reset_mock()
        request.side_effect = [self.streams(), config, OSError('private')]
        result = errors.capture(self.principal, 'admin')
        self.assertEqual((result['status'], result['accepted'], request.call_count), ('unconfirmed', None, 3))
        request.side_effect = [self.streams(row)]
        self.assertEqual(errors.capture(self.principal, 'admin')['accepted'], 0)
        self.assertEqual(request.call_count, 4)

        request.reset_mock()
        with patch.object(logs, '_authority', return_value='initial') as authority:
            def revoke(*args, **kwargs):
                authority.return_value = 'changed'
                return self.streams()
            request.side_effect = revoke
            with self.assertRaises(OperationError):
                errors.capture(self.principal, 'admin')
            self.assertEqual(request.call_count, 1)

    def test_loki_uses_all_classified_severe_rows_and_distinguishes_missing_sources(self):
        config, request = self.prepare_loki()
        owned = self.read.side_effect
        sample = b''.join(
            f'2026-11-04T00:00:00.{ns:09d}Z level=error msg=private\n'.encode()
            for ns in range(100)
        )

        def read(args, deadline):
            if args[0] == 'logs':
                return sample, b''
            if args[-1] == logs.agenthouse.NAME:
                raise OSError('unavailable')
            return owned(args, deadline)

        self.read.side_effect = read
        request.side_effect = [self.streams(), config, '']
        result = errors.capture(self.principal, 'admin')
        self.assertEqual(result['sources'], dict(agenthouse='unavailable', loki='available'))
        self.assertEqual(result['accepted'], 100)
        self.assertEqual(len(request.call_args.args[1]['streams'][0]['values']), 100)

        request.reset_mock()
        self.read.side_effect = OSError('unavailable')
        result = errors.capture(self.principal, 'admin')
        self.assertEqual((result['status'], result['accepted']), ('unavailable', None))
        request.assert_not_called()

    def test_loki_authority_loss_wins_even_when_push_was_attempted(self):
        config, request = self.prepare_loki()
        owned = self.read.side_effect

        def read(args, deadline):
            if args[0] == 'logs':
                return b'2026-11-04T00:00:00.000000001Z level=fatal msg=private\n', b''
            return owned(args, deadline)

        self.read.side_effect = read
        with patch.object(logs, '_authority', return_value='initial') as authority:
            def reply(path, payload, *, timeout, expected_status=None, max_bytes=None):
                if path == '/config':
                    return config
                if path == '/loki/api/v1/push':
                    authority.return_value = 'changed'
                    raise OSError('private')
                return self.streams()

            request.side_effect = reply
            with self.assertRaises(OperationError) as denied:
                errors.capture(self.principal, 'admin')
            self.assertEqual(denied.exception.status, 403)
            self.assertEqual(request.call_count, 3)

    def test_loki_search_strict_streams_and_nanosecond_window(self):
        _, request = self.prepare_loki()
        row = dict(stream=dict(errors._LABELS, source='agenthouse', severity='fatal', detected_level='fatal'),
                   values=[['1793750100000000000', 'telemetry-severity'],
                           ['1793750700000000000', 'telemetry-severity']])
        request.return_value = self.streams(row)
        result = errors.search(self.principal, 'admin')
        self.assertEqual(result, dict(operation='search', status='available', since_minutes=10,
                                      window_end='2026-11-04T00:05:00.000000000Z', limit=20, hits=[
            dict(timestamp='2026-11-04T00:05:00.000000000Z', source='agenthouse', severity='fatal'),
            dict(timestamp='2026-11-03T23:55:00.000000000Z', source='agenthouse', severity='fatal'),
        ]))
        self.read.assert_not_called()
        self.assertIn('limit=20', request.call_args.args[0])
        for invalid in (row | {'values': [['1793750700000000001', 'telemetry-severity']]},
                        row | {'values': [[1793750400000000000, 'telemetry-severity']]},
                        row | {'values': row['values'] * 11},
                        row | {'stream': row['stream'] | {'detected_level': 'error'}},
                        row | {'stream': row['stream'] | {'namespace': 'legacy'}},
                        row | {'stream': row['stream'] | {'severity': 'warning'}}):
            request.return_value = self.streams(invalid)
            result = errors.search(self.principal, 'admin')
            self.assertEqual((result['status'], result['hits']), ('unavailable', []))
        request.return_value = self.streams()
        self.assertEqual(errors.search(self.principal, 'admin')['status'], 'available')

    def test_loki_both_http_adapters_and_no_store(self):
        _, request = self.prepare_loki()
        request.return_value = self.streams()
        same = 'Origin: http://127.0.0.1\r\n'
        for handler in self.handlers():
            for method in ('GET', 'POST'):
                for options, status in (
                    ({'headers': same}, 200),
                    ({'headers': same + 'Content-Length: 0\r\n'}, 200),
                    ({'headers': 'Origin: http://other\r\n'}, 403),
                    ({'headers': same * 2}, 403),
                    ({'headers': same, 'suffix': '?'}, 400),
                    ({'headers': same, 'suffix': '?expected_status=204'}, 400),
                    ({'headers': same, 'suffix': '#'}, 400),
                    ({'headers': same + 'Content-Length: 2\r\n', 'body': '{}'}, 400),
                    ({'headers': same + 'Transfer-Encoding: chunked\r\n'}, 400),
                    ({'headers': same + 'X-Test: a\r\nX-Test: b\r\n'}, 400),
                    ({'headers': same, 'role': 'bi_viewer'}, 403),
                    ({'headers': same, 'principal': replace(self.principal, platform_admin=False)}, 403),
                ):
                    with self.subTest(handler=handler, method=method, options=options):
                        self.read.reset_mock()
                        request.reset_mock()
                        code, dto, cache = self.request(handler, method=method, loki_errors=True, **options)
                        self.assertEqual((code, cache), (status, 'no-store'))
                        if status == 200:
                            self.assertEqual(dto['status'], 'available' if method == 'GET' else 'no_new_events')
                        else:
                            self.read.assert_not_called()
                            request.assert_not_called()
            code, _, cache = self.request(handler, method='POST', loki_errors=True)
            self.assertEqual((code, cache), (403 if handler.__name__ == 'WorkbenchHandler' else 200, 'no-store'))

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
