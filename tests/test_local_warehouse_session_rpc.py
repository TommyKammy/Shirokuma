"""Real private Unix sockets, injected handlers, and no Kubernetes/runtime calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from tools.local_experiment.warehouse_lifecycle import session_rpc as R


class SessionRPCTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name).resolve()
        self.output.chmod(0o700)
        self.token = uuid.uuid4().hex
        self.calls = []
        self.rpc = None
        self.thread = None
        self.errors = []
        self.done = threading.Event()
        self.release = threading.Event()

    def tearDown(self):
        self.done.set()
        self.release.set()
        if self.thread:
            self.thread.join(5)
            self.assertFalse(self.thread.is_alive(), 'test server did not drain')
        if self.rpc and self.rpc._directory_identity:
            self.rpc.close()
        self.temp.cleanup()

    def request(self, action='chill', request_id=None):
        return {'schema_version': 1, 'session_id': self.token,
                'request_id': request_id or str(uuid.uuid4()), 'warehouse': 'XS', 'action': action}

    def response(self, action, request_id, result='verified'):
        request = self.request(action, request_id)
        target = deepcopy(R.TARGETS[action])
        prior = deepcopy(R.TARGETS['wake' if action == 'chill' else 'chill'])
        return {**request, 'result': result, 'before': target if result == 'noop' else prior,
                'after': target, 'operation_id': str(uuid.uuid4()), 'receipt_sha256': 'a' * 64}

    def handler(self, action, request_id):
        self.calls.append((action, request_id))
        saved = json.loads(self.rpc.journal_path.read_text())
        entry = next(x for x in saved['requests'] if x['request']['request_id'] == request_id)
        self.assertEqual(entry['state'], 'pending')
        self.assertIsNone(entry['response'])
        return self.response(action, request_id)

    def start(self, handler=None, **kwargs):
        kwargs.setdefault('replay_grace_seconds', 0.05)
        self.rpc = R.SessionRPC(self.token, self.output, handler or self.handler, **kwargs)
        self.rpc.__enter__()
        def run():
            try:
                self.stats = self.rpc.serve(stop=self.done.is_set)
            except BaseException as error:
                self.errors.append(type(error).__name__)
        self.thread = threading.Thread(target=run)
        self.thread.start()
        return self.rpc

    def wire(self, body, headers=None, request_line=b'POST /v1/transition HTTP/1.1\r\n'):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        if headers is None:
            headers = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        return request_line + headers + b'\r\n' + body

    def exchange(self, request, *, raw=False):
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(4)
        connection.connect(str(self.rpc.socket_path))
        with connection:
            connection.sendall(request if raw else self.wire(request))
            parts = []
            while True:
                part = connection.recv(16385)
                if not part:
                    break
                parts.append(part)
        header, body = b''.join(parts).split(b'\r\n\r\n', 1)
        self.assertIn(b'Content-Type: application/json', header)
        self.assertLessEqual(len(body), 16384)
        return int(header.split(b' ', 2)[1]), json.loads(body)

    def stop(self):
        self.done.set()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.errors, [])

    def test_private_socket_same_uid_and_durable_replay(self):
        self.start()
        self.assertEqual(stat.S_IMODE(self.rpc.socket_directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.rpc.socket_path.stat().st_mode), 0o600)
        request = self.request()
        status, result = self.exchange(request)
        self.assertEqual(status, 200)
        self.assertEqual(result['result'], 'verified')
        self.assertEqual(self.exchange(request), (status, result))
        self.assertEqual(self.calls, [('chill', request['request_id'])])
        journal = json.loads(self.rpc.journal_path.read_text())
        self.assertEqual(journal['requests'][0]['response'], result)
        self.assertEqual(journal['replies'], [result, result])
        self.assertEqual(stat.S_IMODE(self.rpc.journal_path.stat().st_mode), 0o600)
        self.stop()
        self.rpc.close()
        self.assertFalse(self.rpc.socket_directory.exists())

    def test_same_id_different_action_cannot_execute_or_overwrite(self):
        self.start()
        request = self.request()
        _, original = self.exchange(request)
        status, refused = self.exchange({**request, 'action': 'wake'})
        self.assertEqual((status, refused['result']), (409, 'unknown'))
        self.assertEqual(self.exchange(request), (200, original))
        self.assertEqual(len(self.calls), 1)
        journal = json.loads(self.rpc.journal_path.read_text())
        self.assertEqual(journal['requests'][0]['response'], original)
        self.assertEqual(journal['replies'][1], refused)

    def test_disconnect_does_not_cancel_handler_and_retry_replays(self):
        entered = threading.Event()
        def handler(action, request_id):
            self.calls.append((action, request_id))
            entered.set()
            self.release.wait(3)
            return self.response(action, request_id)
        self.start(handler)
        request = self.request()
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.rpc.socket_path))
            connection.sendall(self.wire(request))
        self.assertTrue(entered.wait(2))
        self.release.set()
        deadline = time.monotonic() + 2
        while self.rpc._active and time.monotonic() < deadline:
            time.sleep(0.01)
        status, response = self.exchange(request)
        self.assertEqual((status, response['result']), (200, 'verified'))
        self.assertEqual(len(self.calls), 1)

    def test_serial_handler_busy_and_pending_retry_are_not_reexecuted(self):
        entered = threading.Event()
        def handler(action, request_id):
            self.calls.append((action, request_id))
            entered.set()
            self.release.wait(3)
            return self.response(action, request_id)
        self.start(handler)
        first, second = self.request(), self.request('wake')
        received = []
        sender = threading.Thread(target=lambda: received.append(self.exchange(first)))
        sender.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.exchange(first)[1]['result'], 'unknown')
            busy = self.exchange(second)
            self.assertEqual((busy[0], busy[1]['result']), (409, 'busy'))
        finally:
            self.release.set()
            sender.join(3)
        self.assertEqual(received[0][0], 200)
        self.assertEqual(self.exchange(second), busy)
        self.assertEqual(len(self.calls), 1)

    def test_ttl_stops_admission_but_drains_admitted_handler(self):
        entered = threading.Event()
        def handler(action, request_id):
            entered.set()
            self.release.wait(3)
            return self.response(action, request_id)
        self.start(handler, ttl_seconds=0.05)
        result = []
        sender = threading.Thread(target=lambda: result.append(self.exchange(self.request())))
        sender.start()
        try:
            self.assertTrue(entered.wait(2))
            time.sleep(0.15)
            self.assertTrue(self.thread.is_alive())
        finally:
            self.release.set()
            sender.join(3)
        self.thread.join(3)
        self.assertEqual(self.stats, {'reason': 'ttl', 'requests': 1, 'completed': 1,
                                     'result': 'verified', 'error_codes': []})
        self.assertEqual(result[0][0], 200)

    def test_request_bound_counts_even_bad_requests(self):
        self.start(max_requests=1)
        self.assertEqual(self.exchange(b'{}', raw=False)[0], 400)
        self.thread.join(3)
        self.assertEqual(self.stats, {'reason': 'request_limit', 'requests': 1, 'completed': 0,
                                     'result': 'verified', 'error_codes': []})
        self.assertEqual(self.calls, [])

    def test_stop_after_handler_keeps_durable_response_and_send(self):
        def handler(action, request_id):
            self.done.set()
            return self.response(action, request_id)
        self.start(handler)
        status, response = self.exchange(self.request('wake'))
        self.thread.join(3)
        self.assertEqual(status, 200)
        self.assertEqual(self.stats['reason'], 'stopped')
        self.assertEqual(json.loads(self.rpc.journal_path.read_text())['replies'], [response])

    def test_handler_exception_never_leaks_message_or_runs_again(self):
        def handler(action, request_id):
            self.calls.append((action, request_id))
            raise RuntimeError('secret-token-do-not-save')
        self.start(handler)
        request = self.request()
        status, result = self.exchange(request)
        self.assertEqual((status, result['result']), (409, 'unknown'))
        self.assertEqual(self.exchange(request), (status, result))
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('secret-token', self.rpc.journal_path.read_text())
        self.stop()
        self.assertEqual(self.stats['result'], 'failed')

    def test_invalid_handler_success_is_unknown_without_payload_leak(self):
        def handler(action, request_id):
            result = self.response(action, request_id)
            result['after']['replicas'] = 1
            result['secret'] = 'private-data'
            return result
        self.start(handler)
        status, result = self.exchange(self.request())
        self.assertEqual((status, result['result']), (409, 'unknown'))
        self.assertNotIn('private-data', self.rpc.journal_path.read_text())

    def test_failed_intent_write_prevents_handler(self):
        self.start()
        with mock.patch.object(self.rpc, '_persist', side_effect=OSError('sensitive path')):
            status, result = self.exchange(self.request())
        self.assertEqual((status, result['result']), (409, 'unknown'))
        self.assertEqual(self.calls, [])

    def test_failed_response_write_cannot_return_success(self):
        self.start()
        original = self.rpc._persist
        def persist():
            if self.calls:
                raise OSError('sensitive path')
            original()
        with mock.patch.object(self.rpc, '_persist', side_effect=persist):
            status, result = self.exchange(self.request())
        self.assertEqual((status, result['result']), (409, 'unknown'))
        saved = json.loads(self.rpc.journal_path.read_text())
        self.assertEqual(saved['requests'][0]['state'], 'pending')
        self.assertIsNone(saved['requests'][0]['response'])
        self.assertEqual(len(self.calls), 1)
        self.thread.join(3)
        self.assertEqual(self.stats['result'], 'failed')
        self.assertIn('JOURNAL_WRITE_FAILED', self.stats['error_codes'])

    def test_lost_wake_reply_replays_during_grace_but_new_ids_cannot_run(self):
        finished = threading.Event()
        def handler(action, request_id):
            self.calls.append((action, request_id))
            self.done.set()
            finished.set()
            return self.response(action, request_id)
        self.start(handler, replay_grace_seconds=0.4)
        request = self.request('wake')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.rpc.socket_path))
            connection.sendall(self.wire(request))
        self.assertTrue(finished.wait(2))
        deadline = time.monotonic() + 1
        while self.rpc._active and time.monotonic() < deadline:
            time.sleep(0.005)
        status, response = self.exchange(request)
        self.assertEqual((status, response['result']), (200, 'verified'))
        status, rejected = self.exchange(self.request('chill'))
        self.assertEqual((status, rejected['result']), (409, 'unknown'))
        self.assertEqual(len(self.calls), 1)
        self.thread.join(3)
        self.assertEqual(self.stats['result'], 'verified')
        self.assertEqual(self.stats['reason'], 'stopped')

    def test_http_headers_and_envelope_fail_before_handler(self):
        self.start()
        body = json.dumps(self.request()).encode()
        length = str(len(body)).encode()
        invalid = [
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\nTransfer-Encoding: identity\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\ncontent-length: '+length+b'\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: 4097\r\n'),
            self.wire(body, b'Content-Length: '+length+b'\r\n'),
            self.wire(body, request_line=b'POST /v1/other HTTP/1.1\r\n'),
            self.wire(body, request_line=b'GET /v1/transition HTTP/1.1\r\n'),
        ]
        for request in invalid:
            with self.subTest(request=request[:60]):
                self.assertEqual(self.exchange(request, raw=True)[0], 400)
        self.assertEqual(self.calls, [])

    def test_slow_partial_request_has_absolute_deadline(self):
        self.start()
        with mock.patch.object(R, 'READ_TIMEOUT_SECONDS', 0.15):
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(2)
                connection.connect(str(self.rpc.socket_path))
                started = time.monotonic()
                connection.sendall(b'POST ')
                time.sleep(0.09)
                connection.sendall(b'/v1/')
                reply = connection.recv(16384)
                self.assertIn(b'400 Bad Request', reply)
                self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.calls, [])

    def test_json_exact_keys_types_duplicates_and_trailing_data(self):
        valid = self.request()
        invalid = [{**valid, 'sql': 'DROP anything'}, {**valid, 'schema_version': True},
                   {**valid, 'schema_version': 1.0}, {**valid, 'warehouse': 'S'},
                   {**valid, 'session_id': '0' * 32}, {**valid, 'action': 'delete'},
                   {**valid, 'request_id': valid['request_id'].replace('-', '')}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(R.RPCError):
                R.parse_request(json.dumps(value).encode(), self.token)
        raw = json.dumps(valid).encode()
        for value in [raw + b' {}', raw[:-1] + b',"action":"wake"}', b'{"schema_version":NaN}', b'[]']:
            with self.subTest(value=value), self.assertRaises(R.RPCError):
                R.parse_request(value, self.token)

    def test_success_requires_valid_receipt_and_transition_not_boolean_replicas(self):
        request = self.request()
        response = self.response('chill', request['request_id'])
        self.assertEqual(R.validate_response(response, request), response)
        variants = []
        for key, value in [('operation_id', None), ('receipt_sha256', None), ('receipt_sha256', 'A'*64),
                           ('schema_version', True), ('request_id', str(uuid.uuid4()))]:
            item = deepcopy(response); item[key] = value; variants.append(item)
        for state, key, value in [('after', 'replicas', False), ('before', 'replicas', None),
                                  ('after', 'backend_active', True), ('before', 'backend_active', 1)]:
            item = deepcopy(response); item[state][key] = value; variants.append(item)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(R.RPCError):
                R.validate_response(item, request)
        noop = self.response('chill', request['request_id'], 'noop')
        self.assertEqual(R.validate_response(noop, request), noop)

    def test_peer_identity_mismatch_never_dispatches(self):
        self.start()
        with mock.patch.object(R, 'peer_uid', return_value=os.getuid() + 1):
            self.assertEqual(self.exchange(self.request())[0], 400)
        self.assertEqual(self.calls, [])

    def test_socket_directory_collision_is_not_adopted(self):
        directory = R.SOCKET_ROOT / ('shirokuma-wl-' + self.token)
        directory.mkdir(mode=0o700)
        try:
            rpc = R.SessionRPC(self.token, self.output, self.handler)
            with self.assertRaisesRegex(R.RPCError, 'SOCKET_COLLISION'):
                rpc.__enter__()
            self.assertTrue(directory.exists())
        finally:
            directory.rmdir()

    def test_socket_inode_replacement_refuses_cleanup(self):
        self.start()
        self.stop()
        path = self.rpc.socket_path
        original = path.with_name('original.sock')
        path.rename(original)
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(path)); path.chmod(0o600)
        try:
            with self.assertRaisesRegex(R.RPCError, 'SOCKET_IDENTITY_CHANGED'):
                self.rpc.close()
            self.assertTrue(path.exists())
        finally:
            replacement.close(); path.unlink(); original.rename(path)

    def test_symlink_output_and_socket_directory_are_rejected(self):
        link = self.output / 'link'
        link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(R.RPCError):
            R.SessionRPC(self.token, link, self.handler).__enter__()
        directory = R.SOCKET_ROOT / ('shirokuma-wl-' + self.token)
        directory.symlink_to(self.output, target_is_directory=True)
        try:
            with self.assertRaisesRegex(R.RPCError, 'SOCKET_COLLISION'):
                R.SessionRPC(self.token, self.output, self.handler).__enter__()
        finally:
            directory.unlink()


if __name__ == '__main__':
    unittest.main()
