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
from tools.local_experiment.warehouse_lifecycle import status as S


class SessionRPCTest(unittest.TestCase):
    def test_one_shot_dispatch_stop_cannot_admit_later_new_uuid(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler,
                                replay_grace_seconds=0.05)
        self.rpc.__enter__()
        checks = 0
        def one_shot_stop():
            nonlocal checks
            checks += 1
            return checks == 1
        self.rpc._stop_callback = one_shot_stop
        first, second = self.request(), self.request()
        self.assertEqual(self.rpc._dispatch(first)['result'], 'unknown')
        self.assertIsNotNone(self.rpc._replay_until)
        self.assertEqual(self.rpc._dispatch(second)['result'], 'unknown')
        self.assertEqual(checks, 1)
        self.assertEqual(self.calls, [])
        self.assertEqual([entry['state'] for entry in self.rpc._entries.values()],
                         ['rejected', 'rejected'])
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual(len(saved['requests']), 2)
        self.assertTrue(all(item['state'] == 'rejected' for item in saved['requests']))

    def test_isolated_termination_boundaries_leave_unknown_saved_intent(self):
        # Simulated process termination is injected before dispatch can write
        # the next journal state; never feed either saved UUID to a new owner.
        for boundary in ('after_intent', 'after_handler'):
            with self.subTest(boundary=boundary):
                self.rpc = R.SessionRPC(self.token, self.output, self.handler)
                self.rpc.__enter__()
                request = self.request(request_id=str(uuid.uuid4()))
                original = self.rpc._persist
                writes = 0
                def interrupt():
                    nonlocal writes
                    writes += 1
                    original()
                    if writes == (1 if boundary == 'after_intent' else 2):
                        raise KeyboardInterrupt('simulated termination')
                with mock.patch.object(self.rpc, '_persist', side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        self.rpc._dispatch(request)
                saved = S.project(self.rpc.journal_path.read_bytes(), self.token, request['request_id'])
                self.assertEqual(saved['request_lookup']['recorded_result'],
                                 'pending' if boundary == 'after_intent' else 'verified')
                self.assertEqual(saved['reply_count'], 0)
                self.assertEqual(saved['runtime_state'], 'unknown')
                self.assertEqual(len(self.calls), 0 if boundary == 'after_intent' else 1)
                self.rpc.close()
                directory = self.rpc.socket_directory
                self.assertEqual(list(directory.iterdir()), [])
                directory.rmdir()  # Only the test-owned empty directory.
                self.rpc = None
                # New disposable identity for the next boundary, never replay
                # the interrupted request or reuse its journal directory.
                self.token = uuid.uuid4().hex
                self.output = Path(self.temp.name) / self.token
                self.output.mkdir(mode=0o700)
                self.calls.clear()

    def test_recorded_reply_before_send_does_not_allow_session_restart(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        request = self.request()
        server, client = socket.socketpair()
        with client, mock.patch.object(R, 'peer_uid', return_value=os.getuid()), \
                mock.patch.object(self.rpc, '_read_request', return_value=request), \
                mock.patch.object(self.rpc, '_send', side_effect=KeyboardInterrupt('before send')):
            with self.assertRaises(KeyboardInterrupt):
                self.rpc._connection(server)
        saved = S.project(self.rpc.journal_path.read_bytes(), self.token, request['request_id'])
        self.assertEqual(saved['request_lookup']['recorded_result'], 'verified')
        self.assertEqual(saved['reply_count'], 1)
        self.assertEqual(saved['runtime_state'], 'unknown')
        self.assertEqual(self.calls, [('chill', request['request_id'])])
        # Do not remove the original journal or replay the UUID. Even though
        # the socket has closed, a fresh owner cannot adopt this session ID.
        with self.assertRaisesRegex(R.RPCError, 'JOURNAL_COLLISION'):
            R.SessionRPC(self.token, self.output, self.handler).__enter__()
        self.assertEqual(self.calls, [('chill', request['request_id'])])
        self.assertEqual(S.project(self.rpc.journal_path.read_bytes(), self.token, request['request_id']), saved)

    def test_failing_stop_callback_rejects_new_request_without_dispatch(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        request = self.request()
        def broken_stop():
            raise RuntimeError('disposable callback failure')
        self.rpc._stop_callback = broken_stop
        response = self.rpc._dispatch(request)
        self.assertEqual(response['result'], 'unknown')
        self.assertEqual(self.calls, [])
        saved = S.project(self.rpc.journal_path.read_bytes(), self.token, request['request_id'])
        self.assertEqual(saved['request_lookup']['recorded_result'], 'unknown')
        self.assertEqual(self.rpc._entries[request['request_id']]['state'], 'rejected')
        self.assertIn('STOP_CALLBACK_FAILED', self.rpc._error_codes)

    def test_failing_stop_callback_preserves_completed_id_without_new_dispatch(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.request()
        completed = self.rpc._dispatch(original)
        self.assertEqual(completed['result'], 'verified')
        def broken_stop():
            raise RuntimeError('disposable callback failure')
        self.rpc._stop_callback = broken_stop
        fresh = self.request()
        self.assertEqual(self.rpc._dispatch(fresh)['result'], 'unknown')
        self.assertEqual(self.rpc._dispatch(original), completed)
        self.assertEqual(self.calls, [('chill', original['request_id'])])
        self.assertIn('STOP_CALLBACK_FAILED', self.rpc._error_codes)
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual([entry['state'] for entry in saved['requests']], ['completed', 'rejected'])

    def test_failed_owner_cannot_admit_waiting_worker_after_callback_removed(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.request()
        completed = self.rpc._dispatch(original)

        def broken_stop():
            raise RuntimeError('disposable callback failure')

        self.rpc._stop_callback = broken_stop
        self.assertEqual(self.rpc._dispatch(self.request())['result'], 'unknown')
        self.assertTrue(self.rpc._stop.is_set())
        # A worker already accepted by serve may reach dispatch after the
        # callback is removed; the stopped owner must not run its handler.
        self.rpc._stop_callback = None
        late = self.request()
        self.assertEqual(self.rpc._dispatch(late)['result'], 'unknown')
        self.assertEqual(self.rpc._dispatch(original), completed)
        self.assertEqual(self.calls, [('chill', original['request_id'])])
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual([entry['state'] for entry in saved['requests']],
                          ['completed', 'rejected', 'rejected'])

    def test_stopped_owner_does_not_invoke_callback_for_waiting_worker(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.request()
        completed = self.rpc._dispatch(original)
        self.rpc._stop.set()
        checks = []
        def unexpected_stop_check():
            checks.append(True)
            raise RuntimeError('callback should not run after owner stopped')
        self.rpc._stop_callback = unexpected_stop_check
        fresh = self.request()
        self.assertEqual(self.rpc._dispatch(fresh)['result'], 'unknown')
        self.assertEqual(self.rpc._dispatch(original), completed)
        self.assertEqual(checks, [])
        self.assertEqual(self.calls, [('chill', original['request_id'])])
        self.assertNotIn('STOP_CALLBACK_FAILED', self.rpc._error_codes)
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual([entry['state'] for entry in saved['requests']], ['completed', 'rejected'])

    def test_expired_owner_does_not_invoke_callback_for_waiting_worker(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        self.rpc._started = time.monotonic() - self.rpc.ttl_seconds - 1
        checks = []
        def unexpected_stop_check():
            checks.append(True)
            raise RuntimeError('callback should not run after TTL')
        self.rpc._stop_callback = unexpected_stop_check
        request = self.request()
        self.assertEqual(self.rpc._dispatch(request)['result'], 'unknown')
        self.assertEqual(checks, [])
        self.assertEqual(self.calls, [])
        self.assertNotIn('STOP_CALLBACK_FAILED', self.rpc._error_codes)
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual(saved['requests'][0]['state'], 'rejected')

    def test_owner_expiring_during_stop_check_rejects_waiting_worker(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        checks = []

        def expiring_stop_check():
            checks.append(True)
            self.rpc._started = time.monotonic() - self.rpc.ttl_seconds - 1
            return False

        self.rpc._stop_callback = expiring_stop_check
        request = self.request()
        self.assertEqual(self.rpc._dispatch(request)['result'], 'unknown')
        self.assertEqual(checks, [True])
        self.assertEqual(self.calls, [])
        self.assertNotIn('STOP_CALLBACK_FAILED', self.rpc._error_codes)
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual(saved['requests'][0]['state'], 'rejected')

    def test_owner_stopping_during_false_stop_check_rejects_waiting_worker(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        checks = []

        def stopping_check():
            checks.append(True)
            self.rpc._stop.set()
            return False

        self.rpc._stop_callback = stopping_check
        request = self.request()
        self.assertEqual(self.rpc._dispatch(request)['result'], 'unknown')
        self.assertEqual(checks, [True])
        self.assertEqual(self.calls, [])
        self.assertNotIn('STOP_CALLBACK_FAILED', self.rpc._error_codes)
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual(saved['requests'][0]['state'], 'rejected')

    def test_stop_check_raising_after_stopping_owner_preserves_completed_replay(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.request()
        completed = self.rpc._dispatch(original)
        checks = []

        def stopping_failure():
            checks.append(True)
            self.rpc._stop.set()
            raise RuntimeError('disposable stop failure')

        self.rpc._stop_callback = stopping_failure
        fresh = self.request()
        self.assertEqual(self.rpc._dispatch(fresh)['result'], 'unknown')
        self.assertEqual(self.rpc._dispatch(original), completed)
        self.assertEqual(self.rpc._dispatch(self.request())['result'], 'unknown')
        self.assertEqual(checks, [True])
        self.assertEqual(self.calls, [('chill', original['request_id'])])
        self.assertEqual(self.rpc._error_codes.count('STOP_CALLBACK_FAILED'), 1)
        saved = json.loads(self.rpc.journal_path.read_bytes())
        self.assertEqual([entry['state'] for entry in saved['requests']],
                         ['completed', 'rejected', 'rejected'])

    def test_failing_serve_stop_callback_returns_failed_without_dispatch(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        def broken_stop():
            raise RuntimeError('disposable callback failure')
        result = self.rpc.serve(stop=broken_stop)
        self.assertEqual(result['result'], 'failed')
        self.assertIn('STOP_CALLBACK_FAILED', result['error_codes'])
        self.assertEqual(result['requests'], 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_serve_stop_event_set_during_false_callback_never_accepts(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        checks = []

        def stopping_check():
            checks.append(True)
            self.rpc._stop.set()
            return False

        with mock.patch.object(socket.socket, 'accept', side_effect=AssertionError('accepted after stop')):
            result = self.rpc.serve(stop=stopping_check)
        self.assertEqual(checks, [True])
        self.assertEqual(result['requests'], 0)
        self.assertEqual(self.calls, [])

    def test_serve_ttl_expiring_during_false_callback_never_accepts(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        checks = []

        def expiring_check():
            checks.append(True)
            self.rpc._started = time.monotonic() - self.rpc.ttl_seconds - 1
            return False

        with mock.patch.object(socket.socket, 'accept', side_effect=AssertionError('accepted after TTL')):
            result = self.rpc.serve(stop=expiring_check)
        self.assertEqual(checks, [True])
        self.assertEqual(result['reason'], 'ttl')
        self.assertEqual(result['requests'], 0)
        self.assertEqual(self.calls, [])

    def test_serve_clock_advancing_during_false_callback_never_accepts(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        clock = [self.rpc._started + self.rpc.ttl_seconds - 0.1]

        def expiring_check():
            clock[0] += 0.2
            return False

        with mock.patch.object(R.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(socket.socket, 'accept', side_effect=AssertionError('accepted after TTL')):
            result = self.rpc.serve(stop=expiring_check)
        self.assertEqual(result['reason'], 'ttl')
        self.assertEqual(result['requests'], 0)
        self.assertEqual(self.calls, [])

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
            directory = self.rpc.socket_directory
            state = directory.lstat()
            self.assertEqual((state.st_dev, state.st_ino), self.rpc._directory_identity)
            self.assertEqual(list(directory.iterdir()), [])
            directory.rmdir()  # Test-owned disposable directory, not the owner contract.
        self.temp.cleanup()

    def request(self, action='chill', request_id=None):
        return {'schema_version': 1, 'session_id': self.token,
                'request_id': request_id or str(uuid.uuid4()), 'warehouse': 'XS', 'action': action}

    def response(self, action, request_id, result='verified'):
        request = self.request(action, request_id)
        target = deepcopy(R.TARGETS['wake' if action == 'query' else action])
        prior = deepcopy(R.TARGETS['wake' if action == 'chill' else 'chill'])
        value = {**request, 'result': result, 'before': target if result == 'noop' else prior,
                'after': target, 'operation_id': str(uuid.uuid4()), 'receipt_sha256': 'a' * 64}
        if action == 'query':
            value['query'] = {'case': 'xs_caller', 'query_id': '20260918_010203_00001_abcde',
                'rows_observed': 25, 'rows_sha256': 'b' * 64, 'post_count': 1,
                'response_complete': True, 'matched': True} if result == 'verified' else None
        return value

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

    def test_journal_directory_swap_after_open_never_writes_into_replacement(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.output / 'original-journal'
        replacement = self.output / 'replacement'
        replacement.mkdir(mode=0o700)
        actual_open = os.open
        swapped = False

        def swap_after_open(path, flags, *args, **kwargs):
            nonlocal swapped
            descriptor = actual_open(path, flags, *args, **kwargs)
            if not swapped and str(path) == str(self.rpc.journal_directory):
                swapped = True
                self.rpc.journal_directory.rename(original)
                self.rpc.journal_directory.symlink_to(replacement, target_is_directory=True)
            return descriptor

        self.rpc._error_codes.append('TEST_MARKER')
        with mock.patch.object(R.os, 'open', side_effect=swap_after_open):
            with self.assertRaisesRegex(R.RPCError, 'UNSAFE_PATH|JOURNAL_IDENTITY_CHANGED'):
                self.rpc._persist()
        self.assertTrue(swapped)
        self.assertEqual(list(replacement.iterdir()), [])
        self.assertEqual(json.loads((original / 'journal.json').read_text())['error_codes'], ['TEST_MARKER'])

    def test_journal_directory_swap_before_open_does_not_write(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.output / 'original-journal'
        replacement = self.output / 'replacement'
        replacement.mkdir(mode=0o700)
        before = self.rpc.journal_path.read_bytes()
        actual_open = os.open
        swapped = False

        def swap_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and str(path) == str(self.rpc.journal_directory):
                swapped = True
                self.rpc.journal_directory.rename(original)
                self.rpc.journal_directory.symlink_to(replacement, target_is_directory=True)
            return actual_open(path, flags, *args, **kwargs)

        self.rpc._error_codes.append('TEST_MARKER')
        with mock.patch.object(R.os, 'open', side_effect=swap_before_open):
            with self.assertRaisesRegex(R.RPCError, 'JOURNAL_IDENTITY_CHANGED'):
                self.rpc._persist()
        self.assertTrue(swapped)
        self.assertEqual((original / 'journal.json').read_bytes(), before)
        self.assertEqual(list(replacement.iterdir()), [])

    def test_failed_journal_replace_does_not_delete_a_replaced_temp(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        self.rpc.__enter__()
        original = self.rpc.journal_path.read_bytes()
        replacement = b'OTHER ACTOR FILE'
        replaced_name = []
        actual_unlink = os.unlink

        def replace_with_other_file(source, target, *, src_dir_fd, dst_dir_fd):
            self.assertEqual(target, 'journal.json')
            self.assertEqual(src_dir_fd, dst_dir_fd)
            actual_unlink(source, dir_fd=src_dir_fd)
            with os.fdopen(os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                   0o600, dir_fd=src_dir_fd), 'wb') as stream:
                stream.write(replacement)
            replaced_name.append(source)
            raise OSError('injected replace failure')

        with mock.patch.object(R.os, 'replace', side_effect=replace_with_other_file):
            with self.assertRaises(OSError):
                self.rpc._persist()
        self.assertEqual(self.rpc.journal_path.read_bytes(), original)
        self.assertEqual((self.rpc.journal_directory / replaced_name[0]).read_bytes(), replacement)

    def wire(self, body, headers=None, request_line=b'POST /v1/transition HTTP/1.1\r\n'):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        if headers is None:
            headers = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        return request_line + headers + b'\r\n' + body

    def exchange(self, request, *, raw=False, allow_early_reject=False):
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(4)
        connection.connect(str(self.rpc.socket_path))
        with connection:
            try:
                connection.sendall(request if raw else self.wire(request))
            except BrokenPipeError:
                # A rejected peer may close before the client finishes writing.
                # Still require and validate the server's complete 400 response.
                if not allow_early_reject:
                    raise
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

    def test_query_is_opt_in_and_cannot_add_sql_or_case_input(self):
        request = self.request('query')
        with self.assertRaises(R.RPCError):
            R.parse_request(json.dumps(request).encode(), self.token)
        self.assertEqual(R.parse_request(json.dumps(request).encode(), self.token, allow_query=True), request)
        for key in ('sql', 'case', 'url', 'headers'):
            with self.assertRaises(R.RPCError):
                R.parse_request(json.dumps({**request, key: 'PRIVATE'}).encode(), self.token, allow_query=True)
        with self.assertRaises(R.RPCError):
            R.SessionRPC(self.token, self.output, self.handler, allow_query=1)
        self.start()
        self.assertEqual(self.exchange(request)[0], 400)
        self.assertEqual(self.calls, [])

    def test_query_proof_is_exact_complete_and_has_one_post_without_noop(self):
        request = self.request('query')
        response = self.response('query', request['request_id'])
        self.assertEqual(R.validate_response(response, request), response)
        variants = []
        for key, bad in [('case', 'xs_after'), ('query_id', 'bad'), ('rows_observed', 24),
                         ('rows_observed', True), ('rows_sha256', 'x' * 64), ('post_count', 2),
                         ('post_count', True), ('response_complete', 1), ('matched', False)]:
            value = deepcopy(response); value['query'][key] = bad; variants.append(value)
        for change in ({'result': 'noop'}, {'query': None}, {'before': R.TARGETS['wake']},
                       {'operation_id': None}, {'receipt_sha256': None}):
            variants.append({**response, **change})
        value = deepcopy(response); value['query']['sql'] = 'PRIVATE'; variants.append(value)
        value = deepcopy(response); del value['query']['matched']; variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(R.RPCError):
                R.validate_response(value, request)
        for result in ('busy', 'unknown', 'failed'):
            valid = R._uncertain(request, result)
            self.assertIsNone(valid['query'])
            self.assertEqual(R.validate_response(valid, request), valid)
            with self.assertRaises(R.RPCError):
                R.validate_response({**valid, 'query': response['query']}, request)

    def test_query_same_id_returns_saved_result_and_cross_action_is_not_reused(self):
        self.start(allow_query=True)
        request = self.request('query')
        answer = self.exchange(request)
        self.assertEqual(answer[0], 200)
        self.assertEqual(answer[1]['query']['post_count'], 1)
        self.assertEqual(self.exchange(request), answer)
        status, wrong = self.exchange({**request, 'action': 'wake'})
        self.assertEqual((status, wrong['result']), (409, 'unknown'))
        self.assertNotIn('query', wrong)
        self.assertEqual(self.exchange(request), answer)
        self.assertEqual(self.calls, [('query', request['request_id'])])
        saved = json.loads(self.rpc.journal_path.read_text())
        self.assertEqual(saved['requests'][0]['response'], answer[1])
        self.assertEqual(S.project(self.rpc.journal_path.read_bytes(),self.token,request['request_id'])
                         ['request_lookup']['recorded_result'],'verified')

    def test_query_disconnect_and_inflight_retry_do_not_repeat_owner_call(self):
        entered = threading.Event()
        def handler(action, request_id):
            self.calls.append((action, request_id)); entered.set()
            # Hold the disposable handler until both retries have been read;
            # otherwise a busy suite can expire the fixture before inspection.
            self.release.wait()
            return self.response(action, request_id)
        self.start(handler, allow_query=True)
        request = self.request('query')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.rpc.socket_path)); connection.sendall(self.wire(request))
        try:
            self.assertTrue(entered.wait(2))
            status, pending = self.exchange(request)
            self.assertEqual((status, pending.get('result'), pending.get('request_id'), pending.get('query')),
                             (409, 'unknown', request['request_id'], None))
            competing = self.request('query')
            busy = self.exchange(competing)
            self.assertEqual((busy[0], busy[1].get('result'), busy[1].get('request_id'), busy[1].get('query')),
                             (409, 'busy', competing['request_id'], None))
        finally:
            self.release.set()
        deadline = time.monotonic() + 2
        while self.rpc._active and time.monotonic() < deadline: time.sleep(0.01)
        self.assertEqual(self.exchange(request)[0], 200)
        self.assertEqual(self.exchange(competing), busy)
        self.assertEqual(len(self.calls), 1)

    def test_query_done_grace_replays_only_saved_identity_then_closes(self):
        def handler(action, request_id):
            self.calls.append((action, request_id)); self.done.set()
            return self.response(action, request_id)
        self.start(handler, allow_query=True, replay_grace_seconds=0.3)
        request = self.request('query'); answer = self.exchange(request)
        self.assertEqual(self.exchange(request), answer)
        refused = self.exchange(self.request('query'))
        self.assertEqual((refused[0], refused[1]['result'], refused[1]['query']), (409, 'unknown', None))
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive()); self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.stats['result'], 'verified')

    def test_query_invalid_owner_proof_is_unknown_durable_and_never_reexecuted(self):
        def handler(action, request_id):
            self.calls.append((action, request_id))
            response = self.response(action, request_id); response['query']['post_count'] = 2
            return response
        self.start(handler, allow_query=True)
        request = self.request('query'); answer = self.exchange(request)
        self.assertEqual((answer[0], answer[1]['result'], answer[1]['query']), (409, 'unknown', None))
        self.assertEqual(self.exchange(request), answer); self.assertEqual(len(self.calls), 1)
        self.stop(); self.assertEqual(self.stats['result'], 'failed')

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
        self.assertTrue(self.rpc.socket_directory.is_dir())
        self.assertEqual(list(self.rpc.socket_directory.iterdir()), [])

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
        projected=S.project(self.rpc.journal_path.read_bytes(),self.token,request['request_id'])
        self.assertEqual(projected['reply_count'],3)
        self.assertEqual(projected['request_lookup']['recorded_action'],'chill')

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
            # Keep the first request pending until both concurrent responses
            # are observed; a wall-clock wait can expire under suite load.
            self.release.wait()
            return self.response(action, request_id)
        self.start(handler)
        first, second = self.request(), self.request('wake')
        received = []
        sender = threading.Thread(target=lambda: received.append(self.exchange(first)))
        sender.start()
        try:
            self.assertTrue(entered.wait(2))
            pending = self.exchange(first)
            self.assertEqual((pending[0], pending[1].get('result'), pending[1].get('request_id')),
                             (409, 'unknown', first['request_id']))
            busy = self.exchange(second)
            self.assertEqual((busy[0], busy[1].get('result'), busy[1].get('request_id')),
                             (409, 'busy', second['request_id']))
        finally:
            self.release.set()
            sender.join(3)
        self.assertFalse(sender.is_alive(), 'first caller did not finish after handler release')
        self.assertEqual(len(received), 1)
        self.assertEqual((received[0][0], received[0][1].get('result'),
                          received[0][1].get('request_id'), received[0][1].get('action')),
                         (200, 'verified', first['request_id'], first['action']))
        saved = json.loads(self.rpc.journal_path.read_text())
        self.assertEqual(len(saved['requests']), 2)
        self.assertEqual(saved['requests'][0]['response'], received[0][1])
        self.assertEqual(saved['requests'][1]['request']['request_id'], second['request_id'])
        self.assertEqual(saved['requests'][1]['response'], busy[1])
        self.assertEqual(saved['replies'][:3], [pending[1], busy[1], received[0][1]])
        # The pending retry did not pin an uncertain response: once the
        # original finishes, only this test-owned UUID replays its exact result.
        self.assertEqual(self.exchange(first), received[0])
        self.assertEqual(self.exchange(second), busy)
        self.assertEqual(len(self.calls), 1)
        replayed = json.loads(self.rpc.journal_path.read_text())
        self.assertEqual(len(replayed['requests']), 2)
        self.assertEqual(replayed['requests'][0]['response'], received[0][1])
        self.assertEqual(replayed['requests'][1]['response'], busy[1])
        self.assertEqual(replayed['replies'], [pending[1], busy[1], received[0][1],
                                               received[0][1], busy[1]])

    def test_ttl_stops_admission_but_drains_admitted_handler(self):
        entered = threading.Event()
        def handler(action, request_id):
            entered.set()
            # TTL expires while the admitted handler remains in flight, not
            # because this fixture's wall-clock wait happened to time out.
            self.release.wait()
            return self.response(action, request_id)
        self.start(handler, ttl_seconds=0.05)
        result = []
        sender = threading.Thread(target=lambda: result.append(self.exchange(self.request())))
        sender.start()
        try:
            self.assertTrue(entered.wait(2))
            time.sleep(0.15)
            self.assertTrue(self.thread.is_alive())
            self.assertEqual(result, [], 'admitted handler completed before explicit release')
        finally:
            self.release.set()
            sender.join(3)
        self.thread.join(3)
        self.assertEqual(self.stats, {'reason': 'ttl', 'requests': 1, 'completed': 1,
                                     'result': 'verified', 'error_codes': []})
        self.assertEqual(result[0][0], 200)

    def test_accepted_connection_dispatched_after_ttl_never_calls_handler(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler, ttl_seconds=1)
        self.rpc.__enter__()
        self.rpc._started = time.monotonic() - 2
        request = self.request()
        response = self.rpc._dispatch(request)
        self.assertEqual((response['result'], response['request_id']),
                         ('unknown', request['request_id']))
        self.assertEqual(self.calls, [])
        saved = S.project(self.rpc.journal_path.read_bytes(), self.token, request['request_id'])
        self.assertEqual(saved['request_lookup']['recorded_result'], 'unknown')
        self.assertEqual(saved['runtime_state'], 'unknown')

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
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\nCONTENT-LENGTH: '+length+b'\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\n\tX-Extra: yes\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\n X-Extra: yes\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: 4097\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length:  '+length+b'\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b' \r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length:\t'+length+b'\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\nX-Extra: yes\x00no\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\nX-Extra: yes\x1fno\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\nX-Extra: yes\x7fno\r\n'),
            self.wire(body, b'Content-Type: application/json\r\nContent-Length: '+length+b'\r\nX-Extra: yes\x80no\r\n'),
            self.wire(body, b'Content-Length: '+length+b'\r\n'),
            self.wire(body, request_line=b'POST /v1/other HTTP/1.1\r\n'),
            self.wire(body, request_line=b'GET /v1/transition HTTP/1.1\r\n'),
        ]
        for request in invalid:
            with self.subTest(request=request[:60]):
                self.assertEqual(self.exchange(request, raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_noncanonical_content_length_never_dispatches(self):
        self.start()
        body = json.dumps(self.request()).encode()
        for length in (b'0' + str(len(body)).encode(), b'00' + str(len(body)).encode()):
            with self.subTest(length=length):
                headers = b'Content-Type: application/json\r\nContent-Length: ' + length + b'\r\n'
                self.assertEqual(self.exchange(self.wire(body, headers), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_invalid_extension_header_names_and_framing_never_dispatch(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        body = json.dumps(self.request()).encode()
        length = str(len(body)).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + length + b'\r\n'
        class BufferedConnection:
            def __init__(self, payload):
                self.payload = payload

            def settimeout(self, timeout):
                pass

            def recv(self, limit):
                payload, self.payload = self.payload, b''
                return payload

        for name in (b'X_Extra', b'X-\x80Extra', b'X-Extra '):
            with self.subTest(name=name), self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(BufferedConnection(self.wire(body, base + name + b': yes\r\n')))
        # Reject protocol negotiation even if its advertised body is already
        # complete and parses as a valid transition. Neither path may journal.
        for extra in (b'Expect: 100-continue\r\n', b'Transfer-Encoding: chunked\r\n',
                       b'Upgrade: websocket\r\n', b'Trailer: X-Digest\r\n',
                       b'TE: trailers\r\n', b'Proxy-Connection: keep-alive\r\n',
                       b'Keep-Alive: timeout=5\r\n',
                       b'Connection: upgrade\r\n', b'Connection: keep-alive, Upgrade\r\n',
                       b'Connection: keep-alive\r\n', b'Connection: X-Trace\r\n'):
            with self.subTest(extra=extra), self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(BufferedConnection(self.wire(body, base + extra)))
        # The Go client uses warehouse.local. An explicit different authority
        # must not label a transition as if it targeted that fixed endpoint.
        for extra in (b'Host: other.local\r\n', b'Host: warehouse.local:80\r\n'):
            with self.subTest(extra=extra), self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(BufferedConnection(self.wire(body, base + extra)))
        # This socket authenticates by its private filesystem scope, not by
        # bearer, proxy or browser credentials supplied in HTTP headers.
        for extra in (b'Authorization: Bearer example\r\n',
                      b'Proxy-Authorization: Basic example\r\n', b'Cookie: session=example\r\n'):
            with self.subTest(extra=extra), self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(BufferedConnection(self.wire(body, base + extra)))
        self.assertEqual(self.rpc._read_request(BufferedConnection(
            self.wire(body, base + b'Host: warehouse.local\r\n'))), json.loads(body))
        self.assertEqual(self.rpc._read_request(BufferedConnection(
            self.wire(body, base + b'Connection: Close\r\n'))), json.loads(body))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_printable_extension_header_remains_compatible_without_duplicate_aliases(self):
        self.start()
        body = json.dumps(self.request()).encode()
        length = str(len(body)).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + length + b'\r\n'
        duplicate = base + b'X-Trace: printable-123\r\nx-trace: another\r\n'
        self.assertEqual(self.exchange(self.wire(body, duplicate), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})
        single = base + b'X-Trace: printable-123\r\n'
        status, response = self.exchange(self.wire(body, single), raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(response['result'], 'verified')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.rpc._entries), 1)

    def test_forwarded_authority_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'Forwarded: host=other.local\r\n',
                        b'Forwarded-Host: other.local\r\n',
                        b'Forwarded-For: 192.0.2.1\r\n',
                        b'X-Forwarded: host=other.local\r\n',
                       b'X-Forwarded-Host: other.local\r\n',
                       b'X-Forwarded-Proto: https\r\n',
                       b'X-Forwarded-For: 192.0.2.1\r\n',
                       b'X-Forwarded-Port: 443\r\n',
                       b'X-Forwarded-Server: other.local\r\n',
                       b'X-Forwarded-Uri: /other\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_proxy_real_identity_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'X-Real: other.local\r\n',
                       b'X-Real-IP: 192.0.2.1\r\n', b'X-Real-Host: other.local\r\n',
                      b'X-Real-Server: other.local\r\n', b'X-Real-URI: /other\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_proxy_chain_metadata_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'Proxy: other.local\r\n', b'Via: 1.1 other.local\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_proxy_extension_namespace_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'Proxy-Host: other.local\r\n', b'Proxy-Status: remote\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_client_ip_headers_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'Client-IP: 192.0.2.1\r\n', b'X-Client-IP: 192.0.2.1\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_x_proxy_namespace_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'X-Proxy: other.local\r\n', b'X-Proxy-User: someone\r\n',
                      b'X-Proxy-Client-IP: 192.0.2.1\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_envoy_identity_and_target_hints_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'X-Envoy: other.local\r\n', b'X-Envoy-Original-Path: /other\r\n',
                      b'X-Envoy-External-Address: 192.0.2.1\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_remote_identity_hints_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'Remote-User: someone\r\n', b'X-Remote-User: someone\r\n',
                      b'X-Remote-Role: admin\r\n', b'X-Remote-Host: other.local\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_rewritten_target_cannot_label_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'X-Original-URL: /v1/other\r\n',
                       b'X-Original-URI: /v1/other\r\n',
                       b'X-Original-Host: other.local\r\n',
                       b'X-Original-Path: /v1/other\r\n',
                       b'X-Original: /v1/other\r\n',
                       b'X-Rewrite-URL: /v1/other\r\n',
                       b'X-Rewrite-URI: /v1/other\r\n',
                       b'X-Rewrite: /v1/other\r\n',
                       b'X-Forwarded-Prefix: /other\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_method_override_cannot_relabel_private_transition(self):
        self.start()
        body = json.dumps(self.request()).encode()
        base = b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'
        for extra in (b'X-HTTP-Method-Override: GET\r\n', b'X-Method-Override: DELETE\r\n',
                      b'X-HTTP-Method: GET\r\n', b'X-Original-Method: DELETE\r\n'):
            with self.subTest(extra=extra):
                self.assertEqual(self.exchange(self.wire(body, base + extra), raw=True)[0], 400)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})

    def test_buffered_bytes_after_declared_body_are_refused(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        body = json.dumps(self.request()).encode()

        class BufferedConnection:
            def __init__(self, payload):
                self.payload = payload

            def settimeout(self, timeout):
                pass

            def recv(self, limit):
                payload, self.payload = self.payload, b''
                return payload

        for extra in (b'\x00', b'POST /v1/transition HTTP/1.1\r\n'):
            with self.subTest(extra=extra), self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(BufferedConnection(self.wire(body) + extra))
        self.assertEqual(self.rpc._entries, {})
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
                second_attempt = time.monotonic()
                closed_before_second_fragment = False
                try:
                    connection.sendall(b'/v1/')
                except (BrokenPipeError, ConnectionResetError):
                    # The absolute deadline may expire while the test thread is
                    # asleep or descheduled; closing before this fragment is safe.
                    closed_before_second_fragment = True
                try:
                    reply = connection.recv(16384)
                except ConnectionResetError:
                    reply = b''
                if reply:
                    self.assertIn(b'400 Bad Request', reply)
                else:
                    self.assertTrue(closed_before_second_fragment)
                # Do not mistake a delayed test-thread wake for a late server.
                self.assertLess(time.monotonic() - started, max(0.5, second_attempt - started + 0.4))
        self.assertEqual(self.calls, [])

    def test_complete_request_arriving_after_read_deadline_is_refused(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        payload = self.wire(json.dumps(self.request()).encode())

        class LateConnection:
            def settimeout(self, timeout):
                self.timeout = timeout

            def recv(self, limit):
                return payload

        with mock.patch.object(R, 'READ_TIMEOUT_SECONDS', 0.1), \
             mock.patch.object(R.time, 'monotonic', side_effect=[100, 100, 100.2]):
            with self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(LateConnection())
        self.assertEqual(self.rpc._entries, {})
        self.assertEqual(self.calls, [])

    def test_request_parsed_after_read_deadline_is_refused(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        payload = self.wire(json.dumps(self.request()).encode())
        clock = [100.0]
        original_parse = R.parse_request

        class Connection:
            def settimeout(self, timeout):
                pass

            def recv(self, limit):
                return payload

        def late_parse(*args, **kwargs):
            request = original_parse(*args, **kwargs)
            clock[0] = 101.0
            return request

        with mock.patch.object(R, 'READ_TIMEOUT_SECONDS', 0.5), \
             mock.patch.object(R.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(R, 'parse_request', side_effect=late_parse):
            with self.assertRaisesRegex(R.RPCError, 'INVALID_HTTP'):
                self.rpc._read_request(Connection())
        self.assertEqual(self.rpc._entries, {})
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
        with mock.patch.object(R, 'peer_uid', return_value=os.getuid() + 1), \
             mock.patch.object(self.rpc, '_read_request', side_effect=AssertionError('unauthorized read')) as reader:
            self.assertEqual(self.exchange(self.request(), allow_early_reject=True)[0], 400)
        reader.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rpc._entries, {})
        self.assertEqual(self.rpc._replies, [])

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

    def test_bind_parent_swap_never_creates_socket_in_decoy(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        directory = self.rpc.socket_directory
        moved = directory.with_name(directory.name + '-moved')
        decoy = self.output / 'decoy-bind'
        decoy.mkdir(mode=0o700)
        original_run = R.subprocess.run
        cwd = os.getcwd()
        swapped = False

        def swap_before_child(*args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                directory.rename(moved)
                directory.symlink_to(decoy, target_is_directory=True)
            return original_run(*args, **kwargs)

        try:
            with mock.patch.object(R.subprocess, 'run', side_effect=swap_before_child):
                with self.assertRaisesRegex(R.RPCError, 'UNSAFE_PATH'):
                    self.rpc.__enter__()
            self.assertTrue(swapped)
            self.assertEqual(os.getcwd(), cwd)
            self.assertEqual(list(decoy.iterdir()), [])
            self.assertTrue((moved / 'control.sock').is_socket())
        finally:
            if directory.is_symlink():
                directory.unlink()
            if moved.exists():
                moved.rename(directory)
            if self.rpc._directory_identity is not None:
                state = directory.lstat()
                self.assertEqual((state.st_dev, state.st_ino), self.rpc._directory_identity)
                if self.rpc.socket_path.is_socket():
                    self.rpc.socket_path.unlink()
                self.assertEqual(list(directory.iterdir()), [])
                directory.rmdir()  # Test-owned disposable directory only.
                self.rpc._directory_identity = None

    def test_bind_child_failure_refuses_without_parent_cwd_change(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        cwd = os.getcwd()
        with mock.patch.object(R.subprocess, 'run', side_effect=OSError('private-path')):
            with self.assertRaisesRegex(R.RPCError, 'SOCKET_BIND_FAILED'):
                self.rpc.__enter__()
        self.assertEqual(os.getcwd(), cwd)
        self.assertEqual(list(self.rpc.socket_directory.iterdir()), [])

    def test_startup_entry_swap_between_reads_does_not_change_other_file(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        decoy = self.output / 'other-file'
        decoy.write_text('keep')
        decoy.chmod(0o644)
        actual_stat = os.stat
        reads = 0

        def swap_before_second_read(path, *args, **kwargs):
            nonlocal reads
            if path == 'control.sock' and kwargs.get('dir_fd') is not None:
                reads += 1
                if reads == 2:
                    self.rpc.socket_path.rename(self.rpc.socket_directory / 'original.sock')
                    self.rpc.socket_path.symlink_to(decoy)
            return actual_stat(path, *args, **kwargs)

        try:
            with mock.patch.object(R.os, 'stat', side_effect=swap_before_second_read):
                with self.assertRaises((R.RPCError, OSError)):
                    self.rpc.__enter__()
            self.assertEqual(reads, 2)
            self.assertEqual(stat.S_IMODE(decoy.stat().st_mode), 0o644)
            self.assertEqual(decoy.read_text(), 'keep')
        finally:
            directory = self.rpc.socket_directory
            if self.rpc._directory_identity is not None:
                state = directory.lstat()
                self.assertEqual((state.st_dev, state.st_ino), self.rpc._directory_identity)
                if self.rpc.socket_path.is_symlink():
                    self.rpc.socket_path.unlink()
                original = directory / 'original.sock'
                if original.is_socket():
                    original.unlink()
                self.assertEqual(list(directory.iterdir()), [])
                directory.rmdir()  # Test-created and identity-checked only.
                self.rpc._directory_identity = None

    def test_startup_socket_entry_swap_never_chmods_other_socket(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        directory = self.rpc.socket_directory
        other = self.output / 'other.sock'
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(other))
        other.chmod(0o644)
        actual_stat = os.stat
        reads = 0

        def replace_before_second_read(path, *args, **kwargs):
            nonlocal reads
            if path == 'control.sock' and kwargs.get('dir_fd') is not None:
                reads += 1
                if reads == 2:
                    self.rpc.socket_path.rename(directory / 'original.sock')
                    other.rename(self.rpc.socket_path)
            return actual_stat(path, *args, **kwargs)

        try:
            with mock.patch.object(R.os, 'stat', side_effect=replace_before_second_read):
                with mock.patch.object(R.os, 'chmod', side_effect=AssertionError('chmod after bind')):
                    with self.assertRaisesRegex(R.RPCError, 'SOCKET_DIRECTORY_NOT_EMPTY'):
                        self.rpc.__enter__()
            self.assertEqual(reads, 2)
            self.assertEqual(stat.S_IMODE(self.rpc.socket_path.lstat().st_mode), 0o644)
        finally:
            replacement.close()
            if self.rpc._directory_identity is not None:
                state = directory.lstat()
                self.assertEqual((state.st_dev, state.st_ino), self.rpc._directory_identity)
                if self.rpc.socket_path.is_socket():
                    self.rpc.socket_path.unlink()
                original = directory / 'original.sock'
                if original.is_socket():
                    original.unlink()
                self.assertEqual(list(directory.iterdir()), [])
                directory.rmdir()  # Test-created and identity-checked only.
                self.rpc._directory_identity = None
            if other.exists():
                other.unlink()

    def test_startup_parent_swap_between_reads_does_not_change_other_socket(self):
        self.rpc = R.SessionRPC(self.token, self.output, self.handler)
        directory = self.rpc.socket_directory
        moved = directory.with_name(directory.name + '-moved')
        decoy = self.output / 'decoy'
        decoy.mkdir(mode=0o700)
        other = decoy / 'control.sock'
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(other))
        other.chmod(0o644)
        actual_stat = os.stat
        reads = 0

        def swap_parent(path, *args, **kwargs):
            nonlocal reads
            if path == 'control.sock' and kwargs.get('dir_fd') is not None:
                reads += 1
                if reads == 2:
                    directory.rename(moved)
                    directory.symlink_to(decoy, target_is_directory=True)
            return actual_stat(path, *args, **kwargs)

        try:
            with mock.patch.object(R.os, 'stat', side_effect=swap_parent):
                with self.assertRaises(R.RPCError):
                    self.rpc.__enter__()
            self.assertEqual(reads, 2)
            self.assertTrue(other.is_socket())
            self.assertEqual(stat.S_IMODE(other.stat().st_mode), 0o644)
        finally:
            replacement.close()
            if directory.is_symlink():
                directory.unlink()
            if moved.exists():
                moved.rename(directory)
            if self.rpc._directory_identity is not None:
                state = directory.lstat()
                self.assertEqual((state.st_dev, state.st_ino), self.rpc._directory_identity)
                if self.rpc.socket_path.is_socket():
                    self.rpc.socket_path.unlink()
                self.assertEqual(list(directory.iterdir()), [])
                directory.rmdir()  # Only this test's own disposable directory.
                self.rpc._directory_identity = None
            if other.exists():
                other.unlink()

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

    def test_socket_parent_swap_after_open_does_not_remove_other_socket(self):
        self.start()
        self.stop()
        directory = self.rpc.socket_directory
        moved = directory.with_name(directory.name + '-moved')
        decoy = self.output / 'decoy'
        decoy.mkdir(mode=0o700)
        other = decoy / 'control.sock'
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(other))
        other.chmod(0o600)
        actual_open = os.open
        swapped = False

        def swap_after_open(path, flags, *args, **kwargs):
            nonlocal swapped
            descriptor = actual_open(path, flags, *args, **kwargs)
            if str(path) == str(directory) and not swapped:
                swapped = True
                directory.rename(moved)
                directory.symlink_to(decoy, target_is_directory=True)
            return descriptor

        try:
            with mock.patch.object(R.os, 'open', side_effect=swap_after_open):
                with self.assertRaises(R.RPCError):
                    self.rpc.close()
            self.assertTrue(swapped)
            self.assertTrue(other.is_socket(), 'replacement socket was deleted')
            self.assertFalse((moved / 'control.sock').exists())
        finally:
            replacement.close()
            if other.exists():
                other.unlink()
            if directory.is_symlink():
                directory.unlink()
            if moved.exists():
                moved.rename(directory)
            if self.rpc.socket_path.exists() and self.rpc._socket_identity is None:
                self.rpc.socket_path.unlink()

    def test_socket_parent_swap_before_open_refuses_cleanup(self):
        self.start()
        self.stop()
        directory = self.rpc.socket_directory
        moved = directory.with_name(directory.name + '-moved')
        decoy = self.output / 'decoy'
        decoy.mkdir(mode=0o700)
        other = decoy / 'control.sock'
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(other))
        other.chmod(0o600)
        actual_open = os.open
        swapped = False

        def swap_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if str(path) == str(directory) and not swapped:
                swapped = True
                directory.rename(moved)
                directory.symlink_to(decoy, target_is_directory=True)
            return actual_open(path, flags, *args, **kwargs)

        try:
            with mock.patch.object(R.os, 'open', side_effect=swap_before_open):
                with self.assertRaisesRegex(R.RPCError, 'SOCKET_DIRECTORY_CHANGED'):
                    self.rpc.close()
            self.assertTrue(swapped)
            self.assertTrue(other.is_socket())
            self.assertTrue((moved / 'control.sock').is_socket())
        finally:
            replacement.close()
            if other.exists():
                other.unlink()
            if directory.is_symlink():
                directory.unlink()
            if moved.exists():
                moved.rename(directory)

    def test_empty_socket_directory_swap_at_rmdir_does_not_delete_other_directory(self):
        self.start()
        self.stop()
        directory = self.rpc.socket_directory
        owned_identity = self.rpc._directory_identity
        moved = directory.with_name(directory.name + '-moved')
        decoy = self.output / 'decoy'
        decoy.mkdir(mode=0o700)
        original_rmdir = Path.rmdir
        swapped = False

        def swap_at_rmdir(path):
            nonlocal swapped
            if path == directory and not swapped:
                swapped = True
                directory.rename(moved)
                decoy.rename(directory)
            return original_rmdir(path)

        try:
            with mock.patch.object(Path, 'rmdir', swap_at_rmdir):
                self.rpc.close()
            self.assertFalse(swapped, 'close attempted an unsafe path-based rmdir')
            self.assertTrue(decoy.is_dir())
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])
        finally:
            if swapped:
                if directory.exists():
                    directory.rename(decoy)
                if moved.exists():
                    moved.rename(directory)
            if (self.rpc._directory_identity is None and directory.exists()
                    and (directory.stat().st_dev, directory.stat().st_ino) == owned_identity
                    and not any(directory.iterdir())):
                directory.rmdir()  # Only this test's own disposable directory.

    def test_unexpected_socket_directory_entry_is_not_removed_or_accepted(self):
        self.start()
        self.stop()
        foreign = self.rpc.socket_directory / 'other-actor-entry'
        foreign.write_text('do not remove')
        try:
            with self.assertRaisesRegex(R.RPCError, 'SOCKET_DIRECTORY_NOT_EMPTY'):
                self.rpc.close()
            self.assertEqual(foreign.read_text(), 'do not remove')
            self.assertFalse(self.rpc.socket_path.exists())
        finally:
            foreign.unlink()  # Only this test's injected disposable entry.

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
